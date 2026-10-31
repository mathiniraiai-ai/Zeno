# Zeno — Team Onboarding

Zeno turns raw clips and a script into a finished vertical video. An AI agent (Claude Code, Antigravity, or OpenAI) makes the decisions; the Python tools do the heavy work. This page gets you from zero to a first edit.

## What you need
- A Mac (Apple Silicon) or a Windows PC (an NVIDIA GPU is optional; CPU works)
- Git, Python 3.12, FFmpeg
- A Gemini API key (ask the Zeno owner for the shared key)
- One AI agent set up on your machine (Claude Code, Antigravity, or OpenAI)

## 1. Install (once, about 15 minutes, plus a 360 MB download)

**Mac**
```bash
git clone <repo-url> Zeno && cd Zeno
brew install ffmpeg uv
uv venv --python 3.12 .venv
uv pip install --python .venv/bin/python -r requirements.txt
```

**Windows (PowerShell)**
```powershell
git clone <repo-url> Zeno; cd Zeno
winget install Gyan.FFmpeg
py -3.12 -m venv .venv
.venv\Scripts\python -m pip install torch torchaudio --index-url https://download.pytorch.org/whl/<cuda-tag>   # pick <cuda-tag> at pytorch.org; use the cpu index if you have no NVIDIA GPU
.venv\Scripts\python -m pip install -r requirements.txt
```

Then, on both (use `.venv\Scripts\python` on Windows):
```bash
cp .env.example .env          # open .env and paste your GEMINI_API_KEY
.venv/bin/python scripts/setup.py --prewarm   # downloads the alignment model into models/torch (360 MB)
.venv/bin/python scripts/setup.py --smoke     # real checks; every line should say [OK]
```
Never commit `.env`. It is git-ignored; keep it that way.

## 2. Make a video

Wherever this page says `python`, use the project's Python: `.venv/bin/python` on Mac, `.venv\Scripts\python` on Windows.

Start a **fresh agent session for each video** (it keeps token use low), open this folder, and tell the agent what you have. The agent reads `agent/DIRECTOR.md` and follows it. The steps underneath are:

1. **Get the script.** Paste the Google Doc link of the one tab you need:
   `python scripts/script_intake.py "<link-to-the-tab>" --name <project>`
   The doc must be shared as "anyone with the link can view". Only that tab is saved to `input/script/<project>.txt`.
2. **Put the raw clips** in `input/footage/`.
3. **Analyse and plan:** `python scripts/prepare.py input/footage/*.mp4 --script input/script/<project>.txt --lang <lang>`
4. **Review** `work/<project>/brief.txt` and `plan.edl`; the agent fixes anything off.
5. **Render:** `python scripts/render.py work/<project>/plan.edl --verify` → `output/<name>.mp4`

Languages are mixed freely: native words stay native, English words stay English. `--lang` names the main native language.

## 3. What works today
- General editing: best takes, pause-snapped cuts, layouts (`single`, `split_v`, `split_h`, `chat_bar`, `video_call`, `hybrid`), ✓/✗ marks, loudness.
- Learned style **micronarrative** (`styles/micronarrative/`): the look and transitions are built and checked against the reference. Arranging raw clips into this style is **not built yet**; it needs one real raw set to finish.
- Burned-in subtitles in client footage are ignored for now.

## 4. Where files go

| Folder | What | Share? |
|---|---|---|
| `input/footage/`, `input/script/` | your raw clips and script | no (local) |
| `work/<project>/` | transcript, plan, analysis | no (local, deletable) |
| `output/` | finished videos | **move finished videos to the shared drive** |
| `styles/` | learned styles | owner changes these; you just `git pull` |

## 5. Keep your disk small
Each 90-second video leaves about 330 MB of intermediates. Clean up weekly:
```bash
python scripts/clean.py            # shows what would go
python scripts/clean.py --apply    # deletes it
```
It removes the render cache and old work files. It never touches `output/`, `styles/`, your raw footage or scripts.

## 6. Stay up to date
`git pull` for new styles and fixes. Do not edit `styles/`, `agent/` or `scripts/` yourself; tell the owner what you need.

## Licences
- Alignment model: Meta wav2vec2 base (95M), MIT licence, commercial use allowed.
- Gemini is used under Google's API terms with your team key.
- Not yet audited: uroman, the YuNet face model, fonts, SFX packs. Check them before sharing outside the company.

## If something goes wrong
- `setup.py --smoke` shows a `[FAIL]` line: it names the part (FFmpeg, model, key). Fix that first.
- Google Doc says private: ask for "anyone with the link can view".
- Out of disk: run `scripts/clean.py --apply`.
- A transcript looks wrong: tell the agent the video, the line, and what you hear.
