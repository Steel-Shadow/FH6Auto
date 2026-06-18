from __future__ import annotations

from typing import TYPE_CHECKING

from ..automation import GameWindowService, RecoveryService
from ..flows.auto_wheelspin import AutoWheelspinFlow
from ..flows.buy_car import BuyCarFlow
from ..flows.mastery import MasteryFlow
from ..flows.race import RaceFlow
from ..flows.remove_car import RemoveCarFlow
from ..input import InputActionsService
from ..vision import ImageCacheService, ImageMatcherService, ImageWaitsService, OcrService
from .config_service import BackendConfigService
from .runtime import BackendRuntimeService

if TYPE_CHECKING:
    from .app import BackendApp


class AppServices:
    def __init__(self, app: BackendApp) -> None:
        self.config = BackendConfigService(app)
        self.game_window = GameWindowService(app)
        self.input_actions = InputActionsService(app)
        self.runtime = BackendRuntimeService(app)
        self.recovery = RecoveryService(app)
        self.image_cache = ImageCacheService(app)
        self.image_matcher = ImageMatcherService(app)
        self.ocr = OcrService(app)
        self.image_waits = ImageWaitsService(app)


class AppFlows:
    def __init__(self, app: BackendApp) -> None:
        self.race = RaceFlow(app)
        self.buy_car = BuyCarFlow(app)
        self.mastery = MasteryFlow(app)
        self.auto_wheelspin = AutoWheelspinFlow(app)
        self.remove_car = RemoveCarFlow(app)
