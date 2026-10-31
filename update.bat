@echo off
REM Zeno updater for Windows. Double-click this file.
REM Pulls the latest Zeno, then refreshes packages. Your .env, clips, work\ and output\ are never touched.
cd /d "%~dp0"
if not exist ".git" (
  echo This copy was not installed with git, so it can't update itself.
  echo Ask the Zeno owner for the download key and the one-line setup in ONBOARDING.md.
  pause
  exit /b 1
)
echo Getting the latest Zeno...
git pull --ff-only || (pause & exit /b 1)
call install.bat
