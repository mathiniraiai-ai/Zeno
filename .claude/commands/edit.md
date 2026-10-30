---
description: Run the low-token 3-step video editing workflow (prepare -> review brief/plan.edl -> render --verify) on input/footage/ and input/script/.
argument-hint: "[optional: --lang <lang> --preset <auto|single|split_v|split_h|chat_bar|video_call|hybrid> --mark-pack <default|glossy|flash|vignette>]"
---

# `/edit` — Zeno Automated Low-Token Video Edit Command

Execute the complete 3-step **Zeno** pipeline within the **`12k–18k tokens / min`** budget (strict ceiling: **`20k tokens / min`**).

User arguments (if provided): `$ARGUMENTS`

## Strict Guardrails
- **NEVER** modify files outside `/Users/mathiniraiselvan/Documents/Office project/Zeno`.
- **NEVER** read `work/*/words.json`, `work/*/silences.json`, `work/*/align.json`, or `scripts/*.py`.
- Read **only** `work/<project>/brief.txt`, `work/<project>/plan.edl`, and the final `work/<project>/marks_contact_sheet.jpg`.

## Execution Steps

1. **Locate Inputs & Run `prepare.py` (`1 command`)**:
   - Check `input/footage/` and `input/script/` (if the user pasted the script in chat, save it to `input/script/<project>.txt` first).
   - Run:
     ```bash
     python3 scripts/prepare.py input/footage/*.mp4 \
       --script input/script/<script>.txt \
       --lang <lang_or_auto> \
       --preset <preset_or_auto> \
       --mark-pack <pack_or_default>
     ```

2. **Review Brief & Refine `plan.edl` (`1 read + 1 tiny edit`)**:
   - Read `work/<project>/brief.txt` and `work/<project>/plan.edl`.
   - Confirm:
     - Only the cleanest take of each scripted line is kept (all retakes, stutters, dead air, and end-of-clip AI babble removed).
     - Cut points (`start-end`) snap cleanly into pauses (`0.08–0.15s` breath padding).
     - Same-clip consecutive `single` cuts have `zoom=1.08` punch-in; topic transitions use `fade:0.3`.
     - Layout mode (`single`, `split_v`, `split_h`, `chat_bar`, `video_call`, or `hybrid`) and `✓`/`✗` marks (`right`/`wrong`) match the script's conversational structure and right/wrong cues.
   - If any take, timestamp, layout, or mark needs refinement, edit `work/<project>/plan.edl` directly.
   - If messy footage threatens to push token usage past `20k tokens / min`, warn the user immediately.

3. **Render & Verify (`1 command + 1 image read`)**:
   - Run:
     ```bash
     python3 scripts/render.py work/<project>/plan.edl --verify
     ```
   - Read `work/<project>/marks_contact_sheet.jpg` (`1 image read`) to visually verify `✓`/`✗` badge placement and speaker framing.

4. **Mandatory Token Usage Report**:
   - Append a new row to the `## Token usage log` table in `README.md`.
   - Output the final delivery summary and Token Usage table:
     ```markdown
     | Date | Task | Context tokens (start → end) | Tokens used | 5-hour limit | Weekly limit |
     |---|---|---|---|---|---|
     | YYYY-MM-DD | **<Language> – "<Title>"** (<N> clips, <M> script lines): <MM:SS> final, <K> ✓/✗ marks → `output/<name>.mp4` | <start> → <end> | ~<delta> | <start>% → <end>% | <start>% → <end>% |
     ```
