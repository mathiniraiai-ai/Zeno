#!/bin/bash
# Zeno one-click installer for macOS (Apple Silicon or Intel).
# Double-click in Finder (first time: right-click > Open), or run:  bash install.command
# Safe to run again: it skips anything already installed. No admin password, no Homebrew needed.
set -e
cd "$(dirname "$0")"

# 0. Team rule: Zeno lives in ~/Downloads/Office Project/Zeno and finished videos go to ~/Downloads/Zeno Outputs.
#    If this folder is somewhere else, move it there first. (The owner's dev copy has a .zeno-dev file and stays put.)
TARGET="$HOME/Downloads/Office Project/Zeno"
HERE="$(pwd -P)"
if [ ! -f .zeno-dev ] && [ "$HERE" != "$(cd "$TARGET" 2>/dev/null && pwd -P)" ]; then
  if [ -e "$TARGET" ]; then
    echo "Zeno is already installed at: $TARGET"
    echo "Open that folder and double-click install.command there (or delete it first to install this copy)."
    exit 1
  fi
  echo "Moving Zeno to $TARGET ..."
  mkdir -p "$(dirname "$TARGET")"
  mv "$HERE" "$TARGET"
  exec bash "$TARGET/install.command"
fi
mkdir -p "$HOME/Downloads/Zeno Outputs"

echo "=============================================="
echo " Zeno installer (macOS)"
echo " Folder : $(pwd)"
echo " Videos : $HOME/Downloads/Zeno Outputs"
echo "=============================================="

# 1. uv: installs Python 3.12 and the packages (user folder only, no admin)
export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"
if command -v uv >/dev/null 2>&1; then
  echo "[1/5] uv already installed"
else
  echo "[1/5] Installing uv (Python manager)..."
  curl -LsSf https://astral.sh/uv/install.sh | sh
  export PATH="$HOME/.local/bin:$PATH"
fi

# 2. FFmpeg: use one already on the machine, else download a static build into tools/ffmpeg/
if [ -x tools/ffmpeg/ffmpeg ] && [ -x tools/ffmpeg/ffprobe ]; then
  echo "[2/5] FFmpeg already in tools/ffmpeg"
elif command -v ffmpeg >/dev/null 2>&1 && command -v ffprobe >/dev/null 2>&1; then
  echo "[2/5] Using system FFmpeg: $(command -v ffmpeg)"
else
  echo "[2/5] Downloading FFmpeg (about 70 MB)..."
  case "$(uname -m)" in arm64) ARCH=arm64 ;; *) ARCH=amd64 ;; esac
  FINAL=$(curl -sIL -o /dev/null -w '%{url_effective}' "https://ffmpeg.martin-riedl.de/redirect/latest/macos/$ARCH/release/ffmpeg.zip")
  mkdir -p tools/ffmpeg
  TMP=$(mktemp -d)
  curl -fL --progress-bar -o "$TMP/ffmpeg.zip" "$FINAL"
  curl -fL --progress-bar -o "$TMP/ffprobe.zip" "${FINAL%ffmpeg.zip}ffprobe.zip"
  unzip -oq "$TMP/ffmpeg.zip" -d tools/ffmpeg
  unzip -oq "$TMP/ffprobe.zip" -d tools/ffmpeg
  rm -rf "$TMP"
  chmod +x tools/ffmpeg/ffmpeg tools/ffmpeg/ffprobe
  xattr -d com.apple.quarantine tools/ffmpeg/ffmpeg tools/ffmpeg/ffprobe 2>/dev/null || true
  echo "      FFmpeg installed in tools/ffmpeg"
fi

# 3. Python 3.12 virtual environment + packages (torch, torchaudio, opencv, google-genai, ...)
if [ -x .venv/bin/python ]; then
  echo "[3/5] .venv exists; updating packages"
else
  echo "[3/5] Creating .venv with Python 3.12..."
  uv venv --python 3.12 .venv
fi
uv pip install --python .venv/bin/python -r requirements.txt

# 4. .env from the template (never overwrites an existing one)
if [ -f .env ]; then
  echo "[4/5] .env already exists (kept as is)"
else
  cp .env.example .env
  echo "[4/5] Created .env from .env.example"
fi

# 5. Alignment model (Meta wav2vec2, about 360 MB, into models/torch) + real checks
echo "[5/5] Downloading the alignment model (first time only) and checking everything..."
.venv/bin/python scripts/setup.py --prewarm >/dev/null || true
.venv/bin/python scripts/setup.py --smoke || true

echo ""
echo "=============================================="
if grep -q '^SCRIPTCUT_CLOUD_TOKEN=..*' .env 2>/dev/null; then
  echo " Done. Zeno is ready."
else
  echo " Almost done: generate your key at"
  echo "   https://zeno-production-bef7.up.railway.app"
  echo " and paste it after SCRIPTCUT_CLOUD_TOKEN= in the .env file."
fi
echo "=============================================="
