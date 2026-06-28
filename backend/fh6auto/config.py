import json
from pathlib import Path
from typing import Any


DEFAULT_CONFIG: dict[str, Any] = {
    "race_count": 99,
    "race_until_skill_cap": False,
    "buy_count": 30,
    "mastery_count": 30,
    "mastery_use_all": False,
    "wheelspin_count": 30,
    "normal_wheelspin_count": 0,
    "wheelspin_use_all": False,
    "super_wheelspin_use_all": False,
    "normal_wheelspin_use_all": False,
    "wheelspin_sell_threshold": 100000,
    "chk_1": True,
    "chk_2": True,
    "chk_3": True,
    "chk_4": True,
    "next_1": 2,
    "next_2": 3,
    "next_3": 4,
    "next_4": 1,
    "global_loops": 10,
    "global_loop_infinite": False,
    "skill_dirs": ["right", "up", "up", "up", "left"],
    "share_code": "659086805",
    "auto_restart": True,
    "restart_cmd": "start steam://run/2483190",
    "manufacturer_scan_steps": 50,
    "log_level": "info",
    "calc_a": "",
    "calc_b": "81700",
    "calc_c": "30",
    "calc_d": "50",
}

def current_config_only(raw_config: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in raw_config.items() if key in DEFAULT_CONFIG}


def load_config_file(path: str | Path) -> tuple[dict[str, Any], bool]:
    config = DEFAULT_CONFIG.copy()
    config_path = Path(path)
    loaded = True

    if config_path.exists():
        try:
            with config_path.open("r", encoding="utf-8") as f:
                raw_config = json.load(f)
            if isinstance(raw_config, dict):
                config.update(current_config_only(raw_config))
            else:
                loaded = False
        except Exception:
            loaded = False

    save_config_file(path, config)
    return config, loaded


def save_config_file(path: str | Path, config: dict[str, Any]) -> None:
    config_path = Path(path)
    config_path.parent.mkdir(parents=True, exist_ok=True)
    normalized = DEFAULT_CONFIG.copy()
    normalized.update(current_config_only(config))

    with config_path.open("w", encoding="utf-8") as f:
        json.dump(normalized, f, indent=4, ensure_ascii=False)
