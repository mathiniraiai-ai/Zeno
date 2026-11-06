# AGENTS.md — Zeno (Antigravity Gemini 4 Argon & Dual-Agent Instructions)

Always-on operational rules for **Antigravity (Gemini 4 Argon)** and **Claude Code (Opus 5.5 / Sonnet 5.5)** inside this Zeno folder (team PCs: `~/Documents/Zeno`; owner: `/Users/mathiniraiselvan/Documents/Office project/Zeno`).

**Agent entry point** (Antigravity, OpenAI and any other agent, not only Claude): read `agent/DIRECTOR.md` first. It sets the decision loop and points to one concept file per topic in `agent/concepts/`. Learned styles live in `styles/<name>/`. A style may allow captions or extra graphics only if its own `SKILL.md` says so; otherwise rule 4 below applies.

---

## 1. Hard Rules

1. **Strict Workspace Boundary**: Never modify, move, or delete any file outside this Zeno folder (team PCs: `~/Documents/Zeno`; owner: `/Users/mathiniraiselvan/Documents/Office project/Zeno`). Other projects in `Documents` are strictly off-limits.
2. **Folders & Notion Naming**: Zeno runs from `~/Documents/Zeno` on team PCs (the installer puts it there). Never create folders with random or invented names: always ask the user for the **Notion name** and pass `--name "<Notion name>"`. Every edit gets its own folder `~/Downloads/Zeno Outputs/<Notion name>/` holding the 9:16 reel (`reel_<Notion name>.mp4` — `"reel"` before the name), the 1:1 square (`SQ_<Notion name>.mp4` — `"SQ"` before the name), the timeline, the Premiere files (`<Notion name>.prproj`, `<Notion name>.premiere.xml`), the script and the plan, never inside the repo. A second edit of the same name becomes `<Notion name> (2)`. Raw clips can stay in any local folder: pass their path to `prepare.py`.
3. **Takes**: The agent chooses the take. `line_map.md` lists every clip stretch that says each line, `facts.md` has each word. Never assume the first, middle or last take is right, and never trust file names or file order: the script is the timeline.
4. **Speakers**: The agent sets `sp="<name>"` on every segment. `--check-plan` measures the voice in each cut against it and blocks `WRONG VOICE`: pick another take, and if no clip has the right voice, tell the user to reshoot. `voices not checked` means the voices could not be told apart: say so in the report. Reference samples in `input/voices/<Speaker>.wav` make this reliable with few clips.
5. **Never Read Raw Word JSONs or Python Source**:
   - **Never** read `work/*/words.json`, `work/*/silences.json`, or `work/*/align.json` unless isolating a single word boundary defect.
   - **Never** read `scripts/*.py` during normal editing jobs. Use the 3-step CLI workflow and `plan.edl` schema below.
6. **Fresh Context Per Video**: Start each new video edit in a fresh conversation with unused MCP connectors turned off.
7. **Clean Editorial Style**: No B-roll, no unrequested captions, no CTAs, no decorative clutter. Only best takes, pause-snapped cuts, dynamic speaker layouts, and script-driven `✓` / `✗` marks + SFX.

---

## 2. Token Budget & Mandatory Reporting

- **Budget**: **`50k–70k tokens` per video** is the usual range; **quality comes first and it is not a hard stop**. Do not trim thinking, reading or checks to hit a number; remove only waste. Claude is the main brain and spends the tokens on editorial decisions; wav2vec2 (timing), the voice detector, the face/vision model and FFmpeg only measure and render.
- **Mid-Job Note**: If a job passes 70k, say so and why in the report; keep working to the same quality (no stop).
- **Per-edit count**: the token window starts at the pre-job checks and ends when the render finishes (`work/_job_marker.json`), so the report counts exactly one edit. Start every video in a fresh session so it does not include other work.
- **Mandatory End-of-Job Table**: Always output the Token Usage table at the end of every job and append a row to `README.md`:

```markdown
| Date | Task | Context tokens (start → end) | Tokens used | 5-hour limit | Weekly limit |
|---|---|---|---|---|---|
| YYYY-MM-DD | **<Language> – "<Title>"** (<N> clips, <M> script lines): <MM:SS> final, <K> ✓/✗ marks → `~/Downloads/Zeno Outputs/<name>.mp4` | <start> → <end> | ~<delta> | <start>% → <end>% | <start>% → <end>% |
```

---

## 3. The Job (`scripts/zeno.py`)

**Pre-job gate (mandatory, every job, every agent).** Do not start from a guess and do not start before the user has handed over the inputs:
1. Run `python3 scripts/zeno.py --preflight`. It runs live checks (FFmpeg, OpenCV face detection on a real frame, the wav2vec2 aligner, and a small WAV sent to the Zeno server, which checks its key store and Gemini). Show the user the PASS/FAIL table.
2. If every check passes, ask the user: "Share the script link (the Google Doc tab), the footage folder, and the Notion name for this video." The footage must be a local folder or files; a Drive or web link is not downloaded, so ask them to download it first. A Google Doc link must end in `?tab=...`; never load a whole document.
3. Only after the user answers, run `python3 scripts/zeno.py --inspect <footage> --script <script> --name "<Notion name>"`. It repeats the live checks, then checks the script (spoken lines, scenes, strike-through text, layout notes) and every clip (readable, has audio) and prints them. If anything fails it stops with `[BLOCKED]` and exits with code 2; fix the listed problem with the user. Never use `--skip-preflight`.


Zeno operates like a **Senior Human Video Editor** in 5 transparent stages with a **mandatory Pre-Render Yes/No Approval Gate** and **48–50s live backend progress updates**:

**The job, step by step**
0. **Stage 0 — Edit Bay Readiness Check**: `python3 scripts/zeno.py --check` (prints `[✓] Installed` vs `[✗] Remaining` across all 8 dependencies without reading `.env` secrets).
1. **Measure (`1 command` + 50s Live Heartbeat)**:
   ```bash
   python3 scripts/zeno.py --inspect <raw_dir_or_clips> --script <script_path_or_google_doc_url> [--lang <lang>] [--style <name>] [--name "<video name>"] [--mark-pack default|glossy|flash|vignette]
   ```
   - `--style <name>`: the style folder this job follows (for example `micronarrative`). Always pass it when the style is known: it writes `# style: <name>` into the plan so the job counts toward the style's readiness level and the team's learning.
   - `<lang>`: `en`, `kn`/`kannada`, `ta`/`tamil`, `hi`/`hindi`, `te`/`telugu`, `ml`/`malayalam`, or `auto`.
   - Writes `work/<project>/line_map.md`, `facts.md`, `clip_sheet_<n>.jpg`, and `plan.edl` with a header and **no segments**. Progress every ~48s (`work/<project>/progress.json` / `python3 scripts/progress.py`).
2. **Edit (the agent decides everything)**: follow `agent/EDIT_RULES.md`: read the line map, facts and clip sheets, write every segment of `plan.edl`, then run `python3 scripts/zeno.py --check-plan work/<project>/plan.edl` until it passes. It blocks lines out of script order, missing or said twice, cuts inside a word, and a voice that does not match `sp=`. A deliberate exception goes in `work/<project>/decisions.txt` (`accept L2 <reason>`).
3. **Stop and ask**: show the user the plan and ask Yes/No once before rendering.
4. **Render, Premiere `.prproj` Export & Verify (`1 command` after Yes)**:
   ```bash
   python3 scripts/zeno.py --execute work/<project>/plan.edl [--square] [--loudness -11.5] [--gain-db +2.0]
   ```
   - Renders `~/Downloads/Zeno Outputs/<name>/reel_<name>.mp4` (9:16 Reel) + `SQ_<name>.mp4` (`1080×1080` Square Meta Ad) + `reel_<name>.timeline.txt` + `<name>.script.txt` + `<name>.plan.edl` + **editable Adobe Premiere Pro project files** (`<name>.prproj` and `<name>.premiere.xml`), repeats the plan check, verifies script order from the words in each cut, `-11.5 LUFS` (`-1.0 dBTP` cap) loudness, and generates `work/<project>/marks_contact_sheet.jpg`. Use `vol=+3dB` on any segment in `plan.edl` for exact mid-video dB boosts.

## 4. Layout Modes, Pre-Stitched Split Screens & Swappable Mark Packs

- **Layouts (`--preset` or per-segment in `plan.edl`)**:
  - `single` — Full-frame speaker (`1080×1920`), `zoom=1.08` punch-in for jump cuts, `zoom=1.0->1.06` slow push, `zoom=1.08->1.0` pull-out, `flash_zoom`. **Also used for Google Flow pre-stitched vertical split-screen clips** (match straight as `single` with `zoom=1.0`; never double-composite with `split_h`/`split_v` when the split screen is already baked into the raw clip).
  - `split_v` — Top/bottom 50/50 split (`1080×960` each) when Character A and Character B are **separate raw files** (e.g., HeyGen separate renders).
  - `split_h` — Left/right 50/50 vertical split (`540×1920` each) when Character A and Character B are **separate raw files**.
  - `chat_bar` — Main speaker + glassmorphic prompt bar displaying the target phrase/question.
  - `video_call` — Main speaker + rounded Picture-in-Picture (PiP) card for the second speaker.
  - `hybrid` (`auto`) — Dynamically combines `single` for teaching monologues with `split_v` / `video_call` / `chat_bar` for dialogue turns.
- **Listener rule (every split: left/right or top/bottom).** While one character speaks, the OTHER panel shows the other character, silent and listening: left speaks -> right listens, right speaks -> left listens, and the same for top and bottom. Who sits where comes from the script (`Top: Robot | Bottom: Lady 1`, or `Woman - Right screen | Miss Nova - Left screen`, or "left screen" in the character list). The picture comes from that character's silent clip: put the character's name in the file name (`Robot_listening.mp4`, `Lady1_idle.mp4`); an unnamed silent clip is matched by how it looks (colours) and is left unassigned if unsure. Silent clips are walked through turn after turn, never restarted from the same frame. With no silent clip, a quiet mouth-closed moment of the character's own clip is used (a still frame if it is under 1 s) and the brief says `[LISTENER_FALLBACK]`; `[LISTENER_GUESS]` and `[LISTENER_MISSING]` mean a silent clip could not be tied to the character or does not exist. A character that lands in two panels is reported as `[PANEL_FLIP]`.

- **Mark Packs (`--mark-pack` or `# mark_pack:` in `plan.edl`)**:
  - `flash` (**default**): Full-frame `1080×1920` subtle `0.50` alpha Red/Green PNG border overlays (`assets/marks/red_flash.png` & `green_flash.png`) that trigger **only at the end of the wrong/right sentence** and **flash 2 times (`0.56s`: Pulse 1 → OFF → Pulse 2)** with single clean error SFX (`+4 dB` `sn_wrong.wav`) and crisp right SFX (`+2.5 dB` `correct_ding.wav` + `sn_correct.wav`).
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

## 5. One Director, Full Review

The agent makes all decisions in one pass, in the order given by `agent/EDIT_RULES.md`: timeline, takes, speakers, cuts, layouts, motion, transitions, marks, then a self-review as an editor. After rendering, `verify.py` checks the result locally (order, clipped words, pauses, loudness, marks, split rules). Read its output, look at `marks_contact_sheet.jpg`, fix `plan.edl` and render again if anything fails.

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
wrong | L2 | 1.40 | 0.56 | top-right | 0.22 | sn_wrong.wav | +4
right | L3 | 2.10 | 0.56 | top-right | 0.22 | correct_ding.wav | +2.5

# SFX: file | segment | at | gain_db
whoosh.wav | L3 | 0.0 | -2
```

## Developing Zeno itself
When the task is to change Zeno's own code (not to edit a video), read `DEVELOPING.md` first (test commands, architecture, the transcription service, verified status). Never read it during a video edit: it only costs tokens.

## GPU use (all agents)
Zeno uses the computer's GPU by itself: the aligner runs on NVIDIA CUDA or the Apple GPU (MPS), and H.264 encoding uses VideoToolbox (Mac), NVENC / Quick Sync / AMF (Windows), each tested with a real encode and falling back to CPU (`libx264`) if it fails. `python3 scripts/gpu_install.py --status` and `setup.py --smoke` show what a computer uses. Force with `ZENO_DEVICE=cpu|cuda|mps` or `ZENO_ENCODER=cpu|h264_nvenc|...`. On Windows the installer (and the daily updater) switches to the CUDA build of torch when `nvidia-smi` finds a driver with CUDA 12.6 or newer; it never overwrites a CUDA torch with the CPU build.

**Keep the computer cool.** The aligner used to be forced onto the CPU in `--inspect` (`get_shared_pipeline(device="cpu")`), which ran flat out and heated the Mac; it now follows the GPU choice above and falls back to the CPU for one clip only if the GPU errors. Every `zeno.py` job also runs at low priority: Mac Utility/Efficiency QoS (`taskpolicy`), Windows BELOW_NORMAL, Linux nice 10, with FFmpeg and PyTorch threads capped (`ZENO_MAX_THREADS`, default 4). While rendering, a Mac that `pmset -g therm` reports as slowed by heat waits up to 90 s before the next segment. `ZENO_COOL=0` turns the low priority off.

## Caches & confirmations (all agents)
Never ask the user to clear caches and never delete them by hand (`rm -rf work/...`). Caches under `work/_clips/<stem>-<fingerprint>` refresh themselves: each folder records the engine version that made it (`engine_version.json`), a change to the transcript code (prompt, healing, lexicons) drops that clip's transcript and alignment, a change to the aligner drops only the alignment (the paid transcript stays), and a damaged file is rebuilt. Writes are atomic. Only if a result is wrong for a reason the code cannot see, run the job once with `--fresh`. The only confirmation in a job is the Yes/No before rendering; ask it once, in chat, and do not repeat it after the user says Yes. Disk space is handled by the daily background task (`clean.auto_clean`, only when free disk is under 20 GB); to do it by hand run `python3 scripts/clean.py` (dry run), show the total, ask once, then `python3 scripts/clean.py --apply` as one command.

## Secrets (all agents)
Never open, print, quote or paste `.env`, `.zeno_session.json`, or any key in a chat or log. They hold the user's personal Zeno session token (or a Gemini key). To check setup, run `python3 scripts/setup.py --smoke` or `python3 scripts/setup.py --whoami`; they report [OK]/[FAIL] without showing keys. Read `.env.example` instead if you need the variable names.
