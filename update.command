#!/bin/bash
# Zeno Cloud updater for macOS. Double-click (first time: right-click > Open), or run:  bash update.command
# Downloads the latest Zeno update from Zeno Cloud (no GitHub needed). Your .env, clips, work/ and output/ are never touched.
set -e
cd "$(dirname "$0")"
if [ ! -x .venv/bin/python ]; then
  echo "Run install.command first."
  exit 1
fi
echo "Checking Zeno Cloud for the latest update..."
.venv/bin/python scripts/auto_updater.py
echo "Done."
