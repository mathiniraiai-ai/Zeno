#!/usr/bin/env python3
"""Automated Per-Clip & Per-Agent Token Telemetry Tracker for Zeno.

Tracks and stores:
1. Per-clip raw process telemetry during `prepare.py`:
   - Clip name, raw duration -> kept duration, words aligned, script lines matched,
     anomalies pruned ([RETAKE], [TAIL_BABBLE_CUT], [PAUSE_SPLIT], [BOUNDARY_DUP]),
     and Gemini transcription tokens (input_tokens, output_tokens, total_tokens).
2. Active AI Agent & Model telemetry during `prepare.py` -> `render.py`:
   - Reads the named Codex chat's counters, or scoped Claude/Antigravity harness usage.
   - Computes agent input tokens, cached tokens, output tokens, and total tokens spent on the job.
3. Persists every run to:
   - `work/<project>/token_telemetry.json` & `work/<project>/token_report.md`
   - Local SQL database: `work/zeno_telemetry.db` (`job_runs` and `clip_runs` tables)
   - Notable repo ledger: `TOKEN_LEDGER.md` (and `README.md` token usage log)
   - Railway PostgreSQL database via `POST /v1/telemetry` (when `SCRIPTCUT_CLOUD_URL` + `SCRIPTCUT_CLOUD_TOKEN` are set)

CLI usage:
    .venv/bin/python scripts/token_tracker.py --project <project_name> [--refresh] [--sync-cloud]
    .venv/bin/python scripts/token_tracker.py --list
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
from common import OUTPUT, ROOT, WORK, read_json, tc, write_json  # noqa: E402

LEDGER_MD = ROOT / "TOKEN_LEDGER.md"
LOCAL_DB = WORK / "zeno_telemetry.db"


def _iso_to_epoch(ts_str: str) -> float:
    if not ts_str:
        return 0.0
    try:
        return dt.datetime.fromisoformat(ts_str.replace("Z", "+00:00")).timestamp()
    except Exception:
        return 0.0


CODE_PATH_HINTS = ("/scripts/", "/tests/", "/service/", "/agent/", "/styles/", "/subtitles/")


def claude_window_usage(lines: List[str], cutoff: float, ceiling: float) -> Optional[Dict[str, Any]]:
    """Exact token sums of the assistant turns of one Claude Code transcript that fall inside [cutoff, ceiling]: fresh input, cache writes, cache
    reads and output, each summed over every turn (the old report took the last turn's cache and the growth of the context). It also notes whether the
    window holds work on Zeno's own code, in which case the number is not the cost of one edit."""
    # Claude Code writes one log line per content block of a response (each parallel tool call is its own line) and every line repeats that response's
    # usage, so lines are grouped by message id and each response is counted once, taking the largest value of each field.
    by_id: Dict[str, Tuple[float, Dict[str, int]]] = {}
    order: List[str] = []
    code_edits = 0
    model_name = "unknown"
    for n, line in enumerate(lines):
        if not line.strip():
            continue
        try:
            d = json.loads(line)
        except ValueError:
            continue
        if d.get("type") != "assistant" or not isinstance(d.get("message"), dict):
            continue
        msg = d["message"]
        u = msg.get("usage") or {}
        if not u:
            continue
        ts = _iso_to_epoch(str(d.get("timestamp") or ""))
        if not (cutoff <= ts <= ceiling):
            continue
        if msg.get("model"):
            model_name = str(msg["model"])
        mid = str(msg.get("id") or f"line{n}")
        cur = by_id.get(mid)
        if cur is None:
            by_id[mid] = (ts, {k: int(u.get(k) or 0) for k in ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens", "output_tokens")})
            order.append(mid)
        else:
            for k in cur[1]:
                cur[1][k] = max(cur[1][k], int(u.get(k) or 0))
        for blk in msg.get("content") or []:
            if isinstance(blk, dict) and blk.get("type") == "tool_use" and blk.get("name") in ("Edit", "Write", "MultiEdit", "NotebookEdit"):
                path = str((blk.get("input") or {}).get("file_path") or "").replace("\\", "/")
                if any(h in path for h in CODE_PATH_HINTS) or path.endswith(".py"):
                    code_edits += 1
            elif isinstance(blk, dict) and blk.get("type") == "tool_use" and blk.get("name") == "Bash":
                cmd = str((blk.get("input") or {}).get("command") or "").replace("scripts/zeno.py", "")      # running Zeno is the edit; changing code through the shell (a commit, a push, an in-place edit, a script that writes files) is development
                if any(w in cmd for w in ("git commit", "git push", "sed -i", ".write_text(", "unittest")) and (any(h in cmd for h in CODE_PATH_HINTS) or ".py" in cmd):
                    code_edits += 1
    turns = [by_id[m] for m in order]
    if not turns:
        return None
    g = lambda u, k: int(u.get(k) or 0)   # noqa: E731
    fresh = sum(g(u, "input_tokens") for _, u in turns)
    write = sum(g(u, "cache_creation_input_tokens") for _, u in turns)
    read = sum(g(u, "cache_read_input_tokens") for _, u in turns)
    out = sum(g(u, "output_tokens") for _, u in turns)
    first, last = turns[0][1], turns[-1][1]
    ctx = lambda u: g(u, "input_tokens") + g(u, "cache_creation_input_tokens") + g(u, "cache_read_input_tokens")   # noqa: E731
    return {
        "agent_name": "Claude Code",
        "agent_model": model_name,
        "steps_count": len(turns),
        "turns": len(turns),
        "context_start": ctx(first),
        "context_end": ctx(last),
        "input_tokens": fresh + write,
        "cache_write_tokens": write,
        "cached_tokens": read,
        "output_tokens": out,
        "total_tokens": fresh + write + out,
        "token_precision": "exact",
        "code_edits": code_edits,
        "window_minutes": round((turns[-1][0] - turns[0][0]) / 60.0, 1),
        "mixed_with_development": code_edits > 0,
    }


def detect_agent_usage(
    since_epoch: Optional[float] = None,
    until_epoch: Optional[float] = None,
    session_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Auto-detect which AI agent harness (Antigravity vs Claude Code) and model are active,
    and measure ONLY the input/output tokens spent during this specific video edit window.
    """
    now = time.time()
    cutoff = since_epoch if (since_epoch and since_epoch > 0) else (now - 900.0)
    ceiling = until_epoch if (until_epoch and until_epoch > 0) else (now + 60.0)

    import codex_usage
    use_codex = bool(session_id or not os.getenv("CLAUDECODE"))
    codex = codex_usage.detect(cutoff, ceiling, ROOT, session_id=session_id) if use_codex else None
    if codex is not None:
        return codex
    if use_codex and (session_id or os.getenv("CODEX_THREAD_ID")):
        return {"agent_name": "Codex", "agent_model": os.getenv("ZENO_MODEL", "unknown"),
                "input_tokens": 0, "cached_tokens": 0, "output_tokens": 0,
                "total_tokens": 0, "token_precision": "unavailable",
                "session_id": session_id or os.getenv("CODEX_THREAD_ID")}

    candidates: List[Tuple[float, Dict[str, Any]]] = []

    # 1. Check Antigravity transcripts (~/.gemini/antigravity/brain/*/.system_generated/logs/transcript.jsonl)
    agy_pattern = os.path.expanduser("~/.gemini/antigravity/brain/*/.system_generated/logs/transcript.jsonl")
    agy_files = [] if os.getenv("CLAUDECODE") else sorted(glob.glob(agy_pattern), key=os.path.getmtime, reverse=True)
    for path_str in agy_files[:3]:
        try:
            mtime = os.path.getmtime(path_str)
            lines = Path(path_str).read_text(encoding="utf-8", errors="ignore").splitlines()
            if not lines:
                continue
            raw_sample = "\n".join(lines[-40:])
            if len(agy_files) > 1 and ("Zeno" not in raw_sample and "Claude Editor" not in raw_sample) and (now - mtime > 300):
                continue

            # Parse exact model name from Antigravity's <USER_SETTINGS_CHANGE> blocks
            model_name = "unknown"
            for line in lines:
                if "USER_SETTINGS_CHANGE" in line and "Model Selection" in line:
                    try:
                        obj = json.loads(line)
                        content_str = str(obj.get("content") or "")
                        m = re.search(
                            r"setting `Model Selection` from [^\n]+? to ([A-Za-z0-9 ._-]+?)(?:\s*\(|\.\s|\.\n)",
                            content_str,
                        )
                        if m:
                            cand_model = m.group(1).strip()
                            if len(cand_model) >= 4:
                                model_name = cand_model
                    except Exception:
                        pass

            # Collect steps strictly inside the video edit window [cutoff, ceiling]
            window_steps = []
            all_parsed = []
            for line in lines:
                if not line.strip():
                    continue
                d = json.loads(line)
                step_ts = _iso_to_epoch(str(d.get("created_at") or ""))
                all_parsed.append((step_ts, d))
                if cutoff <= step_ts <= ceiling:
                    window_steps.append(d)


            if window_steps:
                in_chars = 0
                out_chars = 0
                planner_steps = []
                for s in window_steps:
                    st = str(s.get("type") or "")
                    if st in ("USER_INPUT", "GENERIC", "SYSTEM_MESSAGE", "TOOL_RESPONSE", "SYSTEM"):
                        in_chars += len(str(s.get("content") or ""))
                    elif st == "PLANNER_RESPONSE":
                        out_chars += (
                            len(str(s.get("content") or ""))
                            + len(str(s.get("thinking") or ""))
                            + len(json.dumps(s.get("tool_calls") or [], ensure_ascii=False))
                        )
                        if s.get("input_tokens") or s.get("output_tokens"):
                            planner_steps.append(s)

                edit_in_tok = max(1, in_chars // 4)
                edit_out_tok = max(1, out_chars // 4)
                first_ctx = (
                    int(planner_steps[0].get("input_tokens") or 0) + int(planner_steps[0].get("cache_read_tokens") or 0)
                    if planner_steps else 0
                )
                last_ctx = (
                    int(planner_steps[-1].get("input_tokens") or 0) + int(planner_steps[-1].get("cache_read_tokens") or 0)
                    if planner_steps else 0
                )
                cached_in = sum(int(step.get("cache_read_tokens") or 0) for step in planner_steps)
                if planner_steps:
                    edit_in_tok = sum(int(step.get("input_tokens") or 0) for step in planner_steps)
                    edit_out_tok = sum(int(step.get("output_tokens") or 0) for step in planner_steps)

                candidates.append((
                    mtime,
                    {
                        "agent_name": "Antigravity",
                        "agent_model": model_name,
                        "steps_count": len(planner_steps) or len(window_steps),
                        "context_start": first_ctx,
                        "context_end": last_ctx,
                        "input_tokens": edit_in_tok,
                        "cached_tokens": cached_in,
                        "output_tokens": edit_out_tok,
                        "total_tokens": edit_in_tok + edit_out_tok,
                        "token_precision": "exact" if planner_steps else "est",
                        "session_id": Path(path_str).parts[-4] if len(Path(path_str).parts) >= 4 else "unknown",
                    },
                ))
                break
        except Exception:
            continue

    # 2. Check Claude Code transcripts (~/.claude/projects/**/*.jsonl)
    claude_sid = os.getenv("CLAUDE_CODE_SESSION_ID", "") if os.getenv("CLAUDECODE") else ""
    claude_pattern = os.path.expanduser(f"~/.claude/projects/**/{claude_sid or '*'}.jsonl")
    claude_files = sorted(glob.glob(claude_pattern, recursive=True), key=os.path.getmtime, reverse=True)
    for path_str in claude_files[:5]:
        try:
            mtime = os.path.getmtime(path_str)
            lines = Path(path_str).read_text(encoding="utf-8", errors="ignore").splitlines()
            import agent_log
            if not agent_log.chat_size(path_str, ROOT):
                continue
            got = claude_window_usage(lines, cutoff, ceiling)
            if got is None:
                continue
            got["session_id"] = Path(path_str).stem
            candidates.append((mtime, got))
            break
        except Exception:
            continue

    if not candidates:
        return {
            "agent_name": os.getenv("ZENO_AGENT", "unknown"),
            "agent_model": os.getenv("ZENO_MODEL", "unknown"),
            "steps_count": 0,
            "context_start": 0,
            "context_end": 0,
            "input_tokens": 0,
            "cached_tokens": 0,
            "output_tokens": 0,
            "total_tokens": 0,
            "token_precision": "unavailable",
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
        c_segs = [s for s in segments if str(s.get("src", "")) == str(c.get("src", ""))]
        kept_dur = round(sum(max(0.0, float(s.get("end", 0.0)) - float(s.get("start", 0.0))) for s in c_segs), 2)
        words = c.get("hq_words") or []
        l_ids = ",".join(l["id"] for l in c.get("matched_lines", [])) or "-"
        segs_str = " + ".join(f"{s['id']}[{float(s['start']):.2f}-{float(s['end']):.2f}s]" for s in c_segs) or ("unused take" if c.get("dropped_take") else "dropped")
        anoms = list(c.get("anomalies") or [])

        # Read Gemini transcription token usage from work/<stem>/gemini_transcript.json
        gt = {}
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
            "transcription_reused": c.get("transcription_reused"),
            "transcription_usage_known": bool(gt_path.is_file() and isinstance(gt.get("usage"), dict)
                                              and any(k in gt["usage"] for k in ("input_tokens", "output_tokens", "total_tokens"))),
        })
    return out


def _seg_zoom_repr(seg: Dict[str, Any]) -> str:
    z0 = float(seg.get("zoom", 1.0) or 1.0)
    z1 = float(seg.get("zoom_to", z0) or z0)
    z_style = str(seg.get("zoom_style") or "").strip()
    z_at = seg.get("zoom_at")
    if z_style:
        base = f"{z_style}:{z1:g}" if abs(z0 - 1.0) < 1e-4 else f"{z_style}:{z0:g}->{z1:g}"
    elif abs(z1 - z0) > 1e-4:
        base = f"{z0:g}->{z1:g}"
    else:
        base = f"{z0:g}"
    return f"{base}@{float(z_at):.2f}" if z_at is not None else base


def _base_line_id(seg_id: str) -> str:
    m = re.match(r"^(L\d+)", str(seg_id or "").strip(), re.I)
    return m.group(1).upper() if m else str(seg_id or "").strip()


def compute_edl_learning_diff(initial_edl_text: str, final_edl_text: str) -> Dict[str, Any]:
    """Compare baseline `plan.auto.edl` vs refined `plan.edl` to extract structured editorial learnings."""
    import edl_parser

    init_plan = edl_parser.parse_edl(initial_edl_text) if initial_edl_text.strip() else {}
    final_plan = edl_parser.parse_edl(final_edl_text) if final_edl_text.strip() else {}

    init_segs = init_plan.get("segments") or []
    final_segs = final_plan.get("segments") or []
    init_by_id = {str(s.get("id")): s for s in init_segs}
    final_by_id = {str(s.get("id")): s for s in final_segs}

    init_by_line: Dict[str, List[Dict[str, Any]]] = {}
    for s in init_segs:
        init_by_line.setdefault(_base_line_id(str(s.get("id"))), []).append(s)
    final_by_line: Dict[str, List[Dict[str, Any]]] = {}
    for s in final_segs:
        final_by_line.setdefault(_base_line_id(str(s.get("id"))), []).append(s)

    take_swaps: List[Dict[str, Any]] = []
    for line_id, f_list in final_by_line.items():
        i_list = init_by_line.get(line_id) or []
        if not i_list:
            continue
        i_clips = sorted({Path(str(s.get("src") or "")).name for s in i_list if s.get("src")})
        f_clips = sorted({Path(str(s.get("src") or "")).name for s in f_list if s.get("src")})
        if i_clips != f_clips:
            take_swaps.append({
                "line_id": line_id,
                "from_clips": i_clips,
                "to_clips": f_clips,
            })

    cut_timing_deltas: List[Dict[str, Any]] = []
    zoom_changes: List[Dict[str, Any]] = []
    transition_changes: List[Dict[str, Any]] = []
    layout_changes: List[Dict[str, Any]] = []

    zoom_dist: Dict[str, int] = {}
    tr_dist: Dict[str, int] = {}
    layout_dist: Dict[str, Dict[str, Any]] = {}

    default_init_layout = str(init_plan.get("preset") or "single")
    default_final_layout = str(final_plan.get("preset") or "single")

    for f_seg in final_segs:
        sid = str(f_seg.get("id") or "")
        dur = round(max(0.0, float(f_seg.get("end", 0.0)) - float(f_seg.get("start", 0.0))), 3)
        lay = str(f_seg.get("layout") or default_final_layout)
        z_repr = _seg_zoom_repr(f_seg)
        tr_name = str(f_seg.get("transition") or "cut")
        tr_dur = f_seg.get("transition_duration")
        tr_repr = f"{tr_name}@{float(tr_dur):g}" if tr_dur else tr_name

        zoom_dist[z_repr] = zoom_dist.get(z_repr, 0) + 1
        tr_dist[tr_repr] = tr_dist.get(tr_repr, 0) + 1
        ld = layout_dist.setdefault(lay, {"segments": 0, "seconds": 0.0})
        ld["segments"] += 1
        ld["seconds"] = round(ld["seconds"] + dur, 2)

        i_seg = init_by_id.get(sid)
        if not i_seg:
            continue

        ds = round(float(f_seg.get("start", 0.0)) - float(i_seg.get("start", 0.0)), 3)
        de = round(float(f_seg.get("end", 0.0)) - float(i_seg.get("end", 0.0)), 3)
        if abs(ds) >= 0.01 or abs(de) >= 0.01:
            cut_timing_deltas.append({
                "seg_id": sid,
                "clip": Path(str(f_seg.get("src") or "")).name,
                "start_delta_sec": ds,
                "end_delta_sec": de,
                "dur_delta_sec": round(de - ds, 3),
            })

        i_z = _seg_zoom_repr(i_seg)
        if i_z != z_repr:
            zoom_changes.append({"seg_id": sid, "from": i_z, "to": z_repr})

        i_tr_name = str(i_seg.get("transition") or "cut")
        i_tr_dur = i_seg.get("transition_duration")
        i_tr = f"{i_tr_name}@{float(i_tr_dur):g}" if i_tr_dur else i_tr_name
        if i_tr != tr_repr:
            transition_changes.append({"seg_id": sid, "from": i_tr, "to": tr_repr})

        i_lay = str(i_seg.get("layout") or default_init_layout)
        if i_lay != lay:
            layout_changes.append({"seg_id": sid, "from": i_lay, "to": lay})

    init_marks = init_plan.get("marks") or []
    final_marks = final_plan.get("marks") or []
    wrong_cnt = sum(1 for m in final_marks if str(m.get("type", "")).lower() in ("wrong", "x", "cross", "error"))
    right_cnt = sum(1 for m in final_marks if str(m.get("type", "")).lower() in ("right", "check", "correct"))

    marks_summary = {
        "mark_pack": str(final_plan.get("mark_pack") or init_plan.get("mark_pack") or "flash"),
        "initial_count": len(init_marks),
        "final_count": len(final_marks),
        "wrong_count": wrong_cnt,
        "right_count": right_cnt,
        "marks": [
            {"segment": m.get("segment"), "type": m.get("type"), "at": m.get("at"), "sfx": m.get("sfx")}
            for m in final_marks
        ],
    }

    segments_added = [sid for sid in final_by_id if sid not in init_by_id]
    segments_removed = [sid for sid in init_by_id if sid not in final_by_id]

    has_edits = bool(
        take_swaps or cut_timing_deltas or zoom_changes or transition_changes
        or layout_changes or segments_added or segments_removed
        or len(init_marks) != len(final_marks)
    )

    return {
        "has_edits": has_edits,
        "initial_segments_count": len(init_segs),
        "final_segments_count": len(final_segs),
        "segments_added": segments_added,
        "segments_removed": segments_removed,
        "take_swaps": take_swaps,
        "cut_timing_deltas": cut_timing_deltas,
        "zoom_changes": {"distribution": zoom_dist, "changes": zoom_changes},
        "transition_changes": {"distribution": tr_dist, "changes": transition_changes},
        "layout_distribution": {"distribution": layout_dist, "changes": layout_changes},
        "marks_summary": marks_summary,
    }


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
    snap_path = proj_dir / "token_telemetry.json"
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
        "agent_session_id": agent.get("session_id"),
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
    write_json(snap_path, snapshot)
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
    columns = {row[1] for row in conn.execute("PRAGMA table_info(job_runs)")}
    if "details_json" not in columns:
        conn.execute("ALTER TABLE job_runs ADD COLUMN details_json TEXT NOT NULL DEFAULT '{}'")
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
    completed_at: Optional[float] = None,
) -> Dict[str, Any]:
    """Finalize clip + agent token telemetry after `render.py`, write SQL + Markdown ledgers, and sync to Railway Postgres."""
    proj_dir = plan_path.parent
    project_name = proj_dir.name
    snap_path = proj_dir / "token_telemetry.json"
    snap: Dict[str, Any] = read_json(snap_path) if snap_path.is_file() else {}

    job_start = float(snap.get("job_start_epoch") or (time.time() - 900.0))
    job_end = float(completed_at if completed_at is not None else snap.get("job_end_epoch") or time.time())
    agent = detect_agent_usage(since_epoch=job_start, until_epoch=job_end,
                               session_id=snap.get("agent_session_id") if str(snap.get("agent_name", "")).startswith("Codex") else None)
    import cost_control
    agent = cost_control.combine(agent, proj_dir, job_start, job_end)

    clips: List[Dict[str, Any]] = snap.get("clips") or []
    t_in = int(snap.get("transcribe_in_tokens") or sum(int(c.get("input_tokens") or 0) for c in clips))
    t_out = int(snap.get("transcribe_out_tokens") or sum(int(c.get("output_tokens") or 0) for c in clips))
    t_tot = int(snap.get("transcribe_total_tokens") or (t_in + t_out))

    # An inspect has no cuts. Recompute kept duration from the final parsed plan.
    for clip in clips:
        def source_path(value: Any) -> Path:
            path = Path(str(value or ""))
            return (path if path.is_absolute() else ROOT / path).resolve()
        source = source_path(clip.get("src"))
        kept = [seg for seg in plan.get("segments", [])
                if source_path(seg.get("src")) == source]
        clip["kept_seconds"] = round(sum(float(seg["end"]) - float(seg["start"]) for seg in kept), 3)
        clip["segments"] = " + ".join(str(seg["id"]) for seg in kept) or "listener only / unused"
        clip["script_lines"] = ",".join(dict.fromkeys(_base_line_id(str(seg["id"])) for seg in kept)) or "-"

    fresh_clips = [c for c in clips if c.get("transcription_reused") is False]
    reused_clips = [c for c in clips if c.get("transcription_reused") is True]
    fresh_transcription = sum(c.get("total_tokens", 0) for c in fresh_clips)
    reused_transcription = sum(c.get("total_tokens", 0) for c in reused_clips)
    import token_cost
    cost = token_cost.estimate(agent)
    a_in = int(agent.get("input_tokens") or 0)
    a_cache = int(agent.get("cached_tokens") or 0)
    a_out = int(agent.get("output_tokens") or 0)
    a_tot = int(agent.get("total_tokens") or (a_in + a_out))
    grand_in = sum(c.get("input_tokens", 0) for c in fresh_clips) + a_in + a_cache
    grand_out = sum(c.get("output_tokens", 0) for c in fresh_clips) + a_out
    grand_total = fresh_transcription + a_tot + a_cache

    now_dt = dt.datetime.now()
    date_str = now_dt.strftime("%Y-%m-%d")
    ts_str = now_dt.strftime("%Y-%m-%d %H:%M:%S")
    lang = str(snap.get("language") or "auto")
    raw_sec = float(snap.get("raw_seconds") or 0.0)
    vid_sec = round(float(video_seconds or snap.get("video_seconds") or 0.0), 2)
    marks_cnt = len(plan.get("marks") or [])
    clips_cnt = int(snap.get("clips_count") or len(clips) or len(plan.get("segments") or []))
    rel_out = str(output_mp4)
    tok_per_min = int(round(a_tot / max(0.25, vid_sec / 60.0)))

    initial_edl_p = proj_dir / "plan.auto.edl"
    final_edl_p = proj_dir / "plan.edl"
    initial_edl = initial_edl_p.read_text(encoding="utf-8", errors="ignore") if initial_edl_p.is_file() else ""
    final_edl = final_edl_p.read_text(encoding="utf-8", errors="ignore") if final_edl_p.is_file() else ""
    if not initial_edl and final_edl:
        initial_edl = final_edl
    try:
        edl_diff = compute_edl_learning_diff(initial_edl, final_edl)
    except Exception:
        edl_diff = {}

    try:
        import brain_memory
        # only what a person recorded on this PC: the rules that ship with Zeno are not "learned" and must not be re-uploaded
        learned_rules = [r for r in brain_memory.load_brain_memory("micronarrative") if r.get("source") == "remember"]
    except Exception:
        learned_rules = []

    spec_tuning: Dict[str, Any] = {}
    spec_path = ROOT / "styles" / "micronarrative" / "spec.json"
    if spec_path.is_file():
        try:
            spec_tuning = (read_json(spec_path) or {}).get("tuning") or {}
        except Exception:
            spec_tuning = {}
    assets_used = {
        "preset": str(plan.get("preset") or "auto"),
        "mark_pack": str(plan.get("mark_pack") or "flash"),
        "sfx_pack": str(plan.get("sfx_pack") or "default"),
        "wrong_sfx_primary": str(plan.get("error_sfx") or spec_tuning.get("wrong_sfx_primary") or "sn_wrong.wav"),
        "wrong_sfx_layer": str(spec_tuning.get("wrong_sfx_layer") or "none"),
        "right_sfx_primary": str(spec_tuning.get("right_sfx_primary") or "correct_ding.wav"),
        "right_sfx_layer": str(spec_tuning.get("right_sfx_layer") or "sn_correct.wav"),
        "whoosh_sfx": str(spec_tuning.get("whoosh_sfx") or "whoosh.wav"),
    }

    try:
        import auth_session
        user_email = auth_session.get_logged_in_email()
    except Exception:
        user_email = os.getenv("ZENO_USER_EMAIL", "")

    record = {
        "created_at": ts_str,
        "date": date_str,
        "project": project_name,
        "language": lang,
        "user_email": user_email,
        "job_start_epoch": job_start,
        "job_end_epoch": job_end,
        "agent_name": agent["agent_name"],
        "agent_model": agent["agent_model"],
        "agent_token_precision": agent.get("token_precision", "unavailable"),
        "agent_session_id": agent.get("session_id"),
        "agent_cache_write_tokens": agent.get("cache_write_tokens", 0),
        "agent_reasoning_tokens": agent.get("reasoning_tokens", 0),
        "agent_steps": agent.get("steps_count", 0),
        "agent_total_including_cache": a_tot + a_cache,
        "transcribe_new_total_tokens": fresh_transcription,
        "transcribe_reused_total_tokens": reused_transcription,
        "transcribe_reuse_known": all(c.get("transcription_reused") is not None for c in clips),
        "cost_estimate": cost,
        "cost_stages": cost_control.stage_report(proj_dir, agent, job_start, job_end),
        "host_usage_unknown": agent.get("host_usage_unknown", False),
        "direct_usage_unmerged": agent.get("direct_usage_unmerged", False),
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
        "grand_total_definition": "agent including cached input + known new transcription",
        "transcription_unknown_clips": sum(c.get("transcription_reused") is None or
                                           (c.get("transcription_reused") is False and not c.get("transcription_usage_known"))
                                           for c in clips),
        "tokens_per_min": tok_per_min,
        "output_mp4": rel_out,
        "clips": clips,
        "initial_edl": initial_edl,
        "final_edl": final_edl,
        "edl_diff": edl_diff,
        "learned_rules": learned_rules,
        "assets_used": assets_used,
    }
    try:
        import job_report
        record["job_report"] = job_report.final_report(proj_dir, plan)
        record["job_report"]["agent"] = {k: agent.get(k) for k in ("turns", "cache_write_tokens", "code_edits", "window_minutes", "mixed_with_development") if k in agent}
        record["job_report"]["accounting"] = {k: record[k] for k in (
            "agent_session_id", "agent_token_precision", "agent_total_including_cache",
            "agent_reasoning_tokens", "transcribe_new_total_tokens", "transcribe_reused_total_tokens",
            "transcription_unknown_clips", "cost_estimate", "grand_total_definition")}
    except Exception:
        pass
    write_json(snap_path, record)

    # 1. Write per-project markdown report: work/<project>/token_report.md
    report_md = _format_project_report_md(record)
    (proj_dir / "token_report.md").write_text(report_md, encoding="utf-8")

    # 2. Save into local SQLite database: work/zeno_telemetry.db
    try:
        LOCAL_DB.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(str(LOCAL_DB)) as conn:
            _init_local_db(conn)
            # Retrying a report for the same output replaces that output's local row atomically.
            previous = conn.execute("SELECT id FROM job_runs WHERE project=? AND output_mp4=?",
                                    (project_name, rel_out)).fetchall()
            for (old_id,) in previous:
                conn.execute("DELETE FROM clip_runs WHERE job_id=?", (old_id,))
                conn.execute("DELETE FROM job_runs WHERE id=?", (old_id,))
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
            conn.execute("UPDATE job_runs SET details_json=? WHERE id=?", (json.dumps(record, ensure_ascii=False), job_id))
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

    _update_readme(record)

    # 4. Sync to Railway PostgreSQL (/v1/telemetry) if configured
    if sync_cloud:
        _sync_to_cloud(record)

    return record


def _format_project_report_md(r: Dict[str, Any]) -> str:
    precision = r.get("agent_token_precision", "unavailable")
    total = r.get("agent_total_including_cache", r["agent_total_tokens"] + r.get("agent_cached_tokens", 0))
    lines = [
        f"# Zeno token report — {r['project']}", "",
        f"{r['created_at']} · {r['agent_name']} · {r['agent_model']} · measurement {precision}",
        f"{r['clips_count']} clips · {tc(r['video_seconds'])} final · {r['output_mp4']}", "",
        "| Agent usage | Tokens |", "|---|---:|",
        f"| Uncached input (includes cache writes) | {r['agent_in_tokens']:,} |",
        f"| Cached input | {r.get('agent_cached_tokens', 0):,} |",
        f"| Cache writes, included above | {r.get('agent_cache_write_tokens', 0):,} |",
        f"| Output (includes reasoning) | {r['agent_out_tokens']:,} |",
        f"| **Total including cached input** | **{total:,}** |",
        f"| Uncached input + output | {r['agent_total_tokens']:,} |", "",
        "Counts cover recorded usage during this job window. Provider records may arrive after the report. "
        "Unavailable counters do not establish zero usage.", "",
        f"Transcription newly measured this run: {r.get('transcribe_new_total_tokens', 0):,} tokens.",
        f"Prior transcription reused: {r.get('transcribe_reused_total_tokens', 0):,} tokens.",
    ]
    if not r.get("transcribe_reuse_known"):
        lines.append("Some legacy clips have unknown reuse status; their prior counts cannot be assigned to this run.")
    if r.get("host_usage_unknown") or r.get("direct_usage_unmerged"):
        lines.append("Host/direct accounting is incomplete; measured totals are not a complete job bill.")
    if r.get("transcription_unknown_clips"):
        lines.append(f"Transcription accounting is incomplete for {r['transcription_unknown_clips']} clip(s); missing usage is not zero spending.")
    cost = r.get("cost_estimate") or {}
    if cost.get("available"):
        lines += ["", f"Estimated model cost: USD {cost['usd']:.6f} / INR {cost['inr']:.2f}.",
                  f"Basis: {cost['basis']}. Speed assumption: {cost['speed_assumption']}.",
                  f"Rates verified {cost['rates_as_of']}: {cost['rate_source']}",
                  f"USD/INR {cost['usd_inr']} as of {cost['fx_as_of']}: {cost['fx_source']}",
                  "Set ZENO_USD_INR and ZENO_COST_SPEED for a different exchange rate or speed. "
                  "Transcription, tool fees, tax and post-render chat are excluded."]
        lines += ["", "| Cost component | USD | INR |", "|---|---:|---:|"]
        for label, amount in cost.get("usd_by_type", {}).items():
            lines.append(f"| {label.replace('_', ' ')} | {amount:.6f} | {amount * cost['usd_inr']:.2f} |")
    else:
        lines += ["", "Cost estimate unavailable: " + cost.get("reason", "No verified price.")]
    if r.get("cost_stages"):
        lines += ["", "Stage attribution follows request timestamps; it is not a bill for local Python/FFmpeg work.",
                  "| Stage | Requests | New input | Cached input | Output | Est. INR |",
                  "|---|---:|---:|---:|---:|---:|"]
        for stage in r["cost_stages"]:
            amount = stage["cost"]
            price = f"{amount['inr']:.2f}" if amount.get("available") else "unknown"
            lines.append(f"| {stage['stage']} | {stage['steps_count']} | {stage['input_tokens']:,} | "
                         f"{stage['cached_tokens']:,} | {stage['output_tokens']:,} | {price} |")
    lines += ["", "| Clip | Raw seconds | Kept seconds | Cuts | Transcription model | Input | Output | Reused |",
              "|---|---:|---:|---|---|---:|---:|---|"]
    for c in r.get("clips", []):
        reuse = {True: "yes", False: "no", None: "unknown"}[c.get("transcription_reused")]
        lines.append(f"| {c['clip']} | {c['raw_seconds']:.2f} | {c['kept_seconds']:.2f} | "
                     f"{c['segments']} | {c['transcribe_model']} | {c['input_tokens']:,} | {c['output_tokens']:,} | {reuse} |")
    return "\n".join(lines) + "\n"


def _update_readme(r: Dict[str, Any]) -> None:
    """Replace only this output's row, preserving all other README contents."""
    path = ROOT / "README.md"
    if not path.is_file():
        return
    marker = f"<!-- zeno-output:{r['output_mp4']} -->"
    row = (f"| {r['date']} | **{r['language']} – {r['project']}** ({r['clips_count']} clips): "
           f"{tc(r['video_seconds'])}, {r['marks_count']} marks → \u0060{r['output_mp4']}\u0060 {marker} | "
           f"{r.get('agent_context_start', 0):,} → {r.get('agent_context_end', 0):,} | "
           f"{r['agent_total_tokens']:,} uncached input + output; {r['agent_cached_tokens']:,} cached "
           f"({r.get('agent_token_precision', 'unavailable')}) | unavailable | unavailable |")
    content = path.read_text(encoding="utf-8")
    rows = content.splitlines()
    for i, line in enumerate(rows):
        if marker in line:
            rows[i] = row
            break
    else:
        rows.append(row)
    path.write_text("\n".join(rows) + "\n", encoding="utf-8")


def _update_ledger_md(r: Dict[str, Any]) -> None:
    header = """# Zeno — Automated Token & Clip Telemetry Ledger

Automatically updated by [`scripts/token_tracker.py`](scripts/token_tracker.py) after every video edit (`prepare.py` → `render.py`).
The latest record per output is also stored in local SQLite (`work/zeno_telemetry.db`) and synced to Railway PostgreSQL (`/admin`). Historical rows retain their original inline labels.

## 1. All Video Edit Runs (AI Agent Tokens & Gemini Transcription Registered Separately)

| Date | Project (`Lang`) | Agent | Model | Clips (`Raw → Final`) | Agent Tokens (uncached / cached / output / total) | Transcription Tokens (new / reused; unknown where unavailable) |
|---|---|---|---|---|---|---|
"""
    marker = f"<!-- zeno-output:{r['output_mp4']} -->"
    row = (
        f"| `{r['created_at']}` | **`{r['project']}`** (`{r['language']}`) | **{r['agent_name']}** | `{r['agent_model']}` | "
        f"{r['clips_count']} (`{r['raw_seconds']:.0f}s → {tc(r['video_seconds'])}`) | "
        f"`{r['agent_in_tokens']:,}` uncached in / `{r['agent_cached_tokens']:,}` cached / "
        f"`{r['agent_out_tokens']:,}` out / **`{r.get('agent_total_including_cache', r['agent_total_tokens'] + r['agent_cached_tokens']):,}` total including cache** | "
        f"`{r.get('transcribe_new_total_tokens', 0):,}` new / `{r.get('transcribe_reused_total_tokens', 0):,}` reused; "
        f"{r.get('transcription_unknown_clips', 0)} unknown clips {marker} |"
    )

    existing_rows: List[str] = []
    if LEDGER_MD.is_file():
        for line in LEDGER_MD.read_text(encoding="utf-8", errors="ignore").splitlines():
            if line.startswith("| `20") and marker not in line and not (f"**`{r['project']}`**" in line and f"`{r['created_at']}`" in line):
                existing_rows.append(line)
    existing_rows.append(row)

    clip_section = [
        "",
        f"## 2. Latest Job Clip-by-Clip Breakdown — `{r['project']}` (`{r['created_at']}`)",
        "",
        "| Clip | Raw → Kept | Words | Script Lines | Kept Segments | Raw Clip Process (Retakes / Cuts / Anomalies) | Gemini In | Gemini Out | Gemini Total |",
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


def _version_headers() -> Dict[str, str]:
    import platform as _platform
    try:
        ver = (ROOT / "VERSION").read_text(encoding="utf-8").strip()[:40]
    except Exception:
        ver = ""
    return {"X-Zeno-Version": ver, "X-Zeno-Platform": f"{_platform.system()} {_platform.machine()}"[:80]}


def _sync_to_cloud(r: Dict[str, Any]) -> bool:
    try:
        import auth_session
        sess = auth_session.load_session()
        url = str(sess.get("cloud_url") or auth_session.get_cloud_url()).strip()
        token = str(sess.get("token") or "").strip()
    except Exception:
        url = (os.getenv("ZENO_CLOUD_URL") or os.getenv("SCRIPTCUT_CLOUD_URL") or "").strip()
        token = (os.getenv("ZENO_CLOUD_TOKEN") or os.getenv("SCRIPTCUT_CLOUD_TOKEN") or "").strip()
    if not url or not token:
        return False
    try:
        import httpx
        resp = httpx.post(
            url.rstrip("/") + "/v1/telemetry",
            headers={"Authorization": f"Bearer {token}", **_version_headers()},
            json={**r, "zeno_version": _version_headers()["X-Zeno-Version"]},
            timeout=10.0,
        )
        return resp.status_code == 200
    except Exception:
        return False


_push_to_zeno_cloud = _sync_to_cloud


def print_console_summary(r: Dict[str, Any]) -> None:
    total = r.get("agent_total_including_cache", r["agent_total_tokens"] + r.get("agent_cached_tokens", 0))
    print(f"[TOKENS] {r['agent_name']} / {r['agent_model']} ({r.get('agent_token_precision', 'unavailable')})")
    print(f"[TOKENS] uncached input={r['agent_in_tokens']:,}, cached input={r.get('agent_cached_tokens', 0):,}, "
          f"output={r['agent_out_tokens']:,}, total including cache={total:,}")
    print(f"[TOKENS] transcription new={r.get('transcribe_new_total_tokens', 0):,}, "
          f"prior reused={r.get('transcribe_reused_total_tokens', 0):,}, "
          f"reuse status known={r.get('transcribe_reuse_known', False)}")
    cost = r.get("cost_estimate") or {}
    if cost.get("available"):
        print(f"[COST] API-equivalent {cost['speed_assumption']} estimate USD {cost['usd']:.4f} / INR {cost['inr']:.2f}; "
              f"FX dated {cost['fx_as_of']}, before tax. Subscription billing differs.")
    else:
        print("[COST] unavailable: " + cost.get("reason", "No verified price."))
    print(f"[REPORT] work/{r['project']}/token_report.md")


def main() -> None:
    ap = argparse.ArgumentParser(description="Inspect or finalize Zeno per-clip and per-agent token telemetry.")
    ap.add_argument("--project", help="Project folder name under work/<project>")
    ap.add_argument("--refresh", action="store_true", help="Explicitly recompute and persist a historical report")
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
        snapshot = pdir / "token_telemetry.json"
        if snapshot.is_file() and not args.refresh:
            record = read_json(snapshot)
            if "agent_in_tokens" in record:
                print_console_summary(record)
                return
        if not args.refresh:
            raise SystemExit("No completed report yet. Finish the render, or use --refresh explicitly.")
        plan_path = pdir / "plan.json"
        if not plan_path.is_file():
            raise SystemExit(f"No plan.json found in {pdir}")
        plan = read_json(plan_path)
        segs = plan.get("segments") or []
        vid_sec = sum(max(0.0, float(s.get("end", 0.0)) - float(s.get("start", 0.0))) for s in segs)
        out_mp4 = Path(str(plan.get("output") or (OUTPUT / f"{args.project}.mp4")))
        out_mp4 = out_mp4 if out_mp4.is_absolute() else ROOT / out_mp4
        rec = finalize_job_telemetry(plan_path, plan, vid_sec, out_mp4, sync_cloud=not args.no_cloud)
        print_console_summary(rec)
    else:
        agent = detect_agent_usage()
        print(json.dumps(agent, indent=2))


if __name__ == "__main__":
    main()
