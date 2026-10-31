---
name: token-telemetry
description: Automated per-clip raw footage process logging and per-agent (Antigravity / Claude Code / Gemini) input, output, cached, and total token tracking into TOKEN_LEDGER.md, work/zeno_telemetry.db, and Railway PostgreSQL.
---

# Zeno Token & Clip Telemetry Skill (`token-telemetry`)

Whenever a video is processed in Zeno (`prepare.py` → `render.py --verify`), [`scripts/token_tracker.py`](../../../scripts/token_tracker.py) automatically records both **clip-by-clip raw process telemetry** and **AI agent token telemetry** without manual calculation.

---

## 1. What Is Automatically Recorded

### A. Clip-by-Clip Raw Process (`prepare.py`)
For each clip (`S1.mp4`, `S2.mp4`, …):
- **Raw Duration → Kept Duration** (e.g. `14.2s → 12.6s`)
- **Aligned Words & Matched Script Lines** (e.g. `38 words`, `L1,L2`)
- **Kept Segments & Anomalies Pruned** (`[RETAKE]`, `[TAIL_BABBLE_CUT]`, `[PAUSE_SPLIT]`, `[BOUNDARY_DUP]`)
- **Gemini Transcription Tokens** (exact `input_tokens`, `output_tokens`, `total_tokens` returned by `resp.usage_metadata` from the Railway service or direct Gemini API)

### B. Active AI Agent & Model (`prepare.py` → `render.py`)
`scripts/token_tracker.py` inspects local session transcripts to auto-detect which harness and model ran the job:
- **Antigravity**: Reads `~/.gemini/antigravity/brain/<conversation-id>/.system_generated/logs/transcript.jsonl` (`Gemini 4 Argon`, `Claude Sonnet 5.5`, `Claude Opus 5.5`).
- **Claude Code**: Reads `~/.claude/projects/**/*.jsonl` (`claude-sonnet-5-5`, `claude-opus-5-5`).
- Records **Agent Input Tokens**, **Agent Cached Tokens**, **Agent Output Tokens**, and **Agent Total Tokens**, plus the **Overall Combined Job Tokens** (`Transcription + Agent`).

---

## 2. Where Data Is Stored Automatically

1. **[`TOKEN_LEDGER.md`](../../../TOKEN_LEDGER.md)** (Root of repo):
   - Table 1: All Video Edit Runs (Date, Project, Agent & Model, Clips, Transcription `In / Out / Total`, Agent `In / Cached / Out / Total`, Overall `In / Out / Grand Total`).
   - Table 2: Latest Job Clip-by-Clip Breakdown (`S1..SN` raw → kept, words, script lines, cuts/anomalies, `In / Out / Total` tokens).
2. **`work/<project>/token_report.md` & `work/<project>/token_telemetry.json`**:
   - Full per-project Markdown report and JSON telemetry artifact.
3. **`work/zeno_telemetry.db`** (Local SQLite database):
   - SQL tables `job_runs` and `clip_runs` for local SQL queries.
4. **Railway PostgreSQL (`POST /v1/telemetry` → `/admin`)**:
   - Synced automatically using `SCRIPTCUT_CLOUD_URL` and `SCRIPTCUT_CLOUD_TOKEN` from `.env`, visible in the `/admin` dashboard under **Video Edit Runs**.

---

## 3. Commands

- **Automatic**: Runs automatically at the end of `python3 scripts/prepare.py ...` and `python3 scripts/render.py work/<project>/plan.edl --verify`.
- **Manual Re-Sync / Print**:
  ```bash
  .venv/bin/python scripts/token_tracker.py --project <project_name>
  ```
- **List SQL History**:
  ```bash
  .venv/bin/python scripts/token_tracker.py --list
  ```
- **Check Active Agent Detection**:
  ```bash
  .venv/bin/python scripts/token_tracker.py
  ```
