#!/bin/bash
# Zeno v2.0.0 — Quick Launcher for OpenAI Codex (macOS)
# Opens Codex in Zeno using AGENTS.md, ready for 1 video or a multi-video sequential queue -> ~/Downloads/Zeno Outs/<VideoName>/
cd "$(dirname "$0")" || exit 1
export PATH="$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:$PATH"
unset CODEX_THREAD_ID

if [ ! -x .venv/bin/python ] && [ -f ./install.command ]; then
  echo "First-time setup: preparing Zeno Python environment..."
  bash ./install.command || exit $?
fi

CODEX_BIN="$(command -v codex 2>/dev/null || true)"
if [ -z "$CODEX_BIN" ]; then
  for cand in \
    "/Applications/ChatGPT.app/Contents/Resources/codex-cli/CodexCLI.app/Contents/MacOS/codex" \
    "/Applications/Codex.app/Contents/Resources/codex" \
    "/Applications/Codex.app/Contents/MacOS/codex" \
    "$HOME/Applications/ChatGPT.app/Contents/Resources/codex-cli/CodexCLI.app/Contents/MacOS/codex" \
    "$HOME/Applications/Codex.app/Contents/Resources/codex"; do
    if [ -x "$cand" ]; then
      CODEX_BIN="$cand"
      break
    fi
  done
fi

if [ -z "$CODEX_BIN" ]; then
  echo "Codex CLI not found on PATH or inside ChatGPT.app / Codex.app."
  [ -t 0 ] && read -r -p "Press Return to close."
  exit 1
fi

# Note: reasoning effort is pinned to low, web search is off, and multi_agent=true enables isolated subagents for multi-video queues.
MODEL="${ZENO_CODEX_MODEL:-gpt-6.1-sol}"
echo "Starting Zeno v2.0.0 (Codex: $MODEL) — drop 1 video or a multi-video queue to edit into ~/Downloads/Zeno Outs/<VideoName>/..."
if [ "$#" -gt 0 ]; then
  exec "$CODEX_BIN" --model "$MODEL" -c 'model_instructions_file="AGENTS.md"' -c 'model_reasoning_effort="low"' -c features.web_search_request=false -c features.multi_agent=true "$@"
else
  exec "$CODEX_BIN" --model "$MODEL" -c 'model_instructions_file="AGENTS.md"' -c 'model_reasoning_effort="low"' -c features.web_search_request=false -c features.multi_agent=true \
    "Zeno v2.0.0 edit session ready (follow AGENTS.md). Share 1 video or a multi-video queue (footage folder, Google Doc ?tab=... link or script path, and video name) to edit and deliver sequentially to ~/Downloads/Zeno Outs/<VideoName>/."
fi
