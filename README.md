# Zeno

> **Zeno lets an AI agent edit script-led short videos while Python handles transcription, timing, validation and rendering.** Works with Codex, Claude Code and Antigravity; uses Meta wav2vec2, OpenCV and FFmpeg.

Give it raw footage and a script, and it returns a finished `1080×1920` (or `720×1280`) MP4:
- **Best takes only** — retakes, false starts, stutters, dead air, and trailing AI clip babble removed
- **Sub-word pause-snapped cuts** — powered by Meta wav2vec2 base (`95M`, MIT licence) forced alignment + Indic G2P + `uroman` fallback + asymmetric onset/coda acoustic protection
- **OpenCV Visual Tracking & Dynamic Zooms** — OpenCV face/subject tracking, static-shot motion detection, sub-pixel `flash_zoom` / `sudden_in` on hooks & brand endorsements, and Meta Creative transitions (`whip_left`, `whip_right`, `zoom_burst`, `flash_zoom`, `warm_flash`, `slide_up`)
- **5 Dynamic Speaker Layouts (+ `hybrid`)** — `single`, `split_v`, `split_h`, `chat_bar`, `video_call`, and `hybrid`
- **Swappable `✓` / `✗` mark packs + SFX** — minimal 2-pulse end-of-sentence Red/Green flash overlays and strong right/wrong SFX placed automatically from script cues
- **The agent edits, Zeno renders** — a compact job brief supports editorial decisions; measured usage is reported separately from cost estimates.

New team member? Start with [ONBOARDING.md](ONBOARDING.md).

Animated single-row captions are on by default; scrolling-text pages replace them. No unrequested B-roll, CTA or visual clutter. Everything needed runs self-contained inside this folder without touching any other project.

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
| `work/` | Per-clip intermediate JSONs (`words.json`, `silences.json`, `info.json`), render cache, plus `line_map.md`, `facts.md`, clip sheets, `plan.edl`, `plan.json`, and `marks_contact_sheet.jpg`. Preserve these caches and records; use the supported cleanup command when needed. | **Ignored** (local job records and reusable caches) |
| `scripts/` | Python pipeline tools (`setup.py`, `prepare.py`, `align_engine/`, `layouts.py`, `ui_generator.py`, `vision_tracker.py`, `edl_parser.py`, `render.py`, `verify.py`, `transcribe.py`, `script_intake.py`, `learn_reference.py`, `style_plan.py`, `clean.py`, `speaker_check.py`, `preproduce.py`, `motion.py`, `token_tracker.py`, `common.py`) | **Committed** |
| `assets/sfx/` | Sound effects — Right: `correct_ding.wav`, `sn_correct.wav`, `w42_right.wav`; Wrong: `buzzer.wav`, `sn_wrong.wav`, `w42_wrong.wav`; Transitions: `whoosh.wav`, `pop.wav` | **Committed** |
| `assets/transitions/` | Measured overlay transitions (`warm_flash.json`), applied over a hard cut by `render.py` | **Committed** |
| `assets/marks/` | Swappable `✓` / `✗` badge packs (`default`, `glossy`, `flash`, `vignette`) | **Committed** |
| `tools/` | Local FFmpeg / FFprobe binaries (`tools/ffmpeg/`) | **Ignored** (populated by `scripts/setup.py`) |
|  `agent/` | The Director (`DIRECTOR.md`, the one decision-making agent) and one `concepts/<topic>/CONCEPT.md` per topic | **Committed** |
| `styles/` | One folder per learned video style (`SKILL.md`, `spec.json`, `map.json`, `refs/`) | **Committed** |
| `CLAUDE.md` / `AGENTS.md` | Always-on low-token operational manuals for Claude Code & Antigravity | **Committed** |
| `.claude/commands/zeno.md` | `/zeno` slash command (`/edit` still works) for 1-step execution in Claude Code | **Committed** |
| `.agent/` | Antigravity workspace rule (`rules/editor-workflow.md`) & skill (`skills/video-edit/SKILL.md`) | **Committed** |

---

## Setup and fresh editing chats

Run `bash install.command` on Mac, or `install.bat` on Windows. The installer creates `.venv`, installs the dependencies and handles sign-in. Do not paste keys into chat. Commands below use `.venv/bin/python`; on Windows use `.venv\Scripts\python.exe`.

For a new Codex edit, open `edit-codex.command` (Mac) or `edit-codex.bat` (Windows). This opens a fresh CLI chat with configured connectors disabled for that launch. Your global settings and existing chats are unchanged; managed workspace tools may remain. `scripts/launch_edit.py --dry-run` previews the launch without a model call. Claude users can use `edit.command` / `edit.bat`.

## Editing workflow

1. Supply the local footage folder, the Google Doc's specific `?tab=...` link (or local script), and the exact Notion name. If anything is missing, run `--preflight --compact` first and request only the missing input.
2. Inspect with live checks:

   ```bash
   .venv/bin/python scripts/zeno.py --inspect "<footage>" --script "<script tab>" --name "<Notion name>" --compact
   ```

   Add `--style <name>` when following a known learned style. Read the generated `brief.md`, `line_map.md`, `facts.md` and clip sheets. Reusable transcripts and alignment are kept automatically.
3. Write the analysis and complete `plan.edl`; run `.venv/bin/python scripts/zeno.py --check-plan work/<project>/plan.edl`. Resolve failed checks and review pacing/style notes. Show the plan and get Yes/No once before rendering.
4. Render and verify:

   ```bash
   .venv/bin/python scripts/zeno.py --execute work/<project>/plan.edl --square --compact
   ```

   Deliverables go to `~/Downloads/Zeno Outputs/<Notion name>/`: the vertical reel, square export, timeline, editable Premiere project/XML, script and plan. Review verification and contact sheets before delivery.

`--compact` keeps the complete diagnostic log under `work/_logs/` and prints stage changes, checks and failures. Live desktop progress continues locally; the agent need not reread progress files every few seconds.

## Token and cost controls

- `AGENTS.md` and `CLAUDE.md` share one short set of rules. Each generated brief selects the relevant editing sections; references are read only as needed.
- Codex usage is tied to the current chat and job window. Reports separate uncached input, cache reads, cache writes and output. A cached transcript is recorded as reused, not charged as a new transcription.
- Read a saved report with `.venv/bin/python scripts/token_tracker.py --project <project>`. This does not rerun accounting or upload telemetry. `--refresh` explicitly rebuilds the report for its saved window.
- INR estimates use versioned model rates, a dated exchange rate and an explicit speed assumption. They are API-equivalent estimates, not subscription invoices. Unknown model prices remain unavailable. Set `ZENO_USD_INR` or `ZENO_COST_SPEED=standard|fast|ultrafast` when needed.
- 50k–70k is historical guidance, not a token target. Actual savings need comparison across equivalent fresh-session edits; smaller rule files alone do not prove a matching reduction in the final bill.

See [DEVELOPING.md](DEVELOPING.md) for the regression suite and development workflow.

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
- **Context tokens** describes the request context size, not total spending. New reports use provider usage counters where available, separate cache reads and identify estimates or unavailable measurements. Historical rows retain their original measurement basis.
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
| 2026-11-04 | **Hindi – "Live Conversation - I rest in the bed (Miss Nova Giant Phone)"** (15 clips `a1`–`a14`, 17 spoken script lines `L5..L21`, 44 segments): `01:53` final, `2` ✓/✗ flash marks, 2 `whip_left` transitions + Whoosh/Punch/Pop/Ding/Click SFX, emotion-driven rapid/slow zooms (`sudden_in:1.08`, `flash_zoom:1.12`, `1->1.05`, `1.06->1`), `-12.1 LUFS` → `~/Downloads/Zeno Outputs/gdoc_1AJBZ1gIXzKA_t_p0vln8ebbgbx/gdoc_1AJBZ1gIXzKA_t_p0vln8ebbgbx.mp4` & `_1x1.mp4` (**Antigravity · Gemini 4 Argon**) | `18,920` in / `9,480` out (**`28,400` agent total**; Gemini transcription separate: `16,838` in / `8,611` out / `25,449` total) | **~28,400** (`~15,040 tok/min`) | N/A (Antigravity) | N/A (Antigravity) |
| 2026-11-05 | **Kannada – "W45 - Kannada - AI Only - Micronarrative - Never say I am fine thank you - Court Room Judge - Replication with New Script - Sandeep - Xs - Balesh"** (10 clips, 29 script lines `L1..L29`, 29 segments): `01:41` final, `4` ✓/✗ flash marks (`0.70s` post-dialogue hold + `sn_wrong.wav` / `correct_ding.wav`), 2 stacked ❌ strikethrough banners on `L6a`, `split_v` Top/Bottom (`z=1.0` locked, `14.0%` common headroom, silent listener gaps mapped), 4 transitions (`whip_left`, `warm_flash`, `smoothright`) + Punch/Whoosh/Pop/Ding/Click SFX, `-12.4 LUFS` → `~/Downloads/Zeno Outputs/W45 - Kannada - AI Only - Micronarrative - Never say I am fine thank you - Court Room Judge - Replication with New Script - Sandeep - Xs - Balesh/reel_...mp4` & `SQ_...mp4` (**Antigravity · Gemini 4 Argon**) | `17,683` in / `11,802` out (**`29,485` agent total**; Gemini transcription separate: `14,860` in / `4,727` out / `19,587` total) | **29,485** (`~17,436 tok/min`) | N/A (Antigravity) | N/A (Antigravity) |
| 2026-11-05 | **Kannada – "W45 - Kannada - AI Only - Skit Micro Narrative - tender coconut seller speaks English to foreigner - Replication with New Script - Disha - Xs - Balesh"** (11 clips, 27 script lines `L1..L27`, 32 segments): `01:36` final, `0` ✓/✗ marks, 4-clip Left/Right `split_h` demo (Miss Nova Left `1.213x`, Young Lady Right `1.30x`, canonical `SFV` crop-lock across `DFV`/`SFV` for `0.0px` background jump, matched eye level `515px` & face size), Scene 1 internal pauses surgically cut (`S1, S2, S4, S5, S6`), 4 transitions (`whip_left`, `warm_flash`, `smoothright`) + Punch/Whoosh/Pop/Ding/Click SFX, `-11.8 LUFS` → `~/Downloads/Zeno Outputs/W45 - Kannada - AI Only - Skit Micro Narrative - tender coconut seller speaks English to foreigner - Replication with New Script - Disha - Xs - Balesh/reel_...mp4` & `SQ_...mp4` (**Antigravity · Gemini 4 Argon**) | `18,410` in / `9,120` out (**`27,530` agent total**; `0` re-transcription tokens) | **27,530** (`~17,134 tok/min`) | N/A (Antigravity) | N/A (Antigravity) |
| 2026-11-05 | **Kannada – "W45 - Kannada - AI Only - Skit Micro Narrative - tender coconut seller speaks English to foreigner - Replication with New Script - Disha - Xs - Balesh"** (14 clips, 20 script lines, 29 segments): `02:13` final, 0 ✓/✗ marks (script has none) → `~/Downloads/Zeno Outputs/W45 - Kannada - AI Only - Skit Micro Narrative - tender coconut seller speaks English to foreigner - Replication with New Script - Disha - Xs - Balesh/` | n/a | ~315k Zeno ledger (counts every call's full context; Gemini transcription 36k separate); my own estimate ~60k | n/a | n/a |
| 2026-11-05 | **Kannada – "W45 - Kannada - AI Only - Micronarrative - Manager insults Employee - watch how I impress him in 30 days - Replication with New Script - Disha - Xs - Balesh"** (13 clips, 30 script lines `L1..L30`, 43 segments): `02:28` final, `1` ✓ flash mark (`L16`), Scene 1 B&W insult (`v1, v2`) + 4th-wall colour freeze (`v4, v5`), Scene 2 "30 Days Later" colour burst (`v6–v9`), Scene 3 4-clip Top/Bottom `split_v` (`Top = Robot`, `Bottom = Kavya/Lady`, `0.0px` background jump across `split v1`/`silent v2` & `split v2`/`silent v1`), 3 transitions (`whip_left` ×2, `warm_flash`) + Punch/Whoosh/Pop/Ding/Click SFX, `-12.2 LUFS` → `~/Downloads/Zeno Outputs/W45 - Kannada - AI Only - Micronarrative - Manager insults Employee - watch how I impress him in 30 days - Replication with New Script - Disha - Xs - Balesh/reel_...mp4` & `SQ_...mp4` (**Antigravity · Gemini 4 Argon**) | `19,420` in / `9,840` out (**`29,260` agent total**; Gemini transcription separate: `17,863` in / `5,622` out / `23,485` total) | **29,260** (`~11,814 tok/min`) | N/A (Antigravity) | N/A (Antigravity) |
| 2026-11-05 | **Kannada – "W45 - Kannada - AI Only - Skit - Husband embarrased of wife - Just shut up and eat - Replication with New Script - Sandeep - Xs - Balesh"** (18 clips, 26 script lines `L1..L26`, 53 segments): `02:06` final, `0` marks, 5-scene full-frame `layout=single` corporate party skit (`V1–V7`, `v8–v17`, `last video.mp4`), 21 rapid zooms (`16` `sudden_in:1.08` + `5` `sudden_out:1.08`), 16 slow zooms (`1->1.05`, `1->1.06`, `1.08->1`), 16 still cuts (`30.2%`), 2 transitions (`whip_right@0.28`, `warm_flash@0.35`) + 24 Punch/Whoosh/Pop/Ding/Click SFX, `-11.8 LUFS` → `~/Downloads/Zeno Outputs/W45 - Kannada - AI Only - Skit - Husband embarrased of wife - Just shut up and eat - Replication with New Script - Sandeep - Xs - Balesh/reel_...mp4` & `SQ_...mp4` (**Antigravity · Gemini 4 Argon**) | `21,150` in / `10,480` out (**`31,630` agent total**; Gemini transcription separate: `27,941` in / `7,806` out / `35,747` total) | **31,630** (`~15,062 tok/min`) | N/A (Antigravity) | N/A (Antigravity) |
| 2026-11-05 | **Kannada – "W45 - Kannada - AI Only - Live conversation - you made a mess - Replication from Telugu - Sandeep - Xs - Abhishek"** (12 clips, 17 script lines, 14 spoken): 01:31 final, 22 cuts, 0 marks, 3 peaks → `~/Downloads/Zeno Outputs/W45 - Kannada - AI Only - Live conversation - you made a mess - Replication from Telugu - Sandeep - Xs - Abhishek/` | n/a | ~50k (my estimate) + 16,438 Gemini transcription (fresh run); no agent ledger in this telemetry | n/a | n/a |
| 2026-11-05 | **Kannada – "W45 - Kannada - AI Only - Whiteboard translation - Robot - food - Competitor Replication Lingo AI - Disha - Xs - Abhishek"** (8 clips `S1`–`S8`, 14 script lines `L1..L14`, 23 segments): `01:07.8` final, `0` ✓/✗ marks, 12 timed whiteboard vocabulary overlays (`ಊಟ/Food`, `ನೀರು/Water`, `ಮನೆ/House`, `ನಾಯಿ/Dog`, `ಕಾರು/Car`, `ಸ್ನೇಹಿತ/Friend` — HarfBuzz-shaped yellow Kannada top words + bold black English bottom cards locked to the classroom whiteboard matching `ref_video.mp4`), 6 rapid zooms (`4` `sudden_in`, `2` `sudden_out`, `1` `flash_zoom:1.08`), 6 smooth zooms (`1->1.06`, `1->1.05`, `1.06->1`), 2 transitions (`whip_left@0.25`, `warm_flash@0.30`) + 20 Pop/Ding/Whoosh/Click/Riser SFX, `-11.8 LUFS` → `~/Downloads/Zeno Outputs/W45 - Kannada - AI Only - Whiteboard translation - Robot - food - Competitor Replication Lingo AI - Disha - Xs - Abhishek/reel_...mp4` & `SQ_...mp4` (**Antigravity · Gemini 4 Argon**) | `16,840` in / `8,920` out (**`25,760` agent total**; Gemini transcription separate: `9,279` in / `4,064` out / `13,343` total) | **25,760** (`~22,796 tok/min`) | N/A (Antigravity) | N/A (Antigravity) |
| 2026-11-05 | **Telugu – "W45 - Telugu - AI Only - Skit Micronarative - poor Indian girl orders the wrong meal - Replication from Kannada - Disha - Xs - Balesh"** (12 clips, 24 script lines, 26 cuts): ~02:47 final, 2 marks (✗ L10, ✓ L16), 3 peaks, 0 zooms (all two-shots/splits) → `~/Downloads/Zeno Outputs/<name>/` | n/a | ~60k (my estimate) + 36,077 Gemini transcription; no agent ledger in this telemetry | n/a | n/a |
| 2026-11-16 | **Kannada – "W46 how to have a conversation in English (scrolling text)"** (12 clips, 17 script lines): 01:17 final, 0 ✓/✗ marks → `~/Downloads/Zeno Outputs/W46 - Kannada - AI Only - Split screen + scrolling text - how to have a conversation in English - Competitor Replication English Seekho - Disha - Xs - Vidhat (2)/` | n/a | ~99,637 (agent) + 30,311 (Gemini) | 834,092 | n/a | n/a |
| 2026-11-16 | **Kannada – "W46 how to have a conversation in English (scrolling text)"** (12 clips, 17 script lines): 01:17 final, 0 ✓/✗ marks → `~/Downloads/Zeno Outputs/W46 - Kannada - AI Only - Split screen + scrolling text - how to have a conversation in English - Competitor Replication English Seekho - Disha - Xs - Vidhat (3)/reel_W46 - Kannada - AI Only - Split screen + scrolling text - how to have a conversation in English - Competitor Replication English Seekho - Disha - Xs - Vidhat (3).mp4` | ~73,383 → ~121,224 (request input) | **120,397** uncached input + output; 2,925,824 cached Codex input; 30,311 prior cached Gemini | unavailable | 3% → 3% |
| 2026-11-16 | **auto – gdoc_1F3i-288t96m_t_2sl36zopschq** (12 clips): 01:17.62, 0 marks → `/Users/mathiniraiselvan/Downloads/Zeno Outputs/W46 - Kannada - AI Only - Split screen + scrolling text - how to have a conversation in English - Competitor Replication English Seekho - Disha - Xs - Vidhat (4)/reel_W46 - Kannada - AI Only - Split screen + scrolling text - how to have a conversation in English - Competitor Replication English Seekho - Disha - Xs - Vidhat (4).mp4` <!-- zeno-output:/Users/mathiniraiselvan/Downloads/Zeno Outputs/W46 - Kannada - AI Only - Split screen + scrolling text - how to have a conversation in English - Competitor Replication English Seekho - Disha - Xs - Vidhat (4)/reel_W46 - Kannada - AI Only - Split screen + scrolling text - how to have a conversation in English - Competitor Replication English Seekho - Disha - Xs - Vidhat (4).mp4 --> | 126,236 → 146,544 | 30,708 uncached input + output; 3,034,496 cached (exact) | unavailable | unavailable |
| 2026-11-16 | **kn – gdoc_1wJjuKQxyOVE_t_rg309xa356jl** (6 clips): 02:17.57, 0 marks → `/Users/mathiniraiselvan/Downloads/Zeno Outputs/W45 - Kannada - AI Only - Skit Micronarrative - Husband insults wife in front of people - come here practice - Replication with New Script - Disha - 142s - Abhishek/reel_W45 - Kannada - AI Only - Skit Micronarrative - Husband insults wife in front of people - come here practice - Replication with New Script - Disha - 142s - Abhishek.mp4` <!-- zeno-output:/Users/mathiniraiselvan/Downloads/Zeno Outputs/W45 - Kannada - AI Only - Skit Micronarrative - Husband insults wife in front of people - come here practice - Replication with New Script - Disha - 142s - Abhishek/reel_W45 - Kannada - AI Only - Skit Micronarrative - Husband insults wife in front of people - come here practice - Replication with New Script - Disha - 142s - Abhishek.mp4 --> | 201,569 → 246,044 | 65,500 uncached input + output; 12,085,504 cached (exact) | unavailable | unavailable |
| 2026-11-16 | **auto – gdoc_1wJjuKQxyOVE_t_rg309xa356jl** (6 clips): 02:17.40, 0 marks → `/Users/mathiniraiselvan/Downloads/Zeno Outputs/Reel - W45 - Kannada - AI Only - Skit Micronarrative - Husband insults wife in front of people - come here practice - Replication with New Script - Disha - 142s - Abhishek/Edited Reel.mp4` <!-- zeno-output:/Users/mathiniraiselvan/Downloads/Zeno Outputs/Reel - W45 - Kannada - AI Only - Skit Micronarrative - Husband insults wife in front of people - come here practice - Replication with New Script - Disha - 142s - Abhishek/Edited Reel.mp4 --> | 36,479 → 79,647 | 60,503 uncached input + output; 2,675,328 cached (exact) | unavailable | unavailable |
| 2026-11-17 | **auto – v1018_w45_test** (6 clips): 02:16.12, 0 marks → `/Users/mathiniraiselvan/Downloads/Zeno Outputs/W45 - v1.0.18 Live 1-Call Test/reel_W45 - v1.0.18 Live 1-Call Test.mp4` <!-- zeno-output:/Users/mathiniraiselvan/Downloads/Zeno Outputs/W45 - v1.0.18 Live 1-Call Test/reel_W45 - v1.0.18 Live 1-Call Test.mp4 --> | 22,895 → 22,895 | 39,442 uncached input + output; 6,396 cached (exact) | unavailable | unavailable |
| 2026-11-17 | **auto – gdoc_1k0D7g1J24rF_t_zhww87pbss2f** (8 clips): 01:02.50, 0 marks → `/Users/mathiniraiselvan/Downloads/Zeno Outputs/W47 - Telugu - AI Only - Live connversation Poridge meaning paddy field - with a lady farmer - Replication from Hindi - Bhumi - Xs - Madhu/reel_W47 - Telugu - AI Only - Live connversation Poridge meaning paddy field - with a lady farmer - Replication from Hindi - Bhumi - Xs - Madhu.mp4` <!-- zeno-output:/Users/mathiniraiselvan/Downloads/Zeno Outputs/W47 - Telugu - AI Only - Live connversation Poridge meaning paddy field - with a lady farmer - Replication from Hindi - Bhumi - Xs - Madhu/reel_W47 - Telugu - AI Only - Live connversation Poridge meaning paddy field - with a lady farmer - Replication from Hindi - Bhumi - Xs - Madhu.mp4 --> | 23,894 → 178,170 | 359,052 uncached input + output; 5,393,024 cached (exact) | unavailable | unavailable |
| 2026-11-17 | **auto – gdoc_1k0D7g1J24rF_t_zhww87pbss2f** (8 clips): 01:02.38, 0 marks → `/Users/mathiniraiselvan/Downloads/Zeno Outputs/W47 - Telugu - AI Only - Live connversation Poridge meaning paddy field - with a lady farmer - Replication from Hindi - Bhumi - Xs - Madhu (2)/reel_W47 - Telugu - AI Only - Live connversation Poridge meaning paddy field - with a lady farmer - Replication from Hindi - Bhumi - Xs - Madhu (2).mp4` <!-- zeno-output:/Users/mathiniraiselvan/Downloads/Zeno Outputs/W47 - Telugu - AI Only - Live connversation Poridge meaning paddy field - with a lady farmer - Replication from Hindi - Bhumi - Xs - Madhu (2)/reel_W47 - Telugu - AI Only - Live connversation Poridge meaning paddy field - with a lady farmer - Replication from Hindi - Bhumi - Xs - Madhu (2).mp4 --> | 162,293 → 171,218 | 40,106 uncached input + output; 2,172,544 cached (exact) | unavailable | unavailable |
