---
description: Edit one or a queued batch of script-led videos with Zeno's 1-call planner and verified plan.
argument-hint: "<footage> <script-tab> --name <Notion name>"
---
# /zeno

User inputs: `$ARGUMENTS`

Follow `AGENTS.md` and `agent/DIRECTOR.md`. One video per chat: start each video in a new chat, so every step re-reads a small chat (claude.ai connectors are off, so the editor gives the exact Notion name); use supplied inputs without asking again. The planner makes at most 2 model calls per video. If the output says `[PLANNER STOPPED]` (the planner used its 2 model calls and the plan still fails), do not plan by hand: report the `[BLOCKED]` lines to the editor and ask. Each `--inspect` opens an isolated per-video `INR 40` / `24`-request cost window.

## Procedure
1. **Turn 1 (Primary 1-call)**: Run `<PY> scripts/zeno.py --inspect <footage> --script <script> --name "<name>" --auto-plan --compact` (if inputs are missing, run `--preflight --compact`, show results, and ask for them; never `--skip-preflight`). `--auto-plan` runs live preflight, inspects/reuses clips, runs Zeno's isolated 1-call subscription planner (`claude -p` or `codex exec`, zero connector/history overhead, typically `20k–55k` tokens), runs `--check-plan`, prints the Pitch Card, and automatically renders 9:16 + 1:1 Square without asking Yes/No. When `--auto-plan` passes `[PLAN PASS]`, do NOT open packets/sheets/rules; deliver outputs + token report.
2. **Strict Manual / Fallback Cap (4–5 turns max, NEVER 40+)**:
   - **Turn 2**: Read `editing_packet_p*.md` (or `editing_packet.md`) + ALL `clip_sheet_*.jpg` in ONE parallel batch.
   - **Turn 3**: Write `script_analysis.md` and `plan.edl` (plus `decisions.txt` if needed) in ONE parallel batch.
   - **Turn 4**: Never pause to ask Yes/No before rendering (unless the user explicitly requested `--no-auto-render` / plan-only review). Run ONE combined check+render command: `<PY> scripts/zeno.py --execute <plan> --square --compact` (no separate `cost_control.py` + `--check-plan` + `--execute` roundtrips unless `--check-plan` is explicitly needed or the plan fails!; never pipe to `tail` in a way that hides exit status). If a command backgrounds, **END TURN immediately** and wait for system completion notification — NEVER poll `manage_task`, `write_stdin`, `send_input`, or progress/log files!
   - **Turn 5**: `verify.py` checks both 9:16 and 1:1 Square framing and builds a single compact `review_sheet.jpg` automatically (never write custom FFmpeg/Python frame-extraction scripts). Read `review_sheet.jpg` ONCE (unless verify fails) and present deliverables (MP4s + Premiere files) with the saved per-video token report. README and ledger entries are automatic.
3. **`[DO NOT]` during edits**: NEVER open `agent/REFERENCE.md`, `CONCEPT.md`, `agent/EDIT_RULES.md`, `agent/FORMATS.md`, `scripts/*.py`, `clip_caches.json`, `facts.md`, `line_map.md`, `ffprobe`, or `dir`/`Get-ChildItem`/`ls` (`editing_packet.md` already holds syntax card, raw footage inventory, unmatched line diagnostics, and all words/candidates/rules).

For revisions, record feedback verbatim first (`--feedback "<words>" --plan <plan>`), then read the saved plan and only affected line evidence (`edit_packet.py` + `--check-plan`, checking `cost_control.py` if retries repeat). Reuse measured footage and preserve every visual rule in `AGENTS.md`.
