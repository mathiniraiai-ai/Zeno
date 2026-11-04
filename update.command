#!/bin/bash
# Zeno updater for macOS. Double-click (first time: right-click > Open), or run:  bash update.command
# Pulls the latest Zeno, then refreshes packages. Your .env, clips, work/ and output/ are never touched.
set -e
cd "$(dirname "$0")"
if [ ! -d .git ]; then
  echo "This copy is not connected to GitHub yet. Connecting it now..."
  if [ ! -x .venv/bin/python ]; then
    echo "Run install.command first."
    exit 1
  fi
  .venv/bin/python scripts/auto_updater.py --connect-git
fi
echo "Getting the latest Zeno..."
git pull --ff-only
bash install.command
