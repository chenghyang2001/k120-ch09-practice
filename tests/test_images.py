import pytest
from PIL import Image

from app.errors import AppError
from app.pipeline.images import make_thumb, save_webp


def _png(path, size=(800, 600), mode="RGB"):
    Image.new(mode, size, "red" if mode == "RGB" else 128).save(path)
    return path


def test_save_webp_keeps_resolution(tmp_path):
    dst = save_webp(_png(tmp_path / "a.png"), tmp_path / "out" / "a.webp")
    with Image.open(dst) as img:
        assert img.format == "WEBP"
        assert img.size == (800, 600)


def test_save_webp_converts_palette_mode(tmp_path):
    src = tmp_path / "p.png"
    Image.new("P", (40, 30)).save(src)
    with Image.open(save_webp(src, tmp_path / "p.webp")) as img:
        assert img.mode == "RGB"


def test_make_thumb_width_320_keeps_ratio(tmp_path):
    dst = make_thumb(_png(tmp_path / "a.png"), tmp_path / "t" / "a.webp")
    with Image.open(dst) as img:
        assert img.format == "WEBP"
        assert img.size == (320, 240)


def test_make_thumb_does_not_upscale(tmp_path):
    dst = make_thumb(_png(tmp_path / "s.png", size=(200, 100)), tmp_path / "s.webp")
    with Image.open(dst) as img:
        assert img.size == (200, 100)


def test_broken_image_raises(tmp_path):
    bad = tmp_path / "bad.png"
    bad.write_bytes(b"not an image")
    with pytest.raises(AppError) as e:
        save_webp(bad, tmp_path / "bad.webp")
    assert e.value.code == "image_failed"


def test_missing_source_raises(tmp_path):
    with pytest.raises(AppError) as e:
        make_thumb(tmp_path / "nope.png", tmp_path / "nope.webp")
    assert e.value.code == "image_failed"
