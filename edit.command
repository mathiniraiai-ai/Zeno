#!/bin/bash
# Start a lean Zeno edit session (Mac): Claude Code in this folder with the claude.ai connectors and extra MCP servers off.
# Measured: it starts the session about 7.5k tokens smaller. Double-click (first time: right-click > Open), then answer what /zeno asks.
cd "$(dirname "$0")"
if ! command -v claude >/dev/null 2>&1; then
  echo "Claude Code is not installed. Get it from https://claude.com/claude-code, then run this again."
  read -r -p "Press Return to close."
  exit 1
fi
export ENABLE_CLAUDEAI_MCP_SERVERS=false
echo "Starting a lean Zeno edit session (new session, connectors off)..."
exec claude --strict-mcp-config "/zeno"
