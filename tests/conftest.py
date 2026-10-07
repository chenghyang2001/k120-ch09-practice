"""測試共用 fixture。"""

import json
from pathlib import Path

import pytest

FIXTURES_DIR = Path(__file__).parent / "fixtures"


@pytest.fixture
def load_json3():
    def _load(name: str) -> dict:
        return json.loads((FIXTURES_DIR / name).read_text(encoding="utf-8"))

    return _load


# M2：合成測試影片。三段都用靜態圖樣，testsrc／mandelbrot 會隨時間變化，段內 pHash 就會超過門檻
SEGMENT_SOURCES = (
    "smptebars=s=320x240:d=4:r=10",
    "rgbtestsrc=s=320x240:d=4:r=10",
    (
        "color=c=black:s=320x240:d=4:r=10,"
        "drawbox=x=0:y=0:w=160:h=120:c=white:t=fill,"
        "drawbox=x=160:y=120:w=160:h=120:c=white:t=fill"
    ),
)


@pytest.fixture
def synthetic_video(tmp_path):
    """12 秒影片：0–4 秒 A、4–8 秒 B、8–12 秒 C，三段畫面明顯不同。"""
    import shutil
    import subprocess

    if shutil.which("ffmpeg") is None:
        pytest.skip("系統沒有 ffmpeg")
    video = tmp_path / "synthetic.mp4"
    cmd = ["ffmpeg", "-nostdin", "-loglevel", "error"]
    for source in SEGMENT_SOURCES:
        cmd += ["-f", "lavfi", "-i", source]
    cmd += ["-filter_complex", "[0][1][2]concat=n=3:v=1:a=0,format=yuv420p",
            "-c:v", "libx264", "-y", str(video)]
    proc = subprocess.run(cmd, capture_output=True, encoding="utf-8", check=False)
    assert proc.returncode == 0, proc.stderr
    return video
