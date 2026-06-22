from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from threading import RLock
import time
from typing import Any


@dataclass
class ProgressSnapshot:
    task_name: str = "等待中"
    current: int = 0
    total: int = 0


class RuntimeState:
    LOG_LEVELS = {
        "debug": 10,
        "info": 20,
        "warning": 30,
        "error": 40,
    }

    def __init__(self, max_logs: int = 1000) -> None:
        self._lock = RLock()
        self._logs: deque[dict[str, Any]] = deque(maxlen=max_logs)
        self._next_log_id = 1
        self.is_running = False
        self.is_paused = False
        self.current_thread: Any | None = None
        self.current_task = "等待中"
        self.progress = ProgressSnapshot()
        self.race_counter = 0
        self.car_counter = 0
        self.mastery_counter = 0
        self.wheelspin_counter = 0
        self.sc_count = 0
        self.memory_car_page = 0
        self.loop_current = 0
        self.loop_total = 0
        self.started_at: float | None = None
        self.status = "idle"

    @classmethod
    def normalize_log_level(cls, level: str) -> str:
        level = str(level or "info").lower()
        return level if level in cls.LOG_LEVELS else "info"

    @classmethod
    def should_emit_log(cls, level: str, min_level: str = "info") -> bool:
        level = cls.normalize_log_level(level)
        min_level = cls.normalize_log_level(min_level)
        return cls.LOG_LEVELS[level] >= cls.LOG_LEVELS[min_level]

    def append_log(self, message: str, level: str = "info", min_level: str = "info") -> dict[str, Any] | None:
        level = self.normalize_log_level(level)
        if not self.should_emit_log(level, min_level):
            return None

        with self._lock:
            item = {
                "id": self._next_log_id,
                "time": time.strftime("%H:%M:%S"),
                "level": level,
                "message": message,
            }
            self._next_log_id += 1
            self._logs.append(item)
            return item

    def mark_started(self) -> None:
        with self._lock:
            self.is_running = True
            self.is_paused = False
            self.started_at = time.time()
            self.status = "running"

    def mark_idle(self) -> None:
        with self._lock:
            self.is_running = False
            self.is_paused = False
            self.current_thread = None
            self.status = "idle"

    def mark_paused(self, paused: bool) -> None:
        with self._lock:
            if self.is_running:
                self.is_paused = bool(paused)
                self.status = "paused" if paused else "running"

    def set_task(self, task_name: str = "", current: int = 0, total: int = 0) -> None:
        with self._lock:
            if task_name:
                self.current_task = task_name
                self.progress.task_name = task_name
            if total > 0:
                self.progress.current = int(current)
                self.progress.total = int(total)

    def set_loop(self, current: int, total: int) -> None:
        with self._lock:
            self.loop_current = int(current)
            self.loop_total = int(total)

    def reset_counters(self) -> None:
        with self._lock:
            self.race_counter = 0
            self.car_counter = 0
            self.mastery_counter = 0
            self.wheelspin_counter = 0
            self.sc_count = 0

    def reset_progress(self) -> None:
        with self._lock:
            self.current_task = "等待中"
            self.progress = ProgressSnapshot()
            self.loop_current = 0
            self.loop_total = 0
            self.memory_car_page = 0

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            elapsed = 0
            if self.started_at is not None and self.status != "idle":
                elapsed = int(time.time() - self.started_at)

            return {
                "status": self.status,
                "is_running": self.is_running,
                "is_paused": self.is_paused,
                "current_task": self.current_task,
                "progress": {
                    "task_name": self.progress.task_name,
                    "current": self.progress.current,
                    "total": self.progress.total,
                },
                "loop": {
                    "current": self.loop_current,
                    "total": self.loop_total,
                },
                "counters": {
                    "race": self.race_counter,
                    "buy": self.car_counter,
                    "mastery": self.mastery_counter,
                    "auto_wheelspin": self.wheelspin_counter,
                    "sell": self.sc_count,
                },
                "elapsed_seconds": elapsed,
                "logs": list(self._logs),
            }

