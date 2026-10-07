"""M7 播放清單測試：list_playlist 用假 YoutubeDL，submit 以 monkeypatch 取代 list_playlist，不連網。"""

import sqlite3
import threading
from typing import ClassVar

import pytest

from app import config
from app.errors import AppError
from app.jobs import JobStore
from app.pipeline import metadata, runner

PLAYLIST_URL = "https://www.youtube.com/playlist?list=PL1234567890"
VIDS = ["aaaaaaaaaaa", "bbbbbbbbbbb", "ccccccccccc"]


def _done_runner(job, report, cancelled):
    out = config.DATA_DIR / "jobs" / job["id"]
    out.mkdir(parents=True, exist_ok=True)
    (out / "slides.json").write_text("[]", encoding="utf-8")
    return {"title": f"影片 {job['video_id']}", "duration": 1.0, "slide_count": 0, "translate_status": "claude"}


def _runner_failing_for(*bad_ids):
    def run(job, report, cancelled):
        if job["video_id"] in bad_ids:
            raise AppError("no_speech", "影片中沒有可辨識的語音")
        return _done_runner(job, report, cancelled)
    return run


@pytest.fixture
def fake_playlist(monkeypatch):
    state = {"title": "測試清單", "ids": list(VIDS)}
    monkeypatch.setattr(metadata, "list_playlist", lambda pid: (state["title"], state["ids"]))
    return state


def _submit(client) -> list[str]:
    resp = client.post("/api/jobs", json={"url": PLAYLIST_URL})
    assert resp.status_code == 201, resp.text
    return resp.json()["job_ids"]


def _store(client) -> JobStore:
    return client.app.state.manager.store


# ---------- list_playlist ----------

class FakeYDL:
    opts: ClassVar[dict] = {}

    def __init__(self, opts):
        FakeYDL.opts = opts

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def extract_info(self, url, download):
        assert url.endswith("list=PLx") and download is False
        return {"title": "我的清單", "entries": [
            {"id": "aaaaaaaaaaa", "title": "正常"},
            {"id": "bbbbbbbbbbb", "title": "[Private video]"},
            {"id": "ccccccccccc", "title": "[Deleted video]"},
            {"id": "too-short", "title": "id 不合法"},
            None,
            {"id": "ddddddddddd", "title": "私人", "availability": "private"},
            {"id": "eeeeeeeeeee", "title": None},
        ]}


def test_list_playlist_filters_invalid(monkeypatch):
    monkeypatch.setattr(metadata.yt_dlp, "YoutubeDL", FakeYDL)
    title, ids = metadata.list_playlist("PLx")
    assert title == "我的清單"
    assert ids == ["aaaaaaaaaaa", "eeeeeeeeeee"]
    assert FakeYDL.opts["extract_flat"] == "in_playlist"


def test_list_playlist_error_mapping(monkeypatch):
    class Broken(FakeYDL):
        def extract_info(self, url, download):
            raise metadata.yt_dlp.utils.DownloadError("ERROR: This video is private video")

    monkeypatch.setattr(metadata.yt_dlp, "YoutubeDL", Broken)
    with pytest.raises(AppError) as exc:
        metadata.list_playlist("PLx")
    assert exc.value.code == "private_video"


# ---------- submit／聚合 ----------

def test_submit_creates_parent_and_children(make_client, wait_status, fake_playlist, monkeypatch):
    monkeypatch.setattr(runner, "run_pipeline", _done_runner)
    with make_client() as client:
        parent_id, *child_ids = _submit(client)
        assert len(child_ids) == 3
        parent = wait_status(client, parent_id, "done")
        assert (parent["kind"], parent["video_id"], parent["title"]) == ("playlist", None, "測試清單")
        children = _store(client).list(parent_id)
        assert [c["id"] for c in children] == child_ids
        assert [c["video_id"] for c in children] == VIDS
        assert all(c["parent_id"] == parent_id and c["status"] == "done" for c in children)


def test_parent_done_when_some_fail(make_client, wait_status, fake_playlist, monkeypatch):
    monkeypatch.setattr(runner, "run_pipeline", _runner_failing_for("bbbbbbbbbbb"))
    with make_client() as client:
        parent_id, *_ = _submit(client)
        wait_status(client, parent_id, "done")
        top = client.get("/api/jobs").json()
        # 最上層只有清單本身，子工作不出現
        assert [j["id"] for j in top] == [parent_id]
        assert (top[0]["children_total"], top[0]["children_done"], top[0]["children_failed"]) == (3, 2, 1)


def test_parent_failed_when_all_fail(make_client, wait_status, fake_playlist, monkeypatch):
    monkeypatch.setattr(runner, "run_pipeline", _runner_failing_for(*VIDS))
    with make_client() as client:
        parent_id, *_ = _submit(client)
        wait_status(client, parent_id, "failed")


def test_cancel_cascades_to_children(make_client, wait_status, fake_playlist, monkeypatch):
    gate = threading.Event()

    def gated(job, report, cancelled):
        while not gate.wait(0.01):
            if cancelled():
                raise runner.JobCancelled()
        return _done_runner(job, report, cancelled)

    monkeypatch.setattr(runner, "run_pipeline", gated)
    with make_client() as client:
        parent_id, first, *rest = _submit(client)
        wait_status(client, first, "running")
        client.post(f"/api/jobs/{parent_id}/cancel")
        for child_id in (first, *rest):
            wait_status(client, child_id, "cancelled")
        assert wait_status(client, parent_id, "cancelled")["finished_at"] is not None


def test_empty_playlist_400(make_client, fake_playlist):
    fake_playlist["ids"] = []
    with make_client() as client:
        resp = client.post("/api/jobs", json={"url": PLAYLIST_URL})
        assert resp.status_code == 400
        assert resp.json()["error"]["code"] == "empty_playlist"
        assert client.get("/api/jobs").json() == []


def test_done_video_reused_not_rerun(make_client, wait_status, fake_playlist, data_dir, monkeypatch):
    ran = []

    def recording(job, report, cancelled):
        ran.append(job["video_id"])
        return _done_runner(job, report, cancelled)

    monkeypatch.setattr(runner, "run_pipeline", recording)
    with make_client() as client:
        single = client.post("/api/jobs", json={"url": f"https://youtu.be/{VIDS[0]}"}).json()["job_ids"][0]
        wait_status(client, single, "done")
        parent_id, reused, *_ = _submit(client)
        wait_status(client, parent_id, "done")
        assert ran.count(VIDS[0]) == 1
        assert reused != single
        assert (data_dir / "jobs" / reused / "slides.json").is_file()
        # 刪掉清單只刪自己的子工作，原本那個單支工作的檔案要留著
        assert client.delete(f"/api/jobs/{parent_id}").status_code == 204
        assert _store(client).list(parent_id) == []
        assert not (data_dir / "jobs" / reused).exists()
        assert (data_dir / "jobs" / single / "slides.json").is_file()


# ---------- schema 遷移 ----------

OLD_SCHEMA = """
CREATE TABLE jobs (
    id TEXT PRIMARY KEY, video_id TEXT NOT NULL, url TEXT NOT NULL, quality INTEGER NOT NULL,
    target_lang TEXT NOT NULL, status TEXT NOT NULL, stage TEXT, progress INTEGER NOT NULL DEFAULT 0,
    message TEXT, error_code TEXT, error TEXT, title TEXT, duration REAL, slide_count INTEGER,
    translate_status TEXT, created_at TEXT NOT NULL, finished_at TEXT
)
"""


def test_old_database_migrated(data_dir):
    data_dir.mkdir(parents=True)
    db = data_dir / "app.db"
    conn = sqlite3.connect(db)
    conn.execute(OLD_SCHEMA)
    conn.execute("INSERT INTO jobs (id, video_id, url, quality, target_lang, status, created_at)"
                 " VALUES ('old1', 'aaaaaaaaaaa', 'u', 720, 'zh-TW', 'done', '2026-01-01')")
    conn.commit()
    conn.close()

    store = JobStore(db)
    store.init()
    store.init()  # 第二次啟動不可重複遷移
    old = store.get("old1")
    assert (old["kind"], old["parent_id"], old["status"]) == ("video", None, "done")
    parent = store.create(None, PLAYLIST_URL, 720, "zh-TW", kind="playlist", status="running")
    assert store.get(parent["id"])["video_id"] is None
