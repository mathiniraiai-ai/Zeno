---
name: video-edit
description: End-to-end low-token script-led video editing workflow for Zeno. Use whenever editing raw footage in input/footage/ against a script in input/script/, selecting takes, applying dynamic speaker layouts (single, split_v, split_h, chat_bar, video_call, hybrid), placing right/wrong (✓/✗) marks and SFX, or rendering and verifying final MP4 outputs.
---

# Zeno Video Edit Skill (`video-edit`)

Complete reference for executing a script-led video edit inside this Zeno folder (team PCs: `~/Documents/Zeno`; owner: `/Users/mathiniraiselvan/Documents/Office project/Zeno`) under the **12k–18k tokens/min** target budget (strict ceiling: **20k tokens/min**).

---

## 1. Non-Negotiable Guardrails

1. **Workspace Isolation**: Only read and write files inside this Zeno folder (team PCs: `~/Documents/Zeno`; owner: `/Users/mathiniraiselvan/Documents/Office project/Zeno`). Never modify any other project in `Documents`.
2. **Folders**: Zeno runs from `~/Documents/Zeno` on team PCs (the installer puts it there). Every finished video (`.mp4` + `.timeline.txt`) goes to `~/Downloads/Zeno Outputs/`, never inside the repo. Raw clips can stay in any local folder: pass their path to `prepare.py`.
3. **Takes**: Every clip is transcribed. Clips that say the same thing are takes of one line, whatever their file names, their order, or even if the audio or video is identical (2, 3 or 5 copies): `prepare.py` scores all of them and keeps exactly ONE (`[BEST_TAKE]`); the others are listed as `[ALT_TAKE]` with scores in `brief.txt`. Never assume the first, middle or last take is right: check the scores and swap a take in `plan.edl` if needed.
4. **Speakers**: The script names who speaks each line (`L2 Robot: ...`). `prepare.py` measures the voices and cuts any line spoken by the wrong character (`[WRONG_SPEAKER]` in `brief.txt`), then looks in every clip, including unused takes, for that line said by the right character and splices it in (`[FILL]`). `[WRONG_SPEAKER_NO_FIX]` means no clip has the right voice saying it: tell the user to reshoot. `[SPEAKER_CHECK] not applied` means the voices could not be told apart: say so in the report. Reference samples in `input/voices/<Speaker>.wav` make this reliable with few clips.
5. **Zero Raw Telemetry Reads**:
   - **NEVER** read `work/*/words.json`, `work/*/silences.json`, or `work/*/align.json`.
   - **NEVER** read `scripts/*.py` to discover flags or schema fields—everything is documented in this skill.
   - Only read `work/<project>/brief.txt` (`~900 tokens`) and `work/<project>/plan.edl` (`~240 tokens`).
6. **Editorial Minimalism**:
   - Best takes only; all retakes, false starts, stutters, dead air, and end-of-clip AI babble removed.
   - Hard cuts by default, snapped into natural pauses (`0.08–0.15s` breath padding).
   - `0.3s` `fade` only at major topic changes.
   - `8–10%` punch-in (`zoom=1.08` or `1.10`) to hide same-speaker jump cuts; occasional slow push (`zoom=1.0->1.06`).
   - `✓` (`right`) and `✗` (`wrong`) badges + SFX placed at the exact spoken moment a phrase is judged right or wrong.

---

## 2. Step-by-Step Execution Procedure

### Step 1: Run `prepare.py` (1 Command)

Identify the footage files in `input/footage/` and the script in `input/script/` (or save a user-pasted script to `input/script/<project>.txt`). Run:

```bash
python3 scripts/prepare.py input/footage/*.mp4 \
  --script input/script/<script>.txt \
  --lang <lang> \
  --preset <auto|single|split_v|split_h|chat_bar|video_call|hybrid> \
  --mark-pack <default|glossy|flash|vignette>
```

**Supported `--lang` values**:
- `en` (English — Gemini Flash-Lite + Meta Wav2Vec2 95M forced alignment)
- `kn` / `kannada`, `ta` / `tamil`, `hi` / `hindi`, `te` / `telugu`, `ml` / `malayalam` (Indic languages — Gemini Flash-Lite transcript + Meta Wav2Vec2 95M forced aligner with `uroman` & Indic G2P)
- `auto` (auto-detects spoken language)

**What `prepare.py` generates automatically**:
- Extracts audio, detects silences, transcribes clips, and runs sub-word forced alignment (`<0.25s/clip` on Apple Silicon M2).
- Aligns clips against `input/script/<script>.txt`, prunes retakes/false starts, snaps cuts to pauses, and detects `✓`/`✗` cue words.
- Writes:
  - `work/<project>/brief.txt` — Human/LLM-readable summary of all clips, candidate takes, detected pauses, and auto-selected cuts (`~900 tokens`).
  - `work/<project>/plan.edl` — Compact pipe-delimited edit decision list (`~240 tokens`).
  - `work/<project>/plan.json` — Full JSON mirror of `plan.edl`.

---

### Step 2: Review `brief.txt` & Refine `plan.edl` (1 Read + 1 Edit)

1. Read `work/<project>/brief.txt` and `work/<project>/plan.edl`.
2. Check every segment against the script:
   - **Completeness**: Every script line is covered in the exact scripted order.
   - **Retake Removal**: If a clip has multiple attempts at a line, confirm the cleanest, most natural take is selected (typically the last complete attempt, watching out for trailing AI babble or repeated words at clip edges).
   - **Pause-Snapped Cuts**: Confirm `start` and `end` timestamps sit inside a silence/pause window or have `0.08–0.15s` padding around the first/last word.
   - **Jump-Cut Disguise**: If two consecutive `single`-layout segments come from the same clip (`src`), set `zoom=1.08` (or `1.10`) on the second segment.
   - **Layout Dynamics**: Confirm the layout mode matches the scene type (see Section 3).
   - **Marks & SFX**: Confirm every right/wrong teaching moment has a `right` (`✓`) or `wrong` (`✗`) mark timed to when the judgement or target phrase occurs (`at` is seconds relative to the segment's `start`).
3. Edit `work/<project>/plan.edl` (or `work/<project>/plan.json`) with your adjustments.

---

### Step 3: Render & Verify in One Pass (1 Command)

```bash
python3 scripts/render.py work/<project>/plan.edl --verify
```

**What `render.py --verify` does**:
1. Parses `plan.edl` (and syncs `plan.json`).
2. Renders each segment with exact frame rounding, `12ms` edge audio fades (zero clicks), dynamic layout compositing, and two-pass `-14 LUFS` EBU R128 loudness normalization.
3. Writes `~/Downloads/Zeno Outputs/<name>.mp4` (H.264 CRF 18, AAC 192 kbps) and `~/Downloads/Zeno Outputs/<name>.timeline.txt`.
4. **Deterministic Verification**:
   - Checks that all scripted lines are present and in order without re-running expensive LLM loops.
   - Verifies audio loudness and absence of clipping/dropouts.
   - Generates `work/<project>/marks_contact_sheet.jpg` showing a composite grid of every `✓`/`✗` mark frame with timecodes.
5. **Visual Check (`1 image read`)**:
   - View `work/<project>/marks_contact_sheet.jpg` to confirm `✓`/`✗` badges and layouts look clean and unobstructed.

---

## 3. Layout Selection Rules (5 Modes + `hybrid` + Pre-Stitched Split Screen)

Choose the layout preset via `--preset` or per-segment in `plan.edl` (see also [`styles/micronarrative/SKILL.md`](../../../styles/micronarrative/SKILL.md), [`agent/DIRECTOR.md`](../../../agent/DIRECTOR.md), and [`agent/concepts/word_boundary_training.md`](../../../agent/concepts/word_boundary_training.md)):

| Layout Mode | Visual Structure (`1080×1920`) | Best Used For | Key Segment Extras |
|---|---|---|---|
| `single` | Full-screen vertical frame (`1080×1920`). | Teacher monologue, intro hook, live conversation shots, **OR pre-stitched Google Flow vertical split-screen clips** (matched straight without re-compositing). | `focus=0.5,0.45`, `zoom=1.08`, `zoom=1.0->1.06`, `zoom_type=flash_zoom` |
| `split_v` | Top/bottom 50/50 split (`1080×960` each) with clean divider line and subtle dimming on inactive speaker. | Two-person dialogue when Character A and Character B are **separate raw files** (e.g., HeyGen separate renders). | `top=S1@5.4`, `bottom=S2`, `active=bottom` |
| `split_h` | Left/right 50/50 vertical split (`540×1920` each) with divider. | Side-by-side split screen when Character A and Character B are **separate raw files**. | `left=S1@5.4`, `right=S2`, `active=right` |
| `chat_bar` | Full-screen speaker + frosted glassmorphic chat bar overlay showing the prompt/phrase. | Language drills ("How do you say 'I am hungry' in Kannada?"). | `label="Prompt text"`, `bar_pos=bottom` |
| `video_call` | Full-screen active speaker + rounded Picture-in-Picture (PiP) card in top-left/top-right corner. | Simulated phone/video call roleplay between two clips. | `pip=S1@5.4`, `pip_pos=top-left` |
| `hybrid` | Automatically uses `single` for teaching/monologue lines and `split_v` / `video_call` / `chat_bar` for conversational turns. | **Recommended default (`auto`)** for multi-clip educational reels. | Any per-segment layout override |

### 3B. Macro / Micronarrative & Pre-Stitched Split-Screen Rules ([`styles/micronarrative/SKILL.md`](../../../styles/micronarrative/SKILL.md))

1. **Google Flow Pre-Stitched Vertical Split Screen vs. Separate HeyGen Clips**:
   - **Pre-Stitched (Google Flow)**: When the raw clip already has the vertical split screen baked into a single video frame, **match the clips straight as `layout=single`**. Never use `split_h`/`split_v` with `peer=` on already-stitched footage.
   - **Separate Clips (HeyGen)**: When Character A and Character B are separate raw video files, composite them via `split_h` or `split_v` with `peer=<clip>@<in>..<out>`.
2. **Where to Zoom vs. Where NOT to Zoom**:
   - **NO Zoom / NO Zoom-Out (`zoom=1.0`)**: Never apply zoom-in or zoom-out on **pre-stitched split-screen clips**, **continuous two-shots**, or clips where OpenCV (`detect_static_camera_motion`) detects existing camera motion.
   - **Where to Zoom**: Apply 3× supersampled sub-pixel zooms (`zoom=1.08` jump-cut punch-in, `mild_in` `1.0->1.06`, `mild_out` `1.08->1.0`, or `flash_zoom`) **only** on static single-speaker shots.
3. **Where to Put On-Screen Text vs. Where NOT to**:
   - Place on-screen text banners (e.g., `"Stop saying: ..."`) **only** where explicitly requested by the script. Never add unrequested captions or subtitles.
4. **Where to Show Errors (`✗` / `✓`) vs. Where NOT to**:
   - Place `wrong` (`✗`) and `right` (`✓`) marks **only** on explicit spoken-English learner mistake and corrected-repeat beats, using the **Minimal 2-Pulse End-of-Sentence Flash** (`0.50` alpha subtle Red/Green frame glow flashing **2 times** over `0.56s` right as the sentence finishes).
5. **Where Transitions Are Added**:
   - Place transitions (`warm_flash@0.38`, `whip_left`, `whip_right`, `zoom_burst`, `flash_zoom`, `slide_up`) at **scene boundaries**, backed by **Zero-Audio-Loss (`tpad` video tail clone + `0.00s` audio concat)** so zero words or syllables are ever clipped.

---

## 4. Swappable `✓` / `✗` Mark Packs & Minimal 2-Pulse End-of-Sentence Overlays

Configure via `--mark-pack <pack>` on `prepare.py` or `# mark_pack: <pack>` in `plan.edl`:

| Pack Name | Visual Style | Asset Path |
|---|---|---|
| `flash` (**default**) | Full-frame `1080×1920` subtle `0.50` alpha green/red glow that triggers **at the end of the wrong/right sentence** and **flashes 2 times (`0.56s`: Pulse 1 → OFF → Pulse 2)** | `assets/marks/green_flash.png`, `assets/marks/red_flash.png` |
| `vignette` | Full-frame `1080×1920` radial edge glow (`0.50` alpha, 2 pulses at sentence end) | `assets/marks/w42_green_vignette.png`, `assets/marks/w42_red_vignette.png` |
| `default` | Classic high-contrast flat green circle `✓` and red circle `✗` badge | `assets/marks/right.png`, `assets/marks/wrong.png` |
| `glossy` | 3D glassmorphic pill/circle badge with specular highlight & drop shadow | `assets/marks/glossy_right.png`, `assets/marks/glossy_wrong.png` |
| `<custom>` | Any user-supplied `right.png` & `wrong.png` placed in `assets/marks/<custom>/` | `assets/marks/<custom>/right.png`, `wrong.png` |

**Available SFX (`assets/sfx/`)**:
- **Wrong (`✗`, strong double-layered error sound at `+4 dB`)**: `buzzer.wav` + `kn20228_wrong_core.mp3` (auto-layered on strobe marks), `sn_wrong.wav`, `w42_wrong.wav`
- **Right (`✓`, crisp double-layered ding at `+2.5 dB`)**: `correct_ding.wav` + `sn_correct.wav` (auto-layered on strobe marks), `w42_right.wav`
- **Transitions / Accents (`-2 dB`)**: `whoosh.wav` (custom Whoosh SFX, auto-triggered on visual transitions or via `sfx whoosh.wav seg=<id> at=0.0 gain=-2`), `pop.wav`

---

## 4B. Mandatory 5-Agent Parallel Workflow & Agent 5 Deep Post-Render Analyzer

For every video edit (defaulting to **Antigravity** or **Claude Code**), assign at least **5 specialized agents**:

1. **Agent 1 — Script Coverage & Take Auditor**: Verifies 100% verbatim script coverage in order, speaker identity, and best-take selection across all clips.
2. **Agent 2 — Pause-Cut & De-Duplication Specialist**: Eliminates repeated dialogues/words/false starts (both intra-clip retakes like `"Almost, almost"` or 3x repeated words, and inter-clip boundary overlaps) and tightens empty internal silences (`>0.85s` dead air) using 10ms WAV RMS energy boundaries.
3. **Agent 3 — Dynamic Zoom & Layout Choreographer**: Assigns punch-ins (`z=1.08`) on same-clip jump cuts, slow push-ins (`z=1.0->1.06`), pull-outs (`z=1.08->1.0`), and speaker layouts.
4. **Agent 4 — Transitions, Rapid-Strobe Overlays & SFX Designer**: Places scene transitions (`smoothleft`, `smoothright`, `warm_flash`, `fade`) + custom `whoosh.wav` SFX (`-2 dB`), and configures full-frame rapid-strobe Red/Green PNG overlays (`mark_pack: flash`) with strong `+4 dB` error buzzer and `+2.5 dB` right ding SFX.
5. **Agent 5 — Post-Render Deep Video Analyzer & Auto-Fixer (Mandatory Post-Render Gate)**:
   - Runs **after** `render.py` generates the final MP4.
   - Uses local Python dependencies (`scripts/verify.py`, cached `words.json`/`hq_words.json` timestamps, FFmpeg `silencedetect`, 10ms WAV RMS energy, and RGB frame inspection — **without** sending the video to external APIs) to inspect the complete output video.
   - Verifies: (a) **0 repeated words/dialogues** inside or across segments, (b) **0 empty silences `>1.05s`** in the rendered audio stream, (c) **active transitions & `whoosh.wav` SFX**, and (d) **full-frame rapid-strobe Red/Green PNG overlays + strong error/right SFX**.
   - If any defect is detected, Agent 5 pinpoints the exact segment/timestamp, fixes `plan.edl`, and re-renders before delivery.

---

## 5. Schema Reference: `plan.edl` & `plan.json`

### 5.1 `plan.edl` Format (Preferred — Uses 75% Fewer Tokens)

```text
# output: output/Kannada_I_am_hungry.mp4
# canvas: 1080x1920@30
# preset: hybrid
# mark_pack: default
# loudness: -14

# SEGMENTS: id | src | start-end | layout | zoom | transition | extras | note
L1 | S1 | 1.12-5.48 | single | 1.0->1.06 | cut | focus=0.5,0.45 | Hook question
L2 | S2 | 0.85-4.90 | split_v:top=S1@5.48 | 1.0 | cut | active=bottom | Wrong student answer
L3 | S3 | 1.05-6.20 | single | 1.08 | fade:0.3 | focus=0.5,0.4 | Teacher explanation
L4 | S4 | 0.60-4.15 | chat_bar | 1.0 | cut | label="ನನಗೆ ಹಸಿವಾಗಿದೆ" | Correct phrase drill

# MARKS: type | segment | at | duration | pos | size | sfx | gain_db
wrong | L2 | 1.40 | 1.3 | top-right | 0.22 | buzzer.wav | -4
right | L4 | 1.80 | 1.3 | top-right | 0.22 | correct_ding.wav | -4

# SFX: file | segment | at | gain_db
whoosh.wav | L3 | 0.0 | -10
```

- **`src`**: Clip shorthand (`S1` resolves to `input/footage/S1.mp4`) or relative path (`input/footage/S1.mp4`).
- **`start-end`**: In/out points in seconds (e.g., `1.12-5.48`).
- **`zoom`**: Static zoom (`1.0`, `1.08`) or eased push (`1.0->1.06`).
- **`transition`**: `cut` (default), `fade:0.3`, `fadeblack:0.3`, or any FFmpeg `xfade` transition (`wipeleft:0.25`, `smoothleft:0.25`).
- **`at` (in MARKS / SFX)**: Offset in seconds from the **start of the referenced segment** (`0.0` = beginning of that segment).

### 5.2 `plan.json` Format (Full JSON Equivalent)

```json
{
  "output": "output/Kannada_I_am_hungry.mp4",
  "preset": "hybrid",
  "mark_pack": "default",
  "canvas": {"width": 1080, "height": 1920, "fps": 30},
  "segments": [
    {
      "id": "L1",
      "src": "input/footage/S1.mp4",
      "start": 1.12,
      "end": 5.48,
      "layout": "single",
      "zoom": 1.0,
      "zoom_to": 1.06,
      "focus": [0.5, 0.45],
      "transition": "cut",
      "transition_duration": 0.3,
      "note": "Hook question"
    },
    {
      "id": "L2",
      "src": "input/footage/S2.mp4",
      "start": 0.85,
      "end": 4.90,
      "layout": "split_v",
      "partner_src": "input/footage/S1.mp4",
      "partner_at": 5.48,
      "active": "bottom",
      "zoom": 1.0,
      "transition": "cut",
      "note": "Wrong student answer"
    }
  ],
  "marks": [
    {
      "type": "wrong",
      "segment": "L2",
      "at": 1.40,
      "duration": 1.3,
      "position": "top-right",
      "size": 0.22,
      "pack": "default",
      "sfx": "buzzer.wav",
      "sfx_gain_db": -4
    }
  ],
  "sfx": [
    {"file": "whoosh.wav", "segment": "L3", "at": 0.0, "gain_db": -10}
  ],
  "audio": {"loudness": -11.5}
}
```

- **1:1 Square Meta Ads Export (`--square`)**: Run `python3 scripts/render.py work/<project>/plan.edl --verify --square` (or `python3 scripts/render.py <video.mp4> --square`) to generate `<name>_1x1.mp4` (`1080×1080`: Layer 1 is a `1:1` center-cropped background with 5–10% Gaussian blur `gblur=sigma=18`; Layer 2 is the full `9:16` clip centered on top).
- **Per-Segment Audio Cap & Exact `dB` Override**: Every segment is automatically normalized to `-11.5 LUFS` (`-1.0 dBTP` hard limiter) before stitching. Add `vol=+3dB` (or `vol=-2dB`) to any segment in `plan.edl` (or `--gain-db +2.0` on CLI) to set the exact dB on any section.

---

## 6. Mandatory Token Reporting Template

At the conclusion of every video edit:
1. Warn the user if context growth approached or exceeded **`20k tokens / min`** of finished video and note why (e.g., unscripted retakes or corrupt clips).
2. Append the job's metrics row to `README.md` under `## Token usage log`.
3. Display the completed summary table in your final response:

```markdown
### Edit Summary & Token Usage
- **Output Video**: `~/Downloads/Zeno Outputs/<name>.mp4` (`<MM:SS>`, `<N>` segments, `<K>` ✓/✗ marks)
- **Layout & Marks**: Preset `<preset>`, Mark Pack `<mark_pack>`
- **Timeline & Verification**: `~/Downloads/Zeno Outputs/<name>.timeline.txt` | `work/<project>/marks_contact_sheet.jpg`

| Date | Task | Context tokens (start → end) | Tokens used | 5-hour limit | Weekly limit |
|---|---|---|---|---|---|
| YYYY-MM-DD | **<Language> – "<Title>"** (<N> clips, <M> script lines): <MM:SS> final, <K> ✓/✗ marks → `~/Downloads/Zeno Outputs/<name>.mp4` | <start> → <end> | ~<delta> | <start>% → <end>% | <start>% → <end>% |
```
