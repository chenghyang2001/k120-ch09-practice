import shutil
from collections import namedtuple
from typing import ClassVar

import pytest
import yt_dlp

from app.config import DEDUPE_THRESHOLD
from app.errors import AppError
from app.pipeline import frames
from app.pipeline.dedupe import phash
from app.pipeline.frames import (
    download_video,
    ensure_disk_space,
    estimate_bytes,
    extract_frames,
    midpoints,
    probe_duration,
    video_format,
)
from app.pipeline.subtitles import Cue

requires_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="系統沒有 ffmpeg")


def test_midpoints():
    cues = [Cue(0, 0.0, 2.0, "a"), Cue(1, 3.0, 4.0, "b")]
    assert midpoints(cues) == [1.0, 3.5]


@requires_ffmpeg
def test_probe_duration(synthetic_video):
    assert probe_duration(synthetic_video) == pytest.approx(12.0, abs=0.2)


@requires_ffmpeg
def test_synthetic_segments_are_distinct(synthetic_video, tmp_path):
    paths = extract_frames(synthetic_video, [1, 3, 5, 7, 9, 11], tmp_path / "f")
    h = [phash(p) for p in paths]
    for a, b in [(0, 1), (2, 3), (4, 5)]:
        assert h[a] - h[b] <= DEDUPE_THRESHOLD
    for a, b in [(0, 2), (0, 4), (2, 4)]:
        assert h[a] - h[b] > DEDUPE_THRESHOLD


@requires_ffmpeg
def test_extract_frames_count_and_order(synthetic_video, tmp_path):
    out = tmp_path / "frames"
    paths = extract_frames(synthetic_video, [9.0, 1.0, 5.0], out, workers=2)
    assert [p.name for p in paths] == ["00000.png", "00001.png", "00002.png"]
    assert all(p.is_file() for p in paths)
    # 順序要對應時間點：第 0 張（9 秒，C 段）跟第 1 張（1 秒，A 段）不同
    assert phash(paths[0]) - phash(paths[1]) > DEDUPE_THRESHOLD


@requires_ffmpeg
def test_extract_frames_past_end(synthetic_video, tmp_path):
    paths = extract_frames(synthetic_video, [11.0, 30.0], tmp_path / "f")
    assert all(p.is_file() for p in paths)
    assert phash(paths[0]) - phash(paths[1]) <= DEDUPE_THRESHOLD


def test_extract_frames_ffmpeg_missing(monkeypatch, tmp_path):
    monkeypatch.setattr(frames.shutil, "which", lambda name: None)
    with pytest.raises(AppError) as e:
        extract_frames(tmp_path / "v.mp4", [1.0], tmp_path / "f")
    assert e.value.code == "ffmpeg_missing"


@requires_ffmpeg
def test_extract_frames_bad_video(tmp_path):
    bad = tmp_path / "bad.mp4"
    bad.write_bytes(b"garbage")
    with pytest.raises(AppError) as e:
        extract_frames(bad, [1.0], tmp_path / "f")
    assert e.value.code == "frame_failed"


def test_video_format():
    assert video_format(720) == "bv*[height<=720][ext=mp4]/bv*[height<=720]"
    with pytest.raises(ValueError):
        video_format(144)


def test_estimate_bytes():
    assert estimate_bytes(100, 720) == int(100 * 2.5e6 / 8 * 1.5)
    assert estimate_bytes(0, 1080) == 0


Usage = namedtuple("Usage", "total used free")


def test_ensure_disk_space(monkeypatch, tmp_path):
    monkeypatch.setattr(frames.shutil, "disk_usage", lambda p: Usage(100, 90, 10))
    ensure_disk_space(tmp_path, 10)
    with pytest.raises(AppError) as e:
        ensure_disk_space(tmp_path, 11)
    assert e.value.code == "disk_full"


class FakeYDL:
    calls: ClassVar[list] = []
    info: ClassVar[dict] = {}
    error: ClassVar[Exception | None] = None

    def __init__(self, opts):
        FakeYDL.calls.append(opts)
        self.opts = opts

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def extract_info(self, url, download):
        FakeYDL.calls.append(url)
        if FakeYDL.error:
            raise FakeYDL.error
        return FakeYDL.info

    def prepare_filename(self, info):
        return self.opts["outtmpl"].replace("%(ext)s", info["ext"])


@pytest.fixture
def fake_ydl(monkeypatch):
    FakeYDL.calls, FakeYDL.info, FakeYDL.error = [], {"ext": "mp4", "height": 480}, None
    monkeypatch.setattr(yt_dlp, "YoutubeDL", FakeYDL)
    return FakeYDL


def test_download_video(fake_ydl, tmp_path):
    path, height = download_video("abcdefghijk", 720, tmp_path)
    opts, url = fake_ydl.calls
    assert opts["format"] == video_format(720)
    assert opts["quiet"] and opts["no_warnings"]
    assert url == "https://www.youtube.com/watch?v=abcdefghijk"
    assert path == tmp_path / "video.mp4"
    assert height == 480


def test_download_video_height_fallback(fake_ydl, tmp_path):
    fake_ydl.info = {"ext": "webm"}
    path, height = download_video("abcdefghijk", 360, tmp_path)
    assert path == tmp_path / "video.webm"
    assert height == 360


def test_download_video_error(fake_ydl, tmp_path):
    fake_ydl.error = yt_dlp.utils.DownloadError("boom")
    with pytest.raises(AppError) as e:
        download_video("abcdefghijk", 720, tmp_path)
    assert e.value.code == "video_download_failed"
    assert isinstance(e.value.__cause__, yt_dlp.utils.DownloadError)


def test_download_video_checks_disk(fake_ydl, monkeypatch, tmp_path):
    monkeypatch.setattr(frames.shutil, "disk_usage", lambda p: Usage(100, 100, 0))
    with pytest.raises(AppError) as e:
        download_video("abcdefghijk", 720, tmp_path, duration=60)
    assert e.value.code == "disk_full"
    assert fake_ydl.calls == []
