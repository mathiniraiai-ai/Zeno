---
description: Run a Zeno edit. Zeno measures and renders; you make every editing decision and write plan.edl.
argument-hint: "<raw_dir_or_clips> <script_path_or_gdoc_url> [--lang <lang>] [--style <name>]"
---

# `/edit` — Zeno Video Edit

You are the editor. Zeno transcribes, measures and renders; every editing decision is yours. Budget: **50k to 70k tokens per video**.

User arguments (if provided): `$ARGUMENTS`

## Guardrails
- **NEVER** modify files outside the Zeno folder.
- **NEVER** read `work/*/words.json`, `silences.json`, `align.json` or `scripts/*.py` (`facts.md` has the words).
- **NEVER** open or print `.env`. Check setup with `python3 scripts/setup.py --smoke`.
- Finished videos go to `~/Downloads/Zeno Outputs/`, never inside the repo.

## Steps
0. **Pre-job gate.** Run `python3 scripts/zeno.py --preflight` and show the PASS/FAIL table. If anything fails, stop and fix it with the user. If all pass and you do not have them yet, ask: "Share the script link (Google Doc tab) and the footage folder." Never use `--skip-preflight`.
1. **Measure.** In the background: `python3 scripts/zeno.py --inspect <footage> --script <script> [--lang <lang>] [--style <name>]`. It stops with `[BLOCKED]` on any input problem.
2. **Edit.** Read `agent/DIRECTOR.md` and `agent/EDIT_RULES.md`. Then read `line_map.md`, `facts.md` and every `clip_sheet_<n>.jpg` in `work/<project>/`, and write all segments of `plan.edl`.
3. **Check.** `python3 scripts/zeno.py --check-plan work/<project>/plan.edl`. Fix the plan until it passes.
4. **Ask once.** Show the user the plan (clip, words, layout per line, plus anything you are unsure of) and ask Yes/No. Do not ask again after Yes.
5. **Render.** `python3 scripts/zeno.py --execute work/<project>/plan.edl`. Read the verify output and `marks_contact_sheet.jpg`. If anything fails, fix `plan.edl` and render again.
6. **Report.** Print the Token Usage table and add a row to `README.md`.
