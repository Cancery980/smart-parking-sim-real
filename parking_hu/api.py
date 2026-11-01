"""Loopback-only HTTP shell for the local single-user application."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import Body, FastAPI, HTTPException
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .service import ParkingService
from .real_photos import RealPhotoError, RealPhotoService

ROOT = Path(__file__).resolve().parents[1]


def create_app(service: ParkingService) -> FastAPI:
    app = FastAPI(title="智慧停车仿真 · 胡轩铭模块", version="1.2.0")
    app.state.parking = service
    # No data/model reads at startup: the original simulation remains independent.
    app.state.real_photos = RealPhotoService(ROOT)

    @app.exception_handler(RealPhotoError)
    async def real_photo_error(_request, error):
        return JSONResponse({'status':'error','reason_code':error.code,'message':error.message},
                            status_code=error.status)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request, error):
        if request.url.path.startswith('/api/real/'):
            return JSONResponse({'status':'error','reason_code':'invalid_photo_request',
                                 'message':'请求格式不正确，请选择照片后重新检测。'},status_code=422)
        return await request_validation_exception_handler(request,error)

    @app.get('/api/real/photos')
    def real_photo_catalog():
        return app.state.real_photos.catalog()

    @app.post('/api/real/analyze')
    def real_photo_analyze(request: dict[str, Any] = Body(...)):
        return app.state.real_photos.analyze(request.get('photo_id'))

    @app.get('/api/real/image/{photo_id}')
    def real_photo_image(photo_id: str):
        return FileResponse(app.state.real_photos.image_path(photo_id),media_type='image/jpeg')

    @app.get('/api/real/report')
    def real_photo_report():
        target = ROOT / 'reports' / 'real_photo_report.html'
        if not target.is_file():
            raise RealPhotoError('real_photo_report_unavailable','真实照片实验报告尚未生成。')
        return FileResponse(target,media_type='text/html')

    @app.get('/real')
    def real_photo_page():
        return FileResponse(ROOT / 'static' / 'real.html')

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
