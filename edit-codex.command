#!/bin/bash
cd "$(dirname "$0")" || exit 1
if [ ! -x .venv/bin/python ]; then
  bash install.command || exit $?
fi
exec .venv/bin/python scripts/launch_edit.py --agent codex "$@"
