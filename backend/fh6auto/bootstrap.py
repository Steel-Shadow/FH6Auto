import ctypes
import os
import sys


def configure_environment() -> None:
    """Apply process-level environment fixes before heavy native imports."""
    os.environ["KMP_DUPLICATE_LIB_OK"] = "TRUE"


def check_windows_dependencies() -> None:
    if sys.platform != "win32":
        return

    missing_dlls = []
    required_dlls = ["vcruntime140.dll", "msvcp140.dll", "vcruntime140_1.dll"]

    for dll in required_dlls:
        try:
            ctypes.WinDLL(dll)
        except OSError:
            missing_dlls.append(dll)

    if missing_dlls:
        msg = (
            f"警告：系统缺失以下关键运行库，大概率会导致程序闪退或图像识别失败：\n\n"
            f"{', '.join(missing_dlls)}\n\n"
            f"这是因为您的电脑缺少微软 C++ 运行环境。\n"
            f"请搜索下载【微软常用运行库合集】或【VC++ 2015-2022】安装后重试。\n\n"
            f"点击“确定”强行继续运行（如果闪退请安装运行库）。"
        )
        ctypes.windll.user32.MessageBoxW(0, msg, "缺少运行库拦截提示", 0x30 | 0x0)


def configure_dpi_awareness() -> None:
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass


def prepare_runtime() -> None:
    configure_environment()
    check_windows_dependencies()
    configure_dpi_awareness()

