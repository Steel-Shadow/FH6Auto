from __future__ import annotations

from collections.abc import Callable
from typing import Any

from ..config import DEFAULT_CONFIG, load_config_file, save_config_file
from ..paths import USER_CONFIG_FILE

LogFn = Callable[..., None]
ActionFn = Callable[[], None]


class BackendConfigService:
    def __init__(self, *, log: LogFn, apply_input_backend: ActionFn | None = None) -> None:
        self.log = log
        self.apply_input_backend = apply_input_backend or (lambda: None)
        self.values: dict[str, Any] = DEFAULT_CONFIG.copy()

    def set_apply_input_backend(self, apply_input_backend: ActionFn) -> None:
        self.apply_input_backend = apply_input_backend

    def load(self) -> None:
        self.values, loaded = load_config_file(USER_CONFIG_FILE)
        if not loaded:
            self.log("用户 config.json 损坏，已自动恢复默认配置。")

    def save(self) -> None:
        try:
            save_config_file(USER_CONFIG_FILE, self.values)
        except Exception as e:
            self.log(f"保存配置失败: {e}")

    def update(self, updates: dict[str, Any]) -> dict[str, Any]:
        normalized = self._normalize_updates(updates)
        self.values.update(normalized)
        self.save()
        self.apply_input_backend()
        self.log("配置已保存。")
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
            "next_1",
            "next_2",
            "next_3",
            "next_4",
            "global_loops",
            "manufacturer_scan_steps",
        }
        bool_keys = {
            "chk_1",
            "chk_2",
            "chk_3",
            "chk_4",
            "auto_restart",
            "race_until_skill_cap",
            "mastery_use_all",
            "wheelspin_use_all",
            "super_wheelspin_use_all",
            "normal_wheelspin_use_all",
            "global_loop_infinite",
        }
        string_keys = {"share_code", "restart_cmd", "calc_a", "calc_b", "calc_c", "calc_d", "log_level"}

        for key, value in updates.items():
            if key not in DEFAULT_CONFIG:
                continue
            if key in int_keys:
                try:
                    int_value = int(value)
                    if key == "wheelspin_sell_threshold":
                        normalized[key] = max(0, int_value)
                    elif key == "manufacturer_scan_steps":
                        normalized[key] = max(5, min(100, int_value))
                    else:
                        normalized[key] = int_value
                except Exception:
                    continue
            elif key in bool_keys:
                normalized[key] = bool(value)
            elif key in string_keys:
                normalized[key] = str(value).lower() if key == "log_level" else str(value)
            elif key == "skill_cells":
                normalized[key] = self._valid_skill_cells(value)
        return normalized

    def _valid_skill_cells(self, value: Any) -> list[int]:
        if not isinstance(value, list):
            return list(self.values.get("skill_cells", []))

        cells: list[int] = []
        seen: set[int] = set()
        for item in value:
            try:
                index = int(item)
            except Exception:
                continue
            if not (0 <= index < 16) or index == 12 or index in seen:
                continue
            seen.add(index)
            cells.append(index)

        connected = {12}
        remaining = set(cells)
        while True:
            added: set[int] = set()
            for index in remaining:
                if any(self._is_adjacent_skill_cell(index, connected_index) for connected_index in connected):
                    added.add(index)
            if not added:
                break
            connected.update(added)
            remaining.difference_update(added)

        return sorted(index for index in cells if index in connected)

    @staticmethod
    def _is_adjacent_skill_cell(left: int, right: int) -> bool:
        left_row, left_col = divmod(left, 4)
        right_row, right_col = divmod(right, 4)
        return abs(left_row - right_row) + abs(left_col - right_col) == 1
