@echo off
chcp 65001 >nul
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo [1/4] Creating virtual environment...
  python -m venv .venv
  ".venv\Scripts\python.exe" -m pip install -r requirements.txt
)
".venv\Scripts\python.exe" -m pip show pyinstaller >nul 2>&1
if errorlevel 1 (
  echo [2/4] Installing PyInstaller...
  ".venv\Scripts\python.exe" -m pip install pyinstaller
)
echo [3/4] Ensuring offline translation model is present...
".venv\Scripts\python.exe" fetch_model.py
if errorlevel 1 (
  echo.
  echo [WARN] Model download failed. The exe will not include Chinese offline translation.
  echo        You can re-run build.bat later once the network is available.
)
echo [4/4] Building exe...
".venv\Scripts\python.exe" -m PyInstaller --noconfirm yt_downloader.spec
if errorlevel 1 (
  echo Build failed.
  pause
  exit /b 1
)
echo.
echo Done: dist\VideoTranslateTool.exe
pause
