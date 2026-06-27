from __future__ import annotations
from importlib.metadata import version
from pathlib import Path
import threading
from typing import Any
from collections.abc import Callable

from ..paths import LOG_FILE, auto_extract_images
from .state import RuntimeState
from ..automation import GameWindowService, RecoveryService
from ..flows.auto_wheelspin import AutoWheelspinFlow
from ..flows.buy_car import BuyCarFlow
from ..flows.mastery import MasteryFlow
from ..flows.race import RaceFlow
from ..flows.remove_car import RemoveCarFlow
from ..input import InputActionsService
from ..vision import (
    ImageCacheService,
    FooterDetector,
    ImageMatcherService,
    ImageWaitsService,
    ManufacturerDetector,
    OcrService,
    PlayerStatsDetector,
    TextDetector,
)
from .config_service import BackendConfigService
from .runtime import BackendRuntimeService


class BackendApp:
    def __init__(self) -> None:
        self.state = RuntimeState()
        self.version = version("fh6auto")
        self._log_file_lock = threading.RLock()

        self.services = AppServices(state=self.state, log=self.log)
        self.services.config.load()
        self.services.game_window.init_regions()

        self.flows = AppFlows(state=self.state, services=self.services, log=self.log)

        self.services.input_actions.apply_input_backend(log_change=False)
        self.services.runtime.start_hotkey_listener()
        threading.Thread(target=self._background_init, daemon=True).start()
        self.log(f"FH6Auto 后端已启动，当前版本 v{self.version}。")

    def _background_init(self) -> None:
        auto_extract_images()

    def snapshot(self) -> dict[str, Any]:
        runtime = self.state.snapshot()
        runtime.update(
            {
                "regions": self.services.game_window.regions,
            }
        )
        return {
            "version": self.version,
            "runtime": runtime,
            "config": self.services.config.snapshot(),
        }

    def _write_log_file(self, item: dict[str, Any]) -> None:
        try:
            log_path = Path(LOG_FILE)
            log_path.parent.mkdir(parents=True, exist_ok=True)
            with self._log_file_lock:
                with log_path.open("a", encoding="utf-8") as f:
                    f.write(f"[{item['time']}] [{item['level'].upper()}] {item['message']}\n")
        except Exception as e:
            print(f"写入日志文件失败: {e}", flush=True)

    def log(self, message: str, level: str = "info") -> None:
        level = self.state.normalize_log_level(level)
        min_level = "info"
        try:
            min_level = str(self.services.config.values.get("log_level", "info"))
        except Exception:
            pass

        item = self.state.append_log(str(message), level=level, min_level=min_level)
        if item is not None:
            print(f"[{item['time']}] [{level.upper()}] {message}", flush=True)
            self._write_log_file(item)


LogFn = Callable[..., None]


class AppServices:
    def __init__(self, *, state, log: LogFn) -> None:
        self.config = BackendConfigService(log=log)
        self.game_window = GameWindowService(log=log)
        self.input_actions = InputActionsService(
            get_game_region=lambda: self.game_window.regions["全界面"],
            save_config=self.config.save,
            log=log,
        )
        self.config.set_apply_input_backend(lambda: self.input_actions.apply_input_backend(log_change=False))
        self.runtime = BackendRuntimeService(
            state=state,
            config=self.config,
            game_window=self.game_window,
            input_actions=self.input_actions,
            log=log,
        )
        self.input_actions.set_ensure_running(self.runtime.ensure_running)
        self.image_cache = ImageCacheService(game_window=self.game_window, log=log)
        self.ocr = OcrService(
            state=state,
            image_cache=self.image_cache,
            game_window=self.game_window,
            log=log,
        )
        self.image_matcher = ImageMatcherService(
            state=state,
            image_cache=self.image_cache,
            ocr=self.ocr,
            log=log,
        )
        self.text = TextDetector(
            state=state,
            image_cache=self.image_cache,
            ocr=self.ocr,
            log=log,
        )
        self.ocr.text_detector = self.text
        self.recovery = RecoveryService(
            state=state,
            config=self.config,
            game_window=self.game_window,
            image_matcher=self.image_matcher,
            ocr=self.ocr,
            text_detector=self.text,
            input_actions=self.input_actions,
            check_pause=self.runtime.check_pause,
            log=log,
        )
        self.runtime.set_runtime_dependencies(
            recovery=self.recovery,
            ocr=self.ocr,
            image_matcher=self.image_matcher,
        )
        self.manufacturer = ManufacturerDetector(
            state=state,
            image_cache=self.image_cache,
            ocr=self.ocr,
            input_actions=self.input_actions,
            config=self.config,
            log=log,
        )
        self.ocr.manufacturer = self.manufacturer
        self.player_stats = PlayerStatsDetector(
            state=state,
            image_cache=self.image_cache,
            game_window=self.game_window,
            ocr=self.ocr,
            log=log,
        )
        self.ocr.player_stats = self.player_stats
        self.footer = FooterDetector(
            state=state,
            image_cache=self.image_cache,
            game_window=self.game_window,
            ocr=self.ocr,
            log=log,
        )
        self.ocr.footer = self.footer
        self.image_waits = ImageWaitsService(
            state=state,
            image_matcher=self.image_matcher,
            ocr=self.ocr,
            text_detector=self.text,
            footer=self.footer,
            manufacturer=self.manufacturer,
        )


class AppFlows:
    def __init__(self, *, state, services: Any, log: LogFn) -> None:
        self.race = RaceFlow(
            state=state,
            config=services.config,
            game_window=services.game_window,
            input_actions=services.input_actions,
            image_matcher=services.image_matcher,
            image_waits=services.image_waits,
            manufacturer=services.manufacturer,
            footer=services.footer,
            player_stats=services.player_stats,
            recovery=services.recovery,
            runtime=services.runtime,
            sleep=services.runtime.sleep,
            log=log,
        )
        self.buy_car = BuyCarFlow(
            state=state,
            config=services.config,
            recovery=services.recovery,
            input_actions=services.input_actions,
            image_waits=services.image_waits,
            image_matcher=services.image_matcher,
            manufacturer=services.manufacturer,
            player_stats=services.player_stats,
            sleep=services.runtime.sleep,
            log=log,
        )
        self.mastery = MasteryFlow(
            state=state,
            config=services.config,
            game_window=services.game_window,
            input_actions=services.input_actions,
            image_matcher=services.image_matcher,
            image_waits=services.image_waits,
            manufacturer=services.manufacturer,
            player_stats=services.player_stats,
            recovery=services.recovery,
            sleep=services.runtime.sleep,
            log=log,
        )
        self.auto_wheelspin = AutoWheelspinFlow(
            state=state,
            config=services.config,
            game_window=services.game_window,
            image_cache=services.image_cache,
            image_matcher=services.image_matcher,
            input_actions=services.input_actions,
            ocr=services.ocr,
            player_stats=services.player_stats,
            recovery=services.recovery,
            runtime=services.runtime,
            log=log,
        )
        self.remove_car = RemoveCarFlow(
            state=state,
            game_window=services.game_window,
            input_actions=services.input_actions,
            image_matcher=services.image_matcher,
            image_waits=services.image_waits,
            manufacturer=services.manufacturer,
            footer=services.footer,
            recovery=services.recovery,
            sleep=services.runtime.sleep,
            log=log,
        )
        services.runtime.set_flows(self)
