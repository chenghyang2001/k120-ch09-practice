"""截圖轉 WebP 與縮圖。"""

from pathlib import Path

from PIL import Image, UnidentifiedImageError

from app.config import THUMB_WIDTH, WEBP_QUALITY
from app.errors import AppError


def _open_rgb(src: Path) -> Image.Image:
    try:
        with Image.open(src) as img:
            # WebP 不支援部分調色盤／CMYK 模式，統一轉 RGB 才穩定
            return img.convert("RGB")
    except (FileNotFoundError, UnidentifiedImageError, OSError) as e:
        raise AppError("image_failed", "圖片處理失敗") from e


def _save(img: Image.Image, dst: Path, quality: int) -> Path:
    dst.parent.mkdir(parents=True, exist_ok=True)
    img.save(dst, "WEBP", quality=quality)
    return dst


def save_webp(src: Path, dst: Path, quality: int = WEBP_QUALITY) -> Path:
    return _save(_open_rgb(src), dst, quality)


def make_thumb(src: Path, dst: Path, width: int = THUMB_WIDTH) -> Path:
    img = _open_rgb(src)
    if img.width > width:
        height = max(1, round(img.height * width / img.width))
        img = img.resize((width, height), Image.LANCZOS)
    return _save(img, dst, WEBP_QUALITY)
