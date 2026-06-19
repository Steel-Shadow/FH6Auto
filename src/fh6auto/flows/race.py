from __future__ import annotations

import time
from typing import TYPE_CHECKING

from ..input import DIK_CODES

if TYPE_CHECKING:
    from ..backend.app import BackendApp


class RaceFlow:
    RACE_TIMEOUT_SECONDS = 150.0
    RACE_CAR_TEMPLATE = "skillcar.png"
    RACE_CAR_FAVORITE_TAG = "liketag.png"
    RACE_CAR_MATCH_PARAMS = {
        "fast_mode": True,
        "final_threshold": 0.78,
        "title_threshold": 0.72,
        "pi_threshold": 0.82,
        "rarity_threshold": 0.68,
        "body_threshold": 0.55,
        "tag_threshold": 0.55,
    }

    def __init__(self, app: BackendApp) -> None:
        self.app = app

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

    def _wait_for_race_car(self, timeout: float = 2.0):
        return self.app.services.image_waits.wait_for_car_card(
            self.RACE_CAR_TEMPLATE,
            required_tag_path=self.RACE_CAR_FAVORITE_TAG,
            region=self.app.services.game_window.regions["全界面"],
            timeout=timeout,
            interval=0.25,
            fast_mode=self.RACE_CAR_MATCH_PARAMS["fast_mode"],
            final_threshold=self.RACE_CAR_MATCH_PARAMS["final_threshold"],
            title_threshold=self.RACE_CAR_MATCH_PARAMS["title_threshold"],
            pi_threshold=self.RACE_CAR_MATCH_PARAMS["pi_threshold"],
            rarity_threshold=self.RACE_CAR_MATCH_PARAMS["rarity_threshold"],
            body_threshold=self.RACE_CAR_MATCH_PARAMS["body_threshold"],
            tag_threshold=self.RACE_CAR_MATCH_PARAMS["tag_threshold"],
        )

    def _wait_for_race_start_button(self, timeout: float = 0.7):
        return self.app.services.image_waits.wait_for_image_sift(
            "start.png",
            region=self.app.services.game_window.regions["下"],
            min_inliers=24,
            timeout=timeout,
            interval=0.2,
        )

    def _find_race_result_screen(self):
        return self.app.services.image_matcher.find_race_result_table(
            region=self.app.services.game_window.regions["全界面"]
        )

    def _find_like_author_prompt(self):
        return self.app.services.image_matcher.find_any_image_sift(
            ["likeauthor.png", "dislikeauthor.png"],
            region=self.app.services.game_window.regions["中间"],
            min_inliers=18,
        )

    def _restart_timed_out_race(self) -> None:
        time.sleep(0.5)
        self.app.services.input_actions.hw_press("esc")
        time.sleep(1.5)

        pos_restarta = self.app.services.image_waits.wait_for_image_sift(
            "restarta.png",
            region=self.app.services.game_window.regions["全界面"],
            min_inliers=6,
            timeout=3.0,
            interval=0.3,
        )
        if not pos_restarta:
            pos_restarta = self.app.services.image_waits.wait_for_image_sift(
                "restart.png",
                region=self.app.services.game_window.regions["全界面"],
                min_inliers=20,
                timeout=2.0,
                interval=0.3,
            )

        if pos_restarta:
            self.app.log("找到重开赛事入口，点击重开赛事...")
            self.app.services.input_actions.game_click(pos_restarta)
            time.sleep(1.0)
            self.app.services.input_actions.hw_press("enter")
            time.sleep(4.0)
            return

        self.app.log("未识别重开赛事入口，尝试键盘菜单路径重开赛事...")
        self.app.services.input_actions.hw_press("down")
        time.sleep(0.3)
        self.app.services.input_actions.hw_press("enter")
        time.sleep(1.0)
        self.app.services.input_actions.hw_press("enter")
        time.sleep(4.0)

    def _select_event_by_share_code(self) -> bool:
        self.app.services.input_actions.hw_press("backspace")
        time.sleep(0.8)
        self.app.services.input_actions.hw_press("up")
        time.sleep(0.4)
        self.app.services.input_actions.hw_press("enter")
        time.sleep(0.8)

        code_text = "".join(c for c in str(self.app.services.config.values.get("share_code", "")) if c.isdigit())
        for char in code_text:
            if not self.app.state.is_running:
                return False
            if char in DIK_CODES:
                self.app.services.input_actions.hw_press(char, delay=0.05)
                time.sleep(0.05)

        time.sleep(0.4)
        self.app.services.input_actions.hw_press("enter")
        time.sleep(0.8)
        self.app.services.input_actions.hw_press("down")
        time.sleep(0.3)
        self.app.services.input_actions.hw_press("enter")
        time.sleep(1.5)

        if self._wait_for_event_loaded():
            return True

        self.app.log("链接超时")
        return False

    def logic_race(self, target_count):
        if self.app.state.race_counter >= target_count:
            return True

        self.app.state.set_task("循环跑图", self.app.state.race_counter, target_count)

        self.app.log("准备验证/进入菜单...")
        if not self.app.services.recovery.enter_menu():
            return False

        self.app.log("切换到创意中心...")
        for _ in range(4):
            self.app.services.input_actions.hw_press("pagedown", delay=0.15)
            time.sleep(0.3)

        time.sleep(0.8)

        pos_el = self.app.services.image_waits.wait_for_image_sift(
            "eventlab.png",
            region=self.app.services.game_window.regions["全界面"],
            min_inliers=12,
            timeout=5,
            interval=0.25,
        )

        if not pos_el:
            self.app.log("未找到 eventlab")
            return False

        self.app.services.input_actions.game_click(pos_el)
        time.sleep(1.2)

        pos_yg = self.app.services.image_waits.wait_for_image_sift(
            "playenent.png",
            region=self.app.services.game_window.regions["中间"],
            min_inliers=10,
            timeout=40,
            interval=0.3,
        )
        if not pos_yg:
            self.app.log("未找到游玩赛事")
            return False

        self.app.services.input_actions.game_click(pos_yg)
        time.sleep(1.5)

        if not self._select_event_by_share_code():
            return False

        self.app.services.input_actions.hw_press("enter")
        time.sleep(2.0)
        self.app.services.input_actions.hw_press("enter")
        time.sleep(2.0)

        pos_target = self._wait_for_race_car(timeout=2)

        if not pos_target:
            self.app.log("未找到目标跑图车辆，重新选择制造商...")
            self.app.services.input_actions.hw_press("backspace")
            time.sleep(1.2)

            pos_brand = self.app.services.image_waits.scan_for_manufacturer_text(
                "斯巴鲁",
                threshold=0.75,
                label="刷图车辆制造商",
            )
            if not pos_brand:
                self.app.log("未找到刷图车辆制造商。")
                return False

            self.app.services.input_actions.game_click(pos_brand)
            time.sleep(1.2)

            for _ in range(20):
                if not self.app.state.is_running:
                    return False

                pos_target = self._wait_for_race_car(timeout=2)
                if pos_target:
                    break

                for _ in range(4):
                    self.app.services.input_actions.hw_press("right", delay=0.08)
                    time.sleep(0.08)
                time.sleep(0.4)

        if not pos_target:
            self.app.log("翻页未能找到目标跑图车辆。")
            return False

        self.app.services.input_actions.game_click(pos_target)
        time.sleep(0.5)
        self.app.services.input_actions.hw_press("enter")
        time.sleep(4.0)

        self.app.log("前置完成，开始循环跑图！")

        while self.app.state.race_counter < target_count:
            if not self.app.state.is_running:
                return False

            self.app.log(f"跑图 {self.app.state.race_counter + 1}/{target_count}: 找开始竞赛赛事按钮...")

            pos = None
            for _ in range(120):
                if not self.app.state.is_running:
                    return False

                pos = self._wait_for_race_start_button(timeout=0.7)
                if pos:
                    break

                self.app.services.input_actions.hw_press("down")
                time.sleep(0.25)

            if not pos:
                self.app.log("找不到开始竞赛赛事按钮，退出跑图。")
                return False

            self.app.services.input_actions.game_click(pos)
            time.sleep(4.0)
            self.app.services.input_actions.hw_key_down("w")
            self.app.services.input_actions.hw_key_down("up")

            # 初始化各类计时器
            race_start_time = time.time()  # 新增：记录跑图发车时间
            last_like_chk = time.time()
            last_chk = 0
            finished = False
            timeout_triggered = False  # 标记是否触发超时

            driving_keys_held = True  # <--- 【新增】标记油门状态

            while self.app.state.is_running:
                # ====== 【新增】跑图专用暂停处理逻辑 ======
                if self.app.state.is_paused:
                    if driving_keys_held:  # 刚进入暂停，松开油门
                        self.app.services.input_actions.hw_key_up("w")
                        self.app.services.input_actions.hw_key_up("up")
                        driving_keys_held = False
                    self.app.services.runtime.check_pause()  # 阻塞在此处
                    # 从暂停中恢复，如果还没跑完，重新按下油门
                    if self.app.state.is_running:
                        self.app.services.input_actions.hw_key_down("w")
                        self.app.services.input_actions.hw_key_down("up")
                        driving_keys_held = True

                    # 避免恢复瞬间触发超时，重置计时器
                    race_start_time = time.time()
                    last_like_chk = time.time()
                    last_chk = time.time()
                    continue
                # =========================================
                now = time.time()

                if now - race_start_time > self.RACE_TIMEOUT_SECONDS:
                    self.app.log(f"跑图超时(已超过{self.RACE_TIMEOUT_SECONDS:.0f}秒)！触发强制重开赛事逻辑...")
                    timeout_triggered = True
                    break

                # 每隔3秒处理一次跑图中的特殊界面/异常
                if now - last_like_chk >= 3.0:
                    vram_result = self.app.services.recovery.check_vramne_during_race()
                    if vram_result is True:
                        self.app.log("VRAM恢复完成，结束当前跑图流程，交给外层重新恢复。")
                        return False
                    elif vram_result is False:
                        self.app.log("VRAM恢复失败。")
                        return False
                    pos_like = self._find_like_author_prompt()
                    if pos_like:
                        self.app.log("识别到点赞作界面，执行回车确认！")
                        self.app.services.input_actions.hw_press("enter")
                    last_like_chk = now

                # 每1秒检测一次结果页(正常完赛)
                if now - last_chk >= 1.0:
                    found_result = self._find_race_result_screen()
                    if found_result:
                        finished = True
                        break
                    last_chk = now

                time.sleep(0.3)

            # 无论正常结束还是超时，都必须先松开油门和方向
            self.app.services.input_actions.hw_key_up("w")
            self.app.services.input_actions.hw_key_up("up")

            if not self.app.state.is_running:
                return False

            # ====== 【新增】：执行超时重置操作 ======
            if timeout_triggered:
                self._restart_timed_out_race()
                # 【关键】：直接跳过下方的结算流程，回到最外层 while 重新找 start.png（并且本次不计入 race_counter）
                continue
            # ========================================

            if not finished:
                return False

            if self.app.state.race_counter == target_count - 1:
                self.app.services.input_actions.hw_press("enter")
                time.sleep(2.0)
            else:
                self.app.services.input_actions.hw_press("x")
                time.sleep(0.8)
                self.app.services.input_actions.hw_press("enter")
                time.sleep(2.0)

            self.app.state.race_counter += 1
            self.app.state.set_task("循环跑图", self.app.state.race_counter, target_count)

        return True
