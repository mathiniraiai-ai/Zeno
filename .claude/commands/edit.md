---
description: Run the Zeno 5-stage edit (inspect -> review brief/plan.edl/pitch card -> Yes/No gate -> execute) on a clips folder and script.
argument-hint: "<raw_dir_or_clips> <script_path_or_gdoc_url> [--lang <lang> --preset <auto|single|split_v|split_h|chat_bar|video_call|hybrid> --mark-pack <flash|vignette|glossy|default>]"
---

# `/edit` — Zeno Low-Token Video Edit

Run the **`scripts/zeno.py`** harness within the **`12k–18k tokens / min`** budget (strict ceiling: **`20k tokens / min`** of finished video). Read `agent/DIRECTOR.md` first if this is a fresh session.

User arguments (if provided): `$ARGUMENTS`

## Strict Guardrails
- **NEVER** modify files outside the Zeno folder (team PCs: `~/Documents/Zeno`; owner: `/Users/mathiniraiselvan/Documents/Office project/Zeno`).
- **NEVER** read `work/*/words.json`, `work/*/silences.json`, `work/*/align.json`, or `scripts/*.py`.
- **NEVER** open or print `.env`. Check setup with `python3 scripts/setup.py --smoke`.
- Read **only** `work/<project>/brief.txt`, `work/<project>/plan.edl`, `work/<project>/pitch_card.md`, and the final `work/<project>/marks_contact_sheet.jpg`.
- Finished videos go to `~/Downloads/Zeno Outputs/`, never inside the repo.

## Execution Steps

0. **Pre-job gate (every job, before anything else)**:
   - Run `python3 scripts/zeno.py --preflight` and show the PASS/FAIL table. If anything fails, stop and fix it with the user.
   - If all pass and the user has not given them yet, ask: "Share the script link (Google Doc tab) and the footage folder." Do not start without both. Footage must be local; Drive/web links are not downloaded.
   - `--inspect` then checks the script and each clip again and stops with `[BLOCKED]` on any problem. Never use `--skip-preflight`.

1. **Inspect (`1 command`, Stages 1–3)**:
   - Clips can live in any folder. If the user pasted the script in chat, save it to `input/script/<project>.txt` first.
   - Run (in the background; it prints progress every ~48s, also in `work/<project>/progress.json` / `python3 scripts/progress.py`):
     ```bash
     python3 scripts/zeno.py --inspect <raw_dir_or_clips> --script <script_path_or_gdoc_url> \
       [--lang <lang>] [--preset <preset>] [--mark-pack <pack>]
     ```
   - Relay stage updates to the user while it runs.

2. **Review & refine (`1 read + tiny edits`)**:
   - Read `brief.txt`, `plan.edl`, `pitch_card.md`.
   - Confirm: one `[BEST_TAKE]` per line (check `[ALT_TAKE]` scores, swap in `plan.edl` if wrong); `[WRONG_SPEAKER_NO_FIX]` means tell the user to reshoot; `[SPEAKER_CHECK] not applied` goes in the report; cuts snap to pauses; same-clip consecutive `single` cuts use `zoom=1.08`; layouts and ✓/✗ marks match the script.
   - Edit `plan.edl` directly, then refresh the card: `python3 scripts/zeno.py --pitch work/<project>/plan.edl`
   - Warn the user immediately if messy footage threatens `20k tokens / min`.

3. **Gate (`Mandatory Stop`)**: present the Editorial Pitch Card and ask the user **Yes/No**. Do not render before a Yes.

4. **Execute (`1 command` after Yes, Stages 4–5)**:
   ```bash
   python3 scripts/zeno.py --execute work/<project>/plan.edl [--square] [--loudness -11.5] [--gain-db +2.0]
   ```
   Renders the 9:16 master, `_1x1` square, `.timeline.txt`, and Premiere `.prproj` / `.premiere.xml`, then runs `verify.py`. Read `marks_contact_sheet.jpg` once to check ✓/✗ placement and speaker framing.

5. **Token report (mandatory)**: print this table and append the row to `README.md` (and note the agent and model used):
   ```markdown
   | Date | Task | Context tokens (start → end) | Tokens used | 5-hour limit | Weekly limit |
   |---|---|---|---|---|---|
   | YYYY-MM-DD | **<Language> – "<Title>"** (<N> clips, <M> script lines): <MM:SS> final, <K> ✓/✗ marks → `~/Downloads/Zeno Outputs/<name>.mp4` | <start> → <end> | ~<delta> | <start>% → <end>% | <start>% → <end>% |
   ```
