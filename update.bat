@echo off
REM Zeno Cloud updater for Windows. Double-click this file.
REM Downloads the latest Zeno update from Zeno Cloud (no GitHub needed). Your .env, clips, work\ and output\ are never touched.
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Run install.bat first.
  pause
  exit /b 1
)
echo Checking Zeno Cloud for the latest update...
".venv\Scripts\python.exe" scripts\auto_updater.py
if errorlevel 1 (
  pause
  exit /b 1
)
echo Done.
pause
