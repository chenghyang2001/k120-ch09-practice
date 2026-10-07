"""M3 翻譯測試：全部 mock subprocess／which／download_track，不呼叫真的 claude、不連網。"""

import json
import subprocess
from types import SimpleNamespace

import pytest

from app.errors import AppError
from app.pipeline import translate as tr
from app.pipeline.subtitles import Cue


def _cues(n: int) -> list[Cue]:
    return [Cue(i, i * 2.0, i * 2.0 + 2.0, f"sentence {i}") for i in range(n)]


def _payload(prompt: str) -> list[dict]:
    # prompt 最後一段就是送出的 JSON
    return json.loads(prompt.rsplit("\n\n", 1)[1])


def _ok_reply(items: list[dict]) -> str:
    return json.dumps([{"id": it["id"], "text": f"譯{it['id']}"} for it in items], ensure_ascii=False)


@pytest.fixture
def fake_claude(monkeypatch):
    """回傳 calls 清單；測試用 state["reply"] 指定回覆函式 (items, call_no) -> stdout。"""
    calls: list[dict] = []
    state = {"reply": lambda items, n: _ok_reply(items), "returncode": 0}

    def fake_run(cmd, **kwargs):
        items = _payload(kwargs["input"])
        calls.append({"cmd": cmd, "env": kwargs["env"], "items": items})
        return SimpleNamespace(returncode=state["returncode"], stdout=state["reply"](items, len(calls)), stderr="")

    monkeypatch.setattr(tr.shutil, "which", lambda name: "C:/fake/claude.cmd")
    monkeypatch.setattr(tr.subprocess, "run", fake_run)
    return calls, state


# ---------- same_language ----------

@pytest.mark.parametrize("source, target, expected", [
    ("zh-Hant", "zh-TW", True),
    ("zh-TW", "zh-TW", True),
    ("en-US", "en", True),
    ("zh-Hans", "zh-TW", False),
    ("ja", "en", False),
    (None, "zh-TW", False),
])
def test_same_language(source, target, expected):
    assert tr.same_language(source, target) is expected


# ---------- find_manual_target ----------

def _fmt(ext: str, url: str) -> dict:
    return {"ext": ext, "url": url}


def test_find_manual_target_alias():
    info = {"subtitles": {"en": [_fmt("json3", "u-en")], "zh-Hant": [_fmt("vtt", "v"), _fmt("json3", "u-hant")]}}
    assert tr.find_manual_target(info, "zh-TW") == "u-hant"


def test_find_manual_target_ignores_auto():
    info = {"subtitles": {}, "automatic_captions": {"zh-TW": [_fmt("json3", "auto")]}}
    assert tr.find_manual_target(info, "zh-TW") is None


def test_find_manual_target_no_json3():
    info = {"subtitles": {"zh-TW": [_fmt("vtt", "v")]}}
    assert tr.find_manual_target(info, "zh-TW") is None


# ---------- align_translation ----------

def test_align_one_to_one():
    source = [Cue(0, 0, 2, "a"), Cue(1, 2, 4, "b")]
    target = [Cue(0, 0.1, 2.1, "甲"), Cue(1, 2.1, 4, "乙")]
    assert [c.translation for c in tr.align_translation(source, target, "zh-TW")] == ["甲", "乙"]


def test_align_many_to_one_cjk_no_space():
    source = [Cue(0, 0, 4, "a long sentence")]
    target = [Cue(1, 2, 4, "後半"), Cue(0, 0, 2, "前半")]
    assert tr.align_translation(source, target, "zh-TW")[0].translation == "前半後半"


def test_align_many_to_one_spaced():
    source = [Cue(0, 0, 4, "甲乙")]
    target = [Cue(0, 0, 2, "first"), Cue(1, 2, 4, "second")]
    assert tr.align_translation(source, target, "en")[0].translation == "first second"


def test_align_no_overlap():
    source = [Cue(0, 0, 2, "a")]
    target = [Cue(0, 5, 6, "甲")]
    assert tr.align_translation(source, target, "zh-TW")[0].translation is None


def test_align_does_not_mutate_input():
    source = [Cue(0, 0, 2, "a")]
    result = tr.align_translation(source, [Cue(0, 0, 2, "甲")], "zh-TW")
    assert source[0].translation is None
    assert result[0] is not source[0]


# ---------- _parse_reply ----------

BATCH = [Cue(0, 0, 1, "a"), Cue(1, 1, 2, "b")]
GOOD = '[{"id": 0, "text": "甲"}, {"id": 1, "text": "乙"}]'


def test_parse_plain():
    assert tr._parse_reply(GOOD, BATCH) == {0: "甲", 1: "乙"}


def test_parse_code_fence():
    assert tr._parse_reply(f"```json\n{GOOD}\n```", BATCH) == {0: "甲", 1: "乙"}


def test_parse_noise():
    assert tr._parse_reply(f"好的，以下是翻譯：\n{GOOD}\n完成。", BATCH) == {0: "甲", 1: "乙"}


def test_parse_id_mismatch():
    with pytest.raises(ValueError):
        tr._parse_reply('[{"id": 0, "text": "甲"}, {"id": 5, "text": "乙"}]', BATCH)


def test_parse_count_mismatch():
    with pytest.raises(ValueError):
        tr._parse_reply('[{"id": 0, "text": "甲"}]', BATCH)


def test_parse_empty_text():
    with pytest.raises(ValueError):
        tr._parse_reply('[{"id": 0, "text": "甲"}, {"id": 1, "text": "  "}]', BATCH)


def test_parse_not_json():
    with pytest.raises(ValueError):
        tr._parse_reply("抱歉，我無法翻譯", BATCH)


# ---------- translate_cues ----------

def test_translate_success_three_batches_without_api_key(fake_claude, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-fake")
    calls, _ = fake_claude
    cues = _cues(45)
    result, status = tr.translate_cues(cues, "en", "zh-TW")

    assert status == "claude"
    assert sorted(len(c["items"]) for c in calls) == [5, 20, 20]
    assert [c.translation for c in result] == [f"譯{i}" for i in range(45)]
    assert all(c.translation is None for c in cues)  # 不修改輸入
    for call in calls:
        assert "ANTHROPIC_API_KEY" not in call["env"]
        assert call["cmd"] == ["C:/fake/claude.cmd", "-p", "--model", "haiku", "--setting-sources=", "--strict-mcp-config"]


def test_translate_retry_after_non_json(fake_claude):
    calls, state = fake_claude
    state["reply"] = lambda items, n: "我不是 JSON" if n == 1 else _ok_reply(items)
    result, status = tr.translate_cues(_cues(3), "en", "zh-TW")
    assert status == "claude"
    assert len(calls) == 2
    assert [c.translation for c in result] == ["譯0", "譯1", "譯2"]


def test_translate_split_in_half(fake_claude):
    calls, state = fake_claude
    # 整批 4 句時永遠少回一句；切成 2 句一批就正常
    state["reply"] = lambda items, n: _ok_reply(items[:-1] if len(items) == 4 else items)
    result, status = tr.translate_cues(_cues(4), "en", "zh-TW")
    assert status == "claude"
    assert [len(c["items"]) for c in calls] == [4, 4, 2, 2]
    assert all(c.translation is not None for c in result)


def test_translate_single_cue_fails_partial(fake_claude):
    _, state = fake_claude
    # id 1 永遠回空字串
    state["reply"] = lambda items, n: json.dumps(
        [{"id": it["id"], "text": "" if it["id"] == 1 else "ok"} for it in items])
    result, status = tr.translate_cues(_cues(3), "en", "zh-TW")
    assert status == "partial"
    assert [c.translation for c in result] == ["ok", None, "ok"]


def test_translate_timeout_and_returncode_degrade(fake_claude, monkeypatch):
    def boom(cmd, **kwargs):
        raise subprocess.TimeoutExpired(cmd, 180)

    monkeypatch.setattr(tr.subprocess, "run", boom)
    result, status = tr.translate_cues(_cues(2), "en", "zh-TW")
    assert status == "partial"
    assert all(c.translation is None for c in result)


def test_translate_nonzero_returncode(fake_claude):
    _, state = fake_claude
    state["returncode"] = 1
    _, status = tr.translate_cues(_cues(1), "en", "ja")
    assert status == "partial"


def test_translate_claude_unavailable(monkeypatch):
    monkeypatch.setattr(tr.shutil, "which", lambda name: None)
    cues = _cues(2)
    result, status = tr.translate_cues(cues, "en", "zh-TW")
    assert status == "claude_unavailable"
    assert [c.text for c in result] == [c.text for c in cues]
    assert all(c.translation is None for c in result)


def test_translate_same_language(monkeypatch):
    def must_not_run(*a, **k):
        raise AssertionError("同語言不應呼叫 claude")

    monkeypatch.setattr(tr.subprocess, "run", must_not_run)
    _, status = tr.translate_cues(_cues(2), "zh-Hant", "zh-TW")
    assert status == "same_language"


ZH_JSON3 = {
    "wireMagic": "pb3",
    "events": [
        {"tStartMs": 0, "dDurationMs": 2000, "segs": [{"utf8": "大家好。"}]},
        {"tStartMs": 2000, "dDurationMs": 2000, "segs": [{"utf8": "今天談測試。"}]},
    ],
}


def test_translate_youtube_manual(monkeypatch):
    seen = {}

    def fake_download(track):
        seen["track"] = track
        return ZH_JSON3

    monkeypatch.setattr(tr, "download_track", fake_download)
    monkeypatch.setattr(tr.subprocess, "run", lambda *a, **k: pytest.fail("不應呼叫 claude"))
    info = {"subtitles": {"zh-Hant": [{"ext": "json3", "url": "u-hant"}]}}
    source = [Cue(0, 0, 2, "Hello everyone."), Cue(1, 2, 4, "Today we talk about testing.")]
    result, status = tr.translate_cues(source, "en", "zh-TW", info)
    assert status == "youtube_manual"
    assert seen["track"].kind == "manual" and seen["track"].url == "u-hant"
    assert [c.translation for c in result] == ["大家好。", "今天談測試。"]


def test_translate_youtube_manual_download_fails_falls_back(fake_claude, monkeypatch):
    def broken(track):
        raise AppError("subtitle_download_failed", "字幕下載失敗")

    monkeypatch.setattr(tr, "download_track", broken)
    info = {"subtitles": {"zh-TW": [{"ext": "json3", "url": "u"}]}}
    _, status = tr.translate_cues(_cues(2), "en", "zh-TW", info)
    assert status == "claude"


def test_translate_invalid_target():
    with pytest.raises(AppError) as e:
        tr.translate_cues(_cues(1), "en", "fr")
    assert e.value.code == "invalid_target_lang"


def test_build_prompt_contents():
    prompt = tr._build_prompt([Cue(0, 0, 1, "[Music]")], "zh-TW")
    assert "繁體中文（台灣用語）" in prompt
    assert "code fence" in prompt
    assert _payload(prompt) == [{"id": 0, "text": "[Music]"}]
