from __future__ import annotations

import math
import time
from collections.abc import Callable

from ..recovery import RecoveryService
from ..window import GameWindowService
from ..backend.config_service import BackendConfigService
from ..backend.runtime import BackendRuntimeService
from ..backend.state import RuntimeState
from ..input.actions import InputActionsService
from ..vision.car_cards import CarCardPageSelector, CarCardSearchOptions
from ..vision.footer import FooterDetector
from ..vision.manufacturer import ManufacturerDetector
from ..vision.matcher import ImageMatcherService
from ..vision.player_stats import PlayerStatsDetector
from ..vision.polling import ImageWaitsService


class RaceFlow:
    RACE_TIMEOUT_SECONDS = 300.0
    RACE_CAR_TEMPLATE = "skillcar.png"
    RACE_CAR_FAVORITE_TAG = "liketag.png"

    def __init__(
        self,
        *,
        state: RuntimeState,
        config: BackendConfigService,
        game_window: GameWindowService,
        input_actions: InputActionsService,
        image_matcher: ImageMatcherService,
        image_waits: ImageWaitsService,
        manufacturer: ManufacturerDetector,
        footer: FooterDetector,
        player_stats: PlayerStatsDetector,
        recovery: RecoveryService,
        runtime: BackendRuntimeService,
        sleep: Callable[[float], None],
        log: Callable[..., None],
    ) -> None:
        self.state = state
        self.config = config
        self.game_window = game_window
        self.input_actions = input_actions
        self.image_matcher = image_matcher
        self.image_waits = image_waits
        self.manufacturer = manufacturer
        self.footer = footer
        self.player_stats = player_stats
        self.recovery = recovery
        self.runtime = runtime
        self.sleep = sleep
        self.log = log
        self.car_cards = CarCardPageSelector(
            image_matcher=self.image_matcher,
            input_actions=self.input_actions,
            sleep=self.sleep,
            log=self.log,
        )

    def _skill_points_per_race(self) -> int:
        raw_points = self.config.values.get("calc_d", "50")
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
        return self.image_waits.wait_for_footer_text_ui("查看赛事信息")

    def _find_like_author_prompt(self):
        return self.image_matcher.find_any_image_sift(
            ["likeauthor.png", "dislikeauthor.png"],
            region=self.game_window.regions["中间"],
            min_inliers=18,
        )

    def _restart_timed_out_race(self) -> None:
        sleep = self.sleep

        sleep(0.5)
        self.input_actions.hw_press("esc")
        sleep(1.5)

        pos_restarta = self.image_waits.wait_for_image_sift(
            "restarta.png",
            min_inliers=6,
            timeout=3.0,
        )
        if not pos_restarta:
            pos_restarta = self.image_waits.wait_for_image_sift(
                "restart.png",
                min_inliers=20,
                timeout=2.0,
            )

        if pos_restarta:
            self.log("找到重开赛事入口，点击重开赛事...", level="debug")
            self.input_actions.game_click(pos_restarta)
            sleep(1.0)
            self.input_actions.hw_press("enter")
            sleep(4.0)
            return

        self.log("未识别重开赛事入口，尝试键盘菜单路径重开赛事...", level="warning")
        self.input_actions.hw_press("down")
        sleep(0.3)
        self.input_actions.hw_press("enter")
        sleep(1.0)
        self.input_actions.hw_press("enter")
        sleep(4.0)

    def _select_event_by_share_code(self) -> bool:
        sleep = self.sleep

        self.input_actions.hw_press("backspace")
        sleep(0.8)
        self.input_actions.hw_press("up")
        sleep(0.4)
        self.input_actions.hw_press("enter")
        sleep(0.8)

        code_text = "".join(c for c in str(self.config.values.get("share_code", "")) if c.isdigit())
        for char in code_text:
            self.input_actions.hw_press(char, delay=0.05)
            sleep(0.05)

        sleep(0.4)
        self.input_actions.hw_press("enter")
        sleep(0.8)
        self.input_actions.hw_press("down")
        sleep(0.3)
        self.input_actions.hw_press("enter")
        sleep(1.5)

        if self._wait_for_event_loaded():
            return True

        self.log("链接超时", level="warning")
        return False

    def logic_race(self, target_count, *, until_skill_cap: bool = False):
        until_skill_cap = bool(until_skill_cap)
        target_count = max(0, int(target_count))
        start_count = self.state.race_counter
        sleep = self.sleep

        if not until_skill_cap and self.state.race_counter >= target_count:
            self.log("循环跑图流程结束：完成 0 次。")
            return True

        if until_skill_cap:
            self.state.set_task("循环跑图")
        else:
            self.state.set_task("循环跑图", self.state.race_counter, target_count)

        self.log("准备验证/进入菜单...", level="debug")
        if not self.recovery.enter_menu():
            return False

        self.log("切换到车辆页，读取当前技术点数...", level="debug")
        self.input_actions.hw_press("pagedown", delay=0.15)
        sleep(0.8)

        current_skill_points = self.player_stats.find_current_skill_points_value()
        if current_skill_points is None:
            self.log("循环跑图：未能通过 OCR 识别当前技术点数，无法计算实际跑图次数。", level="warning")
            return False

        capped_skill_points = min(max(0, current_skill_points), 999)
        points_per_race = self._skill_points_per_race()
        remaining_user_count = max(0, target_count - self.state.race_counter)
        needed_to_cap = math.ceil(max(0, 999 - capped_skill_points) / points_per_race)
        planned_count = needed_to_cap if until_skill_cap else min(remaining_user_count, needed_to_cap)
        effective_target = self.state.race_counter + planned_count
        target_text = "目标模式：跑到技术点上限" if until_skill_cap else f"用户剩余目标 {remaining_user_count} 次"
        self.log(
            f"循环跑图：当前技术点 {capped_skill_points}/999，单次跑图预计 {points_per_race} 点，"
            f"{target_text}，达到上限最多还需 {needed_to_cap} 次，预计跑图 {planned_count} 次。"
        )

        if planned_count <= 0:
            reason = "当前技术点已达上限" if needed_to_cap <= 0 else "执行次数为 0"
            self.log(f"循环跑图流程结束：完成 0 次。原因：{reason}。")
            return True

        self.state.set_task("循环跑图", self.state.race_counter, effective_target)

        self.log("切换到创意中心...", level="debug")
        for _ in range(3):
            self.input_actions.hw_press("pagedown", delay=0.15)
            sleep(0.3)

        self.input_actions.hw_press("enter")
        sleep(1.0)
        self.input_actions.hw_press("enter")
        sleep(1.0)

        if not self._select_event_by_share_code():
            return False

        self.input_actions.hw_press("enter")
        sleep(2.0)
        self.input_actions.hw_press("enter")
        sleep(1.0)

        if not self.image_waits.wait_for_footer_text_ui("选择", timeout=15, interval=0.5):
            self.log("进入车辆选择页后未找到底部“选择”按钮。", level="warning")
            return False

        race_car_options = CarCardSearchOptions(
            card_path=self.RACE_CAR_TEMPLATE,
            label="目标跑图车辆",
            required_tag_path=self.RACE_CAR_FAVORITE_TAG,
            tag_threshold=0.55,
            max_pages=1,
        )
        race_car_result = self.car_cards.find(race_car_options)

        if not race_car_result:
            self.log("未找到目标跑图车辆，重新选择制造商...", level="debug")
            self.input_actions.hw_press("backspace")
            sleep(1.0)

            pos_brand = self.manufacturer.scan_for_text(
                "斯巴鲁",
                label="刷图车辆制造商",
            )
            if not pos_brand:
                self.log("未找到刷图车辆制造商。", level="warning")
                return False

            self.input_actions.game_click(pos_brand)
            sleep(1.2)
            race_car_result = self.car_cards.find(
                CarCardSearchOptions(
                    card_path=self.RACE_CAR_TEMPLATE,
                    label="目标跑图车辆",
                    required_tag_path=self.RACE_CAR_FAVORITE_TAG,
                    tag_threshold=0.55,
                    max_pages=20,
                    turn_key_delay=0.08,
                )
            )

        if not race_car_result:
            self.log("翻页未能找到目标跑图车辆。", level="warning")
            return False

        self.input_actions.game_click(race_car_result.position)
        sleep(0.5)
        self.input_actions.hw_press("enter")
        sleep(4.0)

        self.log("前置完成，开始循环跑图！", level="debug")

        while self.state.race_counter < effective_target:
            self.log(
                f"跑图 {self.state.race_counter + 1}/{effective_target}: 找开始竞赛赛事按钮...",
                level="debug",
            )

            pos = self.image_waits.wait_for_footer_text_ui("选择")

            if not pos:
                self.log("未找到'选择'按钮。", level="warning")
                return False
            self.input_actions.hw_press("enter")
            # self.input_actions.game_click(pos)

            sleep(4.0)
            self.input_actions.hw_key_down("w")

            # 初始化各类计时器
            race_start_time = time.time()
            last_like_chk = time.time()
            last_chk = 0
            finished = False
            timeout_triggered = False  # 标记是否触发超时

            driving_keys_held = True  # 标记油门状态

            while True:
                self.runtime.ensure_running()

                # ====== 跑图专用暂停处理逻辑 ======
                if self.state.is_paused:
                    if driving_keys_held:  # 刚进入暂停，松开油门
                        self.input_actions.hw_key_up("w")
                        driving_keys_held = False
                    self.runtime.check_pause()  # 阻塞在此处
                    self.runtime.ensure_running()
                    # 从暂停中恢复，如果还没跑完，重新按下油门
                    self.input_actions.hw_key_down("w")
                    driving_keys_held = True

                    # 避免恢复瞬间触发超时，重置计时器
                    race_start_time = time.time()
                    last_like_chk = time.time()
                    last_chk = time.time()
                    continue
                # =========================================
                now = time.time()

                if now - race_start_time > self.RACE_TIMEOUT_SECONDS:
                    self.log(
                        f"跑图超时(已超过{self.RACE_TIMEOUT_SECONDS:.0f}秒)！触发强制重开赛事逻辑...", level="warning"
                    )
                    timeout_triggered = True
                    break

                # 每隔3秒处理一次跑图中的特殊界面/异常
                if now - last_like_chk >= 3.0:
                    vram_result = self.recovery.check_vramne_during_race()
                    if vram_result is True:
                        self.log("VRAM恢复完成，结束当前跑图流程，交给外层重新恢复。", level="warning")
                        return False
                    elif vram_result is False:
                        self.log("VRAM恢复失败。", level="warning")
                        return False
                    pos_like = self._find_like_author_prompt()
                    if pos_like:
                        self.log("识别到点赞作界面，执行回车确认！", level="debug")
                        self.input_actions.hw_press("enter")
                    last_like_chk = now

                # 每1秒检测一次结果页(正常完赛)
                if now - last_chk >= 1.0:
                    found_result = self.footer.find_text("重新开始")
                    if found_result:
                        finished = True
                        break
                    last_chk = now

                sleep(1.0)

            # 无论正常结束还是超时，都必须先松开油门
            self.input_actions.hw_key_up("w")
            self.runtime.ensure_running()

            # ====== 执行超时重置操作 ======
            if timeout_triggered:
                self._restart_timed_out_race()
                # 【关键】：直接跳过下方的结算流程，回到最外层 while 重新找 start.png（并且本次不计入 race_counter）
                continue
            # ========================================

            if not finished:
                return False

            if self.state.race_counter == effective_target - 1:
                self.input_actions.hw_press("enter")
                sleep(2.0)
            else:
                self.input_actions.hw_press("x")
                sleep(0.8)
                self.input_actions.hw_press("enter")
                sleep(2.0)

            self.state.race_counter += 1
            self.state.set_task("循环跑图", self.state.race_counter, effective_target)

        self.log(f"循环跑图流程结束：完成 {self.state.race_counter - start_count} 次。")
        return True
