#!/bin/bash
# Start a lean Zeno edit session (Mac): Claude Code in this folder with Notion & Meta connectors kept enabled,
# while global ~/.claude/skills/synced bloat and Chrome browser tools are stripped.
# Double-click (first time: right-click > Open), then answer what /zeno asks or queue multiple edits (up to 1M context).
cd "$(dirname "$0")" || exit 1
export PATH="$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:$PATH"
if [ ! -x .venv/bin/python ]; then
  echo "First-time setup: installing Zeno Python 3.12 environment and tools..."
  bash ./install.command || exit $?
fi
if ! command -v claude >/dev/null 2>&1; then
  echo "Claude Code is not installed. Get it from https://claude.com/claude-code, then run this again."
  read -r -p "Press Return to close."
  exit 1
fi
echo "Starting a lean Zeno edit session (Notion & Meta connectors active; global skill/Chrome bloat off)..."
exec claude --setting-sources project,local --no-chrome "$@" "/zeno"
