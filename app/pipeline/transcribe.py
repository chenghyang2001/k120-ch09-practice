"""沒有字幕時的備援：yt-dlp 只下載音訊 → faster-whisper 轉錄成 Cue。"""

import subprocess
import threading
from collections.abc import Callable
from pathlib import Path

import numpy as np
import yt_dlp

from app import config
from app.errors import AppError
from app.pipeline.subtitles import Cue, normalize_cues

# 模型載入要好幾秒、佔數百 MB，整個程序共用一份
_model = None
_model_lock = threading.Lock()
SAMPLE_RATE = 16000  # Whisper 固定吃 16 kHz 單聲道


class TranscriptionCancelled(Exception):
    """轉錄途中使用者取消；runner 轉成 JobCancelled。"""


def download_audio(video_id: str, tmp_dir: Path, progress_hook=None) -> Path:
    tmp_dir.mkdir(parents=True, exist_ok=True)
    opts = {
        "format": "bestaudio",
        "outtmpl": str(tmp_dir / "audio.%(ext)s"),
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
            return Path(path or ydl.prepare_filename(info))
    except yt_dlp.utils.DownloadError as e:
        raise AppError("audio_download_failed", "音訊下載失敗") from e


def _get_model():
    global _model
    with _model_lock:
        if _model is None:
            # 延遲 import：faster_whisper 會載入 ctranslate2，啟動伺服器時不需要付這個成本
            from faster_whisper import WhisperModel

            _model = WhisperModel(config.WHISPER_MODEL, device=config.WHISPER_DEVICE,
                                  compute_type=config.WHISPER_COMPUTE)
        return _model


def _decode_audio(audio: Path) -> np.ndarray:
    """用 ffmpeg 解成 16 kHz 單聲道 float32。

    不交給 faster-whisper 自己解：它的 decode_audio 會傳 metadata_errors 給 av.open，
    PyAV 19 已移除這個參數，直接丟 TypeError。
    """
    cmd = ["ffmpeg", "-nostdin", "-loglevel", "error", "-i", str(audio),
           "-f", "s16le", "-ac", "1", "-ar", str(SAMPLE_RATE), "-"]
    try:
        proc = subprocess.run(cmd, capture_output=True, check=False)
    except OSError as e:
        raise AppError("audio_decode_failed", "音訊解碼失敗，請確認 ffmpeg 已安裝") from e
    if proc.returncode != 0:
        raise AppError("audio_decode_failed", "音訊解碼失敗")
    return np.frombuffer(proc.stdout, np.int16).astype(np.float32) / 32768.0


def transcribe(audio: Path, language: str | None, cancelled: Callable[[], bool] | None = None,
               progress: Callable[[float], None] | None = None) -> tuple[list[Cue], str]:
    """回傳 (整理後的 cues, Whisper 偵測到的語言)；progress 收到 0–1 的比例。"""
    # Whisper 只認 base code（en、zh），YouTube 的 en-US、zh-Hant 要先去掉地區
    whisper_lang = language.split("-")[0] if language else None
    samples = _decode_audio(audio)
    segments, info = _get_model().transcribe(samples, language=whisper_lang, vad_filter=True)
    cues: list[Cue] = []
    # segments 是 generator，真正的運算發生在逐段走訪時，所以取消要在迴圈裡檢查
    for segment in segments:
        if cancelled is not None and cancelled():
            raise TranscriptionCancelled()
        cues.append(Cue(id=len(cues), start=segment.start, end=segment.end, text=segment.text.strip()))
        if progress is not None and info.duration:
            progress(min(segment.end / info.duration, 1.0))
    return normalize_cues(cues, info.language), info.language
