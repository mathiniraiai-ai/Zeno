# Zeno — Automated Token & Clip Telemetry Ledger

Automatically updated by [`scripts/token_tracker.py`](scripts/token_tracker.py) after every video edit (`prepare.py` → `render.py`).
Full relational history is also stored in local SQLite (`work/zeno_telemetry.db`) and synced to Railway PostgreSQL (`/admin`).

## 1. All Video Edit Runs (Agent + Transcription Rollup)

| Date | Project (`Lang`) | Agent & Model | Clips (`Raw → Final`) | Transcribe Tokens (`In / Out / Total`) | Agent Tokens (`In / Cached / Out / Total`) | Overall (`In / Out / Grand Total`) |
|---|---|---|---|---|---|---|

## 2. Latest Job Clip-by-Clip Breakdown

*Run `python3 scripts/prepare.py ...` and `python3 scripts/render.py work/<project>/plan.edl --verify` to populate the first automated clip-by-clip breakdown.*
