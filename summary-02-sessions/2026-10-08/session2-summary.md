# Session 2 Summary — 雲端化評估、Drive／Gmail 傳送、grill-me-dev 升級、快速模式重跑教材

- **日期**：2026-10-07 晚～2026-10-08（接續 Session 1 同一個對話）
- **專案**：`~/workspace/k120-ch09-practice`（另外動到 `~/.claude`、`k120-ch08-practice-grill`、`kindle-120-claude-code-vibe-coding-2e`）
- **機器**：家用機 DESKTOP-6LST1BR

## 完成事項

### 1. 使用說明與總整理

- 撰寫「自己跑一次完整流程」的操作說明（啟動、7 步試跑、需要的條件、已知狀況、資料位置），同時顯示在對話裡並寄到 Gmail。
- 把 Session 1 到目前的所有工作整理成一封總整理信寄到 Gmail（開發過程、雲端評估結論、使用方式、設定狀態、待辦、已知限制，不含密碼）。

### 2. 雲端部署評估（結論：不上雲）

- 比較 claude.ai artifact、GitHub Pages、Vercel、VPS、NUC＋Cloudflare Tunnel：前三者不能執行 yt-dlp／ffmpeg；NUC 目前無法使用。
- 使用者決定：只有自己使用、用 VPS、翻譯盡量走 Max 訂閱。
- VPS（`claude@187.127.109.145`）偵察：2 核、7.8G RAM、磁碟只剩 9.6G（90%）、nginx＋sslip.io 憑證、cloudflared、docker；`claude -p` 走 Max 可用。
- **YouTube 擋 VPS**：3 支影片 2 支出現「Sign in to confirm you're not a bot」，影片下載全部失敗；VPS 預設走 IPv6 出口。
- 要求在 VPS 嘗試繞過機器人檢查（強制 IPv4／WARP／PO Token），被 auto mode 以 `[Third-Party Attack]` 拒絕，依規則停止、未執行任何繞過。
- 使用者改選 **B 方案**：轉換仍在家用或公司網路的電腦做，成果存到 Google Drive 並寄到 Gmail；只有使用者一人使用。

### 3. Drive／Gmail 傳送＋換電腦一鍵安裝（commit `9149567`，已 push）

- `app/pipeline/delivery.py`：rclone 上傳 Drive（`gdrive:YouTube投影片/<標題>`）、Gmail SMTP 寄離線 HTML＋PDF；附件上限從 20MB 改成 **18MB**（base64 膨脹 1.37 倍才不會超過 Gmail 25MB）。
- 送出表單可勾選「完成後存到 Drive／寄到 Gmail」，歷史卡片和閱讀器新增手動按鈕，不可用時反灰並顯示原因。
- `setup.sh`（支援 `--dry-run`）、`start.bat`（雙擊啟動並開瀏覽器）、`README.md`、`.env.example`；新增 python-dotenv。
- 測試：**269 passed**，ruff 通過。
- 設定過程：
  - 使用者第一次 `rclone config` 建錯成 `gofile` 類型、名稱 `K120-Youtube-To-Slides`，指導刪除後重建 `gdrive`（drive 類型），`rclone lsd gdrive:` 成功。
  - 使用者把 Gmail 應用程式密碼貼在對話裡；我建立 `.env` 被權限的 deny 規則擋下，改請使用者自己用 `! cp .env.example .env && notepad .env` 建立。最後確認 `drive_status()`、`gmail_status()` 都是 True。
  - 確認本 session 對話記錄檔已被 `~/.claude` 的 gitignore 排除，不會推上 GitHub。

### 4. grill-me-dev 改成使用者層級

- 新增 `~/.claude/skills/grill-me-dev/SKILL.md`：內文 8 行保留，description 拿掉「提到 grill-me 時使用」，加上觸發詞與「不觸發：自我測驗、考考我、grill me（交給 grill-me）」，避免和使用者層級的 `grill-me` 撞觸發詞。
- 從 `k120-ch08-practice-grill` 用 `git rm` 刪除專案層級那份（commit `849c795`）；`~/.claude` commit `8b678cf4`（含 `doc/my-skills-catalog.md`「開發／專案／Git」分類 11→12）。
- 驗證：在 ch09 專案的 session 中，skill 清單已出現新版 `grill-me-dev`。

### 5. 第 1–9 章快速模式重跑教材（教材 repo commit `9373161`，已 push）

- 使用者決定：不做旁白與字幕影片；第 7 章只做 7-1；第 9 章只做書上 4 步；另一台是家用電腦。
- 子代理建立：
  - `teaching/FAST-RUN.md`（236 行）：逐章練習資料夾、提示詞位置、保留／省略、對照檢查點、預估時間、可否平行。
  - 改寫 `teaching/steps/fast-demo.md` 成第 1–9 章通用。
  - `teaching/START-PROMPT.md` 最前面新增「另一台家用電腦：快速模式重跑」貼上段。
  - `teaching/tools/fast-start.bat`（134 行，支援 `--no-launch`）與 `fast-start-exception.md`。
  - `teaching/HANDOFF.md` 新增快速模式進度表。
- 使用說明與「第 3／5／6／7 章例外做法」更正，已各寄 Gmail 和 Telegram。

## 關鍵決定

1. **不把轉換搬上雲端**：VPS 被 YouTube 擋，而繞過檢查被判定為攻擊第三方，改採「家用或公司網路的電腦轉換＋Drive／Gmail 保存成果」。
2. **翻譯維持 `claude -p` 走 Max 訂閱**，不改用 API。
3. **grill-me-dev 改為使用者層級**，並與 grill-me 明確區分觸發詞；刪掉專案層級那份。
4. **第 1–9 章重跑採快速模式**：只關掉三 agent 鐵律和教學儀式，保留各章學習重點（subagent、grill-me 提問、Plan 模式訪談）。

## 關鍵技術筆記

- **auto mode 會擋的事**：在 VPS 架設無人值守 Ralph（`[Create Unsafe Agents]`）、嘗試繞過 YouTube 機器人檢查（`[Third-Party Attack]`）。兩者都不能換工具或主機重試。
- **權限 deny 規則**：禁止主 Claude 建立或修改 `.env` 類檔案，連 `cp .env.example .env` 也會被擋；要請使用者用 `!` 自己執行。
- **cmd 執行含中文的 .bat 會出錯**：`goto` 之後會把中文行切壞，`findstr /c:` 也比對不到中文。fast-start.bat 改成全英文訊息，中文內容放在 UTF-8 的 .md，用 `type` 追加；判斷是否已加過，用 `findstr /g:` 比對暫存檔中的標題行。
- **rclone 內建的共用 client_id 會在 2026 年內停用**，需要建立自己的 client_id。
- **`~/.claude` 的 `git pull --rebase`** 會因為其他 session 留下、未 commit 的改動而失敗；push 本身仍可成功（遠端已確認 `8b678cf4`）。
- **子代理測試時在 `%TEMP%` 下了 `rm ~/AppData/Local/Temp/k120*`**，刪除前沒有先列出符合的檔案，可能誤刪其他 k120 開頭的暫存檔（`k120yt` 資料夾仍在）。

## 產出檔案

| 路徑 | 說明 | commit |
| --- | --- | --- |
| `app/pipeline/delivery.py`、`tests/test_delivery.py` | Drive／Gmail 傳送與 23 個測試 | `9149567` |
| `app/config.py`、`app/jobs.py`、`app/main.py`、`app/static/*`、`app/templates/*` | 傳送設定、自動傳送、API、UI 按鈕 | `9149567` |
| `setup.sh`、`start.bat`、`README.md`、`.env.example` | 換電腦安裝與說明 | `9149567` |
| `~/.claude/skills/grill-me-dev/SKILL.md`、`~/.claude/doc/my-skills-catalog.md` | 使用者層級 skill 與目錄 | `8b678cf4` |
| `k120-ch08-practice-grill/.claude/skills/grill-me-dev/`（刪除） | 移除專案層級 | `849c795` |
| `kindle-120-.../teaching/FAST-RUN.md`、`steps/fast-demo.md`、`START-PROMPT.md`、`HANDOFF.md`、`tools/fast-start.bat`、`tools/fast-start-exception.md` | 快速模式重跑教材 | `9373161` |
| `.env`（不入版控） | 使用者自行建立，含 Gmail 帳號與應用程式密碼 | — |

## HANDOFF（下次 session 優先處理）

### 立即行動

- [ ] 在家用機雙擊 `start.bat`，用短片 `jNQXAC9IVRw` 勾選「存到 Drive＋寄到 Gmail」實測一次，確認 Drive 出現 `YouTube投影片/` 資料夾、Gmail 收到 HTML＋PDF 附件
- [ ] 【安全】Gmail 應用程式密碼曾貼在對話裡：確認能寄信後，到 <https://myaccount.google.com/apppasswords> 刪除舊的、產生新的，再自己更新 `.env`
- [ ] 在另一台家用電腦照 `teaching/START-PROMPT.md` 最前面那段開始快速模式重跑第 1–9 章

### 進行中（需接續）

- Drive／Gmail 功能程式已完成、設定都已就緒（`drive_status`、`gmail_status` 皆 True），但**尚未用真實影片完整跑過自動傳送**。
- 快速模式重跑教材已推上教材 repo（`9373161`），**尚未在另一台電腦實際使用**；教材的 HANDOFF 快速模式進度表全部是 ⬜。
- VPS 上 `~/k120-ch09-practice` 仍保留（Ralph 未啟動、`.venv` 保留），之後不用可以刪除。

### 注意事項

- 本專案 M2 之後都是快速模式，未經 QA／審查；Drive／Gmail 功能也一樣。
- 不要再嘗試在 VPS 上繞過 YouTube 的機器人檢查；auto mode 已判定為攻擊第三方。
- rclone 共用 client_id 在 2026 年內會停用，到時上傳 Drive 會失敗，需要自建 client_id。
- 主 Claude 不能寫 `.env`（deny 規則），需要時請使用者用 `!` 自己執行。
- fast-start.bat 的第 3、5、6、7 章有例外做法，細節以 `teaching/FAST-RUN.md` 為準。
