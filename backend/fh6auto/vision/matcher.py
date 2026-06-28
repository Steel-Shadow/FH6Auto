from __future__ import annotations

import os
import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from difflib import SequenceMatcher

import cv2
import numpy as np

from ..backend.state import RuntimeState
from ..paths import get_img_path
from .cache import ImageCacheService
from .ocr import OcrService
from .timing import VisionTimingMixin


@dataclass(frozen=True)
class CardText:
    text: str
    normalized: str
    score: float
    center: tuple[float, float]


@dataclass(frozen=True)
class CarCardSpec:
    title: str | None = None
    manufacturer: str | None = None
    rarity: str | None = None
    car_class: str | None = None
    pi: str | None = None
    year: str | None = None
    is_new: bool | None = None


@dataclass(frozen=True)
class CarCardOcrCandidate:
    box: tuple[int, int, int, int]
    pos: tuple[int, int]
    spec: CarCardSpec
    texts: tuple[CardText, ...]
    score: float
    failed: tuple[str, ...]


@dataclass(frozen=True)
class CarCardActionMatch:
    action: str
    position: tuple[int, int]
    spec: CarCardSpec
    score: float


class ImageMatcherService(VisionTimingMixin):
    """提供当前画面的图像目标匹配，复合目标可使用 OCR 结果作为特征。"""

    TIMING_NAME = "Match"
    CAR_CARD_TITLE_SIMILARITY_THRESHOLD = 0.80

    RARITY_WORDS = ("传奇", "史诗", "稀有", "普通")
    TAG_WORDS = ("全新",)
    PI_RE = re.compile(r"\b(X|S2|S1|A|B|C|D)\s*([0-9]{3})\b", re.IGNORECASE)
    YEAR_RE = re.compile(r"(19|20)\d{2}")

    def __init__(
        self,
        *,
        state: RuntimeState,
        image_cache: ImageCacheService,
        ocr: OcrService,
        log: Callable[..., None],
    ) -> None:
        self.state = state
        self.image_cache = image_cache
        self.ocr = ocr
        self.log = log
        self.last_positions: dict[str, tuple[int, int]] = {}
        self.sift_feature_cache = {}
        self._tag_contour_cache: dict[str, tuple[np.ndarray, tuple[int, int, int, int]] | None] = {}

    @staticmethod
    def _crop_ratio(img, x1, y1, x2, y2):
        h, w = img.shape[:2]
        left = max(0, min(w, int(round(w * x1))))
        top = max(0, min(h, int(round(h * y1))))
        right = max(left + 1, min(w, int(round(w * x2))))
        bottom = max(top + 1, min(h, int(round(h * y2))))
        return img[top:bottom, left:right]

    @staticmethod
    def _contains_cjk(text: str) -> bool:
        return any("\u4e00" <= ch <= "\u9fff" for ch in text)

    @staticmethod
    def _cjk_only(text: str) -> str:
        return "".join(ch for ch in text if "\u4e00" <= ch <= "\u9fff")

    @staticmethod
    def _text_similarity(left: str, right: str) -> float:
        if not left or not right:
            return 0.0
        return float(SequenceMatcher(None, left, right).ratio())

    @staticmethod
    def _box_center(box) -> tuple[float, float] | None:
        if box is None:
            return None
        points = list(box)
        if not points:
            return None
        xs = [float(point[0]) for point in points]
        ys = [float(point[1]) for point in points]
        return sum(xs) / len(xs), sum(ys) / len(ys)

    @staticmethod
    def _cluster_axis_positions(values, tolerance):
        clusters = []
        for value in sorted(int(v) for v in values):
            if not clusters or abs(value - clusters[-1][-1]) > tolerance:
                clusters.append([value])
            else:
                clusters[-1].append(value)
        return [int(round(sum(cluster) / len(cluster))) for cluster in clusters]

    @staticmethod
    def _car_card_column_order(x, y, score=0.0):
        """车辆卡片按列优先：同一列从上到下，再移动到右侧下一列。"""
        return (float(x), float(y), -float(score))

    def _segment_car_card_boxes(self, screen_bgr):
        screen_h, screen_w = screen_bgr.shape[:2]
        gray = cv2.cvtColor(screen_bgr, cv2.COLOR_BGR2GRAY)
        mask = cv2.threshold(gray, 180, 255, cv2.THRESH_BINARY)[1]
        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

        min_w = max(100, int(screen_w * 0.06))
        min_h = max(80, int(screen_h * 0.08))
        top_limit = int(screen_h * 0.14)
        bottom_limit = int(screen_h * 0.94)

        boxes = []
        for contour in contours:
            x, y, w, h = cv2.boundingRect(contour)
            if w < min_w or h < min_h:
                continue
            if y < top_limit or y > bottom_limit:
                continue

            aspect = w / h if h else 0
            if not 0.9 <= aspect <= 1.8:
                continue

            area_ratio = cv2.contourArea(contour) / float(w * h)
            if area_ratio < 0.45:
                continue

            boxes.append((x, y, w, h))

        if len(boxes) >= 4:
            median_w = int(round(np.median([box[2] for box in boxes])))
            median_h = int(round(np.median([box[3] for box in boxes])))
            x_positions = self._cluster_axis_positions([box[0] for box in boxes], max(16, median_w // 3))
            y_positions = self._cluster_axis_positions([box[1] for box in boxes], max(16, median_h // 3))

            if len(x_positions) >= 2 and len(y_positions) >= 2:
                grid_boxes = []
                seen = set()
                for x in x_positions:
                    for y in y_positions:
                        box = (
                            max(0, x),
                            max(0, y),
                            min(median_w, screen_w - x),
                            min(median_h, screen_h - y),
                        )
                        key = (box[0] // 6, box[1] // 6)
                        if box[2] > 0 and box[3] > 0 and key not in seen:
                            seen.add(key)
                            grid_boxes.append(box)
                if grid_boxes:
                    boxes = grid_boxes

        boxes.sort(key=lambda item: self._car_card_column_order(item[0], item[1], item[2] * item[3]))
        deduped = []
        for box in boxes:
            x, y, w, h = box
            center = (x + w / 2, y + h / 2)
            if any(abs(center[0] - (bx + bw / 2)) < 20 and abs(center[1] - (by + bh / 2)) < 20 for bx, by, bw, bh in deduped):
                continue
            deduped.append(box)
        return deduped

    def _parse_car_card_spec(self, texts: list[CardText] | tuple[CardText, ...]) -> CarCardSpec:
        title_parts: list[str] = []
        manufacturer = None
        rarity = None
        car_class = None
        pi = None
        year = None
        is_new = None

        for item in texts:
            raw = item.text.strip()
            norm = item.normalized
            if not raw or not norm:
                continue

            year_match = self.YEAR_RE.search(raw)
            if year_match and year is None:
                year = year_match.group(0)

            for word in self.TAG_WORDS:
                if word in raw:
                    is_new = True

            for word in self.RARITY_WORDS:
                if word in raw and rarity is None:
                    rarity = word

            pi_match = self.PI_RE.search(norm)
            if pi_match:
                car_class = pi_match.group(1).upper()
                pi = pi_match.group(2)
            elif car_class is None and norm in {"X", "S2", "S1", "A", "B", "C", "D"}:
                car_class = norm
            elif pi is None and norm.isdigit() and len(norm) == 3:
                pi = norm

            cjk = self._cjk_only(raw)
            if cjk and manufacturer is None and cjk not in self.RARITY_WORDS and cjk not in self.TAG_WORDS:
                manufacturer = cjk

            if (
                not self._contains_cjk(raw)
                and any(ch.isalpha() for ch in norm)
                and not self.PI_RE.fullmatch(norm)
                and norm not in {"X", "S2", "S1", "A", "B", "C", "D"}
                and len(norm) >= 3
            ):
                title_parts.append(norm)

        title = "".join(title_parts) if title_parts else None
        if is_new is None:
            is_new = False
        return CarCardSpec(
            title=title,
            manufacturer=manufacturer,
            rarity=rarity,
            car_class=car_class,
            pi=pi,
            year=year,
            is_new=is_new,
        )

    def _ocr_texts_for_image(self, image_bgr) -> list[CardText]:
        results = self.ocr.read(image_bgr, text_score=0.3)
        texts: list[CardText] = []
        for result in results:
            center = self._box_center(result.box)
            if center is None:
                continue
            normalized = self.ocr.normalize_text(result.text)
            if not normalized:
                continue
            texts.append(
                CardText(
                    text=result.text,
                    normalized=normalized,
                    score=float(result.score),
                    center=center,
                )
            )
        return texts

    def _read_car_card_spec_from_template(self, card_path) -> CarCardSpec | None:
        template_orig, _ = self.image_cache.load_template(card_path)
        if template_orig is None:
            return None

        texts = self._ocr_texts_for_image(template_orig)
        if not texts:
            return None
        return self._parse_car_card_spec(texts)

    def _normalize_tag_text(self, tag_text) -> str | None:
        if not tag_text:
            return None
        text = str(tag_text)
        normalized = self.ocr.normalize_text(text)
        if len(normalized) < 2 or "?" in normalized:
            return None
        return text

    @staticmethod
    def _contour_circularity(contour) -> float:
        area = cv2.contourArea(contour)
        perimeter = cv2.arcLength(contour, True)
        if area <= 0 or perimeter <= 0:
            return 0.0
        return float(4.0 * np.pi * area / (perimeter * perimeter))

    @staticmethod
    def _has_favorite_badge(card_bgr) -> bool:
        """检测车辆卡右下角的收藏爱心图标。

        只在稳定的局部区域内做暗色连通域/形状检测，不做像素模板匹配。
        """
        if card_bgr is None or card_bgr.size == 0:
            return False

        card_h, card_w = card_bgr.shape[:2]
        x1 = int(round(card_w * 0.72))
        y1 = int(round(card_h * 0.65))
        x2 = min(card_w, int(round(card_w * 0.995)))
        y2 = min(card_h, int(round(card_h * 0.99)))
        search = card_bgr[y1:y2, x1:x2]
        if search.size == 0:
            return False

        gray = cv2.cvtColor(search, cv2.COLOR_BGR2GRAY)
        mask = cv2.inRange(gray, 0, 80)
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((2, 2), np.uint8), iterations=1)
        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

        min_w = max(5, int(card_w * 0.030))
        min_h = max(5, int(card_h * 0.030))
        max_w = max(min_w + 1, int(card_w * 0.115))
        max_h = max(min_h + 1, int(card_h * 0.145))

        for contour in contours:
            x, y, w, h = cv2.boundingRect(contour)
            if not (min_w <= w <= max_w and min_h <= h <= max_h):
                continue
            aspect = w / max(1, h)
            if not 0.65 <= aspect <= 1.55:
                continue
            area = cv2.contourArea(contour)
            fill_ratio = area / max(1, w * h)
            if not 0.20 <= fill_ratio <= 0.85:
                continue
            center_x = (x + w / 2) / max(1, search.shape[1])
            center_y = (y + h / 2) / max(1, search.shape[0])
            if center_x < 0.55 or center_y < 0.35:
                continue
            circularity = ImageMatcherService._contour_circularity(contour)
            if 0.10 <= circularity <= 0.95:
                return True

        return False

    @staticmethod
    def _has_driving_badge(card_bgr) -> bool:
        """检测车辆卡右下角绿色正在驾驶方向盘图标。"""
        if card_bgr is None or card_bgr.size == 0:
            return False

        card_h, card_w = card_bgr.shape[:2]
        x1 = int(round(card_w * 0.80))
        y1 = int(round(card_h * 0.60))
        x2 = min(card_w, int(round(card_w * 0.99)))
        y2 = min(card_h, int(round(card_h * 0.88)))
        search = card_bgr[y1:y2, x1:x2]
        if search.size == 0:
            return False

        hsv = cv2.cvtColor(search, cv2.COLOR_BGR2HSV)
        green_mask = cv2.inRange(
            hsv,
            np.array([34, 180, 150], dtype=np.uint8),
            np.array([42, 255, 255], dtype=np.uint8),
        )
        contours, _ = cv2.findContours(green_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        for contour in contours:
            x, y, w, h = cv2.boundingRect(contour)
            if w < max(8, int(card_w * 0.04)) or h < max(8, int(card_h * 0.05)):
                continue
            if w > card_w * 0.14 or h > card_h * 0.20:
                continue

            area = max(1, w * h)
            green_ratio = np.count_nonzero(green_mask[y : y + h, x : x + w]) / area
            if green_ratio < 0.55:
                continue

            badge_gray = cv2.cvtColor(search[y : y + h, x : x + w], cv2.COLOR_BGR2GRAY)
            black_mask = cv2.inRange(badge_gray, 0, 80)
            black_ratio = np.count_nonzero(black_mask) / area
            if black_ratio < 0.06:
                continue

            icon_contours, _ = cv2.findContours(black_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
            for icon_contour in icon_contours:
                icon_area = cv2.contourArea(icon_contour)
                if icon_area < max(4, area * 0.035):
                    continue
                ix, iy, iw, ih = cv2.boundingRect(icon_contour)
                if iw < 3 or ih < 3:
                    continue
                aspect = iw / max(1, ih)
                circularity = ImageMatcherService._contour_circularity(icon_contour)
                if 0.55 <= aspect <= 1.80 and circularity >= 0.12:
                    return True

            if black_ratio >= 0.14:
                return True

        return False

    @staticmethod
    def _tag_foreground_masks(img) -> list[np.ndarray]:
        if img is None or img.size == 0:
            return []

        if img.ndim == 3 and img.shape[2] == 4:
            bgr = img[:, :, :3]
        else:
            bgr = img
        gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY) if bgr.ndim == 3 else bgr
        _, binary = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        masks = [binary, 255 - binary]

        output = []
        total = max(1, gray.shape[0] * gray.shape[1])
        kernel = np.ones((2, 2), np.uint8)
        for mask in masks:
            ratio = np.count_nonzero(mask) / total
            if not 0.03 <= ratio <= 0.85:
                continue
            clean = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel)
            clean = cv2.morphologyEx(clean, cv2.MORPH_CLOSE, kernel)
            if np.count_nonzero(clean) >= 8:
                output.append(clean)
        return output

    @staticmethod
    def _largest_contour(mask):
        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        contours = [contour for contour in contours if cv2.contourArea(contour) >= 8]
        if not contours:
            return None
        return max(contours, key=cv2.contourArea)

    def _tag_contour_template(self, tag_path):
        if not tag_path:
            return None

        actual_path = get_img_path(tag_path)
        if actual_path in self._tag_contour_cache:
            return self._tag_contour_cache[actual_path]

        tpl = cv2.imread(actual_path, cv2.IMREAD_UNCHANGED)
        best = None
        for mask in self._tag_foreground_masks(tpl):
            contour = self._largest_contour(mask)
            if contour is None:
                continue
            x, y, w, h = cv2.boundingRect(contour)
            score = cv2.contourArea(contour)
            if best is None or score > best[0]:
                best = (score, contour, (x, y, w, h))

        data = (best[1], best[2]) if best else None
        self._tag_contour_cache[actual_path] = data
        return data

    @staticmethod
    def _icon_kind_from_path(tag_path) -> str | None:
        if not tag_path:
            return None
        name = os.path.basename(str(tag_path)).lower()
        if "liketag" in name or ("like" in name and "author" not in name):
            return "favorite"
        if "driving" in name or "wheel" in name or "steering" in name:
            return "driving"
        return None

    def _match_tag_contour_score(self, roi, tag_path) -> float:
        icon_kind = self._icon_kind_from_path(tag_path)
        if icon_kind == "favorite":
            return 1.0 if self._has_favorite_badge(roi) else 0.0
        if icon_kind == "driving":
            return 1.0 if self._has_driving_badge(roi) else 0.0

        template = self._tag_contour_template(tag_path)
        if template is None or roi is None or roi.size == 0:
            return 0.0

        template_contour, (_, _, tpl_w, tpl_h) = template
        tpl_aspect = tpl_w / max(1, tpl_h)
        roi_h, roi_w = roi.shape[:2]
        best_score = 0.0

        for mask in self._tag_foreground_masks(roi):
            contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
            for contour in contours:
                area = cv2.contourArea(contour)
                if area < 8:
                    continue

                x, y, w, h = cv2.boundingRect(contour)
                if w < 6 or h < 6:
                    continue
                if w > roi_w * 0.45 or h > roi_h * 0.45:
                    continue

                aspect = w / max(1, h)
                aspect_score = min(aspect, tpl_aspect) / max(aspect, tpl_aspect)
                if aspect_score < 0.45:
                    continue

                shape_distance = cv2.matchShapes(template_contour, contour, cv2.CONTOURS_MATCH_I1, 0.0)
                shape_score = 1.0 / (1.0 + max(0.0, float(shape_distance)) * 8.0)
                score = shape_score * 0.78 + aspect_score * 0.22
                best_score = max(best_score, score)

        return best_score

    def _score_car_card_ocr_candidate(
        self,
        target: CarCardSpec,
        candidate: CarCardSpec,
        required_tag_text: str | None,
        excluded_tag_text: str | None,
        required_tag_contour_score: float | None = None,
        excluded_tag_contour_score: float | None = None,
        tag_threshold: float = 0.55,
    ) -> tuple[float, tuple[str, ...]]:
        score = 0.0
        possible_score = 0.0
        checks = 0
        identity_checks = 0
        failed: list[str] = []

        def check_equal(name: str, expected: str | None, actual: str | None, weight: float) -> None:
            nonlocal score, possible_score, checks, identity_checks
            if not expected:
                return
            possible_score += weight
            checks += 1
            if name in {"manufacturer", "class", "pi"}:
                identity_checks += 1
            expected_norm = self.ocr.normalize_text(expected)
            actual_norm = self.ocr.normalize_text(actual or "")
            if actual_norm == expected_norm:
                score += weight
            else:
                failed.append(name)

        check_equal("manufacturer", target.manufacturer, candidate.manufacturer, 0.35)
        check_equal("rarity", target.rarity, candidate.rarity, 0.22)
        check_equal("class", target.car_class, candidate.car_class, 0.14)
        check_equal("pi", target.pi, candidate.pi, 0.19)

        if required_tag_text:
            checks += 1
            possible_score += 0.10
            tag_norm = self.ocr.normalize_text(required_tag_text)
            if tag_norm == self.ocr.normalize_text("全新"):
                if candidate.is_new:
                    score += 0.10
                else:
                    failed.append("required_tag")
            elif target.title and candidate.title and tag_norm in candidate.title:
                score += 0.10
            else:
                failed.append("required_tag")
        elif required_tag_contour_score is not None:
            checks += 1
            possible_score += 0.10
            if required_tag_contour_score >= tag_threshold:
                score += 0.10
            else:
                failed.append("required_tag")

        if excluded_tag_text:
            tag_norm = self.ocr.normalize_text(excluded_tag_text)
            if tag_norm == self.ocr.normalize_text("全新") and candidate.is_new:
                failed.append("excluded_tag")
        elif excluded_tag_contour_score is not None and excluded_tag_contour_score >= tag_threshold:
            failed.append("excluded_tag")

        if target.title and candidate.title:
            possible_score += 0.06
            identity_checks += 1
            target_title = self.ocr.normalize_text(target.title)
            candidate_title = self.ocr.normalize_text(candidate.title)
            title_similarity = self._text_similarity(target_title, candidate_title)
            if (
                len(target_title) >= 4
                and len(candidate_title) >= 4
                and (
                    target_title in candidate_title
                    or candidate_title in target_title
                    or title_similarity >= self.CAR_CARD_TITLE_SIMILARITY_THRESHOLD
                )
            ):
                score += 0.06
            else:
                failed.append("title")

        if target.year and candidate.year:
            possible_score += 0.04
            if target.year == candidate.year:
                score += 0.04
            else:
                failed.append("year")

        if checks == 0 or identity_checks == 0:
            failed.append("no_target_fields")
        if possible_score <= 0:
            return 0.0, tuple(failed)
        return min(1.0, score / possible_score), tuple(failed)

    def _find_first_car_card_ocr_candidate(
        self,
        card_path,
        screen_bgr,
        required_tag_path=None,
        excluded_tag_path=None,
        required_tag_text=None,
        excluded_tag_text=None,
        exclude_driving=False,
        min_score=0.75,
        tag_threshold=0.55,
        target: CarCardSpec | None = None,
    ) -> CarCardOcrCandidate | None:
        target = target or self._read_car_card_spec_from_template(card_path)
        if target is None:
            return None

        boxes = self._segment_car_card_boxes(screen_bgr)
        if not boxes:
            return None

        required_tag_text = self._normalize_tag_text(required_tag_text)
        excluded_tag_text = self._normalize_tag_text(excluded_tag_text)

        for x, y, w, h in boxes:
            roi = screen_bgr[y : y + h, x : x + w]
            if exclude_driving and self._has_driving_badge(roi):
                continue
            card_texts = tuple(self._ocr_texts_for_image(roi))
            if not card_texts:
                continue

            spec = self._parse_car_card_spec(card_texts)
            required_tag_contour_score = (
                self._match_tag_contour_score(roi, required_tag_path)
                if required_tag_path and required_tag_text is None
                else None
            )
            excluded_tag_contour_score = (
                self._match_tag_contour_score(roi, excluded_tag_path)
                if excluded_tag_path and excluded_tag_text is None
                else None
            )
            score, failed = self._score_car_card_ocr_candidate(
                target,
                spec,
                required_tag_text,
                excluded_tag_text,
                required_tag_contour_score=required_tag_contour_score,
                excluded_tag_contour_score=excluded_tag_contour_score,
                tag_threshold=tag_threshold,
            )
            if score >= min_score and not failed:
                return CarCardOcrCandidate(
                    box=(x, y, w, h),
                    pos=(int(x + w // 2), int(y + h // 2)),
                    spec=spec,
                    texts=card_texts,
                    score=score,
                    failed=failed,
                )

        return None

    def _find_first_consumable_car_card_action_candidate(
        self,
        screen_bgr,
        *,
        mastery_target: CarCardSpec,
        remove_target: CarCardSpec,
        min_score=0.75,
        tag_threshold=0.55,
    ) -> tuple[str, CarCardOcrCandidate] | None:
        boxes = self._segment_car_card_boxes(screen_bgr)
        if not boxes:
            return None

        required_new_text = self._normalize_tag_text("全新")
        excluded_new_text = self._normalize_tag_text("全新")

        for x, y, w, h in boxes:
            roi = screen_bgr[y : y + h, x : x + w]
            card_texts = tuple(self._ocr_texts_for_image(roi))
            if not card_texts:
                continue

            spec = self._parse_car_card_spec(card_texts)
            mastery_score, mastery_failed = self._score_car_card_ocr_candidate(
                mastery_target,
                spec,
                required_new_text,
                None,
                tag_threshold=tag_threshold,
            )
            if mastery_score >= min_score and not mastery_failed:
                return (
                    "mastery",
                    CarCardOcrCandidate(
                        box=(x, y, w, h),
                        pos=(int(x + w // 2), int(y + h // 2)),
                        spec=spec,
                        texts=card_texts,
                        score=mastery_score,
                        failed=mastery_failed,
                    ),
                )

            if self._has_driving_badge(roi):
                continue

            remove_score, remove_failed = self._score_car_card_ocr_candidate(
                remove_target,
                spec,
                None,
                excluded_new_text,
                tag_threshold=tag_threshold,
            )
            if remove_score >= min_score and not remove_failed:
                return (
                    "remove",
                    CarCardOcrCandidate(
                        box=(x, y, w, h),
                        pos=(int(x + w // 2), int(y + h // 2)),
                        spec=spec,
                        texts=card_texts,
                        score=remove_score,
                        failed=remove_failed,
                    ),
                )

        return None

    def find_consumable_car_card_action(
        self,
        *,
        mastery_card_path="newCC.png",
        remove_card_path="removecarobject.png",
        region=None,
        final_threshold=0.78,
        tag_threshold=0.70,
        mask_areas=None,
    ) -> CarCardActionMatch | None:
        if not self.state.is_running:
            return None

        started = time.perf_counter()
        capture_ms = None
        ocr_ms = None
        result_text = "miss"
        action = "-"
        try:
            capture_started = time.perf_counter()
            frame = self.image_cache.capture_frame(region, mask_areas=mask_areas)
            capture_ms = self._elapsed_ms(capture_started)
            screen_bgr = frame.image

            mastery_target = self._read_car_card_spec_from_template(mastery_card_path)
            remove_target = self._read_car_card_spec_from_template(remove_card_path)
            if mastery_target is None or remove_target is None:
                result_text = "target_spec_missing"
                self.log(
                    f"[CarCardOCR] 无法读取合并车辆目标字段: mastery={mastery_card_path}, remove={remove_card_path}",
                    level="debug",
                )
                return None

            ocr_started = time.perf_counter()
            result = self._find_first_consumable_car_card_action_candidate(
                screen_bgr,
                mastery_target=mastery_target,
                remove_target=remove_target,
                min_score=max(0.75, min(float(final_threshold), 0.85)),
                tag_threshold=min(float(tag_threshold), 0.55),
            )
            ocr_ms = self._elapsed_ms(ocr_started)
            if result is None:
                result_text = "ocr_miss"
                return None

            action, candidate = result
            position = frame.to_screen_point(candidate.pos)
            spec = candidate.spec
            self.log(
                f"[CarCardOCR] 合并锁定: {action} | 综合:{candidate.score:.3f} | "
                f"车型:{spec.title or '-'} | 制造商:{spec.manufacturer or '-'} | 稀有度:{spec.rarity or '-'} | "
                f"PI:{spec.car_class or '-'} {spec.pi or '-'} | 年份:{spec.year or '-'} | "
                f"全新:{'是' if spec.is_new else '否'}",
                level="debug",
            )
            result_text = "hit"
            return CarCardActionMatch(
                action=action,
                position=position,
                spec=spec,
                score=candidate.score,
            )

        except Exception as e:
            result_text = "error"
            self.log(f"find_consumable_car_card_action 异常: {e}", level="warning")
            return None
        finally:
            self._log_timing(
                "find_consumable_car_card_action",
                started,
                capture_ms=capture_ms,
                ocr_ms=ocr_ms,
                action=action,
                result=result_text,
            )

    def find_car_card(
        self,
        card_path,
        required_tag_path=None,
        excluded_tag_path=None,
        required_tag_text=None,
        excluded_tag_text=None,
        exclude_driving=False,
        region=None,
        final_threshold=0.78,
        tag_threshold=0.70,
        mask_areas=None,
    ):
        if not self.state.is_running:
            return None

        started = time.perf_counter()
        capture_ms = None
        ocr_ms = None
        candidates_count = 0
        result_text = "miss"
        method = "-"
        try:
            capture_started = time.perf_counter()
            frame = self.image_cache.capture_frame(region, mask_areas=mask_areas)
            capture_ms = self._elapsed_ms(capture_started)
            screen_bgr = frame.image

            target_spec = self._read_car_card_spec_from_template(card_path)
            if target_spec is None:
                result_text = "target_spec_missing"
                self.log(f"[CarCardOCR] 无法从参考图读取目标车辆字段: {card_path}", level="debug")
                return None

            ocr_started = time.perf_counter()
            first_ocr_candidate = self._find_first_car_card_ocr_candidate(
                card_path,
                screen_bgr,
                required_tag_path=required_tag_path,
                excluded_tag_path=excluded_tag_path,
                required_tag_text=required_tag_text,
                excluded_tag_text=excluded_tag_text,
                exclude_driving=exclude_driving,
                min_score=max(0.75, min(float(final_threshold), 0.85)),
                tag_threshold=min(float(tag_threshold), 0.55),
                target=target_spec,
            )
            ocr_ms = self._elapsed_ms(ocr_started)
            candidates_count = 1 if first_ocr_candidate else 0
            if first_ocr_candidate:
                best_ocr = first_ocr_candidate
                pos = frame.to_screen_point(best_ocr.pos)
                self.last_positions[card_path] = pos
                spec = best_ocr.spec
                self.log(
                    f"[CarCardOCR] 锁定: {card_path} | 综合:{best_ocr.score:.3f} | "
                    f"车型:{spec.title or '-'} | 制造商:{spec.manufacturer or '-'} | 稀有度:{spec.rarity or '-'} | "
                    f"PI:{spec.car_class or '-'} {spec.pi or '-'} | 年份:{spec.year or '-'} | "
                    f"全新:{'是' if spec.is_new else '否'}",
                    level="debug",
                )
                result_text = "hit"
                method = "ocr"
                return pos

            result_text = "ocr_miss"
            self.log(
                f"[CarCardOCR] 未命中: {card_path} | 目标字段: "
                f"车型={target_spec.title or '-'}, 制造商={target_spec.manufacturer or '-'}, "
                f"稀有度={target_spec.rarity or '-'}, "
                f"PI={target_spec.car_class or '-'} {target_spec.pi or '-'}, "
                f"年份={target_spec.year or '-'}, 全新={'是' if target_spec.is_new else '否'}",
                level="debug",
            )
            return None

        except Exception as e:
            result_text = "error"
            self.log(f"find_car_card 异常: {e}", level="warning")
            return None
        finally:
            self._log_timing(
                "find_car_card",
                started,
                capture_ms=capture_ms,
                ocr_ms=ocr_ms,
                candidates=candidates_count,
                method=method,
                result=result_text,
            )

    def _get_sift_reference_features(self, reference_path, max_features=2500):
        actual_path = get_img_path(reference_path)
        try:
            stat = os.stat(actual_path)
        except OSError:
            self.log(f"SIFT 参考图不存在：{reference_path}", level="warning")
            return None

        cache_key = ("sift", actual_path, stat.st_mtime, stat.st_size, int(max_features))
        if cache_key in self.sift_feature_cache:
            return self.sift_feature_cache[cache_key]

        reference_gray = cv2.imread(actual_path, cv2.IMREAD_GRAYSCALE)
        if reference_gray is None:
            self.log(f"SIFT 参考图读取失败：{reference_path}", level="warning")
            return None

        try:
            sift = cv2.SIFT_create(nfeatures=int(max_features))
        except Exception as e:
            self.log(f"当前 OpenCV 不支持 SIFT：{e}", level="warning")
            return None

        keypoints, descriptors = sift.detectAndCompute(reference_gray, None)
        if descriptors is None or len(keypoints) < 4:
            self.log(f"SIFT 参考图特征不足：{reference_path}", level="warning")
            return None

        data = (keypoints, descriptors, reference_gray.shape[:2], actual_path)
        self.sift_feature_cache[cache_key] = data
        return data

    def _match_sift_reference(
        self,
        reference_path,
        reference_data,
        screen_keypoints,
        screen_descriptors,
        screen_shape,
        min_inliers=50,
        ratio=0.75,
        ransac_reproj_threshold=5.0,
    ):
        if screen_descriptors is None or len(screen_keypoints) < 4:
            return None

        reference_keypoints, reference_descriptors, reference_shape, _ = reference_data

        matcher = cv2.BFMatcher(cv2.NORM_L2)
        raw_matches = matcher.knnMatch(reference_descriptors, screen_descriptors, k=2)
        good_matches = []
        for pair in raw_matches:
            if len(pair) != 2:
                continue
            first, second = pair
            if first.distance < float(ratio) * second.distance:
                good_matches.append(first)

        if len(good_matches) < 4:
            return None

        src_pts = np.float32([reference_keypoints[m.queryIdx].pt for m in good_matches]).reshape(-1, 1, 2)
        dst_pts = np.float32([screen_keypoints[m.trainIdx].pt for m in good_matches]).reshape(-1, 1, 2)
        homography, inlier_mask = cv2.findHomography(
            src_pts,
            dst_pts,
            cv2.RANSAC,
            float(ransac_reproj_threshold),
        )
        if homography is None or inlier_mask is None:
            return None

        inliers = int(inlier_mask.ravel().sum())
        if inliers < int(min_inliers):
            return None

        ref_h, ref_w = reference_shape
        corners = np.float32([[0, 0], [ref_w, 0], [ref_w, ref_h], [0, ref_h]]).reshape(-1, 1, 2)
        projected = cv2.perspectiveTransform(corners, homography).reshape(-1, 2)
        center_x = float(projected[:, 0].mean())
        center_y = float(projected[:, 1].mean())

        screen_h, screen_w = screen_shape[:2]
        if not (0 <= center_x < screen_w and 0 <= center_y < screen_h):
            return None

        projected_area = abs(cv2.contourArea(projected.astype(np.float32)))
        reference_area = float(ref_w * ref_h)
        if projected_area < 25 or reference_area <= 0:
            return None

        return {
            "reference_path": reference_path,
            "center": (center_x, center_y),
            "inliers": inliers,
            "good": len(good_matches),
            "scale": (projected_area / reference_area) ** 0.5,
            "projected": projected,
        }

    def find_any_image_sift(
        self,
        image_list,
        region=None,
        min_inliers=12,
        ratio=0.75,
        max_features=2500,
        ransac_reproj_threshold=5.0,
    ):
        if not self.state.is_running:
            return None

        started = time.perf_counter()
        capture_ms = None
        detect_ms = None
        match_ms = None
        refs_count = 0
        matches_count = 0
        result_text = "miss"
        try:
            capture_started = time.perf_counter()
            frame = self.image_cache.capture_frame(region)
            capture_ms = self._elapsed_ms(capture_started)
            screen_bgr = frame.image
            screen_gray = cv2.cvtColor(screen_bgr, cv2.COLOR_BGR2GRAY)
            sift = cv2.SIFT_create(nfeatures=int(max_features))
            detect_started = time.perf_counter()
            screen_keypoints, screen_descriptors = sift.detectAndCompute(screen_gray, None)
            detect_ms = self._elapsed_ms(detect_started)

            matches = []
            match_started = time.perf_counter()
            for reference_path in image_list:
                refs_count += 1
                reference_data = self._get_sift_reference_features(reference_path, max_features=max_features)
                if reference_data is None:
                    continue
                result = self._match_sift_reference(
                    reference_path,
                    reference_data,
                    screen_keypoints,
                    screen_descriptors,
                    screen_bgr.shape,
                    min_inliers=min_inliers,
                    ratio=ratio,
                    ransac_reproj_threshold=ransac_reproj_threshold,
                )
                if result:
                    matches.append(result)
            match_ms = self._elapsed_ms(match_started)
            matches_count = len(matches)

            if not matches:
                return None

            best = max(matches, key=lambda item: (item["inliers"], item["good"]))
            center_x, center_y = best["center"]

            pos = frame.to_screen_point((center_x, center_y))
            self.last_positions[best["reference_path"]] = pos
            self.log(
                f"[SIFTMatch] 命中: {best['reference_path']} | 内点: {best['inliers']}/{best['good']} "
                f"(阈值 {min_inliers}) | 估算缩放: {best['scale']:.3f}",
                level="debug",
            )
            result_text = "hit"
            return pos

        except Exception as e:
            result_text = "error"
            self.log(f"find_any_image_sift 异常: {e}", level="warning")
            return None
        finally:
            self._log_timing(
                "find_any_image_sift",
                started,
                capture_ms=capture_ms,
                detect_ms=detect_ms,
                match_ms=match_ms,
                refs=refs_count,
                matches=matches_count,
                result=result_text,
            )

    def find_image_sift(
        self,
        reference_path,
        region=None,
        min_inliers=50,
        ratio=0.75,
        max_features=2500,
        ransac_reproj_threshold=5.0,
    ):
        return self.find_any_image_sift(
            [reference_path],
            region=region,
            min_inliers=min_inliers,
            ratio=ratio,
            max_features=max_features,
            ransac_reproj_threshold=ransac_reproj_threshold,
        )
