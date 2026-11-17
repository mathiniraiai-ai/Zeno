---
description: Edit one or a queued batch of script-led videos with Zeno's 1-call planner and verified plan.
argument-hint: "<footage> <script-tab> --name <Notion name>"
---
# /zeno

User inputs: `$ARGUMENTS`

Follow `AGENTS.md` and `agent/DIRECTOR.md`. Queue multiple videos back-to-back in one chat (up to `1M` / `1,000,000` context tokens) with Notion and Meta connectors enabled; read the exact Notion name from Notion when a card/queue is shared, and use supplied inputs without asking again. Each `--inspect` opens an isolated per-video `INR 40` / `24`-request cost window.

## Procedure
1. Run `<PY> scripts/zeno.py --inspect <footage> --script <script> --name "<name>" --auto-plan --compact` (if inputs are missing, run `--preflight --compact`, show results, and ask for them; never `--skip-preflight`). `--auto-plan` runs live preflight, inspects/reuses clips, runs Zeno's isolated 1-call subscription planner (`claude -p` or `codex exec`, zero connector/history overhead, typically `20k–55k` tokens), runs `--check-plan`, prints the Pitch Card, and automatically renders 9:16 + 1:1 Square without asking Yes/No.
2. When `--auto-plan` passes `[PLAN PASS]`, do NOT re-open `editing_packet.md`, `line_map.md`, `facts.md`, `EDIT_RULES.md`, or `clip_sheet_*.jpg` in the outer chat. Open `editing_packet.md` and `clip_sheet_*.jpg` once only if manual planning is required, or run `scripts/edit_packet.py <project> --line L12` for targeted revisions (checking `scripts/cost_control.py <project>` before manual rounds and running `--check-plan` with manual `script_analysis.md` / `plan.edl` / `decisions.txt` updates).
3. Never pause to ask Yes/No before rendering (unless the user explicitly requested `--no-auto-render` / plan-only review).
4. If manual planning or revision was needed, immediately run `<PY> scripts/zeno.py --execute <plan> --square --compact` after `--check-plan` passes as one single blocking/completion-notified run — never poll `write_stdin` or progress files in a loop, and never pipe to `tail` in a way that hides exit status. Full diagnostics remain at the printed log path.
5. `verify.py` checks both 9:16 and 1:1 Square framing and builds a single compact `review_sheet.jpg` automatically — never write custom FFmpeg/Python frame-extraction scripts. Inspect `review_sheet.jpg` once (or on failure), fix failed checks, and deliver the MP4s and Premiere files with the saved per-video token report. README and ledger entries are automatic.

For revisions, record feedback verbatim first (`--feedback "<words>" --plan <plan>`), then read the saved plan and only affected line evidence (`edit_packet.py`). Reuse measured footage and preserve every visual rule in `AGENTS.md`.
