from __future__ import annotations

import json
import os
import pickle
from typing import TYPE_CHECKING

import cv2
import numpy as np
import pyautogui
from PIL import ImageGrab

from ..paths import (
    APP_DIR,
    CACHE_DIR,
    INTERNAL_DIR,
    TEMPLATE_CACHE_FILE,
    TEMPLATE_META_FILE,
    get_img_path,
)

if TYPE_CHECKING:
    from ..backend.app import BackendApp


class ImageCacheService:
    def __init__(self, app: BackendApp) -> None:
        self.app = app
        self.template_cache = {}
        self.scaled_template_cache = {}
        self.file_template_cache = {}
        self.text_ui_template_cache = {}


    # ==========================================
    # --- 图像寻找 ---
    # ==========================================
    def load_template(self, template_path):
        actual_path = get_img_path(template_path)
        cache_key = actual_path

        if cache_key in self.template_cache:
            return self.template_cache[cache_key], actual_path

        tpl = cv2.imread(actual_path, cv2.IMREAD_COLOR)
        if tpl is not None:
            self.template_cache[cache_key] = tpl
        return tpl, actual_path



    def get_images_root_dir(self):
        ext_dir = os.path.join(APP_DIR, "images")
        if os.path.isdir(ext_dir):
            return ext_dir

        int_dir = os.path.join(INTERNAL_DIR, "images")
        if os.path.isdir(int_dir):
            return int_dir

        return None


    def get_template_meta(self):
        images_dir = self.get_images_root_dir()
        meta_data = {}
        if not images_dir:
            return meta_data

        for root, _, files in os.walk(images_dir):
            for file in files:
                if not file.lower().endswith((".png", ".jpg", ".jpeg", ".bmp")):
                    continue

                path = os.path.join(root, file)
                rel_path = os.path.relpath(path, images_dir).replace("\\", "/")

                try:
                    stat = os.stat(path)
                    meta_data[rel_path] = {
                        "mtime": stat.st_mtime,
                        "size": stat.st_size,
                    }
                except Exception:
                    pass

        return meta_data


    def is_template_cache_valid(self):
        if not os.path.exists(TEMPLATE_CACHE_FILE) or not os.path.exists(TEMPLATE_META_FILE):
            return False

        try:
            with open(TEMPLATE_META_FILE, "r", encoding="utf-8") as f:
                cached_meta = json.load(f)
        except Exception:
            return False

        new_meta = self.get_template_meta()
        return cached_meta == new_meta


    def build_template_file_cache(self):
        self.app.log("开始构建模板缓存文件...")
        os.makedirs(CACHE_DIR, exist_ok=True)

        images_dir = self.get_images_root_dir()
        if not images_dir:
            self.app.log("未找到 images 目录，无法构建模板缓存。")
            return False

        cache_data = {}
        meta_data = self.get_template_meta()

        scales = self.get_scales_to_try(fast_mode=False)

        for rel_path in meta_data.keys():
            img_path = os.path.join(images_dir, rel_path)
            tpl = cv2.imread(img_path, cv2.IMREAD_COLOR)
            if tpl is None:
                continue

            cache_data[rel_path] = {}
            for scale in scales:
                try:
                    if scale == 1.0:
                        scaled = tpl.copy()
                    else:
                        scaled = cv2.resize(tpl, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)

                    cache_data[rel_path][str(round(scale, 3))] = scaled
                except Exception:
                    continue

        try:
            with open(TEMPLATE_CACHE_FILE, "wb") as f:
                pickle.dump(cache_data, f, protocol=pickle.HIGHEST_PROTOCOL)

            with open(TEMPLATE_META_FILE, "w", encoding="utf-8") as f:
                json.dump(meta_data, f, ensure_ascii=False, indent=2)

            self.app.log("模板缓存文件构建完成。")
            return True
        except Exception as e:
            self.app.log(f"写入模板缓存失败: {e}")
            return False


    def load_template_file_cache(self):
        try:
            with open(TEMPLATE_CACHE_FILE, "rb") as f:
                self.file_template_cache = pickle.load(f)
            self.app.log("模板缓存文件加载成功。")
            return True
        except Exception as e:
            self.app.log(f"加载模板缓存失败: {e}")
            self.file_template_cache = {}
            return False


    def prepare_template_cache(self):
        os.makedirs(CACHE_DIR, exist_ok=True)

        if self.is_template_cache_valid():
            if self.load_template_file_cache():
                return

        self.app.log("模板缓存不存在或已失效，开始后台重建（这可能需要几秒钟）...")
        if self.build_template_file_cache():
            self.template_cache.clear()
            self.scaled_template_cache.clear()
            self.load_template_file_cache()


    def capture_region(self, region=None, mask_areas=None):
        try:
            if region:
                x, y, w, h = region
                bbox = (int(x), int(y), int(x + w), int(y + h))
                screen = ImageGrab.grab(bbox=bbox, all_screens=True)
            else:
                screen = ImageGrab.grab(all_screens=True)
        except Exception:
            screen = pyautogui.screenshot(region=region)

        screen_bgr = cv2.cvtColor(np.array(screen), cv2.COLOR_RGB2BGR)

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

        return screen_bgr


    def get_scales_to_try(self, fast_mode=True):
        full_region = self.app.services.game_window.regions.get("全界面")
        curr_w = full_region[2] if full_region else pyautogui.size()[0]
        # 你的图主要是按 2560 截的，就优先围绕 2560 计算
        primary_base = 2560
        primary_scale = curr_w / primary_base
        scales = []

        def add_scale(s):
            s = round(float(s), 3)
            if 0.35 <= s <= 1.8 and s not in scales:
                scales.append(s)

        # 先加“最可能正确”的比例及其微调
        add_scale(primary_scale)
        add_scale(primary_scale * 0.98)
        add_scale(primary_scale * 1.02)
        add_scale(primary_scale * 0.95)
        add_scale(primary_scale * 1.05)
        add_scale(primary_scale * 0.92)
        add_scale(primary_scale * 1.08)
        # 再覆盖其它常见基准
        for bw in [1920, 1600]:
            s = curr_w / bw
            add_scale(s)
            add_scale(s * 0.98)
            add_scale(s * 1.02)
        # 最后兜底常用比例
        for s in [1.0, 0.95, 1.05, 0.9, 1.1, 0.85, 1.15, 0.8, 0.75, 0.7]:
            add_scale(s)
        if fast_mode:
            return scales[:8]
        return scales


    def get_scaled_template(self, template_path, scale):
        actual_path = get_img_path(template_path)
        images_dir = self.get_images_root_dir()

        if images_dir and os.path.exists(actual_path):
            try:
                rel_key = os.path.relpath(actual_path, images_dir).replace("\\", "/")
            except Exception:
                rel_key = os.path.basename(actual_path)
        else:
            rel_key = os.path.basename(actual_path)

        mem_key = (actual_path, round(scale, 3))
        if mem_key in self.scaled_template_cache:
            return self.scaled_template_cache[mem_key], actual_path

        scale_key = str(round(scale, 3))
        if rel_key in self.file_template_cache:
            tpl = self.file_template_cache[rel_key].get(scale_key)
            if tpl is not None:
                self.scaled_template_cache[mem_key] = tpl
                return tpl, actual_path

        template_orig, actual_path = self.load_template(template_path)
        if template_orig is None:
            return None, actual_path

        try:
            if scale == 1.0:
                tpl = template_orig.copy()
            else:
                tpl = cv2.resize(
                    template_orig,
                    None,
                    fx=scale,
                    fy=scale,
                    interpolation=cv2.INTER_AREA,
                )

            self.scaled_template_cache[mem_key] = tpl
            return tpl, actual_path
        except Exception:
            return None, actual_path


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
        gradient = cv2.normalize(gradient, None, 0, 255, cv2.NORM_MINMAX).astype(np.uint8)
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


    @staticmethod
    def _content_bbox(mask, pad=2):
        ys, xs = np.where(mask > 0)
        if len(xs) == 0:
            return None
        h, w = mask.shape[:2]
        return (
            max(0, int(xs.min()) - pad),
            max(0, int(ys.min()) - pad),
            min(w, int(xs.max()) + pad + 1),
            min(h, int(ys.max()) + pad + 1),
        )


    def prepare_text_ui_template(self, template_path, scale):
        actual_path = get_img_path(template_path)
        cache_key = ("text-ui", actual_path, round(float(scale), 3))
        if cache_key in self.text_ui_template_cache:
            return self.text_ui_template_cache[cache_key], actual_path

        tpl = cv2.imread(actual_path, cv2.IMREAD_UNCHANGED)
        if tpl is None:
            return None, actual_path

        try:
            if scale == 1.0:
                tpl_scaled = tpl.copy()
            else:
                tpl_scaled = cv2.resize(tpl, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)

            text_map = self.to_text_ui_image(tpl_scaled)
            if text_map is None:
                return None, actual_path

            content_mask = text_map
            if np.count_nonzero(content_mask) < 8 and tpl_scaled.ndim == 3 and tpl_scaled.shape[2] == 4:
                content_mask = tpl_scaled[:, :, 3]

            bbox = self._content_bbox(content_mask)
            if bbox is None:
                return None, actual_path

            x1, y1, x2, y2 = bbox
            text_crop = text_map[y1:y2, x1:x2]
            if text_crop.shape[0] < 5 or text_crop.shape[1] < 5 or np.count_nonzero(text_crop) < 8:
                return None, actual_path

            meta = {
                "text": text_crop,
                "bbox": bbox,
                "size": tpl_scaled.shape[:2],
            }
            self.text_ui_template_cache[cache_key] = meta
            return meta, actual_path
        except Exception:
            return None, actual_path


    @staticmethod
    def _text_shape_score(patch, template):
        patch_bin = patch > 0
        tpl_bin = template > 0
        patch_count = int(patch_bin.sum())
        tpl_count = int(tpl_bin.sum())
        if patch_count < 8 or tpl_count < 8:
            return 0.0, 0.0, 0.0

        kernel = np.ones((3, 3), np.uint8)
        patch_dilated = cv2.dilate(patch_bin.astype(np.uint8), kernel, iterations=1).astype(bool)
        tpl_dilated = cv2.dilate(tpl_bin.astype(np.uint8), kernel, iterations=1).astype(bool)

        recall = float((tpl_bin & patch_dilated).sum()) / tpl_count
        precision = float((patch_bin & tpl_dilated).sum()) / patch_count
        if precision + recall <= 0:
            return 0.0, precision, recall

        return (2 * precision * recall) / (precision + recall), precision, recall


    def score_text_ui_maps(self, search_text, template_text, top_k=24):
        try:
            th, tw = template_text.shape[:2]
            sh, sw = search_text.shape[:2]
            if th < 5 or tw < 5 or th > sh or tw > sw:
                return None

            corr_res = cv2.matchTemplate(search_text, template_text, cv2.TM_CCORR_NORMED)
            flat = corr_res.ravel()
            if flat.size == 0:
                return None

            pick_count = min(int(top_k), flat.size)
            idxs = np.argpartition(flat, -pick_count)[-pick_count:]
            checked = set()
            best = None

            for idx in idxs:
                y, x = np.unravel_index(idx, corr_res.shape)
                key = (int(x) // 3, int(y) // 3)
                if key in checked:
                    continue
                checked.add(key)

                corr_score = float(corr_res[y, x])
                patch = search_text[y : y + th, x : x + tw]
                if patch.shape[:2] != template_text.shape[:2]:
                    continue

                coeff_score = cv2.matchTemplate(patch, template_text, cv2.TM_CCOEFF_NORMED)[0, 0]
                coeff_score = float(coeff_score) if np.isfinite(coeff_score) else 0.0
                shape_score, precision, recall = self._text_shape_score(patch, template_text)
                final_score = shape_score * 0.50 + max(0.0, coeff_score) * 0.35 + corr_score * 0.15

                if best is None or final_score > best["score"]:
                    best = {
                        "score": final_score,
                        "corr": corr_score,
                        "coeff": coeff_score,
                        "shape": shape_score,
                        "precision": precision,
                        "recall": recall,
                        "loc": (int(x), int(y)),
                    }

            return best
        except Exception:
            return None


    def match_text_ui_score(self, src_bgr, template_path, scale):
        try:
            src_text = self.to_text_ui_image(src_bgr)
            meta, _ = self.prepare_text_ui_template(template_path, scale)
            if src_text is None or meta is None:
                return 0.0

            result = self.score_text_ui_maps(src_text, meta["text"])
            return float(result["score"]) if result else 0.0
        except Exception:
            return 0.0


    def to_gray_image(self, img):
        return cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)


    def to_edge_image(self, img):
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        blur = cv2.GaussianBlur(gray, (3, 3), 0)
        edge = cv2.Canny(blur, 50, 150)
        return edge


    def match_template_score(self, src, tpl):
        try:
            if tpl is None or src is None:
                return 0.0
            th, tw = tpl.shape[:2]
            sh, sw = src.shape[:2]
            if th < 5 or tw < 5 or th > sh or tw > sw:
                return 0.0
            res = cv2.matchTemplate(src, tpl, cv2.TM_CCOEFF_NORMED)
            return cv2.minMaxLoc(res)[1]
        except Exception:
            return 0.0
