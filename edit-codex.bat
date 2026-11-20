@echo off
REM Zeno v2.0.0 -- Quick Launcher for OpenAI Codex (Windows)
REM Opens Codex in Zeno using AGENTS.md, ready for 1 video or a multi-video sequential queue -> %USERPROFILE%\Downloads\Zeno Outs\<VideoName>\
setlocal
cd /d "%~dp0"
set "PATH=%USERPROFILE%\.local\bin;%APPDATA%\npm;%PATH%"
set "CODEX_THREAD_ID="

if not exist ".venv\Scripts\python.exe" (
  echo First-time setup: installing Zeno Python 3.12 environment, FFmpeg, and GPU tools...
  powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\install_windows.ps1"
  if errorlevel 1 exit /b 1
)

where codex >nul 2>nul
if errorlevel 1 (
  echo Codex CLI 'codex' is not found on PATH. Install OpenAI Codex CLI and run again.
  if not defined ZENO_UNATTENDED if not defined CI pause
  exit /b 1
)

if not defined ZENO_CODEX_MODEL set "ZENO_CODEX_MODEL=gpt-6.1-sol"
echo Starting Zeno v2.0.0 (Codex: %ZENO_CODEX_MODEL%) -- drop 1 video or a multi-video queue to edit into %%USERPROFILE%%\Downloads\Zeno Outs\^<VideoName^>\...
if "%~1"=="" (
  codex --model "%ZENO_CODEX_MODEL%" -s danger-full-access -c sandbox_workspace_write.network_access=true -c "model_instructions_file=\"AGENTS.md\"" -c "model_reasoning_effort=\"low\"" -c features.web_search_request=false -c features.multi_agent=true "Zeno v2.0.0 edit session ready (follow AGENTS.md). Share 1 video or a multi-video queue (footage folder, Google Doc ?tab=... link or script path, and video name) to edit and deliver sequentially to %USERPROFILE%\Downloads\Zeno Outs\<VideoName>\."
) else (
  codex --model "%ZENO_CODEX_MODEL%" -s danger-full-access -c sandbox_workspace_write.network_access=true -c "model_instructions_file=\"AGENTS.md\"" -c "model_reasoning_effort=\"low\"" -c features.web_search_request=false -c features.multi_agent=true %*
)
exit /b %ERRORLEVEL%
