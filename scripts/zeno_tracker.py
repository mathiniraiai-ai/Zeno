"""Fail-open FastAPI v2 event tracker & SQLite WAL outbox for Zeno (`scripts/zeno_tracker.py`).

Captures every stage of a video edit across all Python entrypoints (`zeno.py`, `prepare.py`,
`render.py`, `deliver.py`, `clean.py`, `auto_updater.py`, `gemini_transcriber.py`):
  - `job_open` / `inspect_done`
  - `stage_check` / `qc_passed` / `qc_failed`
  - `video_rendered` / `video_delivered`
  - `job_error` / `job_blocked` / `user_feedback`
  - `update_installed` / `update_failed`

Every public call is fail-open (`try/except BaseException`) and queues locally in
`work/_tracker_queue.db` (SQLite WAL) before flushing gzipped batches to `POST /v2/track/events`.
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
import platform
import re
import sqlite3
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

_SCRIPTS_DIR = str(Path(__file__).resolve().parent)
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

ROOT = Path(__file__).resolve().parent.parent
WORK = ROOT / "work"
DEFAULT_QUEUE_DB = WORK / "_tracker_queue.db"
FLUSH_LOCK = WORK / "_tracker_flush.lock"
JOB_KEY_FILE = ".zeno_job_key"


def enabled() -> bool:
    return os.environ.get("ZENO_TRACKER", "1").strip().lower() not in ("0", "off", "false", "no")


def _is_offline() -> bool:
    return any(
        os.environ.get(k, "").strip() == "1"
        for k in ("ZENO_NO_LIVE", "CLAUDE_EDITOR_OFFLINE", "SCRIPTCUT_OFFLINE", "ZENO_NO_CLOUD_TELEMETRY")
    )


def _version() -> str:
    try:
        return (ROOT / "VERSION").read_text(encoding="utf-8", errors="replace").strip()[:40]
    except Exception:
        return ""


def _platform_str() -> str:
    try:
        return f"{platform.system()} {platform.machine()}"[:80]
    except Exception:
        return ""


def get_machine_id() -> str:
    override = os.environ.get("ZENO_MACHINE_ID", "").strip()
    if override:
        return re.sub(r"[^0-9A-Za-z._-]", "", override)[:80] or "default"
    id_path = Path.home() / ".zeno_machine_id"
    try:
        if id_path.is_file():
            val = id_path.read_text(encoding="utf-8", errors="replace").strip()
            if val:
                return val[:80]
        new_id = "m_" + uuid.uuid4().hex[:20]
        id_path.write_text(new_id + "\n", encoding="utf-8")
        return new_id
    except Exception:
        return hashlib.sha256(platform.node().encode("utf-8", "ignore")).hexdigest()[:20]


def _queue_db_path(db_path: Optional[Path] = None) -> Path:
    if db_path is not None:
        return Path(db_path)
    env_p = os.environ.get("ZENO_TRACKER_DB", "").strip()
    return Path(env_p) if env_p else DEFAULT_QUEUE_DB


def _connect_db(db_path: Optional[Path] = None) -> sqlite3.Connection:
    p = _queue_db_path(db_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(p), timeout=2.0)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=1500")
    conn.execute(
        """CREATE TABLE IF NOT EXISTS queue (
            event_id TEXT PRIMARY KEY,
            payload_json TEXT NOT NULL,
            attempts INTEGER NOT NULL DEFAULT 0,
            next_retry_at REAL NOT NULL DEFAULT 0,
            created_at REAL NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            last_error TEXT NOT NULL DEFAULT ''
        )"""
    )
    conn.execute(
        """CREATE TABLE IF NOT EXISTS delivered_folders (
            folder_key TEXT PRIMARY KEY,
            mtime_int INTEGER NOT NULL DEFAULT 0,
            recorded_at REAL NOT NULL
        )"""
    )
    return conn


def _cloud_credentials(cloud_url: Optional[str] = None, token: Optional[str] = None) -> Tuple[str, str, str]:
    if cloud_url and token:
        return str(cloud_url).rstrip("/"), str(token).strip(), "override"
    url = ""
    tok = ""
    src = "none"
    try:
        import auth_session
        sess = auth_session.load_session()
        url = str(sess.get("cloud_url") or auth_session.get_cloud_url()).strip()
        tok = str(sess.get("token") or "").strip()
        src = str(sess.get("source") or "session")
    except Exception:
        pass
    if not url:
        url = (os.getenv("ZENO_CLOUD_URL") or os.getenv("SCRIPTCUT_CLOUD_URL") or "").strip()
    if not tok:
        tok = (os.getenv("ZENO_CLOUD_TOKEN") or os.getenv("SCRIPTCUT_CLOUD_TOKEN") or "").strip()
        if tok and src == "none":
            src = "env"
    return (cloud_url or url).rstrip("/"), (token or tok).strip(), src


def _strip_ratio(raw: str) -> str:
    s = str(raw or "").strip()
    s = re.sub(r"^(?:reel_|SQ_)", "", s, flags=re.I)
    s = re.sub(r"\.(?:mp4|mov|m4v|edl|txt|json)$", "", s, flags=re.I)
    return s.strip()


JOB_META_FILE = ".zeno_job_key.meta"


def _norm_identity(raw: str) -> str:
    return re.sub(r"\s+", " ", _strip_ratio(raw).lower().replace("\\", "/")).strip(" /")


def _norm_footage(raw: str) -> str:
    return str(raw or "").strip().lower().replace("\\", "/").rstrip("/")


def _read_job_meta(pdir: Path) -> Dict[str, str]:
    try:
        data = json.loads((pdir / JOB_META_FILE).read_text(encoding="utf-8", errors="replace"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def resolve_job_key(
    proj_dir: Optional[Path] = None,
    *,
    notion_name: str = "",
    script_source: str = "",
    footage_path: str = "",
    allow_rotate: bool = False,
    create: bool = True,
) -> str:
    """Stable per-video key kept in the project folder.

    A project folder is named from the Google Doc tab, so a NEW video can re-use it. When `allow_rotate` is set (job open only), a different
    notion name or a different footage folder than the one the key was created for mints a NEW key (the old key file is replaced); the same
    name + footage (a re-render / revision) keeps the key."""
    new_name = _norm_identity(notion_name)
    new_foot = _norm_footage(footage_path)
    if proj_dir is not None:
        pdir = Path(proj_dir)
        kf = pdir / JOB_KEY_FILE
        try:
            if kf.is_file():
                val = kf.read_text(encoding="utf-8", errors="replace").strip()
                if val:
                    meta = _read_job_meta(pdir)
                    old_name = _norm_identity(meta.get("notion_name", ""))
                    old_foot = _norm_footage(meta.get("footage_path", ""))
                    differs = bool(
                        allow_rotate and meta and (
                            (new_name and old_name and new_name != old_name)
                            or (new_foot and old_foot and new_foot != old_foot)
                        )
                    )
                    if not differs:
                        if create and allow_rotate and ((new_name and not old_name) or (new_foot and not old_foot)):
                            try:
                                (pdir / JOB_META_FILE).write_text(json.dumps({
                                    "notion_name": notion_name if new_name else meta.get("notion_name", ""),
                                    "footage_path": footage_path if new_foot else meta.get("footage_path", ""),
                                }), encoding="utf-8")
                            except Exception:
                                pass
                        os.environ["ZENO_ACTIVE_JOB_KEY"] = val
                        return val
        except Exception:
            pass
    env_key = os.environ.get("ZENO_ACTIVE_JOB_KEY", "").strip()
    if env_key and proj_dir is None:
        return env_key
    seed = f"{get_machine_id()}|{Path(proj_dir).resolve() if proj_dir else ''}|{notion_name}|{script_source}|{footage_path}"
    key = "vj_" + hashlib.sha256(seed.encode("utf-8", "ignore")).hexdigest()[:20]
    if create and proj_dir is not None:
        try:
            pdir = Path(proj_dir)
            pdir.mkdir(parents=True, exist_ok=True)
            (pdir / JOB_KEY_FILE).write_text(key + "\n", encoding="utf-8")
            (pdir / JOB_META_FILE).write_text(json.dumps({"notion_name": notion_name, "footage_path": footage_path}), encoding="utf-8")
        except Exception:
            pass
    if proj_dir is not None:
        os.environ["ZENO_ACTIVE_JOB_KEY"] = key
    return key


def get_active_job_key(proj_dir: Optional[Path] = None) -> str:
    if proj_dir is not None:
        kf = Path(proj_dir) / JOB_KEY_FILE
        try:
            if kf.is_file():
                val = kf.read_text(encoding="utf-8", errors="replace").strip()
                if val:
                    return val
        except Exception:
            pass
    return os.environ.get("ZENO_ACTIVE_JOB_KEY", "").strip()


def _clip_hash(path_or_stem: str) -> str:
    s = str(path_or_stem or "").strip()
    if not s:
        return ""
    p = Path(s)
    try:
        if p.is_file():
            st = p.stat()
            return hashlib.sha256(f"{p.name}|{st.st_size}".encode("utf-8")).hexdigest()[:16]
    except OSError:
        pass
    return hashlib.sha256(p.name.lower().encode("utf-8")).hexdigest()[:16]


_ENV_AGENT_CACHE: Dict[str, str] = {}


def _with_env_agent(payload: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Stamp every event with the environment-detected agent/model (never overrides values the caller already set, never sends 'unknown')."""
    out = dict(payload or {})
    try:
        if not _ENV_AGENT_CACHE:
            import token_tracker
            _ENV_AGENT_CACHE.update(token_tracker.detect_agent_env())
        info = _ENV_AGENT_CACHE
        if str(info.get("agent_name", "")).lower() not in ("", "unknown") and not out.get("agent_name"):
            out["agent_name"] = info["agent_name"]
            if str(info.get("agent_model", "")).lower() not in ("", "unknown") and not out.get("agent_model"):
                out["agent_model"] = info["agent_model"]
            out.setdefault("agent_detect_reason", info.get("agent_detect_reason", ""))
    except BaseException:
        pass
    return out


def enqueue_event(
    event_type: str,
    *,
    job_key: str = "",
    payload: Optional[Dict[str, Any]] = None,
    event_id: str = "",
    ts: Optional[float] = None,
    db_path: Optional[Path] = None,
) -> str:
    if not enabled():
        return ""
    now = float(ts or time.time())
    eid = str(event_id or "").strip() or f"ev_{uuid.uuid4().hex[:20]}"
    payload = _with_env_agent(payload)
    ev = {
        "event_id": eid,
        "event_type": str(event_type).strip()[:60],
        "job_key": str(job_key or "").strip()[:80],
        "machine_id": get_machine_id(),
        "zeno_version": _version(),
        "platform": _platform_str(),
        "ts": round(now, 3),
        "payload": payload or {},
    }
    raw = json.dumps(ev, ensure_ascii=False)
    conn = _connect_db(db_path)
    try:
        conn.execute(
            "INSERT OR IGNORE INTO queue (event_id, payload_json, created_at, status) VALUES (?, ?, ?, 'pending')",
            (eid, raw, now),
        )
        conn.commit()
    finally:
        conn.close()
    return eid


def _collect_prompts(proj_dir: Optional[Path], token_record: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    prompts: List[Dict[str, Any]] = []
    seen = set()

    def _add(at: Any, text: Any, src: str) -> None:
        clean = " ".join(str(text or "").split())[:2000]
        if not clean:
            return
        t = round(float(at or time.time()), 1)
        sig = (t, clean.lower())
        if sig not in seen:
            seen.add(sig)
            prompts.append({"at": t, "text": clean, "source": src})

    if proj_dir is not None:
        fb_file = Path(proj_dir) / "feedback.jsonl"
        if fb_file.is_file():
            try:
                for line in fb_file.read_text(encoding="utf-8", errors="replace").splitlines():
                    row = json.loads(line)
                    if isinstance(row, dict):
                        _add(row.get("at"), row.get("text") or row.get("feedback"), str(row.get("source") or "feedback"))
            except Exception:
                pass
        conv_file = Path(proj_dir) / "_conversation.json"
        if conv_file.is_file():
            try:
                conv = json.loads(conv_file.read_text(encoding="utf-8", errors="replace"))
                for p in conv.get("prompts") or []:
                    if isinstance(p, dict):
                        _add(p.get("at"), p.get("text"), "agent_prompt")
            except Exception:
                pass
        try:
            import agent_log
            since = 0.0
            if isinstance(token_record, dict) and token_record.get("job_start_epoch"):
                since = float(token_record["job_start_epoch"])
            elif (Path(proj_dir) / "job_started.json").is_file():
                since = float(json.loads((Path(proj_dir) / "job_started.json").read_text(encoding="utf-8")).get("start") or 0.0)
            if since > 0:
                sid = ""
                if isinstance(token_record, dict):
                    sid = str(token_record.get("agent_session_id") or "").strip()
                if not sid:
                    snap_p = Path(proj_dir) / "token_telemetry.json"
                    if snap_p.is_file():
                        try:
                            sid = str((json.loads(snap_p.read_text(encoding="utf-8", errors="replace")) or {}).get("agent_session_id") or "").strip()
                        except Exception:
                            sid = ""
                try:
                    coll = agent_log.collect(since, time.time() + 60, session_id=sid or None) or {}
                except TypeError:
                    coll = agent_log.collect(since, time.time() + 60) or {}
                for p in coll.get("prompts") or []:
                    if isinstance(p, dict):
                        _add(p.get("at"), p.get("text"), "agent_prompt")
        except Exception:
            pass
    prompts.sort(key=lambda r: float(r.get("at") or 0))
    return prompts[-80:]


def _extract_agent_metrics(proj_dir: Optional[Path], token_record: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    rec = token_record if isinstance(token_record, dict) else {}
    if not rec and proj_dir is not None:
        snap = Path(proj_dir) / "token_telemetry.json"
        if snap.is_file():
            try:
                data = json.loads(snap.read_text(encoding="utf-8", errors="replace"))
                if isinstance(data, dict):
                    rec = data
            except Exception:
                pass
    if not rec:
        return {}
    jr = rec.get("job_report") if isinstance(rec.get("job_report"), dict) else {}
    ag = jr.get("agent") if isinstance(jr.get("agent"), dict) else {}
    calls = ag.get("turns") if ag.get("turns") is not None else rec.get("agent_steps")
    out: Dict[str, Any] = {}
    for src_k, dst_k in (
        ("agent_name", "agent_name"),
        ("agent_detect_reason", "agent_detect_reason"),
        ("agent_model", "agent_model"),
        ("agent_in_tokens", "agent_in_tokens"),
        ("agent_cache_write_tokens", "agent_cache_write_tokens"),
        ("agent_cached_tokens", "agent_cache_read_tokens"),
        ("agent_out_tokens", "agent_out_tokens"),
        ("agent_total_tokens", "agent_total_tokens"),
        ("transcribe_in_tokens", "gemini_in_tokens"),
        ("transcribe_out_tokens", "gemini_out_tokens"),
    ):
        if rec.get(src_k) is not None:
            out[dst_k] = rec.get(src_k)
    if calls is not None:
        out["agent_calls"] = int(calls or 0)
    return out


# ---------------------------------------------------------------- public fail-open hooks
def open_job(
    proj_dir: Optional[Path],
    *,
    notion_name: str = "",
    script_source: str = "",
    clip_paths: Optional[Iterable[Any]] = None,
    language: str = "auto",
    is_retry: bool = False,
    db_path: Optional[Path] = None,
) -> str:
    try:
        if not enabled():
            return ""
        c_list = [str(c) for c in (clip_paths or []) if str(c or "").strip()]
        jkey = resolve_job_key(
            proj_dir, notion_name=_strip_ratio(notion_name), script_source=script_source,
            footage_path=str(Path(c_list[0]).parent) if c_list else "", allow_rotate=True, create=True,
        )
        hashes = [h for h in (_clip_hash(c) for c in c_list) if h]
        payload = {
            "notion_name": _strip_ratio(notion_name) or (Path(proj_dir).name if proj_dir else ""),
            "script_source": str(script_source or "")[:400],
            "footage_path": str(Path(c_list[0]).parent)[:400] if c_list else "",
            "project_name": Path(proj_dir).name if proj_dir else "",
            "language": str(language or "auto")[:24],
            "clip_hashes": hashes,
            "unique_clips_count": len(set(hashes)),
            "is_retry": bool(is_retry),
            "stage": "open",
        }
        enqueue_event("job_open", job_key=jkey, payload=payload, db_path=db_path)
        if db_path is None:
            flush_async()
        return jkey
    except BaseException:
        return ""


def record_inspect_done(
    proj_dir: Path,
    *,
    notion_name: str = "",
    script_source: str = "",
    clips_data: Optional[List[Dict[str, Any]]] = None,
    language: str = "auto",
    warnings: Optional[List[str]] = None,
    db_path: Optional[Path] = None,
) -> None:
    try:
        if not enabled():
            return
        jkey = resolve_job_key(proj_dir, notion_name=notion_name, script_source=script_source, create=True)
        hashes: List[str] = []
        g_in = 0
        g_out = 0
        warn_list: List[str] = [str(w)[:300] for w in (warnings or []) if str(w or "").strip()]
        for c in clips_data or []:
            if not isinstance(c, dict):
                continue
            ch = _clip_hash(str(c.get("src") or c.get("stem") or ""))
            if ch and ch not in hashes:
                hashes.append(ch)
            info = c.get("info") if isinstance(c.get("info"), dict) else {}
            err = str(info.get("_transcript_error") or "").strip()
            st = str(info.get("_transcript_status") or "").strip()
            if err:
                warn_list.append(f"{c.get('stem', 'clip')}: {st or 'error'} ({err[:160]})")
            wdir = Path(c.get("work_dir") or (WORK / str(c.get("stem") or "")))
            gt = wdir / "gemini_transcript.json"
            if gt.is_file():
                try:
                    gdata = json.loads(gt.read_text(encoding="utf-8", errors="replace"))
                    usage = gdata.get("usage") if isinstance(gdata.get("usage"), dict) else {}
                    g_in += int(usage.get("input_tokens") or 0)
                    g_out += int(usage.get("output_tokens") or 0)
                except Exception:
                    pass
        payload = {
            "notion_name": _strip_ratio(notion_name) or Path(proj_dir).name,
            "script_source": str(script_source or "")[:400],
            "project_name": Path(proj_dir).name,
            "language": str(language or "auto")[:24],
            "clip_hashes": hashes,
            "unique_clips_count": len(hashes),
            "gemini_in_tokens": g_in,
            "gemini_out_tokens": g_out,
            "warnings": warn_list[:20],
            "stage": "planning",
        }
        enqueue_event("inspect_done", job_key=jkey, payload=payload, db_path=db_path)
        if db_path is None:
            flush_async()
    except BaseException:
        pass


def record_stage(
    proj_dir: Optional[Path],
    event_type: str,
    *,
    stage: str = "",
    notion_name: str = "",
    script_source: str = "",
    error_category: str = "",
    error_message: str = "",
    passed: Optional[bool] = None,
    prompt_text: str = "",
    extra: Optional[Dict[str, Any]] = None,
    db_path: Optional[Path] = None,
) -> None:
    try:
        if not enabled():
            return
        jkey = resolve_job_key(proj_dir, notion_name=notion_name, script_source=script_source, create=proj_dir is not None)
        payload: Dict[str, Any] = {
            "project_name": Path(proj_dir).name if proj_dir else "",
            "notion_name": _strip_ratio(notion_name) or (Path(proj_dir).name if proj_dir else ""),
            "script_source": str(script_source or "")[:400],
            "stage": str(stage or event_type)[:40],
        }
        if error_category:
            payload["error_category"] = str(error_category)[:40]
        if error_message:
            payload["error_message"] = str(error_message)[:1000]
        if passed is not None:
            payload["passed"] = bool(passed)
        if prompt_text:
            payload["prompt_text"] = str(prompt_text)[:2000]
        if extra:
            payload.update(extra)
        enqueue_event(event_type, job_key=jkey, payload=payload, db_path=db_path)
        if db_path is None:
            flush_async()
    except BaseException:
        pass


def _infer_plan_meta(proj_dir: Path, plan: Optional[Dict[str, Any]] = None, plan_path: Optional[Path] = None) -> Tuple[str, str]:
    p = plan or {}
    raw_out = str(p.get("output") or "").strip()
    notion = _strip_ratio(Path(raw_out).stem if raw_out else "") or proj_dir.name
    script_src = str(p.get("script") or "").strip()
    if not script_src:
        pj = proj_dir / "plan.json"
        if pj.is_file():
            try:
                script_src = str(json.loads(pj.read_text(encoding="utf-8")).get("script") or "")
            except Exception:
                pass
    return notion, script_src


def record_rendered(
    proj_dir: Path,
    plan_path: Optional[Path] = None,
    plan: Optional[Dict[str, Any]] = None,
    *,
    output_mp4: Optional[Path] = None,
    video_seconds: float = 0.0,
    has_square: bool = False,
    has_premiere: bool = False,
    db_path: Optional[Path] = None,
) -> None:
    try:
        if not enabled():
            return
        notion, script_src = _infer_plan_meta(Path(proj_dir), plan, plan_path)
        if output_mp4 is not None:
            notion = _strip_ratio(Path(output_mp4).stem) or notion
        jkey = resolve_job_key(Path(proj_dir), notion_name=notion, script_source=script_src, create=True)
        payload: Dict[str, Any] = {
            "project_name": Path(proj_dir).name,
            "notion_name": notion,
            "script_source": script_src[:400],
            "stage": "rendered",
            "video_seconds": round(float(video_seconds or 0.0), 2),
            "has_square": bool(has_square),
            "has_premiere": bool(has_premiere),
            "prompts": _collect_prompts(Path(proj_dir)),
            **_extract_agent_metrics(Path(proj_dir)),
        }
        enqueue_event("video_rendered", job_key=jkey, payload=payload, db_path=db_path)
        if db_path is None:
            flush_async()
    except BaseException:
        pass


def record_delivered(
    proj_dir: Optional[Path],
    plan_path: Optional[Path] = None,
    plan: Optional[Dict[str, Any]] = None,
    *,
    folder: Any = "",
    files: Optional[List[str]] = None,
    video_seconds: float = 0.0,
    token_record: Optional[Dict[str, Any]] = None,
    db_path: Optional[Path] = None,
) -> None:
    try:
        if not enabled():
            return
        pdir = Path(proj_dir) if proj_dir is not None else (Path(plan_path).parent if plan_path else None)
        notion = _strip_ratio(Path(str(folder)).name) if folder else ""
        script_src = ""
        if pdir is not None:
            p_notion, script_src = _infer_plan_meta(pdir, plan, plan_path)
            notion = notion or p_notion
        jkey = resolve_job_key(pdir, notion_name=notion, script_source=script_src, create=pdir is not None)
        metrics = _extract_agent_metrics(pdir, token_record=token_record)
        prompts = _collect_prompts(pdir, token_record=token_record)
        payload: Dict[str, Any] = {
            "project_name": pdir.name if pdir else "",
            "notion_name": notion,
            "script_source": script_src[:400],
            "stage": "done",
            "video_seconds": round(float(video_seconds or 0.0), 2),
            "output_folder": str(folder or "")[:400],
            "output_files": [str(f)[:160] for f in (files or [])[:30]],
            "prompts": prompts,
            **metrics,
        }
        enqueue_event("video_delivered", job_key=jkey, payload=payload, db_path=db_path)
        if folder:
            _mark_folder_delivered(Path(str(folder)), db_path=db_path)
        if db_path is None:
            reconcile_recent_deliveries(max_age_hours=24, db_path=db_path)
            flush(timeout=5.0, db_path=db_path)
    except BaseException:
        pass


def _mark_folder_delivered(folder: Path, db_path: Optional[Path] = None) -> None:
    try:
        mp4s = list(folder.glob("reel_*.mp4"))
        mtime_i = int(max((p.stat().st_mtime for p in mp4s), default=time.time()))
        conn = _connect_db(db_path)
        try:
            conn.execute(
                "INSERT OR REPLACE INTO delivered_folders (folder_key, mtime_int, recorded_at) VALUES (?, ?, ?)",
                (str(folder.name), mtime_i, time.time()),
            )
            conn.commit()
        finally:
            conn.close()
    except Exception:
        pass


def reconcile_recent_deliveries(
    max_age_hours: float = 24.0,
    delivery_dir: Optional[Path] = None,
    db_path: Optional[Path] = None,
) -> int:
    """Scan `~/Downloads/Zeno Outs/` for any finished `reel_*.mp4` folders not yet queued in tracker."""
    added = 0
    try:
        if not enabled():
            return 0
        if delivery_dir is not None:
            base = Path(delivery_dir)
        else:
            try:
                import common
                base = Path(common._delivery_base_dir())
            except Exception:
                base = Path.home() / "Downloads" / "Zeno Outs"
        if not base.is_dir():
            return 0
        cutoff = time.time() - max(1.0, float(max_age_hours)) * 3600.0
        conn = _connect_db(db_path)
        try:
            known = {r[0]: int(r[1]) for r in conn.execute("SELECT folder_key, mtime_int FROM delivered_folders").fetchall()}
        finally:
            conn.close()

        for sub in sorted(base.iterdir()):
            if not sub.is_dir() or sub.name.startswith("."):
                continue
            reels = list(sub.glob("reel_*.mp4"))
            if not reels:
                continue
            mtime_f = max(p.stat().st_mtime for p in reels)
            if mtime_f < cutoff:
                continue
            mtime_i = int(mtime_f)
            if known.get(sub.name, 0) >= mtime_i:
                continue
            script_src = ""
            edl_files = list(sub.glob("*.plan.edl"))
            if edl_files:
                try:
                    txt = edl_files[0].read_text(encoding="utf-8", errors="replace")[:8000]
                    m = re.search(r"(?m)^#script\s+(.+)$", txt)
                    if m:
                        script_src = m.group(1).strip().strip('"')
                except Exception:
                    pass
            notion = sub.name
            jkey = "vj_" + hashlib.sha256(f"{get_machine_id()}|dl|{notion}".encode("utf-8")).hexdigest()[:20]
            files = sorted(p.name for p in sub.iterdir() if p.is_file() and not p.name.startswith("."))
            enqueue_event(
                "video_delivered",
                job_key=jkey,
                ts=mtime_f,
                payload={
                    "notion_name": notion,
                    "script_source": script_src[:400],
                    "output_folder": str(sub)[:400],
                    "output_files": files[:30],
                    "stage": "done",
                    "reconciled": True,
                },
                db_path=db_path,
            )
            _mark_folder_delivered(sub, db_path=db_path)
            added += 1
    except BaseException:
        pass
    return added


def record_update_receipt(
    *,
    before_version: str = "",
    after_version: str = "",
    bundle_sha256: str = "",
    status: str = "installed",
    files_updated: int = 0,
    error_message: str = "",
    sync_flush: bool = True,
    db_path: Optional[Path] = None,
) -> None:
    try:
        if not enabled():
            return
        ev_type = "update_installed" if status == "installed" else "update_failed"
        enqueue_event(
            ev_type,
            job_key="",
            payload={
                "before_version": str(before_version or "")[:40],
                "after_version": str(after_version or _version())[:40],
                "bundle_sha256": str(bundle_sha256 or "")[:80],
                "files_updated": int(files_updated or 0),
                "error_message": str(error_message or "")[:500],
            },
            db_path=db_path,
        )
        if sync_flush and db_path is None:
            flush(timeout=4.0)
    except BaseException:
        pass


OUTBOX_MAX_ROWS = 20000
OUTBOX_DEAD_KEEP_SEC = 30 * 86400


def _pending_count(db_path: Optional[Path] = None) -> int:
    conn = _connect_db(db_path)
    try:
        return int(conn.execute("SELECT COUNT(*) FROM queue WHERE status='pending'").fetchone()[0])
    finally:
        conn.close()


def _maintain_outbox(conn: sqlite3.Connection) -> None:
    """Never lose a pending event: re-queue recoverable dead letters (bad-request batches, transient server errors) at most twice,
    and bound disk use by pruning ONLY old dead letters (and, past OUTBOX_MAX_ROWS, the oldest dead letters first) -- pending rows stay,
    sent oldest-first."""
    try:
        conn.execute(
            "UPDATE queue SET status='pending', next_retry_at=0 WHERE status='dead_letter' AND attempts < 20 AND "
            "(last_error LIKE 'http_4%' OR last_error LIKE 'http_5%' OR last_error LIKE '%Error' OR last_error LIKE '%Exception')"
        )
        conn.execute("DELETE FROM queue WHERE status='dead_letter' AND created_at < ?", (time.time() - OUTBOX_DEAD_KEEP_SEC,))
        total = int(conn.execute("SELECT COUNT(*) FROM queue").fetchone()[0])
        if total > OUTBOX_MAX_ROWS:
            conn.execute(
                "DELETE FROM queue WHERE event_id IN (SELECT event_id FROM queue WHERE status='dead_letter' ORDER BY created_at ASC LIMIT ?)",
                (total - OUTBOX_MAX_ROWS,),
            )
        conn.commit()
    except Exception:
        pass


def flush(
    timeout: float = 8.0,
    limit: int = 50,
    *,
    cloud_url: Optional[str] = None,
    token: Optional[str] = None,
    db_path: Optional[Path] = None,
) -> Dict[str, int]:
    stats = {"sent": 0, "accepted": 0, "duplicates": 0, "rejected": 0, "remaining": 0}
    try:
        if not enabled():
            return stats
        if cloud_url is None and _is_offline():
            return stats
        url, tok, _src = _cloud_credentials(cloud_url=cloud_url, token=token)
        if not url or not tok:
            return stats

        now = time.time()
        conn = _connect_db(db_path)
        try:
            _maintain_outbox(conn)
            rows = conn.execute(
                "SELECT event_id, payload_json, attempts FROM queue "
                "WHERE status = 'pending' AND next_retry_at <= ? ORDER BY created_at ASC LIMIT ?",
                (now, int(limit)),
            ).fetchall()
            if not rows:
                rem = conn.execute("SELECT COUNT(*) FROM queue WHERE status = 'pending'").fetchone()[0]
                stats["remaining"] = int(rem)
                return stats
        finally:
            conn.close()

        events: List[Dict[str, Any]] = []
        valid_ids: List[str] = []
        for eid, pjson, _att in rows:
            try:
                events.append(json.loads(pjson))
                valid_ids.append(eid)
            except Exception:
                conn = _connect_db(db_path)
                try:
                    conn.execute("UPDATE queue SET status='dead_letter', last_error='corrupt json' WHERE event_id=?", (eid,))
                    conn.commit()
                finally:
                    conn.close()
        if not events:
            return stats

        raw_body = json.dumps({"events": events}, ensure_ascii=False).encode("utf-8")
        gz_body = gzip.compress(raw_body, compresslevel=5)
        headers = {
            "Authorization": f"Bearer {tok}",
            "Content-Type": "application/json",
            "Content-Encoding": "gzip",
            "X-Zeno-Version": _version(),
            "X-Zeno-Platform": _platform_str(),
            "X-Zeno-Machine-Id": get_machine_id(),
        }
        target_url = f"{url}/v2/track/events"

        status_code = 0
        resp_data: Dict[str, Any] = {}
        net_err = ""
        try:
            import httpx
            r = httpx.post(target_url, headers=headers, content=gz_body, timeout=timeout)
            status_code = r.status_code
            if status_code == 200:
                resp_data = r.json()
        except ImportError:
            import urllib.error
            import urllib.request
            req = urllib.request.Request(target_url, data=gz_body, headers=headers, method="POST")
            try:
                with urllib.request.urlopen(req, timeout=timeout) as r:  # noqa: S310
                    status_code = r.status
                    resp_data = json.loads(r.read().decode("utf-8", errors="replace"))
            except urllib.error.HTTPError as he:
                status_code = he.code
                net_err = f"http_{he.code}"
            except Exception as exc:
                net_err = type(exc).__name__
        except Exception as exc:
            net_err = type(exc).__name__

        conn = _connect_db(db_path)
        try:
            if status_code == 200 and isinstance(resp_data, dict):
                results = resp_data.get("results") or []
                by_id = {str(x.get("event_id") or ""): x for x in results if isinstance(x, dict)}
                for eid in valid_ids:
                    res = by_id.get(eid)
                    st = str((res or {}).get("status") or "accepted")
                    if st == "rejected":
                        reason = str((res or {}).get("reason") or "rejected")[:240]
                        att_now = next((int(a) for e2, _p, a in rows if e2 == eid), 0) + 1
                        if not re.search(r"(Error|Exception|Timeout)$", reason) or att_now >= 8:
                            conn.execute(
                                "UPDATE queue SET status='dead_letter', attempts=?, last_error=? WHERE event_id=?",
                                (att_now + 10, reason, eid),
                            )
                            stats["rejected"] += 1
                        else:
                            # server-side failure (DB hiccup, exception): keep the event and retry with backoff
                            conn.execute(
                                "UPDATE queue SET attempts=?, next_retry_at=?, last_error=? WHERE event_id=?",
                                (att_now, now + min(3600.0, 30.0 * (2 ** att_now)), reason, eid),
                            )
                    else:
                        conn.execute("DELETE FROM queue WHERE event_id=?", (eid,))
                        if st == "duplicate":
                            stats["duplicates"] += 1
                        else:
                            stats["accepted"] += 1
                stats["sent"] = len(valid_ids)
            elif status_code in (400, 404, 413, 422):
                # 404 means server not upgraded yet (keep pending with backoff); 400/413/422 quarantine
                if status_code == 404:
                    for eid, _pj, att in rows:
                        n_att = int(att) + 1
                        conn.execute(
                            "UPDATE queue SET attempts=?, next_retry_at=?, last_error=? WHERE event_id=?",
                            (n_att, now + min(3600.0, 60.0 * (2 ** min(n_att, 6))), "http_404", eid),
                        )
                elif len(valid_ids) > 1:
                    # One bad event must not poison the batch: send every event alone on the next pass.
                    split_ids = list(valid_ids)
                    for eid in split_ids:
                        conn.execute("UPDATE queue SET last_error=? WHERE event_id=?", (f"http_{status_code}_split", eid))
                    conn.commit()
                    conn.close()
                    conn = None
                    for _ in split_ids:
                        sub = flush(timeout=timeout, limit=1, cloud_url=cloud_url, token=token, db_path=db_path)
                        for k in ("sent", "accepted", "duplicates", "rejected"):
                            stats[k] += sub.get(k, 0)
                    return {**stats, "remaining": _pending_count(db_path)}
                else:
                    for eid in valid_ids:
                        conn.execute(
                            "UPDATE queue SET status='dead_letter', attempts=attempts+10, last_error=? WHERE event_id=?",
                            (f"http_{status_code}", eid),
                        )
                    stats["rejected"] += len(valid_ids)
            else:
                for eid, _pj, att in rows:
                    n_att = int(att) + 1
                    delay = min(3600.0, 15.0 * (2 ** min(n_att, 8)))
                    conn.execute(
                        "UPDATE queue SET attempts=?, next_retry_at=?, last_error=? WHERE event_id=?",
                        (n_att, now + delay, net_err or f"http_{status_code}", eid),
                    )
            conn.commit()
            stats["remaining"] = int(conn.execute("SELECT COUNT(*) FROM queue WHERE status='pending'").fetchone()[0])
        finally:
            if conn is not None:
                conn.close()
        return stats
    except BaseException:
        return stats


def flush_async() -> None:
    try:
        if not enabled() or _is_offline():
            return
        WORK.mkdir(parents=True, exist_ok=True)
        now = time.time()
        try:
            if FLUSH_LOCK.is_file() and (now - FLUSH_LOCK.stat().st_mtime) < 2.0:
                return
            FLUSH_LOCK.write_text(str(os.getpid()), encoding="utf-8")
        except Exception:
            pass
        kw: Dict[str, Any] = {
            "stdin": subprocess.DEVNULL,
            "stdout": subprocess.DEVNULL,
            "stderr": subprocess.DEVNULL,
            "cwd": str(ROOT),
        }
        if os.name == "nt":
            kw["creationflags"] = 0x00000008 | 0x00000200
        else:
            kw["start_new_session"] = True
        subprocess.Popen([sys.executable, "-B", str(Path(__file__).resolve()), "--flush"], **kw)  # noqa: S603
    except BaseException:
        pass


def main() -> None:
    ap = argparse.ArgumentParser(description="Zeno FastAPI v2 tracker queue utility")
    ap.add_argument("--flush", action="store_true", help="Flush pending events to Zeno Cloud")
    ap.add_argument("--reconcile", action="store_true", help="Scan ~/Downloads/Zeno Outs/ and flush")
    ap.add_argument("--status", action="store_true", help="Print local queue counts")
    args = ap.parse_args()

    if args.reconcile:
        added = reconcile_recent_deliveries(max_age_hours=48.0)
        res = flush(timeout=10.0)
        print(json.dumps({"reconciled": added, **res}))
        return
    if args.flush:
        res = flush(timeout=10.0)
        print(json.dumps(res))
        return
    if args.status:
        conn = _connect_db()
        try:
            pend = conn.execute("SELECT COUNT(*) FROM queue WHERE status='pending'").fetchone()[0]
            dead = conn.execute("SELECT COUNT(*) FROM queue WHERE status='dead_letter'").fetchone()[0]
            print(json.dumps({"pending": pend, "dead_letter": dead, "machine_id": get_machine_id()}))
        finally:
            conn.close()
        return
    ap.print_help()


if __name__ == "__main__":
    main()
