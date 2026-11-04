# 06 · Token budget & automated telemetry

**Goal:** 15–20k tokens per minute of finished video. Earlier fully-Claude edits ran about 70k.
CLAUDE.md sets the working numbers: target 12–18k, ceiling 20k.

## How to stay under budget
- The Engine does the work. You read `brief.txt` (~900 tokens) and `plan.edl` (~240 tokens), plus one contact-sheet image.
- One script tab, never the whole document.
- A fresh session for each video. Unused connectors off.
- Cheaper tier by default (for example Sonnet 5.5). Strongest tier (for example Opus 5.5) only for learning a new style. Same budget for any agent.
- Warn the user mid-job if retakes or messy footage risk passing 20k per minute.

## Automated per-clip & per-agent token tracking (`scripts/token_tracker.py`)
`prepare.py` and `render.py` call `scripts/token_tracker.py` automatically:
1. **Per-clip raw process + transcription tokens:** records each clip's raw → kept duration, words aligned, script lines, retakes/cuts pruned, and exact Gemini transcription `input_tokens`, `output_tokens`, `total_tokens`.
2. **Active agent + model tokens:** auto-detects whether **Antigravity** or **Claude Code** is running (and which model) from local harness logs, and records `input_tokens`, `cached_tokens`, `output_tokens`, and `total_tokens`.
3. **Database & ledger storage:** writes every run to:
   - `TOKEN_LEDGER.md` (repo root) and `work/<project>/token_report.md`
   - `work/zeno_telemetry.db` (local SQLite tables `job_runs` and `clip_runs`)
   - Railway PostgreSQL (`POST /v1/telemetry`, visible at `/admin` on the Railway service)

## End of every job
Show the console summary printed by `render.py` (or run `python3 scripts/token_tracker.py --project <project>`), verify `TOKEN_LEDGER.md` is updated, and append the summary row to `README.md` (format in CLAUDE.md §2).
