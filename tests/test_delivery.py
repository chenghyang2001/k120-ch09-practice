"""成果傳送：Drive（rclone）、Gmail（SMTP）、deliver 整合與 API；全部 monkeypatch，不連網。"""

import json
import shutil
import smtplib
import sqlite3
import subprocess
import time
from pathlib import Path
from typing import ClassVar

import pytest

from app import config
from app.errors import AppError
from app.pipeline import delivery, export, metadata
from tests.test_export import make_done_job


class FakeProc:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode, self.stdout, self.stderr = returncode, stdout, stderr


@pytest.fixture
def drive_ready(monkeypatch):
    """rclone 存在且有 gdrive remote；記錄每次 subprocess.run 的指令。"""
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append((cmd, kwargs))
        if cmd[1] == "listremotes":
            return FakeProc(stdout="gdrive:\nother:\n")
        return FakeProc()

    monkeypatch.setattr(shutil, "which", lambda name: "/usr/bin/rclone")
    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr(config, "DRIVE_REMOTE", "gdrive")
    monkeypatch.setattr(config, "DRIVE_DIR", "YouTube投影片")
    monkeypatch.setattr(config, "DRIVE_ROOT_FOLDER_ID", None)
    return calls


@pytest.fixture
def gmail_ready(monkeypatch):
    monkeypatch.setattr(config, "GMAIL_USER", "me@gmail.com")
    monkeypatch.setattr(config, "GMAIL_APP_PASSWORD", "app-password")
    monkeypatch.setattr(config, "MAIL_TO", "me@gmail.com")


class FakeSMTP:
    """取代 smtplib.SMTP_SSL；sent 收集送出的信，login_error 可模擬認證失敗。"""

    sent: ClassVar[list] = []
    login_error: ClassVar[Exception | None] = None

    def __init__(self, host, port, timeout=None):
        assert (host, port, timeout) == ("smtp.gmail.com", 465, 60)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def login(self, user, password):
        if FakeSMTP.login_error is not None:
            raise FakeSMTP.login_error

    def send_message(self, msg):
        FakeSMTP.sent.append(msg)


@pytest.fixture
def fake_smtp(monkeypatch):
    FakeSMTP.sent = []
    FakeSMTP.login_error = None
    monkeypatch.setattr(smtplib, "SMTP_SSL", FakeSMTP)
    return FakeSMTP


def _file(tmp_path, name, size):
    path = tmp_path / name
    path.write_bytes(b"x" * size)
    return path


# ---------- drive_status ----------
def test_drive_status_no_rclone(monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda name: None)
    ok, reason = delivery.drive_status()
    assert not ok and "找不到 rclone" in reason


def test_drive_status_no_remote(monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda name: "/usr/bin/rclone")
    monkeypatch.setattr(subprocess, "run", lambda cmd, **kw: FakeProc(stdout="onedrive:\n"))
    monkeypatch.setattr(config, "DRIVE_REMOTE", "gdrive")
    ok, reason = delivery.drive_status()
    assert not ok and "gdrive" in reason and "rclone config" in reason


def test_drive_status_ok(drive_ready):
    assert delivery.drive_status() == (True, "")


# ---------- upload_to_drive ----------
def test_upload_to_drive_command(drive_ready, monkeypatch, tmp_path):
    monkeypatch.setattr(config, "DRIVE_ROOT_FOLDER_ID", "FOLDER123")
    files = [_file(tmp_path, "a.html", 3), _file(tmp_path, "a.pdf", 3)]
    dest = delivery.upload_to_drive(files, "標題_vid")
    assert dest == "gdrive:YouTube投影片/標題_vid"
    copies = [(cmd, kw) for cmd, kw in drive_ready if cmd[1] == "copy"]
    assert [c[0] for c in copies] == [
        ["rclone", "copy", str(f), dest, "--drive-root-folder-id", "FOLDER123"] for f in files]
    assert all(kw["encoding"] == "utf-8" and kw["timeout"] == 300 for _, kw in copies)


def test_upload_to_drive_failed(drive_ready, monkeypatch, tmp_path, caplog):
    def fake_run(cmd, **kwargs):
        if cmd[1] == "listremotes":
            return FakeProc(stdout="gdrive:\n")
        return FakeProc(returncode=1, stderr="quota exceeded")

    monkeypatch.setattr(subprocess, "run", fake_run)
    with pytest.raises(AppError) as exc:
        delivery.upload_to_drive([_file(tmp_path, "a.html", 3)], "f")
    assert exc.value.code == "drive_failed"
    assert "quota exceeded" in caplog.text


def test_upload_to_drive_unavailable(monkeypatch, tmp_path):
    monkeypatch.setattr(shutil, "which", lambda name: None)
    with pytest.raises(AppError) as exc:
        delivery.upload_to_drive([_file(tmp_path, "a.html", 3)], "f")
    assert exc.value.code == "drive_unavailable"


# ---------- send_gmail ----------
def test_send_gmail_message(gmail_ready, fake_smtp, tmp_path):
    files = [_file(tmp_path, "簡報.html", 10), _file(tmp_path, "簡報.pdf", 10)]
    attached = delivery.send_gmail(files, "YouTube 投影片：中文標題", "內文 中文")
    assert attached == ["簡報.html", "簡報.pdf"]
    msg = fake_smtp.sent[0]
    assert msg["Subject"] == "YouTube 投影片：中文標題"
    assert msg["To"] == "me@gmail.com"
    assert [p.get_filename() for p in msg.iter_attachments()] == ["簡報.html", "簡報.pdf"]
    assert "內文 中文" in msg.get_body().get_content()


def test_send_gmail_skips_oversized(gmail_ready, fake_smtp, monkeypatch, tmp_path):
    monkeypatch.setattr(config, "MAIL_MAX_BYTES", 100)
    files = [_file(tmp_path, "a.html", 60), _file(tmp_path, "a.pdf", 60)]
    assert delivery.send_gmail(files, "s", "b") == ["a.html"]
    assert len(list(fake_smtp.sent[0].iter_attachments())) == 1


def test_send_gmail_all_too_large(gmail_ready, fake_smtp, monkeypatch, tmp_path):
    monkeypatch.setattr(config, "MAIL_MAX_BYTES", 10)
    with pytest.raises(AppError) as exc:
        delivery.send_gmail([_file(tmp_path, "a.html", 60)], "s", "b")
    assert exc.value.code == "mail_too_large"
    assert fake_smtp.sent == []


def test_send_gmail_auth_failed(gmail_ready, fake_smtp, tmp_path):
    fake_smtp.login_error = smtplib.SMTPAuthenticationError(535, b"bad credentials")
    with pytest.raises(AppError) as exc:
        delivery.send_gmail([_file(tmp_path, "a.html", 3)], "s", "b")
    assert exc.value.code == "gmail_auth_failed"


def test_send_gmail_unavailable(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "GMAIL_USER", None)
    with pytest.raises(AppError) as exc:
        delivery.send_gmail([_file(tmp_path, "a.html", 3)], "s", "b")
    assert exc.value.code == "gmail_unavailable"


def test_safe_name():
    assert delivery.safe_name('a/b:c*d?"e<f>g|h\\i', "vid") == "abcdefghi_vid"
    assert delivery.safe_name("長" * 80, "vid") == "長" * 60 + "_vid"
    assert delivery.safe_name("", "vid") == "vid"


# ---------- deliver ----------
@pytest.fixture
def client(make_client):
    with make_client() as c:
        yield c


def test_deliver_one_target_fails(client, data_dir, drive_ready, gmail_ready, fake_smtp, monkeypatch):
    monkeypatch.setattr(export, "build_pdf", lambda job, job_dir: b"%PDF-fake")
    fake_smtp.login_error = smtplib.SMTPAuthenticationError(535, b"bad")
    job = make_done_job(client, data_dir)
    result = delivery.deliver(job, ["drive", "gmail"])
    assert result["drive"] == "已上傳到 gdrive:YouTube投影片/測試影片 b_abcdefghijk"
    assert result["gmail"] == "失敗：Gmail 登入失敗，請確認應用程式密碼"
    copied = [cmd[2] for cmd, _ in drive_ready if cmd[1] == "copy"]
    assert [Path(c).name for c in copied] == [
        "測試影片 b_abcdefghijk.html", "測試影片 b_abcdefghijk.pdf"]
    assert not (data_dir / "tmp" / f"deliver_{job['id']}").exists()


def test_deliver_pdf_failure_html_only(client, data_dir, gmail_ready, fake_smtp, monkeypatch):
    def no_pdf(job, job_dir):
        raise export.PdfUnavailable()

    monkeypatch.setattr(export, "build_pdf", no_pdf)
    job = make_done_job(client, data_dir)
    result = delivery.deliver(job, ["gmail"])
    assert result == {"gmail": "已寄出（附件 1 個）"}
    msg = fake_smtp.sent[0]
    assert msg["Subject"] == "YouTube 投影片：測試影片 <b>"
    body = msg.get_body().get_content()
    assert "https://www.youtube.com/watch?v=abcdefghijk" in body and "頁數：2" in body
    assert "時長：00:20" in body and "附件 HTML 可直接用瀏覽器開啟離線閱讀" in body
    assert not (data_dir / "tmp" / f"deliver_{job['id']}").exists()


# ---------- API ----------
def test_api_delivery_status(client, monkeypatch):
    monkeypatch.setattr(delivery, "drive_status", lambda: (False, "找不到 rclone"))
    monkeypatch.setattr(delivery, "gmail_status", lambda: (True, ""))
    assert client.get("/api/delivery/status").json() == {
        "drive": {"ok": False, "reason": "找不到 rclone"}, "gmail": {"ok": True, "reason": ""}}


def test_api_deliver_not_done_409(client, data_dir):
    job = make_done_job(client, data_dir, status="running")
    resp = client.post(f"/api/jobs/{job['id']}/deliver", json={"targets": ["drive"]})
    assert resp.status_code == 409


def test_api_deliver_empty_targets_422(client, data_dir):
    job = make_done_job(client, data_dir)
    assert client.post(f"/api/jobs/{job['id']}/deliver", json={"targets": []}).status_code == 422


def test_api_deliver_done_200(client, data_dir, monkeypatch):
    calls = []

    def fake_deliver(job, targets):
        calls.append((job["id"], targets))
        return {t: "已寄出（附件 2 個）" for t in targets}

    monkeypatch.setattr(delivery, "deliver", fake_deliver)
    job = make_done_job(client, data_dir)
    resp = client.post(f"/api/jobs/{job['id']}/deliver", json={"targets": ["gmail"]})
    assert resp.status_code == 200
    assert resp.json() == {"gmail": "已寄出（附件 2 個）"}
    assert calls == [(job["id"], ["gmail"])]
    stored = client.get(f"/api/jobs/{job['id']}").json()["deliver_result"]
    assert json.loads(stored) == {"gmail": "已寄出（附件 2 個）"}


def test_submit_with_deliver_auto_delivers(make_client, wait_status, synthetic_video, fake_stages, monkeypatch):
    def fake_download(video_id, height, tmp_dir, duration=None, progress_hook=None):
        tmp_dir.mkdir(parents=True, exist_ok=True)
        return synthetic_video, 480

    fake_stages(fake_download)
    calls = []

    def fake_deliver(job, targets):
        calls.append((job["title"], targets))
        return {"drive": "已上傳到 gdrive:x", "gmail": "失敗：寄送 Gmail 失敗"}

    monkeypatch.setattr(delivery, "deliver", fake_deliver)
    with make_client() as client:
        resp = client.post("/api/jobs", json={"url": "https://youtu.be/aaaaaaaaaaa",
                                              "deliver": ["drive", "gmail"]})
        assert resp.status_code == 201, resp.text
        job = wait_status(client, resp.json()["job_ids"][0], "done", "failed", timeout=60)
    # 某個 target 失敗不影響工作狀態
    assert job["status"] == "done"
    assert job["deliver"] == "drive,gmail"
    assert calls == [("測試影片", ["drive", "gmail"])]
    assert json.loads(job["deliver_result"])["drive"] == "已上傳到 gdrive:x"


def test_submit_invalid_deliver_422(client):
    resp = client.post("/api/jobs", json={"url": "https://youtu.be/aaaaaaaaaaa", "deliver": ["dropbox"]})
    assert resp.status_code == 422


def test_deliver_crash_does_not_fail_job(make_client, wait_status, monkeypatch):
    from app.pipeline import runner
    from tests.test_api import _done_runner

    monkeypatch.setattr(runner, "run_pipeline", _done_runner)

    def boom(job, targets):
        raise RuntimeError("boom")

    monkeypatch.setattr(delivery, "deliver", boom)
    with make_client() as client:
        job_id = client.post("/api/jobs", json={"url": "https://youtu.be/aaaaaaaaaaa",
                                                "deliver": ["gmail"]}).json()["job_ids"][0]
        job = wait_status(client, job_id, "done", "failed")
    assert job["status"] == "done"
    assert json.loads(job["deliver_result"]) == {"gmail": "失敗：發生未預期錯誤"}


def test_playlist_children_inherit_deliver(make_client, monkeypatch):
    from app.pipeline import runner

    monkeypatch.setattr(metadata, "list_playlist", lambda pid: ("清單", ["aaaaaaaaaaa", "bbbbbbbbbbb"]))
    # 卡住 worker，只檢查建立出來的資料列
    monkeypatch.setattr(runner, "run_pipeline", lambda job, report, cancelled: time.sleep(0.5) or {})
    with make_client() as client:
        resp = client.post("/api/jobs", json={"url": "https://www.youtube.com/playlist?list=PL123",
                                              "deliver": ["drive"]})
        assert resp.status_code == 201, resp.text
        ids = resp.json()["job_ids"]
        store = client.app.state.manager.store
        assert [store.get(i)["deliver"] for i in ids] == ["drive", "drive", "drive"]


def test_migrate_adds_deliver_columns(data_dir):
    from app.jobs import JobStore

    data_dir.mkdir(parents=True)
    db = data_dir / "app.db"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE jobs (id TEXT PRIMARY KEY, video_id TEXT, url TEXT NOT NULL,"
                 " quality INTEGER NOT NULL, target_lang TEXT NOT NULL, status TEXT NOT NULL, stage TEXT,"
                 " progress INTEGER NOT NULL DEFAULT 0, message TEXT, error_code TEXT, error TEXT, title TEXT,"
                 " duration REAL, slide_count INTEGER, translate_status TEXT, created_at TEXT NOT NULL,"
                 " finished_at TEXT, parent_id TEXT, kind TEXT DEFAULT 'video')")
    conn.commit()
    conn.close()
    store = JobStore(db)
    store.init()
    job = store.create("aaaaaaaaaaa", "u", 720, "zh-TW", deliver="gmail")
    assert job["deliver"] == "gmail" and job["deliver_result"] is None
