import ctypes
import time
from typing import Callable

import pydirectinput

from .win_input import DIK_CODES, Input, Input_I, KeyBdInput, MouseInput, SendInput


RegionFn = Callable[[], tuple[int, int, int, int]]

class KeyboardMouseController():
    def __init__(self, get_game_region: RegionFn):
        self.get_game_region = get_game_region

    def _keyboard_key(self, key: str) -> str:
        return "esc" if key == "menu" else key

    def key_down(self, key: str) -> None:
        key = self._keyboard_key(key)
        if key not in DIK_CODES:
            return
        scan_code, extended = DIK_CODES[key]
        flags = 0x0008 | (0x0001 if extended else 0)
        extra = ctypes.c_ulong(0)
        ii_ = Input_I()
        ii_.ki = KeyBdInput(0, scan_code, flags, 0, ctypes.pointer(extra))
        x = Input(ctypes.c_ulong(1), ii_)
        SendInput(1, ctypes.pointer(x), ctypes.sizeof(x))

    def key_up(self, key: str) -> None:
        key = self._keyboard_key(key)
        if key not in DIK_CODES:
            return
        scan_code, extended = DIK_CODES[key]
        flags = 0x000A | (0x0001 if extended else 0)
        extra = ctypes.c_ulong(0)
        ii_ = Input_I()
        ii_.ki = KeyBdInput(0, scan_code, flags, 0, ctypes.pointer(extra))
        x = Input(ctypes.c_ulong(1), ii_)
        SendInput(1, ctypes.pointer(x), ctypes.sizeof(x))

    def press(self, key: str, delay: float = 0.08) -> None:
        self.key_down(key)
        time.sleep(delay)
        self.key_up(key)

    def mouse_move(self, x: int, y: int) -> None:
        SM_XVIRTUALSCREEN = 76
        SM_YVIRTUALSCREEN = 77
        SM_CXVIRTUALSCREEN = 78
        SM_CYVIRTUALSCREEN = 79
        left = ctypes.windll.user32.GetSystemMetrics(SM_XVIRTUALSCREEN)
        top = ctypes.windll.user32.GetSystemMetrics(SM_YVIRTUALSCREEN)
        width = ctypes.windll.user32.GetSystemMetrics(SM_CXVIRTUALSCREEN)
        height = ctypes.windll.user32.GetSystemMetrics(SM_CYVIRTUALSCREEN)
        if width == 0 or height == 0:
            return

        calc_x = int((x - left) * 65535 / width)
        calc_y = int((y - top) * 65535 / height)
        flags = 0x0001 | 0x8000 | 0x4000
        extra = ctypes.c_ulong(0)
        ii_ = Input_I()
        ii_.mi = MouseInput(calc_x, calc_y, 0, flags, 0, ctypes.pointer(extra))
        cmd = Input(ctypes.c_ulong(0), ii_)
        SendInput(1, ctypes.pointer(cmd), ctypes.sizeof(cmd))

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
        for key in DIK_CODES.keys():
            self.key_up(key)

        for key in ["w", "e", "y", "enter", "esc", "up", "down", "left", "right", "space", "backspace"]:
            self.key_up(key)

        try:
            pydirectinput.mouseUp()
        except Exception:
            pass


