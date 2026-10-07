"""匯出：離線 HTML 單檔、PDF（Playwright 列印 HTML）、Markdown zip。"""

import base64
import io
import json
import tempfile
import zipfile
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from app.errors import AppError

FORMATS = ("html", "pdf", "md")
TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates"
MIME = {".webp": "image/webp", ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg"}

_env = Environment(loader=FileSystemLoader(TEMPLATES_DIR), autoescape=select_autoescape(["html"]))


class PdfUnavailable(AppError):
    """Chromium 未安裝；status 給 main.py 的錯誤處理器回 500。"""

    status = 500

    def __init__(self):
        super().__init__("pdf_unavailable", "PDF 匯出需要 Chromium，請執行 uv run playwright install chromium")


def fmt_time(sec: float) -> str:
    """mm:ss；超過一小時分鐘數照累加（75:03），和規格的 mm:ss 一致。"""
    total = int(sec or 0)
    return f"{total // 60:02d}:{total % 60:02d}"


def load_slides(job_dir: Path) -> list[dict]:
    return json.loads((job_dir / "slides.json").read_text(encoding="utf-8"))


def _data_uri(path: Path) -> str:
    mime = MIME.get(path.suffix.lower(), "application/octet-stream")
    return f"data:{mime};base64,{base64.b64encode(path.read_bytes()).decode('ascii')}"


def _safe_json(value) -> str:
    """嵌進 <script type="application/json">：</ 會提前關閉 script，<!-- 會讓解析器進入 escaped 狀態。"""
    text = json.dumps(value, ensure_ascii=False)
    return text.replace("</", "<\\/").replace("<!--", "\\u003c!--")


def build_html(job: dict, job_dir: Path) -> str:
    slides = load_slides(job_dir)
    embedded = [{**s, "image": _data_uri(job_dir / s["image"])} for s in slides]
    payload = {"title": job.get("title") or job["video_id"], "video_id": job["video_id"], "slides": embedded}
    return _env.get_template("export.html").render(title=payload["title"], data_json=_safe_json(payload))


def build_markdown_zip(job: dict, job_dir: Path) -> bytes:
    slides = load_slides(job_dir)
    lines = [f"# {job.get('title') or job['video_id']}", "",
             f"https://www.youtube.com/watch?v={job['video_id']}", ""]
    for s in slides:
        lines += [f"## {s['index']} · {fmt_time(s['start'])}–{fmt_time(s['end'])}", "", f"![]({s['image']})", ""]
        for cue in s.get("cues", []):
            lines.append(f"- **{fmt_time(cue['start'])}** {cue['text']}")
            if cue.get("translation"):
                lines.append(f"  - {cue['translation']}")
        lines.append("")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("slides.md", "\n".join(lines))
        for s in slides:
            zf.write(job_dir / s["image"], s["image"])
    return buf.getvalue()


def build_pdf(job: dict, job_dir: Path) -> bytes:
    """同步 Playwright：呼叫端必須放在 thread 裡跑，不可在 event loop 中直接呼叫。"""
    try:
        from playwright.sync_api import Error, sync_playwright
    except ImportError as e:
        raise PdfUnavailable() from e
    html = build_html(job, job_dir)
    with tempfile.TemporaryDirectory() as tmp:
        # 圖片內嵌後 HTML 可能數十 MB，寫檔用 goto 比 set_content 經 CDP 傳字串穩定
        page_path = Path(tmp) / "export.html"
        page_path.write_text(html, encoding="utf-8")
        try:
            with sync_playwright() as p:
                browser = p.chromium.launch()
                try:
                    page = browser.new_page()
                    page.goto(page_path.as_uri(), wait_until="load")
                    return page.pdf(format="A4", landscape=True, print_background=True)
                finally:
                    browser.close()
        except Error as e:
            if "Executable doesn't exist" in str(e):
                raise PdfUnavailable() from e
            raise
