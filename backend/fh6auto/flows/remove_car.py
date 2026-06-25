from __future__ import annotations
from typing import TYPE_CHECKING

from ..vision.matcher import CarCardPageSelector, CarCardSearchOptions

if TYPE_CHECKING:
    from ..backend.app import BackendApp


class RemoveCarFlow:
    def __init__(self, app: BackendApp) -> None:
        self.app = app
        self.car_cards = CarCardPageSelector(app)

    # ==========================================
    # --- 模块：移除车辆 ---
    # ==========================================
    def find_and_remove_consumable_car(self, target_count, *, use_all: bool = False):
        target_count = max(0, int(target_count))
        use_all = bool(use_all)
        sleep = self.app.services.runtime.sleep
        start_count = self.app.state.sc_count

        def finish(reason: str | None = None) -> bool:
            suffix = f"原因：{reason}" if reason else ""
            self.app.log(f"移除车辆流程结束：完成 {self.app.state.sc_count - start_count} 次。{suffix}")
            return True

        if not use_all and self.app.state.sc_count >= target_count:
            return finish()

        if use_all:
            self.app.state.set_task("移除车辆")
        else:
            self.app.state.set_task("移除车辆", self.app.state.sc_count, target_count)

        self.app.log("准备验证/进入菜单。", level="debug")
        if not self.app.services.recovery.enter_menu():
            return False

        # self.app.log("进入菜单页面：车辆")
        self.app.services.input_actions.hw_press("pagedown", delay=0.15)
        sleep(1.0)

        pos_buycar = self.app.services.image_matcher.find_image_sift(
            "buy_new_used_cars.png",
            self.app.services.game_window.regions["左"],
            20,
        )
        if not pos_buycar:
            self.app.log("未识别到 购买新车与二手车", level="warning")
            return False

        self.app.services.input_actions.game_click(pos_buycar)
        # 点击“购买新车与二手车”
        self.app.services.input_actions.move_to_game_coord(5, 5)
        sleep(0.8)
        self.app.services.input_actions.hw_press("enter")
        sleep(1)

        pos_bs = self.app.services.image_waits.wait_for_footer_text_ui(
            "选择",
            region=self.app.services.game_window.regions["下"],
        )
        if not pos_bs:
            self.app.log("未找到 选择", level="warning")
            return False

        # 进入“我的车辆”
        self.app.services.input_actions.hw_press("pagedown")
        sleep(1.0)
        self.app.services.input_actions.hw_press("enter")
        sleep(2.0)

        # 筛选重复项
        self.app.services.input_actions.hw_press("y")
        sleep(1.0)
        self.app.services.input_actions.hw_press("down")
        sleep(0.1)
        self.app.services.input_actions.hw_press("down")
        sleep(0.1)
        self.app.services.input_actions.hw_press("enter")
        sleep(0.5)
        self.app.services.input_actions.hw_press("esc")
        sleep(0.5)

        # 切换到消耗品制造商
        self.app.log("切换到消耗品制造商...", level="debug")
        self.app.services.input_actions.hw_press("backspace")
        manufacturer_pos = self.app.services.image_waits.scan_for_manufacturer_text(
            "斯巴鲁", threshold=0.75, label="消耗品制造商"
        )
        if not manufacturer_pos:
            self.app.log("未找到制造商", level="warning")
            return False

        self.app.services.input_actions.game_click(manufacturer_pos)
        sleep(1.0)

        self.app.log("开始删除车辆", level="debug")

        while use_all or self.app.state.sc_count < target_count:
            car_result = self.car_cards.find(
                CarCardSearchOptions(
                    "removecarobject.png",
                    label="可移除消耗品车辆",
                    excluded_tag_text="全新",
                    exclude_driving=True,
                    max_pages=5,
                    page_timeout=0.5,
                    interval=0.2,
                )
            )

            if not car_result:
                self.app.log("连续翻找 5 页仍未搜索到目标车辆，视为车辆已全部清理完毕。", level="debug")
                break

            self.app.log("精准锁定目标车辆，执行点击...", level="debug")
            self.app.services.input_actions.game_click(car_result.position)
            self.app.services.input_actions.move_to_game_coord(5, 5)
            sleep(0.5)  # 等待点击后的反应

            # 若该车在点击前已经被选中，则会直接弹出“选择操作”菜单，否则会将列表车辆列表先滑动到指定位置
            pos_cancel = self.app.services.ocr.find_footer_text_ui("取消")
            if not pos_cancel:
                self.app.services.input_actions.hw_press("enter")
                sleep(0.5)

            for i in range(4):
                self.app.services.input_actions.hw_press("down")
                sleep(0.1)
            self.app.services.input_actions.hw_press("enter")

            sleep(0.8)  # 等待“你确定要移除吗”的确认弹窗

            # 确认移除操作 (按向下选"嗯"，然后回车)
            self.app.log("确认移除...", level="debug")
            self.app.services.input_actions.hw_press("down")
            sleep(0.1)
            self.app.services.input_actions.hw_press("enter")
            sleep(1.0)

            self.app.state.sc_count += 1
            progress_total = self.app.state.sc_count if use_all else target_count
            self.app.state.set_task("移除车辆", self.app.state.sc_count, progress_total)
            progress_text = (
                f"{self.app.state.sc_count}/全部" if use_all else f"{self.app.state.sc_count}/{target_count}"
            )
            self.app.log(f"成功移除车辆！当前进度: {progress_text}", level="debug")

        # 循环结束，退回上一级
        for _ in range(3):
            self.app.services.input_actions.hw_press("esc")
            sleep(1.0)

        return finish()
