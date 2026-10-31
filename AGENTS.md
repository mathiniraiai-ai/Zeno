# AGENTS.md — Zeno (Antigravity Gemini 4 Argon & Dual-Agent Instructions)

Always-on operational rules for **Antigravity (Gemini 4 Argon)** and **Claude Code (Opus 4.5 / 5.5)** inside this Zeno folder (team PCs: `~/Downloads/Office Project/Zeno`; owner: `/Users/mathiniraiselvan/Documents/Office project/Zeno`).

**Agent entry point** (Antigravity, OpenAI and any other agent, not only Claude): read `agent/DIRECTOR.md` first. It sets the decision loop and points to one concept file per topic in `agent/concepts/`. Learned styles live in `styles/<name>/`. A style may allow captions or extra graphics only if its own `SKILL.md` says so; otherwise rule 4 below applies.

---

## 1. Hard Rules

1. **Strict Workspace Boundary**: Never modify, move, or delete any file outside this Zeno folder (team PCs: `~/Downloads/Office Project/Zeno`; owner: `/Users/mathiniraiselvan/Documents/Office project/Zeno`). Other projects in `Documents` are strictly off-limits.
2. **Folders**: Zeno runs from `~/Downloads/Office Project/Zeno` on team PCs (the installer puts it there). Every finished video (`.mp4` + `.timeline.txt`) goes to `~/Downloads/Zeno Outputs/`, never inside the repo. Raw clips can stay in any local folder: pass their path to `prepare.py`.
3. **Takes**: Every clip is transcribed. Clips that say the same thing are takes of one line, whatever their file names, their order, or even if the audio or video is identical (2, 3 or 5 copies): `prepare.py` scores all of them and keeps exactly ONE (`[BEST_TAKE]`); the others are listed as `[ALT_TAKE]` with scores in `brief.txt`. Never assume the first, middle or last take is right: check the scores and swap a take in `plan.edl` if needed.
4. **Never Read Raw Word JSONs or Python Source**:
   - **Never** read `work/*/words.json`, `work/*/silences.json`, or `work/*/align.json` unless isolating a single word boundary defect.
   - **Never** read `scripts/*.py` during normal editing jobs. Use the 3-step CLI workflow and `plan.edl` schema below.
5. **Fresh Context Per Video**: Start each new video edit in a fresh conversation with unused MCP connectors turned off.
6. **Clean Editorial Style**: No B-roll, no unrequested captions, no CTAs, no decorative clutter. Only best takes, pause-snapped cuts, dynamic speaker layouts, and script-driven `✓` / `✗` marks + SFX.

---

## 2. Token Budget & Mandatory Reporting

- **Target**: **`12k–18k tokens` per minute of finished video** (`~20k–30k total` for a typical 1.5-min edit).
- **Strict Ceiling**: **`20k tokens / min`** of finished video.
- **Early Warning**: Warn the user mid-job if messy footage (excessive retakes, garbled audio, or missing lines) risks exceeding `20k tokens / min`.
- **Mandatory End-of-Job Table**: Always output the Token Usage table at the end of every job and append a row to `README.md`:

```markdown
| Date | Task | Context tokens (start → end) | Tokens used | 5-hour limit | Weekly limit |
|---|---|---|---|---|---|
| YYYY-MM-DD | **<Language> – "<Title>"** (<N> clips, <M> script lines): <MM:SS> final, <K> ✓/✗ marks → `~/Downloads/Zeno Outputs/<name>.mp4` | <start> → <end> | ~<delta> | <start>% → <end>% | <start>% → <end>% |
```

---

## 3. The 3-Step Workflow

1. **Analyse & Auto-Plan (`1 command`)**:
   ```bash
   python3 scripts/prepare.py input/footage/*.mp4 \
     --script input/script/<script>.txt \
     --lang <lang> \
     [--preset auto|single|split_v|split_h|chat_bar|video_call|hybrid] \
     [--mark-pack default|glossy|flash|vignette]
   ```
2. **Review Brief & Refine Plan (`1 read + 1 tiny edit`)**:
   - Read **ONLY** `work/<project>/brief.txt` (`~900 tokens`) and `work/<project>/plan.edl` (`~240 tokens`).
   - Verify layout mode (`single`, `split_v`, `split_h`, `chat_bar`, `video_call`, or `hybrid`), retake pruning, pause-snapped cut points, and `✓`/`✗` marks.
   - Edit `work/<project>/plan.edl` (or `work/<project>/plan.json`) only where adjustments are needed.
3. **Render & Verify (`1 command`)**:
   ```bash
   python3 scripts/render.py work/<project>/plan.edl --verify
   ```
   - Renders `~/Downloads/Zeno Outputs/<name>.mp4` and `~/Downloads/Zeno Outputs/<name>.timeline.txt`, runs deterministic script coverage & `-14 LUFS` loudness verification, and outputs `work/<project>/marks_contact_sheet.jpg` (`1 image read` to verify `✓`/`✗` overlays).

---

## 4. Layout Modes & Swappable Mark Packs

- **Layouts (`--preset` or per-segment in `plan.edl`)**:
  - `single` — Full-frame speaker (`1080×1920`), `zoom=1.08` punch-in for jump cuts, `zoom=1.0->1.06` slow push.
  - `split_v` — Top/bottom 50/50 split (`1080×960` each) with divider & active speaker focus.
  - `split_h` — Left/right 50/50 vertical split (`540×1920` each) for rapid exchanges.
  - `chat_bar` — Main speaker + glassmorphic prompt bar displaying the target phrase/question.
  - `video_call` — Main speaker + rounded Picture-in-Picture (PiP) card for the second speaker.
  - `hybrid` (`auto`) — Dynamically combines `single` for teaching monologues with `split_v` / `video_call` / `chat_bar` for dialogue turns.
- **Mark Packs (`--mark-pack` or `# mark_pack:` in `plan.edl`)**:
  - `default` (flat circle badge), `glossy` (3D glass pill/badge), `flash` (badge + green/red border flash), `vignette` (soft corner radial glow + badge), or custom PNGs in `assets/marks/<pack>/`.
- **Skill Reference**: Activate `.agent/skills/video-edit/SKILL.md` for full `plan.edl` / `plan.json` schema details and troubleshooting.

## Secrets (all agents)
Never open, print, quote or paste `.env` or any key in a chat or log. It holds the user's personal Zeno key (or a Gemini key). To check setup, run `python3 scripts/setup.py --smoke`; it reports [OK]/[FAIL] without showing keys. Read `.env.example` instead if you need the variable names.
