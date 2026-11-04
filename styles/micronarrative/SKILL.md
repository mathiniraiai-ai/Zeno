# micronarrative (Macro / Micro-Narrative Style Skill)

A short story skit (`60–130s`): a live conversation problem scene, a friend's tip or dialogue continuation, and an AI-teacher split-screen or in-frame 3D hologram demo with Miss Nova. Learned from reference metrics in [`spec.json`](spec.json) and production-hardened across Zeno's multi-agent harness.

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

- The script has `Scene N` / `SCENE N` headings, named speakers (`Speaker: line`), a live conversation first half, and either a vertical split-screen section or an in-frame 3D hologram / two-shot section with Miss Nova (or two speakers).
- The user refers to the edit as a **macro narrative**, **micronarrative**, **skit based micro narrative (`1.1`)**, or **hologram (`2.1`)**.

---

## 3. Split-Screen & Hologram Two-Shot Handling: Three Distinct Footage Modes

Always check how the raw footage was generated before assigning layouts in `plan.edl`:

### Mode A — Pre-Stitched Vertical Split Screen (Google Flow)
- **How the raw clips look**: Each raw `.mp4` clip coming from **Google Flow** already has the vertical split screen (Character A + Character B side-by-side or stacked) **stitched inside a single video frame** (`720×1280` or `1080×1920`).
- **How Zeno edits them**:
  - **DO NOT** use `split_h` or `split_v` with `peer=` (which would crop and double-stack an already-split frame).
  - Match the clips **straight in sequence using `layout=single`** and lock **`zoom=1.0`**.
  - Cut cleanly from clip to clip at each dialogue turn boundary (`0.10–0.20s` pre-roll before speech onset, `0.03–0.20s` post-roll after speech end).

### Mode B — Separate Character Clips (HeyGen / Separate Renders)
- **How the raw clips look**: Character A (e.g., Miss Nova) and Character B are generated as **two separate single-person video files**.
- **How Zeno edits them**:
  - Composite them together using `layout=split_h` (left/right 50/50 vertical split) or `layout=split_v` (top/bottom 50/50 split) with `peer=<partner_clip>@<in>..<out>`.
  - Swap panels cleanly at turn boundaries while keeping the full frame divider locked (`#E1DBE1` thin divider, both panels live).

### Mode C — In-Frame Two-Shot & 3D Hologram Skit (`1.1 Skit based micro narrative` + `2.1 Hologram`)
- **How the raw clips look**: Portrait `9:16` (`720×1280`) clips where Scene 1 uses single/OTS shots, Scene 2 frames two characters side-by-side in a galley/room two-shot, and Scene 3 features Miss Nova's 3D hologram popping out of a phone in-frame with the learner.
- **How Zeno edits them**:
  - Keep **`layout=single`** across all scenes.
  - Lock **`zoom=1.0`** on all Scene 2 and Scene 3 two-shots and hologram shots so characters near the left/right frame edges and the glowing 3D hologram are never cropped.
  - Preserve visual action beats (e.g., opening seat-recline/spill hook, silent embarrassment beat `L6`, and the 2.5s hologram pop-out tail at the end of Scene 2).

### Mode D — Top-Center Rounded-Rectangle Floating Window (`top_pip` — Subtopic `1.1` / `3.1`)
- **How the raw clips look**: Separate portrait `9:16` talking clips for the human learner (`M1.mp4`, `M2.mp4`) and the AI Robot (`R1.mp4`..`r4.mp4`), plus dedicated silent listening clips (`M extra.mp4`, `R extra.mp4`), and optional `<stem>VO.mp4` voiceover audio clips (e.g., `1.2.mp4` video + `1.2VO.mp4` Partner audio).
- **How Zeno edits them**:
  - **Voiceover Mux (`<stem>VO.mp4`)**: Mux `<stem>.mp4` video (`0:v:0`) with `<stem>VO.mp4` audio (`1:a:0`) so the listening actor's video carries the clean phone-partner voiceover and word timestamps.
  - **Strikethrough Cutaway (`3.mp4`)**: Stack 3 red strikethrough pill banners (`banner:"How are you?"@0.18`, `banner:"How was your weekend?"@1.01`, `banner:"How's the weather?"@2.38`) synchronized to each phrase onset + `mark:wrong`.
  - **Top Floating Window (`layout=top_pip`)**: Full-screen `1080×1920` background shows the human learner (`M1`, `M2`, or `M extra.mp4` looping live when listening), while a clean top-center rounded rectangle (`750×420` at `x=165, y=20, radius=36px`, no phone status-bar chrome) shows the AI Robot (`R1`..`r4`, or `R extra.mp4` looping live when listening).
  - **Multi-Turn Interleaving & Head-Repeat Cuts**: Split multi-turn clips (`R1.mp4` -> `L12, L14, L16`; `M1.mp4` -> `L13, L15, L17`; `M2.mp4` -> `L19, L21`) at exact inter-turn silence midpoints, and trim head false-starts (e.g., `R2.mp4` first `"कोई बात नहीं!"` `0.00–1.02s` discarded).

---

## 4. Scene Structure & Editorial Rules

1. **First Half — Live Conversation (`Scene 1` Problem + `Scene 2` Tip/Continuation)**:
   - `layout=single`, speaker-follow cuts across wide establishing shots, speaker close-ups, two-shots, and reaction beats.
   - Preserve natural reaction/laughter beats where indicated in the script.
2. **Second Half — AI Teacher Demo (`Scene 3` Split Screen or Hologram)**:
   - If pre-stitched in Google Flow or in-frame 3D hologram: `layout=single`, `zoom=1.0`.
   - If separate HeyGen clips: `layout=split_h` / `split_v` with `peer=` compositing, `zoom=1.0`.
3. **Where to Zoom vs. Where NOT to Zoom**:
   - **NO Zoom / NO Zoom-Out (`zoom=1.0`)**:
     - **Never** put zoom-ins or zoom-outs on **pre-stitched split-screen clips**, **in-frame two-shots**, or **3D hologram two-shots**.
     - **Never** put zoom on clips that already have camera movement (detected automatically by OpenCV `detect_static_camera_motion()`).
   - **Where to Apply Zoom**:
     - Apply zoom **only** on static single-speaker close-ups/mid-shots in Scene 1 or single-speaker turns when needed:
       - `zoom=1.08` punch-in to mask same-clip jump cuts.
       - 3× supersampled sub-pixel `mild_in` (`1.0->1.06`), `mild_out` (`1.08->1.0`), or `flash_zoom` (`sudden_in` / `sudden_out`) on static single-speaker emphasis lines.
4. **Where to Put On-Screen Text vs. Where NOT to (`21132` "Stop Saying" Stacked Strikethrough Hook)**:
   - **Put on-screen text ONLY** where the script explicitly cues a "Stop saying..." hook list (e.g., `Stop saying: "What do you mean?", "I don't understand", "Repeat please"`).
   - **Never** add unrequested decorative text where not specified in the script. Use `subs=ref` when reference-style pill subtitles are requested.
5. **Where to Show Errors (`✗` / `✓`) vs. Where NOT to**:
   - **Show `✗` (`wrong`) and `✓` (`right`) marks ONLY** when the script contains an explicit spoken-English learner mistake (`[WRONG]`) followed by the teacher's correction (`[RIGHT]`).
   - Uses Zeno's **Minimal 2-Pulse End-of-Sentence Flash (`mark_pack: flash`)** (`0.56s`, `buzzer.wav` `+4 dB` or `correct_ding.wav` `+2.5 dB`).
6. **Where Transitions Are Added**:
   - Placed **at scene boundaries** (e.g., `Scene 1 -> Scene 2` and `Scene 2 -> Scene 3`) using `tr=whip_left@0.28` / `whip_right@0.28` + `whoosh.wav` (or `tr=warm_flash@0.38`), while keeping all intra-scene cuts as clean hard cuts (`tr=cut`).
   - All transitions use **Zero-Audio-Loss (`tpad` video tail cloning + `0.00s` audio concat)** so zero words or syllables are ever swallowed at cuts.
7. **4-Clip Split-Screen Workflow (2 Talking Clips + 2 Dedicated Listening Clips — Never Static Images)**:
   - When the user provides **4 clips** for a `split_h` or `split_v` dialogue scene (Character A Talking, Character B Talking, Character A Listening, Character B Listening), [`scripts/layouts.py`](../../scripts/layouts.py) loops the dedicated listening video clip so the listening character is **100% live moving video** at all times.

---

## 5. Who decides

The Director makes every decision in one pass, by `agent/EDIT_RULES.md` and this style's rules.
