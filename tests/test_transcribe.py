"""M7 Whisper 備援測試：假的 WhisperModel／YoutubeDL，不載入真模型、不連網。"""

import json
import subprocess
from collections import namedtuple
from types import SimpleNamespace
from typing import ClassVar

import numpy as np
import pytest
import yt_dlp

from app import config
from app.errors import AppError
from app.pipeline import runner
from app.pipeline import transcribe as tc

Segment = namedtuple("Segment", "start end text")


class FakeModel:
    def __init__(self, segments, language="en", duration=10.0):
        self.segments, self.language, self.duration = segments, language, duration
        self.calls = []

    def transcribe(self, audio, language=None, vad_filter=False):
        self.calls.append({"audio": audio, "language": language, "vad_filter": vad_filter})
        info = SimpleNamespace(language=self.language, duration=self.duration)
        return iter(self.segments), info


SEGMENTS = [Segment(0.0, 2.5, " Hello everyone."), Segment(2.5, 5.0, " Today we talk."),
            Segment(5.0, 10.0, " About testing.")]


@pytest.fixture
def fake_model(monkeypatch):
    model = FakeModel(SEGMENTS)
    monkeypatch.setattr(tc, "_model", model)
    monkeypatch.setattr(tc, "_decode_audio", lambda audio: f"samples:{audio.name}")
    return model


def test_transcribe_converts_segments(fake_model, tmp_path):
    cues, lang = tc.transcribe(tmp_path / "a.m4a", "en-US")
    assert lang == "en"
    assert [(c.id, c.start, c.end, c.text) for c in cues] == [
        (0, 0.0, 2.5, "Hello everyone."), (1, 2.5, 5.0, "Today we talk."), (2, 5.0, 10.0, "About testing.")]
    # 地區碼要去掉，VAD 一定要開
    assert fake_model.calls == [{"audio": "samples:a.m4a", "language": "en", "vad_filter": True}]


def test_transcribe_progress_and_detect(fake_model, tmp_path):
    ratios = []
    _, lang = tc.transcribe(tmp_path / "a.m4a", None, progress=ratios.append)
    assert fake_model.calls[0]["language"] is None
    assert lang == "en"
    assert ratios == [0.25, 0.5, 1.0]


def test_transcribe_cancel(fake_model, tmp_path):
    seen = []

    def cancelled():
        seen.append(1)
        return len(seen) > 1

    with pytest.raises(tc.TranscriptionCancelled):
        tc.transcribe(tmp_path / "a.m4a", "en", cancelled=cancelled)
    assert len(seen) == 2


def test_model_loaded_lazily_once(monkeypatch):
    import faster_whisper

    created = []

    def fake_cls(name, device, compute_type):
        created.append((name, device, compute_type))
        return FakeModel([])

    monkeypatch.setattr(tc, "_model", None)
    monkeypatch.setattr(faster_whisper, "WhisperModel", fake_cls)
    first, second = tc._get_model(), tc._get_model()
    assert first is second
    assert created == [("small", "cpu", "int8")]


def test_decode_audio_with_ffmpeg(tmp_path):
    import shutil

    if shutil.which("ffmpeg") is None:
        pytest.skip("系統沒有 ffmpeg")
    audio = tmp_path / "tone.wav"
    subprocess.run(["ffmpeg", "-nostdin", "-loglevel", "error", "-f", "lavfi", "-i", "sine=frequency=440:duration=1",
                    "-ar", "44100", "-ac", "2", "-y", str(audio)], check=True)
    samples = tc._decode_audio(audio)
    assert samples.dtype == np.float32
    assert abs(len(samples) - tc.SAMPLE_RATE) < 200
    assert 0 < float(np.abs(samples).max()) <= 1.0


def test_decode_audio_bad_file(tmp_path):
    bad = tmp_path / "bad.webm"
    bad.write_bytes(b"not audio")
    with pytest.raises(AppError) as exc:
        tc._decode_audio(bad)
    assert exc.value.code == "audio_decode_failed"


class FakeYDL:
    opts: ClassVar[dict] = {}
    error: Exception | None = None

    def __init__(self, opts):
        FakeYDL.opts = opts

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def extract_info(self, url, download):
        if FakeYDL.error is not None:
            raise FakeYDL.error
        path = self.opts["outtmpl"].replace("%(ext)s", "webm")
        return {"requested_downloads": [{"filepath": path}]}


def test_download_audio(monkeypatch, tmp_path):
    FakeYDL.error = None
    monkeypatch.setattr(tc.yt_dlp, "YoutubeDL", FakeYDL)
    hook = lambda d: None
    path = tc.download_audio("aaaaaaaaaaa", tmp_path / "tmp", progress_hook=hook)
    assert path == tmp_path / "tmp" / "audio.webm"
    assert FakeYDL.opts["format"] == "bestaudio"
    assert FakeYDL.opts["noprogress"] is True
    assert FakeYDL.opts["progress_hooks"] == [hook]


def test_download_audio_error(monkeypatch, tmp_path):
    FakeYDL.error = yt_dlp.utils.DownloadError("boom")
    monkeypatch.setattr(tc.yt_dlp, "YoutubeDL", FakeYDL)
    with pytest.raises(AppError) as exc:
        tc.download_audio("aaaaaaaaaaa", tmp_path)
    assert exc.value.code == "audio_download_failed"
    FakeYDL.error = None


# ---------- runner 整合 ----------

def test_runner_uses_whisper_when_no_subtitles(data_dir, synthetic_video, fake_stages, monkeypatch):
    def fake_download(video_id, height, tmp_dir, duration=None, progress_hook=None):
        tmp_dir.mkdir(parents=True, exist_ok=True)
        return synthetic_video, 480

    def fake_audio(video_id, tmp_dir, progress_hook=None):
        tmp_dir.mkdir(parents=True, exist_ok=True)
        audio = tmp_dir / "audio.webm"
        audio.write_bytes(b"x")
        return audio

    seen = {}

    def fake_transcribe(audio, lang, cancelled=None, progress=None):
        seen["lang"] = lang
        for ratio in (0.5, 1.0):
            progress(ratio)
        cues = [tc.Cue(id=i, start=i * 2.0, end=i * 2.0 + 2.0, text=f"第{i}句") for i in range(6)]
        return cues, "ja"

    fake_stages(fake_download, has_subtitles=False)
    monkeypatch.setattr(runner, "download_audio", fake_audio)
    monkeypatch.setattr(runner, "transcribe", fake_transcribe)
    job = {"id": "jobw", "video_id": "aaaaaaaaaaa", "url": "u", "quality": 720, "target_lang": "zh-TW"}
    reports = []
    runner.run_pipeline(job, lambda *a: reports.append(a), lambda: False)

    meta = json.loads((config.DATA_DIR / "jobs" / "jobw" / "meta.json").read_text(encoding="utf-8"))
    assert (meta["subtitle_kind"], meta["source_lang"]) == ("whisper", "ja")
    assert seen["lang"] == "en"
    assert ("subtitles", 15, "無字幕，使用 Whisper 轉錄（較慢）") in reports
    assert ("subtitles", 30) in [(s, p) for s, p, _ in reports]
    progresses = [p for _, p, _ in reports]
    assert progresses == sorted(progresses)
    # 音訊在 tmp 裡，結束後要一起清掉
    assert not (config.DATA_DIR / "tmp" / "jobw").exists()


def test_runner_whisper_cancel(data_dir, fake_stages, monkeypatch):
    def cancelling_transcribe(audio, lang, cancelled=None, progress=None):
        raise tc.TranscriptionCancelled()

    fake_stages(lambda *a, **k: pytest.fail("不該下載影片"), has_subtitles=False)
    monkeypatch.setattr(runner, "download_audio", lambda vid, tmp, progress_hook=None: tmp / "a.webm")
    monkeypatch.setattr(runner, "transcribe", cancelling_transcribe)
    job = {"id": "jobc", "video_id": "aaaaaaaaaaa", "url": "u", "quality": 720, "target_lang": "zh-TW"}
    with pytest.raises(runner.JobCancelled):
        runner.run_pipeline(job, lambda *a: None, lambda: False)
