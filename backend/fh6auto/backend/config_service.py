from __future__ import annotations

from typing import TYPE_CHECKING, Any

from ..config import DEFAULT_CONFIG, load_config_file, save_config_file
from ..paths import USER_CONFIG_FILE

if TYPE_CHECKING:
    from .app import BackendApp


class BackendConfigService:
    def __init__(self, app: BackendApp) -> None:
        self.app = app
        self.values: dict[str, Any] = DEFAULT_CONFIG.copy()

    def load(self) -> None:
        self.values, loaded = load_config_file(USER_CONFIG_FILE)
        if not loaded:
            self.app.log("用户 config.json 损坏，已自动恢复默认配置。")

    def save(self) -> None:
        try:
            save_config_file(USER_CONFIG_FILE, self.values)
        except Exception as e:
            self.app.log(f"保存配置失败: {e}")

    def update(self, updates: dict[str, Any]) -> dict[str, Any]:
        normalized = self._normalize_updates(updates)
        self.values.update(normalized)
        self.save()
        self.app.services.input_actions.apply_input_backend(log_change=False)
        self.app.log("配置已保存。")
        return self.snapshot()

    def snapshot(self) -> dict[str, Any]:
        return self.values.copy()

    def _normalize_updates(self, updates: dict[str, Any]) -> dict[str, Any]:
        normalized: dict[str, Any] = {}

        int_keys = {
            "race_count",
            "buy_count",
            "mastery_count",
            "wheelspin_count",
            "normal_wheelspin_count",
            "wheelspin_sell_threshold",
            "sc_count",
            "next_1",
            "next_2",
            "next_3",
            "next_4",
            "next_5",
            "global_loops",
            "manufacturer_scan_steps",
        }
        bool_keys = {
            "chk_1",
            "chk_2",
            "chk_3",
            "chk_4",
            "chk_5",
            "auto_restart",
            "race_until_skill_cap",
            "mastery_use_all",
            "wheelspin_use_all",
            "super_wheelspin_use_all",
            "normal_wheelspin_use_all",
            "remove_car_use_all",
        }
        string_keys = {"share_code", "restart_cmd", "calc_a", "calc_b", "calc_c", "calc_d", "log_level"}

        for key, value in updates.items():
            if key not in DEFAULT_CONFIG:
                continue
            if key in int_keys:
                try:
                    normalized[key] = max(0, int(value)) if key == "wheelspin_sell_threshold" else int(value)
                except Exception:
                    continue
            elif key in bool_keys:
                normalized[key] = bool(value)
            elif key in string_keys:
                normalized[key] = str(value).lower() if key == "log_level" else str(value)
            elif key == "skill_dirs":
                normalized[key] = self._valid_skill_dirs(value)
        return normalized

    def _valid_skill_dirs(self, value: Any) -> list[str]:
        if not isinstance(value, list):
            return list(self.values.get("skill_dirs", []))

        valid: list[str] = []
        row, col = 3, 0
        for item in value:
            direction = str(item)
            if direction == "up":
                row -= 1
            elif direction == "down":
                row += 1
            elif direction == "left":
                col -= 1
            elif direction == "right":
                col += 1
            else:
                continue

            if not (0 <= row < 4 and 0 <= col < 4):
                break
            valid.append(direction)
        return valid
