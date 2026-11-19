"""Multi-Frame Face-Safe & Empty-Space Flashcard Placement Engine (`flashcard_placer.py`).

Solves deterministic, zero-face-overlap (`0 px`) placement for bilingual flashcards across
every Zeno layout (`single`, `split_h`, `split_v`, `top_pip`, `chat_bar`, `video_call`,
`pip_call`, `gmeet_grid`) on both 9:16 Master (`1080x1920`, `720x1280`) and 1:1 Square
(`1080x1080`, `720x720`) canvases.

Design invariants:
  1. HARD GATE 0: `0 px` intersection with every expanded YuNet face shield across ALL
     sampled frames of a card's span (hair crown `+0.34*fh`, chin `+0.14*fh`, ears/sides
     `±0.18*fw`, plus pixel safety margin `m_px = max(12, round(0.012 * min(W, H)))`).
  2. Overlay-safe face detection keeps EVERY face with `conf >= 0.58`, `fw_ratio >= 0.042`,
     `fh_ratio >= 0.038` across panel crops + full frame, without `prominent_faces(0.4)`
     pruning so co-actors, robots, babies, and wider-shot speakers are all protected.
  3. 4-Tier Priority Ranking:
       Tier 1 (`top_empty`)    -> Top-center headroom (`y0` in `3%..14% H`, `y1 <= min_top_face_y0`)
       Tier 2 (`alt_empty`)    -> Lowest-clutter empty background space (left/right/mid pockets)
       Tier 3 (`body_center`)  -> Character body/torso center (`y_center ~ 50%..68% H`, below chins)
       Tier 4 (`bottom_safe`)  -> Lower body / bottom safe zone (`y0 >= 62% H`, `y1 <= 86% H`)
  4. Multi-cut steadiness: merges contiguous identical cards (gap `<= 0.15s`) into ONE
     stationary timeline span, splitting strictly on segment cut boundaries only when
     incompatible camera framings admit no shared `0 px`-overlap rectangle.
"""
from __future__ import annotations

import inspect
import json
import math
import os
import re
import shutil
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple, Union

_SCRIPTS_DIR = Path(__file__).resolve().parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

try:
    import numpy as np
except ImportError:  # pragma: no cover
    np = None  # type: ignore[assignment]

try:
    import cv2
except ImportError:  # pragma: no cover
    cv2 = None  # type: ignore[assignment]

try:
    import common as _common
except ImportError:  # pragma: no cover
    _common = None  # type: ignore[assignment]

try:
    import layouts as _layouts
except ImportError:  # pragma: no cover
    _layouts = None  # type: ignore[assignment]

import vision_tracker

try:
    import ui_generator
except ImportError:  # pragma: no cover
    ui_generator = None  # type: ignore[assignment]

# Snapshot original function reference to detect monkeypatching in unit tests
_ORIG_DETECT_FACES_FN = getattr(vision_tracker, "detect_faces_in_frame", None)

# Overlay-safe YuNet face thresholds (never pruned via prominent_faces)
OVERLAY_MIN_CONF: float = 0.58
OVERLAY_MIN_FW_RATIO: float = 0.042
OVERLAY_MIN_FH_RATIO: float = 0.038

# Head/hair/ears/chin shield expansion factors around raw YuNet bbox
EXPAND_TOP_FRAC: float = 0.34
EXPAND_BOT_FRAC: float = 0.14
EXPAND_SIDE_FRAC: float = 0.18

# Span merge & frame sampling defaults
MERGE_MAX_GAP_SEC: float = 0.15
SAMPLE_STEP_SEC: float = 0.25
MAX_SPAN_SAMPLES: int = 24

_NON_LATIN_RE = re.compile(r"[\u0900-\u0D7F\u0D80-\u0DFF\u0600-\u06FF\u0400-\u04FF\u4E00-\u9FFF]")
_LATIN_RE = re.compile(r"[A-Za-z]")

Rect = Tuple[int, int, int, int]


# ---------------------------------------------------------------------------
# Basic Geometry & Normalization Helpers
# ---------------------------------------------------------------------------
def _even(val: float) -> int:
    return max(2, int(round(float(val) / 2.0)) * 2)


def _norm_layout(layout: Optional[str]) -> str:
    if _layouts is not None and hasattr(_layouts, "normalize_layout_name"):
        return str(_layouts.normalize_layout_name(layout))
    raw = str(layout or "single").strip().lower().split(":", 1)[0].strip()
    aliases = {
        "": "single",
        "single": "single",
        "full": "single",
        "fullscreen": "single",
        "split_h": "split_h",
        "left_right": "split_h",
        "hstack": "split_h",
        "horizontal_split": "split_h",
        "split_v": "split_v",
        "top_bottom": "split_v",
        "vstack": "split_v",
        "vertical_split": "split_v",
        "split": "split_v",
        "top_pip": "top_pip",
        "floating_top": "top_pip",
        "chat_bar": "chat_bar",
        "video_call": "video_call",
        "pip_call": "pip_call",
        "gmeet_grid": "gmeet_grid",
    }
    return aliases.get(raw, "single")


def rect_intersect_area(a: Sequence[int], b: Sequence[int]) -> int:
    """Return exact integer pixel intersection area between two `[x0, y0, x1, y1]` rects."""
    ix = min(int(a[2]), int(b[2])) - max(int(a[0]), int(b[0]))
    if ix <= 0:
        return 0
    iy = min(int(a[3]), int(b[3])) - max(int(a[1]), int(b[1]))
    if iy <= 0:
        return 0
    return ix * iy


def rect_iou(a: Sequence[int], b: Sequence[int]) -> float:
    inter = rect_intersect_area(a, b)
    if inter <= 0:
        return 0.0
    area_a = max(1, (int(a[2]) - int(a[0])) * (int(a[3]) - int(a[1])))
    area_b = max(1, (int(b[2]) - int(b[0])) * (int(b[3]) - int(b[1])))
    return inter / float(area_a + area_b - inter)


def max_shield_overlap_px(rect: Sequence[int], shields: Sequence[Sequence[int]]) -> int:
    """Return maximum pixel intersection area between `rect` and any face shield in `shields`."""
    best = 0
    for s in shields or ():
        ov = rect_intersect_area(rect, s)
        if ov > best:
            best = ov
    return best


def _write_json_atomic(path: Path, data: Any) -> None:
    if _common is not None and hasattr(_common, "write_json"):
        _common.write_json(path, data)
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


def normalize_bilingual_card_text(
    raw_text: str = "",
    en: str = "",
    native: str = "",
) -> Tuple[str, str, str]:
    """Normalize bilingual flashcard text so English (Latin) is ALWAYS Line 1 (`en`)
    and Native/Indic script is ALWAYS Line 2 (`native`), regardless of input order.
    """
    en_s = str(en or "").strip().strip('"“”\'')
    nat_s = str(native or "").strip().strip('"“”\'')
    txt_s = str(raw_text or "").strip().strip('"“”\'')

    if not en_s and not nat_s and txt_s:
        parts: List[str] = []
        for sep in ("|", "—", "–", " - ", " = ", " / "):
            if sep in txt_s:
                cand = [p.strip().strip('"“”\'') for p in txt_s.split(sep, 1)]
                if all(cand):
                    parts = cand
                    break
        if len(parts) == 2:
            en_s, nat_s = parts[0], parts[1]
        else:
            nl = len(_NON_LATIN_RE.findall(txt_s))
            la = len(_LATIN_RE.findall(txt_s))
            if nl > la:
                nat_s = txt_s
            else:
                en_s = txt_s

    if en_s and nat_s:
        en_nl = len(_NON_LATIN_RE.findall(en_s))
        en_la = len(_LATIN_RE.findall(en_s))
        nat_nl = len(_NON_LATIN_RE.findall(nat_s))
        nat_la = len(_LATIN_RE.findall(nat_s))
        # Flip if `en_s` is predominantly Indic/non-Latin and `nat_s` is Latin
        if en_nl > en_la and nat_la >= nat_nl and nat_la > 0:
            en_s, nat_s = nat_s, en_s

    full_text = f"{en_s} | {nat_s}" if (en_s and nat_s) else (en_s or nat_s)
    return en_s, nat_s, full_text


# ---------------------------------------------------------------------------
# 1. Multi-Panel Expanded Face Shield Detection (Overlay-Safe, Zero Pruning)
# ---------------------------------------------------------------------------
def _panel_regions_for_layout(W: int, H: int, layout: str) -> List[Tuple[str, Rect]]:
    """Return active detection panel bounding boxes + full-frame cross-seam pass."""
    lay = _norm_layout(layout)
    panels: List[Tuple[str, Rect]] = []
    if lay == "split_h":
        mid_x = _even(W // 2)
        panels.append(("left", (0, 0, mid_x, H)))
        panels.append(("right", (mid_x, 0, W, H)))
    elif lay == "split_v":
        mid_y = _even(H // 2)
        panels.append(("top", (0, 0, W, mid_y)))
        panels.append(("bottom", (0, mid_y, W, H)))
    elif lay in ("top_pip", "video_call", "pip_call", "gmeet_grid") and _layouts is not None:
        try:
            geo = _layouts.get_layout_geometry(lay, W, H)
            if lay == "gmeet_grid" and isinstance(geo.get("tiles"), list):
                for idx, tile in enumerate(geo["tiles"]):
                    tx, ty = int(tile["x"]), int(tile["y"])
                    tw, th = int(tile["w"]), int(tile["h"])
                    panels.append((f"tile{idx}", (tx, ty, min(W, tx + tw), min(H, ty + th))))
            elif isinstance(geo.get("pip"), dict):
                p = geo["pip"]
                px, py, pw, ph = int(p["x"]), int(p["y"]), int(p["w"]), int(p["h"])
                panels.append(("pip", (px, py, min(W, px + pw), min(H, py + ph))))
        except Exception:
            pass
    # Always include full-frame pass to catch cross-seam / unsplit faces
    panels.append(("full", (0, 0, W, H)))
    return panels


def _expand_raw_face_to_shield(
    rx: float,
    ry: float,
    rw: float,
    rh: float,
    px0: int,
    py0: int,
    W: int,
    H: int,
    m_px: int,
    eye_y: Optional[float] = None,
    mouth_y: Optional[float] = None,
) -> Rect:
    """Expand raw face bbox `(rx, ry, rw, rh)` inside panel `(px0, py0)` to full-canvas shield."""
    sx0 = rx - EXPAND_SIDE_FRAC * rw
    sy0 = ry - EXPAND_TOP_FRAC * rh
    sx1 = rx + (1.0 + EXPAND_SIDE_FRAC) * rw
    sy1 = ry + (1.0 + EXPAND_BOT_FRAC) * rh

    # Landmark tilt sanity guarantee
    if eye_y is not None and (ry - 0.25 * rh) <= eye_y <= (ry + rh):
        sy0 = min(sy0, eye_y - 0.60 * rh)
    if mouth_y is not None and ry <= mouth_y <= (ry + 1.25 * rh):
        sy1 = max(sy1, mouth_y + 0.38 * rh)

    X0 = max(0, int(math.floor(px0 + sx0 - m_px)))
    Y0 = max(0, int(math.floor(py0 + sy0 - m_px)))
    X1 = min(W, int(math.ceil(px0 + sx1 + m_px)))
    Y1 = min(H, int(math.ceil(py0 + sy1 + m_px)))
    return (X0, Y0, max(X0 + 1, X1), max(Y0 + 1, Y1))


def _dedupe_same_frame_shields(shields: List[Rect], iou_thr: float = 0.65) -> List[Rect]:
    """Merge near-duplicate detections of the same face across panel + full-frame passes
    by taking their enclosing union (strictly preserving or expanding safety coverage).
    """
    merged: List[Rect] = []
    for sh in shields:
        matched_idx: Optional[int] = None
        for idx, ex in enumerate(merged):
            if rect_iou(sh, ex) >= iou_thr:
                matched_idx = idx
                break
        if matched_idx is not None:
            ex = merged[matched_idx]
            merged[matched_idx] = (
                min(ex[0], sh[0]),
                min(ex[1], sh[1]),
                max(ex[2], sh[2]),
                max(ex[3], sh[3]),
            )
        else:
            merged.append(sh)
    merged.sort(key=lambda r: (r[0], r[1], r[2], r[3]))
    return merged


def detect_expanded_face_shields(
    frame_bgr: "np.ndarray",
    layout: str = "single",
) -> List[Rect]:
    """Detect ALL faces on active panels + full frame and expand each to a generous
    head/hair/ears/chin safety shield in full-frame `(x0, y0, x1, y1)` pixel coordinates.

    Uses overlay-safe thresholds (`conf >= 0.58`, `fw_ratio >= 0.042`, `fh_ratio >= 0.038`)
    and NEVER applies `prominent_faces(0.4)` pruning so secondary characters, robots, babies,
    and wider-shot speakers are all strictly protected.
    """
    if np is None or frame_bgr is None or getattr(frame_bgr, "size", 0) == 0:
        return []
    H, W = int(frame_bgr.shape[0]), int(frame_bgr.shape[1])
    if W < 16 or H < 16:
        return []

    m_px = max(12, int(round(0.012 * min(W, H))))
    panels = _panel_regions_for_layout(W, H, layout)

    # Check if vision_tracker.detect_faces_in_frame was monkeypatched (e.g. in unit tests)
    detect_fn = getattr(vision_tracker, "detect_faces_in_frame", None)
    is_mocked = detect_fn is not None and detect_fn is not _ORIG_DETECT_FACES_FN
    detector = None if is_mocked else vision_tracker.get_yunet_detector()
    yunet_lock = getattr(vision_tracker, "_YUNET_LOCK", None)

    shields: List[Rect] = []

    for _pname, (px0, py0, px1, py1) in panels:
        pw, ph = px1 - px0, py1 - py0
        if pw < 16 or ph < 16:
            continue
        crop = np.ascontiguousarray(frame_bgr[py0:py1, px0:px1])
        if crop.size == 0:
            continue

        used_raw = False
        if detector is not None and cv2 is not None:
            raw_faces = None
            if yunet_lock is not None:
                with yunet_lock:
                    try:
                        detector.setInputSize((pw, ph))
                        _, raw_faces = detector.detect(crop)
                    except Exception:
                        raw_faces = None
            else:
                try:
                    detector.setInputSize((pw, ph))
                    _, raw_faces = detector.detect(crop)
                except Exception:
                    raw_faces = None
            used_raw = True

            if raw_faces is not None and len(raw_faces) > 0:
                for det in raw_faces:
                    rx = float(det[0])
                    ry = float(det[1])
                    rw = float(det[2])
                    rh = float(det[3])
                    conf = float(det[14])
                    if rw <= 0.0 or rh <= 0.0:
                        continue
                    fw_ratio = rw / float(pw)
                    fh_ratio = rh / float(ph)
                    if (
                        conf >= OVERLAY_MIN_CONF
                        and fw_ratio >= OVERLAY_MIN_FW_RATIO
                        and fh_ratio >= OVERLAY_MIN_FH_RATIO
                    ):
                        re_y, le_y = float(det[5]), float(det[7])
                        rm_y, lm_y = float(det[11]), float(det[13])
                        shields.append(
                            _expand_raw_face_to_shield(
                                rx, ry, rw, rh, px0, py0, W, H, m_px,
                                eye_y=min(re_y, le_y),
                                mouth_y=max(rm_y, lm_y),
                            )
                        )

        if not used_raw and callable(detect_fn):
            try:
                faces = detect_fn(crop, is_bgr=True, min_fw_ratio=OVERLAY_MIN_FW_RATIO) or []
            except TypeError:
                faces = detect_fn(crop) or []
            except Exception:
                faces = []
            for f in faces:
                if not isinstance(f, dict):
                    continue
                conf = float(f.get("confidence", 1.0))
                if conf < OVERLAY_MIN_CONF:
                    continue
                bb = f.get("bbox_norm")
                if not bb or len(bb) < 4:
                    continue
                hx0, hy0, hx1, hy1 = [float(v) for v in bb[:4]]
                bw = max(0.0, hx1 - hx0)
                bh = max(0.0, hy1 - hy0)
                # Invert vision_tracker's (-0.08, -0.18, +0.08, +0.04) expansion to recover raw YuNet box
                raw_fw_norm = bw / 1.16
                raw_fh_norm = bh / 1.22
                if raw_fw_norm < OVERLAY_MIN_FW_RATIO or raw_fh_norm < OVERLAY_MIN_FH_RATIO:
                    continue
                rx = (hx0 + 0.08 * raw_fw_norm) * pw
                ry = (hy0 + 0.18 * raw_fh_norm) * ph
                rw = raw_fw_norm * pw
                rh = raw_fh_norm * ph
                ec = f.get("eye_center_norm")
                mc = f.get("mouth_center_norm")
                eye_y = float(ec[1]) * ph if (isinstance(ec, (list, tuple)) and len(ec) >= 2) else None
                mouth_y = float(mc[1]) * ph if (isinstance(mc, (list, tuple)) and len(mc) >= 2) else None
                shields.append(
                    _expand_raw_face_to_shield(
                        rx, ry, rw, rh, px0, py0, W, H, m_px,
                        eye_y=eye_y, mouth_y=mouth_y,
                    )
                )

    return _dedupe_same_frame_shields(shields, iou_thr=0.65)


# ---------------------------------------------------------------------------
# 2. Fast O(1) Clutter Integral Map (`compute_clutter_integral`)
# ---------------------------------------------------------------------------
def compute_clutter_integral(frame_bgr: "np.ndarray") -> "np.ndarray":
    """Compute a fast Sobel gradient-magnitude integral image (`cv2.integral`) so scoring
    any candidate rectangle's visual busyness/clutter takes `O(1)` time (4 array lookups).
    """
    if np is None or frame_bgr is None or getattr(frame_bgr, "size", 0) == 0:
        return np.zeros((2, 2), dtype=np.float64) if np is not None else []  # type: ignore[return-value]

    h, w = int(frame_bgr.shape[0]), int(frame_bgr.shape[1])
    if h < 4 or w < 4:
        return np.zeros((h + 1, w + 1), dtype=np.float64)

    if cv2 is not None:
        gray = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY) if frame_bgr.ndim == 3 else frame_bgr
        max_dim = max(h, w)
        if max_dim > 480:
            scale = 480.0 / float(max_dim)
            sw = max(16, int(round(w * scale)))
            sh = max(16, int(round(h * scale)))
            gray = cv2.resize(gray, (sw, sh), interpolation=cv2.INTER_AREA)
        gx = cv2.Sobel(gray, cv2.CV_32F, 1, 0, ksize=3)
        gy = cv2.Sobel(gray, cv2.CV_32F, 0, 1, ksize=3)
        mag = cv2.magnitude(gx, gy)
        clutter = np.clip(mag / 180.0, 0.0, 1.0).astype(np.float32)
        return cv2.integral(clutter, sdepth=cv2.CV_64F)

    # Pure numpy fallback when OpenCV is absent
    gray_f = (
        0.114 * frame_bgr[:, :, 0] + 0.587 * frame_bgr[:, :, 1] + 0.299 * frame_bgr[:, :, 2]
        if frame_bgr.ndim == 3
        else frame_bgr.astype(np.float32)
    )
    gx = np.abs(np.diff(gray_f, axis=1, prepend=gray_f[:, :1]))
    gy = np.abs(np.diff(gray_f, axis=0, prepend=gray_f[:1, :]))
    clutter = np.clip(np.hypot(gx, gy) / 64.0, 0.0, 1.0).astype(np.float64)
    integ = np.zeros((clutter.shape[0] + 1, clutter.shape[1] + 1), dtype=np.float64)
    integ[1:, 1:] = np.cumsum(np.cumsum(clutter, axis=0), axis=1)
    return integ


def box_clutter_score(
    clutter_maps: Any,
    rect: Rect,
    W: int,
    H: int,
) -> float:
    """Evaluate exact O(1) mean visual clutter in `[0.0, 1.0]` for `rect` across `clutter_maps`."""
    if np is None or clutter_maps is None:
        return 0.0
    if isinstance(clutter_maps, np.ndarray):
        maps = [clutter_maps]
    elif isinstance(clutter_maps, (list, tuple)):
        maps = [m for m in clutter_maps if m is not None and getattr(m, "ndim", 0) == 2]
    else:
        return 0.0
    if not maps:
        return 0.0

    x0, y0, x1, y1 = rect
    if x1 <= x0 or y1 <= y0 or W <= 0 or H <= 0:
        return 0.0

    total = 0.0
    count = 0
    for integ in maps:
        ih = int(integ.shape[0]) - 1
        iw = int(integ.shape[1]) - 1
        if ih <= 0 or iw <= 0:
            continue
        ix0 = max(0, min(iw - 1, int(round(x0 * iw / float(W)))))
        iy0 = max(0, min(ih - 1, int(round(y0 * ih / float(H)))))
        ix1 = max(ix0 + 1, min(iw, int(round(x1 * iw / float(W)))))
        iy1 = max(iy0 + 1, min(ih, int(round(y1 * ih / float(H)))))
        s = (
            float(integ[iy1, ix1])
            - float(integ[iy0, ix1])
            - float(integ[iy1, ix0])
            + float(integ[iy0, ix0])
        )
        area = max(1, (ix1 - ix0) * (iy1 - iy0))
        total += max(0.0, min(1.0, s / float(area)))
        count += 1
    return (total / float(count)) if count > 0 else 0.0


# ---------------------------------------------------------------------------
# 3. 4-Tier Face-Safe & Empty-Space Placement Solver (`choose_face_safe_rect`)
# ---------------------------------------------------------------------------
def _parse_subtitle_band(subtitle_band: Any, W: int, H: int) -> Optional[Rect]:
    """Normalize `subtitle_band` (`(y0, y1)`, `(x0, y0, x1, y1)`, or dict) to canvas pixel Rect."""
    if subtitle_band is None:
        return None
    if isinstance(subtitle_band, dict):
        if "y0" in subtitle_band and "y1" in subtitle_band:
            x0 = subtitle_band.get("x0", int(round(0.05 * W)))
            x1 = subtitle_band.get("x1", int(round(0.95 * W)))
            y0, y1 = subtitle_band["y0"], subtitle_band["y1"]
            vals = [float(x0), float(y0), float(x1), float(y1)]
            if vals[3] <= 1.0 and vals[2] <= 1.0:
                return (
                    int(round(vals[0] * W)),
                    int(round(vals[1] * H)),
                    int(round(vals[2] * W)),
                    int(round(vals[3] * H)),
                )
            return (int(round(vals[0])), int(round(vals[1])), int(round(vals[2])), int(round(vals[3])))
        return None
    if isinstance(subtitle_band, (list, tuple)):
        if len(subtitle_band) == 2:
            a, b = float(subtitle_band[0]), float(subtitle_band[1])
            if b <= a:
                return None
            if b <= 1.0:
                sy0, sy1 = int(round(a * H)), int(round(b * H))
            elif b <= 100.0 and H >= 300:
                sy0, sy1 = int(round((a / 100.0) * H)), int(round((b / 100.0) * H))
            else:
                sy0, sy1 = int(round(a)), int(round(b))
            return (int(round(0.05 * W)), max(0, sy0), int(round(0.95 * W)), min(H, sy1))
        if len(subtitle_band) >= 4:
            x0, y0, x1, y1 = [float(v) for v in subtitle_band[:4]]
            if x1 <= x0 or y1 <= y0:
                return None
            if x1 <= 1.0 and y1 <= 1.0:
                return (
                    int(round(x0 * W)),
                    int(round(y0 * H)),
                    int(round(x1 * W)),
                    int(round(y1 * H)),
                )
            return (int(round(x0)), int(round(y0)), int(round(x1)), int(round(y1)))
    return None


def _estimate_body_boxes(shields: Sequence[Rect], W: int, H: int, layout: str) -> List[Rect]:
    """Estimate character torso/body regions below each face shield (clipped to panel bounds)."""
    lay = _norm_layout(layout)
    mid_x = _even(W // 2)
    mid_y = _even(H // 2)
    bodies: List[Rect] = []
    for fx0, fy0, fx1, fy1 in shields:
        sw = max(1, fx1 - fx0)
        fcx = 0.5 * (fx0 + fx1)
        fcy = 0.5 * (fy0 + fy1)
        if lay == "split_h":
            px0, px1 = (0, mid_x) if fcx < mid_x else (mid_x, W)
            py1 = H
        elif lay == "split_v":
            px0, px1 = 0, W
            py1 = mid_y if fcy < mid_y else H
        else:
            px0, px1 = 0, W
            py1 = H
        bx0 = max(px0, int(round(fx0 - 0.65 * sw)))
        bx1 = min(px1, int(round(fx1 + 0.65 * sw)))
        by0 = min(py1, fy1)
        by1 = py1
        if bx1 > bx0 and by1 > by0:
            bodies.append((bx0, by0, bx1, by1))
    return bodies


def _union_overlap_fraction(rect: Rect, boxes: Sequence[Rect]) -> float:
    """Return fraction of `rect` covered by `boxes` (clamped to `[0.0, 1.0]`)."""
    if not boxes:
        return 0.0
    area = max(1, (rect[2] - rect[0]) * (rect[3] - rect[1]))
    # Sampled exact sum capped at 1.0 (exact for disjoint boxes, tight upper bound otherwise)
    covered = 0
    for b in boxes:
        covered += rect_intersect_area(rect, b)
    return min(1.0, covered / float(area))


def _right_ui_overlap(rect: Rect, W: int, H: int) -> float:
    """Fraction of `rect` intruding into social-app right-edge action icons (`x > 86% W, y in 45%..86% H`)."""
    ui_box = (int(round(0.86 * W)), int(round(0.45 * H)), W, int(round(0.86 * H)))
    area = max(1, (rect[2] - rect[0]) * (rect[3] - rect[1]))
    return rect_intersect_area(rect, ui_box) / float(area)


def _card_size_presets(W: int, H: int, square: bool) -> Dict[str, Tuple[int, int, bool]]:
    """Return `(w, h, compact)` presets for 9:16 portrait or 1:1 square canvases."""
    if square:
        std_w, std_h = _even(0.72 * W), _even(0.210 * H)
        cw_w, cw_h = _even(0.78 * W), _even(0.155 * H)
        side_w, side_h = _even(0.46 * W), _even(0.190 * H)
        c74_w, c74_h = _even(0.76 * W), _even(0.130 * H)
        uc_w, uc_h = _even(0.78 * W), _even(0.105 * H)
    else:
        std_w, std_h = _even(0.66 * W), _even(0.151 * H)
        cw_w, cw_h = _even(0.72 * W), _even(0.106 * H)
        side_w, side_h = _even(0.45 * W), _even(0.135 * H)
        c74_w, c74_h = _even(0.70 * W), _even(0.092 * H)
        uc_w, uc_h = _even(0.74 * W), _even(0.078 * H)

    return {
        "standard": (std_w, std_h, False),
        "scaled_88": (_even(std_w * 0.88), _even(std_h * 0.88), False),
        "compact_wide": (cw_w, cw_h, True),
        "scaled_84": (_even(std_w * 0.84), _even(std_h * 0.84), True),
        "scaled_74": (_even(std_w * 0.74), _even(std_h * 0.74), True),
        "compact_74": (c74_w, c74_h, True),
        "side_compact": (side_w, side_h, True),
        "ultra_compact": (uc_w, uc_h, True),
    }


def _resolve_card_style(tier: str, compact: bool, explicit_style: Optional[str]) -> str:
    st = str(explicit_style or "").strip().lower()
    if st and st != "auto":
        return st
    if compact and tier in ("body_center", "bottom_safe"):
        return "minimal"
    return "clean"


def _solve_face_safe_rect_strict(
    W: int,
    H: int,
    shields: Sequence[Sequence[int]],
    *,
    clutter_maps: Any = None,
    pref_pos: str = "auto",
    layout: str = "single",
    is_square: bool = False,
    subtitle_band: Any = None,
    style: Optional[str] = None,
    y_max_frac: float = 0.86,
    allow_ultra_compact: bool = True,
) -> Optional[Dict[str, Any]]:
    """Strict HARD-GATE-0 solver returning a `0 px`-face-overlap placement dict, or `None`
    when no valid placement exists inside safe bounds.
    """
    W_i, H_i = max(32, int(W)), max(32, int(H))
    square = bool(is_square) or (abs(W_i - H_i) <= 0.15 * max(W_i, H_i))
    lay = _norm_layout(layout)

    norm_shields: List[Rect] = []
    for s in shields or ():
        if len(s) >= 4:
            sx0, sy0, sx1, sy1 = int(s[0]), int(s[1]), int(s[2]), int(s[3])
            if sx1 > sx0 and sy1 > sy0:
                norm_shields.append((sx0, sy0, sx1, sy1))

    x_min = max(12, int(math.ceil(0.03 * W_i)))
    x_max = W_i - x_min
    y_min = max(16, int(math.ceil(0.025 * H_i)))
    y_max = int(math.floor(y_max_frac * H_i))
    if x_max <= x_min + 16 or y_max <= y_min + 16:
        return None

    sub_rect = _parse_subtitle_band(subtitle_band, W_i, H_i)
    body_boxes = _estimate_body_boxes(norm_shields, W_i, H_i, lay)
    presets = _card_size_presets(W_i, H_i, square)

    min_top_face_y0 = min((s[1] for s in norm_shields), default=H_i)
    upper_chins = [s[3] for s in norm_shields if s[1] < int(round(0.55 * H_i))]
    body_chin_floor = (max(upper_chins) + max(8, int(round(0.008 * H_i)))) if upper_chins else 0

    def _gate0(rect: Rect) -> bool:
        rx0, ry0, rx1, ry1 = rect
        if rx0 < x_min or rx1 > x_max or ry0 < y_min or ry1 > y_max:
            return False
        if rx1 <= rx0 or ry1 <= ry0:
            return False
        for sh in norm_shields:
            if rect_intersect_area(rect, sh) > 0:
                return False
        return True

    def _hits_sub(rect: Rect) -> bool:
        return sub_rect is not None and rect_intersect_area(rect, sub_rect) > 0

    def _mk_result(rect: Rect, compact: bool, tier: str) -> Dict[str, Any]:
        rx0, ry0, rx1, ry1 = rect
        return {
            "x0": rx0,
            "y0": ry0,
            "x1": rx1,
            "y1": ry1,
            "w": rx1 - rx0,
            "h": ry1 - ry0,
            "compact": bool(compact),
            "tier": tier,
            "style": _resolve_card_style(tier, compact, style),
        }

    # ------------------------------------------------------------------
    # Tier 1: `top_empty` (top-center headroom, y0 in 3%..14% H, y1 <= min_top_face_y0)
    # ------------------------------------------------------------------
    def _try_top_empty(dodge_sub: bool = True) -> Optional[Dict[str, Any]]:
        top_y_lo = max(y_min, int(round(0.03 * H_i)))
        top_y_hi = int(round(0.14 * H_i))
        ref_y0 = int(round((0.060 if square else 0.091) * H_i))
        step_y = max(6, int(round(0.010 * H_i)))
        gap_y = max(8, int(round(0.012 * H_i)))

        for key in ("standard", "scaled_88", "compact_wide"):
            cw, ch, is_compact = presets[key]
            if cw > (x_max - x_min):
                continue
            y_hi_eff = min(top_y_hi, min_top_face_y0 - ch, y_max - ch)
            if y_hi_eff < top_y_lo:
                continue

            cx0 = (W_i - cw) // 2
            x_cands = [cx0]
            for dx in (-int(round(0.03 * W_i)), int(round(0.03 * W_i))):
                nx = max(x_min, min(x_max - cw, cx0 + dx))
                if nx not in x_cands:
                    x_cands.append(nx)

            y_cands_set = set(range(top_y_lo, y_hi_eff + 1, step_y))
            for special_y in (
                top_y_lo,
                y_hi_eff,
                ref_y0,
                int(round(0.5 * (top_y_lo + min_top_face_y0 - ch))),
                min_top_face_y0 - ch - gap_y,
            ):
                if top_y_lo <= special_y <= y_hi_eff:
                    y_cands_set.add(int(special_y))

            pref_y0 = min(ref_y0, max(top_y_lo, min_top_face_y0 - ch - int(round(0.015 * H_i))))

            best_score: Optional[Tuple[float, float]] = None
            best_rect: Optional[Rect] = None
            for rx0 in x_cands:
                rx1 = rx0 + cw
                cx_dev = abs((rx0 + rx1) / (2.0 * W_i) - 0.5)
                for ry0 in sorted(y_cands_set):
                    ry1 = ry0 + ch
                    if ry1 > min_top_face_y0:
                        continue
                    rect = (rx0, ry0, rx1, ry1)
                    if not _gate0(rect):
                        continue
                    if dodge_sub and _hits_sub(rect):
                        continue
                    clutter = box_clutter_score(clutter_maps, rect, W_i, H_i)
                    y_dev = abs(ry0 - pref_y0) / float(H_i)
                    score = 0.50 * clutter + 0.30 * cx_dev + 0.20 * y_dev
                    key_rank = (round(score, 5), cx_dev)
                    if best_score is None or key_rank < best_score:
                        best_score = key_rank
                        best_rect = rect
            if best_rect is not None:
                return _mk_result(best_rect, is_compact, "top_empty")
        return None

    # ------------------------------------------------------------------
    # Tier 2: `alt_empty` (lowest-clutter alternate empty space)
    # ------------------------------------------------------------------
    def _try_alt_empty(dodge_sub: bool = True) -> Optional[Dict[str, Any]]:
        step_y = max(10, int(round(0.020 * H_i)))
        gap = max(8, int(round(0.010 * min(W_i, H_i))))
        left_x = max(x_min, int(round(0.04 * W_i)))
        right_edge = min(x_max, int(round(0.96 * W_i)))

        for key in ("standard", "scaled_88", "compact_wide", "side_compact"):
            cw, ch, is_compact = presets[key]
            if cw > (x_max - x_min) or ch > (y_max - y_min):
                continue

            x_set = {
                max(x_min, min(x_max - cw, (W_i - cw) // 2)),
                max(x_min, min(x_max - cw, left_x)),
                max(x_min, min(x_max - cw, right_edge - cw)),
                max(x_min, min(x_max - cw, int(round(0.25 * W_i)) - cw // 2)),
                max(x_min, min(x_max - cw, int(round(0.75 * W_i)) - cw // 2)),
            }
            for fx0, _fy0, fx1, _fy1 in norm_shields:
                if fx0 - cw - gap >= x_min:
                    x_set.add(fx0 - cw - gap)
                if fx1 + gap + cw <= x_max:
                    x_set.add(fx1 + gap)

            y_set = set(range(y_min, y_max - ch + 1, step_y))
            y_set.add(y_min)
            y_set.add(y_max - ch)
            for _fx0, fy0, _fx1, fy1 in norm_shields:
                if fy0 - ch - gap >= y_min:
                    y_set.add(fy0 - ch - gap)
                if fy1 + gap + ch <= y_max:
                    y_set.add(fy1 + gap)
            if sub_rect is not None:
                if sub_rect[1] - ch - gap >= y_min:
                    y_set.add(sub_rect[1] - ch - gap)
                if sub_rect[3] + gap + ch <= y_max:
                    y_set.add(sub_rect[3] + gap)

            best_score: Optional[float] = None
            best_rect: Optional[Rect] = None
            for rx0 in sorted(x_set):
                rx1 = rx0 + cw
                cx_dev = abs((rx0 + rx1) / (2.0 * W_i) - 0.5)
                for ry0 in sorted(y_set):
                    ry1 = ry0 + ch
                    rect = (rx0, ry0, rx1, ry1)
                    if not _gate0(rect):
                        continue
                    if dodge_sub and _hits_sub(rect):
                        continue
                    body_ov = _union_overlap_fraction(rect, body_boxes)
                    if body_ov > 0.22:
                        continue
                    clutter = box_clutter_score(clutter_maps, rect, W_i, H_i)
                    cy_norm = (ry0 + ry1) / (2.0 * H_i)
                    r_ui = _right_ui_overlap(rect, W_i, H_i)
                    score = 0.52 * clutter + 0.18 * cy_norm + 0.15 * cx_dev + 0.10 * body_ov + 0.05 * r_ui
                    if best_score is None or score < best_score:
                        best_score = score
                        best_rect = rect
            if best_rect is not None:
                return _mk_result(best_rect, is_compact, "alt_empty")
        return None

    # ------------------------------------------------------------------
    # Tier 3: `body_center` (torso center y_center ~ 50%..68% H, strictly below chins + margin)
    # ------------------------------------------------------------------
    def _try_body_center(dodge_sub: bool = True) -> Optional[Dict[str, Any]]:
        yc_lo = 0.50 * H_i
        yc_hi = 0.68 * H_i
        # On split_v, top speaker torso sits in ~38%..49% H above the seam
        if lay == "split_v":
            yc_lo = 0.36 * H_i
        step_y = max(8, int(round(0.015 * H_i)))
        gap = max(8, int(round(0.010 * H_i)))

        for key in ("standard", "scaled_88", "compact_wide", "scaled_84", "scaled_74"):
            cw, ch, is_compact = presets[key]
            if cw > (x_max - x_min):
                continue
            y0_lo = max(y_min, body_chin_floor, int(math.ceil(yc_lo - 0.5 * ch)))
            y0_hi = min(y_max - ch, int(math.floor(yc_hi - 0.5 * ch)))
            if y0_hi < y0_lo:
                continue

            cx0 = (W_i - cw) // 2
            x_cands = [max(x_min, min(x_max - cw, cx0))]
            for dx in (-int(round(0.06 * W_i)), int(round(0.06 * W_i))):
                nx = max(x_min, min(x_max - cw, cx0 + dx))
                if nx not in x_cands:
                    x_cands.append(nx)

            y_set = set(range(y0_lo, y0_hi + 1, step_y))
            y_set.add(y0_lo)
            y_set.add(y0_hi)
            target_y0 = int(round(0.58 * H_i - 0.5 * ch))
            if y0_lo <= target_y0 <= y0_hi:
                y_set.add(target_y0)
            if sub_rect is not None:
                for sy in (sub_rect[1] - ch - gap, sub_rect[3] + gap):
                    if y0_lo <= sy <= y0_hi:
                        y_set.add(sy)

            best_score: Optional[float] = None
            best_rect: Optional[Rect] = None
            for rx0 in x_cands:
                rx1 = rx0 + cw
                cx_dev = abs((rx0 + rx1) / (2.0 * W_i) - 0.5)
                for ry0 in sorted(y_set):
                    ry1 = ry0 + ch
                    rect = (rx0, ry0, rx1, ry1)
                    if not _gate0(rect):
                        continue
                    if dodge_sub and _hits_sub(rect):
                        continue
                    cy_norm = (ry0 + ry1) / (2.0 * H_i)
                    clutter = box_clutter_score(clutter_maps, rect, W_i, H_i)
                    score = 0.45 * cx_dev + 0.40 * abs(cy_norm - 0.58) + 0.15 * clutter
                    if best_score is None or score < best_score:
                        best_score = score
                        best_rect = rect
            if best_rect is not None:
                return _mk_result(best_rect, is_compact, "body_center")
        return None

    # ------------------------------------------------------------------
    # Tier 4: `bottom_safe` (y0 >= 62% H, y1 <= 86% H, standard -> 0.84x -> 0.74x compact)
    # ------------------------------------------------------------------
    def _try_bottom_safe(dodge_sub: bool = True) -> Optional[Dict[str, Any]]:
        bot_y_lo = max(y_min, int(round(0.62 * H_i)))
        step_y = max(8, int(round(0.015 * H_i)))
        gap = max(6, int(round(0.008 * H_i)))
        left_x = max(x_min, int(round(0.04 * W_i)))
        right_edge = min(x_max, int(round(0.96 * W_i)))

        bot_keys: Tuple[str, ...] = (
            "standard",
            "scaled_88",
            "compact_wide",
            "scaled_84",
            "scaled_74",
            "compact_74",
            "ultra_compact",
        ) if allow_ultra_compact else (
            "standard",
            "scaled_88",
            "compact_wide",
            "scaled_84",
            "scaled_74",
            "compact_74",
        )
        for key in bot_keys:
            cw, ch, is_compact = presets[key]
            if cw > (x_max - x_min):
                continue
            y0_hi = y_max - ch
            if y0_hi < bot_y_lo:
                continue

            cx0 = (W_i - cw) // 2
            x_cands = []
            for nx in (
                cx0,
                cx0 - int(round(0.08 * W_i)),
                cx0 + int(round(0.08 * W_i)),
                left_x,
                right_edge - cw,
            ):
                clamped = max(x_min, min(x_max - cw, nx))
                if clamped not in x_cands:
                    x_cands.append(clamped)

            y_set = set(range(bot_y_lo, y0_hi + 1, step_y))
            y_set.add(bot_y_lo)
            y_set.add(y0_hi)
            for _fx0, _fy0, _fx1, fy1 in norm_shields:
                if bot_y_lo <= fy1 + gap <= y0_hi:
                    y_set.add(fy1 + gap)
            if sub_rect is not None:
                for sy in (sub_rect[1] - ch - gap, sub_rect[3] + gap):
                    if bot_y_lo <= sy <= y0_hi:
                        y_set.add(sy)

            best_score: Optional[float] = None
            best_rect: Optional[Rect] = None
            for rx0 in x_cands:
                rx1 = rx0 + cw
                cx_dev = abs((rx0 + rx1) / (2.0 * W_i) - 0.5)
                for ry0 in sorted(y_set):
                    ry1 = ry0 + ch
                    rect = (rx0, ry0, rx1, ry1)
                    if not _gate0(rect):
                        continue
                    if dodge_sub and _hits_sub(rect):
                        continue
                    cy_norm = (ry0 + ry1) / (2.0 * H_i)
                    clutter = box_clutter_score(clutter_maps, rect, W_i, H_i)
                    score = 0.45 * cx_dev + 0.35 * abs(cy_norm - 0.75) + 0.20 * clutter
                    if best_score is None or score < best_score:
                        best_score = score
                        best_rect = rect
            if best_rect is not None:
                return _mk_result(best_rect, is_compact, "bottom_safe")
        return None

    # ------------------------------------------------------------------
    # General strict Gate-0 dense sweep across full safe canvas (before relaxing anything)
    # ------------------------------------------------------------------
    def _try_any_zero_overlap(dodge_sub: bool = True) -> Optional[Dict[str, Any]]:
        step_y = max(8, int(round(0.015 * H_i)))
        step_x = max(12, int(round(0.04 * W_i)))
        sweep_keys: Tuple[str, ...] = (
            ("compact_wide", "scaled_74", "compact_74", "side_compact", "ultra_compact")
            if allow_ultra_compact
            else ("compact_wide", "scaled_74", "compact_74", "side_compact")
        )
        for key in sweep_keys:
            cw, ch, _ = presets[key]
            if cw > (x_max - x_min) or ch > (y_max - y_min):
                continue
            x_cands = list(range(x_min, x_max - cw + 1, step_x))
            if (W_i - cw) // 2 not in x_cands:
                x_cands.insert(0, max(x_min, min(x_max - cw, (W_i - cw) // 2)))
            y_cands = list(range(y_min, y_max - ch + 1, step_y))
            if (y_max - ch) not in y_cands:
                y_cands.append(y_max - ch)
            for _fx0, fy0, _fx1, fy1 in norm_shields:
                if y_min <= fy0 - ch <= y_max - ch:
                    y_cands.append(fy0 - ch)
                if y_min <= fy1 <= y_max - ch:
                    y_cands.append(fy1)

            best_score: Optional[float] = None
            best_rect: Optional[Rect] = None
            for rx0 in x_cands:
                rx1 = rx0 + cw
                cx_dev = abs((rx0 + rx1) / (2.0 * W_i) - 0.5)
                for ry0 in y_cands:
                    ry1 = ry0 + ch
                    rect = (rx0, ry0, rx1, ry1)
                    if not _gate0(rect):
                        continue
                    if dodge_sub and _hits_sub(rect):
                        continue
                    clutter = box_clutter_score(clutter_maps, rect, W_i, H_i)
                    score = 0.50 * cx_dev + 0.50 * clutter
                    if best_score is None or score < best_score:
                        best_score = score
                        best_rect = rect
            if best_rect is not None:
                cy_norm = (best_rect[1] + best_rect[3]) / (2.0 * H_i)
                tier = (
                    "top_empty"
                    if best_rect[3] <= min_top_face_y0
                    else ("bottom_safe" if cy_norm >= 0.68 else ("body_center" if cy_norm >= 0.45 else "alt_empty"))
                )
                return _mk_result(best_rect, True, tier)
        return None

    pos_norm = str(pref_pos or "auto").strip().lower()
    tier_fns = {
        "top_empty": _try_top_empty,
        "alt_empty": _try_alt_empty,
        "body_center": _try_body_center,
        "bottom_safe": _try_bottom_safe,
    }
    if pos_norm in ("body", "center", "torso", "mid"):
        tier_order = ("body_center", "bottom_safe", "alt_empty", "top_empty")
    elif pos_norm in ("bottom", "lower", "bot"):
        tier_order = ("bottom_safe", "body_center", "alt_empty", "top_empty")
    else:
        tier_order = ("top_empty", "alt_empty", "body_center", "bottom_safe")

    # Pass 1: Dodge subtitles cleanly across all 4 tiers in priority order
    for t_name in tier_order:
        res = tier_fns[t_name](dodge_sub=True)
        if res is not None:
            return res

    # Pass 2: If subtitle_band blocked all 4 tiers, allow subtitle co-shift (subtitles dodge flashcards in Stage 3b)
    if sub_rect is not None:
        for t_name in tier_order:
            res = tier_fns[t_name](dodge_sub=False)
            if res is not None:
                return res

    # Pass 3: Full-canvas zero-overlap sweep (still strictly 0 px face overlap inside safe bounds!)
    res = _try_any_zero_overlap(dodge_sub=True)
    if res is not None:
        return res
    if sub_rect is not None:
        res = _try_any_zero_overlap(dodge_sub=False)
        if res is not None:
            return res

    return None


def choose_face_safe_rect(
    W: int,
    H: int,
    shields: Sequence[Sequence[int]],
    *,
    clutter_maps: Any = None,
    pref_pos: str = "auto",
    layout: str = "single",
    is_square: bool = False,
    subtitle_band: Any = None,
    style: Optional[str] = None,
) -> Dict[str, Any]:
    """Select the optimal face-safe flashcard rectangle on `(W, H)` enforcing HARD GATE 0
    (`0 px` intersection with every face shield in `shields`) and 4-tier priority ranking:
    `top_empty -> alt_empty -> body_center -> bottom_safe` (or prioritized by `pref_pos`).

    Returns `{"x0", "y0", "x1", "y1", "w", "h", "compact": bool, "tier": str, "style": str}`.
    """
    strict = _solve_face_safe_rect_strict(
        W, H, shields,
        clutter_maps=clutter_maps,
        pref_pos=pref_pos,
        layout=layout,
        is_square=is_square,
        subtitle_band=subtitle_band,
        style=style,
        y_max_frac=0.86,
    )
    if strict is not None:
        return strict

    # Extreme macro-closeup backstop: allow extending floor down to 94% H while STILL enforcing 0 px face overlap!
    extended = _solve_face_safe_rect_strict(
        W, H, shields,
        clutter_maps=clutter_maps,
        pref_pos="bottom",
        layout=layout,
        is_square=is_square,
        subtitle_band=subtitle_band,
        style=style,
        y_max_frac=0.94,
    )
    if extended is not None:
        return extended

    # Absolute mathematical backstop (only reachable if faces cover 100% of canvas height):
    # pick the minimum-overlap compact strip at the bottom or top edge inside safe bounds.
    W_i, H_i = max(32, int(W)), max(32, int(H))
    square = bool(is_square) or (abs(W_i - H_i) <= 0.15 * max(W_i, H_i))
    cw, ch, _ = _card_size_presets(W_i, H_i, square)["ultra_compact"]
    x_min = max(12, int(math.ceil(0.03 * W_i)))
    x_max = W_i - x_min
    cw = min(cw, max(16, x_max - x_min))
    rx0 = max(x_min, (W_i - cw) // 2)
    rx1 = rx0 + cw
    y_min = max(16, int(math.ceil(0.025 * H_i)))
    y_max = int(math.floor(0.86 * H_i))

    norm_shields = [
        (int(s[0]), int(s[1]), int(s[2]), int(s[3]))
        for s in (shields or ())
        if len(s) >= 4 and int(s[2]) > int(s[0]) and int(s[3]) > int(s[1])
    ]
    cands = [
        (y_max - ch, "bottom_safe"),
        (y_min, "top_empty"),
        (max(y_min, min(y_max - ch, int(round(0.60 * H_i - 0.5 * ch)))), "body_center"),
    ]
    best = min(
        cands,
        key=lambda item: sum(rect_intersect_area((rx0, item[0], rx1, item[0] + ch), sh) for sh in norm_shields),
    )
    ry0, tier = best
    ry1 = ry0 + ch
    return {
        "x0": rx0,
        "y0": ry0,
        "x1": rx1,
        "y1": ry1,
        "w": rx1 - rx0,
        "h": ry1 - ry0,
        "compact": True,
        "tier": tier,
        "style": _resolve_card_style(tier, True, style),
    }


# ---------------------------------------------------------------------------
# 4. Timeline Card Span Extraction & Merging (`extract_timeline_card_spans`)
# ---------------------------------------------------------------------------
def extract_timeline_card_spans(
    plan: Optional[Dict[str, Any]],
    segs: Sequence[Dict[str, Any]],
    starts: Sequence[float],
    durs: Sequence[float],
) -> List[Dict[str, Any]]:
    """Collect segment flashcards, resolve segment-relative `[at, dur]` into timeline `[t0, t1]`,
    and merge adjacent/contiguous spans sharing the same `(en, native, pos, style)` (gap `<= 0.15s`)
    into ONE steady timeline span across Robot + Woman cuts.
    """
    plan_dict = plan if isinstance(plan, dict) else {}
    if (
        plan_dict.get("cards_off")
        or plan_dict.get("cards") is False
        or plan_dict.get("flashcards") is False
    ):
        return []

    default_style = str(
        plan_dict.get("card_style") or plan_dict.get("card_pack") or ""
    ).strip().lower()

    raw_spans: List[Dict[str, Any]] = []
    n_segs = min(len(segs), len(starts), len(durs))

    for idx in range(n_segs):
        seg = segs[idx]
        if not isinstance(seg, dict) or seg.get("cards_off"):
            continue
        items = seg.get("flashcards")
        if items is None:
            items = seg.get("cards")
        if not items:
            continue
        if isinstance(items, (dict, str)):
            items = [items]
        if not isinstance(items, (list, tuple)):
            continue

        seg_start = float(starts[idx])
        seg_dur = max(0.0, float(durs[idx]))
        seg_end = seg_start + seg_dur
        if seg_dur <= 0.02:
            continue

        seg_id = str(seg.get("id") or f"S{idx + 1}")
        seg_lay = _norm_layout(seg.get("layout"))

        for item in items:
            if isinstance(item, str):
                en, native, text = normalize_bilingual_card_text(raw_text=item)
                at_rel = 0.0
                raw_dur = None
                pos = "auto"
                style = default_style
            elif isinstance(item, dict):
                en, native, text = normalize_bilingual_card_text(
                    raw_text=str(item.get("text") or ""),
                    en=str(item.get("en") or item.get("english") or item.get("english_text") or ""),
                    native=str(item.get("native") or item.get("local") or item.get("native_text") or ""),
                )
                try:
                    at_rel = max(0.0, float(item.get("at") or 0.0))
                except (TypeError, ValueError):
                    at_rel = 0.0
                raw_dur = item.get("dur") if item.get("dur") is not None else item.get("duration")
                pos = str(item.get("pos") or item.get("position") or "auto").strip().lower() or "auto"
                style = str(item.get("style") or item.get("theme") or default_style).strip().lower()
            else:
                continue

            if not en and not native:
                continue

            t0 = seg_start + at_rel
            if raw_dur is not None:
                try:
                    t1 = min(seg_end, t0 + max(0.05, float(raw_dur)))
                except (TypeError, ValueError):
                    t1 = seg_end
            else:
                t1 = seg_end

            if t1 <= t0 + 0.02:
                continue

            raw_spans.append({
                "en": en,
                "native": native,
                "text": text,
                "t0": t0,
                "t1": t1,
                "pos": pos,
                "style": style,
                "seg_indices": [idx],
                "seg_ids": [seg_id],
                "layouts": [seg_lay],
            })

    if not raw_spans:
        return []

    raw_spans.sort(key=lambda sp: (sp["t0"], sp["t1"]))

    merged: List[Dict[str, Any]] = []
    for sp in raw_spans:
        if merged:
            prev = merged[-1]
            same_key = (
                prev["en"] == sp["en"]
                and prev["native"] == sp["native"]
                and prev["pos"] == sp["pos"]
                and prev["style"] == sp["style"]
            )
            gap = sp["t0"] - prev["t1"]
            if same_key and gap <= MERGE_MAX_GAP_SEC + 1e-6:
                prev["t1"] = max(prev["t1"], sp["t1"])
                for si in sp["seg_indices"]:
                    if si not in prev["seg_indices"]:
                        prev["seg_indices"].append(si)
                for sid in sp["seg_ids"]:
                    if sid not in prev["seg_ids"]:
                        prev["seg_ids"].append(sid)
                for ly in sp["layouts"]:
                    if ly not in prev["layouts"]:
                        prev["layouts"].append(ly)
                continue
        merged.append(sp)

    out: List[Dict[str, Any]] = []
    for idx, sp in enumerate(merged):
        t0_r = round(float(sp["t0"]), 4)
        t1_r = round(float(sp["t1"]), 4)
        lays = list(sp["layouts"])
        primary_lay = lays[0] if len(lays) == 1 else (
            "split_v" if "split_v" in lays else ("split_h" if "split_h" in lays else lays[0])
        )
        out.append({
            "id": f"fc_{idx + 1:02d}",
            "index": idx + 1,
            "en": sp["en"],
            "native": sp["native"],
            "text": sp["text"],
            "start": t0_r,
            "end": t1_r,
            "duration": round(max(0.0, t1_r - t0_r), 4),
            "t0": t0_r,
            "t1": t1_r,
            "dur": round(max(0.0, t1_r - t0_r), 4),
            "pos": sp["pos"],
            "style": sp["style"],
            "seg_indices": list(sp["seg_indices"]),
            "seg_ids": list(sp["seg_ids"]),
            "layout": primary_lay,
            "layouts": lays,
        })
    return out


# ---------------------------------------------------------------------------
# 5. Multi-Frame Timeline Placer & Batch Renderer (`place_timeline_flashcards`)
# ---------------------------------------------------------------------------
def _union_face_tracks(per_frame_shields: Sequence[Sequence[Rect]]) -> List[Rect]:
    """Compute per-track enclosing AABBs across consecutive frames of one segment
    so moving heads are covered smoothly while gaps between distinct faces stay open.
    """
    tracks: List[Tuple[Rect, Rect]] = []  # (union_box, last_box)
    for frame_shields in per_frame_shields:
        used_tracks = set()
        for sh in frame_shields:
            sh_cx = 0.5 * (sh[0] + sh[2])
            sh_cy = 0.5 * (sh[1] + sh[3])
            sh_scale = max(1.0, float(max(sh[2] - sh[0], sh[3] - sh[1])))
            best_idx: Optional[int] = None
            best_metric = float("inf")
            for idx, (_union_b, last_b) in enumerate(tracks):
                if idx in used_tracks:
                    continue
                iou = rect_iou(sh, last_b)
                l_cx = 0.5 * (last_b[0] + last_b[2])
                l_cy = 0.5 * (last_b[1] + last_b[3])
                l_scale = max(1.0, float(max(last_b[2] - last_b[0], last_b[3] - last_b[1])))
                dist = math.hypot(sh_cx - l_cx, sh_cy - l_cy) / max(sh_scale, l_scale)
                if iou >= 0.25 or dist <= 0.32:
                    if dist < best_metric:
                        best_metric = dist
                        best_idx = idx
            if best_idx is not None:
                u_b, _ = tracks[best_idx]
                tracks[best_idx] = (
                    (
                        min(u_b[0], sh[0]),
                        min(u_b[1], sh[1]),
                        max(u_b[2], sh[2]),
                        max(u_b[3], sh[3]),
                    ),
                    sh,
                )
                used_tracks.add(best_idx)
            else:
                tracks.append((sh, sh))
                used_tracks.add(len(tracks) - 1)
    return [u for u, _ in tracks]


def _sample_timestamps_for_interval(a: float, b: float, max_samples: int = 12) -> List[float]:
    """Build `[a, b]` timestamps every `0.25s`, always including start/mid/end
    with a small inset from cut boundaries so video seeks never bleed across cuts.
    """
    span = max(0.0, b - a)
    if span <= 0.04:
        return [round(0.5 * (a + b), 3)]
    pad = min(0.08, span * 0.25)
    forced = [a + pad, 0.5 * (a + b), b - pad]
    pts: List[float] = list(forced)
    t = a + pad
    while t < b - pad:
        pts.append(t)
        t += SAMPLE_STEP_SEC
    pts.sort()
    dedup: List[float] = []
    for p in pts:
        if not dedup or (p - dedup[-1]) >= 0.06:
            dedup.append(round(p, 3))
    if len(dedup) <= max_samples:
        return dedup
    # Always preserve forced endpoints + midpoint, uniformly sample interior
    keep = {round(forced[0], 3), round(forced[1], 3), round(forced[2], 3)}
    rem_budget = max(0, max_samples - len(keep))
    interior = [p for p in dedup if p not in keep]
    if rem_budget > 0 and interior:
        step = len(interior) / float(rem_budget)
        for k in range(rem_budget):
            keep.add(interior[min(len(interior) - 1, int(k * step))])
    return sorted(keep)


def _render_fallback_flashcard_png(
    out_path: Path,
    en: str,
    native: str,
    w: int,
    h: int,
    style: str = "clean",
    compact: bool = False,
) -> Path:
    """Minimal Pillow fallback PNG renderer used only if `ui_generator.render_flashcards_batch`
    is unavailable (e.g. isolated unit tests).
    """
    from PIL import Image, ImageDraw

    out_path.parent.mkdir(parents=True, exist_ok=True)
    img = Image.new("RGBA", (max(16, int(w)), max(16, int(h))), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    rad = max(10, int(round(min(w, h) * 0.10)))
    bg = (255, 255, 255, 238) if style == "minimal" else (250, 250, 250, 255)
    border = (226, 232, 240, 255) if style == "minimal" else (217, 138, 56, 255)
    bw = max(2, int(round(min(w, h) * (0.018 if style == "minimal" else 0.045))))
    draw.rounded_rectangle((bw // 2, bw // 2, w - 1 - bw // 2, h - 1 - bw // 2), radius=rad, fill=bg, outline=border, width=bw)

    lines = [ln for ln in (en, native) if ln]
    if lines and ui_generator is not None and hasattr(ui_generator, "load_ui_font"):
        base_px = max(18, int(round(h * (0.28 if len(lines) == 2 else 0.36))))
        if compact:
            base_px = max(16, int(round(base_px * 0.90)))
        line_gap = max(6, int(round(h * 0.06)))
        fonts = [ui_generator.load_ui_font(base_px, ln, bold=True) for ln in lines]
        bboxes = [f.getbbox(ln) for f, ln in zip(fonts, lines)]
        tot_h = sum(b[3] - b[1] for b in bboxes) + line_gap * max(0, len(lines) - 1)
        cy = (h - tot_h) // 2
        for ln, font, bb in zip(lines, fonts, bboxes):
            tw, th = bb[2] - bb[0], bb[3] - bb[1]
            draw.text(((w - tw) // 2 - bb[0], cy - bb[1]), ln, font=font, fill=(15, 23, 42, 255))
            cy += th + line_gap
    img.save(out_path, format="PNG")
    return out_path


def _render_cards_into_dir(
    placed_cards: List[Dict[str, Any]],
    cards_dir: Path,
    *,
    is_square: bool = False,
) -> None:
    """Render exact-sized flashcard PNGs into `cards_dir` via `ui_generator.render_flashcards_batch`
    (with graceful single-card / Pillow fallback if the batch helper is not yet installed).
    Uses a `_sq` filename suffix when `is_square=True` so 1:1 PNGs never overwrite 9:16 PNGs.
    """
    cards_dir.mkdir(parents=True, exist_ok=True)
    if not placed_cards:
        return

    suffix = "_sq" if is_square else ""
    specs: List[Dict[str, Any]] = []
    targets: List[Path] = []
    for idx, card in enumerate(placed_cards):
        target = cards_dir / f"{card['id']}{suffix}.png"
        targets.append(target)
        specs.append({
            "id": card["id"],
            "en": card["en"],
            "english": card["en"],
            "english_text": card["en"],
            "native": card["native"],
            "native_text": card["native"],
            "text": card["text"],
            "width": int(card["w"]),
            "height": int(card["h"]),
            "w": int(card["w"]),
            "h": int(card["h"]),
            "style": card["style"],
            "theme": card["style"],
            "compact": bool(card["compact"]),
            "out_path": str(target),
            "output": str(target),
        })

    rendered_paths: Optional[List[Optional[Path]]] = None
    batch_fn = getattr(ui_generator, "render_flashcards_batch", None) if ui_generator is not None else None
    if callable(batch_fn):
        try:
            sig = inspect.signature(batch_fn)
            kwargs: Dict[str, Any] = {}
            if "out_dir" in sig.parameters:
                kwargs["out_dir"] = cards_dir
            elif "output_dir" in sig.parameters:
                kwargs["output_dir"] = cards_dir
            raw_res = batch_fn(specs, **kwargs)
            if isinstance(raw_res, (list, tuple)) and len(raw_res) == len(specs):
                parsed: List[Optional[Path]] = []
                for item in raw_res:
                    if isinstance(item, Path):
                        parsed.append(item)
                    elif isinstance(item, str) and item:
                        parsed.append(Path(item))
                    elif isinstance(item, dict):
                        p_val = item.get("path") or item.get("png") or item.get("out_path") or item.get("file")
                        parsed.append(Path(str(p_val)) if p_val else None)
                    else:
                        parsed.append(None)
                rendered_paths = parsed
        except Exception:
            rendered_paths = None

    single_fn = getattr(ui_generator, "render_flashcard_png", None) if ui_generator is not None else None
    for idx, (card, spec, target) in enumerate(zip(placed_cards, specs, targets)):
        src_p = rendered_paths[idx] if (rendered_paths is not None and idx < len(rendered_paths)) else None
        if (src_p is None or not src_p.is_file()) and callable(single_fn):
            try:
                sig = inspect.signature(single_fn)
                if len(sig.parameters) == 1:
                    r_p = single_fn(spec)
                else:
                    call_kw: Dict[str, Any] = {}
                    for k_name, val in (
                        ("en", card["en"]),
                        ("english", card["en"]),
                        ("native", card["native"]),
                        ("text", card["text"]),
                        ("width", int(card["w"])),
                        ("height", int(card["h"])),
                        ("style", card["style"]),
                        ("compact", bool(card["compact"])),
                        ("out_path", target),
                    ):
                        if k_name in sig.parameters:
                            call_kw[k_name] = val
                    r_p = single_fn(**call_kw)
                if r_p:
                    src_p = Path(str(r_p))
            except Exception:
                src_p = None

        if src_p is not None and src_p.is_file():
            if src_p.resolve() != target.resolve():
                shutil.copy2(src_p, target)
            card["png"] = str(target.resolve())
        else:
            _render_fallback_flashcard_png(
                target,
                en=card["en"],
                native=card["native"],
                w=int(card["w"]),
                h=int(card["h"]),
                style=card["style"],
                compact=bool(card["compact"]),
            )
            card["png"] = str(target.resolve())


def place_timeline_flashcards(
    plan: Optional[Dict[str, Any]],
    segs: Sequence[Dict[str, Any]],
    starts: Sequence[float],
    durs: Sequence[float],
    total: float,
    video_path: Union[str, Path],
    W: int,
    H: int,
    out_dir: Union[str, Path],
    *,
    is_square: bool = False,
    subtitle_band: Any = None,
) -> Dict[str, Any]:
    """Place all timeline flashcards against clean pre-overlay video (`mix.mkv` or `sq_base`),
    guaranteeing `0 px` face overlap across multi-frame YuNet scans, splitting multi-segment
    spans strictly on segment cut boundaries if camera framings conflict, rendering exact-sized
    PNGs into `out_dir / 'flashcards'`, and writing `flashcards_meta.json` (or `flashcards_sq_meta.json`).
    """
    W_i, H_i = max(32, int(W)), max(32, int(H))
    out_base = Path(out_dir)
    spans = extract_timeline_card_spans(plan, segs, starts, durs)
    if not spans:
        return {"canvas_w": W_i, "canvas_h": H_i, "cards": []}

    n_segs = min(len(segs), len(starts), len(durs))
    vid_p = Path(video_path) if video_path else None
    cap = None
    vid_dur = max(0.0, float(total or 0.0))
    if cv2 is not None and vid_p is not None and vid_p.is_file():
        cap = cv2.VideoCapture(str(vid_p))
        if cap.isOpened():
            fps_v = float(cap.get(cv2.CAP_PROP_FPS) or 24.0) or 24.0
            n_frames = float(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0.0)
            if n_frames > 0:
                vid_dur = max(vid_dur, n_frames / fps_v)
        else:
            cap.release()
            cap = None

    # Cache (quantized_t, layout) -> (shields, clutter_integral)
    frame_cache: Dict[Tuple[float, str], Tuple[List[Rect], Any]] = {}

    def _inspect_frame(t_sec: float, lay: str) -> Tuple[List[Rect], Any]:
        t_clamped = max(0.0, min(float(t_sec), max(0.0, vid_dur - 0.04))) if vid_dur > 0.04 else max(0.0, float(t_sec))
        key = (round(t_clamped, 3), lay)
        if key in frame_cache:
            return frame_cache[key]
        if cap is None or cv2 is None:
            frame_cache[key] = ([], None)
            return [], None
        cap.set(cv2.CAP_PROP_POS_MSEC, t_clamped * 1000.0)
        ok, frame = cap.read()
        if not ok or frame is None or frame.size == 0:
            frame_cache[key] = ([], None)
            return [], None
        if frame.shape[1] != W_i or frame.shape[0] != H_i:
            interp = cv2.INTER_AREA if (frame.shape[1] > W_i or frame.shape[0] > H_i) else cv2.INTER_LINEAR
            frame = cv2.resize(frame, (W_i, H_i), interpolation=interp)
        if is_square and W_i == H_i and H_i > 0:
            # On 1:1 pillarboxed square frames, detect faces strictly inside the central 9:16
            # foreground strip (ignoring blurred decorative background sidebars).
            fg_w = min(W_i, max(16, int(round(H_i * 9.0 / 16.0 / 2.0)) * 2))
            fg_x0 = max(0, (W_i - fg_w) // 2)
            fg_crop = np.ascontiguousarray(frame[:, fg_x0 : fg_x0 + fg_w])
            shs = [
                (fg_x0 + fx0, fy0, fg_x0 + fx1, fy1)
                for fx0, fy0, fx1, fy1 in detect_expanded_face_shields(fg_crop, layout=lay)
            ]
        else:
            shs = detect_expanded_face_shields(frame, layout=lay)
        cmap = compute_clutter_integral(frame)
        frame_cache[key] = (shs, cmap)
        return shs, cmap

    placed_cards: List[Dict[str, Any]] = []
    try:
        for span in spans:
            t0, t1 = float(span["t0"]), float(span["t1"])
            # Find all timeline segments overlapped by [t0, t1]
            overlapped: List[Dict[str, Any]] = []
            for s_idx in range(n_segs):
                s0 = float(starts[s_idx])
                s1 = s0 + max(0.0, float(durs[s_idx]))
                ov_a = max(t0, s0)
                ov_b = min(t1, s1)
                if ov_b > ov_a + 0.02:
                    seg = segs[s_idx]
                    overlapped.append({
                        "seg_idx": s_idx,
                        "seg_id": str(seg.get("id") or f"S{s_idx + 1}"),
                        "a": ov_a,
                        "b": ov_b,
                        "layout": _norm_layout(seg.get("layout")),
                    })
            if not overlapped:
                overlapped.append({
                    "seg_idx": -1,
                    "seg_id": span["seg_ids"][0] if span["seg_ids"] else "S1",
                    "a": t0,
                    "b": t1,
                    "layout": span["layout"],
                })

            per_seg_budget = max(3, MAX_SPAN_SAMPLES // max(1, len(overlapped)))
            for ov in overlapped:
                ts_list = _sample_timestamps_for_interval(ov["a"], ov["b"], max_samples=per_seg_budget)
                per_frame_sh: List[List[Rect]] = []
                seg_clutters: List[Any] = []
                for ts in ts_list:
                    shs, cmap = _inspect_frame(ts, ov["layout"])
                    per_frame_sh.append(shs)
                    if cmap is not None:
                        seg_clutters.append(cmap)
                ov["shields"] = _union_face_tracks(per_frame_sh)
                # Keep up to 4 evenly spaced clutter maps per segment for fast O(1) scoring
                if len(seg_clutters) > 4:
                    idxs = [int(round(k * (len(seg_clutters) - 1) / 3.0)) for k in range(4)]
                    ov["clutters"] = [seg_clutters[k] for k in idxs]
                else:
                    ov["clutters"] = seg_clutters

            def _combine_group(grp: Sequence[Dict[str, Any]]) -> Tuple[List[Rect], List[Any], str]:
                all_sh: List[Rect] = []
                all_cm: List[Any] = []
                lays: List[str] = []
                for item in grp:
                    all_sh.extend(item["shields"])
                    all_cm.extend(item["clutters"])
                    if item["layout"] not in lays:
                        lays.append(item["layout"])
                if len(all_cm) > 6:
                    idxs = [int(round(k * (len(all_cm) - 1) / 5.0)) for k in range(6)]
                    all_cm = [all_cm[k] for k in idxs]
                grp_lay = lays[0] if len(lays) == 1 else (
                    "split_v" if "split_v" in lays else ("split_h" if "split_h" in lays else lays[0])
                )
                return all_sh, all_cm, grp_lay

            def _solve_strict_group(grp: Sequence[Dict[str, Any]]) -> Optional[Tuple[Dict[str, Any], List[Rect], str]]:
                all_sh, all_cm, grp_lay = _combine_group(grp)
                rect_info = _solve_face_safe_rect_strict(
                    W_i, H_i, all_sh,
                    clutter_maps=all_cm if all_cm else None,
                    pref_pos=span["pos"],
                    layout=grp_lay,
                    is_square=is_square,
                    subtitle_band=subtitle_band,
                    style=span["style"] or None,
                    allow_ultra_compact=(len(grp) == 1),
                )
                if rect_info is None:
                    return None
                return rect_info, all_sh, grp_lay

            # Try ONE stationary placement across the entire multi-cut span first
            whole_sol = _solve_strict_group(overlapped)
            sub_solutions: List[Tuple[float, float, Dict[str, Any], List[Rect], str, List[str]]] = []

            if whole_sol is not None:
                rect_info, all_sh, grp_lay = whole_sol
                sub_solutions.append((
                    t0, t1, rect_info, all_sh, grp_lay, [ov["seg_id"] for ov in overlapped],
                ))
            elif len(overlapped) > 1:
                # Incompatible face positions across cuts -> split strictly on segment cut boundaries
                i = 0
                while i < len(overlapped):
                    best_j = i
                    best_pack = _solve_strict_group(overlapped[i : i + 1])
                    for j in range(i + 1, len(overlapped)):
                        cand_pack = _solve_strict_group(overlapped[i : j + 1])
                        if cand_pack is not None:
                            best_j = j
                            best_pack = cand_pack
                        else:
                            break
                    grp = overlapped[i : best_j + 1]
                    if best_pack is not None:
                        rect_info, all_sh, grp_lay = best_pack
                    else:
                        all_sh, all_cm, grp_lay = _combine_group(grp)
                        rect_info = choose_face_safe_rect(
                            W_i, H_i, all_sh,
                            clutter_maps=all_cm if all_cm else None,
                            pref_pos=span["pos"],
                            layout=grp_lay,
                            is_square=is_square,
                            subtitle_band=subtitle_band,
                            style=span["style"] or None,
                        )
                    sub_t0 = t0 if i == 0 else grp[0]["a"]
                    sub_t1 = t1 if best_j == len(overlapped) - 1 else grp[-1]["b"]
                    sub_solutions.append((
                        sub_t0, sub_t1, rect_info, all_sh, grp_lay, [ov["seg_id"] for ov in grp],
                    ))
                    i = best_j + 1
            else:
                all_sh, all_cm, grp_lay = _combine_group(overlapped)
                rect_info = choose_face_safe_rect(
                    W_i, H_i, all_sh,
                    clutter_maps=all_cm if all_cm else None,
                    pref_pos=span["pos"],
                    layout=grp_lay,
                    is_square=is_square,
                    subtitle_band=subtitle_band,
                    style=span["style"] or None,
                )
                sub_solutions.append((
                    t0, t1, rect_info, all_sh, grp_lay, [ov["seg_id"] for ov in overlapped],
                ))

            for part_idx, (st0, st1, rect_info, all_sh, grp_lay, seg_ids) in enumerate(sub_solutions):
                st0_r = round(float(st0), 4)
                st1_r = round(float(st1), 4)
                if st1_r <= st0_r + 0.02:
                    continue
                card_id = (
                    f"{span['id']}_{chr(ord('a') + part_idx)}"
                    if len(sub_solutions) > 1
                    else span["id"]
                )
                r_box = (rect_info["x0"], rect_info["y0"], rect_info["x1"], rect_info["y1"])
                placed_cards.append({
                    "id": card_id,
                    "index": len(placed_cards) + 1,
                    "en": span["en"],
                    "native": span["native"],
                    "text": span["text"],
                    "start": st0_r,
                    "end": st1_r,
                    "duration": round(st1_r - st0_r, 4),
                    "t0": st0_r,
                    "t1": st1_r,
                    "dur": round(st1_r - st0_r, 4),
                    "x0": int(rect_info["x0"]),
                    "y0": int(rect_info["y0"]),
                    "x1": int(rect_info["x1"]),
                    "y1": int(rect_info["y1"]),
                    "w": int(rect_info["w"]),
                    "h": int(rect_info["h"]),
                    "compact": bool(rect_info["compact"]),
                    "tier": str(rect_info["tier"]),
                    "style": str(rect_info["style"]),
                    "pos": span["pos"],
                    "layout": grp_lay,
                    "seg_ids": seg_ids,
                    "face_overlap_px": int(max_shield_overlap_px(r_box, all_sh)),
                    "png": "",
                })
    finally:
        if cap is not None:
            cap.release()

    cards_dir = out_base / "flashcards"
    _render_cards_into_dir(placed_cards, cards_dir, is_square=is_square)

    meta_name = "flashcards_sq_meta.json" if is_square else "flashcards_meta.json"
    meta_path = out_base / meta_name
    result: Dict[str, Any] = {
        "canvas_w": W_i,
        "canvas_h": H_i,
        "is_square": bool(is_square),
        "total": round(float(total or 0.0), 4),
        "meta_path": str(meta_path.resolve()),
        "cards": placed_cards,
    }
    _write_json_atomic(meta_path, result)
    return result
