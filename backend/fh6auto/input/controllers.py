import time
from typing import Callable

import pydirectinput


RegionFn = Callable[[], tuple[int, int, int, int]]
KEY_ALIASES = {
    "menu": "esc",
    "delete": "del",
    "lshift": "shift",
    "rshift": "shift",
    "lctrl": "ctrl",
    "rctrl": "ctrl",
    "lalt": "alt",
    "ralt": "alt",
}
RELEASE_KEYS = (
    "w",
    "e",
    "y",
    "enter",
    "esc",
    "up",
    "down",
    "left",
    "right",
    "space",
    "backspace",
    "shift",
    "ctrl",
    "alt",
)

pydirectinput.FAILSAFE = False
pydirectinput.PAUSE = 0


class KeyboardMouseController:
    def __init__(self, get_game_region: RegionFn):
        self.get_game_region = get_game_region

    def _keyboard_key(self, key: str) -> str:
        normalized = str(key).lower()
        return KEY_ALIASES.get(normalized, normalized)

    def _is_supported_key(self, key: str) -> bool:
        return key in pydirectinput.KEYBOARD_MAPPING

    def key_down(self, key: str) -> None:
        key = self._keyboard_key(key)
        if not self._is_supported_key(key):
            return
        pydirectinput.keyDown(key)

    def key_up(self, key: str) -> None:
        key = self._keyboard_key(key)
        if not self._is_supported_key(key):
            return
        pydirectinput.keyUp(key)

    def press(self, key: str, delay: float = 0.08) -> None:
        key = self._keyboard_key(key)
        if not self._is_supported_key(key):
            return
        # pydirectinput.press(interval=...) 的 interval 是按键完成后的间隔，不是按住时长。
        pydirectinput.keyDown(key)
        time.sleep(delay)
        pydirectinput.keyUp(key)

    def mouse_move(self, x: int, y: int) -> None:
        pydirectinput.moveTo(int(x), int(y))

    def click(self, pos: tuple[int, int] | None, double: bool = False) -> None:
        if not pos:
            return

        x, y = int(pos[0]), int(pos[1])
        self.mouse_move(x, y)
        time.sleep(0.2)
        for _ in range(2 if double else 1):
            pydirectinput.mouseDown()
            time.sleep(0.1)
            pydirectinput.mouseUp()
            time.sleep(0.1)
        time.sleep(0.1)
        try:
            gx, gy, _, _ = self.get_game_region()
            self.mouse_move(gx + 5, gy + 5)
        except Exception:
            self.mouse_move(5, 5)
        time.sleep(0.2)

    def move_to_game_coord(self, x: int, y: int) -> None:
        try:
            gx, gy, _, _ = self.get_game_region()
            self.mouse_move(gx + x, gy + y)
        except Exception:
            self.mouse_move(x, y)

    def release_all(self) -> None:
        for key in RELEASE_KEYS:
            self.key_up(key)

        try:
            pydirectinput.mouseUp()
        except Exception:
            pass


