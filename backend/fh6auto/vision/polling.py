from __future__ import annotations

import time
from collections.abc import Callable
from typing import TYPE_CHECKING, TypeVar

from ..backend.state import RuntimeState

if TYPE_CHECKING:
    from .footer import FooterDetector
    from .manufacturer import ManufacturerDetector
    from .matcher import ImageMatcherService
    from .ocr import OcrService
    from .text import TextDetector

T = TypeVar("T")


class PollingWaiter:
    """Small interruptible polling helper shared by vision detectors."""

    _SLEEP_STEP = 0.05

    def __init__(self, is_running: Callable[[], bool]) -> None:
        self.is_running = is_running

    def sleep(self, duration: float, *, deadline: float | None = None) -> None:
        sleep_deadline = time.monotonic() + max(0.0, duration)
        if deadline is not None:
            sleep_deadline = min(sleep_deadline, deadline)

        while self.is_running():
            remaining = sleep_deadline - time.monotonic()
            if remaining <= 0:
                return
            time.sleep(min(self._SLEEP_STEP, remaining))

    def wait_for(self, finder: Callable[[], T | None], *, timeout: float, interval: float) -> T | None:
        deadline = time.monotonic() + max(0.0, timeout)
        while self.is_running() and time.monotonic() < deadline:
            result = finder()
            if result is not None:
                return result
            self.sleep(interval, deadline=deadline)
        return None


class ImageWaitsService:
    """为即时图像匹配和 OCR 定位提供可中断的轮询等待。"""

    def __init__(
        self,
        *,
        state: RuntimeState,
        image_matcher: ImageMatcherService,
        ocr: OcrService,
        text_detector: TextDetector,
        footer: FooterDetector,
        manufacturer: ManufacturerDetector,
    ) -> None:
        self.state = state
        self.image_matcher = image_matcher
        self.ocr = ocr
        self.text_detector = text_detector
        self.footer = footer
        self.manufacturer = manufacturer
        self.polling = PollingWaiter(lambda: bool(self.state.is_running))

    def _sleep_while_running(self, duration: float, *, deadline: float | None = None) -> None:
        self.polling.sleep(duration, deadline=deadline)

    def _wait_for(self, finder: Callable[[], T | None], *, timeout: float, interval: float) -> T | None:
        return self.polling.wait_for(finder, timeout=timeout, interval=interval)

    def wait_for_image_sift(
        self,
        reference_path,
        region=None,
        min_inliers=50,
        ratio=0.75,
        max_features=2500,
        timeout: float = 30,
        interval: float = 0.4,
    ):
        return self._wait_for(
            lambda: self.image_matcher.find_image_sift(
                reference_path,
                region=region,
                min_inliers=min_inliers,
                ratio=ratio,
                max_features=max_features,
            ),
            timeout=timeout,
            interval=interval,
        )

    def wait_for_any_text_ui(
        self,
        text_list,
        region=None,
        threshold=0.65,
        timeout: float = 30,
        interval: float = 0.3,
    ):
        return self._wait_for(
            lambda: self.text_detector.find_any_text_ui(
                text_list,
                region=region,
                threshold=threshold,
            ),
            timeout=timeout,
            interval=interval,
        )

    def wait_for_menu_text_ui(
        self,
        target_text,
        region=None,
        timeout: float = 30,
        interval: float = 0.5,
        threshold=0.65,
    ):
        return self._wait_for(
            lambda: self.text_detector.find_menu_text_ui(
                target_text,
                region=region,
                threshold=threshold,
            ),
            timeout=timeout,
            interval=interval,
        )

    def wait_for_footer_text_ui(
        self,
        target_text,
        region=None,
        threshold=0.65,
        timeout: float = 30,
        interval: float = 0.5,
    ):
        return self._wait_for(
            lambda: self.footer.find_text(
                target_text,
                region=region,
                threshold=threshold,
            ),
            timeout=timeout,
            interval=interval,
        )

    def scan_for_manufacturer_text(
        self,
        target_text,
        threshold=0.75,
        max_steps=None,
        label="目标制造商",
    ):
        return self.manufacturer.scan_for_text(
            target_text,
            threshold=threshold,
            max_steps=max_steps,
            label=label,
        )
