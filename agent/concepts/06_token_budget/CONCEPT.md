# 06 · Token budget & automated telemetry

**Budget:** a video usually takes 50k to 70k tokens; quality comes first and this is not a hard stop. The Director makes every editing decision, so spend tokens on looking properly.

## How to spend them
- Read `line_map.md`, `facts.md` and every clip sheet in full, then write `plan.edl` yourself.
- One script tab, never the whole document.
- A fresh session for each video. Unused connectors off.
- Sonnet 5.5 or Opus 5.5. Opus for messy footage, or when a plan fails its check twice.
- If the job passes 70k, say so and why in the report, and keep the same quality.

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
