"""工作佇列：SQLite 狀態、單一 worker、取消旗標、SSE 訂閱、播放清單群組。"""

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
from app.pipeline import metadata, runner
from app.pipeline.url import parse_youtube_url

logger = logging.getLogger(__name__)

TERMINAL = ("done", "failed", "cancelled", "interrupted")
COLUMNS = ("id", "video_id", "url", "quality", "target_lang", "status", "stage", "progress", "message",
           "error_code", "error", "title", "duration", "slide_count", "translate_status",
           "created_at", "finished_at", "parent_id", "kind")
# 播放清單那一列沒有 video_id，所以 video_id 可為 NULL
SCHEMA = """
CREATE TABLE IF NOT EXISTS {table} (
    id TEXT PRIMARY KEY, video_id TEXT, url TEXT NOT NULL, quality INTEGER NOT NULL,
    target_lang TEXT NOT NULL, status TEXT NOT NULL, stage TEXT, progress INTEGER NOT NULL DEFAULT 0,
    message TEXT, error_code TEXT, error TEXT, title TEXT, duration REAL, slide_count INTEGER,
    translate_status TEXT, created_at TEXT NOT NULL, finished_at TEXT,
    parent_id TEXT, kind TEXT DEFAULT 'video'
)
"""
# 只附在播放清單那一列的統計；failed 包含 cancelled、interrupted
CHILD_COUNTS = """
SELECT parent_id, COUNT(*) AS children_total,
       SUM(status = 'done') AS children_done,
       SUM(status IN ('failed', 'cancelled', 'interrupted')) AS children_failed
FROM jobs WHERE parent_id IS NOT NULL GROUP BY parent_id
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
        self._execute(SCHEMA.format(table="jobs"))
        self._migrate()

    def _migrate(self) -> None:
        """M4 建的舊資料庫：補 parent_id、kind 欄位，並拿掉 video_id 的 NOT NULL。"""
        info = {c["name"]: c for c in self._execute("PRAGMA table_info(jobs)")}
        if "parent_id" not in info:
            self._execute("ALTER TABLE jobs ADD COLUMN parent_id TEXT")
        if "kind" not in info:
            self._execute("ALTER TABLE jobs ADD COLUMN kind TEXT DEFAULT 'video'")
        if not info["video_id"]["notnull"]:
            return
        # SQLite 不能用 ALTER 拿掉 NOT NULL，只能在同一個交易裡重建資料表
        columns = ", ".join(COLUMNS)
        with self._lock:
            conn = sqlite3.connect(self.db_path)
            try:
                with conn:
                    conn.execute(SCHEMA.format(table="jobs_new"))
                    conn.execute(f"INSERT INTO jobs_new ({columns}) SELECT {columns} FROM jobs")
                    conn.execute("DROP TABLE jobs")
                    conn.execute("ALTER TABLE jobs_new RENAME TO jobs")
            finally:
                conn.close()

    def create(self, video_id: str | None, url: str, quality: int, target_lang: str,
               parent_id: str | None = None, kind: str = "video", status: str = "queued") -> dict:
        job_id = uuid.uuid4().hex[:12]
        self._execute(
            "INSERT INTO jobs (id, video_id, url, quality, target_lang, status, progress, created_at,"
            " parent_id, kind) VALUES (?, ?, ?, ?, ?, ?, 0, ?, ?, ?)",
            (job_id, video_id, url, quality, target_lang, status, _now(), parent_id, kind),
        )
        return self.get(job_id)

    def get(self, job_id: str) -> dict | None:
        rows = self._execute("SELECT * FROM jobs WHERE id = ?", (job_id,))
        return rows[0] if rows else None

    def all(self) -> list[dict]:
        """包含播放清單底下的子工作，給啟動復原用。"""
        # rowid 當次要排序：同一微秒建立的工作順序才穩定
        return self._execute("SELECT * FROM jobs ORDER BY created_at DESC, rowid DESC")

    # 要定義在 list 方法之前：類別內 list 之後的型別註記會指到方法而不是內建 list
    def child_ids(self, parent_id: str) -> list[str]:
        return [r["id"] for r in self._execute("SELECT id FROM jobs WHERE parent_id = ?", (parent_id,))]

    def list(self, parent_id: str | None = None) -> list[dict]:
        """不給 parent_id：最上層列表（播放清單附 children_*）；給了：該清單的子工作，依清單順序。"""
        if parent_id is not None:
            return self._execute("SELECT * FROM jobs WHERE parent_id = ? ORDER BY rowid", (parent_id,))
        rows = self._execute("SELECT * FROM jobs WHERE parent_id IS NULL ORDER BY created_at DESC, rowid DESC")
        counts = {r["parent_id"]: r for r in self._execute(CHILD_COUNTS)}
        for row in rows:
            if row["kind"] == "playlist":
                c = counts.get(row["id"]) or {}
                for key in ("children_total", "children_done", "children_failed"):
                    row[key] = c.get(key) or 0
        return rows

    def update(self, job_id: str, **fields) -> None:
        unknown = set(fields) - set(COLUMNS)
        if unknown:
            raise ValueError(f"未知欄位：{unknown}")
        assignments = ", ".join(f"{k} = ?" for k in fields)
        self._execute(f"UPDATE jobs SET {assignments} WHERE id = ?", (*fields.values(), job_id))

    def delete(self, job_id: str) -> None:
        """播放清單會連帶刪掉子工作的資料列；輸出目錄由 JobManager.delete 處理。"""
        self._execute("DELETE FROM jobs WHERE id = ? OR parent_id = ?", (job_id, job_id))

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
        jobs = list(reversed(self.store.all()))
        # 單一 worker，啟動當下不可能有工作在跑；殘留的 running 都是上次程序中斷留下的
        for job in jobs:
            if job["kind"] == "playlist":
                continue
            if job["status"] == "running":
                self.store.update(job["id"], status="interrupted", finished_at=_now())
            elif job["status"] == "queued":
                self._queue.put_nowait(job["id"])
        # 子工作全被標成 interrupted 的清單要在這裡收尾，否則會永遠停在 running
        for job in jobs:
            if job["kind"] == "playlist" and job["status"] == "running":
                self._settle_parent(job["id"])
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
            return self._submit_playlist(url, parsed.playlist_id, quality, target_lang)
        existing = self.store.find(parsed.video_id, quality, target_lang, ("done", "queued", "running"))
        if existing is not None:
            return [existing["id"]]
        job = self.store.create(parsed.video_id, url, quality, target_lang)
        self._queue.put_nowait(job["id"])
        return [job["id"]]

    def _submit_playlist(self, url: str, playlist_id: str, quality: int, target_lang: str) -> list[str]:
        """回傳 [清單 id, *子工作 id]。"""
        title, video_ids = metadata.list_playlist(playlist_id)
        if not video_ids:
            raise AppError("empty_playlist", "播放清單沒有可處理的影片")
        parent = self.store.create(None, url, quality, target_lang, kind="playlist", status="running")
        self.store.update(parent["id"], title=title)
        child_ids, queued = [], []
        for video_id in video_ids:
            child_url = f"https://www.youtube.com/watch?v={video_id}"
            child = self._reuse_done(video_id, child_url, quality, target_lang, parent["id"])
            if child is None:
                child = self.store.create(video_id, child_url, quality, target_lang, parent_id=parent["id"])
                queued.append(child["id"])
            child_ids.append(child["id"])
        # 全部建好才排進佇列，避免第一支做完就以為整份清單都結束了
        for job_id in queued:
            self._queue.put_nowait(job_id)
        self._settle_parent(parent["id"])
        return [parent["id"], *child_ids]

    def _reuse_done(self, video_id: str, url: str, quality: int, target_lang: str,
                    parent_id: str) -> dict | None:
        """已做完的影片：建一列 done 子工作並複製輸出，不重跑 pipeline。

        複製而非共用目錄：刪除播放清單會刪子工作目錄，不能連帶刪掉原本那個工作的檔案。
        """
        done = self.store.find_done(video_id, quality, target_lang)
        if done is None or not (self.data_dir / "jobs" / done["id"]).is_dir():
            return None
        child = self.store.create(video_id, url, quality, target_lang, parent_id=parent_id, status="done")
        try:
            shutil.copytree(self.data_dir / "jobs" / done["id"], self.data_dir / "jobs" / child["id"])
        except OSError:
            logger.exception("複製工作 %s 的輸出失敗，改為重新處理", done["id"])
            shutil.rmtree(self.data_dir / "jobs" / child["id"], ignore_errors=True)
            self.store.update(child["id"], status="queued")
            self._queue.put_nowait(child["id"])
            return child
        self.store.update(child["id"], progress=100, finished_at=_now(), title=done["title"],
                          duration=done["duration"], slide_count=done["slide_count"],
                          translate_status=done["translate_status"])
        return child

    def _settle_parent(self, parent_id: str | None) -> None:
        """所有子工作都進終態時，把清單標成 done（至少一支成功）或 failed（全部失敗）。"""
        if parent_id is None:
            return
        parent = self.store.get(parent_id)
        if parent is None or parent["status"] in TERMINAL:
            return
        statuses = [c["status"] for c in self.store.list(parent_id)]
        if not statuses or any(s not in TERMINAL for s in statuses):
            return
        if "done" in statuses:
            status = "done"
        elif all(s == "cancelled" for s in statuses):
            status = "cancelled"
        else:
            status = "failed"
        self.store.update(parent_id, status=status, progress=100, finished_at=_now())
        self._publish(parent_id)

    def cancel(self, job_id: str) -> dict | None:
        job = self.store.get(job_id)
        if job is None:
            return None
        if job["kind"] == "playlist":
            for child_id in self.store.child_ids(job_id):
                self.cancel(child_id)
        elif job["status"] == "queued":
            self.store.update(job_id, status="cancelled", finished_at=_now())
            self._publish(job_id)
            self._settle_parent(job["parent_id"])
        elif job["status"] == "running" and job_id in self._cancel_flags:
            self._cancel_flags[job_id].set()
        return self.store.get(job_id)

    def delete(self, job_id: str) -> None:
        """呼叫端需先確認不是 running；queued 的被刪掉後 worker 取出時會跳過。播放清單連子工作目錄一起刪。"""
        targets = [job_id, *self.store.child_ids(job_id)]
        self.store.delete(job_id)
        for target in targets:
            shutil.rmtree(self.data_dir / "jobs" / target, ignore_errors=True)

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
        self._settle_parent(job["parent_id"])
