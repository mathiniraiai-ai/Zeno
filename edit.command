#!/bin/bash
# Zeno v2.0.0 — Minimal Cache-Read Launcher for Claude Code (macOS, zero MCPs, Bash-only, effort=low)
cd "$(dirname "$0")" || exit 1
export PATH="$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:$PATH"
unset CLAUDECODE CLAUDE_CODE_ENTRYPOINT
export ENABLE_CLAUDEAI_MCP_SERVERS="false"

if ! command -v claude >/dev/null 2>&1; then
  echo "Claude Code ('claude') is not installed. Install it from https://claude.com/claude-code and run again."
  [ -t 0 ] && read -r -p "Press Return to close."
  exit 1
fi

SYS_PROMPT="$(cat CLAUDE.md)"
echo "Starting Zeno v2.0.0 (Claude Code, zero-MCP minimal cache mode) — drop 1 video or a multi-video queue -> ~/Downloads/Zeno Outs/<VideoName>/..."
exec claude --setting-sources project,local --no-chrome --effort low \
  --system-prompt "$SYS_PROMPT" --tools "Bash" --disable-slash-commands --strict-mcp-config "$@" \
  "Zeno v2.0.0 ready. Share 1 video or a queue of videos (footage folder, script link/file, and video name) to edit sequentially into ~/Downloads/Zeno Outs/<VideoName>/."
