---
description: Same as /zeno (kept so older habits and launchers keep working).
argument-hint: "<raw_dir_or_clips> <script_path_or_gdoc_url> [--lang <lang>] [--style <name>] [--name \"<video name>\"]"
---
# /edit (alias for /zeno)

User inputs: `$ARGUMENTS` — follow `AGENTS.md` and `agent/DIRECTOR.md` (one video per chat; claude.ai connectors are off, so the editor gives the exact Notion name; at most 2 planner calls per video, and on `[PLANNER STOPPED]` report the `[BLOCKED]` lines and ask, never plan by hand; use supplied inputs directly):

1. **Turn 1 (Primary 1-call)**: Run `<PY> scripts/zeno.py --inspect <footage> --script <script> --name "<name>" --auto-plan --compact` (or `--preflight --compact` first if inputs are missing; never `--skip-preflight`). This plans in 1 isolated call, verifies `--check-plan`, and automatically renders 9:16 + 1:1 Square without asking Yes/No. When `--auto-plan` passes `[PLAN PASS]`, do NOT open packets/sheets/rules; deliver outputs + token report.
2. **Strict Manual / Fallback Cap (4–5 turns max, NEVER 40+)**:
   - **Turn 2**: Read `editing_packet_p*.md` (or `editing_packet.md`) + ALL `clip_sheet_*.jpg` in ONE parallel batch.
   - **Turn 3**: Write `script_analysis.md` and `plan.edl` (plus `decisions.txt` if needed) in ONE parallel batch.
   - **Turn 4**: Never pause to ask Yes/No before rendering (unless the user explicitly requested `--no-auto-render` / plan-only review). Run ONE combined check+render command: `<PY> scripts/zeno.py --execute <plan> --square --compact` (no separate `cost_control.py` + `--check-plan` + `--execute` roundtrips unless `--check-plan` is explicitly needed or the plan fails!). If a command backgrounds, **END TURN immediately** and wait for system completion notification — NEVER poll `manage_task`, `write_stdin`, `send_input`, or progress/log files!
   - **Turn 5**: `verify.py` checks both 9:16 and 1:1 Square framing and builds `review_sheet.jpg` automatically (never write custom FFmpeg/Python frame-extraction scripts). Read `review_sheet.jpg` ONCE (unless verify fails) and deliver exports + saved per-video token report. For targeted revisions, log `--feedback` first and use `edit_packet.py --line` + `--check-plan` (checking `cost_control.py` if retries repeat).
3. **`[DO NOT]` during edits**: NEVER open `agent/REFERENCE.md`, `CONCEPT.md`, `agent/EDIT_RULES.md`, `agent/FORMATS.md`, `scripts/*.py`, `clip_caches.json`, `facts.md`, `line_map.md`, `ffprobe`, or `dir`/`Get-ChildItem`/`ls` (`editing_packet.md` already holds syntax card, raw footage inventory, unmatched line diagnostics, and all words/candidates/rules).
