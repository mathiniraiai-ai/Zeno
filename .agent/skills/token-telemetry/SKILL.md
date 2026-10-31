---
name: token-telemetry
description: Automated per-clip raw footage process logging and separated per-agent (Antigravity / Claude Code + exact model) vs. Gemini audio transcription token tracking into TOKEN_LEDGER.md, work/zeno_telemetry.db, and Railway PostgreSQL.
---

# Zeno Token & Clip Telemetry Skill (`token-telemetry`)

Whenever a video is processed in Zeno (`prepare.py` → `render.py --verify`), [`scripts/token_tracker.py`](../../../scripts/token_tracker.py) automatically records **three separated telemetry layers** without mixing Gemini audio transcription tokens into the AI Agent's editing token count:

---

## 1. What Is Automatically Recorded & Separated

### A. AI Agent Video Edit Tokens (Registered for That Video Edit Only)
- **Active Agent Harness**: Auto-detected (`Antigravity` vs. `Claude Code`).
- **Active Model**: Auto-detected from session settings (`Gemini 4 Argon`, `Claude Sonnet 5.5`, `Claude Opus 5.5`, `claude-sonnet-5-5`, `claude-opus-5-5`).
- **Edit Window Scoping (`[job_start_epoch, job_end_epoch]`)**: Counts **only** the input and output tokens spent during that specific video edit (`prepare.py` → `render.py`), ignoring earlier or later conversation history in the same chat thread.
- **Metrics Recorded**:
  - `agent_in_tokens` (Input tokens for that edit)
  - `agent_out_tokens` (Output tokens for that edit)
  - `agent_total_tokens` (`agent_in_tokens + agent_out_tokens`)
  - `tokens_per_min` (`agent_total_tokens` per minute of finished video)

### B. Gemini Cloud Audio Transcription Tokens (Registered Separately)
- Tracks the `gemini-2.5-flash-lite` audio transcription API usage per clip (`S1..SN`) and in aggregate (`transcribe_in_tokens`, `transcribe_out_tokens`, `transcribe_total_tokens`) in its own separate bucket so it never inflates the AI Agent's token count.

### C. Clip-by-Clip Raw Process (`prepare.py`)
For each clip (`S1.mp4`, `S2.mp4`, …):
- **Raw Duration → Kept Duration** (e.g. `10.0s → 8.8s`)
- **Aligned Words & Matched Script Lines**
- **Kept Segments & Anomalies Pruned** (`[RETAKE]`, `[TAIL_BABBLE_CUT]`, `[PAUSE_SPLIT]`, `[BOUNDARY_DUP]`, `[WRONG_SPEAKER]`, `[ALT_TAKE]`)
- **Per-Clip Gemini Transcription Tokens** (`input_tokens`, `output_tokens`, `total_tokens`)

---

## 2. Where Data Is Stored Automatically

1. **[`TOKEN_LEDGER.md`](../../../TOKEN_LEDGER.md)** (Root of repo):
   - Table 1: All Video Edit Runs (`Date | Project | Agent | Model | Clips | Video Edit Agent Tokens (In / Out / Total · tok/min) | Gemini Transcription Tokens (Separate In / Out / Total)`).
   - Table 2: Latest Job Clip-by-Clip Breakdown (`S1..SN`).
2. **`work/<project>/token_report.md` & `work/<project>/token_telemetry.json`**:
   - Full per-project Markdown report and JSON telemetry artifact with separated Agent and Gemini Transcription tables.
3. **`work/zeno_telemetry.db`** (Local SQLite database):
   - SQL tables `job_runs` and `clip_runs`.
4. **Railway PostgreSQL (`POST /v1/telemetry` → `/admin`)**:
   - Synced automatically using `SCRIPTCUT_CLOUD_URL` and `SCRIPTCUT_CLOUD_TOKEN` from `.env`.

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
- **Check Active Agent & Model Detection**:
  ```bash
  .venv/bin/python scripts/token_tracker.py
  ```
