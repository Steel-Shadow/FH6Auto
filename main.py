import os
import sys
import argparse
import shutil
import socket
import subprocess
import threading
import time
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen

ROOT_DIR = os.path.dirname(os.path.abspath(__file__))
SRC_DIR = os.path.join(ROOT_DIR, "src")
if SRC_DIR not in sys.path:
    sys.path.insert(0, SRC_DIR)

from fh6auto.bootstrap import prepare_runtime


FRONTEND_DIR = Path(ROOT_DIR) / "frontend"


def find_frontend_dist() -> Path:
    candidates = [
        Path(getattr(sys, "_MEIPASS", "")) / "frontend" / "dist",
        Path(sys.executable).resolve().parent / "frontend" / "dist",
        Path(ROOT_DIR) / "frontend" / "dist",
    ]
    for candidate in candidates:
        if (candidate / "index.html").exists():
            return candidate
    return candidates[-1]


def wait_for_url(url: str, timeout: float = 20.0) -> None:
    deadline = time.time() + timeout
    last_error: Exception | None = None

    while time.time() < deadline:
        try:
            with urlopen(url, timeout=1.0) as response:
                if 200 <= response.status < 500:
                    return
        except (OSError, URLError) as e:
            last_error = e
        time.sleep(0.2)

    if last_error is not None:
        raise RuntimeError(f"等待服务启动超时: {url} ({last_error})") from last_error
    raise RuntimeError(f"等待服务启动超时: {url}")


def is_port_open(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=0.3):
            return True
    except OSError:
        return False


def start_backend(host: str, port: int):
    if is_port_open(host, port):
        wait_for_url(f"http://{host}:{port}/api/state", timeout=3.0)
        return None

    import uvicorn
    from fh6auto.backend.api import app

    prepare_runtime()

    config = uvicorn.Config(app, host=host, port=port, log_level="warning")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    wait_for_url(f"http://{host}:{port}/api/state")
    return server


def start_frontend_dev(host: str, port: int) -> subprocess.Popen | None:
    if is_port_open(host, port):
        wait_for_url(f"http://{host}:{port}/", timeout=3.0)
        return None

    npm = "npm.cmd" if os.name == "nt" else "npm"
    if shutil.which(npm) is None:
        raise RuntimeError("未找到 npm，且 frontend/dist 不存在，无法启动 Vue 前端。")
    if not FRONTEND_DIR.exists():
        raise RuntimeError(f"未找到前端目录: {FRONTEND_DIR}")

    creationflags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    process = subprocess.Popen(
        [npm, "run", "dev", "--", "--host", host, "--port", str(port), "--strictPort"],
        cwd=FRONTEND_DIR,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=creationflags,
    )
    wait_for_url(f"http://{host}:{port}/")
    return process


def resolve_frontend_url(
    mode: str, backend_host: str, backend_port: int, frontend_port: int
) -> tuple[str, subprocess.Popen | None]:
    if mode not in {"auto", "dist", "dev"}:
        raise ValueError(f"未知前端模式: {mode}")

    frontend_dist = find_frontend_dist()
    if mode in {"auto", "dist"} and (frontend_dist / "index.html").exists():
        return f"http://{backend_host}:{backend_port}/", None

    if mode == "dist":
        raise RuntimeError(f"未找到已构建前端: {frontend_dist}")

    frontend_process = start_frontend_dev("127.0.0.1", frontend_port)
    return f"http://127.0.0.1:{frontend_port}/", frontend_process


def show_window(url: str, width: int, height: int) -> None:
    import webview

    webview.create_window(
        title="FH6Auto",
        url=url,
        width=width,
        height=height,
        min_size=(1000, 680),
    )
    webview.start()


def main() -> None:
    parser = argparse.ArgumentParser(description="Start FH6Auto backend and desktop frontend window.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", default=8000, type=int)
    parser.add_argument("--frontend-port", default=5173, type=int)
    parser.add_argument("--frontend-mode", choices=["auto", "dist", "dev"], default="auto")
    parser.add_argument("--server-only", action="store_true", help="Only start the Python backend, without a window.")
    parser.add_argument("--window-width", default=1280, type=int)
    parser.add_argument("--window-height", default=820, type=int)
    args = parser.parse_args()

    backend_server = start_backend(args.host, args.port)
    frontend_process: subprocess.Popen | None = None

    try:
        if args.server_only:
            while True:
                time.sleep(3600)

        frontend_url, frontend_process = resolve_frontend_url(
            args.frontend_mode,
            args.host,
            args.port,
            args.frontend_port,
        )
        show_window(frontend_url, args.window_width, args.window_height)
    except KeyboardInterrupt:
        return
    finally:
        if frontend_process is not None:
            frontend_process.terminate()
        if backend_server is not None:
            backend_server.should_exit = True


if __name__ == "__main__":
    main()
