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
                pos_continue = self.app.services.image_waits.find_text_ui(
                    "继续",
                    threshold=0.65,
                    fast_mode=True,
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
        else:
            # TODO: 停止运行会直接在这里杀死游戏进程，回顾这里的问题
            pass
            # 进程还在，使用【高级状态机】尝试动态退回
            # if not self.advanced_enter_menu():
            #     self.app.log("高级动态退回失败(可能游戏卡死或致命报错)，准备强杀进程并重启...")
            #     try:
            #         os.system("taskkill /F /IM forzahorizon6.exe /T")
            #         time.sleep(4)
            #     except Exception:
            #         pass

            #     # 杀进程后重新拉起
            #     if not self.restart_game_and_boot():
            #         return False
        self.app.log("环境重置成功！即将从中断处继续剩余任务。")
        return True


    def wait_for_freeroam(self):
        self.app.log("验证漫游状态...")
        for i in range(100):
            if not self.app.state.is_running:
                return False

            if self.app.services.image_waits.find_text_ui(
                "安娜",
                region=self.app.services.game_window.regions["左下"],
                threshold=0.65,
                fast_mode=True,
            ):
                self.app.log("验证成功：已确认处于游戏漫游界面。")
                return True

            self.app.log(f"重试返回漫游界面({i + 1}/100)")
            self.app.services.input_actions.hw_press("esc")

            for _ in range(20):
                if not self.app.state.is_running:
                    return False
                time.sleep(0.1)

        self.app.log("多次尝试验证漫游界面失败，尝试进入菜单。")
        return True


    def recover_to_menu(self):
        self.app.log("开始尝试退回主菜单...")
        return self.enter_menu()


    def is_in_menu(self):
        return self.app.services.image_matcher.find_image_sift(
            "collectionjournal.png",
            region=self.app.services.game_window.regions["左"],
            min_inliers=20,
        )


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


    def advanced_enter_menu(self):
        """
        高级状态机退回：专门用于故障恢复。
        能够识别中途的特定弹窗、中间过渡画面，并执行点击，没找到目标才按 ESC。
        """
        self.app.log("正在使用【高级恢复模式】尝试退回主菜单...")

        # ==========================================
        # 动态读取 images/obstacles/ 里的所有图片
        # ==========================================
        obstacles_dir = os.path.join("images", "obstacles")
        dynamic_obstacles = []

        # 检查文件夹是否存在
        if os.path.exists(obstacles_dir):
            for file in os.listdir(obstacles_dir):
                # 只要是 png 或 jpg 格式的图片，统统加进来
                if file.lower().endswith((".png", ".jpg", ".jpeg")):
                    # 拼成 "obstacles/文件名.png"，这样 find_any_image_gray 就能正确找到路径
                    dynamic_obstacles.append(f"obstacles/{file}")

        if not dynamic_obstacles:
            self.app.log("提示：images/obstacles/ 文件夹为空或不存在，将只使用 ESC 退回。")
        # 连续尝试 80 次，处理较长的随机过程
        for i in range(80):
            if hasattr(self.app, "check_pause"):
                self.app.services.runtime.check_pause()  # 配合暂停功能
            if not self.app.state.is_running:
                return False

            # 1. 终极判断：是不是已经在菜单了？
            if self.is_in_menu():
                self.app.log(f"成功定位到菜单锚点！(尝试次数: {i + 1})")
                time.sleep(0.5)
                return True

            # 2. 致命错误排查 (检测到显存不足，强制休息 10 分钟)
            if self.app.services.image_waits.find_text_ui(
                "显存不足",
                region=self.app.services.game_window.regions["全界面"],
                threshold=0.65,
                fast_mode=True,
            ):
                self.app.log("!!! 严重警告: 检测到显存不足 (VRAMNE.png) 报错！")
                self.app.log("2秒后强杀游戏，随后冷却 10 分钟...")
                time.sleep(2.0)
                try:
                    os.system("taskkill /F /IM forzahorizon6.exe /T")
                    self.app.log("已强杀 forzahorizon6.exe")
                except Exception as e:
                    self.app.log(f"强杀游戏失败: {e}")
                    return False
                for _ in range(600):
                    if hasattr(self.app, "check_pause"):
                        self.app.services.runtime.check_pause()
                    if not self.app.state.is_running:
                        return False
                    time.sleep(1)
                self.app.log("10 分钟冷却完毕，交给外层执行重启流程。")
                return False

            # 3. 动态扫描所有可能的弹窗 / 需要点击的中间图片
            pos_obs = self.app.services.image_waits.find_any_image_gray(
                dynamic_obstacles,
                region=self.app.services.game_window.regions["全界面"],
                threshold=0.75,
                fast_mode=True,
            )
            if pos_obs:
                self.app.log(f"退回途中检测到已知图片/弹窗，点击推进... ({i + 1}/80)")
                self.app.services.input_actions.game_click(pos_obs)
                time.sleep(1.5)  # 给画面跳转留出动画时间
                continue  # 点击后，跳过本轮，不要按 ESC

            # 4. 如果既没进菜单，也没看到特定的图片，说明处于常规界面，按 ESC 退回
            self.app.log(f"未在主菜单且无已知特定图片，按下 ESC... ({i + 1}/80)")
            self.app.services.input_actions.hw_press("esc")
            time.sleep(1.2)  # 给游戏一点动画加载时间

        self.app.log("80 次动态尝试均未进入菜单，高级退回失败。")
        return False

