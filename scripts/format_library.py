#!/usr/bin/env python3
"""Format library: how split screens, video calls, selfies and close-ups should look, kept as small recipes.

The footage is never stored and no model ever looks at frames. OpenCV measures a reference video locally (no tokens) and the
numbers go into `styles/formats/<id>.json`. At edit time the script's own words pick the recipe, so the script directs the edit.

    python3 scripts/format_library.py list
    python3 scripts/format_library.py search "floating window of the robot, horizontal split screen"
    python3 scripts/format_library.py show split_h            # the compact text an agent reads (about 100 tokens)
    python3 scripts/format_library.py extract <video> --id split_h --layout split_h --start 37 --end 93 \\
        --names "vertical split screen,side by side" --description "..." [--thumbs]

Retrieval is alias-phrase matching with a word-overlap fallback. That is enough for dozens of recipes; swap `search()` for an
embedding lookup (for example through the Zeno server) once the library holds hundreds.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common  # noqa: E402

ROOT = common.ROOT
LIB_DIR = ROOT / "styles" / "formats"
ALIAS_SCORE = 3.0        # one alias phrase found in the text
MATCH_THRESHOLD = 3.0    # below this, search() still ranks results but best_layout() answers nothing


def load_recipes(lib_dir: Path = LIB_DIR) -> List[Dict[str, Any]]:
    out = []
    for p in sorted(lib_dir.rglob("*.json")) if lib_dir.is_dir() else []:   # includes team/, the approved team recipes
        try:
            out.append(json.loads(p.read_text(encoding="utf-8")))
        except ValueError:
            continue
    return out


def _words(text: str) -> set:
    return set(re.findall(r"[a-z0-9]+", (text or "").lower()))


def search(query: str, k: int = 3, lib_dir: Path = LIB_DIR) -> List[Dict[str, Any]]:
    """Rank recipes for a piece of script wording. Each hit carries a `score`."""
    q = (query or "").lower()
    qw = _words(q)
    hits = []
    for r in load_recipes(lib_dir):
        score = sum(ALIAS_SCORE for a in r.get("names", []) if re.search(r"(?<![a-z0-9])" + re.escape(a.lower()) + r"(?![a-z0-9])", q))
        doc = _words(" ".join([r.get("id", ""), r.get("description", ""), r.get("when", ""), " ".join(r.get("names", []))]))
        score += 0.2 * len(qw & doc)
        if score > 0:
            hits.append({**r, "score": round(score, 2)})
    return sorted(hits, key=lambda h: -h["score"])[:k]


def best_layout(text: str, lib_dir: Path = LIB_DIR) -> str:
    """The Zeno layout (`split_h`, `split_v`, `video_call`, ...) whose alias appears in the script wording, or ''."""
    hits = search(text, 1, lib_dir)
    return hits[0]["layout"] if hits and hits[0]["score"] >= MATCH_THRESHOLD else ""


def recipe_text(r: Dict[str, Any]) -> str:
    """Compact text for an agent: what the format is, when to use it, the numbers, and how sure we are of them."""
    geo = r.get("geometry") or {}
    nums = "; ".join(f"{k}={v}" for k, v in geo.items())
    return (f"{r['id']} -> layout {r['layout']} [{r.get('confidence', 'defined')}]: {r.get('description', '')} "
            f"Use when: {r.get('when', '-')}. {nums}. Source: {r.get('source', '-')}.").strip()


# ----------------------------------------------------------------------------- measurement (local, no LLM)
def _pct(vals: List[float]) -> Optional[List[float]]:
    if not vals:
        return None
    v = sorted(vals)
    pick = lambda q: round(v[min(len(v) - 1, int(q * (len(v) - 1) + 0.5))], 3)
    return [pick(0.25), pick(0.5), pick(0.75)]


def _seam_position(frame, kind: str) -> Optional[float]:
    """Where the baked seam sits, as a fraction of the width (vertical seam) or height (horizontal seam)."""
    import numpy as np

    gray = frame.mean(axis=2).astype(np.float32)
    h, w = gray.shape
    if kind == "split_h_baked":
        d = np.mean(np.abs(np.diff(gray, axis=1)), axis=0)
        lo, hi = int(w * 0.35), int(w * 0.65)
        return round((lo + int(np.argmax(d[lo:hi]))) / w, 4)
    d = np.mean(np.abs(np.diff(gray, axis=0)), axis=1)
    lo, hi = int(h * 0.35), int(h * 0.65)
    return round((lo + int(np.argmax(d[lo:hi]))) / h, 4)


def measure_video(video: Path, start: float = 0.0, end: Optional[float] = None, n_frames: int = 24, thumbs_to: Optional[Path] = None) -> Dict[str, Any]:
    """Sample frames across [start, end], then measure split seams, faces, framing and motion with OpenCV and YuNet."""
    import cv2
    import numpy as np
    import vision_tracker

    cap = cv2.VideoCapture(str(video))
    if not cap.isOpened():
        raise SystemExit(f"cannot open {video}")
    fps = cap.get(cv2.CAP_PROP_FPS) or 24.0
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    w, h = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)), int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    dur = total / fps if total else 0.0
    t0, t1 = max(0.0, start), min(end if end else dur, dur) if dur else (end or 0.0)
    times = [t0 + (t1 - t0) * (i + 0.5) / n_frames for i in range(n_frames)]
    seams, seam_pos, counts, widths, eye_y, heads, cx, prev, motion, saved = [], [], [], [], [], [], [], None, [], 0
    for t in times:
        cap.set(cv2.CAP_PROP_POS_MSEC, t * 1000.0)
        ok, frame = cap.read()
        if not ok:
            continue
        seam = vision_tracker.detect_baked_split_seam(frame)
        seams.append(seam or "none")
        if seam:
            pos = _seam_position(frame, seam)
            if pos is not None:
                seam_pos.append(pos)
        faces = vision_tracker.detect_faces_in_frame(frame)
        counts.append(len(faces))
        for f in faces:
            x0, y0, x1, y1 = f["bbox_norm"]
            widths.append(x1 - x0)
            heads.append(y0)
            eye_y.append(f["eye_center_norm"][1])
            cx.append((x0 + x1) / 2)
        small = cv2.resize(frame, (90, 160)).astype(np.float32)
        if prev is not None:
            motion.append(float(np.mean(np.abs(small - prev)) / 255.0))
        prev = small
        if thumbs_to is not None and saved < 3 and len(seams) % max(1, n_frames // 3) == 0:
            thumbs_to.parent.mkdir(parents=True, exist_ok=True)
            cv2.imwrite(str(thumbs_to.with_name(f"{thumbs_to.name}_{saved + 1}.jpg")), cv2.resize(frame, (180, 320)), [cv2.IMWRITE_JPEG_QUALITY, 70])
            saved += 1
    cap.release()
    kinds = {k: seams.count(k) for k in set(seams)}
    top = max(kinds, key=kinds.get) if kinds else "none"
    return {
        "video": video.name, "size": f"{w}x{h}", "fps": round(fps, 2), "window_sec": [round(t0, 1), round(t1, 1)], "frames_sampled": len(seams),
        "seam": {"kind": top, "share_of_frames": round(kinds.get(top, 0) / max(1, len(seams)), 2), "position_frac": (_pct(seam_pos) or [None, None])[1]},
        "faces_per_frame_median": sorted(counts)[len(counts) // 2] if counts else 0,
        "face_width_frac_p25_p50_p75": _pct(widths), "eye_y_frac_p25_p50_p75": _pct(eye_y),
        "headroom_frac_p25_p50_p75": _pct(heads), "face_center_x_frac_p25_p50_p75": _pct(cx),
        "motion_mean": round(sum(motion) / len(motion), 4) if motion else 0.0,
    }


def write_recipe(recipe: Dict[str, Any], lib_dir: Path = LIB_DIR) -> Path:
    lib_dir.mkdir(parents=True, exist_ok=True)
    path = lib_dir / f"{recipe['id']}.json"
    path.write_text(json.dumps(recipe, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    return path


def extract(video: Path, rid: str, layout: str, names: List[str], description: str, when: str, start: float, end: Optional[float], thumbs: bool,
            lib_dir: Path = LIB_DIR) -> Dict[str, Any]:
    """Measure a reference and merge the numbers into the recipe (created if new). The video itself is not kept."""
    m = measure_video(video, start, end, thumbs_to=(lib_dir / "thumbs" / rid) if thumbs else None)
    path = lib_dir / f"{rid}.json"
    r = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {"id": rid}
    r.update({"layout": layout or r.get("layout", "single")})
    if names:
        r["names"] = names
    if description:
        r["description"] = description
    if when:
        r["when"] = when
    r["measured"] = m
    r["confidence"] = "measured"
    r["source"] = f"measured from {video.name} ({m['window_sec'][0]}-{m['window_sec'][1]} s, {m['frames_sampled']} frames) on {dt.date.today()}"
    write_recipe(r, lib_dir)
    return r


def sync_team(lib_dir: Path = LIB_DIR) -> Dict[str, Any]:
    """Copy the recipes an editor approved on the Zeno server into styles/formats/team/. Local measured recipes are never touched."""
    try:
        import auth_session
        import httpx

        token, base = auth_session.get_active_token(), auth_session.get_cloud_url().rstrip("/")
        if not token:
            return {"ok": False, "message": "not signed in (python3 scripts/setup.py --login)"}
        r = httpx.get(base + "/v1/formats", headers={"Authorization": f"Bearer {token}"}, timeout=15.0)
        if r.status_code == 404:
            return {"ok": True, "pulled": 0, "message": "the server has no team formats yet"}
        r.raise_for_status()
        formats = r.json().get("formats", [])
    except Exception as exc:   # noqa: BLE001
        return {"ok": False, "message": f"could not reach the server ({type(exc).__name__})"}
    layouts = {"single", "split_h", "split_v", "video_call", "chat_bar"}
    for f in formats:
        rid = f"team_{f['style']}_{f['format']}"
        st = f.get("stats", {})
        mean = st.get("mean_shot_sec", {})
        write_recipe({
            "id": rid, "layout": f["format"] if f["format"] in layouts else "single", "names": [],
            "description": (f"Seen in {f['n_jobs']} approved team jobs of style {f['style']}: shots of {mean.get('p25', '?')} to {mean.get('p75', '?')} s "
                            f"(median {mean.get('median', '?')} s)."),
            "when": f"format {f['format']} in style {f['style']}",
            "geometry": {k: v.get("median") for k, v in st.items() if k in ("mean_shot_sec", "zoom_in_share", "transitions_per_min", "strike_banners")},
            "confidence": "team-approved", "source": f"{f['n_jobs']} team jobs, approved by {f.get('approved_by', '?')}",
        }, lib_dir / "team")
    return {"ok": True, "pulled": len(formats), "message": f"{len(formats)} approved team format(s) synced"}


def main() -> None:
    ap = argparse.ArgumentParser(description="Format library: measure, store and look up how video formats should look")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list")
    sub.add_parser("sync", help="copy team-approved recipes from the Zeno server")
    s = sub.add_parser("search"); s.add_argument("query")
    sh = sub.add_parser("show"); sh.add_argument("id")
    e = sub.add_parser("extract")
    e.add_argument("video"); e.add_argument("--id", required=True); e.add_argument("--layout", default="")
    e.add_argument("--names", default=""); e.add_argument("--description", default=""); e.add_argument("--when", default="")
    e.add_argument("--start", type=float, default=0.0); e.add_argument("--end", type=float, default=None)
    e.add_argument("--thumbs", action="store_true", help="also save 3 tiny JPEGs (about 8 KB each) for a person to look at")
    a = ap.parse_args()
    if a.cmd == "sync":
        print(sync_team()["message"])
    elif a.cmd == "list":
        for r in load_recipes():
            print(f"{r['id']:<12} layout={r['layout']:<10} {r.get('confidence', 'defined'):<9} {r.get('description', '')[:70]}")
    elif a.cmd == "search":
        for h in search(a.query):
            print(f"{h['score']:>5}  {h['id']:<12} -> {h['layout']}")
        print("layout from script wording:", best_layout(a.query) or "none")
    elif a.cmd == "show":
        r = next((x for x in load_recipes() if x["id"] == a.id), None)
        sys.exit(f"no recipe named {a.id}") if not r else print(recipe_text(r))
    elif a.cmd == "extract":
        r = extract(Path(a.video), a.id, a.layout, [x.strip() for x in a.names.split(",") if x.strip()], a.description, a.when, a.start, a.end, a.thumbs)
        print(json.dumps(r["measured"], indent=1))


if __name__ == "__main__":
    main()
