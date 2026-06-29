from __future__ import annotations

import gc
import os
import re
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from collections.abc import Callable
from typing import TYPE_CHECKING

import numpy as np
from rapidocr import EngineType, RapidOCR

from ..backend.state import RuntimeState
from .cache import ImageCacheService
from .timing import VisionTimingMixin

if TYPE_CHECKING:
    from ..window import GameWindowService
    from .footer import FooterDetector
    from .manufacturer import ManufacturerDetector
    from .player_stats import PlayerStatsDetector
    from .text import TextDetector


@dataclass(frozen=True)
class OcrText:
    text: str
    score: float
    box: tuple[tuple[float, float], ...] | None = None


class OcrService(VisionTimingMixin):
    """提供 OCR 引擎能力和旧入口兼容委托。"""

    TIMING_NAME = "OCR"

    def __init__(
        self,
        *,
        state: RuntimeState,
        image_cache: ImageCacheService,
        game_window: GameWindowService,
        log: Callable[..., None],
    ) -> None:
        self.state = state
        self.image_cache = image_cache
        self.game_window = game_window
        self.log = log
        self.text_detector: TextDetector | None = None
        self.player_stats: PlayerStatsDetector | None = None
        self.footer: FooterDetector | None = None
        self.manufacturer: ManufacturerDetector | None = None
        self._providers: dict[str, list[str] | None] = {}
        self._dll_handles: list[object] = []
        self._dll_dirs: set[str] = set()
        self._lock = threading.RLock()
        self._engine: RapidOCR | None = None
        self._read_count = 0
        self._reads_since_gc = 0
        self._gc_read_interval = 10
        self._ensure_engine()

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

    def _ensure_engine(self) -> RapidOCR:
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
            if not self._cuda_provider_active():
                provider_summary = ", ".join(
                    f"{name}={providers or ['unknown']}" for name, providers in self._providers.items()
                )
                self._engine = None
                self._providers = {}
                raise RuntimeError(
                    "OCR CUDA 初始化失败，已拒绝回退到 CPU。"
                    f"当前 providers: {provider_summary or 'unknown'}。"
                    "请确认 nvidia-cuda-runtime、nvidia-cudnn-cu13 等依赖 DLL 可被加载。"
                )
            provider_summary = ", ".join(
                f"{name}={providers or ['unknown']}" for name, providers in self._providers.items()
            )
            self.log(f"OCR 引擎已初始化，{provider_summary or 'providers=unknown'}。", level="debug")
            return self._engine

    def _cuda_provider_active(self) -> bool:
        if not self._providers:
            return False
        for name in ("text_det", "text_cls", "text_rec"):
            providers = self._providers.get(name) or []
            if "CUDAExecutionProvider" not in providers:
                return False
        return True

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

        gc.collect()
        return True

    @staticmethod
    def normalize_text(text: str) -> str:
        normalized = text.upper()
        normalized = re.sub(r"[\s·・.\-_—:：/\\|]+", "", normalized)
        normalized = re.sub(r"[，,。!！?？（）()\[\]【】]+", "", normalized)
        return normalized

    def _mark_read_completed_locked(self) -> bool:
        self._read_count += 1
        self._reads_since_gc += 1

        should_gc = self._reads_since_gc >= self._gc_read_interval
        if should_gc:
            self._reads_since_gc = 0
        return should_gc

    def read(self, img: np.ndarray | str | Path, *, use_det=True, use_cls=True, text_score=0.5) -> list[OcrText]:
        should_gc = False
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

            should_gc = self._mark_read_completed_locked()

            del result, raw_texts, raw_scores, raw_boxes, texts, scores, boxes

        if should_gc:
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
