from __future__ import annotations

import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..backend.app import BackendApp


class RemoveCarFlow:
    def __init__(self, app: BackendApp) -> None:
        self.app = app

    # ==========================================
    # --- 模块：移除车辆 ---
    # ==========================================
    def find_and_remove_consumable_car(self, target_count):
        if self.app.state.sc_count >= target_count:
            return True

        self.app.state.set_task("移除车辆", self.app.state.sc_count, target_count)

        self.app.log("准备验证/进入菜单！！！使用前请人工核验到正常移除车辆再进行自动化移除处理")
        if not self.app.services.recovery.enter_menu():
            return False

        # self.app.log("进入菜单页面：车辆")
        self.app.services.input_actions.hw_press("pagedown", delay=0.15)
        time.sleep(1.0)

        pos_buycar = self.app.services.image_matcher.find_image_sift(
            "buy_new_used_cars.png",
            self.app.services.game_window.regions["左"],
            20,
        )
        if not pos_buycar:
            self.app.log("未识别到 购买新车与二手车")
            return False

        self.app.services.input_actions.game_click(pos_buycar)
        # 点击“购买新车与二手车”
        self.app.services.input_actions.move_to_game_coord(5, 5)
        time.sleep(0.8)
        self.app.services.input_actions.hw_press("enter")
        time.sleep(1)

        pos_bs = self.app.services.image_waits.wait_for_footer_text_ui(
            "选择",
            region=self.app.services.game_window.regions["下"],
        )
        if not pos_bs:
            self.app.log("未找到 选择")
            return False

        # 进入“我的车辆”
        self.app.services.input_actions.hw_press("pagedown")
        time.sleep(1.0)
        self.app.services.input_actions.hw_press("enter")
        time.sleep(2.0)

        # 筛选
        self.app.services.input_actions.hw_press("y")
        time.sleep(1.0)

        pos_repeat = self.app.services.image_waits.wait_for_menu_text_ui(
            "重复项",
            region=self.app.services.game_window.regions["全界面"],
            timeout=1.0,
        )

        if not pos_repeat:
            self.app.log("未找到重复项")
            return False

        self.app.services.input_actions.game_click(pos_repeat)
        time.sleep(0.5)
        self.app.services.input_actions.hw_press("esc")
        time.sleep(0.5)

        # 切换到消耗品制造商
        self.app.log("切换到消耗品制造商...")
        self.app.services.input_actions.hw_press("backspace")
        manufacturer_pos = self.app.services.image_waits.scan_for_manufacturer_text(
            "斯巴鲁", threshold=0.75, label="消耗品制造商"
        )
        if not manufacturer_pos:
            self.app.log("未找到制造商")
            return False

        self.app.services.input_actions.game_click(manufacturer_pos)
        time.sleep(1.0)

        self.app.log("开始删除车辆")

        not_found_pages = 0
        while self.app.state.sc_count < target_count:
            if not self.app.state.is_running:
                return False
            self.app.log(f"正在使用 3模式 严格扫描当前页面... (连续未找到: {not_found_pages}/5)")

            pos_target = self.app.services.image_waits.wait_for_car_card(
                "removecarobject.png",
                excluded_tag_text="全新",
                exclude_driving=True,
                region=self.app.services.game_window.regions["全界面"],
                final_threshold=0.80,
                title_threshold=0.74,
                pi_threshold=0.84,
                rarity_threshold=0.70,
                exclude_tag_threshold=0.65,
                timeout=3.0,
                interval=0.2,
            )

            if not pos_target:
                not_found_pages += 1
                if not_found_pages >= 5:
                    self.app.log("连续翻找 5 页仍未搜索到目标车辆！视为车辆已全部清理完毕。")
                    self.app.log("主动结束清理任务，准备进入下一步骤...")
                    break  # 直接跳出循环，结束当前任务

                self.app.log(f"当前页面未找到，向右翻页寻找... (第 {not_found_pages} 次翻页)")
                for _ in range(4):
                    self.app.services.input_actions.hw_press("right", delay=0.06)
                    time.sleep(0.1)
                time.sleep(0.4)
                continue
            # ====== 找到了目标车辆，重置翻页计数器 ======
            not_found_pages = 0

            self.app.log("精准锁定目标车辆，执行点击...")
            self.app.services.input_actions.game_click(pos_target)
            time.sleep(1.0)  # 等待点击后的反应

            # 若该车在点击前已经被选中，则会直接弹出“选择操作”菜单，否则会将列表车辆列表先滑动到指定位置
            pos_cancel = self.app.services.ocr.find_footer_text_ui("取消")
            if not pos_cancel:
                self.app.services.input_actions.hw_press("enter")
                time.sleep(1.0)  # 等待点击后的反应

            self.app.log("寻找 '从车库移除车辆' 按钮...")
            pos_remove = self.app.services.image_waits.wait_for_any_text_ui(
                ["从车库移除车辆"],
                region=self.app.services.game_window.regions["全界面"],
                timeout=3.0,
                interval=0.3,
            )

            if pos_remove:
                self.app.log("找到移除按钮，点击...")
                self.app.services.input_actions.game_click(pos_remove)
                self.app.services.input_actions.move_to_game_coord(5, 5)
            else:
                self.app.log("找不到移除按钮")
                return False

            time.sleep(0.8)  # 等待“你确定要移除吗”的确认弹窗

            # 确认移除操作 (按向下选"嗯"，然后回车)
            self.app.log("确认移除...")
            self.app.services.input_actions.hw_press("down")
            time.sleep(0.3)
            self.app.services.input_actions.hw_press("enter")
            time.sleep(1.2)

            self.app.state.sc_count += 1
            self.app.state.set_task("移除车辆", self.app.state.sc_count, target_count)
            self.app.log(f"成功移除车辆！当前进度: {self.app.state.sc_count}/{target_count}")

        # 循环结束，退回上一级
        for _ in range(3):
            if not self.app.state.is_running:
                return False
            self.app.services.input_actions.hw_press("esc")
            time.sleep(1.0)

        return True
