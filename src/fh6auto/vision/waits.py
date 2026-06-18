from __future__ import annotations

import time
from typing import TYPE_CHECKING

import cv2
import numpy as np

if TYPE_CHECKING:
    from ..backend.app import BackendApp


class ImageWaitsService:
    def __init__(self, app: BackendApp) -> None:
        self.app = app
        self._text_targets_cache = {}

    def wait_for_car_card(
        self,
        card_path,
        required_tag_path=None,
        excluded_tag_path=None,
        required_tag_text=None,
        excluded_tag_text=None,
        exclude_driving=False,
        region=None,
        timeout: float = 30,
        interval: float = 0.4,
        fast_mode=True,
        candidate_threshold=0.50,
        final_threshold=0.78,
        title_threshold=0.72,
        pi_threshold=0.82,
        rarity_threshold=0.68,
        body_threshold=0.55,
        tag_threshold=0.70,
        exclude_tag_threshold=0.65,
        max_candidates=80,
        mask_areas=None,
        template_fallback=False,
    ):
        start = time.time()

        while self.app.state.is_running and time.time() - start < timeout:
            pos = self.app.services.image_matcher.find_car_card(
                card_path,
                required_tag_path=required_tag_path,
                excluded_tag_path=excluded_tag_path,
                required_tag_text=required_tag_text,
                excluded_tag_text=excluded_tag_text,
                exclude_driving=exclude_driving,
                region=region,
                fast_mode=fast_mode,
                candidate_threshold=candidate_threshold,
                final_threshold=final_threshold,
                title_threshold=title_threshold,
                pi_threshold=pi_threshold,
                rarity_threshold=rarity_threshold,
                body_threshold=body_threshold,
                tag_threshold=tag_threshold,
                exclude_tag_threshold=exclude_tag_threshold,
                max_candidates=max_candidates,
                mask_areas=mask_areas,
                template_fallback=template_fallback,
            )
            if pos:
                return pos

            sleep_end = time.time() + interval
            while self.app.state.is_running and time.time() < sleep_end:
                time.sleep(0.05)

        return None

    def wait_for_image_sift(
        self,
        reference_path,
        region=None,
        min_inliers=50,
        ratio=0.75,
        max_features=2500,
        timeout: float = 30,
        interval: float = 0.4,
    ):
        start = time.time()

        while self.app.state.is_running and time.time() - start < timeout:
            pos = self.app.services.image_matcher.find_image_sift(
                reference_path,
                region=region,
                min_inliers=min_inliers,
                ratio=ratio,
                max_features=max_features,
            )
            if pos:
                return pos

            sleep_end = time.time() + interval
            while self.app.state.is_running and time.time() < sleep_end:
                time.sleep(0.05)

        return None

    def find_any_image_gray(self, image_list, region=None, threshold=0.75, fast_mode=True, invert_mode=False):
        """
        纯灰度多图查找，支持多分辨率缩放 + 可选翻转模式
        参数:
            image_list (list): 模板图片路径列表，如 ["a.png", "b.png", "c.png"]
            region (tuple|list|None): 搜索区域，格式通常为 (x, y, w, h)，None 表示全屏/默认区域
            threshold (float): 匹配阈值，范围通常 0~1，越高越严格
            fast_mode (bool): 是否使用快速缩放搜索模式，True=较少缩放比，False=更多缩放比
            invert_mode (bool): 是否启用翻转模式，True 时会同时匹配原图和反相图（白底黑字 / 黑底白字都能识别）
        返回:
            tuple|None:
                - 找到任意一张时返回匹配中心点坐标 (x, y)
                - 都找不到返回 None
        """
        if not self.app.state.is_running:
            return None
        try:
            screen_bgr = self.app.services.image_cache.capture_region(region)
            screen_gray = cv2.cvtColor(screen_bgr, cv2.COLOR_BGR2GRAY)
            scales_to_try = self.app.services.image_cache.get_scales_to_try(fast_mode=fast_mode)

            for img_path in image_list:
                # 【新增】模板只读取一次
                tpl_gray_raw = self.app.services.image_cache.load_template_gray(img_path)
                if tpl_gray_raw is None:
                    continue

                for scale in scales_to_try:
                    # 【改动】从原始模板复制
                    tpl_gray = tpl_gray_raw
                    if scale != 1.0:
                        tpl_gray = cv2.resize(
                            tpl_gray,
                            None,
                            fx=scale,
                            fy=scale,
                            interpolation=cv2.INTER_AREA,
                        )

                    h, w = tpl_gray.shape[:2]
                    if h < 5 or w < 5 or h > screen_gray.shape[0] or w > screen_gray.shape[1]:
                        continue

                    # ==============================
                    # 原图匹配
                    # ==============================
                    res = cv2.matchTemplate(screen_gray, tpl_gray, cv2.TM_CCOEFF_NORMED)
                    _, max_val, _, max_loc = cv2.minMaxLoc(res)
                    if max_val >= threshold:
                        self.app.log(
                            f"[GrayMatchAny] 命中: {img_path} | 模式: 原图 | 灰度得分: {max_val:.3f} (阈值 {threshold}) | 缩放比: {scale:.3f}"
                        )
                        return (
                            max_loc[0] + w // 2 + (region[0] if region else 0),
                            max_loc[1] + h // 2 + (region[1] if region else 0),
                        )

                    # ==============================
                    # 【新增】翻转模式：反相模板匹配
                    # ==============================
                    if invert_mode:
                        tpl_inv = 255 - tpl_gray
                        res_inv = cv2.matchTemplate(screen_gray, tpl_inv, cv2.TM_CCOEFF_NORMED)
                        _, max_val_inv, _, max_loc_inv = cv2.minMaxLoc(res_inv)
                        if max_val_inv >= threshold:
                            self.app.log(
                                f"[GrayMatchAny] 命中: {img_path} | 模式: 反相 | 灰度得分: {max_val_inv:.3f} (阈值 {threshold}) | 缩放比: {scale:.3f}"
                            )
                            return (
                                max_loc_inv[0] + w // 2 + (region[0] if region else 0),
                                max_loc_inv[1] + h // 2 + (region[1] if region else 0),
                            )

            return None
        except Exception as e:
            self.app.log(f"find_any_image_gray 异常: {e}")
            return None


    @staticmethod
    def _merge_overlapping_boxes(boxes, tolerance=8):
        merged = []
        for box in sorted(boxes, key=lambda item: (item[1], item[0], -item[2] * item[3])):
            x, y, w, h = box
            x2 = x + w
            y2 = y + h
            duplicate = False
            for idx, (mx, my, mw, mh) in enumerate(merged):
                mx2 = mx + mw
                my2 = my + mh
                ix1 = max(x, mx)
                iy1 = max(y, my)
                ix2 = min(x2, mx2)
                iy2 = min(y2, my2)
                if ix2 <= ix1 or iy2 <= iy1:
                    continue
                intersection = (ix2 - ix1) * (iy2 - iy1)
                area = min(w * h, mw * mh)
                if intersection >= max(1, area) * 0.65:
                    nx1 = min(x, mx)
                    ny1 = min(y, my)
                    nx2 = max(x2, mx2)
                    ny2 = max(y2, my2)
                    merged[idx] = (nx1, ny1, nx2 - nx1, ny2 - ny1)
                    duplicate = True
                    break
            if not duplicate:
                merged.append((x, y, w, h))

        return [
            (x, y, w, h)
            for x, y, w, h in merged
            if w > tolerance and h > tolerance
        ]

    def _find_text_line_candidate_boxes(self, screen_bgr, max_candidates=60):
        """用文字边缘图聚合候选文字行，供 OCR 单行识别。"""
        text_map = self.app.services.image_cache.to_text_ui_image(screen_bgr)
        if text_map is None:
            return []

        screen_h, screen_w = text_map.shape[:2]
        component_count, labels, stats, _ = cv2.connectedComponentsWithStats(text_map, 8)
        clean = np.zeros_like(text_map)
        screen_area = screen_w * screen_h
        for idx in range(1, component_count):
            x, y, w, h, area = stats[idx]
            if area < 5 or area > screen_area * 0.01:
                continue
            if w < 2 or h < 4:
                continue
            if w > screen_w * 0.35 or h > screen_h * 0.18:
                continue
            density = area / max(1, w * h)
            if density > 0.75:
                continue
            clean[labels == idx] = 255

        if np.count_nonzero(clean) < 8:
            return []

        horizontal = max(10, int(round(screen_w * 0.012)))
        vertical = max(3, int(round(screen_h * 0.004)))
        kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (horizontal, vertical))
        grouped = cv2.dilate(clean, kernel, iterations=1)
        grouped = cv2.morphologyEx(grouped, cv2.MORPH_CLOSE, kernel)

        contours, _ = cv2.findContours(grouped, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        boxes = []
        for contour in contours:
            x, y, w, h = cv2.boundingRect(contour)
            if w < max(24, screen_w * 0.015) or h < max(10, screen_h * 0.010):
                continue
            if w > screen_w * 0.85 or h > screen_h * 0.24:
                continue
            if w / max(1, h) < 1.1:
                continue

            area = cv2.contourArea(contour)
            if area < max(20, w * h * 0.03):
                continue

            pad_x = max(8, int(round(w * 0.10)))
            pad_y = max(6, int(round(h * 0.55)))
            x1 = max(0, x - pad_x)
            y1 = max(0, y - pad_y)
            x2 = min(screen_w, x + w + pad_x)
            y2 = min(screen_h, y + h + pad_y)
            boxes.append((x1, y1, x2 - x1, y2 - y1))

        boxes = self._merge_overlapping_boxes(boxes)
        boxes.sort(key=lambda item: (item[1], item[0], item[2] * item[3]))
        return boxes[: int(max_candidates)]

    def _build_text_targets(self, text_list):
        if isinstance(text_list, str):
            text_list = [text_list]

        cache_key = tuple(str(text) for text in text_list)
        if cache_key in self._text_targets_cache:
            return self._text_targets_cache[cache_key]

        targets = []
        for target_text in cache_key:
            target_norm = self.app.services.ocr.normalize_text(target_text)
            if len(target_norm) < 2 or "?" in target_norm:
                self.app.log(f"[TextOCR] 跳过目标文字 {target_text}：内容不可用。")
                continue
            targets.append((target_text, target_norm))
        self._text_targets_cache[cache_key] = targets
        return targets

    def _score_text_ocr_match(
        self,
        candidate_text,
        candidate_score,
        target_norm,
        threshold,
        *,
        allow_candidate_subset=True,
        coverage_floor=0.65,
    ):
        candidate_norm = self.app.services.ocr.normalize_text(candidate_text)
        if len(candidate_norm) < 2:
            return None

        exact = candidate_norm == target_norm
        partial = len(target_norm) >= 3 and (
            target_norm in candidate_norm or (allow_candidate_subset and candidate_norm in target_norm)
        )
        if not exact and not partial:
            return None

        if candidate_score < max(0.35, threshold - 0.20):
            return None

        coverage = min(len(candidate_norm), len(target_norm)) / max(len(candidate_norm), len(target_norm))
        score = float(candidate_score) * (1.0 if exact else 0.88) * max(coverage_floor, coverage)
        return score if score >= threshold else None

    def _find_any_text_ocr(self, text_list, region=None, threshold=0.65):
        """通过候选文字框 + 单行 OCR 定位文字 UI。"""
        try:
            targets = self._build_text_targets(text_list)
            if not targets:
                return None

            screen_bgr = self.app.services.image_cache.capture_region(region)
            boxes = self._find_text_line_candidate_boxes(screen_bgr)
            if not boxes:
                screen_h, screen_w = screen_bgr.shape[:2]
                if screen_w <= 900 and screen_h <= 420:
                    boxes = [(0, 0, screen_w, screen_h)]
            best = None
            for x, y, w, h in boxes:
                roi = screen_bgr[y : y + h, x : x + w]
                if roi.size == 0:
                    continue
                line_result = self.app.services.ocr.recognize_line(roi, min_score=0.35)
                results = [line_result] if line_result is not None else self.app.services.ocr.read(roi, text_score=0.35)
                for target_text, target_norm in targets:
                    for result in results:
                        score = self._score_text_ocr_match(result.text, result.score, target_norm, threshold)
                        if score is None:
                            continue
                        pos = (
                            int(round(x + w / 2 + (region[0] if region else 0))),
                            int(round(y + h / 2 + (region[1] if region else 0))),
                        )
                        if best is None or score > best["score"]:
                            best = {
                                "target": target_text,
                                "text": result.text,
                                "score": score,
                                "pos": pos,
                                "box": (x, y, w, h),
                            }

            if best and best["score"] >= threshold:
                self.app.services.image_matcher.last_positions[best["target"]] = best["pos"]
                x, y, w, h = best["box"]
                self.app.log(
                    f"[TextOCR] 命中: {best['text']} "
                    f"(目标:{best['target']}) | 分数:{best['score']:.3f} "
                    f"(阈值 {threshold}) | 候选框: x={x}, y={y}, w={w}, h={h}"
                )
                return best["pos"]

            return None
        except Exception as e:
            self.app.log(f"_find_any_text_ocr 异常: {e}")
            return None

    def _find_menu_button_candidate_boxes(self, screen_bgr, max_candidates=16):
        """用按钮背景/选中边框定位左侧菜单行，再交给 OCR 识别文字。"""
        if screen_bgr is None or screen_bgr.size == 0:
            return []

        screen_h, screen_w = screen_bgr.shape[:2]
        hsv = cv2.cvtColor(screen_bgr, cv2.COLOR_BGR2HSV)

        white_mask = cv2.inRange(
            hsv,
            np.array([0, 0, 150], dtype=np.uint8),
            np.array([180, 90, 255], dtype=np.uint8),
        )
        selected_border_mask = cv2.inRange(
            hsv,
            np.array([35, 80, 120], dtype=np.uint8),
            np.array([90, 255, 255], dtype=np.uint8),
        )

        masks = [white_mask, selected_border_mask]
        boxes = []
        for mask in masks:
            kernel = cv2.getStructuringElement(
                cv2.MORPH_RECT,
                (max(3, screen_w // 160), max(3, screen_h // 160)),
            )
            mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)
            contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

            for contour in contours:
                x, y, w, h = cv2.boundingRect(contour)
                if w < max(80, int(screen_w * 0.14)):
                    continue
                if h < max(18, int(screen_h * 0.045)) or h > max(60, int(screen_h * 0.18)):
                    continue
                if w > int(screen_w * 0.70) or x > int(screen_w * 0.58):
                    continue
                if y > int(screen_h * 0.86):
                    continue
                if w / max(1, h) < 3.0:
                    continue

                area_ratio = cv2.contourArea(contour) / max(1, w * h)
                if area_ratio < 0.25:
                    continue

                pad_x = max(4, int(round(w * 0.02)))
                pad_y = max(3, int(round(h * 0.12)))
                x1 = max(0, x - pad_x)
                y1 = max(0, y - pad_y)
                x2 = min(screen_w, x + w + pad_x)
                y2 = min(screen_h, y + h + pad_y)
                boxes.append((x1, y1, x2 - x1, y2 - y1))

        boxes = self._merge_overlapping_boxes(boxes)
        boxes.sort(key=lambda item: (item[1], item[0], item[2] * item[3]))
        return boxes[: int(max_candidates)]

    def _find_any_menu_text_ocr(self, text_list, region=None, threshold=0.65):
        """OCR 识别规则菜单行，适用于左侧竖向按钮列表。"""
        try:
            targets = self._build_text_targets(text_list)
            if not targets:
                return None

            screen_bgr = self.app.services.image_cache.capture_region(region)
            boxes = self._find_menu_button_candidate_boxes(screen_bgr)
            best = None

            def consider_result(result, box, *, require_ocr_box=False):
                nonlocal best
                matched = False
                x, y, w, h = box
                for target_text, target_norm in targets:
                    score = self._score_text_ocr_match(result.text, result.score, target_norm, threshold)
                    if score is None:
                        continue

                    offset_x = x + (region[0] if region else 0)
                    offset_y = y + (region[1] if region else 0)
                    ocr_box = None
                    if result.box:
                        xs = [float(point[0]) + offset_x for point in result.box]
                        ys = [float(point[1]) + offset_y for point in result.box]
                        ocr_box = (
                            int(round(min(xs))),
                            int(round(min(ys))),
                            int(round(max(xs))),
                            int(round(max(ys))),
                        )
                        pos = ((ocr_box[0] + ocr_box[2]) // 2, (ocr_box[1] + ocr_box[3]) // 2)
                    elif require_ocr_box:
                        continue
                    else:
                        pos = (
                            int(round(x + w / 2 + (region[0] if region else 0))),
                            int(round(y + h / 2 + (region[1] if region else 0))),
                        )

                    if best is None or score > best["score"]:
                        best = {
                            "target": target_text,
                            "text": result.text,
                            "score": score,
                            "pos": pos,
                            "box": box,
                            "ocr_box": ocr_box,
                        }
                    matched = True
                return matched

            for x, y, w, h in boxes:
                box = (x, y, w, h)
                roi = screen_bgr[y : y + h, x : x + w]
                if roi.size == 0:
                    continue

                candidate_too_tall = h > max(90, int(screen_bgr.shape[0] * 0.12))
                if candidate_too_tall:
                    for result in self.app.services.ocr.read(roi, text_score=0.25):
                        consider_result(result, box, require_ocr_box=True)
                    continue

                matched = False
                line_result = self.app.services.ocr.recognize_line(roi, min_score=0.25)
                if line_result is not None:
                    matched = consider_result(line_result, box)

                if not matched:
                    for result in self.app.services.ocr.read(roi, text_score=0.25):
                        consider_result(result, box)

            if best and best["score"] >= threshold:
                self.app.services.image_matcher.last_positions[best["target"]] = best["pos"]
                x, y, w, h = best["box"]
                ocr_box_text = ""
                if best.get("ocr_box"):
                    ox1, oy1, ox2, oy2 = best["ocr_box"]
                    ocr_box_text = f" | OCR框: x1={ox1}, y1={oy1}, x2={ox2}, y2={oy2}"
                self.app.log(
                    f"[MenuOCR] 命中: {best['text']} "
                    f"(目标:{best['target']}) | 分数:{best['score']:.3f} "
                    f"(阈值 {threshold}) | 候选框: x={x}, y={y}, w={w}, h={h}{ocr_box_text}"
                )
                return best["pos"]

            return None
        except Exception as e:
            self.app.log(f"_find_any_menu_text_ocr 异常: {e}")
            return None

    def _footer_text_regions(self, region):
        if region is None:
            full_region = self.app.services.game_window.regions.get("全界面")
            if full_region is None:
                screen_bgr = self.app.services.image_cache.capture_region(None)
                h, w = screen_bgr.shape[:2]
                region = (0, 0, w, h)
            else:
                region = full_region

        sx, sy, sw, sh = map(int, region)
        bottom_y = sy + int(sh * 0.80)
        bottom_h = max(1, sh - int(sh * 0.80))
        right_x = sx + int(sw * 0.45)
        right_w = max(1, sw - int(sw * 0.45))
        return [
            ("右下提示栏", (right_x, bottom_y, right_w, bottom_h)),
            ("底部提示栏", (sx, bottom_y, sw, bottom_h)),
        ]

    def _footer_ocr_candidates(self, results, offset_x, offset_y, max_window=4):
        ordered = sorted(
            (result for result in results if result is not None and self.app.services.ocr.normalize_text(result.text)),
            key=lambda result: (
                min((point[1] for point in result.box), default=0) if result.box else 0,
                min((point[0] for point in result.box), default=0) if result.box else 0,
            ),
        )
        candidates = []
        for start in range(len(ordered)):
            for end in range(start, min(len(ordered), start + max_window)):
                window = ordered[start : end + 1]
                text = "".join(result.text for result in window)
                score = min(float(result.score) for result in window)
                boxes = [result.box for result in window if result.box]
                if boxes:
                    xs = [float(point[0]) + offset_x for box in boxes for point in box]
                    ys = [float(point[1]) + offset_y for box in boxes for point in box]
                    box = (
                        int(round(min(xs))),
                        int(round(min(ys))),
                        int(round(max(xs))),
                        int(round(max(ys))),
                    )
                    pos = ((box[0] + box[2]) // 2, (box[1] + box[3]) // 2)
                else:
                    box = None
                    pos = None
                candidates.append((text, score, pos, box))
        return candidates

    def _find_any_footer_text_ocr(self, text_list, region=None, threshold=0.65):
        """专门识别底部按键提示栏文字，避免通用候选框漏检右侧提示。"""
        try:
            targets = self._build_text_targets(text_list)
            if not targets:
                return None

            for roi_name, roi in self._footer_text_regions(region):
                rx, ry, rw, rh = roi
                roi_bgr = self.app.services.image_cache.capture_region(roi)
                if roi_bgr.size == 0:
                    continue

                results = self.app.services.ocr.read(roi_bgr, text_score=max(0.25, threshold - 0.45))
                best = None
                candidates = self._footer_ocr_candidates(results, rx, ry)
                for target_text, target_norm in targets:
                    for text, candidate_score, candidate_pos, candidate_box in candidates:
                        score = self._score_text_ocr_match(
                            text,
                            candidate_score,
                            target_norm,
                            threshold,
                            allow_candidate_subset=False,
                            coverage_floor=0.82,
                        )
                        if score is None:
                            continue

                        fallback_pos = (int(rx + rw / 2), int(ry + rh / 2))
                        pos = candidate_pos or fallback_pos
                        box = candidate_box
                        if best is None or score > best["score"]:
                            best = {
                                "target": target_text,
                                "text": text,
                                "score": score,
                                "pos": pos,
                                "box": box,
                                "roi_name": roi_name,
                            }

                if best and best["score"] >= threshold:
                    self.app.services.image_matcher.last_positions[best["target"]] = best["pos"]
                    box_text = f" | OCR框: {best['box']}" if best["box"] else ""
                    self.app.log(
                        f"[FooterOCR] 命中: {best['text']} "
                        f"(目标:{best['target']}) | 分数:{best['score']:.3f} "
                        f"(阈值 {threshold}) | 区域:{best['roi_name']}{box_text}"
                    )
                    return best["pos"]

            return None
        except Exception as e:
            self.app.log(f"_find_any_footer_text_ocr 异常: {e}")
            return None


    def find_any_text_ui(self, text_list, region=None, threshold=0.65, fast_mode=True):
        """OCR-only UI 文字查找。"""
        if not self.app.state.is_running:
            return None
        try:
            return self._find_any_text_ocr(text_list, region=region, threshold=threshold)
        except Exception as e:
            self.app.log(f"find_any_text_ui 异常: {e}")
            return None


    def find_text_ui(self, target_text, region=None, threshold=0.65, fast_mode=True):
        return self.find_any_text_ui(
            [target_text],
            region=region,
            threshold=threshold,
            fast_mode=fast_mode,
        )

    def find_menu_text_ui(self, target_text, region=None, threshold=0.65):
        if not self.app.state.is_running:
            return None
        return self._find_any_menu_text_ocr([target_text], region=region, threshold=threshold)

    def find_footer_text_ui(self, target_text, region=None, threshold=0.65):
        if not self.app.state.is_running:
            return None
        return self._find_any_footer_text_ocr([target_text], region=region, threshold=threshold)


    def wait_for_any_text_ui(
        self,
        text_list,
        region=None,
        threshold=0.65,
        timeout: float = 30,
        interval: float = 0.3,
        fast_mode=True,
    ):
        start = time.time()
        while self.app.state.is_running and time.time() - start < timeout:
            pos = self.find_any_text_ui(
                text_list,
                region=region,
                threshold=threshold,
                fast_mode=fast_mode,
            )
            if pos:
                return pos

            sleep_end = time.time() + interval
            while self.app.state.is_running and time.time() < sleep_end:
                time.sleep(0.05)
        return None


    def wait_for_text_ui(
        self,
        target_text,
        region=None,
        threshold=0.65,
        timeout: float = 30,
        interval: float = 0.3,
        fast_mode=True,
    ):
        return self.wait_for_any_text_ui(
            [target_text],
            region=region,
            threshold=threshold,
            timeout=timeout,
            interval=interval,
            fast_mode=fast_mode,
        )

    def wait_for_menu_text_ui(
        self,
        target_text,
        region=None,
        threshold=0.65,
        timeout: float = 30,
        interval: float = 0.3,
    ):
        start = time.time()
        while self.app.state.is_running and time.time() - start < timeout:
            pos = self.find_menu_text_ui(
                target_text,
                region=region,
                threshold=threshold,
            )
            if pos:
                return pos

            sleep_end = time.time() + interval
            while self.app.state.is_running and time.time() < sleep_end:
                time.sleep(0.05)
        return None

    def wait_for_footer_text_ui(
        self,
        target_text,
        region=None,
        threshold=0.65,
        timeout: float = 30,
        interval: float = 0.3,
    ):
        start = time.time()
        while self.app.state.is_running and time.time() - start < timeout:
            pos = self.find_footer_text_ui(
                target_text,
                region=region,
                threshold=threshold,
            )
            if pos:
                return pos

            sleep_end = time.time() + interval
            while self.app.state.is_running and time.time() < sleep_end:
                time.sleep(0.05)
        return None

    @staticmethod
    def _cluster_axis_positions(values, tolerance):
        clusters = []
        for value in sorted(int(v) for v in values):
            if not clusters or abs(value - clusters[-1][-1]) > tolerance:
                clusters.append([value])
            else:
                clusters[-1].append(value)
        return [int(round(sum(cluster) / len(cluster))) for cluster in clusters]

    def _find_manufacturer_cells(self, screen_bgr):
        h, w = screen_bgr.shape[:2]
        gray = cv2.cvtColor(screen_bgr, cv2.COLOR_BGR2GRAY)
        _, mask = cv2.threshold(gray, 234, 255, cv2.THRESH_BINARY)
        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

        raw_cells = []
        min_w = max(80, int(w * 0.10))
        max_w = int(w * 0.30)
        min_h = max(24, int(h * 0.025))
        max_h = int(h * 0.08)
        for contour in contours:
            x, y, cell_w, cell_h = cv2.boundingRect(contour)
            if cell_w < min_w or cell_w > max_w or cell_h < min_h or cell_h > max_h:
                continue
            if y < int(h * 0.18) or y > int(h * 0.92):
                continue
            area_ratio = cv2.contourArea(contour) / float(cell_w * cell_h)
            if area_ratio < 0.72:
                continue
            raw_cells.append((x, y, cell_w, cell_h))

        if len(raw_cells) < 4:
            return raw_cells

        median_w = int(round(np.median([cell[2] for cell in raw_cells])))
        median_h = int(round(np.median([cell[3] for cell in raw_cells])))
        x_positions = self._cluster_axis_positions([cell[0] for cell in raw_cells], max(12, median_w // 4))
        y_positions = self._cluster_axis_positions([cell[1] for cell in raw_cells], max(8, median_h // 2))

        if len(x_positions) < 2 or len(y_positions) < 2:
            return raw_cells

        grid_cells = []
        seen = set()
        for y in y_positions:
            for x in x_positions:
                cell = (
                    max(0, x),
                    max(0, y),
                    min(median_w, w - x),
                    min(median_h, h - y),
                )
                key = (cell[0] // 4, cell[1] // 4)
                if cell[2] > 0 and cell[3] > 0 and key not in seen:
                    seen.add(key)
                    grid_cells.append(cell)

        return grid_cells or raw_cells

    def find_manufacturer_text(self, target_text, region=None, threshold=0.75):
        """在制造商表格单元格内通过 OCR 查找目标制造商文字。"""
        if not self.app.state.is_running:
            return None
        try:
            screen_bgr = self.app.services.image_cache.capture_region(region)
            if not target_text:
                return None
            target_norm = self.app.services.ocr.normalize_text(target_text)

            matched = None
            for x, y, cell_w, cell_h in self._find_manufacturer_cells(screen_bgr):
                cell = screen_bgr[y : y + cell_h, x : x + cell_w]
                result = self.app.services.ocr.recognize_cell_text(cell, min_score=0.3)
                if result is None:
                    continue

                candidate = {
                    "text": result.text,
                    "score": result.score,
                    "cell": (x, y, cell_w, cell_h),
                    "pos": (
                        int(round(x + cell_w / 2 + (region[0] if region else 0))),
                        int(round(y + cell_h / 2 + (region[1] if region else 0))),
                    ),
                }
                if self.app.services.ocr.normalize_text(result.text) == target_norm and result.score >= threshold:
                    matched = candidate
                    break

            if matched:
                self.app.services.image_matcher.last_positions[target_text] = matched["pos"]
                x, y, cell_w, cell_h = matched["cell"]
                self.app.log(
                    f"[ManufacturerOCR] 命中: {matched['text']} (目标:{target_text}) | 分数:{matched['score']:.3f} "
                    f"(阈值 {threshold}) | 单元格: x={x}, y={y}, w={cell_w}, h={cell_h}"
                )
                return matched["pos"]

            return None
        except Exception as e:
            self.app.log(f"find_manufacturer_text 异常: {e}")
            return None

    def find_manufacturer_text_by_shape(self, template_path, region=None, threshold=0.75):
        """旧的制造商表格字形匹配实现，保留用于 OCR 迁移期对比。"""
        if not self.app.state.is_running:
            return None
        try:
            screen_bgr = self.app.services.image_cache.capture_region(region)
            template_mask = self.app.services.image_cache.text_foreground_mask_from_template(template_path)
            if template_mask is None:
                return None

            best = None
            for x, y, cell_w, cell_h in self._find_manufacturer_cells(screen_bgr):
                cell = screen_bgr[y : y + cell_h, x : x + cell_w]
                candidate_mask = self.app.services.image_cache.text_foreground_mask_from_cell(cell)
                score = self.app.services.image_cache.score_text_foreground_masks(candidate_mask, template_mask)
                if best is None or score > best["score"]:
                    best = {
                        "score": score,
                        "cell": (x, y, cell_w, cell_h),
                        "pos": (
                            int(round(x + cell_w / 2 + (region[0] if region else 0))),
                            int(round(y + cell_h / 2 + (region[1] if region else 0))),
                        ),
                    }

            if best and best["score"] >= threshold:
                self.app.services.image_matcher.last_positions[template_path] = best["pos"]
                x, y, cell_w, cell_h = best["cell"]
                self.app.log(
                    f"[ManufacturerShape] 命中: {template_path} | 分数:{best['score']:.3f} "
                    f"(阈值 {threshold}) | 单元格: x={x}, y={y}, w={cell_w}, h={cell_h}"
                )
                return best["pos"]

            return None
        except Exception as e:
            self.app.log(f"find_manufacturer_text_by_shape 异常: {e}")
            return None

    def wait_for_manufacturer_text(
        self,
        target_text,
        region=None,
        threshold=0.75,
        timeout: float = 30,
        interval: float = 0.3,
    ):
        start = time.time()
        while self.app.state.is_running and time.time() - start < timeout:
            pos = self.find_manufacturer_text(
                target_text,
                region=region,
                threshold=threshold,
            )
            if pos:
                return pos

            sleep_end = time.time() + interval
            while self.app.state.is_running and time.time() < sleep_end:
                time.sleep(0.05)
        return None



    def find_manufacturer_by_text(self, target_text, threshold=0.75, max_steps=None, label="目标制造商"):
        try:
            if max_steps is None:
                max_steps = int(self.app.services.config.values.get("manufacturer_scan_steps", 50))
        except Exception:
            max_steps = 50
        max_steps = max(5, min(50, int(max_steps)))

        def wait_for_manufacturer_once(timeout):
            return self.wait_for_manufacturer_text(
                target_text,
                region=self.app.services.game_window.regions["全界面"],
                threshold=threshold,
                timeout=timeout,
                interval=0.1,
            )

        pos = wait_for_manufacturer_once(timeout=1.0)
        if pos:
            self.app.log(f"已在当前页面找到{label}。")
            return pos

        scan_plan = (("up", "上", max_steps), ("down", "下", max_steps * 2))
        for direction, direction_label, steps in scan_plan:
            self.app.log(f"当前页面未找到{label}，开始向{direction_label}扫描制造商列表 ({steps} 步)...")
            for i in range(steps):
                if not self.app.state.is_running:
                    return None

                self.app.services.input_actions.hw_press(direction)
                time.sleep(0.18)

                pos = wait_for_manufacturer_once(timeout=0.35)
                if pos:
                    self.app.log(f"找到{label}：向{direction_label}扫描第 {i + 1} 步。")
                    return pos

        self.app.log(f"扫描制造商列表后仍未找到{label}。")
        return None

