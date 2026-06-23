from __future__ import annotations
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..backend.app import BackendApp


class MasteryFlow:
    NOT_FOUND_PAGE_LIMIT = 5

    def __init__(self, app: BackendApp) -> None:
        self.app = app

    def _skill_points_per_car(self) -> int:
        raw_points = self.app.services.config.values.get("calc_c", "30")
        digits = "".join(ch for ch in str(raw_points) if ch.isdigit())
        try:
            points = int(digits)
        except Exception:
            points = 30
        return max(1, points)

    def _check_no_skill_points(self):
        pos = self.app.services.image_matcher.find_image_sift(
            "SPNE.png",
            min_inliers=24,
        )
        if pos:
            self.app.log("检测到技能点不足，准备提前结束熟练度加点。", level="debug")
            sleep = self.app.services.runtime.sleep
            self.app.services.input_actions.hw_press("enter")
            sleep(0.8)
            for _ in range(3):
                self.app.services.input_actions.hw_press("esc")
                sleep(1.0)
            return True
        else:
            return False

    # ==========================================
    # --- 模块：熟练度加点 ---
    # ==========================================
    def logic_mastery(self, target_count, *, use_all: bool = False):
        target_count = max(0, int(target_count))
        use_all = bool(use_all)
        sleep = self.app.services.runtime.sleep
        start_count = self.app.state.mastery_counter

        def finish(reason: str | None = None) -> bool:
            suffix = f"原因：{reason}" if reason else ""
            self.app.log(f"熟练度加点流程结束：完成 {self.app.state.mastery_counter - start_count} 次。{suffix}")
            return True

        if not use_all and self.app.state.mastery_counter >= target_count:
            return finish()

        if use_all:
            self.app.state.set_task("熟练度加点")
        else:
            self.app.state.set_task("熟练度加点", self.app.state.mastery_counter, target_count)
        self.app.log("准备验证/进入菜单...", level="debug")
        if not self.app.services.recovery.enter_menu():
            return False

        self.app.log("进入车辆与收藏...", level="debug")
        self.app.services.input_actions.hw_press("pagedown", delay=0.15)
        sleep(1.0)

        available_skill_points = self.app.services.ocr.find_current_skill_points_value()
        if available_skill_points is None:
            self.app.log("熟练度加点：未能通过 OCR 识别当前技术点数，无法计算动态加点数量。", level="warning")
            return False

        points_per_car = self._skill_points_per_car()
        remaining_user_count = max(0, target_count - self.app.state.mastery_counter)
        affordable_count = available_skill_points // points_per_car
        planned_count = affordable_count if use_all else min(remaining_user_count, affordable_count)
        effective_target = self.app.state.mastery_counter + planned_count
        target_text = "目标模式：用完可用技术点" if use_all else f"用户剩余目标 {remaining_user_count} 辆"
        self.app.log(
            f"熟练度加点：当前技术点 {available_skill_points}，单车消耗 {points_per_car} 点，"
            f"{target_text}，动态最多可加点 {affordable_count} 辆，预计处理 {planned_count} 辆。"
        )

        if planned_count <= 0:
            reason = "当前技术点不足以完成一辆车加点" if affordable_count <= 0 else "执行次数为 0"
            return finish(reason)

        self.app.state.set_task("熟练度加点", self.app.state.mastery_counter, effective_target)

        pos_buycar = self.app.services.image_waits.wait_for_image_sift(
            "buy_new_used_cars.png",
            region=self.app.services.game_window.regions["左"],
            min_inliers=20,
            timeout=15,
            interval=0.3,
        )
        if not pos_buycar:
            self.app.log("未识别到 购买新车与二手车", level="warning")
            return False

        self.app.services.input_actions.game_click(pos_buycar)
        sleep(0.8)
        self.app.services.input_actions.hw_press("enter")
        sleep(2)

        pos_bs = self.app.services.image_waits.wait_for_footer_text_ui(
            "选择",
            region=self.app.services.game_window.regions["下"],
            timeout=3.0,
            interval=0.3,
        )
        if not pos_bs:
            self.app.log("未找到 选择", level="warning")
            return False

        self.app.services.input_actions.hw_press("pagedown", delay=0.15)
        self.app.log("进入车辆界面...", level="debug")
        sleep(0.5)

        while self.app.state.mastery_counter < effective_target:
            self.app.log("进入我的车辆.", level="debug")
            self.app.services.input_actions.hw_press("enter")
            sleep(2.0)
            self.app.services.input_actions.hw_press("backspace")
            sleep(1.0)

            manufacturer_pos = self.app.services.image_waits.scan_for_manufacturer_text(
                "斯巴鲁", threshold=0.75, label="消耗品制造商"
            )
            if not manufacturer_pos:
                self.app.log("选择制造商失败", level="warning")
                return False

            self.app.services.input_actions.game_click(manufacturer_pos)
            sleep(1.0)
            start_page = max(0, int(self.app.state.memory_car_page or 0))

            if start_page > 0:
                self.app.log(f"智能记忆触发：从第 {start_page} 页开始扫描...", level="debug")
                for _ in range(start_page):
                    for _ in range(4):
                        self.app.services.input_actions.hw_press("right", delay=0.06)
                        sleep(0.1)
                    sleep(0.15)  # 给一点点动画缓冲时间
            pos_target = None
            found_car = False
            current_page = start_page
            not_found_pages = 0

            while not_found_pages < self.NOT_FOUND_PAGE_LIMIT:
                self.app.log(
                    f"扫描全新消耗品车辆... (连续未找到: {not_found_pages}/{self.NOT_FOUND_PAGE_LIMIT})",
                    level="debug",
                )
                pos_target = self.app.services.image_waits.wait_for_car_card(
                    "newCC.png",
                    required_tag_text="全新",
                    final_threshold=0.78,
                    title_threshold=0.72,
                    pi_threshold=0.82,
                    rarity_threshold=0.68,
                    body_threshold=0.55,
                    tag_threshold=0.70,
                    timeout=1.5,
                    interval=0.2,
                    fast_mode=True,
                )

                if pos_target:
                    self.app.services.input_actions.game_click(pos_target)
                    found_car = True
                    self.app.state.memory_car_page = current_page
                    self.app.log(f"锁定目标车辆！已记录当前页码: {current_page}", level="debug")
                    break

                not_found_pages += 1
                if not_found_pages >= self.NOT_FOUND_PAGE_LIMIT:
                    break

                self.app.log(f"当前页面未找到全新车辆，向右翻页寻找... (第 {not_found_pages} 次翻页)", level="debug")
                for _ in range(4):
                    self.app.services.input_actions.hw_press("right", delay=0.06)
                    sleep(0.1)
                sleep(0.4)
                current_page += 1
            if not found_car:
                self.app.log(
                    f"从记忆页码 {start_page} 开始连续翻找 {self.NOT_FOUND_PAGE_LIMIT} 页仍未找到全新消耗品车辆，视为车辆已全部处理完毕。",
                    level="debug",
                )
                self.app.state.memory_car_page = 0  # 没找到说明车刷完了，清零记忆
                for _ in range(2):
                    self.app.services.input_actions.hw_press("esc")
                    sleep(0.8)
                return finish("未找到全新消耗品车辆")
            sleep(0.5)
            self.app.log("确认上车并驾驶当前车辆...", level="debug")
            self.app.services.input_actions.hw_press("enter")
            sleep(1.0)
            self.app.services.input_actions.hw_press("enter")

            sleep(10.0)
            pos_drive = self.app.services.image_waits.wait_for_footer_text_ui(
                "驾驶",
                region=self.app.services.game_window.regions["下"],
                timeout=10,
                interval=1.0,
            )
            if not pos_drive:
                self.app.log("上新车后的检视，底部未找到“驾驶”", level="warning")
                return False

            self.app.services.input_actions.hw_press("esc")
            sleep(1.0)

            pos_sjy = self.app.services.image_waits.wait_for_menu_text_ui(
                "升级与调校",
                region=self.app.services.game_window.regions["左下"],
            )
            if not pos_sjy:
                self.app.log("找不到升级页面", level="warning")
                return False

            self.app.services.input_actions.game_click(pos_sjy)

            pos_mastery = self.app.services.image_waits.wait_for_menu_text_ui(
                "车辆专精",
                region=self.app.services.game_window.regions["左下"],
            )
            if not pos_mastery:
                self.app.log("未找到车辆专精", level="warning")
                return False
            self.app.services.input_actions.game_click(pos_mastery)
            sleep(1.0)

            pos_exp = self.app.services.image_matcher.find_image_sift(
                "EXPwU.png",
                region=self.app.services.game_window.regions["左"],
                min_inliers=8,
            )
            if pos_exp:
                self.app.log("该车辆技能已点过，跳过计数", level="debug")
            else:
                self.app.services.input_actions.hw_press("enter")
                sleep(1.2)
                if self._check_no_skill_points():
                    return finish("技能点不足")

                for dk in self.app.services.config.values["skill_dirs"]:
                    self.app.services.input_actions.hw_press(dk)
                    sleep(0.2)
                    self.app.services.input_actions.hw_press("enter")
                    sleep(1.2)
                    if self._check_no_skill_points():
                        return finish("技能点不足")

                self.app.state.mastery_counter += 1
                self.app.state.set_task("熟练度加点", self.app.state.mastery_counter, effective_target)

            self.app.services.input_actions.hw_press("esc")
            sleep(1.2)
            self.app.services.input_actions.hw_press("esc")
            sleep(0.8)
            self.app.services.input_actions.hw_press("up", delay=0.15)
            sleep(0.8)
        self.app.services.input_actions.hw_press("esc")
        sleep(1.2)
        self.app.services.input_actions.hw_press("esc")
        sleep(1.2)
        return finish()
