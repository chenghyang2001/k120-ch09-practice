"""影片下載（只取視訊流）與字幕中點截圖。"""

import shutil
import subprocess
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import yt_dlp

from app.config import FRAME_WORKERS, QUALITY_HEIGHTS
from app.errors import AppError
from app.pipeline.subtitles import Cue

# 各畫質的粗估位元率（bit/s），只用來預估磁碟空間
BITRATES = {360: 0.5e6, 480: 1e6, 720: 2.5e6, 1080: 5e6}


def midpoints(cues: list[Cue]) -> list[float]:
    return [(c.start + c.end) / 2 for c in cues]


def _run(cmd: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, encoding="utf-8", check=False)


def probe_duration(video: Path) -> float:
    proc = _run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                 "-of", "csv=p=0", str(video)])
    try:
        return float(proc.stdout.strip())
    except ValueError as e:
        raise AppError("frame_failed", "截圖失敗") from e


def _grab(video: Path, t: float, dst: Path) -> Path:
    # -ss 放在 -i 前做關鍵影格快速 seek，長片截圖才不會從頭解碼
    proc = _run(["ffmpeg", "-nostdin", "-loglevel", "error", "-ss", f"{t:.3f}",
                 "-i", str(video), "-frames:v", "1", "-q:v", "2", "-y", str(dst)])
    # ffmpeg seek 到片尾之後常常 returncode 0 卻沒產出檔案，所以兩者都要檢查
    if proc.returncode != 0 or not dst.is_file():
        raise AppError("frame_failed", "截圖失敗")
    return dst


def extract_frames(video: Path, timestamps: list[float], out_dir: Path,
                   workers: int = FRAME_WORKERS) -> list[Path]:
    if shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None:
        raise AppError("ffmpeg_missing", "找不到 ffmpeg，請先安裝並加入 PATH")
    out_dir.mkdir(parents=True, exist_ok=True)
    last = max(0.0, probe_duration(video) - 0.1)
    jobs = [(min(t, last), out_dir / f"{i:05d}.png") for i, t in enumerate(timestamps)]
    with ThreadPoolExecutor(workers) as pool:
        # map 保留輸入順序；任一張丟 AppError 會在取結果時重新拋出
        return list(pool.map(lambda job: _grab(video, *job), jobs))


def video_format(height: int) -> str:
    if height not in QUALITY_HEIGHTS:
        raise ValueError(f"不支援的畫質：{height}")
    return f"bv*[height<={height}][ext=mp4]/bv*[height<={height}]"


def estimate_bytes(duration: float, height: int) -> int:
    # 乘 1.5 留安全餘裕：實際位元率依內容變動很大
    return int(duration * BITRATES[height] / 8 * 1.5)


def ensure_disk_space(path: Path, needed: int) -> None:
    if shutil.disk_usage(path).free < needed:
        raise AppError("disk_full", "磁碟空間不足")


def download_video(video_id: str, height: int, tmp_dir: Path,
                   duration: float | None = None, progress_hook=None) -> tuple[Path, int]:
    tmp_dir.mkdir(parents=True, exist_ok=True)
    if duration is not None:
        ensure_disk_space(tmp_dir, estimate_bytes(duration, height))
    opts = {
        "format": video_format(height),
        "outtmpl": str(tmp_dir / "video.%(ext)s"),
        "quiet": True, "noprogress": True,
        "no_warnings": True,
    }
    if progress_hook is not None:
        opts["progress_hooks"] = [progress_hook]
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(f"https://www.youtube.com/watch?v={video_id}", download=True)
            downloads = info.get("requested_downloads") or []
            path = downloads[0].get("filepath") if downloads else None
            path = Path(path or ydl.prepare_filename(info))
    except yt_dlp.utils.DownloadError as e:
        raise AppError("video_download_failed", "影片下載失敗，請稍後再試") from e
    return path, info.get("height") or height
