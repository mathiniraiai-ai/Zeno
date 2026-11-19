# Zeno (v2.0.0)

> **Script-led AI short-video editing suite for Claude Code, Codex, and Antigravity (Host-AI Direct, zero MCP).**  
> Powered by Meta `wav2vec2` forced alignment (`95M`, MIT) + Indic G2P + `uroman`, OpenCV/YuNet subject tracking, segregated format specs, HarfBuzz/Skia captions, FFmpeg compositing, and native desktop completion notifications.

Give **Zeno** one video or a multi-video queue of raw clips and scripts, and it produces clean, broadcast-ready exports delivered directly into `~/Downloads/Zeno Outs/<VideoName>/` (with automatic desktop sound and pop-up notifications):
- **Best takes in script order** — false starts, stumbles, retakes, dead air, and trailing AI clip chatter removed automatically
- **Pause-snapped sub-word cuts** — forced alignment across Kannada, Tamil, Telugu, Malayalam, Hindi, and English with 10ms RMS breath-pause snapping
- **9 Segregated Video Formats (+2 Style Wrappers)** — dedicated format cards and validation specs under `formats/` so only the matched format enters each project's `editing_packet.md`
- **Multi-Format Delivery** — `1080×1920` vertical (`9:16`), `1080×1080` square (`1:1`, 2-layer 5–10% Gaussian blur wrap of the `9:16` reel), editable Adobe Premiere `.prproj` + XML, `review_sheet.jpg`, plan, and script

---

## Setup & Readiness Check (macOS & Windows)

### One-Click Installers
- **macOS (Apple Silicon & Intel):** Double-click `install.command` (or run `bash install.command`). First launch on macOS 15+: *System Settings > Privacy & Security > Open Anyway*.
- **Windows (10/11):** Double-click `install.bat` (runs `scripts\install_windows.ps1`).

Both installers set up `uv`, Python 3.12 `.venv`, FFmpeg (`tools/ffmpeg/`, NVENC-capable on Windows), Node.js caption rendering (`subtitles/`), `.env` from `.env.example`, the `wav2vec2` alignment model (`models/torch`), and browser Google Sign-In (`@gosupernova.live`).

### Manual Sign-In & Readiness Verification

Use `.venv/bin/python` on macOS/Linux or `.venv\Scripts\python.exe` on Windows as `<PY>`:

```bash
# macOS / Linux
.venv/bin/python scripts/setup.py --login
.venv/bin/python scripts/zeno.py --check
.venv/bin/python scripts/zeno.py --preflight --compact
```

```powershell
# Windows
.venv\Scripts\python.exe scripts\setup.py --login
.venv\Scripts\python.exe scripts\zeno.py --check
.venv\Scripts\python.exe scripts\zeno.py --preflight --compact
```

---

## Electron Desktop App (`app/`) & Cloud OTA Auto-Updater

### Zeno Desktop App (`Zeno.app` / `Zeno.exe`)
Located in `app/`, the Electron companion monitors live job telemetry (`scripts/jobs.py`) across all 5 pipeline stages, face scans, speaker detections, cut renders, timeline plans, and output files:
- **Installers:** `Zeno-<version>-arm64.dmg` / `Zeno-<version>-x64.dmg` (macOS) and `Zeno Setup <version>.exe` (Windows). Signing in on a fresh computer automatically downloads Zeno into `~/Documents/Zeno` and triggers setup.
- **Dev build commands:** `cd app && npm install`, `npm start`, `npm run dist:mac`, `npm run dist:win`.

### Cloud OTA Auto-Updater (`update.command` / `update.bat`)
Client machines receive software updates directly from Zeno Cloud (`https://zeno-production-bef7.up.railway.app`) with zero Git or GitHub required:
- **One-click update:** Double-click `update.command` (macOS) or `update.bat` (Windows), or run `<PY> scripts/auto_updater.py`.
- **Desktop App Live Popup:** `Zeno.app` / `Zeno.exe` also checks Zeno Cloud automatically and offers one-click hot-reload updates without reinstalling the `.dmg` or `.exe`.
- **Safe Local Preservation:** Protected paths (`.env`, `.zeno_session.json`, `.zeno_owner_session.json`, `.zeno-dev`, `.claude/settings.local.json`, `.venv/`, `models/`, `tools/`, `work/`, `input/`, `output/`, `scratch/`, `service/`) are never overwritten or deleted.

---

## Zero-MCP Launchers (`edit*`)

Zeno v2.0.0 runs in pure **Host-AI Direct mode** with zero MCP servers (`.mcp.json` is `{}`, `.claude/settings.json` denies `mcp__*`, and `.codex/config.toml` disables external MCPs and non-editing plugins):

| Launcher | Platform | Purpose |
|---|---|---|
| `edit.command` | macOS | Minimal-cache Claude Code session (zero MCPs, `--tools "Bash"`, `--effort low`) |
| `edit-7k.command` | macOS | Strict 7.5K cache-read Claude Code session (zero MCP schemas, `--effort low`) |
| `edit-codex.command` | macOS | Lean OpenAI Codex session wired to `AGENTS.md` |
| `edit.bat` | Windows | Claude Code launcher (auto-bootstraps `.venv` via `scripts\install_windows.ps1` if missing) |
| `edit-codex.bat` | Windows | Codex launcher (auto-bootstraps `.venv` if missing) |

---

## Host-AI Direct Editing Workflows

Editors can drop **1, 2, or 3+ videos in one chat** without Yes/No prompts. Zeno processes queued videos **one by one sequentially** and delivers every finished folder to `~/Downloads/Zeno Outs/<VideoName>/`.

### 1. Single Video Workflow

1. **Inspect & Build Compact Packet**
   ```bash
   .venv/bin/python scripts/zeno.py "<footage_dir>" --script "<gdoc_tab_url_or_path>" --name "<VideoName>" --inspect --compact
   ```
   *(Windows: replace `.venv/bin/python` with `.venv\Scripts\python.exe`.)*
2. **Read Focused Evidence Once**  
   Read **only** `work/<project>/editing_packet.md` (or `editing_packet_p*.md` if chunked) and `work/<project>/clip_sheet_*.jpg` in one batch. Never open `formats/*`, `scripts/*`, `facts.md`, `line_map.md`, or `clip_caches.json`.
3. **Write Emotion Analysis & Edit Plan**  
   Create `work/<project>/script_analysis.md` (line 1: `Format: <id> (<title>)`) and `work/<project>/plan.edl` (plus `work/<project>/decisions.txt` only when documenting genuine unscripted gaps).
4. **Validate, Repair, Render & Deliver**
   ```bash
   .venv/bin/python scripts/zeno.py --execute work/<project>/plan.edl --square --compact
   ```
   Validates script order, word bounds, and speaker voices, snaps breath pauses, renders `9:16` + `1:1` + Premiere exports, runs Deep QC (`review_sheet.jpg`), moves deliverables to `~/Downloads/Zeno Outs/<VideoName>/`, and plays automatic desktop sound + pop-up notification. For any `[BLOCKED]` line, inspect targeted candidates with:
   ```bash
   .venv/bin/python scripts/edit_packet.py work/<project> --line <ID>
   ```
   instead of re-reading the full packet.

### 2. Multi-Video Sequential Queue (`2+` videos)

1. **Prepare All Packets Outside Main Chat**
   ```bash
   .venv/bin/python scripts/zeno.py --queue work/queue.json --inspect-only --compact
   ```
2. **Spawn 1 Isolated Subagent Per Queued Video**  
   Each subagent reads only its `work/<project>/editing_packet.md` + `work/<project>/clip_sheet_*.jpg`, writes `script_analysis.md` + `plan.edl`, resolves any `[BLOCKED]` lines via `scripts/edit_packet.py work/<project> --line <ID>`, and runs `--execute work/<project>/plan.edl --square --compact` sequentially — returning only `~/Downloads/Zeno Outs/<VideoName>/` + status so packets and images never accumulate in the main chat.

---

## Deliverables (`~/Downloads/Zeno Outs/<VideoName>/`)

Every completed job is delivered as a self-contained folder under `~/Downloads/Zeno Outs/<VideoName>/` (override root via `ZENO_OUTPUT_DIR` in `.env`), accompanied by an automatic cross-platform desktop chime and floating notification toast (`scripts/notify.py` / `scripts/notify_popup.py`, or delegated cleanly to the Electron app when open):

| File | Description |
|---|---|
| `reel_<VideoName>.mp4` | Final `1080×1920` (`9:16`) vertical reel with captions, layouts, marks, transitions, and SFX |
| `SQ_<VideoName>.mp4` | Final `1080×1080` (`1:1`) square export (2-layer 5–10% Gaussian blur wrap of the `9:16` reel) |
| `<VideoName>.prproj` / `.xml` | Editable Adobe Premiere Pro project and FCP7 XML timeline |
| `review_sheet.jpg` | Automated visual QC contact sheet across `9:16` and `1:1` renders |
| `<VideoName>.plan.edl` | Verified edit decision list (`plan.edl`) |
| `<VideoName>.script.txt` | Matched clean script text |

---

## The 9 Segregated Formats (`formats/`)

Format definitions, machine-checked transition specs, and layout recipes are cleanly segregated inside `formats/` (`formats/catalog/`, `formats/specs/`, `formats/layouts/`):

| # | Format ID | Title | Primary Layouts | Editorial Pattern |
|---|---|---|---|---|
| 1 | `skit` | Skit-based micro-narrative | `single`, `split_h`, `split_v`, `top_pip` | Live multi-scene hook & speaker-follow cuts into a locked `z=1` AI teacher drill |
| 2 | `direct_hook` | Direct-hook micro-narrative | `single`, `split_h`, `split_v` | Direct-to-camera selfie hook with punch-in, then `split_reveal` into a still teacher split |
| 3 | `street_interview` | Street-interview micro-narrative | `single`, `split_h`, `split_v` | Handheld mic interview hard cuts, bridge reveal, and locked `z=1` Miss Nova split |
| 4 | `stop_saying` | Stop saying / Never say | `single`, `split_h`, `split_v` | Stacked red-`✗` strikethrough phrase banners on hook, split drill with green `✓`, return to scene 1 |
| 5 | `live_conversation` | Live conversation / hologram / tablet | `single`, `video_call` | Shared-frame human + AI teacher or screen call; `1.08` punch-ins hide pauses, transitions only on location changes |
| 6 | `split_screen` | Full-video split screen | `split_h`, `split_v`, `video_call`, `chat_bar` | Two characters across the entire reel at `z=1` with 14% headroom and advancing silent listener clips |
| 7 | `scrolling_text` | Scrolling dialogue page | `split_h` + `#page scroll` | Two still speaker panels above a blue rule and live word-highlighted scrolling dialogue page (replaces `#subs`) |
| 8 | `translate_drill` | How would you translate / flashcard | `single`, `chat_bar` | Flashcard banners synced to each mother-tongue -> English prompt, alternating `z=1.0` and `1.0->1.06` pushes |
| 9 | `story_pitch` | Story-based direct pitch | `single` | Narrative monologue with `dissolve` flashback entries/exits and `1.08` pause-masking punch-ins |

> Generation-style wrappers (`realistic_to_3d`, `3d_to_2d`) inherit the editorial pattern of the underlying script format above.

---

## Editorial Rules, Layouts, Captions & Mark Packs

- **Layouts & Framing:** `single` (`1.08` jump-cut punch-ins on `>0.25s` pauses, `1.0->1.06` slow pushes on `>=2.5s` turns), `split_h` (`540×1920` left/right), `split_v` (`1080×960` top/bottom), `top_pip`, `video_call`, `chat_bar`, and `#page scroll`. All splits lock at `z=1` with `14%` headroom and advancing silent listener clips (`peer=`).
- **Transitions:** Purposeful, story-driven transitions (`+1-2` tasteful beats per reel on real scene/location/act shifts; dialogue stays crisp hard cuts; zero intra-split, same-shot, or mid-line transitions).
- **Captions & CTA Safety:** Default `#subs auto y=68` (`54px` single row at `1080w`; `y=50` on `split_v`). `#page scroll` replaces `#subs`, banners, marks, and SFX. Keep bottom `20-25%` clean (never burn in fake `"Install Now"` CTA buttons).
- **1:1 Square Export (`--square`):** Always derived directly from the completed `9:16` reel (2-layer 5–10% Gaussian blur wrap; no separate `1:1` images, flashcards, timestamps, or subtitles).
- **Mark Packs & Audio:** Script-cued `✓` / `✗` badges (`flash`, `vignette`, `glossy`, `default`) paired with calibrated SFX (`assets/sfx/`, packs `default`, `supernova`, `kn20228`, `w42`), zero continuous BGM by default, mastered to `-10.5 LUFS`.

---

## Folder Layout

| Path | Purpose |
|---|---|
| `scripts/` | Core Python pipeline (`zeno.py`, `prepare.py`, `align_engine/`, `render.py`, `verify.py`, `deliver.py`, `notify.py`, `notify_popup.py`, `edit_packet.py`, `edl_parser.py`, `auto_updater.py`, `setup.py`, etc.) |
| `formats/` | Segregated format cards (`catalog/`), machine-checked specs (`specs/`), and layout recipes (`layouts/`) |
| `subtitles/` | HarfBuzz/Skia single-row subtitle, flashcard & scrolling-page renderer + bundled Indic/Latin fonts |
| `assets/` | Reusable `✓`/`✗` mark packs (`marks/`), SFX + completion chime (`sfx/`), UI toast (`ui/`), overlays, templates, transitions, and Premiere `.prproj` base shell |
| `app/` | Electron desktop companion (`Zeno.app` / `Zeno.exe`) with live pipeline view & Cloud OTA auto-updates |
| `models/` | YuNet ONNX face detector (`face_detection_yunet_2023mar.onnx`) and local `wav2vec2` weights |
| `tools/` | Local `ffmpeg` and `ffprobe` binaries |
| `input/` | Optional local staging folders (`footage/`, `script/`, `voices/`, `standins/`) |
| `work/` | Per-project working folders (`editing_packet.md`, `clip_sheet_*.jpg`, `script_analysis.md`, `plan.edl`, reusable clip caches) |
| `output/` | Temporary staging directory prior to automatic delivery to `~/Downloads/Zeno Outs/<VideoName>/` |
| `AGENTS.md` / `CLAUDE.md` | Identical operational instructions for Codex, Antigravity, and Claude Code |
| `.agent/` / `.claude/` / `.codex/` | Workspace skill (`zeno`), always-on rule (`zeno.md`), `/zeno` & `/edit` commands, and zero-MCP configs |
| `install.*` / `update.*` / `edit*` | Cross-platform one-click installers, Cloud OTA updaters, and zero-MCP AI launchers |

---

## License

**Zeno** is licensed under the **Supernova Internal Use License** for authorized internal use only. Third-party components retain their respective licenses, including the universal romanizer `uroman` by Ulf Hermjakob, USC Information Sciences Institute (2015–2020).
