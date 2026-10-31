"""Loopback-only HTTP shell for the local single-user application."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import Body, FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .service import ParkingService

ROOT = Path(__file__).resolve().parents[1]


def create_app(service: ParkingService) -> FastAPI:
    app = FastAPI(title="智慧停车仿真 · 胡轩铭模块", version="1.0.0")
    app.state.parking = service

    @app.get("/api/state")
    def state():
        return service.state()

    @app.post("/api/action")
    def action(request: dict[str, Any] = Body(...)):
        result = service.action(request)
        return {"result": result, "state": service.state()}

    @app.get("/api/frame")
    def frame(frame_id: str | None = None):
        with service.lock:
            if not service.image_available or (frame_id and frame_id != service.frame_id):
                raise HTTPException(404, "当前帧不存在或已失效")
            # FileResponse opens later; a small immutable byte response prevents a frame rewrite race.
            from fastapi.responses import Response
            return Response(service.image_path.read_bytes(), media_type="image/png", headers={"Cache-Control": "no-store"})

    @app.get("/api/export")
    def export():
        return JSONResponse(service.export_context(), headers={"Content-Disposition": 'attachment; filename="planner_context_v1.json"'})

    @app.get("/api/report")
    def report():
        target = ROOT / "reports" / "report.html"
        if not target.exists():
            return JSONResponse({"status": "not_generated", "message": "评测报告尚未生成。"})
        return FileResponse(target, media_type="text/html")

    @app.get("/")
    def index():
        return FileResponse(ROOT / "static" / "index.html")

    app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")
    return app
