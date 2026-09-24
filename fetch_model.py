"""Download the offline English->Chinese translation model (CTranslate2 int8).

Usage:
    python fetch_model.py

Downloads OPUS-MT eng->zho (Helsinki-NLP/opus-mt-en-zh, Apache-2.0, ~80 MB)
into models/opus-mt-en-zh-ct2/ next to the exe (or the project root).
Supports resume: run it again if the download is interrupted.
Set HF_ENDPOINT to use another mirror.
"""

import os
import sys
import urllib.request
from pathlib import Path

ENDPOINT = os.environ.get("HF_ENDPOINT", "https://hf-mirror.com")
ROOT = Path(__file__).resolve().parent
DEST = ROOT / "models" / "opus-mt-en-zh-ct2"

# OPUS-MT eng->zho (Helsinki-NLP/opus-mt-en-zh, Apache-2.0) converted to
# CTranslate2 int8. Needs shared_vocabulary.json to load and the two
# SentencePiece models to tokenize.
FILES = [
    ("jiangzhuo9357/opus-mt-en-zh-ct2/resolve/main/model.bin", "model.bin", 76),
    ("jiangzhuo9357/opus-mt-en-zh-ct2/resolve/main/config.json", "config.json", 0),
    ("jiangzhuo9357/opus-mt-en-zh-ct2/resolve/main/shared_vocabulary.json", "shared_vocabulary.json", 1),
    ("jiangzhuo9357/opus-mt-en-zh-ct2/resolve/main/source.spm", "source.spm", 0.75),
    ("jiangzhuo9357/opus-mt-en-zh-ct2/resolve/main/target.spm", "target.spm", 0.75),
]


def complete(dest, approx_mb):
    """True when the file is already there and looks fully downloaded."""
    if not dest.exists():
        return False
    size = dest.stat().st_size
    if approx_mb <= 0:
        return size > 0
    return size >= approx_mb * 1048576 * 0.9


def fetch(url, dest, approx_mb):
    tmp = dest.with_name(dest.name + ".part")
    pos = tmp.stat().st_size if tmp.exists() else 0
    if pos and dest.exists() and dest.stat().st_size == pos:
        tmp.replace(dest)
        pos = 0
    req = urllib.request.Request(url, headers={"User-Agent": "curl/8"})
    if pos:
        req.add_header("Range", "bytes=%d-" % pos)
        print("  resuming %s at %.1f MB" % (dest.name, pos / 1048576))
    with urllib.request.urlopen(req, timeout=60) as resp:
        total = int(resp.headers.get("Content-Length") or 0) + pos
        mode = "ab" if pos else "wb"
        done = pos
        with open(tmp, mode) as out:
            while True:
                chunk = resp.read(1 << 20)
                if not chunk:
                    break
                out.write(chunk)
                done += len(chunk)
                if total:
                    pct = done * 100 // total
                    sys.stdout.write("\r  %s  %d%%  (%.1f/%.1f MB)" % (dest.name, pct, done / 1048576, total / 1048576))
                    sys.stdout.flush()
    tmp.replace(dest)
    print("\r  %s  done (%.1f MB)%s" % (dest.name, dest.stat().st_size / 1048576, " " * 30))


def main():
    DEST.mkdir(parents=True, exist_ok=True)
    print("model dir: %s" % DEST)
    print("endpoint : %s" % ENDPOINT)
    for path, name, size in FILES:
        target = DEST / name
        if complete(target, size):
            print("  %s already present, skip" % name)
            continue
        fetch("%s/%s" % (ENDPOINT, path), target, size)
    print("\nTranslation model is ready.")


if __name__ == "__main__":
    main()
