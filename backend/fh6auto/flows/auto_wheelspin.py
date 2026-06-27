from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

NORMAL_WHEELSPIN_REFERENCE = "wheelspin.png"
SUPER_WHEELSPIN_REFERENCE = "superwheelspin.png"
NORMAL_DUPLICATE_POPUP_LIMIT = 1
SUPER_DUPLICATE_POPUP_LIMIT = 3
DEFAULT_OWNED_CAR_SELL_THRESHOLD = 100_000


class AutoWheelspinFlow:
    STATE_TIMEOUT_SECONDS = 90.0

    def __init__(
        self,
        *,
        state: Any,
        config: Any,
        game_window: Any,
        image_cache: Any,
        image_matcher: Any,
        input_actions: Any,
        ocr: Any,
        player_stats: Any,
        recovery: Any,
        runtime: Any,
        log: Callable[..., None],
    ) -> None:
        self.state = state
        self.config = config
        self.game_window = game_window
        self.image_cache = image_cache
        self.image_matcher = image_matcher
        self.input_actions = input_actions
        self.ocr = ocr
        self.player_stats = player_stats
        self.recovery = recovery
        self.runtime = runtime
        self.log = log

    def _footer_region(self):
        x, y, w, h = self.game_window.regions["全界面"]
        footer_y = y + int(h * 0.80)
        return (x, footer_y, w, max(1, y + h - footer_y))

    def _dialog_region(self):
        x, y, w, h = self.game_window.regions["全界面"]
        return (
            x + int(w * 0.25),
            y + int(h * 0.12),
            max(1, int(w * 0.50)),
            max(1, int(h * 0.76)),
        )

    def _read_footer_norm_text(self) -> str:
        try:
            results = self.ocr.read(
                self.image_cache.capture_region(self._footer_region()), text_score=0.25
            )
        except Exception as e:
            self.log(f"读取抽奖底部提示失败: {e}", level="warning")
            return ""

        parts = []
        for result in results:
            if result.score < 0.25:
                continue
            text = self.ocr.normalize_text(result.text)
            if text:
                parts.append(text)
        return "".join(parts)

    def _detect_wheelspin_footer_state(self) -> str | None:
        text = self._read_footer_norm_text()
        if not text:
            return None
        elif "跳过" in text:
            return "skip"
        elif "再次抽奖" in text:
            return "claim_again"
        elif "领取奖励" in text:
            return "claim"
        elif "重置车辆位置" in text:
            return "not_in_wheelspin"
        else:
            return None

    def _detect_owned_car_dialog(self) -> bool:
        try:
            results = self.ocr.read(
                self.image_cache.capture_region(self._dialog_region()),
                text_score=0.25,
            )
        except Exception as e:
            self.log(f"读取重复车辆弹窗失败: {e}", level="warning")
            return False

        text = "".join(self.ocr.normalize_text(result.text) for result in results if result.score >= 0.25)
        return "已拥有车辆" in text or "添加至车库" in text

    def _read_owned_car_sell_price(self, timeout: float = 1.5) -> int | None:
        deadline = time.time() + max(0.0, timeout)
        while time.time() < deadline:
            self.runtime.ensure_running()
            price = self.player_stats.find_sell_price_value(
                region=self._dialog_region(),
                threshold=0.25,
            )
            if price is not None:
                return price
            self.runtime.sleep(0.25)
        return None

    def _sell_owned_car(self) -> None:
        for _ in range(2):
            self.input_actions.hw_press("down", delay=0.08)
            self.runtime.sleep(0.15)
        self.input_actions.hw_press("enter")

    def _owned_car_sell_threshold(self) -> int:
        try:
            threshold = int(
                self.config.values.get(
                    "wheelspin_sell_threshold",
                    DEFAULT_OWNED_CAR_SELL_THRESHOLD,
                )
            )
        except Exception:
            threshold = DEFAULT_OWNED_CAR_SELL_THRESHOLD
        return max(0, threshold)

    def _handle_owned_car_dialog(self, label: str) -> bool:
        if not self._detect_owned_car_dialog():
            return False

        price = self._read_owned_car_sell_price()
        sell_threshold = self._owned_car_sell_threshold()
        if price is not None and price < sell_threshold:
            self.log(
                f"检测到{label}重复车辆，出售价格 CR {price:,}，低于 CR {sell_threshold:,}，选择出售。",
                level="debug",
            )
            self._sell_owned_car()
        else:
            if price is None:
                self.log(f"检测到{label}重复车辆弹窗，但未识别到出售价格，保守选择添加至车库。", level="debug")
            else:
                self.log(
                    f"检测到{label}重复车辆，出售价格 CR {price:,}，不低于 CR {sell_threshold:,}，选择添加至车库。",
                    level="debug",
                )
            self.input_actions.hw_press("enter")
        self.runtime.sleep(1.0)
        return True

    def _clear_owned_car_dialogs(self, label: str, popup_limit: int) -> None:
        handled = 0
        deadline = time.time() + 2.5

        while time.time() < deadline and handled < popup_limit:
            self.runtime.ensure_running()
            if self._handle_owned_car_dialog(label):
                handled += 1
                deadline = time.time() + 2.5
                continue
            self.runtime.sleep(0.25)

    @staticmethod
    def _fixed_progress_total(
        super_count: int,
        normal_count: int,
        super_use_all: bool,
        normal_use_all: bool,
    ) -> int:
        total = 0
        if not super_use_all:
            total += max(0, int(super_count))
        if not normal_use_all:
            total += max(0, int(normal_count))
        return total

    def _update_wheelspin_progress(self, progress_total: int) -> None:
        self.state.set_task("自动抽奖", self.state.wheelspin_counter, progress_total)

    def _prepare_my_horizon_menu(self) -> bool:
        self.log("准备验证/进入菜单...", level="debug")
        if not self.recovery.enter_menu():
            return False

        self.log("切换到我的地平线...", level="debug")
        for _ in range(2):
            self.input_actions.hw_press("pagedown", delay=0.15)
            self.runtime.sleep(0.3)
        return True

    def _find_wheelspin_entry(self, reference_path: str, label: str, timeout: float):
        self.log(f"定位{label}入口...", level="debug")
        pos_wheelspin = self.image_matcher.find_image_sift(reference_path)
        if not pos_wheelspin:
            self.log(f"未找到{label}入口。", level="warning")
        return pos_wheelspin

    def _enter_wheelspin_entry(self, reference_path: str, label: str) -> bool:
        pos_wheelspin = self._find_wheelspin_entry(reference_path, label, timeout=1.5)
        if pos_wheelspin:
            self.log(f"已在当前页面找到{label}入口。", level="debug")
        else:
            if not self._prepare_my_horizon_menu():
                return False
            pos_wheelspin = self._find_wheelspin_entry(reference_path, label, timeout=12)

        if not pos_wheelspin:
            return False

        self.input_actions.game_click(pos_wheelspin)
        self.runtime.sleep(1.0)
        self.input_actions.hw_press("enter")
        self.runtime.sleep(0.8)
        self.log(f"已进入{label}并确认。", level="debug")
        return True

    def _run_wheelspin_loop(
        self,
        label: str,
        target_count: int,
        use_all: bool,
        progress_total: int,
        duplicate_popup_limit: int,
    ) -> bool:
        target_count = max(0, int(target_count))
        if not use_all and target_count <= 0:
            self.log(f"{label}次数为 0，跳过。", level="debug")
            return True

        self.log(f"开始执行{label}循环。", level="debug")
        last_state_time = time.time()
        completed_count = 0

        while use_all or completed_count < target_count:
            self.runtime.ensure_running()

            if self._handle_owned_car_dialog(label):
                last_state_time = time.time()
                continue

            state = self._detect_wheelspin_footer_state()

            if state == "not_in_wheelspin":
                self.log(f"未检测到不在{label}界面，终止{label}流程。可能是由于抽奖次数为0。", level="warning")
                return True

            elif state == "skip":
                self.log("检测到抽奖动画，可跳过，按 Enter。", level="debug")
                self.input_actions.hw_press("enter")
                last_state_time = time.time()
                self.runtime.sleep(1.0)
                continue

            elif state == "claim_again":
                completed_count += 1
                self.state.wheelspin_counter += 1
                self._update_wheelspin_progress(progress_total)

                if not use_all and completed_count >= target_count:
                    self.log(f"{label} {completed_count}/{target_count} 完成，领取奖励并退出。", level="debug")
                    self.input_actions.hw_press("esc")
                    self.runtime.sleep(1.5)
                    self._clear_owned_car_dialogs(label, duplicate_popup_limit)
                    return True

                progress_text = f"{completed_count}/{target_count}" if not use_all else f"已完成 {completed_count} 次"
                self.log(f"{label} {progress_text}，领取奖励并继续下一次。", level="debug")
                self.input_actions.hw_press("enter")
                last_state_time = time.time()
                self.runtime.sleep(1.5)
                continue

            elif state == "claim":
                completed_count += 1
                self.state.wheelspin_counter += 1
                self._update_wheelspin_progress(progress_total)
                if use_all:
                    self.log(f"{label}已用完，累计完成 {completed_count} 次，领取奖励后结束。", level="debug")
                elif completed_count >= target_count:
                    self.log(f"{label} {completed_count}/{target_count} 完成，领取奖励后退出。", level="debug")
                else:
                    self.log(
                        f"{label}机会已用完，当前进度 {completed_count}/{target_count}，领取奖励后结束。", level="debug"
                    )
                self.input_actions.hw_press("enter")
                self.runtime.sleep(1.5)
                self._clear_owned_car_dialogs(label, duplicate_popup_limit)
                return True

            if time.time() - last_state_time > self.STATE_TIMEOUT_SECONDS:
                self.log("等待抽奖界面底部提示超时，自动抽奖流程停止。", level="warning")
                return False

            self.runtime.sleep(0.4)

        return use_all or completed_count >= target_count

    def _run_wheelspin_type(
        self,
        label: str,
        reference_path: str,
        target_count: int,
        use_all: bool,
        progress_total: int,
        duplicate_popup_limit: int,
    ) -> bool:
        if not use_all and int(target_count) <= 0:
            self.log(f"{label}次数为 0，跳过。", level="debug")
            return True
        if not self._enter_wheelspin_entry(reference_path, label):
            return False
        return self._run_wheelspin_loop(
            label,
            target_count,
            use_all,
            progress_total,
            duplicate_popup_limit,
        )

    def logic_auto_wheelspin(
        self,
        target_count: int,
        normal_count: int = 0,
        use_all: bool = False,
        super_use_all: bool = False,
        normal_use_all: bool = False,
    ) -> bool:
        use_all = bool(use_all)
        super_use_all = use_all or bool(super_use_all)
        normal_use_all = use_all or bool(normal_use_all)
        start_count = self.state.wheelspin_counter

        def finish(reason: str | None = None) -> bool:
            suffix = f"原因：{reason}" if reason else ""
            self.log(f"自动抽奖流程结束：完成 {self.state.wheelspin_counter - start_count} 次。{suffix}")
            return True

        progress_total = self._fixed_progress_total(
            target_count,
            normal_count,
            super_use_all,
            normal_use_all,
        )
        self._update_wheelspin_progress(progress_total)

        should_run_super = super_use_all or int(target_count) > 0
        should_run_normal = normal_use_all or int(normal_count) > 0

        if not should_run_super and not should_run_normal:
            return finish("抽奖次数为 0")

        if should_run_super and not self._run_wheelspin_type(
            "超级抽奖",
            SUPER_WHEELSPIN_REFERENCE,
            target_count,
            super_use_all,
            progress_total,
            SUPER_DUPLICATE_POPUP_LIMIT,
        ):
            # 关闭弹窗，已无剩余超级抽奖
            self.input_actions.hw_press("enter")

        if should_run_normal and not self._run_wheelspin_type(
            "普通抽奖",
            NORMAL_WHEELSPIN_REFERENCE,
            normal_count,
            normal_use_all,
            progress_total,
            NORMAL_DUPLICATE_POPUP_LIMIT,
        ):
            # 关闭弹窗，已无剩余抽奖
            self.input_actions.hw_press("enter")

        return finish()
