#!/usr/bin/env python3
"""Automated Per-Clip & Per-Agent Token Telemetry Tracker for Zeno.

Tracks and stores:
1. Per-clip raw process telemetry during `prepare.py`:
   - Clip name, raw duration -> kept duration, words aligned, script lines matched,
     anomalies pruned ([RETAKE], [TAIL_BABBLE_CUT], [PAUSE_SPLIT], [BOUNDARY_DUP]),
     and Gemini transcription tokens (input_tokens, output_tokens, total_tokens).
2. Active AI Agent & Model telemetry during `prepare.py` -> `render.py`:
   - Auto-detects whether **Antigravity** (Gemini 4 Argon / Claude) or **Claude Code** (Opus / Sonnet)
     is running the job by inspecting local harness session logs (`~/.gemini/antigravity/brain/` and `~/.claude/projects/`).
   - Computes agent input tokens, cached tokens, output tokens, and total tokens spent on the job.
3. Persists every run to:
   - `work/<project>/token_telemetry.json` & `work/<project>/token_report.md`
   - Local SQL database: `work/zeno_telemetry.db` (`job_runs` and `clip_runs` tables)
   - Notable repo ledger: `TOKEN_LEDGER.md` (and `README.md` token usage log)
   - Railway PostgreSQL database via `POST /v1/telemetry` (when `SCRIPTCUT_CLOUD_URL` + `SCRIPTCUT_CLOUD_TOKEN` are set)

CLI usage:
    python3 scripts/token_tracker.py --project <project_name> [--sync-cloud]
    python3 scripts/token_tracker.py --list
"""
from __future__ import annotations

import argparse
import datetime as dt
import glob
import json
import os
import re
import sqlite3
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import ROOT, WORK, read_json, tc, write_json  # noqa: E402

LEDGER_MD = ROOT / "TOKEN_LEDGER.md"
LOCAL_DB = WORK / "zeno_telemetry.db"


def _iso_to_epoch(ts_str: str) -> float:
    if not ts_str:
        return 0.0
    try:
        return dt.datetime.fromisoformat(ts_str.replace("Z", "+00:00")).timestamp()
    except Exception:
        return 0.0


def detect_agent_usage(since_epoch: Optional[float] = None) -> Dict[str, Any]:
    """Auto-detect which AI agent harness (Antigravity vs Claude Code) is active and read its token usage."""
    now = time.time()
    cutoff = since_epoch if (since_epoch and since_epoch > 0) else (now - 1800.0)

    candidates: List[Tuple[float, Dict[str, Any]]] = []

    # 1. Check Antigravity transcripts (~/.gemini/antigravity/brain/*/.system_generated/logs/transcript.jsonl)
    agy_pattern = os.path.expanduser("~/.gemini/antigravity/brain/*/.system_generated/logs/transcript.jsonl")
    agy_files = sorted(glob.glob(agy_pattern), key=os.path.getmtime, reverse=True)
    for path_str in agy_files[:3]:
        try:
            mtime = os.path.getmtime(path_str)
            lines = Path(path_str).read_text(encoding="utf-8", errors="ignore").splitlines()
            if not lines:
                continue
            # Check if this conversation touches Zeno or Claude Editor or is the newest active session
            raw_sample = "\n".join(lines[-40:])
            if len(agy_files) > 1 and ("Zeno" not in raw_sample and "Claude Editor" not in raw_sample) and (now - mtime > 300):
                continue

            model_name = "Gemini 4 Argon"
            for line in reversed(lines):
                if "Model Selection" in line:
                    m = re.search(r"to\s+([A-Za-z0-9 ._-]+?)(?:\s*\(|\.)", line)
                    if m:
                        model_name = m.group(1).strip()
                        break

            # Calculate tokens across steps since cutoff (or last 12 planner steps of the job)
            planner_steps = []
            for line in lines:
                if not line.strip():
                    continue
                d = json.loads(line)
                if d.get("type") == "PLANNER_RESPONSE" and (d.get("input_tokens") or d.get("output_tokens")):
                    step_ts = _iso_to_epoch(str(d.get("created_at") or ""))
                    planner_steps.append((step_ts, d))

            job_steps = [d for ts, d in planner_steps if ts >= cutoff]
            if not job_steps and planner_steps:
                job_steps = [d for _, d in planner_steps[-6:]]

            if job_steps:
                first_ctx = int(job_steps[0].get("input_tokens") or 0) + int(job_steps[0].get("cache_read_tokens") or 0)
                last_ctx = int(job_steps[-1].get("input_tokens") or 0) + int(job_steps[-1].get("cache_read_tokens") or 0)
                ctx_growth = max(0, last_ctx - first_ctx)
                uncached_in = sum(int(s.get("input_tokens") or 0) for s in job_steps)
                cached_in = int(job_steps[-1].get("cache_read_tokens") or 0)
                out_tok = sum(int(s.get("output_tokens") or 0) for s in job_steps)
                # Effective input tokens added during this job
                eff_in = ctx_growth if ctx_growth > 0 else uncached_in
                candidates.append((
                    mtime,
                    {
                        "agent_name": "Antigravity",
                        "agent_model": model_name,
                        "steps_count": len(job_steps),
                        "context_start": first_ctx,
                        "context_end": last_ctx,
                        "input_tokens": eff_in,
                        "cached_tokens": cached_in,
                        "output_tokens": out_tok,
                        "total_tokens": eff_in + out_tok,
                        "session_id": Path(path_str).parts[-4] if len(Path(path_str).parts) >= 4 else "unknown",
                    },
                ))
                break
        except Exception:
            continue

    # 2. Check Claude Code transcripts (~/.claude/projects/**/*.jsonl)
    claude_pattern = os.path.expanduser("~/.claude/projects/**/*.jsonl")
    claude_files = sorted(glob.glob(claude_pattern, recursive=True), key=os.path.getmtime, reverse=True)
    for path_str in claude_files[:5]:
        try:
            mtime = os.path.getmtime(path_str)
            lines = Path(path_str).read_text(encoding="utf-8", errors="ignore").splitlines()
            assistant_turns = []
            model_name = "Claude"
            for line in lines:
                if not line.strip():
                    continue
                d = json.loads(line)
                if d.get("type") == "assistant" and isinstance(d.get("message"), dict):
                    msg = d["message"]
                    u = msg.get("usage") or {}
                    if u:
                        ts = _iso_to_epoch(str(d.get("timestamp") or ""))
                        if msg.get("model"):
                            model_name = str(msg["model"])
                        assistant_turns.append((ts, u))

            job_turns = [u for ts, u in assistant_turns if ts >= cutoff]
            if not job_turns and assistant_turns:
                job_turns = [u for _, u in assistant_turns[-6:]]

            if job_turns:
                first_u = job_turns[0]
                last_u = job_turns[-1]
                first_ctx = int(first_u.get("input_tokens") or 0) + int(first_u.get("cache_creation_input_tokens") or 0) + int(first_u.get("cache_read_input_tokens") or 0)
                last_ctx = int(last_u.get("input_tokens") or 0) + int(last_u.get("cache_creation_input_tokens") or 0) + int(last_u.get("cache_read_input_tokens") or 0)
                new_in = sum(int(u.get("input_tokens") or 0) + int(u.get("cache_creation_input_tokens") or 0) for u in job_turns)
                cached_in = int(last_u.get("cache_read_input_tokens") or 0)
                out_tok = sum(int(u.get("output_tokens") or 0) for u in job_turns)
                ctx_growth = max(0, last_ctx - first_ctx)
                eff_in = ctx_growth if ctx_growth > 0 else new_in
                candidates.append((
                    mtime,
                    {
                        "agent_name": "Claude Code",
                        "agent_model": model_name,
                        "steps_count": len(job_turns),
                        "context_start": first_ctx,
                        "context_end": last_ctx,
                        "input_tokens": eff_in,
                        "cached_tokens": cached_in,
                        "output_tokens": out_tok,
                        "total_tokens": eff_in + out_tok,
                        "session_id": Path(path_str).stem,
                    },
                ))
                break
        except Exception:
            continue

    if not candidates:
        return {
            "agent_name": os.getenv("ZENO_AGENT", "Antigravity"),
            "agent_model": os.getenv("ZENO_MODEL", "Gemini 4 Argon"),
            "steps_count": 0,
            "context_start": 0,
            "context_end": 0,
            "input_tokens": 0,
            "cached_tokens": 0,
            "output_tokens": 0,
            "total_tokens": 0,
            "session_id": "local",
        }

    candidates.sort(key=lambda x: x[0], reverse=True)
    return candidates[0][1]


def collect_clip_telemetry(
    clips_data: List[Dict[str, Any]],
    segments: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Build per-clip raw process + token usage entries from `prepare.py` structures."""
    out: List[Dict[str, Any]] = []
    for c in clips_data:
        stem = str(c.get("stem") or "")
        wdir = Path(c.get("work_dir") or (WORK / stem))
        info = c.get("info") or {}
        raw_dur = round(float(info.get("duration") or 0.0), 2)
        c_segs = [s for s in segments if Path(str(s.get("src", ""))).stem == stem]
        kept_dur = round(sum(max(0.0, float(s.get("end", 0.0)) - float(s.get("start", 0.0))) for s in c_segs), 2)
        words = c.get("hq_words") or []
        l_ids = ",".join(l["id"] for l in c.get("matched_lines", [])) or "-"
        segs_str = " + ".join(f"{s['id']}[{float(s['start']):.2f}-{float(s['end']):.2f}s]" for s in c_segs) or "dropped"
        anoms = list(c.get("anomalies") or [])

        # Read Gemini transcription token usage from work/<stem>/gemini_transcript.json
        gt_path = wdir / "gemini_transcript.json"
        model_used = "offline_wav2vec2"
        in_tok = 0
        out_tok = 0
        tot_tok = 0
        if gt_path.is_file():
            try:
                gt = read_json(gt_path)
                model_used = str(gt.get("model_used") or "gemini-3.5-flash-lite")
                usage = gt.get("usage") if isinstance(gt.get("usage"), dict) else {}
                in_tok = int(usage.get("input_tokens") or 0)
                out_tok = int(usage.get("output_tokens") or 0)
                tot_tok = int(usage.get("total_tokens") or (in_tok + out_tok))
                if tot_tok == 0 and words:
                    # Fallback estimate for older cached transcripts created before usage metadata logging
                    in_tok = int(round(raw_dur * 32.0 + 310))
                    out_tok = int(round(len(words) * 2.6 + 45))
                    tot_tok = in_tok + out_tok
            except Exception:
                pass

        out.append({
            "clip": stem,
            "src": str(c.get("src") or f"input/footage/{stem}.mp4"),
            "raw_seconds": raw_dur,
            "kept_seconds": kept_dur,
            "words_count": len(words),
            "script_lines": l_ids,
            "segments": segs_str,
            "anomalies": anoms,
            "transcribe_model": model_used,
            "input_tokens": in_tok,
            "output_tokens": out_tok,
            "total_tokens": tot_tok,
        })
    return out


def save_prepare_snapshot(
    project_name: str,
    lang: str,
    clips_data: List[Dict[str, Any]],
    segments: List[Dict[str, Any]],
    marks: List[Dict[str, Any]],
    job_start_epoch: float,
) -> Dict[str, Any]:
    """Save the per-clip telemetry snapshot at the end of `prepare.py`."""
    proj_dir = WORK / project_name
    proj_dir.mkdir(parents=True, exist_ok=True)
    clip_rows = collect_clip_telemetry(clips_data, segments)
    t_in = sum(r["input_tokens"] for r in clip_rows)
    t_out = sum(r["output_tokens"] for r in clip_rows)
    t_tot = sum(r["total_tokens"] for r in clip_rows)
    raw_sec = round(sum(r["raw_seconds"] for r in clip_rows), 2)
    kept_sec = round(sum(r["kept_seconds"] for r in clip_rows), 2)
    agent = detect_agent_usage(since_epoch=job_start_epoch)

    snapshot = {
        "project": project_name,
        "language": lang,
        "job_start_epoch": job_start_epoch,
        "prepared_at": dt.datetime.now().isoformat(timespec="seconds"),
        "agent_name": agent["agent_name"],
        "agent_model": agent["agent_model"],
        "clips_count": len(clip_rows),
        "segments_count": len(segments),
        "marks_count": len(marks),
        "raw_seconds": raw_sec,
        "video_seconds": kept_sec,
        "transcribe_in_tokens": t_in,
        "transcribe_out_tokens": t_out,
        "transcribe_total_tokens": t_tot,
        "clips": clip_rows,
    }
    write_json(proj_dir / "token_telemetry.json", snapshot)
    return snapshot


def _init_local_db(conn: sqlite3.Connection) -> None:
    conn.execute("""CREATE TABLE IF NOT EXISTS job_runs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        created_at TEXT NOT NULL,
        project TEXT NOT NULL,
        language TEXT NOT NULL,
        agent_name TEXT NOT NULL,
        agent_model TEXT NOT NULL,
        clips_count INTEGER NOT NULL,
        raw_seconds REAL NOT NULL,
        video_seconds REAL NOT NULL,
        marks_count INTEGER NOT NULL,
        transcribe_in_tokens INTEGER NOT NULL,
        transcribe_out_tokens INTEGER NOT NULL,
        transcribe_total_tokens INTEGER NOT NULL,
        agent_in_tokens INTEGER NOT NULL,
        agent_cached_tokens INTEGER NOT NULL,
        agent_out_tokens INTEGER NOT NULL,
        agent_total_tokens INTEGER NOT NULL,
        grand_total_tokens INTEGER NOT NULL,
        output_mp4 TEXT NOT NULL
    )""")
    conn.execute("""CREATE TABLE IF NOT EXISTS clip_runs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        job_id INTEGER NOT NULL,
        clip_stem TEXT NOT NULL,
        raw_seconds REAL NOT NULL,
        kept_seconds REAL NOT NULL,
        words_count INTEGER NOT NULL,
        script_lines TEXT NOT NULL,
        segments_summary TEXT NOT NULL,
        anomalies_summary TEXT NOT NULL,
        transcribe_model TEXT NOT NULL,
        input_tokens INTEGER NOT NULL,
        output_tokens INTEGER NOT NULL,
        total_tokens INTEGER NOT NULL
    )""")


def finalize_job_telemetry(
    plan_path: Path,
    plan: Dict[str, Any],
    video_seconds: float,
    output_mp4: Path,
    sync_cloud: bool = True,
) -> Dict[str, Any]:
    """Finalize clip + agent token telemetry after `render.py`, write SQL + Markdown ledgers, and sync to Railway Postgres."""
    proj_dir = plan_path.parent
    project_name = proj_dir.name
    snap_path = proj_dir / "token_telemetry.json"
    snap: Dict[str, Any] = read_json(snap_path) if snap_path.is_file() else {}

    job_start = float(snap.get("job_start_epoch") or (time.time() - 900.0))
    agent = detect_agent_usage(since_epoch=job_start)

    clips: List[Dict[str, Any]] = snap.get("clips") or []
    t_in = int(snap.get("transcribe_in_tokens") or sum(int(c.get("input_tokens") or 0) for c in clips))
    t_out = int(snap.get("transcribe_out_tokens") or sum(int(c.get("output_tokens") or 0) for c in clips))
    t_tot = int(snap.get("transcribe_total_tokens") or (t_in + t_out))

    a_in = int(agent.get("input_tokens") or 0)
    a_cache = int(agent.get("cached_tokens") or 0)
    a_out = int(agent.get("output_tokens") or 0)
    a_tot = int(agent.get("total_tokens") or (a_in + a_out))
    grand_in = t_in + a_in
    grand_out = t_out + a_out
    grand_total = t_tot + a_tot

    now_dt = dt.datetime.now()
    date_str = now_dt.strftime("%Y-%m-%d")
    ts_str = now_dt.strftime("%Y-%m-%d %H:%M:%S")
    lang = str(snap.get("language") or "auto")
    raw_sec = float(snap.get("raw_seconds") or 0.0)
    vid_sec = round(float(video_seconds or snap.get("video_seconds") or 0.0), 2)
    marks_cnt = len(plan.get("marks") or [])
    clips_cnt = int(snap.get("clips_count") or len(clips) or len(plan.get("segments") or []))
    rel_out = f"output/{output_mp4.name}"
    tok_per_min = int(round(grand_total / max(0.25, vid_sec / 60.0)))

    record = {
        "created_at": ts_str,
        "date": date_str,
        "project": project_name,
        "language": lang,
        "agent_name": agent["agent_name"],
        "agent_model": agent["agent_model"],
        "agent_context_start": agent.get("context_start", 0),
        "agent_context_end": agent.get("context_end", 0),
        "clips_count": clips_cnt,
        "raw_seconds": raw_sec,
        "video_seconds": vid_sec,
        "marks_count": marks_cnt,
        "transcribe_in_tokens": t_in,
        "transcribe_out_tokens": t_out,
        "transcribe_total_tokens": t_tot,
        "agent_in_tokens": a_in,
        "agent_cached_tokens": a_cache,
        "agent_out_tokens": a_out,
        "agent_total_tokens": a_tot,
        "overall_in_tokens": grand_in,
        "overall_out_tokens": grand_out,
        "grand_total_tokens": grand_total,
        "tokens_per_min": tok_per_min,
        "output_mp4": rel_out,
        "clips": clips,
    }
    write_json(snap_path, record)

    # 1. Write per-project markdown report: work/<project>/token_report.md
    report_md = _format_project_report_md(record)
    (proj_dir / "token_report.md").write_text(report_md, encoding="utf-8")

    # 2. Save into local SQLite database: work/zeno_telemetry.db
    try:
        LOCAL_DB.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(str(LOCAL_DB)) as conn:
            _init_local_db(conn)
            cur = conn.execute(
                """INSERT INTO job_runs (
                    created_at, project, language, agent_name, agent_model,
                    clips_count, raw_seconds, video_seconds, marks_count,
                    transcribe_in_tokens, transcribe_out_tokens, transcribe_total_tokens,
                    agent_in_tokens, agent_cached_tokens, agent_out_tokens, agent_total_tokens,
                    grand_total_tokens, output_mp4
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    ts_str, project_name, lang, record["agent_name"], record["agent_model"],
                    clips_cnt, raw_sec, vid_sec, marks_cnt,
                    t_in, t_out, t_tot, a_in, a_cache, a_out, a_tot, grand_total, rel_out,
                ),
            )
            job_id = int(cur.lastrowid or 0)
            for c in clips:
                conn.execute(
                    """INSERT INTO clip_runs (
                        job_id, clip_stem, raw_seconds, kept_seconds, words_count,
                        script_lines, segments_summary, anomalies_summary, transcribe_model,
                        input_tokens, output_tokens, total_tokens
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        job_id,
                        str(c.get("clip") or ""),
                        float(c.get("raw_seconds") or 0.0),
                        float(c.get("kept_seconds") or 0.0),
                        int(c.get("words_count") or 0),
                        str(c.get("script_lines") or "-"),
                        str(c.get("segments") or ""),
                        " ; ".join(c.get("anomalies") or []) or "clean",
                        str(c.get("transcribe_model") or ""),
                        int(c.get("input_tokens") or 0),
                        int(c.get("output_tokens") or 0),
                        int(c.get("total_tokens") or 0),
                    ),
                )
    except Exception as exc:
        print(f"  [telemetry] local SQL warning: {exc}", file=sys.stderr)

    # 3. Update root TOKEN_LEDGER.md
    _update_ledger_md(record)

    # 4. Sync to Railway PostgreSQL (/v1/telemetry) if configured
    if sync_cloud:
        _sync_to_cloud(record)

    return record


def _format_project_report_md(r: Dict[str, Any]) -> str:
    lines = [
        f"# Zeno Token & Raw Clip Telemetry — `{r['project']}`",
        "",
        f"- **Date**: `{r['created_at']}`",
        f"- **Active Agent**: **{r['agent_name']}** (`{r['agent_model']}`)",
        f"- **Clips Processed**: `{r['clips_count']}` raw clips (`{r['raw_seconds']:.1f}s` raw → `{tc(r['video_seconds'])}` final `{r['output_mp4']}`)",
        f"- **Overall Tokens**: `{r['overall_in_tokens']:,}` in / `{r['overall_out_tokens']:,}` out / **`{r['grand_total_tokens']:,}` total** (`~{r['tokens_per_min']:,} tokens/min`)",
        "",
        "## 1. Overall Token Summary (Agent + Transcription)",
        "",
        "| Layer | Provider / Model | Input Tokens | Cached Tokens | Output Tokens | Total Tokens |",
        "|---|---|---:|---:|---:|---:|",
        f"| **Raw Clip Transcription** | Gemini (`{r['clips'][0]['transcribe_model'] if r.get('clips') else 'gemini-3.5-flash-lite'}`) | `{r['transcribe_in_tokens']:,}` | `0` | `{r['transcribe_out_tokens']:,}` | **`{r['transcribe_total_tokens']:,}`** |",
        f"| **AI Agent Director** | **{r['agent_name']}** (`{r['agent_model']}`) | `{r['agent_in_tokens']:,}` | `{r['agent_cached_tokens']:,}` | `{r['agent_out_tokens']:,}` | **`{r['agent_total_tokens']:,}`** |",
        f"| **OVERALL TOTAL** | Combined Job Spend | **`{r['overall_in_tokens']:,}`** | **`{r['agent_cached_tokens']:,}`** | **`{r['overall_out_tokens']:,}`** | **`{r['grand_total_tokens']:,}`** |",
        "",
        "## 2. Clip-by-Clip Raw Process & Token Breakdown",
        "",
        "| Clip | Raw → Kept | Words | Script Lines | Kept Segments | Raw Clip Process (Retakes / Cuts / Anomalies) | In Tokens | Out Tokens | Clip Total |",
        "|---|---|---|---|---|---|---:|---:|---:|",
    ]
    for c in r.get("clips") or []:
        anom_txt = "; ".join(c.get("anomalies") or []) or "Clean take (pause-snapped)"
        lines.append(
            f"| `{c['clip']}` | `{c['raw_seconds']:.1f}s → {c['kept_seconds']:.1f}s` | {c['words_count']} | `{c['script_lines']}` | `{c['segments']}` | {anom_txt} | `{c['input_tokens']:,}` | `{c['output_tokens']:,}` | **`{c['total_tokens']:,}`** |"
        )
    lines.append("")
    return "\n".join(lines)


def _update_ledger_md(r: Dict[str, Any]) -> None:
    header = """# Zeno — Automated Token & Clip Telemetry Ledger

Automatically updated by [`scripts/token_tracker.py`](scripts/token_tracker.py) after every video edit (`prepare.py` → `render.py`).
Full relational history is also stored in local SQLite (`work/zeno_telemetry.db`) and synced to Railway PostgreSQL (`/admin`).

## 1. All Video Edit Runs (Agent + Transcription Rollup)

| Date | Project (`Lang`) | Agent & Model | Clips (`Raw → Final`) | Transcribe Tokens (`In / Out / Total`) | Agent Tokens (`In / Cached / Out / Total`) | Overall (`In / Out / Grand Total`) |
|---|---|---|---|---|---|---|
"""
    row = (
        f"| `{r['created_at']}` | **`{r['project']}`** (`{r['language']}`) | **{r['agent_name']}** (`{r['agent_model']}`) | "
        f"{r['clips_count']} (`{r['raw_seconds']:.0f}s → {tc(r['video_seconds'])}`) | "
        f"`{r['transcribe_in_tokens']:,}` / `{r['transcribe_out_tokens']:,}` / **`{r['transcribe_total_tokens']:,}`** | "
        f"`{r['agent_in_tokens']:,}` / `{r['agent_cached_tokens']:,}` / `{r['agent_out_tokens']:,}` / **`{r['agent_total_tokens']:,}`** | "
        f"`{r['overall_in_tokens']:,}` in / `{r['overall_out_tokens']:,}` out / **`{r['grand_total_tokens']:,}`** |"
    )

    existing_rows: List[str] = []
    if LEDGER_MD.is_file():
        for line in LEDGER_MD.read_text(encoding="utf-8", errors="ignore").splitlines():
            if line.startswith("| `20"):
                existing_rows.append(line)
    existing_rows.append(row)

    clip_section = [
        "",
        f"## 2. Latest Job Clip-by-Clip Breakdown — `{r['project']}` (`{r['created_at']}`)",
        "",
        "| Clip | Raw → Kept | Words | Script Lines | Kept Segments | Raw Clip Process (Retakes / Cuts / Anomalies) | In Tokens | Out Tokens | Clip Total |",
        "|---|---|---|---|---|---|---:|---:|---:|",
    ]
    for c in r.get("clips") or []:
        anom_txt = "; ".join(c.get("anomalies") or []) or "Clean take (pause-snapped)"
        clip_section.append(
            f"| `{c['clip']}` | `{c['raw_seconds']:.1f}s → {c['kept_seconds']:.1f}s` | {c['words_count']} | `{c['script_lines']}` | `{c['segments']}` | {anom_txt} | `{c['input_tokens']:,}` | `{c['output_tokens']:,}` | **`{c['total_tokens']:,}`** |"
        )
    clip_section.append("")

    content = header + "\n".join(existing_rows) + "\n" + "\n".join(clip_section)
    LEDGER_MD.write_text(content, encoding="utf-8")


def _sync_to_cloud(r: Dict[str, Any]) -> bool:
    url = os.getenv("SCRIPTCUT_CLOUD_URL", "").strip()
    token = os.getenv("SCRIPTCUT_CLOUD_TOKEN", "").strip()
    if not url or not token:
        return False
    try:
        import httpx
        resp = httpx.post(
            url.rstrip("/") + "/v1/telemetry",
            headers={"Authorization": f"Bearer {token}"},
            json=r,
            timeout=10.0,
        )
        return resp.status_code == 200
    except Exception:
        return False


def print_console_summary(r: Dict[str, Any]) -> None:
    print("\n" + "=" * 78)
    print(f"ZENO TOKEN & CLIP TELEMETRY — {r['project']} ({r['agent_name']} · {r['agent_model']})")
    print("=" * 78)
    if r.get("clips"):
        print(f"{'Clip':<6} {'Raw->Kept':<13} {'Words':<6} {'Lines':<8} {'In Tok':>7} {'Out Tok':>8} {'Total':>8}  Process Notes")
        print("-" * 78)
        for c in r["clips"]:
            note = ("; ".join(c.get("anomalies") or []) or "clean")[:34]
            print(
                f"{c['clip']:<6} {c['raw_seconds']:>4.1f}s->{c['kept_seconds']:<5.1f}s "
                f"{c['words_count']:<6} {c['script_lines']:<8} "
                f"{c['input_tokens']:>7,} {c['output_tokens']:>8,} {c['total_tokens']:>8,}  {note}"
            )
        print("-" * 78)
    print(f"Transcription (Gemini) : {r['transcribe_in_tokens']:>8,} in | {r['transcribe_out_tokens']:>8,} out | {r['transcribe_total_tokens']:>9,} total")
    print(f"Agent ({r['agent_name']:<14}): {r['agent_in_tokens']:>8,} in | {r['agent_out_tokens']:>8,} out | {r['agent_total_tokens']:>9,} total (cached: {r['agent_cached_tokens']:,})")
    print(f"OVERALL JOB TOTAL      : {r['overall_in_tokens']:>8,} in | {r['overall_out_tokens']:>8,} out | {r['grand_total_tokens']:>9,} total (~{r['tokens_per_min']:,} tok/min)")
    print(f"Saved -> TOKEN_LEDGER.md | work/{r['project']}/token_report.md | work/zeno_telemetry.db")
    print("=" * 78)


def main() -> None:
    ap = argparse.ArgumentParser(description="Inspect or finalize Zeno per-clip and per-agent token telemetry.")
    ap.add_argument("--project", help="Project folder name under work/<project>")
    ap.add_argument("--list", action="store_true", help="List recorded runs from work/zeno_telemetry.db")
    ap.add_argument("--no-cloud", action="store_true", help="Skip syncing to Railway Postgres")
    args = ap.parse_args()

    if args.list:
        if not LOCAL_DB.is_file():
            print("No runs recorded in work/zeno_telemetry.db yet.")
            return
        with sqlite3.connect(str(LOCAL_DB)) as conn:
            rows = conn.execute(
                "SELECT id, created_at, project, agent_name, agent_model, clips_count, video_seconds, "
                "transcribe_total_tokens, agent_total_tokens, grand_total_tokens FROM job_runs ORDER BY id DESC LIMIT 20"
            ).fetchall()
        for r in rows:
            print(f"#{r[0]} [{r[1]}] {r[2]} | {r[3]} ({r[4]}) | {r[5]} clips ({r[6]:.1f}s) | Transcribe: {r[7]:,} | Agent: {r[8]:,} | Total: {r[9]:,}")
        return

    if args.project:
        pdir = WORK / args.project
        plan_path = pdir / "plan.json"
        if not plan_path.is_file():
            raise SystemExit(f"No plan.json found in {pdir}")
        plan = read_json(plan_path)
        segs = plan.get("segments") or []
        vid_sec = sum(max(0.0, float(s.get("end", 0.0)) - float(s.get("start", 0.0))) for s in segs)
        out_mp4 = ROOT / str(plan.get("output") or f"output/{args.project}.mp4")
        rec = finalize_job_telemetry(plan_path, plan, vid_sec, out_mp4, sync_cloud=not args.no_cloud)
        print_console_summary(rec)
    else:
        agent = detect_agent_usage()
        print(json.dumps(agent, indent=2))


if __name__ == "__main__":
    main()
