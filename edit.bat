@echo off
REM Start a lean Zeno edit session (Windows): Claude Code in this folder with Notion and Meta connectors kept enabled,
REM while global ~/.claude/skills/synced bloat and Chrome browser tools are stripped.
REM Double-click, then answer what /zeno asks or queue multiple edits (up to 1M context).
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo First-time setup: installing Zeno Python 3.12 environment, FFmpeg, and GPU tools...
  powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\install_windows.ps1"
  if errorlevel 1 exit /b 1
)
where claude >nul 2>nul
if errorlevel 1 (
  echo Claude Code is not installed. Get it from https://claude.com/claude-code, then run this again.
  pause
  exit /b 1
)
echo Starting a lean Zeno edit session (Notion and Meta connectors active; global skill/Chrome bloat off)...
claude --setting-sources project,local --no-chrome %* "/zeno"
