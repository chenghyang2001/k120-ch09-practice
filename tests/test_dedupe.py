import imagehash
import numpy as np
import pytest
from PIL import Image, ImageDraw

from app.pipeline.dedupe import SlideGroup, group_frames, phash
from app.pipeline.subtitles import Cue


def _cues(n, step=2.0):
    return [Cue(id=i, start=i * step, end=(i + 1) * step, text=f"句{i}") for i in range(n)]


def _image():
    img = Image.new("L", (256, 256), 0)
    ImageDraw.Draw(img).rectangle([0, 0, 127, 255], fill=255)
    return img


def _bits(*on):
    """直接造 8x8 hash，方便精準控制漢明距離。"""
    arr = np.zeros(64, dtype=bool)
    arr[list(on)] = True
    return imagehash.ImageHash(arr.reshape(8, 8))


# 三種彼此距離遠大於門檻的畫面
A, B, C = _bits(), _bits(*range(20)), _bits(*range(40, 64))


def test_phash_from_file(tmp_path):
    path = tmp_path / "a.png"
    _image().save(path)
    assert phash(path) - imagehash.phash(_image()) == 0


def test_same_frames_merge():
    hashes = [A] * 3
    assert group_frames(_cues(3), hashes) == [SlideGroup(cue_indices=[0, 1, 2], frame_index=2)]


def test_different_frames_split():
    hashes = [A, A, B, C]
    groups = group_frames(_cues(4), hashes)
    assert [g.cue_indices for g in groups] == [[0, 1], [2], [3]]


def test_compares_with_first_not_previous():
    # 每張只比前一張多 4 bit：跟前一張比會全部合併，跟第一張比到第 3 張就超過門檻 8
    hashes = [_bits(), _bits(*range(4)), _bits(*range(8)), _bits(*range(12)), _bits(*range(16))]
    groups = group_frames(_cues(5), hashes, threshold=8)
    assert [g.cue_indices for g in groups] == [[0, 1, 2], [3, 4]]


def test_max_sec_forces_split():
    groups = group_frames(_cues(5, step=10.0), [A] * 5, max_sec=30.0)
    assert [g.cue_indices for g in groups] == [[0, 1, 2], [3, 4]]


def test_max_cues_forces_split():
    groups = group_frames(_cues(5), [A] * 5, max_cues=2)
    assert [g.cue_indices for g in groups] == [[0, 1], [2, 3], [4]]


def test_representative_is_last_frame():
    groups = group_frames(_cues(4), [A, A, B, B])
    assert [g.frame_index for g in groups] == [1, 3]


def test_length_mismatch_raises():
    with pytest.raises(ValueError):
        group_frames(_cues(2), [A])


def test_empty_input():
    assert group_frames([], []) == []
