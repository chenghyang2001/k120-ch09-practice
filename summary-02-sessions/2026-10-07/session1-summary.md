# Session 1 Summary — YouTube 影片轉投影片閱讀器：規格到 M7 全部完成

- **日期**：2026-10-07
- **專案**：`~/workspace/k120-ch09-practice`（K120 第 9 章演練）
- **GitHub**：`chenghyang2001/k120-ch09-practice`（public，本 session 建立）
- **機器**：DESKTOP-6LST1BR（家用機）

## 完成事項

### 規劃

- Plan mode 三輪 AskUserQuestion 訪談（部署、切頁、字幕來源、翻譯引擎、技術堆疊、輸出、影片快取、截圖時點、語言呈現、後續操作、支援範圍），產出 `docs/spec.md`（commit `b8fc6a0`）
- 依 M1 真實字幕資料修訂規格 2.3 斷句與短句合併規則（`bb5068e`）

### 里程碑實作（7 個全部完成並 push）

| 里程碑 | 內容 | commit | 模式 |
| --- | --- | --- | --- |
| M1 | 網址解析、yt-dlp 影片資訊與錯誤對應、json3 字幕取得／斷句／整理、probe CLI | `a7fd8ba` | 完整三 agent（4 輪 QA＋2 輪 reviewer） |
| M2 | 只下載視訊流、字幕中點截圖、pHash 去重（與組內第一張比、60 秒／10 句上限、代表圖取最後一張）、WebP | `7299196` | 快速 |
| M3 | `claude -p` Haiku 分批翻譯、YouTube 人工字幕對齊、重試→對半切→單句放棄 | `b89a7a4` | 快速 |
| M4 | FastAPI、SQLite JobStore、單 worker JobManager、SSE、取消、歷史、重啟標 interrupted | `79872e3` | 快速 |
| M5＋M6 | 首頁／閱讀器 UI（雙語、鍵盤、RWD、深色）、HTML／PDF（Playwright）／Markdown zip 匯出 | `c8bb11e` | 快速（與 M7 平行子代理） |
| M7 | Whisper 備援（ffmpeg 解碼後交 faster-whisper）、播放清單 parent／child、翻譯途中可取消 | `7f5a739` | 快速（與 M5/M6 平行子代理） |

- 最終測試：**246 passed**，`uvx ruff check app tests` 通過
- 其他 commit：`361a0a0`（Ralph 施工圖 `.ralph/` + pillow/imagehash）、`8e5e695`（jinja2/playwright/faster-whisper）

### 研究與嘗試

- 從本機記憶與 repo `chenghyang2001/gsd-ralph-todo` 的 `docs/DEMO-GSD-RALPH-VPS.md` 摘要使用者指定的 NotebookLM「GSD→VPS→Ralph 自主執行 todo-cli demo 實錄」（NotebookLM auth 已過期，未直接開啟）
- 嘗試把 M2 交給 VPS（`claude@187.127.109.145`）Ralph：環境全綠，但架設與啟動被 auto mode 分類器以 `[Create Unsafe Agents]` 拒絕，改本機快速模式完成

## 關鍵決定

1. **規格決策**：本機個人工具只綁 127.0.0.1；pHash 去重合併；截圖取字幕中點；人工 > 自動（排除 tlang 機器翻譯軌）> Whisper；`claude -p` 走 Max 訂閱；只下載視訊流用完即刪；FastAPI＋原生 HTML/JS。
2. **快速 demo 模式**：使用者在 M1 收尾時選擇（新規則 `instructions/learning-fast-mode.md`），M2 起只派 code-writer＋冒煙測試，不跑 QA／reviewer。
3. **不再提用量警告**：使用者明確要求不轉述 usage budget、不建議開新 session，要盡快做到 M7。
4. **避免 context rot**：每個里程碑派新的 code-writer 子代理；M5/M6 與 M7 以「檔案擁有權清單＋資料合約」平行開發，依賴先由主 Claude 統一安裝避免 `uv add` 衝突。

## 關鍵技術筆記（踩坑）

- **YouTube json3 自動字幕**：event 第一個 seg 無前導空白、後續 seg 有；1217/1244 個 event 與下一個重疊 → 字的結束時間要用 `min(start+0.5, 下一字 start)` 估計；日文是整句單 seg，必須保留 dDurationMs；`[Music]` 等音效標記要強制獨立成句且不參與合併。
- **yt-dlp 真實錯誤字串**：「This video is unavailable」「Premieres in…」「not made this video available in your country」「try again later」都要對應，否則會誤報「yt-dlp 過舊」。
- **`claude -p` 從程式呼叫**：
  - 要移除 `ANTHROPIC_API_KEY`（走 Max）。
  - 加 `--setting-sources= --strict-mcp-config` 隔離全域 hooks，啟動從 8 秒降到 4 秒。
  - 不能用 `--bare`，它強制 API key 認證。
  - 在專案目錄跑時會觸發使用者的自動 commit hook，曾產生不明 commit `35ccf3a`、`c6ee00a`。
- **翻譯批次**：40 句長英文 Haiku 要 59 秒 → 改 20 句一批、3 路平行、逾時 120 秒；30 分鐘影片翻譯約 4–5 分鐘。
- **Windows 取消 claude 子程序**：`claude.cmd` 會再起 node，要 `taskkill /T /F` 殺整棵程序樹，否則 `communicate` 卡住。
- **faster-whisper 1.2.1 與 PyAV 19.0.1 不相容**（`metadata_errors` 參數）→ 改用 ffmpeg 解碼成 16kHz float32 陣列再交給模型。
- **`pytest.approx` 不支援 `>=` 比較**。
- **yt-dlp 要加 `noprogress: True`**，否則進度條洗版。
- **ffmpeg 合成測試影片**：`testsrc` 與 `mandelbrot` 會隨時間變化，pHash 不穩定；改用 `smptebars`／`rgbtestsrc`／drawbox 靜態圖樣。

## 實測數據

- 30 分鐘英文演講 X811bvy4EXE：222 句 → 51 頁，360p 截圖流程 34 秒，輸出 1.4MB；加翻譯共 4 分 49 秒，222/222 翻譯成功。
- 取消：翻譯途中從 90 秒以上降到 2 秒；Whisper 途中 10 秒內停止；tmp 皆清空。
- Whisper small/int8/CPU：19 秒影片轉錄 7 秒（含載入模型），快取後 3 秒。

## 產出檔案

| 路徑 | 說明 |
| --- | --- |
| `docs/spec.md` | 完整規格書 |
| `.ralph/PROMPT.md`、`.ralph/fix_plan.md`、`.ralph/specs/m2-spec.md` | M2 施工圖（Ralph 未啟動，但作為本機實作依據） |
| `app/errors.py`、`app/config.py` | 錯誤型別、全域常數 |
| `app/pipeline/url.py`、`metadata.py`、`subtitles.py`、`probe.py` | M1 |
| `app/pipeline/frames.py`、`dedupe.py`、`images.py`、`slides.py`、`make_slides.py` | M2 |
| `app/pipeline/translate.py` | M3 |
| `app/jobs.py`、`app/main.py`、`app/pipeline/runner.py` | M4 |
| `app/templates/*`、`app/static/*`、`app/pipeline/export.py` | M5／M6 |
| `app/pipeline/transcribe.py` | M7 |
| `tests/*`（含 `fixtures/real_auto_en.json3` 真實字幕） | 246 個測試 |
| `.gitignore` | 排除 data/、.venv 等 |

## HANDOFF（下次 session 優先處理）

### 立即行動

- [ ] 使用者親自用瀏覽器跑一輪：`uv run uvicorn app.main:app --host 127.0.0.1 --port 8000` → 送一支影片 → 閱讀器 → 三種匯出，收集回饋
- [ ] 視需要改善截圖時點：目前取字幕中點，偶爾截到轉場淡入淡出（M2 實測第 30 頁）；可改為區間內 3–5 幀挑 Laplacian 銳利度最高者
- [ ] 視需要用一支 5–10 分鐘的無字幕短片，完整跑完 Whisper → 翻譯 → 投影片（目前只驗證 19 秒短片與中途取消）

### 進行中（需接續）

- M1–M7 全部完成並已 push（最新 `7f5a739`），無未完成的程式工作。
- VPS 上 `~/k120-ch09-practice` 已 clone 但 Ralph 未啟動；若不再嘗試可刪除。

### 注意事項

- M2 之後是快速 demo 模式，未經 QA／reviewer；已知風險：
  - 播放清單 submit 在 async handler 中同步呼叫 yt-dlp，會卡事件迴圈數秒（改成 to_thread 需讓 `jobs.py` 的 `put_nowait` 改 `call_soon_threadsafe`）。
  - PDF 頁數多於投影片（字幕溢出）。
  - YouTube 人工字幕對齊路徑只有單元測試。
- 本 session 密集打 YouTube，曾被暫時限流（`network_error`）；再測時注意間隔。
- VPS Ralph 需使用者自行在權限設定允許 `ssh claude@187.127.109.145 *` 或親自 SSH，subagent 不得繞過。
- `claude -p` 在 repo 目錄內執行時務必帶 `--setting-sources=`，否則全域 hooks 可能自動 commit WIP。
