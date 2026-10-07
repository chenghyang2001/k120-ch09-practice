"""FastAPI 入口：uv run uvicorn app.main:app --host 127.0.0.1 --port 8000"""

import json
import logging
import shutil
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, FastAPI, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

from app import config
from app.errors import AppError
from app.jobs import JobManager
from app.pipeline import export

logger = logging.getLogger(__name__)
APP_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=APP_DIR / "templates")


class HttpError(AppError):
    """帶 HTTP 狀態碼的 AppError（404、409）。"""

    def __init__(self, status: int, code: str, message: str):
        super().__init__(code, message)
        self.status = status


class JobRequest(BaseModel):
    url: str
    quality: Literal[config.QUALITY_HEIGHTS] = 720
    target_lang: Literal[tuple(config.LANG_NAMES)] = "zh-TW"


router = APIRouter()


def _manager(request: Request) -> JobManager:
    return request.app.state.manager


def _get_job(request: Request, job_id: str) -> dict:
    job = _manager(request).store.get(job_id)
    if job is None:
        raise HttpError(404, "not_found", "找不到這個工作")
    return job


@router.post("/api/jobs", status_code=201)
async def create_job(body: JobRequest, request: Request) -> dict:
    return {"job_ids": _manager(request).submit(body.url, body.quality, body.target_lang)}


@router.get("/api/jobs")
async def list_jobs(request: Request, parent: str | None = None) -> list[dict]:
    store = _manager(request).store
    if parent is None:
        return store.list()
    try:
        return store.list(parent_id=parent)
    except TypeError:
        # M7 之前 JobStore.list 不接受 parent_id，視為沒有子工作
        return []


@router.get("/api/jobs/{job_id}")
async def get_job(job_id: str, request: Request) -> dict:
    return _get_job(request, job_id)


@router.get("/api/jobs/{job_id}/events")
async def job_events(job_id: str, request: Request) -> StreamingResponse:
    _get_job(request, job_id)

    async def stream():
        async for event in _manager(request).subscribe(job_id):
            yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"

    return StreamingResponse(stream(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})


@router.post("/api/jobs/{job_id}/cancel")
async def cancel_job(job_id: str, request: Request) -> dict:
    _get_job(request, job_id)
    return _manager(request).cancel(job_id)


@router.delete("/api/jobs/{job_id}", status_code=204)
async def delete_job(job_id: str, request: Request) -> Response:
    if _get_job(request, job_id)["status"] == "running":
        raise HttpError(409, "job_running", "工作執行中，請先取消")
    _manager(request).delete(job_id)
    return Response(status_code=204)


@router.get("/api/jobs/{job_id}/slides")
async def get_slides(job_id: str, request: Request) -> FileResponse:
    if _get_job(request, job_id)["status"] != "done":
        raise HttpError(409, "job_not_done", "工作尚未完成")
    path = _manager(request).data_dir / "jobs" / job_id / "slides.json"
    if not path.is_file():
        raise HttpError(404, "not_found", "找不到投影片資料")
    return FileResponse(path, media_type="application/json")


EXPORTERS = {
    "html": (export.build_html, "text/html; charset=utf-8", "html"),
    "pdf": (export.build_pdf, "application/pdf", "pdf"),
    "md": (export.build_markdown_zip, "application/zip", "zip"),
}


@router.get("/api/jobs/{job_id}/export")
async def export_job(job_id: str, request: Request, format: str = "html") -> Response:
    job = _get_job(request, job_id)
    if format not in EXPORTERS:
        raise HttpError(400, "bad_format", "匯出格式只支援 html、pdf、md")
    if job["status"] != "done":
        raise HttpError(409, "job_not_done", "工作尚未完成")
    job_dir = _manager(request).data_dir / "jobs" / job_id
    if not (job_dir / "slides.json").is_file():
        raise HttpError(404, "not_found", "找不到投影片資料")
    build, media_type, ext = EXPORTERS[format]
    # 圖片 base64、zip 壓縮、Playwright sync API 都是阻塞操作，放 thread 跑
    content = await run_in_threadpool(build, job, job_dir)
    return Response(content, media_type=media_type,
                    headers={"Content-Disposition": f'attachment; filename="{job["video_id"]}.{ext}"'})


@router.get("/", include_in_schema=False)
async def index_page(request: Request) -> Response:
    return templates.TemplateResponse(request, "index.html", {
        "qualities": config.QUALITY_HEIGHTS, "langs": config.LANG_NAMES,
    })


@router.get("/read/{job_id}", include_in_schema=False)
async def reader_page(job_id: str, request: Request) -> Response:
    job = _get_job(request, job_id)
    return templates.TemplateResponse(request, "reader.html", {"job": job})


async def _app_error_handler(request: Request, exc: AppError) -> JSONResponse:
    return JSONResponse({"error": {"code": exc.code, "message": exc.message}},
                        status_code=getattr(exc, "status", 400))


async def _internal_error_handler(request: Request, exc: Exception) -> JSONResponse:
    logger.exception("未預期的伺服器錯誤：%s %s", request.method, request.url.path)
    return JSONResponse({"error": {"code": "internal", "message": "伺服器內部錯誤"}}, status_code=500)


def _check_tools() -> None:
    for tool in ("ffmpeg", "ffprobe"):
        if shutil.which(tool) is None:
            logger.error("找不到 %s，截圖功能無法使用，請安裝並加入 PATH", tool)


def create_app() -> FastAPI:
    """建立 app；建立當下才讀 DATA_DIR，測試換成 tmp_path 後再呼叫即可。"""
    data_dir = config.DATA_DIR
    manager = JobManager(data_dir)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        _check_tools()
        (data_dir / "jobs").mkdir(parents=True, exist_ok=True)
        await manager.start()
        try:
            yield
        finally:
            await manager.stop()

    app = FastAPI(lifespan=lifespan)
    app.state.manager = manager
    app.include_router(router)
    app.add_exception_handler(AppError, _app_error_handler)
    app.add_exception_handler(Exception, _internal_error_handler)
    # check_dir=False：目錄在 lifespan 才建立，import 時不在磁碟留東西
    app.mount("/media", StaticFiles(directory=data_dir / "jobs", check_dir=False), name="media")
    app.mount("/static", StaticFiles(directory=APP_DIR / "static"), name="static")
    return app


app = create_app()
