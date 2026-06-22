from __future__ import annotations

from typing import TYPE_CHECKING

import pyautogui

from .controllers import KeyboardMouseController

if TYPE_CHECKING:
    from ..backend.app import BackendApp

pyautogui.FAILSAFE = False


class InputActionsService:
    def __init__(self, app: BackendApp) -> None:
        self.app = app
        self.keyboard_mouse_controller = KeyboardMouseController(
            lambda: self.app.services.game_window.regions["全界面"]
        )
        self.controller = self.keyboard_mouse_controller

    def apply_input_backend(self, log_change=True):
        self.controller = self.keyboard_mouse_controller
        if log_change:
            self.app.log("控制方式已固定为：键盘鼠标")
            self.app.services.config.save()

    # ==========================================
    # --- 核心操作与流程控制 ---
    # ==========================================
    def hw_key_down(self, key):
        self.app.services.runtime.ensure_running()
        self.controller.key_down(key)

    def hw_key_up(self, key):
        self.controller.key_up(key)

    def hw_press(self, key, delay=0.08):
        self.app.services.runtime.ensure_running()
        self.controller.press(key, delay=delay)

    def game_click(self, pos, double=False):
        self.app.services.runtime.ensure_running()
        if not pos:
            return
        self.controller.click(pos, double=double)

    def move_to_game_coord(self, x, y):
        """
        将鼠标移动到以【游戏窗口左上角】为起点的 (x, y) 坐标。
        例如传入 (5, 5)，就会移动到游戏内左上角 5 像素的安全位置。
        """
        self.app.services.runtime.ensure_running()
        self.controller.move_to_game_coord(x, y)

    def release_all(self):
        self.controller.release_all()
