from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

import cv2
import numpy as np

from ..paths import get_img_path

if TYPE_CHECKING:
    from ..backend.app import BackendApp


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


class ImageMatcherService:
    RARITY_WORDS = ("传奇", "史诗", "稀有", "普通")
    TAG_WORDS = ("全新",)
    PI_RE = re.compile(r"\b(X|S2|S1|A|B|C|D)\s*([0-9]{3})\b", re.IGNORECASE)
    YEAR_RE = re.compile(r"(19|20)\d{2}")

    def __init__(self, app: BackendApp) -> None:
        self.app = app
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
                for y in y_positions:
                    for x in x_positions:
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

        boxes.sort(key=lambda item: (item[1], item[0], -item[2] * item[3]))
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
        results = self.app.services.ocr.read(image_bgr, text_score=0.3)
        texts: list[CardText] = []
        for result in results:
            center = self._box_center(result.box)
            if center is None:
                continue
            normalized = self.app.services.ocr.normalize_text(result.text)
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
        template_orig, _ = self.app.services.image_cache.load_template(card_path)
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
        normalized = self.app.services.ocr.normalize_text(text)
        if len(normalized) < 2 or "?" in normalized:
            return None
        return text

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

    def _match_tag_contour_score(self, roi, tag_path) -> float:
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
            expected_norm = self.app.services.ocr.normalize_text(expected)
            actual_norm = self.app.services.ocr.normalize_text(actual or "")
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
            tag_norm = self.app.services.ocr.normalize_text(required_tag_text)
            if tag_norm == self.app.services.ocr.normalize_text("全新"):
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
            tag_norm = self.app.services.ocr.normalize_text(excluded_tag_text)
            if tag_norm == self.app.services.ocr.normalize_text("全新") and candidate.is_new:
                failed.append("excluded_tag")
        elif excluded_tag_contour_score is not None and excluded_tag_contour_score >= tag_threshold:
            failed.append("excluded_tag")

        if target.title and candidate.title:
            possible_score += 0.06
            identity_checks += 1
            target_title = self.app.services.ocr.normalize_text(target.title)
            candidate_title = self.app.services.ocr.normalize_text(candidate.title)
            if (
                len(target_title) >= 4
                and len(candidate_title) >= 4
                and (target_title in candidate_title or candidate_title in target_title)
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

    def _find_car_card_ocr_candidates(
        self,
        card_path,
        screen_bgr,
        required_tag_path=None,
        excluded_tag_path=None,
        required_tag_text=None,
        excluded_tag_text=None,
        min_score=0.75,
        tag_threshold=0.55,
        target: CarCardSpec | None = None,
    ) -> list[CarCardOcrCandidate]:
        target = target or self._read_car_card_spec_from_template(card_path)
        if target is None:
            return []

        boxes = self._segment_car_card_boxes(screen_bgr)
        if not boxes:
            return []

        screen_texts = self._ocr_texts_for_image(screen_bgr)
        if not screen_texts:
            return []

        required_tag_text = self._normalize_tag_text(required_tag_text)
        excluded_tag_text = self._normalize_tag_text(excluded_tag_text)
        candidates: list[CarCardOcrCandidate] = []

        for x, y, w, h in boxes:
            roi = screen_bgr[y : y + h, x : x + w]
            card_texts = tuple(
                text
                for text in screen_texts
                if x <= text.center[0] <= x + w and y <= text.center[1] <= y + h
            )
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
                candidates.append(
                    CarCardOcrCandidate(
                        box=(x, y, w, h),
                        pos=(int(x + w // 2), int(y + h // 2)),
                        spec=spec,
                        texts=card_texts,
                        score=score,
                        failed=failed,
                    )
                )

        candidates.sort(key=lambda item: (-item.score, item.box[1], item.box[0]))
        return candidates

    def _match_region_score(self, roi, template, template_rect, search_rect=None, mode="color"):
        try:
            tpl_part = self._crop_ratio(template, *template_rect)
            search_part = self._crop_ratio(roi, *(search_rect or template_rect))

            if mode == "gray":
                tpl_part = self.app.services.image_cache.to_gray_image(tpl_part)
                search_part = self.app.services.image_cache.to_gray_image(search_part)
            elif mode == "edge":
                tpl_part = self.app.services.image_cache.to_edge_image(tpl_part)
                search_part = self.app.services.image_cache.to_edge_image(search_part)

            return self.app.services.image_cache.match_template_score(search_part, tpl_part)
        except Exception:
            return 0.0

    def _match_required_tag(self, roi, tag_path, threshold, scales=None):
        if not tag_path:
            return 1.0

        best_score = 0.0
        scales_to_try = scales if scales is not None else self.app.services.image_cache.get_scales_to_try(fast_mode=True)
        for scale in scales_to_try:
            score = self.app.services.image_cache.match_text_ui_score(roi, tag_path, scale)
            best_score = max(best_score, score)
            if best_score >= threshold:
                return best_score
        return best_score

    def _car_card_candidate_scores(
        self,
        roi,
        template,
        required_tag_path,
        excluded_tag_path,
        tag_threshold,
        tag_scales=None,
    ):
        roi_gray = self.app.services.image_cache.to_gray_image(roi)
        tpl_gray = self.app.services.image_cache.to_gray_image(template)

        full_score = self.app.services.image_cache.match_template_score(roi, template)
        gray_score = self.app.services.image_cache.match_template_score(roi_gray, tpl_gray)
        title_score = self._match_region_score(
            roi,
            template,
            template_rect=(0.03, 0.00, 0.97, 0.28),
            search_rect=(0.02, 0.00, 0.98, 0.34),
            mode="gray",
        )
        body_score = self._match_region_score(
            roi,
            template,
            template_rect=(0.05, 0.24, 0.95, 0.75),
            search_rect=(0.02, 0.18, 0.98, 0.78),
            mode="edge",
        )
        rarity_score = self._match_region_score(
            roi,
            template,
            template_rect=(0.02, 0.78, 0.72, 0.99),
            search_rect=(0.00, 0.74, 0.76, 1.00),
            mode="color",
        )
        pi_score = self._match_region_score(
            roi,
            template,
            template_rect=(0.72, 0.78, 0.99, 0.99),
            search_rect=(0.67, 0.74, 1.00, 1.00),
            mode="color",
        )
        required_tag_score = self._match_required_tag(roi, required_tag_path, tag_threshold, scales=tag_scales)
        excluded_tag_score = self._match_required_tag(roi, excluded_tag_path, tag_threshold, scales=tag_scales)

        final_score = (
            title_score * 0.30
            + pi_score * 0.22
            + rarity_score * 0.16
            + body_score * 0.14
            + gray_score * 0.10
            + full_score * 0.08
        )

        return {
            "final": final_score,
            "full": full_score,
            "gray": gray_score,
            "title": title_score,
            "body": body_score,
            "rarity": rarity_score,
            "pi": pi_score,
            "required_tag": required_tag_score,
            "excluded_tag": excluded_tag_score,
        }

    def _car_card_failed_checks(
        self,
        scores,
        required_tag_path,
        excluded_tag_path,
        final_threshold,
        title_threshold,
        pi_threshold,
        rarity_threshold,
        body_threshold,
        tag_threshold,
        exclude_tag_threshold,
    ):
        failed = []
        if scores["title"] < title_threshold:
            failed.append("title")
        if scores["pi"] < pi_threshold:
            failed.append("pi")
        if scores["rarity"] < rarity_threshold:
            failed.append("rarity")
        if scores["body"] < body_threshold:
            failed.append("body")
        if required_tag_path and scores["required_tag"] < tag_threshold:
            failed.append("required_tag")
        if excluded_tag_path and scores["excluded_tag"] >= exclude_tag_threshold:
            failed.append("excluded_tag")
        if scores["final"] < final_threshold:
            failed.append("final")
        return failed

    def _find_car_card_sift_candidate(
        self,
        card_path,
        screen_bgr,
        template,
        min_inliers=10,
        ratio=0.75,
        max_features=3000,
        ransac_reproj_threshold=5.0,
    ):
        reference_data = self._get_sift_reference_features(card_path, max_features=max_features)
        if reference_data is None:
            return None

        screen_gray = cv2.cvtColor(screen_bgr, cv2.COLOR_BGR2GRAY)
        sift = cv2.SIFT_create(nfeatures=int(max_features))
        screen_keypoints, screen_descriptors = sift.detectAndCompute(screen_gray, None)
        result = self._match_sift_reference(
            card_path,
            reference_data,
            screen_keypoints,
            screen_descriptors,
            screen_bgr.shape,
            min_inliers=min_inliers,
            ratio=ratio,
            ransac_reproj_threshold=ransac_reproj_threshold,
        )
        if not result:
            return None

        projected = result.get("projected")
        if projected is None:
            return None

        ref_h, ref_w = template.shape[:2]
        projected = projected.astype(np.float32)
        target = np.float32([[0, 0], [ref_w, 0], [ref_w, ref_h], [0, ref_h]])
        transform = cv2.getPerspectiveTransform(projected, target)
        roi = cv2.warpPerspective(screen_bgr, transform, (ref_w, ref_h))

        center_x, center_y = result["center"]
        return {
            "roi": roi,
            "pos": (int(round(center_x)), int(round(center_y))),
            "sort_key": (float(projected[:, 1].min()), float(projected[:, 0].min())),
            "scale": result["scale"],
            "inliers": result["inliers"],
            "good": result["good"],
        }

    def _match_vehicle_region_score(self, roi, template):
        try:
            tpl_part = self._crop_ratio(template, 0.05, 0.24, 0.95, 0.75)
            roi_part = self._crop_ratio(roi, 0.05, 0.24, 0.95, 0.75)
            if tpl_part.shape[0] < 40 or tpl_part.shape[1] < 40 or roi_part.shape[0] < 40 or roi_part.shape[1] < 40:
                return 0.0

            target_size = (240, 130)
            tpl_part = cv2.resize(tpl_part, target_size, interpolation=cv2.INTER_AREA)
            roi_part = cv2.resize(roi_part, target_size, interpolation=cv2.INTER_AREA)
            tpl_gray = cv2.cvtColor(tpl_part, cv2.COLOR_BGR2GRAY)
            roi_gray = cv2.cvtColor(roi_part, cv2.COLOR_BGR2GRAY)

            orb = cv2.ORB_create(nfeatures=500)
            tpl_keypoints, tpl_descriptors = orb.detectAndCompute(tpl_gray, None)
            roi_keypoints, roi_descriptors = orb.detectAndCompute(roi_gray, None)
            if (
                tpl_descriptors is None
                or roi_descriptors is None
                or len(tpl_keypoints) < 12
                or len(roi_keypoints) < 12
            ):
                return 0.0

            matcher = cv2.BFMatcher(cv2.NORM_HAMMING)
            raw_matches = matcher.knnMatch(tpl_descriptors, roi_descriptors, k=2)
            good_matches = []
            for pair in raw_matches:
                if len(pair) != 2:
                    continue
                first, second = pair
                if first.distance < 0.75 * second.distance:
                    good_matches.append(first)

            return len(good_matches) / max(1, min(len(tpl_keypoints), len(roi_keypoints)))
        except Exception:
            return 0.0

    def _find_car_card_segment_candidates(
        self,
        screen_bgr,
        template,
        required_tag_path,
        excluded_tag_path,
        tag_threshold,
    ):
        ref_h, ref_w = template.shape[:2]
        candidates = []
        tag_scales = (0.75, 0.8, 0.85, 0.9, 0.95, 1.0, 1.05, 1.1, 1.15)

        for x, y, w, h in self._segment_car_card_boxes(screen_bgr):
            roi = screen_bgr[y : y + h, x : x + w]
            if roi.size == 0:
                continue

            normalized = cv2.resize(roi, (ref_w, ref_h), interpolation=cv2.INTER_AREA)
            scores = self._car_card_candidate_scores(
                normalized,
                template,
                required_tag_path=required_tag_path,
                excluded_tag_path=excluded_tag_path,
                tag_threshold=tag_threshold,
                tag_scales=tag_scales,
            )
            vehicle_score = self._match_vehicle_region_score(normalized, template)
            layout_score = (
                scores["pi"] * 0.32
                + scores["rarity"] * 0.24
                + vehicle_score * 0.20
                + scores["gray"] * 0.15
                + scores["full"] * 0.07
                + scores["body"] * 0.02
                + max(scores["title"], 0.0) * 0.04
            )
            scores["vehicle"] = vehicle_score
            scores["layout"] = layout_score
            scores["final"] = max(scores["final"], layout_score)
            scores["scale"] = (w * h / float(ref_w * ref_h)) ** 0.5

            candidates.append(
                {
                    "pos": (int(x + w // 2), int(y + h // 2)),
                    "scores": scores,
                    "sort_key": (y, x),
                    "method": "segment",
                }
            )

        return candidates

    def find_car_card(
        self,
        card_path,
        required_tag_path=None,
        excluded_tag_path=None,
        required_tag_text=None,
        excluded_tag_text=None,
        region=None,
        fast_mode=True,
        candidate_threshold=0.50,
        final_threshold=0.78,
        title_threshold=0.72,
        pi_threshold=0.82,
        rarity_threshold=0.68,
        body_threshold=0.55,
        tag_threshold=0.70,
        exclude_tag_threshold=0.65,
        max_candidates=80,
        mask_areas=None,
        template_fallback=False,
    ):
        if not self.app.state.is_running:
            return None

        try:
            screen_bgr = self.app.services.image_cache.capture_region(region, mask_areas=mask_areas)
            candidates = []
            template_orig, _ = self.app.services.image_cache.load_template(card_path)
            if template_orig is None:
                return None

            target_spec = self._read_car_card_spec_from_template(card_path)
            ocr_candidates = self._find_car_card_ocr_candidates(
                card_path,
                screen_bgr,
                required_tag_path=required_tag_path,
                excluded_tag_path=excluded_tag_path,
                required_tag_text=required_tag_text,
                excluded_tag_text=excluded_tag_text,
                min_score=max(0.75, min(float(final_threshold), 0.85)),
                tag_threshold=min(float(tag_threshold), 0.55),
                target=target_spec,
            )
            if ocr_candidates:
                best_ocr = ocr_candidates[0]
                pos = (
                    int(best_ocr.pos[0] + (region[0] if region else 0)),
                    int(best_ocr.pos[1] + (region[1] if region else 0)),
                )
                self.last_positions[card_path] = pos
                spec = best_ocr.spec
                self.app.log(
                    f"[CarCardOCR] 锁定: {card_path} | 综合:{best_ocr.score:.3f} | "
                    f"车型:{spec.title or '-'} | 制造商:{spec.manufacturer or '-'} | 稀有度:{spec.rarity or '-'} | "
                    f"PI:{spec.car_class or '-'} {spec.pi or '-'} | 年份:{spec.year or '-'} | "
                    f"全新:{'是' if spec.is_new else '否'}"
                )
                return pos
            if target_spec is not None:
                # self.app.log(
                #     f"[CarCardOCR] 未命中: {card_path} | 目标字段: "
                #     f"车型={target_spec.title or '-'}, 制造商={target_spec.manufacturer or '-'}, "
                #     f"稀有度={target_spec.rarity or '-'}, "
                #     f"PI={target_spec.car_class or '-'} {target_spec.pi or '-'}, "
                #     f"年份={target_spec.year or '-'}, 全新={'是' if target_spec.is_new else '否'}"
                # )
                return None

            sift_candidate = self._find_car_card_sift_candidate(card_path, screen_bgr, template_orig)
            if sift_candidate is not None:
                scores = self._car_card_candidate_scores(
                    sift_candidate["roi"],
                    template_orig,
                    required_tag_path=required_tag_path,
                    excluded_tag_path=excluded_tag_path,
                    tag_threshold=tag_threshold,
                    tag_scales=(0.8, 0.85, 0.9, 0.95, 1.0, 1.05, 1.1, 1.15),
                )
                scores["scale"] = sift_candidate["scale"]
                scores["inliers"] = sift_candidate["inliers"]
                scores["good"] = sift_candidate["good"]
                failed = self._car_card_failed_checks(
                    scores,
                    required_tag_path,
                    excluded_tag_path,
                    final_threshold,
                    title_threshold,
                    pi_threshold,
                    rarity_threshold,
                    min(body_threshold, 0.40),
                    tag_threshold,
                    exclude_tag_threshold,
                )
                if not failed:
                    candidates.append(
                        {
                            "pos": (
                                int(sift_candidate["pos"][0] + (region[0] if region else 0)),
                                int(sift_candidate["pos"][1] + (region[1] if region else 0)),
                            ),
                            "scores": scores,
                            "sort_key": sift_candidate["sort_key"],
                            "method": "sift",
                        }
                    )

            if not candidates:
                segment_candidates = self._find_car_card_segment_candidates(
                    screen_bgr,
                    template_orig,
                    required_tag_path=required_tag_path,
                    excluded_tag_path=excluded_tag_path,
                    tag_threshold=tag_threshold,
                )
                for segment_candidate in segment_candidates:
                    scores = segment_candidate["scores"]
                    failed = []
                    if scores["layout"] < max(0.50, min(final_threshold, 0.55)):
                        failed.append("layout")
                    if scores["pi"] < min(pi_threshold, 0.50):
                        failed.append("pi")
                    if scores["rarity"] < min(rarity_threshold, 0.50):
                        failed.append("rarity")
                    if scores.get("vehicle", 0.0) < 0.12:
                        failed.append("vehicle")
                    if required_tag_path and scores["required_tag"] < min(tag_threshold, 0.65):
                        failed.append("required_tag")
                    if excluded_tag_path and scores["excluded_tag"] >= exclude_tag_threshold:
                        failed.append("excluded_tag")

                    if "excluded_tag" in failed:
                        self.app.log(
                            f"[CarCardMatch] 排除候选: {card_path} | 排除标签 {excluded_tag_path}: "
                            f"{scores['excluded_tag']:.3f}"
                        )
                    if failed:
                        continue
                    candidates.append(segment_candidate)

            for scale in (
                () if candidates or not template_fallback else self.app.services.image_cache.get_scales_to_try(fast_mode=fast_mode)
            ):
                card_tpl, _ = self.app.services.image_cache.get_scaled_template(card_path, scale)
                if card_tpl is None:
                    continue

                h, w = card_tpl.shape[:2]
                if h < 30 or w < 30 or h > screen_bgr.shape[0] or w > screen_bgr.shape[1]:
                    continue

                res = cv2.matchTemplate(screen_bgr, card_tpl, cv2.TM_CCOEFF_NORMED)
                flat = res.ravel()
                if flat.size == 0:
                    continue

                top_k = min(int(max_candidates), flat.size)
                idxs = np.argpartition(flat, -top_k)[-top_k:]
                idxs = idxs[np.argsort(flat[idxs])[::-1]]
                checked = set()

                for idx in idxs:
                    y, x = np.unravel_index(idx, res.shape)
                    base_score = float(res[y, x])
                    if base_score < candidate_threshold:
                        continue

                    key = (x // 10, y // 10)
                    if key in checked:
                        continue
                    checked.add(key)

                    roi = screen_bgr[y : y + h, x : x + w]
                    if roi.shape[:2] != card_tpl.shape[:2]:
                        continue

                    scores = self._car_card_candidate_scores(
                        roi,
                        card_tpl,
                        required_tag_path=required_tag_path,
                        excluded_tag_path=excluded_tag_path,
                        tag_threshold=tag_threshold,
                        tag_scales=(scale, scale * 0.98, scale * 1.02),
                    )
                    scores["base"] = base_score
                    scores["scale"] = scale

                    failed = self._car_card_failed_checks(
                        scores,
                        required_tag_path,
                        excluded_tag_path,
                        final_threshold,
                        title_threshold,
                        pi_threshold,
                        rarity_threshold,
                        body_threshold,
                        tag_threshold,
                        exclude_tag_threshold,
                    )
                    if "excluded_tag" in failed:
                        self.app.log(
                            f"[CarCardMatch] 排除候选: {card_path} | 排除标签 {excluded_tag_path}: "
                            f"{scores['excluded_tag']:.3f}"
                        )
                    if failed:
                        continue

                    candidates.append(
                        {
                            "pos": (
                                int(x + w // 2 + (region[0] if region else 0)),
                                int(y + h // 2 + (region[1] if region else 0)),
                            ),
                            "scores": scores,
                            "sort_key": (y, x),
                            "method": "template",
                        }
                    )

            if not candidates:
                return None

            # 同一页可能有多个同款目标车，按视觉顺序取左上第一个，便于连续处理。
            candidates.sort(key=lambda item: (-item["scores"]["final"], item["sort_key"][0], item["sort_key"][1]))
            best = candidates[0]
            scores = best["scores"]
            self.last_positions[card_path] = best["pos"]
            self.app.log(
                f"[CarCardMatch] 锁定: {card_path} | 方法:{best.get('method', 'template')} | 综合:{scores['final']:.3f} | "
                f"标题:{scores['title']:.3f} | PI:{scores['pi']:.3f} | 稀有度:{scores['rarity']:.3f} | "
                f"车身:{scores['body']:.3f} | 车辆:{scores.get('vehicle', 0.0):.3f} | "
                f"标签:{scores['required_tag']:.3f} | 缩放:{scores['scale']:.3f}"
            )
            return best["pos"]

        except Exception as e:
            self.app.log(f"find_car_card 异常: {e}")
            return None

    def _get_sift_reference_features(self, reference_path, max_features=2500):
        actual_path = get_img_path(reference_path)
        try:
            stat = os.stat(actual_path)
        except OSError:
            self.app.log(f"SIFT 参考图不存在：{reference_path}")
            return None

        cache_key = ("sift", actual_path, stat.st_mtime, stat.st_size, int(max_features))
        if cache_key in self.sift_feature_cache:
            return self.sift_feature_cache[cache_key]

        reference_gray = cv2.imread(actual_path, cv2.IMREAD_GRAYSCALE)
        if reference_gray is None:
            self.app.log(f"SIFT 参考图读取失败：{reference_path}")
            return None

        try:
            sift = cv2.SIFT_create(nfeatures=int(max_features))
        except Exception as e:
            self.app.log(f"当前 OpenCV 不支持 SIFT：{e}")
            return None

        keypoints, descriptors = sift.detectAndCompute(reference_gray, None)
        if descriptors is None or len(keypoints) < 4:
            self.app.log(f"SIFT 参考图特征不足：{reference_path}")
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
        if not self.app.state.is_running:
            return None

        try:
            screen_bgr = self.app.services.image_cache.capture_region(region)
            screen_gray = cv2.cvtColor(screen_bgr, cv2.COLOR_BGR2GRAY)
            sift = cv2.SIFT_create(nfeatures=int(max_features))
            screen_keypoints, screen_descriptors = sift.detectAndCompute(screen_gray, None)

            matches = []
            for reference_path in image_list:
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

            if not matches:
                return None

            best = max(matches, key=lambda item: (item["inliers"], item["good"]))
            center_x, center_y = best["center"]

            pos = (
                int(round(center_x + (region[0] if region else 0))),
                int(round(center_y + (region[1] if region else 0))),
            )
            self.last_positions[best["reference_path"]] = pos
            self.app.log(
                f"[SIFTMatch] 命中: {best['reference_path']} | 内点: {best['inliers']}/{best['good']} "
                f"(阈值 {min_inliers}) | 估算缩放: {best['scale']:.3f}"
            )
            return pos

        except Exception as e:
            self.app.log(f"find_any_image_sift 异常: {e}")
            return None

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

    def find_race_result_table(self, region=None):
        if not self.app.state.is_running:
            return None

        try:
            screen_bgr = self.app.services.image_cache.capture_region(region)
            screen_h, screen_w = screen_bgr.shape[:2]
            if screen_h <= 0 or screen_w <= 0:
                return None

            hsv = cv2.cvtColor(screen_bgr, cv2.COLOR_BGR2HSV)
            lime_mask = cv2.inRange(
                hsv,
                np.array([35, 80, 120], dtype=np.uint8),
                np.array([90, 255, 255], dtype=np.uint8),
            )
            kernel = cv2.getStructuringElement(
                cv2.MORPH_RECT,
                (max(9, screen_w // 120), max(3, screen_h // 220)),
            )
            lime_mask = cv2.morphologyEx(lime_mask, cv2.MORPH_CLOSE, kernel)
            contours, _ = cv2.findContours(lime_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

            candidates = []
            for contour in contours:
                x, y, w, h = cv2.boundingRect(contour)
                if w < screen_w * 0.45:
                    continue
                if not (screen_h * 0.012 <= h <= screen_h * 0.08):
                    continue
                if not (screen_h * 0.12 <= y <= screen_h * 0.55):
                    continue

                roi_mask = lime_mask[y : y + h, x : x + w]
                density = cv2.countNonZero(roi_mask) / max(1, w * h)
                if density < 0.55:
                    continue

                table_y1 = min(screen_h, y + h + 4)
                table_y2 = min(screen_h, y + h + max(40, int(screen_h * 0.42)))
                table_x1 = max(0, x)
                table_x2 = min(screen_w, x + w)
                table_roi = screen_bgr[table_y1:table_y2, table_x1:table_x2]
                if table_roi.size == 0:
                    continue

                table_gray = cv2.cvtColor(table_roi, cv2.COLOR_BGR2GRAY)
                if float(table_gray.mean()) > 95:
                    continue

                candidates.append((x, y, w, h, density))

            if not candidates:
                return None

            x, y, w, h, density = max(candidates, key=lambda item: (item[2], item[4]))
            pos = (
                int(round(x + w / 2 + (region[0] if region else 0))),
                int(round(y + h / 2 + (region[1] if region else 0))),
            )
            self.app.log(
                f"[RaceResultTable] 命中结果页表格 | 宽:{w} 高:{h} | 亮色密度:{density:.3f}"
            )
            return pos

        except Exception as e:
            self.app.log(f"find_race_result_table 异常: {e}")
            return None


    # ==========================================
    # --- 【终极安全锁 V5.1】：排他 + 右下角调校精准狙击 + 强制从左到右 ---
    # ==========================================
