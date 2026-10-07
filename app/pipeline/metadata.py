"""用 yt-dlp 取得影片資訊、檢查是否支援、判斷主要語言。"""

from dataclasses import dataclass

import yt_dlp

from app.config import MAX_DURATION_SEC
from app.errors import AppError
from app.pipeline.url import VIDEO_ID_RE

# 依序比對，先命中者勝；bot 檢查要排在 age 之前，兩者都以 "sign in to confirm" 開頭。
# 「不可播放」與網路錯誤放最後，因為其他訊息也可能附帶 unavailable、try again later 字樣
_ERROR_PATTERNS: tuple[tuple[tuple[str, ...], str, str], ...] = (
    (("private video", "this video is private"), "private_video", "這是私人影片，無法處理"),
    (("members-only", "join this channel"), "members_only", "會員限定影片不支援"),
    (("sign in to confirm you're not a bot",), "bot_check", "YouTube 要求驗證不是機器人，請稍後再試"),
    (("sign in to confirm your age", "age-restricted"), "age_restricted", "年齡限制影片不支援"),
    (
        ("live event will begin", "premieres in", "premiere will begin"),
        "live_not_supported",
        "直播中或尚未開播的影片不支援",
    ),
    (("not made this video available in your country",), "video_unavailable", "此影片在你所在的地區無法觀看"),
    (
        ("video unavailable", "this video is unavailable", "no longer available", "has been removed"),
        "video_unavailable",
        "影片不存在或已被移除",
    ),
    (
        ("unable to download webpage", "try again later", "timed out", "getaddrinfo failed"),
        "network_error",
        "網路連線失敗或被 YouTube 暫時限流，請稍後再試",
    ),
)

_UNSUPPORTED_AVAILABILITY = ("subscriber_only", "premium_only", "needs_auth")

# flat 模式拿不到 availability 時，YouTube 只會把私人／已刪除影片的標題換成這兩種佔位字
_UNAVAILABLE_TITLES = ("[private video]", "[deleted video]")


@dataclass
class VideoMeta:
    video_id: str
    title: str
    duration: float | None
    thumbnail: str | None
    uploader: str | None
    language: str | None
    webpage_url: str


def _map_download_error(error_text: str) -> AppError:
    # yt-dlp 的 bot 訊息用彎引號（you’re），先統一成直引號再比對
    lowered = error_text.lower().replace("’", "'")
    for needles, code, message in _ERROR_PATTERNS:
        if any(n in lowered for n in needles):
            return AppError(code, message)
    return AppError(
        "ytdlp_outdated",
        "無法取得影片資訊，可能是 yt-dlp 過舊，請執行 uv lock --upgrade-package yt-dlp",
    )


def fetch_video_info(video_id: str) -> dict:
    """取得影片資訊（不下載），yt-dlp 錯誤轉成 AppError。"""
    opts = {"quiet": True, "no_warnings": True, "skip_download": True}
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(f"https://www.youtube.com/watch?v={video_id}", download=False)
            return ydl.sanitize_info(info)
    except yt_dlp.utils.DownloadError as e:
        raise _map_download_error(str(e)) from e


def validate_info(info: dict) -> None:
    """拒絕直播、會員限定與過長影片。"""
    if info.get("is_live") or info.get("live_status") in ("is_live", "is_upcoming"):
        raise AppError("live_not_supported", "直播中或尚未開播的影片不支援")
    if info.get("availability") in _UNSUPPORTED_AVAILABILITY:
        raise AppError("members_only", "會員限定或需登入的影片不支援")
    duration = info.get("duration")
    if duration is not None and duration > MAX_DURATION_SEC:
        hours = MAX_DURATION_SEC // 3600
        raise AppError("too_long", f"影片超過 {hours} 小時，不支援")


def detect_primary_language(info: dict) -> str | None:
    """依 language → 人工字幕 → 自動字幕 -orig 的順序判斷主要語言。"""
    if info.get("language"):
        return info["language"]
    for key in info.get("subtitles") or {}:
        if key != "live_chat":
            return key
    for key in info.get("automatic_captions") or {}:
        if key.endswith("-orig"):
            return key.removesuffix("-orig")
    return None


def to_meta(info: dict) -> VideoMeta:
    return VideoMeta(
        video_id=info["id"],
        title=info.get("title") or "",
        duration=info.get("duration"),
        thumbnail=info.get("thumbnail"),
        uploader=info.get("uploader"),
        language=detect_primary_language(info),
        webpage_url=info.get("webpage_url") or f"https://www.youtube.com/watch?v={info['id']}",
    )


def _is_playable_entry(entry: dict | None) -> bool:
    if not entry or not isinstance(entry.get("id"), str) or not VIDEO_ID_RE.fullmatch(entry["id"]):
        return False
    if entry.get("availability") in ("private", *_UNSUPPORTED_AVAILABILITY):
        return False
    return (entry.get("title") or "").strip().lower() not in _UNAVAILABLE_TITLES


def list_playlist(playlist_id: str) -> tuple[str, list[str]]:
    """回傳 (清單標題, 可處理的 video_id 清單)；只列項目不解析每支影片，速度才快。"""
    opts = {"extract_flat": "in_playlist", "quiet": True}
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(f"https://www.youtube.com/playlist?list={playlist_id}", download=False)
    except yt_dlp.utils.DownloadError as e:
        raise _map_download_error(str(e)) from e
    entries = info.get("entries") or []
    return info.get("title") or "", [e["id"] for e in entries if _is_playable_entry(e)]
