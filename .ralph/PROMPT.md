# Ralph 開發指令：k120-ch09-practice M2

## 目標

在既有的 YouTube → 投影片閱讀器專案上實作 **M2：視訊流下載 → 字幕中點截圖 → pHash 去重合併 → WebP 輸出**。
完整施工圖：`.ralph/specs/m2-spec.md`（以它為準）；整體背景：`docs/spec.md`。

## 每一輪的做法

1. 讀 `.ralph/fix_plan.md`，挑**第一個**未勾選的項目，只做這一項。
2. 測試先行：先寫該項的測試（應該先失敗），再寫實作到通過。
3. 執行 `uv run pytest -q`（第一輪先跑 `uv sync`）。**全部**測試（含 M1 既有的 121 個）都必須通過才算完成這一項。
4. 完成後把 fix_plan 該項改成 `- [x]`，然後 `git add` 具名檔案 + `git commit`（繁體中文訊息，格式「新增／修復 + 簡短說明」）。不要 push。

## 原則

- 程式碼精簡：只寫規格要求的東西，不加額外功能、不過度抽象。函式 ≤ 30 行為佳。
- 註解與 docstring 用繁體中文，註解寫「為什麼」不寫「做什麼」。
- 所有 subprocess 加 `encoding="utf-8"`；讀寫檔案加 `encoding="utf-8"`。
- 路徑用 `pathlib.Path`，不寫死任何使用者路徑。
- **測試不可連網**：yt-dlp 一律 monkeypatch；影片用 ffmpeg lavfi 在 `tmp_path` 合成。
- 需要 ffmpeg 的測試在這台機器上必須真的執行並通過（不能全被 skip）。
- Implementation > Tests > Docs，不要寫多餘文件或 busywork。

## 受保護檔案（不可修改）

- `app/pipeline/url.py`、`app/pipeline/metadata.py`、`app/pipeline/subtitles.py`、`app/pipeline/probe.py`、`app/errors.py`
- `tests/test_url.py`、`tests/test_metadata.py`、`tests/test_subtitles.py`、`tests/fixtures/`
- `docs/`、`.ralph/specs/`、`.ralph/PROMPT.md`、`pyproject.toml`、`uv.lock`、`CLAUDE.md`
- `app/config.py` 只能在檔尾**追加**規格指定的常數。
- `tests/conftest.py` 只能**追加** fixture，不可改既有內容。

## 退出規則

每輪結束輸出狀態塊。只有當 **fix_plan 全部勾選** 且 **`uv run pytest -q` 全部通過（0 failed、0 error）** 時才設 `EXIT_SIGNAL: true`：

```
---RALPH_STATUS---
STATUS: IN_PROGRESS | COMPLETE | BLOCKED
TASKS_COMPLETED_THIS_LOOP: <數字>
FILES_MODIFIED: <數字>
TESTS_STATUS: PASSING | FAILING | NOT_RUN
WORK_TYPE: IMPLEMENTATION | TESTING | DOCUMENTATION | REFACTORING
EXIT_SIGNAL: false | true
RECOMMENDATION: <一句話說明下一步>
---END_RALPH_STATUS---
```

如果同一個錯誤連續兩輪無法解決（例如 ffmpeg 合成影片的 pHash 無法區分），把 STATUS 設為 BLOCKED，並在 RECOMMENDATION 寫清楚卡在哪裡，不要繞過或刪掉測試。
