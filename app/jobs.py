"""工作佇列：SQLite 狀態、單一 worker、取消旗標、SSE 訂閱。"""

import asyncio
import logging
import shutil
import sqlite3
import threading
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path

from app.errors import AppError
from app.pipeline import runner
from app.pipeline.url import parse_youtube_url

logger = logging.getLogger(__name__)

TERMINAL = ("done", "failed", "cancelled", "interrupted")
COLUMNS = ("id", "video_id", "url", "quality", "target_lang", "status", "stage", "progress", "message",
           "error_code", "error", "title", "duration", "slide_count", "translate_status",
           "created_at", "finished_at")
SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY, video_id TEXT NOT NULL, url TEXT NOT NULL, quality INTEGER NOT NULL,
    target_lang TEXT NOT NULL, status TEXT NOT NULL, stage TEXT, progress INTEGER NOT NULL DEFAULT 0,
    message TEXT, error_code TEXT, error TEXT, title TEXT, duration REAL, slide_count INTEGER,
    translate_status TEXT, created_at TEXT NOT NULL, finished_at TEXT
)
"""


def _now() -> str:
    return datetime.now(UTC).isoformat()


class JobStore:
    def __init__(self, db_path: Path):
        self.db_path = db_path
        self._lock = threading.Lock()

    def _execute(self, sql: str, params: tuple = ()) -> list[dict]:
        with self._lock:
            conn = sqlite3.connect(self.db_path, check_same_thread=False)
            try:
                conn.row_factory = sqlite3.Row
                rows = [dict(r) for r in conn.execute(sql, params).fetchall()]
                conn.commit()
                return rows
            finally:
                conn.close()

    def init(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._execute(SCHEMA)

    def create(self, video_id: str, url: str, quality: int, target_lang: str) -> dict:
        job_id = uuid.uuid4().hex[:12]
        self._execute(
            "INSERT INTO jobs (id, video_id, url, quality, target_lang, status, progress, created_at)"
            " VALUES (?, ?, ?, ?, ?, 'queued', 0, ?)",
            (job_id, video_id, url, quality, target_lang, _now()),
        )
        return self.get(job_id)

    def get(self, job_id: str) -> dict | None:
        rows = self._execute("SELECT * FROM jobs WHERE id = ?", (job_id,))
        return rows[0] if rows else None

    def list(self) -> list[dict]:
        # rowid 當次要排序：同一微秒建立的工作順序才穩定
        return self._execute("SELECT * FROM jobs ORDER BY created_at DESC, rowid DESC")

    def update(self, job_id: str, **fields) -> None:
        unknown = set(fields) - set(COLUMNS)
        if unknown:
            raise ValueError(f"未知欄位：{unknown}")
        assignments = ", ".join(f"{k} = ?" for k in fields)
        self._execute(f"UPDATE jobs SET {assignments} WHERE id = ?", (*fields.values(), job_id))

    def delete(self, job_id: str) -> None:
        self._execute("DELETE FROM jobs WHERE id = ?", (job_id,))

    def find(self, video_id: str, quality: int, target_lang: str, statuses: tuple[str, ...]) -> dict | None:
        marks = ", ".join("?" * len(statuses))
        rows = self._execute(
            f"SELECT * FROM jobs WHERE video_id = ? AND quality = ? AND target_lang = ? AND status IN ({marks})"
            " ORDER BY created_at DESC LIMIT 1",
            (video_id, quality, target_lang, *statuses),
        )
        return rows[0] if rows else None

    def find_done(self, video_id: str, quality: int, target_lang: str) -> dict | None:
        return self.find(video_id, quality, target_lang, ("done",))


class JobManager:
    def __init__(self, data_dir: Path):
        self.data_dir = data_dir
        self.store = JobStore(data_dir / "app.db")
        self._queue: asyncio.Queue[str] | None = None
        self._cancel_flags: dict[str, threading.Event] = {}
        self._subscribers: dict[str, set[asyncio.Queue]] = {}
        self._loop: asyncio.AbstractEventLoop | None = None
        self._worker: asyncio.Task | None = None

    async def start(self) -> None:
        self._loop = asyncio.get_running_loop()
        self._queue = asyncio.Queue()
        self.store.init()
        # 單一 worker，啟動當下不可能有工作在跑；殘留的 running 都是上次程序中斷留下的
        for job in reversed(self.store.list()):
            if job["status"] == "running":
                self.store.update(job["id"], status="interrupted", finished_at=_now())
            elif job["status"] == "queued":
                self._queue.put_nowait(job["id"])
        shutil.rmtree(self.data_dir / "tmp", ignore_errors=True)
        self._worker = asyncio.create_task(self._work())

    async def stop(self) -> None:
        # thread 無法強制終止，先設取消旗標讓 pipeline 自己停
        for flag in self._cancel_flags.values():
            flag.set()
        if self._worker is not None:
            self._worker.cancel()
            try:
                await self._worker
            except asyncio.CancelledError:
                pass

    def submit(self, url: str, quality: int, target_lang: str) -> list[str]:
        parsed = parse_youtube_url(url)
        if parsed.kind == "playlist":
            raise AppError("playlist_unsupported", "播放清單將在 M7 支援")
        existing = self.store.find(parsed.video_id, quality, target_lang, ("done", "queued", "running"))
        if existing is not None:
            return [existing["id"]]
        job = self.store.create(parsed.video_id, url, quality, target_lang)
        self._queue.put_nowait(job["id"])
        return [job["id"]]

    def cancel(self, job_id: str) -> dict | None:
        job = self.store.get(job_id)
        if job is None:
            return None
        if job["status"] == "queued":
            self.store.update(job_id, status="cancelled", finished_at=_now())
            self._publish(job_id)
        elif job["status"] == "running" and job_id in self._cancel_flags:
            self._cancel_flags[job_id].set()
        return self.store.get(job_id)

    def delete(self, job_id: str) -> None:
        """呼叫端需先確認不是 running；queued 的被刪掉後 worker 取出時會跳過。"""
        self.store.delete(job_id)
        shutil.rmtree(self.data_dir / "jobs" / job_id, ignore_errors=True)

    async def subscribe(self, job_id: str) -> AsyncIterator[dict]:
        """先送目前狀態，再逐一推送事件，直到終態。"""
        queue: asyncio.Queue[dict] = asyncio.Queue()
        # 先登記再讀狀態，避免讀完到登記之間的事件漏掉
        self._subscribers.setdefault(job_id, set()).add(queue)
        try:
            job = self.store.get(job_id)
            if job is None:
                return
            yield job
            while job["status"] not in TERMINAL:
                job = await queue.get()
                yield job
        finally:
            subs = self._subscribers.get(job_id)
            if subs is not None:
                subs.discard(queue)
                if not subs:
                    del self._subscribers[job_id]

    def _publish(self, job_id: str) -> None:
        """只能在事件迴圈 thread 呼叫。"""
        subs = self._subscribers.get(job_id)
        if not subs:
            return
        job = self.store.get(job_id)
        if job is None:
            return
        for queue in subs:
            queue.put_nowait(job)

    async def _work(self) -> None:
        while True:
            job_id = await self._queue.get()
            job = self.store.get(job_id)
            # 排隊期間被取消或刪除
            if job is None or job["status"] != "queued":
                continue
            await self._run_one(job)

    async def _run_one(self, job: dict) -> None:
        job_id = job["id"]
        flag = self._cancel_flags[job_id] = threading.Event()
        self.store.update(job_id, status="running", progress=0)
        self._publish(job_id)

        def report(stage: str, progress: int, message: str) -> None:
            self.store.update(job_id, stage=stage, progress=progress, message=message)
            try:
                self._loop.call_soon_threadsafe(self._publish, job_id)
            except RuntimeError:
                pass  # 事件迴圈已關閉（伺服器正在停止），沒有訂閱者可送

        try:
            result = await asyncio.to_thread(runner.run_pipeline, job, report, flag.is_set)
            self.store.update(job_id, status="done", progress=100, finished_at=_now(), **result)
        except runner.JobCancelled:
            self.store.update(job_id, status="cancelled", finished_at=_now())
            shutil.rmtree(self.data_dir / "jobs" / job_id, ignore_errors=True)
        except AppError as e:
            self.store.update(job_id, status="failed", error_code=e.code, error=e.message, finished_at=_now())
        except Exception:
            logger.exception("工作 %s 發生未預期錯誤", job_id)
            self.store.update(job_id, status="failed", error_code="internal",
                              error="處理時發生未預期錯誤", finished_at=_now())
        finally:
            self._cancel_flags.pop(job_id, None)
        self._publish(job_id)
