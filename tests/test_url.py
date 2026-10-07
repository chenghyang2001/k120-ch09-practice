"""url.parse_youtube_url 測試。"""

import pytest

from app.errors import AppError
from app.pipeline.url import ParsedUrl, parse_youtube_url

VID = "dQw4w9WgXcQ"
PL = "PLabc123_-XYZ"


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        (f"https://www.youtube.com/watch?v={VID}", ParsedUrl("video", VID, None)),
        (f"https://youtu.be/{VID}", ParsedUrl("video", VID, None)),
        (f"https://www.youtube.com/shorts/{VID}", ParsedUrl("video", VID, None)),
        (f"https://www.youtube.com/embed/{VID}", ParsedUrl("video", VID, None)),
        (f"https://www.youtube.com/live/{VID}", ParsedUrl("video", VID, None)),
        (f"https://m.youtube.com/watch?v={VID}", ParsedUrl("video", VID, None)),
        (f"youtube.com/watch?v={VID}", ParsedUrl("video", VID, None)),
        (f"  https://youtu.be/{VID}?si=abcDEF&t=30  ", ParsedUrl("video", VID, None)),
        (f"https://www.youtube.com/watch?v={VID}&t=1m30s&si=xyz", ParsedUrl("video", VID, None)),
        (f"https://www.youtube.com/watch?v={VID}&list={PL}", ParsedUrl("video", VID, PL)),
        (f"https://www.youtube.com/playlist?list={PL}", ParsedUrl("playlist", None, PL)),
        (f"https://www.youtube.com/embed/videoseries?list={PL}", ParsedUrl("playlist", None, PL)),
        # 沒有 scheme、但 query 裡帶有 :// 的網址仍要補上 https://
        (f"youtube.com/watch?v={VID}&ref=https://example.com", ParsedUrl("video", VID, None)),
        (f"HTTPS://www.youtube.com/watch?v={VID}", ParsedUrl("video", VID, None)),
    ],
)
def test_parse_valid(url, expected):
    assert parse_youtube_url(url) == expected


@pytest.mark.parametrize(
    "url",
    [
        "https://vimeo.com/123456",
        f"https://youtube.com.evil.com/watch?v={VID}",
        f"https://evil-youtube.com/watch?v={VID}",
        "https://www.youtube.com/watch?v=short",
        f"https://youtu.be/{VID}X",
        "",
        "   ",
        "https://www.youtube.com/channel/UCabcdefghij",
        "https://www.youtube.com/playlist",
        "ftp://youtube.com/watch?v=" + VID,
        f"https://www.youtube.com/watch?v={VID}%0a",
        f"https://www.youtube.com/playlist?list={PL}%0a",
        "https://www.youtube.com/embed/videoseries",
        None,
        123,
    ],
)
def test_parse_invalid(url):
    with pytest.raises(AppError) as exc:
        parse_youtube_url(url)
    assert exc.value.code == "invalid_url"
