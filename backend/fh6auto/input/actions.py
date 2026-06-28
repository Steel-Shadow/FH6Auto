from __future__ import annotations

from collections.abc import Callable

import pyautogui

from .controllers import KeyboardMouseController

pyautogui.FAILSAFE = False

RegionFn = Callable[[], tuple[int, int, int, int]]
LogFn = Callable[..., None]
ActionFn = Callable[[], None]


class InputActionsService:
    def __init__(
        self,
        *,
        get_game_region: RegionFn,
        save_config: ActionFn,
        log: LogFn,
        ensure_running: ActionFn | None = None,
    ) -> None:
        self.save_config = save_config
        self.log = log
        self.ensure_running = ensure_running or (lambda: None)
        self.keyboard_mouse_controller = KeyboardMouseController(get_game_region, log=log)
        self.controller = self.keyboard_mouse_controller

    def set_ensure_running(self, ensure_running: ActionFn) -> None:
        self.ensure_running = ensure_running

    def apply_input_backend(self, log_change=True):
        self.controller = self.keyboard_mouse_controller
        if log_change:
            self.log("控制方式已固定为：键盘鼠标")
            self.save_config()

    # ==========================================
    # --- 核心操作与流程控制 ---
    # ==========================================
    def hw_key_down(self, key):
        self.ensure_running()
        self.controller.key_down(key)

    def hw_key_up(self, key):
        self.controller.key_up(key)

    def hw_press(self, key, delay=0.08):
        self.ensure_running()
        self.controller.press(key, delay=delay)

    def game_click(self, pos, double=False):
        """点击屏幕绝对坐标；视觉识别服务对外统一返回这类坐标。"""
        self.ensure_running()
        if not pos:
            return
        self.controller.click(pos, double=double)

    def move_to_game_coord(self, x, y):
        """
        将鼠标移动到以【游戏窗口左上角】为起点的 (x, y) 坐标。
        例如传入 (5, 5)，就会移动到游戏内左上角 5 像素的安全位置。
        """
        self.ensure_running()
        self.controller.move_to_game_coord(x, y)

    def release_all(self):
        self.controller.release_all()
