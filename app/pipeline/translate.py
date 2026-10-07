"""字幕翻譯：同語言跳過 → YouTube 人工目標語言字幕對齊 → claude -p 分批翻譯（含降級）。"""

import json
import os
import shutil
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace

from app.config import (
    LANG_ALIASES,
    LANG_NAMES,
    TRANSLATE_BATCH,
    TRANSLATE_CONCURRENCY,
    TRANSLATE_MODEL,
    TRANSLATE_TIMEOUT_SEC,
)
from app.errors import AppError
from app.pipeline.subtitles import (
    Cue,
    SubtitleTrack,
    _base,
    _is_cjk,
    _json3_url,
    download_track,
    normalize_cues,
    parse_json3,
)

# 這些都代表「claude 這次回得不能用」，一律走重試／對半切的降級流程
_RETRYABLE = (RuntimeError, ValueError, OSError, subprocess.TimeoutExpired)


def _canonical(lang: str) -> str:
    for key, aliases in LANG_ALIASES.items():
        if lang in aliases:
            return key
    return _base(lang)


def same_language(source: str | None, target: str) -> bool:
    if source is None:
        return False
    return _canonical(source) == _canonical(target)


def find_manual_target(info: dict, target: str) -> str | None:
    """只看人工字幕；automatic_captions 裡的目標語言是 YouTube 機器翻譯，品質不如 claude。"""
    manual = info.get("subtitles") or {}
    for key in LANG_ALIASES.get(target, (target,)):
        if key in manual:
            url = _json3_url(manual[key])
            if url is not None:
                return url
    return None


def _overlap(a: Cue, b: Cue) -> float:
    return min(a.end, b.end) - max(a.start, b.start)


def align_translation(source: list[Cue], target: list[Cue], target_lang: str | None = None) -> list[Cue]:
    """把每句目標字幕併到時間重疊最多的原文句；兩邊斷句不同，只能靠時間對齊。"""
    pieces: dict[int, list[str]] = {}
    for t in sorted(target, key=lambda c: c.start):
        best_index, best_overlap = None, 0.0
        for i, s in enumerate(source):
            overlap = _overlap(s, t)
            if overlap > best_overlap:
                best_index, best_overlap = i, overlap
        if best_index is not None:
            pieces.setdefault(best_index, []).append(t.text)
    sep = "" if _is_cjk(target_lang) else " "
    return [replace(s, translation=sep.join(pieces[i])) if i in pieces else replace(s)
            for i, s in enumerate(source)]


def _claude_path() -> str | None:
    # Windows 上 claude 可能是 claude.cmd，必須用 which 回傳的完整路徑才叫得起來
    return shutil.which("claude")


def _build_prompt(batch: list[Cue], target: str) -> str:
    payload = json.dumps([{"id": c.id, "text": c.text} for c in batch], ensure_ascii=False)
    return (
        f"請把以下 JSON 陣列中每個物件的 text 翻譯成{LANG_NAMES[target]}。\n"
        "這些是影片字幕的連續片段，翻譯時請參考上下文，但每個 id 必須一對一對應，不可以合併或拆開。\n"
        "[Music] 這類方括號音效標記可以翻成對應語言的標記。\n"
        '只輸出 JSON 陣列 [{"id":..,"text":..}]，不要有任何說明，也不要加 code fence。\n\n'
        f"{payload}"
    )


def _call_claude(prompt: str) -> str:
    path = _claude_path()
    if path is None:
        raise RuntimeError("找不到 claude CLI")
    env = os.environ.copy()
    # 成本規則：CLI 只要看到 ANTHROPIC_API_KEY 就會改扣 API Credits，移除後才會走 Max 訂閱額度
    env.pop("ANTHROPIC_API_KEY", None)
    proc = subprocess.run(
        [path, "-p", "--model", TRANSLATE_MODEL],
        input=prompt, capture_output=True, encoding="utf-8",
        timeout=TRANSLATE_TIMEOUT_SEC, env=env, check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"claude 結束碼 {proc.returncode}：{(proc.stderr or '')[:200]}")
    return proc.stdout or ""


def _parse_reply(reply: str, batch: list[Cue]) -> dict[int, str]:
    """容許前後雜訊與 code fence，但 id 必須與送出的一模一樣、text 不可空。"""
    start, end = reply.find("["), reply.rfind("]")
    if start < 0 or end < start:
        raise ValueError("回覆中找不到 JSON 陣列")
    items = json.loads(reply[start:end + 1])
    if not isinstance(items, list) or len(items) != len(batch):
        raise ValueError("回覆數量不符")
    result: dict[int, str] = {}
    for item in items:
        if not isinstance(item, dict) or not isinstance(item.get("id"), int):
            raise ValueError("回覆格式錯誤")  # noqa: TRY004 降級流程統一捕捉 ValueError
        text = item.get("text")
        if not isinstance(text, str) or not text.strip():
            raise ValueError(f"id {item['id']} 的譯文是空的")
        result[item["id"]] = text.strip()
    if set(result) != {c.id for c in batch}:
        raise ValueError("回覆 id 不符")
    return result


def _translate_batch(batch: list[Cue], target: str) -> dict[int, str]:
    """失敗先重試 1 次，再失敗就對半切開遞迴；單句仍失敗就放棄這句，不讓整個工作失敗。"""
    prompt = _build_prompt(batch, target)
    for _ in range(2):
        try:
            return _parse_reply(_call_claude(prompt), batch)
        except _RETRYABLE as e:
            print(f"翻譯批次失敗（id {batch[0].id}–{batch[-1].id}）：{type(e).__name__}", file=sys.stderr)
            continue
    if len(batch) == 1:
        return {}
    mid = len(batch) // 2
    return {**_translate_batch(batch[:mid], target), **_translate_batch(batch[mid:], target)}


def _from_youtube_manual(cues: list[Cue], target: str, info: dict | None) -> list[Cue] | None:
    url = find_manual_target(info or {}, target)
    if url is None:
        return None
    try:
        data = download_track(SubtitleTrack("manual", target, url))
    except AppError:
        return None
    target_cues = normalize_cues(parse_json3(data, "manual", target), target)
    if not target_cues:
        return None
    return align_translation(cues, target_cues, target)


def translate_cues(cues: list[Cue], source_lang: str | None, target: str,
                   info: dict | None = None) -> tuple[list[Cue], str]:
    """回傳 (新 cues, status)；status 為 same_language／youtube_manual／claude_unavailable／claude／partial。"""
    if target not in LANG_NAMES:
        raise AppError("invalid_target_lang", "不支援的目標語言")
    if same_language(source_lang, target):
        return [replace(c) for c in cues], "same_language"
    aligned = _from_youtube_manual(cues, target, info)
    if aligned is not None:
        return aligned, "youtube_manual"
    if _claude_path() is None:
        return [replace(c) for c in cues], "claude_unavailable"

    batches = [cues[i:i + TRANSLATE_BATCH] for i in range(0, len(cues), TRANSLATE_BATCH)]
    translations: dict[int, str] = {}
    with ThreadPoolExecutor(TRANSLATE_CONCURRENCY) as pool:
        for part in pool.map(lambda b: _translate_batch(b, target), batches):
            translations.update(part)
    result = [replace(c, translation=translations.get(c.id)) for c in cues]
    status = "partial" if any(c.translation is None for c in result) else "claude"
    return result, status
