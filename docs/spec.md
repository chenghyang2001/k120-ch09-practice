# YouTube 影片 → 靜態閱讀版（投影片）Web App 規格書

## Context

使用者想把 YouTube 影片（演講、教學、簡報類影片）轉成「可翻頁閱讀的投影片」：每頁是一張關鍵畫面，下方附上這段時間的字幕（原文＋譯文），不用看影片就能快速讀完內容。repo `k120-ch09-practice` 目前是空的（只有 `CLAUDE.md`），屬於全新專案。


### 訪談決策摘要

| 議題 | 決定 |
| --- | --- |
| 部署 | 本機個人工具，只綁定 `127.0.0.1`，不做帳號系統（家用 IP 可避開 YouTube 對機房 IP 的反爬蟲） |
| 切頁 | 每句字幕都截一張圖 → 用 pHash 判斷畫面是否相近 → 相近的就合併成同一頁，該頁列出所有字幕 |
| 截圖時點 | 每句字幕時間區間的**中點** |
| 字幕來源 | 人工字幕 > 自動字幕（清洗後）> faster-whisper 本機轉錄 |
| 翻譯 | `claude -p` 子程序（走 Max 訂閱，不花 API Credits），分批送出並保留編號 |
| 語言 | 原文＋一個目標語言（預設 zh-TW），閱讀器可切換原文／譯文／雙語 |
| 影片檔 | 只下載不含音訊的視訊流，截完圖就刪除，只保留 WebP 圖片與 JSON |
| 技術堆疊 | FastAPI ＋ 原生 HTML/JS（不用 build），環境用 uv 管理 |
| 輸出 | App 內閱讀器 ＋ 匯出單檔 HTML／PDF／Markdown(zip) |
| 後續操作 | 歷史列表、處理中可取消（**不做**手動編輯投影片、**不做**以新參數重新產生） |
| 支援範圍 | 一般影片、Shorts、播放清單（**不支援**會員限定／年齡限制影片，不處理 cookies） |

本機環境已確認：ffmpeg 9.0.2 / ffprobe / uv / yt-dlp / claude CLI 都有。**沒有 NVIDIA GPU**，所以 Whisper 只能用 CPU 跑（int8）。

---

## 1. 系統架構

```
瀏覽器（index / reader）
   │ REST + SSE（進度）
FastAPI (127.0.0.1:8000)
   ├── JobManager：asyncio 佇列，同一時間只處理 1 個工作，狀態存在 SQLite
   └── Pipeline（每個階段都可以取消，會回報進度）
        1 解析網址 → 2 取得影片資訊 → 3 取得字幕 → 4 整理字幕
        → 5 翻譯 → 6 下載視訊流 → 7 截圖 → 8 去重合併 → 9 壓縮與輸出
資料：data/app.db、data/jobs/<job_id>/{meta.json, slides.json, images/*.webp, thumb.webp}
暫存：data/tmp/<job_id>/（影片、PNG、音訊；完成、失敗或取消時一律清除）
```

### 目錄結構

```
pyproject.toml            # uv 專案
app/main.py               # FastAPI 路由、啟動時檢查 ffmpeg 與 claude
app/config.py             # 預設值（門檻、批次大小、上限）
app/jobs.py               # 佇列、SQLite、取消（終止子程序）、SSE 事件
app/pipeline/url.py       # 網址解析
app/pipeline/metadata.py  # yt-dlp 影片資訊、主要語言判斷
app/pipeline/subtitles.py # 下載字幕、解析 json3、清洗自動字幕、整理字幕
app/pipeline/transcribe.py# faster-whisper 備援
app/pipeline/translate.py # claude -p 分批翻譯
app/pipeline/frames.py    # 下載視訊流、用 ffmpeg 截取中點畫面
app/pipeline/dedupe.py    # pHash 分組
app/pipeline/images.py    # WebP 壓縮、縮圖
app/pipeline/export.py    # HTML / PDF / MD 匯出
app/templates/{index,reader,export}.html
app/static/{app.js,reader.js,style.css}
tests/fixtures/           # json3、vtt、claude 回應樣本、測試圖片
tests/test_*.py
docs/spec.md
```

## 2. 處理流程細節

### 2.1 解析網址（`url.py`）

- 支援以下格式：`youtube.com/watch?v=`、`youtu.be/<id>`、`youtube.com/shorts/<id>`、`m.youtube.com`、`youtube.com/playlist?list=`。
- 同時有 `v=` 和 `list=` 時，只處理那支影片；只有 `list=` 時當作播放清單。會忽略 `t=`、`si=` 等參數。
- 播放清單：用 `yt-dlp --flat-playlist` 列出影片，每支影片建成一個子工作排隊處理；無法播放的項目略過並記錄下來。

### 2.2 影片資訊（`metadata.py`）

- 呼叫 `yt_dlp.YoutubeDL.extract_info(download=False)`，取得 id、title、duration、thumbnail、uploader、`language`、`subtitles`、`automatic_captions`、`is_live`、`live_status`。
- 判斷主要語言：`info.language` → 第一個人工字幕的語言 → `automatic_captions` 中帶 `-orig` 後綴的語言 → 都沒有就交給 Whisper 偵測。
- 直接拒絕的情況：直播中或尚未開播的首播、私人影片、已刪除影片、會員限定或年齡限制影片。錯誤訊息要清楚。

### 2.3 字幕取得與整理（`subtitles.py`、`transcribe.py`）

- 優先順序：主要語言的人工字幕 → 自動字幕 → Whisper。
- 一律下載 **json3** 格式：自動字幕的 json3 有逐字時間戳，可以避開 VTT 的「滾動重複」問題。
- 自動字幕清洗：把逐字時間戳攤平，再重新斷句。斷句規則：停頓超過 0.8 秒、遇到標點、或超過長度上限（英文約 20 字、CJK 約 40 字）。CJK 不加空格。
- Whisper：只下載 `bestaudio`，使用 faster-whisper `small`、`int8`、開啟 VAD。CPU 上 1 小時的影片約需 10–20 分鐘，進度依處理到的秒數回報。
- 字幕整理：時間重疊的修正掉；長度為 0 的刪除；短於 1 秒的併入下一句；長於 15 秒的依標點切開。
- 輸出統一格式：`Cue{id, start, end, text}`。

### 2.4 翻譯（`translate.py`）

- 目標語言等於原文語言時跳過。語言代碼對應：zh-TW ↔ zh-Hant、zh-CN ↔ zh-Hans。
- 如果 YouTube 已經有目標語言的**人工字幕**，就直接使用，依時間重疊比例對齊到原文字幕上，不另外翻譯。
- 否則用 `claude -p --model haiku`：每批 40 句，以 JSON `[{"id":..,"text":..}]` 透過 stdin 送出，要求回傳 id 與數量都一模一樣的 JSON 陣列。最多同時跑 2 個子程序。
- 驗證：JSON 解析失敗或 id／數量不符時重試 1 次 → 再失敗就把這批對半切開重送 → 還是失敗就標記為 `translation_missing`，但不讓整個工作失敗。
- 啟動子程序時，從 env 中**移除 `ANTHROPIC_API_KEY`**，確保一定走訂閱額度（符合 cost-rules）。subprocess 設 `encoding='utf-8'`。
- claude CLI 不存在或沒登入時：跳過翻譯，產出只有原文的版本，並在 UI 顯示警告。

### 2.5 視訊流與截圖（`frames.py`）

- 畫質：360/480/720/1080。format 寫法為 `bv*[height<=H][ext=mp4]/bv*[height<=H]`，只抓視訊流。實際取得的高度如果比要求的低，記錄在 meta 中。
- 下載前檢查磁碟剩餘空間（預估：時長 × 該畫質的位元率 × 1.5）。
- 每句字幕取 `t = (start+end)/2`，執行 `ffmpeg -ss t -i video -frames:v 1 -q:v 2 out.png`（-ss 放在 -i 前面做快速 seek），用 4 條執行緒並行。
- 取消時終止 yt-dlp、ffmpeg 的子程序，Windows 上用 `taskkill /T /F` 連子程序樹一起關掉。

### 2.6 去重合併（`dedupe.py`）

- 每張圖算 64-bit pHash（`imagehash`）。
- 依時間順序分組：跟**這組第一張圖**比較（不是跟前一張比，避免畫面慢慢變化時一路累積偏移），漢明距離 ≤ 門檻（預設 8）就歸到同一組。
- 每組上限 60 秒或 10 句字幕。超過就強制切頁，避免講者對著鏡頭講話的影片整支變成一頁文字牆。
- 代表圖選**這組最後一張**：簡報常常是條列項目一行一行出現，最後一張的內容最完整。
- Slide 結構：`{index, start, end, image, cues:[{start,end,text,translation}]}`。

### 2.7 壓縮與輸出（`images.py`）

- 只把代表圖轉成 WebP（quality 80，保持所選畫質的解析度），另外產生 320px 縮圖給縮圖列使用。
- 寫入 `slides.json`、`meta.json` 後，刪除 `data/tmp/<job_id>/`。

## 3. 工作管理（`jobs.py`）

- SQLite 資料表 `jobs`：id、parent_id（播放清單用）、video_id、url、quality、target_lang、status（queued/running/done/failed/cancelled/interrupted）、stage、progress(0–100)、error、created_at、finished_at。
- 去重鍵是 `video_id+quality+target_lang`：已經做完的就直接回傳舊的工作，不重新處理。
- 進度用 SSE `GET /api/jobs/{id}/events` 推送，事件內容是 `{stage, progress, message}`。
- 伺服器重啟時，把狀態為 running 的工作標成 `interrupted`，並清掉它的 tmp。

## 4. API

| 方法 | 路徑 | 說明 |
| --- | --- | --- |
| POST | `/api/jobs` | `{url, quality, target_lang}` → `{job_ids:[...]}`（播放清單會回傳多個） |
| GET | `/api/jobs` | 歷史列表（不含子工作，播放清單顯示成一個群組） |
| GET | `/api/jobs/{id}` | 單一工作的狀態與 meta |
| GET | `/api/jobs/{id}/events` | SSE 進度 |
| POST | `/api/jobs/{id}/cancel` | 取消（播放清單會連帶取消所有子工作） |
| DELETE | `/api/jobs/{id}` | 刪除工作與它的檔案 |
| GET | `/api/jobs/{id}/slides` | slides.json |
| GET | `/api/jobs/{id}/export?format=html\|pdf\|md` | 下載匯出檔 |
| GET | `/media/{id}/...` | 圖片（StaticFiles） |

錯誤回傳 400（網址無效、不支援的影片）、404、409（工作還沒完成就要匯出）、500（不洩漏內部細節，只顯示可讀的錯誤訊息與錯誤代碼）。

## 5. UI

**首頁 `/`**

- 網址輸入框、畫質下拉選單（預設 720p）、目標語言下拉選單（zh-TW/zh-CN/en/ja/ko，預設 zh-TW）、「開始」按鈕。
- 處理中的卡片：縮圖、標題、目前階段（中文）、進度條、取消按鈕。
- 歷史列表：縮圖、標題、時長、頁數、建立日期、開啟／刪除。

**閱讀器 `/read/{id}`**

- 主畫面是投影片圖（直式的 Shorts 會自動改成左圖右文的版面），下方是這頁的字幕；每句字幕前面有時間戳，點下去會在新分頁開啟 YouTube 的 `&t=` 對應秒數。
- 頂部工具列：原文／譯文／雙語切換、頁碼「12 / 87」、匯出選單、全螢幕。
- 底部可以收合的縮圖列。
- 鍵盤操作：← → 翻頁、Home/End、F 全螢幕、L 切換語言模式。用 localStorage 記住上次看到的頁數和語言模式（讀寫都包 try/catch）。
- 響應式設計，手機寬度也能閱讀。字幕一律用 `textContent` 插入，防止 XSS。

## 6. 匯出（`export.py`）

- **HTML**：Jinja 樣板 `export.html` 的離線版閱讀器，圖片用 base64 內嵌，JS 也內嵌。檔案超過 50MB 時在 UI 提示建議改選較低畫質。
- **PDF**：用 Playwright（Chromium）把 export.html 的列印版式（一頁一張投影片＋字幕）印成 PDF。可以直接用系統字型處理中、日、韓文，不用另外帶字型檔。第一次使用時要執行 `uv run playwright install chromium`。
- **Markdown**：zip 檔，內含 `slides.md`（每頁一個 `## 頁碼 · 時間`、圖片、字幕）和 `images/`。

## 7. 邊界情況（都要處理）

1. 網址無效、私人或已刪除影片、直播、會員限定或年齡限制影片 → 400，附上中文說明。
2. 沒有任何字幕 → 改用 Whisper；連音訊也拿不到 → 工作失敗並說明原因。
3. 要求的畫質不存在 → 自動降到可用的最高畫質，並記錄下來。
4. claude 回傳不是 JSON、數量不符或 CLI 不存在 → 依 2.4 的規則降級處理，整個工作不會失敗。
5. 超長影片（> 4 小時，可在 config 調整）→ 拒絕。
6. yt-dlp 因為 YouTube 改版而失效 → 錯誤訊息提示執行 `uv lock --upgrade-package yt-dlp`。
7. 影片標題含中文或特殊字元 → 所有路徑只用 video_id 與 job_id 命名。
8. 同一個網址加上同樣的參數重複送出 → 回傳現有的工作。
9. 取消、失敗或伺服器重啟 → 一律清除 tmp，不留下好幾 GB 的殘檔。
10. 播放清單中有部分影片失敗 → 其他影片照常處理，群組卡片顯示「成功 n / 失敗 m」。

## 8. 依賴

fastapi、uvicorn、jinja2、yt-dlp、faster-whisper、imagehash、pillow、playwright；開發用：pytest、httpx。外部執行檔：ffmpeg/ffprobe（啟動時檢查是否在 PATH）、claude CLI（選用）。前端不用任何框架。

## 9. 里程碑（實作時依序進行）

1. **M1** 網址解析＋影片資訊＋字幕（json3 解析、清洗、整理）→ 驗證：用 fixtures 跑單元測試，再對一支真實影片用 CLI 跑一次並印出字幕
2. **M2** 視訊流下載＋截圖＋去重合併＋WebP → 驗證：真實影片產出 slides.json，頁數合理（約字幕句數的 1/5 到 1/3）
3. **M3** claude -p 翻譯 → 驗證：用 mock subprocess 測試數量不符、非 JSON 的情況，再跑 1 支真實影片
4. **M4** FastAPI＋JobManager＋SSE＋取消＋歷史 → 驗證：用 httpx 做 API 測試，取消後確認 tmp 已清空
5. **M5** 首頁與閱讀器 UI → 驗證：用 Playwright 在三種寬度截圖
6. **M6** 匯出 HTML／PDF／MD → 驗證：打開三種檔案確認內容
7. **M7** Whisper 備援＋播放清單 → 驗證：一支沒有字幕的影片、一個 3 支影片的播放清單

實作階段依照全域鐵律，`.py`／`.yml` 檔案要走 code-writer → code-qa → code-reviewer。本專案屬於「複雜」等級（多個檔案、超過 200 行），QA 要跑 20 個以上的 test case，一定要派 reviewer。

## 10. 驗證（端對端）

- `uv run pytest`：網址解析（6 種格式＋無效網址）、json3 清洗（滾動重複的 fixture）、字幕整理、去重分組（合成圖片）、翻譯降級（mock）、API 流程。
- `uv run uvicorn app.main:app` → 用瀏覽器送出一支約 10 分鐘的簡報型影片 → 確認進度條、閱讀器翻頁、雙語切換、三種匯出都正常 → 再送一支影片並在處理中途取消，確認 `data/tmp` 已清空。
