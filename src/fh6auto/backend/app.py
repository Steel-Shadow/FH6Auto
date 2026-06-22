from __future__ import annotations

from importlib.metadata import version
from pathlib import Path
import threading
from typing import Any

from ..paths import LOG_FILE, auto_extract_images
from .composition import AppFlows, AppServices
from .state import RuntimeState


class BackendApp:
    def __init__(self) -> None:
        self.state = RuntimeState()
        self.version = version("fh6auto")
        self._log_file_lock = threading.RLock()

        self.services = AppServices(self)
        self.services.config.load()
        self.services.game_window.init_regions()

        self.flows = AppFlows(self)

        self.services.input_actions.apply_input_backend(log_change=False)
        self.services.runtime.start_hotkey_listener()
        threading.Thread(target=self._background_init, daemon=True).start()
        self.log(f"FH6Auto 后端已启动，当前版本 v{self.version}。")

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
