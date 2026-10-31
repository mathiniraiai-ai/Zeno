# Zeno — Low-Token Operational Manual

Script-led AI video editing harness (`1080x1920` MP4). Given raw clips in `input/footage/` and a script in `input/script/`, output best takes, pause-snapped cuts, dynamic speaker layouts, and `✓`/`✗` marks + SFX to `~/Downloads/Zeno Outputs/`.

**Agent entry point**: read `agent/DIRECTOR.md` first. It sets the decision loop and points to one concept file per topic in `agent/concepts/` (script intake, footage intake, style library, reference learning, on-screen text, token budget). Learned styles live in `styles/<name>/`. A style may allow captions or extra graphics only if its own `SKILL.md` says so; otherwise rule 4 below applies.

## 1. Hard Rules
1. **Workspace Isolation**: Never modify any files outside this Zeno folder (team PCs: `~/Downloads/Office Project/Zeno`; owner: `/Users/mathiniraiselvan/Documents/Office project/Zeno`). Never touch other projects in `Documents`.
2. **Folders**: Zeno runs from `~/Downloads/Office Project/Zeno` on team PCs (the installer puts it there). Every finished video (`.mp4` + `.timeline.txt`) goes to `~/Downloads/Zeno Outputs/`, never inside the repo. Raw clips can stay in any local folder: pass their path to `prepare.py`.
3. **Takes**: Every clip is transcribed. Clips that say the same thing are takes of one line, whatever their file names, their order, or even if the audio or video is identical (2, 3 or 5 copies): `prepare.py` scores all of them and keeps exactly ONE (`[BEST_TAKE]`); the others are listed as `[ALT_TAKE]` with scores in `brief.txt`. Never assume the first, middle or last take is right: check the scores and swap a take in `plan.edl` if needed.
4. **Speakers**: The script names who speaks each line (`L2 Robot: ...`). `prepare.py` measures the voices and cuts any line spoken by the wrong character (`[WRONG_SPEAKER]` in `brief.txt`), then looks in every clip, including unused takes, for that line said by the right character and splices it in (`[FILL]`). `[WRONG_SPEAKER_NO_FIX]` means no clip has the right voice saying it: tell the user to reshoot. `[SPEAKER_CHECK] not applied` means the voices could not be told apart: say so in the report. Reference samples in `input/voices/<Speaker>.wav` make this reliable with few clips.
5. **Never Read Raw JSONs or Python Source**:
   - **Never** read `work/*/words.json`, `work/*/silences.json`, or `work/*/align.json` unless debugging a specific word timing.
   - **Never** read `scripts/*.py` during normal edits. Use the 3-step CLI workflow and `plan.edl` syntax below.
6. **Fresh Session**: Start every video in a fresh session (`/clear` or new chat) with unused MCP connectors disabled.
7. **Clean Editorial Style**: No B-roll, no unrequested captions, no CTAs, no extra graphics.

## 2. Token Budget & Mandatory Reporting
- **Target**: **`12k–18k tokens` per minute of finished video** (`~20k–30k total` for a typical 1.5-min edit).
- **Strict Ceiling**: **`20k tokens / min`** of finished video.
- **Mid-Job Warning**: Warn the user immediately if messy footage or excessive retakes risk exceeding `20k tokens / min`.
- **Mandatory End-of-Job Output**: Print the Token Usage table at the end of every job **and** append a row to `README.md`:

```markdown
| Date | Task | Context tokens (start → end) | Tokens used | 5-hour limit | Weekly limit |
|---|---|---|---|---|---|
| YYYY-MM-DD | **<Language> – "<Title>"** (<N> clips, <M> script lines): <MM:SS> final, <K> ✓/✗ marks → `~/Downloads/Zeno Outputs/<name>.mp4` | <start> → <end> | ~<delta> | <start>% → <end>% | <start>% → <end>% |
```

## 3. The 3-Step Workflow

1. **Analyse & Auto-Plan (`1 command`)**:
   ```bash
   python3 scripts/prepare.py input/footage/*.mp4 --script input/script/<script>.txt --lang <lang> [--preset auto|single|split_v|split_h|chat_bar|video_call|hybrid] [--mark-pack default|glossy|flash|vignette]
   ```
   - `<lang>`: `en`, `kn`/`kannada`, `ta`/`tamil`, `hi`/`hindi`, `te`/`telugu`, `ml`/`malayalam`, or `auto`.
2. **Review Brief & Refine Plan (`1 read + 1 tiny edit`)**:
   - Read **ONLY** `work/<project>/brief.txt` (`~900 tokens`) and `work/<project>/plan.edl` (`~240 tokens`).
   - Verify layout mode (`single`, `split_v`, `split_h`, `chat_bar`, `video_call`, or `hybrid`), retake removal, pause-snapped cut points, and `✓`/`✗` marks.
   - Edit `work/<project>/plan.edl` (or `plan.json`) only where adjustments are needed.
3. **Render & Verify (`1 command`)**:
   ```bash
   python3 scripts/render.py work/<project>/plan.edl --verify
   ```
   - Renders `~/Downloads/Zeno Outputs/<name>.mp4` + `~/Downloads/Zeno Outputs/<name>.timeline.txt`, runs deterministic script coverage & `-14 LUFS` loudness check, and generates `work/<project>/marks_contact_sheet.jpg` (`1 image read` to verify `✓`/`✗` marks).

## 4. The 5 Dynamic Layout Modes & Swappable Mark Packs

| Layout Mode | Visual Layout (`1080×1920`) | When to Use |
|---|---|---|
| `single` | Full-frame clip. Use `zoom=1.08` punch-in on jump cuts, or `zoom=1.0->1.06` slow push. | Solo teaching lines, hooks, wrap-ups. |
| `split_v` | Top/bottom 50/50 split (`1080×960` each) with divider & inactive speaker dim. | Two-character Q&A or roleplay dialogues. |
| `split_h` | Left/right 50/50 vertical split (`540×1920` each) with active border highlight. | Fast back-and-forth exchanges. |
| `chat_bar` | Full-frame speaker + frosted glassmorphic prompt bar (`label="Phrase..."`). | Language drills ("How to say X?"). |
| `video_call` | Full-frame speaker + rounded Picture-in-Picture (PiP) card (`pip=S1@3.2`). | Remote call / FaceTime style dialogues. |
| `hybrid` | Mixes `single` on teaching monologues with `split_v`/`video_call`/`chat_bar` on dialogue turns. | **Default (`auto`)** — best retention. |

**Swappable `✓`/`✗` Mark Packs (`--mark-pack` or `# mark_pack:`)**:
- `default`: Flat circle badge (`assets/marks/right.png`, `wrong.png`)
- `glossy`: 3D glass gradient badge with shadow (`assets/marks/glossy/`)
- `flash`: Badge + subtle green/red edge flash (`assets/marks/flash/`)
- `vignette`: Soft corner radial glow + badge (`assets/marks/vignette/`)
- Custom: Place `right.png` & `wrong.png` in `assets/marks/<pack>/`.

## 5. `plan.edl` Quick Syntax

```text
# output: output/Kannada_I_am_hungry.mp4
# canvas: 1080x1920@30
# preset: hybrid
# mark_pack: glossy
# loudness: -14

# SEGMENTS: id | src | start-end | layout | zoom | transition | extras | note
L1 | S1 | 1.12-5.48 | single | 1.0->1.06 | cut | focus=0.5,0.45 | Hook question
L2 | S2 | 0.85-4.90 | split_v:top=S1@5.48 | 1.0 | cut | active=bottom | Wrong student attempt
L3 | S3 | 1.05-6.20 | chat_bar | 1.08 | fade:0.3 | label="ನನಗೆ ಹಸಿವಾಗಿದೆ" | Teacher correction

# MARKS: type | segment | at | duration | pos | size | sfx | gain_db
wrong | L2 | 1.40 | 1.3 | top-right | 0.22 | buzzer.wav | -4
right | L3 | 2.10 | 1.3 | top-right | 0.22 | correct_ding.wav | -4

# SFX: file | segment | at | gain_db
whoosh.wav | L3 | 0.0 | -10
```
- **SFX (`assets/sfx/`)**: Right: `correct_ding.wav`, `sn_correct.wav`, `w42_right.wav` | Wrong: `buzzer.wav`, `sn_wrong.wav`, `w42_wrong.wav` | Transitions: `whoosh.wav`, `pop.wav`.

## Secrets (all agents)
Never open, print, quote or paste `.env` or any key in a chat or log. It holds the user's personal Zeno key (or a Gemini key). To check setup, run `python3 scripts/setup.py --smoke`; it reports [OK]/[FAIL] without showing keys. Read `.env.example` instead if you need the variable names.
