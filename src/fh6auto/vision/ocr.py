from __future__ import annotations

import gc
import os
import re
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

import numpy as np

if TYPE_CHECKING:
    from ..backend.app import BackendApp


@dataclass(frozen=True)
class OcrText:
    text: str
    score: float
    box: tuple[tuple[float, float], ...] | None = None


class OcrService:
    def __init__(self, app: BackendApp) -> None:
        self.app = app
        self._engine: Any | None = None
        self._providers: dict[str, list[str] | None] = {}
        self._dll_handles: list[Any] = []
        self._dll_dirs: set[str] = set()
        self._lock = threading.RLock()

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
            from rapidocr import RapidOCR

            params = {
                "EngineConfig.onnxruntime.use_cuda": True,
                "Global.log_level": "warning",
            }
            self._engine = RapidOCR(params=params)
            self._providers = self._read_session_providers()
            provider_summary = ", ".join(
                f"{name}={providers or ['unknown']}" for name, providers in self._providers.items()
            )
            self.app.log(f"OCR 引擎已初始化，{provider_summary or 'providers=unknown'}。")
            return self._engine

    def _read_session_providers(self) -> dict[str, list[str] | None]:
        providers: dict[str, list[str] | None] = {}
        if self._engine is None:
            return providers

        for name in ("text_det", "text_cls", "text_rec"):
            session = getattr(getattr(getattr(self._engine, name, None), "session", None), "session", None)
            providers[name] = session.get_providers() if session is not None else None
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

        gc.collect()
        return True

    @staticmethod
    def normalize_text(text: str) -> str:
        normalized = text.upper()
        normalized = re.sub(r"[\s·・.\-_—:：/\\|]+", "", normalized)
        normalized = re.sub(r"[，,。!！?？（）()\[\]【】]+", "", normalized)
        return normalized

    def read(self, img: np.ndarray | str | Path, *, use_det=True, use_cls=True, text_score=0.5) -> list[OcrText]:
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

    @staticmethod
    def crop_region(img: np.ndarray, region: tuple[int, int, int, int] | None) -> np.ndarray:
        if region is None:
            return img
        x, y, w, h = map(int, region)
        return img[y : y + h, x : x + w]
