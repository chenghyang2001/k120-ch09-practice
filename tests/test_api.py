import json
import threading
import time

import pytest

from app import config
from app.errors import AppError
from app.jobs import JobStore
from app.pipeline import runner

URL_A = "https://www.youtube.com/watch?v=aaaaaaaaaaa"
URL_B = "https://youtu.be/bbbbbbbbbbb"


def _done_runner(job, report, cancelled):
    out = config.DATA_DIR / "jobs" / job["id"]
    out.mkdir(parents=True, exist_ok=True)
    (out / "slides.json").write_text('[{"index": 1}]', encoding="utf-8")
    report("output", 100, "完成")
    return {"title": "測試影片", "duration": 12.0, "slide_count": 1, "translate_status": "claude"}


def _gated_runner(gate: threading.Event):
    """在 gate 放行前一直卡住，期間持續檢查取消旗標。"""
    def run(job, report, cancelled):
        report("download", 40, "下載影片")
        while not gate.wait(0.01):
            if cancelled():
                raise runner.JobCancelled()
        return _done_runner(job, report, cancelled)
    return run


@pytest.fixture
def done_runner(monkeypatch):
    monkeypatch.setattr(runner, "run_pipeline", _done_runner)


def _submit(client, url=URL_A, **extra) -> str:
    resp = client.post("/api/jobs", json={"url": url, **extra})
    assert resp.status_code == 201, resp.text
    return resp.json()["job_ids"][0]


def test_submit_until_done(make_client, wait_status, done_runner):
    with make_client() as client:
        job_id = _submit(client)
        job = wait_status(client, job_id, "done")
        assert job["progress"] == 100
        assert job["title"] == "測試影片"
        assert job["quality"] == 720 and job["target_lang"] == "zh-TW"
        assert [j["id"] for j in client.get("/api/jobs").json()] == [job_id]


def test_invalid_url_400(make_client):
    with make_client() as client:
        resp = client.post("/api/jobs", json={"url": "https://example.com/watch?v=aaaaaaaaaaa"})
        assert resp.status_code == 400
        assert resp.json()["error"]["code"] == "invalid_url"


def test_playlist_400(make_client):
    with make_client() as client:
        resp = client.post("/api/jobs", json={"url": "https://www.youtube.com/playlist?list=PL1234567890"})
        assert resp.status_code == 400
        assert resp.json()["error"]["code"] == "playlist_unsupported"


@pytest.mark.parametrize("body", [{"url": URL_A, "quality": 999}, {"url": URL_A, "target_lang": "fr"}])
def test_invalid_params_422(make_client, body):
    with make_client() as client:
        assert client.post("/api/jobs", json=body).status_code == 422


def test_resubmit_done_returns_same_id(make_client, wait_status, done_runner):
    with make_client() as client:
        first = _submit(client)
        wait_status(client, first, "done")
        # 同一部影片不同網址寫法，去重鍵是 video_id
        assert _submit(client, "https://youtu.be/aaaaaaaaaaa") == first
        assert _submit(client, quality=480) != first


def test_resubmit_active_returns_same_id(make_client, wait_status, monkeypatch):
    gate = threading.Event()
    monkeypatch.setattr(runner, "run_pipeline", _gated_runner(gate))
    with make_client() as client:
        first = _submit(client)
        wait_status(client, first, "running")
        assert _submit(client) == first
        gate.set()
        wait_status(client, first, "done")


def test_cancel_queued(make_client, wait_status, monkeypatch):
    gate = threading.Event()
    monkeypatch.setattr(runner, "run_pipeline", _gated_runner(gate))
    with make_client() as client:
        first = _submit(client)
        wait_status(client, first, "running")
        second = _submit(client, URL_B)
        assert client.get(f"/api/jobs/{second}").json()["status"] == "queued"
        assert client.post(f"/api/jobs/{second}/cancel").json()["status"] == "cancelled"
        gate.set()
        wait_status(client, first, "done")
        assert client.get(f"/api/jobs/{second}").json()["status"] == "cancelled"


def test_cancel_running_cleans_tmp(make_client, wait_status, data_dir, fake_stages):
    def slow_download(video_id, height, tmp_dir, duration=None, progress_hook=None):
        tmp_dir.mkdir(parents=True, exist_ok=True)
        (tmp_dir / "video.mp4").write_bytes(b"partial")
        for _ in range(1000):
            progress_hook({"status": "downloading", "downloaded_bytes": 10, "total_bytes": 100})
            time.sleep(0.01)
        raise AssertionError("下載沒有被取消")

    fake_stages(slow_download)
    with make_client() as client:
        job_id = _submit(client)
        job = wait_status(client, job_id, "running")
        while job["stage"] != "download":
            time.sleep(0.01)
            job = client.get(f"/api/jobs/{job_id}").json()
        assert (data_dir / "tmp" / job_id).is_dir()
        client.post(f"/api/jobs/{job_id}/cancel")
        wait_status(client, job_id, "cancelled")
        assert not (data_dir / "tmp" / job_id).exists()
        assert not (data_dir / "jobs" / job_id).exists()


def test_app_error_marks_failed(make_client, wait_status, monkeypatch):
    def failing(job, report, cancelled):
        raise AppError("no_subtitles", "此影片沒有字幕")

    monkeypatch.setattr(runner, "run_pipeline", failing)
    with make_client() as client:
        job = wait_status(client, _submit(client), "failed")
        assert job["error_code"] == "no_subtitles"
        assert job["error"] == "此影片沒有字幕"


def test_unexpected_error_is_internal(make_client, wait_status, monkeypatch):
    def crashing(job, report, cancelled):
        raise RuntimeError("secret-detail")

    monkeypatch.setattr(runner, "run_pipeline", crashing)
    with make_client() as client:
        job_id = _submit(client)
        job = wait_status(client, job_id, "failed")
        assert job["error_code"] == "internal"
        assert job["error"] == "處理時發生未預期錯誤"
        text = client.get(f"/api/jobs/{job_id}").text
        assert "secret-detail" not in text and "Traceback" not in text


def test_server_error_500_hides_traceback(make_client, monkeypatch):
    def broken(self):
        raise RuntimeError("secret-db-detail")

    with make_client() as client:
        # 啟動後才弄壞，lifespan 也會呼叫 list
        monkeypatch.setattr(JobStore, "list", broken)
        resp = client.get("/api/jobs")
        assert resp.status_code == 500
        assert resp.json() == {"error": {"code": "internal", "message": "伺服器內部錯誤"}}
        assert "secret-db-detail" not in resp.text and "Traceback" not in resp.text


def test_unknown_job_404(make_client):
    with make_client() as client:
        resp = client.get("/api/jobs/nope")
        assert resp.status_code == 404
        assert resp.json()["error"]["code"] == "not_found"


def test_delete_running_409_and_done_204(make_client, wait_status, data_dir, monkeypatch):
    gate = threading.Event()
    monkeypatch.setattr(runner, "run_pipeline", _gated_runner(gate))
    with make_client() as client:
        job_id = _submit(client)
        wait_status(client, job_id, "running")
        assert client.delete(f"/api/jobs/{job_id}").status_code == 409
        gate.set()
        wait_status(client, job_id, "done")
        assert (data_dir / "jobs" / job_id / "slides.json").is_file()
        assert client.delete(f"/api/jobs/{job_id}").status_code == 204
        assert not (data_dir / "jobs" / job_id).exists()
        assert client.get(f"/api/jobs/{job_id}").status_code == 404


def test_slides_409_until_done(make_client, wait_status, monkeypatch):
    gate = threading.Event()
    monkeypatch.setattr(runner, "run_pipeline", _gated_runner(gate))
    with make_client() as client:
        job_id = _submit(client)
        wait_status(client, job_id, "running")
        resp = client.get(f"/api/jobs/{job_id}/slides")
        assert resp.status_code == 409
        assert resp.json()["error"]["code"] == "job_not_done"
        gate.set()
        wait_status(client, job_id, "done")
        assert client.get(f"/api/jobs/{job_id}/slides").json() == [{"index": 1}]
        # /media 掛載的是同一個輸出目錄
        assert client.get(f"/media/{job_id}/slides.json").status_code == 200


def test_sse_receives_terminal_event(make_client, monkeypatch):
    def slow(job, report, cancelled):
        for stage, progress in (("metadata", 5), ("subtitles", 15)):
            time.sleep(0.1)
            report(stage, progress, "")
        return _done_runner(job, report, cancelled)

    monkeypatch.setattr(runner, "run_pipeline", slow)
    with make_client() as client:
        job_id = _submit(client)
        with client.stream("GET", f"/api/jobs/{job_id}/events") as resp:
            assert resp.headers["content-type"].startswith("text/event-stream")
            events = [json.loads(line[len("data: "):]) for line in resp.iter_lines() if line.startswith("data: ")]
        assert events[-1]["status"] == "done"
        progresses = [e["progress"] for e in events]
        assert progresses == sorted(progresses)
