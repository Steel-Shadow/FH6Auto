from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import time

import cv2
import numpy as np

from ..backend.state import RuntimeState
from .cache import Box, ImageCacheService, Point
from .ocr import OcrService, OcrText
from .timing import VisionTimingMixin


@dataclass(slots=True)
class _TextMatch:
    target: str
    text: str
    score: float
    pos: Point
    box: Box | None = None
    ocr_box: Box | None = None
    region_name: str | None = None


class TextDetector(VisionTimingMixin):
    """基于 OCR 引擎定位当前 UI 中的通用文字和规则菜单文字。"""

    TIMING_NAME = "Text"

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
        self._text_targets_cache: dict[tuple[str, ...], list[tuple[str, str]]] = {}
        self.last_positions: dict[str, Point] = {}

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

    def _build_targets(self, text_list):
        if isinstance(text_list, str):
            text_list = [text_list]

        cache_key = tuple(str(text) for text in text_list)
        if cache_key in self._text_targets_cache:
            return self._text_targets_cache[cache_key]

        targets = []
        for target_text in cache_key:
            target_norm = self.ocr.normalize_text(target_text)
            if len(target_norm) < 2 or "?" in target_norm:
                self.log(f"[TextOCR] 跳过目标文字 {target_text}：内容不可用。", level="debug")
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
        candidate_norm = self.ocr.normalize_text(candidate_text)
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
        text_map = self.image_cache.to_text_ui_image(screen_bgr)
        if text_map is None:
            return []

        screen_h, screen_w = text_map.shape[:2]
        component_count, labels, stats, _ = cv2.connectedComponentsWithStats(text_map, connectivity=8)
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
        if not self.state.is_running:
            return None
        started = time.perf_counter()
        capture_ms = None
        boxes_count = 0
        result_text = "miss"
        try:
            targets = self._build_targets(text_list)
            if not targets:
                result_text = "no_targets"
                return None

            capture_started = time.perf_counter()
            frame = self.image_cache.capture_frame(region)
            capture_ms = self._elapsed_ms(capture_started)
            screen_bgr = frame.image
            boxes = self._find_text_line_candidate_boxes(screen_bgr)
            if not boxes:
                screen_h, screen_w = screen_bgr.shape[:2]
                if screen_w <= 900 and screen_h <= 420:
                    boxes = [(0, 0, screen_w, screen_h)]
            boxes_count = len(boxes)

            best = None
            for x, y, w, h in boxes:
                box = (x, y, w, h)
                roi = screen_bgr[y : y + h, x : x + w]
                if roi.size == 0:
                    continue
                line_result = self.ocr.recognize_line(roi, min_score=0.35)
                results = [line_result] if line_result is not None else self.ocr.read(roi, text_score=0.35)
                for result in results:
                    candidate = self._match_text_candidate(
                        result.text,
                        result.score,
                        targets,
                        threshold,
                        pos=frame.box_center(box),
                        box=box,
                    )
                    best = self._better_match(best, candidate)

            if best is None:
                offset_x, offset_y = frame.origin
                for result in self.ocr.read(screen_bgr, text_score=0.35):
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
                self.log(
                    f"[TextOCR] 命中: {best.text} "
                    f"(目标:{best.target}) | 分数:{best.score:.3f} "
                    f"(阈值 {threshold}) | 候选框: x={x}, y={y}, w={w}, h={h}",
                    level="debug",
                )
                result_text = "hit"
                return self._remember_text_match(best)

            return None
        except Exception as e:
            result_text = "error"
            self.log(f"find_any_text_ui 异常: {e}", level="warning")
            return None
        finally:
            self._log_timing(
                "find_any_text_ui",
                started,
                capture_ms=capture_ms,
                boxes=boxes_count,
                result=result_text,
            )

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
        if not self.state.is_running:
            return None
        started = time.perf_counter()
        capture_ms = None
        boxes_count = 0
        result_text = "miss"
        try:
            targets = self._build_targets([target_text])
            if not targets:
                result_text = "no_targets"
                return None

            capture_started = time.perf_counter()
            frame = self.image_cache.capture_frame(region)
            capture_ms = self._elapsed_ms(capture_started)
            screen_bgr = frame.image
            boxes = self._find_menu_button_candidate_boxes(screen_bgr)
            boxes_count = len(boxes)
            best: _TextMatch | None = None
            region_x, region_y = frame.origin

            def consider_result(result: OcrText, box: Box, *, require_ocr_box=False) -> bool:
                nonlocal best
                x, y, _, _ = box
                ocr_box = self._point_bounds(result.box, offset_x=x + region_x, offset_y=y + region_y)
                if ocr_box is None and require_ocr_box:
                    return False

                pos = self._bounds_center(ocr_box) if ocr_box else frame.box_center(box)
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
                    ocr_results: list[OcrText] = self.ocr.read(roi, text_score=0.25)
                    for result in ocr_results:
                        consider_result(result, box, require_ocr_box=True)
                    continue

                matched = False
                line_result = self.ocr.recognize_line(roi, min_score=0.25)
                if line_result is not None:
                    matched = consider_result(line_result, box)

                if not matched:
                    ocr_results = self.ocr.read(roi, text_score=0.25)
                    for result in ocr_results:
                        consider_result(result, box)

            if best is not None and best.box is not None:
                x, y, w, h = best.box
                ocr_box_text = ""
                if best.ocr_box:
                    ox1, oy1, ox2, oy2 = best.ocr_box
                    ocr_box_text = f" | OCR框: x1={ox1}, y1={oy1}, x2={ox2}, y2={oy2}"
                self.log(
                    f"[MenuOCR] 命中: {best.text} "
                    f"(目标:{best.target}) | 分数:{best.score:.3f} "
                    f"(阈值 {threshold}) | 候选框: x={x}, y={y}, w={w}, h={h}{ocr_box_text}",
                    level="debug",
                )
                result_text = "hit"
                return self._remember_text_match(best)

            return None
        except Exception as e:
            result_text = "error"
            self.log(f"find_menu_text_ui 异常: {e}", level="warning")
            return None
        finally:
            self._log_timing(
                "find_menu_text_ui",
                started,
                capture_ms=capture_ms,
                boxes=boxes_count,
                result=result_text,
            )
