import os
import re
import shutil
import sys
import io
import json
import time
import uuid
import html
import queue
import threading
import tempfile
import webbrowser
import urllib.error
import urllib.request
from pathlib import Path

import yt_dlp
from flask import (
    Flask,
    request,
    jsonify,
    render_template,
    Response,
    stream_with_context,
    send_file,
)

app = Flask(__name__)
app.config["TEMPLATES_AUTO_RELOAD"] = True

if getattr(sys, "frozen", False):
    BASE_DIR = Path(sys.executable).resolve().parent
else:
    BASE_DIR = Path(__file__).resolve().parent
DEFAULT_DOWNLOAD_DIR = None


def _default_download_dir():
    """Resolve the default save folder on first use, not at startup.

    Prefers a ``downloads`` folder next to the exe / project so the program and
    models stay in their own directory while videos land in a separate
    ``downloads`` folder; falls back to ``E:\\YouTube Downloader`` when it
    cannot be created. Creating it lazily keeps a plain launch (e.g.
    double-click on the exe) from leaving an empty folder behind.
    """
    global DEFAULT_DOWNLOAD_DIR
    if DEFAULT_DOWNLOAD_DIR is not None:
        return DEFAULT_DOWNLOAD_DIR
    for candidate in (BASE_DIR / "downloads", Path(r"E:\YouTube Downloader")):
        try:
            candidate.mkdir(parents=True, exist_ok=True)
        except OSError:
            continue
        DEFAULT_DOWNLOAD_DIR = candidate
        return candidate
    DEFAULT_DOWNLOAD_DIR = BASE_DIR / "downloads"
    return DEFAULT_DOWNLOAD_DIR

TASKS = {}
TASKS_LOCK = threading.Lock()
PLAY_CACHE = {}


def _cache_dir():
    d = Path(tempfile.gettempdir()) / "VideoTranslateTool"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _config_paths():
    """Look for config.json next to the exe first, then the bundled default."""
    paths = [BASE_DIR / "config.json"]
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        paths.append(Path(sys._MEIPASS) / "config.json")
    paths.append(Path.cwd() / "config.json")
    return paths


def _load_config():
    """Merge config files; earlier paths win and empty values never override."""
    cfg = {}
    for p in _config_paths():
        try:
            if not p.exists():
                continue
            data = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        if isinstance(data, dict):
            for k, v in data.items():
                if v not in (None, "", []):
                    cfg.setdefault(k, v)
    return cfg


# ----------------------------- helpers -----------------------------

def human_size(num):
    if not num:
        return ""
    for unit in ("B", "KB", "MB", "GB"):
        if num < 1024:
            return f"{num:.1f}{unit}"
        num /= 1024
    return f"{num:.1f}TB"


def safe_extract_info(url, opts):
    with yt_dlp.YoutubeDL(opts) as ydl:
        return ydl.extract_info(url, download=False)


# ----------------------------- routes ------------------------------

@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/formats", methods=["POST"])
def api_formats():
    data = request.get_json(force=True)
    url = (data.get("url") or "").strip()
    if not url:
        return jsonify({"error": "请输入视频链接"}), 400

    opts = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "noplaylist": True,
    }
    try:
        info = safe_extract_info(url, opts)
    except Exception as e:
        return jsonify({"error": f"解析失败: {e}"}), 400

    formats = []
    for f in info.get("formats", []):
        if f.get("vcodec") == "none" and f.get("acodec") == "none":
            continue
        res = f.get("resolution") or (
            f"{f.get('height')}p" if f.get("height") else "audio"
        )
        size = f.get("filesize") or f.get("filesize_approx")
        formats.append({
            "id": f.get("format_id"),
            "ext": f.get("ext"),
            "resolution": res,
            "fps": f.get("fps"),
            "vcodec": f.get("vcodec"),
            "acodec": f.get("acodec"),
            "abr": f.get("abr"),
            "note": f.get("format_note") or "",
            "size": human_size(size),
            "size_bytes": size or 0,
            "has_video": f.get("vcodec") not in (None, "none"),
            "has_audio": f.get("acodec") not in (None, "none"),
        })

    formats.sort(
        key=lambda x: (x["has_video"], x["size_bytes"]),
        reverse=True,
    )

    subs = list((info.get("subtitles") or {}).keys())
    auto_subs = list((info.get("automatic_captions") or {}).keys())

    return jsonify({
        "id": info.get("id"),
        "title": info.get("title"),
        "uploader": info.get("uploader"),
        "duration": info.get("duration"),
        "thumbnail": info.get("thumbnail"),
        "formats": formats,
        "subtitles": subs,
        "auto_subtitles": auto_subs,
    })


def _progress_hook(task_id):
    def hook(d):
        with TASKS_LOCK:
            task = TASKS.get(task_id)
            if task is None:
                return
            if task.get("pause") and d.get("status") == "downloading":
                task["status"] = "pausing"
                raise yt_dlp.utils.DownloadCancelled()
            status = d.get("status")
            if status == "downloading":
                downloaded = d.get("downloaded_bytes") or 0
                total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
                task["status"] = "downloading"
                task["downloaded"] = downloaded
                task["total"] = total
                task["percent"] = (downloaded / total * 100) if total else 0
                task["speed"] = d.get("speed")
                task["eta"] = d.get("eta")
                task["filename"] = os.path.basename(d.get("filename") or "")
            elif status == "finished":
                task["status"] = "processing"
                task["percent"] = 100
                task["filename"] = os.path.basename(d.get("filename") or "")
    return hook


def _ffmpeg_path():
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        import shutil
        return shutil.which("ffmpeg")


def _resolve_output_path(path):
    """prepare_filename may report the pre-merge extension; find the real file."""
    p = Path(path)
    if p.exists():
        return str(p)
    for ext in (".mp4", ".mkv", ".webm", ".m4a", ".mp3", ".opus"):
        cand = p.with_suffix(ext)
        if cand.exists():
            return str(cand)
    return str(p)


def _run_download(task_id, url, fmt, out_dir, merge_audio, gen_srt=False):
    def work():
        try:
            best = (
                "bestvideo*[vcodec^=avc1]+bestaudio[acodec^=mp4a]"
                "/bestvideo*+bestaudio/best"
            )
            if fmt in ("", "best", "bestvideo+bestaudio"):
                selector = best
            elif merge_audio:
                selector = f"{fmt}+bestaudio/{fmt}/{best}"
            else:
                selector = f"{fmt}/{best}"

            opts = {
                "format": selector,
                "outtmpl": str(Path(out_dir) / "%(title)s.%(ext)s"),
                "progress_hooks": [_progress_hook(task_id)],
                "noplaylist": True,
                "quiet": True,
                "no_warnings": True,
                "merge_output_format": "mp4",
            }
            ffmpeg = _ffmpeg_path()
            if ffmpeg:
                opts["ffmpeg_location"] = ffmpeg
            with TASKS_LOCK:
                TASKS[task_id]["status"] = "starting"
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(url, download=True)
                path = _resolve_output_path(ydl.prepare_filename(info))

            if gen_srt:
                with TASKS_LOCK:
                    TASKS[task_id]["status"] = "subtitles"
                    TASKS[task_id]["message"] = "正在生成双语字幕…"
                try:
                    srt = generate_bilingual_srt(url, path, lang="en")
                    with TASKS_LOCK:
                        TASKS[task_id]["srt"] = str(srt) if srt else ""
                except Exception as e:
                    with TASKS_LOCK:
                        TASKS[task_id]["srt_error"] = str(e)

            with TASKS_LOCK:
                task = TASKS[task_id]
                task["status"] = "finished"
                task["percent"] = 100
                task["path"] = path
                task["message"] = "下载完成"
        except yt_dlp.utils.DownloadCancelled:
            with TASKS_LOCK:
                task = TASKS.get(task_id)
                if task is not None:
                    task["status"] = "paused"
                    task["pause"] = False
                    task["message"] = "已暂停"
        except Exception as e:
            with TASKS_LOCK:
                task = TASKS.get(task_id)
                if task is not None:
                    task["status"] = "error"
                    task["error"] = str(e)
    threading.Thread(target=work, daemon=True).start()


@app.route("/api/download", methods=["POST"])
def api_download():
    data = request.get_json(force=True)
    url = (data.get("url") or "").strip()
    fmt = (data.get("format") or "best").strip()
    out_dir = (data.get("save_dir") or "").strip() or str(_default_download_dir())
    merge_audio = bool(data.get("merge_audio", True))
    gen_srt = bool(data.get("gen_srt", False))

    if not url:
        return jsonify({"error": "缺少链接"}), 400

    try:
        Path(out_dir).mkdir(parents=True, exist_ok=True)
    except Exception as e:
        return jsonify({"error": f"无法创建保存目录: {e}"}), 400

    task_id = uuid.uuid4().hex
    with TASKS_LOCK:
        TASKS[task_id] = {
            "status": "queued",
            "percent": 0,
            "downloaded": 0,
            "total": 0,
            "speed": None,
            "eta": None,
            "filename": "",
            "message": "",
            "error": "",
            "srt": "",
            "srt_error": "",
            "pause": False,
            "url": url,
            "fmt": fmt,
            "out_dir": out_dir,
            "merge_audio": merge_audio,
            "gen_srt": gen_srt,
        }
    _run_download(task_id, url, fmt, out_dir, merge_audio, gen_srt)
    return jsonify({"task_id": task_id})


@app.route("/api/pause/<task_id>", methods=["POST"])
def api_pause(task_id):
    with TASKS_LOCK:
        task = TASKS.get(task_id)
        if task is None:
            return jsonify({"error": "任务不存在"}), 404
        if task["status"] not in ("queued", "starting", "downloading"):
            return jsonify({"error": "当前状态无法暂停"}), 400
        task["pause"] = True
        task["status"] = "pausing"
    return jsonify({"ok": True})


@app.route("/api/resume/<task_id>", methods=["POST"])
def api_resume(task_id):
    with TASKS_LOCK:
        task = TASKS.get(task_id)
        if task is None:
            return jsonify({"error": "任务不存在"}), 404
        if task["status"] != "paused":
            return jsonify({"error": "任务未处于暂停状态"}), 400
        task["pause"] = False
        task["status"] = "queued"
        params = (
            task["url"],
            task["fmt"],
            task["out_dir"],
            task["merge_audio"],
            task["gen_srt"],
        )
    _run_download(task_id, *params)
    return jsonify({"ok": True})


@app.route("/api/cache", methods=["POST"])
def api_cache():
    """Download video+audio and mux to one file for stutter-free online playback."""
    data = request.get_json(force=True)
    url = (data.get("url") or "").strip()
    if not url:
        return jsonify({"error": "缺少链接"}), 400

    with TASKS_LOCK:
        tid = PLAY_CACHE.get(url)
        task = TASKS.get(tid) if tid else None
        if task and task.get("status") == "finished" and task.get("path") and Path(task["path"]).exists():
            return jsonify({"task_id": tid, "ready": True})

    out_dir = str(_cache_dir())
    task_id = uuid.uuid4().hex
    with TASKS_LOCK:
        TASKS[task_id] = {
            "status": "queued",
            "percent": 0,
            "downloaded": 0,
            "total": 0,
            "speed": None,
            "eta": None,
            "filename": "",
            "message": "",
            "error": "",
            "srt": "",
            "srt_error": "",
            "pause": False,
            "cache": True,
            "url": url,
            "fmt": "best",
            "out_dir": out_dir,
            "merge_audio": True,
            "gen_srt": False,
        }
        PLAY_CACHE[url] = task_id
    _run_download(task_id, url, "best", out_dir, True, False)
    return jsonify({"task_id": task_id, "ready": False})


@app.route("/api/local/<task_id>")
def api_local(task_id):
    with TASKS_LOCK:
        task = TASKS.get(task_id)
    path = task.get("path") if task else None
    if not path or not Path(path).exists():
        return "文件不存在或已被移动", 404
    return send_file(path, conditional=True)


@app.route("/api/progress/<task_id>")
def api_progress(task_id):
    def gen():
        last = None
        while True:
            with TASKS_LOCK:
                task = TASKS.get(task_id)
                snapshot = dict(task) if task else None
            if snapshot is None:
                yield "event: error\ndata: {\"error\":\"任务不存在\"}\n\n"
                return
            payload = dict(snapshot)
            if payload != last:
                import json
                yield "data: " + json.dumps(payload) + "\n\n"
                last = payload
            if snapshot["status"] in ("finished", "error"):
                return
            import time
            time.sleep(0.4)
    return Response(gen(), mimetype="text/event-stream")


# ----------------------------- streaming ---------------------------

STREAM_CACHE = {}
STREAM_LOCK = threading.Lock()

STREAM_FORMATS = {
    "video": (
        "bestvideo[protocol^=https][vcodec^=avc1]"
        "/bestvideo[protocol^=https][ext=mp4]"
        "/bestvideo[protocol^=https][height<=720]"
        "/bestvideo[protocol^=https]"
    ),
    "audio": "bestaudio[protocol^=https][ext=m4a]/bestaudio[protocol^=https]",
}


def _resolve_stream(src, kind, force=False):
    key = (src, kind)
    now = time.time()
    with STREAM_LOCK:
        entry = STREAM_CACHE.get(key)
        if entry and not force and entry["expires"] > now:
            return entry

    opts = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "noplaylist": True,
        "format": STREAM_FORMATS.get(kind, STREAM_FORMATS["video"]),
    }
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(src, download=False)

    entry = {
        "url": info.get("url"),
        "headers": info.get("http_headers") or {},
        "ext": (info.get("ext") or "").lower(),
        "expires": now + 3600,
    }
    with STREAM_LOCK:
        STREAM_CACHE[key] = entry
    return entry


@app.route("/api/stream")
def api_stream():
    src = (request.args.get("url") or "").strip()
    kind = request.args.get("kind") or "video"
    if not src:
        return "missing url", 400
    if kind not in STREAM_FORMATS:
        kind = "video"

    upstream = None
    entry = None
    for attempt in range(2):
        try:
            entry = _resolve_stream(src, kind, force=attempt > 0)
        except Exception as e:
            return f"resolve failed: {e}", 502

        headers = dict(entry["headers"])
        rng = request.headers.get("Range")
        if rng:
            headers["Range"] = rng
        req = urllib.request.Request(entry["url"], headers=headers)
        try:
            upstream = urllib.request.urlopen(req, timeout=30)
            break
        except urllib.error.HTTPError as e:
            if e.code in (401, 403) and attempt == 0:
                continue
            return f"upstream error {e.code}", e.code
        except Exception as e:
            return f"upstream error: {e}", 502

    ext = (entry.get("ext") or "").lower()
    if kind == "video":
        ctype = "video/webm" if ext == "webm" else "video/mp4"
    else:
        ctype = "audio/webm" if ext == "webm" else "audio/mp4"

    resp_headers = {"Content-Type": ctype, "Accept-Ranges": "bytes"}
    for h in ("Content-Length", "Content-Range"):
        v = upstream.headers.get(h)
        if v:
            resp_headers[h] = v

    def generate():
        try:
            while True:
                chunk = upstream.read(65536)
                if not chunk:
                    break
                yield chunk
        except Exception:
            pass
        finally:
            upstream.close()

    return Response(
        stream_with_context(generate()),
        status=upstream.status,
        headers=resp_headers,
    )


# ----------------------------- subtitles ---------------------------

TIME_RE = re.compile(
    r"(\d+):(\d{2}):(\d{2})[.,](\d{3})\s*-->\s*(\d+):(\d{2}):(\d{2})[.,](\d{3})"
)
TAG_RE = re.compile(r"<[^>]+>")


def _to_seconds(h, m, s, ms):
    return int(h) * 3600 + int(m) * 60 + int(s) + int(ms) / 1000.0


def _norm_text(s):
    return " ".join(s.split())


def _word_overlap(a, b):
    """Longest k where the last k words of a equal the first k words of b."""
    max_k = min(len(a), len(b))
    for k in range(max_k, 0, -1):
        if a[-k:] == b[:k]:
            return k
    return 0


def dedupe_cues(cues):
    """Collapse YouTube rolling auto-caption repeats into clean lines."""
    out = []
    for c in cues:
        text = _norm_text(c["text"])
        if not text:
            continue
        if out:
            prev = out[-1]
            pt = prev["text"]
            if text == pt or text in pt:
                prev["end"] = max(prev["end"], c["end"])
                continue
            if pt in text:
                prev["text"] = text
                prev["end"] = max(prev["end"], c["end"])
                continue
            k = _word_overlap(pt.split(), text.split())
            if k >= 1:
                trimmed = " ".join(text.split()[k:]).strip()
                if not trimmed:
                    prev["end"] = max(prev["end"], c["end"])
                    continue
                text = trimmed
        out.append({"start": c["start"], "end": c["end"], "text": text})
    return out


def parse_vtt(path):
    text = Path(path).read_text(encoding="utf-8", errors="ignore")
    lines = text.splitlines()
    cues = []
    i = 0
    while i < len(lines):
        m = TIME_RE.search(lines[i])
        if m:
            start = _to_seconds(*m.group(1, 2, 3, 4))
            end = _to_seconds(*m.group(5, 6, 7, 8))
            i += 1
            buf = []
            while i < len(lines) and lines[i].strip() != "":
                buf.append(lines[i])
                i += 1
            content = TAG_RE.sub("", " ".join(buf))
            content = (
                content.replace("&nbsp;", " ")
                .replace("&amp;", "&")
                .replace("&gt;", ">")
                .replace("&lt;", "<")
                .strip()
            )
            if content:
                cues.append({"start": start, "end": end, "text": content})
        else:
            i += 1

    return merge_sentences(dedupe_cues(cues))


_SENT_RE = re.compile(r'(?<=[.!?\u2026])\s+')


def _split_sentences(text):
    return [p.strip() for p in _SENT_RE.split(text) if p.strip()]


def _emit_block(lines, start, end):
    text = " ".join(lines).strip()
    if not text:
        return []
    sentences = _split_sentences(text) or [text]
    total = sum(len(s) for s in sentences) or 1
    span = max(end - start, 0.0)
    out = []
    t = start
    for s in sentences:
        dur = span * len(s) / total
        out.append({"start": round(t, 3), "end": round(t + dur, 3), "text": s})
        t += dur
    return out


def merge_sentences(cues, max_len=260):
    """Merge rolling fragments into whole sentences (split only at punctuation)."""
    out = []
    buf = []
    start = end = None
    for c in cues:
        t = c["text"].strip()
        if not t:
            continue
        if buf and (t.startswith(">>") or len(" ".join(buf)) + len(t) + 1 > max_len):
            out.extend(_emit_block(buf, start, end))
            buf, start, end = [], None, None
        if start is None:
            start = c["start"]
        end = c["end"]
        buf.append(t)
        if " ".join(buf).rstrip().endswith((".", "!", "?", "\u2026")):
            out.extend(_emit_block(buf, start, end))
            buf, start, end = [], None, None
    if buf:
        out.extend(_emit_block(buf, start, end))
    return out


_TRANSLATION_CACHE = {}
_OPUS_LOCK = threading.Lock()
_OPUS = None
_OPUS_FAILED = False

# ------------------------ offline translation ---------------------------
# OPUS-MT eng->zho (Helsinki-NLP/opus-mt-en-zh, Apache-2.0, ~76 MB int8) in a
# CTranslate2 build. Looked up in this order:
#   1. `models/` next to the exe / project (lets users drop in their own copy)
#   2. a persistent cache extracted from the bundled copy (one-time, first run)
#   3. the bundle itself (`sys._MEIPASS/models`, extracted by PyInstaller)
# Bundling it keeps the exe self-contained without paying the extraction cost on
# every launch: the cache under %LOCALAPPDATA% survives restarts.

_OPUS_DIR = "opus-mt-en-zh-ct2"
_OPUS_FILES = (
    "model.bin",
    "config.json",
    "shared_vocabulary.json",
    "source.spm",
    "target.spm",
)
# OPUS-MT wants an initial target-language token on the source side.
# ">>cmn_Hans<<" is Simplified Chinese (Mandarin).
_OPUS_LANG = ">>cmn_Hans<<"

_SPECIAL_TOKENS = ("</s>", "<s>", "<pad>")


def _has_model_files(d, files):
    """True when every required model file exists under ``d``."""
    try:
        return all((d / n).exists() for n in files)
    except OSError:
        return False


def _model_cache_root():
    """Folder for the model extracted from the bundle.

    Prefers the ``models`` folder next to the exe / project so an extracted
    copy lives with the program (e.g. on the E: drive) instead of the system
    drive; falls back to a per-user cache when that location is not writable.
    """
    local = BASE_DIR / "models"
    try:
        local.mkdir(parents=True, exist_ok=True)
        probe = local / ".write-test"
        probe.write_text("", encoding="utf-8")
        probe.unlink()
        return local
    except OSError:
        pass
    base = os.environ.get("LOCALAPPDATA") or os.environ.get("XDG_CACHE_HOME")
    root = Path(base) if base else Path.home() / ".cache"
    return root / "VideoTranslateTool" / "models"


def _extract_bundled_model(name, files):
    """Copy the bundled model into the persistent cache once, then reuse it.

    Re-extracts only when the bundled files change (detected via total size),
    so a fresh build with an updated model still replaces a stale cache.
    Returns the cache path, or None when there is no bundled copy.
    """
    if not hasattr(sys, "_MEIPASS"):
        return None
    src = Path(sys._MEIPASS) / "models" / name
    if not _has_model_files(src, files):
        return None
    dest = _model_cache_root() / name
    stamp = dest / ".bundle-size"
    try:
        size = sum((src / n).stat().st_size for n in files)
    except OSError:
        return None
    try:
        if stamp.exists() and stamp.read_text(encoding="utf-8").strip() == str(size) and _has_model_files(dest, files):
            return dest
        tmp = dest.with_name(name + ".tmp")
        shutil.rmtree(tmp, ignore_errors=True)
        shutil.copytree(src, tmp)
        (tmp / ".bundle-size").write_text(str(size), encoding="utf-8")
        shutil.rmtree(dest, ignore_errors=True)
        tmp.replace(dest)
        return dest
    except OSError:
        return None


def _ct2_model_dir(name, files):
    """Locate a model folder, extracting the bundled copy on first use."""
    external = [BASE_DIR / "models" / name, Path.cwd() / "models" / name]
    for d in external:
        if _has_model_files(d, files):
            return d
    if hasattr(sys, "_MEIPASS"):
        d = _extract_bundled_model(name, files)
        if d is not None and _has_model_files(d, files):
            return d
        d = Path(sys._MEIPASS) / "models" / name
        if _has_model_files(d, files):
            return d
    return None


def _ensure_opus():
    """Lazily load the OPUS-MT en->zh model and its SentencePiece tokenizers."""
    global _OPUS, _OPUS_FAILED
    if _OPUS is not None:
        return _OPUS
    if _OPUS_FAILED:
        return None
    with _OPUS_LOCK:
        if _OPUS is not None:
            return _OPUS
        if _OPUS_FAILED:
            return None
        try:
            import ctranslate2
            import sentencepiece as spm

            d = _ct2_model_dir(_OPUS_DIR, _OPUS_FILES)
            if d is None:
                raise FileNotFoundError("OPUS-MT model folder not found")
            _OPUS = (
                ctranslate2.Translator(str(d), device="cpu", compute_type="int8"),
                spm.SentencePieceProcessor(model_file=str(d / "source.spm")),
                spm.SentencePieceProcessor(model_file=str(d / "target.spm")),
            )
        except Exception:
            _OPUS_FAILED = True
            return None
    return _OPUS


def _opus_sentences(text):
    """Split a cue into sentences: the model repeats itself on multi-sentence input."""
    text = " ".join((text or "").split())
    if not text:
        return []
    parts = [p.strip() for p in _SENT_RE.split(text) if p.strip()]
    return parts or [text]


def _opus_translate_batch(lines):
    """Translate English lines to Chinese with the offline OPUS-MT model."""
    obj = _ensure_opus()
    if obj is None:
        raise RuntimeError("OPUS-MT model unavailable")
    translator, source_spm, target_spm = obj

    jobs, owners = [], []
    for i, line in enumerate(lines):
        for sentence in _opus_sentences(line):
            jobs.append([_OPUS_LANG] + source_spm.encode(sentence, out_type=str))
            owners.append(i)
    if not jobs:
        return ["" for _ in lines]

    results = translator.translate_batch(
        jobs,
        beam_size=4,
        batch_type="examples",
        max_batch_size=16,
    )
    merged = [[] for _ in lines]
    for owner, result in zip(owners, results):
        tokens = [t for t in result.hypotheses[0] if t not in _SPECIAL_TOKENS]
        merged[owner].append(target_spm.decode(tokens).strip())
    return ["".join(parts).strip() for parts in merged]


def translate_lines(lines, target="zh-CN"):
    """Offline translation with OPUS-MT; unresolved lines stay empty.

    An empty entry means no translation is available, so the UI just shows the
    English source for that line.
    """
    todo = [l for l in lines if l not in _TRANSLATION_CACHE]
    if not todo:
        return [_TRANSLATION_CACHE.get(l, "") for l in lines]

    try:
        for line, tr in zip(todo, _opus_translate_batch(todo)):
            tr = (tr or "").strip()
            if tr:
                _TRANSLATION_CACHE[line] = tr
    except Exception:
        pass

    return [_TRANSLATION_CACHE.get(l, "") for l in lines]


def align_by_time(src_cues, tr_cues):
    """Align translated cues onto source cues by maximum time overlap."""
    out = []
    j = 0
    n = len(tr_cues)
    for c in src_cues:
        while j < n and tr_cues[j]["end"] <= c["start"]:
            j += 1
        best, best_ov = "", 0.0
        k = j
        while k < n and tr_cues[k]["start"] < c["end"]:
            ov = min(c["end"], tr_cues[k]["end"]) - max(c["start"], tr_cues[k]["start"])
            if ov > best_ov:
                best_ov, best = ov, tr_cues[k]["text"]
            k += 1
        out.append(best or (tr_cues[j]["text"] if j < n else ""))
    return out


def collect_subtitles(url, lang="en", translate=False):
    """Download subtitles and return (info, cues, zh_texts, src_name).

    `cues` is a list of {"start", "end", "text"}. `zh_texts` is a parallel list
    of Chinese strings taken from the video's own zh captions, or None.
    """
    target = "zh-Hans"
    wanted = [lang]
    if not lang.startswith("en"):
        wanted.append("en")
    if translate and not lang.startswith("zh"):
        wanted.append(target)
    seen = set()
    wanted = [x for x in wanted if not (x in seen or seen.add(x))]

    with tempfile.TemporaryDirectory() as tmp:
        opts = {
            "skip_download": True,
            "writesubtitles": True,
            "writeautomaticsub": True,
            "subtitleslangs": wanted,
            "subtitlesformat": "vtt",
            "outtmpl": str(Path(tmp) / "%(id)s.%(ext)s"),
            "quiet": True,
            "no_warnings": True,
            "noplaylist": True,
            "ignoreerrors": True,
        }

        info = {}
        vtts = []

        def _has(code):
            return any(p.name.endswith("." + code + ".vtt") for p in vtts)

        for attempt in range(2):
            try:
                with yt_dlp.YoutubeDL(opts) as ydl:
                    info = ydl.extract_info(url, download=True) or {}
            except Exception as e:
                if attempt < 1:
                    time.sleep(2)
                    continue
                raise RuntimeError(f"获取字幕失败: {e}")
            vtts = list(Path(tmp).glob("*.vtt"))
            if vtts and (not translate or lang.startswith("zh") or _has(target)):
                break
            time.sleep(2)

        if not vtts:
            raise RuntimeError("该视频没有可用字幕（可能被限流，请稍后重试）")

        def find(code):
            for p in vtts:
                if p.name.endswith("." + code + ".vtt"):
                    return p
            return None

        src_vtt = find(lang) or find("en") or vtts[0]
        cues = parse_vtt(src_vtt)

        zh_texts = None
        if translate and not lang.startswith("zh"):
            zh_vtt = find(target)
            if zh_vtt and zh_vtt != src_vtt:
                tr_cues = parse_vtt(zh_vtt)
                if tr_cues:
                    zh_texts = align_by_time(cues, tr_cues)

    return info, cues, zh_texts, src_vtt.name


def _ensure_zh(cues, zh_texts):
    """Return a Chinese line per cue, translating when captions lack zh."""
    if zh_texts and any(z for z in zh_texts):
        return [
            (zh_texts[i] if i < len(zh_texts) else "") for i in range(len(cues))
        ]
    return translate_lines([c["text"] for c in cues])


def _srt_time(t):
    if t < 0:
        t = 0
    ms = int(round(t * 1000))
    h, ms = divmod(ms, 3600000)
    m, ms = divmod(ms, 60000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def build_srt(cues, zh_texts=None):
    blocks = []
    for i, c in enumerate(cues, 1):
        zh = (zh_texts[i - 1] or "").strip() if zh_texts and i - 1 < len(zh_texts) else ""
        lines = [str(i), f"{_srt_time(c['start'])} --> {_srt_time(c['end'])}", c["text"]]
        if zh:
            lines.append(zh)
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks) + "\n"


def generate_bilingual_srt(url, video_path, lang="en"):
    _, cues, zh_texts, _ = collect_subtitles(url, lang=lang, translate=True)
    if not cues:
        return None
    zh = _ensure_zh(cues, zh_texts)
    out = Path(video_path).with_suffix(".zh-en.srt")
    out.write_text(build_srt(cues, zh), encoding="utf-8")
    return out


_HTML_HEAD = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>__TITLE__ · 双语对照</title>
<style>
  :root { --line: #e5e7eb; --muted: #6b7280; --en: #1b1f27; --zh: #c2410c; }
  * { box-sizing: border-box; }
  body {
    margin: 0; padding: 40px 32px 64px; background: #fff; color: var(--en);
    font-family: "Segoe UI", "Microsoft YaHei", system-ui, sans-serif; line-height: 1.6;
  }
  .wrap { max-width: 900px; margin: 0 auto; }
  h1 { font-size: 20px; margin: 0 0 4px; }
  .meta { color: var(--muted); font-size: 13px; margin-bottom: 24px; }
  table { width: 100%; border-collapse: collapse; }
  th, td { text-align: left; vertical-align: top; padding: 7px 10px; border-bottom: 1px solid var(--line); }
  th { font-size: 12px; text-transform: uppercase; letter-spacing: .06em; color: var(--muted); border-bottom: 2px solid var(--line); }
  td.t { width: 64px; color: var(--muted); font-size: 12px; font-variant-numeric: tabular-nums; white-space: nowrap; }
  td.en { width: 48%; color: var(--en); }
  td.zh { color: var(--zh); }
  tr { break-inside: avoid; page-break-inside: avoid; }
  @media print {
    body { padding: 0; }
    thead { display: table-header-group; }
  }
</style>
</head>
<body>
<div class="wrap">
<h1>__TITLE__</h1>
<div class="meta">__COUNT__ 句 · 英文原文 / 中文翻译</div>
<table>
<thead><tr><th>时间</th><th>English</th><th>中文</th></tr></thead>
<tbody>
__ROWS__
</tbody>
</table>
</div>
</body>
</html>
"""


def build_bilingual_html(title, lines):
    rows = []
    for ln in lines:
        rows.append(
            "<tr>"
            f'<td class="t">{_srt_time(ln["start"]).split(",")[0]}</td>'
            f'<td class="en">{html.escape(ln["en"])}</td>'
            f'<td class="zh">{html.escape(ln.get("zh") or "")}</td>'
            "</tr>"
        )
    return (
        _HTML_HEAD.replace("__TITLE__", html.escape(title or "双语字幕"))
        .replace("__COUNT__", str(len(lines)))
        .replace("__ROWS__", "\n".join(rows))
    )


@app.route("/api/subtitles", methods=["POST"])
def api_subtitles():
    data = request.get_json(force=True)
    url = (data.get("url") or "").strip()
    lang = "en"
    translate = bool(data.get("translate", True))
    if not url:
        return jsonify({"error": "缺少链接"}), 400

    try:
        info, cues, zh_texts, src_name = collect_subtitles(url, lang, translate)
    except Exception as e:
        status = 404 if "没有可用字幕" in str(e) else 400
        return jsonify({"error": str(e)}), status

    if not cues:
        return jsonify({"error": "字幕内容为空"}), 404

    result = []
    for i, c in enumerate(cues):
        result.append({
            "start": c["start"],
            "end": c["end"],
            "en": c["text"],
            "zh": (zh_texts[i] if zh_texts else ""),
        })

    return jsonify({
        "title": info.get("title"),
        "file": src_name,
        "lines": result,
        "translated": bool(zh_texts),
    })


@app.route("/api/export_html")
def api_export_html():
    url = (request.args.get("url") or "").strip()
    if not url:
        return "缺少链接", 400
    try:
        info, cues, zh_texts, _ = collect_subtitles(url, lang="en", translate=True)
    except Exception as e:
        return f"获取字幕失败: {e}", 400
    if not cues:
        return "字幕内容为空", 404

    zh = _ensure_zh(cues, zh_texts)
    lines = [
        {"start": c["start"], "en": c["text"], "zh": zh[i] if i < len(zh) else ""}
        for i, c in enumerate(cues)
    ]
    doc = build_bilingual_html(info.get("title"), lines)
    name = re.sub(r'[\\/:*?"<>|]+', "_", (info.get("title") or "subtitles")).strip()[:80]
    return send_file(
        io.BytesIO(doc.encode("utf-8")),
        mimetype="text/html",
        as_attachment=True,
        download_name=f"{name}.bilingual.html",
    )


@app.route("/api/translate", methods=["POST"])
def api_translate():
    data = request.get_json(force=True)
    texts = data.get("texts") or []
    if not isinstance(texts, list) or not texts:
        return jsonify({"translations": []})
    texts = [str(t) for t in texts]
    translations = translate_lines(texts)
    if any(t.strip() for t in texts) and not any(translations):
        return jsonify({"error": "翻译服务暂时被限流，请稍后重试"}), 429
    return jsonify({"translations": translations})


def _background_image_path():
    """Config override wins; otherwise use the copy bundled in static/."""
    p = (_load_config().get("background_image") or "").strip()
    if p:
        cand = Path(os.path.expandvars(os.path.expanduser(p)))
        if not cand.is_absolute():
            cand = BASE_DIR / cand
        if cand.exists():
            return cand
    if app.static_folder:
        cand = Path(app.static_folder) / "0xzcbo0tzlg.jpg"
        if cand.exists():
            return cand
    return Path.home() / "Pictures" / "0xzcbo0tzlg.jpg"


@app.route("/api/background")
def api_background():
    path = _background_image_path()
    if not path.exists():
        return "", 404
    return send_file(path)


_PICK_REQUESTS = queue.Queue()


@app.route("/api/pickdir", methods=["POST"])
def api_pickdir():
    done = threading.Event()
    req = {"done": done, "dir": "", "error": ""}
    _PICK_REQUESTS.put(req)
    if not done.wait(timeout=3600):
        return jsonify({"dir": "", "error": "等待目录选择超时"})
    return jsonify({"dir": req["dir"], "error": req["error"]})


def _picker_loop():
    """Tkinter dialogs must run in the process main thread."""
    while True:
        req = _PICK_REQUESTS.get()
        try:
            import tkinter as tk
            from tkinter import filedialog

            root = tk.Tk()
            root.withdraw()
            root.attributes("-topmost", True)
            folder = filedialog.askdirectory(title="选择保存目录")
            root.destroy()
            req["dir"] = folder or ""
        except Exception as e:
            req["error"] = str(e)
        finally:
            req["done"].set()


if __name__ == "__main__":
    threading.Thread(target=_ensure_opus, daemon=True).start()
    for _f in _cache_dir().glob("*"):
        try:
            _f.unlink()
        except OSError:
            pass
    url = "http://127.0.0.1:5000"
    threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    threading.Thread(
        target=app.run,
        kwargs={"host": "127.0.0.1", "port": 5000, "debug": False, "threaded": True},
        daemon=True,
    ).start()
    _picker_loop()
