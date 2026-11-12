@echo off
REM Zeno one-click installer for Windows. Double-click this file, or run from a terminal.
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\install_windows.ps1"
set "ZENO_RC=%ERRORLEVEL%"
if not defined ZENO_UNATTENDED if not defined CLAUDECODE if not defined CI if /I not "%TERM_PROGRAM%"=="vscode" pause
exit /b %ZENO_RC%
