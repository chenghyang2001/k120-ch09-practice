// 閱讀器：投影片＋本頁字幕、語言模式、翻頁、縮圖列、匯出、全螢幕
(function () {
  "use strict";

  var MODES = ["orig", "tr", "both"];
  var HTML_WARN_BYTES = 50 * 1024 * 1024;
  var jobId = document.body.dataset.jobId;
  var pageKey = "k120:page:" + jobId;
  var modeKey = "k120:mode";
  var job = null;
  var slides = [];
  var page = 0;
  var mode = "both";

  function $(id) {
    return document.getElementById(id);
  }

  function el(tag, cls, text) {
    var node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text !== undefined && text !== null) node.textContent = text;
    return node;
  }

  // 無痕模式或封鎖儲存時 localStorage 會丟例外，失敗就當作沒有記錄
  function load(key) {
    try {
      return window.localStorage.getItem(key);
    } catch (e) {
      return null;
    }
  }
  function save(key, value) {
    try {
      window.localStorage.setItem(key, String(value));
    } catch (e) {
      /* 忽略 */
    }
  }

  function fmtTime(sec) {
    var t = Math.floor(sec || 0),
      h = Math.floor(t / 3600),
      m = Math.floor((t % 3600) / 60),
      s = t % 60;
    var pad = function (n) {
      return (n < 10 ? "0" : "") + n;
    };
    return h > 0 ? h + ":" + pad(m) + ":" + pad(s) : pad(m) + ":" + pad(s);
  }

  function notice(message, isError) {
    var box = $("notice");
    box.textContent = message || "";
    box.classList.toggle("error", Boolean(isError));
    box.hidden = !message;
  }

  async function fetchJson(path) {
    var res = await fetch(path);
    var body = null;
    try {
      body = await res.json();
    } catch (e) {
      /* 非 JSON */
    }
    if (!res.ok) {
      throw new Error(
        body && body.error
          ? body.error.message
          : "讀取失敗（HTTP " + res.status + "）",
      );
    }
    return body;
  }

  function mediaUrl(path) {
    return "/media/" + encodeURIComponent(jobId) + "/" + path;
  }

  function hasTranslation(cue) {
    return (
      cue.translation !== undefined &&
      cue.translation !== null &&
      cue.translation !== ""
    );
  }

  // 字幕一律 textContent 插入，避免字幕內容被當 HTML 解析
  function renderCues(cues) {
    var list = $("cue-list");
    list.replaceChildren();
    cues.forEach(function (cue) {
      var li = el("li");
      var ts = el("a", "ts", fmtTime(cue.start));
      ts.href =
        "https://www.youtube.com/watch?v=" +
        encodeURIComponent(job.video_id) +
        "&t=" +
        Math.floor(cue.start || 0) +
        "s";
      ts.target = "_blank";
      ts.rel = "noopener noreferrer";
      var text = el("div", "cue-text");
      if (mode !== "tr" || !hasTranslation(cue))
        text.appendChild(el("div", "cue-orig", cue.text));
      if (mode !== "orig" && hasTranslation(cue))
        text.appendChild(el("div", "cue-tr", cue.translation));
      li.append(ts, text);
      list.appendChild(li);
    });
  }

  function render() {
    var slide = slides[page];
    if (!slide) return;
    var img = $("slide-img");
    img.src = mediaUrl(slide.image);
    img.alt = "投影片 " + slide.index;
    $("page-counter").textContent = page + 1 + " / " + slides.length;
    renderCues(slide.cues || []);
    document.querySelectorAll("[data-mode]").forEach(function (b) {
      b.setAttribute("aria-pressed", String(b.dataset.mode === mode));
    });
    document.body.classList.remove("mode-orig", "mode-tr", "mode-both");
    document.body.classList.add("mode-" + mode);
    document.querySelectorAll(".thumb").forEach(function (t, i) {
      var active = i === page;
      t.classList.toggle("active", active);
      if (active) t.scrollIntoView({ block: "nearest", inline: "center" });
    });
    $("prev-btn").disabled = page === 0;
    $("next-btn").disabled = page === slides.length - 1;
  }

  function go(n) {
    if (!slides.length) return;
    page = Math.max(0, Math.min(slides.length - 1, n));
    save(pageKey, page);
    render();
  }

  function setMode(m) {
    mode = MODES.indexOf(m) === -1 ? "both" : m;
    save(modeKey, mode);
    render();
  }

  function buildThumbs() {
    var strip = $("thumb-strip");
    strip.replaceChildren();
    slides.forEach(function (slide, i) {
      var btn = el("button", "thumb");
      btn.type = "button";
      btn.setAttribute("aria-label", "第 " + slide.index + " 頁");
      var img = el("img");
      img.loading = "lazy";
      img.alt = "";
      img.src = mediaUrl(slide.thumb || slide.image);
      btn.appendChild(img);
      btn.addEventListener("click", function () {
        go(i);
      });
      strip.appendChild(btn);
    });
  }

  function toggleFullscreen() {
    try {
      var result = document.fullscreenElement
        ? document.exitFullscreen()
        : document.documentElement.requestFullscreen();
      if (result && result.catch)
        result.catch(function () {
          notice("無法切換全螢幕", true);
        });
    } catch (e) {
      notice("瀏覽器不支援全螢幕", true);
    }
  }

  // 用 fetch 下載，失敗時能在頁面上顯示錯誤（例如 PDF 缺 Chromium），而不是跳到一頁 JSON
  async function downloadExport(link) {
    var format = link.dataset.export;
    $("export-menu").open = false;
    notice("正在產生 " + format.toUpperCase() + "，請稍候…");
    try {
      var res = await fetch(link.href);
      if (!res.ok) {
        var body = null;
        try {
          body = await res.json();
        } catch (e) {
          /* 非 JSON */
        }
        throw new Error(
          body && body.error ? body.error.message : "HTTP " + res.status,
        );
      }
      var blob = await res.blob();
      var match = /filename="([^"]+)"/.exec(
        res.headers.get("Content-Disposition") || "",
      );
      var a = el("a");
      a.href = URL.createObjectURL(blob);
      a.download = match ? match[1] : "export." + format;
      document.body.appendChild(a);
      a.click();
      a.remove();
      setTimeout(function () {
        URL.revokeObjectURL(a.href);
      }, 10000);
      if (format === "html" && blob.size > HTML_WARN_BYTES) {
        notice("HTML 檔案超過 50MB，建議改選較低畫質重新處理");
      } else {
        notice("");
      }
    } catch (e) {
      notice("匯出失敗：" + e.message, true);
    }
  }

  var DELIVER_TARGETS = { drive: "Drive", gmail: "Gmail" };

  async function deliver(btn) {
    var target = btn.dataset.deliver;
    btn.disabled = true;
    notice("傳送中…");
    try {
      var res = await fetch(
        "/api/jobs/" + encodeURIComponent(jobId) + "/deliver",
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ targets: [target] }),
        },
      );
      var body = null;
      try {
        body = await res.json();
      } catch (e) {
        /* 非 JSON */
      }
      if (!res.ok) {
        throw new Error(
          body && body.error ? body.error.message : "HTTP " + res.status,
        );
      }
      var text = body[target] || "";
      notice(DELIVER_TARGETS[target] + "：" + text, text.indexOf("失敗") === 0);
    } catch (e) {
      notice(DELIVER_TARGETS[target] + "：失敗：" + e.message, true);
    } finally {
      btn.disabled = false;
    }
  }

  // 狀態載入前先反灰，不可用時以 title 說明原因
  async function loadDeliveryStatus() {
    var buttons = document.querySelectorAll("[data-deliver]");
    buttons.forEach(function (b) {
      b.disabled = true;
      b.title = "檢查中…";
    });
    var status = null;
    try {
      status = await fetchJson("/api/delivery/status");
    } catch (e) {
      status = null;
    }
    buttons.forEach(function (b) {
      var s = status && status[b.dataset.deliver];
      b.disabled = !(s && s.ok);
      b.title = s ? s.reason : "無法取得傳送狀態";
    });
  }

  function bindEvents() {
    document.querySelectorAll("[data-deliver]").forEach(function (b) {
      b.addEventListener("click", function () {
        deliver(b);
      });
    });
    document.querySelectorAll("[data-mode]").forEach(function (b) {
      b.addEventListener("click", function () {
        setMode(b.dataset.mode);
      });
    });
    $("prev-btn").addEventListener("click", function () {
      go(page - 1);
    });
    $("next-btn").addEventListener("click", function () {
      go(page + 1);
    });
    $("fullscreen-btn").addEventListener("click", toggleFullscreen);
    $("thumb-toggle").addEventListener("click", function () {
      var bar = $("thumb-bar");
      var collapsed = bar.classList.toggle("collapsed");
      this.setAttribute("aria-expanded", String(!collapsed));
      this.textContent = collapsed ? "縮圖列 ▴" : "縮圖列 ▾";
    });
    document.querySelectorAll("[data-export]").forEach(function (a) {
      a.addEventListener("click", function (e) {
        e.preventDefault();
        downloadExport(a);
      });
    });
    $("slide-img").addEventListener("load", function () {
      // 直式（Shorts）改成左圖右文
      $("reader-main").classList.toggle(
        "portrait",
        this.naturalHeight > this.naturalWidth,
      );
    });
    document.addEventListener("keydown", function (e) {
      if (e.ctrlKey || e.metaKey || e.altKey) return;
      var tag = (e.target.tagName || "").toLowerCase();
      if (tag === "input" || tag === "select" || tag === "textarea") return;
      var key = e.key;
      if (key === "ArrowLeft") go(page - 1);
      else if (key === "ArrowRight") go(page + 1);
      else if (key === "Home") go(0);
      else if (key === "End") go(slides.length - 1);
      else if (key === "f" || key === "F") toggleFullscreen();
      else if (key === "l" || key === "L")
        setMode(MODES[(MODES.indexOf(mode) + 1) % MODES.length]);
      else return;
      e.preventDefault();
    });
  }

  async function init() {
    bindEvents();
    loadDeliveryStatus();
    var savedMode = load(modeKey);
    if (MODES.indexOf(savedMode) !== -1) mode = savedMode;
    try {
      job = await fetchJson("/api/jobs/" + encodeURIComponent(jobId));
      slides = await fetchJson(
        "/api/jobs/" + encodeURIComponent(jobId) + "/slides",
      );
    } catch (e) {
      notice(e.message, true);
      return;
    }
    if (!Array.isArray(slides) || !slides.length) {
      notice("這個工作沒有投影片", true);
      return;
    }
    if (job.title) {
      $("reader-title").textContent = job.title;
      document.title = job.title + " · 閱讀器";
    }
    var savedPage = parseInt(load(pageKey), 10);
    page = isNaN(savedPage)
      ? 0
      : Math.max(0, Math.min(slides.length - 1, savedPage));
    buildThumbs();
    render();
  }

  init();
})();
