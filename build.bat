@echo off
chcp 65001 >nul
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo [1/3] Creating virtual environment...
  python -m venv .venv
  ".venv\Scripts\python.exe" -m pip install -r requirements.txt
)
".venv\Scripts\python.exe" -m pip show pyinstaller >nul 2>&1
if errorlevel 1 (
  echo [2/3] Installing PyInstaller...
  ".venv\Scripts\python.exe" -m pip install pyinstaller
)
echo [3/3] Building exe...
".venv\Scripts\python.exe" -m PyInstaller --noconfirm yt_downloader.spec
if errorlevel 1 (
  echo Build failed.
  pause
  exit /b 1
)
echo.
echo Done: dist\VideoTranslateTool.exe
pause
