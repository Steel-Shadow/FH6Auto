from __future__ import annotations

import gc
import os
import re
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

import numpy as np
from rapidocr import EngineType, RapidOCR


@dataclass(frozen=True)
class OcrText:
    text: str
    score: float
    box: tuple[tuple[float, float], ...] | None = None


class OcrService:
    """提供 OCR 引擎能力和旧入口兼容委托。"""

    def __init__(self, *, state: Any, image_cache: Any, game_window: Any, log: Callable[..., None]) -> None:
        self.state = state
        self.image_cache = image_cache
        self.game_window = game_window
        self.log = log
        self._providers: dict[str, list[str] | None] = {}
        self._dll_handles: list[Any] = []
        self._dll_dirs: set[str] = set()
        self._lock = threading.RLock()
        self._engine: RapidOCR | None = None
        self._read_count = 0
        self._reads_since_gc = 0
        self._reads_since_recycle = 0
        self._last_recycle_time = time.monotonic()
        self._gc_read_interval = 200
        self._recycle_read_interval = 1200
        self._recycle_seconds = 30 * 60
        self._ensure_engine()

    @staticmethod
    def _elapsed_ms(start: float) -> float:
        return (time.perf_counter() - start) * 1000.0

    def _log_timing(self, name: str, start: float, **details) -> None:
        parts = [f"total={self._elapsed_ms(start):.1f}ms"]
        for key, value in details.items():
            if value is None:
                continue
            if isinstance(value, float):
                parts.append(f"{key}={value:.1f}ms" if key.endswith("_ms") else f"{key}={value:.3f}")
            else:
                parts.append(f"{key}={value}")
        self.log(f"[VisionTiming] OCR.{name} " + " ".join(parts), level="debug")

    def _add_nvidia_dll_paths(self) -> list[str]:
        try:
            import nvidia
        except Exception:
            return []

        dll_dirs = sorted({str(p.parent) for base in nvidia.__path__ for p in Path(base).rglob("*.dll")})
        if not dll_dirs:
            return []

        current_path = os.environ.get("PATH", "")
        os.environ["PATH"] = ";".join(dll_dirs + [current_path])
        for dll_dir in dll_dirs:
            if dll_dir in self._dll_dirs:
                continue
            try:
                handle = os.add_dll_directory(dll_dir)
                self._dll_handles.append(handle)
                self._dll_dirs.add(dll_dir)
            except Exception:
                pass
        return dll_dirs

    def _ensure_engine(self):
        with self._lock:
            if self._engine is not None:
                return self._engine

            self._add_nvidia_dll_paths()

            params = {
                "Det.engine_type": EngineType.ONNXRUNTIME,
                "Cls.engine_type": EngineType.ONNXRUNTIME,
                "Rec.engine_type": EngineType.ONNXRUNTIME,
                "EngineConfig.onnxruntime.use_cuda": True,
                "Global.log_level": "warning",
            }
            self._engine = RapidOCR(params=params)
            self._providers = self._read_session_providers()
            provider_summary = ", ".join(
                f"{name}={providers or ['unknown']}" for name, providers in self._providers.items()
            )
            self.log(f"OCR 引擎已初始化，{provider_summary or 'providers=unknown'}。", level="debug")
            return self._engine

    def _read_session_providers(self) -> dict[str, list[str] | None]:
        providers: dict[str, list[str] | None] = {}
        if self._engine is None:
            return providers

        for name in ("text_det", "text_cls", "text_rec"):
            infer_session = getattr(getattr(self._engine, name, None), "session", None)
            onnx_session = getattr(infer_session, "session", None)
            if onnx_session is not None and hasattr(onnx_session, "get_providers"):
                providers[name] = onnx_session.get_providers()
                continue

            device = getattr(infer_session, "device", None)
            if device is not None:
                providers[name] = [f"{type(infer_session).__name__}:{device}"]
                continue

            providers[name] = [type(infer_session).__name__] if infer_session is not None else None
        return providers

    @property
    def providers(self) -> dict[str, list[str] | None]:
        with self._lock:
            if self._engine is None:
                return {}
            return dict(self._providers)

    def release(self) -> bool:
        with self._lock:
            if self._engine is None:
                return False

            self._engine = None
            self._providers = {}
            self._reads_since_gc = 0
            self._reads_since_recycle = 0
            self._last_recycle_time = time.monotonic()

        gc.collect()
        return True

    @staticmethod
    def normalize_text(text: str) -> str:
        normalized = text.upper()
        normalized = re.sub(r"[\s·・.\-_—:：/\\|]+", "", normalized)
        normalized = re.sub(r"[，,。!！?？（）()\[\]【】]+", "", normalized)
        return normalized

    def _mark_read_completed_locked(self) -> tuple[bool, bool]:
        self._read_count += 1
        self._reads_since_gc += 1
        self._reads_since_recycle += 1
        now = time.monotonic()

        should_gc = self._reads_since_gc >= self._gc_read_interval
        should_recycle = (
            self._reads_since_recycle >= self._recycle_read_interval
            or now - self._last_recycle_time >= self._recycle_seconds
        )

        if should_gc:
            self._reads_since_gc = 0
        if should_recycle:
            self._engine = None
            self._providers = {}
            self._reads_since_recycle = 0
            self._last_recycle_time = now
        return should_gc, should_recycle

    def read(self, img: np.ndarray | str | Path, *, use_det=True, use_cls=True, text_score=0.5) -> list[OcrText]:
        should_gc = False
        should_recycle = False
        started = time.perf_counter()
        with self._lock:
            engine = self._ensure_engine()
            result = engine(img, use_det=use_det, use_cls=use_cls, text_score=text_score)

            raw_texts = getattr(result, "txts", None)
            raw_scores = getattr(result, "scores", None)
            raw_boxes = getattr(result, "boxes", None)
            texts = list(raw_texts) if raw_texts is not None else []
            scores = list(raw_scores) if raw_scores is not None else []
            boxes = list(raw_boxes) if raw_boxes is not None else []

            output: list[OcrText] = []
            for idx, text in enumerate(texts):
                score = float(scores[idx]) if idx < len(scores) else 0.0
                box = None
                if idx < len(boxes):
                    box = tuple((float(x), float(y)) for x, y in boxes[idx])
                output.append(OcrText(str(text), score, box))

            should_gc, should_recycle = self._mark_read_completed_locked()
            if should_recycle:
                self.log(
                    "OCR 引擎达到周期回收阈值，已释放当前会话，下次识别会重新初始化。",
                    level="debug",
                )
                engine = None

            del result, raw_texts, raw_scores, raw_boxes, texts, scores, boxes

        if should_gc or should_recycle:
            gc.collect()
            shape = ""
            if isinstance(img, np.ndarray) and img.ndim >= 2:
                shape = f"{img.shape[1]}x{img.shape[0]}"
            elif isinstance(img, str | Path):
                shape = "path"
            self._log_timing(
                "read",
                started,
                use_det=use_det,
                use_cls=use_cls,
                threshold=float(text_score),
                items=len(output),
                image=shape,
            )
        return output


    def recognize_line(self, img: np.ndarray | str | Path, *, min_score=0.5) -> OcrText | None:
        results = self.read(img, use_det=False, use_cls=False, text_score=min_score)
        if not results:
            return None
        best = max(results, key=lambda item: item.score)
        return best if best.score >= min_score else None

    def recognize_cell_text(self, cell_bgr: np.ndarray, *, min_score=0.5) -> OcrText | None:
        if cell_bgr is None or cell_bgr.size == 0:
            return None

        return self.recognize_line(cell_bgr, min_score=min_score)

    def find_any_text_ui(self, text_list, region=None, threshold=0.65):
        """兼容旧入口；实际实现位于 TextDetector。"""
        detector = getattr(self, "text_detector", None)
        if detector is None:
            self.log("find_any_text_ui 未绑定 TextDetector。", level="warning")
            return None
        return detector.find_any_text_ui(text_list, region=region, threshold=threshold)

    def find_sell_price_value(self, region=None, threshold=0.25) -> int | None:
        """兼容旧入口；实际实现位于 PlayerStatsDetector。"""
        detector = getattr(self, "player_stats", None)
        if detector is None:
            self.log("find_sell_price_value 未绑定 PlayerStatsDetector。", level="warning")
            return None
        return detector.find_sell_price_value(region=region, threshold=threshold)

    def find_current_credit_value(self, region=None, threshold=0.25) -> int | None:
        """兼容旧入口；实际实现位于 PlayerStatsDetector。"""
        detector = getattr(self, "player_stats", None)
        if detector is None:
            self.log("find_current_credit_value 未绑定 PlayerStatsDetector。", level="warning")
            return None
        return detector.find_current_credit_value(region=region, threshold=threshold)

    def find_current_skill_points_value(self, region=None, threshold=0.25) -> int | None:
        """兼容旧入口；实际实现位于 PlayerStatsDetector。"""
        detector = getattr(self, "player_stats", None)
        if detector is None:
            self.log("find_current_skill_points_value 未绑定 PlayerStatsDetector。", level="warning")
            return None
        return detector.find_current_skill_points_value(region=region, threshold=threshold)

    def find_menu_text_ui(self, target_text, region=None, threshold=0.65):
        """兼容旧入口；实际实现位于 TextDetector。"""
        detector = getattr(self, "text_detector", None)
        if detector is None:
            self.log("find_menu_text_ui 未绑定 TextDetector。", level="warning")
            return None
        return detector.find_menu_text_ui(target_text, region=region, threshold=threshold)

    def find_footer_text_ui(self, target_text, region=None, threshold=0.65):
        """兼容旧入口；实际实现位于 FooterDetector。"""
        detector = getattr(self, "footer", None)
        if detector is None:
            self.log("find_footer_text_ui 未绑定 FooterDetector。", level="warning")
            return None
        return detector.find_text(target_text, region=region, threshold=threshold)

    def find_manufacturer_text(self, target_text, region=None, threshold=0.75):
        """兼容旧入口；实际实现位于 ManufacturerDetector。"""
        detector = getattr(self, "manufacturer", None)
        if detector is None:
            self.log("find_manufacturer_text 未绑定 ManufacturerDetector。", level="warning")
            return None
        return detector.find_text(target_text, region=region, threshold=threshold)

    def _find_manufacturer_cells(self, screen_bgr):
        """兼容旧调试入口；实际实现位于 ManufacturerDetector。"""
        detector = getattr(self, "manufacturer", None)
        if detector is None:
            return []
        return detector.find_cells(screen_bgr)
