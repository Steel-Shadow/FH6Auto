from __future__ import annotations

import os
import time
from typing import TYPE_CHECKING

from ..paths import get_img_path

if TYPE_CHECKING:
    from ..backend.app import BackendApp

NORMAL_WHEELSPIN_REFERENCE = "wheelspin.png"
SUPER_WHEELSPIN_REFERENCE = "superwheelspin.png"
NORMAL_DUPLICATE_POPUP_LIMIT = 1
SUPER_DUPLICATE_POPUP_LIMIT = 3


class AutoWheelspinFlow:
    STATE_TIMEOUT_SECONDS = 90.0

    def __init__(self, app: BackendApp) -> None:
        self.app = app

    def _footer_region(self):
        x, y, w, h = self.app.services.game_window.regions["全界面"]
        footer_y = y + int(h * 0.80)
        return (x, footer_y, w, max(1, y + h - footer_y))

    def _dialog_region(self):
        x, y, w, h = self.app.services.game_window.regions["全界面"]
        return (
            x + int(w * 0.25),
            y + int(h * 0.12),
            max(1, int(w * 0.50)),
            max(1, int(h * 0.76)),
        )

    def _read_footer_norm_text(self) -> str:
        try:
            results = self.app.services.ocr.read(self.app.services.image_cache.capture_region(self._footer_region()), text_score=0.25)
        except Exception as e:
            self.app.log(f"读取抽奖底部提示失败: {e}")
            return ""

        parts = []
        for result in results:
            if result.score < 0.25:
                continue
            text = self.app.services.ocr.normalize_text(result.text)
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
            results = self.app.services.ocr.read(self.app.services.image_cache.capture_region(self._dialog_region()), text_score=0.25)
        except Exception as e:
            self.app.log(f"读取重复车辆弹窗失败: {e}")
            return False

        text = "".join(
            self.app.services.ocr.normalize_text(result.text)
            for result in results
            if result.score >= 0.25
        )
        return "已拥有车辆" in text or "添加至车库" in text

    def _handle_owned_car_dialog(self, label: str) -> bool:
        if not self._detect_owned_car_dialog():
            return False

        self.app.log(f"检测到{label}重复车辆弹窗，选择添加至车库。")
        self.app.services.input_actions.hw_press("enter")
        time.sleep(1.0)
        return True

    def _clear_owned_car_dialogs(self, label: str, popup_limit: int) -> None:
        handled = 0
        deadline = time.time() + 2.5

        while self.app.state.is_running and time.time() < deadline and handled < popup_limit:
            self.app.services.runtime.check_pause()
            if self._handle_owned_car_dialog(label):
                handled += 1
                deadline = time.time() + 2.5
                continue
            time.sleep(0.25)

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
        self.app.state.set_task("自动抽奖", self.app.state.wheelspin_counter, progress_total)

    def _prepare_my_horizon_menu(self) -> bool:
        self.app.log("准备验证/进入菜单...")
        if not self.app.services.recovery.enter_menu():
            return False

        self.app.log("切换到我的地平线...")
        for _ in range(2):
            if not self.app.state.is_running:
                return False
            self.app.services.input_actions.hw_press("pagedown", delay=0.15)
            time.sleep(0.3)
        return True

    def _find_wheelspin_entry(self, reference_path: str, label: str, timeout: float):
        self.app.log(f"定位{label}入口...")
        pos_wheelspin = self.app.services.image_waits.wait_for_image_sift(
            reference_path,
            region=self.app.services.game_window.regions["全界面"],
            min_inliers=50,
            ratio=0.75,
            max_features=2500,
            timeout=timeout,
            interval=0.3,
        )
        if not pos_wheelspin:
            self.app.log(f"未找到{label}入口。")
        return pos_wheelspin

    def _enter_wheelspin_entry(self, reference_path: str, label: str) -> bool:
        if not os.path.exists(get_img_path(reference_path)):
            self.app.log(f"缺少{label}入口参考图：images/{reference_path}")
            return False

        pos_wheelspin = self._find_wheelspin_entry(reference_path, label, timeout=1.5)
        if pos_wheelspin:
            self.app.log(f"已在当前页面找到{label}入口。")
        else:
            if not self._prepare_my_horizon_menu():
                return False
            pos_wheelspin = self._find_wheelspin_entry(reference_path, label, timeout=12)

        if not pos_wheelspin:
            return False

        self.app.services.input_actions.game_click(pos_wheelspin)
        time.sleep(1.0)
        self.app.services.input_actions.hw_press("enter")
        time.sleep(0.8)
        self.app.log(f"已进入{label}并确认。")
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
            self.app.log(f"{label}次数为 0，跳过。")
            return True

        self.app.log(f"开始执行{label}循环。")
        last_state_time = time.time()
        completed_count = 0

        while self.app.state.is_running and (use_all or completed_count < target_count):
            self.app.services.runtime.check_pause()

            if self._handle_owned_car_dialog(label):
                last_state_time = time.time()
                continue

            state = self._detect_wheelspin_footer_state()

            if state =="not_in_wheelspin":
                self.app.log(f"未检测到不在{label}界面，终止{label}流程。")
                return True

            elif state == "skip":
                self.app.log("检测到抽奖动画，可跳过，按 Enter。")
                self.app.services.input_actions.hw_press("enter")
                last_state_time = time.time()
                time.sleep(1.0)
                continue

            elif state == "claim_again":
                completed_count += 1
                self.app.state.wheelspin_counter += 1
                self._update_wheelspin_progress(progress_total)

                if not use_all and completed_count >= target_count:
                    self.app.log(f"{label} {completed_count}/{target_count} 完成，领取奖励并退出。")
                    self.app.services.input_actions.hw_press("esc")
                    time.sleep(1.5)
                    self._clear_owned_car_dialogs(label, duplicate_popup_limit)
                    return True

                progress_text = f"{completed_count}/{target_count}" if not use_all else f"已完成 {completed_count} 次"
                self.app.log(f"{label} {progress_text}，领取奖励并继续下一次。")
                self.app.services.input_actions.hw_press("enter")
                last_state_time = time.time()
                time.sleep(1.5)
                continue

            elif state == "claim":
                completed_count += 1
                self.app.state.wheelspin_counter += 1
                self._update_wheelspin_progress(progress_total)
                if use_all:
                    self.app.log(f"{label}已用完，累计完成 {completed_count} 次，领取奖励后结束。")
                elif completed_count >= target_count:
                    self.app.log(f"{label} {completed_count}/{target_count} 完成，领取奖励后退出。")
                else:
                    self.app.log(f"{label}机会已用完，当前进度 {completed_count}/{target_count}，领取奖励后结束。")
                self.app.services.input_actions.hw_press("enter")
                time.sleep(1.5)
                self._clear_owned_car_dialogs(label, duplicate_popup_limit)
                return True

            if time.time() - last_state_time > self.STATE_TIMEOUT_SECONDS:
                self.app.log("等待抽奖界面底部提示超时，自动抽奖流程停止。")
                return False

            time.sleep(0.4)

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
            self.app.log(f"{label}次数为 0，跳过。")
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
        super_use_all: bool = False,
        normal_use_all: bool = False,
    ) -> bool:
        progress_total = self._fixed_progress_total(
            target_count,
            normal_count,
            super_use_all,
            normal_use_all,
        )
        self._update_wheelspin_progress(progress_total)

        tasks = (
            ("超级抽奖", SUPER_WHEELSPIN_REFERENCE, target_count, super_use_all, SUPER_DUPLICATE_POPUP_LIMIT),
            ("普通抽奖", NORMAL_WHEELSPIN_REFERENCE, normal_count, normal_use_all, NORMAL_DUPLICATE_POPUP_LIMIT),
        )

        if not any(use_all or int(count) > 0 for _, _, count, use_all, _ in tasks):
            self.app.log("自动抽奖次数均为 0，跳过。")
            return True

        for label, reference_path, count, use_all, duplicate_popup_limit in tasks:
            if not self.app.state.is_running:
                return False
            if not self._run_wheelspin_type(
                label,
                reference_path,
                count,
                use_all,
                progress_total,
                duplicate_popup_limit,
            ):
                return False

        return True
