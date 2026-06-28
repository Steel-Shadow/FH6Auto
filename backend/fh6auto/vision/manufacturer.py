from __future__ import annotations

import time
from collections.abc import Callable

import cv2
import numpy as np

from ..backend.config_service import BackendConfigService
from ..backend.state import RuntimeState
from ..input.actions import InputActionsService
from .cache import ImageCacheService
from .ocr import OcrService, OcrText
from .polling import PollingWaiter
from .timing import VisionTimingMixin

Box = tuple[int, int, int, int]


class ManufacturerDetector(VisionTimingMixin):
    """Manufacturer table detection and scrolling search."""

    TIMING_NAME = "Manufacturer"

    def __init__(
        self,
        *,
        state: RuntimeState,
        image_cache: ImageCacheService,
        ocr: OcrService,
        input_actions: InputActionsService,
        config: BackendConfigService,
        log: Callable[..., None],
    ) -> None:
        self.state = state
        self.image_cache = image_cache
        self.ocr = ocr
        self.input_actions = input_actions
        self.config = config
        self.log = log
        self.last_positions: dict[str, tuple[int, int]] = {}
        self.polling = PollingWaiter(lambda: bool(self.state.is_running))

    @staticmethod
    def _cluster_axis_positions(values, tolerance):
        clusters = []
        for value in sorted(int(v) for v in values):
            if not clusters or abs(value - clusters[-1][-1]) > tolerance:
                clusters.append([value])
            else:
                clusters[-1].append(value)
        return [int(round(sum(cluster) / len(cluster))) for cluster in clusters]

    def _find_header_box(self, screen_bgr) -> Box | None:
        h, w = screen_bgr.shape[:2]
        hsv = cv2.cvtColor(screen_bgr, cv2.COLOR_BGR2HSV)
        mask = cv2.inRange(
            hsv,
            np.array([35, 80, 150], dtype=np.uint8),
            np.array([90, 255, 255], dtype=np.uint8),
        )
        mask[: int(h * 0.10), :] = 0
        mask[int(h * 0.42) :, :] = 0

        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        candidates = []
        for contour in contours:
            x, y, box_w, box_h = cv2.boundingRect(contour)
            if box_w < int(w * 0.45):
                continue
            if box_h < int(h * 0.035) or box_h > int(h * 0.09):
                continue

            area_ratio = cv2.contourArea(contour) / float(box_w * box_h)
            if area_ratio < 0.75:
                continue
            candidates.append((box_w * box_h, x, y, box_w, box_h))

        if not candidates:
            return None

        _, x, y, box_w, box_h = max(candidates, key=lambda item: item[0])
        return (x, y, box_w, box_h)

    def _find_white_cells(self, screen_bgr):
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

        return raw_cells

    def _grid_from_cells(self, screen_bgr, raw_cells):
        if len(raw_cells) < 4:
            return raw_cells

        h, w = screen_bgr.shape[:2]
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

    def _grid_bottom(self, screen_bgr, header_box: Box) -> int | None:
        h, w = screen_bgr.shape[:2]
        header_x, header_y, header_w, header_h = header_box
        x1 = max(0, header_x + header_w - 2)
        x2 = min(w, header_x + header_w + 14)
        y1 = max(0, header_y + header_h)
        y2 = min(h, int(h * 0.90))
        if x2 <= x1 or y2 <= y1:
            return None

        gray = cv2.cvtColor(screen_bgr, cv2.COLOR_BGR2GRAY)
        band = gray[y1:y2, x1:x2]
        line_mask = (band > 90) & (band < 245)
        row_scores = np.mean(line_mask, axis=1)
        rows = np.where(row_scores > 0.20)[0]
        if rows.size == 0:
            return None

        return int(y1 + rows.max())

    def _grid_cells(self, screen_bgr, raw_cells):
        header_box = self._find_header_box(screen_bgr)
        if header_box is None:
            return []

        h, w = screen_bgr.shape[:2]
        header_x, header_y, header_w, header_h = header_box
        col_count = 4
        col_step = header_w / float(col_count)

        if len(raw_cells) >= 4:
            median_h = int(round(np.median([cell[3] for cell in raw_cells])))
            y_positions = self._cluster_axis_positions([cell[1] for cell in raw_cells], max(8, median_h // 2))
            y_diffs = [b - a for a, b in zip(y_positions, y_positions[1:]) if b > a]
            row_step = int(round(np.median(y_diffs))) if y_diffs else max(24, int(round(header_h * 0.73)))
            cell_h = median_h
        else:
            row_step = max(24, int(round(header_h * 0.73)))
            cell_h = max(20, row_step - 3)

        cell_w = max(60, int(round(col_step)) - 3)
        first_row_y = header_y + header_h + max(1, int(round(header_h * 0.07)))
        if raw_cells:
            raw_top = min(cell[1] for cell in raw_cells)
            if abs(raw_top - first_row_y) <= max(6, row_step // 3):
                first_row_y = raw_top

        detected_bottom = self._grid_bottom(screen_bgr, header_box)
        raw_bottom = max((cell[1] + cell[3] for cell in raw_cells), default=None)
        bottom_candidates = [first_row_y + cell_h]
        if detected_bottom is not None:
            bottom_candidates.append(detected_bottom)
        if raw_bottom is not None:
            bottom_candidates.append(raw_bottom)
        if detected_bottom is None and raw_bottom is None:
            bottom_candidates.append(int(h * 0.86))
        table_bottom = min(h, max(bottom_candidates))

        row_count = max(1, min(16, int((table_bottom - first_row_y) // row_step) + 1))

        cells = []
        seen = set()
        for row in range(row_count):
            y = int(round(first_row_y + row * row_step))
            if y >= h:
                break
            for col in range(col_count):
                x = int(round(header_x + col * col_step + 1))
                cell = (
                    max(0, x),
                    max(0, y),
                    min(cell_w, w - x),
                    min(cell_h, h - y),
                )
                key = (cell[0] // 4, cell[1] // 4)
                if cell[2] > 0 and cell[3] > 0 and key not in seen:
                    seen.add(key)
                    cells.append(cell)

        return cells

    def find_cells(self, screen_bgr):
        raw_cells = self._find_white_cells(screen_bgr)
        grid_cells = self._grid_cells(screen_bgr, raw_cells)
        if grid_cells:
            return grid_cells
        return self._grid_from_cells(screen_bgr, raw_cells)

    @staticmethod
    def _ocr_box_center(box: tuple[tuple[float, float], ...] | None) -> tuple[float, float] | None:
        if not box:
            return None
        xs = [point[0] for point in box]
        ys = [point[1] for point in box]
        if not xs or not ys:
            return None
        return (sum(xs) / len(xs), sum(ys) / len(ys))

    @staticmethod
    def _cell_contains_point(cell_box: Box, point: tuple[float, float], *, margin: int = 3) -> bool:
        x, y, cell_w, cell_h = cell_box
        px, py = point
        return x - margin <= px <= x + cell_w + margin and y - margin <= py <= y + cell_h + margin

    @staticmethod
    def _table_crop_box(screen_bgr, cells: list[Box]) -> Box:
        h, w = screen_bgr.shape[:2]
        min_x = min(cell[0] for cell in cells)
        min_y = min(cell[1] for cell in cells)
        max_x = max(cell[0] + cell[2] for cell in cells)
        max_y = max(cell[1] + cell[3] for cell in cells)
        margin_x = max(4, int(round((max_x - min_x) * 0.01)))
        margin_y = max(4, int(round((max_y - min_y) * 0.02)))
        x1 = max(0, min_x - margin_x)
        y1 = max(0, min_y - margin_y)
        x2 = min(w, max_x + margin_x)
        y2 = min(h, max_y + margin_y)
        return (x1, y1, max(0, x2 - x1), max(0, y2 - y1))

    def detect_cell_texts(self, screen_bgr, cells: list[Box] | None = None) -> tuple[dict[Box, OcrText], float, int]:
        """用 GPU det OCR 一次识别制造商表格，并把 OCR 结果归属到视觉单元格。"""
        if cells is None:
            cells = self.find_cells(screen_bgr)
        if not cells:
            return {}, 0.0, 0

        crop_x, crop_y, crop_w, crop_h = self._table_crop_box(screen_bgr, cells)
        if crop_w <= 0 or crop_h <= 0:
            return {}, 0.0, 0

        table_bgr = screen_bgr[crop_y : crop_y + crop_h, crop_x : crop_x + crop_w]
        ocr_started = time.perf_counter()
        detections = self.ocr.read(table_bgr, use_det=True, use_cls=False, text_score=0.3)
        ocr_ms = self._elapsed_ms(ocr_started)

        mapped: dict[Box, OcrText] = {}
        for detection in detections:
            center = self._ocr_box_center(detection.box)
            if center is None:
                continue
            point = (center[0] + crop_x, center[1] + crop_y)
            for cell_box in cells:
                if not self._cell_contains_point(cell_box, point):
                    continue
                current = mapped.get(cell_box)
                if current is None or detection.score > current.score:
                    mapped[cell_box] = detection
                break

        return mapped, ocr_ms, len(detections)

    @staticmethod
    def _matches_target_text(candidate_norm: str, target_norm: str, score: float, threshold: float) -> bool:
        if not candidate_norm or not target_norm:
            return False
        if candidate_norm == target_norm:
            return score >= threshold

        # 输入已经是单个制造商单元格，可以容忍 OCR 在文字前后多识别少量噪声，
        # 例如“奥迪”被识别成“廣奥迪”。
        substring_threshold = max(0.55, threshold - 0.20)
        return len(target_norm) >= 2 and target_norm in candidate_norm and score >= substring_threshold

    def find_text(self, target_text: str, *, region=None, threshold: float = 0.75):
        """在当前画面的制造商表格中定位目标文字。"""
        if not self.state.is_running or not target_text:
            return None
        started = time.perf_counter()
        capture_ms = None
        ocr_ms = None
        ocr_items = None
        cells_count = 0
        result_text = "miss"
        try:
            capture_started = time.perf_counter()
            frame = self.image_cache.capture_frame(region)
            capture_ms = self._elapsed_ms(capture_started)
            screen_bgr = frame.image
            target_norm = self.ocr.normalize_text(target_text)
            cells = self.find_cells(screen_bgr)
            cells_count = len(cells)
            detected_texts, ocr_ms, ocr_items = self.detect_cell_texts(screen_bgr, cells)

            for cell_box in cells:
                x, y, cell_w, cell_h = cell_box
                result = detected_texts.get(cell_box)
                if result is None:
                    continue

                candidate_norm = self.ocr.normalize_text(result.text)
                candidate_score = float(result.score)
                if not self._matches_target_text(candidate_norm, target_norm, candidate_score, threshold):
                    continue

                pos = frame.box_center(cell_box)
                self.last_positions[target_text] = pos
                self.log(
                    f"[ManufacturerOCR] 命中: {result.text} (目标:{target_text}) | "
                    f"分数:{result.score:.3f} (阈值 {threshold}) | OCR:det-table | "
                    f"单元格: x={x}, y={y}, w={cell_w}, h={cell_h}",
                    level="debug",
                )
                result_text = "hit"
                return pos

            return None
        except Exception as e:
            result_text = "error"
            self.log(f"find_text 异常: {e}", level="warning")
            return None
        finally:
            self._log_timing(
                "find_text",
                started,
                capture_ms=capture_ms,
                ocr_ms=ocr_ms,
                ocr_items=ocr_items,
                cells=cells_count,
                result=result_text,
            )

    def scan_for_text(
        self,
        target_text,
        threshold=0.75,
        max_steps=None,
        label="目标制造商",
    ):
        """查找目标制造商；当前画面未命中时自动翻动整个制造商列表。"""
        if max_steps is None:
            max_steps = int(self.config.values.get("manufacturer_scan_steps", 50))
        max_steps = max(5, min(100, max_steps))

        pos = self.find_text(target_text, threshold=threshold)
        if pos:
            self.log(f"已在当前页面找到{label}。", level="debug")
            return pos

        scan_plan = (("up", "上", max_steps), ("down", "下", max_steps * 2))
        for direction, direction_label, steps in scan_plan:
            self.log(
                f"当前页面未找到{label}，开始向{direction_label}扫描制造商列表 ({steps} 步)...",
                level="debug",
            )
            for step in range(steps):
                self.input_actions.hw_press(direction)
                self.polling.sleep(0.18)

                pos = self.find_text(target_text, threshold=threshold)
                if pos:
                    self.log(f"找到{label}：向{direction_label}扫描第 {step + 1} 步。", level="debug")
                    return pos

        self.log(f"扫描制造商列表后仍未找到{label}。", level="debug")
        return None
