import json
import shutil

import pytest
from PIL import Image

from app.errors import AppError
from app.pipeline import slides as slides_mod
from app.pipeline.slides import build_slides
from app.pipeline.subtitles import Cue

requires_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="系統沒有 ffmpeg")


def _six_cues():
    # 每段 4 秒放 2 句，中點分別落在 1、3、5、7、9、11 秒
    return [Cue(id=i, start=i * 2.0, end=i * 2.0 + 2.0, text=f"第{i}句") for i in range(6)]


@requires_ffmpeg
def test_build_slides_three_pages(synthetic_video, tmp_path):
    job_dir, tmp_dir = tmp_path / "job", tmp_path / "tmp"
    cues = _six_cues()
    cues[2].translation = "Sentence 2"
    result = build_slides(cues, synthetic_video, job_dir, tmp_dir)

    assert [s["index"] for s in result] == [1, 2, 3]
    assert [(s["start"], s["end"]) for s in result] == [(0.0, 4.0), (4.0, 8.0), (8.0, 12.0)]
    assert result[0]["image"] == "images/0001.webp"
    assert result[0]["thumb"] == "thumbs/0001.webp"
    assert [c["text"] for c in result[1]["cues"]] == ["第2句", "第3句"]
    assert [c["translation"] for c in result[1]["cues"]] == ["Sentence 2", None]
    for s in result:
        with Image.open(job_dir / s["image"]) as img:
            assert img.format == "WEBP" and img.size == (320, 240)
        assert (job_dir / s["thumb"]).is_file()

    saved = json.loads((job_dir / "slides.json").read_text(encoding="utf-8"))
    assert saved == result
    assert "第0句" in (job_dir / "slides.json").read_text(encoding="utf-8")
    assert not (tmp_dir / "frames").exists()


def test_build_slides_cleans_frames_on_failure(monkeypatch, tmp_path):
    tmp_dir = tmp_path / "tmp"

    def broken_extract(video, timestamps, out_dir):
        out_dir.mkdir(parents=True)
        (out_dir / "00000.png").write_bytes(b"partial")
        raise AppError("frame_failed", "截圖失敗")

    monkeypatch.setattr(slides_mod, "extract_frames", broken_extract)
    with pytest.raises(AppError) as e:
        build_slides(_six_cues(), tmp_path / "v.mp4", tmp_path / "job", tmp_dir)
    assert e.value.code == "frame_failed"
    assert not (tmp_dir / "frames").exists()


def test_build_slides_no_cues(tmp_path):
    with pytest.raises(AppError) as e:
        build_slides([], tmp_path / "v.mp4", tmp_path / "job", tmp_path / "tmp")
    assert e.value.code == "no_cues"
