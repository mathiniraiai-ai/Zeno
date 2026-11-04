@echo off
REM Zeno updater for Windows. Double-click this file.
REM Pulls the latest Zeno, then refreshes packages. Your .env, clips, work\ and output\ are never touched.
cd /d "%~dp0"
if not exist ".git" (
  echo This copy is not connected to GitHub yet. Connecting it now...
  if not exist ".venv\Scripts\python.exe" (
    echo Run install.bat first.
    pause
    exit /b 1
  )
  ".venv\Scripts\python.exe" scripts\auto_updater.py --connect-git
  if errorlevel 1 (
    pause
    exit /b 1
  )
)
echo Getting the latest Zeno...
git pull --ff-only || (pause & exit /b 1)
call install.bat
