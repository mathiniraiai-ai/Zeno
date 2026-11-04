# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

# Zeno — Low-Token Operational Manual

Script-led AI video editing harness (`1080x1920` MP4). Given raw clips in `input/footage/` and a script in `input/script/`, output best takes, pause-snapped cuts, dynamic speaker layouts, and `✓`/`✗` marks + SFX to `~/Downloads/Zeno Outputs/`.

**Agent entry point**: read `agent/DIRECTOR.md` first. It sets the decision loop and points to one concept file per topic in `agent/concepts/` (script intake, footage intake, style library, reference learning, on-screen text, token budget). Learned styles live in `styles/<name>/`. A style may allow captions or extra graphics only if its own `SKILL.md` says so; otherwise rule 4 below applies.

## 1. Hard Rules
1. **Workspace Isolation**: Never modify any files outside this Zeno folder (team PCs: `~/Documents/Zeno`; owner: `/Users/mathiniraiselvan/Documents/Office project/Zeno`). Never touch other projects in `Documents`.
2. **Folders**: Zeno runs from `~/Documents/Zeno` on team PCs (the installer puts it there). Every finished video (`.mp4` + `.timeline.txt`) goes to `~/Downloads/Zeno Outputs/`, never inside the repo. Raw clips can stay in any local folder: pass their path to `prepare.py`.
3. **Takes**: Every clip is transcribed. Clips that say the same thing are takes of one line, whatever their file names, their order, or even if the audio or video is identical (2, 3 or 5 copies): `prepare.py` scores all of them and keeps exactly ONE (`[BEST_TAKE]`); the others are listed as `[ALT_TAKE]` with scores in `brief.txt`. Never assume the first, middle or last take is right: check the scores and swap a take in `plan.edl` if needed.
4. **Speakers**: The script names who speaks each line (`L2 Robot: ...`). `prepare.py` measures the voices and cuts any line spoken by the wrong character (`[WRONG_SPEAKER]` in `brief.txt`), then looks in every clip, including unused takes, for that line said by the right character and splices it in (`[FILL]`). `[WRONG_SPEAKER_NO_FIX]` means no clip has the right voice saying it: tell the user to reshoot. `[SPEAKER_CHECK] not applied` means the voices could not be told apart: say so in the report. Reference samples in `input/voices/<Speaker>.wav` make this reliable with few clips.
5. **Never Read Raw JSONs or Python Source**:
   - **Never** read `work/*/words.json`, `work/*/silences.json`, or `work/*/align.json` unless debugging a specific word timing.
   - **Never** read `scripts/*.py` during normal edits. Use the 3-step CLI workflow and `plan.edl` syntax below.
6. **Fresh Session**: Start every video in a fresh session (`/clear` or new chat) with unused MCP connectors disabled.
7. **Clean Editorial Style**: No B-roll, no unrequested captions, no CTAs, no extra graphics.

## 2. Token Budget & Mandatory Reporting
- **Target**: **`12k–18k tokens` per minute of finished video** (`~20k–30k total` for a typical 1.5-min edit).
- **Strict Ceiling**: **`20k tokens / min`** of finished video.
- **Mid-Job Warning**: Warn the user immediately if messy footage or excessive retakes risk exceeding `20k tokens / min`.
- **Mandatory End-of-Job Output**: Print the Token Usage table at the end of every job **and** append a row to `README.md`:

```markdown
| Date | Task | Context tokens (start → end) | Tokens used | 5-hour limit | Weekly limit |
|---|---|---|---|---|---|
| YYYY-MM-DD | **<Language> – "<Title>"** (<N> clips, <M> script lines): <MM:SS> final, <K> ✓/✗ marks → `~/Downloads/Zeno Outputs/<name>.mp4` | <start> → <end> | ~<delta> | <start>% → <end>% | <start>% → <end>% |
```

## 3. The 5-Stage Interactive Zeno Harness (`scripts/zeno.py`)

**Pre-job gate (mandatory, every job, every agent).** Do not start from a guess and do not start before the user has handed over the inputs:
1. Run `python3 scripts/zeno.py --preflight`. It runs live checks (FFmpeg, OpenCV face detection on a real frame, the wav2vec2 aligner, and a small WAV sent to the Zeno server, which checks its key store and Gemini). Show the user the PASS/FAIL table.
2. If every check passes, ask the user one question: "Share the script link (the Google Doc tab) and the footage folder." The footage must be a local folder or files; a Drive or web link is not downloaded, so ask them to download it first. A Google Doc link must end in `?tab=...`; never load a whole document.
3. Only after the user answers, run `python3 scripts/zeno.py --inspect <footage> --script <script>`. It repeats the live checks, then checks the script (spoken lines, scenes, strike-through text, layout notes) and every clip (readable, has audio) and prints them. If anything fails it stops with `[BLOCKED]` and exits with code 2; fix the listed problem with the user. Never use `--skip-preflight`.


0. **Stage 0 — Edit Bay Readiness Check**:
   `python3 scripts/zeno.py --check` (prints `[✓] Installed` vs `[✗] Remaining` across all 8 dependencies without reading `.env` secrets).
1. **Stages 1–3 — Ingest, Transcribe, Match & Pitch (`1 command` + 50s Live Heartbeat)**:
   ```bash
   python3 scripts/zeno.py --inspect <raw_dir_or_clips> --script <script_path_or_google_doc_url> [--lang <lang>] [--style <name>] [--preset auto|single|split_v|split_h|chat_bar|video_call|hybrid] [--mark-pack default|glossy|flash|vignette]
   ```
   - `--style <name>`: the style folder this job follows (for example `micronarrative`). Always pass it when the style is known: it writes `# style: <name>` into the plan so the job counts toward the style's readiness level (`scripts/style_readiness.py`) and the team's learning.
   - `<lang>`: `en`, `kn`/`kannada`, `ta`/`tamil`, `hi`/`hindi`, `te`/`telugu`, `ml`/`malayalam`, or `auto`.
   - Emits live stage progress every ~48s (`work/<project>/progress.json` / `python3 scripts/progress.py`). Schedule a 50s timer while running in the background so the user gets live stage updates in chat.
2. **Stage 3 Gate — Refine Plan & Ask Yes/No Before Rendering (`Mandatory Stop`)**:
   - Read **ONLY** `work/<project>/brief.txt`, `work/<project>/plan.edl`, and `work/<project>/pitch_card.md`.
   - Refine `work/<project>/plan.edl` using Agents 1–4, refresh the pitch card (`python3 scripts/zeno.py --pitch work/<project>/plan.edl`), **present the Editorial Pitch Card**, and **ask the user Yes/No** before rendering.
3. **Stages 4–5 — Render, Premiere `.prproj` Export & Verify (`1 command` after Yes)**:
   ```bash
   python3 scripts/zeno.py --execute work/<project>/plan.edl [--square] [--loudness -11.5] [--gain-db +2.0]
   ```
   - Renders `~/Downloads/Zeno Outputs/<name>.mp4` + `<name>_1x1.mp4` (`1080×1080` Square Meta Ad) + `<name>.timeline.txt` + **editable Adobe Premiere Pro project files** (`<name>.prproj` and `<name>.premiere.xml`), runs deterministic script coverage & `-11.5 LUFS` (`-1.0 dBTP` cap) loudness check, and generates `work/<project>/marks_contact_sheet.jpg`. Use `vol=+3dB` on any segment in `plan.edl` for exact mid-video dB boosts, or run `python3 scripts/premiere_export.py work/<project>/plan.edl` standalone.

## 4. The 5 Dynamic Layout Modes, Pre-Stitched Split Screens & Swappable Mark Packs

| Layout Mode | Visual Layout (`1080×1920`) | When to Use |
|---|---|---|
| `single` | Full-frame clip (`zoom=1.08` jump-cut punch-in, `zoom=1.0->1.06` slow push, `flash_zoom`). | Solo teaching lines, hooks, live conversation, **OR pre-stitched Google Flow vertical split-screen clips** (match straight as `single` with `zoom=1.0`). |
| `split_v` | Top/bottom 50/50 split (`1080×960` each) with divider & inactive speaker dim. | Two-character Q&A when Character A and Character B are **separate raw files** (e.g., HeyGen renders). |
| `split_h` | Left/right 50/50 vertical split (`540×1920` each) with divider. | Side-by-side split screen when Character A and Character B are **separate raw files**. |
| `chat_bar` | Full-frame speaker + frosted glassmorphic prompt bar (`label="Phrase..."`). | Language drills ("How to say X?"). |
| `video_call` | Full-frame speaker + rounded Picture-in-Picture (PiP) card (`pip=S1@3.2`). | Remote call / FaceTime style dialogues. |
| `hybrid` | Mixes `single` on teaching monologues with `split_v`/`video_call`/`chat_bar` on dialogue turns. | **Default (`auto`)** — best retention. |

- **Listener rule (every split: left/right or top/bottom).** While one character speaks, the OTHER panel shows the other character, silent and listening: left speaks -> right listens, right speaks -> left listens, and the same for top and bottom. Who sits where comes from the script (`Top: Robot | Bottom: Lady 1`, or `Woman - Right screen | Miss Nova - Left screen`, or "left screen" in the character list). The picture comes from that character's silent clip: put the character's name in the file name (`Robot_listening.mp4`, `Lady1_idle.mp4`); an unnamed silent clip is matched by how it looks (colours) and is left unassigned if unsure. Silent clips are walked through turn after turn, never restarted from the same frame. With no silent clip, a quiet mouth-closed moment of the character's own clip is used (a still frame if it is under 1 s) and the brief says `[LISTENER_FALLBACK]`; `[LISTENER_GUESS]` and `[LISTENER_MISSING]` mean a silent clip could not be tied to the character or does not exist. A character that lands in two panels is reported as `[PANEL_FLIP]`.

**Swappable `✓`/`✗` Mark Packs (`--mark-pack` or `# mark_pack:`)**:
- `flash` (**default**): Full-frame `1080×1920` subtle `0.50` alpha green/red border overlay (`assets/marks/green_flash.png`, `red_flash.png`) that triggers **only at the end of the wrong/right sentence** and **flashes 2 times (`0.56s`: Pulse 1 → OFF → Pulse 2)** with `+4 dB` error buzzer (`buzzer.wav` + `kn20228_wrong_core.mp3`) and `+2.5 dB` right ding (`correct_ding.wav` + `sn_correct.wav`).
- `vignette`: Full-frame radial glow (`assets/marks/w42_green_vignette.png`, `w42_red_vignette.png`, `0.50` alpha 2-pulse at sentence end).
- `default`: Flat circle badge (`assets/marks/right.png`, `wrong.png`)
- `glossy`: 3D glass gradient badge (`assets/marks/glossy_right.png`, `glossy_wrong.png`)
- Custom: Place `right.png` & `wrong.png` in `assets/marks/<pack>/`.
- **Skill Links**: [`.agent/skills/video-edit/SKILL.md`](.agent/skills/video-edit/SKILL.md) | [`styles/micronarrative/SKILL.md`](styles/micronarrative/SKILL.md) | [`agent/DIRECTOR.md`](agent/DIRECTOR.md) | [`agent/concepts/word_boundary_training.md`](agent/concepts/word_boundary_training.md)

## 5. Mandatory 5-Agent Parallel Workflow & Agent 5 Deep Post-Render Analyzer

Assign at least **5 specialized agents** per editing task (defaulting to **Antigravity** or **Claude Code**):
1. **Agent 1 (`Script Coverage & Take Auditor`)**: Audits 100% script coverage, speaker identity, and best takes.
2. **Agent 2 (`Pause-Cut & De-Duplication Specialist`)**: Removes repeated dialogues/words/false starts and cuts internal silences (`>0.85s`).
3. **Agent 3 (`Dynamic Zoom & Layout Choreographer`)**: Applies punch-ins (`z=1.08`), push-ins (`z=1.0->1.06`), pull-outs (`z=1.08->1.0`), and layouts.
4. **Agent 4 (`Transitions, Rapid-Strobe Overlays & SFX Designer`)**: Applies transitions (`smoothleft`, `smoothright`, `warm_flash`, `fade`) + `whoosh.wav` (`-2 dB`) and rapid-strobe full-frame Red/Green PNG overlays + strong error/right SFX.
5. **Agent 5 (`Post-Render Deep Video Analyzer & Auto-Fixer`)**: After `render.py` completes, inspects the complete rendered video locally via Python (`verify.py`, `words.json` timestamps, FFmpeg `silencedetect`, WAV RMS energy, and RGB frame checks — without external API calls) to catch and fix any repeated dialogues, empty silences, or missing transitions/SFX/overlays.

## 6. `plan.edl` Quick Syntax

```text
# output: output/Kannada_I_am_hungry.mp4
# canvas: 1080x1920@30
# preset: hybrid
# mark_pack: glossy
# loudness: -14

# SEGMENTS: id | src | start-end | layout | zoom | transition | extras | note
L1 | S1 | 1.12-5.48 | single | 1.0->1.06 | cut | focus=0.5,0.45 | Hook question
L2 | S2 | 0.85-4.90 | split_v:top=S1@5.48 | 1.0 | cut | active=bottom | Wrong student attempt
L3 | S3 | 1.05-6.20 | chat_bar | 1.08 | fade:0.3 | label="ನನಗೆ ಹಸಿವಾಗಿದೆ" | Teacher correction

# MARKS: type | segment | at | duration | pos | size | sfx | gain_db
wrong | L2 | 1.40 | 1.3 | top-right | 0.22 | buzzer.wav | -4
right | L3 | 2.10 | 1.3 | top-right | 0.22 | correct_ding.wav | -4

# SFX: file | segment | at | gain_db
whoosh.wav | L3 | 0.0 | -10
```
- **SFX (`assets/sfx/`)**: Right: `correct_ding.wav`, `sn_correct.wav`, `w42_right.wav` | Wrong: `buzzer.wav`, `sn_wrong.wav`, `w42_wrong.wav` | Transitions: `whoosh.wav`, `pop.wav`.

## 7. Developing the Zeno code itself (not editing a video)

Use `.venv/bin/python` (Python 3.12; `uv venv --python 3.12 .venv && uv pip install --python .venv/bin/python -r requirements.txt`, or run `install.command` / `install.bat`). `scripts/zeno.py` re-execs itself into `.venv` automatically.

```bash
.venv/bin/python -m unittest tests.test_engine                                  # engine regression suite (~0.1 s, no API calls)
.venv/bin/python -m unittest tests.test_engine.TestZenoEngine.<test_name>       # one test
.venv/bin/python scripts/setup.py --smoke                                       # env check, never prints keys (--prewarm downloads the 360 MB wav2vec2 weights)
.venv/bin/python scripts/eval_alignment.py --help                               # score word timing against hand-marked labels
.venv/bin/python scripts/knowledge_sync.py pull|push|match "<wording>"                # knowledge kept in the server database (no commit or redeploy for new recipes)
.venv/bin/python scripts/format_library.py search "<script wording>"                  # look up how a format should look; `extract <video> --id ...` measures a reference locally, no tokens, video not kept
.venv/bin/python scripts/style_readiness.py [style] [--json]                    # measured readiness level (0-3) of a style from files and tests
.venv/bin/python scripts/clean.py [--apply]                                     # free disk (dry run by default; only touches work/ and input/standins|reference)
python service/test_keys.py                                                     # service: signup/admin/revoke/usage on local SQLite (add --database-url postgresql://... for Postgres)
python service/test_service.py [--no-live | --url https://<host> --token zeno_...]
python service/check_prompt_sync.py                                             # must pass after editing either copy of the Gemini prompt
cd service && uvicorn app:app --port 8000                                       # run the key/transcription service locally
```
There is no linter or build step. `tests/test_engine.py` also scores a gold-set plan under `work/` (git-ignored), so that check only runs where the work folder exists.

## 8. Architecture (big picture)

**Director / Engine split.** A thin LLM "Director" (`agent/DIRECTOR.md` + `agent/concepts/*`) only reads small text files and edits `plan.edl`; everything measurable is Python in `scripts/`. This is why the token budget is low: do not move measuring work into prompts. `CLAUDE.md` and `AGENTS.md` (Antigravity/OpenAI) are parallel manuals and must be kept in sync.

**Pipeline** (`scripts/zeno.py` orchestrates; the older `prepare.py` → `render.py` CLI still works): ingest/extract audio → transcribe → align → group takes and match script lines → write `brief.txt` + `plan.edl`/`plan.json` + `pitch_card.md` → Yes/No gate → `render.py` (+ `premiere_export.py`, `verify.py`, token telemetry).
- `prepare.py` is the large planner: take grouping/scoring, wrong-speaker detection (`speaker_check.py`), silent-beat handling (`motion.py`), cut snapping to 10 ms RMS minima, mark/SFX placement. Per-clip caches live in `work/_clips/<stem>-<fingerprint>`.
- `edl_parser.py` is the lossless `plan.edl` ⇄ `plan.json` bridge; `render.py` + `layouts.py` turn segments into FFmpeg filter graphs (layouts, zooms, overlay transitions from `assets/transitions/*.json`, marks, SFX, loudness). `vision_tracker.py` (OpenCV/YuNet) drives framing.
- `scripts/align_engine/` is the alignment stack: Gemini verbatim transcript (`gemini_transcriber.py`) → `guardian.py`/lexicon loanword healing → Indic G2P (`phonetics/`, `uroman/` fallback) → wav2vec2 base CTC (`acoustic.py`, `viterbi.py`) → energy snaps in `onset_verifier.py`. wav2vec2 base (MIT) is the only aligner; MMS (CC-BY-NC) must not come back.
- Tuning thresholds live in `styles/<name>/spec.json` (`tuning`/`engine` blocks) read through `style_config.py`; learned editor rules persist in `styles/<name>/brain_memory.json` (`brain_memory.py`) and are injected into `brief.txt`. New styles are learned from a reference video with `learn_reference.py` (scan → frames → align → standins).

**Transcription & Editorial Learning Service** (`service/`, FastAPI on Railway, deployed from this folder as its own root): holds the only Gemini key and collects team editorial learnings. Team PCs authenticate once via zero-touch Google OAuth (`python3 scripts/setup.py --login` or `install.command`, which opens the browser for `@gosupernova.live` sign-in (more domains only via `ZENO_ALLOWED_EMAIL_DOMAINS`) and automatically saves `.zeno_session.json` + `ZENO_CLOUD_URL` / `ZENO_CLOUD_TOKEN` in `.env`, with legacy `SCRIPTCUT_CLOUD_*` fallback). Keys are stored as SHA-256 hashes in Postgres (SQLite locally) and revocable at `/admin`. `scripts/token_tracker.py` posts per-run telemetry, `plan.auto.edl -> plan.edl` diffs, and `brain_memory.json` rules to `/v1/telemetry`, and `python3 scripts/zeno.py --sync-learnings` syncs shared rules via `/v1/learnings`. `service/prompt.py` is a deliberate copy of the prompt in `gemini_transcriber.py` (Railway only sees `service/`); keep them identical.

## GPU use (all agents)
Zeno uses the computer's GPU by itself: the aligner runs on NVIDIA CUDA or the Apple GPU (MPS), and H.264 encoding uses VideoToolbox (Mac), NVENC / Quick Sync / AMF (Windows), each tested with a real encode and falling back to CPU (`libx264`) if it fails. `python3 scripts/gpu_install.py --status` and `setup.py --smoke` show what a computer uses. Force with `ZENO_DEVICE=cpu|cuda|mps` or `ZENO_ENCODER=cpu|h264_nvenc|...`. On Windows the installer (and the daily updater) switches to the CUDA build of torch when `nvidia-smi` finds a driver with CUDA 12.6 or newer; it never overwrites a CUDA torch with the CPU build.

## Caches & confirmations (all agents)
Never ask the user to clear caches and never delete them by hand (`rm -rf work/...`). Caches under `work/_clips/<stem>-<fingerprint>` refresh themselves: each folder records the engine version that made it (`engine_version.json`), a change to the transcript code (prompt, healing, lexicons) drops that clip's transcript and alignment, a change to the aligner drops only the alignment (the paid transcript stays), and a damaged file is rebuilt. Writes are atomic. Only if a result is wrong for a reason the code cannot see, run the job once with `--fresh`. The only confirmation in a job is the Stage 3 Yes/No before rendering; ask it once, in chat, and do not repeat it after the user says Yes. Disk space is handled by the daily background task (`clean.auto_clean`, only when free disk is under 20 GB); to do it by hand run `python3 scripts/clean.py` (dry run), show the total, ask once, then `python3 scripts/clean.py --apply` as one command.

## Secrets (all agents)
Never open, print, quote or paste `.env`, `.zeno_session.json`, or any key in a chat or log. They hold the user's personal Zeno session token (or a Gemini key). To check setup, run `python3 scripts/setup.py --smoke` or `python3 scripts/setup.py --whoami`; they report [OK]/[FAIL] without showing keys. Read `.env.example` instead if you need the variable names.
