from __future__ import annotations

import os
import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..backend.app import BackendApp


class RecoveryService:
    def __init__(self, app: BackendApp) -> None:
        self.app = app


    def restart_game_and_boot(self, force_test=False):
        # 除非点击了测试按钮(force_test)，否则检查设置里是否允许自动重启
        if not force_test:
            if not self.app.services.config.values.get("auto_restart", False):
                self.app.log("未开启自动重启，任务结束。")
                return False

        self.app.log("触发启动机制！正在拉起游戏...")
        try:
            cmd_str = self.app.services.config.values.get("restart_cmd", "start steam://run/2483190")
            os.system(cmd_str)
        except Exception as e:
            self.app.log(f"执行启动命令失败: {e}")
            return False

        self.app.log("等待游戏进程出现 (最多60秒)...")
        process_found = False
        for _ in range(120):
            if hasattr(self.app, "check_pause"):
                self.app.services.runtime.check_pause()
            if not self.app.state.is_running:
                return False
            if self.app.services.game_window.check_and_focus_game():
                process_found = True
                break
            time.sleep(1)

        if not process_found:
            self.app.log("未检测到游戏进程，启动失败。")
            return False

        self.app.log("游戏进程已启动，进入动态识别阶段 (限制5分钟)...")
        start_time = time.time()

        passed_screen_1 = False  # 记录是否已经按过画面1的回车
        last_continue_time = 0  # 记录最后一次看到/点击“继续按钮”的时间戳

        while self.app.state.is_running and time.time() - start_time < 300:
            if hasattr(self.app, "check_pause"):
                self.app.services.runtime.check_pause()

            # ==============================
            # 画面1：寻找左下角 horizon6.png -> 按回车
            # ==============================
            if not passed_screen_1:
                pos_h6 = self.app.services.image_matcher.find_image_sift(
                    "horizon6.png",
                    region=self.app.services.game_window.regions["全界面"],
                    min_inliers=8,
                )

                if pos_h6:
                    self.app.log("✅ 成功识别到 画面1 (horizon6.png)，按下【回车键】...")
                    time.sleep(1)
                    for _ in range(2):
                        self.app.services.input_actions.hw_press("enter")
                        time.sleep(1)
                    passed_screen_1 = True
                    # 激活画面2的倒计时机制，如果在后续的寻找中一直没看到画面2，也会在30秒后尝试进菜单
                    last_continue_time = time.time()
                    self.app.log("已确认画面1，强制等待 10 秒等待画面2加载...")
                    time.sleep(10)  # 等待10秒
                    continue
                else:
                    self.app.log("未找到画面1。正在使用全比例深度扫描...")

            # ==============================
            # 画面2：寻找右下角继续按钮，文字候选框由 OCR 统一识别。
            # ==============================
            # 只有在通过了画面1的前提下，才去寻找画面2
            if passed_screen_1:
                pos_continue = self.app.services.ocr.find_any_text_ui(
                    ["继续"],
                )
                if pos_continue:
                    self.app.log("识别到 画面2 (继续按钮)，进行点击...")
                    self.app.services.input_actions.game_click(pos_continue)

                    # 【核心逻辑】：只要点击了，就刷新时间戳！
                    last_continue_time = time.time()

                    time.sleep(3.0)  # 点击后过3秒再试，只要有就继续点
                    continue

                # ==============================
                # 状态转化：进入漫游与菜单呼出
                # ==============================
                # 如果当前时间 距离【最后一次点击画面2的时间】已经超过了 30秒，且期间再也没找到过
                time_since_last_seen = time.time() - last_continue_time
                if time_since_last_seen >= 30.0:
                    self.app.log("✅ 已经连续 30 秒未再发现继续按钮，判定为漫游载入完毕！开始尝试进入菜单...")

                    if self.enter_menu():
                        self.app.log("🎉 验证成功：已成功进入游戏主菜单！启动流程完美结束。")
                        return True
                    else:
                        self.app.log("普通进入菜单失败(可能还在黑屏或有新弹窗)，重置 30秒倒计时，继续观察...")
                        # 如果没进成功，重置时间戳，脚本会继续找画面2，或者再等30秒重试进菜单
                        last_continue_time = time.time()

            time.sleep(1.0)  # 每次总循环休息1秒，防止CPU占用过高

        self.app.log("自动启动超时(5分钟)，放弃抢救。")
        return False


    def handle_vramne_restart(self):
        self.app.log("!!! 检测到 VRAMNE.png，2秒后强杀游戏，等待10分钟再重启...")
        time.sleep(2.0)

        if not self.app.state.is_running:
            return False

        try:
            os.system("taskkill /F /IM forzahorizon6.exe /T")
            self.app.log("已强杀 forzahorizon6.exe")
        except Exception as e:
            self.app.log(f"强杀游戏失败: {e}")
            return False

        self.app.log("开始等待 10 分钟释放显存...")
        for _ in range(600):
            if hasattr(self.app, "check_pause"):
                self.app.services.runtime.check_pause()
            if not self.app.state.is_running:
                return False
            time.sleep(1)

        self.app.log("10分钟等待结束，准备自动重启游戏...")
        return self.restart_game_and_boot()


    def check_vramne_during_race(self):
        try:
            pos_vram = self.app.services.image_matcher.find_image_sift(
                "VRAMNE.png",
                region=self.app.services.game_window.regions["全界面"],
                min_inliers=24,
            )
            if pos_vram:
                return self.handle_vramne_restart()
            return None
        except Exception as e:
            self.app.log(f"检测到显存不足: {e}")
            return None


    def attempt_recovery(self):
        self.app.log("任务执行异常中断，准备执行断点恢复流程...")
        if not self.app.services.game_window.check_and_focus_game():
            # 游戏没开或者进程没了，直接走重启流程
            if not self.restart_game_and_boot():
                return False
        self.app.log("环境重置成功！即将从中断处继续剩余任务。")
        return True

    def enter_menu(self):
        self.app.log("正在尝试进入主菜单...")
        # 连续尝试 60 次，大概花费 40~60 秒
        for i in range(60):
            if not self.app.state.is_running:
                return False

            pos_menu = self.app.services.image_matcher.find_image_sift(
                "collectionjournal.png",
                region=self.app.services.game_window.regions["左"],
                min_inliers=20,
            )

            if pos_menu:
                self.app.log(f"成功定位到菜单锚点！({i + 1}/60)")
                time.sleep(0.5)
                return True

            self.app.log(f"未在主菜单... ({i + 1}/60)")
            self.app.services.input_actions.hw_press("menu")
            # 给游戏一点动画加载时间
            time.sleep(1.0)

        self.app.log("60 次尝试均未进入菜单，请检查游戏状态。")
        return False
