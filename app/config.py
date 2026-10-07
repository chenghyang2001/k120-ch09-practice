"""全域預設值。"""

MAX_DURATION_SEC = 4 * 3600
SUPPORTED_TARGET_LANGS = ("zh-TW", "zh-CN", "en", "ja", "ko")

# 自動字幕重新斷句門檻
PAUSE_SPLIT_SEC = 0.8
MAX_WORDS_SPACED = 20
MAX_CHARS_CJK = 40
# 自動字幕只給字的開始時間；event 區間幾乎都和下一行重疊，不能拿來推結束時間，改用單字最長時長估計
WORD_MAX_SEC = 0.5

# 字幕整理門檻
MIN_CUE_SEC = 1.0
MAX_CUE_SEC = 15.0
SENTENCE_END = ".?!。？！"

# 以語言 base code（例如 zh-TW → zh）判斷，這些語言不以空白分詞
CJK_LANGS = ("zh", "ja")
