@echo off
chcp 65001 >nul
rem 雙擊啟動：開瀏覽器並啟動伺服器（關掉這個視窗就停止）
cd /d %~dp0
set PYTHONUTF8=1

where uv >nul 2>nul
if errorlevel 1 (
  echo 錯誤：找不到 uv，請先執行 winget install astral-sh.uv，或在 Git Bash 執行 bash setup.sh
  pause
  exit /b 1
)

rem 伺服器啟動需要幾秒，瀏覽器先開時可能要重新整理一次
start "" http://127.0.0.1:8000
uv run uvicorn app.main:app --host 127.0.0.1 --port 8000
if errorlevel 1 (
  echo 錯誤：伺服器啟動失敗，請看上面的訊息；8000 埠被占用時請先關掉另一個視窗
  pause
)
