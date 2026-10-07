"""M1 手動驗證 CLI：uv run python -m app.pipeline.probe <URL>"""

import sys

from app.errors import AppError
from app.pipeline.metadata import (
    detect_primary_language,
    fetch_video_info,
    validate_info,
)
from app.pipeline.subtitles import fetch_cues
from app.pipeline.url import parse_youtube_url

PREVIEW_COUNT = 10


def _fmt(seconds: float) -> str:
    # 先四捨五入到十分之一秒再拆，避免 59.96 變成 00:60.0
    tenths = round(seconds * 10)
    minutes, rest = divmod(tenths, 600)
    return f"{minutes:02d}:{rest // 10:02d}.{rest % 10}"


def run(url: str) -> None:
    parsed = parse_youtube_url(url)
    if parsed.kind == "playlist":
        print("播放清單將在 M7 支援")
        return

    info = fetch_video_info(parsed.video_id)
    validate_info(info)
    lang = detect_primary_language(info)
    print(f"標題：{info.get('title')}")
    print(f"時長：{info.get('duration')} 秒")
    print(f"主要語言：{lang}")

    result = fetch_cues(info, lang) if lang else None
    if result is None:
        print("無字幕，需 Whisper（M7 實作）")
        return
    cues, track = result
    print(f"字幕種類：{track.kind}")
    print(f"cue 數量：{len(cues)}")
    for cue in cues[:PREVIEW_COUNT]:
        print(f"[{_fmt(cue.start)}-{_fmt(cue.end)}] {cue.text}")


def main() -> None:
    # Windows 主控台預設 cp950，印中文或特殊字元會 UnicodeEncodeError
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    if len(sys.argv) != 2:
        print("用法：python -m app.pipeline.probe <YouTube 網址>", file=sys.stderr)
        sys.exit(2)
    try:
        run(sys.argv[1])
    except AppError as e:
        print(f"錯誤 [{e.code}]：{e.message}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
