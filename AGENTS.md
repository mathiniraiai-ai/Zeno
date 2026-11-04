# AGENTS.md — Zeno (Antigravity Gemini 4 Argon & Dual-Agent Instructions)

Always-on operational rules for **Antigravity (Gemini 4 Argon)** and **Claude Code (Opus 4.5 / 5.5)** inside this Zeno folder (team PCs: `~/Documents/Zeno`; owner: `/Users/mathiniraiselvan/Documents/Office project/Zeno`).

**Agent entry point** (Antigravity, OpenAI and any other agent, not only Claude): read `agent/DIRECTOR.md` first. It sets the decision loop and points to one concept file per topic in `agent/concepts/`. Learned styles live in `styles/<name>/`. A style may allow captions or extra graphics only if its own `SKILL.md` says so; otherwise rule 4 below applies.

---

## 1. Hard Rules

1. **Strict Workspace Boundary**: Never modify, move, or delete any file outside this Zeno folder (team PCs: `~/Documents/Zeno`; owner: `/Users/mathiniraiselvan/Documents/Office project/Zeno`). Other projects in `Documents` are strictly off-limits.
2. **Folders**: Zeno runs from `~/Documents/Zeno` on team PCs (the installer puts it there). Every finished video (`.mp4` + `.timeline.txt`) goes to `~/Downloads/Zeno Outputs/`, never inside the repo. Raw clips can stay in any local folder: pass their path to `prepare.py`.
3. **Takes**: The agent chooses the take. `line_map.md` lists every clip stretch that says each line, `facts.md` has each word. Never assume the first, middle or last take is right, and never trust file names or file order: the script is the timeline.
4. **Speakers**: The agent sets `sp="<name>"` on every segment. `--check-plan` measures the voice in each cut against it and blocks `WRONG VOICE`: pick another take, and if no clip has the right voice, tell the user to reshoot. `voices not checked` means the voices could not be told apart: say so in the report. Reference samples in `input/voices/<Speaker>.wav` make this reliable with few clips.
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

## 3. The Job (`scripts/zeno.py`)

**Pre-job gate (mandatory, every job, every agent).** Do not start from a guess and do not start before the user has handed over the inputs:
1. Run `python3 scripts/zeno.py --preflight`. It runs live checks (FFmpeg, OpenCV face detection on a real frame, the wav2vec2 aligner, and a small WAV sent to the Zeno server, which checks its key store and Gemini). Show the user the PASS/FAIL table.
2. If every check passes, ask the user one question: "Share the script link (the Google Doc tab) and the footage folder." The footage must be a local folder or files; a Drive or web link is not downloaded, so ask them to download it first. A Google Doc link must end in `?tab=...`; never load a whole document.
3. Only after the user answers, run `python3 scripts/zeno.py --inspect <footage> --script <script>`. It repeats the live checks, then checks the script (spoken lines, scenes, strike-through text, layout notes) and every clip (readable, has audio) and prints them. If anything fails it stops with `[BLOCKED]` and exits with code 2; fix the listed problem with the user. Never use `--skip-preflight`.


Zeno operates like a **Senior Human Video Editor** in 5 transparent stages with a **mandatory Pre-Render Yes/No Approval Gate** and **48–50s live backend progress updates**:

**The job, step by step**
0. **Stage 0 — Edit Bay Readiness Check**: `python3 scripts/zeno.py --check` (prints `[✓] Installed` vs `[✗] Remaining` across all 8 dependencies without reading `.env` secrets).
1. **Measure (`1 command` + 50s Live Heartbeat)**:
   ```bash
   python3 scripts/zeno.py --inspect <raw_dir_or_clips> --script <script_path_or_google_doc_url> [--lang <lang>] [--style <name>] [--mark-pack default|glossy|flash|vignette]
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
   - Renders `~/Downloads/Zeno Outputs/<name>.mp4` + `<name>_1x1.mp4` (`1080×1080` Square Meta Ad) + `<name>.timeline.txt` + **editable Adobe Premiere Pro project files** (`<name>.prproj` and `<name>.premiere.xml`), repeats the plan check, verifies script order from the words in each cut, `-11.5 LUFS` (`-1.0 dBTP` cap) loudness, and generates `work/<project>/marks_contact_sheet.jpg`. Use `vol=+3dB` on any segment in `plan.edl` for exact mid-video dB boosts.

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
.venv/bin/python scripts/knowledge_sync.py pull|push|match "<wording>"                # knowledge kept in the server database (no commit or redeploy for new recipes)
.venv/bin/python scripts/format_library.py search "<script wording>"                  # look up how a format should look; `extract <video> --id ...` measures a reference locally, no tokens, video not kept
.venv/bin/python scripts/style_readiness.py [style] [--json]                    # measured readiness level (0-3) of a style from files and tests
.venv/bin/python scripts/clean.py [--apply]                                     # free disk
python3 -m unittest service/test_keys.py service/test_service.py                # service & Google OAuth / learnings unit tests
python3 service/check_prompt_sync.py                                            # verify service & local Gemini prompt sync
```

## 8. Architecture (big picture)

**Director / Engine split.** A thin LLM "Director" (`agent/DIRECTOR.md` + `agent/concepts/*`) only reads small text files and edits `plan.edl`; everything measurable is Python in `scripts/`. `CLAUDE.md` and `AGENTS.md` are parallel manuals and must be kept in sync.

**Transcription & Editorial Learning Service** (`service/`, FastAPI on Railway): holds the only Gemini key and collects team editorial learnings. Team PCs authenticate once via zero-touch Google OAuth (`python3 scripts/setup.py --login` or `install.command`, which opens the browser for `@gosupernova.live` sign-in (more domains only via `ZENO_ALLOWED_EMAIL_DOMAINS`) and automatically saves `.zeno_session.json` + `ZENO_CLOUD_URL` / `ZENO_CLOUD_TOKEN` in `.env`, with legacy `SCRIPTCUT_CLOUD_*` fallback). `scripts/token_tracker.py` posts per-run telemetry, `plan.auto.edl -> plan.edl` diffs, and `brain_memory.json` rules to `/v1/telemetry`, and `python3 scripts/zeno.py --sync-learnings` syncs shared rules via `/v1/learnings`.

## GPU use (all agents)
Zeno uses the computer's GPU by itself: the aligner runs on NVIDIA CUDA or the Apple GPU (MPS), and H.264 encoding uses VideoToolbox (Mac), NVENC / Quick Sync / AMF (Windows), each tested with a real encode and falling back to CPU (`libx264`) if it fails. `python3 scripts/gpu_install.py --status` and `setup.py --smoke` show what a computer uses. Force with `ZENO_DEVICE=cpu|cuda|mps` or `ZENO_ENCODER=cpu|h264_nvenc|...`. On Windows the installer (and the daily updater) switches to the CUDA build of torch when `nvidia-smi` finds a driver with CUDA 12.6 or newer; it never overwrites a CUDA torch with the CPU build.

## Caches & confirmations (all agents)
Never ask the user to clear caches and never delete them by hand (`rm -rf work/...`). Caches under `work/_clips/<stem>-<fingerprint>` refresh themselves: each folder records the engine version that made it (`engine_version.json`), a change to the transcript code (prompt, healing, lexicons) drops that clip's transcript and alignment, a change to the aligner drops only the alignment (the paid transcript stays), and a damaged file is rebuilt. Writes are atomic. Only if a result is wrong for a reason the code cannot see, run the job once with `--fresh`. The only confirmation in a job is the Yes/No before rendering; ask it once, in chat, and do not repeat it after the user says Yes. Disk space is handled by the daily background task (`clean.auto_clean`, only when free disk is under 20 GB); to do it by hand run `python3 scripts/clean.py` (dry run), show the total, ask once, then `python3 scripts/clean.py --apply` as one command.

## Verified status & open improvements (audit of 2026-11-04)
Checked and passing: all Python files compile; `tests.test_engine` (28), `service/test_keys.py` (60 checks), `service/test_service.py --no-live` (6), prompt sync, JSON validity, shell syntax, `zeno.py --preflight`. Do not treat an item as fixed until it is under "Fixed".

**Fixed**
- `zeno.py` imports (`--swap-layout`, `--swap-take`, `--error-sfx` no longer crash); a Google Doc link loads only its `?tab=` (a link with no tab is refused).
- Lexicons have no key with two different values (Tamil `டேக்ஸ்` is "takes"; "tax" is `டாக்ஸ்`).
- Script reading (`parse_script_file`, also used by `learn_reference.dialogue`): reference, visual, character-list, link and scene-change lines are directions, not dialogue; `Scene changes` / `Back to` / `Cut to` / `Flashback` start a scene and get a transition; `(X Sign and Text Strikethrough)` and `[TEXT: "..." | strike]` become struck-through banners; layout notes such as `floating window ... horizontal split screen` or `Horizontal Split screen Top: Robot | Bottom: Lady 1` set the layout and who sits where; emoji marks (`❌` wrong, `✅` right) become marks; `Speaker: : text`, hyphenated names (`Co-worker 3`) and one name per character (`Robot` = `Female Humanoid Robot`) are handled.
- `group_takes` uses the script: two clips that each fit a different script line are never takes of one line (a learner repeating the teacher's corrected sentence used to be pruned, which dropped the ✓).
- Service: name-only signup needs `ZENO_SIGNUP_CODE`; company keys are not admin unless listed in `ZENO_ADMIN_EMAILS` (or `ZENO_ADMIN_ALLOW_DOMAIN=1`); only `gosupernova.live` unless `ZENO_ALLOWED_EMAIL_DOMAINS` says otherwise; Google ID token for our client id only; a revoked key stays revoked after a restart. These need a Railway redeploy of `service/`.
- Listener rule in every split: the other panel shows the other character from their silent clip (matched by file name, else by looks), walked through turn after turn; panels are read from `Top: A | Bottom: B`, `Name - Left screen` and the character list; no silent clip means a quiet moment (or a held frame) with a `[LISTENER_FALLBACK]` alert; names that appear only in the panel note are merged with the speaker (`Robot` = `Female Humanoid Robot`).
- Windows with no Git / a ZIP-installed folder: the installers install Git if missing (winget) and run `auto_updater.py --connect-git`, which turns the folder into a normal clone of the private repository without touching any file (the repository is public, so no GitHub account or sign-in is needed); `update.bat`/`update.command` connect instead of stopping; the updater says why it cannot update (`git_missing`, `no_git_repo`) instead of failing silently, and its pull fails fast instead of hanging on a sign-in prompt. Untested on a real Windows PC until the tester reports.
- `--style <name>` writes `# style: <name>` into the plan (and survives quick tweaks), so jobs count toward a style's readiness level and the team's learning; the gate prints which style the job follows.
- Pre-job gate (`zeno.py --preflight`, enforced by `--inspect`), `style_readiness.py`, the format library (`styles/formats/`), per-clip engine versions in caches, `--fresh`, automatic low-disk cleanup, the server transcript cache and team format learning (observations, admin approval, `--sync-learnings`).
- micronarrative now has two references (`variants` in its spec.json): W42 (left/right split, warm flash) and W43 Hindi (top/bottom split with the seam at 0.472 of the height, fast blur whips and a glitch frame, one 91 s split-screen lesson).

**Open**
- micronarrative is level 1 (RENDER-ONLY) in `style_readiness.py`: nothing is proven on real raw footage, there is no hand-marked gold set, 6 unknowns are open (including whether the transition style is chosen per reel, per scene type or by the script).
- "Horizontal split screen" is read as a horizontal divider (top and bottom, `split_v`); change `SCRIPT_SPLIT_LAYOUT` in `prepare.py` if the team means the opposite (the W43 reference confirms top and bottom).
- Only the strike-through on-screen text cue is rendered; the general text format is undefined.
- The Railway service must be redeployed for `/v1/selftest`, the transcript cache, `/v1/observations` and the security fixes to take effect; no admin page yet for approving formats.
- The renderer's blur-whip transitions, caption colours and the W43 split seam position (0.472, not 0.5) are measured or seen but not yet reproduced by `layouts.py`.

**Improvements (planned)**
- Editor scorecard after every render: clipped words, longest pause, A/V offset, shot-length spread, transition count, loudness, text readability, script coverage.
- J-cuts and L-cuts (audio leads or trails the picture by 4-12 frames) and reaction holds read from the script dialogue.
- Cut-aware contact sheets with a short text caption per reference, stored in Postgres, for people to review.
- Daily per-computer preflight with a desktop notification on failure.
- Measure alignment on a hand-marked gold set with `eval_alignment.py` before changing any threshold; measure editing numbers on the user's best finished videos and confirm with A/B tests.
- Keep `CLAUDE.md` and `AGENTS.md` in sync with a check script; tests for `render.py` and `layouts.py`; CI on every push; test the Windows installer.

## Secrets (all agents)
Never open, print, quote or paste `.env`, `.zeno_session.json`, or any key in a chat or log. They hold the user's personal Zeno session token (or a Gemini key). To check setup, run `python3 scripts/setup.py --smoke` or `python3 scripts/setup.py --whoami`; they report [OK]/[FAIL] without showing keys. Read `.env.example` instead if you need the variable names.
