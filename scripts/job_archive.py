"""The job archive: every edit stored end to end on the Zeno server, so the team can later study where the editing fell short.

One record per job, text only (never footage or video):
  the script, the script analysis, every version of the plan (from the first one the Director wrote to the one that was rendered),
  what each plan check said, the decisions file, the verification results, the timeline, how the split panels were framed,
  the token numbers, the editor's feedback, and how many times the job was rendered.

`snapshot_plan` keeps the plan's history as the job goes; `add_feedback` records what the editor said; `archive_job` builds the
record and sends it. A record that cannot be sent waits in `work/_job_outbox/` and goes with the next job. Nothing here can stop
or slow an edit: every failure is swallowed. `ZENO_ARCHIVE=0` turns it off. Standard library only (httpx if present).
"""
from __future__ import annotations

import hashlib
import json
import os
import platform
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parent.parent
WORK = ROOT / "work"
OUTBOX = WORK / "_job_outbox"
HISTORY = "_history"
FEEDBACK = "feedback.jsonl"
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
                out.append(json.loads(line))
            except Exception:
                continue
    except Exception:
        pass
    return out[-100:]


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

    segs = [s for s in (plan.get("segments") or []) if isinstance(s, dict)]
    layouts: Dict[str, int] = {}
    for s in segs:
        lay = str(s.get("layout") or "single").split(":")[0]
        layouts[lay] = layouts.get(lay, 0) + 1
    script_file = None
    try:
        from common import resolve
        script_file = resolve(plan["script"]) if plan.get("script") else None
    except Exception:
        script_file = None

    def _json(name: str) -> Any:
        try:
            return json.loads((proj / name).read_text(encoding="utf-8"))
        except Exception:
            return None

    out_name = Path(str(plan.get("output") or output_mp4 or proj.name)).stem
    timeline = ""
    if output_mp4 is not None:
        timeline = _read(Path(output_mp4).with_suffix(".timeline.txt"), 120_000)
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
        "verify": _json("verify_report.json"),
        "render_notes": _json("render_notes.json"),
        "page_meta": _json("_page/page_meta.json"),
        "timeline": timeline,
        "tokens": _json("token_telemetry.json"),
        "feedback": _feedback(proj),
    }
    if extra:
        record.update(extra)
    # keep the record under the server's limit: the big measurement files go first
    for key in ("facts", "line_map"):
        if len(json.dumps(record, ensure_ascii=False).encode("utf-8")) <= MAX_RECORD:
            break
        record[key] = record[key][:40_000] + "\n… [cut to fit]"
    return record


# ---------------------------------------------------------------- sending
def _cloud() -> Optional[Dict[str, str]]:
    try:
        import auth_session
        sess = auth_session.load_session()
        url = str(sess.get("cloud_url") or auth_session.get_cloud_url()).strip()
        token = str(sess.get("token") or "").strip()
    except Exception:
        url = (os.getenv("ZENO_CLOUD_URL") or os.getenv("SCRIPTCUT_CLOUD_URL") or "").strip()
        token = (os.getenv("ZENO_CLOUD_TOKEN") or os.getenv("SCRIPTCUT_CLOUD_TOKEN") or "").strip()
    return {"url": url.rstrip("/"), "token": token} if url and token else None


def _post(record: Dict[str, Any], timeout: float = 20.0) -> bool:
    cloud = _cloud()
    if cloud is None:
        return False
    body = json.dumps(record, ensure_ascii=False).encode("utf-8")
    headers = {"Authorization": f"Bearer {cloud['token']}", "Content-Type": "application/json"}
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
