#!/bin/bash
# Start a lean Zeno edit session with Codex (Mac): keeps Notion, Meta, and Zeno MCP connectors active
# while disabling unrelated MCP servers, non-editing plugins, and built-in web search bloat.
cd "$(dirname "$0")" || exit 1
export PATH="$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:$PATH"
if [ ! -x .venv/bin/python ]; then
  echo "First-time setup: installing Zeno Python 3.12 environment and tools..."
  bash ./install.command || exit $?
fi
exec .venv/bin/python scripts/launch_edit.py --agent codex "$@"
