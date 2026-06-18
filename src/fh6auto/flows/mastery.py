from __future__ import annotations

import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..backend.app import BackendApp


class MasteryFlow:
    def __init__(self, app: BackendApp) -> None:
        self.app = app


    def _find_vehicle_view_drive_prompt(self):
        return self.app.services.image_waits.find_footer_text_ui(
            "Space驾驶",
            region=self.app.services.game_window.regions["下"],
            threshold=0.50,
        )


    def _wait_for_upgrade_menu_after_get_in(self, timeout: float = 45.0):
        start = time.time()
        last_escape_time = 0.0
        vehicle_view_seen = False

        while self.app.state.is_running and time.time() - start < timeout:
            now = time.time()

            if not vehicle_view_seen:
                vehicle_view_ready = self._find_vehicle_view_drive_prompt()
                if vehicle_view_ready:
                    self.app.log("检测到车辆展示界面，返回车辆菜单...")
                    vehicle_view_seen = True
                    self.app.services.input_actions.hw_press("esc")
                    last_escape_time = now
                    time.sleep(1.0)
                    continue

                # 兜底：如果展示界面提示识别失败，不在动画刚开始时反复按 Esc，只做低频恢复尝试。
                if now - start >= 18.0 and now - last_escape_time >= 8.0:
                    self.app.log("等待车辆展示界面超时，尝试返回车辆菜单...")
                    self.app.services.input_actions.hw_press("esc")
                    vehicle_view_seen = True
                    last_escape_time = now
                time.sleep(0.5)
                continue

            pos_sjy = self.app.services.image_waits.find_menu_text_ui(
                "升级与调校",
                region=self.app.services.game_window.regions["左下"],
                threshold=0.65,
            )
            if pos_sjy:
                return pos_sjy

            time.sleep(0.5)

        return None


    # ==========================================
    # --- 模块：熟练度加点 ---
    # ==========================================
    def logic_mastery(self, target_count):
        if self.app.state.mastery_counter >= target_count:
            return True

        self.app.state.set_task("熟练度加点", self.app.state.mastery_counter, target_count)
        try:
            max_scan_pages = int(self.app.services.config.values.get("mastery_scan_pages", 100))
        except Exception:
            max_scan_pages = 100
        max_scan_pages = max(1, min(100, max_scan_pages))
        self.app.log("准备验证/进入菜单...")
        if not self.app.services.recovery.enter_menu():
            return False

        self.app.log("进入车辆与收藏...")
        self.app.services.input_actions.hw_press("pagedown", delay=0.15)
        time.sleep(1.0)

        pos_buycar = self.app.services.image_waits.wait_for_image_sift(
            "buy_new_used_cars.png",
            region=self.app.services.game_window.regions["左"],
            min_inliers=20,
            timeout=15,
            interval=0.3,
        )
        if not pos_buycar:
            self.app.log("未识别到 购买新车与二手车")
            return False

        self.app.services.input_actions.game_click(pos_buycar)
        time.sleep(0.8)
        self.app.services.input_actions.hw_press("enter")
        time.sleep(5)

        pos_bs = self.app.services.image_waits.wait_for_text_ui(
            "购买与出售",
            region=self.app.services.game_window.regions["左上"],
            threshold=0.65,
            timeout=60,
            interval=0.5,
        )
        if not pos_bs:
            self.app.log("未找到购买与出售")
            return False

        self.app.services.input_actions.hw_press("pagedown", delay=0.15)
        self.app.log("进入车辆界面...")
        time.sleep(0.5)

        while self.app.state.mastery_counter < target_count:
            if not self.app.state.is_running:
                return False
            self.app.log("进入我的车辆.")
            self.app.services.input_actions.hw_press("enter")
            time.sleep(2.0)
            self.app.services.input_actions.hw_press("backspace")
            time.sleep(1.0)

            manufacturer_pos = self.app.services.image_waits.find_manufacturer_by_text("斯巴鲁", threshold=0.75, label="消耗品制造商")
            if not manufacturer_pos:
                self.app.log("选择制造商失败")
                return False

            self.app.services.input_actions.game_click(manufacturer_pos)
            time.sleep(1.0)
            jump_pages = min(max(0, self.app.state.memory_car_page - 1), max_scan_pages - 1)

            if jump_pages > 0:
                self.app.log(f"智能记忆触发：快速跳过前 {jump_pages} 页...")
                for _ in range(jump_pages):
                    if not self.app.state.is_running:
                        return False
                    for _ in range(4):
                        self.app.services.input_actions.hw_press("right", delay=0.06)
                        time.sleep(0.1)
                    time.sleep(0.15)  # 给一点点动画缓冲时间
            pos_target = None
            found_car = False
            current_page = jump_pages  # 记录当前所在的真实页码
            scan_pages_left = max_scan_pages - jump_pages

            # 最大翻页次数扣除已经跳过的页数，避免未识别目标时长时间翻页
            for _ in range(scan_pages_left):
                if not self.app.state.is_running:
                    return False
                pos_target = self.app.services.image_waits.wait_for_car_card(
                    "newCC.png",
                    required_tag_text="全新",
                    region=self.app.services.game_window.regions["全界面"],
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
                    # 记住这次找到车是在哪一页
                    self.app.state.memory_car_page = current_page
                    self.app.log(f"锁定目标车辆！已记录当前页码: {current_page}")
                    break

                # 翻下一页
                for _ in range(4):
                    self.app.services.input_actions.hw_press("right", delay=0.06)
                    time.sleep(0.1)
                time.sleep(0.4)
                current_page += 1
            if not found_car:
                self.app.log(
                    f"连续扫描 {max_scan_pages} 页仍未找到带 NEW 标记的消耗品车辆，已停止熟练度加点模块以避免反复翻页。"
                )
                self.app.log("请检查是否还有新购车辆，或更新 newCC.png 模板。")
                self.app.state.memory_car_page = 0  # 没找到说明车刷完了，清零记忆
                for _ in range(2):
                    if not self.app.state.is_running:
                        return False
                    self.app.services.input_actions.hw_press("esc")
                    time.sleep(0.8)
                return True
            time.sleep(0.5)
            self.app.log("确认上车并驾驶当前车辆...")
            self.app.services.input_actions.hw_press("enter")
            time.sleep(1.0)
            self.app.services.input_actions.hw_press("enter")

            pos_sjy = self._wait_for_upgrade_menu_after_get_in()
            if not pos_sjy:
                self.app.log("找不到升级页面")
                return False

            self.app.services.input_actions.game_click(pos_sjy)

            pos_mastery = self.app.services.image_waits.wait_for_menu_text_ui(
                "车辆专精",
                region=self.app.services.game_window.regions["左下"],
                threshold=0.65,
                timeout=20,
                interval=0.3,
            )
            if not pos_mastery:
                self.app.log("未找到车辆专精")
                return False
            self.app.services.input_actions.game_click(pos_mastery)

            pos_exp = self.app.services.image_waits.wait_for_image_sift(
                "EXPwU.png",
                region=self.app.services.game_window.regions["左"],
                min_inliers=8,
                timeout=1.5,
                interval=0.3,
            )

            if pos_exp:
                self.app.log("该车辆技能已点过，跳过计数")
            else:
                time.sleep(1.0)
                self.app.services.input_actions.hw_press("enter")
                time.sleep(1.5)

                for dk in self.app.services.config.values["skill_dirs"]:
                    if not self.app.state.is_running:
                        return False
                    self.app.services.input_actions.hw_press(dk)
                    time.sleep(0.2)
                    self.app.services.input_actions.hw_press("enter")
                    time.sleep(1.2)

                spne_found = self.app.services.image_matcher.find_image_sift(
                    "SPNE.png",
                    region=self.app.services.game_window.regions["全界面"],
                    min_inliers=24,
                )

                if spne_found:
                    self.app.log("已无技能点或技能已点完，提前结束熟练度加点！")
                    time.sleep(1.0)
                    self.app.services.input_actions.hw_press("enter")
                    time.sleep(0.8)
                    self.app.services.input_actions.hw_press("esc")
                    time.sleep(1.0)
                    self.app.services.input_actions.hw_press("esc")
                    time.sleep(1.0)
                    self.app.services.input_actions.hw_press("esc")
                    time.sleep(1.0)
                    return True
                self.app.state.mastery_counter += 1
                self.app.state.set_task("熟练度加点", self.app.state.mastery_counter, target_count)

            self.app.services.input_actions.hw_press("esc")
            time.sleep(1.2)
            self.app.services.input_actions.hw_press("esc")
            time.sleep(0.8)
            self.app.services.input_actions.hw_press("up", delay=0.15)
            time.sleep(0.8)
        self.app.services.input_actions.hw_press("esc")
        time.sleep(1.2)
        self.app.services.input_actions.hw_press("esc")
        time.sleep(1.2)
        return True
