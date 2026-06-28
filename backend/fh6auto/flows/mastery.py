from __future__ import annotations
from collections.abc import Callable

from ..recovery import RecoveryService
from ..window import GameWindowService
from ..backend.config_service import BackendConfigService
from ..backend.state import RuntimeState
from ..input.actions import InputActionsService
from ..vision.car_cards import CarCardPageSelector, ConsumableCarCardSearchOptions
from ..vision.manufacturer import ManufacturerDetector
from ..vision.matcher import ImageMatcherService
from ..vision.player_stats import PlayerStatsDetector
from ..vision.polling import ImageWaitsService


class MasteryFlow:
    NOT_FOUND_PAGE_LIMIT = 5

    def __init__(
        self,
        *,
        state: RuntimeState,
        config: BackendConfigService,
        game_window: GameWindowService,
        input_actions: InputActionsService,
        image_matcher: ImageMatcherService,
        image_waits: ImageWaitsService,
        manufacturer: ManufacturerDetector,
        player_stats: PlayerStatsDetector,
        recovery: RecoveryService,
        sleep: Callable[[float], None],
        log: Callable[..., None],
    ) -> None:
        self.state = state
        self.config = config
        self.game_window = game_window
        self.input_actions = input_actions
        self.image_matcher = image_matcher
        self.image_waits = image_waits
        self.manufacturer = manufacturer
        self.player_stats = player_stats
        self.recovery = recovery
        self.sleep = sleep
        self.log = log
        self.car_cards = CarCardPageSelector(
            image_matcher=self.image_matcher,
            input_actions=self.input_actions,
            sleep=self.sleep,
            log=self.log,
        )

    def _skill_points_per_car(self) -> int:
        raw_points = self.config.values.get("calc_c", "30")
        digits = "".join(ch for ch in str(raw_points) if ch.isdigit())
        try:
            points = int(digits)
        except Exception:
            points = 30
        return max(1, points)

    def _check_no_skill_points(self):
        pos = self.image_matcher.find_image_sift(
            "SPNE.png",
            min_inliers=24,
        )
        if pos:
            self.log("检测到技能点不足，准备提前结束熟练度加点。", level="debug")
            sleep = self.sleep
            self.input_actions.hw_press("enter")
            sleep(0.8)
            for _ in range(3):
                self.input_actions.hw_press("esc")
                sleep(1.0)
            return True
        else:
            return False

    def _enter_consumable_manufacturer_list(self) -> bool:
        sleep = self.sleep
        self.log("进入我的车辆.", level="debug")
        self.input_actions.hw_press("enter")
        sleep(2.0)

        self.input_actions.hw_press("y")
        sleep(0.5)
        for _ in range(2):
            self.input_actions.hw_press("down")
            sleep(0.1)
        self.input_actions.hw_press("enter")
        sleep(0.1)
        self.input_actions.hw_press("esc")
        sleep(0.5)

        self.input_actions.hw_press("backspace")
        sleep(0.5)

        manufacturer_pos = self.manufacturer.scan_for_text("斯巴鲁", threshold=0.75, label="消耗品制造商")
        if not manufacturer_pos:
            self.log("选择制造商失败", level="warning")
            return False

        self.input_actions.game_click(manufacturer_pos)
        sleep(1.0)
        return True

    def _remove_selected_consumable_car(self) -> bool:
        sleep = self.sleep
        self.log("删除符合条件的消耗品车辆...", level="debug")
        self.input_actions.move_to_game_coord(5, 5)
        sleep(0.5)

        # 若点击前车辆已选中，会直接弹出“选择操作”菜单；否则需要 Enter 打开菜单。
        pos_cancel = self.image_waits.wait_for_footer_text_ui("取消", timeout=1.0, interval=0.2)
        if not pos_cancel:
            self.input_actions.hw_press("enter")
            sleep(0.5)

        for _ in range(4):
            self.input_actions.hw_press("down")
            sleep(0.1)
        self.input_actions.hw_press("enter")
        sleep(0.5)

        self.input_actions.hw_press("down")
        sleep(0.1)
        self.input_actions.hw_press("enter")
        sleep(1.0)

        self.state.sc_count += 1
        self.log(f"已移除消耗品车辆，累计移除 {self.state.sc_count} 辆。", level="debug")
        return True

    def _drive_selected_car_and_apply_mastery(self, effective_target: int) -> str | None:
        sleep = self.sleep
        self.log("确认上车并驾驶当前车辆...", level="debug")
        self.input_actions.hw_press("enter")
        sleep(1.0)
        self.input_actions.hw_press("enter")

        sleep(10.0)
        pos_drive = self.image_waits.wait_for_footer_text_ui("驾驶")
        if not pos_drive:
            self.log("上新车后的检视，底部未找到“驾驶”", level="warning")
            return "error"

        # 退出新车检视界面，返回车辆菜单
        self.input_actions.hw_press("esc")
        sleep(1.5)

        # 点击 升级与调校
        self.input_actions.hw_press("down")
        sleep(0.1)
        self.input_actions.hw_press("enter")
        sleep(1.0)

        # 点击 车辆专精
        for _ in range(7):
            self.input_actions.hw_press("down")
            sleep(0.1)
        self.input_actions.hw_press("enter")
        sleep(1.0)

        self.input_actions.hw_press("enter")
        sleep(1.2)
        if self._check_no_skill_points():
            return "技能点不足"

        for dk in self.config.values["skill_dirs"]:
            self.input_actions.hw_press(dk)
            sleep(0.2)
            self.input_actions.hw_press("enter")
            sleep(1.2)
            if self._check_no_skill_points():
                return "技能点不足"

        self.state.mastery_counter += 1
        self.state.set_task("加点&删车", self.state.mastery_counter, effective_target)

        self.input_actions.hw_press("esc")
        sleep(1.2)
        self.input_actions.hw_press("esc")
        sleep(0.8)
        self.input_actions.hw_press("up", delay=0.15)
        sleep(0.8)
        return None

    # ==========================================
    # --- 模块：熟练度加点 ---
    # ==========================================
    def logic_mastery(self, target_count, *, use_all: bool = False):
        target_count = max(0, int(target_count))
        use_all = bool(use_all)
        sleep = self.sleep
        start_count = self.state.mastery_counter
        start_remove_count = self.state.sc_count

        def finish(reason: str | None = None) -> bool:
            suffix = f"原因：{reason}" if reason else ""
            removed_count = self.state.sc_count - start_remove_count
            self.log(f"熟练度加点流程结束：完成 {self.state.mastery_counter - start_count} 次，移除 {removed_count} 辆。{suffix}")
            return True

        if not use_all and self.state.mastery_counter >= target_count:
            return finish()

        if use_all:
            self.state.set_task("熟练度加点")
        else:
            self.state.set_task("熟练度加点", self.state.mastery_counter, target_count)
        self.log("准备验证/进入菜单...", level="debug")
        if not self.recovery.enter_menu():
            return False

        self.log("进入车辆与收藏...", level="debug")
        self.input_actions.hw_press("pagedown", delay=0.15)
        sleep(1.0)

        available_skill_points = self.player_stats.find_current_skill_points_value()
        if available_skill_points is None:
            self.log("熟练度加点：未能通过 OCR 识别当前技术点数，无法计算动态加点数量。", level="warning")
            return False

        points_per_car = self._skill_points_per_car()
        remaining_user_count = max(0, target_count - self.state.mastery_counter)
        affordable_count = available_skill_points // points_per_car
        planned_count = affordable_count if use_all else min(remaining_user_count, affordable_count)
        effective_target = self.state.mastery_counter + planned_count
        target_text = "目标模式：用完可用技术点" if use_all else f"用户剩余目标 {remaining_user_count} 辆"
        self.log(
            f"熟练度加点：当前技术点 {available_skill_points}，单车消耗 {points_per_car} 点，"
            f"{target_text}，动态最多可加点 {affordable_count} 辆，预计处理 {planned_count} 辆。"
        )

        if planned_count <= 0:
            reason = "当前技术点不足以完成一辆车加点" if affordable_count <= 0 else "执行次数为 0"
            return finish(reason)

        self.state.set_task("熟练度加点", self.state.mastery_counter, effective_target)

        pos_buycar = self.image_waits.wait_for_image_sift(
            "buy_new_used_cars.png",
            region=self.game_window.regions["左"],
            min_inliers=20,
            timeout=15,
            interval=0.3,
        )
        if not pos_buycar:
            self.log("未识别到 购买新车与二手车", level="warning")
            return False

        self.input_actions.game_click(pos_buycar)
        sleep(0.8)
        self.input_actions.hw_press("enter")
        sleep(2)

        pos_bs = self.image_waits.wait_for_footer_text_ui("选择")
        if not pos_bs:
            self.log("未找到 选择", level="warning")
            return False

        self.input_actions.hw_press("pagedown", delay=0.15)
        self.log("进入车辆界面...", level="debug")
        sleep(0.5)

        current_page_position = max(0, int(self.state.memory_car_page or 0))
        list_needs_reset = True

        while self.state.mastery_counter < effective_target:
            if list_needs_reset and not self._enter_consumable_manufacturer_list():
                return False

            start_page = current_page_position if list_needs_reset else 0
            base_page = 0 if list_needs_reset else current_page_position

            car_result = self.car_cards.find_consumable_action(
                ConsumableCarCardSearchOptions(
                    label="消耗品车辆",
                    max_pages=self.NOT_FOUND_PAGE_LIMIT,
                    start_page=start_page,
                )
            )

            if not car_result:
                self.log(
                    f"从当前页码 {base_page + start_page} 开始连续翻找 {self.NOT_FOUND_PAGE_LIMIT} 页仍未找到可处理消耗品车辆，视为车辆已全部处理完毕。",
                    level="debug",
                )
                self.state.memory_car_page = 0  # 没找到说明车刷完了，清零记忆
                for _ in range(2):
                    self.input_actions.hw_press("esc")
                    sleep(0.8)
                return finish("未找到可处理消耗品车辆")

            current_page_position = base_page + car_result.page_index
            self.state.memory_car_page = current_page_position
            self.input_actions.game_click(car_result.position)
            self.log(
                f"锁定目标车辆，动作: {car_result.action}，已记录当前页码: {current_page_position}",
                level="debug",
            )
            sleep(0.5)

            if car_result.action == "remove":
                if not self._remove_selected_consumable_car():
                    return False
                list_needs_reset = False
                continue

            mastery_result = self._drive_selected_car_and_apply_mastery(effective_target)
            if mastery_result == "技能点不足":
                return finish("技能点不足")
            if mastery_result is not None:
                return False
            list_needs_reset = True

        self.input_actions.hw_press("esc")
        sleep(1.2)
        self.input_actions.hw_press("esc")
        sleep(1.2)
        return finish()
