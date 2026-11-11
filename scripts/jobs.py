#!/usr/bin/env python3
"""One clear answer to "what is each edit doing?" for the Zeno desktop app (and anything else that wants to watch): it reads the files the engine
already writes (progress, plan, events, reports, outputs) and returns plain JSON. It reads only; it never changes a job.

    python3 scripts/jobs.py list
    python3 scripts/jobs.py detail <work folder name> [--since <last event id>]
    python3 scripts/jobs.py info
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parent.parent
WORK = ROOT / "work"
sys.path.insert(0, str(Path(__file__).resolve().parent))
import events  # noqa: E402

STAGES = ["Mapping", "Transcribing", "Matching", "Editing", "Mastering"]
STALE_SEC = 240           # a running job that has not reported for this long is shown as stalled


def _read(p: Path) -> Any:
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:   # noqa: BLE001
        return None


def _plan_file(d: Path) -> Optional[Path]:
    """The plan the agent is working on: the newest plan*.edl that has cuts (the auto copy is only a backup)."""
    best, best_t = None, 0.0
    for f in d.glob("plan*.edl"):
        if f.name == "plan.auto.edl":
            continue
        try:
            if re.search(r"^L\d", f.read_text(encoding="utf-8", errors="ignore"), re.M) and f.stat().st_mtime >= best_t:
                best, best_t = f, f.stat().st_mtime
        except OSError:
            continue
    return best


_SFX_LEN: Dict[str, float] = {}


def _sfx_seconds(name: str) -> float:
    """Length of a sound effect file in seconds (0.4 when it cannot be read)."""
    if name in _SFX_LEN:
        return _SFX_LEN[name]
    n = 0.4
    try:
        f = next(iter((ROOT / "assets").rglob(Path(name).name)), None)
        if f is not None and f.suffix.lower() == ".wav":
            import wave

            with wave.open(str(f)) as w:
                n = w.getnframes() / float(w.getframerate() or 1)
        elif f is not None:
            import subprocess

            r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(f)], capture_output=True, text=True, timeout=8)
            n = float(r.stdout.strip() or n)
    except Exception:   # noqa: BLE001
        pass
    _SFX_LEN[name] = round(max(0.05, n), 2)
    return _SFX_LEN[name]


def _plan_summary(d: Path) -> Optional[Dict[str, Any]]:
    f = _plan_file(d)
    if f is None:
        return None
    try:
        import edl_parser

        plan = edl_parser.parse_edl(f.read_text(encoding="utf-8"))
    except Exception:   # noqa: BLE001
        return None
    segs = plan.get("segments") or []
    t, rows = 0.0, []
    for s in segs:
        dur = max(0.0, float(s.get("end", 0)) - float(s.get("start", 0)))
        rows.append({"id": str(s.get("id")), "start": round(t, 2), "dur": round(dur, 2), "layout": str(s.get("layout") or "single"),
                     "src": Path(str(s.get("src", ""))).name, "zoom": s.get("zoom_style") or ("move" if s.get("zoom_to") else ("fixed" if float(s.get("zoom") or 1) != 1.0 else "")),
                     "transition": str(s.get("transition") or "cut"), "speaker": str(s.get("speaker") or "")})
        t += dur
    footage = next((str(Path(str(s.get('src'))).parent) for s in segs if Path(str(s.get('src', ''))).is_absolute()), '')
    layouts: Dict[str, int] = {}
    for r in rows:
        layouts[r["layout"]] = layouts.get(r["layout"], 0) + 1
    # the layers of the timeline: sound effects, marks and transitions placed on the final timeline (the whoosh rule mirrors render.py)
    starts = {r["id"]: r["start"] for r in rows}
    segs_by_id = {str(x.get("id")): x for x in segs}
    sfx: List[Dict[str, Any]] = []
    for x in plan.get("sfx") or []:
        if str(x.get("segment")) in starts:
            sfx.append({"t": round(starts[str(x["segment"])] + float(x.get("at") or 0), 2), "file": str(x.get("file", "")), "gain": x.get("gain_db"), "kind": "sfx", "dur": _sfx_seconds(str(x.get("file", "")))})
    for m in plan.get("marks") or []:
        seg_id = str(m.get("segment"))
        if seg_id in starts:
            sfx.append({"t": round(starts[seg_id] + float(m.get("at") or 0), 2), "file": str(m.get("sfx") or m.get("type") or "mark"), "gain": m.get("gain_db"), "kind": "mark", "mark": str(m.get("type") or ""), "dur": _sfx_seconds(str(m.get("sfx") or ""))})
    whoosh = {"whip_left", "whip_right", "flash_zoom", "zoom_burst", "slide_up", "smoothleft", "smoothright", "warm_flash"}
    for r in rows[1:]:
        if r["transition"].lower() in whoosh and not any(abs(x["t"] - r["start"]) <= 0.35 and "whoosh" in x["file"].lower() for x in sfx):
            sfx.append({"t": r["start"], "file": "whoosh.wav", "gain": None, "kind": "auto", "dur": _sfx_seconds("whoosh.wav")})
    sfx.sort(key=lambda x: x["t"])
    return {"file": f.name, "footage": footage, "cuts": len(rows), "seconds": round(t, 1), "layouts": layouts, "timeline": rows, "sfx": sfx,
            "transition_list": [{"id": r["id"], "t": r["start"], "name": r["transition"], "dur": float(segs_by_id.get(r["id"], {}).get("transition_duration") or 0.25)} for r in rows if r["transition"] != "cut"],
            "transitions": sum(1 for r in rows if r["transition"] != "cut"),
            "subs": (plan.get("subs") or {}).get("style") if isinstance(plan.get("subs"), dict) else None, "output": str(plan.get("output") or "")}


def _outputs(plan_output: str) -> List[Dict[str, Any]]:
    if not plan_output:
        return []
    folder = Path(plan_output).parent
    out = []
    if folder.is_dir():
        for f in sorted(folder.iterdir()):
            if f.is_file() and f.suffix.lower() in (".mp4", ".prproj", ".xml", ".srt", ".txt", ".edl"):
                kind = ("reel" if f.name.startswith("reel_") else "square" if f.name.startswith("SQ_") else "video") if f.suffix.lower() == ".mp4" else f.suffix.lower().lstrip(".")
                out.append({"name": f.name, "kind": kind, "mb": round(f.stat().st_size / 1e6, 1), "at": f.stat().st_mtime, "path": str(f)})
    return out


def _title(d: Path, plan: Optional[Dict[str, Any]]) -> str:
    if plan and plan.get("output"):
        stem = Path(plan["output"]).stem
        return re.sub(r"^(reel_|SQ_)", "", stem)
    return d.name


def _status(d: Path, prog: Optional[Dict[str, Any]], plan: Optional[Dict[str, Any]], outs: List[Dict[str, Any]]) -> Dict[str, Any]:
    now = time.time()
    reel = next((o for o in outs if o["kind"] == "reel"), None)
    plan_t = (d / plan["file"]).stat().st_mtime if plan and (d / plan["file"]).is_file() else 0
    age = None
    if prog and prog.get("updated_at"):
        try:
            import datetime as _dt

            stamp = _dt.datetime.fromisoformat(str(prog["updated_at"]))      # progress.json stores UTC with an offset; a naive stamp is local time
            age = now - (stamp.timestamp() if stamp.tzinfo else time.mktime(stamp.timetuple()))
        except ValueError:
            age = None
    if prog and prog.get("state") == "running" and (age is None or age < STALE_SEC):
        return {"phase": "working", "label": "Working", "detail": prog.get("detail", "")}
    if prog and prog.get("state") == "running":
        return {"phase": "stalled", "label": "No update for a while", "detail": prog.get("detail", "")}
    if prog and prog.get("state") == "failed":
        return {"phase": "failed", "label": "Failed", "detail": prog.get("detail", "")}
    if reel and reel["at"] >= plan_t:
        return {"phase": "done", "label": "Rendered", "detail": f"{reel['mb']} MB"}
    if plan:
        return {"phase": "plan", "label": "Plan ready, waiting for your Yes", "detail": f"{plan['cuts']} cuts, {plan['seconds']} s"}
    if (d / "line_map.md").is_file():
        return {"phase": "measured", "label": "Measured, waiting for the editor's plan", "detail": ""}
    return {"phase": "new", "label": "New", "detail": ""}


def summary(d: Path) -> Dict[str, Any]:
    prog = _read(d / "progress.json")
    plan = _plan_summary(d)
    outs = _outputs(plan["output"]) if plan else []
    st = _status(d, prog, plan, outs)
    return {"name": d.name, "title": _title(d, plan), "status": st, "stage": (prog or {}).get("stage"), "stage_name": (prog or {}).get("stage_name"),
            "pct": (prog or {}).get("pct", 0), "completed": (prog or {}).get("completed"), "total": (prog or {}).get("total"), "elapsed": (prog or {}).get("elapsed_mmss"), "updated": max((p.stat().st_mtime for p in d.iterdir() if p.is_file()), default=0)}


def list_jobs(limit: int = 40) -> List[Dict[str, Any]]:
    rows = []
    if WORK.is_dir():
        for d in WORK.iterdir():
            if d.is_dir() and not d.name.startswith("_") and ((d / "line_map.md").is_file() or (d / "progress.json").is_file()):
                try:
                    rows.append(summary(d))
                except Exception:   # noqa: BLE001
                    continue
    rows.sort(key=lambda r: r["updated"], reverse=True)
    return rows[:limit]


def detail(name: str, since: int = 0) -> Dict[str, Any]:
    d = WORK / name
    if not d.is_dir():
        return {"error": f"No job named {name}"}
    base = summary(d)
    plan = _plan_summary(d)
    prog = _read(d / "progress.json") or {}
    outs = _outputs(plan["output"]) if plan else []
    tele = _read(d / "token_telemetry.json") or {}
    rep = _read(d / "job_report.json") or {}
    allev = events.read(d, since=since, limit=1500)
    scans = [e for e in allev if e.get("kind") == "scan"][-900:]
    ev = [e for e in allev if e.get("kind") != "scan"][-400:]
    # footage: the newest face event of each clip
    clips: Dict[str, Dict[str, Any]] = {}
    for e in events.read(d, since=0, limit=4000):
        if e.get("kind") == "face" and e.get("clip") and "faces" in e:      # a face-scan result, not a summary line
            clips[e["clip"]] = {"clip": e["clip"], "faces": len(e.get("faces") or []), "shot": e.get("shot"), "silent": bool(e.get("silent")), "spans": e.get("faces") or []}
    checks = [e for e in events.read(d, since=0, limit=4000) if e.get("kind") == "verify"][-8:]
    last_check = None
    for pc in reversed(rep.get("plan_checks") or []):
        last_check = pc
        break
    images = {k: str(d / f) for k, f in (("clip_sheet", "clip_sheet_1.jpg"), ("review_sheet", "review_sheet.jpg"), ("marks", "marks_contact_sheet.jpg")) if (d / f).is_file()}
    agent = (rep.get("agent") or {})
    tokens = None
    if tele:
        tokens = {"agent": tele.get("agent_name"), "model": tele.get("agent_model"), "in": tele.get("agent_in_tokens"), "cached": tele.get("agent_cached_tokens"),
                  "out": tele.get("agent_out_tokens"), "total": tele.get("agent_total_tokens"), "gemini": tele.get("transcribe_total_tokens"),
                  "mixed": bool(agent.get("mixed_with_development")), "turns": agent.get("turns")}
    base.update({"stages": STAGES, "detail": prog.get("detail", ""), "plan": plan, "outputs": outs, "clips": sorted(clips.values(), key=lambda c: c["clip"].lower()),
                 "verify": checks, "plan_check": last_check, "subs": rep.get("subs"), "cleanup": rep.get("cleanup"), "failures": rep.get("failures") or [],
                 "tokens": tokens, "images": images, "events": ev, "scans": scans, "last_event": allev[-1]["id"] if allev else since, "format": rep.get("format")})
    return base


def zeno_info(check_updates: bool = True, force_check: bool = False) -> Dict[str, Any]:
    info: Dict[str, Any] = {"version": "", "sha": "", "behind": None, "update": None, "root": str(ROOT), "signed_in": None}
    try:
        info["version"] = (ROOT / "VERSION").read_text(encoding="utf-8").strip()
    except OSError:
        pass
    rel_state = _read(WORK / ".cloud_release_state.json") or {}
    if rel_state.get("sha"):
        info["sha"] = str(rel_state["sha"])
    if check_updates:
        try:
            import auto_updater

            chk = auto_updater.check_cloud_update(force=force_check, timeout_sec=8.0, cache_ttl_sec=120.0)
            if chk.get("ok"):
                avail = bool(chk.get("available"))
                info["behind"] = 1 if avail else 0
                if chk.get("sha"):
                    info["sha"] = str(chk["sha"])
                info["update"] = {
                    "available": avail,
                    "current_version": chk.get("current_version") or info["version"],
                    "latest_version": chk.get("latest_version") or info["version"],
                    "notes": chk.get("notes") or "",
                    "size_mb": chk.get("size_mb") or 0.0,
                    "mandatory": bool(chk.get("mandatory")),
                    "dmg_url": chk.get("dmg_url") or "",
                    "exe_url": chk.get("exe_url") or "",
                    "bundle_sha256": chk.get("bundle_sha256") or "",
                }
            else:
                info["behind"] = None
        except Exception:   # noqa: BLE001
            info["behind"] = None
    else:
        cached = _read(WORK / "_update_check.json") or {}
        if cached.get("ok"):
            avail = bool(cached.get("available"))
            info["behind"] = 1 if avail else 0
            info["update"] = {
                "available": avail,
                "current_version": cached.get("current_version") or info["version"],
                "latest_version": cached.get("latest_version") or info["version"],
                "notes": cached.get("notes") or "",
                "size_mb": cached.get("size_mb") or 0.0,
                "mandatory": bool(cached.get("mandatory")),
                "dmg_url": cached.get("dmg_url") or "",
                "exe_url": cached.get("exe_url") or "",
                "bundle_sha256": cached.get("bundle_sha256") or "",
            }
    try:
        import auth_session

        s = auth_session.get_session_info()
        info["signed_in"] = (s.get("email") or s.get("name")) if s.get("authenticated") else None
    except Exception:   # noqa: BLE001
        pass
    return info


def main(argv: List[str]) -> int:
    cmd = argv[1] if len(argv) > 1 else "list"
    if cmd == "list":
        out: Any = list_jobs()
    elif cmd == "detail" and len(argv) > 2:
        since = int(argv[argv.index("--since") + 1]) if "--since" in argv else 0
        out = detail(argv[2], since)
    elif cmd == "info":
        out = zeno_info("--no-update-check" not in argv, "--force-update-check" in argv)
    else:
        print(__doc__)
        return 2
    sys.stdout.write(json.dumps(out, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
