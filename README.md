# 影片轉投影片

把 YouTube 影片（或整份播放清單）轉成「一頁投影片 + 該頁字幕」的閱讀器：自動截圖去重、抓字幕（沒有字幕就用 Whisper 轉錄）、用 Claude 翻譯成雙語，可匯出離線 HTML／PDF／Markdown，也能在完成後自動存到 Google Drive、寄到 Gmail，在任何地方都看得到。

## 為什麼要在家用或公司電腦上跑

YouTube 會擋雲端主機（VPS）的下載請求，放在 VPS 上幾乎都會失敗。所以這個 App 要在**家裡或公司網路的電腦**上執行；成果再透過 Drive／Gmail 帶到其他地方看。

## 換電腦安裝（約 10 分鐘）

1. `git clone https://github.com/chenghyang2001/k120-ch09-practice.git` 後進入資料夾
2. 在 Git Bash 執行 `bash setup.sh`
   - 逐項檢查 uv、ffmpeg、claude、rclone、.env，缺什麼就照 ❌ 下面的「修正」指令裝
   - 會自動執行 `uv sync` 與 `uv run playwright install chromium`（PDF 用）
   - 只想看缺什麼、不安裝：`bash setup.sh --dry-run`
3. 打開 `.env` 填寫 Gmail 設定（見下方）
4. 執行 `rclone config` 建立 Google Drive remote（見下方）
5. 雙擊 `start.bat`，瀏覽器會打開 <http://127.0.0.1:8000>

## Google Drive 設定

1. `winget install Rclone.Rclone`
2. `rclone config` → `n` 新增 → 名稱輸入 `gdrive` → 類型選 `drive` → 其餘按 Enter 用預設，瀏覽器跳出時登入 Google 授權
3. 成果會放在 Drive 的 `YouTube投影片/<影片標題>_<影片ID>/`
4. 想改 remote 名稱或資料夾，在 `.env` 設 `DRIVE_REMOTE`、`DRIVE_DIR`；要放進特定資料夾就填 `DRIVE_ROOT_FOLDER_ID`

## Gmail 設定

1. Google 帳戶先開啟「兩步驟驗證」
2. 到 <https://myaccount.google.com/apppasswords> 建立「應用程式密碼」（16 碼）
3. 在 `.env` 填 `GMAIL_USER`（你的 Gmail）與 `GMAIL_APP_PASSWORD`；收件人預設是自己，要改就填 `MAIL_TO`
4. 附件是離線 HTML 與 PDF，總大小上限 20MB，超過的檔案會跳過

## 使用方式

- 首頁貼網址 → 選畫質與目標語言 → 勾「完成後存到 Google Drive」「完成後寄到 Gmail」→ 開始
- 已完成的工作，可在歷史卡片或閱讀器工具列按「存到 Drive」「寄到 Gmail」補送
- 沒設定好的項目會反灰，滑鼠移上去或旁邊小字會說明原因

## 常見問題

| 狀況 | 處理 |
| --- | --- |
| 勾選框反灰，顯示「找不到 rclone」或「尚未設定 rclone remote」 | 照上面 Google Drive 設定做完，重新整理頁面 |
| Gmail 顯示「登入失敗，請確認應用程式密碼」 | 密碼要用應用程式密碼，不是 Google 登入密碼；改完 `.env` 要重啟 start.bat |
| 只收到 HTML 沒有 PDF | 還沒裝 Chromium，執行 `uv run playwright install chromium` |
| 只有原文、沒有翻譯 | 找不到 claude 或未登入，執行 `claude` 完成登入 |
| 下載影片失敗 | 確認不是在 VPS 或公司擋 YouTube 的網路上執行 |
| `start.bat` 一閃就關 | 沒裝 uv，先跑 `bash setup.sh` |
