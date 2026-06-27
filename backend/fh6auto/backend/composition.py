from __future__ import annotations

from collections.abc import Callable
from typing import Any

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
        self.config.set_apply_input_backend(
            lambda: self.input_actions.apply_input_backend(log_change=False)
        )
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
