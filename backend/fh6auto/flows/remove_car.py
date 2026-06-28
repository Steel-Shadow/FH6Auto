from __future__ import annotations
from collections.abc import Callable

from ..recovery import RecoveryService
from ..window import GameWindowService
from ..backend.state import RuntimeState
from ..input.actions import InputActionsService
from ..vision.car_cards import CarCardPageSelector, CarCardSearchOptions
from ..vision.footer import FooterDetector
from ..vision.manufacturer import ManufacturerDetector
from ..vision.matcher import ImageMatcherService
from ..vision.polling import ImageWaitsService


class RemoveCarFlow:
    def __init__(
        self,
        *,
        state: RuntimeState,
        game_window: GameWindowService,
        input_actions: InputActionsService,
        image_matcher: ImageMatcherService,
        image_waits: ImageWaitsService,
        manufacturer: ManufacturerDetector,
        footer: FooterDetector,
        recovery: RecoveryService,
        sleep: Callable[[float], None],
        log: Callable[..., None],
    ) -> None:
        self.state = state
        self.game_window = game_window
        self.input_actions = input_actions
        self.image_matcher = image_matcher
        self.image_waits = image_waits
        self.manufacturer = manufacturer
        self.footer = footer
        self.recovery = recovery
        self.sleep = sleep
        self.log = log
        self.car_cards = CarCardPageSelector(
            image_matcher=self.image_matcher,
            input_actions=self.input_actions,
            sleep=self.sleep,
            log=self.log,
        )

    # ==========================================
    # --- 模块：移除车辆 ---
    # ==========================================
    def find_and_remove_consumable_car(self, target_count, *, use_all: bool = False):
        target_count = max(0, int(target_count))
        use_all = bool(use_all)
        start_count = self.state.sc_count

        def finish(reason: str | None = None) -> bool:
            suffix = f"原因：{reason}" if reason else ""
            self.log(f"移除车辆流程结束：完成 {self.state.sc_count - start_count} 次。{suffix}")
            return True

        if not use_all and self.state.sc_count >= target_count:
            return finish()

        if use_all:
            self.state.set_task("移除车辆")
        else:
            self.state.set_task("移除车辆", self.state.sc_count, target_count)

        self.log("准备验证/进入菜单。", level="debug")
        if not self.recovery.enter_menu():
            return False

        # 进入菜单页面：车辆
        self.input_actions.move_to_game_coord(5, 5)
        self.input_actions.hw_press("pagedown")
        self.sleep(0.5)

        pos_buycar = self.image_matcher.find_image_sift(
            "buy_new_used_cars.png",
            self.game_window.regions["左"],
            20,
        )
        if not pos_buycar:
            self.log("未识别到 购买新车与二手车", level="warning")
            return False

        self.input_actions.hw_press("enter")
        self.sleep(1.0)

        # 筛选重复项
        self.input_actions.hw_press("y")
        self.sleep(1.0)
        self.input_actions.hw_press("down")
        self.sleep(0.1)
        self.input_actions.hw_press("down")
        self.sleep(0.1)
        self.input_actions.hw_press("enter")
        self.sleep(0.5)
        self.input_actions.hw_press("esc")
        self.sleep(0.5)

        # 切换到消耗品制造商
        self.log("切换到消耗品制造商...", level="debug")
        self.input_actions.hw_press("backspace")
        self.sleep(1.0)
        manufacturer_pos = self.manufacturer.scan_for_text("斯巴鲁", threshold=0.75, label="消耗品制造商")
        if not manufacturer_pos:
            self.log("未找到制造商", level="warning")
            return False

        self.input_actions.game_click(manufacturer_pos)
        self.sleep(1.0)

        self.log("开始删除车辆", level="debug")

        while use_all or self.state.sc_count < target_count:
            car_result = self.car_cards.find(
                CarCardSearchOptions(
                    "removecarobject.png",
                    label="可移除消耗品车辆",
                    excluded_tag_text="全新",
                    exclude_driving=True,
                    max_pages=5,
                )
            )

            if not car_result:
                self.log("连续翻找 5 页仍未搜索到目标车辆，视为车辆已全部清理完毕。", level="debug")
                break

            self.log("精准锁定目标车辆，执行点击...", level="debug")
            self.input_actions.game_click(car_result.position)
            self.input_actions.move_to_game_coord(5, 5)
            self.sleep(0.5)  # 等待点击后的反应

            # 若该车在点击前已经被选中，则会直接弹出“选择操作”菜单，否则会将列表车辆列表先滑动到指定位置
            pos_cancel = self.footer.find_text("取消")
            if not pos_cancel:
                self.input_actions.hw_press("enter")
                self.sleep(0.5)

            for _ in range(4):
                self.input_actions.hw_press("down")
                self.sleep(0.1)
            self.input_actions.hw_press("enter")

            self.sleep(0.5)  # 等待“你确定要移除吗”的确认弹窗

            # 确认移除操作 (按向下选"嗯"，然后回车)
            self.log("确认移除...", level="debug")
            self.input_actions.hw_press("down")
            self.sleep(0.1)
            self.input_actions.hw_press("enter")
            self.sleep(1.0)

            self.state.sc_count += 1
            progress_total = self.state.sc_count if use_all else target_count
            self.state.set_task("移除车辆", self.state.sc_count, progress_total)
            progress_text = f"{self.state.sc_count}/全部" if use_all else f"{self.state.sc_count}/{target_count}"
            self.log(f"成功移除车辆！当前进度: {progress_text}", level="debug")

        # 循环结束，退回上一级
        for _ in range(3):
            self.input_actions.hw_press("esc")
            self.sleep(1.0)

        return finish()
