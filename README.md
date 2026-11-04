# Zeno

> **Zeno is a harness layer that lets any AI agent edit script-led short videos at about a quarter of the token cost** — for **Claude Code (Opus 4.5 / 5.5)** & **Antigravity (Gemini 4 Argon)** with **Meta Wav2Vec2 + `uroman` forced alignment**, **OpenCV visual tracking & motion analysis**, and **5 dynamic layouts**.

Give it raw footage and a script, and it returns a finished `1080×1920` (or `720×1280`) MP4:
- **Best takes only** — retakes, false starts, stutters, dead air, and trailing AI clip babble removed
- **Sub-word pause-snapped cuts** — powered by Meta wav2vec2 base (`95M`, MIT licence) forced alignment + Indic G2P + `uroman` fallback + asymmetric onset/coda acoustic protection
- **OpenCV Visual Tracking & Dynamic Zooms** — OpenCV face/subject tracking, static-shot motion detection, sub-pixel `flash_zoom` / `sudden_in` on hooks & brand endorsements, and Meta Creative transitions (`whip_left`, `whip_right`, `zoom_burst`, `flash_zoom`, `warm_flash`, `slide_up`)
- **5 Dynamic Speaker Layouts (+ `hybrid`)** — `single`, `split_v`, `split_h`, `chat_bar`, `video_call`, and `hybrid`
- **Swappable `✓` / `✗` mark packs + SFX** — minimal 2-pulse end-of-sentence Red/Green flash overlays and strong right/wrong SFX placed automatically from script cues
- **Claude edits, Zeno renders** — the agent makes every editing decision, at about `50k–70k tokens` per video

New team member? Start with [ONBOARDING.md](ONBOARDING.md).

No B-roll, no unrequested captions, no CTA, no extra visual clutter. Everything needed runs self-contained inside this folder without touching any other project.

---

## License

Zeno is licensed under the [Supernova Internal Use License](LICENSE). It may be used only by Supernova's Authorized Users for Supernova's own internal purposes. Redistribution, third-party use and offering it as a service are not permitted without written permission.

Third-party components keep their own licenses (see Section 7 of [LICENSE](LICENSE)). This project uses the universal romanizer software "uroman" written by Ulf Hermjakob, USC Information Sciences Institute (2015-2020).

---

## Folder layout

| Folder / File | What's in it | Committed to GitHub? |
|---|---|---|
| `input/footage/` | Raw input video clips (`S1.mp4`, `S2.mp4`, …) | Ignored (`*.mp4`, `.gitkeep` only) |
| `input/script/` | Script files (`.txt`, `.md`, or `.docx` — read with the standard library, tables supported; converted to `work/_script_cache/<name>.txt`), or saved from chat | Tracked or ignored per project |
| `output/` | Rendered final MP4 videos (`*.mp4`) + `*.timeline.txt` cut/effect logs | Ignored (`*.mp4`), `*.timeline.txt` optional |
| `work/` | Per-clip intermediate JSONs (`words.json`, `silences.json`, `info.json`), render cache, plus `line_map.md`, `facts.md`, clip sheets, `plan.edl`, `plan.json`, and `marks_contact_sheet.jpg`. Safe to delete anytime. | **Ignored** (ephemeral local scratch) |
| `scripts/` | Python pipeline tools (`setup.py`, `prepare.py`, `align_engine/`, `layouts.py`, `ui_generator.py`, `vision_tracker.py`, `edl_parser.py`, `render.py`, `verify.py`, `transcribe.py`, `script_intake.py`, `learn_reference.py`, `style_plan.py`, `clean.py`, `speaker_check.py`, `preproduce.py`, `motion.py`, `token_tracker.py`, `common.py`) | **Committed** |
| `assets/sfx/` | Sound effects — Right: `correct_ding.wav`, `sn_correct.wav`, `w42_right.wav`; Wrong: `buzzer.wav`, `sn_wrong.wav`, `w42_wrong.wav`; Transitions: `whoosh.wav`, `pop.wav` | **Committed** |
| `assets/transitions/` | Measured overlay transitions (`warm_flash.json`), applied over a hard cut by `render.py` | **Committed** |
| `assets/marks/` | Swappable `✓` / `✗` badge packs (`default`, `glossy`, `flash`, `vignette`) | **Committed** |
| `tools/` | Local FFmpeg / FFprobe binaries (`tools/ffmpeg/`) | **Ignored** (populated by `scripts/setup.py`) |
|  `agent/` | The Director (`DIRECTOR.md`, the one decision-making agent) and one `concepts/<topic>/CONCEPT.md` per topic | **Committed** |
| `styles/` | One folder per learned video style (`SKILL.md`, `spec.json`, `map.json`, `refs/`) | **Committed** |
| `CLAUDE.md` / `AGENTS.md` | Always-on low-token operational manuals for Claude Code & Antigravity | **Committed** |
| `.claude/commands/edit.md` | `/edit` slash command for 1-step execution in Claude Code | **Committed** |
| `.agent/` | Antigravity workspace rule (`rules/editor-workflow.md`) & skill (`skills/video-edit/SKILL.md`) | **Committed** |

---

## One-time setup (macOS Apple Silicon & Windows)

1. **Configure `.env`** (for Gemini 2.5 Flash Indic transcription fallback/assist):
   ```bash
   cp .env.example .env   # or create .env at project root
   # Add: GEMINI_API_KEY=your_api_key_here
   ```
2. **Run the setup bootstrapper**:
   ```bash
   python3 scripts/setup.py
   ```
   - Verifies/links native `ffmpeg` + `ffprobe` (ARM64 on Apple Silicon M2 Air or `tools/ffmpeg/`).
   - Generates all swappable `✓`/`✗` mark packs in `assets/marks/` (`default`, `glossy`, `flash`, `vignette`).
   - Prepares the forced-alignment environment (Indic G2P, `uroman` fallback, and the Meta wav2vec2 aligner).

### Windows (NVIDIA GPU)

Zeno runs the same on Windows. The aligner uses CUDA automatically when `torch.cuda.is_available()`, otherwise CPU (alignment is `<1s/clip` either way); rendering is FFmpeg `libx264` on CPU on both platforms.

```powershell
winget install Gyan.FFmpeg
py -3.12 -m venv .venv
.venv\Scripts\python -m pip install torch torchaudio --index-url https://download.pytorch.org/whl/<cuda-tag>   # pick <cuda-tag> (e.g. cu126) at pytorch.org
.venv\Scripts\python -m pip install -r requirements.txt
.venv\Scripts\python scripts\setup.py --prewarm   # downloads the wav2vec2 weights into models/torch/ (~360 MB, not committed)
```
Run the pipeline with `.venv\Scripts\python scripts\prepare.py ...` (the scripts also auto-detect `.venv\Scripts\python.exe`).

On Apple Silicon / macOS: `uv venv --python 3.12 .venv && uv pip install --python .venv/bin/python -r requirements.txt`.

---

## The 3-Step Low-Token Workflow

In Claude Code, simply type **`/edit`** (or follow these 3 steps manually in Claude Code or Antigravity):

### 1. Analyse & Auto-Plan (`1 command`)
```bash
python3 scripts/prepare.py input/footage/*.mp4 \
  --script input/script/<script>.txt \
  --lang <lang> \
  [--preset auto|single|split_v|split_h|chat_bar|video_call|hybrid] \
  [--mark-pack default|glossy|flash|vignette]
```
- Transcribes clips, aligns every word, measures pauses, faces and silent clips. It decides nothing. It writes:
  - `work/<project>/line_map.md` (the script, and which clip stretches say each line)
  - `work/<project>/facts.md` (every word with its time)
  - `work/<project>/clip_sheet_<n>.jpg` (pictures of every clip)
  - `work/<project>/plan.edl` (header only)

### 2. The agent edits
- Claude Opus or Sonnet makes every decision by `agent/EDIT_RULES.md` and writes `plan.edl`, about 50k to 70k tokens per video.
- `python3 scripts/zeno.py --check-plan work/<project>/plan.edl` must pass: script order, every line once, no cut inside a word, voices match.

### 3. Render & Verify (`1 command`)
```bash
python3 scripts/render.py work/<project>/plan.edl --verify
```
- Renders `output/<name>.mp4` (H.264 CRF 18, AAC 192 kbps, `12ms` edge fades, two-pass `-14 LUFS` normalization) and `output/<name>.timeline.txt`.
- Runs deterministic script-coverage & audio loudness checks (no second re-transcription pass needed).
- Generates `work/<project>/marks_contact_sheet.jpg` so the agent can visually verify all `✓`/`✗` marks in **a single image read**.

---

## Alignment Engine: Meta wav2vec2 (`95M`, MIT) + Indic G2P + `uroman` fallback

- **Transcript + Script Matching**: Uses the script text paired with Gemini Flash-Lite verbatim transcription (`scripts/align_engine/gemini_transcriber.py`) + `TranscriptionGuardian` loanword healing across Kannada, Tamil, Hindi, Telugu, Malayalam, and English.
- **Sub-Word Forced Alignment**: Normalizes Indic scripts via **Indic G2P + `uroman`** into Roman phonemes and runs **Meta's wav2vec2 base (95M) CTC aligner** (`scripts/align_engine/`). Boundary accuracy on real raw footage has not been measured yet.
- **Pause Snapping**: Every word boundary is cross-checked against detected acoustic silences and 10ms RMS energy minima so cuts always land naturally inside breath pauses.

---

## Dynamic Layout Modes & Swappable Mark Packs

### 5 Layout Modes (+ `hybrid`)
Pass `--preset <mode>` to `prepare.py`, set `# preset: <mode>` in `plan.edl`, or override per segment:

| Mode | Layout (`1080×1920`) | Best For |
|---|---|---|
| `single` | Full-frame vertical clip with `8–10%` punch-in (`zoom=1.08`) on jump cuts and `6%` slow pushes (`1.0->1.06`) | Monologues, hooks, teacher explanations |
| `split_v` | Top/bottom 50/50 split (`1080×960` each) with divider & subtle inactive-speaker dimming | Two-character Q&A or roleplay dialogues |
| `split_h` | Left/right 50/50 vertical split (`540×1920` each) with active speaker highlight | Fast back-and-forth exchanges |
| `chat_bar` | Full-frame speaker + frosted glassmorphic prompt bar displaying the target phrase/question | Language learning drills ("How to say X?") |
| `video_call` | Full-frame active speaker + rounded Picture-in-Picture (PiP) caller card | Remote call / FaceTime style conversations |
| `hybrid` | Mixes `single` on solo teaching lines with `split_v` / `video_call` / `chat_bar` on dialogue turns | **Default (`auto`)** — maximum visual variety |

### Swappable `✓` / `✗` Mark Packs
Pass `--mark-pack <pack>` or set `# mark_pack: <pack>` in `plan.edl`:
- `default` — Clean flat green `✓` and red `✗` badges (`assets/marks/right.png`, `wrong.png`)
- `glossy` — 3D glassmorphic gradient badges with specular sheen & drop shadow (`assets/marks/glossy/`)
- `flash` — Crisp badge + subtle green/red edge screen flash (`assets/marks/flash/`)
- `vignette` — Soft corner radial vignette glow + floating icon (`assets/marks/vignette/`)
- **Custom** — Drop any `right.png` and `wrong.png` into `assets/marks/<custom_pack>/`

---

## Token usage log

Measured with the Claude desktop app's usage reading before and after each task:
- **Context tokens** is how much the session's working context grew during the task. It's the closest figure available; the exact billed count isn't readable from inside the session.
- **Plan %** is the share of the 5-hour and weekly limits (Max plan) used during the task.

| Date | Task | Context tokens (start → end) | Tokens used | 5-hour limit | Weekly limit |
|---|---|---|---|---|---|
| 2026-10-29 | Workspace setup: tools, SFX, ✓/✗ badges, transcribe + render scripts, test render, this README | 115,913 → 156,611 | ~40,700 | 22% → 22% | 14% → 14% |
| 2026-10-29 | **Kannada – "I am hungry"** (17 clips, 21 script lines): 2:09 final, 4 ✓/✗ marks → `output/Kannada_I_am_hungry.mp4` | 159,619 → 232,685 | ~73,100 | 23% → limit reset mid-job → 0% | 14% → 14% |
| 2026-10-30 | **Zeno Harness Upgrade & GitHub Release (`github.com/mathiniraiai-ai/Zeno`)**: 5-agent build of Meta Wav2Vec2 + `uroman` aligner, 5 layout modes, swappable mark packs, `prepare.py` + `verify.py` (`<20k` token workflow), Whisper removal, and rebrand to **Zeno** | — | — | — | — |
| 2026-10-31 | **Kannada – "Grammar Correction with Miss Nova - I Am Hungry"** (17 clips, 21 character turns `L1..L21`): `02:06` final, 4 minimal 2-pulse end-of-sentence Red/Green flashes, 7 Meta Creative transitions (`whip_left`, `whip_right`, `zoom_burst`, `flash_zoom`, `warm_flash`, `slide_up`) + Whoosh SFX, OpenCV static-shot zooms + keyword/brand flash zooms, 0 clipped words → `~/Downloads/Zeno Outputs/Kannada_I_am_hungry_test.mp4` (**Antigravity · Gemini 4 Argon**) | `9,367` in / `5,455` out (**`14,822` agent total**; Gemini transcription separate: `22,204` in / `7,947` out / `30,151` total) | **14,822** (`~7,028 tok/min`) | N/A (Antigravity) | N/A (Antigravity) |
| 2026-11-01 | **Malayalam – "Micronarrative - Friends Ordering Food Abroad"** (18 clips, 20 script lines `L1..L20`, 21 segments): `01:42` final, `0` ✓/✗ marks, 2 `warm_flash` scene transitions, Google Flow pre-stitched vertical split-screen matched straight (`layout=single`, `zoom=1.0`), `-13.9 LUFS` → `~/Downloads/Zeno Outputs/Malayalam_Friends_Ordering_Food_Abroad.mp4` (**Antigravity · Gemini 4 Argon**) | `10,939` in / `6,578` out (**`17,517` agent total**; Gemini transcription separate: `24,110` in / `6,337` out / `30,447` total) | **17,517** (`~10,267 tok/min`) | N/A (Antigravity) | N/A (Antigravity) |
| 2026-11-02 | **Kannada – "21545 X Stop saying this selfie style - girl on scooter"** (5 clips, 9 script lines `L1..L9`, 16 segments): `01:22` final, 3 stacked "Stop Saying" strikethrough banners in Scene 1 (`layout=single` + rapid zooms/transitions/SFX), Scene 2 50/50 vertical split screen (`layout=split_h`, `zoom=1.0`, live moving listener video in both halves), `4` ✓/✗ marks, `-11.8 LUFS` → `~/Downloads/Zeno Outputs/Kannada_Stop_Saying_Girl_On_Scooter.mp4` & `Kannada_Stop_Saying_Girl_On_Scooter_1x1.mp4` (**Antigravity · Gemini 4 Argon**) | `14,820` in / `8,410` out (**`23,230` agent total**; Gemini transcription separate: `9,150` in / `13,398` out / `22,548` total) | **23,230** (`~17,018 tok/min`) | N/A (Antigravity) | N/A (Antigravity) |
| 2026-11-04 | **Malayalam – "Direct hook – AI English teacher test"** (4 clips, 31 script lines `L1..L31`, 43 segments): `02:03` final, `6` ✓/✗ flash marks, hook rapid zoom, 3 peaks (L9 mark, L23 pop, L30 flash_zoom), 3 transitions, split_h drill + full-frame teacher blocks, `-11.9 LUFS` → `~/Downloads/Zeno Outputs/gdoc_1K9F19D9zW6r_t_y538j0cf7sj3/` | not measured | ~95k estimated (14.6k of it Zeno transcription); over the 70k ceiling because preflight retries and long file reads came first | n/a | n/a |
