#!/usr/bin/env python3
"""One small image of every clip, so the Director can look at all the footage for about 2k tokens.

Three frames per clip (start, middle, end), labelled with the clip's name and duration. Sheets go to work/<project>/clip_sheet_<n>.jpg,
30 clips per sheet. A sheet shows what a transcript cannot: who is in a clip, silent clips, pre-stitched split screens, bad frames.
It says nothing about speech: cut times still come from the measured words.

    python3 scripts/clip_sheet.py <work/project> <clip> [<clip> ...]
"""
from __future__ import annotations

import math
import re
import sys
from pathlib import Path
from typing import List, Sequence

import cv2
import numpy as np

CLIPS_PER_SHEET = 30
FRAME_H = 170
FRAME_W = 96
LABEL_H = 22
MAX_SIDE = 1560        # keeps one sheet near 2k tokens


def _grab(path: str, spots: Sequence[float] = (0.12, 0.5, 0.88)) -> tuple:
    cap = cv2.VideoCapture(str(path))
    n = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    fps = float(cap.get(cv2.CAP_PROP_FPS) or 0) or 30.0
    frames = []
    for s in spots:
        cap.set(cv2.CAP_PROP_POS_FRAMES, max(0, int(n * s)))
        ok, f = cap.read()
        frames.append(f if ok else None)
    cap.release()
    return frames, n / fps


def _fit(frame, w: int, h: int):
    cell = np.zeros((h, w, 3), np.uint8)
    if frame is None:
        return cell
    fh, fw = frame.shape[:2]
    k = min(w / fw, h / fh)
    nw, nh = max(1, int(fw * k)), max(1, int(fh * k))
    small = cv2.resize(frame, (nw, nh), interpolation=cv2.INTER_AREA)
    x, y = (w - nw) // 2, (h - nh) // 2
    cell[y:y + nh, x:x + nw] = small
    return cell


def _tile(path: str, name: str) -> np.ndarray:
    frames, dur = _grab(path)
    strip = np.hstack([_fit(f, FRAME_W, FRAME_H) for f in frames])
    bar = np.full((LABEL_H, strip.shape[1], 3), 245, np.uint8)
    label = re.sub(r"[^\x20-\x7e]", "?", name)[:30] + f" {dur:.1f}s"
    cv2.putText(bar, label, (3, 16), cv2.FONT_HERSHEY_SIMPLEX, 0.38, (0, 0, 0), 1, cv2.LINE_AA)
    tile = np.vstack([bar, strip])
    return cv2.copyMakeBorder(tile, 2, 2, 2, 2, cv2.BORDER_CONSTANT, value=(120, 120, 120))


def build(out_dir: Path, clips: List[str]) -> List[Path]:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    for old in out_dir.glob("clip_sheet_*.jpg"):
        old.unlink()
    made: List[Path] = []
    for page in range(0, len(clips), CLIPS_PER_SHEET):
        group = clips[page:page + CLIPS_PER_SHEET]
        tiles = [_tile(c, Path(c).stem) for c in group]
        cols = max(1, min(int(math.ceil(math.sqrt(len(tiles) * 1.2))), 6))
        rows = []
        for r in range(0, len(tiles), cols):
            row = tiles[r:r + cols]
            while len(row) < cols:
                row.append(np.zeros_like(tiles[0]))
            rows.append(np.hstack(row))
        sheet = np.vstack(rows)
        k = MAX_SIDE / max(sheet.shape[:2])
        if k < 1.0:
            sheet = cv2.resize(sheet, None, fx=k, fy=k, interpolation=cv2.INTER_AREA)
        path = out_dir / f"clip_sheet_{len(made) + 1}.jpg"
        cv2.imwrite(str(path), sheet, [cv2.IMWRITE_JPEG_QUALITY, 72])
        made.append(path)
    return made


if __name__ == "__main__":
    for p in build(Path(sys.argv[1]), sys.argv[2:]):
        print(p)
