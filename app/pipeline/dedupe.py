"""以 pHash 把連續相似的截圖合併成同一頁投影片。"""

from dataclasses import dataclass
from pathlib import Path

import imagehash
from PIL import Image

from app.config import DEDUPE_THRESHOLD, MAX_SLIDE_CUES, MAX_SLIDE_SEC
from app.pipeline.subtitles import Cue


@dataclass
class SlideGroup:
    cue_indices: list[int]
    # 代表圖取組內最後一張：簡報常逐條出現條列，最後一張內容最完整
    frame_index: int


def phash(path: Path) -> imagehash.ImageHash:
    with Image.open(path) as img:
        return imagehash.phash(img)


def _fits(group: list[int], i: int, cues: list[Cue], hashes: list, threshold: int,
          max_sec: float, max_cues: int) -> bool:
    first = group[0]
    # 跟組內第一張比，而非前一張：緩慢變化的畫面才不會一路累積偏移、整支影片變一頁
    return (
        hashes[first] - hashes[i] <= threshold
        and cues[i].end - cues[first].start <= max_sec
        and len(group) < max_cues
    )


def group_frames(cues: list[Cue], hashes: list[imagehash.ImageHash],
                 threshold: int = DEDUPE_THRESHOLD, max_sec: float = MAX_SLIDE_SEC,
                 max_cues: int = MAX_SLIDE_CUES) -> list[SlideGroup]:
    if len(cues) != len(hashes):
        raise ValueError(f"cues 與 hashes 數量不符：{len(cues)} != {len(hashes)}")
    groups: list[list[int]] = []
    for i in range(len(cues)):
        if groups and _fits(groups[-1], i, cues, hashes, threshold, max_sec, max_cues):
            groups[-1].append(i)
        else:
            groups.append([i])
    return [SlideGroup(cue_indices=g, frame_index=g[-1]) for g in groups]
