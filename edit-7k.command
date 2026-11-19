#!/bin/bash
# Zeno v2.0.0 — Strict 7.5K Cache-Read Launcher for Claude Code (macOS, zero MCP schemas: ~7,573 tokens/call)
# Note: effort is always pinned to low. Use when passing local footage + Google Doc link directly without MCPs.
cd "$(dirname "$0")" || exit 1
export PATH="$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:$PATH"
unset CLAUDECODE CLAUDE_CODE_ENTRYPOINT
export ENABLE_CLAUDEAI_MCP_SERVERS="false"

if ! command -v claude >/dev/null 2>&1; then
  echo "Claude Code ('claude') is not installed."
  [ -t 0 ] && read -r -p "Press Return to close."
  exit 1
fi

SYS_PROMPT="$(cat CLAUDE.md)"
echo "Starting Zeno v2.0.0 (Strict 7.5K/call mode) — drop 1 video or a multi-video queue -> ~/Downloads/Zeno Outs/<VideoName>/..."
exec claude --setting-sources project,local --no-chrome --effort low \
  --system-prompt "$SYS_PROMPT" --tools "Bash" --disable-slash-commands --strict-mcp-config "$@" \
  "Zeno v2.0.0 ready. Share 1 video or a queue of videos (footage folder, script link/file, and video name) to edit sequentially into ~/Downloads/Zeno Outs/<VideoName>/."
