from __future__ import annotations

import gc
import os
import re
import threading
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

import cv2
import numpy as np
from rapidocr import RapidOCR

if TYPE_CHECKING:
    from ..backend.app import BackendApp


@dataclass(frozen=True)
class OcrText:
    text: str
    score: float
    box: tuple[tuple[float, float], ...] | None = None


Point = tuple[int, int]
Box = tuple[int, int, int, int]


@dataclass(slots=True)
class _TextMatch:
    target: str
    text: str
    score: float
    pos: Point
    box: Box | None = None
    ocr_box: Box | None = None
    region_name: str | None = None


class OcrService:
    """提供 OCR 引擎能力及当前画面的文字目标定位。"""

    def __init__(self, app: BackendApp) -> None:
        self.app = app
        self._providers: dict[str, list[str] | None] = {}
        self._dll_handles: list[Any] = []
        self._dll_dirs: set[str] = set()
        self._lock = threading.RLock()
        self._engine: RapidOCR | None = None
        self._text_targets_cache: dict[tuple[str, ...], list[tuple[str, str]]] = {}
        self.last_positions: dict[str, Point] = {}
        self._ensure_engine()

    def _add_nvidia_dll_paths(self) -> list[str]:
        try:
            import nvidia
        except Exception:
            return []

        dll_dirs = sorted({str(p.parent) for base in nvidia.__path__ for p in Path(base).rglob("*.dll")})
        if not dll_dirs:
            return []

        current_path = os.environ.get("PATH", "")
        os.environ["PATH"] = ";".join(dll_dirs + [current_path])
        for dll_dir in dll_dirs:
            if dll_dir in self._dll_dirs:
                continue
            try:
                handle = os.add_dll_directory(dll_dir)
                self._dll_handles.append(handle)
                self._dll_dirs.add(dll_dir)
            except Exception:
                pass
        return dll_dirs

    def _ensure_engine(self):
        with self._lock:
            if self._engine is not None:
                return self._engine

            self._add_nvidia_dll_paths()

            params = {
                "EngineConfig.onnxruntime.use_cuda": True,
                "Global.log_level": "warning",
            }
            self._engine = RapidOCR(params=params)
            self._providers = self._read_session_providers()
            provider_summary = ", ".join(
                f"{name}={providers or ['unknown']}" for name, providers in self._providers.items()
            )
            self.app.log(f"OCR 引擎已初始化，{provider_summary or 'providers=unknown'}。", level="debug")
            return self._engine

    def _read_session_providers(self) -> dict[str, list[str] | None]:
        providers: dict[str, list[str] | None] = {}
        if self._engine is None:
            return providers

        for name in ("text_det", "text_cls", "text_rec"):
            session = getattr(getattr(getattr(self._engine, name, None), "session", None), "session", None)
            providers[name] = session.get_providers() if session is not None else None
        return providers

    @property
    def providers(self) -> dict[str, list[str] | None]:
        with self._lock:
            if self._engine is None:
                return {}
            return dict(self._providers)

    def release(self) -> bool:
        with self._lock:
            if self._engine is None:
                return False

            self._engine = None
            self._providers = {}

        gc.collect()
        return True

    @staticmethod
    def normalize_text(text: str) -> str:
        normalized = text.upper()
        normalized = re.sub(r"[\s·・.\-_—:：/\\|]+", "", normalized)
        normalized = re.sub(r"[，,。!！?？（）()\[\]【】]+", "", normalized)
        return normalized

    @staticmethod
    def parse_credit_value(text: str) -> int | None:
        """从 OCR 文本中提取 CR 金额，优先解析“出售价格”之后的数字。"""
        normalized = unicodedata.normalize("NFKC", str(text)).upper()
        label = re.search(r"出售\s*价格|出售价|售\s*价格|价格", normalized)
        if label:
            normalized = normalized[label.end() :]
        elif "CR" in normalized:
            normalized = normalized[normalized.find("CR") + 2 :]
        else:
            return None

        match = re.search(r"(?:CR)?[^0-9]*([0-9][0-9\s,，.]*[0-9]|[0-9])", normalized)
        if not match:
            return None

        digits = re.sub(r"\D", "", match.group(1))
        return int(digits) if digits else None

    @staticmethod
    def _ocr_result_bounds(result: OcrText) -> Box | None:
        if result.box is None:
            return None
        xs = [float(point[0]) for point in result.box]
        ys = [float(point[1]) for point in result.box]
        if not xs or not ys:
            return None
        x1 = int(round(min(xs)))
        y1 = int(round(min(ys)))
        x2 = int(round(max(xs)))
        y2 = int(round(max(ys)))
        return (x1, y1, x2 - x1, y2 - y1)

    @staticmethod
    def _ocr_result_center(result: OcrText) -> tuple[float, float] | None:
        bounds = OcrService._ocr_result_bounds(result)
        if bounds is None:
            return None
        x, y, w, h = bounds
        return x + w / 2, y + h / 2

    @staticmethod
    def _number_candidates_from_text(text: str) -> list[tuple[int, bool]]:
        normalized = unicodedata.normalize("NFKC", str(text)).upper()
        candidates: list[tuple[int, bool]] = []
        for match in re.finditer(r"[0-9][0-9\s,，.]*[0-9]|[0-9]", normalized):
            raw = match.group(0)
            digits = re.sub(r"\D", "", raw)
            if not digits:
                continue

            has_separator = bool(re.search(r"[\s,，.]", raw))
            if len(digits) < 4:
                continue
            if len(digits) < 5 and not has_separator:
                continue

            value = int(digits)
            if value < 1000:
                continue
            candidates.append((value, has_separator))
        return candidates

    @classmethod
    def parse_current_credit_value(cls, results: list[OcrText] | tuple[OcrText, ...], threshold=0.25) -> int | None:
        """从右上角 OCR 结果中提取玩家当前 CR 余额。

        余额区域里 RapidOCR 有时能识别出独立的“CR”，有时只识别出数字。
        因此这里不要求数字文本必须包含 CR，而是优先选择：
        1. 与 CR 文本在同一行、位于 CR 右侧的数字；
        2. 带千分位分隔符的长数字；
        3. 纯数字长数字。
        """
        filtered = [result for result in results if result is not None and result.score >= threshold]
        if not filtered:
            return None

        combined_text = "".join(result.text for result in filtered)
        value_after_cr = cls.parse_credit_value(combined_text)
        if value_after_cr is not None:
            return value_after_cr

        cr_centers = [
            center
            for result in filtered
            if "CR" in unicodedata.normalize("NFKC", result.text).upper()
            for center in [cls._ocr_result_center(result)]
            if center is not None
        ]

        best: tuple[float, int] | None = None
        for result in filtered:
            text = unicodedata.normalize("NFKC", result.text).upper()
            alpha_without_cr = re.sub(r"CR", "", text)
            has_unrelated_alpha = bool(re.search(r"[A-Z]", alpha_without_cr))

            for value, has_separator in cls._number_candidates_from_text(text):
                if has_unrelated_alpha and not has_separator:
                    continue

                score = float(result.score)
                digit_count = len(str(value))
                candidate_score = score + digit_count * 0.08 + (0.35 if has_separator else 0.0)

                center = cls._ocr_result_center(result)
                if center is not None:
                    cx, cy = center
                    for cr_x, cr_y in cr_centers:
                        if cx >= cr_x - 8 and abs(cy - cr_y) <= 24:
                            candidate_score += 1.0
                            break

                if best is None or candidate_score > best[0]:
                    best = (candidate_score, value)

        return best[1] if best is not None else None

    @staticmethod
    def _small_integer_candidates_from_text(text: str) -> list[int]:
        normalized = unicodedata.normalize("NFKC", str(text))
        candidates = []
        for match in re.finditer(r"\d{1,3}", normalized):
            value = int(match.group(0))
            if 0 <= value <= 999:
                candidates.append(value)
        return candidates

    @classmethod
    def parse_current_skill_points_value(
        cls,
        results: list[OcrText] | tuple[OcrText, ...],
        threshold=0.25,
    ) -> int | None:
        """从车辆菜单页 OCR 结果中提取可用技术点数。"""
        filtered = [result for result in results if result is not None and result.score >= threshold]
        if not filtered:
            return None

        label_results = [
            result
            for result in filtered
            if "技术点" in unicodedata.normalize("NFKC", result.text)
            or "技能点" in unicodedata.normalize("NFKC", result.text)
        ]

        for result in label_results:
            values = cls._small_integer_candidates_from_text(result.text)
            if values:
                return values[0]

        best: tuple[float, int] | None = None
        for label_result in label_results:
            label_center = cls._ocr_result_center(label_result)
            if label_center is None:
                continue
            label_x, label_y = label_center

            for result in filtered:
                if result is label_result:
                    continue
                center = cls._ocr_result_center(result)
                if center is None:
                    continue
                value_x, value_y = center
                if abs(value_y - label_y) > 28 or value_x > label_x + 8:
                    continue

                for value in cls._small_integer_candidates_from_text(result.text):
                    candidate_score = float(result.score) - abs(value_y - label_y) * 0.01
                    if best is None or candidate_score > best[0]:
                        best = (candidate_score, value)

        if best is not None:
            return best[1]

        if len(filtered) <= 2:
            for result in filtered:
                values = cls._small_integer_candidates_from_text(result.text)
                if values:
                    return values[0]

        return None

    def read(self, img: np.ndarray | str | Path, *, use_det=True, use_cls=True, text_score=0.5) -> list[OcrText]:
        with self._lock:
            engine = self._ensure_engine()
            result = engine(img, use_det=use_det, use_cls=use_cls, text_score=text_score)

            raw_texts = getattr(result, "txts", None)
            raw_scores = getattr(result, "scores", None)
            raw_boxes = getattr(result, "boxes", None)
            texts = list(raw_texts) if raw_texts is not None else []
            scores = list(raw_scores) if raw_scores is not None else []
            boxes = list(raw_boxes) if raw_boxes is not None else []

            output: list[OcrText] = []
            for idx, text in enumerate(texts):
                score = float(scores[idx]) if idx < len(scores) else 0.0
                box = None
                if idx < len(boxes):
                    box = tuple((float(x), float(y)) for x, y in boxes[idx])
                output.append(OcrText(str(text), score, box))
            return output

    def recognize_line(self, img: np.ndarray | str | Path, *, min_score=0.5) -> OcrText | None:
        results = self.read(img, use_det=False, use_cls=False, text_score=min_score)
        if not results:
            return None
        best = max(results, key=lambda item: item.score)
        return best if best.score >= min_score else None

    def recognize_cell_text(self, cell_bgr: np.ndarray, *, min_score=0.5) -> OcrText | None:
        if cell_bgr is None or cell_bgr.size == 0:
            return None

        return self.recognize_line(cell_bgr, min_score=min_score)

    @staticmethod
    def _region_offset(region) -> Point:
        return (int(region[0]), int(region[1])) if region else (0, 0)

    @classmethod
    def _box_center(cls, box: Box, region=None) -> Point:
        x, y, w, h = box
        offset_x, offset_y = cls._region_offset(region)
        return (int(round(x + w / 2 + offset_x)), int(round(y + h / 2 + offset_y)))

    @staticmethod
    def _point_bounds(points, *, offset_x=0, offset_y=0) -> Box | None:
        if not points:
            return None
        xs = [float(point[0]) + offset_x for point in points]
        ys = [float(point[1]) + offset_y for point in points]
        return (
            int(round(min(xs))),
            int(round(min(ys))),
            int(round(max(xs))),
            int(round(max(ys))),
        )

    @staticmethod
    def _bounds_center(bounds: Box) -> Point:
        x1, y1, x2, y2 = bounds
        return ((x1 + x2) // 2, (y1 + y2) // 2)

    @staticmethod
    def _expand_box(box: Box, pad_x: int, pad_y: int, max_width: int, max_height: int) -> Box:
        x, y, w, h = box
        x1 = max(0, x - pad_x)
        y1 = max(0, y - pad_y)
        x2 = min(max_width, x + w + pad_x)
        y2 = min(max_height, y + h + pad_y)
        return (x1, y1, x2 - x1, y2 - y1)

    @staticmethod
    def _merge_overlapping_boxes(boxes, tolerance=8):
        merged = []
        for box in sorted(boxes, key=lambda item: (item[1], item[0], -item[2] * item[3])):
            x, y, w, h = box
            x2 = x + w
            y2 = y + h
            duplicate = False
            for idx, (mx, my, mw, mh) in enumerate(merged):
                mx2 = mx + mw
                my2 = my + mh
                ix1 = max(x, mx)
                iy1 = max(y, my)
                ix2 = min(x2, mx2)
                iy2 = min(y2, my2)
                if ix2 <= ix1 or iy2 <= iy1:
                    continue
                intersection = (ix2 - ix1) * (iy2 - iy1)
                area = min(w * h, mw * mh)
                if intersection >= max(1, area) * 0.65:
                    nx1 = min(x, mx)
                    ny1 = min(y, my)
                    nx2 = max(x2, mx2)
                    ny2 = max(y2, my2)
                    merged[idx] = (nx1, ny1, nx2 - nx1, ny2 - ny1)
                    duplicate = True
                    break
            if not duplicate:
                merged.append((x, y, w, h))

        return [(x, y, w, h) for x, y, w, h in merged if w > tolerance and h > tolerance]

    def _finalize_candidate_boxes(self, boxes, max_candidates):
        boxes = self._merge_overlapping_boxes(boxes)
        boxes.sort(key=lambda item: (item[1], item[0], item[2] * item[3]))
        return boxes[: int(max_candidates)]

    def _build_text_targets(self, text_list):
        if isinstance(text_list, str):
            text_list = [text_list]

        cache_key = tuple(str(text) for text in text_list)
        if cache_key in self._text_targets_cache:
            return self._text_targets_cache[cache_key]

        targets = []
        for target_text in cache_key:
            target_norm = self.normalize_text(target_text)
            if len(target_norm) < 2 or "?" in target_norm:
                self.app.log(f"[TextOCR] 跳过目标文字 {target_text}：内容不可用。", level="debug")
                continue
            targets.append((target_text, target_norm))
        self._text_targets_cache[cache_key] = targets
        return targets

    def _score_text_match(
        self,
        candidate_text,
        candidate_score,
        target_norm,
        threshold,
        *,
        allow_candidate_subset=True,
        coverage_floor=0.65,
    ):
        candidate_norm = self.normalize_text(candidate_text)
        if len(candidate_norm) < 2:
            return None

        exact = candidate_norm == target_norm
        partial = len(target_norm) >= 3 and (
            target_norm in candidate_norm or (allow_candidate_subset and candidate_norm in target_norm)
        )
        if not exact and not partial:
            return None

        if candidate_score < max(0.35, threshold - 0.20):
            return None

        coverage = min(len(candidate_norm), len(target_norm)) / max(len(candidate_norm), len(target_norm))
        score = float(candidate_score) * (1.0 if exact else 0.88) * max(coverage_floor, coverage)
        return score if score >= threshold else None

    def _match_text_candidate(
        self,
        text: str,
        candidate_score: float,
        targets: list[tuple[str, str]],
        threshold: float,
        *,
        pos: Point,
        box: Box | None = None,
        ocr_box: Box | None = None,
        region_name: str | None = None,
        allow_candidate_subset: bool = True,
        coverage_floor: float = 0.65,
    ) -> _TextMatch | None:
        best = None
        for target_text, target_norm in targets:
            score = self._score_text_match(
                text,
                candidate_score,
                target_norm,
                threshold,
                allow_candidate_subset=allow_candidate_subset,
                coverage_floor=coverage_floor,
            )
            if score is None:
                continue

            candidate = _TextMatch(
                target=target_text,
                text=text,
                score=score,
                pos=pos,
                box=box,
                ocr_box=ocr_box,
                region_name=region_name,
            )
            if best is None or candidate.score > best.score:
                best = candidate
        return best

    @staticmethod
    def _better_match(current: _TextMatch | None, candidate: _TextMatch | None) -> _TextMatch | None:
        if candidate is None:
            return current
        if current is None or candidate.score > current.score:
            return candidate
        return current

    def _remember_text_match(self, match: _TextMatch) -> Point:
        self.last_positions[match.target] = match.pos
        return match.pos

    def _find_text_line_candidate_boxes(self, screen_bgr, max_candidates=60):
        """用文字边缘图聚合候选文字行，供 OCR 单行识别。"""
        text_map = self.app.services.image_cache.to_text_ui_image(screen_bgr)
        if text_map is None:
            return []

        screen_h, screen_w = text_map.shape[:2]
        component_count, labels, stats, _ = cv2.connectedComponentsWithStats(text_map, 8)
        clean = np.zeros_like(text_map)
        screen_area = screen_w * screen_h
        for idx in range(1, component_count):
            x, y, w, h, area = stats[idx]
            if area < 5 or area > screen_area * 0.01:
                continue
            if w < 2 or h < 4:
                continue
            if w > screen_w * 0.35 or h > screen_h * 0.18:
                continue
            density = area / max(1, w * h)
            if density > 0.75:
                continue
            clean[labels == idx] = 255

        if np.count_nonzero(clean) < 8:
            return []

        horizontal = max(10, int(round(screen_w * 0.012)))
        vertical = max(3, int(round(screen_h * 0.004)))
        kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (horizontal, vertical))
        grouped = cv2.dilate(clean, kernel, iterations=1)
        grouped = cv2.morphologyEx(grouped, cv2.MORPH_CLOSE, kernel)

        contours, _ = cv2.findContours(grouped, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        boxes = []
        for contour in contours:
            x, y, w, h = cv2.boundingRect(contour)
            if w < max(24, screen_w * 0.015) or h < max(10, screen_h * 0.010):
                continue
            if w > screen_w * 0.85 or h > screen_h * 0.24:
                continue
            if w / max(1, h) < 1.1:
                continue

            area = cv2.contourArea(contour)
            if area < max(20, w * h * 0.03):
                continue

            pad_x = max(8, int(round(w * 0.10)))
            pad_y = max(6, int(round(h * 0.55)))
            boxes.append(self._expand_box((x, y, w, h), pad_x, pad_y, screen_w, screen_h))

        return self._finalize_candidate_boxes(boxes, max_candidates)

    def find_any_text_ui(self, text_list, region=None, threshold=0.65):
        """在当前画面中通过 OCR 定位任意目标文字。"""
        if not self.app.state.is_running:
            return None
        try:
            targets = self._build_text_targets(text_list)
            if not targets:
                return None

            screen_bgr = self.app.services.image_cache.capture_region(region)
            boxes = self._find_text_line_candidate_boxes(screen_bgr)
            if not boxes:
                screen_h, screen_w = screen_bgr.shape[:2]
                if screen_w <= 900 and screen_h <= 420:
                    boxes = [(0, 0, screen_w, screen_h)]

            best = None
            for x, y, w, h in boxes:
                box = (x, y, w, h)
                roi = screen_bgr[y : y + h, x : x + w]
                if roi.size == 0:
                    continue
                line_result = self.recognize_line(roi, min_score=0.35)
                results = [line_result] if line_result is not None else self.read(roi, text_score=0.35)
                for result in results:
                    candidate = self._match_text_candidate(
                        result.text,
                        result.score,
                        targets,
                        threshold,
                        pos=self._box_center(box, region),
                        box=box,
                    )
                    best = self._better_match(best, candidate)

            if best is None:
                offset_x, offset_y = self._region_offset(region)
                for result in self.read(screen_bgr, text_score=0.35):
                    bounds = self._point_bounds(result.box)
                    if bounds is None:
                        continue
                    x1, y1, x2, y2 = bounds
                    center_x, center_y = self._bounds_center(bounds)
                    candidate = self._match_text_candidate(
                        result.text,
                        result.score,
                        targets,
                        threshold,
                        pos=(center_x + offset_x, center_y + offset_y),
                        box=(x1, y1, x2 - x1, y2 - y1),
                    )
                    best = self._better_match(best, candidate)

            if best is not None and best.box is not None:
                x, y, w, h = best.box
                self.app.log(
                    f"[TextOCR] 命中: {best.text} "
                    f"(目标:{best.target}) | 分数:{best.score:.3f} "
                    f"(阈值 {threshold}) | 候选框: x={x}, y={y}, w={w}, h={h}",
                    level="debug",
                )
                return self._remember_text_match(best)

            return None
        except Exception as e:
            self.app.log(f"find_any_text_ui 异常: {e}", level="warning")
            return None

    def find_sell_price_value(self, region=None, threshold=0.25) -> int | None:
        """识别重复车辆弹窗里的“出售价格：CR x”。"""
        if not self.app.state.is_running:
            return None
        try:
            screen_bgr = self.app.services.image_cache.capture_region(region)
            if screen_bgr.size == 0:
                return None

            results = sorted(
                (result for result in self.read(screen_bgr, text_score=threshold) if result.score >= threshold),
                key=lambda result: (
                    min((point[1] for point in result.box), default=0) if result.box else 0,
                    min((point[0] for point in result.box), default=0) if result.box else 0,
                ),
            )
            combined_text = "".join(result.text for result in results)
            value = self.parse_credit_value(combined_text)
            if value is not None:
                self.app.log(f"[PriceOCR] 出售价格: CR {value:,} | OCR: {combined_text}", level="debug")
            return value
        except Exception as e:
            self.app.log(f"find_sell_price_value 异常: {e}", level="warning")
            return None

    def _default_credit_region(self):
        full_region = self.app.services.game_window.regions.get("全界面")
        if full_region is None:
            return None

        x, y, w, h = map(int, full_region)
        return (
            x + int(w * 0.68),
            y + int(h * 0.08),
            max(1, int(w * 0.22)),
            max(1, int(h * 0.12)),
        )

    def find_current_credit_value(self, region=None, threshold=0.25) -> int | None:
        """识别当前界面右上角的玩家 CR 余额。"""
        if not self.app.state.is_running:
            return None
        try:
            credit_region = region or self._default_credit_region()
            screen_bgr = self.app.services.image_cache.capture_region(credit_region)
            if screen_bgr.size == 0:
                return None

            results = self.read(screen_bgr, text_score=threshold)
            value = self.parse_current_credit_value(results, threshold=threshold)
            if value is not None:
                combined_text = "".join(result.text for result in results if result.score >= threshold)
                self.app.log(f"[CreditOCR] 当前 CR: {value:,} | OCR: {combined_text}", level="debug")
            return value
        except Exception as e:
            self.app.log(f"find_current_credit_value 异常: {e}", level="warning")
            return None

    def _default_skill_points_region(self):
        full_region = self.app.services.game_window.regions.get("全界面")
        if full_region is None:
            return None

        x, y, w, h = map(int, full_region)
        return (
            x + int(w * 0.27),
            y + int(h * 0.70),
            max(1, int(w * 0.26)),
            max(1, int(h * 0.10)),
        )

    def find_current_skill_points_value(self, region=None, threshold=0.25) -> int | None:
        """识别车辆菜单页中“技术点数可用”的数值。"""
        if not self.app.state.is_running:
            return None
        try:
            skill_region = region or self._default_skill_points_region()
            screen_bgr = self.app.services.image_cache.capture_region(skill_region)
            if screen_bgr.size == 0:
                return None

            results = self.read(screen_bgr, text_score=threshold)
            value = self.parse_current_skill_points_value(results, threshold=threshold)
            if value is not None:
                combined_text = "".join(result.text for result in results if result.score >= threshold)
                self.app.log(f"[SkillPointOCR] 当前技术点: {value} | OCR: {combined_text}", level="debug")
            return value
        except Exception as e:
            self.app.log(f"find_current_skill_points_value 异常: {e}", level="warning")
            return None

    def _find_menu_button_candidate_boxes(self, screen_bgr, max_candidates=16):
        """用按钮背景/选中边框定位左侧菜单行，再交给 OCR 识别文字。"""
        if screen_bgr is None or screen_bgr.size == 0:
            return []

        screen_h, screen_w = screen_bgr.shape[:2]
        hsv = cv2.cvtColor(screen_bgr, cv2.COLOR_BGR2HSV)

        white_mask = cv2.inRange(
            hsv,
            np.array([0, 0, 150], dtype=np.uint8),
            np.array([180, 90, 255], dtype=np.uint8),
        )
        selected_border_mask = cv2.inRange(
            hsv,
            np.array([35, 80, 120], dtype=np.uint8),
            np.array([90, 255, 255], dtype=np.uint8),
        )

        boxes = []
        for mask in (white_mask, selected_border_mask):
            # 只修复水平缺口，避免相邻菜单行在纵向粘连。
            kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (max(3, screen_w // 160), 1))
            mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)
            contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

            for contour in contours:
                x, y, w, h = cv2.boundingRect(contour)
                if w < max(80, int(screen_w * 0.14)):
                    continue
                if h < max(18, int(screen_h * 0.045)) or h > max(60, int(screen_h * 0.18)):
                    continue
                if w > int(screen_w * 0.70) or x > int(screen_w * 0.58):
                    continue
                if y > int(screen_h * 0.86):
                    continue
                if w / max(1, h) < 3.0:
                    continue

                area_ratio = cv2.contourArea(contour) / max(1, w * h)
                if area_ratio < 0.25:
                    continue

                pad_x = max(4, int(round(w * 0.02)))
                pad_y = max(3, int(round(h * 0.12)))
                boxes.append(self._expand_box((x, y, w, h), pad_x, pad_y, screen_w, screen_h))

        return self._finalize_candidate_boxes(boxes, max_candidates)

    def find_menu_text_ui(self, target_text, region=None, threshold=0.65):
        """在当前画面的规则菜单行中定位目标文字。"""
        if not self.app.state.is_running:
            return None
        try:
            targets = self._build_text_targets([target_text])
            if not targets:
                return None

            screen_bgr = self.app.services.image_cache.capture_region(region)
            boxes = self._find_menu_button_candidate_boxes(screen_bgr)
            best = None
            region_x, region_y = self._region_offset(region)

            def consider_result(result, box, *, require_ocr_box=False):
                nonlocal best
                x, y, _, _ = box
                ocr_box = self._point_bounds(result.box, offset_x=x + region_x, offset_y=y + region_y)
                if ocr_box is None and require_ocr_box:
                    return False

                pos = self._bounds_center(ocr_box) if ocr_box else self._box_center(box, region)
                candidate = self._match_text_candidate(
                    result.text,
                    result.score,
                    targets,
                    threshold,
                    pos=pos,
                    box=box,
                    ocr_box=ocr_box,
                )
                best = self._better_match(best, candidate)
                return candidate is not None

            for x, y, w, h in boxes:
                box = (x, y, w, h)
                roi = screen_bgr[y : y + h, x : x + w]
                if roi.size == 0:
                    continue

                candidate_too_tall = h > max(90, int(screen_bgr.shape[0] * 0.12))
                if candidate_too_tall:
                    for result in self.read(roi, text_score=0.25):
                        consider_result(result, box, require_ocr_box=True)
                    continue

                matched = False
                line_result = self.recognize_line(roi, min_score=0.25)
                if line_result is not None:
                    matched = consider_result(line_result, box)

                if not matched:
                    for result in self.read(roi, text_score=0.25):
                        consider_result(result, box)

            if best is not None and best.box is not None:
                x, y, w, h = best.box
                ocr_box_text = ""
                if best.ocr_box:
                    ox1, oy1, ox2, oy2 = best.ocr_box
                    ocr_box_text = f" | OCR框: x1={ox1}, y1={oy1}, x2={ox2}, y2={oy2}"
                self.app.log(
                    f"[MenuOCR] 命中: {best.text} "
                    f"(目标:{best.target}) | 分数:{best.score:.3f} "
                    f"(阈值 {threshold}) | 候选框: x={x}, y={y}, w={w}, h={h}{ocr_box_text}",
                    level="debug",
                )
                return self._remember_text_match(best)

            return None
        except Exception as e:
            self.app.log(f"find_menu_text_ui 异常: {e}", level="warning")
            return None

    def _footer_text_regions(self, region):
        if region is None:
            full_region = self.app.services.game_window.regions.get("全界面")
            if full_region is None:
                screen_bgr = self.app.services.image_cache.capture_region(None)
                h, w = screen_bgr.shape[:2]
                region = (0, 0, w, h)
            else:
                region = full_region

        sx, sy, sw, sh = map(int, region)
        bottom_y = sy + int(sh * 0.80)
        bottom_h = max(1, sh - int(sh * 0.80))
        return [("底部提示栏", (sx, bottom_y, sw, bottom_h))]

    def find_footer_text_ui(self, target_text, region=None, threshold=0.65):
        """在当前画面的底部按键提示栏中定位目标文字。"""
        if not self.app.state.is_running:
            return None
        try:
            targets = self._build_text_targets([target_text])
            if not targets:
                return None

            for roi_name, roi in self._footer_text_regions(region):
                rx, ry, rw, rh = roi
                roi_bgr = self.app.services.image_cache.capture_region(roi)
                if roi_bgr.size == 0:
                    continue

                results = self.read(roi_bgr, text_score=max(0.25, threshold - 0.45))
                best = None
                fallback_pos = (int(rx + rw / 2), int(ry + rh / 2))
                for result in results:
                    if result is None:
                        continue

                    candidate_norm = self.normalize_text(result.text)
                    if not candidate_norm:
                        continue

                    score = float(result.score)
                    if score < threshold:
                        continue

                    box = self._point_bounds(result.box, offset_x=rx, offset_y=ry) if result.box else None
                    pos = self._bounds_center(box) if box is not None else fallback_pos
                    for target_text, target_norm in targets:
                        if target_norm not in candidate_norm:
                            continue

                        candidate = _TextMatch(
                            target=target_text,
                            text=result.text,
                            score=score,
                            pos=pos,
                            ocr_box=box,
                            region_name=roi_name,
                        )
                        best = self._better_match(best, candidate)

                if best is not None:
                    box_text = f" | OCR框: {best.ocr_box}" if best.ocr_box else ""
                    self.app.log(
                        f"[FooterOCR] 命中: {best.text} "
                        f"(目标:{best.target}) | 分数:{best.score:.3f} "
                        f"(阈值 {threshold}) | 区域:{best.region_name}{box_text}",
                        level="debug",
                    )
                    return self._remember_text_match(best)

            return None
        except Exception as e:
            self.app.log(f"find_footer_text_ui 异常: {e}", level="warning")
            return None

    @staticmethod
    def _cluster_axis_positions(values, tolerance):
        clusters = []
        for value in sorted(int(v) for v in values):
            if not clusters or abs(value - clusters[-1][-1]) > tolerance:
                clusters.append([value])
            else:
                clusters[-1].append(value)
        return [int(round(sum(cluster) / len(cluster))) for cluster in clusters]

    def _find_manufacturer_cells(self, screen_bgr):
        h, w = screen_bgr.shape[:2]
        gray = cv2.cvtColor(screen_bgr, cv2.COLOR_BGR2GRAY)
        _, mask = cv2.threshold(gray, 234, 255, cv2.THRESH_BINARY)
        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

        raw_cells = []
        min_w = max(80, int(w * 0.10))
        max_w = int(w * 0.30)
        min_h = max(24, int(h * 0.025))
        max_h = int(h * 0.08)
        for contour in contours:
            x, y, cell_w, cell_h = cv2.boundingRect(contour)
            if cell_w < min_w or cell_w > max_w or cell_h < min_h or cell_h > max_h:
                continue
            if y < int(h * 0.18) or y > int(h * 0.92):
                continue
            area_ratio = cv2.contourArea(contour) / float(cell_w * cell_h)
            if area_ratio < 0.72:
                continue
            raw_cells.append((x, y, cell_w, cell_h))

        if len(raw_cells) < 4:
            return raw_cells

        median_w = int(round(np.median([cell[2] for cell in raw_cells])))
        median_h = int(round(np.median([cell[3] for cell in raw_cells])))
        x_positions = self._cluster_axis_positions([cell[0] for cell in raw_cells], max(12, median_w // 4))
        y_positions = self._cluster_axis_positions([cell[1] for cell in raw_cells], max(8, median_h // 2))

        if len(x_positions) < 2 or len(y_positions) < 2:
            return raw_cells

        grid_cells = []
        seen = set()
        for y in y_positions:
            for x in x_positions:
                cell = (
                    max(0, x),
                    max(0, y),
                    min(median_w, w - x),
                    min(median_h, h - y),
                )
                key = (cell[0] // 4, cell[1] // 4)
                if cell[2] > 0 and cell[3] > 0 and key not in seen:
                    seen.add(key)
                    grid_cells.append(cell)

        return grid_cells or raw_cells

    def find_manufacturer_text(self, target_text, region=None, threshold=0.75):
        """在当前画面的制造商表格中定位目标文字。"""
        if not self.app.state.is_running or not target_text:
            return None
        try:
            screen_bgr = self.app.services.image_cache.capture_region(region)
            target_norm = self.normalize_text(target_text)

            for cell_box in self._find_manufacturer_cells(screen_bgr):
                x, y, cell_w, cell_h = cell_box
                cell = screen_bgr[y : y + cell_h, x : x + cell_w]
                result = self.recognize_cell_text(cell, min_score=0.3)
                if result is None:
                    continue

                if self.normalize_text(result.text) != target_norm or result.score < threshold:
                    continue

                pos = self._box_center(cell_box, region)
                self.last_positions[target_text] = pos
                self.app.log(
                    f"[ManufacturerOCR] 命中: {result.text} (目标:{target_text}) | 分数:{result.score:.3f} "
                    f"(阈值 {threshold}) | 单元格: x={x}, y={y}, w={cell_w}, h={cell_h}",
                    level="debug",
                )
                return pos

            return None
        except Exception as e:
            self.app.log(f"find_manufacturer_text 异常: {e}", level="warning")
            return None
