// 首頁：送出工作、處理中卡片（SSE 進度）、歷史列表（開啟／頁內確認刪除／播放清單子工作）
(function () {
  "use strict";

  var STAGE_NAMES = {
    queued: "排隊中",
    metadata: "取得影片資訊",
    subtitles: "取得字幕",
    transcribe: "語音轉錄",
    translate: "翻譯字幕",
    download: "下載影片",
    frames: "截圖與去重",
    output: "寫入輸出",
  };
  var STATUS_NAMES = {
    queued: "排隊中",
    running: "處理中",
    done: "完成",
    failed: "失敗",
    cancelled: "已取消",
    interrupted: "已中斷",
  };
  var ACTIVE = ["queued", "running"];
  var sources = {}; // job_id → EventSource
  var pollTimer = null;

  function $(id) {
    return document.getElementById(id);
  }

  function el(tag, cls, text) {
    var node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text !== undefined && text !== null) node.textContent = text;
    return node;
  }

  function fmtDuration(sec) {
    if (sec === null || sec === undefined) return "";
    var t = Math.round(sec),
      h = Math.floor(t / 3600),
      m = Math.floor((t % 3600) / 60),
      s = t % 60;
    var pad = function (n) {
      return (n < 10 ? "0" : "") + n;
    };
    return h > 0 ? h + ":" + pad(m) + ":" + pad(s) : m + ":" + pad(s);
  }

  function fmtDate(iso) {
    if (!iso) return "";
    var d = new Date(iso);
    return isNaN(d) ? iso : d.toLocaleString("zh-TW", { hour12: false });
  }

  function showError(node, message) {
    node.textContent = message;
    node.hidden = !message;
  }

  // FastAPI 的 422 是 {detail:[...]}，自訂錯誤是 {error:{code,message}}
  async function errorMessage(res) {
    try {
      var body = await res.json();
      if (body && body.error && body.error.message) return body.error.message;
      if (body && body.detail) return "輸入格式不正確，請檢查網址、畫質與語言";
    } catch (e) {
      /* 非 JSON 回應 */
    }
    return "請求失敗（HTTP " + res.status + "）";
  }

  async function api(path, options) {
    var res;
    try {
      res = await fetch(path, options);
    } catch (e) {
      throw new Error("無法連線到伺服器");
    }
    if (!res.ok) throw new Error(await errorMessage(res));
    return res.status === 204 ? null : res.json();
  }

  function thumbFor(job) {
    var img = el("img", "job-thumb");
    img.alt = "";
    img.loading = "lazy";
    if (job.status === "done") {
      img.src = "/media/" + encodeURIComponent(job.id) + "/thumbs/0001.webp";
      img.onerror = function () {
        img.removeAttribute("src");
      };
    }
    return img;
  }

  function titleOf(job) {
    return job.title || job.video_id || job.url || job.id;
  }

  // ---------- 處理中卡片 ----------
  function renderActiveCard(job) {
    var card = el("article", "job-card");
    card.dataset.id = job.id;
    var body = el("div", "job-body");
    body.appendChild(el("h3", "job-title", titleOf(job)));
    var stage = el("div", "job-meta");
    stage.dataset.role = "stage";
    body.appendChild(stage);
    var bar = el("div", "progress");
    var fill = el("div", "progress-fill");
    bar.appendChild(fill);
    body.appendChild(bar);
    var actions = el("div", "job-actions");
    var cancel = el("button", "btn btn-danger", "取消");
    cancel.type = "button";
    cancel.addEventListener("click", function () {
      cancelJob(job.id, cancel);
    });
    actions.appendChild(cancel);
    body.appendChild(actions);
    card.append(thumbFor(job), body);
    updateActiveCard(card, job);
    return card;
  }

  function updateActiveCard(card, job) {
    var stageName =
      job.status === "queued"
        ? STAGE_NAMES.queued
        : STAGE_NAMES[job.stage] || job.message || "處理中";
    var stage = card.querySelector('[data-role="stage"]');
    if (job.kind === "playlist") {
      stage.textContent = playlistSummary(job);
    } else {
      stage.textContent = stageName + " · " + (job.progress || 0) + "%";
    }
    var pct =
      job.kind === "playlist" && job.children_total
        ? Math.round(
            (100 * ((job.children_done || 0) + (job.children_failed || 0))) /
              job.children_total,
          )
        : job.progress || 0;
    card.querySelector(".progress-fill").style.width =
      Math.max(0, Math.min(100, pct)) + "%";
    var title = card.querySelector(".job-title");
    title.textContent = titleOf(job);
  }

  async function cancelJob(id, button) {
    button.disabled = true;
    try {
      await api("/api/jobs/" + encodeURIComponent(id) + "/cancel", {
        method: "POST",
      });
      await loadJobs();
    } catch (e) {
      showError($("list-error"), "取消失敗：" + e.message);
      button.disabled = false;
    }
  }

  function subscribe(job) {
    if (sources[job.id] || job.kind === "playlist") return;
    var es;
    try {
      es = new EventSource(
        "/api/jobs/" + encodeURIComponent(job.id) + "/events",
      );
    } catch (e) {
      return;
    }
    sources[job.id] = es;
    es.onmessage = function (ev) {
      var data;
      try {
        data = JSON.parse(ev.data);
      } catch (e) {
        return;
      }
      var card = $("active-list").querySelector(
        '[data-id="' + CSS.escape(job.id) + '"]',
      );
      if (card) updateActiveCard(card, Object.assign({}, job, data));
      if (ACTIVE.indexOf(data.status) === -1) {
        closeSource(job.id);
        loadJobs();
      }
    };
    // 串流在終態後由伺服器關閉也會觸發 error；交給列表重新整理判斷
    es.onerror = function () {
      closeSource(job.id);
      setTimeout(loadJobs, 1500);
    };
  }

  function closeSource(id) {
    if (sources[id]) {
      sources[id].close();
      delete sources[id];
    }
  }

  // ---------- 歷史卡片 ----------
  function playlistSummary(job) {
    return (
      "成功 " +
      (job.children_done || 0) +
      " / 失敗 " +
      (job.children_failed || 0) +
      " / 共 " +
      (job.children_total || 0)
    );
  }

  function renderHistoryCard(job) {
    var card = el("article", "job-card");
    card.dataset.id = job.id;
    var body = el("div", "job-body");
    body.appendChild(el("h3", "job-title", titleOf(job)));
    var meta = el("div", "job-meta");
    if (job.status !== "done")
      meta.appendChild(
        el(
          "span",
          "badge badge-" + job.status,
          STATUS_NAMES[job.status] || job.status,
        ),
      );
    if (job.kind === "playlist") {
      meta.appendChild(el("span", null, "播放清單 · " + playlistSummary(job)));
    } else {
      if (job.duration !== null && job.duration !== undefined)
        meta.appendChild(el("span", null, "時長 " + fmtDuration(job.duration)));
      if (job.slide_count !== null && job.slide_count !== undefined)
        meta.appendChild(el("span", null, job.slide_count + " 頁"));
    }
    meta.appendChild(el("span", null, fmtDate(job.created_at)));
    body.appendChild(meta);
    if (job.error) body.appendChild(el("p", "job-error", job.error));

    var actions = el("div", "job-actions");
    if (job.kind === "playlist") {
      var toggle = el("button", "btn", "展開");
      toggle.type = "button";
      toggle.setAttribute("aria-expanded", "false");
      toggle.addEventListener("click", function () {
        toggleChildren(job, body, toggle);
      });
      actions.appendChild(toggle);
    } else if (job.status === "done") {
      var open = el("a", "btn btn-primary", "開啟");
      open.href = "/read/" + encodeURIComponent(job.id);
      actions.appendChild(open);
    }
    actions.appendChild(deleteControl(job, actions));
    body.appendChild(actions);
    card.append(thumbFor(job), body);
    return card;
  }

  // 頁內確認：第一次點擊換成「確定刪除？ 確定／取消」，不用 confirm()
  function deleteControl(job, actions) {
    var del = el("button", "btn btn-danger", "刪除");
    del.type = "button";
    del.addEventListener("click", function () {
      var box = el("span", "job-actions");
      var text = el("span", "confirm-text", "確定刪除？");
      var yes = el("button", "btn btn-danger", "確定");
      var no = el("button", "btn", "取消");
      yes.type = no.type = "button";
      yes.addEventListener("click", async function () {
        yes.disabled = no.disabled = true;
        try {
          await api("/api/jobs/" + encodeURIComponent(job.id), {
            method: "DELETE",
          });
          await loadJobs();
        } catch (e) {
          showError($("list-error"), "刪除失敗：" + e.message);
          box.replaceWith(del);
        }
      });
      no.addEventListener("click", function () {
        box.replaceWith(del);
      });
      box.append(text, yes, no);
      del.replaceWith(box);
    });
    return del;
  }

  async function toggleChildren(job, body, toggle) {
    var existing = body.querySelector(".children");
    if (existing) {
      existing.remove();
      toggle.textContent = "展開";
      toggle.setAttribute("aria-expanded", "false");
      return;
    }
    toggle.disabled = true;
    try {
      var children = await api(
        "/api/jobs?parent=" + encodeURIComponent(job.id),
      );
      var box = el("div", "children");
      if (!children.length) box.appendChild(el("span", "empty", "沒有子工作"));
      children.forEach(function (child) {
        var row = el("div", "child-row");
        row.appendChild(el("span", null, titleOf(child)));
        row.appendChild(
          el(
            "span",
            "badge badge-" + child.status,
            STATUS_NAMES[child.status] || child.status,
          ),
        );
        if (child.status === "done") {
          var open = el("a", null, "開啟");
          open.href = "/read/" + encodeURIComponent(child.id);
          row.appendChild(open);
        } else if (child.error) {
          row.appendChild(el("span", "job-error", child.error));
        }
        box.appendChild(row);
      });
      body.appendChild(box);
      toggle.textContent = "收合";
      toggle.setAttribute("aria-expanded", "true");
    } catch (e) {
      showError($("list-error"), "讀取子工作失敗：" + e.message);
    } finally {
      toggle.disabled = false;
    }
  }

  // ---------- 列表 ----------
  async function loadJobs() {
    var jobs;
    try {
      jobs = await api("/api/jobs");
      showError($("list-error"), "");
    } catch (e) {
      showError($("list-error"), "讀取列表失敗：" + e.message);
      return;
    }
    var active = jobs.filter(function (j) {
      return ACTIVE.indexOf(j.status) !== -1;
    });
    var history = jobs.filter(function (j) {
      return ACTIVE.indexOf(j.status) === -1;
    });

    var activeList = $("active-list");
    activeList.replaceChildren.apply(activeList, active.map(renderActiveCard));
    $("active-empty").hidden = active.length > 0;
    var historyList = $("history-list");
    historyList.replaceChildren.apply(
      historyList,
      history.map(renderHistoryCard),
    );
    $("history-empty").hidden = history.length > 0;

    var activeIds = active.map(function (j) {
      return j.id;
    });
    Object.keys(sources).forEach(function (id) {
      if (activeIds.indexOf(id) === -1) closeSource(id);
    });
    active.forEach(subscribe);

    // 播放清單沒有 SSE，有處理中的播放清單就輪詢
    clearTimeout(pollTimer);
    if (
      active.some(function (j) {
        return j.kind === "playlist";
      })
    )
      pollTimer = setTimeout(loadJobs, 3000);
  }

  $("job-form").addEventListener("submit", async function (ev) {
    ev.preventDefault();
    var url = $("url").value.trim();
    var errorBox = $("form-error");
    if (!url) {
      showError(errorBox, "請輸入 YouTube 網址");
      return;
    }
    var button = $("submit-btn");
    button.disabled = true;
    showError(errorBox, "");
    try {
      await api("/api/jobs", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          url: url,
          quality: Number($("quality").value),
          target_lang: $("target-lang").value,
        }),
      });
      $("url").value = "";
      await loadJobs();
    } catch (e) {
      showError(errorBox, e.message);
    } finally {
      button.disabled = false;
    }
  });

  loadJobs();
})();
