import json

import pytest

from app import config
from app.errors import AppError
from app.jobs import JobStore
from app.pipeline import runner


def _done_runner(job, report, cancelled):
    return {"title": "t", "duration": 1.0, "slide_count": 0, "translate_status": "claude"}


def test_store_crud_and_order(data_dir):
    store = JobStore(data_dir / "app.db")
    store.init()
    a = store.create("aaaaaaaaaaa", "u", 720, "zh-TW")
    b = store.create("bbbbbbbbbbb", "u", 720, "zh-TW")
    assert [j["id"] for j in store.list()] == [b["id"], a["id"]]
    assert store.find_done("aaaaaaaaaaa", 720, "zh-TW") is None
    store.update(a["id"], status="done")
    assert store.find_done("aaaaaaaaaaa", 720, "zh-TW")["id"] == a["id"]
    assert store.find_done("aaaaaaaaaaa", 480, "zh-TW") is None
    with pytest.raises(ValueError):
        store.update(a["id"], **{"status = 'x'; --": 1})
    store.delete(a["id"])
    assert store.get(a["id"]) is None


def test_restart_marks_running_interrupted(data_dir, make_client, wait_status, monkeypatch):
    store = JobStore(data_dir / "app.db")
    store.init()
    running = store.create("aaaaaaaaaaa", "u", 720, "zh-TW")
    store.update(running["id"], status="running", stage="download", progress=50)
    queued = store.create("bbbbbbbbbbb", "u", 720, "zh-TW")
    leftover = data_dir / "tmp" / running["id"] / "video.mp4"
    leftover.parent.mkdir(parents=True)
    leftover.write_bytes(b"x")
    monkeypatch.setattr(runner, "run_pipeline", _done_runner)

    with make_client() as client:
        job = client.get(f"/api/jobs/{running['id']}").json()
        assert job["status"] == "interrupted"
        assert job["finished_at"] is not None
        assert not leftover.exists()
        # 排隊中的工作重啟後會繼續處理
        wait_status(client, queued["id"], "done")


def test_run_pipeline_integration(data_dir, synthetic_video, fake_stages):
    def fake_download(video_id, height, tmp_dir, duration=None, progress_hook=None):
        tmp_dir.mkdir(parents=True, exist_ok=True)
        progress_hook({"status": "downloading", "downloaded_bytes": 50, "total_bytes": 100})
        return synthetic_video, 480

    fake_stages(fake_download)
    job = {"id": "job123", "video_id": "aaaaaaaaaaa", "url": "https://youtu.be/aaaaaaaaaaa",
           "quality": 720, "target_lang": "zh-TW"}
    reports = []
    result = runner.run_pipeline(job, lambda *args: reports.append(args), lambda: False)

    out = config.DATA_DIR / "jobs" / "job123"
    meta = json.loads((out / "meta.json").read_text(encoding="utf-8"))
    slides = json.loads((out / "slides.json").read_text(encoding="utf-8"))
    assert len(slides) == 3
    assert meta["slide_count"] == 3
    assert (meta["quality"], meta["actual_quality"]) == (720, 480)
    assert (meta["source_lang"], meta["subtitle_kind"], meta["translate_status"]) == ("en", "manual", "claude")
    assert result == {"title": "測試影片", "duration": 12.0, "slide_count": 3, "translate_status": "claude"}
    assert not (config.DATA_DIR / "tmp" / "job123").exists()

    progresses = [p for _, p, _ in reports]
    assert progresses == sorted(progresses)
    assert progresses[-1] == 100
    assert ("download", 50) in [(s, p) for s, p, _ in reports]
    assert reports[0][0] == "metadata"


def test_run_pipeline_no_subtitles_no_speech(data_dir, fake_stages, monkeypatch):
    # M7 起沒字幕改走 Whisper；轉錄結果是空的才算失敗
    fake_stages(lambda *a, **k: pytest.fail("不該下載影片"), has_subtitles=False)
    monkeypatch.setattr(runner, "download_audio", lambda vid, tmp, progress_hook=None: tmp / "audio.m4a")
    monkeypatch.setattr(runner, "transcribe", lambda audio, lang, cancelled=None, progress=None: ([], "en"))
    job = {"id": "job456", "video_id": "aaaaaaaaaaa", "url": "u", "quality": 720, "target_lang": "zh-TW"}
    with pytest.raises(AppError) as exc:
        runner.run_pipeline(job, lambda *a: None, lambda: False)
    assert exc.value.code == "no_speech"
    assert not (config.DATA_DIR / "tmp" / "job456").exists()


def test_run_pipeline_cancel_before_start(data_dir, fake_stages):
    fake_stages(lambda *a, **k: pytest.fail("不該下載"))
    job = {"id": "job789", "video_id": "aaaaaaaaaaa", "url": "u", "quality": 720, "target_lang": "zh-TW"}
    with pytest.raises(runner.JobCancelled):
        runner.run_pipeline(job, lambda *a: None, lambda: True)


def test_run_pipeline_translate_cancel_and_progress(data_dir, fake_stages, monkeypatch):
    from app.pipeline.translate import TranslationCancelled

    def cancelling_translate(cues, src, tgt, info, cancelled=None, progress=None):
        progress(1, 4)
        progress(2, 4)
        raise TranslationCancelled()

    fake_stages(lambda *a, **k: pytest.fail("不該下載"))
    monkeypatch.setattr(runner, "translate_cues", cancelling_translate)
    job = {"id": "jobt", "video_id": "aaaaaaaaaaa", "url": "u", "quality": 720, "target_lang": "zh-TW"}
    reports = []
    with pytest.raises(runner.JobCancelled):
        runner.run_pipeline(job, lambda *a: reports.append(a), lambda: False)
    # 翻譯進度換算到 15→40
    assert [(s, p) for s, p, _ in reports if s == "translate"] == [("translate", 15), ("translate", 21),
                                                                   ("translate", 27)]
