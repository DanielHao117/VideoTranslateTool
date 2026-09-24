const $ = (id) => document.getElementById(id);

let currentUrl = "";
let lastInfo = null;

let cues = [];
let activeIdx = -1;
let syncTimer = null;
let localMode = false;
let lastTaskId = null;
let paused = false;

function fmtDuration(sec) {
  if (!sec) return "";
  const h = Math.floor(sec / 3600);
  const m = Math.floor((sec % 3600) / 60);
  const s = Math.floor(sec % 60);
  return (h ? h + ":" : "") + String(m).padStart(2, "0") + ":" + String(s).padStart(2, "0");
}

function fmtSpeed(bps) {
  if (!bps) return "";
  const units = ["B/s", "KB/s", "MB/s", "GB/s"];
  let i = 0;
  while (bps >= 1024 && i < units.length - 1) { bps /= 1024; i++; }
  return bps.toFixed(1) + units[i];
}

async function postJSON(url, body) {
  const res = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || "请求失败");
  return data;
}

$("url").addEventListener("keydown", (e) => {
  if (e.key === "Enter") {
    e.preventDefault();
    $("fetchBtn").click();
  }
});

function switchView(view) {
  document.querySelectorAll(".nav-item").forEach((b) => {
    b.classList.toggle("active", b.dataset.view === view);
  });
  $("view-download").classList.toggle("hidden", view !== "download");
  $("view-play").classList.toggle("hidden", view !== "play");
  document.body.classList.toggle("playing", view === "play");
}

document.querySelectorAll(".nav-item").forEach((btn) => {
  btn.addEventListener("click", () => {
    switchView(btn.dataset.view);
    if (btn.dataset.view === "play") startOnline();
  });
});

$("fetchBtn").addEventListener("click", async () => {
  const url = $("url").value.trim();
  if (!url) return alert("请输入视频链接");
  currentUrl = url;
  const btn = $("fetchBtn");
  btn.disabled = true;
  btn.textContent = "解析中…";
  try {
    const info = await postJSON("/api/formats", { url });
    lastInfo = info;

    $("meta").classList.remove("hidden");
    $("thumb").src = info.thumbnail || "";
    $("title").textContent = info.title || "";
    $("sub").textContent =
      (info.uploader ? info.uploader + " · " : "") + fmtDuration(info.duration);

    const sel = $("format");
    sel.innerHTML = "";
    const best = document.createElement("option");
    best.value = "best";
    best.textContent = "最佳 (自动)";
    sel.appendChild(best);

    const byHeight = new Map();
    for (const f of info.formats) {
      if (!f.has_video) continue;
      const m = /x(\d+)$/.exec(f.resolution || "") || /(\d+)p$/.exec(f.resolution || "");
      if (!m) continue;
      const h = +m[1];
      const cur = byHeight.get(h);
      if (!cur || (f.size_bytes || 0) > (cur.size_bytes || 0)) byHeight.set(h, f);
    }
    for (const h of [...byHeight.keys()].sort((a, b) => b - a)) {
      const f = byHeight.get(h);
      const opt = document.createElement("option");
      opt.value = f.id;
      opt.textContent = h + "p" + (f.size ? " · " + f.size : "");
      sel.appendChild(opt);
    }

    $("downloadCard").classList.remove("hidden");
    $("nowPlaying").textContent = info.title || "当前视频";
    loadSubtitles();
  } catch (e) {
    alert(e.message);
  } finally {
    btn.disabled = false;
    btn.textContent = "解析";
  }
});

$("pickBtn").addEventListener("click", async () => {
  try {
    const data = await postJSON("/api/pickdir", {});
    if (data.dir) $("saveDir").value = data.dir;
    else if (data.error) alert("无法打开目录选择器: " + data.error);
  } catch (e) {
    alert("无法打开目录选择器，请手动输入路径");
  }
});

$("downloadBtn").addEventListener("click", async () => {
  if (!currentUrl) return alert("请先解析链接");
  const btn = $("downloadBtn");
  btn.disabled = true;
  $("progressWrap").classList.remove("hidden");
  $("bar").style.width = "0%";
  $("progressText").textContent = "正在启动…";

  try {
    const { task_id } = await postJSON("/api/download", {
      url: currentUrl,
      format: $("format").value,
      save_dir: $("saveDir").value.trim(),
      gen_srt: $("genSrt").checked,
    });
    lastTaskId = task_id;
    paused = false;
    $("pauseBtn").classList.remove("hidden");
    $("pauseBtn").textContent = "暂停";
    $("pauseBtn").disabled = false;
    trackProgress(task_id);
  } catch (e) {
    alert(e.message);
    btn.disabled = false;
  }
});

function trackProgress(taskId) {
  const es = new EventSource("/api/progress/" + taskId);
  es.onmessage = (ev) => {
    const t = JSON.parse(ev.data);
    const pct = (t.percent || 0).toFixed(1);
    $("bar").style.width = pct + "%";

    if (t.status === "subtitles") {
      $("progressText").textContent = t.message || "正在生成双语字幕…";
      return;
    }

    if (t.status === "pausing") {
      $("progressText").textContent = "正在暂停…";
      return;
    }

    if (t.status === "paused") {
      paused = true;
      $("pauseBtn").textContent = "继续";
      $("pauseBtn").disabled = false;
      $("progressText").textContent = "已暂停";
      return;
    }

    const parts = [pct + "%"];
    if (t.downloaded) parts.push((t.downloaded / 1048576).toFixed(1) + "MB" + (t.total ? " / " + (t.total / 1048576).toFixed(1) + "MB" : ""));
    if (t.speed) parts.push(fmtSpeed(t.speed));
    if (t.eta != null) parts.push("剩余 " + t.eta + "s");
    if (t.status === "finished") parts.push("完成 ✓");
    if (t.status === "error") parts.push("出错: " + (t.error || ""));
    $("progressText").textContent = parts.join("  ·  ");

    if (t.status === "finished") {
      es.close();
      paused = false;
      $("pauseBtn").classList.add("hidden");
      $("downloadBtn").disabled = false;
      if (t.srt) {
        $("progressText").textContent += "  ·  双语字幕 " + t.srt.split(/[\\/]/).pop();
      } else if (t.srt_error) {
        $("progressText").textContent += "  ·  字幕生成失败: " + t.srt_error;
      }
      $("playLocalBtn").classList.remove("hidden");
      $("localBtn").disabled = false;
    }
    if (t.status === "error") {
      es.close();
      paused = false;
      $("pauseBtn").classList.add("hidden");
      $("downloadBtn").disabled = false;
      alert("下载失败: " + t.error);
    }
  };
  es.onerror = () => { es.close(); $("downloadBtn").disabled = false; };
}

$("pauseBtn").addEventListener("click", async () => {
  if (!lastTaskId) return;
  const btn = $("pauseBtn");
  btn.disabled = true;
  try {
    if (paused) {
      await postJSON("/api/resume/" + lastTaskId, {});
      paused = false;
      btn.textContent = "暂停";
      $("progressText").textContent = "正在继续…";
    } else {
      await postJSON("/api/pause/" + lastTaskId, {});
      paused = true;
      btn.textContent = "继续";
      $("progressText").textContent = "正在暂停…";
    }
  } catch (e) {
    alert(e.message);
  } finally {
    btn.disabled = false;
  }
});

async function loadSubtitles() {
  if (!currentUrl) return;
  resetSubtitles();
  $("subStatus").textContent = "正在获取字幕…";
  try {
    const data = await postJSON("/api/subtitles", {
      url: currentUrl,
      translate: false,
    });
    cues = (data.lines || []).map((l) => ({ ...l, zh: l.zh || "" }));
    buildSubtitleTrack();
    translateAll(data.file);
  } catch (e) {
    $("subStatus").textContent = "失败: " + e.message;
  }
}

async function translateAll(file) {
  const BATCH = 50;
  const total = cues.length;
  const status = $("subStatus");

  if (total === 0) {
    status.textContent = `${file} · 没有可翻译的字幕`;
    return;
  }

  status.textContent = `${file} · 共 ${total} 句 · 翻译中… 0%`;

  for (let i = 0; i < total; i += BATCH) {
    const batch = cues.slice(i, i + BATCH);
    try {
      const res = await postJSON("/api/translate", { texts: batch.map((c) => c.en) });
      const arr = res.translations || [];
      for (let j = 0; j < batch.length; j++) batch[j].zh = arr[j] || "";
    } catch (e) {
      status.textContent = `${file} · 翻译失败（第 ${i + 1} 句起）: ${e.message}`;
      return;
    }
    if (activeIdx >= i && activeIdx < i + batch.length) updateOverlay();
    updateTrackText();

    const done = Math.min(i + BATCH, total);
    const pct = Math.round((done / total) * 100);
    status.textContent = `${file} · 翻译中… ${done}/${total} · ${pct}%`;
  }

  status.textContent = `${file} · 共 ${total} 句 · 翻译完成 100%`;
}

function resetSubtitles() {
  cues = [];
  activeIdx = -1;
  buildSubtitleTrack();
  $("subStatus").textContent = "";
  $("overlay").innerHTML = "";
}

let onlineUrl = "";
let onlineTaskId = null;
let onlineReady = false;

function startOnline() {
  if (!currentUrl) return;
  switchView("play");
  if (onlineUrl === currentUrl && onlineTaskId) {
    if (onlineReady) playCached();
    return;
  }
  onlineUrl = currentUrl;
  onlineTaskId = null;
  onlineReady = false;
  const status = $("cacheStatus");
  status.textContent = "正在缓冲视频…";
  postJSON("/api/cache", { url: currentUrl })
    .then((data) => {
      onlineTaskId = data.task_id;
      if (data.ready) {
        onlineReady = true;
        status.textContent = "缓冲完成";
        playCached();
      } else {
        trackCache(data.task_id);
      }
    })
    .catch((e) => { status.textContent = "缓冲失败: " + e.message; });
}

function trackCache(taskId) {
  const es = new EventSource("/api/progress/" + taskId);
  es.onmessage = (ev) => {
    const t = JSON.parse(ev.data);
    if (t.status === "finished") {
      es.close();
      onlineReady = true;
      $("cacheStatus").textContent = "缓冲完成";
      playCached();
    } else if (t.status === "error") {
      es.close();
      $("cacheStatus").textContent = "缓冲失败: " + (t.error || "");
    } else {
      const parts = ["正在缓冲 " + (t.percent || 0).toFixed(0) + "%"];
      if (t.speed) parts.push(fmtSpeed(t.speed));
      if (t.eta != null) parts.push("剩余 " + t.eta + "s");
      $("cacheStatus").textContent = parts.join("  ·  ");
    }
  };
  es.onerror = () => es.close();
}

function playCached() {
  if (!onlineTaskId) return;
  localMode = true;
  $("onlineBtn").classList.add("active");
  $("localBtn").classList.remove("active");
  const v = $("player");
  v.muted = false;
  v.src = "/api/local/" + onlineTaskId;
  v.load();
  startSync();
  v.play().catch(() => {});
}

function playLocal(taskId) {
  if (!taskId) return alert("请先下载视频");
  localMode = true;
  $("onlineBtn").classList.remove("active");
  $("localBtn").classList.add("active");
  const v = $("player");
  const a = $("playerAudio");
  a.pause();
  a.removeAttribute("src");
  v.muted = false;
  v.src = "/api/local/" + taskId;
  v.load();
  $("soundBtn").classList.add("hidden");
  startSync();
  switchView("play");
  v.play().catch(() => {});
}

$("playLocalBtn").addEventListener("click", () => playLocal(lastTaskId));
$("localBtn").addEventListener("click", () => playLocal(lastTaskId));
$("onlineBtn").addEventListener("click", () => startOnline());

function seekTo(t) {
  const v = $("player");
  v.currentTime = t;
  if (!localMode) $("playerAudio").currentTime = t;
  if (v.paused) v.play().catch(() => {});
}

function playAudio() {
  if (localMode) return;
  const a = $("playerAudio");
  const hint = $("soundBtn");
  a.play().then(() => hint.classList.add("hidden")).catch(() => hint.classList.remove("hidden"));
}

$("soundBtn").addEventListener("click", () => {
  $("playerAudio").play()
    .then(() => $("soundBtn").classList.add("hidden"))
    .catch(() => {});
});

function startSync() {
  if (syncTimer) clearInterval(syncTimer);
  syncTimer = setInterval(syncTick, 120);

  const v = $("player");
  if (v.dataset.wired) return;
  v.dataset.wired = "1";
  v.addEventListener("play", playAudio);
  v.addEventListener("click", playAudio);
  v.addEventListener("pause", () => $("playerAudio").pause());
  v.addEventListener("seeking", syncAudioTime);
  v.addEventListener("seeked", onSeeked);
  v.addEventListener("ratechange", () => { if (!localMode) $("playerAudio").playbackRate = v.playbackRate; });
  setInterval(keepAudioInSync, 1000);
}

function syncAudioTime() {
  if (localMode) return;
  const v = $("player");
  const a = $("playerAudio");
  if (a.readyState === 0 || a.seeking) return;
  if (Math.abs(a.currentTime - v.currentTime) > 0.05) a.currentTime = v.currentTime;
}

function onSeeked() {
  syncAudioTime();
  if (localMode) return;
  const v = $("player");
  const a = $("playerAudio");
  if (!v.paused) playAudio();
}

function keepAudioInSync() {
  if (localMode) return;
  const v = $("player");
  const a = $("playerAudio");
  if (v.paused || a.paused || a.seeking || a.readyState < 2) return;
  if (Math.abs(a.currentTime - v.currentTime) > 0.3) a.currentTime = v.currentTime;
}

function syncTick() {
  const v = $("player");
  if (!v || isNaN(v.currentTime)) return;
  if (subsTrack && document.fullscreenElement !== v && document.webkitFullscreenElement !== v
      && subsTrack.track.mode === "showing") {
    subsTrack.track.mode = "hidden";
  }
  const idx = findCue(v.currentTime);
  if (idx !== activeIdx) setActive(idx);
}

function findCue(t) {
  let lo = 0, hi = cues.length - 1, ans = -1;
  while (lo <= hi) {
    const mid = (lo + hi) >> 1;
    if (cues[mid].start <= t) { ans = mid; lo = mid + 1; }
    else { hi = mid - 1; }
  }
  return ans;
}

function setActive(idx) {
  activeIdx = idx;
  updateOverlay();
}

function updateOverlay() {
  const ov = $("overlay");
  const mode = $("subMode").value;
  const c = activeIdx >= 0 ? cues[activeIdx] : null;
  if (mode === "off" || !c) {
    ov.innerHTML = "";
    return;
  }
  const showEn = mode === "both" || mode === "en";
  const showZh = (mode === "both" || mode === "zh") && c.zh;
  let out = "";
  if (showEn) out += '<div class="ov-en"></div>';
  if (showZh) out += '<div class="ov-zh"></div>';
  ov.innerHTML = out;
  if (showEn) ov.querySelector(".ov-en").textContent = c.en;
  if (showZh) ov.querySelector(".ov-zh").textContent = c.zh;
}

$("subMode").addEventListener("change", () => {
  updateOverlay();
  updateTrackText();
  applyTrackMode();
});

let subsTrack = null;

function fmtVtt(t) {
  const ms = Math.max(0, Math.round(t * 1000));
  const p = (n, w) => String(n).padStart(w, "0");
  return `${p(Math.floor(ms / 3600000), 2)}:${p(Math.floor(ms / 60000) % 60, 2)}:${p(Math.floor(ms / 1000) % 60, 2)}.${p(ms % 1000, 3)}`;
}

function cueText(c) {
  const mode = $("subMode").value;
  if (mode === "en") return c.en;
  if (mode === "zh") return c.zh || c.en;
  return c.zh ? `${c.en}\n${c.zh}` : c.en;
}

function buildSubtitleTrack() {
  const v = $("player");
  if (subsTrack) {
    subsTrack.remove();
    subsTrack = null;
  }
  if (!cues.length) return;
  let vtt = "WEBVTT\n\n";
  cues.forEach((c, i) => {
    vtt += `${i + 1}\n${fmtVtt(c.start)} --> ${fmtVtt(c.end)}\n${cueText(c)}\n\n`;
  });
  const el = document.createElement("track");
  el.kind = "subtitles";
  el.label = "字幕";
  el.src = URL.createObjectURL(new Blob([vtt], { type: "text/vtt" }));
  el.addEventListener("load", () => {
    el.track.mode = "hidden";
    updateTrackText();
    applyTrackMode();
  });
  v.appendChild(el);
  subsTrack = el;
}

function updateTrackText() {
  if (!subsTrack) return;
  const list = subsTrack.track.cues;
  if (!list) return;
  for (let i = 0; i < list.length; i++) list[i].text = cueText(cues[i]);
}

function applyTrackMode() {
  if (!subsTrack) return;
  const v = $("player");
  const fs = document.fullscreenElement || document.webkitFullscreenElement;
  const mode = $("subMode").value;
  subsTrack.track.mode = (fs === v && mode !== "off") ? "showing" : "hidden";
}

document.addEventListener("fullscreenchange", applyTrackMode);
document.addEventListener("webkitfullscreenchange", applyTrackMode);
