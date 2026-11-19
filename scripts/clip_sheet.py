#!/usr/bin/env python3
"""Compact Footage Contact Sheet Generator (`clip_sheet.py`).

Generates token-efficient visual contact sheets (`work/<project>/clip_sheet_<n>.jpg`)
with 3 frames per clip (start 12%, middle 50%, end 88%), labelled with clip name and
duration, up to 30 clips per sheet and bounded to `<= 1024px` on the longest side.

Usage:
    python3 scripts/clip_sheet.py <work/project> <clip> [<clip> ...]
"""
from __future__ import annotations

import argparse
import math
import re
import sys
from pathlib import Path
from typing import List, Optional, Sequence, Tuple, Union

import cv2
import numpy as np

CLIPS_PER_SHEET: int = 30
FRAME_H: int = 170
FRAME_W: int = 96
LABEL_H: int = 22
MAX_SIDE: int = 1024  # Caps each sheet to <= 2x2 512px vision tiles


def _grab(
    path: Union[str, Path],
    spots: Sequence[float] = (0.12, 0.50, 0.88),
) -> Tuple[List[Optional[np.ndarray]], float]:
    """Decode frames at normalized `spots` across `path` and return `(frames, duration_sec)`."""
    cap = cv2.VideoCapture(str(path))
    n_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    fps = float(cap.get(cv2.CAP_PROP_FPS) or 0.0) or 30.0
    frames: List[Optional[np.ndarray]] = []
    for spot in spots:
        if n_frames > 0:
            cap.set(cv2.CAP_PROP_POS_FRAMES, min(n_frames - 1, max(0, int(n_frames * spot))))
        ok, frame = cap.read()
        frames.append(frame if (ok and frame is not None and frame.size > 0) else None)
    cap.release()
    return frames, (n_frames / fps if fps > 0 else 0.0)


def _fit(frame: Optional[np.ndarray], w: int, h: int) -> np.ndarray:
    """Letterbox/pillarbox `frame` into a `w x h` BGR cell while preserving aspect ratio."""
    cell = np.zeros((h, w, 3), dtype=np.uint8)
    if frame is None or frame.size == 0:
        return cell
    fh, fw = frame.shape[:2]
    if fh <= 0 or fw <= 0:
        return cell
    scale = min(w / float(fw), h / float(fh))
    nw = max(1, int(fw * scale))
    nh = max(1, int(fh * scale))
    resized = cv2.resize(frame, (nw, nh), interpolation=cv2.INTER_AREA)
    x0 = (w - nw) // 2
    y0 = (h - nh) // 2
    cell[y0 : y0 + nh, x0 : x0 + nw] = resized
    return cell


def _tile(path: Union[str, Path], name: str) -> np.ndarray:
    """Render one 3-frame contact tile with a light header strip showing clip stem and duration."""
    frames, dur = _grab(path)
    strip = np.hstack([_fit(f, FRAME_W, FRAME_H) for f in frames])
    bar = np.full((LABEL_H, strip.shape[1], 3), 245, dtype=np.uint8)
    ascii_name = re.sub(r"[^\x20-\x7e]", "?", str(name))[:30]
    label = f"{ascii_name} {dur:.1f}s"
    cv2.putText(
        bar,
        label,
        (3, 16),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.38,
        (0, 0, 0),
        1,
        cv2.LINE_AA,
    )
    tile = np.vstack([bar, strip])
    return cv2.copyMakeBorder(tile, 2, 2, 2, 2, cv2.BORDER_CONSTANT, value=(120, 120, 120))


def build(
    out_dir: Union[str, Path],
    clips: Sequence[Union[str, Path]],
) -> List[Path]:
    """Build `clip_sheet_1.jpg`, `clip_sheet_2.jpg`, ... inside `out_dir` for `clips`."""
    target_dir = Path(out_dir)
    target_dir.mkdir(parents=True, exist_ok=True)
    for stale in target_dir.glob("clip_sheet_*.jpg"):
        try:
            stale.unlink()
        except OSError:
            pass

    clip_list = [str(c) for c in clips]
    if not clip_list:
        return []

    made: List[Path] = []
    for page_start in range(0, len(clip_list), CLIPS_PER_SHEET):
        group = clip_list[page_start : page_start + CLIPS_PER_SHEET]
        tiles = [_tile(c, Path(c).stem) for c in group]
        cols = max(1, min(int(math.ceil(math.sqrt(len(tiles) * 1.2))), 6))
        rows: List[np.ndarray] = []
        for r_idx in range(0, len(tiles), cols):
            row_tiles = list(tiles[r_idx : r_idx + cols])
            while len(row_tiles) < cols:
                row_tiles.append(np.zeros_like(tiles[0]))
            rows.append(np.hstack(row_tiles))

        sheet = np.vstack(rows)
        sh_h, sh_w = sheet.shape[:2]
        scale = MAX_SIDE / float(max(sh_h, sh_w))
        if scale < 1.0:
            nw = max(1, min(MAX_SIDE, int(round(sh_w * scale))))
            nh = max(1, min(MAX_SIDE, int(round(sh_h * scale))))
            sheet = cv2.resize(sheet, (nw, nh), interpolation=cv2.INTER_AREA)

        sheet_path = target_dir / f"clip_sheet_{len(made) + 1}.jpg"
        cv2.imwrite(str(sheet_path), sheet, [int(cv2.IMWRITE_JPEG_QUALITY), 72])
        made.append(sheet_path)

    return made


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Build 3-frame-per-clip contact sheets (`clip_sheet_<n>.jpg`)."
    )
    parser.add_argument("out_dir", help="Output project directory")
    parser.add_argument("clips", nargs="*", help="Source video clip paths")
    args = parser.parse_args(argv)

    for sheet_path in build(Path(args.out_dir), args.clips):
        print(sheet_path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
