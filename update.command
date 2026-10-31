#!/bin/bash
# Zeno updater for macOS. Double-click (first time: right-click > Open), or run:  bash update.command
# Pulls the latest Zeno, then refreshes packages. Your .env, clips, work/ and output/ are never touched.
set -e
cd "$(dirname "$0")"
if [ ! -d .git ]; then
  echo "This copy was not installed with git, so it can't update itself."
  echo "Ask the Zeno owner for the download key and the one-line setup in ONBOARDING.md."
  exit 1
fi
echo "Getting the latest Zeno..."
git pull --ff-only
bash install.command
