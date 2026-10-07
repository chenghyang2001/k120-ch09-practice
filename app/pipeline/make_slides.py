"""手動驗證 CLI：uv run python -m app.pipeline.make_slides <URL> [--quality 720] [--target-lang zh-TW] [--no-translate] [--out data/jobs]"""

import argparse
import shutil
import sys
from pathlib import Path

from app.config import LANG_NAMES, QUALITY_HEIGHTS
from app.errors import AppError
from app.pipeline.frames import download_video
from app.pipeline.metadata import (
    detect_primary_language,
    fetch_video_info,
    validate_info,
)
from app.pipeline.slides import build_slides
from app.pipeline.subtitles import fetch_cues
from app.pipeline.translate import translate_cues
from app.pipeline.url import parse_youtube_url

TMP_ROOT = Path("data/tmp")


def run(url: str, quality: int, out: Path, target_lang: str | None) -> None:
    """target_lang 為 None 代表不翻譯。"""
    parsed = parse_youtube_url(url)
    if parsed.kind == "playlist":
        print("播放清單將在 M7 支援")
        return

    info = fetch_video_info(parsed.video_id)
    validate_info(info)
    lang = detect_primary_language(info)
    result = fetch_cues(info, lang) if lang else None
    if result is None:
        print("無字幕，需 Whisper（M7 實作）")
        return
    cues, _track = result
    if target_lang is not None:
        cues, status = translate_cues(cues, lang, target_lang, info)
        print(f"翻譯：{status}")
        if status == "claude_unavailable":
            print("警告：找不到 claude CLI，只輸出原文字幕")
        elif status == "partial":
            print("警告：部分字幕翻譯失敗，這些句子只有原文")

    tmp_dir = TMP_ROOT / parsed.video_id
    try:
        video, height = download_video(parsed.video_id, quality, tmp_dir, info.get("duration"))
        suffix = f"_{target_lang}" if target_lang is not None else ""
        job_dir = out / f"{parsed.video_id}_{quality}{suffix}"
        slides = build_slides(cues, video, job_dir, tmp_dir)
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)
    print(f"頁數：{len(slides)}")
    print(f"cue 數量：{len(cues)}")
    print(f"實際畫質：{height}p")
    print(f"輸出路徑：{job_dir}")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="python -m app.pipeline.make_slides")
    parser.add_argument("url", help="YouTube 影片網址")
    parser.add_argument("--quality", type=int, default=720, choices=QUALITY_HEIGHTS)
    parser.add_argument("--out", type=Path, default=Path("data/jobs"))
    parser.add_argument("--target-lang", default="zh-TW", choices=list(LANG_NAMES))
    parser.add_argument("--no-translate", action="store_true", help="跳過翻譯")
    return parser.parse_args()


def main() -> None:
    # Windows 主控台預設 cp950，印中文或特殊字元會 UnicodeEncodeError
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    # 缺參數時 argparse 會印用法並以 exit 2 結束
    args = _parse_args()
    try:
        run(args.url, args.quality, args.out, None if args.no_translate else args.target_lang)
    except AppError as e:
        print(f"錯誤 [{e.code}]：{e.message}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
