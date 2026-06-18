import os
import shutil
import sys
from pathlib import Path


def get_app_dir() -> str:
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)

    return str(Path(__file__).resolve().parents[2])


def get_internal_dir() -> str:
    meipass_dir = getattr(sys, "_MEIPASS", None)
    if meipass_dir:
        return meipass_dir
    return get_app_dir()


APP_DIR = get_app_dir()
INTERNAL_DIR = get_internal_dir()
USER_CONFIG_FILE = os.path.join(APP_DIR, "config.json")
LOG_FILE = os.path.join(APP_DIR, "bot_log.txt")
CACHE_DIR = os.path.join(APP_DIR, "cache")
TEMPLATE_CACHE_FILE = os.path.join(CACHE_DIR, "template_cache.pkl")
TEMPLATE_META_FILE = os.path.join(CACHE_DIR, "template_meta.json")


def auto_extract_images(folder_name: str = "images") -> None:
    internal_dir = os.path.join(INTERNAL_DIR, folder_name)
    external_dir = os.path.join(APP_DIR, folder_name)

    if not os.path.isdir(internal_dir):
        print(f"[auto_extract_images] 内置目录不存在: {internal_dir}")
        return

    try:
        os.makedirs(external_dir, exist_ok=True)

        for root, _, files in os.walk(internal_dir):
            rel_path = os.path.relpath(root, internal_dir)
            target_root = external_dir if rel_path == "." else os.path.join(external_dir, rel_path)
            os.makedirs(target_root, exist_ok=True)

            for file in files:
                src_file = os.path.join(root, file)
                dst_file = os.path.join(target_root, file)

                if not os.path.exists(dst_file):
                    shutil.copy2(src_file, dst_file)

    except Exception as e:
        print(f"[auto_extract_images] 释放 images 失败: {e}")


def get_img_path(filename: str) -> str:
    basename = os.path.basename(filename)

    ext_path = os.path.join(APP_DIR, "images", basename)
    if os.path.exists(ext_path):
        return ext_path

    int_path = os.path.join(INTERNAL_DIR, "images", basename)
    if os.path.exists(int_path):
        return int_path

    return filename


def get_asset_path(*parts: str) -> str | None:
    asset_path = os.path.join(INTERNAL_DIR, "assets", *parts)
    if os.path.exists(asset_path):
        return asset_path

    dev_asset_path = os.path.join(get_app_dir(), "assets", *parts)
    if os.path.exists(dev_asset_path):
        return dev_asset_path

    return None
