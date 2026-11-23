"""Scrolling-text page renderer (`#page scroll` in `plan.edl`).

In the `#page scroll` layout, the speakers appear in the top video area above an accent divider
line, while the lower half displays a white scrolling page of the full dialogue. A highlight box
tracks the active word being spoken, incorrect phrases are rendered in bold red, correct phrases
are rendered in bold green, and an optional progress bar can appear at the top edge.

`geometry` computes the stacked region heights, `build_paragraphs` groups timeline cues into
speaker turns, and `build_page_video` renders the scrolling page stream (`page.mkv`) that
`render.py` stacks beneath the video track.
"""
from __future__ import annotations

import json
import subprocess
import sys
import unicodedata
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import subtitles
from common import FFMPEG

SUB_DIR = _HERE.parent / "subtitles"

DEFAULTS: Dict[str, Any] = {
    "bg": "#FFFFFF",            # Page background color
    "color": "#111111",         # Default dialogue text color
    "wrong": "#D0021B",         # Color for incorrect phrases (bold) when listed in the plan
    "right": "#16A34A",         # Color for correct phrases (bold) when listed in the plan
    "highlight": "#A9C1FF",     # Box color behind the currently spoken word
    "accent": "#1F4DA1",        # Divider line below the video (and optional progress bar accent)
    "size": 44,                 # Base font size in pixels on a 1080px-wide canvas
    "bar": False,               # Optional top progress bar (`bar=on`)
    "dash": False,              # Optional em-dash prefix on each speaker turn (`dash=on`)
    "fade": False,              # Optional soft fade on top/bottom edges of the page (`fade=on`)
}
BAR_FRAC = 0.046                # Optional top progress strip height / full canvas height
VIDEO_FRAC = 0.4854             # Video region height (including optional bar) / full canvas height
DIVIDER_FRAC = 0.0104           # Divider line height / full canvas height


def even(v: float) -> int:
    """Round `v` to the nearest even integer for YUV420-safe video dimensions."""
    return int(round(v / 2.0)) * 2


def page_config(plan: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Merge the plan's `#page` configuration over `DEFAULTS`, or return None when `#page` is absent."""
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
    """Compute even pixel heights for the stacked layout regions: `bar_h`, `video_h`, `divider_h`, `page_h`, and `below_h`."""
    bar_h = even(height * BAR_FRAC) if cfg.get("bar") else 0
    video_h = even(height * VIDEO_FRAC) - bar_h
    divider_h = even(height * DIVIDER_FRAC)
    page_h = height - bar_h - video_h - divider_h
    return {
        "bar_h": bar_h,
        "video_h": video_h,
        "divider_h": divider_h,
        "page_h": page_h,
        "below_h": divider_h + page_h,
        "width": width,
        "height": height,
    }


def _rgb(hexcol: str) -> Tuple[int, int, int]:
    h = str(hexcol).strip().lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    try:
        return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    except (ValueError, IndexError):
        return 255, 255, 255


def _norm(word: str) -> str:
    """Normalize a word to NFC case-folded letters, numbers, and combining marks for punctuation-agnostic phrase matching."""
    return "".join(
        ch for ch in unicodedata.normalize("NFC", str(word)).casefold()
        if unicodedata.category(ch)[0] in ("L", "N", "M")
    )


def build_paragraphs(
    cues: Sequence[Dict[str, Any]],
    segs: Sequence[Dict[str, Any]],
    cfg: Dict[str, Any],
) -> List[Dict[str, Any]]:
    """Group timeline cues into one paragraph per speaker turn (merging cuts of the same base script line),
    and mark `wrong_phrases` / `right_phrases` with bold styling and their configured colors."""
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
            text = str(w["text"]).strip()
            if not text:
                continue
            # Standalone punctuation tokens (e.g. "." or ",") attach to the preceding word
            if not any(ch.isalnum() for ch in text) and paras[-1]["words"]:
                prev = paras[-1]["words"][-1]
                prev["text"] = prev["text"].rstrip() + text
                prev["end"] = max(prev["end"], float(w["endTime"]))
                continue
            paras[-1]["words"].append({
                "text": text,
                "start": float(w["startTime"]),
                "end": float(w["endTime"]),
            })

    def _mark(phrases: Sequence[str], color: str) -> None:
        for phrase in phrases:
            want = [n for n in (_norm(p) for p in str(phrase).split()) if n]
            if not want:
                continue
            for para in paras:
                got = [_norm(w["text"]) for w in para["words"]]
                for i in range(0, len(got) - len(want) + 1):
                    if got[i:i + len(want)] == want:
                        for w in para["words"][i:i + len(want)]:
                            w["color"], w["bold"] = color, True

    _mark(cfg["wrong_phrases"], cfg["wrong"])
    _mark(cfg["right_phrases"], cfg["right"])
    return [p for p in paras if p["words"]]


def render_page_image(paras: List[Dict[str, Any]], width: int, cfg: Dict[str, Any], out_dir: Path) -> Dict[str, Any]:
    """Render the full dialogue into a single tall transparent PNG via `subtitles/render_page.js` (preserving Indic script shaping)
    and return the word bounding-box layout `{"png", "height", "rowHeight", "words": [{p, i, x, y, w, h, row}]}`."""
    out_dir.mkdir(parents=True, exist_ok=True)
    scale = max(0.4, width / 1080.0)
    job = {
        "width": width,
        "fontSize": int(round(float(cfg.get("size", 44)) * scale)),
        "lineHeight": 1.32,
        "paraGap": 1.0,
        "padLeft": int(round(216 * scale)),
        "padRight": int(round(48 * scale)),
        "padTop": int(round(34 * scale)),
        "padBottom": int(round(160 * scale)),
        "fontWeight": 500,
        "color": cfg["color"],
        "dash": bool(cfg.get("dash", True)),
        "out": str(out_dir / "page.png"),
        "paragraphs": [
            {
                "words": [
                    {"text": w["text"], **({"color": w["color"], "bold": True} if w.get("bold") else {})}
                    for w in p["words"]
                ]
            }
            for p in paras
        ],
    }
    if cfg.get("font"):
        resolved_font = subtitles.resolve_font(cfg["font"]) or str(cfg["font"]).strip()
        if resolved_font:
            job["fontFamily"] = resolved_font
    jf = out_dir / "page_job.json"
    jf.write_text(json.dumps(job, ensure_ascii=False), encoding="utf-8")
    r = subprocess.run(
        [subtitles.node_path(), str(SUB_DIR / "render_page.js"), str(jf)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=600,
    )
    if r.returncode != 0:
        raise RuntimeError(f"page renderer failed: {(r.stderr or r.stdout).strip()[-400:]}")
    pango_warns = subtitles.check_pango_warnings(r.stderr)
    if pango_warns:
        print(f"  [PAGE WARN] Pango font warning(s): {pango_warns[0]}")
        tofu_res = subtitles.detect_tofu_boxes_in_png(job["out"])
        if tofu_res.get("tofu_count", 0) > 0:
            raise RuntimeError(
                f"page renderer hit font load failure ({pango_warns[0]}) and produced "
                f"{tofu_res['tofu_count']} tofu box(es) in {job['out']}"
            )
    layout = json.loads([ln for ln in r.stdout.splitlines() if ln.startswith("{")][-1])
    layout["png"] = job["out"]
    if pango_warns:
        layout["pango_warnings"] = pango_warns
    return layout


def scroll_positions(
    paras: List[Dict[str, Any]],
    layout: Dict[str, Any],
    page_h: int,
    fps: int,
    total: float,
) -> Tuple[List[float], List[int]]:
    """Compute per-frame vertical scroll offsets (pixels) and active word indices (`-1` when idle).
    Smoothly glides toward holding the spoken row ~34% down the page and releases highlights during pauses > 0.70s."""
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
        cur += (target - cur) * 0.085
        scrolls.append(cur)
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
    """Encode `page.mkv` containing the optional top progress strip (`bar_h`) stacked above the divider and scrolling page (`below_h`)."""
    import cv2
    import numpy as np

    w_px, bar_h, page_h, div_h = geo["width"], geo["bar_h"], geo["page_h"], geo.get("divider_h", 0)
    layout = render_page_image(paras, w_px, cfg, out_dir)
    png = cv2.imread(layout["png"], cv2.IMREAD_UNCHANGED)
    if png is None or png.shape[2] < 4:
        raise RuntimeError("Rendered page PNG could not be read.")
    pad = np.zeros((page_h, w_px, 4), dtype=np.uint8)
    png = np.vstack([png, pad])
    alpha = png[:, :, 3:4].astype(np.uint16)
    premult = (png[:, :, :3].astype(np.uint16) * alpha) // 255
    inv = 255 - alpha

    bg = np.array(_rgb(cfg["bg"])[::-1], dtype=np.uint16)
    hi = tuple(int(v) for v in _rgb(cfg["highlight"])[::-1])
    accent = tuple(int(v) for v in _rgb(cfg["accent"])[::-1])
    track = tuple(int(0.80 * v + 0.20 * a) for v, a in zip(_rgb(cfg["bg"])[::-1], (120, 120, 120)))

    scrolls, active = scroll_positions(paras, layout, page_h, fps, total)
    flat_boxes = [b for b in sorted(layout["words"], key=lambda b: (b["p"], b["i"]))]
    ramp16 = None
    if cfg.get("fade"):
        fade_top, fade_bot = int(page_h * 0.045), int(page_h * 0.13)
        ramp = np.ones((page_h, 1, 1), dtype=np.float32)
        ramp[:fade_top, 0, 0] = np.linspace(0.0, 1.0, fade_top)
        ramp[page_h - fade_bot:, 0, 0] = np.linspace(1.0, 0.0, fade_bot)
        ramp16 = (ramp * 256).astype(np.uint16)
    divider = np.empty((div_h, w_px, 3), dtype=np.uint8)
    divider[:] = np.array(accent, dtype=np.uint8)

    out = out_dir / "page.mkv"
    cmd = [
        str(FFMPEG), "-v", "error", "-y",
        "-f", "rawvideo", "-pix_fmt", "bgr24",
        "-s", f"{w_px}x{bar_h + div_h + page_h}", "-r", str(fps),
        "-i", "-",
        "-c:v", "libx264",
        "-preset", "ultrafast" if draft else "veryfast",
        "-crf", "20" if draft else "14",
        "-pix_fmt", "yuv420p",
        str(out),
    ]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=subprocess.PIPE)
    n_frames = len(scrolls)
    x0, x1 = int(w_px * 0.115), int(w_px * 0.885)
    try:
        for f in range(n_frames):
            s = int(round(scrolls[f]))
            page = np.empty((page_h, w_px, 3), dtype=np.uint16)
            page[:] = bg
            page8 = page.astype(np.uint8)
            k = active[f]
            if k >= 0:
                b = flat_boxes[k]
                by = b["y"] - s
                padx, pady = int(b["h"] * 0.12), int(b["h"] * 0.06)
                cv2.rectangle(page8, (b["x"] - padx, by + pady), (b["x"] + b["w"] + padx, by + b["h"] - pady), hi, -1, cv2.LINE_AA)
            page = page8.astype(np.uint16)
            page = premult[s:s + page_h] + (page * inv[s:s + page_h]) // 255
            if ramp16 is not None:
                page = bg + ((page.astype(np.int32) - bg) * ramp16) // 256
            page = page.astype(np.uint8)
            if div_h:
                page = np.vstack([divider, page])
            if bar_h:
                bar = np.empty((bar_h, w_px, 3), dtype=np.uint8)
                bar[:] = bg.astype(np.uint8)
                cy = bar_h // 2
                prog = x0 + int((x1 - x0) * min(1.0, f / max(1.0, n_frames - 1.0)))
                cv2.line(bar, (x0, cy), (x1, cy), track, max(2, bar_h // 22), cv2.LINE_AA)
                cv2.line(bar, (x0, cy), (prog, cy), accent, max(3, bar_h // 16), cv2.LINE_AA)
                cv2.circle(bar, (prog, cy), max(5, bar_h // 9), accent, -1, cv2.LINE_AA)
                ch = max(8, bar_h // 6)
                cx = int(w_px * 0.055)
                chevron = np.array([[cx + ch // 2, cy - ch], [cx - ch // 2, cy], [cx + ch // 2, cy + ch]], np.int32)
                cv2.polylines(bar, [chevron], False, accent, max(3, bar_h // 18), cv2.LINE_AA)
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
        raise RuntimeError(f"Scrolling page video encode failed: {err[-300:]}")
    (out_dir / "page_meta.json").write_text(
        json.dumps({
            "frames": n_frames,
            "rows": max((b["row"] for b in layout["words"]), default=0) + 1,
            "page_height": layout["height"],
            "words": len(layout["words"]),
            "geometry": geo,
            **({"pango_warnings": layout["pango_warnings"]} if layout.get("pango_warnings") else {}),
        }),
        encoding="utf-8",
    )
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
    """Build `(page_video_path, geometry)` for a `#page scroll` plan, or return None if `#page` is disabled or empty."""
    cfg = page_config(plan)
    if cfg is None:
        return None
    if not subtitles.ensure_ready():
        print("  [PAGE] Node.js or node-canvas is unavailable; cannot render scrolling page.")
        return None

    script_lines: List[Dict[str, Any]] = []
    try:
        from prepare import parse_script_file
        from common import resolve

        if plan.get("script"):
            _, script_lines = parse_script_file(resolve(plan["script"]))
    except Exception:  # noqa: BLE001
        script_lines = []

    base = Path(report_dir) if report_dir is not None else Path(work_dir)
    cache_hint = base if (base / "clip_caches.json").is_file() else Path(work_dir)
    cues = subtitles.build_cues(segs, starts, script_lines, work_dir=cache_hint)
    if not cues:
        print("  [PAGE] No spoken words found for scrolling page.")
        return None
    if srt_path is not None:
        subtitles.write_srt(cues, srt_path)

    paras = build_paragraphs(cues, segs, cfg)
    geo = geometry(full_w, full_h, cfg)
    out_dir = base / "_page"
    video = build_page_video(paras, cfg, geo, fps, total, out_dir, draft=draft)
    print(
        f"  [PAGE] Scrolling page rendered: {len(paras)} turns, "
        f"{sum(len(p['words']) for p in paras)} words; "
        f"{len(cfg['wrong_phrases'])} wrong and {len(cfg['right_phrases'])} right phrases highlighted."
    )
    return video, geo
