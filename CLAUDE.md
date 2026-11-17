# Zeno — agent instructions

Zeno edits script-led videos (`plan.edl` by agent; Python measures/renders/checks). Start: `agent/DIRECTOR.md` (`DEVELOPING.md` for code work).

## Working boundaries
- Stay in this Zeno repo (footage from user's folder; outputs to `~/Downloads/Zeno Outputs/<Notion name>/`). Other projects off-limits.
- Run `.venv/bin/python` (Mac/Linux) or `.venv\Scripts\python.exe` (Windows), never bare Python (`bash install.command` / `scripts/install_windows.ps1` once).
- Never open/print `.env`, `.zeno_session.json`, `.zeno_owner_session.json`, or creds (`<PY> scripts/setup.py --smoke` / `--whoami`).
- Preserve local work; never clear caches (`--fresh` only for proven cache bugs). Cleanup needs dry-run approval.

## Video workflow
- One video per chat: start each video in a new chat, so every step re-reads a small chat (claude.ai connectors are off: `.claude/settings.json` loads the Zeno server only); each `--inspect` isolates a cost window. The planner makes at most 2 model calls per video (`planner_max_calls`). If the output says `[PLANNER STOPPED]` (the planner used its 2 model calls and the plan still fails), do not plan by hand: report the `[BLOCKED]` lines to the editor and ask. Require `?tab=...` or local script, local footage, exact Notion name (given by the editor; never invent name/tab). Never re-ask inputs.
- **Turn 1 (Primary 1-call)**: Missing inputs -> `<PY> scripts/zeno.py --preflight --compact`, show results, ask. Else run `<PY> scripts/zeno.py --inspect <footage> --script <script> --name "<name>" --auto-plan --compact` (never `--skip-preflight`): preflight, clip reuse, 1-call planner (`20k–55k`), `--check-plan`, Pitch Card, auto 9:16 + 1:1 render without Yes/No. If `[PLAN PASS]`, do NOT open packets/sheets/rules; deliver outputs + token report.
- **Manual / Fallback Cap (4–5 turns max, NEVER 40+)**:
  - **Turn 2**: Read `editing_packet_p*.md` (or `editing_packet.md`) + ALL `clip_sheet_*.jpg` in ONE parallel batch.
  - **Turn 3**: Write `script_analysis.md` and `plan.edl` (plus `decisions.txt` if needed) in ONE parallel batch. Script is timeline (every line once, ordered, no repeats/stumbles); pick takes from words/pictures; every cut needs `sp="Name"`; wrong voice needs reshoot/take (disclose unchecked voices).
  - **Turn 4**: Never ask Yes/No (unless `--no-auto-render`/plan-only). Run ONE combined check+render: `<PY> scripts/zeno.py --execute <plan> --square --compact` (no separate `cost_control.py` + `--check-plan` + `--execute` roundtrips unless `--check-plan` needed/plan fails). If backgrounded, **END TURN immediately** and wait for completion — NEVER poll `manage_task`, `write_stdin`, `send_input`, or progress/log files.
  - **Turn 5**: `verify.py` checks 9:16 + 1:1 and builds `review_sheet.jpg` (no custom frame scripts). Read `review_sheet.jpg` ONCE (unless verify fails) and present deliverables + token report. Log feedback verbatim (`--feedback "<words>" --plan <plan>`); fixes use `<PY> scripts/edit_packet.py <project> --line L12` + `--check-plan`.
- **[DO NOT] during edits**: NEVER open `agent/REFERENCE.md`, `CONCEPT.md`, `agent/EDIT_RULES.md`, `agent/FORMATS.md`, `scripts/*.py`, `clip_caches.json`, `facts.md`, `line_map.md`, `ffprobe`, or `dir`/`Get-ChildItem`/`ls` (`editing_packet.md` already holds syntax card, raw footage inventory, unmatched line diagnostics, and all words/candidates/rules).

## Owner's visual rules
- All split screens, including baked splits, stay at `z=1` in fixed frames with 14% headroom. Characters keep their panels; the other character listens silently. Advance listener footage across turns and disclose fallback/loops.
- Captions default to `#subs auto y=68`, one style and fixed size (54 px at 1080 width), one row. Top/bottom split captions sit at y=50. Colours adapt only with `adapt=on`. Disable only when requested.
- Scrolling text uses `#page scroll`: two still pictures above a blue rule and a white dialogue page with spoken-word highlight. All cuts `split_h z=1`. The page replaces captions. No extra decorations.
- Transitions follow changes in place, time or layout. Never inside splits or continuous same-shot dialogue. Marks and banners follow the script. SFX normally -4 to 0 dB; mark exceptions are defined in the brief.

## Reporting and diagnostics
- `config/cost_controls.json`: INR 40 / 24 reqs/video, 2 planner calls/video. Run `<PY> scripts/cost_control.py <project>` if retries repeat; if blocked, stop model work, save plan, explain (approved renders finish; unknown != 0).
- Direct API (opt-in): `<PY> scripts/direct_mode.py <project> --provider openai --dry-run` first (max 3 Sol calls, separate billing; never use Codex creds, switch silently, or retry unresolved charges; `LOW_COST_WORKFLOW.md` only here).
- Target `< 1,00,000` tokens/video (`20k–55k` via `--auto-plan`), never a quality ceiling; cut repeated calls.
- Report saved tokens: uncached/cached input, output, total, new vs reused transcription, unknowns (API estimate, not invoice). README & TOKEN_LEDGER auto-update.
- `<PY> scripts/zeno.py --check` verifies deps (GPU/low-priority auto). Debug via `DEVELOPING.md`; keep edits/caches.
