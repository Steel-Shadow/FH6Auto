from __future__ import annotations

import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..backend.app import BackendApp


class RemoveCarFlow:
    def __init__(self, app: BackendApp) -> None:
        self.app = app


    def _buy_sell_title_region(self):
        x, y, w, h = self.app.services.game_window.regions["全界面"]
        return (x, y + int(h * 0.20), int(w * 0.48), int(h * 0.18))


    def _find_buy_sell_page_title(self):
        region = self._buy_sell_title_region()
        pos = self.app.services.image_waits.find_text_ui(
            "购买与出售",
            region=region,
            threshold=0.56,
            fast_mode=True,
        )
        if pos:
            return pos

        target_text = "购买与出售"
        target_norm = self.app.services.ocr.normalize_text(target_text)
        if len(target_norm) < 2:
            return None

        try:
            results = self.app.services.ocr.read(self.app.services.image_cache.capture_region(region), text_score=0.25)
        except Exception as e:
            self.app.log(f"购买与出售标题 OCR 异常: {e}")
            return None

        rx, ry, rw, rh = region
        for result in results:
            candidate_norm = self.app.services.ocr.normalize_text(result.text)
            if len(candidate_norm) < 2:
                continue
            if target_norm not in candidate_norm and candidate_norm not in target_norm:
                continue

            if result.box:
                xs = [float(point[0]) + rx for point in result.box]
                ys = [float(point[1]) + ry for point in result.box]
                pos = (int(round((min(xs) + max(xs)) / 2)), int(round((min(ys) + max(ys)) / 2)))
            else:
                pos = (int(rx + rw / 2), int(ry + rh / 2))

            self.app.log(
                f"[TitleOCR] 命中购买与出售标题: {result.text} "
                f"(目标:{target_text}) | 分数:{result.score:.3f}"
            )
            return pos

        return None


    def _wait_for_buy_sell_page_title(self, timeout: float = 30, interval: float = 0.3):
        start = time.time()
        while self.app.state.is_running and time.time() - start < timeout:
            pos = self._find_buy_sell_page_title()
            if pos:
                return pos

            sleep_end = time.time() + interval
            while self.app.state.is_running and time.time() < sleep_end:
                time.sleep(0.05)

        return None


    def _enter_my_cars_from_buy_sell_page(self, settle_time: float = 1.5) -> bool:
        if not self.app.state.is_running:
            return False

        self.app.log("切换到车辆标签并进入我的车辆...")
        self.app.services.input_actions.hw_press("pagedown", delay=0.15)
        time.sleep(1.0)
        if not self.app.state.is_running:
            return False

        self.app.services.input_actions.hw_press("enter")
        time.sleep(settle_time)
        return True


    def _wait_for_duplicate_filter_item(self, timeout: float = 6.0, interval: float = 0.3):
        start = time.time()
        while self.app.state.is_running and time.time() - start < timeout:
            pos = self.app.services.image_waits.find_menu_text_ui(
                "重复项",
                region=self.app.services.game_window.regions["全界面"],
                threshold=0.58,
            )
            if pos:
                return pos

            pos = self.app.services.image_waits.find_text_ui(
                "重复项",
                region=self.app.services.game_window.regions["全界面"],
                threshold=0.58,
                fast_mode=True,
            )
            if pos:
                return pos

            sleep_end = time.time() + interval
            while self.app.state.is_running and time.time() < sleep_end:
                time.sleep(0.05)

        return None


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

        self.app.log("进入车辆与收藏！！！使用前请人工核验到正常移除车辆再进行自动化移除处理")
        self.app.services.input_actions.hw_press("pagedown", delay=0.15)
        time.sleep(1.0)

        pos_buycar = self.app.services.image_waits.wait_for_image_sift(
            "buy_new_used_cars.png",
            region=self.app.services.game_window.regions["左"],
            min_inliers=20,
            timeout=12,
            interval=0.3,
        )
        if not pos_buycar:
            self.app.log("未识别到 购买新车与二手车")
            return False

        self.app.services.input_actions.game_click(pos_buycar)
        time.sleep(0.8)
        self.app.services.input_actions.hw_press("enter")

        pos_bs = self._wait_for_buy_sell_page_title(timeout=40, interval=0.5)
        if not pos_bs:
            self.app.log("未找到购买与出售")
            return False

        if not self._enter_my_cars_from_buy_sell_page(settle_time=2.0):
            return False
        # 选择一辆收藏
        self.app.services.input_actions.hw_press("y")
        time.sleep(1.0)
        self.app.services.input_actions.hw_press("enter")
        time.sleep(0.8)
        self.app.services.input_actions.hw_press("esc")
        time.sleep(1.5)
        # 驾驶收藏的车
        self.app.services.input_actions.hw_press("enter")
        time.sleep(1.0)
        self.app.services.input_actions.move_to_game_coord(5, 5)

        pos = self.app.services.image_waits.wait_for_text_ui(
            "上车",
            region=self.app.services.game_window.regions["全界面"],
            threshold=0.65,
            timeout=1,
            interval=0.2,
            fast_mode=True,
        )
        if pos:
            self.app.log("找到上车，执行点击")
            self.app.services.input_actions.game_click(pos)
            time.sleep(2.0)
        else:
            self.app.log("该车辆已经驾驶，或未找到图片，执行两次ESC")
            self.app.services.input_actions.hw_press("esc")
            time.sleep(1.5)
            self.app.services.input_actions.hw_press("esc")
        time.sleep(2.0)

        found = False
        for i in range(30):
            if not self.app.state.is_running:
                return False

            pos = self._wait_for_buy_sell_page_title(timeout=0.8, interval=0.2)
            if pos:
                self.app.log(f"第 {i + 1} 次检测到购买与出售，进入车辆界面")
                if not self._enter_my_cars_from_buy_sell_page(settle_time=1.5):
                    return False
                found = True
                break
            self.app.log(f"第 {i + 1} 次未检测到购买与出售，等待后重试")
            time.sleep(1.0)
        if not found:
            self.app.log("30次内未找到购买与出售")
            return False
        # 筛选
        self.app.services.input_actions.hw_press("y")
        time.sleep(1.0)
        """
        for _ in range(2):
            self.app.services.input_actions.hw_press("down", delay=0.06)
            time.sleep(0.2)
        time.sleep(0.5)
        self.app.services.input_actions.hw_press("enter")
        time.sleep(1.0)
        """
        pos_repitem = self._wait_for_duplicate_filter_item()
        if not pos_repitem:
            self.app.log("未识别到筛选菜单中的重复项")
            return False

        self.app.services.input_actions.game_click(pos_repitem)
        time.sleep(0.8)

        self.app.services.input_actions.hw_press("esc")
        time.sleep(1.0)

        # 切换到消耗品制造商
        self.app.log("切换到消耗品制造商...")
        self.app.services.input_actions.hw_press("backspace")
        manufacturer_pos = self.app.services.image_waits.find_manufacturer_by_text("斯巴鲁", threshold=0.75, label="消耗品制造商")
        if not manufacturer_pos:
            self.app.log("未找到制造商")
            return False

        self.app.services.input_actions.game_click(manufacturer_pos)
        time.sleep(0.8)

        self.app.log("开始删除最近获得的车辆！！！请人工确认是否移除")

        not_found_pages = 0
        while self.app.state.sc_count < target_count:
            if not self.app.state.is_running:
                return False
            self.app.log(f"正在使用 3模式 严格扫描当前页面... (连续未找到: {not_found_pages}/5)")

            pos_target = self.app.services.image_waits.wait_for_car_card(
                "removecarobject.png",
                excluded_tag_text="全新",
                region=self.app.services.game_window.regions["全界面"],
                final_threshold=0.80,
                title_threshold=0.74,
                pi_threshold=0.84,
                rarity_threshold=0.70,
                body_threshold=0.58,
                exclude_tag_threshold=0.65,
                timeout=3.0,
                interval=0.2,
            )

            if not pos_target:
                not_found_pages += 1
                if not_found_pages >= 5:
                    self.app.log("=连续翻找 5 页仍未搜索到目标车辆！视为车辆已全部清理完毕。")
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
            time.sleep(1.2)  # 等待点击后的反应

            # ==========================================
            # 核心逻辑：寻找“从车库移除车辆”菜单项
            # ==========================================
            self.app.log("寻找 '从车库移除' 按钮...")
            pos_remove = self.app.services.image_waits.find_text_ui(
                "从车库移除车辆",
                region=self.app.services.game_window.regions["全界面"],
                threshold=0.65,
                fast_mode=True,
            )

            if pos_remove:
                self.app.log("直接找到移除按钮，点击...")
                self.app.services.input_actions.game_click(pos_remove)
            else:
                self.app.log("未直接找到移除按钮，按下 Enter 呼出菜单...")
                self.app.services.input_actions.hw_press("enter")
                time.sleep(0.8)  # 等待菜单弹出动画

                # 再次寻找
                pos_remove = self.app.services.image_waits.find_text_ui(
                    "从车库移除车辆",
                    region=self.app.services.game_window.regions["全界面"],
                    threshold=0.65,
                    fast_mode=True,
                )
                if pos_remove:
                    self.app.log("呼出菜单后找到移除按钮，点击...")
                    self.app.services.input_actions.game_click(pos_remove)
                else:
                    self.app.log("仍未找到移除按钮，可能点错了/该车无法移除，按 ESC 放弃该车...")
                    self.app.services.input_actions.hw_press("esc")
                    time.sleep(1.0)
                    self.app.services.input_actions.hw_press("right")  # 往右挪一格，防止死循环一直点这辆假车
                    time.sleep(1.2)
                    continue

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

