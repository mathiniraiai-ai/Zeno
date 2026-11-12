"""Scrolling-text page format (`#page scroll` in plan.edl): the two characters sit side by side in the top half, and the
lower half is a white page on which the whole dialogue scrolls up as it is spoken. A box highlights the word being spoken,
wrong phrases are red, right phrases are green, and a thin progress line runs across the very top.

This file only measures and draws: `geometry` says where the three areas sit, `build_paragraphs` turns the spoken words of the
finished timeline into paragraphs, and `build_page_video` writes the video of the progress strip plus the page. `render.py`
renders the edit into the picture area and stacks the three areas.
"""
from __future__ import annotations

import json
import re
import subprocess
import unicodedata
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import subtitles
from common import FFMPEG

SUB_DIR = Path(__file__).resolve().parent.parent / "subtitles"

DEFAULTS: Dict[str, Any] = {
    "bg": "#FFFFFF",            # the page
    "color": "#2B2B2B",         # ordinary words
    "wrong": "#D0021B",         # wrong phrases (bold)
    "right": "#16A34A",         # right phrases (bold)
    "highlight": "#8C9BFF",     # the box behind the word being spoken
    "accent": "#3B5BDB",        # progress line and dot
    "size": 56,                 # text size on a 1080-wide picture
    "bar": True,                # progress strip at the top
    "dash": True,               # every speaker turn starts with a dash
}
BAR_FRAC = 0.046                # progress strip height / full height
VIDEO_FRAC = 0.454              # picture (the two characters) height / full height


def even(v: float) -> int:
    return int(round(v / 2.0)) * 2


def page_config(plan: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """The plan's page settings merged over the defaults, or None when the plan has no `#page`."""
    raw = plan.get("page")
    if not raw:
        return None
    cfg = dict(DEFAULTS)
    if isinstance(raw, dict):
        cfg.update({k: v for k, v in raw.items() if v is not None})
    cfg["wrong_phrases"] = list(cfg.get("wrong_phrases") or [])
    cfg["right_phrases"] = list(cfg.get("right_phrases") or [])
    return cfg


def geometry(width: int, height: int, cfg: Dict[str, Any]) -> Dict[str, int]:
    """Heights of the three stacked areas of the full picture: progress strip, picture, page."""
    bar_h = even(height * BAR_FRAC) if cfg.get("bar", True) else 0
    video_h = even(height * VIDEO_FRAC) + (0 if bar_h else even(height * BAR_FRAC))
    return {"bar_h": bar_h, "video_h": video_h, "page_h": height - bar_h - video_h, "width": width, "height": height}


def _rgb(hexcol: str) -> Tuple[int, int, int]:
    h = str(hexcol).strip().lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    try:
        return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    except (ValueError, IndexError):
        return 255, 255, 255


def _norm(word: str) -> str:
    """A word reduced to its letters and digits (case-folded), so phrases match whatever the punctuation."""
    return "".join(ch for ch in unicodedata.normalize("NFC", str(word)).casefold()
                   if unicodedata.category(ch)[0] in ("L", "N", "M"))


def build_paragraphs(
    cues: Sequence[Dict[str, Any]],
    segs: Sequence[Dict[str, Any]],
    cfg: Dict[str, Any],
) -> List[Dict[str, Any]]:
    """One paragraph per speaker turn (the cuts of one script line stay together), every word with its time on the
    finished timeline. Words inside a wrong / right phrase get that colour and are bold."""
    paras: List[Dict[str, Any]] = []
    last_key: Optional[str] = None
    for cue in cues:
        idx = cue.get("segIndex")
        seg = segs[idx] if isinstance(idx, int) and 0 <= idx < len(segs) else {}
        key = subtitles._base_line(str(seg.get("id") or "")) if seg else None
        key = f"{key}|{seg.get('speaker') or ''}" if key else f"cue{len(paras)}"
        if key != last_key or not paras:
            paras.append({"words": [], "speaker": seg.get("speaker") or ""})
            last_key = key
        for w in cue.get("words") or []:
            paras[-1]["words"].append({"text": str(w["text"]), "start": float(w["startTime"]), "end": float(w["endTime"])})

    def _mark(phrases: Sequence[str], colour: str) -> None:
        for phrase in phrases:
            want = [n for n in (_norm(p) for p in str(phrase).split()) if n]
            if not want:
                continue
            for para in paras:
                got = [_norm(w["text"]) for w in para["words"]]
                for i in range(0, len(got) - len(want) + 1):
                    if got[i:i + len(want)] == want:
                        for w in para["words"][i:i + len(want)]:
                            w["color"], w["bold"] = colour, True

    _mark(cfg["wrong_phrases"], cfg["wrong"])
    _mark(cfg["right_phrases"], cfg["right"])
    return [p for p in paras if p["words"]]


def render_page_image(paras: List[Dict[str, Any]], width: int, cfg: Dict[str, Any], out_dir: Path) -> Dict[str, Any]:
    """Draw the whole dialogue as one tall transparent PNG (Node, so Indic scripts are shaped correctly) and return the
    layout: `{"png", "height", "rowHeight", "words": [{p, i, x, y, w, h, row}]}`."""
    out_dir.mkdir(parents=True, exist_ok=True)
    scale = max(0.4, width / 1080.0)
    job = {
        "width": width,
        "fontSize": int(round(float(cfg.get("size", 56)) * scale)),
        "padX": int(round(54 * scale)),
        "padTop": int(round(56 * scale)),
        "padBottom": int(round(160 * scale)),
        "color": cfg["color"],
        "dash": bool(cfg.get("dash", True)),
        "out": str(out_dir / "page.png"),
        "paragraphs": [{"words": [{"text": w["text"], **({"color": w["color"], "bold": True} if w.get("bold") else {})}
                                  for w in p["words"]]} for p in paras],
    }
    jf = out_dir / "page_job.json"
    jf.write_text(json.dumps(job, ensure_ascii=False), encoding="utf-8")
    r = subprocess.run([subtitles.node_path(), str(SUB_DIR / "render_page.js"), str(jf)],
                       capture_output=True, text=True, encoding="utf-8", timeout=600)
    if r.returncode != 0:
        raise RuntimeError(f"page renderer failed: {(r.stderr or r.stdout).strip()[-400:]}")
    layout = json.loads([l for l in r.stdout.splitlines() if l.startswith("{")][-1])
    layout["png"] = job["out"]
    return layout


def scroll_positions(
    paras: List[Dict[str, Any]],
    layout: Dict[str, Any],
    page_h: int,
    fps: int,
    total: float,
) -> Tuple[List[float], List[int]]:
    """For every output frame: how far the page has scrolled (pixels) and which word is highlighted (-1 = none).
    The row being spoken is held about a third of the way down the page; the page glides, it never jumps."""
    boxes = {(b["p"], b["i"]): b for b in layout["words"]}
    flat: List[Tuple[float, float, Dict[str, Any], int]] = []
    for p, para in enumerate(paras):
        for i, w in enumerate(para["words"]):
            b = boxes.get((p, i))
            if b is not None:
                flat.append((w["start"], w["end"], b, len(flat)))
    n_frames = max(1, int(round(total * fps)))
    anchor = page_h * 0.34
    max_scroll = max(0.0, float(layout["height"]) - page_h)
    scrolls: List[float] = []
    active: List[int] = []
    cur = 0.0
    k = -1
    for f in range(n_frames):
        t = f / float(fps)
        while k + 1 < len(flat) and flat[k + 1][0] <= t + 1e-6:
            k += 1
        target = cur
        if k >= 0:
            row_mid = flat[k][2]["y"] + flat[k][2]["h"] / 2.0
            target = min(max_scroll, max(0.0, row_mid - anchor))
        cur += (target - cur) * 0.085            # glide towards the row being spoken
        scrolls.append(cur)
        # the box stays on a word until the next one starts, but lets go in a pause longer than 0.7 s
        on = k >= 0 and (t <= flat[k][1] + 0.70 or (k + 1 < len(flat) and flat[k + 1][0] - flat[k][1] <= 0.70))
        active.append(k if on else -1)
    return scrolls, active


def build_page_video(
    paras: List[Dict[str, Any]],
    cfg: Dict[str, Any],
    geo: Dict[str, int],
    fps: int,
    total: float,
    out_dir: Path,
    draft: bool = False,
) -> Path:
    """Write `page.mkv`: the progress strip (top `bar_h` rows) stacked on the scrolling page (`page_h` rows)."""
    import cv2
    import numpy as np

    W, bar_h, page_h = geo["width"], geo["bar_h"], geo["page_h"]
    layout = render_page_image(paras, W, cfg, out_dir)
    png = cv2.imread(layout["png"], cv2.IMREAD_UNCHANGED)          # BGRA, text on transparent
    if png is None or png.shape[2] < 4:
        raise RuntimeError("page picture could not be read")
    pad = np.zeros((page_h, W, 4), dtype=np.uint8)                 # room to scroll past the last row
    png = np.vstack([png, pad])
    alpha = png[:, :, 3:4].astype(np.uint16)
    premult = (png[:, :, :3].astype(np.uint16) * alpha) // 255     # text colour x alpha
    inv = 255 - alpha

    bg = np.array(_rgb(cfg["bg"])[::-1], dtype=np.uint16)
    hi = tuple(int(v) for v in _rgb(cfg["highlight"])[::-1])
    accent = tuple(int(v) for v in _rgb(cfg["accent"])[::-1])
    track = tuple(int(0.80 * v + 0.20 * a) for v, a in zip(_rgb(cfg["bg"])[::-1], (120, 120, 120)))

    scrolls, active = scroll_positions(paras, layout, page_h, fps, total)
    flat_boxes = [b for b in sorted(layout["words"], key=lambda b: (b["p"], b["i"]))]
    fade_top, fade_bot = int(page_h * 0.045), int(page_h * 0.13)
    ramp = np.ones((page_h, 1, 1), dtype=np.float32)               # 1 = text fully visible, 0 = page colour
    ramp[:fade_top, 0, 0] = np.linspace(0.0, 1.0, fade_top)
    ramp[page_h - fade_bot:, 0, 0] = np.linspace(1.0, 0.0, fade_bot)
    ramp16 = (ramp * 256).astype(np.uint16)

    out = out_dir / "page.mkv"
    cmd = [str(FFMPEG), "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "bgr24", "-s", f"{W}x{bar_h + page_h}", "-r", str(fps),
           "-i", "-", "-c:v", "libx264", "-preset", "ultrafast" if draft else "veryfast", "-crf", "20" if draft else "14",
           "-pix_fmt", "yuv420p", str(out)]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=subprocess.PIPE)
    n_frames = len(scrolls)
    x0, x1 = int(W * 0.115), int(W * 0.885)
    try:
        for f in range(n_frames):
            s = int(round(scrolls[f]))
            page = np.empty((page_h, W, 3), dtype=np.uint16)
            page[:] = bg
            page8 = page.astype(np.uint8)
            k = active[f]
            if k >= 0:
                b = flat_boxes[k]
                by = b["y"] - s
                padx, pady = int(b["h"] * 0.10), int(b["h"] * 0.16)
                cv2.rectangle(page8, (b["x"] - padx, by + pady), (b["x"] + b["w"] + padx, by + b["h"] - pady), hi, -1, cv2.LINE_AA)
            page = page8.astype(np.uint16)
            page = (premult[s:s + page_h] + (page * inv[s:s + page_h]) // 255)        # the text over page and box
            page = (bg + ((page.astype(np.int32) - bg) * ramp16) // 256).astype(np.uint8)   # soft top and bottom edges
            if bar_h:
                bar = np.empty((bar_h, W, 3), dtype=np.uint8)
                bar[:] = bg.astype(np.uint8)
                cy = bar_h // 2
                prog = x0 + int((x1 - x0) * min(1.0, f / max(1.0, n_frames - 1.0)))
                cv2.line(bar, (x0, cy), (x1, cy), track, max(2, bar_h // 22), cv2.LINE_AA)
                cv2.line(bar, (x0, cy), (prog, cy), accent, max(3, bar_h // 16), cv2.LINE_AA)
                cv2.circle(bar, (prog, cy), max(5, bar_h // 9), accent, -1, cv2.LINE_AA)
                ch = max(8, bar_h // 6)                                                 # the back chevron
                cx = int(W * 0.055)
                cv2.polylines(bar, [np.array([[cx + ch // 2, cy - ch], [cx - ch // 2, cy], [cx + ch // 2, cy + ch]], np.int32)],
                              False, accent, max(3, bar_h // 18), cv2.LINE_AA)
                frame = np.vstack([bar, page])
            else:
                frame = page
            proc.stdin.write(frame.tobytes())
    finally:
        if proc.stdin:
            proc.stdin.close()
        err = proc.stderr.read().decode("utf-8", "ignore") if proc.stderr else ""
        proc.wait()
    if proc.returncode != 0 or not out.is_file():
        raise RuntimeError(f"page video could not be written: {err[-300:]}")
    (out_dir / "page_meta.json").write_text(json.dumps({
        "frames": n_frames, "rows": max((b["row"] for b in layout["words"]), default=0) + 1,
        "page_height": layout["height"], "words": len(layout["words"]), "geometry": geo,
    }), encoding="utf-8")
    return out


def build(
    plan: Dict[str, Any],
    segs: List[Dict[str, Any]],
    starts: List[float],
    total: float,
    full_w: int,
    full_h: int,
    fps: int,
    work_dir: Path,
    report_dir: Optional[Path] = None,
    srt_path: Optional[Path] = None,
    draft: bool = False,
) -> Optional[Tuple[Path, Dict[str, int]]]:
    """Everything `render.py` needs for a `#page scroll` plan: `(page video, geometry)`, or None when there is nothing to show."""
    cfg = page_config(plan)
    if cfg is None:
        return None
    if not subtitles.ensure_ready():
        print("  [PAGE] Node.js or its caption package is missing, so the scrolling page cannot be drawn.")
        return None
    script_lines: List[Dict[str, Any]] = []
    try:
        from prepare import parse_script_file
        from common import resolve

        if plan.get("script"):
            _, script_lines = parse_script_file(resolve(plan["script"]))
    except Exception:   # noqa: BLE001
        script_lines = []
    base = Path(report_dir) if report_dir is not None else Path(work_dir)
    cache_hint = base if (base / "clip_caches.json").is_file() else Path(work_dir)
    cues = subtitles.build_cues(segs, starts, script_lines, work_dir=cache_hint)
    if not cues:
        print("  [PAGE] no spoken words to show.")
        return None
    if srt_path is not None:
        subtitles.write_srt(cues, srt_path)
    paras = build_paragraphs(cues, segs, cfg)
    geo = geometry(full_w, full_h, cfg)
    out_dir = base / "_page"
    video = build_page_video(paras, cfg, geo, fps, total, out_dir, draft=draft)
    print(f"  [PAGE] scrolling page: {len(paras)} turns, {sum(len(p['words']) for p in paras)} words; "
          f"{len(cfg['wrong_phrases'])} wrong and {len(cfg['right_phrases'])} right phrases coloured.")
    return video, geo
