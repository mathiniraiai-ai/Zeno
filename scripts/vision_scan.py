#!/usr/bin/env python3
"""Live Frame-by-Frame YuNet Face Scanner & Event Telemetry Reporter (`vision_scan.py`).

Scans source video clips at fixed time steps using `vision_tracker`, writes compact
JPEG frame thumbnails into `work/<project>/_scan/`, and emits `"scan"` telemetry events
for desktop live inspection without mutating source media or edit plans.

Usage:
    python3 scripts/vision_scan.py "<clip_or_folder>" [--step 0.5] [--max 60] [--project <name>]
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Union

_SCRIPTS_DIR = Path(__file__).resolve().parent
ROOT: Path = _SCRIPTS_DIR.parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

try:
    import events  # type: ignore[import-not-found]
except ImportError:  # pragma: no cover
    events = None  # type: ignore[assignment]

THUMB_W: int = 220
VIDEO_EXT: Set[str] = {".mp4", ".mov", ".m4v", ".mkv", ".webm", ".avi"}


def _resolve_active_project_dir(project_dir: Optional[Union[str, Path]] = None) -> Optional[Path]:
    """Resolve the active job work directory for thumbnail and event recording."""
    if project_dir is not None:
        return Path(project_dir)
    if events is None:
        return None
    if hasattr(events, "_project_dir"):
        return events._project_dir()
    if hasattr(events, "project_dir"):
        return events.project_dir()
    job_name = os.environ.get("ZENO_JOB_PROJECT", "").strip()
    if job_name:
        work_root = Path(getattr(events, "WORK", ROOT / "work"))
        return work_root / job_name
    return None


def _face_row(face: Dict[str, Any]) -> Dict[str, Any]:
    """Format a detected face dictionary into a compact telemetry event record."""
    return {
        "box": face.get("bbox_norm"),
        "eyes": face.get("eye_center_norm"),
        "mouth": face.get("mouth_roi_norm"),
        "mar": round(float(face.get("mar_score") or 0.0), 3),
        "speaking": bool(face.get("is_speaking")),
        "conf": round(float(face.get("confidence") or 0.0), 2),
    }


def record(
    clip_name: str,
    t: float,
    frame: Any,
    faces: List[Dict[str, Any]],
    i: int,
    n: int,
    ms: float,
    project_dir: Optional[Path] = None,
    thumb_w: int = THUMB_W,
) -> None:
    """Persist a compact frame thumbnail and emit a `"scan"` event. Never raises."""
    try:
        import cv2

        target_dir = _resolve_active_project_dir(project_dir)
        if target_dir is None or not target_dir.is_dir() or frame is None:
            return

        scan_dir = target_dir / "_scan"
        scan_dir.mkdir(exist_ok=True)
        h, w = frame.shape[:2]
        if h <= 0 or w <= 0:
            return

        tw = max(16, int(thumb_w))
        th = max(1, int(h * tw / w))
        thumb = cv2.resize(frame, (tw, th), interpolation=cv2.INTER_AREA)

        safe_stem = "".join(ch if ch.isalnum() else "_" for ch in Path(clip_name).stem)[:24] or "clip"
        thumb_path = scan_dir / f"{safe_stem}_{int(i):03d}.jpg"
        cv2.imwrite(str(thumb_path), thumb, [int(cv2.IMWRITE_JPEG_QUALITY), 72])

        if events is not None and hasattr(events, "emit"):
            events.emit(
                "scan",
                f"{clip_name} {t:.2f}s: {len(faces)} face(s), {ms:.0f} ms",
                clip=clip_name,
                t_clip=round(float(t), 2),
                i=int(i),
                n=int(n),
                ms=round(float(ms), 1),
                faces=[_face_row(f) for f in faces],
                thumb=str(thumb_path),
                size=[int(w), int(h)],
            )
    except Exception:
        pass


def scan_clip(
    path: Union[str, Path],
    step: float = 0.5,
    max_frames: int = 60,
    project: Optional[str] = None,
    pause: float = 0.0,
    echo: bool = True,
    thumb_w: int = THUMB_W,
) -> List[Dict[str, Any]]:
    """Scan a single video clip at `step`-second intervals with the YuNet face detector."""
    import cv2
    import vision_tracker

    clip_path = Path(path)
    if project and events is not None and hasattr(events, "set_project"):
        events.set_project(project)

    cap = cv2.VideoCapture(str(clip_path))
    if not cap.isOpened():
        raise SystemExit(f"Cannot open video clip: {clip_path}")

    fps = float(cap.get(cv2.CAP_PROP_FPS) or 25.0) or 25.0
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    duration = total_frames / fps if total_frames > 0 else 0.0

    step_sec = max(0.04, float(step))
    times: List[float] = []
    t_cur = 0.0
    while t_cur < duration and len(times) < int(max_frames):
        times.append(t_cur)
        t_cur += step_sec

    if echo:
        print(
            f"{clip_path.name}: {duration:.1f} s, {fps:.0f} fps, scanning {len(times)} frames "
            f"(every {step_sec:g} s) with YuNet face detector"
        )

    rows: List[Dict[str, Any]] = []
    for idx, ts in enumerate(times, 1):
        cap.set(cv2.CAP_PROP_POS_MSEC, ts * 1000.0)
        ok, frame = cap.read()
        if not ok or frame is None:
            continue

        t0 = time.perf_counter()
        faces = vision_tracker.prominent_faces(vision_tracker.detect_faces_in_frame(frame))
        elapsed_ms = (time.perf_counter() - t0) * 1000.0

        record(clip_path.name, ts, frame, faces, idx, len(times), elapsed_ms, thumb_w=thumb_w)
        rows.append({"t": ts, "faces": len(faces), "ms": elapsed_ms})

        if echo:
            desc = "  ".join(
                f"[{(f['bbox_norm'][0] + f['bbox_norm'][2]) / 2:.2f},"
                f"{(f['bbox_norm'][1] + f['bbox_norm'][3]) / 2:.2f}]"
                f"{' speaking' if f.get('is_speaking') else ''}"
                for f in faces
            )
            print(
                f"  frame {idx:>3}/{len(times)}  t={ts:5.2f}s  {len(faces)} face(s)  "
                f"{elapsed_ms:4.0f} ms  {desc}",
                flush=True,
            )
        if pause > 0.0:
            time.sleep(pause)

    cap.release()

    if echo and rows:
        avg_ms = sum(r["ms"] for r in rows) / len(rows)
        print(f"  done: {len(rows)} frames, average {avg_ms:.0f} ms/frame")

    if events is not None and hasattr(events, "emit"):
        faces_seen = sum(1 for r in rows if r["faces"] > 0)
        events.emit(
            "info",
            f"{clip_path.name}: scanned {len(rows)} frames, faces seen in {faces_seen}",
            clip=clip_path.name,
        )
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("clips", nargs="+", help="Video file(s) or folder(s) to scan")
    parser.add_argument("--step", type=float, default=0.5, help="Seconds between scanned frames")
    parser.add_argument("--max", type=int, default=60, help="Maximum frames scanned per clip")
    parser.add_argument("--project", default=None, help="Work project name to report events into")
    parser.add_argument("--pause", type=float, default=0.0, help="Delay in seconds after each frame")
    parser.add_argument("--thumb", type=int, default=THUMB_W, help="Thumbnail width in pixels")
    args = parser.parse_args()

    clip_paths: List[Path] = []
    for raw in args.clips:
        target = Path(raw)
        if target.is_dir():
            clip_paths.extend(
                sorted(
                    p
                    for p in target.iterdir()
                    if p.is_file() and p.suffix.lower() in VIDEO_EXT and not p.name.startswith(".")
                )
            )
        else:
            clip_paths.append(target)

    for clip_path in clip_paths:
        scan_clip(
            clip_path,
            step=args.step,
            max_frames=args.max,
            project=args.project,
            pause=args.pause,
            thumb_w=args.thumb,
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
