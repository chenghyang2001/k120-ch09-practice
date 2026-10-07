"""subtitles 模組測試（不連網）。"""

from itertools import pairwise

import pytest
from pytest import approx

from app.errors import AppError
from app.pipeline import subtitles
from app.pipeline.subtitles import (
    Cue,
    SubtitleTrack,
    choose_subtitle_track,
    download_track,
    fetch_cues,
    normalize_cues,
    parse_json3,
    resegment_words,
)


def _formats(url: str, ext: str = "json3") -> list[dict]:
    return [{"ext": "vtt", "url": url + ".vtt"}, {"ext": ext, "url": url}]


# ---------- choose_subtitle_track ----------

def test_choose_prefers_manual():
    info = {"subtitles": {"en": _formats("m-en")}, "automatic_captions": {"en-orig": _formats("a-en")}}
    assert choose_subtitle_track(info, "en") == SubtitleTrack("manual", "en", "m-en")


def test_choose_exact_before_base():
    info = {"subtitles": {"en-GB": _formats("gb"), "en": _formats("en")}}
    assert choose_subtitle_track(info, "en").url == "en"


@pytest.mark.parametrize(("sub_key", "lang"), [("en-US", "en"), ("en", "en-US")])
def test_choose_base_code_match(sub_key, lang):
    info = {"subtitles": {sub_key: _formats("u")}}
    assert choose_subtitle_track(info, lang) == SubtitleTrack("manual", sub_key, "u")


def test_choose_auto_orig_first():
    info = {"subtitles": {}, "automatic_captions": {"en": _formats("tr"), "en-orig": _formats("orig")}}
    assert choose_subtitle_track(info, "en") == SubtitleTrack("auto", "en", "orig")


def test_choose_auto_plain_lang():
    info = {"automatic_captions": {"ja": _formats("ja")}}
    assert choose_subtitle_track(info, "ja") == SubtitleTrack("auto", "ja", "ja")


def test_choose_rejects_machine_translated_auto():
    info = {
        "language": "zh-Hant",
        "automatic_captions": {
            "en-orig": _formats("https://x/en-orig"),
            "zh-Hant": _formats("https://x/api/timedtext?lang=en&tlang=zh-Hant&fmt=json3"),
        },
    }
    assert choose_subtitle_track(info, "zh-Hant") is None


def test_choose_auto_base_code_orig():
    info = {"language": "en-US", "automatic_captions": {"en-orig": _formats("orig")}}
    assert choose_subtitle_track(info, "en-US") == SubtitleTrack("auto", "en", "orig")


def test_choose_auto_base_code_rejects_tlang():
    info = {"automatic_captions": {"en": _formats("https://x/timedtext?lang=ja&tlang=en&fmt=json3")}}
    assert choose_subtitle_track(info, "en-US") is None


def test_choose_skips_track_without_json3():
    info = {"subtitles": {"en": [{"ext": "vtt", "url": "v"}]}, "automatic_captions": {"en-orig": _formats("a")}}
    assert choose_subtitle_track(info, "en") == SubtitleTrack("auto", "en", "a")
    assert choose_subtitle_track({"subtitles": {"en": [{"ext": "srv3", "url": "s"}]}}, "en") is None


@pytest.mark.parametrize(
    "info",
    [{}, {"subtitles": None, "automatic_captions": None}, {"subtitles": {"live_chat": _formats("lc")}}],
)
def test_choose_no_subtitles(info):
    assert choose_subtitle_track(info, "en") is None


# ---------- parse_json3 ----------

def test_parse_manual(load_json3):
    cues = parse_json3(load_json3("manual.json3"), "manual")
    assert [c.text for c in cues] == [
        "Hello everyone.",
        "Today we talk about testing.",
        "First, write the test.",
        "Then make it pass.",
        "Thank you!",
    ]
    assert [(c.start, c.end) for c in cues] == [
        approx((0.0, 2.0)), approx((2.5, 5.5)), approx((6.0, 8.5)), approx((9.0, 12.0)), approx((12.5, 14.5)),
    ]


def test_parse_auto_en(load_json3):
    cues = parse_json3(load_json3("auto_en.json3"), "auto", "en")
    assert [c.text for c in cues] == [
        "so today i want to show you how we can build a small tool that turns long videos into slides",
        "you can read quickly",
        "this is the second part",
        "and it ends here.",
        "after the period",
    ]
    assert [(c.start, c.end) for c in cues] == [
        approx((1.0, 7.0)), approx((7.0, 8.2)), approx((9.5, 11.0)), approx((12.0, 13.2)), approx((13.2, 14.3)),
    ]
    assert all("  " not in c.text for c in cues)


def test_parse_auto_zh(load_json3):
    cues = parse_json3(load_json3("auto_zh.json3"), "auto", "zh-TW")
    full = "今天我們要來介紹一個可以把很長的影片轉換成投影片的小工具讓大家不用看完整支影片也能快速理解重"
    assert [c.text for c in cues] == [full[:40], full[40:]]
    assert all(" " not in c.text for c in cues)
    assert cues[0].start == approx(0.5)
    assert cues[1].end == approx(6.5)


def test_parse_auto_overlapping_events_still_split_on_gap():
    # 逐字 event 區間重疊（第一個 event 到 5 秒才結束），但 there 與 again 的 start 差 2 秒，仍要斷開
    data = {
        "events": [
            {"tStartMs": 0, "dDurationMs": 5000, "wWinId": 1,
             "segs": [{"utf8": "hello"}, {"utf8": " there", "tOffsetMs": 300}]},
            {"tStartMs": 2300, "dDurationMs": 3000, "wWinId": 1,
             "segs": [{"utf8": "again"}, {"utf8": " now", "tOffsetMs": 300}]},
        ]
    }
    cues = parse_json3(data, "auto", "en")
    assert [(c.start, c.end, c.text) for c in cues] == [
        (0.0, approx(0.8), "hello there"),
        (2.3, approx(3.1), "again now"),
    ]


def test_parse_auto_word_end_capped():
    # 字間 1 秒但未達停頓門檻時，cue 結束時間仍用估計值（start + WORD_MAX_SEC）
    data = {"events": [{"tStartMs": 0, "dDurationMs": 9000, "segs": [{"utf8": "a"}, {"utf8": " b", "tOffsetMs": 1000}]}]}
    cues = parse_json3(data, "auto", "en")
    assert [(c.start, c.end, c.text) for c in cues] == [(0.0, approx(1.5), "a b")]


def test_parse_auto_single_seg_events_keep_duration():
    # 部分日文自動字幕一個 event 只有一個整句 seg，end 要保留 dDurationMs，不能被單字上限截短
    data = {
        "events": [
            {"tStartMs": 0, "dDurationMs": 3000, "wWinId": 1, "segs": [{"utf8": "こんにちは"}]},
            {"tStartMs": 5500, "dDurationMs": 3000, "wWinId": 1, "segs": [{"utf8": "ありがとう"}]},
        ]
    }
    cues = parse_json3(data, "auto", "ja")
    assert [(c.start, c.end, c.text) for c in cues] == [
        (0.0, approx(3.0), "こんにちは"),
        (5.5, approx(8.5), "ありがとう"),
    ]
    assert len(normalize_cues(cues, "ja")) == 2


def test_parse_auto_single_seg_event_capped_by_next_start():
    data = {
        "events": [
            {"tStartMs": 0, "dDurationMs": 5000, "segs": [{"utf8": "first line"}]},
            {"tStartMs": 4000, "dDurationMs": 3000, "segs": [{"utf8": "second line"}]},
        ]
    }
    cues = parse_json3(data, "auto", "en")
    assert [(c.start, c.end) for c in cues] == [(0.0, approx(7.0))]
    assert cues[0].text == "first line second line"


def test_parse_manual_newline_join_by_lang():
    data = {"events": [{"tStartMs": 0, "dDurationMs": 2000, "segs": [{"utf8": "第一行\n第二行"}]}]}
    assert parse_json3(data, "manual", "zh-TW")[0].text == "第一行第二行"
    data_en = {"events": [{"tStartMs": 0, "dDurationMs": 2000, "segs": [{"utf8": "line one \n line two"}]}]}
    assert parse_json3(data_en, "manual", "en")[0].text == "line one line two"


def test_sound_tag_not_glued_to_speech():
    data = {
        "events": [
            {"tStartMs": 0, "dDurationMs": 6000, "segs": [{"utf8": "[Music]"}]},
            {"tStartMs": 5800, "dDurationMs": 3000,
             "segs": [{"utf8": "hello"}, {"utf8": " everyone", "tOffsetMs": 400}]},
        ]
    }
    cues = normalize_cues(parse_json3(data, "auto", "en"), "en")
    assert [(c.start, c.end, c.text) for c in cues] == [
        (0.0, approx(5.8), "[Music]"),
        (5.8, approx(6.7), "hello everyone"),
    ]


@pytest.mark.parametrize("tag", ["[Applause]", "[音樂]"])
def test_resegment_sound_tag_own_cue(tag):
    words = [(0.0, 0.3, "我們"), (0.3, 0.6, tag), (0.6, 0.9, "開始")]
    assert [c.text for c in resegment_words(words, "zh")] == ["我們", tag, "開始"]


def _speech_with_overlapping_tag(tag_duration_ms: int) -> dict:
    # 真實日文自動字幕：[音楽] 在整句講話的中途就開始
    return {
        "events": [
            {"tStartMs": 288440, "dDurationMs": 2438, "segs": [{"utf8": "そうですね。遅刻しないように"}]},
            {"tStartMs": 288858, "dDurationMs": tag_duration_ms, "segs": [{"utf8": "[音楽]"}]},
        ]
    }


@pytest.mark.parametrize("tag_duration_ms", [2020, 4000])
def test_overlapping_sound_tag_does_not_truncate_speech(tag_duration_ms):
    cues = normalize_cues(parse_json3(_speech_with_overlapping_tag(tag_duration_ms), "auto", "ja"), "ja")
    speech = [c for c in cues if c.text != "[音楽]"]
    assert [(c.start, c.end) for c in speech] == [(approx(288.44), approx(290.878))]
    for tag_cue in (c for c in cues if c.text == "[音楽]"):
        assert tag_cue.start >= 290.878 - 1e-6
    for prev, cue in pairwise(cues):
        assert prev.start < cue.start
        assert prev.end <= cue.start
    if tag_duration_ms == 4000:
        assert [c.text for c in cues][-1] == "[音楽]"


def test_parse_real_auto_en(load_json3):
    # X811bvy4EXE 前 40 個 event 的真實 YouTube 自動字幕
    cues = normalize_cues(parse_json3(load_json3("real_auto_en.json3"), "auto", "en"), "en")
    assert cues[0].text == "[Music]"
    assert len(cues) > 2
    for prev, cue in pairwise(cues):
        assert prev.start < cue.start
        assert prev.end <= cue.start
    assert all(c.start < c.end for c in cues)
    assert all("  " not in c.text for c in cues)


def test_parse_empty_data():
    assert parse_json3({}, "manual") == []
    assert parse_json3({"events": None}, "auto", "en") == []


# ---------- resegment_words ----------

def test_resegment_pause_split():
    words = [(0.0, 0.5, "hello"), (0.5, 1.0, " world"), (1.9, 2.3, " again")]
    cues = resegment_words(words, "en")
    assert [c.text for c in cues] == ["hello world", "again"]
    assert (cues[0].start, cues[0].end) == (0.0, 1.0)


def test_resegment_pause_below_threshold_keeps_together():
    words = [(0.0, 0.5, "hello"), (1.2, 1.5, " world")]
    assert [c.text for c in resegment_words(words, "en")] == ["hello world"]


def test_resegment_word_limit():
    words = [(i * 0.2, (i + 1) * 0.2, f" w{i}") for i in range(25)]
    cues = resegment_words(words, "en")
    assert [len(c.text.split()) for c in cues] == [20, 5]
    assert cues[1].start == approx(4.0)


def test_resegment_cjk_char_limit():
    words = [(i * 0.1, (i + 1) * 0.1, "字") for i in range(45)]
    cues = resegment_words(words, "zh-TW")
    assert [len(c.text) for c in cues] == [40, 5]


def test_resegment_sentence_end():
    words = [(0.0, 0.3, "Hi."), (0.3, 0.6, " There"), (0.6, 0.9, " you go")]
    assert [c.text for c in resegment_words(words, "en")] == ["Hi.", "There you go"]
    ja = [(0.0, 0.3, "はい。"), (0.3, 0.6, "次へ")]
    assert [c.text for c in resegment_words(ja, "ja")] == ["はい。", "次へ"]


def test_resegment_duration_limit_spaced():
    words = [(i * 0.7, i * 0.7 + 0.5, f" w{i}") for i in range(20)]
    assert all(c.end - c.start <= 15.0 for c in resegment_words(words, "en"))


def test_resegment_duration_limit_cjk():
    # 30 個字、每字間隔 0.7 秒共約 21 秒，字數沒達 40 也沒有停頓，只能靠時長上限切開
    words = [(i * 0.7, i * 0.7 + 0.5, "字") for i in range(30)]
    cues = resegment_words(words, "zh")
    assert len(cues) == 2
    assert all(c.end - c.start <= 15.0 for c in cues)


def test_resegment_empty():
    assert resegment_words([], "en") == []


# ---------- normalize_cues ----------

def test_normalize_fixes_overlap():
    cues = normalize_cues([Cue(0, 0.0, 3.0, "first."), Cue(1, 2.0, 5.0, "second.")], "en")
    assert [(c.start, c.end) for c in cues] == [(0.0, 2.0), (2.0, 5.0)]


def test_normalize_overlap_same_start_keeps_text():
    cues = normalize_cues([Cue(0, 1.0, 3.0, "first"), Cue(1, 1.0, 4.0, "second")], "en")
    assert [(c.start, c.end, c.text) for c in cues] == [(1.0, 4.0, "first second")]


def test_normalize_overlap_same_start_keeps_text_cjk():
    cues = normalize_cues([Cue(0, 1.0, 5.0, "前句"), Cue(1, 1.0, 4.0, "後句")], "ja")
    assert [(c.start, c.end, c.text) for c in cues] == [(1.0, 5.0, "前句後句")]


def test_normalize_short_sound_tag_not_merged():
    cues = normalize_cues([Cue(0, 0.0, 0.5, "[Applause]"), Cue(1, 0.5, 3.0, "thanks")], "en")
    assert [c.text for c in cues] == ["[Applause]", "thanks"]


def test_normalize_drops_empty_and_zero_length():
    cues = normalize_cues(
        [Cue(0, 1.0, 1.0, "zero"), Cue(1, 2.0, 4.0, "   "), Cue(2, 5.0, 4.0, "neg"), Cue(3, 6.0, 8.0, "ok")], "en"
    )
    assert [c.text for c in cues] == ["ok"]


def test_normalize_merges_short_into_next():
    cues = normalize_cues([Cue(0, 0.0, 0.5, "Hi"), Cue(1, 0.5, 3.0, "there")], "en")
    assert [(c.start, c.end, c.text) for c in cues] == [(0.0, 3.0, "Hi there")]


def test_normalize_merges_short_cjk_without_space():
    cues = normalize_cues([Cue(0, 0.0, 0.4, "你好"), Cue(1, 0.4, 3.0, "世界")], "zh-TW")
    assert cues[0].text == "你好世界"


def test_normalize_last_short_merges_into_previous():
    cues = normalize_cues([Cue(0, 0.0, 3.0, "Hello"), Cue(1, 3.0, 3.4, "bye")], "en")
    assert [(c.start, c.end, c.text) for c in cues] == [(0.0, 3.4, "Hello bye")]


def test_normalize_short_not_merged_across_gap():
    cues = normalize_cues([Cue(0, 0.5, 1.0, "[Music]"), Cue(1, 11.0, 14.0, "hi everyone")], "en")
    assert [(c.start, c.end, c.text) for c in cues] == [(0.5, 1.0, "[Music]"), (11.0, 14.0, "hi everyone")]


def test_normalize_last_short_not_merged_across_gap():
    cues = normalize_cues([Cue(0, 0.0, 3.0, "Hello"), Cue(1, 10.0, 10.4, "[Applause]")], "en")
    assert [c.text for c in cues] == ["Hello", "[Applause]"]


def test_normalize_short_falls_back_to_previous():
    # 下一句離太遠時，改併入緊接的前一句
    cues = normalize_cues([Cue(0, 0.0, 3.0, "Hello"), Cue(1, 3.1, 3.5, "there"), Cue(2, 20.0, 23.0, "later")], "en")
    assert [(c.start, c.end, c.text) for c in cues] == [(0.0, 3.5, "Hello there"), (20.0, 23.0, "later")]


def test_normalize_single_short_cue_kept():
    cues = normalize_cues([Cue(0, 0.0, 0.5, "solo")], "en")
    assert [c.text for c in cues] == ["solo"]


def test_normalize_splits_long_by_punctuation():
    text = "First sentence here. Second one is longer here!"
    cues = normalize_cues([Cue(0, 0.0, 20.0, text)], "en")
    assert [c.text for c in cues] == ["First sentence here.", "Second one is longer here!"]
    first_len, second_len = len("First sentence here."), len("Second one is longer here!")
    assert cues[0].end == approx(20.0 * first_len / (first_len + second_len))
    assert cues[1].start == approx(cues[0].end)
    assert cues[1].end == 20.0


def test_normalize_splits_long_cjk():
    cues = normalize_cues([Cue(0, 0.0, 16.0, "第一句。第二句！")], "zh")
    assert [c.text for c in cues] == ["第一句。", "第二句！"]
    assert cues[0].end == approx(8.0)


def test_normalize_long_without_inner_punctuation_unchanged():
    for text in ("no punctuation at all", "version 3.5 is out.", "ends with dot."):
        cues = normalize_cues([Cue(0, 0.0, 20.0, text)], "en")
        assert [(c.start, c.end, c.text) for c in cues] == [(0.0, 20.0, text)]


def test_normalize_sorts_and_renumbers():
    cues = normalize_cues([Cue(7, 5.0, 7.0, "b"), Cue(3, 0.0, 2.0, "a"), Cue(9, 10.0, 12.0, "c")], "en")
    assert [(c.id, c.text) for c in cues] == [(0, "a"), (1, "b"), (2, "c")]


def test_normalize_does_not_mutate_input():
    original = [Cue(0, 0.0, 3.0, "first"), Cue(1, 2.0, 5.0, "second")]
    normalize_cues(original, "en")
    assert original[0].end == 3.0


# ---------- download_track / fetch_cues ----------

def _fake_ydl(payload: bytes | None = None, error: Exception | None = None):
    class FakeResponse:
        def read(self):
            return payload

    class FakeYDL:
        def __init__(self, opts):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def urlopen(self, url):
            if error is not None:
                raise error
            return FakeResponse()

    return FakeYDL


TRACK = SubtitleTrack("manual", "en", "https://example.invalid/sub.json3")


def test_download_track_ok(monkeypatch):
    monkeypatch.setattr(subtitles.yt_dlp, "YoutubeDL", _fake_ydl(b'{"events": []}'))
    assert download_track(TRACK) == {"events": []}


@pytest.mark.parametrize("payload", [b"<html>not json</html>", b"\xff\xfe\x00bad"])
def test_download_track_invalid_payload(monkeypatch, payload):
    monkeypatch.setattr(subtitles.yt_dlp, "YoutubeDL", _fake_ydl(payload))
    with pytest.raises(AppError) as exc:
        download_track(TRACK)
    assert exc.value.code == "subtitle_download_failed"


def test_download_track_network_error(monkeypatch):
    signed_url_error = OSError("connection reset: https://www.youtube.com/api/timedtext?signature=SECRET")
    monkeypatch.setattr(subtitles.yt_dlp, "YoutubeDL", _fake_ydl(error=signed_url_error))
    with pytest.raises(AppError) as exc:
        download_track(TRACK)
    assert exc.value.code == "subtitle_download_failed"
    assert exc.value.message == "字幕下載失敗，請稍後再試"
    assert "SECRET" not in exc.value.message
    assert exc.value.__cause__ is signed_url_error


def test_fetch_cues_pipeline(monkeypatch, load_json3):
    monkeypatch.setattr(subtitles, "download_track", lambda track: load_json3("auto_en.json3"))
    info = {"automatic_captions": {"en-orig": _formats("orig")}}
    cues, track = fetch_cues(info, "en")
    assert track == SubtitleTrack("auto", "en", "orig")
    assert [c.id for c in cues] == list(range(len(cues)))
    assert cues[0].text.startswith("so today")


def test_fetch_cues_no_track():
    assert fetch_cues({}, "en") is None
