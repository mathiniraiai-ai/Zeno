#!/usr/bin/env python3
"""Picture-movement analysis: finds where something happens in a clip that has little or no speech.

    python3 scripts/motion.py <video> [--window 2.0]

Prints the movement per 0.25 s as a compact strip and the most active window. Used by prepare.py to place silent
story beats (a hand-over, a laugh, crying, an entrance) when the script describes a moment with no dialogue.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import List, Optional, Tuple

STEP = 0.25


def motion_curve(video: Path, step: float = STEP) -> List[Tuple[float, float]]:
    """[(time, movement)] with movement = mean absolute change of a small grayscale frame between samples (0-255)."""
    import cv2
    import numpy as np

    cap = cv2.VideoCapture(str(video))
    if not cap.isOpened():
        return []
    fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
    total = cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0
    dur = total / fps if total else 0.0
    out: List[Tuple[float, float]] = []
    prev = None
    t = 0.0
    while t < max(dur, step):
        cap.set(cv2.CAP_PROP_POS_MSEC, t * 1000.0)
        ok, frame = cap.read()
        if not ok:
            break
        g = cv2.resize(cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY), (64, 64)).astype("float32")
        if prev is not None:
            out.append((round(t, 2), float(np.mean(np.abs(g - prev)))))
        prev = g
        t += step
    cap.release()
    return out


def best_window(curve: List[Tuple[float, float]], length: float, duration: float, prefer_late: bool = True) -> Tuple[float, float]:
    """Start and end (seconds) of the most active stretch of the given length. Ties go to the later stretch
    (the strongest moment is usually just before the next line)."""
    if not curve or duration <= length + 0.3:
        return 0.0, round(duration, 2)
    n = max(1, int(round(length / STEP)))
    best_i, best_v = 0, -1.0
    for i in range(0, max(1, len(curve) - n + 1)):
        v = sum(m for _, m in curve[i:i + n]) / n
        if v > best_v + 1e-9 or (prefer_late and abs(v - best_v) <= 1e-9):
            best_i, best_v = i, v
    start = max(0.0, curve[best_i][0] - STEP)          # include the frame just before the movement starts
    return round(start, 2), round(min(duration, start + length), 2)


def describe(video: Path, length: float = 2.0) -> str:
    curve = motion_curve(video)
    if not curve:
        return f"{video.name}: could not read frames"
    peak = max(m for _, m in curve) or 1.0
    bars = "".join(" .:-=+*#%@"[min(9, int(9 * m / peak))] for _, m in curve)
    dur = curve[-1][0] + STEP
    s, e = best_window(curve, length, dur)
    return f"{video.name}  {dur:.1f}s  movement per 0.25s: [{bars}]  most active {length:g}s: {s:.2f}-{e:.2f}s"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("video", nargs="+")
    ap.add_argument("--window", type=float, default=2.0)
    a = ap.parse_args()
    for v in a.video:
        print(describe(Path(v), a.window))


if __name__ == "__main__":
    main()
