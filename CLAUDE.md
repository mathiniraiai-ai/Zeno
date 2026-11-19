# Zeno — agent instructions

Zeno edits script-led videos (`plan.edl` by agent; Python measures/renders/checks). Start: `agent/DIRECTOR.md` (`DEVELOPING.md` for code work).

## Working boundaries
- Stay in this Zeno repo (footage from user's folder; outputs to `~/Downloads/Zeno Outputs/<Notion name>/`). Other projects off-limits.
- Run `.venv/bin/python` (Mac/Linux) or `.venv\Scripts\python.exe` (Windows), never bare Python (`bash install.command` / `scripts/install_windows.ps1` once).
- Never open/print `.env`, `.zeno_session.json`, `.zeno_owner_session.json`, or creds (`<PY> scripts/setup.py --smoke` / `--whoami`).
- Preserve local work; never clear caches (`--fresh` only for proven cache bugs). Cleanup needs dry-run approval.

## Video workflow
- Automatic edits may share a chat; always use isolated `--auto-plan`. Manual planning needs a small chat. `--inspect` never resets the project budget. Require script `?tab=...` or local path, local footage and exact editor-supplied Notion name; never re-ask supplied inputs.
- **Turn 1 (Automatic planning)**: Missing inputs -> `<PY> scripts/zeno.py --preflight --compact`, show results, ask. Else run `<PY> scripts/zeno.py --inspect <footage> --script <script> --name "<name>" --auto-plan --compact` (never `--skip-preflight`): preflight, clip reuse, plan plus at most one targeted correction per invocation, `--check-plan`, Pitch Card, auto 9:16 + 1:1 render without Yes/No. After render, review QC + `review_sheet.jpg` once before delivery; do not reopen planning packets/rules.
- **[PLANNER STOPPED]**: Stop model work, report `[BLOCKED]` lines and ask the editor. No retry, manual planning or packet reread. A new chat cannot reset the budget; reconcile pending/unknown charges.
- **Manual fallback only when explicitly returned before planning, or authorized by the editor (4–5 turns max)**; never use it to bypass a planner or budget stop:
  - **Turn 2**: Read `editing_packet_p*.md` (or `editing_packet.md`) + ALL `clip_sheet_*.jpg` in ONE parallel batch.
  - **Turn 3**: Write `script_analysis.md` and `plan.edl` (plus `decisions.txt` if needed) in ONE parallel batch. Script is timeline (every line once, ordered, no repeats/stumbles); pick takes from words/pictures; every cut needs `sp="Name"`; wrong voice needs reshoot/take (disclose unchecked voices).
  - **Turn 4**: Unless plan-only, run ONE check+render without Yes/No: `<PY> scripts/zeno.py --execute <plan> --square --compact`. Separate `--check-plan` only if needed/plan fails. If backgrounded, **END TURN immediately**, wait for completion; NEVER poll `manage_task`, `write_stdin`, `send_input`, or logs.
  - **Turn 5**: `verify.py` checks 9:16 + 1:1 and builds `review_sheet.jpg` (no custom frame scripts). Read `review_sheet.jpg` ONCE (unless verify fails) and present deliverables + token report. Log feedback verbatim (`--feedback "<words>" --plan <plan>`); fixes use `<PY> scripts/edit_packet.py <project> --line L12` + `--check-plan`.
- **[DO NOT] during edits**: NEVER open `agent/REFERENCE.md`, `CONCEPT.md`, `agent/EDIT_RULES.md`, `agent/FORMATS.md`, `scripts/*.py`, `clip_caches.json`, `facts.md`, `line_map.md`, `ffprobe`, or `dir`/`Get-ChildItem`/`ls`; the packet contains syntax, inventory, diagnostics and evidence.

## Owner's visual rules
- All split screens, including baked splits, stay at `z=1` in fixed frames with 14% headroom. Characters keep their panels; the other character listens silently. Advance listener footage across turns and disclose fallback/loops.
- Captions default to `#subs auto y=68`, one style and fixed size (54 px at 1080 width), one row. Top/bottom split captions sit at y=50. Colours adapt only with `adapt=on`. Disable only when requested.
- Scrolling text uses `#page scroll`: two still pictures above a blue rule and a white dialogue page with spoken-word highlight. All cuts `split_h z=1`. The page replaces captions. No extra decorations.
- Transitions follow changes in place, time or layout. Never inside splits or continuous same-shot dialogue. Marks and banners follow the script. SFX normally -4 to 0 dB; mark exceptions are defined in the brief.

## Reporting and diagnostics
- Limits (`config/cost_controls.json`): 2 planner calls/invocation; 8 managed requests/project lifetime; 2 full-context requests with combined 100,000 conservative input reserve; corrections 24,000 UTF-8 bytes. Preserve `request_budget.sqlite3` and pending-call records. Details: `LOW_COST_WORKFLOW.md`.
- Host checkpoints: 8 requests, 1,000,000-token chat limit (the model's own window); model cost estimate INR 40. These cannot cap the chat app/account or guarantee billed total/cache tokens. If retries repeat, run `<PY> scripts/cost_control.py <project>`; if blocked, stop model work, save plan, explain (approved renders finish; unknown != 0).
- Direct API (opt-in): `<PY> scripts/direct_mode.py <project> --provider openai --dry-run` first (same planning limits, separate billing; never use Codex creds, switch silently, or retry unresolved charges).
- Report saved uncached input, cache reads, cache writes, output, combined total, measured requests, elapsed time, new vs reused transcription and unknowns (API-equivalent estimate, not invoice). README & TOKEN_LEDGER auto-update. Four to six total calls is a workflow aim, not a guarantee.
- `<PY> scripts/zeno.py --check` verifies deps (GPU/low-priority auto). Debug via `DEVELOPING.md`; keep edits/caches.
