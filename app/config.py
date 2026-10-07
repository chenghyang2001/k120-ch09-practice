"""全域預設值。"""

import os
from pathlib import Path

# M4：資料目錄（SQLite、輸出、暫存）；測試以環境變數改到 tmp_path
DATA_DIR = Path(os.environ.get("K120_DATA_DIR") or "data")

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

# M2 截圖與去重
QUALITY_HEIGHTS = (360, 480, 720, 1080)
FRAME_WORKERS = 4
DEDUPE_THRESHOLD = 8      # pHash 漢明距離 ≤ 門檻 → 同一頁
MAX_SLIDE_SEC = 60.0      # 一頁最長時間，避免講者影片整支變一頁
MAX_SLIDE_CUES = 10       # 一頁最多字幕句數
WEBP_QUALITY = 80
THUMB_WIDTH = 320

# M3 翻譯
TRANSLATE_BATCH = 20  # 40 句長英文實測 Haiku 要 59 秒，貼著逾時；20 句約 30 秒
TRANSLATE_CONCURRENCY = 3
TRANSLATE_TIMEOUT_SEC = 120  # 一批正常約 30 秒，留兩倍以上餘裕
TRANSLATE_MODEL = "haiku"
LANG_NAMES = {"zh-TW": "繁體中文（台灣用語）", "zh-CN": "简体中文", "en": "English", "ja": "日本語", "ko": "한국어"}
# YouTube 字幕語言代碼的別名：目標語言可能以這些 key 出現在 info["subtitles"]
LANG_ALIASES = {"zh-TW": ("zh-TW", "zh-Hant", "zh-Hant-TW", "zh-HK"), "zh-CN": ("zh-CN", "zh-Hans", "zh-Hans-CN", "zh"), "en": ("en", "en-US", "en-GB"), "ja": ("ja",), "ko": ("ko",)}

# M7 Whisper 備援；這台機器沒有 NVIDIA GPU，只能跑 CPU + int8
WHISPER_MODEL = "small"
WHISPER_COMPUTE = "int8"
WHISPER_DEVICE = "cpu"
