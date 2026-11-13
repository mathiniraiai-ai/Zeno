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

## 2b. Emotion zooms (full-frame only), still split screens, and calibrated SFX

**Split screens never zoom (owner's rule, overrides any older wording in this file).** Every `split_h`, `split_v` and baked-split segment is `z=1`: no push-in, punch-in, step zoom or pull-out. The renderer ignores a zoom written on a split and holds each panel in one fixed frame (14% headroom, the speaking and the silent clip of a character lined up by their background), so switching between them never jumps.

**Captions (owner's rule, overrides any older wording in this file).** `#subs auto y=68` is the default. One style is used for the whole video, at one fixed size (54 px on a 1080-wide picture; `size=` changes it), always on a single row (never two rows), with no per-caption colour changes unless the plan says `adapt=on`. On full-frame cuts captions sit at 65 to 70% of the height (`y=68`). In a top/bottom split they always sit on the divider line in the middle (`y=50`), whatever `y=` says, so neither face is covered.

The reference videos this style was first measured from used no digital zoom (`spec.json` -> `zoom`). For high-retention Reels / Meta ads, Zeno pairs tight storytelling with motion in the full-frame cuts, completely still split screens, transitions only where the story changes, Premiere-grade `#subs auto y=68` captions, and calibrated post-`loudnorm` SFX:

- **The Director names the format, then analyses the script** and writes `work/<project>/script_analysis.md`: line 1 `Format: <id> (<name>)` from [`agent/FORMATS.md`](../../agent/FORMATS.md), then per spoken line the emotion (with intensity 1 to 3), the beat, the zoom kind (full-frame cuts only; a split is always `z=1`), and the calibrated `-4 to 0 dB` SFX, scaling PEAK moments with video length (~1 PEAK every `12 to 18 s`). Full tables and exact timing rules live in [`agent/EDIT_RULES.md`](../../agent/EDIT_RULES.md).
- **Values for this style:** rapid punch-in `sudden_in:1.08@<beat>` (one `flash_zoom:1.12` max on a full-frame climax), smooth push-in `1.0->1.06` / `1.0->1.07` on emotional or teaching turns `>= 2.5 s`, pause-trim / mid-stumble-trim step zoom `1.08`, closing CTA `1.0->1.06`; up to 16 rapid zooms per minute without two rapid `sudden_in` snaps back-to-back, keeping short calm reaction beats at `1.0` for contrast.
- **Split screens (`split_h`, `split_v`, and baked splits) stay still:** every segment is `z=1`; both panels hold one fixed frame and the divider never moves. Emphasis comes from tight turns, marks and clean beat SFX on key words and reveals (`pop.wav` at `-3 dB`, `sn_punch_1.wav` at `-2 to -1 dB`, `sn_riser_1.wav` at `-3 to -2 dB`), never from motion. In `single`, 3D holograms, camera-panning clips, and edge-framed shared two-shots (`none` in `facts.md`) also lock `z=1.0`.
- The library has no laugh, gasp, heartbeat or sad sting. Use the footage's own sound or a short silence, or ask the owner for licensed files.

---

## 2c. The formats

The formats and sub-formats of the team's Testing Formats sheet (8 main formats: micro narrative with skit, direct hook, street interview and stop saying; live conversation; split screen; scrolling text; how would you translate; skit/story based; realistic to 3D; 3D to 2D), how to recognise each from the script, and **each one's edit pattern (layouts, zoom, transitions)** are in [`agent/FORMATS.md`](../../agent/FORMATS.md). Name the format there before the emotion plan.

---

## 3. Split-Screen & Hologram Two-Shot Handling: Three Distinct Footage Modes

Always check how the raw footage was generated before assigning layouts in `plan.edl`:

### Mode A — Pre-Stitched Vertical Split Screen (Google Flow)
- **How the raw clips look**: Each raw `.mp4` clip coming from **Google Flow** already has the vertical split screen (Character A + Character B side-by-side or stacked) **stitched inside a single video frame** (`720×1280` or `1080×1920`).
- **How Zeno edits them**:
  - **DO NOT** use `split_h` or `split_v` with `peer=` (which would crop and double-stack an already-split frame).
  - Match the clips in sequence using **`layout=single` with `slot=top|bottom|left|right` (or `active=top|bottom|left|right`)**, always `z=1`: a baked split never zooms.
  - Tighten dialogue turn joins to **`0.06–0.15s`** (never `> 0.20s` dead air; keep **`0.02–0.05s` breath margin max** around already-padded `facts.md` word timestamps).

### Mode B — Separate Character Clips (HeyGen / Separate Renders)
- **How the raw clips look**: Character A (e.g., Miss Nova) and Character B are generated as **two separate single-person video files**.
- **How Zeno edits them**:
  - Composite them together using `layout=split_h` (left/right 50/50 vertical split) or `layout=split_v` (top/bottom 50/50 split) with `peer=<partner_clip>@<in>..<out>`.
  - **Still panels + beat SFX**: both panels are `z=1` (one fixed frame each, the silent listener in the other panel via `peer=`), with crisp beat SFX on key words (`pop.wav` at `-3 dB`). Cut internal pauses of `0.5 s` or longer inside turns `> 5s` into `L4a`/`L4b` while advancing `peer=` continuously across the cut. Enter or leave a split with `tr=split_reveal@0.40`; never a transition inside it.

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
   - **Separate HeyGen clips (`split_h` / `split_v`)**: Composite with `peer=`, every segment `z=1` (no zoom in either panel), with calibrated `-4 to 0 dB` beat SFX on key words.
   - **Pre-stitched Google Flow split**: `layout=single` with `slot=top|bottom|left|right` (or `active=top|bottom|left|right`), `z=1`.
   - **In-frame 3D hologram**: `layout=single`, `zoom=1.0`.
3. **Where to Zoom vs. Where NOT to Zoom**:
   - **Never zoom (`z=1`)**: every split screen (`split_h`, `split_v`, baked splits), 3D hologram shots, shared single-camera two-shots whose faces sit near the frame edges (`none` in `facts.md`), and clips with deliberate camera movement (pan/dolly).
   - **Zoom in full-frame `single` shots and safe live two-shots (`<= 1.10` with `focus=x,y`)**: `zoom=1.08` step punch-in to mask internal `> 0.25s` pause cuts or mid-sentence stumble cuts (`L5a` + `L5b`); smooth push-in (`1.0->1.06` / `1.0->1.07`) on turns `>= 2.5s`, pull-out (`1.08->1.0` / `1.06->1.0`), or rapid `sudden_in:1.08@<beat>` on punchlines and emphasis words (one `flash_zoom:1.12` at most, on the biggest full-frame peak).
4. **Premiere-Grade Auto-Captions (`#subs auto y=68`) & On-Screen Text (`21132` "Stop Saying" Hook)**:
   - **Animated Auto-Captions ON by Default**: Include `#subs auto y=68` in `plan.edl` on every reel (`y=50` automatically on the `split_v` seam so faces are never covered; set `#subs off` **only** if the user explicitly asks for no subtitles).
   - **Extra `banner:` Cards ONLY When Scripted**: Add stacked banners only where the script cues a `"Stop saying..."` list or strikethrough phrase (no unrequested decorative B-roll or spammy CTAs).
5. **Where to Show Errors (`✗` / `✓`) & Snappy Hold Timing (`0.35–0.42s`)**:
   - **Show `✗` (`wrong`) and `✓` (`right`) marks ONLY** when the script marks a learner mistake (`[WRONG]`) or correction (`[RIGHT]`).
   - **Snappy End-of-Dialogue Placement (`0.35–0.42s` Hold Tail, `0.48s` 2-Pulse Flash)**: Trigger `mark:wrong` or `mark:right` right as the sentence finishes (`at = <last_word_end - cut_start - 0.06>`), set duration to **`0.48s`**, and extend the segment `end` by **only `0.35–0.42s`** after `last_word_end` (never `0.70–1.00s` frozen tails!), playing calibrated post-`loudnorm` SFX (`sn_wrong.wav` at `+1.0 dB` or `correct_ding.wav` + `sn_correct.wav` at `+0.5 dB`).
6. **Transitions follow the story, never a clock (owner's rule, W45 editor feedback; overrides any older wording)**:
   - A transition marks a new place or time (a scene heading), entering or leaving the split (`tr=split_reveal@0.40`), or a separate call-to-action shot; everything else is a hard cut. Never between two cuts of the same shot, inside one line, or inside the split. `warm_flash`/`dip_to_white`/`flash_zoom` only when the script asks, once at most. Each format's own pattern: [`agent/FORMATS.md`](../../agent/FORMATS.md).
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
