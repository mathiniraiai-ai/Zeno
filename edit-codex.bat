@echo off
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  powershell -NoProfile -ExecutionPolicy Bypass -File scripts/install_windows.ps1
  if errorlevel 1 exit /b 1
)
.venv\Scripts\python.exe scripts/launch_edit.py --agent codex %*
exit /b %errorlevel%
