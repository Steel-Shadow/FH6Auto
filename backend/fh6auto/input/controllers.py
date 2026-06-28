from __future__ import annotations

import ctypes
import time
from collections.abc import Callable

import pydirectinput


RegionFn = Callable[[], tuple[int, int, int, int]]
KEY_ALIASES = {
    "menu": "esc",
    "delete": "del",
    "num0": "numpad0",
    "num1": "numpad1",
    "num2": "numpad2",
    "num3": "numpad3",
    "num4": "numpad4",
    "num5": "numpad5",
    "num6": "numpad6",
    "num7": "numpad7",
    "num8": "numpad8",
    "num9": "numpad9",
    "lshift": "shiftleft",
    "rshift": "shiftright",
    "lctrl": "ctrlleft",
    "rctrl": "ctrlright",
    "lalt": "altleft",
    "ralt": "altright",
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
    "pageup",
    "pagedown",
    "home",
    "end",
    "insert",
    "del",
    "space",
    "backspace",
    "shift",
    "ctrl",
    "alt",
)
EXTENDED_KEY_OFFSET = 1024
EXTENDED_KEYS = {
    "up",
    "down",
    "left",
    "right",
    "insert",
    "home",
    "pageup",
    "pagedown",
    "del",
    "delete",
    "end",
    "divide",
    "ctrlright",
    "altright",
    "win",
    "winleft",
    "winright",
    "apps",
}
NUMPAD_KEYBOARD_MAPPING = {
    "numpad0": 0x52,
    "numpad1": 0x4F,
    "numpad2": 0x50,
    "numpad3": 0x51,
    "numpad4": 0x4B,
    "numpad5": 0x4C,
    "numpad6": 0x4D,
    "numpad7": 0x47,
    "numpad8": 0x48,
    "numpad9": 0x49,
}

pydirectinput.FAILSAFE = False
pydirectinput.PAUSE = 0


class KeyboardMouseController:
    def __init__(self, get_game_region: RegionFn, log: Callable[..., None] | None = None):
        self.get_game_region = get_game_region
        self.log = log or (lambda *_args, **_kwargs: None)
        self._warned_unsupported_keys: set[str] = set()

    def _keyboard_key(self, key: str) -> str:
        normalized = str(key).lower()
        return KEY_ALIASES.get(normalized, normalized)

    def _key_code(self, key: str) -> int | None:
        if key in NUMPAD_KEYBOARD_MAPPING:
            return NUMPAD_KEYBOARD_MAPPING[key]
        code = pydirectinput.KEYBOARD_MAPPING.get(key)
        return int(code) if code is not None else None

    def _warn_unsupported_key(self, key: str) -> None:
        if key in self._warned_unsupported_keys:
            return
        self._warned_unsupported_keys.add(key)
        self.log(f"不支持的键盘按键: {key}", level="warning")

    @staticmethod
    def _extended_scan_code(code: int) -> int | None:
        if code < EXTENDED_KEY_OFFSET:
            return None

        raw_scan_code = code - EXTENDED_KEY_OFFSET
        # pydirectinput 的扩展键表使用 0x80 以上的 break code，例如 PageDown=0xD1。
        # SendInput + KEYEVENTF_SCANCODE 需要 make code；keyup 由 KEYEVENTF_KEYUP 表示。
        return raw_scan_code & 0x7F

    def _scan_code(self, key: str, code: int) -> int:
        extended_scan_code = self._extended_scan_code(code)
        if extended_scan_code is not None:
            return extended_scan_code
        return int(code)

    def _is_extended_key(self, key: str, code: int) -> bool:
        return key in EXTENDED_KEYS or code >= EXTENDED_KEY_OFFSET

    @staticmethod
    def _send_scancode(scan_code: int, *, key_up: bool = False, extended: bool = False) -> bool:
        flags = pydirectinput.KEYEVENTF_SCANCODE
        if key_up:
            flags |= pydirectinput.KEYEVENTF_KEYUP
        if extended:
            flags |= pydirectinput.KEYEVENTF_EXTENDEDKEY

        extra = ctypes.c_ulong(0)
        event = pydirectinput.Input_I()
        event.ki = pydirectinput.KeyBdInput(0, int(scan_code), flags, 0, ctypes.pointer(extra))
        payload = pydirectinput.Input(ctypes.c_ulong(1), event)
        inserted = pydirectinput.SendInput(1, ctypes.pointer(payload), ctypes.sizeof(payload))
        return inserted == 1

    def key_down(self, key: str) -> bool:
        key = self._keyboard_key(key)
        code = self._key_code(key)
        if code is None:
            self._warn_unsupported_key(key)
            return False

        return self._send_scancode(
            self._scan_code(key, code),
            extended=self._is_extended_key(key, code),
        )

    def key_up(self, key: str) -> bool:
        key = self._keyboard_key(key)
        code = self._key_code(key)
        if code is None:
            self._warn_unsupported_key(key)
            return False

        return self._send_scancode(
            self._scan_code(key, code),
            key_up=True,
            extended=self._is_extended_key(key, code),
        )

    def press(self, key: str, delay: float = 0.08) -> bool:
        key = self._keyboard_key(key)
        if self._key_code(key) is None:
            self._warn_unsupported_key(key)
            return False
        downed = self.key_down(key)
        time.sleep(delay)
        upped = self.key_up(key)
        return downed and upped

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


