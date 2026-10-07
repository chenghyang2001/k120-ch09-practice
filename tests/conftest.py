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


# M4：API／工作佇列測試共用
@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    from app import config

    path = tmp_path / "data"
    monkeypatch.setattr(config, "DATA_DIR", path)
    return path


@pytest.fixture
def make_client(data_dir):
    """每次呼叫建立新的 app（重新讀 DATA_DIR）；需用 with 進入才會啟動 worker。"""
    from fastapi.testclient import TestClient

    from app.main import create_app

    def _make() -> TestClient:
        return TestClient(create_app(), raise_server_exceptions=False)

    return _make


@pytest.fixture
def wait_status():
    """輪詢 GET /api/jobs/{id} 直到狀態符合，逾時就讓測試失敗。"""
    import time

    def _wait(client, job_id: str, *statuses: str, timeout: float = 10.0) -> dict:
        deadline = time.monotonic() + timeout
        while True:
            job = client.get(f"/api/jobs/{job_id}").json()
            if job.get("status") in statuses:
                return job
            assert time.monotonic() < deadline, f"等待 {statuses} 逾時，目前：{job}"
            time.sleep(0.02)

    return _wait


@pytest.fixture
def fake_stages(monkeypatch):
    """把 runner 會連網的階段換成假的；download 由測試自行提供。"""
    from app.pipeline import runner
    from app.pipeline.subtitles import Cue, SubtitleTrack

    def _patch(download, has_subtitles: bool = True) -> None:
        # 每段 4 秒放 2 句，配合 synthetic_video 剛好三頁
        cues = [Cue(id=i, start=i * 2.0, end=i * 2.0 + 2.0, text=f"第{i}句") for i in range(6)]
        track = SubtitleTrack("manual", "en", "https://example.invalid/sub")
        monkeypatch.setattr(runner, "fetch_video_info",
                            lambda vid: {"id": vid, "title": "測試影片", "duration": 12.0, "language": "en"})
        monkeypatch.setattr(runner, "fetch_cues", lambda info, lang: (cues, track) if has_subtitles else None)
        monkeypatch.setattr(runner, "translate_cues", lambda c, src, tgt, info: (c, "claude"))
        monkeypatch.setattr(runner, "download_video", download)

    return _patch
