# AGENTS.md — Zeno (Antigravity Gemini 4 Argon & Dual-Agent Instructions)

Always-on operational rules for **Antigravity (Gemini 4 Argon)** and **Claude Code (Opus 4.5 / 5.5)** inside this Zeno folder (team PCs: `~/Documents/Zeno`; owner: `/Users/mathiniraiselvan/Documents/Office project/Zeno`).

**Agent entry point** (Antigravity, OpenAI and any other agent, not only Claude): read `agent/DIRECTOR.md` first. It sets the decision loop and points to one concept file per topic in `agent/concepts/`. Learned styles live in `styles/<name>/`. A style may allow captions or extra graphics only if its own `SKILL.md` says so; otherwise rule 4 below applies.

---

## 1. Hard Rules

1. **Strict Workspace Boundary**: Never modify, move, or delete any file outside this Zeno folder (team PCs: `~/Documents/Zeno`; owner: `/Users/mathiniraiselvan/Documents/Office project/Zeno`). Other projects in `Documents` are strictly off-limits.
2. **Folders**: Zeno runs from `~/Documents/Zeno` on team PCs (the installer puts it there). Every finished video (`.mp4` + `.timeline.txt`) goes to `~/Downloads/Zeno Outputs/`, never inside the repo. Raw clips can stay in any local folder: pass their path to `prepare.py`.
3. **Takes**: Every clip is transcribed. Clips that say the same thing are takes of one line, whatever their file names, their order, or even if the audio or video is identical (2, 3 or 5 copies): `prepare.py` scores all of them and keeps exactly ONE (`[BEST_TAKE]`); the others are listed as `[ALT_TAKE]` with scores in `brief.txt`. Never assume the first, middle or last take is right: check the scores and swap a take in `plan.edl` if needed.
4. **Speakers**: The script names who speaks each line (`L2 Robot: ...`). `prepare.py` measures the voices and cuts any line spoken by the wrong character (`[WRONG_SPEAKER]` in `brief.txt`), then looks in every clip, including unused takes, for that line said by the right character and splices it in (`[FILL]`). `[WRONG_SPEAKER_NO_FIX]` means no clip has the right voice saying it: tell the user to reshoot. `[SPEAKER_CHECK] not applied` means the voices could not be told apart: say so in the report. Reference samples in `input/voices/<Speaker>.wav` make this reliable with few clips.
5. **Never Read Raw Word JSONs or Python Source**:
   - **Never** read `work/*/words.json`, `work/*/silences.json`, or `work/*/align.json` unless isolating a single word boundary defect.
   - **Never** read `scripts/*.py` during normal editing jobs. Use the 3-step CLI workflow and `plan.edl` schema below.
6. **Fresh Context Per Video**: Start each new video edit in a fresh conversation with unused MCP connectors turned off.
7. **Clean Editorial Style**: No B-roll, no unrequested captions, no CTAs, no decorative clutter. Only best takes, pause-snapped cuts, dynamic speaker layouts, and script-driven `✓` / `✗` marks + SFX.

---

## 2. Token Budget & Mandatory Reporting

- **Target**: **`12k–18k tokens` per minute of finished video** (`~20k–30k total` for a typical 1.5-min edit).
- **Strict Ceiling**: **`20k tokens / min`** of finished video.
- **Early Warning**: Warn the user mid-job if messy footage (excessive retakes, garbled audio, or missing lines) risks exceeding `20k tokens / min`.
- **Mandatory End-of-Job Table**: Always output the Token Usage table at the end of every job and append a row to `README.md`:

```markdown
| Date | Task | Context tokens (start → end) | Tokens used | 5-hour limit | Weekly limit |
|---|---|---|---|---|---|
| YYYY-MM-DD | **<Language> – "<Title>"** (<N> clips, <M> script lines): <MM:SS> final, <K> ✓/✗ marks → `~/Downloads/Zeno Outputs/<name>.mp4` | <start> → <end> | ~<delta> | <start>% → <end>% | <start>% → <end>% |
```

---

## 3. The 5-Stage Interactive Zeno Harness (`scripts/zeno.py`)

Zeno operates like a **Senior Human Video Editor** in 5 transparent stages with a **mandatory Pre-Render Yes/No Approval Gate** and **48–50s live backend progress updates**:

0. **Stage 0 — Edit Bay Readiness Check (On First Clone / Greeting)**:
   ```bash
   python3 scripts/zeno.py --check
   ```
   - Displays `[✓] Installed` vs `[✗] Remaining` across all 8 dependencies (`.venv`, FFmpeg, OpenCV/Pillow/SciPy, YuNet ONNX, `.env` key presence without reading secrets, Indic/UI fonts, SFX/Marks/Premiere `.prproj` shell, and `~/Downloads/Zeno Outputs/`).
1. **Stages 1–3 — Ingest, Transcribe, Match & Pitch (`1 command` + 50s Live Heartbeat)**:
   ```bash
   python3 scripts/zeno.py --inspect <raw_dir_or_clips> \
     --script <script_path_or_google_doc_url> \
     [--lang auto] [--preset auto|single|split_v|split_h|chat_bar|video_call|hybrid] \
     [--mark-pack flash|vignette|glossy|default]
   ```
   - **Stage 1 (Mapping)**: Scans raw clips, probes resolution/FPS/duration, and extracts 16kHz audio.
   - **Stage 2 (Transcribing)**: Transcribes word-level timestamps, scores `[BEST_TAKE]` vs `[ALT_TAKE]`, and checks speaker voices.
   - **Stage 3 (Matching & Editorial Pitch)**: Builds `work/<project>/brief.txt`, `work/<project>/plan.edl`, and `work/<project>/pitch_card.md`.
   - **Live 50s Chat Heartbeat**: When running `--inspect` or `--execute` in the background, schedule a `50s` timer (`schedule` with `DurationSeconds=50`) and read `python3 scripts/progress.py` (`work/<project>/progress.json`) so the user sees live stage updates every ~50 seconds in chat.
2. **Stage 3 Gate — Refine Plan & Ask Yes/No Before Rendering (`Mandatory Stop`)**:
   - Read **ONLY** `work/<project>/brief.txt`, `work/<project>/plan.edl`, and `work/<project>/pitch_card.md`.
   - Refine `plan.edl` using Agents 1–4 (text-only: script coverage, pause/retake cuts, zooms/layouts, transitions/2-pulse marks) and refresh the pitch card via `python3 scripts/zeno.py --pitch work/<project>/plan.edl`.
   - **Present the Editorial Pitch Card** (Concept & Style, Speakers, Raw Clips Mapped, Listening Video, Timeline Plan, Marks/SFX, and Deliverables) and **ask the user Yes/No (`ask_question`)** before starting Stage 4 rendering.
3. **Stages 4–5 — Render, Premiere `.prproj` Export & Deep QC (`1 command` after Yes)**:
   ```bash
   python3 scripts/zeno.py --execute work/<project>/plan.edl [--square] [--loudness -11.5] [--gain-db +2.0]
   ```
   - **Stage 4 (Editing & Rendering)**: Renders pause-snapped segments, dynamic layouts (`single`, `split_h`, `split_v`), stacked `"Stop Saying"` banners, transitions, and 2-pulse `✓`/`✗` flashes.
   - **Stage 5 (Mastering, `.prproj` & Deep QC)**: Normalizes master audio to `-11.5 LUFS` (`-1.0 dBTP` cap), exports `~/Downloads/Zeno Outputs/<name>.mp4` + `<name>_1x1.mp4` (`1:1` Square Meta Ad) + `<name>.timeline.txt` + **editable Adobe Premiere Pro project files** (`<name>.prproj` and `<name>.premiere.xml`), and runs `verify.py` + `marks_contact_sheet.jpg`.
   - *(Standalone CLI users can also run `python3 scripts/zeno.py <raw_dir> --script <url_or_path>` to run Stage 0 → Stages 1–3 → `[Y/n]` prompt → Stages 4–5 interactively in one command).*

---

## 4. Layout Modes, Pre-Stitched Split Screens & Swappable Mark Packs

- **Layouts (`--preset` or per-segment in `plan.edl`)**:
  - `single` — Full-frame speaker (`1080×1920`), `zoom=1.08` punch-in for jump cuts, `zoom=1.0->1.06` slow push, `zoom=1.08->1.0` pull-out, `flash_zoom`. **Also used for Google Flow pre-stitched vertical split-screen clips** (match straight as `single` with `zoom=1.0`; never double-composite with `split_h`/`split_v` when the split screen is already baked into the raw clip).
  - `split_v` — Top/bottom 50/50 split (`1080×960` each) when Character A and Character B are **separate raw files** (e.g., HeyGen separate renders).
  - `split_h` — Left/right 50/50 vertical split (`540×1920` each) when Character A and Character B are **separate raw files**.
  - `chat_bar` — Main speaker + glassmorphic prompt bar displaying the target phrase/question.
  - `video_call` — Main speaker + rounded Picture-in-Picture (PiP) card for the second speaker.
  - `hybrid` (`auto`) — Dynamically combines `single` for teaching monologues with `split_v` / `video_call` / `chat_bar` for dialogue turns.
- **Mark Packs (`--mark-pack` or `# mark_pack:` in `plan.edl`)**:
  - `flash` (**default**): Full-frame `1080×1920` subtle `0.50` alpha Red/Green PNG border overlays (`assets/marks/red_flash.png` & `green_flash.png`) that trigger **only at the end of the wrong/right sentence** and **flash 2 times (`0.56s`: Pulse 1 → OFF → Pulse 2)** with double-layered error SFX (`+4 dB` `buzzer.wav` + `kn20228_wrong_core.mp3`) and crisp right SFX (`+2.5 dB` `correct_ding.wav` + `sn_correct.wav`).
  - `vignette`: Full-frame radial glow (`assets/marks/w42_red_vignette.png` & `w42_green_vignette.png`, `0.50` alpha 2-pulse at sentence end).
  - `default` (flat circle badge), `glossy` (3D glass pill/badge), or custom PNGs in `assets/marks/<pack>/`.
- **Transitions & Zero-Audio-Loss Splices**:
  - Use `tr=warm_flash@0.38`, `tr=whip_left@0.28`, `tr=whip_right@0.28`, `tr=zoom_burst@0.28`, `tr=flash_zoom@0.28`, `tr=slide_up@0.28`, `tr=smoothleft@0.28`, `tr=smoothright@0.28` at scene/topic turns (backed by `tpad` video clone + `0.00s` audio concat so zero syllables are clipped).
- **Skill References & Links**:
  - Master Video Edit Skill: [`.agent/skills/video-edit/SKILL.md`](.agent/skills/video-edit/SKILL.md)
  - Macro / Micronarrative Style Skill: [`styles/micronarrative/SKILL.md`](styles/micronarrative/SKILL.md) (`spec.json`: [`styles/micronarrative/spec.json`](styles/micronarrative/spec.json))
  - Director Loop: [`agent/DIRECTOR.md`](agent/DIRECTOR.md)
  - Word-Boundary & Zero-Audio-Loss Guide: [`agent/concepts/word_boundary_training.md`](agent/concepts/word_boundary_training.md)

---

## 5. Mandatory 5-Agent Parallel Workflow & Post-Render Deep Analyzer (Agent 5)

For every editing task (running on **Antigravity** by default or **Claude Code**), assign at least **5 specialized agents**:

1. **Agent 1 — Script Coverage & Take Auditor**: Verifies 100% verbatim script coverage in order, speaker identity, and best-take selection across all raw clips.
2. **Agent 2 — Pause-Cut & De-Duplication Specialist**: Eliminates all repeated dialogues/words/false starts (both intra-clip retakes like `"Almost, almost"` and inter-clip boundary repeats) and tightens empty internal silences (`>0.85s` dead air) using 10ms WAV RMS energy boundaries.
3. **Agent 3 — Dynamic Zoom & Layout Choreographer**: Assigns punch-ins (`z=1.08`) on same-clip jump cuts to mask splices, slow push-ins (`z=1.0->1.06`), pull-outs (`z=1.08->1.0`), and speaker layouts.
4. **Agent 4 — Transitions, Rapid-Strobe Overlays & SFX Designer**: Places scene transitions (`smoothleft`, `smoothright`, `warm_flash`, `fade`) + custom `whoosh.wav` SFX (`-2 dB`), and configures full-frame rapid-strobe Red/Green PNG overlays (`mark_pack: flash` / `vignette`) with strong `+4 dB` error buzzer and `+2.5 dB` right ding SFX.
5. **Agent 5 — Post-Render Deep Video Analyzer & Auto-Fixer (Mandatory Post-Render Gate)**:
   - Runs **after** `render.py` generates the final MP4.
   - Uses local Python dependencies (`scripts/verify.py`, cached `words.json`/`hq_words.json` timestamps, FFmpeg `silencedetect`, 10ms WAV RMS energy, and RGB frame inspection — **without** sending the video to external APIs) to inspect the complete output video.
   - Verifies: (a) **0 repeated words/dialogues** inside or across segments, (b) **0 empty silences `>1.05s`** in the rendered audio stream, (c) **active transitions & `whoosh.wav` SFX**, and (d) **full-frame rapid-strobe Red/Green PNG overlays + strong error/right SFX**.
   - If any defect is detected, Agent 5 pinpoints the exact segment/timestamp, fixes `plan.edl`, and re-renders before delivery.
- **CPU & Thermal Guardrail**: Parallel subagents (`Agent 1`–`Agent 4`) must operate strictly on text artifacts (`brief.txt`, `plan.edl`, script files, and cached JSONs) and **must never spawn concurrent Python/OpenCV/Librosa/FFmpeg processes across raw `.mp4` files**. Heavy media commands (`prepare.py`, `render.py`, `verify.py`) run sequentially in the main process with `ZENO_MAX_THREADS=4` and lowered OS priority.

## 6. `plan.edl` Quick Syntax

```text
# output: output/Kannada_I_am_hungry.mp4
# canvas: 1080x1920@30
# preset: hybrid
# mark_pack: flash
# loudness: -11.5

# SEGMENTS: id | src | start-end | layout | zoom | transition | extras | note
L1 | S1 | 1.12-5.48 | single | sudden_in:1.08 | cut | focus=0.5,0.45 | Hook question
L2 | S2 | 0.85-4.90 | split_v:top=S1@5.48 | 1.0 | cut | active=bottom | Wrong student attempt
L3 | S3 | 1.05-6.20 | chat_bar | 1.08 | fade:0.3 | label="ನನಗೆ ಹಸಿವಾಗಿದೆ" | Teacher correction

# MARKS: type | segment | at | duration | pos | size | sfx | gain_db
wrong | L2 | 1.40 | 0.56 | top-right | 0.22 | buzzer.wav | +4
right | L3 | 2.10 | 0.56 | top-right | 0.22 | correct_ding.wav | +2.5

# SFX: file | segment | at | gain_db
whoosh.wav | L3 | 0.0 | -2
```

## 7. Developing the Zeno code itself (not editing a video)

Use `.venv/bin/python` (Python 3.12; or run `install.command` / `install.bat`). `scripts/zeno.py` re-execs itself into `.venv` automatically.

```bash
.venv/bin/python -m unittest tests.test_engine                                  # engine regression suite
.venv/bin/python scripts/setup.py --login                                       # zero-copy browser Google login -> saves .zeno_session.json
.venv/bin/python scripts/setup.py --smoke                                       # env check, never prints keys
.venv/bin/python scripts/eval_alignment.py --self-test                          # score word timing against gold benchmark
.venv/bin/python scripts/clean.py [--apply]                                     # free disk
python3 -m unittest service/test_keys.py service/test_service.py                # service & Google OAuth / learnings unit tests
python3 service/check_prompt_sync.py                                            # verify service & local Gemini prompt sync
```

## 8. Architecture (big picture)

**Director / Engine split.** A thin LLM "Director" (`agent/DIRECTOR.md` + `agent/concepts/*`) only reads small text files and edits `plan.edl`; everything measurable is Python in `scripts/`. `CLAUDE.md` and `AGENTS.md` are parallel manuals and must be kept in sync.

**Transcription & Editorial Learning Service** (`service/`, FastAPI on Railway): holds the only Gemini key and collects team editorial learnings. Team PCs authenticate once via zero-touch Google OAuth (`python3 scripts/setup.py --login` or `install.command`, which opens the browser for `@gosupernova.live` / `@gosupernova.line` sign-in and automatically saves `.zeno_session.json` + `ZENO_CLOUD_URL` / `ZENO_CLOUD_TOKEN` in `.env`, with legacy `SCRIPTCUT_CLOUD_*` fallback). `scripts/token_tracker.py` posts per-run telemetry, `plan.auto.edl -> plan.edl` diffs, and `brain_memory.json` rules to `/v1/telemetry`, and `python3 scripts/zeno.py --sync-learnings` syncs shared rules via `/v1/learnings`.

## Secrets (all agents)
Never open, print, quote or paste `.env`, `.zeno_session.json`, or any key in a chat or log. They hold the user's personal Zeno session token (or a Gemini key). To check setup, run `python3 scripts/setup.py --smoke` or `python3 scripts/setup.py --whoami`; they report [OK]/[FAIL] without showing keys. Read `.env.example` instead if you need the variable names.
