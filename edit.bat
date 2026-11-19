@echo off
REM Zeno v2.0.0 -- Minimal Cache-Read Launcher for Claude Code (Windows, zero MCPs, Bash-only, effort=low)
setlocal
cd /d "%~dp0"
set "PATH=%USERPROFILE%\.local\bin;%PATH%"
set "CLAUDECODE="
set "CLAUDE_CODE_ENTRYPOINT="
set "ENABLE_CLAUDEAI_MCP_SERVERS=false"

if not exist ".venv\Scripts\python.exe" (
  echo First-time setup: installing Zeno Python 3.12 environment, FFmpeg, and GPU tools...
  powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\install_windows.ps1"
  if errorlevel 1 exit /b 1
)

where claude >nul 2>nul
if errorlevel 1 (
  echo Claude Code 'claude' is not installed. Get it from https://claude.com/claude-code, then run this again.
  if not defined ZENO_UNATTENDED if not defined CI pause
  exit /b 1
)

set "ZENO_EXTRA_ARGS=%*"
echo Starting Zeno v2.0.0 (Claude Code, zero-MCP minimal cache mode) -- drop 1 video or a multi-video queue -^> %%USERPROFILE%%\Downloads\Zeno Outs\^<VideoName^>\...
powershell -NoProfile -Command "$q = [char]34; $sys = [System.IO.File]::ReadAllText((Join-Path (Get-Location) 'CLAUDE.md'), [System.Text.Encoding]::UTF8); $extra = @(); if ($env:ZENO_EXTRA_ARGS) { $extra = [regex]::Matches($env:ZENO_EXTRA_ARGS, ($q + '([^' + $q + ']*)' + $q + '|(\S+)')) | ForEach-Object { if ($_.Groups[1].Success) { $_.Groups[1].Value } else { $_.Groups[2].Value } } }; $src = (Get-Command claude -ErrorAction Stop).Source; $exe = $src; $pre = @(); if ($src -match '\.cmd$') { $cli = Join-Path (Split-Path $src) 'node_modules\@anthropic-ai\claude-code\cli.js'; if ((Test-Path $cli) -and (Get-Command node -ErrorAction SilentlyContinue)) { $exe = 'node'; $pre = @($cli) } else { $sys = $sys -replace '\r?\n', ' ' } }; $prompt = 'Zeno v2.0.0 ready. Share 1 video or a queue of videos (footage folder, script link/file, and video name) to edit sequentially into ' + $env:USERPROFILE + '\Downloads\Zeno Outs\<VideoName>\.'; $allArgs = @($pre + @('--setting-sources', 'project,local', '--no-chrome', '--effort', 'low', '--system-prompt', $sys, '--tools', 'Bash', '--disable-slash-commands', '--strict-mcp-config') + @($extra) + @($prompt)); if (Get-Variable PSNativeCommandArgumentPassing -ErrorAction SilentlyContinue) { $PSNativeCommandArgumentPassing = 'Standard' } else { $allArgs = $allArgs | ForEach-Object { $a = [string]$_; if ($a -eq '') { $q + $q } elseif ($a -match '\s') { $a -replace ('(\\*)' + $q), ('$1$1\' + $q) -replace '(\\+)$', '$1$1' } elseif ($a -match $q) { $a -replace ('(\\*)' + $q), ('$1$1\' + $q) } else { $a } } }; & $exe @allArgs; exit $LASTEXITCODE"
exit /b %ERRORLEVEL%
