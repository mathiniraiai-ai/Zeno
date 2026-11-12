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

## 2b. Emotion zooms, active-panel split engagement, and calibrated SFX

**Split screens never zoom (owner's rule, overrides any older wording in this file).** Every `split_h`, `split_v` and baked-split segment is `z=1`: no push-in, punch-in, step zoom or pull-out. The renderer ignores a zoom written on a split and holds each panel in one fixed frame (14% headroom, the speaking and the silent clip of a character lined up by their background), so switching between them never jumps.

**Captions (owner's rule, overrides any older wording in this file).** `#subs auto y=68` is the default. One style is used for the whole video, at one fixed size (54 px on a 1080-wide picture; `size=` changes it), always on a single row (never two rows), with no per-caption colour changes unless the plan says `adapt=on`. On full-frame cuts captions sit at 65 to 70% of the height (`y=68`). In a top/bottom split they always sit on the divider line in the middle (`y=50`), whatever `y=` says, so neither face is covered.

The reference videos this style was first measured from used no digital zoom (`spec.json` -> `zoom`). For high-retention Reels / Meta ads, Zeno pairs tight narrative storytelling with dynamic motion, active-panel split zooms, smooth eased transitions, Premiere-grade `#subs auto y=68` captions, and calibrated post-`loudnorm` SFX:

- **The Director analyses the script first** and writes `work/<project>/script_analysis.md`: per spoken line the emotion (with intensity 1 to 3), the beat, the zoom kind (for both full-frame AND active split-screen panels), and the calibrated `-4 to 0 dB` SFX, scaling PEAK moments with video length (~1 PEAK every `12 to 18 s`). Full tables and exact timing rules live in [`agent/EDIT_RULES.md`](../../agent/EDIT_RULES.md).
- **Values for this style:** rapid punch-in `sudden_in:1.08@<beat>` (one `flash_zoom:1.12` max on a full-frame climax), smooth push-in `1.0->1.06` / `1.0->1.07` on emotional or teaching turns `>= 2.5 s`, pause-trim / mid-stumble-trim step zoom `1.08`, closing CTA `1.0->1.06`; up to 16 rapid zooms per minute without two rapid `sudden_in` snaps back-to-back, keeping short calm reaction beats at `1.0` for contrast.
- **Active-Panel Split Engagement (`split_h`, `split_v`, and baked splits):** Zoom the **active speaking character's panel** (`1.0->1.06` / `1.0->1.07` on turns `>= 2.5 s`, `sudden_in:1.08@<beat>` on punchlines/emphasis words, or `1.08` step zoom when cutting internal `> 0.25 s` pauses; max `1.12`) while the silent listening panel stays locked at `1.0` with a stationary divider seam! Pair key split punch-ins, reveals, and speaker-turn shifts with clean beat SFX (`pop.wav` at `-3 dB`, `whoosh.wav`/`sn_swish_1.wav` at `-3 to -2 dB`, `sn_punch_1.wav` at `-2 to -1 dB`, `sn_riser_1.wav` at `-3 to -2 dB`). Only 3D holograms, camera-panning clips, and edge-framed shared two-shots (`none` in `facts.md`) lock `z=1.0`.
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
| **Split screen: horizontal, vertical, video call (HIGH); with flash card (MEDIUM)**, **Scrolling text: one person, two people in a vertical split (HIGH)**, **How would you translate (MEDIUM)** | **[none]** no example scripts. The engine has `split_h`, `split_v`, `video_call` and `chat_bar` layouts; **Scrolling text** is built: `#page scroll` with `layout=split_h` segments (two characters on top, the dialogue scrolling on a white page below, highlight box on the spoken word, red/green phrases; see `agent/EDIT_RULES.md` and `styles/formats/scroll_page.json`). Two references are measured; the one-person version and the flash-card layout are not built yet. |

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
  - Match the clips in sequence using **`layout=single` with `slot=top|bottom|left|right` (or `active=top|bottom|left|right`)** so active-panel zooms (`1.0->1.06`/`1.07` on turns `>= 2.5s`, `sudden_in:1.08@<beat>` on punchlines, `1.08` step zoom on `> 0.25s` pause cuts; max `1.12`) zoom **only** the speaking half while the baked seam and silent listener stay locked at `1.0`!
  - Tighten dialogue turn joins to **`0.06–0.15s`** (never `> 0.20s` dead air; keep **`0.02–0.05s` breath margin max** around already-padded `facts.md` word timestamps).

### Mode B — Separate Character Clips (HeyGen / Separate Renders)
- **How the raw clips look**: Character A (e.g., Miss Nova) and Character B are generated as **two separate single-person video files**.
- **How Zeno edits them**:
  - Composite them together using `layout=split_h` (left/right 50/50 vertical split) or `layout=split_v` (top/bottom 50/50 split) with `peer=<partner_clip>@<in>..<out>`.
  - **Active-panel zoom + beat SFX**: Zoom the active speaking panel (`1.0->1.06`/`1.07`, `sudden_in:1.08@<beat>`, or `1.08` pause-cut step; max `1.12`) + crisp beat SFX (`pop.wav` at `-3 dB`, `whoosh.wav`/`sn_swish_1.wav` at `-3 to -2 dB` on key shifts) while the silent listener panel (`peer=`) and `#E1DBE1` divider stay locked at `1.0`. Cut internal pauses `> 0.25s` inside turns `> 5s` into `L4a`/`L4b` while advancing `peer=` continuously across the cut. Enter/exit splits smoothly via `tr=split_reveal@0.40` or `tr=warm_flash@0.38`.

### Mode C — In-Frame Two-Shot & 3D Hologram Skit (`1.1 Skit based micro narrative` + `2.1 Hologram`)
- **How the raw clips look**: Portrait `9:16` (`720×1280`) clips where Scene 1 uses single/OTS shots, Scene 2 frames two characters side-by-side in a galley/room two-shot, and Scene 3 features Miss Nova's 3D hologram popping out of a phone in-frame with the learner.
- **How Zeno edits them**:
  - Keep **`layout=single`** across all scenes.
  - Lock **`zoom=1.0`** on hologram shots and on any two-shot whose faces sit near the left or right frame edge, so no one and no part of the 3D hologram is cropped. Other live two-shots may zoom up to `1.10` aimed at the speaker (`focus=`).
  - Preserve visual action beats (e.g., opening seat-recline/spill hook, silent embarrassment beat `L6`, and the 2.5s hologram pop-out tail at the end of Scene 2).

### Mode D — Top-Center Rounded-Rectangle Floating Window (`top_pip` — Subtopic `1.1` / `3.1`)
- **How the raw clips look**: Separate portrait `9:16` talking clips for the human learner (`M1.mp4`, `M2.mp4`) and the AI Robot (`R1.mp4`..`r4.mp4`), plus dedicated silent listening clips (`M extra.mp4`, `R extra.mp4`), and optional `<stem>VO.mp4` voiceover audio clips (e.g., `1.2.mp4` video + `1.2VO.mp4` Partner audio).
- **How Zeno edits them**:
  - **Voiceover Mux (`<stem>VO.mp4`)**: Mux `<stem>.mp4` video (`0:v:0`) with `<stem>VO.mp4` audio (`1:a:0`) so the listening actor's video carries the clean phone-partner voiceover and word timestamps.
  - **Strikethrough Cutaway (`3.mp4`)**: Stack 3 red strikethrough pill banners (`banner:"How are you?"@0.18`, `banner:"How was your weekend?"@1.01`, `banner:"How's the weather?"@2.38`) synchronized to each phrase onset + `mark:wrong`.
  - **Top Floating Window (`layout=top_pip`)**: Full-screen `1080×1920` background shows the human learner (`M1`, `M2`, or `M extra.mp4` looping live when listening), while a clean top-center rounded rectangle (`750×420` at `x=165, y=20, radius=36px`, no phone status-bar chrome) shows the AI Robot (`R1`..`r4`, or `R extra.mp4` looping live when listening).
  - **Multi-Turn Interleaving & Zero Stumbles/Repeats**: Split multi-turn clips (`R1.mp4` -> `L12, L14, L16`; `M1.mp4` -> `L13, L15, L17`; `M2.mp4` -> `L19, L21`) at tight turn boundaries (`0.06–0.15s` joins), and check `! REPEAT / STUMBLE` flags in `facts.md` to trim every head, mid-sentence (`L5a` + `L5b` masked with `1.08` punch-in), and cross-boundary stumble or repeated phrase (e.g., `R2.mp4` first `"कोई बात नहीं!"` `0.00–1.02s` discarded).

---

## 4. Scene Structure & Editorial Rules

1. **First Half — Live-Action Scenes (`Scene 1` Problem + `Scene 2` Tip/Continuation)**:
   - `layout=single`, snappy speaker-follow cuts (`0.06–0.15s` turn joins, `> 0.25s` internal monologue pauses split into `L4a`/`L4b` with `1.08` punch-ins) across wide establishing shots, speaker close-ups, two-shots, and reaction beats.
   - Preserve scripted reaction/laughter beats while trimming every `! REPEAT / STUMBLE` false start or boundary repeat.
2. **Second Half — AI Teacher Demo (`Scene 3` Split Screen or Hologram)**:
   - **Separate HeyGen clips (`split_h` / `split_v`)**: Composite with `peer=`, zooming the active speaking panel (`1.0->1.06`/`1.07` on turns `>= 2.5s`, `sudden_in:1.08@<beat>`, `1.08` step zoom on `> 0.25s` pause cuts; max `1.12`) + calibrated `-4 to 0 dB` beat SFX while the silent listener and divider stay locked at `1.0`.
   - **Pre-stitched Google Flow split**: `layout=single` with `slot=top|bottom|left|right` (or `active=top|bottom|left|right`) for active-panel zoom (`<= 1.08`) with locked seam and steady listener.
   - **In-frame 3D hologram**: `layout=single`, `zoom=1.0`.
3. **Where to Zoom vs. Where NOT to Zoom**:
   - **Lock `zoom=1.0` ONLY on**:
     - **3D hologram shots** or shared single-camera two-shots whose faces sit near the frame edges (`none` in `facts.md`).
     - Clips with deliberate camera movement (pan/dolly, detected by OpenCV `detect_static_camera_motion()`).
   - **Apply Dynamic Zoom on BOTH Full-Frame AND Active Split Panels**:
     - Full-frame `single` shots, safe live two-shots (`<= 1.10` with `focus=x,y`), AND active speaking panels in `split_h`, `split_v`, and baked splits (`<= 1.08`):
       - `zoom=1.08` step punch-in to mask internal `> 0.25s` pause cuts or mid-sentence stumble cuts (`L5a` + `L5b`).
       - Smooth sub-pixel push-in (`1.0->1.06` / `1.0->1.07`) on turns `>= 2.5s`, pull-out (`1.08->1.0` / `1.06->1.0`), or rapid `sudden_in:1.08@<beat>` / `flash_zoom:1.12@<beat>` on punchlines and emphasis words.
4. **Premiere-Grade Auto-Captions (`#subs auto y=68`) & On-Screen Text (`21132` "Stop Saying" Hook)**:
   - **Animated Auto-Captions ON by Default**: Include `#subs auto y=68` in `plan.edl` on every reel (`y=50` automatically on the `split_v` seam so faces are never covered; set `#subs off` **only** if the user explicitly asks for no subtitles).
   - **Extra `banner:` Cards ONLY When Scripted**: Add stacked banners only where the script cues a `"Stop saying..."` list or strikethrough phrase (no unrequested decorative B-roll or spammy CTAs).
5. **Where to Show Errors (`✗` / `✓`) & Snappy Hold Timing (`0.35–0.42s`)**:
   - **Show `✗` (`wrong`) and `✓` (`right`) marks ONLY** when the script marks a learner mistake (`[WRONG]`) or correction (`[RIGHT]`).
   - **Snappy End-of-Dialogue Placement (`0.35–0.42s` Hold Tail, `0.48s` 2-Pulse Flash)**: Trigger `mark:wrong` or `mark:right` right as the sentence finishes (`at = <last_word_end - cut_start - 0.06>`), set duration to **`0.48s`**, and extend the segment `end` by **only `0.35–0.42s`** after `last_word_end` (never `0.70–1.00s` frozen tails!), playing calibrated post-`loudnorm` SFX (`sn_wrong.wav` at `+1.0 dB` or `correct_ding.wav` + `sn_correct.wav` at `+0.5 dB`).
6. **Smooth Eased Motion Transitions & Cadence (`every 7–9s`)**:
   - Place smooth physical transitions **every `7–9s` (`4–6` transitions in a `35–45s` reel)** at scene shifts, hook-to-split entry (`tr=split_reveal@0.40` opposing panel slide-in or `tr=warm_flash@0.38`), split-to-teaching exit (`tr=split_reveal@0.40` or `tr=whip_right@0.34`), and topic turns (`tr=whip_left@0.34`, `tr=whip_right@0.34`, `tr=slide_up@0.36`, `tr=zoom_burst@0.36`, `tr=flash_zoom@0.36`), keeping fast back-and-forth turns inside a continuous split as crisp `tr=cut` swaps.
   - All transitions use **Zero-Audio-Loss (`tpad` video tail cloning + `0.00s` audio concat)** so zero words or syllables are ever clipped.
7. **4-Clip Split-Screen Workflow (2 Talking Clips + 2 Dedicated Listening Clips — Never Static Images)**:
   - When the user provides **4 clips** for a `split_h` or `split_v` dialogue scene (Character A Talking, Character B Talking, Character A Listening, Character B Listening), [`scripts/layouts.py`](../../scripts/layouts.py) uses the dedicated listening clip (a quiet stretch of it, `peer=`) so the listening character is **live moving video**; a still frame is used only when that character has no quiet stretch, and the brief says `[LISTENER_FALLBACK]`.
8. **Notion Naming & Ratio Prefix Convention (`~/Downloads/Zeno Outputs/<Notion name>/`)**:
   - Always ask the user for the **Notion name** (instead of inventing a random folder name) and pass `--name "<Notion name>"` to `zeno.py --inspect` / `prepare.py`.
   - Output folder: `~/Downloads/Zeno Outputs/<Notion name>/`
   - 9:16 Reel ratio output: `reel_<Notion name>.mp4` (`"reel"` before the name)
   - 1:1 Square ratio output: `SQ_<Notion name>.mp4` (`"SQ"` before the name)

---

## 5. Who decides

The Director makes every decision in one pass, by `agent/EDIT_RULES.md` and this style's rules.
