# M2 規格：視訊流下載 → 中點截圖 → pHash 去重合併 → WebP 輸出

整體規格見 `docs/spec.md` 2.5–2.7。本檔是 M2 的施工圖，Ralph 以本檔為準。

## 既有程式（M1，已完成，不可修改）

- `app/pipeline/subtitles.py`：`@dataclass Cue(id:int, start:float, end:float, text:str)`（秒）
- `app/errors.py`：`AppError(code: str, message: str)`，code 英文、message 繁中
- `app/config.py`：既有常數保留，只能**新增**
- `app/pipeline/url.py`、`metadata.py`、`probe.py`
- 既有 121 個測試（`tests/test_url.py`、`test_metadata.py`、`test_subtitles.py`）必須維持全綠

## 依賴（已在 pyproject.toml，不要再加別的套件）

`yt-dlp`、`pillow`、`imagehash`；dev：`pytest`。外部執行檔：`ffmpeg`、`ffprobe`（VPS 已裝）。
一律用 `uv run pytest -q` 執行測試（第一次先 `uv sync`）。

## 新增設定（`app/config.py` 末尾追加）

```python
# M2 截圖與去重
QUALITY_HEIGHTS = (360, 480, 720, 1080)
FRAME_WORKERS = 4
DEDUPE_THRESHOLD = 8      # pHash 漢明距離 ≤ 門檻 → 同一頁
MAX_SLIDE_SEC = 60.0      # 一頁最長時間，避免講者影片整支變一頁
MAX_SLIDE_CUES = 10       # 一頁最多字幕句數
WEBP_QUALITY = 80
THUMB_WIDTH = 320
```

## 模組

### `app/pipeline/images.py`

- `save_webp(src: Path, dst: Path, quality: int = WEBP_QUALITY) -> Path`：Pillow 開檔轉 RGB 存 WebP，保持原解析度；自動建立父目錄。
- `make_thumb(src: Path, dst: Path, width: int = THUMB_WIDTH) -> Path`：等比例縮到寬 `width`（原圖較窄則不放大），存 WebP。
- 來源不存在或不是圖片 → `AppError("image_failed", "圖片處理失敗")`。

### `app/pipeline/dedupe.py`

- `@dataclass SlideGroup(cue_indices: list[int], frame_index: int)`：`frame_index` 是代表圖（該組**最後一張**）的索引。
- `phash(path: Path) -> imagehash.ImageHash`
- `group_frames(cues: list[Cue], hashes: list[ImageHash], threshold=DEDUPE_THRESHOLD, max_sec=MAX_SLIDE_SEC, max_cues=MAX_SLIDE_CUES) -> list[SlideGroup]`
  - `len(cues) != len(hashes)` → `ValueError`；空輸入 → `[]`。
  - 依序處理：與**目前這組第一張**比較（`hashes[anchor] - hashes[i]`），距離 ≤ threshold 且加入後該組 `cues[i].end - cues[first].start <= max_sec` 且組內句數 < max_cues → 加入；否則開新組。
  - 為什麼跟第一張比：跟前一張比會讓緩慢變化的畫面一路累積偏移，最後整支影片變一頁。
  - 為什麼代表圖取最後一張：簡報常逐條出現條列，最後一張內容最完整。

### `app/pipeline/frames.py`

- `midpoints(cues: list[Cue]) -> list[float]`：每句 `(start+end)/2`。
- `extract_frames(video: Path, timestamps: list[float], out_dir: Path, workers: int = FRAME_WORKERS) -> list[Path]`
  - 每個時間點跑 `ffmpeg -nostdin -loglevel error -ss <t> -i <video> -frames:v 1 -q:v 2 -y <out_dir>/<i:05d>.png`（`-ss` 放 `-i` 前做快速 seek），`ThreadPoolExecutor(workers)` 並行，回傳順序與 timestamps 相同。
  - 時間點超過影片長度時改用 `max(0, duration - 0.1)`（duration 用 `probe_duration`）。
  - ffmpeg 不在 PATH → `AppError("ffmpeg_missing", "找不到 ffmpeg，請先安裝並加入 PATH")`；任一張失敗或沒產出檔案 → `AppError("frame_failed", "截圖失敗")`。
  - subprocess 一律 `encoding="utf-8"`、`capture_output=True`、`check=False` 自行判斷 returncode。
- `probe_duration(video: Path) -> float`：`ffprobe -v error -show_entries format=duration -of csv=p=0`。
- `video_format(height: int) -> str`：回傳 `f"bv*[height<={height}][ext=mp4]/bv*[height<={height}]"`；height 不在 `QUALITY_HEIGHTS` → `ValueError`。
- `estimate_bytes(duration: float, height: int) -> int`：位元率表 `{360: 0.5e6, 480: 1e6, 720: 2.5e6, 1080: 5e6}`（bit/s），回傳 `int(duration * bitrate / 8 * 1.5)`。
- `ensure_disk_space(path: Path, needed: int) -> None`：`shutil.disk_usage(path).free < needed` → `AppError("disk_full", "磁碟空間不足")`。
- `download_video(video_id: str, height: int, tmp_dir: Path, duration: float | None = None) -> tuple[Path, int]`
  - 有 duration 就先 `ensure_disk_space`。
  - `yt_dlp.YoutubeDL({"format": video_format(height), "outtmpl": str(tmp_dir / "video.%(ext)s"), "quiet": True, "no_warnings": True})` 下載 `https://www.youtube.com/watch?v={video_id}`，回傳 `(檔案路徑, 實際高度)`（實際高度取 info 的 `height`，缺值時用要求的 height）。
  - `yt_dlp.utils.DownloadError` → `AppError("video_download_failed", "影片下載失敗，請稍後再試")`，`raise ... from e`。
  - **測試一律 monkeypatch `yt_dlp.YoutubeDL`，不可連網。**

### `app/pipeline/slides.py`

- `build_slides(cues: list[Cue], video: Path, job_dir: Path, tmp_dir: Path, threshold: int = DEDUPE_THRESHOLD) -> list[dict]`
  1. `extract_frames(video, midpoints(cues), tmp_dir / "frames")`
  2. 對每張 PNG 算 `phash` → `group_frames`
  3. 每組代表圖 → `job_dir/images/{n:04d}.webp`（`save_webp`）與 `job_dir/thumbs/{n:04d}.webp`（`make_thumb`），n 從 1 起
  4. 產生 slide：`{"index": n, "start": 組首句 start, "end": 組末句 end, "image": "images/0001.webp", "thumb": "thumbs/0001.webp", "cues": [{"start", "end", "text"}]}`
  5. 寫 `job_dir/slides.json`（UTF-8、`ensure_ascii=False`、indent=2），回傳 slides 清單
  6. **無論成功或失敗**都在 `finally` 刪除 `tmp_dir / "frames"`
  - cues 為空 → `AppError("no_cues", "沒有字幕可產生投影片")`

### `app/pipeline/make_slides.py`（本機真實影片驗證用 CLI，Ralph 不需要用真實網路跑它）

`uv run python -m app.pipeline.make_slides <URL> [--quality 720] [--out data/jobs]`：
parse_youtube_url → fetch_video_info → validate_info → detect_primary_language → fetch_cues（None 則印「無字幕，需 Whisper（M7 實作）」exit 0）→ download_video 到 `data/tmp/<video_id>/` → build_slides 到 `<out>/<video_id>_<quality>/` → 印出頁數、cue 數、實際畫質、輸出路徑 → `finally` 刪掉 `data/tmp/<video_id>/`。
播放清單印「播放清單將在 M7 支援」。AppError → stderr 印 `錯誤 [code]：message`，exit 1。開頭 `sys.stdout.reconfigure(encoding="utf-8")`。

## 測試（`tests/test_images.py`、`test_dedupe.py`、`test_frames.py`、`test_slides.py`）

- **合成測試影片**（conftest fixture，`tmp_path` 內）：用 ffmpeg lavfi 串三段純色，例如
  `ffmpeg -f lavfi -i color=c=red:s=320x240:d=4 -f lavfi -i color=c=red:s=320x240:d=4 ...`，或用 `testsrc` / `drawbox` 做出**三段畫面明顯不同**的 12 秒影片（0–4 秒 A、4–8 秒 B、8–12 秒 C）。純色 pHash 可能全相同，所以每段要有不同的幾何圖樣（例如 `testsrc`、`smptebars`、`mandelbrot` 或不同位置的 drawbox），先確認三段的 pHash 距離 > 門檻再寫斷言。
  - 系統沒有 ffmpeg 時用 `pytest.mark.skipif(shutil.which("ffmpeg") is None, ...)` 跳過（VPS 有 ffmpeg，所以在 VPS 上這些測試**必須真的執行並通過**，不可全被 skip）。
- images：存 WebP 後可用 Pillow 開啟、格式為 WEBP、解析度不變；縮圖寬 320；窄圖不放大；壞檔丟 AppError。
- dedupe：用 Pillow 合成圖片算 hash；相同畫面合併、不同畫面分開、跟第一張比較（漸變序列不會一路合併）、max_sec 與 max_cues 強制切頁、代表圖是最後一張、長度不符丟 ValueError、空輸入回 []。
- frames：midpoints 正確；用合成影片截圖張數與順序正確、超過片長的時間點不爆；ffmpeg 不存在時（monkeypatch `shutil.which` 回 None）丟 `ffmpeg_missing`；video_format / estimate_bytes / ensure_disk_space（monkeypatch `shutil.disk_usage`）；download_video 用假 YoutubeDL 驗證 format 字串、回傳路徑與實際高度、DownloadError 對應。
- slides：用合成影片 + 6 句 cue（每段 2 句）→ 產生 3 頁、slides.json 內容與圖片檔存在、tmp frames 目錄已刪除；extract 失敗時 frames 目錄仍被刪除；空 cues 丟 no_cues。

## 不要做

不做 FastAPI、工作佇列、取消、翻譯、Whisper、匯出、UI（M3 以後）。不改 M1 的檔案與測試。不加新的第三方套件。
