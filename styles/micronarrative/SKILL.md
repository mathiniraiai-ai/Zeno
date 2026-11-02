# micronarrative (Macro / Micro-Narrative Style Skill)

A short story skit (`60–130s`): a live conversation problem scene, a friend's tip or dialogue continuation, and an AI-teacher split-screen demo with Miss Nova. Learned from reference metrics in [`spec.json`](spec.json) and production-hardened across Zeno's multi-agent harness.

---

## 1. Related Harness Documents & Links

- **Master Video Edit Skill**: [`.agent/skills/video-edit/SKILL.md`](../../.agent/skills/video-edit/SKILL.md)
- **Director Loop & Entry Point**: [`agent/DIRECTOR.md`](../../agent/DIRECTOR.md)
- **Always-On Workspace Rules**: [`AGENTS.md`](../../AGENTS.md) | [`CLAUDE.md`](../../CLAUDE.md) | [`.agent/rules/editor-workflow.md`](../../.agent/rules/editor-workflow.md)
- **Measured Style Numbers & Timing Spec**: [`styles/micronarrative/spec.json`](spec.json) | [`styles/micronarrative/map.json`](map.json)
- **Word-Boundary & Zero-Audio-Loss Architecture**: [`agent/concepts/word_boundary_training.md`](../../agent/concepts/word_boundary_training.md)
- **Token Telemetry & Ledger Skill**: [`.agent/skills/token-telemetry/SKILL.md`](../../.agent/skills/token-telemetry/SKILL.md)

---

## 2. When to Use This Skill

- The script has `Scene N` / `SCENE N` headings, named speakers (`Speaker: line`), a live conversation first half, and a vertical split-screen section with Miss Nova (or two speakers).
- The user refers to the edit as a **macro narrative** or **micronarrative**.

---

## 3. Split-Screen Handling: Two Distinct Footage Modes

Always check how the split-screen raw footage was generated before assigning layouts in `plan.edl`:

### Mode A — Pre-Stitched Vertical Split Screen (Google Flow)
- **How the raw clips look**: Each raw `.mp4` clip coming from **Google Flow** already has the vertical split screen (Character A + Character B side-by-side or stacked) **stitched inside a single video frame** (`720×1280` or `1080×1920`).
- **How Zeno edits them**:
  - **DO NOT** use `split_h` or `split_v` with `peer=` (which would crop and double-stack an already-split frame).
  - Match the clips **straight in sequence using `layout=single`**.
  - Cut cleanly from clip to clip at each dialogue turn boundary (`0.10–0.20s` pre-roll before speech onset, `0.03–0.20s` post-roll after speech end).

### Mode B — Separate Character Clips (HeyGen / Separate Renders)
- **How the raw clips look**: Character A (e.g., Miss Nova) and Character B are generated as **two separate single-person video files**.
- **How Zeno edits them**:
  - Composite them together using `layout=split_h` (left/right 50/50 vertical split) or `layout=split_v` (top/bottom 50/50 split) with `peer=<partner_clip>@<in>..<out>`.
  - Swap panels cleanly at turn boundaries while keeping the full frame divider locked (`#E1DBE1` thin divider, both panels live).

---

## 4. Scene Structure & Editorial Rules

1. **First Half — Live Conversation (`Scene 1` Problem + `Scene 2` Tip/Continuation)**:
   - `layout=single`, speaker-follow cuts across wide establishing shots, speaker close-ups, two-shots, and reaction beats.
   - Preserve natural reaction/laughter beats where indicated in the script.
2. **Second Half — Vertical Split Screen (`Scene 3` AI Teacher Demo)**:
   - If pre-stitched in Google Flow: `layout=single` (straight cuts matching each turn's pre-stitched clip).
   - If separate HeyGen clips: `layout=split_h` / `split_v` with `peer=` compositing.
3. **Where to Zoom vs. Where NOT to Zoom**:
   - **NO Zoom / NO Zoom-Out (`zoom=1.0`)**:
     - **Never** put zoom-ins or zoom-outs on **pre-stitched split-screen clips** (keeps the split divider and both faces locked in place).
     - **Never** put zoom on **continuous two-shots** (e.g., Scene 2 tip shot) or on clips that already have camera movement (detected automatically by OpenCV `detect_static_camera_motion()`).
   - **Where to Apply Zoom**:
     - Apply zoom **only** on static single-speaker close-ups/mid-shots in the live conversation or单-speaker turns when needed:
       - `zoom=1.08` punch-in to mask same-clip jump cuts.
       - 3× supersampled sub-pixel `mild_in` (`1.0->1.06`), `mild_out` (`1.08->1.0`), or `flash_zoom` (`sudden_in` / `sudden_out`) on static single-speaker emphasis lines.
4. **Where to Put On-Screen Text vs. Where NOT to (`21132` "Stop Saying" Stacked Strikethrough Hook)**:
   - **Put on-screen text ONLY** where the script explicitly cues a "Stop saying..." hook list (e.g., `Stop saying: "What do you mean?", "I don't understand", "Repeat please"`).
   - **How the `21132` "Stop Saying" Stacked Strikethrough Visual Works (`assets/templates/stop_saying_banners.json`)**:
     - Placed horizontally centered (`x_norm = 0.50`) at **lower-chest level (`y0_norm = 0.52`, `step_norm = 0.068`)** so the speaker's face is 100% unobstructed.
     - As the speaker says each phrase (`banner:"What do you mean?"@t1 banner:"I don't understand"@t2 banner:"Repeat please"@t3`), a dark-red gradient pill pops onto the screen, and `0.16s` later a **bold white horizontal strikethrough / dash line slashes across the pill** (`+36px` overshoot on both sides) while the previous struck-through phrases remain stacked above it until the sentence finishes (`0.18s` group fade-out) + subtle red edge flash (`mark_pack: flash`).
   - **Never** add unrequested subtitles, captions, CTAs, or decorative text where not specified in the script.
5. **Where to Show Errors (`✗` / `✓`) vs. Where NOT to**:
   - **Show `✗` (`wrong`) and `✓` (`right`) marks ONLY** when the script contains an explicit spoken-English learner mistake followed by the teacher's correction and the learner's corrected repeat.
   - Uses Zeno's **Minimal 2-Pulse End-of-Sentence Flash (`mark_pack: flash`)**:
     - Triggers **only when the wrong or right sentence finishes speaking**.
     - Subtle `0.50` alpha Red/Green frame glow (`red_flash.png` / `green_flash.png`) that **flashes 2 times** over `0.56s` (`Pulse 1 → OFF → Pulse 2`) with error buzzer (`+4 dB`) or right ding (`+2.5 dB`).
   - On narrative/conversational dialogue lines with no explicit learner mistake/correction beat, **do not place `✗` / `✓` marks**.
6. **Where Transitions Are Added**:
   - Placed **at scene boundaries** (e.g., Scene 1 → Scene 2, and Scene 2 Live Conversation → Scene 3 Vertical Split Screen) using `tr=warm_flash@0.38` (or Meta creative transitions: `whip_left`, `whip_right`, `zoom_burst`, `flash_zoom`, `slide_up`).
   - All transitions use **Zero-Audio-Loss (`tpad` video tail cloning + `0.00s` audio concat)** so zero words or syllables are ever swallowed at cuts.

---

## 5. Mandatory 5-Agent Parallel Workflow (Plus 10-Agent Deep Pipeline)

Every macro/micronarrative edit runs through the **5-Agent Parallel Workflow** codified in [`AGENTS.md`](../../AGENTS.md) and [`agent/DIRECTOR.md`](../../agent/DIRECTOR.md) (scalable to the **10-Agent Deep Pipeline** for multi-feature engineering passes):

1. **Agent 1 — Script Coverage, Concept Brief & Take Auditor**: Verifies 100% verbatim script coverage in order, speaker identity, reaction beats, and `[BEST_TAKE]` selection.
2. **Agent 2 — Acoustic Pause-Cut & De-Duplication Specialist**: Uses Meta `Wav2Vec2` + `uroman` + `AcousticOnsetVerifier` (`-35ms` consonant pre-attack guard, `+45ms` coda release guard) to remove retakes/repeats and tighten dead air (`>0.85s`) with zero clipped syllables.
3. **Agent 3 — OpenCV Motion & Dynamic Zoom/Layout Choreographer**: Enforces `layout=single` straight matching on pre-stitched Google Flow split-screen clips, locks `zoom=1.0` on all split-screen/two-shot/moving clips, and applies 3× supersampled zooms only on static single-speaker shots.
4. **Agent 4 — Transitions, On-Screen Text & Minimal 2-Pulse Marks/SFX Designer**: Places scene-boundary transitions (`warm_flash`), script-cued text banners (only where requested), and end-of-sentence 2-pulse `✗`/`✓` flashes (only where errors/corrections occur).
5. **Agent 5 — Post-Render Deep Video Analyzer & Auto-Fixer (Mandatory Post-Render Gate)**: Runs after `render.py --verify` to inspect the final `.mp4` locally via Python (`verify.py`, `words.json`, FFmpeg `silencedetect`, 10ms WAV RMS energy, and RGB frame checks) for **0 repeated words**, **0 silences `>1.05s`**, **0 clipped words**, active transitions, and `-11.5 LUFS` (`-1.0 dBTP` cap) Meta Ads loudness—auto-fixing `plan.edl` and re-rendering before delivery if any issue is found.
6. **1:1 Square Meta Ads Export (`--square`) & Per-Segment `vol=<dB>` Control**: Pass `--square` to `render.py` to also export a `1:1` (`1080×1080`) Square video (Layer 1: `1:1` center-cropped background with 5–10% Gaussian blur `gblur=sigma=18`; Layer 2: full `9:16` clip centered on top). Every raw segment is capped at `-11.5 LUFS` / `-1.0 dBTP` automatically, and any segment can set `vol=+3dB` / `vol=-2dB` in `plan.edl` for exact mid-demo dB adjustments.
