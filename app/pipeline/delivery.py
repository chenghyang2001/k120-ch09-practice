"""成果傳送：產生離線 HTML／PDF，上傳 Google Drive（rclone）或寄到 Gmail（SMTP）。"""

import logging
import mimetypes
import re
import shutil
import smtplib
import subprocess
from email.message import EmailMessage
from pathlib import Path

from app import config
from app.errors import AppError
from app.pipeline import export

logger = logging.getLogger(__name__)

UNSAFE_CHARS = re.compile(r'[\\/:*?"<>|]')


def drive_status() -> tuple[bool, str]:
    if shutil.which("rclone") is None:
        return False, "找不到 rclone，請執行 winget install Rclone.Rclone"
    try:
        proc = subprocess.run(["rclone", "listremotes"], capture_output=True, encoding="utf-8",
                              timeout=15, check=False)
    except (OSError, subprocess.TimeoutExpired) as e:
        logger.warning("執行 rclone listremotes 失敗：%s", e)
        return False, "無法執行 rclone listremotes"
    remote = f"{config.DRIVE_REMOTE}:"
    if remote not in (proc.stdout or "").split():
        return False, f"尚未設定 rclone remote '{config.DRIVE_REMOTE}'，請執行 rclone config"
    return True, ""


def gmail_status() -> tuple[bool, str]:
    if not config.GMAIL_USER or not config.GMAIL_APP_PASSWORD:
        return False, "尚未在 .env 設定 GMAIL_USER 與 GMAIL_APP_PASSWORD"
    return True, ""


def upload_to_drive(files: list[Path], folder: str) -> str:
    ok, reason = drive_status()
    if not ok:
        raise AppError("drive_unavailable", reason)
    dest = f"{config.DRIVE_REMOTE}:{config.DRIVE_DIR}/{folder}"
    for path in files:
        cmd = ["rclone", "copy", str(path), dest]
        if config.DRIVE_ROOT_FOLDER_ID:
            cmd += ["--drive-root-folder-id", config.DRIVE_ROOT_FOLDER_ID]
        try:
            proc = subprocess.run(cmd, capture_output=True, encoding="utf-8", timeout=300, check=False)
        except (OSError, subprocess.TimeoutExpired) as e:
            logger.error("rclone copy %s 執行失敗：%s", path, e)
            raise AppError("drive_failed", "上傳 Google Drive 失敗") from e
        if proc.returncode != 0:
            logger.error("rclone copy %s 失敗（exit %s）：%s", path, proc.returncode, proc.stderr)
            raise AppError("drive_failed", "上傳 Google Drive 失敗")
    return dest


def send_gmail(files: list[Path], subject: str, body: str) -> list[str]:
    ok, reason = gmail_status()
    if not ok:
        raise AppError("gmail_unavailable", reason)
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = config.GMAIL_USER
    msg["To"] = config.MAIL_TO or config.GMAIL_USER
    msg.set_content(body)
    attached, total = [], 0
    for path in files:
        size = path.stat().st_size
        if total + size > config.MAIL_MAX_BYTES:
            logger.warning("%s（%d bytes）超過 Gmail 附件上限，跳過", path.name, size)
            continue
        mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        maintype, subtype = mime.split("/", 1)
        msg.add_attachment(path.read_bytes(), maintype=maintype, subtype=subtype, filename=path.name)
        attached.append(path.name)
        total += size
    if not attached:
        raise AppError("mail_too_large", "檔案超過 Gmail 附件上限")
    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=60) as smtp:
            smtp.login(config.GMAIL_USER, config.GMAIL_APP_PASSWORD)
            smtp.send_message(msg)
    except smtplib.SMTPAuthenticationError as e:
        raise AppError("gmail_auth_failed", "Gmail 登入失敗，請確認應用程式密碼") from e
    except (smtplib.SMTPException, OSError) as e:
        logger.error("寄送 Gmail 失敗：%s", e)
        raise AppError("gmail_failed", "寄送 Gmail 失敗") from e
    return attached


def safe_name(title: str, video_id: str) -> str:
    name = UNSAFE_CHARS.sub("", title or "").strip()[:60]
    return f"{name}_{video_id}" if name else video_id


def build_deliverables(job_dir: Path, meta: dict, out_dir: Path) -> list[Path]:
    """離線 HTML 一定產生；PDF 需要 Chromium，失敗就只給 HTML。"""
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = safe_name(meta.get("title") or "", meta["video_id"])
    html_path = out_dir / f"{stem}.html"
    html_path.write_text(export.build_html(meta, job_dir), encoding="utf-8")
    files = [html_path]
    try:
        pdf_path = out_dir / f"{stem}.pdf"
        pdf_path.write_bytes(export.build_pdf(meta, job_dir))
        files.append(pdf_path)
    except Exception as e:  # PDF 是加分項，任何失敗都退回只給 HTML
        logger.warning("產生 PDF 失敗，只傳送 HTML：%s", e, exc_info=True)
    return files


def _mail_body(job: dict) -> str:
    video_url = f"https://www.youtube.com/watch?v={job['video_id']}"
    return "\n".join([
        job.get("title") or job["video_id"],
        "",
        f"YouTube 原始網址：{video_url}",
        f"頁數：{job.get('slide_count') or 0}",
        f"時長：{export.fmt_time(job.get('duration') or 0)}",
        "",
        "附件 HTML 可直接用瀏覽器開啟離線閱讀。",
    ])


def _send(target: str, files: list[Path], job: dict, folder: str) -> str:
    if target == "drive":
        return f"已上傳到 {upload_to_drive(files, folder)}"
    if target == "gmail":
        subject = f"YouTube 投影片：{job.get('title') or job['video_id']}"
        attached = send_gmail(files, subject, _mail_body(job))
        skipped = len(files) - len(attached)
        note = f"，{skipped} 個超過上限未附" if skipped else ""
        return f"已寄出（附件 {len(attached)} 個{note}）"
    raise AppError("bad_target", f"不支援的傳送目標：{target}")


def deliver(job: dict, targets: list[str]) -> dict[str, str]:
    """各 target 獨立：一個失敗寫成「失敗：…」，不影響其他。"""
    tmp_dir = config.DATA_DIR / "tmp" / f"deliver_{job['id']}"
    job_dir = config.DATA_DIR / "jobs" / job["id"]
    results: dict[str, str] = {}
    try:
        try:
            files = build_deliverables(job_dir, job, tmp_dir)
        except Exception as e:
            logger.exception("工作 %s 產生傳送檔案失敗", job["id"])
            message = e.message if isinstance(e, AppError) else "產生檔案失敗"
            return {t: f"失敗：{message}" for t in targets}
        folder = safe_name(job.get("title") or "", job["video_id"])
        for target in targets:
            try:
                results[target] = _send(target, files, job, folder)
            except AppError as e:
                results[target] = f"失敗：{e.message}"
            except Exception:
                logger.exception("工作 %s 傳送到 %s 發生未預期錯誤", job["id"], target)
                results[target] = "失敗：發生未預期錯誤"
        return results
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)
