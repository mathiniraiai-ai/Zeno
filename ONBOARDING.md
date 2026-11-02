# Zeno — Team Onboarding

Zeno turns raw clips and a script into a finished vertical video. An AI agent (Claude Code, Antigravity, or OpenAI) makes the decisions; the Python tools do the heavy work. This page gets you from zero to a first edit.

## What you need
- A Mac (Apple Silicon) or a Windows PC (an NVIDIA GPU is optional; CPU works)
- Transcription access: normally your personal key: open https://zeno-production-bef7.up.railway.app (or the Zeno card in the hub), type your name, click Generate, and paste the key into `.env` (two lines, see below); alternatively your own Gemini API key
- One AI agent set up on your machine (Claude Code, Antigravity, or OpenAI)

## 1. Install (once, one double-click, about 5 minutes)

1. Clone the Zeno folder into **`Documents/Zeno`** (or unzip/clone anywhere — the installer automatically places it in **`Documents/Zeno`**, where Zeno always runs, and creates **`Downloads/Zeno Outputs`** for finished videos):
   ```bash
   cd ~/Documents
   git clone https://github.com/mathiniraiai-ai/Zeno.git
   ```
2. Double-click the installer:
   - **Mac:** `install.command`. The first time, macOS may block it: right-click it, choose **Open**, then **Open** again.
   - **Windows:** `install.bat`.
3. Wait for "Done" or "Almost done". The installer, with no admin password and no Homebrew:
   - installs `uv`, which brings Python 3.12,
   - uses FFmpeg if the PC already has it, otherwise downloads it into `tools/ffmpeg/`,
   - creates `.venv` and installs everything in `requirements.txt`,
   - creates `.env` from the template,
   - downloads the alignment model (Meta wav2vec2, about 360 MB, into `models/torch`) and runs the real checks.
4. Open https://zeno-production-bef7.up.railway.app (or the Zeno card in the hub), type your name, click **Generate**, and paste the key after `SCRIPTCUT_CLOUD_TOKEN=` in `.env`.
5. Double-click the installer again. Every line should say `[OK]`.

Running the installer again is always safe; it skips what is already there. Never commit or share `.env`.

## 2. Make a video

Wherever this page says `python`, use the project's Python: `.venv/bin/python` on Mac, `.venv\Scripts\python` on Windows.

Start a **fresh agent session for each video** (it keeps token use low), open this folder, and tell the agent what you have. The agent reads `agent/DIRECTOR.md` and follows it. The steps underneath are:

1. **Check the Edit Bay (Stage 0):**
   `python scripts/zeno.py --check`
   Confirms `.venv`, FFmpeg, OpenCV/Pillow/SciPy, YuNet face tracker, `.env` key presence, Indic/UI fonts, SFX/Marks/Premiere `.prproj` shell, and `~/Downloads/Zeno Outputs/`.
2. **Ingest, Transcribe, Match & Pitch (Stages 1–3):**
   `python scripts/zeno.py --inspect <raw_dir_or_clips> --script "<google_doc_url_or_path>" [--lang auto]`
   Extracts 16kHz audio, transcribes word timings, scores best takes, auto-pairs silent listening clips (`peer=`) for `split_h`/`split_v`, and generates `work/<project>/brief.txt`, `work/<project>/plan.edl`, and `work/<project>/pitch_card.md` (with a 50s live progress heartbeat in `work/<project>/progress.json`).
3. **Review & Approve the Editorial Pitch Card (Stage 3 Gate):**
   Review `pitch_card.md` and `plan.edl` (optionally quick-tweaking via `--swap-layout` or `--swap-take`), then confirm Yes/No before rendering.
4. **Render, Export Premiere `.prproj` & Verify (Stages 4–5):**
   `python scripts/zeno.py --execute work/<project>/plan.edl`
   Renders `~/Downloads/Zeno Outputs/<name>.mp4` (`9:16`), `<name>_1x1.mp4` (`1:1` Square Meta Ad), `<name>.timeline.txt`, and editable Adobe Premiere Pro project files (`<name>.prproj` and `<name>.premiere.xml`), then runs deterministic post-render verification.

Languages are mixed freely: native words stay native, English words stay English. `--lang` names the main native language.

Clips can stay where they are: give the agent the folder path (for example `~/Desktop/shoot_31oct/`) instead of copying them into `input/footage/`.

Zeno transcribes every clip. Clips that say the same thing are treated as takes of one line, whatever they are named and wherever they are in the folder, even if there are 5 copies of the same audio or video. It scores them all and keeps exactly one, the best. The others are listed as `[ALT_TAKE]` in the brief with their scores, so the agent (or you) can swap one in.

If the script names the speakers (`L2 Robot: ...`), Zeno checks every line is spoken by the right character. A line spoken by the wrong one is cut, and the same line is taken from another clip where the right character says it. Optional: put a 10 to 30 second sample of each character in `input/voices/` (`Man.wav`, `Robot.wav`) so it works reliably even with few clips.

**No clips yet?** Zeno can write the Veo shot JSON and first-frame prompt for every script line (`scripts/preproduce.py`, see `agent/concepts/08_preproduction`). Save the generated clips as `<reel-code>_D-1.mp4`, `_D-2.mp4`, ... and run the normal edit.

**Silent moments in the script:** write them as `(Man takes out his phone)` or note a reaction on a line, `L4 Man (laughs mockingly): ...`. Zeno keeps those moments instead of trimming them.

## 3. What works today
- **5-Stage Interactive Zeno Harness (`scripts/zeno.py`)**: Stage 0 readiness check (`--check`), Stages 1–3 ingest/transcribe/match/pitch (`--inspect` + 50s live heartbeat), Stage 3 Pre-Render Yes/No Approval Gate (`pitch_card.md` + `--swap-layout` / `--swap-take`), and Stages 4–5 render + Premiere Pro export + deep QC (`--execute`).
- **Human-Like Editing Engine**: 10ms RMS breath-envelope cuts, rhythm-aware pause tightening, 45ms zero-drift audio crossfades + `-48 dBFS` room-tone floor bridging, per-clip RMS gain matching + single master 2-pass `-11.5 LUFS` / `-1.0 dBTP` loudness mastering, transcript-intent zooms & deadband-smoothed focus pan, kinetic-gated `whoosh.wav` SFX, and 2-pulse `flash`/`vignette` `✓`/`✗` marks.
- **4-Clip Split-Screen Talking + Live Listening Video (`styles/micronarrative/`)**: Auto-detects silent listening clips (`*_listening.*` or 0-word clips) and pairs them as live moving `peer=` streams on `split_h`/`split_v`, plus auto-extracts stacked `"Stop Saying"` hook banners.
- **Adobe Premiere Pro `.prproj` + `.premiere.xml` Export**: Exports native GZIP-compressed `.prproj` and FCP7 `.premiere.xml` files with multi-keyframe eased Scale curves, Center focus offsets, video/audio `<transitionitem>` dissolves, and sequence `<marker>` cues.
- Burned-in subtitles in client footage are ignored for now.

## 4. Where files go

| Folder | What | Share? |
|---|---|---|
| `input/footage/`, `input/script/` | your raw clips and script | no (local) |
| `work/<project>/` | transcript, plan, analysis | no (local, deletable) |
| `Downloads/Zeno Outputs/` | finished videos (outside the Zeno folder) | **move finished videos to the shared drive** |
| `styles/` | learned styles | owner changes these; you just `git pull` |

## 5. Keep your disk small
Each 90-second video leaves about 330 MB of intermediates. Clean up weekly:
```bash
python scripts/clean.py            # shows what would go
python scripts/clean.py --apply    # deletes it
```
It removes the render cache and old work files. It never touches `Downloads/Zeno Outputs/`, `styles/`, your raw footage or scripts.

## 6. Stay up to date
Double-click `update.command` (Mac) or `update.bat` (Windows). It pulls the latest Zeno and refreshes packages; your `.env`, clips, `work/` and `Downloads/Zeno Outputs/` are never touched. Do not edit `styles/`, `agent/` or `scripts/` yourself; tell the owner what you need.

## Licences
- Alignment model: Meta wav2vec2 base (95M), MIT licence, commercial use allowed.
- Gemini is used under Google's API terms with your team key.
- Not yet audited: uroman, the YuNet face model, fonts, SFX packs. Check them before sharing outside the company.

## If something goes wrong
- `setup.py --smoke` shows a `[FAIL]` line: it names the part (FFmpeg, model, key). Fix that first.
- Google Doc says private: ask for "anyone with the link can view".
- Out of disk: run `scripts/clean.py --apply`.
- A transcript looks wrong: tell the agent the video, the line, and what you hear.
