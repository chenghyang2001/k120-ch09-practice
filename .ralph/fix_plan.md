# M2 Fix Plan

依序完成，每項都要測試先行、`uv run pytest -q` 全綠才打勾。細節見 `.ralph/specs/m2-spec.md`。

- [ ] 1. `uv sync` 確認環境；跑 `uv run pytest -q` 確認 M1 既有 121 個測試全綠（基準線）
- [ ] 2. `app/config.py` 檔尾追加 M2 常數（QUALITY_HEIGHTS、FRAME_WORKERS、DEDUPE_THRESHOLD、MAX_SLIDE_SEC、MAX_SLIDE_CUES、WEBP_QUALITY、THUMB_WIDTH）
- [ ] 3. `app/pipeline/images.py`（save_webp、make_thumb）+ `tests/test_images.py`
- [ ] 4. `app/pipeline/dedupe.py`（SlideGroup、phash、group_frames：跟組內第一張比、時長／句數上限、代表圖取最後一張）+ `tests/test_dedupe.py`
- [ ] 5. `tests/conftest.py` 追加合成測試影片 fixture（12 秒、三段畫面 pHash 明顯不同），先驗證三段的 pHash 距離 > DEDUPE_THRESHOLD
- [ ] 6. `app/pipeline/frames.py` 截圖部分（midpoints、probe_duration、extract_frames、ffmpeg_missing／frame_failed 錯誤）+ `tests/test_frames.py`
- [ ] 7. `app/pipeline/frames.py` 下載部分（video_format、estimate_bytes、ensure_disk_space、download_video，yt-dlp 全部 monkeypatch）+ 測試
- [ ] 8. `app/pipeline/slides.py`（build_slides：截圖 → 去重 → WebP／縮圖 → slides.json，finally 清除 frames）+ `tests/test_slides.py`（合成影片 6 句 → 3 頁）
- [ ] 9. `app/pipeline/make_slides.py` CLI（只需確認 `uv run python -m app.pipeline.make_slides` 不帶參數時印出用法並以 exit 2 結束；不要用真實網路跑）
- [ ] 10. 最終檢查：`uv run pytest -q` 全綠、ffmpeg 相關測試沒有被 skip（用 `uv run pytest -q -rs` 確認 skip 清單為空）、受保護檔案沒有被改（`git diff --stat a7fd8ba -- app/pipeline/url.py app/pipeline/metadata.py app/pipeline/subtitles.py tests/test_url.py tests/test_metadata.py tests/test_subtitles.py` 應為空）
