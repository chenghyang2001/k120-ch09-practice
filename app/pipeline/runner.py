"""背景工作用的完整流程：照 make_slides 串接各階段，加上進度回報與取消。"""

import json
import shutil
from collections.abc import Callable

from app import config
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

Report = Callable[[str, int, str], None]


class JobCancelled(Exception):
    """使用者取消工作；worker 收到後標成 cancelled 並刪除輸出。"""


def run_pipeline(job: dict, report: Report, cancelled: Callable[[], bool]) -> dict:
    """同步執行整條流程；回傳要寫回資料庫的欄位。"""
    # 每次呼叫才讀 DATA_DIR，測試才能把它換成 tmp_path
    out_dir = config.DATA_DIR / "jobs" / job["id"]
    tmp_dir = config.DATA_DIR / "tmp" / job["id"]

    def step(stage: str, progress: int, message: str) -> None:
        if cancelled():
            raise JobCancelled()
        report(stage, progress, message)

    try:
        step("metadata", 5, "取得影片資訊")
        info = fetch_video_info(job["video_id"])
        validate_info(info)

        step("subtitles", 15, "取得字幕")
        lang = detect_primary_language(info)
        result = fetch_cues(info, lang) if lang else None
        if result is None:
            raise AppError("no_subtitles", "此影片沒有字幕，Whisper 轉錄將在 M7 支援")
        cues, track = result

        step("translate", 15, "翻譯字幕")
        cues, translate_status = translate_cues(cues, lang, job["target_lang"], info)

        step("download", 40, "下載影片")
        video, height = download_video(job["video_id"], job["quality"], tmp_dir, info.get("duration"),
                                       progress_hook=_download_hook(report, cancelled))

        step("frames", 60, "截圖與去重")
        slides = build_slides(cues, video, out_dir, tmp_dir)

        step("output", 90, "寫入輸出")
        meta = {
            "title": info.get("title") or "",
            "duration": info.get("duration"),
            "video_id": job["video_id"],
            "url": job["url"],
            "quality": job["quality"],
            "actual_quality": height,
            "target_lang": job["target_lang"],
            "source_lang": lang,
            "translate_status": translate_status,
            "subtitle_kind": track.kind,
            "slide_count": len(slides),
        }
        (out_dir / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
        report("output", 100, "完成")
        return {"title": meta["title"], "duration": meta["duration"],
                "slide_count": len(slides), "translate_status": translate_status}
    finally:
        # 影片與截圖可能好幾 GB，成功、失敗、取消都要清
        shutil.rmtree(tmp_dir, ignore_errors=True)


def _download_hook(report: Report, cancelled: Callable[[], bool]) -> Callable[[dict], None]:
    """yt-dlp progress hook：在 hook 裡 raise 才能中斷下載；進度換算成 40→60。"""
    last = 40

    def hook(d: dict) -> None:
        nonlocal last
        if cancelled():
            raise JobCancelled()
        total = d.get("total_bytes") or d.get("total_bytes_estimate")
        if d.get("status") != "downloading" or not total:
            return
        progress = 40 + int(20 * min(d.get("downloaded_bytes") or 0, total) / total)
        # hook 每個 chunk 都會呼叫，只在整數進度變動時才寫資料庫
        if progress > last:
            last = progress
            report("download", progress, "下載影片")

    return hook
