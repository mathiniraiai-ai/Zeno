---
description: Same as /zeno (kept so older habits and launchers keep working).
argument-hint: "<raw_dir_or_clips> <script_path_or_gdoc_url> [--lang <lang>] [--style <name>] [--name \"<video name>\"]"
---
# /edit (alias for /zeno)

User inputs: `$ARGUMENTS` — follow `AGENTS.md` and `agent/DIRECTOR.md` (multi-video queue up to `1M` context tokens with Notion/Meta connectors enabled; read exact Notion name from Notion if shared; use supplied inputs directly):

1. Run `<PY> scripts/zeno.py --inspect <footage> --script <script> --name "<name>" --auto-plan --compact` (or `--preflight --compact` first if inputs are missing; never `--skip-preflight`).
2. When `--auto-plan` passes `[PLAN PASS]`, do NOT re-open `editing_packet.md`, `line_map.md`, `facts.md`, `EDIT_RULES.md`, or `clip_sheet_*.jpg` in the outer chat; open `editing_packet.md` + `clip_sheet_*.jpg` once only for manual planning, or run `scripts/edit_packet.py <project> --line L12` (after `scripts/cost_control.py <project>`) + `--check-plan` for targeted fixes.
3. Show the concrete plan Pitch Card and obtain Yes/No once (unless queued batch rendering was explicitly pre-authorized).
4. Run `<PY> scripts/zeno.py --execute <plan> --square --compact` as one single blocking/completion-notified run — never poll `write_stdin` or progress files in a loop.
5. `verify.py` checks both 9:16 and 1:1 Square framing and builds `review_sheet.jpg` automatically (never write custom FFmpeg/Python frame-extraction scripts); inspect `review_sheet.jpg` once (or on failure), fix failed checks, and deliver exports with the saved per-video token report.
