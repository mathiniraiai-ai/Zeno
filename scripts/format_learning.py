#!/usr/bin/env python3
"""After a job: send the numbers (never the script, footage or video) for each format the plan used to the Zeno server.

The plan already knows every segment's layout, length, zoom, transition and marks, so this costs no tokens and needs no
video analysis. The server groups observations by style and format; once an editor approves a recipe, computers copy it into
styles/formats/team/ with `zeno.py --sync-learnings`. See styles/formats/ and scripts/format_library.py.
"""
from __future__ import annotations

import hashlib
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))

STYLE_TAG_RE = re.compile(r"^#\s*style\s*:?\s*(\S+)", re.I | re.M)
ZOOM_IN_STYLES = {"sudden_in", "punch_in", "flash_zoom"}


def _layout(seg: Dict[str, Any]) -> str:
    return str(seg.get("layout") or "single").split(":")[0].strip().lower() or "single"


def build_observation(plan: Dict[str, Any], plan_text: str = "", script_text: str = "", verified: bool = False) -> Optional[Dict[str, Any]]:
    """Per-format numbers from a plan; None when the plan has no segments."""
    segs = [s for s in plan.get("segments") or [] if float(s.get("end", 0)) > float(s.get("start", 0))]
    if not segs:
        return None
    m = STYLE_TAG_RE.search(plan_text[:2000]) if plan_text else None
    style = (m.group(1) if m else str(plan.get("style") or "unspecified")).strip().lower()
    seg_layout = {str(s.get("id")): _layout(s) for s in segs}
    by_fmt: Dict[str, List[Dict[str, Any]]] = {}
    for s in segs:
        by_fmt.setdefault(_layout(s), []).append(s)
    marks_by_fmt: Dict[str, int] = {}
    for mk in plan.get("marks") or []:
        marks_by_fmt[seg_layout.get(str(mk.get("segment")), "single")] = marks_by_fmt.get(seg_layout.get(str(mk.get("segment")), "single"), 0) + 1
    formats = []
    for fmt, ss in sorted(by_fmt.items()):
        secs = sum(float(s["end"]) - float(s["start"]) for s in ss)
        zoom_in = sum(1 for s in ss if str(s.get("zoom_style") or "") in ZOOM_IN_STYLES or float(s.get("zoom_to") or 0) > float(s.get("zoom") or 1.0) + 0.01)
        trans = sum(1 for s in ss if str(s.get("transition") or "cut").split(":")[0].split("@")[0] not in ("", "cut"))
        formats.append({
            "format": fmt,
            "segments": len(ss),
            "seconds": round(secs, 2),
            "mean_shot_sec": round(secs / len(ss), 3),
            "zoom_in_share": round(zoom_in / len(ss), 3),
            "transitions_per_min": round(trans / max(secs / 60.0, 1 / 60.0), 2),
            "marks": marks_by_fmt.get(fmt, 0),
            "strike_banners": sum(len(s.get("banners") or []) for s in ss),
        })
    basis = script_text.strip() or plan_text or ",".join(sorted(seg_layout))
    return {
        "style": style,
        "script_hash": hashlib.sha256(basis.encode("utf-8")).hexdigest()[:16],
        "verified": bool(verified),
        "video_seconds": round(sum(f["seconds"] for f in formats), 2),
        "formats": formats,
    }


def post_observation(obs: Dict[str, Any]) -> bool:
    """Send to the Zeno server with this computer's key. Quiet and safe: any problem just returns False."""
    import os

    if os.environ.get("ZENO_NO_OBSERVE") == "1":   # demos and test renders must not count as team jobs
        return False
    try:
        import auth_session
        import httpx

        token, base = auth_session.get_active_token(), auth_session.get_cloud_url().rstrip("/")
        if not token:
            return False
        r = httpx.post(base + "/v1/observations", headers={"Authorization": f"Bearer {token}"}, json=obs, timeout=10.0)
        return r.status_code == 200
    except Exception:   # noqa: BLE001
        return False


def observe_job(plan_path: Path, plan: Dict[str, Any], verified: bool) -> Optional[Dict[str, Any]]:
    plan_text = Path(plan_path).read_text(encoding="utf-8", errors="ignore") if Path(plan_path).is_file() else ""
    script_text = ""
    sp = plan.get("script")
    if sp:
        try:
            import common

            script_text = common.read_script_text(common.resolve(sp))
        except Exception:   # noqa: BLE001
            script_text = ""
    obs = build_observation(plan, plan_text, script_text, verified)
    if obs is not None:
        post_observation(obs)
    return obs
