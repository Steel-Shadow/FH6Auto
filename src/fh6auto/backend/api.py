from __future__ import annotations

from pathlib import Path
import sys
from typing import Any, Literal
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from importlib.metadata import version
from packaging.version import InvalidVersion
import uvicorn

from .app import BackendApp


def get_version() -> str:
    """Read the package version managed by uv via pyproject metadata."""
    PACKAGE_NAME = "fh6auto"
    try:
        return version(PACKAGE_NAME)
    except InvalidVersion:
        return "0.0.0"


class ConfigUpdateRequest(BaseModel):
    config: dict[str, Any] = Field(default_factory=dict)


class StartPipelineRequest(BaseModel):
    step: Literal["race", "buy", "mastery", "auto_wheelspin", "sell"]


class SkillDirsRequest(BaseModel):
    directions: list[Literal["up", "down", "left", "right"]]


class CalculateRequest(BaseModel):
    target_cr: int = Field(gt=0)
    cost_per_car: int = Field(default=81700, gt=0)
    sp_per_car: int = Field(default=30, gt=0)
    sp_per_race: int = Field(default=50, gt=0)
    apply: bool = True


backend_app = BackendApp()
app = FastAPI(title="FH6Auto Backend", version=get_version())

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/state")
def get_state() -> dict[str, Any]:
    return backend_app.snapshot()


@app.get("/api/config")
def get_config() -> dict[str, Any]:
    return backend_app.services.config.values.copy()


@app.put("/api/config")
def update_config(payload: ConfigUpdateRequest) -> dict[str, Any]:
    return {"config": backend_app.services.config.update(payload.config)}


@app.post("/api/pipeline/start")
def start_pipeline(payload: StartPipelineRequest) -> dict[str, Any]:
    try:
        started = backend_app.services.runtime.start_pipeline(payload.step)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    if not started:
        raise HTTPException(status_code=409, detail="已有任务正在运行。")
    return {"started": True, "state": backend_app.snapshot()}


@app.post("/api/pipeline/stop")
def stop_pipeline() -> dict[str, Any]:
    backend_app.services.runtime.stop_all()
    return {"stopped": True, "state": backend_app.snapshot()}


@app.post("/api/pipeline/pause")
def toggle_pause() -> dict[str, Any]:
    paused = backend_app.services.runtime.toggle_pause()
    return {"paused": paused, "state": backend_app.snapshot()}


@app.post("/api/pipeline/test-boot")
def start_test_boot() -> dict[str, Any]:
    started = backend_app.services.runtime.start_test_boot()
    if not started:
        raise HTTPException(status_code=409, detail="已有任务正在运行。")
    return {"started": True, "state": backend_app.snapshot()}


@app.post("/api/skill-dirs")
def set_skill_dirs(payload: SkillDirsRequest) -> dict[str, Any]:
    backend_app.services.config.update({"skill_dirs": list(payload.directions)})
    return {"skill_dirs": backend_app.services.config.values.get("skill_dirs", [])}


@app.post("/api/tools/calculate")
def calculate_pipeline(payload: CalculateRequest) -> dict[str, Any]:
    try:
        return backend_app.services.runtime.calculate_pipeline(
            target_cr=payload.target_cr,
            cost_per_car=payload.cost_per_car,
            sp_per_car=payload.sp_per_car,
            sp_per_race=payload.sp_per_race,
            apply=payload.apply,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


def find_frontend_dist() -> Path:
    candidates = [
        Path(getattr(sys, "_MEIPASS", "")) / "frontend" / "dist",
        Path(sys.executable).resolve().parent / "frontend" / "dist",
        Path(__file__).resolve().parents[3] / "frontend" / "dist",
    ]
    for candidate in candidates:
        if (candidate / "index.html").exists():
            return candidate
    return candidates[-1]


FRONTEND_DIST = find_frontend_dist()
if FRONTEND_DIST.exists():
    assets_dir = FRONTEND_DIST / "assets"
    if assets_dir.exists():
        app.mount("/assets", StaticFiles(directory=assets_dir), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    def serve_frontend(path: str = ""):
        requested = FRONTEND_DIST / path
        if path and requested.exists() and requested.is_file():
            return FileResponse(requested)
        return FileResponse(FRONTEND_DIST / "index.html")


def run(host: str = "127.0.0.1", port: int = 8000) -> None:
    uvicorn.run(app, host=host, port=port, log_level="warning")


if __name__ == "__main__":
    run()
