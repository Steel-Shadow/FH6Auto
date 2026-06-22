from __future__ import annotations

import threading
from typing import Any

from ..paths import auto_extract_images
from ..version import CURRENT_VERSION
from .composition import AppFlows, AppServices
from .state import RuntimeState


class BackendApp:
    def __init__(self) -> None:
        self.state = RuntimeState()

        self.services = AppServices(self)
        self.services.config.load()
        self.services.game_window.init_regions()

        self.flows = AppFlows(self)

        self.services.input_actions.apply_input_backend(log_change=False)
        self.services.runtime.start_hotkey_listener()
        threading.Thread(target=self._background_init, daemon=True).start()
        self.log(f"FH6Auto 后端已启动，当前版本 v{CURRENT_VERSION}。")

    def _background_init(self) -> None:
        auto_extract_images()
        self.services.image_cache.prepare_template_cache()

    def snapshot(self) -> dict[str, Any]:
        runtime = self.state.snapshot()
        runtime.update(
            {
                "regions": self.services.game_window.regions,
            }
        )
        return {
            "version": CURRENT_VERSION,
            "runtime": runtime,
            "config": self.services.config.snapshot(),
        }

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
