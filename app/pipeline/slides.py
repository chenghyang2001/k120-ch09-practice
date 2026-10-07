"""截圖 → 去重 → WebP／縮圖 → slides.json。"""

import json
import shutil
from pathlib import Path

from app.config import DEDUPE_THRESHOLD
from app.errors import AppError
from app.pipeline.dedupe import SlideGroup, group_frames, phash
from app.pipeline.frames import extract_frames, midpoints
from app.pipeline.images import make_thumb, save_webp
from app.pipeline.subtitles import Cue


def _make_slide(n: int, group: SlideGroup, cues: list[Cue], frames: list[Path],
                job_dir: Path) -> dict:
    name = f"{n:04d}.webp"
    frame = frames[group.frame_index]
    save_webp(frame, job_dir / "images" / name)
    make_thumb(frame, job_dir / "thumbs" / name)
    members = [cues[i] for i in group.cue_indices]
    return {
        "index": n,
        "start": members[0].start,
        "end": members[-1].end,
        "image": f"images/{name}",
        "thumb": f"thumbs/{name}",
        "cues": [{"start": c.start, "end": c.end, "text": c.text} for c in members],
    }


def build_slides(cues: list[Cue], video: Path, job_dir: Path, tmp_dir: Path,
                 threshold: int = DEDUPE_THRESHOLD) -> list[dict]:
    if not cues:
        raise AppError("no_cues", "沒有字幕可產生投影片")
    frames_dir = tmp_dir / "frames"
    try:
        frames = extract_frames(video, midpoints(cues), frames_dir)
        groups = group_frames(cues, [phash(f) for f in frames], threshold=threshold)
        slides = [_make_slide(n, g, cues, frames, job_dir) for n, g in enumerate(groups, 1)]
        job_dir.mkdir(parents=True, exist_ok=True)
        (job_dir / "slides.json").write_text(
            json.dumps(slides, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return slides
    finally:
        # PNG 截圖很佔空間，無論成敗都要清掉
        shutil.rmtree(frames_dir, ignore_errors=True)
