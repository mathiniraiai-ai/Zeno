---
trigger: always_on
description: Low-token 3-step video editing workflow, hard boundaries, layout presets, and mandatory token reporting for Zeno.
---

# Zeno — Always-On Workspace Rule (Antigravity / Gemini 4 Argon)

## 1. Hard Rules
- **Workspace Isolation**: Never modify any files outside `/Users/mathiniraiselvan/Documents/Office project/Zeno`. Never touch other projects in `Documents`.
- **Token Conservation**: Never read `work/S*/words.json`, `work/S*/silences.json`, or `scripts/*.py` unless debugging a specific word boundary or script exception.
- **Fresh Sessions**: Always run edits in a fresh session per video with unused MCP servers disabled.

## 2. Token Budget & Mandatory Reporting
- **Target**: `12k–18k tokens` per minute of finished video (**strict ceiling: `20k tokens / min`**; under `20k–30k total` for a typical 1.5-min edit).
- **Mid-Job Warning**: Warn the user immediately if messy footage or excessive retakes risk exceeding the `20k tokens / min` ceiling.
- **Mandatory End-of-Job Output**: Print the Token Usage table at the end of every job and append the new row to `README.md`:
  `| Date | Task | Context tokens (start → end) | Tokens used | 5-hour limit | Weekly limit |`

## 3. The 3-Step Workflow
1. **Analyse & Auto-Plan (`1 command`)**:
   `python3 scripts/prepare.py input/footage/*.mp4 --script input/script/<script>.txt --lang <lang> [--preset auto|single|split_v|split_h|chat_bar|video_call|hybrid] [--mark-pack default|glossy|flash|vignette]`
2. **Review Brief & Refine Plan (`1 read + 1 tiny edit`)**:
   Read ONLY `work/<project>/brief.txt` (`~900 tokens`) and `work/<project>/plan.edl` (`~240 tokens`). Verify the layout mode (`single`, `split_v`, `split_h`, `chat_bar`, `video_call`, or `hybrid`), retakes, and `✓`/`✗` marks. Edit `plan.edl` (or `plan.json`) only where adjustments are needed.
3. **Render & Verify (`1 command`)**:
   `python3 scripts/render.py work/<project>/plan.edl --verify`
   Renders the final MP4 to `output/`, runs deterministic script coverage & `-14 LUFS` loudness verification, and generates `work/<project>/marks_contact_sheet.jpg` (`1 image read` to verify `✓`/`✗` marks).

## 4. Dynamic Layouts & Swappable Mark Packs
- **Layouts**: `single` (full-frame + subtle zoom), `split_v` (top/bottom 50/50 split), `split_h` (left/right 50/50 split), `chat_bar` (speaker + floating prompt bar), `video_call` (speaker + rounded PiP card), and `hybrid` (`auto` mix of `single` for monologues and split/PiP/chat_bar for dialogues).
- **Mark Packs**: `default`, `glossy`, `flash`, `vignette`, or custom `right.png`/`wrong.png` inside `assets/marks/<pack>/`.

## Secrets (all agents)
Never open, print, quote or paste `.env` or any key in a chat or log. It holds the user's personal Zeno key (or a Gemini key). To check setup, run `python3 scripts/setup.py --smoke`; it reports [OK]/[FAIL] without showing keys. Read `.env.example` instead if you need the variable names.
