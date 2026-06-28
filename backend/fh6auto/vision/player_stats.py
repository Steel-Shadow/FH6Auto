from __future__ import annotations

import re
import time
import unicodedata
from collections.abc import Callable
from typing import TYPE_CHECKING

from ..backend.state import RuntimeState
from .cache import Box
from .cache import ImageCacheService
from .ocr import OcrService, OcrText

if TYPE_CHECKING:
    from ..window import GameWindowService


class PlayerStatsDetector:
    """OCR-based FH6 numeric value detection."""

    def __init__(
        self,
        *,
        state: RuntimeState,
        image_cache: ImageCacheService,
        game_window: GameWindowService,
        ocr: OcrService,
        log: Callable[..., None],
    ) -> None:
        self.state = state
        self.image_cache = image_cache
        self.game_window = game_window
        self.ocr = ocr
        self.log = log

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
        self.log(f"[VisionTiming] PlayerStats.{name} " + " ".join(parts), level="debug")

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
        bounds = PlayerStatsDetector._ocr_result_bounds(result)
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

    def find_sell_price_value(self, region=None, threshold=0.25) -> int | None:
        if not self.state.is_running:
            return None
        started = time.perf_counter()
        capture_ms = None
        result_text = "miss"
        try:
            capture_started = time.perf_counter()
            frame = self.image_cache.capture_frame(region)
            capture_ms = self._elapsed_ms(capture_started)
            screen_bgr = frame.image
            if screen_bgr.size == 0:
                return None

            results = sorted(
                (result for result in self.ocr.read(screen_bgr, text_score=threshold) if result.score >= threshold),
                key=lambda result: (
                    min((point[1] for point in result.box), default=0) if result.box else 0,
                    min((point[0] for point in result.box), default=0) if result.box else 0,
                ),
            )
            combined_text = "".join(result.text for result in results)
            value = self.parse_credit_value(combined_text)
            if value is not None:
                result_text = "hit"
                self.log(f"[PriceOCR] 出售价格: CR {value:,} | OCR: {combined_text}", level="debug")
            return value
        except Exception as e:
            result_text = "error"
            self.log(f"find_sell_price_value 异常: {e}", level="warning")
            return None
        finally:
            self._log_timing(
                "find_sell_price_value",
                started,
                capture_ms=capture_ms,
                result=result_text,
            )

    def _default_credit_region(self):
        full_region = self.game_window.regions.get("全界面")
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
        if not self.state.is_running:
            return None
        started = time.perf_counter()
        capture_ms = None
        result_text = "miss"
        try:
            credit_region = region or self._default_credit_region()
            capture_started = time.perf_counter()
            frame = self.image_cache.capture_frame(credit_region)
            capture_ms = self._elapsed_ms(capture_started)
            screen_bgr = frame.image
            if screen_bgr.size == 0:
                return None

            results = self.ocr.read(screen_bgr, text_score=threshold)
            value = self.parse_current_credit_value(results, threshold=threshold)
            if value is not None:
                result_text = "hit"
                combined_text = "".join(result.text for result in results if result.score >= threshold)
                self.log(f"[CreditOCR] 当前 CR: {value:,} | OCR: {combined_text}", level="debug")
            return value
        except Exception as e:
            result_text = "error"
            self.log(f"find_current_credit_value 异常: {e}", level="warning")
            return None
        finally:
            self._log_timing(
                "find_current_credit_value",
                started,
                capture_ms=capture_ms,
                result=result_text,
            )

    def _default_skill_points_region(self):
        full_region = self.game_window.regions.get("全界面")
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
        if not self.state.is_running:
            return None
        started = time.perf_counter()
        capture_ms = None
        result_text = "miss"
        try:
            skill_region = region or self._default_skill_points_region()
            capture_started = time.perf_counter()
            frame = self.image_cache.capture_frame(skill_region)
            capture_ms = self._elapsed_ms(capture_started)
            screen_bgr = frame.image
            if screen_bgr.size == 0:
                return None

            results = self.ocr.read(screen_bgr, text_score=threshold)
            value = self.parse_current_skill_points_value(results, threshold=threshold)
            if value is not None:
                result_text = "hit"
                combined_text = "".join(result.text for result in results if result.score >= threshold)
                self.log(f"[SkillPointOCR] 当前技术点: {value} | OCR: {combined_text}", level="debug")
            return value
        except Exception as e:
            result_text = "error"
            self.log(f"find_current_skill_points_value 异常: {e}", level="warning")
            return None
        finally:
            self._log_timing(
                "find_current_skill_points_value",
                started,
                capture_ms=capture_ms,
                result=result_text,
            )
