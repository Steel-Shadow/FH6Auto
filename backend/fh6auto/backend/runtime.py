from __future__ import annotations

from dataclasses import dataclass
import math
import threading
import time
from typing import TYPE_CHECKING, Any

from pynput import keyboard

if TYPE_CHECKING:
    from .app import BackendApp

STEP_LABELS = {
    "race": "循环跑图",
    "buy": "批量买车",
    "mastery": "熟练度加点",
    "auto_wheelspin": "自动抽奖",
    "sell": "移除车辆",
}

PIPELINE_STEPS = ("race", "buy", "mastery", "auto_wheelspin", "sell")
DEFAULT_RACE_SKILL_POINTS = 50


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
    remove_car_count: int
    remove_car_use_all: bool
    total_loops: int
    infinite_loops: bool
    continue_steps: tuple[bool, bool, bool, bool, bool]
    next_steps: tuple[int, int, int, int, int]


class BackendRuntimeService:
    def __init__(self, app: BackendApp) -> None:
        self.app = app

    def _int_config(self, key: str, default: int, minimum: int | None = None) -> int:
        try:
            value = int(self.app.services.config.values.get(key, default))
        except Exception:
            value = default

        if minimum is not None:
            value = max(minimum, value)
        return value

    def _next_step_config(self, key: str, default: int) -> int:
        return max(0, min(len(PIPELINE_STEPS) - 1, self._int_config(key, default) - 1))

    def _read_pipeline_options(self) -> PipelineOptions:
        return PipelineOptions(
            race_count=self._int_config("race_count", 99, minimum=0),
            race_until_skill_cap=bool(self.app.services.config.values.get("race_until_skill_cap", False)),
            buy_count=self._int_config("buy_count", 30, minimum=0),
            mastery_count=self._int_config("mastery_count", 30, minimum=0),
            mastery_use_all=bool(self.app.services.config.values.get("mastery_use_all", False)),
            wheelspin_count=self._int_config("wheelspin_count", 30, minimum=0),
            normal_wheelspin_count=self._int_config("normal_wheelspin_count", 0, minimum=0),
            wheelspin_use_all=bool(self.app.services.config.values.get("wheelspin_use_all", False)),
            super_wheelspin_use_all=bool(self.app.services.config.values.get("super_wheelspin_use_all", False)),
            normal_wheelspin_use_all=bool(self.app.services.config.values.get("normal_wheelspin_use_all", False)),
            remove_car_count=self._int_config("sc_count", 30, minimum=0),
            remove_car_use_all=bool(self.app.services.config.values.get("remove_car_use_all", False)),
            total_loops=self._int_config("global_loops", 10, minimum=1),
            infinite_loops=bool(self.app.services.config.values.get("global_loop_infinite", False)),
            continue_steps=(
                bool(self.app.services.config.values.get("chk_1", False)),
                bool(self.app.services.config.values.get("chk_2", False)),
                bool(self.app.services.config.values.get("chk_3", False)),
                bool(self.app.services.config.values.get("chk_4", True)),
                bool(self.app.services.config.values.get("chk_5", True)),
            ),
            next_steps=(
                self._next_step_config("next_1", 2),
                self._next_step_config("next_2", 3),
                self._next_step_config("next_3", 4),
                self._next_step_config("next_4", 5),
                self._next_step_config("next_5", 1),
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
            "sc_count": cars_per_loop,
            "remove_car_use_all": False,
            "global_loops": final_loops,
            "global_loop_infinite": False,
            "calc_a": str(target_cr),
            "calc_b": str(cost_per_car),
            "calc_c": str(sp_per_car),
            "calc_d": str(sp_per_race),
        }
        if apply:
            self.app.services.config.update(updates)
            self.app.state.set_task("等待中", 0, 0)

        self.app.log(
            f"计算完成：总计需 {total_cars} 车，共跑图 {total_races} 次；"
            f"分配为 {final_loops} 个大循环，每轮跑图 {final_races_per_loop} 次，动作 {cars_per_loop} 辆。"
        )
        return {
            "total_cars": total_cars,
            "total_races": total_races,
            "updates": updates,
            "config": self.app.services.config.values.copy(),
        }

    def start_pipeline(self, start_step: str) -> bool:
        if self.app.state.is_running:
            return False
        if start_step not in PIPELINE_STEPS:
            raise ValueError(f"Unknown pipeline step: {start_step}")

        self.app.services.config.save()
        self.app.services.input_actions.apply_input_backend(log_change=False)
        options = self._read_pipeline_options()
        loop_total = 0 if options.infinite_loops else options.total_loops

        self.app.state.reset_counters()
        self.app.state.reset_progress()
        self.app.state.set_loop(0, loop_total)
        self.app.state.mark_started()
        self.app.state.set_task("等待中", 0, 0)
        self.app.state.set_task("初始化中...")
        self.app.log(f"启动流程：{STEP_LABELS.get(start_step, start_step)}")

        self.app.state.current_thread = threading.Thread(
            target=self._run_pipeline,
            args=(start_step, options),
            daemon=True,
        )
        self.app.state.current_thread.start()
        return True

    def _run_pipeline(self, start_step: str, options: PipelineOptions) -> None:
        try:
            if not self.app.services.game_window.check_and_focus_game():
                return

            curr_idx = PIPELINE_STEPS.index(start_step)
            total_loops = options.total_loops
            loop_total = 0 if options.infinite_loops else total_loops
            self.app.state.set_loop(1, loop_total)

            continuous_failures = 0
            max_recoveries = 10

            while self.app.state.is_running:
                step_name = PIPELINE_STEPS[curr_idx]
                success = False

                try:
                    if step_name == "race":
                        success = self.app.flows.race.logic_race(
                            options.race_count,
                            until_skill_cap=options.race_until_skill_cap,
                        )
                    elif step_name == "buy":
                        success = self.app.flows.buy_car.logic_buy_car(options.buy_count)
                    elif step_name == "mastery":
                        success = self.app.flows.mastery.logic_mastery(
                            options.mastery_count,
                            use_all=options.mastery_use_all,
                        )
                    elif step_name == "auto_wheelspin":
                        success = self.app.flows.auto_wheelspin.logic_auto_wheelspin(
                            options.wheelspin_count,
                            normal_count=options.normal_wheelspin_count,
                            use_all=options.wheelspin_use_all,
                            super_use_all=options.super_wheelspin_use_all,
                            normal_use_all=options.normal_wheelspin_use_all,
                        )
                    elif step_name == "sell":
                        success = self.app.flows.remove_car.find_and_remove_consumable_car(
                            options.remove_car_count,
                            use_all=options.remove_car_use_all,
                        )
                except FlowCancelled:
                    break
                except Exception as e:
                    self.app.log(f"执行模块 {STEP_LABELS.get(step_name, step_name)} 时异常: {e}")
                    success = False

                if not self.app.state.is_running:
                    break

                if not success:
                    continuous_failures += 1
                    if continuous_failures > max_recoveries:
                        self.app.log(f"连续 {continuous_failures} 次恢复失败，已强制终止任务。")
                        break

                    self.app.log(f"正在进行全局恢复 ({continuous_failures}/{max_recoveries})...")
                    if self.app.services.recovery.attempt_recovery():
                        continue
                    self.app.log("致命错误：恢复失败，任务停止。")
                    break

                continuous_failures = 0
                next_idx = curr_idx + 1
                if options.continue_steps[curr_idx]:
                    next_idx = options.next_steps[curr_idx]
                else:
                    break

                if next_idx <= curr_idx:
                    self.app.state.set_loop(self.app.state.loop_current + 1, loop_total)

                    if not options.infinite_loops and self.app.state.loop_current > total_loops:
                        self.app.log("达到设定的总循环次数，任务结束。")
                        break

                    loop_label = "∞" if options.infinite_loops else str(total_loops)
                    self.app.log(f"开启新一轮大循环 ({self.app.state.loop_current}/{loop_label})")
                    self.app.state.reset_counters()
                    self.app.state.set_task("等待中", 0, 0)

                curr_idx = next_idx
        finally:
            self.stop_all(log_message="任务已停止，所有输入状态已重置。")

    def stop_all(self, log_message: str = "任务已停止，所有输入状态已重置。") -> None:
        was_running = self.app.state.is_running
        self.app.state.mark_idle()

        try:
            self.app.services.input_actions.release_all()
        except Exception:
            pass

        try:
            if self.app.services.ocr.release():
                self.app.log("OCR 引擎已释放，CUDA 会话将在下次识别时重新初始化。")
        except Exception as e:
            self.app.log(f"释放 OCR 引擎失败: {e}")

        if was_running:
            self.app.log(log_message)

    def start_test_boot(self) -> bool:
        if self.app.state.is_running:
            self.app.log("已有任务正在运行，请先停止后再测试启动流程。")
            return False

        self.app.services.config.save()
        self.app.state.reset_progress()
        self.app.state.set_task("测试启动流程...")
        self.app.state.mark_started()
        self.app.log("====== 开始独立测试自动开机与识别流程 ======")

        def test_runner() -> None:
            try:
                success = self.app.services.recovery.restart_game_and_boot(force_test=True)
                if success:
                    self.app.log("测试结束：自动开机、状态机识别并到达菜单。")
                else:
                    self.app.log("测试结束：自动开机流程失败，请检查截图或日志。")
            finally:
                self.stop_all()

        self.app.state.current_thread = threading.Thread(target=test_runner, daemon=True)
        self.app.state.current_thread.start()
        return True

    def toggle_pause(self) -> bool:
        if not self.app.state.is_running:
            return False

        self.app.state.mark_paused(not self.app.state.is_paused)

        if self.app.state.is_paused:
            self.app.log("任务已暂停。")
            try:
                self.app.services.input_actions.release_all()
            except Exception:
                pass
        else:
            self.app.log("任务已恢复。")
        return self.app.state.is_paused

    def check_pause(self) -> None:
        while self.app.state.is_paused and self.app.state.is_running:
            time.sleep(0.1)

    def debug(self) -> None:
        is_running = self.app.state.is_running
        self.app.state.is_running = True

        time_start = time.time()
        output = self.app.services.ocr.find_manufacturer_text("斯巴鲁")
        time_end = time.time()
        self.app.log(f"Debug info: {output}, Time taken: {time_end - time_start}", level="debug")

        # time_start = time.time()
        # screen_bgr = self.app.services.image_cache.capture_region(self.app.services.game_window.regions["全界面"])
        # output = self.app.services.ocr._find_manufacturer_cells(screen_bgr)
        # time_end = time.time()
        # self.app.log(f"Debug info: {output}, Time taken: {time_end - time_start}", level="debug")

        self.app.state.is_running = is_running

    def ensure_running(self) -> None:
        self.check_pause()
        if not self.app.state.is_running:
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
                    self.stop_all()
                elif k == keyboard.Key.f1:
                    self.toggle_pause()
                elif k == keyboard.Key.f3:
                    self.debug()

            try:
                with keyboard.Listener(on_press=on_press) as listener:
                    listener.join()
            except Exception as e:
                self.app.log(f"快捷键监听启动失败: {e}")

        threading.Thread(target=hotkey_thread, daemon=True).start()
