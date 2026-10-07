"""YouTube 網址解析。"""

import re
from dataclasses import dataclass
from typing import Literal
from urllib.parse import parse_qs, urlparse

from app.errors import AppError

# 用完整比對而非 endswith，避免 youtube.com.evil.com 之類的網域混過
ALLOWED_HOSTS = ("youtube.com", "www.youtube.com", "m.youtube.com", "youtu.be")
VIDEO_ID_RE = re.compile(r"[A-Za-z0-9_-]{11}")
PLAYLIST_ID_RE = re.compile(r"[A-Za-z0-9_-]{2,64}")
PATH_PREFIXES = ("shorts", "embed", "live")
SCHEME_RE = re.compile(r"^[a-z][a-z0-9+.-]*://", re.IGNORECASE)
# 嵌入式播放清單的固定路徑；videoseries 剛好 11 字元，不先排除會被當成 video id
EMBED_PLAYLIST_PARTS = ["embed", "videoseries"]


@dataclass(frozen=True)
class ParsedUrl:
    kind: Literal["video", "playlist"]
    video_id: str | None
    playlist_id: str | None


def _invalid() -> AppError:
    return AppError("invalid_url", "不是有效的 YouTube 影片或播放清單網址")


def _extract_video_id(host: str, path: str, query: dict[str, list[str]]) -> str | None:
    """依網址型態取出 video id 候選字串；不驗證格式。"""
    parts = [p for p in path.split("/") if p]
    if host == "youtu.be":
        return parts[0] if len(parts) == 1 else None
    if parts == ["watch"]:
        return query.get("v", [None])[0]
    if len(parts) == 2 and parts[0] in PATH_PREFIXES and parts != EMBED_PLAYLIST_PARTS:
        return parts[1]
    return None


def parse_youtube_url(url: str) -> ParsedUrl:
    """解析 YouTube 網址，回傳影片或播放清單 id；無效時拋出 AppError。"""
    if not isinstance(url, str):
        raise _invalid()
    url = url.strip()
    if not url:
        raise _invalid()
    if not SCHEME_RE.match(url):
        url = "https://" + url

    try:
        parsed = urlparse(url)
        host = (parsed.hostname or "").lower()
    except ValueError as e:
        raise _invalid() from e
    if parsed.scheme not in ("http", "https") or host not in ALLOWED_HOSTS:
        raise _invalid()

    query = parse_qs(parsed.query)
    video_id = _extract_video_id(host, parsed.path, query)
    playlist_id = query.get("list", [None])[0]
    if playlist_id is not None and not PLAYLIST_ID_RE.fullmatch(playlist_id):
        playlist_id = None

    if video_id is not None:
        if not VIDEO_ID_RE.fullmatch(video_id):
            raise _invalid()
        return ParsedUrl("video", video_id, playlist_id)

    # /watch?list= 只有清單沒有影片時，YouTube 也是當播放清單處理
    path_parts = [p for p in parsed.path.split("/") if p]
    is_playlist_path = host != "youtu.be" and path_parts in (["playlist"], ["watch"], EMBED_PLAYLIST_PARTS)
    if is_playlist_path and playlist_id is not None:
        return ParsedUrl("playlist", None, playlist_id)
    raise _invalid()
