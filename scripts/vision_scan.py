#!/usr/bin/env python3
"""Watch the face tracker work: scan a clip frame by frame with the same YuNet detector Zeno uses, print one line per frame, and report each frame
(a small picture plus the faces found) to the desktop app's live view. It measures only; it changes nothing.

    python3 scripts/vision_scan.py "<clip or folder>" [--step 0.5] [--max 60] [--project <work folder>]
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))
import events  # noqa: E402

THUMB_W = 220
VIDEO_EXT = {".mp4", ".mov", ".m4v", ".mkv", ".webm", ".avi"}


def _face_row(f: Dict[str, Any]) -> Dict[str, Any]:
    return {"box": f.get("bbox_norm"), "eyes": f.get("eye_center_norm"), "mouth": f.get("mouth_roi_norm"), "mar": round(float(f.get("mar_score") or 0), 3),
            "speaking": bool(f.get("is_speaking")), "conf": round(float(f.get("confidence") or 0), 2)}


def record(clip_name: str, t: float, frame: Any, faces: List[Dict[str, Any]], i: int, n: int, ms: float, project_dir: Optional[Path] = None, thumb_w: int = THUMB_W) -> None:
    """Save a small picture of the scanned frame and report it. Never raises."""
    try:
        import cv2

        d = project_dir or events._project_dir()
        if d is None or not d.is_dir():
            return
        out = d / "_scan"
        out.mkdir(exist_ok=True)
        h, w = frame.shape[:2]
        thumb = cv2.resize(frame, (thumb_w, max(1, int(h * thumb_w / w))), interpolation=cv2.INTER_AREA)
        stem = "".join(c if c.isalnum() else "_" for c in Path(clip_name).stem)[:24]
        f = out / f"{stem}_{i:03d}.jpg"
        cv2.imwrite(str(f), thumb, [int(cv2.IMWRITE_JPEG_QUALITY), 72])
        events.emit("scan", f"{clip_name} {t:.2f}s: {len(faces)} face(s), {ms:.0f} ms", clip=clip_name, t_clip=round(t, 2), i=i, n=n, ms=round(ms, 1),
                    faces=[_face_row(x) for x in faces], thumb=str(f), size=[w, h])
    except Exception:   # noqa: BLE001
        pass


def scan_clip(path: Path, step: float = 0.5, max_frames: int = 60, project: Optional[str] = None, pause: float = 0.0, echo: bool = True, thumb_w: int = THUMB_W) -> List[Dict[str, Any]]:
    import cv2
    import vision_tracker

    if project:
        events.set_project(project)
    cap = cv2.VideoCapture(str(path))
    if not cap.isOpened():
        raise SystemExit(f"Cannot open {path}")
    fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    dur = total / fps if total else 0.0
    times = []
    t = 0.0
    while t < dur and len(times) < max_frames:
        times.append(t)
        t += step
    if echo:
        print(f"{path.name}: {dur:.1f} s, {fps:.0f} fps, scanning {len(times)} frames (one every {step:g} s) with the YuNet face detector")
    rows: List[Dict[str, Any]] = []
    for i, tt in enumerate(times, 1):
        cap.set(cv2.CAP_PROP_POS_MSEC, tt * 1000.0)
        ok, frame = cap.read()
        if not ok or frame is None:
            continue
        t0 = time.perf_counter()
        faces = vision_tracker.prominent_faces(vision_tracker.detect_faces_in_frame(frame))
        ms = (time.perf_counter() - t0) * 1000.0
        record(path.name, tt, frame, faces, i, len(times), ms, thumb_w=thumb_w)
        rows.append({"t": tt, "faces": len(faces), "ms": ms})
        if echo:
            desc = "  ".join(f"[{(f['bbox_norm'][0] + f['bbox_norm'][2]) / 2:.2f},{(f['bbox_norm'][1] + f['bbox_norm'][3]) / 2:.2f}]{' speaking' if f.get('is_speaking') else ''}" for f in faces)
            print(f"  frame {i:>3}/{len(times)}  t={tt:5.2f}s  {len(faces)} face(s)  {ms:4.0f} ms  {desc}", flush=True)
        if pause:
            time.sleep(pause)
    cap.release()
    if echo and rows:
        print(f"  done: {len(rows)} frames, average {sum(r['ms'] for r in rows) / len(rows):.0f} ms a frame")
    events.emit("info", f"{path.name}: scanned {len(rows)} frames, faces seen in {sum(1 for r in rows if r['faces'])}", clip=path.name)
    return rows


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("clips", nargs="+")
    ap.add_argument("--step", type=float, default=0.5, help="seconds between scanned frames")
    ap.add_argument("--max", type=int, default=60, help="most frames per clip")
    ap.add_argument("--project", default=None, help="work folder to report to (default: the latest job)")
    ap.add_argument("--pause", type=float, default=0.0, help="wait this long after each frame (to watch it live)")
    ap.add_argument("--thumb", type=int, default=THUMB_W, help="width in pixels of the picture saved for each frame")
    a = ap.parse_args()
    clips: List[Path] = []
    for c in a.clips:                      # a folder means every video in it
        q = Path(c)
        clips += sorted(x for x in q.iterdir() if x.suffix.lower() in VIDEO_EXT and not x.name.startswith(".")) if q.is_dir() else [q]
    for c in clips:
        scan_clip(c, a.step, a.max, a.project, a.pause, thumb_w=a.thumb)
    return 0


if __name__ == "__main__":
    sys.exit(main())
