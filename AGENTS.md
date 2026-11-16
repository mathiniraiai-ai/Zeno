# Zeno — agent instructions

Zeno edits script-led videos. The agent makes editorial decisions in `plan.edl`; Python transcribes, measures, renders and checks. Start with `agent/DIRECTOR.md`. For code development read `DEVELOPING.md` instead of the editing references.

## Working boundaries
- Work inside this Zeno repository. Raw footage may be read from the user's chosen local folder. Deliverables go to `~/Downloads/Zeno Outputs/<Notion name>/`. Other projects are off-limits.
- Use `.venv/bin/python` on Mac/Linux or `.venv\Scripts\python.exe` on Windows, never bare Python. If missing, run `bash install.command` or `scripts/install_windows.ps1` once.
- Never open or print `.env`, `.zeno_session.json`, `.zeno_owner_session.json` or credentials. Use `<PY> scripts/setup.py --smoke` / `--whoami` for safe checks.
- Preserve existing local work. Never clear caches manually. They invalidate themselves. Use `--fresh` only for a proven cache defect. Disk cleanup requires a dry run and approval.

## Video workflow
- Prefer a fresh chat for each video and only the connectors it needs. Revisions continue with the project's saved plan and brief. Do not request inputs already supplied.
- Require the script's Google Doc `?tab=...` or local script, local footage and exact Notion name. Never invent a name or read another script tab.
- If inputs are missing, run `<PY> scripts/zeno.py --preflight --compact`, show results, then ask for them. With inputs, run `<PY> scripts/zeno.py --inspect <footage> --script <script> --name "<name>" --compact` directly; it runs live checks. Never use `--skip-preflight`.
- Read generated `editing_packet.md` and all clip sheets once. It contains the brief, line map and word timings; do not reread those files by default. The brief selects `agent/EDIT_RULES.md` and format rules. For a specific issue use `<PY> scripts/edit_packet.py <project> --line L12`. Avoid Python sources and raw word/alignment JSON except to isolate a defect.
- Write `script_analysis.md` and every segment of `plan.edl`, then run `--check-plan`. The script is the timeline; every spoken line once, in order, with no unscripted repeats or stumbles. Choose takes from measured words and pictures. Every cut has `sp="Name"`. Wrong voice needs another take or a reshoot. Disclose unchecked voices.
- Obtain Yes/No once on the concrete plan before rendering, unless the user already explicitly authorized rendering without another question. Then `<PY> scripts/zeno.py --execute <plan> --square --compact`. Keep monitoring through completion using the tool's completion notification or bounded waits. Full diagnostics remain in the printed log.
- Review verification and contact sheets. Fix failed checks before delivery. Record user feedback verbatim first with `--feedback "<words>" --plan <plan>`, then act on it.

## Owner's visual rules
- All split screens, including baked splits, stay at `z=1` in fixed frames with 14% headroom. Characters keep their panels; the other character listens silently. Advance listener footage across turns and disclose fallback/loops.
- Captions default to `#subs auto y=68`, one style and fixed size (54 px at 1080 width), one row. Top/bottom split captions sit at y=50. Colours adapt only with `adapt=on`. Disable only when requested.
- Scrolling text uses `#page scroll`: two still pictures above a blue rule and a white dialogue page with spoken-word highlight. All cuts `split_h z=1`. The page replaces captions. No extra decorations.
- Transitions follow changes in place, time or layout. Never inside splits or continuous same-shot dialogue. Marks and banners follow the script. SFX normally -4 to 0 dB; mark exceptions are defined in the brief.

## Reporting and diagnostics
- Follow `config/cost_controls.json`: default INR 40 model estimate checkpoint, 24 chat requests, 100k context at intake. Run `<PY> scripts/cost_control.py <project>` before another planning/correction round; stop further model work when blocked, preserve the plan and explain. Local approved rendering/checks can finish. Host checkpoints cannot enforce a subscription billing cap; unknown usage is not zero.
- Direct API planning is opt-in: `<PY> scripts/direct_mode.py <project> --provider openai --dry-run` first. Sol has at most 3 requests with conservative cost reserves. Requires separate API credentials/billing; never use Codex credentials, switch providers silently or retry unresolved charges. Read `LOW_COST_WORKFLOW.md` only when using this path.
- 50k–70k tokens per video is a historical range, never a spending target or quality ceiling. Reduce repeated context and calls; retain editorial judgment and required checks.
- Read the saved token report. Show uncached input, cached input, output, combined total, new versus reused transcription and any unknowns. Cost is a dated API-equivalent estimate, with exchange rate and speed assumptions, never an asserted subscription charge. README and TOKEN_LEDGER rows are automatic.
- `<PY> scripts/zeno.py --check` reports dependencies. GPU and low-priority execution are automatic. Code bugs require focused debugging and regression checks under `DEVELOPING.md`; do not discard the edit or erase caches.
