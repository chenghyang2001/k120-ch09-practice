#!/bin/bash
set -euo pipefail
# 換電腦一鍵安裝（Git Bash）：逐項檢查必要工具並印出修正指令。
# 用法：bash setup.sh            檢查 + uv sync + 安裝 Chromium
#       bash setup.sh --dry-run  只檢查，不安裝任何東西

cd "$(dirname "$0")"

dry_run=0
case "${1:-}" in
  --dry-run) dry_run=1 ;;
  "") ;;
  *) echo "用法：bash setup.sh [--dry-run]" >&2; exit 2 ;;
esac

failed=0
ok() { echo "✅ $1"; }
bad() { echo "❌ $1"; echo "   修正：$2"; failed=$((failed + 1)); }
warn() { echo "⚠️  $1"; echo "   修正：$2"; }
has() { command -v "$1" >/dev/null 2>&1; }

echo "== 檢查必要工具 =="

has_uv=0
if has uv; then ok "uv：$(uv --version)"; has_uv=1
else bad "找不到 uv" "winget install astral-sh.uv（裝完重開 Git Bash）"; fi

for tool in ffmpeg ffprobe; do
  if has "$tool"; then ok "$tool"
  else bad "找不到 $tool（截圖必備）" "winget install Gyan.FFmpeg（裝完重開 Git Bash）"; fi
done

# 沒有 claude 仍會產出原文版，所以只算警告
if ! has claude; then
  warn "找不到 claude CLI，翻譯功能停用（仍會產出原文版本）" "安裝 Claude Code 後執行 claude 登入"
elif reply=$(timeout 60 claude -p --model haiku --setting-sources= --strict-mcp-config "只回覆 OK 兩個字" 2>/dev/null) \
    && [[ "$reply" == *OK* ]]; then
  ok "claude CLI 已登入"
else
  warn "claude CLI 無法回應（可能未登入），翻譯功能停用" "執行 claude 並完成登入"
fi

remote="gdrive"
if [[ -f .env ]]; then
  # .env 有設定就用它的 remote 名稱；只取值，不 source 整個檔案
  env_remote=$(grep -E '^DRIVE_REMOTE=' .env | head -n 1 | cut -d= -f2- | tr -d '\r"' || true)
  [[ -n "$env_remote" ]] && remote="$env_remote"
fi
if ! has rclone; then
  bad "找不到 rclone（存到 Google Drive 需要）" "winget install Rclone.Rclone，再執行 rclone config"
elif rclone listremotes 2>/dev/null | tr -d '\r' | grep -qx "${remote}:"; then
  ok "rclone remote '${remote}:'"
else
  bad "rclone 尚未設定 remote '${remote}'" "rclone config（新增 Google Drive remote，名稱填 ${remote}）"
fi

if [[ -f .env ]]; then
  ok ".env 已存在"
elif [[ $dry_run -eq 1 ]]; then
  bad ".env 不存在" "cp .env.example .env 後填寫 Gmail 設定（dry-run 不自動複製）"
else
  cp .env.example .env || { echo "錯誤：無法建立 .env" >&2; exit 1; }
  warn "已從 .env.example 建立 .env" "用編輯器打開 .env 填寫 GMAIL_USER、GMAIL_APP_PASSWORD"
fi

echo
echo "== 安裝 Python 套件與 Chromium =="
if [[ $dry_run -eq 1 ]]; then
  echo "（dry-run：略過 uv sync 與 playwright install chromium）"
elif [[ $has_uv -eq 0 ]]; then
  bad "沒有 uv，無法安裝套件" "先安裝 uv 再重跑 bash setup.sh"
else
  uv sync || { echo "錯誤：uv sync 失敗" >&2; exit 1; }
  ok "uv sync"
  # 沒有 Chromium 只影響 PDF，失敗不中斷
  if PYTHONUTF8=1 uv run playwright install chromium; then ok "Chromium（PDF 匯出）"
  else warn "Chromium 安裝失敗，PDF 匯出不可用" "uv run playwright install chromium"; fi
fi

echo
if [[ $failed -gt 0 ]]; then
  echo "還有 ${failed} 項未通過，照上面的「修正」處理後再跑一次 bash setup.sh"
else
  echo "全部就緒"
fi
echo "啟動方式：雙擊 start.bat，或在 Git Bash 執行"
echo "  PYTHONUTF8=1 uv run uvicorn app.main:app --host 127.0.0.1 --port 8000"
echo "然後開 http://127.0.0.1:8000"
