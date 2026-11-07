@echo off
REM Start a lean Zeno edit session (Windows): Claude Code in this folder with the claude.ai connectors and extra MCP servers off.
REM Measured: it starts the session about 7.5k tokens smaller. Double-click, then answer what /zeno asks.
cd /d "%~dp0"
where claude >nul 2>nul
if errorlevel 1 (
  echo Claude Code is not installed. Get it from https://claude.com/claude-code, then run this again.
  pause
  exit /b 1
)
set ENABLE_CLAUDEAI_MCP_SERVERS=false
echo Starting a lean Zeno edit session (new session, connectors off)...
claude --strict-mcp-config "/zeno"
