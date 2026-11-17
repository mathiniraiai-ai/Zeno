# Zeno — agent instructions

Zeno edits script-led videos. The agent makes editorial decisions in `plan.edl`; Python transcribes, measures, renders and checks. Start with `agent/DIRECTOR.md`. For code development read `DEVELOPING.md` instead of the editing references.

## Working boundaries
- Work inside this Zeno repository. Raw footage may be read from the user's chosen local folder. Deliverables go to `~/Downloads/Zeno Outputs/<Notion name>/`. Other projects are off-limits.
- Use `.venv/bin/python` on Mac/Linux or `.venv\Scripts\python.exe` on Windows, never bare Python. If missing, run `bash install.command` or `scripts/install_windows.ps1` once.
- Never open or print `.env`, `.zeno_session.json`, `.zeno_owner_session.json` or credentials. Use `<PY> scripts/setup.py --smoke` / `--whoami` for safe checks.
- Preserve existing local work. Never clear caches manually. They invalidate themselves. Use `--fresh` only for a proven cache defect. Disk cleanup requires a dry run and approval.

## Video workflow
- Queue multiple videos back-to-back in one chat (up to `1M` / `1,000,000` context tokens) with Notion and Meta connectors enabled; each `--inspect` starts an isolated per-video cost window. Revisions use the saved plan and brief. Never re-ask supplied inputs.
- Require Google Doc `?tab=...` or local script, local footage and exact Notion name (read from Notion if a card/queue was shared). Never invent a name or read another tab.
- Missing inputs: run `<PY> scripts/zeno.py --preflight --compact`, show results, then ask. With inputs: run `<PY> scripts/zeno.py --inspect <footage> --script <script> --name "<name>" --auto-plan --compact` (live preflight, clip reuse, isolated 1-call `claude -p` / `codex exec` subscription planner in `20k–55k` tokens, `--check-plan`, and Pitch Card). Never `--skip-preflight`.
- When `--auto-plan` passes `[PLAN PASS]`, do NOT re-open `editing_packet.md`, `line_map.md`, `facts.md`, `agent/EDIT_RULES.md` or `clip_sheet_*.jpg`. Open `editing_packet.md` + `clip_sheet_*.jpg` once only for manual planning or targeted fixes (`<PY> scripts/edit_packet.py <project> --line L12`). Skip Python sources and raw JSON unless isolating a bug.
- Manual plans/fixes write `script_analysis.md` and `plan.edl`, then `--check-plan`. Script is timeline: every line once, in order, no repeats/stumbles. Choose takes from measured words/pictures; every cut needs `sp="Name"`. Wrong voice needs another take/reshoot; disclose unchecked voices.
- Show the concrete plan and get Yes/No once (unless batch rendering was pre-authorized). Run `<PY> scripts/zeno.py --execute <plan> --square --compact` as one blocking/completion-notified run — never poll `write_stdin` or progress files. Full logs stay on disk.
- `verify.py` checks 9:16 + 1:1 Square framing and builds `review_sheet.jpg` automatically; never write custom FFmpeg/Python frame scripts. Inspect `review_sheet.jpg` once (or on failure) and fix failed checks. Log feedback verbatim first (`--feedback "<words>" --plan <plan>`).

## Owner's visual rules
- All split screens, including baked splits, stay at `z=1` in fixed frames with 14% headroom. Characters keep their panels; the other character listens silently. Advance listener footage across turns and disclose fallback/loops.
- Captions default to `#subs auto y=68`, one style and fixed size (54 px at 1080 width), one row. Top/bottom split captions sit at y=50. Colours adapt only with `adapt=on`. Disable only when requested.
- Scrolling text uses `#page scroll`: two still pictures above a blue rule and a white dialogue page with spoken-word highlight. All cuts `split_h z=1`. The page replaces captions. No extra decorations.
- Transitions follow changes in place, time or layout. Never inside splits or continuous same-shot dialogue. Marks and banners follow the script. SFX normally -4 to 0 dB; mark exceptions are defined in the brief.

## Reporting and diagnostics
- `config/cost_controls.json`: INR 40 model estimate and 24 requests isolated per video, `1M` (`1,000,000` tokens) intake context limit. Run `<PY> scripts/cost_control.py <project>` before manual planning/corrections; stop model work if blocked, save plan and explain. Approved local renders can finish. Unknown usage is not zero.
- Opt-in direct API: `<PY> scripts/direct_mode.py <project> --provider openai --dry-run` first (max 3 Sol calls, conservative reserves, separate API billing; never use Codex credentials, switch silently or retry unresolved charges). Read `LOW_COST_WORKFLOW.md` only for this path.
- Target `< 1,00,000` total tokens/video (`20k–55k` typical with `--auto-plan`), never a quality ceiling. Cut repeated context/calls; keep editorial judgment and checks.
- Read the saved token report: show uncached input, cached input, output, combined total, new vs reused transcription and unknowns. Cost is a dated API-equivalent estimate, never a subscription invoice. README and TOKEN_LEDGER update automatically.
- `<PY> scripts/zeno.py --check` checks dependencies. GPU and low-priority execution are automatic. Debug code under `DEVELOPING.md` without discarding edits or caches.
