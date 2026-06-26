from __future__ import annotations

import math
import time
from typing import TYPE_CHECKING

from ..input import DIK_CODES
from ..vision.matcher import CarCardPageSelector, CarCardSearchOptions

if TYPE_CHECKING:
    from ..backend.app import BackendApp


class RaceFlow:
    RACE_TIMEOUT_SECONDS = 150.0
    RACE_CAR_TEMPLATE = "skillcar.png"
    RACE_CAR_FAVORITE_TAG = "liketag.png"

    def __init__(self, app: BackendApp) -> None:
        self.app = app
        self.car_cards = CarCardPageSelector(app)

    def _skill_points_per_race(self) -> int:
        raw_points = self.app.services.config.values.get("calc_d", "50")
        digits = "".join(ch for ch in str(raw_points) if ch.isdigit())
        try:
            points = int(digits)
        except Exception:
            points = 50
        return max(1, points)

    # ==========================================
    # --- 模块：跑图前置与循环跑图 ---
    # ==========================================
    def _wait_for_event_loaded(self, timeout: float = 20.0):
        return self.app.services.image_waits.wait_for_footer_text_ui(
            "查看赛事信息",
            region=self.app.services.game_window.regions["下"],
            timeout=timeout,
            interval=1.0,
        )

    def _find_like_author_prompt(self):
        return self.app.services.image_matcher.find_any_image_sift(
            ["likeauthor.png", "dislikeauthor.png"],
            region=self.app.services.game_window.regions["中间"],
            min_inliers=18,
        )

    def _restart_timed_out_race(self) -> None:
        sleep = self.app.services.runtime.sleep

        sleep(0.5)
        self.app.services.input_actions.hw_press("esc")
        sleep(1.5)

        pos_restarta = self.app.services.image_waits.wait_for_image_sift(
            "restarta.png",
            min_inliers=6,
            timeout=3.0,
        )
        if not pos_restarta:
            pos_restarta = self.app.services.image_waits.wait_for_image_sift(
                "restart.png",
                min_inliers=20,
                timeout=2.0,
            )

        if pos_restarta:
            self.app.log("找到重开赛事入口，点击重开赛事...", level="debug")
            self.app.services.input_actions.game_click(pos_restarta)
            sleep(1.0)
            self.app.services.input_actions.hw_press("enter")
            sleep(4.0)
            return

        self.app.log("未识别重开赛事入口，尝试键盘菜单路径重开赛事...", level="warning")
        self.app.services.input_actions.hw_press("down")
        sleep(0.3)
        self.app.services.input_actions.hw_press("enter")
        sleep(1.0)
        self.app.services.input_actions.hw_press("enter")
        sleep(4.0)

    def _select_event_by_share_code(self) -> bool:
        sleep = self.app.services.runtime.sleep

        self.app.services.input_actions.hw_press("backspace")
        sleep(0.8)
        self.app.services.input_actions.hw_press("up")
        sleep(0.4)
        self.app.services.input_actions.hw_press("enter")
        sleep(0.8)

        code_text = "".join(c for c in str(self.app.services.config.values.get("share_code", "")) if c.isdigit())
        for char in code_text:
            if char in DIK_CODES:
                self.app.services.input_actions.hw_press(char, delay=0.05)
                sleep(0.05)

        sleep(0.4)
        self.app.services.input_actions.hw_press("enter")
        sleep(0.8)
        self.app.services.input_actions.hw_press("down")
        sleep(0.3)
        self.app.services.input_actions.hw_press("enter")
        sleep(1.5)

        if self._wait_for_event_loaded():
            return True

        self.app.log("链接超时", level="warning")
        return False

    def logic_race(self, target_count, *, until_skill_cap: bool = False):
        until_skill_cap = bool(until_skill_cap)
        target_count = max(0, int(target_count))
        start_count = self.app.state.race_counter
        sleep = self.app.services.runtime.sleep

        if not until_skill_cap and self.app.state.race_counter >= target_count:
            self.app.log("循环跑图流程结束：完成 0 次。")
            return True

        if until_skill_cap:
            self.app.state.set_task("循环跑图")
        else:
            self.app.state.set_task("循环跑图", self.app.state.race_counter, target_count)

        self.app.log("准备验证/进入菜单...", level="debug")
        if not self.app.services.recovery.enter_menu():
            return False

        self.app.log("切换到车辆页，读取当前技术点数...", level="debug")
        self.app.services.input_actions.hw_press("pagedown", delay=0.15)
        sleep(0.8)

        current_skill_points = self.app.services.ocr.find_current_skill_points_value()
        if current_skill_points is None:
            self.app.log("循环跑图：未能通过 OCR 识别当前技术点数，无法计算实际跑图次数。", level="warning")
            return False

        capped_skill_points = min(max(0, current_skill_points), 999)
        points_per_race = self._skill_points_per_race()
        remaining_user_count = max(0, target_count - self.app.state.race_counter)
        needed_to_cap = math.ceil(max(0, 999 - capped_skill_points) / points_per_race)
        planned_count = needed_to_cap if until_skill_cap else min(remaining_user_count, needed_to_cap)
        effective_target = self.app.state.race_counter + planned_count
        target_text = "目标模式：跑到技术点上限" if until_skill_cap else f"用户剩余目标 {remaining_user_count} 次"
        self.app.log(
            f"循环跑图：当前技术点 {capped_skill_points}/999，单次跑图预计 {points_per_race} 点，"
            f"{target_text}，达到上限最多还需 {needed_to_cap} 次，预计跑图 {planned_count} 次。"
        )

        if planned_count <= 0:
            reason = "当前技术点已达上限" if needed_to_cap <= 0 else "执行次数为 0"
            self.app.log(f"循环跑图流程结束：完成 0 次。原因：{reason}。")
            return True

        self.app.state.set_task("循环跑图", self.app.state.race_counter, effective_target)

        self.app.log("切换到创意中心...", level="debug")
        for _ in range(3):
            self.app.services.input_actions.hw_press("pagedown", delay=0.15)
            sleep(0.3)

        sleep(0.8)

        pos_el = self.app.services.image_waits.wait_for_image_sift(
            "eventlab.png",
            min_inliers=12,
            timeout=5,
        )

        if not pos_el:
            self.app.log("未找到 eventlab", level="warning")
            return False

        self.app.services.input_actions.game_click(pos_el)
        sleep(1.2)

        pos_yg = self.app.services.image_waits.wait_for_image_sift(
            "playenent.png",
            region=self.app.services.game_window.regions["中间"],
            min_inliers=10,
            timeout=40,
            interval=0.3,
        )
        if not pos_yg:
            self.app.log("未找到游玩赛事", level="warning")
            return False

        self.app.services.input_actions.game_click(pos_yg)
        sleep(1.5)

        if not self._select_event_by_share_code():
            return False

        self.app.services.input_actions.hw_press("enter")
        sleep(2.0)
        self.app.services.input_actions.hw_press("enter")
        sleep(1.0)

        race_car_options = CarCardSearchOptions(
            card_path=self.RACE_CAR_TEMPLATE,
            label="目标跑图车辆",
            required_tag_path=self.RACE_CAR_FAVORITE_TAG,
            tag_threshold=0.55,
            max_pages=1,
            page_timeout=2.0,
            interval=0.25,
        )
        race_car_result = self.car_cards.find(race_car_options)

        if not race_car_result:
            self.app.log("未找到目标跑图车辆，重新选择制造商...", level="debug")
            self.app.services.input_actions.hw_press("backspace")
            sleep(1.2)

            pos_brand = self.app.services.image_waits.scan_for_manufacturer_text(
                "斯巴鲁",
                threshold=0.75,
                label="刷图车辆制造商",
            )
            if not pos_brand:
                self.app.log("未找到刷图车辆制造商。", level="warning")
                return False

            self.app.services.input_actions.game_click(pos_brand)
            sleep(1.2)
            race_car_result = self.car_cards.find(
                CarCardSearchOptions(
                    card_path=self.RACE_CAR_TEMPLATE,
                    label="目标跑图车辆",
                    required_tag_path=self.RACE_CAR_FAVORITE_TAG,
                    tag_threshold=0.55,
                    max_pages=20,
                    page_timeout=2.0,
                    interval=0.25,
                    turn_key_delay=0.08,
                )
            )

        if not race_car_result:
            self.app.log("翻页未能找到目标跑图车辆。", level="warning")
            return False

        self.app.services.input_actions.game_click(race_car_result.position)
        sleep(0.5)
        self.app.services.input_actions.hw_press("enter")
        sleep(4.0)

        self.app.log("前置完成，开始循环跑图！", level="debug")

        while self.app.state.race_counter < effective_target:
            self.app.log(
                f"跑图 {self.app.state.race_counter + 1}/{effective_target}: 找开始竞赛赛事按钮...",
                level="debug",
            )

            pos = self.app.services.image_waits.wait_for_footer_text_ui(
                "选择",
            )

            if not pos:
                self.app.log("未找到'选择'按钮。", level="warning")
                return False
            self.app.services.input_actions.hw_press("enter")
            # self.app.services.input_actions.game_click(pos)

            sleep(4.0)
            self.app.services.input_actions.hw_key_down("w")

            # 初始化各类计时器
            race_start_time = time.time()
            last_like_chk = time.time()
            last_chk = 0
            finished = False
            timeout_triggered = False  # 标记是否触发超时

            driving_keys_held = True  # 标记油门状态

            while True:
                self.app.services.runtime.ensure_running()

                # ====== 跑图专用暂停处理逻辑 ======
                if self.app.state.is_paused:
                    if driving_keys_held:  # 刚进入暂停，松开油门
                        self.app.services.input_actions.hw_key_up("w")
                        driving_keys_held = False
                    self.app.services.runtime.check_pause()  # 阻塞在此处
                    self.app.services.runtime.ensure_running()
                    # 从暂停中恢复，如果还没跑完，重新按下油门
                    self.app.services.input_actions.hw_key_down("w")
                    driving_keys_held = True

                    # 避免恢复瞬间触发超时，重置计时器
                    race_start_time = time.time()
                    last_like_chk = time.time()
                    last_chk = time.time()
                    continue
                # =========================================
                now = time.time()

                if now - race_start_time > self.RACE_TIMEOUT_SECONDS:
                    self.app.log(
                        f"跑图超时(已超过{self.RACE_TIMEOUT_SECONDS:.0f}秒)！触发强制重开赛事逻辑...", level="warning"
                    )
                    timeout_triggered = True
                    break

                # 每隔3秒处理一次跑图中的特殊界面/异常
                if now - last_like_chk >= 3.0:
                    vram_result = self.app.services.recovery.check_vramne_during_race()
                    if vram_result is True:
                        self.app.log("VRAM恢复完成，结束当前跑图流程，交给外层重新恢复。", level="warning")
                        return False
                    elif vram_result is False:
                        self.app.log("VRAM恢复失败。", level="warning")
                        return False
                    pos_like = self._find_like_author_prompt()
                    if pos_like:
                        self.app.log("识别到点赞作界面，执行回车确认！", level="debug")
                        self.app.services.input_actions.hw_press("enter")
                    last_like_chk = now

                # 每1秒检测一次结果页(正常完赛)
                if now - last_chk >= 1.0:
                    found_result = self.app.services.ocr.find_footer_text_ui("重新开始")
                    if found_result:
                        finished = True
                        break
                    last_chk = now

                sleep(1.0)

            # 无论正常结束还是超时，都必须先松开油门
            self.app.services.input_actions.hw_key_up("w")
            self.app.services.runtime.ensure_running()

            # ====== 执行超时重置操作 ======
            if timeout_triggered:
                self._restart_timed_out_race()
                # 【关键】：直接跳过下方的结算流程，回到最外层 while 重新找 start.png（并且本次不计入 race_counter）
                continue
            # ========================================

            if not finished:
                return False

            if self.app.state.race_counter == effective_target - 1:
                self.app.services.input_actions.hw_press("enter")
                sleep(2.0)
            else:
                self.app.services.input_actions.hw_press("x")
                sleep(0.8)
                self.app.services.input_actions.hw_press("enter")
                sleep(2.0)

            self.app.state.race_counter += 1
            self.app.state.set_task("循环跑图", self.app.state.race_counter, effective_target)

        self.app.log(f"循环跑图流程结束：完成 {self.app.state.race_counter - start_count} 次。")
        return True
