# -*- mode: python ; coding: utf-8 -*-
import os

from PyInstaller.utils.hooks import collect_all

# config.json is an optional per-machine file (gitignored), so it is NOT
# bundled. The program runs with zero configuration, or reads a local
# config.json placed next to the exe / in the working directory at runtime.
datas = [
    ("templates", "templates"),
    ("static", "static"),
]

# Bundle the offline translation model (when fetched) so the exe is
# self-contained. app.py extracts it to %LOCALAPPDATA% on first run, not on
# every launch. build.bat runs from the project root, so SPECPATH is reliable.
_models = os.path.join(SPECPATH, "models")
if os.path.isdir(_models):
    datas.append((_models, "models"))

binaries = []
hiddenimports = ["tkinter"]

for pkg in (
    "yt_dlp",
    "curl_cffi",
    "imageio_ffmpeg",
    "ctranslate2",
    "sentencepiece",
):
    d, b, h = collect_all(pkg)
    datas += d
    binaries += b
    hiddenimports += h

a = Analysis(
    ["app.py"],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=[
        "torch",
        "transformers",
        "stanza",
        "spacy",
        "spacy_loggers",
        "spacy_legacy",
        "sacremoses",
        "pyinstaller",
    ],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="VideoTranslateTool",
    debug=False,
    strip=False,
    upx=False,
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
)
