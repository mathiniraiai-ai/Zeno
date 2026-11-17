@echo off
REM Start a lean Zeno edit session with Codex (Windows): keeps Notion, Meta, and Zeno MCP connectors active
REM while disabling unrelated MCP servers, non-editing plugins, and built-in web search bloat.
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo First-time setup: installing Zeno Python 3.12 environment, FFmpeg, and GPU tools...
  powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\install_windows.ps1"
  if errorlevel 1 exit /b 1
)
.venv\Scripts\python.exe scripts/launch_edit.py --agent codex %*
exit /b %errorlevel%
