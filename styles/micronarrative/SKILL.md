# micronarrative (Macro / Micro-Narrative Style Skill)

A short story skit (`60–130s`): a live-action problem scene, a friend's tip or dialogue continuation, and an AI-teacher split-screen or in-frame 3D hologram demo with Miss Nova. Learned from reference metrics in [`spec.json`](spec.json) and production-hardened across Zeno's multi-agent harness.

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

- The script has `Scene N` / `SCENE N` headings, named speakers (`Speaker: line`), a live-action first half, and either a split-screen section or an in-frame 3D hologram / two-shot section with Miss Nova (or two speakers).
- The user refers to the edit as a **macro narrative**, **micronarrative**, **skit based micro narrative (`1.1`)**, or **hologram (`2.1`)**. (The team's sheet uses "Live conversation" for a separate format family; see 2c.)

---

## 2b. Emotion zooms and effect sounds (owner decision, 2026-11-04)

The reference videos this style was learned from used **no digital zoom and no effect sounds** (`spec.json` -> `zoom`, `audio`). The owner asked for emotion-driven zooms and effects on top of that, so they are a deliberate addition, kept sparse:

- **The Director analyses the script first** and writes `work/<project>/script_analysis.md`: per spoken line the emotion (with intensity 1 to 3), the beat, the zoom kind and the effect sound, with at most 3 PEAK moments in the whole video. The full method, the emotion-to-zoom table (rapid and slow zooms, where on the word) and the effect-sound table with exact timing are in [`agent/EDIT_RULES.md`](../../agent/EDIT_RULES.md) -> "Emotion plan", "Zoom" and "Sound effects for emotion".
- **Values for this style (starting points, tune from the owner's feedback):** rapid zoom `1.08` (one `flash_zoom 1.12` at most), slow push `1.04` to `1.06`, closing call to action `1.05`; at most 8 rapid zooms a minute and never two in a row; at least a quarter of the single-speaker cuts stand still.
- **Splits, two-shots and holograms stay still** (`z=1`). Their energy comes from the cut and the effect.
- The library has no laugh, gasp, heartbeat or sad sting. Use the footage's own sound or a short silence, or ask the owner for licensed files.

---

## 2c. The formats (the team's "Editing Automation sheet")

Identify the format from the script **before** the emotion plan, and write it on the first line of `script_analysis.md`. Marks show where each statement comes from: **[sheet]** the team's sheet (names, priority, example links), **[scripts]** read from the example scripts (one or two per format, in Telugu, Hindi, Malayalam or Kannada; the cards show lengths of 79 to 121 s), **[none]** no example script was available. Correct anything that is wrong.

**The backbone most micro-narratives share [scripts]:** a human hook or problem, a bridge ("I used to speak like this too", "let me show you"), a drill with the AI teacher (Miss Nova or the Female Robot) in which a wrong sentence is corrected and praised, then the install call to action. The emotional peaks are the humiliation or surprise early, and the correction or the "Wow" at the payoff.

| Format and priority [sheet] | What its script looks like and how it plays [scripts] |
|---|---|
| **1. Skit based micro narrative** (URGENT; a vertical and a horizontal version) | A `Visual:` paragraph and a reference link at the top, a character list, bold speaker labels, tone notes in parentheses (`(making fun)`), cut notes in brackets. About 30 to 40 spoken lines. Human actors in several locations (an airplane seat, a crew cabin, a construction site), the Female Robot only in the split. Opens on a humiliating incident or a hook, a kind stranger or flashback coaches one line, then the robot drills it. One example strikes three stock phrases with a red X; the other has no marks. |
| **2. Direct hook micro narrative** (URGENT) | `Concept reference`, `Visual:`, `Characters:`, then `Scene 1/2/3`; about 20 to 25 lines; the same script is often repeated in variants (character or visual changes). A selfie talking head hooks straight to camera (an offer, or a whispered secret), then a split with the AI teacher (teacher left, human right), then the call to action. The first example puts a red X and a green tick after the wrong and right answers; the second has none. |
| **3. Street interview micro narrative** (URGENT) | A reference link, `Characters`, `Scene 1/2`; about 20 to 30 lines. A handheld street and microphone look with an unusual interviewee (a couple abroad, an old woman asking for a job). They answer fluently, the reveal is that they studied with an AI teacher, a "let me show you" bridge, then a split with Miss Nova, then the call to action. No X or tick marks in the examples. Peaks: the husband's smile drops; "Are old people not supposed to speak English?" |
| **4. Stop saying / Never say micro narrative** (URGENT) | A reference link, `Characters`, `Scene 1` to `5`; 16 to 24 lines; the shortest format (79 to 101 s). Opens "Stop saying / Never say X"; the three stock phrases appear on screen, each struck through with a red X and a wrong-answer sound as it is said; a bridge ("I used to speak like this too"); a split drill with the robot (left/right in one example, top/bottom in another) with a green tick on the corrected sentence; a return to scene 1; the call to action. A human presenter in a location or a 3D-animated one (the sea, a talk show, a scooter selfie). Peak: the phrases being struck out. |
| **5. Live conversation** (URGENT) is its own family: **hologram**, **AI teacher on a tablet with the human doing an activity**, **interactive (driving a cycle, riding a bull, a road accident)** | Only the tablet type has examples: a `Visual:` line in brackets and no scene numbers; 12 to 20 lines. The robot is a device in the same shot (on a giant phone beside the bed, or on the phone screen throughout), so the layout is a **single full frame, not a split**. A comic misunderstanding or small mistake the robot corrects, objections about judgment, time and cost answered, the call to action, sometimes a cut to the app screen. No X or tick marks. Hologram and interactive: **[none]**. |
| **6. Skit based / story based, direct pitch** (MEDIUM) | One example: five scenes, a `Visual:` line on each, about 11 long narration blocks and some flashback lines; one continuous kitchen shot plus slightly faded flashbacks, a hologram of Miss Nova pops up above the phone, the call to action straight to camera. Single full frame, no split, no marks. |
| **Split screen: horizontal, vertical, video call (HIGH); with flash card (MEDIUM)**, **Scrolling text: one person, two people in a vertical split (HIGH)**, **How would you translate (MEDIUM)** | **[none]** no example scripts. The engine has `split_h`, `split_v`, `video_call` and `chat_bar` layouts; there is no scrolling-text or flash-card layout yet. |

**Things the examples show that affect the edit**
- **The writers use "vertical" and "horizontal" split loosely** (one script calls a floating window "more like a horizontal split"; another says "vertical split" and means top and bottom). Do not trust those words: use the explicit `Left:`, `Right:`, `Top:`, `Bottom:` in the script.
- **A script often holds competitor transcripts and several variant tabs.** The Supernova script of the tab you were given is the one to edit.
- **Marks differ by script, not by format.** Put a red X or a green tick only where that script marks a line.
- **Footage is mostly AI-generated** (3D or live-action looks); a character may be silent in one clip and speaking in another, so check the clip sheet for who is who.
- The cards show lengths of 79 to 121 s, in Telugu, Hindi, Malayalam and Kannada.

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

1. **First Half — Live-Action Scenes (`Scene 1` Problem + `Scene 2` Tip/Continuation)**:
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
