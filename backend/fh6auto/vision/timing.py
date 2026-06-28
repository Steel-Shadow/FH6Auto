from __future__ import annotations

import time
from collections.abc import Callable
from typing import ClassVar


class VisionTimingMixin:
    """Shared debug timing logger for vision services."""

    TIMING_NAME: ClassVar[str] = "Vision"
    log: Callable[..., None]

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
        self.log(f"[VisionTiming] {self.TIMING_NAME}.{name} " + " ".join(parts), level="debug")
