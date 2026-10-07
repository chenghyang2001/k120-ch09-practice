"""字幕挑選、下載 json3、解析、自動字幕重新斷句與整理。"""

import json
import re
from dataclasses import dataclass, replace
from typing import Literal

import yt_dlp

from app.config import (
    CJK_LANGS,
    MAX_CHARS_CJK,
    MAX_CUE_SEC,
    MAX_WORDS_SPACED,
    MIN_CUE_SEC,
    PAUSE_SPLIT_SEC,
    SENTENCE_END,
    WORD_MAX_SEC,
)
from app.errors import AppError

Word = tuple[float, float, str]  # (start, end, text)

# 半形句號只在後面接空白時才切，避免把 3.5、U.S. 這類字切斷；全形標點後面本來就不接空白
_ASCII_END = "".join(c for c in SENTENCE_END if c.isascii())
_WIDE_END = "".join(c for c in SENTENCE_END if not c.isascii())
_SPLIT_RE = re.compile(
    rf"(?<=[{re.escape(_ASCII_END)}])\s+|(?<=[{re.escape(_WIDE_END)}])(?![{re.escape(_WIDE_END)}])"
)
# [Music]、[Applause]、[音樂] 這類音效標記不是講話內容，必須自成一句
_SOUND_TAG_RE = re.compile(r"^\[.*\]$")


@dataclass
class Cue:
    id: int
    start: float
    end: float
    text: str
    translation: str | None = None


@dataclass(frozen=True)
class SubtitleTrack:
    kind: Literal["manual", "auto"]
    lang: str
    url: str


def _base(lang: str | None) -> str:
    return (lang or "").split("-")[0].lower()


def _is_cjk(lang: str | None) -> bool:
    return _base(lang) in CJK_LANGS


def _is_sound_tag(text: str) -> bool:
    return _SOUND_TAG_RE.match(text.strip()) is not None


def _json3_url(formats: list[dict] | None) -> str | None:
    for fmt in formats or []:
        if fmt.get("ext") == "json3" and fmt.get("url"):
            return fmt["url"]
    return None


def choose_subtitle_track(info: dict, lang: str) -> SubtitleTrack | None:
    """依「人工完全相同 → 人工同 base code → 自動 -orig → 自動同代碼 → 自動同 base code」挑出有 json3 的字幕。"""
    manual = {k: v for k, v in (info.get("subtitles") or {}).items() if k != "live_chat"}
    auto = info.get("automatic_captions") or {}

    candidates: list[tuple[Literal["manual", "auto"], str, list | None]] = []
    if lang in manual:
        candidates.append(("manual", lang, manual[lang]))
    candidates += [("manual", k, v) for k, v in manual.items() if k != lang and _base(k) == _base(lang)]
    base = _base(lang)
    auto_keys = dict.fromkeys((f"{lang}-orig", lang, f"{base}-orig", base))  # 去重但保留順序
    candidates += [("auto", key, auto[key]) for key in auto_keys if key in auto]

    for kind, key, formats in candidates:
        url = _json3_url(formats)
        # 帶 tlang= 的是 YouTube 機器翻譯軌，不是原文字幕；寧可交給 Whisper
        if kind == "auto" and not key.endswith("-orig") and url is not None and "tlang=" in url:
            continue
        if url is not None:
            return SubtitleTrack(kind, key.removesuffix("-orig"), url)
    return None


def download_track(track: SubtitleTrack) -> dict:
    """透過 yt-dlp 的連線下載 json3（沿用它的 headers，YouTube 才不會回 403）。"""
    try:
        with yt_dlp.YoutubeDL({"quiet": True}) as ydl:
            raw = ydl.urlopen(track.url).read()
        return json.loads(raw.decode("utf-8"))
    except (yt_dlp.utils.YoutubeDLError, OSError, ValueError) as e:
        # ValueError 涵蓋 UnicodeDecodeError 與 JSONDecodeError；例外訊息可能含帶簽章的 URL，不顯示給使用者
        raise AppError("subtitle_download_failed", "字幕下載失敗，請稍後再試") from e


def _parse_manual(events: list[dict], lang: str | None) -> list[Cue]:
    sep = "" if _is_cjk(lang) else " "
    cues: list[Cue] = []
    for event in events:
        segs = event.get("segs")
        if not segs:
            continue
        lines = "".join(seg.get("utf8", "") for seg in segs).split("\n")
        text = sep.join(line.strip() for line in lines if line.strip())
        if not text:
            continue
        start_ms = event.get("tStartMs", 0)
        end_ms = start_ms + event.get("dDurationMs", 0)
        cues.append(Cue(len(cues), start_ms / 1000, end_ms / 1000, text))
    return cues


def _flatten_words(events: list[dict], lang: str | None) -> list[Word]:
    """把自動字幕的逐字 seg 攤平成 (start, end, text)。"""
    # 先收 (start, event_end, is_event_last, is_per_word, text)，end 要等看到下一個字才能決定
    raw: list[tuple[float, float, bool, bool, str]] = []
    for event in events:
        segs = event.get("segs") or []
        texts = [seg.get("utf8", "") for seg in segs]
        if event.get("aAppend") == 1 and not "".join(texts).strip():
            continue
        start_ms = event.get("tStartMs", 0)
        event_end = (start_ms + event.get("dDurationMs", 0)) / 1000
        kept = [(seg, text) for seg, text in zip(segs, texts) if text.strip()]
        for i, (seg, text) in enumerate(kept):
            # 只有 event 內第 2 個以後的 seg 自帶前導空白；event 第一個字不補空白會和上一行黏在一起
            if i == 0 and not _is_cjk(lang) and not text[0].isspace():
                text = " " + text
            start = (start_ms + seg.get("tOffsetMs", 0)) / 1000
            raw.append((start, event_end, i == len(kept) - 1, len(kept) > 1, text))

    words: list[Word] = []
    for i, (start, event_end, is_event_last, is_per_word, text) in enumerate(raw):
        if words and _is_sound_tag(text):
            # 音效標記常和講話重疊；把它往後推，讓時間維持單調，變成零長度的交給 normalize 濾掉
            start = max(start, words[-1][1])
        next_word = raw[i + 1] if i + 1 < len(raw) else None
        # 下一個是音效標記時不拿它的 start 當上限，否則重疊的整句會被截成零點幾秒
        next_start = next_word[0] if next_word is not None and not _is_sound_tag(next_word[4]) else None
        # 逐字 event 的區間常和下一行重疊，單靠「不超過下一個字的開始」幾乎量不到停頓，所以加單字時長上限；
        # 單一 seg 的 event 是整句（例如部分日文自動字幕），套上限會把整句截成 0.5 秒
        limits = [start + WORD_MAX_SEC] if is_per_word else []
        if next_start is not None:
            limits.append(next_start)
        if is_event_last or next_start is None:
            limits.append(event_end)
        words.append((start, max(min(limits), start), text))
    return words


def _reached_limit(text: str, lang: str | None) -> bool:
    if _is_cjk(lang):
        return len("".join(text.split())) >= MAX_CHARS_CJK
    return len(text.split()) >= MAX_WORDS_SPACED


def resegment_words(words: list[Word], lang: str | None) -> list[Cue]:
    """依停頓、句尾標點、音效標記、字數與時長上限把逐字資料重新組成句子。"""
    cues: list[Cue] = []
    current: list[Word] = []
    for i, word in enumerate(words):
        current.append(word)
        text = "".join(w[2] for w in current).strip()
        next_word = words[i + 1] if i + 1 < len(words) else None
        should_split = (
            next_word is None
            or next_word[0] - word[1] >= PAUSE_SPLIT_SEC
            or (text != "" and text[-1] in SENTENCE_END)
            or _reached_limit(text, lang)
            or _is_sound_tag(word[2])
            or _is_sound_tag(next_word[2])
            # 沒有標點又不停頓的長段落：加入下一個字就會超過時長上限時先切
            or next_word[1] - current[0][0] > MAX_CUE_SEC
        )
        if should_split:
            if text:
                cues.append(Cue(len(cues), current[0][0], current[-1][1], text))
            current = []
    return cues


def parse_json3(data: dict, kind: Literal["manual", "auto"], lang: str | None = None) -> list[Cue]:
    """解析 json3；英文自動字幕 event 內的 seg 已自帶前導空白，直接串接即可。"""
    events = data.get("events") or []
    if kind == "manual":
        return _parse_manual(events, lang)
    return resegment_words(_flatten_words(events, lang), lang)


def _join(first: Cue, second: Cue, lang: str | None) -> Cue:
    sep = "" if _is_cjk(lang) else " "
    return Cue(first.id, first.start, second.end, f"{first.text}{sep}{second.text}")


def _fix_overlaps(cues: list[Cue], lang: str | None) -> list[Cue]:
    fixed: list[Cue] = []
    for cue in cues:
        if fixed and fixed[-1].end > cue.start:
            prev = fixed[-1]
            if cue.start <= prev.start:
                # 截短後會變零長度，改把前一句的文字併進來，避免字幕內容遺失
                fixed.pop()
                cue = replace(_join(prev, cue, lang), id=cue.id, end=max(prev.end, cue.end))
            else:
                prev.end = cue.start
        fixed.append(cue)
    return fixed


def _can_merge(first: Cue, second: Cue | None) -> bool:
    if second is None or _is_sound_tag(first.text) or _is_sound_tag(second.text):
        return False
    return second.start - first.end < PAUSE_SPLIT_SEC


def _merge_short(cues: list[Cue], lang: str | None) -> list[Cue]:
    """短句優先併入下一句，不行再併入前一句；間隔達停頓門檻就不併，避免 [Music] 和講話黏在一起。"""
    merged: list[Cue] = []
    carry: Cue | None = None
    for i, cue in enumerate(cues):
        if carry is not None:
            cue = _join(carry, cue, lang)
            carry = None
        if cue.end - cue.start < MIN_CUE_SEC:
            next_cue = cues[i + 1] if i + 1 < len(cues) else None
            if _can_merge(cue, next_cue):
                carry = cue
                continue
            if merged and _can_merge(merged[-1], cue):
                merged[-1] = _join(merged[-1], cue, lang)
                continue
        merged.append(cue)
    return merged


def _split_long(cue: Cue) -> list[Cue]:
    if cue.end - cue.start <= MAX_CUE_SEC:
        return [cue]
    pieces = [p.strip() for p in _SPLIT_RE.split(cue.text) if p.strip()]
    if len(pieces) < 2:
        return [cue]
    total_chars = sum(len(p) for p in pieces)
    duration = cue.end - cue.start
    result: list[Cue] = []
    start = cue.start
    for i, piece in enumerate(pieces):
        # 最後一段直接對齊原本的結束時間，避免浮點誤差
        end = cue.end if i == len(pieces) - 1 else start + duration * len(piece) / total_chars
        result.append(Cue(cue.id, start, end, piece))
        start = end
    return result


def normalize_cues(cues: list[Cue], lang: str | None = None) -> list[Cue]:
    """排序、去除無效、修正重疊、合併短句、切開長句、重新編號。"""
    ordered = sorted((replace(c) for c in cues), key=lambda c: c.start)
    valid = [c for c in ordered if c.text.strip() and c.end > c.start]
    merged = _merge_short(_fix_overlaps(valid, lang), lang)
    result = [piece for cue in merged for piece in _split_long(cue)]
    for i, cue in enumerate(result):
        cue.id = i
    return result


def fetch_cues(info: dict, lang: str) -> tuple[list[Cue], SubtitleTrack] | None:
    """挑字幕 → 下載 → 解析 → 整理；沒有可用字幕時回傳 None。"""
    track = choose_subtitle_track(info, lang)
    if track is None:
        return None
    data = download_track(track)
    cues = normalize_cues(parse_json3(data, track.kind, track.lang), track.lang)
    if not cues:
        return None
    return cues, track
