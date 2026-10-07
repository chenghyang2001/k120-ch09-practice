"""metadata 模組測試（不連網，yt-dlp 以 monkeypatch 替換）。"""

import pytest
import yt_dlp

from app.config import MAX_DURATION_SEC
from app.errors import AppError
from app.pipeline import metadata
from app.pipeline.metadata import (
    detect_primary_language,
    fetch_video_info,
    to_meta,
    validate_info,
)

# ---------- validate_info ----------

@pytest.mark.parametrize(
    ("info", "code"),
    [
        ({"is_live": True}, "live_not_supported"),
        ({"live_status": "is_live"}, "live_not_supported"),
        ({"live_status": "is_upcoming"}, "live_not_supported"),
        ({"availability": "subscriber_only"}, "members_only"),
        ({"availability": "needs_auth"}, "members_only"),
        ({"duration": MAX_DURATION_SEC + 1}, "too_long"),
    ],
)
def test_validate_info_rejects(info, code):
    with pytest.raises(AppError) as exc:
        validate_info(info)
    assert exc.value.code == code


@pytest.mark.parametrize(
    "info",
    [
        {"duration": 600, "live_status": "not_live", "availability": "public"},
        {"duration": MAX_DURATION_SEC, "live_status": "was_live"},
        {"duration": None},
    ],
)
def test_validate_info_accepts(info):
    validate_info(info)


# ---------- detect_primary_language ----------

@pytest.mark.parametrize(
    ("info", "expected"),
    [
        ({"language": "ja", "subtitles": {"en": []}}, "ja"),
        ({"language": None, "subtitles": {"live_chat": [], "de": []}}, "de"),
        ({"subtitles": {}, "automatic_captions": {"en": [], "ko-orig": [], "fr": []}}, "ko"),
        ({"subtitles": None, "automatic_captions": None}, None),
        ({}, None),
        ({"subtitles": {"live_chat": []}, "automatic_captions": {"en": []}}, None),
    ],
)
def test_detect_primary_language(info, expected):
    assert detect_primary_language(info) == expected


def test_to_meta():
    info = {"id": "dQw4w9WgXcQ", "title": "標題", "duration": 12.5, "language": "en"}
    meta = to_meta(info)
    assert meta.video_id == "dQw4w9WgXcQ"
    assert meta.language == "en"
    assert meta.webpage_url == "https://www.youtube.com/watch?v=dQw4w9WgXcQ"


# ---------- fetch_video_info ----------

def _fake_ydl(error_message: str | None = None, info: dict | None = None):
    class FakeYDL:
        def __init__(self, opts):
            self.opts = opts

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def extract_info(self, url, download=True):
            assert download is False
            if error_message is not None:
                raise yt_dlp.utils.DownloadError(error_message)
            return info

        def sanitize_info(self, raw):
            return {**raw, "sanitized": True}

    return FakeYDL


def test_fetch_video_info_success(monkeypatch):
    monkeypatch.setattr(metadata.yt_dlp, "YoutubeDL", _fake_ydl(info={"id": "abc"}))
    assert fetch_video_info("dQw4w9WgXcQ") == {"id": "abc", "sanitized": True}


@pytest.mark.parametrize(
    ("message", "code"),
    [
        ("ERROR: [youtube] x: Private video. Sign in if you've been granted access", "private_video"),
        ("ERROR: [youtube] x: Sign in to confirm you’re not a bot", "bot_check"),
        ("ERROR: [youtube] x: Sign in to confirm your age", "age_restricted"),
        ("ERROR: Join this channel to get access to members-only content", "members_only"),
        ("ERROR: [youtube] x: Video unavailable", "video_unavailable"),
        ("ERROR: [youtube] aaaaaaaaaaa: This video is unavailable", "video_unavailable"),
        (
            (
                "ERROR: [youtube] x: This video is no longer available because the YouTube account "
                "associated with this video has been terminated."
            ),
            "video_unavailable",
        ),
        ("ERROR: [youtube] x: This video is private", "private_video"),
        ("ERROR: [youtube] x: This live event will begin in 3 hours.", "live_not_supported"),
        ("ERROR: [youtube] x: Premieres in 2 hours", "live_not_supported"),
        ("ERROR: [youtube] x: This premiere will begin shortly", "live_not_supported"),
        (
            "ERROR: [youtube] x: The uploader has not made this video available in your country",
            "video_unavailable",
        ),
        (
            "ERROR: [youtube] x: Unable to download webpage: <urlopen error [Errno 11001] getaddrinfo failed>",
            "network_error",
        ),
        ("ERROR: [youtube] x: HTTP Error 429: Too Many Requests. Try again later", "network_error"),
        ("ERROR: [youtube] x: Read timed out", "network_error"),
        ("ERROR: [youtube] x: nsig extraction failed", "ytdlp_outdated"),
    ],
)
def test_fetch_video_info_error_mapping(monkeypatch, message, code):
    monkeypatch.setattr(metadata.yt_dlp, "YoutubeDL", _fake_ydl(error_message=message))
    with pytest.raises(AppError) as exc:
        fetch_video_info("dQw4w9WgXcQ")
    assert exc.value.code == code
    assert isinstance(exc.value.__cause__, yt_dlp.utils.DownloadError)
    if code == "ytdlp_outdated":
        assert "uv lock --upgrade-package yt-dlp" in exc.value.message
    if "your country" in message:
        assert exc.value.message == "此影片在你所在的地區無法觀看"
