"""The job archive: every edit stored end to end on the Zeno server, so the team can later study where the editing fell short.

One record per job, text only (never footage or video):
  the script, the script analysis, every version of the plan (from the first one the Director wrote to the one that was rendered),
  what each plan check said, the decisions file, the verification results, the timeline, how the split panels were framed,
  the token numbers, the editor's feedback, and how many times the job was rendered;
  the conversation with the AI agent (the editor's prompts word for word, every tool the agent ran, every error on the way:
  see agent_log.py), the stage events (inspect, each check, render, blocked), and the computer it ran on.

Records go up at every stage, not only after a render: when inspect finishes, after each plan check (in the background,
so the check is not slowed), at the render, when it fails, and when a job is blocked before it starts. An edit that is
abandoned half way is therefore on the server too.

`snapshot_plan` keeps the plan's history as the job goes; `add_feedback` records what the editor said; `archive_job` builds the
record and sends it. A record that cannot be sent waits in `work/_job_outbox/` and goes with the next job. Nothing here can stop
or slow an edit: every failure is swallowed. `ZENO_ARCHIVE=0` turns it off. Standard library only (httpx if present).
"""
from __future__ import annotations

import hashlib
import json
import os
import platform
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

_SCRIPTS_DIR = str(Path(__file__).resolve().parent)
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

ROOT = Path(__file__).resolve().parent.parent
WORK = ROOT / "work"
OUTBOX = WORK / "_job_outbox"
HISTORY = "_history"
FEEDBACK = "feedback.jsonl"
EVENTS = "stage_events.jsonl"     # (events.jsonl is the live progress feed of events.py: also sent, as live_events)
LIVE_EVENTS = "events.jsonl"
STARTED = "job_started.json"
MAX_TEXT = 400_000          # one text file inside a record (characters)
MAX_VERSIONS = 40           # plan versions kept per job
MAX_RECORD = 3_500_000      # bytes; the server refuses more than 4 MB


def enabled() -> bool:
    return os.environ.get("ZENO_ARCHIVE", "1").strip().lower() not in ("0", "off", "false", "no")


def _read(path: Path, limit: int = MAX_TEXT) -> str:
    try:
        text = Path(path).read_text(encoding="utf-8", errors="replace")
    except Exception:
        return ""
    if len(text) > limit:
        return text[:limit] + f"\n… [cut: {len(text) - limit} more characters]"
    return text


def _version() -> str:
    return _read(ROOT / "VERSION", 40).strip()


def job_uid(proj_dir: Path, plan: Optional[Dict[str, Any]] = None) -> str:
    """One id per job on this computer: the project folder plus the output name. A re-render updates the same record."""
    out = str((plan or {}).get("output") or "")
    seed = f"{platform.node()}|{Path(proj_dir).resolve()}|{out}"
    return "job_" + hashlib.sha256(seed.encode("utf-8")).hexdigest()[:24]


# ---------------------------------------------------------------- the plan's history
def snapshot_plan(plan_path: Path, stage: str, note: str = "") -> None:
    """Keep this version of the plan if it differs from the last one kept. `stage` says when: check, pitch, render."""
    if not enabled():
        return
    try:
        plan_path = Path(plan_path)
        text = plan_path.read_text(encoding="utf-8", errors="replace")
        hist = plan_path.parent / HISTORY
        hist.mkdir(parents=True, exist_ok=True)
        index_file = hist / "index.json"
        try:
            index: List[Dict[str, Any]] = json.loads(index_file.read_text(encoding="utf-8"))
        except Exception:
            index = []
        digest = hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]
        entry = {"at": round(time.time(), 1), "stage": stage, "sha": digest, "note": str(note)[:2000]}
        if index and index[-1].get("sha") == digest:
            index.append({**entry, "file": index[-1].get("file"), "same_as_previous": True})
        else:
            name = f"plan_{len([e for e in index if not e.get('same_as_previous')]) + 1:02d}.edl"
            (hist / name).write_text(text, encoding="utf-8")
            index.append({**entry, "file": name})
        index_file.write_text(json.dumps(index[-200:], ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass


def add_feedback(proj_dir: Path, text: str, source: str = "editor") -> bool:
    """Record what the editor (or the owner) said about this edit: what was wrong, what they changed, what they liked."""
    try:
        text = " ".join(str(text).split())
        if not text:
            return False
        proj_dir = Path(proj_dir)
        proj_dir.mkdir(parents=True, exist_ok=True)
        with open(proj_dir / FEEDBACK, "a", encoding="utf-8") as fh:
            fh.write(json.dumps({"at": round(time.time(), 1), "source": source, "text": text[:4000]}, ensure_ascii=False) + "\n")
        return True
    except Exception:
        return False


def _feedback(proj_dir: Path) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    try:
        for line in (Path(proj_dir) / FEEDBACK).read_text(encoding="utf-8").splitlines():
            try:
                row = json.loads(line)
                if isinstance(row, dict):
                    if not row.get("text") and row.get("feedback"):
                        row["text"] = str(row["feedback"])[:4000]
                    out.append(row)
            except Exception:
                continue
    except Exception:
        pass
    return out[-100:]


def mark_started(proj_dir: Path) -> None:
    """Remember when this edit began (the first inspect), so the record can find the agent conversation of the whole job."""
    try:
        f = Path(proj_dir) / STARTED
        if f.is_file():
            old = json.loads(f.read_text(encoding="utf-8")).get("start", 0)
            if time.time() - float(old) < 12 * 3600:
                return
        Path(proj_dir).mkdir(parents=True, exist_ok=True)
        try:
            from common import job_start_epoch
            start = job_start_epoch(create=False)
        except Exception:
            start = time.time()
        f.write_text(json.dumps({"start": round(min(start, time.time()), 1)}), encoding="utf-8")
    except Exception:
        pass


def event(proj_dir: Path, kind: str, **data: Any) -> None:
    """One line in the job's stage log: inspect, check, render, blocked, failed (with seconds taken and short results)."""
    try:
        Path(proj_dir).mkdir(parents=True, exist_ok=True)
        row = {"at": round(time.time(), 1), "kind": kind, "version": _version()}
        row.update({k: (v if isinstance(v, (int, float, bool)) or v is None else str(v)[:2000]) for k, v in data.items()})
        with open(Path(proj_dir) / EVENTS, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    except Exception:
        pass


def _events(proj_dir: Path, name: str = EVENTS, last: int = 400) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    try:
        for line in (Path(proj_dir) / name).read_text(encoding="utf-8").splitlines()[-last:]:
            try:
                out.append(json.loads(line))
            except Exception:
                continue
    except Exception:
        pass
    return out


def _job_start(proj: Path, tokens: Any, index: List[Dict[str, Any]]) -> float:
    """When this job began: the first inspect, else the token report's start, else the open job marker."""
    cands: List[Any] = []
    try:
        cands.append(json.loads((proj / STARTED).read_text(encoding="utf-8")).get("start"))
    except Exception:
        pass
    if isinstance(tokens, dict):
        cands.append(tokens.get("job_start_epoch"))
    for cand in cands:
        try:
            if cand and float(cand) > 0:
                return float(cand)
        except Exception:
            continue
    try:
        from common import job_start_epoch
        return job_start_epoch(create=False)
    except Exception:
        pass
    return (float(index[0]["at"]) - 3 * 3600) if index else time.time() - 3 * 3600


CONVERSATION = "_conversation.json"
FIRST_WINDOW = 8 * 3600     # the first look back never reaches further than this (an old project reopened days later)


def conversation(proj: Path, job_start: float) -> Dict[str, Any]:
    """The editor-agent conversation of this job so far. Kept in work/<project>/_conversation.json and extended each time
    with only what is new, so a long or reopened job keeps its whole history and never picks up other jobs' sessions."""
    try:
        import agent_log
        cache_file = Path(proj) / CONVERSATION
        try:
            cache = json.loads(cache_file.read_text(encoding="utf-8"))
        except Exception:
            cache = {}
        now = time.time()
        since = float(cache.get("last_at") or 0) - 120 if cache.get("last_at") else max(float(job_start or 0), now - FIRST_WINDOW)
        new = agent_log.collect(since, now + 60) or {}
        if not cache and not new:
            return {}

        def merged(key: str) -> List[Dict[str, Any]]:
            seen, out = set(), []
            for row in list(cache.get(key) or []) + list(new.get(key) or []):
                sig = (row.get("at"), row.get("kind"), row.get("tool"), row.get("text") or row.get("input"))
                if sig not in seen:
                    seen.add(sig)
                    out.append(row)
            return sorted(out, key=lambda r: float(r.get("at") or 0))

        prompts, steps = merged("prompts"), merged("steps")
        models = dict(cache.get("models") or {})
        for m, n in (new.get("models") or {}).items():
            models[m] = max(int(models.get(m, 0)), int(n))
        sessions = list(dict.fromkeys(list(cache.get("sessions") or []) + list(new.get("sessions") or [])))[-10:]
        last_at = max([float(r.get("at") or 0) for r in steps] + [float(cache.get("last_at") or 0)])
        full = {"agent": new.get("agent") or cache.get("agent") or "", "models": models, "sessions": sessions,
                "prompts": prompts, "steps": steps, "last_at": last_at}
        cache_file.write_text(json.dumps(full, ensure_ascii=False), encoding="utf-8")
        truncated = len(prompts) > agent_log.MAX_PROMPTS or len(steps) > agent_log.MAX_STEPS
        keep_p = prompts[:5] + prompts[5:][-(agent_log.MAX_PROMPTS - 5):] if len(prompts) > agent_log.MAX_PROMPTS else prompts
        keep_s = steps[:50] + steps[50:][-(agent_log.MAX_STEPS - 50):] if len(steps) > agent_log.MAX_STEPS else steps
        return {"agent": full["agent"], "models": models, "sessions": sessions,
                "window": [round(float(prompts[0]["at"] if prompts else since), 1), round(last_at, 1)],
                "counts": agent_log._counts(prompts, steps), "prompts": keep_p, "steps": keep_s, "truncated": truncated}
    except Exception:
        return {}


def environment() -> Dict[str, Any]:
    """The computer the job ran on (no names, paths or secrets)."""
    env = {"os": f"{platform.system()} {platform.release()}", "machine": platform.machine(),
           "python": platform.python_version(), "cpus": os.cpu_count() or 0}
    for k in ("ZENO_DEVICE", "ZENO_ENCODER", "ZENO_MAX_THREADS", "ZENO_COOL"):
        if os.environ.get(k):
            env[k.lower()] = os.environ[k][:40]
    return env


# ---------------------------------------------------------------- the record
def build_record(
    plan_path: Path,
    plan: Optional[Dict[str, Any]] = None,
    video_seconds: float = 0.0,
    output_mp4: Optional[Path] = None,
    kind: str = "render",
    extra: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    plan_path = Path(plan_path)
    proj = plan_path.parent
    if plan is None and plan_path.is_file():
        try:
            from edl_parser import load_edl
            plan = load_edl(plan_path)
        except Exception:
            plan = {}
    plan = plan or {}
    hist = proj / HISTORY
    try:
        index = json.loads((hist / "index.json").read_text(encoding="utf-8"))
    except Exception:
        index = []
    files = []
    for e in index:
        f = e.get("file")
        if f and not e.get("same_as_previous") and f not in [x["file"] for x in files]:
            files.append({"file": f, "stage": e.get("stage"), "at": e.get("at"), "text": _read(hist / f, 120_000)})
    files = files[:2] + files[2:][-(MAX_VERSIONS - 2):] if len(files) > MAX_VERSIONS else files   # always keep the first plans

    def _json(name: str) -> Any:
        try:
            return json.loads((proj / name).read_text(encoding="utf-8"))
        except Exception:
            return None

    segs = [s for s in (plan.get("segments") or []) if isinstance(s, dict)]
    preset = str(plan.get("preset") or "").strip()
    default_layout = preset if preset and preset not in ("auto", "hybrid") else "single"
    layouts: Dict[str, int] = {}
    for s in segs:
        lay = str(s.get("layout") or default_layout).split(":")[0]
        layouts[lay] = layouts.get(lay, 0) + 1
    script_raw = plan.get("script")
    if not script_raw:
        side = _json("plan.json")
        if isinstance(side, dict):
            script_raw = side.get("script")
    script_file = None
    try:
        from common import resolve
        script_file = resolve(script_raw) if script_raw else None
    except Exception:
        script_file = None

    out_name = Path(str(plan.get("output") or output_mp4 or proj.name)).stem
    timeline = ""
    last_render = proj / "_archive_render.json"   # what only the render knows (timeline, length), reused by later sends
    if output_mp4 is not None:
        timeline = _read(Path(output_mp4).with_suffix(".timeline.txt"), 120_000)
        try:
            last_render.write_text(json.dumps({"timeline": timeline, "video_seconds": float(video_seconds or 0.0),
                                               "output": str(output_mp4)}, ensure_ascii=False), encoding="utf-8")
        except Exception:
            pass
    else:
        try:
            kept = json.loads(last_render.read_text(encoding="utf-8"))
            timeline = str(kept.get("timeline") or "")
            video_seconds = video_seconds or float(kept.get("video_seconds") or 0.0)
        except Exception:
            pass
    record: Dict[str, Any] = {
        "job_uid": job_uid(proj, plan),
        "kind": kind,
        "project": proj.name,
        "video_name": out_name,
        "zeno_version": _version(),
        "platform": f"{platform.system()} {platform.machine()}",
        "sent_at": round(time.time(), 1),
        "summary": {
            "video_seconds": round(float(video_seconds or 0.0), 2),
            "segments": len(segs),
            "layouts": layouts,
            "style": plan.get("style"),
            "subs": plan.get("subs"),
            "page": plan.get("page"),
            "marks": len(plan.get("marks") or []),
            "sfx": len(plan.get("sfx") or []),
            "renders": len([e for e in index if e.get("stage") == "render"]),
            "plan_checks": len([e for e in index if e.get("stage") == "check"]),
            "plan_versions": len(files),
        },
        "script": _read(script_file, 200_000) if script_file and Path(script_file).is_file() else "",
        "script_analysis": _read(proj / "script_analysis.md", 120_000),
        "decisions": _read(proj / "decisions.txt", 60_000),
        "line_map": _read(proj / "line_map.md", 250_000),
        "facts": _read(proj / "facts.md", 250_000),
        "plan_history": [{k: v for k, v in e.items() if k != "text"} for e in index][-200:],
        "plan_versions": files,
        "plan_final": _read(plan_path, 150_000),
        "job_report": _json("job_report.json"),
        "progress": _json("progress.json"),
        "verify": _json("verify_report.json"),
        "render_notes": _json("render_notes.json"),
        "page_meta": _json("_page/page_meta.json"),
        "timeline": timeline,
        "tokens": _json("token_telemetry.json"),
        "editing_packet_manifest": _json("editing_packet.json"),
        "feedback": _feedback(proj),
        "events": _events(proj),
        "live_events": _events(proj, LIVE_EVENTS, 300),
        "environment": environment(),
    }
    record["conversation"] = conversation(proj, _job_start(proj, record["tokens"], index))
    if extra:
        record.update(extra)
    # keep the record under the server's limit: the big measurement files go first
    for key in ("facts", "line_map"):
        if len(json.dumps(record, ensure_ascii=False).encode("utf-8")) <= MAX_RECORD:
            break
        record[key] = record[key][:40_000] + "\n… [cut to fit]"
    conv = record.get("conversation") or {}
    while conv.get("steps") and len(json.dumps(record, ensure_ascii=False).encode("utf-8")) > MAX_RECORD:
        conv["steps"] = conv["steps"][: len(conv["steps"]) // 2]   # the agent's step list is the last thing to shrink
        conv["truncated"] = True
    return record


# ---------------------------------------------------------------- sending
def publish_progress(proj: Path, snapshot: Dict[str, Any]) -> bool:
    """A small heartbeat, separate from scripts/plans and from paid model requests."""
    if not enabled():
        return False
    cloud = _cloud()
    if cloud is None:
        return False
    # Never send owner addresses, file paths, script text or raw checker diagnostics to the viewer feed.
    safe = {k: snapshot.get(k) for k in (
        "project", "video_name", "stage", "total_stages", "stage_name", "completed", "total", "pct",
        "elapsed_sec", "state", "started_at", "stage_started_at", "last_progress_at", "updated_at",
        "stale_after_sec", "delay_after_sec",
    )}
    safe["detail"] = {
        "blocked": "Editing stopped: review the flagged voices or source footage with the editor.",
        "failed": "This stage failed. Review the saved check results.",
        "error": "This stage failed. Review the saved check results.",
        "measured": "Footage measured; waiting for the editing plan.",
        "plan_ready": "Plan checked; ready to render.",
        "completed": "Editing and quality checks completed.",
    }.get(str(snapshot.get("state")), str(snapshot.get("stage_name") or "Working"))
    body = json.dumps({"progress_uid": job_uid(proj), "progress": safe}).encode("utf-8")
    try:
        import urllib.request
        request = urllib.request.Request(cloud["url"] + "/v1/jobs/progress", data=body, headers={
            "Authorization": f"Bearer {cloud['token']}", "Content-Type": "application/json",
            "X-Zeno-Version": _version(),
        }, method="POST")
        with urllib.request.urlopen(request, timeout=3.0) as response:
            return response.status == 200
    except Exception:
        return False


def _cloud() -> Optional[Dict[str, str]]:
    url = ""
    token = ""
    try:
        import auth_session
        sess = auth_session.load_session()
        url = str(sess.get("cloud_url") or auth_session.get_cloud_url()).strip()
        token = str(sess.get("token") or "").strip()
    except Exception:
        pass
    if not url:
        url = (os.getenv("ZENO_CLOUD_URL") or os.getenv("SCRIPTCUT_CLOUD_URL") or "").strip()
    if not token:
        token = (os.getenv("ZENO_CLOUD_TOKEN") or os.getenv("SCRIPTCUT_CLOUD_TOKEN") or "").strip()
    return {"url": url.rstrip("/"), "token": token} if url and token else None


def _post(record: Dict[str, Any], timeout: float = 20.0) -> bool:
    cloud = _cloud()
    if cloud is None:
        return False
    body = json.dumps(record, ensure_ascii=False).encode("utf-8")
    headers = {"Authorization": f"Bearer {cloud['token']}", "Content-Type": "application/json",
               "X-Zeno-Version": _version(), "X-Zeno-Platform": f"{platform.system()} {platform.machine()}"}
    try:
        import httpx
        return httpx.post(cloud["url"] + "/v1/jobs", headers=headers, content=body, timeout=timeout).status_code == 200
    except ImportError:
        pass
    except Exception:
        return False
    try:
        import urllib.request
        req = urllib.request.Request(cloud["url"] + "/v1/jobs", data=body, headers=headers, method="POST")
        with urllib.request.urlopen(req, timeout=timeout) as resp:   # noqa: S310  (the URL is the team's own server)
            return resp.status == 200
    except Exception:
        return False


def flush_outbox(limit: int = 5) -> int:
    """Send records that could not be sent earlier (a few per job, oldest first). Returns how many went."""
    sent = 0
    try:
        for f in sorted(OUTBOX.glob("*.json"))[:limit]:
            try:
                rec = json.loads(f.read_text(encoding="utf-8"))
            except Exception:
                f.unlink(missing_ok=True)
                continue
            if _post(rec):
                f.unlink(missing_ok=True)
                sent += 1
            else:
                break
    except Exception:
        pass
    return sent


def send(record: Dict[str, Any]) -> bool:
    """Send one record; keep it in the outbox when the server cannot be reached (the newest version of a job replaces the older)."""
    try:
        OUTBOX.mkdir(parents=True, exist_ok=True)
        flush_outbox()
        if _post(record):
            (OUTBOX / f"{record['job_uid']}.json").unlink(missing_ok=True)
            return True
        (OUTBOX / f"{record['job_uid']}.json").write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")
        old = sorted(OUTBOX.glob("*.json"), key=lambda p: p.stat().st_mtime)
        for f in old[:-60]:                      # never let the outbox grow without end
            f.unlink(missing_ok=True)
    except Exception:
        pass
    return False


def archive_job(
    plan_path: Path,
    plan: Optional[Dict[str, Any]] = None,
    video_seconds: float = 0.0,
    output_mp4: Optional[Path] = None,
    kind: str = "render",
    extra: Optional[Dict[str, Any]] = None,
) -> Optional[bool]:
    """Build this job's record and send it. True = stored on the server, False = waiting in the outbox, None = switched off."""
    if not enabled():
        return None
    try:
        return send(build_record(plan_path, plan, video_seconds, output_mp4, kind=kind, extra=extra))
    except Exception:
        return False


# ---------------------------------------------------------------- jobs that stop early, and sending without waiting
def archive_blocked(stage: str, reason: str, script_arg: str = "", inputs: Optional[List[str]] = None,
                    extra: Optional[Dict[str, Any]] = None) -> Optional[bool]:
    """A job that stopped before it had a plan (a failed live check, a script that would not load, bad footage, a crash in
    inspect). Small record: what stopped it and the agent conversation so far. One record per script per day per computer."""
    if not enabled():
        return None
    try:
        day = time.strftime("%Y-%m-%d")
        seed = f"{platform.node()}|{script_arg}|{day}"
        try:
            from common import job_start_epoch
            since = job_start_epoch(create=False)
        except Exception:
            since = time.time() - 3600
        try:
            import agent_log
            conv = agent_log.collect(since)
        except Exception:
            conv = {}
        try:
            import agent_log as _al
            script_label = _al.redact(str(script_arg))[:300]
        except Exception:
            script_label = str(script_arg)[:300]
        record = {
            "job_uid": "blocked_" + hashlib.sha256(seed.encode("utf-8")).hexdigest()[:24],
            "kind": "blocked",
            "project": "",
            "video_name": "",
            "zeno_version": _version(),
            "platform": f"{platform.system()} {platform.machine()}",
            "sent_at": round(time.time(), 1),
            "blocked": {"stage": stage, "reason": str(reason)[:6000], "script": script_label,
                        "clips": len(inputs or [])},
            "environment": environment(),
            "conversation": conv,
        }
        if extra:
            record.update(extra)
        return send(record)
    except Exception:
        return False


def send_in_background(plan_path: Path, kind: str) -> None:
    """Send this job's record from a separate process, so a plan check or inspect returns at once."""
    if not enabled():
        return
    try:
        import subprocess
        import sys as _sys
        args = [_sys.executable, str(Path(__file__).resolve()), "--send", str(Path(plan_path).resolve()), "--kind", kind]
        kw: Dict[str, Any] = {"stdin": subprocess.DEVNULL, "stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL,
                              "cwd": str(ROOT)}
        if os.name == "nt":
            kw["creationflags"] = 0x00000008 | 0x00000200   # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
        else:
            kw["start_new_session"] = True
        subprocess.Popen(args, **kw)   # noqa: S603  (our own script, fixed arguments)
    except Exception:
        pass


def _main() -> None:
    import argparse
    import sys as _sys
    _sys.path.insert(0, str(Path(__file__).resolve().parent))
    ap = argparse.ArgumentParser(description="Send one job's archive record (used in the background by zeno.py).")
    ap.add_argument("--send", required=True, help="work/<project>/plan.edl")
    ap.add_argument("--kind", default="checking")
    a = ap.parse_args()
    plan_path = Path(a.send)
    plan = None
    try:
        from edl_parser import load_edl
        plan = load_edl(plan_path)
    except Exception:
        plan = None
    archive_job(plan_path, plan, kind=a.kind)


if __name__ == "__main__":
    _main()
