from __future__ import annotations
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..backend.app import BackendApp


class BuyCarFlow:
    def __init__(self, app: BackendApp) -> None:
        self.app = app


    # ==========================================
    # --- 模块：买车 ---
    # ==========================================
    def logic_buy_car(self, target_count):
        sleep = self.app.services.runtime.sleep
        start_count = self.app.state.car_counter
        if self.app.state.car_counter >= target_count:
            self.app.log("批量买车流程结束：完成 0 次。")
            return True

        self.app.state.set_task("批量买车", self.app.state.car_counter, target_count)

        self.app.log("准备验证/进入菜单...", level="debug")
        if not self.app.services.recovery.enter_menu():
            return False

        pos_collectionjournal = self.app.services.image_waits.wait_for_image_sift(
            "collectionjournal.png",
            region=self.app.services.game_window.regions["左"],
            min_inliers=20,
            timeout=30,
            interval=0.4,
        )
        if not pos_collectionjournal:
            self.app.log("未找到收集簿", level="warning")
            return False

        self.app.services.input_actions.game_click(pos_collectionjournal, double=True)
        sleep(1.0)

        pos_masterexplorer = self.app.services.image_waits.wait_for_image_sift(
            "masterexplorer.png",
            region=self.app.services.game_window.regions["全界面"],
            min_inliers=20,
            timeout=30,
            interval=0.4,
        )
        if not pos_masterexplorer:
            self.app.log("未找到探索", level="warning")
            return False

        self.app.services.input_actions.game_click(pos_masterexplorer, double=True)
        sleep(0.6)

        pos_carcollection = self.app.services.image_waits.wait_for_image_sift(
            "carcollection.png",
            region=self.app.services.game_window.regions["全界面"],
            min_inliers=20,
            timeout=30,
            interval=0.3,
        )
        if not pos_carcollection:
            self.app.log("未找到车辆收集", level="warning")
            return False

        self.app.services.input_actions.game_click(pos_carcollection, double=True)
        sleep(1.0)

        self.app.services.input_actions.hw_press("backspace")
        sleep(0.5)

        manufacturer_pos = self.app.services.image_waits.scan_for_manufacturer_text(
            "斯巴鲁",
            threshold=0.75,
            label="消耗品制造商",
        )
        if not manufacturer_pos:
            self.app.log("未找到制造商", level="warning")
            return False

        self.app.services.input_actions.game_click(manufacturer_pos)
        sleep(0.8)
        self.app.services.input_actions.hw_press("down")
        sleep(0.4)

        pos_22b = self.app.services.image_waits.wait_for_car_card(
            "consumablecar.png",
            region=self.app.services.game_window.regions["全界面"],
            final_threshold=0.80,
            title_threshold=0.74,
            pi_threshold=0.84,
            rarity_threshold=0.70,
            body_threshold=0.58,
            timeout=8,
            interval=0.3,
        )
        if not pos_22b:
            self.app.log("未找到消耗品车辆", level="warning")
            return False

        self.app.services.input_actions.game_click(pos_22b, double=True)
        sleep(1.0)

        while self.app.state.car_counter < target_count:
            self.app.services.input_actions.hw_press("space")
            sleep(0.6)
            self.app.services.input_actions.move_to_game_coord(5, 5)
            self.app.services.input_actions.hw_press("down")
            sleep(0.2)
            self.app.services.input_actions.move_to_game_coord(5, 5)
            self.app.services.input_actions.hw_press("enter")
            sleep(0.6)
            self.app.services.input_actions.move_to_game_coord(5, 5)
            self.app.services.input_actions.hw_press("enter")
            sleep(0.6)
            self.app.services.input_actions.move_to_game_coord(5, 5)
            self.app.services.input_actions.hw_press("enter")
            sleep(0.7)

            self.app.state.car_counter += 1
            self.app.state.set_task("批量买车", self.app.state.car_counter, target_count)

        for _ in range(5):
            self.app.services.input_actions.hw_press("esc")
            sleep(0.8)

        self.app.log(f"批量买车流程结束：完成 {self.app.state.car_counter - start_count} 次。")
        return True
