from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import math
import threading
import time
from typing import TYPE_CHECKING, Any

from pynput import keyboard

from .config_service import BackendConfigService
from .state import RuntimeState
from ..window import GameWindowService
from ..input.actions import InputActionsService

if TYPE_CHECKING:
    from .app import AppFlows
    from ..recovery import RecoveryService
    from ..vision.cache import ImageCacheService
    from ..vision.manufacturer import ManufacturerDetector
    from ..vision.matcher import ImageMatcherService
    from ..vision.ocr import OcrService

STEP_LABELS = {
    "race": "循环跑图",
    "buy": "批量买车",
    "mastery": "熟练度加点 / 移除车辆",
    "auto_wheelspin": "自动抽奖",
}

PIPELINE_STEPS = ("race", "buy", "mastery", "auto_wheelspin")
DEFAULT_RACE_SKILL_POINTS = 50
LogFn = Callable[..., None]


class FlowCancelled(Exception):
    """Raised when the current automation run is stopped by the user."""


@dataclass(frozen=True)
class PipelineOptions:
    race_count: int
    race_until_skill_cap: bool
    buy_count: int
    mastery_count: int
    mastery_use_all: bool
    wheelspin_count: int
    normal_wheelspin_count: int
    wheelspin_use_all: bool
    super_wheelspin_use_all: bool
    normal_wheelspin_use_all: bool
    total_loops: int
    infinite_loops: bool
    continue_steps: tuple[bool, bool, bool, bool]
    next_steps: tuple[int, int, int, int]


class BackendRuntimeService:
    def __init__(
        self,
        *,
        state: RuntimeState,
        config: BackendConfigService,
        game_window: GameWindowService,
        input_actions: InputActionsService,
        log: LogFn,
    ) -> None:
        self.state = state
        self.config = config
        self.game_window = game_window
        self.input_actions = input_actions
        self.log = log
        self.recovery: RecoveryService | None = None
        self.image_cache: ImageCacheService | None = None
        self.manufacturer: ManufacturerDetector | None = None
        self.ocr: OcrService | None = None
        self.image_matcher: ImageMatcherService | None = None
        self.flows: AppFlows | None = None

    def set_runtime_dependencies(
        self,
        *,
        recovery: RecoveryService,
        ocr: OcrService,
        image_matcher: ImageMatcherService,
    ) -> None:
        self.recovery = recovery
        self.ocr = ocr
        self.image_matcher = image_matcher

    def set_debug_dependencies(
        self,
        *,
        image_cache: ImageCacheService,
        manufacturer: ManufacturerDetector,
    ) -> None:
        self.image_cache = image_cache
        self.manufacturer = manufacturer

    def set_flows(self, flows: AppFlows) -> None:
        self.flows = flows

    def _get_recovery(self) -> RecoveryService:
        if self.recovery is None:
            raise RuntimeError("Runtime dependency not bound: recovery")
        return self.recovery

    def _get_ocr(self) -> OcrService:
        if self.ocr is None:
            raise RuntimeError("Runtime dependency not bound: ocr")
        return self.ocr

    def _get_image_cache(self) -> ImageCacheService:
        if self.image_cache is None:
            raise RuntimeError("Runtime dependency not bound: image_cache")
        return self.image_cache

    def _get_manufacturer(self) -> ManufacturerDetector:
        if self.manufacturer is None:
            raise RuntimeError("Runtime dependency not bound: manufacturer")
        return self.manufacturer

    def _get_image_matcher(self) -> ImageMatcherService:
        if self.image_matcher is None:
            raise RuntimeError("Runtime dependency not bound: image_matcher")
        return self.image_matcher

    def _get_flows(self) -> AppFlows:
        if self.flows is None:
            raise RuntimeError("Runtime dependency not bound: flows")
        return self.flows

    def _int_config(self, key: str, default: int, minimum: int | None = None) -> int:
        try:
            value = int(self.config.values.get(key, default))
        except Exception:
            value = default

        if minimum is not None:
            value = max(minimum, value)
        return value

    def _next_step_config(self, key: str, default: int) -> int:
        value = self._int_config(key, default)
        if value < 1 or value > len(PIPELINE_STEPS):
            value = default
        return value - 1

    def _read_pipeline_options(self) -> PipelineOptions:
        return PipelineOptions(
            race_count=self._int_config("race_count", 99, minimum=0),
            race_until_skill_cap=bool(self.config.values.get("race_until_skill_cap", False)),
            buy_count=self._int_config("buy_count", 30, minimum=0),
            mastery_count=self._int_config("mastery_count", 30, minimum=0),
            mastery_use_all=bool(self.config.values.get("mastery_use_all", False)),
            wheelspin_count=self._int_config("wheelspin_count", 30, minimum=0),
            normal_wheelspin_count=self._int_config("normal_wheelspin_count", 0, minimum=0),
            wheelspin_use_all=bool(self.config.values.get("wheelspin_use_all", False)),
            super_wheelspin_use_all=bool(self.config.values.get("super_wheelspin_use_all", False)),
            normal_wheelspin_use_all=bool(self.config.values.get("normal_wheelspin_use_all", False)),
            total_loops=self._int_config("global_loops", 10, minimum=1),
            infinite_loops=bool(self.config.values.get("global_loop_infinite", False)),
            continue_steps=(
                bool(self.config.values.get("chk_1", False)),
                bool(self.config.values.get("chk_2", False)),
                bool(self.config.values.get("chk_3", False)),
                bool(self.config.values.get("chk_4", True)),
            ),
            next_steps=(
                self._next_step_config("next_1", 2),
                self._next_step_config("next_2", 3),
                self._next_step_config("next_3", 4),
                self._next_step_config("next_4", 1),
            ),
        )

    def calculate_pipeline(
        self,
        target_cr: int,
        cost_per_car: int = 81700,
        sp_per_car: int = 30,
        sp_per_race: int = DEFAULT_RACE_SKILL_POINTS,
        apply: bool = True,
    ) -> dict[str, Any]:
        if cost_per_car <= 0 or sp_per_car <= 0 or sp_per_race <= 0:
            raise ValueError("单车成本、单车技能点和单次跑图技能点必须大于 0。")

        total_cars = target_cr // cost_per_car
        total_races = math.ceil((total_cars * sp_per_car) / sp_per_race)
        if total_races <= 0:
            raise ValueError(f"目标金额不足，只够买 {total_cars} 辆车，无法产生有效跑图。")

        if total_races <= 99:
            final_loops = 1
            final_races_per_loop = total_races
        else:
            loops = math.ceil(total_races / 99)
            avg_races = total_races // loops
            if avg_races >= 70:
                final_loops = loops
                final_races_per_loop = avg_races
            else:
                final_races_per_loop = 99
                final_loops = total_races // 99

        cars_per_loop = (final_races_per_loop * sp_per_race) // sp_per_car
        if final_loops <= 0:
            raise ValueError("计算后可用大循环次数为 0。")

        updates = {
            "race_count": final_races_per_loop,
            "race_until_skill_cap": False,
            "buy_count": cars_per_loop,
            "mastery_count": cars_per_loop,
            "mastery_use_all": False,
            "wheelspin_count": cars_per_loop,
            "normal_wheelspin_count": 0,
            "wheelspin_use_all": False,
            "super_wheelspin_use_all": False,
            "normal_wheelspin_use_all": False,
            "chk_1": True,
            "chk_2": True,
            "chk_3": True,
            "chk_4": True,
            "next_1": 2,
            "next_2": 3,
            "next_3": 4,
            "next_4": 1,
            "global_loops": final_loops,
            "global_loop_infinite": False,
            "calc_a": str(target_cr),
            "calc_b": str(cost_per_car),
            "calc_c": str(sp_per_car),
            "calc_d": str(sp_per_race),
        }
        if apply:
            self.config.update(updates)
            self.state.set_task("等待中", 0, 0)

        self.log(
            f"计算完成：总计需 {total_cars} 车，共跑图 {total_races} 次；"
            f"分配为 {final_loops} 个大循环，每轮跑图 {final_races_per_loop} 次，动作 {cars_per_loop} 辆。"
        )
        return {
            "total_cars": total_cars,
            "total_races": total_races,
            "updates": updates,
            "config": self.config.values.copy(),
        }

    def start_pipeline(self, start_step: str) -> bool:
        if self.state.is_running:
            return False
        if start_step not in PIPELINE_STEPS:
            raise ValueError(f"Unknown pipeline step: {start_step}")

        self.config.save()
        self.input_actions.apply_input_backend(log_change=False)
        options = self._read_pipeline_options()
        loop_total = 0 if options.infinite_loops else options.total_loops

        self.state.reset_counters()
        self.state.reset_progress()
        self.state.set_loop(0, loop_total)
        self.state.mark_started()
        self.state.set_task("等待中", 0, 0)
        self.state.set_task("初始化中...")
        self.log(f"启动流程：{STEP_LABELS.get(start_step, start_step)}")

        self.state.current_thread = threading.Thread(
            target=self._run_pipeline,
            args=(start_step, options),
            daemon=True,
        )
        self.state.current_thread.start()
        return True

    def _run_pipeline(self, start_step: str, options: PipelineOptions) -> None:
        try:
            flows = self._get_flows()
            recovery = self._get_recovery()

            if not self.game_window.check_and_focus_game():
                return

            curr_idx = PIPELINE_STEPS.index(start_step)
            total_loops = options.total_loops
            loop_total = 0 if options.infinite_loops else total_loops
            self.state.set_loop(1, loop_total)

            continuous_failures = 0
            max_recoveries = 10

            while self.state.is_running:
                step_name = PIPELINE_STEPS[curr_idx]
                success = False

                try:
                    if step_name == "race":
                        success = flows.race.logic_race(
                            options.race_count,
                            until_skill_cap=options.race_until_skill_cap,
                        )
                    elif step_name == "buy":
                        success = flows.buy_car.logic_buy_car(options.buy_count)
                    elif step_name == "mastery":
                        success = flows.mastery.logic_mastery(
                            options.mastery_count,
                            use_all=options.mastery_use_all,
                        )
                    elif step_name == "auto_wheelspin":
                        success = flows.auto_wheelspin.logic_auto_wheelspin(
                            options.wheelspin_count,
                            normal_count=options.normal_wheelspin_count,
                            use_all=options.wheelspin_use_all,
                            super_use_all=options.super_wheelspin_use_all,
                            normal_use_all=options.normal_wheelspin_use_all,
                        )
                except FlowCancelled:
                    break
                except Exception as e:
                    self.log(f"执行模块 {STEP_LABELS.get(step_name, step_name)} 时异常: {e}")
                    success = False

                if not self.state.is_running:
                    break

                if not success:
                    continuous_failures += 1
                    if continuous_failures > max_recoveries:
                        self.log(f"连续 {continuous_failures} 次恢复失败，已强制终止任务。")
                        break

                    self.log(f"正在进行全局恢复 ({continuous_failures}/{max_recoveries})...")
                    if recovery.attempt_recovery():
                        continue
                    self.log("致命错误：恢复失败，任务停止。")
                    break

                continuous_failures = 0
                next_idx = curr_idx + 1
                if options.continue_steps[curr_idx]:
                    next_idx = options.next_steps[curr_idx]
                else:
                    break

                if next_idx <= curr_idx:
                    self.state.set_loop(self.state.loop_current + 1, loop_total)
                    completed_loop = max(1, self.state.loop_current - 1)
                    self.release_ocr_engine(f"大循环 {completed_loop} 结束")

                    if not options.infinite_loops and self.state.loop_current > total_loops:
                        self.log("达到设定的总循环次数，任务结束。")
                        break

                    loop_label = "∞" if options.infinite_loops else str(total_loops)
                    self.log(f"开启新一轮大循环 ({self.state.loop_current}/{loop_label})")
                    self.state.reset_counters()
                    self.state.set_task("等待中", 0, 0)

                curr_idx = next_idx
        finally:
            self.stop_all(log_message="任务已停止，所有输入状态已重置。")

    def release_ocr_engine(self, reason: str = "", *, level: str = "debug") -> None:
        ocr = self._get_ocr()
        try:
            released = ocr.release()
        except Exception as e:
            self.log(f"释放 OCR 引擎失败: {e}", level="warning")
            return

        if released:
            suffix = f"（{reason}）" if reason else ""
            self.log(f"OCR 引擎已释放{suffix}，下次识别会重新初始化。", level=level)

    def stop_all(self, log_message: str = "任务已停止，所有输入状态已重置。") -> None:
        was_running = self.state.is_running
        self.state.mark_idle()

        try:
            self.input_actions.release_all()
        except Exception:
            pass

        self.release_ocr_engine("任务停止")

        if was_running:
            self.log(log_message)

    def start_test_boot(self) -> bool:
        recovery = self._get_recovery()

        if self.state.is_running:
            self.log("已有任务正在运行，请先停止后再测试启动流程。")
            return False

        self.config.save()
        self.state.reset_progress()
        self.state.set_task("测试启动流程...")
        self.state.mark_started()
        self.log("====== 开始独立测试自动开机与识别流程 ======")

        def test_runner() -> None:
            try:
                success = recovery.restart_game_and_boot(force_test=True)
                if success:
                    self.log("测试结束：自动开机、状态机识别并到达菜单。")
                else:
                    self.log("测试结束：自动开机流程失败，请检查截图或日志。")
            finally:
                self.stop_all()

        self.state.current_thread = threading.Thread(target=test_runner, daemon=True)
        self.state.current_thread.start()
        return True

    def toggle_pause(self) -> bool:
        if not self.state.is_running:
            return False

        self.state.mark_paused(not self.state.is_paused)

        if self.state.is_paused:
            self.log("任务已暂停。")
            try:
                self.input_actions.release_all()
            except Exception:
                pass
        else:
            self.log("任务已恢复。")
        return self.state.is_paused

    def check_pause(self) -> None:
        while self.state.is_paused and self.state.is_running:
            time.sleep(0.1)

    def debug(self) -> None:
        is_running = self.state.is_running
        self.state.is_running = True
        if not self.game_window.check_and_focus_game():
            self.log("Debug: 无法定位并聚焦游戏窗口，已中止。", level="warning")
            return
        try:
            # press F3 and debug here
            manufacturer = self._get_manufacturer()
            res = manufacturer.scan_for_text("斯巴鲁")
            self.log(res, level="debug")
            if res:
                self.input_actions.game_click(res)

        except Exception as e:
            self.log(f"Debug: {e}", level="warning")
        finally:
            self.state.is_running = is_running

    def ensure_running(self) -> None:
        self.check_pause()
        if not self.state.is_running:
            raise FlowCancelled()

    def sleep(self, duration: float, *, step: float = 0.05) -> None:
        deadline = time.monotonic() + max(0.0, float(duration))
        while True:
            self.ensure_running()
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return
            time.sleep(min(max(step, 0.01), remaining))

    def start_hotkey_listener(self) -> None:
        def hotkey_thread() -> None:
            def on_press(k) -> None:
                if k == keyboard.Key.f2:
                    if self.state.is_running:
                        self.stop_all()
                    else:
                        self.start_pipeline("race")
                elif k == keyboard.Key.f1:
                    self.toggle_pause()
                elif k == keyboard.Key.f3:
                    self.debug()

            try:
                with keyboard.Listener(on_press=on_press) as listener:
                    listener.join()
            except Exception as e:
                self.log(f"快捷键监听启动失败: {e}")

        threading.Thread(target=hotkey_thread, daemon=True).start()
