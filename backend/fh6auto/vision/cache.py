from __future__ import annotations

from dataclasses import dataclass
from collections.abc import Callable

import cv2
import numpy as np
import pyautogui
from PIL import ImageGrab

from ..automation.window import GameWindowService
from ..paths import get_img_path

Point = tuple[int, int]
Region = tuple[int, int, int, int]
Box = tuple[int, int, int, int]


@dataclass(frozen=True, slots=True)
class CaptureFrame:
    """截图帧。

    image 内部坐标从 (0, 0) 开始；origin 是该截图左上角的屏幕绝对坐标。
    识别算法只处理 image 的局部坐标，对外点击点统一通过这里转换成屏幕绝对坐标。
    """

    image: np.ndarray
    region: Region | None
    origin: Point

    @property
    def width(self) -> int:
        return int(self.image.shape[1]) if self.image is not None and self.image.ndim >= 2 else 0

    @property
    def height(self) -> int:
        return int(self.image.shape[0]) if self.image is not None and self.image.ndim >= 2 else 0

    def to_screen_point(self, point: tuple[float, float]) -> Point:
        return (
            int(round(float(point[0]) + self.origin[0])),
            int(round(float(point[1]) + self.origin[1])),
        )

    def box_center(self, box: Box) -> Point:
        x, y, w, h = box
        return self.to_screen_point((x + w / 2, y + h / 2))

    def to_screen_box(self, box: Box) -> Box:
        x, y, w, h = box
        return (int(x + self.origin[0]), int(y + self.origin[1]), int(w), int(h))


class ImageCacheService:
    def __init__(self, *, game_window: GameWindowService, log: Callable[..., None]) -> None:
        self.game_window = game_window
        self.log = log
        self.image_cache = {}

    # ==========================================
    # --- 图像寻找 ---
    # ==========================================
    def load_template(self, template_path):
        actual_path = get_img_path(template_path)
        cache_key = actual_path

        if cache_key in self.image_cache:
            return self.image_cache[cache_key], actual_path

        tpl = cv2.imread(actual_path, cv2.IMREAD_COLOR)
        if tpl is not None:
            self.image_cache[cache_key] = tpl
        return tpl, actual_path

    def resolve_capture_region(self, region=None):
        """返回实际截图区域；未指定时默认使用游戏窗口客户区。"""
        if region is not None:
            return region
        try:
            return self.game_window.regions.get("全界面")
        except Exception:
            return None

    def capture_offset(self, region=None):
        """返回截图区域左上角的屏幕绝对坐标偏移。"""
        capture_region = self.resolve_capture_region(region)
        if capture_region is None:
            return (0, 0)
        return (int(capture_region[0]), int(capture_region[1]))

    def capture_frame(self, region=None, mask_areas=None) -> CaptureFrame:
        """截取指定屏幕区域并返回带坐标原点的截图帧。"""
        capture_region = self.resolve_capture_region(region)
        screen = None
        try:
            if capture_region is not None:
                x, y, w, h = capture_region
                bbox = (int(x), int(y), int(x + w), int(y + h))
                screen = ImageGrab.grab(bbox=bbox, all_screens=True)
            else:
                screen = ImageGrab.grab(all_screens=True)
        except Exception:
            screen = pyautogui.screenshot(region=capture_region)

        try:
            screen_rgb = np.asarray(screen, dtype=np.uint8)
            if not screen_rgb.flags.c_contiguous:
                screen_rgb = np.ascontiguousarray(screen_rgb)
            if screen_rgb.ndim == 3 and screen_rgb.shape[2] == 4:
                screen_bgr = cv2.cvtColor(screen_rgb, cv2.COLOR_RGBA2BGR)
            else:
                screen_bgr = cv2.cvtColor(screen_rgb, cv2.COLOR_RGB2BGR)
        finally:
            close = getattr(screen, "close", None)
            if callable(close):
                close()
        # 对指定区域打黑块，避免重复识别同一个目标
        if mask_areas:
            for rect in mask_areas:
                try:
                    mx1, my1, mx2, my2 = rect
                    mx1 = max(0, int(mx1))
                    my1 = max(0, int(my1))
                    mx2 = min(screen_bgr.shape[1], int(mx2))
                    my2 = min(screen_bgr.shape[0], int(my2))
                    if mx2 > mx1 and my2 > my1:
                        screen_bgr[my1:my2, mx1:mx2] = 0
                except Exception:
                    pass

        return CaptureFrame(
            image=screen_bgr,
            region=tuple(map(int, capture_region)) if capture_region is not None else None,
            origin=self.capture_offset(capture_region),
        )

    def capture_region(self, region=None, mask_areas=None):
        """截取指定屏幕区域；未指定时默认截取游戏窗口客户区。"""
        return self.capture_frame(region, mask_areas=mask_areas).image

    def _to_gray_any(self, img):
        if img is None:
            return None
        if img.ndim == 2:
            return img
        if img.shape[2] == 4:
            return cv2.cvtColor(img[:, :, :3], cv2.COLOR_BGR2GRAY)
        return cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)

    def to_text_ui_image(self, img):
        """把不同底色/反色文字统一成边缘形状图。"""
        gray = self._to_gray_any(img)
        if gray is None:
            return None

        clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
        norm = clahe.apply(gray)
        blur = cv2.GaussianBlur(norm, (3, 3), 0)
        sobel_x = cv2.Sobel(blur, cv2.CV_32F, 1, 0, ksize=3)
        sobel_y = cv2.Sobel(blur, cv2.CV_32F, 0, 1, ksize=3)
        gradient = cv2.magnitude(sobel_x, sobel_y)
        gradient_u8 = np.empty_like(gradient, dtype=np.uint8)
        cv2.normalize(
            src=gradient,
            dst=gradient_u8,
            alpha=0,
            beta=255,
            norm_type=cv2.NORM_MINMAX,
            dtype=cv2.CV_8U,
        )
        gradient = gradient_u8
        _, gradient_mask = cv2.threshold(gradient, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        canny = cv2.Canny(blur, 40, 120)
        edges = cv2.bitwise_or(gradient_mask, canny)
        edges = cv2.dilate(edges, np.ones((2, 2), np.uint8), iterations=1)

        if img.ndim == 3 and img.shape[2] == 4:
            alpha = img[:, :, 3]
            if alpha.shape != edges.shape:
                alpha = cv2.resize(alpha, (edges.shape[1], edges.shape[0]), interpolation=cv2.INTER_AREA)
            edges[alpha < 16] = 0

        return edges

