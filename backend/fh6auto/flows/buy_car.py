from __future__ import annotations
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..backend.app import BackendApp


class BuyCarFlow:
    def __init__(self, app: BackendApp) -> None:
        self.app = app

    def _cost_per_car(self) -> int:
        raw_cost = self.app.services.config.values.get("calc_b", "81700")
        digits = "".join(ch for ch in str(raw_cost) if ch.isdigit())
        try:
            cost = int(digits)
        except Exception:
            cost = 81700
        return max(1, cost)

    def _skill_points_per_car(self) -> int:
        raw_points = self.app.services.config.values.get("calc_c", "30")
        digits = "".join(ch for ch in str(raw_points) if ch.isdigit())
        try:
            points = int(digits)
        except Exception:
            points = 30
        return max(1, points)

    # ==========================================
    # --- 模块：买车 ---
    # ==========================================
    def logic_buy_car(self, target_count):
        sleep = self.app.services.runtime.sleep
        target_count = max(0, int(target_count))
        start_count = self.app.state.car_counter
        if self.app.state.car_counter >= target_count:
            self.app.log("批量买车流程结束：完成 0 次。")
            return True

        self.app.state.set_task("批量买车", self.app.state.car_counter, target_count)

        self.app.log("准备验证/进入菜单...", level="debug")
        if not self.app.services.recovery.enter_menu():
            return False

        current_cr = self.app.services.ocr.find_current_credit_value()
        if current_cr is None:
            self.app.log("批量买车：未能通过 OCR 识别当前 CR，无法计算动态可购买数量。", level="warning")
            return False

        cost_per_car = self._cost_per_car()
        remaining_user_count = max(0, target_count - self.app.state.car_counter)
        affordable_count = current_cr // cost_per_car

        self.app.services.input_actions.hw_press("pagedown")
        sleep(0.5)
        available_skill_points = self.app.services.ocr.find_current_skill_points_value()
        if available_skill_points is None:
            self.app.log("批量买车：未能通过 OCR 识别当前技术点数，无法计算动态可购买数量。", level="warning")
            return False

        points_per_car = self._skill_points_per_car()
        skill_limited_count = available_skill_points // points_per_car
        planned_count = min(remaining_user_count, affordable_count, skill_limited_count)
        effective_target = self.app.state.car_counter + planned_count
        self.app.log(
            f"批量买车：当前 CR {current_cr:,}，单车成本 CR {cost_per_car:,}，"
            f"当前技术点 {available_skill_points}，单车后续消耗 {points_per_car} 点，"
            f"用户剩余目标 {remaining_user_count} 辆，CR 最多可买 {affordable_count} 辆，"
            f"技术点最多可处理 {skill_limited_count} 辆，预计购买 {planned_count} 辆。"
        )

        if planned_count <= 0:
            if remaining_user_count <= 0:
                reason = "执行次数为 0"
            elif affordable_count <= 0:
                reason = "当前 CR 不足以购买车辆"
            else:
                reason = "当前技术点不足以处理一辆车"
            self.app.log(f"批量买车流程结束：完成 0 次。原因：{reason}。")
            return True

        self.app.state.set_task("批量买车", self.app.state.car_counter, effective_target)

        self.app.services.input_actions.hw_press("pageup")
        sleep(0.5)

        pos_collectionjournal = self.app.services.image_matcher.find_image_sift(
            "collectionjournal.png",
            region=self.app.services.game_window.regions["左"],
            min_inliers=20,
        )
        if not pos_collectionjournal:
            self.app.log("未找到收集簿", level="warning")
            return False

        self.app.services.input_actions.game_click(pos_collectionjournal, double=True)
        sleep(1.0)

        pos_masterexplorer = self.app.services.image_waits.wait_for_image_sift(
            "masterexplorer.png",
            min_inliers=20,
        )
        if not pos_masterexplorer:
            self.app.log("未找到探索", level="warning")
            return False

        self.app.services.input_actions.game_click(pos_masterexplorer, double=True)
        sleep(0.6)

        pos_carcollection = self.app.services.image_waits.wait_for_image_sift(
            "carcollection.png",
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

        pos_22b = self.app.services.image_matcher.find_car_card(
            "consumablecar.png",
        )
        if not pos_22b:
            self.app.log("未找到消耗品车辆", level="warning")
            return False

        self.app.services.input_actions.game_click(pos_22b, double=True)
        sleep(1.0)

        while self.app.state.car_counter < effective_target:
            self.app.services.input_actions.hw_press("space")
            sleep(0.6)
            self.app.services.input_actions.move_to_game_coord(5, 5)
            self.app.services.input_actions.hw_press("down")
            sleep(0.2)
            self.app.services.input_actions.hw_press("enter")
            sleep(0.6)
            self.app.services.input_actions.hw_press("enter")
            sleep(0.6)
            self.app.services.input_actions.hw_press("enter")
            sleep(0.7)

            self.app.state.car_counter += 1
            self.app.state.set_task("批量买车", self.app.state.car_counter, effective_target)

        for _ in range(5):
            self.app.services.input_actions.hw_press("esc")
            sleep(0.8)

        self.app.log(f"批量买车流程结束：完成 {self.app.state.car_counter - start_count} 次。")
        return True
