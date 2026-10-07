"""M6 匯出：HTML（內嵌、跳脫）、Markdown zip、PDF、錯誤碼。"""

import io
import json
import zipfile
from pathlib import Path

import pytest
from PIL import Image

EVIL = "</script><script>alert(1)</script>"


def make_done_job(client, data_dir, *, status: str = "done") -> dict:
    """在 SQLite 建一筆工作，並在 data_dir/jobs/<id>/ 放 2 頁投影片。"""
    store = client.app.state.manager.store
    job = store.create("abcdefghijk", "https://youtu.be/abcdefghijk", 720, "zh-TW")
    store.update(job["id"], status=status, title="測試影片 <b>", duration=20.0, slide_count=2)
    job_dir = data_dir / "jobs" / job["id"]
    for sub in ("images", "thumbs"):
        (job_dir / sub).mkdir(parents=True)
    slides = []
    for i, color in enumerate(("red", "blue"), start=1):
        name = f"{i:04d}.webp"
        Image.new("RGB", (64, 36), color).save(job_dir / "images" / name, "WEBP")
        Image.new("RGB", (32, 18), color).save(job_dir / "thumbs" / name, "WEBP")
        start = (i - 1) * 10.0
        slides.append({"index": i, "start": start, "end": start + 10.0,
                       "image": f"images/{name}", "thumb": f"thumbs/{name}",
                       "cues": [{"start": start + 1, "end": start + 5, "text": f"hello {i}",
                                 "translation": f"你好 {i}"},
                                {"start": start + 5, "end": start + 9, "text": EVIL, "translation": EVIL}]})
    (job_dir / "slides.json").write_text(json.dumps(slides, ensure_ascii=False), encoding="utf-8")
    meta = {"title": "測試影片 <b>", "video_id": "abcdefghijk", "slide_count": 2}
    (job_dir / "meta.json").write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
    return store.get(job["id"])


@pytest.fixture
def client(make_client):
    with make_client() as c:
        yield c


def test_export_html_inlines_images_and_escapes(client, data_dir):
    job = make_done_job(client, data_dir)
    res = client.get(f"/api/jobs/{job['id']}/export?format=html")
    assert res.status_code == 200
    assert res.headers["content-type"].startswith("text/html")
    assert res.headers["content-disposition"] == 'attachment; filename="abcdefghijk.html"'
    html = res.text
    assert "data:image/webp;base64," in html
    assert '<script type="application/json"' in html
    assert EVIL not in html
    assert "<\\/script><script>alert(1)<\\/script>" in html
    # 標題走 Jinja autoescape
    assert "測試影片 &lt;b&gt;" in html
    assert "@media print" in html
    # 不依賴伺服器上的任何資源
    assert "/static/" not in html and "/media/" not in html


def test_export_html_json_roundtrip(client, data_dir):
    job = make_done_job(client, data_dir)
    html = client.get(f"/api/jobs/{job['id']}/export?format=html").text
    start = html.index('id="export-data">') + len('id="export-data">')
    payload = json.loads(html[start:html.index("</script>", start)])
    assert payload["video_id"] == "abcdefghijk"
    assert len(payload["slides"]) == 2
    assert payload["slides"][0]["cues"][1]["text"] == EVIL


def test_export_md_zip(client, data_dir):
    job = make_done_job(client, data_dir)
    res = client.get(f"/api/jobs/{job['id']}/export?format=md")
    assert res.status_code == 200
    assert res.headers["content-type"] == "application/zip"
    assert res.headers["content-disposition"] == 'attachment; filename="abcdefghijk.zip"'
    with zipfile.ZipFile(io.BytesIO(res.content)) as zf:
        assert sorted(zf.namelist()) == ["images/0001.webp", "images/0002.webp", "slides.md"]
        md = zf.read("slides.md").decode("utf-8")
        assert zf.read("images/0002.webp") == (data_dir / "jobs" / job["id"] / "images" / "0002.webp").read_bytes()
    assert "## 1 · 00:00–00:10" in md
    assert "## 2 · 00:10–00:20" in md
    assert "![](images/0001.webp)" in md
    assert "- **00:01** hello 1" in md
    assert "  - 你好 1" in md


def test_export_pdf(client, data_dir):
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        if not Path(p.chromium.executable_path).exists():
            pytest.skip("chromium 未安裝")
    job = make_done_job(client, data_dir)
    res = client.get(f"/api/jobs/{job['id']}/export?format=pdf")
    assert res.status_code == 200, res.text
    assert res.headers["content-type"] == "application/pdf"
    assert res.content.startswith(b"%PDF")


def test_export_pdf_unavailable(client, data_dir, monkeypatch):
    from app import main
    from app.pipeline import export

    def no_chromium(job, job_dir):
        raise export.PdfUnavailable()

    monkeypatch.setitem(main.EXPORTERS, "pdf", (no_chromium, "application/pdf", "pdf"))
    job = make_done_job(client, data_dir)
    res = client.get(f"/api/jobs/{job['id']}/export?format=pdf")
    assert res.status_code == 500
    assert res.json()["error"]["code"] == "pdf_unavailable"
    assert "playwright install chromium" in res.json()["error"]["message"]


def test_export_not_done_409(client, data_dir):
    job = make_done_job(client, data_dir, status="running")
    res = client.get(f"/api/jobs/{job['id']}/export?format=html")
    assert res.status_code == 409
    assert res.json()["error"]["code"] == "job_not_done"


def test_export_bad_format_400(client, data_dir):
    job = make_done_job(client, data_dir)
    res = client.get(f"/api/jobs/{job['id']}/export?format=docx")
    assert res.status_code == 400
    assert res.json()["error"]["code"] == "bad_format"


def test_export_unknown_job_404(client):
    res = client.get("/api/jobs/nope/export?format=html")
    assert res.status_code == 404


def test_fmt_time():
    from app.pipeline.export import fmt_time

    assert fmt_time(0) == "00:00"
    assert fmt_time(65.9) == "01:05"
    assert fmt_time(4503) == "75:03"
