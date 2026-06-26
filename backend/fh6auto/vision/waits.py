from __future__ import annotations

import time
from collections.abc import Callable
from typing import TYPE_CHECKING, TypeVar

if TYPE_CHECKING:
    from ..backend.app import BackendApp


T = TypeVar("T")


class ImageWaitsService:
    """为即时图像匹配和 OCR 定位提供可中断的轮询等待。"""

    _SLEEP_STEP = 0.05

    def __init__(self, app: BackendApp) -> None:
        self.app = app

    def _sleep_while_running(self, duration: float, *, deadline: float | None = None) -> None:
        sleep_deadline = time.monotonic() + max(0.0, duration)
        if deadline is not None:
            sleep_deadline = min(sleep_deadline, deadline)

        while self.app.state.is_running:
            remaining = sleep_deadline - time.monotonic()
            if remaining <= 0:
                return
            time.sleep(min(self._SLEEP_STEP, remaining))

    def _wait_for(self, finder: Callable[[], T | None], *, timeout: float, interval: float) -> T | None:
        deadline = time.monotonic() + max(0.0, timeout)
        while self.app.state.is_running and time.monotonic() < deadline:
            result = finder()
            if result is not None:
                return result
            self._sleep_while_running(interval, deadline=deadline)
        return None

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
            lambda: self.app.services.image_matcher.find_image_sift(
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
            lambda: self.app.services.ocr.find_any_text_ui(
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
            lambda: self.app.services.ocr.find_menu_text_ui(
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
            lambda: self.app.services.ocr.find_footer_text_ui(
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
        """查找目标制造商；当前画面未命中时自动翻动整个制造商列表。"""
        if max_steps is None:
            max_steps = int(self.app.services.config.values.get("manufacturer_scan_steps", 50))
        max_steps = max(5, min(100, max_steps))

        pos = self.app.services.ocr.find_manufacturer_text(
            target_text,
            threshold=threshold,
        )
        if pos:
            self.app.log(f"已在当前页面找到{label}。", level="debug")
            return pos

        scan_plan = (("up", "上", max_steps), ("down", "下", max_steps * 2))
        for direction, direction_label, steps in scan_plan:
            self.app.log(
                f"当前页面未找到{label}，开始向{direction_label}扫描制造商列表 ({steps} 步)...",
                level="debug",
            )
            for step in range(steps):
                self.app.services.input_actions.hw_press(direction)
                self._sleep_while_running(0.18)

                pos = self.app.services.ocr.find_manufacturer_text(target_text, threshold=threshold)
                if pos:
                    self.app.log(f"找到{label}：向{direction_label}扫描第 {step + 1} 步。", level="debug")
                    return pos

        self.app.log(f"扫描制造商列表后仍未找到{label}。", level="debug")
        return None
