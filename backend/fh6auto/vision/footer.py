from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from .cache import Box, Point


@dataclass(frozen=True)
class _FooterTextMatch:
    target: str
    text: str
    score: float
    pos: Point
    ocr_box: Box | None = None
    region_name: str | None = None


class FooterDetector:
    """Detect footer hotkey hint text in the current game screen."""

    def __init__(
        self,
        *,
        state: Any,
        image_cache: Any,
        game_window: Any,
        ocr: Any,
        log: Callable[..., None],
    ) -> None:
        self.state = state
        self.image_cache = image_cache
        self.game_window = game_window
        self.ocr = ocr
        self.log = log
        self.last_positions: dict[str, Point] = {}
        self._targets_cache: dict[tuple[str, ...], list[tuple[str, str]]] = {}

    @staticmethod
    def _elapsed_ms(start: float) -> float:
        return (time.perf_counter() - start) * 1000.0

    def _log_timing(self, name: str, start: float, **details) -> None:
        parts = [f"total={self._elapsed_ms(start):.1f}ms"]
        for key, value in details.items():
            if value is None:
                continue
            if isinstance(value, float):
                parts.append(f"{key}={value:.1f}ms" if key.endswith("_ms") else f"{key}={value:.3f}")
            else:
                parts.append(f"{key}={value}")
        self.log(f"[VisionTiming] Footer.{name} " + " ".join(parts), level="debug")

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
    def _better_match(
        current: _FooterTextMatch | None,
        candidate: _FooterTextMatch | None,
    ) -> _FooterTextMatch | None:
        if candidate is None:
            return current
        if current is None or candidate.score > current.score:
            return candidate
        return current

    def _build_targets(self, text_list):
        if isinstance(text_list, str):
            text_list = [text_list]

        cache_key = tuple(str(text) for text in text_list)
        if cache_key in self._targets_cache:
            return self._targets_cache[cache_key]

        targets = []
        for target_text in cache_key:
            target_norm = self.ocr.normalize_text(target_text)
            if len(target_norm) < 2 or "?" in target_norm:
                self.log(f"[FooterOCR] 跳过目标文字 {target_text}：内容不可用。", level="debug")
                continue
            targets.append((target_text, target_norm))
        self._targets_cache[cache_key] = targets
        return targets

    def _footer_text_regions(self, region):
        if region is None:
            full_region = self.game_window.regions.get("全界面")
            if full_region is None:
                frame = self.image_cache.capture_frame(None)
                region = (frame.origin[0], frame.origin[1], frame.width, frame.height)
            else:
                region = full_region

        sx, sy, sw, sh = map(int, region)
        bottom_h = max(1, int(sh * 0.20))
        bottom_y = sy + sh - bottom_h
        return [("底部提示栏", (sx, bottom_y, sw, bottom_h))]

    def find_text(self, target_text, region=None, threshold=0.65):
        """在当前画面的底部按键提示栏中定位目标文字。"""
        if not self.state.is_running:
            return None
        started = time.perf_counter()
        capture_ms = 0.0
        region_count = 0
        result_text = "miss"
        try:
            targets = self._build_targets([target_text])
            if not targets:
                result_text = "no_targets"
                return None

            for roi_name, roi in self._footer_text_regions(region):
                region_count += 1
                capture_started = time.perf_counter()
                frame = self.image_cache.capture_frame(roi)
                capture_ms += self._elapsed_ms(capture_started)
                rx, ry = frame.origin
                rw, rh = frame.width, frame.height
                roi_bgr = frame.image
                if roi_bgr.size == 0:
                    continue

                results = self.ocr.read(roi_bgr, text_score=max(0.25, threshold - 0.45))
                best = None
                fallback_pos = (int(rx + rw / 2), int(ry + rh / 2))
                for result in results:
                    if result is None:
                        continue

                    candidate_norm = self.ocr.normalize_text(result.text)
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

                        candidate = _FooterTextMatch(
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
                    self.log(
                        f"[FooterOCR] 命中: {best.text} "
                        f"(目标:{best.target}) | 分数:{best.score:.3f} "
                        f"(阈值 {threshold}) | 区域:{best.region_name}{box_text}",
                        level="debug",
                    )
                    result_text = "hit"
                    self.last_positions[best.target] = best.pos
                    return best.pos

            return None
        except Exception as e:
            result_text = "error"
            self.log(f"find_footer_text_ui 异常: {e}", level="warning")
            return None
        finally:
            self._log_timing(
                "find_text",
                started,
                capture_ms=capture_ms,
                regions=region_count,
                result=result_text,
            )
