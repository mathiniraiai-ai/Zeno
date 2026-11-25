"""Multi-Layout Segment Compositor, Auto-Peer Resolver, and Layout-Aware Mark/SFX Engine (`layouts.py`).

Supports all six Zeno layout modes across 9:16 Master (`1080x1920`, `720x1280`)
and 1:1 Square (`1080x1080`, `720x720`) canvases:
  1. `single`                      — Full-frame speaker (static, cosine `1.0->1.06` push-in,
                                     `sudden_in:1.08@t`, `sudden_out`, `flash_zoom:1.12@t`, `focus=x,y`)
                                     or pre-stitched baked split (`slot=top|bottom|left|right`).
  2. `split_v` / `top_bottom`      — Vertical 50/50 split (`vstack`) locked at `z=1` with `14%`
                                     headroom, background-registered stationary frames, colour matching,
                                     and sequential silent listener `peer=` advancing.
  3. `split_h` / `left_right`      — Horizontal 50/50 split (`hstack`) locked at `z=1` with `14%`
                                     headroom, background-registered stationary frames, colour matching,
                                     and sequential silent listener `peer=` advancing.
  4. `top_pip` / `floating_top`    — Full-frame lower speaker (`25.8%` headroom) with a clean
                                     top-center rounded-rectangle floating window (`alphamerge`),
                                     locked at `z=1`.
  5. `chat_bar` / `texting`        — Top frosted chat overlay (`"floating_overlay"` or `"docked"` strip)
                                     with multi-script incoming/outgoing bubbles and `'wrong'/'right'` badges.
  6. `video_call` / `pip_call` /   — Mobile video call chrome (status bar, caller header, live timer,
     `gmeet_grid`                    `alphamerge` rounded/circle PiP or dual grid tiles, and call bar).
"""
from __future__ import annotations

import array
import hashlib
import inspect
import json
import math
import os
import re
import subprocess
import sys
import threading
import wave
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple, Union

_SCRIPTS_DIR = Path(__file__).resolve().parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

import common as _common
from common import (
    DEFAULT_LOUDNESS_LUFS,
    DEFAULT_PEAK_CAP_DB,
    FFMPEG,
    MARKS_DIR,
    ROOT,
    SFX_DIR,
    WORK,
    clip_work_dir,
    ffmpeg,
    ffmpeg_encode,
    hardware_encoder,
    probe,
    read_json,
    resolve,
    resolve_sfx_asset,
)
from ui_generator import (
    UI_CACHE_DIR,
    get_video_call_geometry,
    render_chat_overlay_png,
    render_pip_mask_png,
    render_stop_saying_banner_png,
    render_video_call_chrome_png,
)
import vision_tracker as _vision_tracker
from vision_tracker import (
    TARGET_HEADROOM_PCT,
    analyze_clip_vision,
    compute_paired_split_crop_filters,
    compute_slot_crop_filter,
    derive_registered_crop,
    find_listening_idle_window,
    register_background,
)

EDGE_FADE: float = 0.012       # 12ms audio fade at segment edges to prevent clicks
SPLIT_MAX_ZOOM: float = 1.0    # Split panels (split_h, split_v, top_pip, baked splits) stay locked at z=1
DEFAULT_BANNER_BEAT_DURATION: float = 0.48  # Crisp ~0.48s post-word beat duration for phrase banners
FLASHCARD_SPLIT_HEADROOM: float = 0.24  # Elevated split_h headroom cap (naturally bounded by source ceiling)
_SPLIT_HEADROOM_LOCK = threading.Lock()


def _has_flashcards(
    seg: Optional[Dict[str, Any]] = None,
    plan: Optional[Dict[str, Any]] = None,
) -> bool:
    """Return True when `seg` or `plan` carries active flashcards (and cards are not disabled)."""
    if isinstance(plan, dict):
        if plan.get("cards_off") or plan.get("cards") is False or plan.get("flashcards") is False:
            return False
    if isinstance(seg, dict):
        if seg.get("cards_off") or seg.get("cards") is False or seg.get("flashcards") is False:
            return False
        if seg.get("flashcards") or seg.get("cards"):
            return True
    if isinstance(plan, dict):
        if plan.get("flashcards"):
            return True
        if isinstance(plan.get("cards"), list) and len(plan["cards"]) > 0:
            return True
        for s in plan.get("segments") or ():
            if isinstance(s, dict) and not s.get("cards_off") and (s.get("flashcards") or s.get("cards")):
                return True
    return False


def _is_flashcard_item(item: Any) -> bool:
    """Return True when `item` represents a bilingual flashcard dict (never a Stop-Saying banner)."""
    if not isinstance(item, dict):
        return False
    kind = str(item.get("type") or item.get("kind") or "").strip().lower()
    if kind in ("flashcard", "card", "bilingual_card"):
        return True
    if "native" in item or "en" in item:
        return True
    return False


def _vis_has_real_face_safe(vis: Optional[Dict[str, Any]]) -> bool:
    fn = getattr(_vision_tracker, "_vis_has_real_face", None)
    if callable(fn):
        return bool(fn(vis))
    if not isinstance(vis, dict) or not vis.get("bbox_norm"):
        return False
    if "confidence" in vis:
        return float(vis.get("confidence") or 0.0) > 0.0
    faces = vis.get("faces")
    if isinstance(faces, list) and faces and any(isinstance(f, dict) and "confidence" in f for f in faces):
        return any(isinstance(f, dict) and float(f.get("confidence") or 0.0) > 0.0 for f in faces)
    return True


def _estimate_clip_head_top_y0(
    clip_path: Union[str, Path],
    fallback_focus: Optional[Sequence[float]] = None,
) -> Optional[float]:
    """Estimate normalized face crown `y0` in `[0.04, 0.65]` using the canonical split clip."""
    try:
        p = resolve(clip_path)
        canon_fn = getattr(_vision_tracker, "_canonical_split_clip", None)
        canon = canon_fn(p) if callable(canon_fn) else p
        if canon.is_file():
            vis = analyze_clip_vision(canon) or {}
            if _vis_has_real_face_safe(vis):
                bbox = vis.get("bbox_norm")
                if bbox and len(bbox) >= 4:
                    x0, y0, x1, y1 = [min(1.0, max(0.0, float(v))) for v in bbox[:4]]
                    if x1 > x0 and y1 > y0:
                        return max(0.04, min(0.65, y0))
    except Exception:
        pass
    if fallback_focus and len(fallback_focus) >= 2:
        fy = max(0.10, min(0.75, float(fallback_focus[1])))
        return max(0.06, fy - 0.14)
    return None


def _natural_flashcard_split_headroom(
    live_src: Union[str, Path],
    peer_src: Union[str, Path],
    src_w: int,
    src_h: int,
    peer_w: int,
    peer_h: int,
    live_slot_w: int,
    live_slot_h: int,
    peer_slot_w: int,
    peer_slot_h: int,
    live_focus: Optional[Sequence[float]] = None,
    peer_focus: Optional[Sequence[float]] = None,
) -> float:
    """Compute a flashcard-friendly `split_h` headroom target in `[0.14, 0.24]`, naturally
    capped by what both source clips actually contain above each face so zero black bars
    or top border smear are ever introduced.
    """
    default_hr = round(TARGET_HEADROOM_PCT / 100.0, 4)
    s0_a = max(max(16, live_slot_w) / float(max(16, src_w)), max(16, live_slot_h) / float(max(16, src_h)))
    s0_b = max(max(16, peer_slot_w) / float(max(16, peer_w)), max(16, peer_slot_h) / float(max(16, peer_h)))
    hr_ratio_a = (max(16, src_h) * s0_a) / float(max(16, live_slot_h))
    hr_ratio_b = (max(16, peer_h) * s0_b) / float(max(16, peer_slot_h))

    y0_a = _estimate_clip_head_top_y0(live_src, live_focus)
    y0_b = _estimate_clip_head_top_y0(peer_src, peer_focus)
    avail: List[float] = []
    if y0_a is not None:
        avail.append(y0_a * hr_ratio_a)
    if y0_b is not None:
        avail.append(y0_b * hr_ratio_b)
    if not avail:
        avail.append(0.18 * min(hr_ratio_a, hr_ratio_b))

    natural_cap = min(avail)
    return round(max(default_hr, min(FLASHCARD_SPLIT_HEADROOM, natural_cap)), 4)


def _parse_offset_y(val: Any) -> float:
    """Parse and clamp per-panel vertical framing `offset_y` to `[-0.25, 0.25]`
    (positive shifts the character downward in the panel).
    """
    if val is None or val == "":
        return 0.0
    try:
        v = float(val)
    except (TypeError, ValueError):
        return 0.0
    if not math.isfinite(v):
        return 0.0
    return round(max(-0.25, min(0.25, v)), 4)


def _apply_crop_offset_y_fallback(crop: Dict[str, Any], offset_y: float) -> Dict[str, Any]:
    """Fallback vertical panel shift used only when `vision_tracker.compute_paired_split_crop_filters`
    does not yet accept `live_offset_y` / `peer_offset_y`.
    """
    oy_shift = _parse_offset_y(offset_y)
    if abs(oy_shift) <= 1e-4:
        return dict(crop, offset_y=0.0)
    geom = crop.get("geom")
    slot_size = crop.get("slot_size")
    slot_fn = getattr(_vision_tracker, "slot_filter_from_origin", None)
    if not isinstance(geom, dict) or not slot_size or not callable(slot_fn):
        return dict(crop, offset_y=oy_shift)
    w_in, h_in = int(geom["src"][0]), int(geom["src"][1])
    slot_w, slot_h = int(slot_size[0]), int(slot_size[1])
    oy_new = float(geom["oy"]) - oy_shift * float(slot_h)
    shifted = slot_fn(w_in, h_in, slot_w, slot_h, float(geom["s"]), float(geom["ox"]), oy_new)
    shifted["common_headroom_pct"] = crop.get("common_headroom_pct")
    shifted["extra_zoom"] = crop.get("extra_zoom")
    sf = list(crop.get("slot_focus") or [0.5, 0.36])
    shifted["slot_focus"] = [sf[0], round(min(0.75, max(0.15, float(sf[1]) + oy_shift)), 4)]
    shifted["offset_y"] = oy_shift
    return shifted


def _call_paired_split_crop_filters(
    *,
    live_offset_y: float = 0.0,
    peer_offset_y: float = 0.0,
    live_is_broll: bool = False,
    peer_is_broll: bool = False,
    target_vis_fh: Optional[float] = None,
    target_ey: Optional[float] = None,
    live_min_zoom: float = 1.0,
    peer_min_zoom: float = 1.0,
    **kwargs: Any,
) -> Any:
    """Invoke `compute_paired_split_crop_filters`, passing optional alignment kwargs
    when supported by its signature, and falling back safely otherwise.
    """
    live_oy = _parse_offset_y(live_offset_y)
    peer_oy = _parse_offset_y(peer_offset_y)
    fn = compute_paired_split_crop_filters
    accepts_offset = False
    params: Dict[str, Any] = {}
    has_var_kw = False
    try:
        params = dict(inspect.signature(fn).parameters)
        has_var_kw = any(p.kind == inspect.Parameter.VAR_KEYWORD for p in params.values())
        accepts_offset = (
            ("live_offset_y" in params and "peer_offset_y" in params)
            or has_var_kw
        )
    except (TypeError, ValueError):
        accepts_offset = False

    opt_kw: Dict[str, Any] = {
        "live_is_broll": bool(live_is_broll),
        "peer_is_broll": bool(peer_is_broll),
        "target_vis_fh": target_vis_fh,
        "target_ey": target_ey,
        "live_min_zoom": float(live_min_zoom or 1.0),
        "peer_min_zoom": float(peer_min_zoom or 1.0),
    }
    fwd_kw = {
        k: v for k, v in opt_kw.items()
        if (has_var_kw or k in params) and v is not None
    }

    if accepts_offset:
        try:
            res = fn(live_offset_y=live_oy, peer_offset_y=peer_oy, **fwd_kw, **kwargs)
            if isinstance(res, tuple) and len(res) >= 2:
                lc = dict(res[0])
                pc = dict(res[1])
                lc.setdefault("offset_y", live_oy)
                pc.setdefault("offset_y", peer_oy)
                if len(res) >= 4:
                    return lc, pc, res[2], res[3]
                return lc, pc
            return res
        except TypeError:
            pass

    res = fn(**fwd_kw, **kwargs) if fwd_kw else fn(**kwargs)
    if isinstance(res, tuple) and len(res) >= 2:
        lc = _apply_crop_offset_y_fallback(res[0], live_oy)
        pc = _apply_crop_offset_y_fallback(res[1], peer_oy)
        if len(res) >= 4:
            return lc, pc, list(lc["slot_focus"]), list(pc["slot_focus"])
        return lc, pc
    return res


def _compute_paired_split_crop_filters_for_layout(
    *,
    lay: str,
    seg: Optional[Dict[str, Any]],
    plan: Optional[Dict[str, Any]],
    live_src: Union[str, Path],
    peer_src: Union[str, Path],
    src_w: int,
    src_h: int,
    peer_w: int,
    peer_h: int,
    live_slot_w: int,
    live_slot_h: int,
    peer_slot_w: int,
    peer_slot_h: int,
    live_focus: Optional[Sequence[float]] = None,
    peer_focus: Optional[Sequence[float]] = None,
    return_focus: bool = False,
    live_offset_y: float = 0.0,
    peer_offset_y: float = 0.0,
    force_flashcards: Optional[bool] = None,
    live_is_broll: bool = False,
    peer_is_broll: bool = False,
    target_vis_fh: Optional[float] = None,
    target_ey: Optional[float] = None,
    live_min_zoom: float = 1.0,
    peer_min_zoom: float = 1.0,
) -> Any:
    """Invoke `compute_paired_split_crop_filters`, cleanly raising `split_h` headroom up to
    `0.24` (naturally capped by source clip ceiling so `pad_top == 0` / zero black bars) when
    `seg` or `plan` carries flashcards, and forwarding per-panel `live_offset_y` / `peer_offset_y`
    plus B-roll guard and slot-wide target scale/eyeline overrides.
    """
    default_hr = round(TARGET_HEADROOM_PCT / 100.0, 4)
    live_oy = _parse_offset_y(live_offset_y)
    peer_oy = _parse_offset_y(peer_offset_y)
    has_fc = bool(force_flashcards) if force_flashcards is not None else _has_flashcards(seg, plan)
    elevate = (
        lay == "split_h"
        and has_fc
        and "ZENO_SPLIT_HEADROOM" not in os.environ
    )
    shared_kw: Dict[str, Any] = {
        "live_src": live_src,
        "peer_src": peer_src,
        "src_w": src_w,
        "src_h": src_h,
        "peer_w": peer_w,
        "peer_h": peer_h,
        "live_slot_w": live_slot_w,
        "live_slot_h": live_slot_h,
        "peer_slot_w": peer_slot_w,
        "peer_slot_h": peer_slot_h,
        "live_focus": live_focus,
        "peer_focus": peer_focus,
        "return_focus": return_focus,
        "live_offset_y": live_oy,
        "peer_offset_y": peer_oy,
        "live_is_broll": live_is_broll,
        "peer_is_broll": peer_is_broll,
        "target_vis_fh": target_vis_fh,
        "target_ey": target_ey,
        "live_min_zoom": live_min_zoom,
        "peer_min_zoom": peer_min_zoom,
    }
    if not elevate:
        return _call_paired_split_crop_filters(**shared_kw)

    target_hr = _natural_flashcard_split_headroom(
        live_src=live_src,
        peer_src=peer_src,
        src_w=src_w,
        src_h=src_h,
        peer_w=peer_w,
        peer_h=peer_h,
        live_slot_w=live_slot_w,
        live_slot_h=live_slot_h,
        peer_slot_w=peer_slot_w,
        peer_slot_h=peer_slot_h,
        live_focus=live_focus,
        peer_focus=peer_focus,
    )
    if target_hr <= default_hr + 1e-4:
        return _call_paired_split_crop_filters(**shared_kw)

    with _SPLIT_HEADROOM_LOCK:
        prev_env = os.environ.get("ZENO_SPLIT_HEADROOM")
        try:
            os.environ["ZENO_SPLIT_HEADROOM"] = f"{target_hr:.4f}"
            res = _call_paired_split_crop_filters(**shared_kw)
            live_crop, peer_crop = res[0], res[1]
            pad_a = int(live_crop.get("pad_top") or 0)
            pad_b = int(peer_crop.get("pad_top") or 0)
            excess = max(
                max(0.0, pad_a / float(max(1, live_slot_h)) - max(0.0, live_oy)),
                max(0.0, pad_b / float(max(1, peer_slot_h)) - max(0.0, peer_oy)),
            )
            if excess > 0.001:
                safe_hr = round(max(default_hr, target_hr - excess - 0.002), 4)
                os.environ["ZENO_SPLIT_HEADROOM"] = f"{safe_hr:.4f}"
                res = _call_paired_split_crop_filters(**shared_kw)
            return res
        finally:
            if prev_env is None:
                os.environ.pop("ZENO_SPLIT_HEADROOM", None)
            else:
                os.environ["ZENO_SPLIT_HEADROOM"] = prev_env

FORMATS_LAYOUTS_DIR: Path = ROOT / "formats" / "layouts"

_SILENT_CLIP_NAME_RE = re.compile(r"(?:listen|listening|idle|silent|\bsv\b|\bsfv\b|without[\s_-]*dialog(?:ue)?|extra)", re.I)


def _segment_hold_tail(seg: Optional[Dict[str, Any]]) -> float:
    """Return frozen post-speech tail duration (seconds) from `common.segment_hold_tail` with standalone fallback."""
    fn = getattr(_common, "segment_hold_tail", None)
    if callable(fn):
        return float(fn(seg))
    if not isinstance(seg, dict):
        return 0.0
    try:
        return max(0.0, float(seg.get("hold_tail") or seg.get("hold") or 0.0))
    except (TypeError, ValueError):
        return 0.0


def _segment_audio_end(seg: Optional[Dict[str, Any]]) -> Optional[float]:
    """Return optional relative segment seconds after which clip audio fades cleanly to digital silence."""
    if not isinstance(seg, dict):
        return None
    raw = seg.get("audio_end")
    if raw is None or isinstance(raw, bool):
        return None
    try:
        val = float(raw)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(val) or val <= 0.0:
        return None
    return round(val, 4)


def _segment_hold_span(seg: Optional[Dict[str, Any]]) -> Tuple[Optional[float], Optional[float]]:
    """Return `(hold_start, hold_end)` seconds from `seg['hold_span']` or `hold_start`/`hold_end`."""
    if not isinstance(seg, dict):
        return None, None
    span = seg.get("hold_span")
    if isinstance(span, (list, tuple)) and len(span) >= 2:
        try:
            s0, s1 = float(span[0]), float(span[1])
            if math.isfinite(s0) and math.isfinite(s1):
                return round(max(0.0, s0), 4), round(max(0.0, s1), 4)
        except (TypeError, ValueError):
            pass
    s0_v: Optional[float] = None
    s1_v: Optional[float] = None
    for key, is_start in (("hold_start", True), ("hold_end", False)):
        raw = seg.get(key)
        if raw is not None and not isinstance(raw, bool):
            try:
                val = float(raw)
                if math.isfinite(val):
                    if is_start:
                        s0_v = round(max(0.0, val), 4)
                    else:
                        s1_v = round(max(0.0, val), 4)
            except (TypeError, ValueError):
                pass
    return s0_v, s1_v


def _segment_speed(seg: Optional[Dict[str, Any]]) -> float:
    """Return clamped playback speed factor (`0.25..4.0`, default `1.0`) via `common.segment_speed`."""
    fn = getattr(_common, "segment_speed", None)
    if callable(fn):
        try:
            val = float(fn(seg))
            if math.isfinite(val) and val > 0.0:
                return max(0.25, min(4.0, val))
        except (TypeError, ValueError):
            pass
    if not isinstance(seg, dict):
        return 1.0
    raw = seg.get("speed")
    if raw is None:
        raw = seg.get("rate")
    if raw is None and seg.get("slomo"):
        raw = 0.5
    if raw is None:
        return 1.0
    try:
        s_val = str(raw).strip().lower()
        if s_val.endswith("x"):
            s_val = s_val[:-1].strip()
        val = float(s_val)
    except (TypeError, ValueError):
        return 1.0
    if not math.isfinite(val) or val <= 0.0:
        return 1.0
    return max(0.25, min(4.0, val))


def _segment_is_muted(seg: Optional[Dict[str, Any]]) -> bool:
    """Return True when source audio is explicitly muted via `common.segment_is_muted`."""
    fn = getattr(_common, "segment_is_muted", None)
    if callable(fn):
        return bool(fn(seg))
    if not isinstance(seg, dict):
        return False
    raw = seg.get("mute")
    if isinstance(raw, bool):
        if raw:
            return True
    elif isinstance(raw, (int, float)):
        if raw != 0:
            return True
    elif isinstance(raw, str):
        if raw.strip().lower() in ("1", "true", "yes", "on", "mute", "muted"):
            return True
    audio_mode = str(seg.get("audio_mode") or seg.get("audio") or "").strip().lower()
    return audio_mode in ("mute", "muted", "off", "none", "silent")


def _segment_is_beat(seg: Optional[Dict[str, Any]]) -> bool:
    """Return True for non-spoken visual beats via `common.segment_is_beat`."""
    fn = getattr(_common, "segment_is_beat", None)
    if callable(fn):
        return bool(fn(seg))
    if not isinstance(seg, dict):
        return False
    raw = seg.get("beat")
    if isinstance(raw, bool):
        return raw
    if isinstance(raw, (int, float)):
        return raw != 0
    if isinstance(raw, str):
        return raw.strip().lower() in ("1", "true", "yes", "on", "beat")
    return False


def _segment_play_duration(seg: Dict[str, Any]) -> float:
    """Return rendered moving duration `(end - start) / speed` (excluding `hold_tail`)."""
    fn = getattr(_common, "segment_play_duration", None)
    if callable(fn):
        try:
            val = float(fn(seg))
            if math.isfinite(val) and val > 0.0:
                return max(0.08, val)
        except (TypeError, ValueError):
            pass
    src_dur = max(0.04, float(seg["end"]) - float(seg["start"]))
    spd = _segment_speed(seg)
    return max(0.08, src_dur / spd)


def _segment_duration(seg: Dict[str, Any]) -> float:
    """Return full rendered segment duration (`play_duration + hold_tail`) via `common.segment_duration`."""
    if callable(getattr(_common, "segment_play_duration", None)):
        fn = getattr(_common, "segment_duration", None)
        if callable(fn):
            return float(fn(seg))
    return max(0.08, _segment_play_duration(seg) + _segment_hold_tail(seg))


def _quantize_segment_frames(seg: Dict[str, Any], fps: int) -> Tuple[int, int, int, float, float]:
    """Quantize `(play_frames, hold_frames, frames, play_exact, exact)` with `round()` to match `render.py`/`verify.py`."""
    eff_fps = max(1, int(fps or 24))
    src_dur = max(0.04, float(seg["end"]) - float(seg["start"]))
    is_retimed = abs(_segment_speed(seg) - 1.0) > 1e-3
    play_dur = _segment_play_duration(seg) if is_retimed else max(0.08, src_dur)
    hold_tail = _segment_hold_tail(seg)
    play_frames = max(1, int(round(play_dur * eff_fps)))
    if hold_tail > 0.001:
        frames = max(play_frames, int(round(max(0.08, _segment_duration(seg)) * eff_fps)))
    else:
        frames = play_frames
    hold_frames = max(0, frames - play_frames)
    return play_frames, hold_frames, frames, play_frames / float(eff_fps), frames / float(eff_fps)


def _normalize_mark_type(mark_type: str) -> str:
    """Normalize mark classification strings into `'right'` or `'wrong'`."""
    fn = getattr(_common, "_normalize_mark_type", None) or getattr(_common, "normalize_mark_type", None)
    if callable(fn):
        return str(fn(mark_type))
    low = str(mark_type or "wrong").strip().lower()
    return "right" if low in ("right", "check", "tick", "correct", "ok", "green") else "wrong"


def resolve_format_layouts_dir() -> Path:
    """Return the active format layouts directory (`ROOT / 'formats' / 'layouts'`)."""
    return FORMATS_LAYOUTS_DIR


def load_layout_format_spec(layout_id: str) -> Dict[str, Any]:
    """Load `<layout_id>.json` from `ROOT / 'formats' / 'layouts'`.
    Returns an empty dictionary when absent.
    """
    clean_id = re.sub(r"[^a-zA-Z0-9_-]", "", str(layout_id or "").strip())
    if not clean_id:
        return {}
    cand = FORMATS_LAYOUTS_DIR / f"{clean_id}.json"
    if cand.is_file():
        try:
            data = read_json(cand)
            if isinstance(data, dict):
                return data
        except Exception:
            pass
    return {}


def even(x: float) -> int:
    """Round `x` to the nearest positive even integer (>= 2) for YUV420p compatibility."""
    return max(2, int(round(float(x) / 2.0)) * 2)


def is_square_canvas(W: int, H: int) -> bool:
    """Return True when `(W, H)` represents a ~1:1 Square canvas (`1080x1080`, `720x720`)."""
    w_i, h_i = max(2, int(W)), max(2, int(H))
    return abs(w_i - h_i) <= w_i * 0.15


def get_square_1x1_geometry(sq_size: int = 1080, blur_sigma: float = 18.0) -> Dict[str, Any]:
    """Return exact 1:1 Square (`1080x1080`) framing geometry for compositing a 9:16 master
    reel over a 1:1 center-cropped Gaussian-blurred background.
    """
    sq = even(sq_size)
    fg_w = even(sq * 9.0 / 16.0)
    bg_h = even(sq * 16.0 / 9.0)
    blur_scale = 4
    sq_small = even(sq // blur_scale)
    bg_h_small = even(bg_h // blur_scale)
    sigma_small = max(1.0, float(blur_sigma) / float(blur_scale))
    return {
        "sq_size": sq,
        "fg_w": fg_w,
        "fg_h": sq,
        "fg_x": (sq - fg_w) // 2,
        "fg_y": 0,
        "bg_w": sq,
        "bg_h": bg_h,
        "blur_scale": blur_scale,
        "sq_small": sq_small,
        "bg_h_small": bg_h_small,
        "sigma_small": round(sigma_small, 3),
    }


def get_layout_geometry(
    layout: Optional[str],
    W: int = 1080,
    H: int = 1920,
    seg: Optional[Dict[str, Any]] = None,
    plan: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Return normalized pixel geometry for `layout` on either a 9:16 Master or 1:1 Square canvas."""
    W_e, H_e = even(W), even(H)
    lay = normalize_layout_name(layout)
    square = is_square_canvas(W_e, H_e)

    if lay == "split_v":
        top_h = even(H_e // 2)
        return {
            "layout": "split_v",
            "canvas": (W_e, H_e),
            "is_square": square,
            "top": {"x": 0, "y": 0, "w": W_e, "h": top_h},
            "bottom": {"x": 0, "y": top_h, "w": W_e, "h": H_e - top_h},
            "divider_h": 4,
            "headroom_pct": TARGET_HEADROOM_PCT,
        }
    if lay == "split_h":
        left_w = even(W_e // 2)
        has_page = isinstance((plan or {}).get("page"), dict)
        return {
            "layout": "split_h",
            "canvas": (W_e, H_e),
            "is_square": square,
            "left": {"x": 0, "y": 0, "w": left_w, "h": H_e},
            "right": {"x": left_w, "y": 0, "w": W_e - left_w, "h": H_e},
            "divider_w": 0 if has_page else 4,
            "headroom_pct": round(FLASHCARD_SPLIT_HEADROOM * 100.0, 2) if _has_flashcards(seg, plan) else TARGET_HEADROOM_PCT,
        }
    if lay == "top_pip":
        if square:
            pw = even(W_e * 0.52)
            ph = even(H_e * 0.26)
            py = int(round(H_e * 0.0185))
            prad = max(12, int(round(30 * (W_e / 1080.0))))
        else:
            pw = even(W_e * 0.6944)
            ph = even(H_e * 0.2188)
            py = int(round(H_e * 0.0104))
            prad = max(12, int(round(36 * (W_e / 1080.0))))
        px = (W_e - pw) // 2
        return {
            "layout": "top_pip",
            "canvas": (W_e, H_e),
            "is_square": square,
            "pip": {"x": px, "y": py, "w": pw, "h": ph, "radius": prad},
            "bg": {"x": 0, "y": 0, "w": W_e, "h": H_e, "headroom_pct": 25.8},
        }
    if lay in ("video_call", "pip_call", "gmeet_grid"):
        peer = (seg or {}).get("peer") if isinstance((seg or {}).get("peer"), dict) else {}
        pip_shape = str((seg or {}).get("pip_shape") or peer.get("shape") or "rounded")
        variant = "gmeet_grid" if lay == "gmeet_grid" else "pip_call"
        vc_geo = get_video_call_geometry(W_e, H_e, variant=variant, pip_shape=pip_shape)
        vc_geo["layout"] = lay
        vc_geo["canvas"] = (W_e, H_e)
        vc_geo["is_square"] = square
        return vc_geo
    if lay == "chat_bar":
        chat_spec = (seg or {}).get("chat") if isinstance((seg or {}).get("chat"), dict) else {}
        mode = str(chat_spec.get("mode") or "floating_overlay")
        dock_frac = 0.22
        dock_h = even(H_e * dock_frac) if mode.lower() in ("docked", "dock", "top_strip") else 0
        return {
            "layout": "chat_bar",
            "canvas": (W_e, H_e),
            "is_square": square,
            "mode": mode,
            "dock_h": dock_h,
            "video_h": H_e - dock_h,
        }
    return {
        "layout": "single",
        "canvas": (W_e, H_e),
        "is_square": square,
        "headroom_pct": TARGET_HEADROOM_PCT,
    }


def _resolve_active_slot(seg: Optional[Dict[str, Any]], default: str = "") -> str:
    """Resolve active panel/slot identifier (`top|bottom|left|right|...`) from `seg` fields or `extras`."""
    if not isinstance(seg, dict):
        return default
    for key in ("slot", "active", "active_slot", "panel"):
        val = str(seg.get(key) or "").strip().lower()
        if val:
            return val
    extras = seg.get("extras")
    if isinstance(extras, dict):
        for key in ("slot", "active", "active_slot", "panel"):
            val = str(extras.get(key) or "").strip().lower()
            if val:
                return val
    elif isinstance(extras, str) and extras.strip():
        m = re.search(r"\b(?:slot|active|panel)\s*=\s*([A-Za-z0-9_]+)\b", extras, re.I)
        if m:
            return m.group(1).strip().lower()
    elif isinstance(extras, (list, tuple)):
        for item in extras:
            m = re.search(r"\b(?:slot|active|panel)\s*=\s*([A-Za-z0-9_]+)\b", str(item), re.I)
            if m:
                return m.group(1).strip().lower()
    return default


def _load_clip_vision_meta(src: Optional[Path], seg: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Load vision metadata for `src`, refining to segment `[start, end]` when `mixed_framing` is True."""
    vis: Dict[str, Any] = {}
    if src is not None and src.is_file():
        try:
            vis = dict(analyze_clip_vision(src) or {})
            if vis.get("mixed_framing") and isinstance(seg, dict):
                try:
                    st_s = float(seg.get("start") or 0.0)
                    en_s = float(seg.get("end") or 0.0)
                    file_dur = float(vis.get("duration") or 0.0)
                    if en_s > st_s + 0.1 and (file_dur <= 0.0 or (en_s - st_s) < file_dur * 0.85):
                        vis_win = analyze_clip_vision(src, start_sec=st_s, end_sec=en_s) or {}
                        if vis_win:
                            vis = dict(vis_win)
                except (TypeError, ValueError):
                    pass
        except Exception:
            vis = {}
    if src is not None:
        for cand_json in (
            (clip_work_dir(src) / "vision.json") if src.is_file() else None,
            src.parent / "vision.json",
        ):
            if cand_json is not None and cand_json.is_file():
                try:
                    raw_j = read_json(cand_json)
                    if isinstance(raw_j, dict):
                        clips_map = raw_j.get("clips")
                        if isinstance(clips_map, dict) and src.stem in clips_map and isinstance(clips_map[src.stem], dict):
                            vis.update(clips_map[src.stem])
                        elif "baked_split" in raw_j or "seam_frac" in raw_j or "shot_type" in raw_j:
                            if not raw_j.get("path") or Path(str(raw_j["path"])).name == src.name:
                                vis.update(raw_j)
                except Exception:
                    pass
    if isinstance(seg, dict):
        for k in ("baked_split", "seam_frac", "seam_band_frac", "shot_type"):
            if seg.get(k) is not None:
                vis[k] = seg[k]
    return vis


def _resolve_baked_split_spec(
    seg: Dict[str, Any],
    src: Optional[Path] = None,
) -> Optional[Dict[str, Any]]:
    """Return `{'kind': 'split_v'|'split_h', 'active': 'top'|'bottom'|'left'|'right',
    'seam_frac': float, 'seam_band_frac': [lo, hi], 'vis': dict}` when a `single` segment
    represents a pre-stitched / baked split clip with a known active panel; otherwise `None`.
    """
    if normalize_layout_name(seg.get("layout")) != "single":
        return None

    explicit_panel = _resolve_active_slot(seg, default="")
    vis = _load_clip_vision_meta(src, seg)

    raw_kind = str(vis.get("baked_split") or "").strip().lower()
    if raw_kind in ("split_v_baked", "vertical", "top_bottom", "vstack"):
        raw_kind = "split_v"
    elif raw_kind in ("split_h_baked", "horizontal", "left_right", "hstack"):
        raw_kind = "split_h"
    if raw_kind not in ("split_v", "split_h"):
        st = str(vis.get("shot_type") or "").strip().lower()
        if st == "split_v_baked":
            raw_kind = "split_v"
        elif st == "split_h_baked":
            raw_kind = "split_h"
        else:
            raw_kind = ""

    if explicit_panel in ("top", "bottom"):
        kind = "split_v"
        active_panel = explicit_panel
    elif explicit_panel in ("left", "right"):
        kind = "split_h"
        active_panel = explicit_panel
    elif raw_kind in ("split_v", "split_h"):
        kind = raw_kind
        sf_probe = min(0.65, max(0.35, float(vis.get("seam_frac") or 0.50)))
        seg_foc = seg.get("focus")
        if isinstance(seg_foc, (list, tuple)) and len(seg_foc) >= 2:
            fx_e, fy_e = float(seg_foc[0]), float(seg_foc[1])
            if kind == "split_v" and abs(fy_e - sf_probe) >= 0.02:
                active_panel = "top" if fy_e < sf_probe else "bottom"
            elif kind == "split_h" and abs(fx_e - sf_probe) >= 0.02:
                active_panel = "left" if fx_e < sf_probe else "right"
            else:
                return None
        else:
            return None
    else:
        return None

    if seg.get("seam_frac") is not None:
        sf = round(min(0.65, max(0.35, float(seg["seam_frac"]))), 4)
        band = [sf, sf]
    elif raw_kind == kind and vis.get("seam_frac") is not None:
        sf = round(min(0.65, max(0.35, float(vis["seam_frac"]))), 4)
        raw_b = vis.get("seam_band_frac")
        if isinstance(raw_b, (list, tuple)) and len(raw_b) >= 2:
            band = [
                round(min(0.65, max(0.35, float(raw_b[0]))), 4),
                round(min(0.65, max(0.35, float(raw_b[1]))), 4),
            ]
        else:
            band = [sf, sf]
    else:
        sf = 0.50
        band = [0.50, 0.50]

    return {
        "kind": kind,
        "active": active_panel,
        "seam_frac": sf,
        "seam_band_frac": band,
        "vis": vis,
    }


def normalize_layout_name(layout: Optional[str]) -> str:
    """Normalize layout aliases into canonical Zeno layout identifiers."""
    raw = str(layout or "single").strip().lower()
    if ":" in raw:
        raw = raw.split(":", 1)[0].strip()
    mapping = {
        "": "single",
        "single": "single",
        "full": "single",
        "fullscreen": "single",
        "split_v": "split_v",
        "top_bottom": "split_v",
        "vstack": "split_v",
        "vertical_split": "split_v",
        "split": "split_v",
        "split_h": "split_h",
        "left_right": "split_h",
        "hstack": "split_h",
        "horizontal_split": "split_h",
        "chat_bar": "chat_bar",
        "texting": "chat_bar",
        "chat": "chat_bar",
        "video_call": "video_call",
        "pip_call": "pip_call",
        "pip": "pip_call",
        "gmeet_grid": "gmeet_grid",
        "gmeet": "gmeet_grid",
        "grid_call": "gmeet_grid",
        "top_pip": "top_pip",
        "pip_top": "top_pip",
        "floating_top": "top_pip",
        "floating_pip": "top_pip",
    }
    return mapping.get(raw, "single")


def _infer_segment_speaker(seg: Dict[str, Any], plan: Dict[str, Any]) -> str:
    """Infer canonical speaker key for `seg` from explicit field, note, or `plan['speakers']`."""
    sp = str(seg.get("speaker") or seg.get("sp") or "").strip()
    if sp:
        return sp

    note = str(seg.get("note") or seg.get("text") or "").strip()
    speakers_cfg = plan.get("speakers") or {}
    if isinstance(speakers_cfg, dict):
        for k, v in speakers_cfg.items():
            disp = v.get("name", k) if isinstance(v, dict) else str(v)
            if re.search(rf"\b{re.escape(str(k))}\b|\b{re.escape(str(disp))}\b", note, re.I):
                return str(k)

    if re.search(r"\b(?:miss\s+)?nova\b|\bcoach\b|\bteacher\b", note, re.I):
        return "nova"
    if re.search(r"\bravi\b|\blearner\b|\bstudent\b", note, re.I):
        return "ravi"

    m = re.match(r"^(?:L\d+[a-z]?\s+)?([A-Z][A-Za-z0-9_]{1,18})\b", note)
    if m and m.group(1).lower() not in ("almost", "perfect", "nice", "yes", "no", "the", "those", "real"):
        return m.group(1).lower()
    return ""


def _extract_dialogue_from_note(note: str) -> str:
    """Extract clean dialogue text or quoted phrase from a segment note."""
    if not note:
        return ""
    quotes = re.findall(r'"([^"]{2,120})"', note)
    if quotes:
        return " ".join(quotes)
    cleaned = re.sub(r"^L\d+[a-z]?\s+", "", note.strip())
    cleaned = re.sub(r"^(?:Miss\s+Nova|Nova|Ravi)\s*:?\s*", "", cleaned, flags=re.I)
    cleaned = re.sub(r"\([^)]*\)", "", cleaned)
    cleaned = re.sub(r"\[(?:WRONG|RIGHT|CORRECT)\]", "", cleaned, flags=re.I)
    if "/" in cleaned:
        cleaned = cleaned.split("/")[0]
    return cleaned.strip() or note.strip()


def _speaker_display_name(sp_key: str, plan: Dict[str, Any]) -> str:
    if not sp_key:
        return "Speaker"
    speakers_cfg = plan.get("speakers") or {}
    if isinstance(speakers_cfg, dict) and sp_key in speakers_cfg:
        val = speakers_cfg[sp_key]
        if isinstance(val, dict):
            return str(val.get("name") or val.get("label") or sp_key.title())
        return str(val)
    low = sp_key.lower()
    if low in ("nova", "miss_nova", "miss nova"):
        return "Miss Nova"
    if low == "ravi":
        return "Ravi"
    return sp_key.title()


def _advance_listener_span(
    peer_src: Any,
    base_span: Sequence[float],
    seg_dur: float,
    cursors: Dict[str, float],
) -> Tuple[List[float], float]:
    """Walk sequentially through a silent listener clip or idle window turn after turn
    so listener footage never restarts from the exact same frame on consecutive turns.
    """
    peer_path = resolve(peer_src) if peer_src else None
    key = str(peer_path or peer_src)
    s_lo = max(0.0, float(base_span[0]) if len(base_span) >= 1 else 0.0)
    s_hi = max(s_lo + 0.30, float(base_span[1]) if len(base_span) >= 2 else s_lo + 3.0)

    if peer_path is not None and peer_path.is_file() and _SILENT_CLIP_NAME_RE.search(peer_path.stem):
        try:
            clip_dur = float(probe(peer_path).get("duration") or 0.0)
            if clip_dur >= 0.5:
                s_lo = 0.02 if clip_dur > 0.4 else 0.0
                s_hi = max(s_lo + 0.30, clip_dur - 0.02)
        except Exception:
            pass

    avail = max(0.30, s_hi - s_lo)
    cur = cursors.get(key, s_lo)
    if cur < s_lo or cur >= s_hi - 0.15:
        cur = s_lo
    elif cur + seg_dur > s_hi + 0.05 and seg_dur <= avail:
        cur = s_lo

    end_t = min(s_hi, cur + max(0.30, seg_dur))
    cursors[key] = end_t if end_t < s_hi - 0.15 else s_lo
    return [round(cur, 4), round(end_t, 4)], round(cur, 4)


def resolve_auto_peers(plan: Dict[str, Any]) -> Dict[str, Any]:
    """Automatically pair `peer` from adjacent speaker turns when a segment uses
    `split_v`, `split_h`, `top_pip`, `video_call`, `pip_call`, or `gmeet_grid` without an
    explicit `peer` dict, advance silent listener spans sequentially across turns, and
    auto-populate `chat` bubbles for `chat_bar` segments if omitted.
    """
    segs: List[Dict[str, Any]] = plan.get("segments") or []
    if not segs:
        return plan

    marks_by_seg: Dict[str, List[Dict[str, Any]]] = {}
    for m in plan.get("marks") or []:
        sid = m.get("segment")
        if sid:
            marks_by_seg.setdefault(str(sid), []).append(m)

    # Pass 1: Normalize layout names and infer speaker keys
    speaker_order: List[str] = []
    for i, s in enumerate(segs):
        s.setdefault("id", f"S{i + 1}")
        raw_lay = str(s.get("layout") or "single")
        if ":docked" in raw_lay.lower():
            s.setdefault("chat", {})
            if isinstance(s["chat"], dict):
                s["chat"].setdefault("mode", "docked")
        s["layout"] = normalize_layout_name(raw_lay)
        sp = _infer_segment_speaker(s, plan)
        if sp:
            s["speaker"] = sp
            if sp.lower() not in speaker_order:
                speaker_order.append(sp.lower())

    # Assign stable default slot per speaker so characters never flip panels mid-scene
    speaker_slot_v: Dict[str, str] = {}
    speaker_slot_h: Dict[str, str] = {}
    for idx, sp_low in enumerate(speaker_order):
        speaker_slot_v[sp_low] = "top" if idx == 0 else "bottom"
        speaker_slot_h[sp_low] = "left" if idx == 0 else "right"
    if "nova" in speaker_slot_v and "ravi" in speaker_slot_v:
        speaker_slot_v["nova"], speaker_slot_v["ravi"] = "top", "bottom"
        speaker_slot_h["nova"], speaker_slot_h["ravi"] = "left", "right"

    listener_cursors: Dict[str, float] = {}

    # Pass 2: Resolve auto-peers, advance silent listener cursors, and build chat bubbles
    for i, s in enumerate(segs):
        lay = s["layout"]
        sp = str(s.get("speaker") or "").lower()
        seg_dur = max(0.10, _segment_play_duration(s))

        if lay in ("split_v", "split_h", "video_call", "pip_call", "gmeet_grid", "top_pip"):
            resolved_slot = _resolve_active_slot(s)
            if resolved_slot:
                s["slot"] = resolved_slot
            elif lay == "split_h":
                s["slot"] = speaker_slot_h.get(sp, "left" if i % 2 == 0 else "right")
            else:
                s["slot"] = speaker_slot_v.get(sp, "top" if i % 2 == 0 else "bottom")

            peer = s.get("peer")
            if not isinstance(peer, dict) or not peer.get("src"):
                cand_seg: Optional[Dict[str, Any]] = None
                for dist in range(1, len(segs) + 1):
                    for j in (i - dist, i + dist):
                        if 0 <= j < len(segs):
                            other = segs[j]
                            other_sp = str(other.get("speaker") or "").lower()
                            if sp and other_sp and other_sp != sp:
                                cand_seg = other
                                break
                            if (not sp or not other_sp) and (other.get("src") != s.get("src") or j != i):
                                cand_seg = other
                                break
                    if cand_seg is not None:
                        break

                if cand_seg is None:
                    cand_seg = s

                peer_src = cand_seg["src"]
                peer_sp = str(cand_seg.get("speaker") or ("ravi" if sp == "nova" else "nova"))

                avoid_spans = [(float(s.get("start", 0.0)), float(s.get("end", 5.0)))]
                if cand_seg is not s and cand_seg.get("src") == peer_src:
                    avoid_spans.append((float(cand_seg.get("start", 0.0)), float(cand_seg.get("end", 5.0))))

                idle_win = find_listening_idle_window(peer_src, speech_spans=avoid_spans)
                adv_span, adv_still = _advance_listener_span(
                    peer_src, idle_win["idle_span"], seg_dur, listener_cursors
                )
                peer_focus = list(cand_seg.get("focus") or [0.5, 0.38])

                if peer_src == s.get("src") and abs(peer_focus[0] - float((s.get("focus") or [0.5, 0.38])[0])) < 0.05:
                    vis = analyze_clip_vision(peer_src)
                    if vis.get("face_count", 1) >= 2 and len(vis.get("faces", [])) >= 2:
                        f0, f1 = vis["faces"][0]["focus"], vis["faces"][1]["focus"]
                        s_fx = float((s.get("focus") or f0)[0])
                        peer_focus = f1 if abs(s_fx - f0[0]) <= abs(s_fx - f1[0]) else f0

                opp_slot = (
                    ("right" if s["slot"] == "left" else "left")
                    if lay == "split_h"
                    else ("bottom" if s["slot"] == "top" else "top")
                )

                merged_peer = dict(peer) if isinstance(peer, dict) else {}
                merged_peer.setdefault("src", peer_src)
                merged_peer.setdefault("speaker", peer_sp)
                merged_peer.setdefault("still_at", adv_still)
                merged_peer.setdefault("idle_span", adv_span)
                merged_peer.setdefault("focus", peer_focus)
                merged_peer.setdefault("slot", opp_slot)
                if abs(_parse_offset_y(cand_seg.get("offset_y"))) > 1e-4:
                    merged_peer.setdefault("offset_y", _parse_offset_y(cand_seg.get("offset_y")))
                s["peer"] = merged_peer
            else:
                has_explicit_span = (
                    isinstance(peer.get("idle_span"), (list, tuple)) and len(peer["idle_span"]) >= 2
                )
                if has_explicit_span:
                    p_key = str(resolve(peer["src"]) if peer.get("src") else "")
                    if p_key:
                        listener_cursors[p_key] = float(peer["idle_span"][1])
                    peer.setdefault("still_at", round(float(peer["idle_span"][0]), 4))
                elif peer.get("still_at") is None:
                    idle_win = find_listening_idle_window(peer["src"])
                    adv_span, adv_still = _advance_listener_span(
                        peer["src"], idle_win["idle_span"], seg_dur, listener_cursors
                    )
                    peer["still_at"] = adv_still
                    peer.setdefault("idle_span", adv_span)
                peer.setdefault("focus", [0.5, 0.38])

        elif lay == "chat_bar":
            chat_spec = s.get("chat")
            has_msgs = (
                (isinstance(chat_spec, dict) and bool(chat_spec.get("messages") or chat_spec.get("text")))
                or (isinstance(chat_spec, list) and len(chat_spec) > 0)
            )
            if not has_msgs:
                seg_marks = marks_by_seg.get(str(s["id"]), [])
                status = seg_marks[0].get("type") if seg_marks else None
                note_txt = str(s.get("note") or "")
                if not status:
                    if re.search(r"wrong|mistake", note_txt, re.I):
                        status = "wrong"
                    elif re.search(r"right|correct|perfect", note_txt, re.I):
                        status = "right"

                messages: List[Dict[str, Any]] = []
                if i > 0:
                    prev = segs[i - 1]
                    prev_sp = _speaker_display_name(_infer_segment_speaker(prev, plan), plan)
                    prev_txt = _extract_dialogue_from_note(str(prev.get("note") or ""))
                    if prev_txt:
                        messages.append({
                            "speaker": prev_sp,
                            "role": "incoming" if "nova" in prev_sp.lower() else "outgoing",
                            "text": prev_txt,
                            "status": None,
                            "at": 0.0,
                        })

                cur_sp = _speaker_display_name(sp or "ravi", plan)
                cur_txt = str(s.get("label") or "") or _extract_dialogue_from_note(note_txt) or "..."
                pop_at = float(seg_marks[0].get("at", 0.15)) if seg_marks else 0.10
                pop_at = max(0.0, min(pop_at, max(0.0, _segment_play_duration(s) * 0.4)))
                messages.append({
                    "speaker": cur_sp,
                    "role": "incoming" if "nova" in cur_sp.lower() else "outgoing",
                    "text": cur_txt,
                    "status": _normalize_mark_type(status) if status else None,
                    "at": round(pop_at, 3) if len(messages) > 0 else 0.0,
                })

                mode = chat_spec.get("mode", "floating_overlay") if isinstance(chat_spec, dict) else "floating_overlay"
                s["chat"] = {"mode": mode, "messages": messages}

    return plan


def _file_mtime_sig(path_like: Any) -> str:
    try:
        p = resolve(path_like)
        if p.exists():
            st = p.stat()
            return f"{p.name}:{st.st_size}:{int(st.st_mtime)}"
    except Exception:
        pass
    return str(path_like)


_STATIC_MOTION_CACHE: Dict[str, float] = {}


def detect_static_camera_motion(
    video_path: Union[str, Path],
    start_sec: float,
    end_sec: float,
) -> float:
    """Measure peripheral background camera motion (0-255 grayscale delta) using OpenCV.

    Masks out the central speaker region (`x in [0.22..0.78], y in [0.12..0.88]`) so a
    talking speaker on a locked tripod registers as a static camera (`score < 2.8`).
    """
    resolved = resolve(video_path)
    key = f"{resolved}:{start_sec:.2f}-{end_sec:.2f}"
    if key in _STATIC_MOTION_CACHE:
        return _STATIC_MOTION_CACHE[key]

    try:
        import cv2
        import numpy as np
        cv2.setNumThreads(2)
    except ImportError:
        _STATIC_MOTION_CACHE[key] = 0.0
        return 0.0

    if not resolved.is_file():
        _STATIC_MOTION_CACHE[key] = 0.0
        return 0.0

    cap = cv2.VideoCapture(str(resolved))
    if not cap.isOpened():
        _STATIC_MOTION_CACHE[key] = 0.0
        return 0.0

    dur = max(0.2, float(end_sec) - float(start_sec))
    n_samples = min(6, max(3, int(dur * 2.0)))
    deltas: List[float] = []
    prev_bg = None

    for k in range(n_samples):
        t = float(start_sec) + (k + 0.5) / n_samples * dur
        cap.set(cv2.CAP_PROP_POS_MSEC, t * 1000.0)
        ok, frame = cap.read()
        if not ok or frame is None:
            continue
        small = cv2.resize(cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY), (96, 96)).astype("float32")
        if prev_bg is not None:
            diff = np.abs(small - prev_bg)
            mask = np.ones_like(diff, dtype=bool)
            mask[12:84, 21:75] = False
            deltas.append(float(np.mean(diff[mask])))
        prev_bg = small

    cap.release()
    score = round(float(sum(deltas) / len(deltas)), 3) if deltas else 0.0
    _STATIC_MOTION_CACHE[key] = score
    return score


_RMS_GAIN_CACHE: Dict[str, float] = {}
_COLOR_MATCH_CACHE: Dict[str, Optional[str]] = {}


def _measure_clip_rms_gain_db(src: Path, target_dbfs: float = -18.0) -> float:
    """Measure active-speech RMS of `src` and return a single per-clip gain offset in dB
    so natural intra-sentence dynamics are preserved without double loudness normalization.
    """
    sig = _file_mtime_sig(src)
    if sig in _RMS_GAIN_CACHE:
        return _RMS_GAIN_CACHE[sig]
    try:
        wdir = clip_work_dir(src)
        wav_path = wdir / "audio16k.wav"
        if not wav_path.is_file():
            wav_path = wdir / "audio.wav"
        if not wav_path.is_file():
            _RMS_GAIN_CACHE[sig] = 0.0
            return 0.0
        with wave.open(str(wav_path), "rb") as wf:
            sr = wf.getframerate() or 16000
            raw = wf.readframes(wf.getnframes())
        samples = array.array("h")
        samples.frombytes(raw[: (len(raw) // 2) * 2])
        win = max(160, sr // 20)  # 50ms windows
        active_sq: List[float] = []
        for idx in range(0, len(samples) - win, win):
            chunk = samples[idx : idx + win]
            ms = sum(float(s) * float(s) for s in chunk) / len(chunk)
            if ms > 1e-3:
                dbfs = 10.0 * math.log10(ms / (32768.0 * 32768.0))
                if dbfs > -42.0:
                    active_sq.append(ms)
        if not active_sq:
            _RMS_GAIN_CACHE[sig] = 0.0
            return 0.0
        mean_ms = sum(active_sq) / len(active_sq)
        clip_dbfs = 10.0 * math.log10(mean_ms / (32768.0 * 32768.0))
        gain_db = round(max(-8.0, min(10.0, target_dbfs - clip_dbfs)), 2)
        _RMS_GAIN_CACHE[sig] = gain_db
        return gain_db
    except Exception:
        _RMS_GAIN_CACHE[sig] = 0.0
        return 0.0


def compute_face_color_match_filter(
    seg: Dict[str, Any],
    plan: Optional[Dict[str, Any]] = None,
    target_luma: float = 132.0,
    target_rg: float = 1.08,
    target_bg: float = 0.92,
) -> Optional[str]:
    """Measure exposure (Y) and white balance (R/G, B/G) inside the inner 60% of the
    speaker's face ROI and return a clamped FFmpeg `eq` + `colorbalance` filter (plus
    optional `.cube` LUT) ONLY when explicitly requested (`color_match=True` or `lut` set).
    """
    lut_spec = seg.get("lut") or (plan or {}).get("lut") or ((plan or {}).get("canvas") or {}).get("lut")
    color_enabled = bool(seg.get("color_match") is True or (plan or {}).get("color_match") is True)
    if not color_enabled and not lut_spec:
        return None
    src = resolve(seg["src"])
    if not src.is_file():
        return None
    start_s = float(seg.get("start", 0.0))
    end_s = float(seg.get("end", start_s + 1.0))
    cache_key = f"{_file_mtime_sig(src)}|{start_s:.2f}|{end_s:.2f}|{color_enabled}|{lut_spec or ''}"
    if cache_key in _COLOR_MATCH_CACHE:
        return _COLOR_MATCH_CACHE[cache_key]

    filt_parts: List[str] = []
    try:
        import cv2
        import numpy as np

        cap = cv2.VideoCapture(str(src))
        if cap.isOpened():
            t_mid = max(0.0, 0.5 * (start_s + end_s))
            cap.set(cv2.CAP_PROP_POS_MSEC, t_mid * 1000.0)
            ok, frame = cap.read()
            cap.release()
            if ok and frame is not None and frame.size > 0:
                fh, fw = frame.shape[:2]
                bbox = seg.get("bbox_norm")
                if not bbox:
                    try:
                        vis = analyze_clip_vision(src)
                        bbox = vis.get("bbox_norm")
                    except Exception:
                        bbox = None
                if not bbox or len(bbox) < 4:
                    bbox = [0.28, 0.16, 0.72, 0.78]
                x0, y0, x1, y1 = [float(v) for v in bbox[:4]]
                bw = max(0.08, x1 - x0)
                bh = max(0.08, y1 - y0)
                rx0 = max(0, min(fw - 2, int((x0 + 0.20 * bw) * fw)))
                ry0 = max(0, min(fh - 2, int((y0 + 0.20 * bh) * fh)))
                rx1 = max(rx0 + 2, min(fw, int((x1 - 0.20 * bw) * fw)))
                ry1 = max(ry0 + 2, min(fh, int((y1 - 0.20 * bh) * fh)))
                roi = frame[ry0:ry1, rx0:rx1]
                if roi.size > 0:
                    b_mean = float(np.mean(roi[:, :, 0]))
                    g_mean = float(np.mean(roi[:, :, 1]))
                    r_mean = float(np.mean(roi[:, :, 2]))
                    luma = 0.299 * r_mean + 0.587 * g_mean + 0.114 * b_mean
                    luma_plane = 0.299 * roi[:, :, 2] + 0.587 * roi[:, :, 1] + 0.114 * roi[:, :, 0]
                    std_y = float(np.std(luma_plane))
                    br = round(max(-0.04, min(0.04, (target_luma - luma) / 255.0 * 0.45)), 4)
                    ct = round(max(0.96, min(1.04, 42.0 / max(24.0, std_y))), 3)
                    rs = round(max(-0.05, min(0.05, (target_rg - r_mean / max(1.0, g_mean)) * 0.35)), 4)
                    bs = round(max(-0.05, min(0.05, (target_bg - b_mean / max(1.0, g_mean)) * 0.35)), 4)
                    if abs(br) >= 0.004 or abs(ct - 1.0) >= 0.008:
                        filt_parts.append(f"eq=brightness={br:.4f}:contrast={ct:.3f}")
                    if abs(rs) >= 0.005 or abs(bs) >= 0.005:
                        filt_parts.append(f"colorbalance=rs={rs:.4f}:bs={bs:.4f}")
    except Exception:
        pass

    if lut_spec:
        lut_path = resolve(str(lut_spec))
        if lut_path.is_file():
            filt_parts.append(f"lut3d='{lut_path.as_posix()}'")

    res = ",".join(filt_parts) if filt_parts else None
    _COLOR_MATCH_CACHE[cache_key] = res
    return res


def parse_zoom_spec(spec: Any) -> Dict[str, Any]:
    """Parse a compact zoom specification string or number into `{'z0': float, 'z1': float, 'style': str}`."""
    if isinstance(spec, (int, float)):
        v = float(spec)
        return {"z0": v, "z1": v, "style": "smooth"}
    raw = str(spec or "1.0").strip()
    style = "smooth"
    if ":" in raw:
        st_part, rhs = raw.split(":", 1)
        st_low = st_part.strip().lower()
        if st_low in ("punch_in", "sudden_in", "push", "in"):
            style = "sudden_in"
        elif st_low in ("punch_out", "sudden_out", "pull", "out"):
            style = "sudden_out"
        elif st_low in ("zoom_in", "mild_in"):
            style = "zoom_in"
        elif st_low in ("zoom_out", "mild_out"):
            style = "zoom_out"
        else:
            style = st_low or "smooth"
        raw = rhs.split("@", 1)[0].strip()
    else:
        raw = raw.split("@", 1)[0].strip()
        if raw.lower() == "bell":
            return {"z0": 1.20, "z1": 1.00, "zpeak": 1.55, "style": "bell"}
    if style == "bell":
        parts = [p.strip() for p in raw.split("->")] if raw else []
        try:
            if len(parts) >= 3:
                z0_v = float(parts[0]) if parts[0] else 1.20
                zp_v = float(parts[1]) if parts[1] else 1.55
                z1_v = float(parts[2]) if parts[2] else 1.00
                return {"z0": z0_v, "z1": z1_v, "zpeak": zp_v, "style": "bell"}
            if len(parts) == 2:
                z0_v = float(parts[0]) if parts[0] else 1.20
                z1_v = float(parts[1]) if parts[1] else 1.00
                return {"z0": z0_v, "z1": z1_v, "zpeak": max(1.55, z0_v, z1_v), "style": "bell"}
            if len(parts) == 1 and parts[0]:
                zp_v = float(parts[0])
                return {"z0": 1.20, "z1": 1.00, "zpeak": zp_v, "style": "bell"}
        except ValueError:
            pass
        return {"z0": 1.20, "z1": 1.00, "zpeak": 1.55, "style": "bell"}
    if "->" in raw:
        a, b = raw.split("->", 1)
        try:
            z0_v = float(a.strip()) if a.strip() else 1.0
            z1_v = float(b.strip()) if b.strip() else z0_v
            out_style = "smooth" if style in ("zoom_in", "zoom_out") else style
            return {"z0": z0_v, "z1": z1_v, "style": out_style}
        except ValueError:
            pass
    try:
        v = float(raw)
        if style in ("sudden_in", "flash_zoom"):
            return {"z0": 1.0, "z1": v, "style": style}
        if style == "sudden_out":
            return {"z0": v, "z1": 1.0, "style": style}
        if style == "zoom_in":
            return {"z0": 1.0, "z1": v, "style": "smooth"}
        if style == "zoom_out":
            return {"z0": v, "z1": 1.0, "style": "smooth"}
        return {"z0": v, "z1": v, "style": style}
    except ValueError:
        return {"z0": 1.0, "z1": 1.0, "style": "smooth"}


def _resolve_zoom_peak(
    seg: Optional[Dict[str, Any]],
    z0: float,
    z1: float,
    zoom_style: str,
) -> Optional[float]:
    """Return peak zoom scale for `bell` curves (default `1.55`, clamped `>= 1.0`), or `None`."""
    if str(zoom_style or "").strip().lower() != "bell":
        return None
    raw_peak = seg.get("zoom_peak") if isinstance(seg, dict) else None
    if raw_peak is not None:
        try:
            zp = float(raw_peak)
            if math.isfinite(zp):
                return max(1.0, zp)
        except (TypeError, ValueError):
            pass
    return max(1.0, 1.55, float(z0), float(z1))


def _clamp_split_zoom_spec(
    z0: float,
    z1: float,
    style: str,
    zoom_at: float,
    zoom_dur: float,
    exact_dur: float,
    max_zoom: float = SPLIT_MAX_ZOOM,
) -> Tuple[float, float, str, float, float]:
    """Split panels never zoom (`z=1` lock): zooming a panel would cause the character's
    scale and background to jump when switching between speaking and silent listener clips.
    """
    return 1.0, 1.0, "none", 0.0, exact_dur


def resolve_segment_zoom_spec(
    seg: Dict[str, Any],
    exact_dur: float,
) -> Tuple[float, float, str, float, float]:
    """Return `(z0, z1, zoom_style, zoom_at, zoom_dur)` for `seg`.

    Split panels (`split_h`, `split_v`, `top_pip`, and baked splits with `slot=`/`active=`)
    are strictly locked to `z=1.0`. Full-frame `single` segments preserve static punch-ins,
    smooth cosine `1.0->1.06` push-ins / pull-outs, `sudden_in:1.08@t`, `sudden_out`,
    `flash_zoom:1.12@t`, and 3-phase cosine `bell` curves.
    """
    lay = normalize_layout_name(seg.get("layout"))
    is_split_panel = lay in ("split_h", "split_v", "top_pip") or (
        lay == "single" and _resolve_active_slot(seg) in ("top", "bottom", "left", "right")
    )

    def _finish(
        rz0: float,
        rz1: float,
        rstyle: str,
        rat: float,
        rdur: float,
    ) -> Tuple[float, float, str, float, float]:
        if is_split_panel:
            return _clamp_split_zoom_spec(rz0, rz1, rstyle, rat, rdur, exact_dur)
        return max(1.0, rz0), max(1.0, rz1), rstyle, rat, rdur

    z0_raw = seg.get("zoom")
    z1_raw = seg.get("zoom_end") if seg.get("zoom_end") is not None else seg.get("zoom_to")
    style = str(seg.get("zoom_style") or "").strip().lower()
    zoom_at = float(seg.get("zoom_at") or 0.0)
    zoom_dur = float(seg.get("zoom_dur") or 0.0)

    if style == "bell" or (not style and seg.get("zoom_peak") is not None):
        z0 = max(1.0, float(z0_raw if z0_raw is not None else 1.20))
        z1 = max(1.0, float(z1_raw if z1_raw is not None else 1.00))
        return _finish(z0, z1, "bell", 0.0, exact_dur)

    if style in ("sudden_in", "punch_in"):
        z0 = max(1.0, float(z0_raw if z0_raw is not None else 1.0))
        z1 = max(1.0, float(z1_raw if z1_raw is not None else (z0 if z0 > 1.01 else 1.10)))
        if abs(z1 - z0) < 0.005:
            z0, z1 = 1.0, max(1.08, z1)
        return _finish(z0, z1, "sudden_in", max(0.0, zoom_at), (zoom_dur if zoom_dur > 0.02 else 0.18))

    if style in ("sudden_out", "punch_out"):
        z0 = max(1.0, float(z0_raw if z0_raw is not None else 1.10))
        z1 = max(1.0, float(z1_raw if z1_raw is not None else 1.0))
        if abs(z0 - z1) < 0.005:
            z0, z1 = max(1.08, z0), 1.0
        return _finish(z0, z1, "sudden_out", max(0.0, zoom_at), (zoom_dur if zoom_dur > 0.02 else 0.20))

    if style == "flash_zoom":
        z0 = max(1.0, float(z0_raw if z0_raw is not None else 1.0))
        z1 = max(1.0, float(z1_raw if z1_raw is not None else (z0 if z0 > 1.01 else 1.12)))
        if abs(z1 - z0) < 0.005:
            z0, z1 = 1.0, max(1.10, z1)
        return _finish(z0, z1, "flash_zoom", max(0.0, zoom_at), (zoom_dur if zoom_dur > 0.02 else 0.16))

    if style in ("zoom_in", "mild_in"):
        z0 = max(1.0, float(z0_raw if z0_raw is not None else 1.0))
        z1 = max(1.0, float(z1_raw if z1_raw is not None else (z0 if z0 > 1.01 else 1.06)))
        if abs(z1 - z0) < 0.005:
            z0, z1 = 1.0, max(1.06, z1)
        if seg.get("zoom_at") is not None:
            return _finish(z0, z1, "sudden_in", max(0.0, zoom_at), (zoom_dur if zoom_dur > 0.02 else 0.18))
        return _finish(z0, z1, "smooth", 0.0, exact_dur)

    if style in ("zoom_out", "mild_out"):
        z0 = max(1.0, float(z0_raw if z0_raw is not None else 1.06))
        z1 = max(1.0, float(z1_raw if z1_raw is not None else 1.0))
        if abs(z0 - z1) < 0.005:
            z0, z1 = max(1.06, z0), 1.0
        if seg.get("zoom_at") is not None:
            return _finish(z0, z1, "sudden_out", max(0.0, zoom_at), (zoom_dur if zoom_dur > 0.02 else 0.20))
        return _finish(z0, z1, "smooth", 0.0, exact_dur)

    z0 = max(1.0, float(z0_raw if z0_raw is not None else 1.0))
    z1 = max(1.0, float(z1_raw if z1_raw is not None else z0))

    if seg.get("zoom_at") is not None and abs(z1 - z0) >= 0.005:
        inferred_style = "sudden_out" if z1 < z0 else "sudden_in"
        return _finish(z0, z1, inferred_style, max(0.0, zoom_at), (zoom_dur if zoom_dur > 0.02 else 0.18))

    if abs(z1 - z0) < 0.005 and z0 > 1.005:
        return _finish(z0, z0, "static", 0.0, exact_dur)

    if abs(z1 - z0) >= 0.005:
        return _finish(z0, z1, "smooth", 0.0, exact_dur)

    if exact_dur >= 1.8 and seg.get("auto_zoom", False) is True:
        bg_motion = detect_static_camera_motion(
            seg["src"],
            float(seg.get("start", 0.0)),
            float(seg.get("end", exact_dur)),
        )
        if bg_motion < 2.8:
            sid_str = str(seg.get("id") or "S1")
            note = str(seg.get("note") or seg.get("text") or "")
            callout_re = re.compile(
                r"(supernova|miss\s*nova|\bnova\b|ai\s*tutor|spoken\s*english|improve|two\s*weeks|2\s*weeks|"
                r"15\s*minutes|fifteen|daily\s*speaking|daily\s*practice|stop\s*saying|secret|important|"
                r"mistake|professional|fluent|confidence)",
                re.I,
            )
            if sid_str in ("S1", "S1a", "L1", "L1a"):
                return _finish(1.0, 1.08, "sudden_in", 0.25, 0.18)
            seg_words = seg.get("words") or []
            seg_start = float(seg.get("start", 0.0))
            spd = _segment_speed(seg)
            for w in seg_words:
                wt = str(w.get("w") or "")
                rel_t = (float(w.get("start", seg_start)) - seg_start) / spd
                if 0.08 <= rel_t <= exact_dur - 0.35 and (
                    callout_re.search(wt)
                    or re.search(r"\b(stop|never|wrong|don't|instead|correct|right|always|actually|remember|secret)\b", wt, re.I)
                ):
                    return _finish(1.0, 1.08, "sudden_in", round(rel_t, 3), 0.18)
            if callout_re.search(note):
                return _finish(1.0, 1.08, "sudden_in", 0.12, 0.18)
            if "?" in note or sid_str in ("S2", "S2a", "L2") or re.search(
                r"\b(why|what|how|when|where|who|stop\s+saying|mistake|wrong)\b", note, re.I
            ):
                return _finish(1.0, 1.06, "smooth", 0.0, exact_dur)
            if sid_str.endswith(("b", "c")) and exact_dur <= 3.2:
                return _finish(1.08, 1.0, "sudden_out", 0.10, 0.20)
            if re.search(r"\[(?:right|correct)\]|\b(perfect|correct|exactly|instead|better|right\s+way|means|install|download)\b", note, re.I):
                return _finish(1.06, 1.0, "smooth", 0.0, exact_dur)
            if exact_dur >= 3.5:
                return _finish(1.0, 1.05, "smooth", 0.0, exact_dur)
            return _finish(1.05, 1.0, "smooth", 0.0, exact_dur)

    return 1.0, 1.0, "none", 0.0, exact_dur


def _build_subpixel_zoom_filter(
    slot_w: int,
    slot_h: int,
    fps: int,
    exact_dur: float,
    fx: float,
    fy: float,
    z0: float,
    z1: float,
    zoom_style: str = "smooth",
    zoom_at: float = 0.0,
    zoom_dur: float = 0.18,
    fx1: Optional[float] = None,
    fy1: Optional[float] = None,
    zoom_peak: Optional[float] = None,
) -> List[str]:
    """Build a 2x-supersampled sub-pixel `zoompan` filter chain free of 2px stair-stepping jitter."""
    fx1_v = fx if fx1 is None else max(0.05, min(0.95, float(fx1)))
    fy1_v = fy if fy1 is None else max(0.08, min(0.85, float(fy1)))
    has_pan = math.hypot(fx1_v - fx, fy1_v - fy) > 0.04
    z0_c = max(1.0, float(z0))
    z1_c = max(1.0, float(z1))
    style_norm = str(zoom_style or "").strip().lower()

    zpeak_c: Optional[float] = None
    if style_norm == "bell":
        zpeak_c = max(1.0, float(zoom_peak if zoom_peak is not None else max(1.55, z0_c, z1_c)))
        bell_has_motion = (
            abs(zpeak_c - z0_c) > 0.004
            or abs(zpeak_c - z1_c) > 0.004
            or abs(z1_c - z0_c) > 0.004
        )
        if not bell_has_motion and abs(z0_c - 1.0) <= 0.004 and not has_pan:
            return []
        if not bell_has_motion and z0_c > 1.004 and not has_pan:
            zw = even(slot_w * z0_c)
            zh = even(slot_h * z0_c)
            return [
                f"scale={zw}:{zh}:flags=lanczos",
                f"crop={slot_w}:{slot_h}:x='(iw-{slot_w})*{fx:.4f}':y='(ih-{slot_h})*{fy:.4f}'",
            ]
    else:
        if abs(z0_c - 1.0) <= 0.004 and abs(z1_c - 1.0) <= 0.004 and style_norm in ("none", "static", "") and not has_pan:
            return []

        # Fast zero-overhead path for static punch-in zooms (e.g. z=1.08 with no pan)
        if abs(z1_c - z0_c) <= 0.004 and style_norm in ("none", "static", "") and z0_c > 1.004 and not has_pan:
            zw = even(slot_w * z0_c)
            zh = even(slot_h * z0_c)
            return [
                f"scale={zw}:{zh}:flags=lanczos",
                f"crop={slot_w}:{slot_h}:x='(iw-{slot_w})*{fx:.4f}':y='(ih-{slot_h})*{fy:.4f}'",
            ]

    total_frames = max(2, int(round(exact_dur * fps)))
    n_minus_1 = max(1, total_frames - 1)

    ss_factor = 2
    sw_super = even(slot_w * ss_factor)
    sh_super = even(slot_h * ss_factor)

    if style_norm == "bell" and zpeak_c is not None:
        # 3-phase cosine bell curve over segment duration T:
        # Phase 1 (0..0.30*T): cosine ease-in z0 -> zpeak
        # Phase 2 (0.30*T..0.52*T): hold at zpeak
        # Phase 3 (0.52*T..T): cosine ease-out zpeak -> z1
        p_norm = f"min(on/{n_minus_1},1)"
        u1 = f"clip(({p_norm})/0.30,0,1)"
        u3 = f"clip((({p_norm})-0.52)/0.48,0,1)"
        ease1 = f"((1-cos(PI*{u1}))/2)"
        ease3 = f"((1-cos(PI*{u3}))/2)"
        z_expr = (
            f"max(1.0,{z0_c:.4f}"
            f"+({zpeak_c - z0_c:+.4f})*{ease1}"
            f"+({z1_c - zpeak_c:+.4f})*{ease3})"
        )
    elif style_norm in ("sudden_in", "sudden_out", "flash_zoom"):
        t0 = max(0.0, min(exact_dur - 0.05, float(zoom_at)))
        d_snap = max(0.08, min(1.20, float(zoom_dur or 0.18)))
        u_expr = f"clip((on/{fps:.4f}-{t0:.4f})/{d_snap:.4f},0,1)"
        ease_expr = f"(1-pow(1-{u_expr},3))"
        post_u = f"clip((on/{fps:.4f}-{t0 + d_snap:.4f})/{max(0.2, exact_dur - t0 - d_snap):.4f},0,1)"
        if z1_c < z0_c:
            # Settle sudden_out smoothly to z_settle then breathe down to z1_c, clamped >= 1.0 (zero black borders)
            drift_amp = min(0.015, max(0.0, (z0_c - z1_c) * 0.25))
            z_settle = z1_c + drift_amp
            z_expr = f"max(1.0,{z0_c:.4f}+({z_settle - z0_c:+.4f})*{ease_expr}-{drift_amp:.4f}*{post_u})"
        else:
            z_expr = f"max(1.0,{z0_c:.4f}+({z1_c - z0_c:+.4f})*{ease_expr}+0.015*{post_u})"
    else:
        p_expr = f"(1-cos(PI*min(on/{n_minus_1},1)))/2"
        z_expr = f"max(1.0,{z0_c:.4f}+({z1_c - z0_c:+.4f})*{p_expr})"

    if has_pan:
        pan_p = f"(1-cos(PI*min(on/{n_minus_1},1)))/2"
        fx_e = f"({fx:.4f}+({fx1_v - fx:+.4f})*{pan_p})"
        fy_e = f"({fy:.4f}+({fy1_v - fy:+.4f})*{pan_p})"
        x_expr = f"(iw-iw/zoom)*{fx_e}"
        y_expr = f"(ih-ih/zoom)*{fy_e}"
    else:
        x_expr = f"(iw-iw/zoom)*{fx:.4f}"
        y_expr = f"(ih-ih/zoom)*{fy:.4f}"

    filters = [
        f"scale={sw_super}:{sh_super}:flags=bicubic",
        f"zoompan=z='{z_expr}':x='{x_expr}':y='{y_expr}':d=1:s={slot_w}x{slot_h}:fps={fps}",
    ]
    if style_norm == "flash_zoom":
        t0 = max(0.0, float(zoom_at))
        pulse = f"max(0,1-abs((t-{t0 + 0.04:.4f})/0.09))"
        filters.append(f"eq=brightness='0.12*{pulse}':contrast='1+0.08*{pulse}':eval=frame")
    return filters


def banner_stack_top(seg: Dict[str, Any], num_rows: Optional[int] = None) -> float:
    """Pick the vertical start fraction of a 'Stop Saying' banner stack that avoids
    overlapping any prominent face box (accounting for zoom and crop) while favoring the
    natural chest/collar belt (`0.52..0.55`) and staying clear of the `y=0.60..0.72` (`65%`) caption band.
    """
    candidates = (0.52, 0.55, 0.45, 0.73, 0.20, 0.26, 0.60, 0.64)
    if num_rows is None:
        raw_banners = seg.get("banners") or seg.get("stop_saying") or []
        if isinstance(raw_banners, str):
            rows = 1
        elif isinstance(raw_banners, dict):
            rows = 0 if _is_flashcard_item(raw_banners) else 1
        elif isinstance(raw_banners, (list, tuple)):
            rows = max(1, sum(1 for item in raw_banners if not _is_flashcard_item(item)))
        else:
            rows = 1
        rows = max(1, rows)
    else:
        rows = max(1, int(num_rows))
    banner_h_frac = 0.045 * rows

    try:
        vis = analyze_clip_vision(resolve(seg["src"]))
        raw_faces = [
            f for f in (vis.get("faces") or [])
            if isinstance(f, dict)
            and isinstance(f.get("bbox_norm"), (list, tuple))
            and len(f["bbox_norm"]) >= 4
            and not ("confidence" in f and float(f.get("confidence") or 0.0) <= 0.0)
        ]
        faces = [f["bbox_norm"] for f in raw_faces]
        bb = vis.get("bbox_norm")
        if not faces and not (vis.get("faces")) and bb and len(bb) >= 4:
            faces = [bb]
        if not faces:
            return candidates[0]

        foc = (
            seg["focus"]
            if isinstance(seg.get("focus"), (list, tuple)) and len(seg["focus"]) >= 2
            else (vis.get("focus") or [0.5, 0.4])
        )
        fx, fy = float(foc[0]), float(foc[1])
        zmax = max(
            float(seg.get("zoom") or 1.0),
            float(seg.get("zoom_to") or 1.0),
            float(seg.get("zoom_end") or 1.0),
            float(seg.get("zoom_peak") or 1.0),
        )
        ar_raw = vis.get("aspect_ratio")
        ar = float(ar_raw) if ar_raw else 0.5625
        canvas_ar = 9.0 / 16.0

        zoomed_faces: List[Tuple[float, float, float, float]] = []
        for fb in faces:
            x0, y0, x1, y1 = float(fb[0]), float(fb[1]), float(fb[2]), float(fb[3])
            if ar > canvas_ar + 0.02:
                rx = ar / canvas_ar
                x0 = x0 * rx - fx * (rx - 1.0)
                x1 = x1 * rx - fx * (rx - 1.0)
            elif ar < canvas_ar - 0.02:
                ry = canvas_ar / max(0.1, ar)
                y0 = y0 * ry - fy * (ry - 1.0)
                y1 = y1 * ry - fy * (ry - 1.0)
            zx0 = max(0.0, min(1.0, fx + (x0 - fx) * zmax))
            zx1 = max(0.0, min(1.0, fx + (x1 - fx) * zmax))
            zy0 = max(0.0, min(1.0, fy + (y0 - fy) * zmax))
            zy1 = max(0.0, min(1.0, fy + (y1 - fy) * zmax))
            if zx1 > zx0 + 0.01 and zy1 > zy0 + 0.01:
                zoomed_faces.append((zx0, zy0, zx1, zy1))

        if not zoomed_faces:
            return candidates[0]

        bx0, bx1 = 0.12, 0.88
        best_cand = candidates[0]
        best_score: Optional[Tuple[float, float, int]] = None
        for idx, cand_y in enumerate(candidates):
            span_y0 = cand_y
            span_y1 = min(0.96, cand_y + banner_h_frac)
            max_ov = 0.0
            for fx0_z, fy0_z, fx1_z, fy1_z in zoomed_faces:
                ix = max(0.0, min(fx1_z, bx1) - max(fx0_z, bx0))
                iy = max(0.0, min(fy1_z, span_y1) - max(fy0_z, span_y0))
                f_area = max(1e-6, (fx1_z - fx0_z) * (fy1_z - fy0_z))
                ov = (ix * iy) / f_area
                if ov > max_ov:
                    max_ov = ov
            cap_ov = max(0.0, min(span_y1, 0.72) - max(span_y0, 0.60)) / max(1e-6, span_y1 - span_y0)
            score = (round(max_ov, 4), round(cap_ov, 3), idx)
            if best_score is None or score < best_score:
                best_score = score
                best_cand = cand_y
        return round(best_cand, 3)
    except Exception:
        return candidates[0]


def parse_banners(seg: Dict[str, Any], exact: float) -> List[Tuple[str, float, float, bool]]:
    """Parse segment banners into `[(text, start_sec, duration_sec, is_header), ...]`.
    A `header:<text>` entry renders as a static title above the struck phrase stack;
    phrase banners default to a crisp `DEFAULT_BANNER_BEAT_DURATION` (`0.48s`) beat.
    Strictly ignores `seg["flashcards"]` / `seg["cards"]` and any flashcard-typed dicts.
    """
    banners = seg.get("banners") or seg.get("stop_saying") or []
    if isinstance(banners, (str, dict)):
        banners = [banners]
    out: List[Tuple[str, float, float, bool]] = []
    for b_idx, b_item in enumerate(banners):
        if _is_flashcard_item(b_item):
            continue
        if isinstance(b_item, str):
            b_text = b_item
            b_at = round(0.15 + b_idx * 0.65, 3)
            raw_dur = None
        elif isinstance(b_item, dict):
            b_text = str(b_item.get("text") or b_item.get("phrase") or "STOP SAYING")
            b_at = float(b_item.get("at", round(0.15 + b_idx * 0.65, 3)))
            raw_dur = b_item.get("duration")
        else:
            continue
        is_header = b_text.lower().startswith("header:")
        if raw_dur is not None:
            b_dur = float(raw_dur)
        elif is_header:
            b_dur = max(0.4, exact - b_at)
        elif b_at + DEFAULT_BANNER_BEAT_DURATION <= exact + 0.02:
            b_dur = DEFAULT_BANNER_BEAT_DURATION
        else:
            b_dur = max(0.24, exact - b_at)
        out.append((b_text[7:].strip() if is_header else b_text, b_at, b_dur, is_header))
    return out


def banner_stack_rows(seg: Dict[str, Any], parsed: List[Tuple[str, float, float, bool]]) -> Tuple[float, float]:
    """Return `(header_row_frac, first_phrase_row_frac)` as fractions of canvas height."""
    stack_top = banner_stack_top(seg, num_rows=max(1, len(parsed)))
    has_header = any(pb[3] for pb in parsed)
    return stack_top, (stack_top + 0.075 if has_header else stack_top)


def banner_boxes(seg: Dict[str, Any], W: int, H: int, exact: float) -> List[Dict[str, Any]]:
    """Return exact pixel bounding boxes and active time spans for each banner pill on `(W, H)`."""
    parsed = parse_banners(seg, exact)
    if not parsed:
        return []
    stack_top, phrase_base = banner_stack_rows(seg, parsed)
    scale = W / 1080.0
    boxes: List[Dict[str, Any]] = []
    phrase_idx = 0
    for text, b_at, b_dur, is_header in parsed:
        try:
            from PIL import Image

            png = render_stop_saying_banner_png(text, with_strike=False, cross_icon=is_header)
            with Image.open(png) as im:
                cw, ch = im.size
        except Exception:
            cw, ch = 560, 76
        over = 36 * scale
        pill_w, pill_h = max(10.0, cw * scale - 2 * over), ch * scale
        x0 = (W - cw * scale) / 2.0 + over
        if is_header:
            y0 = int(round(H * stack_top))
        else:
            y0 = int(round(H * (phrase_base + phrase_idx * 0.068)))
            phrase_idx += 1
        boxes.append({
            "text": text,
            "header": is_header,
            "x0": x0,
            "y0": float(y0),
            "x1": x0 + pill_w,
            "y1": y0 + pill_h,
            "t0": b_at,
            "t1": min(exact, b_at + b_dur),
        })
    return boxes


def _build_strikethrough_png(
    strike_items: Sequence[str],
    out_png: Union[str, Path],
    strike_last: bool = True,
    width: int = 1080,
    height: int = 1920,
) -> Path:
    """Composite a transparent full-frame PNG stacking `strike_items` for NLE/Premiere export."""
    from PIL import Image

    out_path = Path(out_png)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    canvas = Image.new("RGBA", (int(width), int(height)), (0, 0, 0, 0))
    scale = width / 1080.0
    base_y = 0.52

    for idx, phrase in enumerate(strike_items):
        is_last = idx == len(strike_items) - 1
        do_strike = strike_last if is_last else True
        pill_path = render_stop_saying_banner_png(str(phrase), with_strike=do_strike, strike_px=7)
        with Image.open(pill_path) as pill_im:
            pw = max(2, int(round(pill_im.width * scale)))
            ph = max(2, int(round(pill_im.height * scale)))
            resized = pill_im.convert("RGBA").resize((pw, ph), resample=Image.Resampling.LANCZOS)
        px = (int(width) - pw) // 2
        py = int(round(height * (base_y + idx * 0.068)))
        canvas.alpha_composite(resized, (px, max(0, min(int(height) - ph, py))))

    canvas.save(out_path, format="PNG")
    return out_path


# ---------------------------------------------------------------------------
# Split Panel Lock: Stationary Background Registration & Colour Consistency
# ---------------------------------------------------------------------------
_SPLIT_LOCK_CACHE: Dict[str, Dict[str, Any]] = {}
_CACHE_LOCK = threading.Lock()


def _split_peer_time(peer: Dict[str, Any]) -> float:
    span = peer.get("idle_span")
    if isinstance(span, (list, tuple)) and len(span) >= 2:
        return max(0.0, float(span[0]))
    if peer.get("mode") == "still" and peer.get("still_at") is not None:
        return max(0.0, float(peer["still_at"]))
    return max(0.0, float(peer.get("start") or 0.0))


def _split_seg_sig(seg: Dict[str, Any]) -> str:
    peer = seg.get("peer") if isinstance(seg.get("peer"), dict) else {}
    h_start, _ = _segment_hold_span(seg)
    return json.dumps([
        normalize_layout_name(seg.get("layout")),
        str(seg.get("src")),
        round(float(seg.get("start") or 0.0), 3),
        round(float(seg.get("end") or 0.0), 3),
        str(peer.get("src")),
        _split_peer_time(peer),
        _resolve_active_slot(seg),
        str(seg.get("speaker") or ""),
        str(peer.get("speaker") or ""),
        _parse_offset_y(seg.get("offset_y")),
        _parse_offset_y(peer.get("offset_y")),
        str(seg.get("hold_src") or ""),
        round(float(h_start or 0.0), 3),
        seg.get("focus"),
        peer.get("focus"),
        bool(seg.get("broll") or seg.get("is_broll")),
        bool(peer.get("broll") or peer.get("is_broll")),
        _segment_is_muted(seg),
    ], default=str)


def _split_slots(lay: str, seg: Dict[str, Any], W: int, H: int) -> Tuple[str, str, Tuple[int, int], Tuple[int, int]]:
    """Return `(live_slot, peer_slot, live_slot_size, peer_slot_size)` for a split segment."""
    if lay == "split_v":
        top_h = even(H // 2)
        sizes = {"top": (W, top_h), "bottom": (W, H - top_h)}
        live = "top" if _resolve_active_slot(seg, default="top") in ("top", "slot_a", "a", "0") else "bottom"
        other = "bottom" if live == "top" else "top"
    else:
        left_w = even(W // 2)
        sizes = {"left": (left_w, H), "right": (W - left_w, H)}
        live = "left" if _resolve_active_slot(seg, default="left") in ("left", "slot_a", "a", "0") else "right"
        other = "right" if live == "left" else "left"
    return live, other, sizes[live], sizes[other]


def _panel_colour_stats(path: Path, crop: Dict[str, Any], t0: float, top_skip: int) -> Optional[Tuple[List[float], List[float]]]:
    """Measure RGB mean and standard deviation of `path` inside its locked panel crop across three frames."""
    try:
        import cv2
        import numpy as np

        g = crop["geom"]
        slot_w, slot_h = crop["slot_size"]
        k = 0.25
        mat = np.float32([[g["s"] * k, 0, -g["ox"] * k], [0, g["s"] * k, -g["oy"] * k]])
        size = (max(8, int(slot_w * k)), max(8, int(slot_h * k)))
        cap = cv2.VideoCapture(str(path))
        if not cap.isOpened():
            return None
        dur = (cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0) / max(1.0, cap.get(cv2.CAP_PROP_FPS) or 30.0)
        px: List[Any] = []
        for dt in (0.0, 0.6, 1.2):
            t = min(max(0.0, t0 + dt), max(0.0, dur - 0.1))
            cap.set(cv2.CAP_PROP_POS_MSEC, t * 1000.0)
            ok, frame = cap.read()
            if not ok or frame is None:
                continue
            view = cv2.warpAffine(frame, mat, size, flags=cv2.INTER_AREA)[int(top_skip * k) :]
            px.append(view.reshape(-1, 3).astype(np.float32))
        cap.release()
        if not px:
            return None
        allpx = np.concatenate(px, axis=0)[:, ::-1]  # BGR -> RGB
        return [float(v) for v in allpx.mean(axis=0)], [float(max(1.0, v)) for v in allpx.std(axis=0)]
    except Exception:
        return None


def _panel_colour_filter(ref: Tuple[List[float], List[float]], clip: Tuple[List[float], List[float]]) -> Tuple[str, str]:
    """Return `(ffmpeg_lutrgb_filter, diagnostic_note)` matching `clip`'s colour to `ref`."""
    gains, offs, capped = [], [], False
    for c in range(3):
        a = ref[1][c] / clip[1][c]
        a_c = min(1.15, max(0.87, a))
        b = ref[0][c] - a_c * clip[0][c]
        b_c = min(20.0, max(-20.0, b))
        capped = capped or abs(a_c - a) > 0.005 or abs(b_c - b) > 0.5
        gains.append(a_c)
        offs.append(b_c)
    shift = max(abs(ref[0][c] - clip[0][c]) for c in range(3))
    if shift < 1.2 and all(abs(a - 1.0) < 0.015 for a in gains):
        return "", ""
    parts = [f"{ch}='clip(val*{gains[i]:.4f}+{offs[i]:.2f},0,255)'" for i, ch in enumerate("rgb")]
    note = f"colour matched to speaking clip (shifted by {shift:.0f}/255)"
    if capped:
        note += "; [SPLIT_COLOUR] colour offset exceeds clamped threshold; matched within safe bounds"
    return "format=gbrp,lutrgb=" + ":".join(parts) + ",format=yuv420p", note


def _compute_split_locks(plan: Dict[str, Any], W: int, H: int) -> Dict[str, Any]:
    """Compute background-registered stationary panel crops across all `split_h` and `split_v`
    segments in `plan`. One reference clip per panel (the silent listener clip when present)
    fixes the panel frame, and every speaking/listening clip in that panel is aligned to it.
    """
    segs = [
        s for s in (plan.get("segments") or [])
        if isinstance(s, dict)
        and normalize_layout_name(s.get("layout")) in ("split_h", "split_v")
        and s.get("src")
    ]
    out: Dict[str, Any] = {"segs": {}, "report": []}
    if not segs:
        return out

    uses: Dict[Tuple[str, str], List[Dict[str, Any]]] = {}
    rows: List[Dict[str, Any]] = []
    src_to_speaker: Dict[str, str] = {}
    slot_speakers: Dict[Tuple[str, str], set] = {}

    for s_any in (plan.get("segments") or []):
        if not isinstance(s_any, dict) or not s_any.get("src"):
            continue
        sp_any = (str(s_any.get("speaker") or s_any.get("sp") or "").strip() or _infer_segment_speaker(s_any, plan)).strip().lower()
        if sp_any:
            try:
                src_to_speaker.setdefault(str(resolve(s_any["src"])), sp_any)
            except Exception:
                pass
        p_any = s_any.get("peer") if isinstance(s_any.get("peer"), dict) else {}
        p_sp_any = str(p_any.get("speaker") or p_any.get("sp") or "").strip().lower()
        if p_sp_any and p_any.get("src"):
            try:
                src_to_speaker.setdefault(str(resolve(p_any["src"])), p_sp_any)
            except Exception:
                pass

    def _valid_focus(val: Any) -> Optional[List[float]]:
        if isinstance(val, (list, tuple)) and len(val) >= 2:
            try:
                return [float(val[0]), float(val[1])]
            except (TypeError, ValueError):
                return None
        return None

    def _is_explicit_broll(s_dict: Dict[str, Any], p_path: Path, is_muted_seg: bool = False) -> bool:
        if s_dict.get("broll") or s_dict.get("is_broll"):
            return True
        if is_muted_seg and re.search(r"\b(?:b[_-]?roll|cutaway)\b", p_path.stem, re.I):
            return True
        return False

    for seg in segs:
        lay = normalize_layout_name(seg.get("layout"))
        peer = seg.get("peer") if isinstance(seg.get("peer"), dict) else {}
        live_src = resolve(seg["src"])
        peer_src = resolve(peer.get("src") or seg["src"])
        if not live_src.is_file() or not peer_src.is_file():
            continue
        live_slot, peer_slot, live_size, peer_size = _split_slots(lay, seg, W, H)
        live_sp = (str(seg.get("speaker") or seg.get("sp") or "").strip() or _infer_segment_speaker(seg, plan)).strip().lower()
        peer_sp = str(peer.get("speaker") or peer.get("sp") or "").strip().lower()
        if not live_sp:
            live_sp = src_to_speaker.get(str(live_src), "")
        if not peer_sp:
            peer_sp = src_to_speaker.get(str(peer_src), "")
        if live_sp:
            slot_speakers.setdefault((lay, live_slot), set()).add(live_sp)
        if peer_sp:
            slot_speakers.setdefault((lay, peer_slot), set()).add(peer_sp)
        live_focus = _valid_focus(seg.get("focus"))
        peer_focus = _valid_focus(peer.get("focus"))
        live_broll = _is_explicit_broll(seg, live_src, _segment_is_muted(seg))
        peer_broll = _is_explicit_broll(peer, peer_src, True)
        hold_part: Optional[Dict[str, Any]] = None
        hold_raw = seg.get("hold_src")
        if hold_raw:
            try:
                hold_path = resolve(hold_raw)
                if hold_path.is_file():
                    h_start_val, _ = _segment_hold_span(seg)
                    hold_part = {
                        "slot": live_slot,
                        "src": hold_path,
                        "t": max(0.0, float(h_start_val or 0.0)),
                        "size": live_size,
                        "role": "hold",
                        "speaker": live_sp,
                        "offset_y_raw": _parse_offset_y(seg.get("offset_y")),
                        "focus": live_focus,
                        "is_broll": live_broll,
                    }
                    if live_sp:
                        src_to_speaker.setdefault(str(hold_path), live_sp)
            except Exception:
                hold_part = None
        row = {
            "sig": _split_seg_sig(seg),
            "seg": seg,
            "lay": lay,
            "live": {
                "slot": live_slot,
                "src": live_src,
                "t": float(seg.get("start") or 0.0),
                "size": live_size,
                "role": "live",
                "speaker": live_sp,
                "offset_y_raw": _parse_offset_y(seg.get("offset_y")),
                "focus": live_focus,
                "is_broll": live_broll,
            },
            "peer": {
                "slot": peer_slot,
                "src": peer_src,
                "t": _split_peer_time(peer),
                "size": peer_size,
                "role": "peer",
                "speaker": peer_sp,
                "offset_y_raw": _parse_offset_y(peer.get("offset_y")),
                "focus": peer_focus,
                "is_broll": peer_broll,
            },
            "hold": hold_part,
        }
        rows.append(row)

    # Infer missing speaker on unlabeled silent peer/hold cuts from single-speaker slot ownership
    for row in rows:
        lay = row["lay"]
        for part in (row["live"], row["peer"], row.get("hold")):
            if not part:
                continue
            if not part["speaker"]:
                sps = slot_speakers.get((lay, part["slot"]), set())
                if len(sps) == 1:
                    part["speaker"] = next(iter(sps))

    # Build speaker-consistent per-panel offset_y maps across ALL dialogue & silent cuts
    offset_by_lay_slot_sp: Dict[Tuple[str, str, str], float] = {}
    offset_by_lay_sp: Dict[Tuple[str, str], float] = {}
    offset_by_sp: Dict[str, float] = {}
    offset_by_lay_slot: Dict[Tuple[str, str], float] = {}
    unlabeled_offset_by_lay_slot: Dict[Tuple[str, str], float] = {}
    offset_by_lay_src: Dict[Tuple[str, str], float] = {}

    for row in rows:
        lay = row["lay"]
        for part in (row["live"], row["peer"], row.get("hold")):
            if not part:
                continue
            oy = float(part.get("offset_y_raw") or 0.0)
            if abs(oy) <= 1e-4:
                continue
            slot = part["slot"]
            sp = part.get("speaker") or ""
            offset_by_lay_slot.setdefault((lay, slot), oy)
            offset_by_lay_src.setdefault((lay, str(part["src"])), oy)
            if sp:
                offset_by_lay_slot_sp.setdefault((lay, slot, sp), oy)
                offset_by_lay_sp.setdefault((lay, sp), oy)
                offset_by_sp.setdefault(sp, oy)
            else:
                unlabeled_offset_by_lay_slot.setdefault((lay, slot), oy)

    # Lowest-priority speaker fallback from non-split cuts or plan['speakers'] metadata
    for s_any in (plan.get("segments") or []):
        if not isinstance(s_any, dict):
            continue
        oy_any = _parse_offset_y(s_any.get("offset_y"))
        if abs(oy_any) > 1e-4:
            sp_any = (str(s_any.get("speaker") or s_any.get("sp") or "").strip() or _infer_segment_speaker(s_any, plan)).strip().lower()
            if sp_any:
                offset_by_sp.setdefault(sp_any, oy_any)
    speakers_cfg = plan.get("speakers")
    if isinstance(speakers_cfg, dict):
        for sp_k, sp_v in speakers_cfg.items():
            if isinstance(sp_v, dict):
                oy_cfg = _parse_offset_y(sp_v.get("offset_y"))
                if abs(oy_cfg) > 1e-4:
                    offset_by_sp.setdefault(str(sp_k).strip().lower(), oy_cfg)

    def _resolve_part_offset_y(lay: str, part: Dict[str, Any]) -> float:
        slot = part["slot"]
        sp = part.get("speaker") or ""
        src_k = str(part["src"])
        if sp:
            if (lay, slot, sp) in offset_by_lay_slot_sp:
                return offset_by_lay_slot_sp[(lay, slot, sp)]
            if (lay, sp) in offset_by_lay_sp:
                return offset_by_lay_sp[(lay, sp)]
            if sp in offset_by_sp:
                return offset_by_sp[sp]
        if (lay, src_k) in offset_by_lay_src:
            return offset_by_lay_src[(lay, src_k)]
        if not sp:
            if (lay, slot) in offset_by_lay_slot:
                return offset_by_lay_slot[(lay, slot)]
        else:
            if (lay, slot) in unlabeled_offset_by_lay_slot:
                return unlabeled_offset_by_lay_slot[(lay, slot)]
            if len(slot_speakers.get((lay, slot), set())) <= 1 and (lay, slot) in offset_by_lay_slot:
                return offset_by_lay_slot[(lay, slot)]
        return float(part.get("offset_y_raw") or 0.0)

    def _part_clip_key(part: Dict[str, Any]) -> str:
        return f"{part['src']}|{float(part.get('offset_y') or 0.0):+.4f}"

    for row in rows:
        lay = row["lay"]
        for part in (row["live"], row["peer"], row.get("hold")):
            if not part:
                continue
            part["offset_y"] = _resolve_part_offset_y(lay, part)
            part["clip_key"] = _part_clip_key(part)
            uses.setdefault((lay, part["slot"]), []).append(part)

    def _bbox(path: Path) -> Optional[List[float]]:
        try:
            vis = analyze_clip_vision(path) or {}
            return vis.get("bbox_norm") if _vis_has_real_face_safe(vis) else None
        except Exception:
            return None

    def _dims(path: Path) -> Tuple[int, int]:
        info = probe(path)
        return int(info.get("width") or W), int(info.get("height") or H)

    clusters: Dict[Tuple[str, str], List[Dict[str, Any]]] = {}
    member: Dict[Tuple[str, str, str], Tuple[Dict[str, Any], Optional[Dict[str, float]], str]] = {}
    for key, parts in uses.items():
        first_t: Dict[str, float] = {}
        peer_n: Dict[str, int] = {}
        live_n: Dict[str, int] = {}
        key_to_path: Dict[str, Path] = {}
        key_to_oy: Dict[str, float] = {}
        key_to_focus: Dict[str, Optional[List[float]]] = {}
        key_to_broll: Dict[str, bool] = {}
        key_to_size: Dict[str, Tuple[int, int]] = {}
        order: List[str] = []
        for part in parts:
            k = part["clip_key"]
            if k not in first_t:
                first_t[k] = part["t"]
                key_to_path[k] = part["src"]
                key_to_oy[k] = float(part.get("offset_y") or 0.0)
                key_to_focus[k] = part.get("focus")
                key_to_broll[k] = bool(part.get("is_broll"))
                key_to_size[k] = part["size"]
                order.append(k)
            elif key_to_focus.get(k) is None and part.get("focus") is not None:
                key_to_focus[k] = part.get("focus")
            if part.get("is_broll"):
                key_to_broll[k] = True
            peer_n[k] = peer_n.get(k, 0) + (1 if part["role"] == "peer" else 0)
            live_n[k] = live_n.get(k, 0) + (1 if part["role"] == "live" else 0)
        order.sort(key=lambda q: -peer_n.get(q, 0))  # silent listener clip anchors the panel frame
        clusters[key] = []
        for k in order:
            path = key_to_path[k]
            oy_k = key_to_oy[k]
            bb, dims = _bbox(path), _dims(path)
            placed = False
            for cl in clusters[key]:
                if abs(float(cl.get("offset_y") or 0.0) - oy_k) > 1e-4:
                    continue
                reg = register_background(cl["ref"], cl["ref_t"], path, first_t[k], cl["bbox"], bb)
                how = "background"
                same_aspect = (
                    dims == cl["dims"]
                    or abs((dims[0] / float(max(1, dims[1]))) - (cl["dims"][0] / float(max(1, cl["dims"][1])))) <= 0.02
                )
                if reg is None and same_aspect and bb and cl["bbox"]:
                    dx = abs(0.5 * (bb[0] + bb[2]) - 0.5 * (cl["bbox"][0] + cl["bbox"][2]))
                    dy = abs(bb[1] - cl["bbox"][1])
                    if dx <= 0.06 and dy <= 0.06:
                        rw, rh = cl["dims"]
                        eff_a = 0.5 * (dims[0] / float(max(1, rw)) + dims[1] / float(max(1, rh)))
                        reg, how = {"a": eff_a, "tx": 0.0, "ty": 0.0}, "assumed"
                if reg is not None:
                    member[(key[0], key[1], k)] = (cl, reg, how)
                    cl["clips"][k] = {"path": path, "t": first_t[k], "live_n": live_n.get(k, 0)}
                    cl["regs"].append((dims, reg))
                    if cl.get("focus") is None and key_to_focus.get(k) is not None:
                        cl["focus"] = key_to_focus.get(k)
                    if key_to_broll.get(k):
                        cl["is_broll"] = True
                    placed = True
                    break
            if not placed:
                cl = {
                    "ref": path,
                    "ref_t": first_t[k],
                    "bbox": bb,
                    "dims": dims,
                    "geom": None,
                    "lay": key[0],
                    "slot": key[1],
                    "slot_size": key_to_size[k],
                    "offset_y": oy_k,
                    "focus": key_to_focus.get(k),
                    "is_broll": bool(key_to_broll.get(k)),
                    "regs": [],
                    "min_zoom": 1.0,
                    "is_th": False,
                    "th_panel": None,
                    "clips": {k: {"path": path, "t": first_t[k], "live_n": live_n.get(k, 0)}},
                    "colour": None,
                }
                clusters[key].append(cl)
                member[(key[0], key[1], k)] = (cl, {"a": 1.0, "tx": 0.0, "ty": 0.0}, "reference")

    # Classify each cluster (talking-head vs B-roll) and compute ORB horizontal slack zoom
    canon_fn = getattr(_vision_tracker, "_canonical_split_clip", None)
    is_th_fn = getattr(_vision_tracker, "_is_talking_head_panel", None)
    group_targets_fn = getattr(_vision_tracker, "compute_split_group_targets", None)
    all_clusters: Dict[int, Dict[str, Any]] = {}
    for key, cls_list in clusters.items():
        for cl in cls_list:
            all_clusters[id(cl)] = cl
            rw, rh = cl["dims"]
            slot_w, slot_h = cl["slot_size"]
            s0 = max(slot_w / float(max(1, rw)), slot_h / float(max(1, rh)))
            sw0 = rw * s0

            ref_canon = canon_fn(cl["ref"]) if callable(canon_fn) else cl["ref"]
            vis_ref: Dict[str, Any] = {}
            try:
                p_vis = ref_canon if (isinstance(ref_canon, Path) and ref_canon.is_file()) else cl["ref"]
                vis_ref = analyze_clip_vision(p_vis) or {}
                if not _vis_has_real_face_safe(vis_ref) and p_vis != cl["ref"] and cl["ref"].is_file():
                    vis_orig = analyze_clip_vision(cl["ref"]) or {}
                    if _vis_has_real_face_safe(vis_orig):
                        vis_ref = vis_orig
            except Exception:
                vis_ref = {}
            if vis_ref and "aspect_ratio" not in vis_ref and rh > 0:
                vis_ref = dict(vis_ref, aspect_ratio=round(rw / float(rh), 4))

            fh_val = 0.28
            fx_val = float(cl["focus"][0]) if cl.get("focus") else 0.5
            if _vis_has_real_face_safe(vis_ref):
                bb_r = vis_ref.get("bbox_norm") or []
                if len(bb_r) >= 4 and float(bb_r[3]) > float(bb_r[1]):
                    fh_val = float(bb_r[3]) - float(bb_r[1])
                    cx_r = 0.5 * (float(bb_r[0]) + float(bb_r[2]))
                    v_f = vis_ref.get("focus")
                    fx_val = 0.65 * cx_r + 0.35 * float(v_f[0]) if (v_f and len(v_f) >= 2) else cx_r

            if cl["regs"]:
                max_tx_px = max(
                    abs((s0 / max(0.01, r_d["a"])) * float(r_d["tx"]))
                    for _, r_d in cl["regs"]
                )
                if max_tx_px > 1.5 and sw0 < slot_w + 2.0 * (max_tx_px + 2.0):
                    min_edge = max(0.20, min(fx_val, 1.0 - fx_val))
                    need_sw = (0.5 * slot_w + max_tx_px + 2.0) / min_edge
                    cl["min_zoom"] = round(min(1.14, max(1.0, need_sw / max(1.0, sw0))), 4)

            is_th = (
                bool(is_th_fn(vis_ref, fh_val, slot_w, slot_h, is_muted_broll=cl["is_broll"]))
                if callable(is_th_fn)
                else (not cl["is_broll"] and _vis_has_real_face_safe(vis_ref) and fh_val >= 0.13)
            )
            cl["is_th"] = is_th
            if is_th:
                cl["th_panel"] = {
                    "src": cl["ref"],
                    "w_in": rw,
                    "h_in": rh,
                    "slot_w": slot_w,
                    "slot_h": slot_h,
                    "focus": cl.get("focus"),
                    "is_broll": cl["is_broll"],
                    "min_zoom": cl["min_zoom"],
                }

    # Union-Find across paired talking heads AND multiple talking heads sharing the same (lay, slot)
    uf_parent: Dict[int, int] = {cid: cid for cid, c in all_clusters.items() if c["is_th"]}

    def _uf_find(cid: int) -> int:
        while uf_parent[cid] != cid:
            uf_parent[cid] = uf_parent[uf_parent[cid]]
            cid = uf_parent[cid]
        return cid

    def _uf_union(c1: int, c2: int) -> None:
        r1, r2 = _uf_find(c1), _uf_find(c2)
        if r1 != r2:
            uf_parent[r2] = r1

    for row in rows:
        lay = row["lay"]
        c_l = member[(lay, row["live"]["slot"], row["live"]["clip_key"])][0]
        c_p = member[(lay, row["peer"]["slot"], row["peer"]["clip_key"])][0]
        if c_l["is_th"] and c_p["is_th"]:
            _uf_union(id(c_l), id(c_p))

    for key, cls_list in clusters.items():
        th_cls = [c for c in cls_list if c["is_th"]]
        if len(th_cls) >= 2:
            base_id = id(th_cls[0])
            for other_c in th_cls[1:]:
                _uf_union(base_id, id(other_c))

    uf_groups: Dict[int, List[Dict[str, Any]]] = {}
    for cid in uf_parent:
        uf_groups.setdefault(_uf_find(cid), []).append(all_clusters[cid])

    group_targets: Dict[int, Tuple[Optional[float], Optional[float]]] = {}
    if callable(group_targets_fn):
        for root_id, g_cls in uf_groups.items():
            if len(g_cls) < 2:
                continue
            g_cids = {id(c) for c in g_cls}
            g_lay = g_cls[0]["lay"]
            g_has_fc = any(
                _has_flashcards(r.get("seg") or {}, plan)
                for r in rows
                if r["lay"] == g_lay
                and (
                    id(member[(g_lay, r["live"]["slot"], r["live"]["clip_key"])][0]) in g_cids
                    or id(member[(g_lay, r["peer"]["slot"], r["peer"]["clip_key"])][0]) in g_cids
                )
            )
            eff_hr: Optional[float] = None
            if g_lay == "split_h" and g_has_fc and "ZENO_SPLIT_HEADROOM" not in os.environ:
                default_hr = round(TARGET_HEADROOM_PCT / 100.0, 4)
                avails: List[float] = []
                for c in g_cls:
                    y0_est = _estimate_clip_head_top_y0(c["ref"], c.get("focus"))
                    if y0_est is not None:
                        cw, ch = c["dims"]
                        sw_s, sh_s = c["slot_size"]
                        s0_c = max(sw_s / float(max(1, cw)), sh_s / float(max(1, ch)))
                        hr_r = (ch * s0_c) / float(max(1, sh_s))
                        avails.append(y0_est * hr_r)
                if avails:
                    eff_hr = round(max(default_hr, min(FLASHCARD_SPLIT_HEADROOM, min(avails))), 4)
            t_vfh, t_ey = group_targets_fn([c["th_panel"] for c in g_cls], common_hr=eff_hr)
            group_targets[root_id] = (t_vfh, t_ey)

    def _geom_crop(lay: str, part: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        cl, reg, how = member[(lay, part["slot"], part["clip_key"])]
        if cl.get("geom") is None:
            return None
        w_in, h_in = _dims(part["src"])
        s_c = cl["geom"]["geom"]["s"] / max(0.01, reg["a"])
        if abs(reg["a"] - 1.0) < 0.003 and abs(s_c * reg["tx"]) < 1.0 and abs(s_c * reg["ty"]) < 1.0:
            reg = {"a": 1.0, "tx": 0.0, "ty": 0.0}
        if (w_in, h_in) != cl["dims"] and how == "assumed":
            crop = derive_registered_crop(cl["geom"], None, w_in, h_in)
        else:
            crop = derive_registered_crop(cl["geom"], reg, w_in, h_in)
        crop["offset_y"] = round(float(cl["geom"].get("offset_y", cl.get("offset_y", 0.0))), 4)
        crop["lock"] = {
            "how": how,
            "scale": round(reg["a"], 4),
            "shift_px": [round(s_c * reg["tx"], 1), round(s_c * reg["ty"], 1)],
        }
        return crop

    def _colour_for(lay: str, slot: str, cl: Dict[str, Any], k: str) -> Tuple[str, str]:
        if len(cl["clips"]) < 2:
            return "", ""
        if cl.get("colour") is None:
            geo = {
                kk: _geom_crop(lay, {"slot": slot, "src": c["path"], "clip_key": kk})
                for kk, c in cl["clips"].items()
            }
            top_skip = max(int((g or {}).get("pad_top") or 0) for g in geo.values())
            stats = {
                kk: (_panel_colour_stats(c["path"], geo[kk], c["t"], top_skip) if geo[kk] else None)
                for kk, c in cl["clips"].items()
            }
            ref_k = max(cl["clips"], key=lambda kk: cl["clips"][kk]["live_n"])
            cl["colour"] = {}
            for kk in cl["clips"]:
                if kk == ref_k or stats.get(kk) is None or stats.get(ref_k) is None:
                    cl["colour"][kk] = ("", "")
                else:
                    cl["colour"][kk] = _panel_colour_filter(stats[ref_k], stats[kk])
        return cl["colour"].get(k, ("", ""))

    def _crop_for(lay: str, part: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        crop = _geom_crop(lay, part)
        if crop is None:
            return None
        cl = member[(lay, part["slot"], part["clip_key"])][0]
        flt, note = _colour_for(lay, part["slot"], cl, part["clip_key"])
        if flt:
            crop = dict(crop, filter=crop["filter"] + "," + flt, colour_note=note)
        return crop

    seen: set = set()
    for row in rows:
        lay = row["lay"]
        cl_live = member[(lay, row["live"]["slot"], row["live"]["clip_key"])][0]
        cl_peer = member[(lay, row["peer"]["slot"], row["peer"]["clip_key"])][0]
        if cl_live.get("geom") is None or cl_peer.get("geom") is None:
            lw, lh = cl_live["dims"]
            pw, ph = cl_peer["dims"]
            comp_ids = {id(cl_live), id(cl_peer)}
            changed = True
            while changed:
                changed = False
                for r in rows:
                    if r["lay"] != lay:
                        continue
                    c_l = member[(lay, r["live"]["slot"], r["live"]["clip_key"])][0]
                    c_p = member[(lay, r["peer"]["slot"], r["peer"]["clip_key"])][0]
                    if id(c_l) in comp_ids or id(c_p) in comp_ids:
                        if id(c_l) not in comp_ids:
                            comp_ids.add(id(c_l))
                            changed = True
                        if id(c_p) not in comp_ids:
                            comp_ids.add(id(c_p))
                            changed = True
            cluster_rows = [
                r for r in rows
                if r["lay"] == lay
                and (
                    id(member[(lay, r["live"]["slot"], r["live"]["clip_key"])][0]) in comp_ids
                    or id(member[(lay, r["peer"]["slot"], r["peer"]["clip_key"])][0]) in comp_ids
                )
            ]
            cluster_has_fc = any(_has_flashcards(r.get("seg") or {}, plan) for r in cluster_rows)
            live_oy = float(cl_live.get("offset_y") or 0.0)
            peer_oy = float(cl_peer.get("offset_y") or 0.0)
            grp_vfh, grp_ey = (None, None)
            if cl_live["is_th"] and _uf_find(id(cl_live)) in group_targets:
                grp_vfh, grp_ey = group_targets[_uf_find(id(cl_live))]
            elif cl_peer["is_th"] and _uf_find(id(cl_peer)) in group_targets:
                grp_vfh, grp_ey = group_targets[_uf_find(id(cl_peer))]
            g_live, g_peer = _compute_paired_split_crop_filters_for_layout(
                lay=lay,
                seg=row.get("seg"),
                plan=plan,
                live_src=cl_live["ref"],
                peer_src=cl_peer["ref"],
                src_w=lw,
                src_h=lh,
                peer_w=pw,
                peer_h=ph,
                live_slot_w=row["live"]["size"][0],
                live_slot_h=row["live"]["size"][1],
                peer_slot_w=row["peer"]["size"][0],
                peer_slot_h=row["peer"]["size"][1],
                live_focus=cl_live.get("focus"),
                peer_focus=cl_peer.get("focus"),
                live_offset_y=live_oy,
                peer_offset_y=peer_oy,
                force_flashcards=cluster_has_fc,
                live_is_broll=cl_live["is_broll"],
                peer_is_broll=cl_peer["is_broll"],
                target_vis_fh=grp_vfh,
                target_ey=grp_ey,
                live_min_zoom=cl_live["min_zoom"],
                peer_min_zoom=cl_peer["min_zoom"],
            )
            if cl_live.get("geom") is None:
                cl_live["geom"] = dict(g_live, offset_y=live_oy)
            if cl_peer.get("geom") is None:
                cl_peer["geom"] = dict(g_peer, offset_y=peer_oy)
        live_crop, peer_crop = _crop_for(lay, row["live"]), _crop_for(lay, row["peer"])
        if live_crop is None or peer_crop is None:
            continue
        seg_lock_entry: Dict[str, Any] = {"live": live_crop, "peer": peer_crop}
        hold_crop = _crop_for(lay, row["hold"]) if row.get("hold") else None
        if hold_crop is not None:
            seg_lock_entry["hold"] = hold_crop
        out["segs"][row["sig"]] = seg_lock_entry
        report_pairs = [(row["live"], live_crop), (row["peer"], peer_crop)]
        if row.get("hold") and hold_crop is not None:
            report_pairs.append((row["hold"], hold_crop))
        for part, crop in report_pairs:
            tag = (lay, part["slot"], str(part["src"]))
            if tag in seen:
                continue
            seen.add(tag)
            lk = crop["lock"]
            if lk["how"] == "reference":
                note = "anchors panel frame"
            elif lk["how"] == "assumed":
                note = "identical framing"
            else:
                note = (
                    f"aligned by background (shift {lk['shift_px'][0]:+.0f},{lk['shift_px'][1]:+.0f} px, "
                    f"scale x{1.0 / max(0.01, lk['scale']):.3f})"
                )
            if crop.get("colour_note"):
                note += "; " + crop["colour_note"]
            if crop.get("clamp_px", 0) > 2.0:
                note += f"; [SPLIT_ALIGN] {crop['clamp_px']:.0f} px clamped at frame edge"
            out["report"].append(f"{part['slot']:>6}: {part['src'].name} - {note}")
    for key, cls in clusters.items():
        if len(cls) > 1:
            names = ", ".join(c["ref"].name for c in cls)
            out["report"].append(
                f"{key[1]:>6}: [SPLIT_ALIGN] distinct backgrounds in panel ({names}); each retains its own frame"
            )
    return out


def split_panel_lock(seg: Dict[str, Any], plan: Optional[Dict[str, Any]], W: int, H: int) -> Optional[Dict[str, Any]]:
    """Return locked crops `{'live': ..., 'peer': ...}` for a split segment, or `None`."""
    if not isinstance(plan, dict) or not plan.get("segments"):
        return None
    try:
        sp_offsets = [
            [str(s.get("speaker") or s.get("sp") or ""), _parse_offset_y(s.get("offset_y"))]
            for s in plan["segments"]
            if isinstance(s, dict) and abs(_parse_offset_y(s.get("offset_y"))) > 1e-4
        ]
        key = hashlib.md5(
            json.dumps(
                [
                    [
                        _split_seg_sig(s),
                        _file_mtime_sig(s["src"]),
                        _file_mtime_sig(s["hold_src"]) if s.get("hold_src") else None,
                        bool(_has_flashcards(s, plan)),
                    ]
                    for s in plan["segments"]
                    if isinstance(s, dict)
                    and normalize_layout_name(s.get("layout")) in ("split_h", "split_v")
                    and s.get("src")
                ]
                + [W, H, bool(_has_flashcards(None, plan)), sp_offsets, plan.get("speakers")],
                default=str,
            ).encode("utf-8")
        ).hexdigest()
        with _CACHE_LOCK:
            if key not in _SPLIT_LOCK_CACHE:
                _SPLIT_LOCK_CACHE[key] = _compute_split_locks(plan, W, H)
            plan_locks = _SPLIT_LOCK_CACHE[key]
        return plan_locks["segs"].get(_split_seg_sig(seg))
    except Exception:
        return None


def split_lock_report(plan: Optional[Dict[str, Any]], W: int, H: int) -> List[str]:
    """Return human-readable diagnostic lines describing how each split panel clip was locked."""
    for seg in ((plan or {}).get("segments") or []):
        if isinstance(seg, dict) and normalize_layout_name(seg.get("layout")) in ("split_h", "split_v"):
            split_panel_lock(seg, plan, W, H)
            break
    for val in _SPLIT_LOCK_CACHE.values():
        if val.get("report") is not None and any(
            _split_seg_sig(s) in val["segs"]
            for s in ((plan or {}).get("segments") or [])
            if isinstance(s, dict) and s.get("src")
        ):
            return list(val["report"])
    return []


def build_segment_cache_key(
    seg: Dict[str, Any],
    plan: Optional[Dict[str, Any]],
    W: int,
    H: int,
    fps: int,
    draft: bool,
) -> str:
    """Compute a deterministic 16-character MD5 hex cache key for a segment and its layout."""
    lay = normalize_layout_name(seg.get("layout"))
    peer = seg.get("peer") if isinstance(seg.get("peer"), dict) else None
    peer_sig = None
    if peer and peer.get("src"):
        peer_sig = {
            "src": _file_mtime_sig(peer["src"]),
            "start": peer.get("start"),
            "still_at": peer.get("still_at"),
            "idle_span": peer.get("idle_span"),
            "mode": peer.get("mode", "loop"),
            "focus": peer.get("focus"),
            "slot": peer.get("slot"),
            "offset_y": _parse_offset_y(peer.get("offset_y")),
        }

    eff_fps = max(1, int(fps or 24))
    spd = _segment_speed(seg)
    is_muted = _segment_is_muted(seg)
    is_beat = _segment_is_beat(seg)
    hold_tail = _segment_hold_tail(seg)
    _, _, _, play_exact, _ = _quantize_segment_frames(seg, eff_fps)
    rz0, rz1, rz_style, rz_at, rz_dur = resolve_segment_zoom_spec(seg, play_exact)
    rz_peak = _resolve_zoom_peak(seg, rz0, rz1, rz_style)
    active_panel = _resolve_active_slot(seg)
    baked_sig = None
    if lay == "single" and (
        abs(rz0 - 1.0) > 0.004
        or abs(rz1 - 1.0) > 0.004
        or rz_style in ("flash_zoom", "bell")
    ):
        src_p = resolve(seg["src"]) if seg.get("src") else None
        bspec = _resolve_baked_split_spec(seg, src_p)
        if bspec is not None:
            baked_sig = {
                "kind": bspec["kind"],
                "active": bspec["active"],
                "seam_frac": bspec["seam_frac"],
                "seam_band_frac": bspec["seam_band_frac"],
            }

    split_lock_sig = None
    if lay in ("split_h", "split_v"):
        lock = split_panel_lock(seg, plan, W, H)
        if lock:
            split_lock_sig = [
                lock["live"]["filter"],
                lock["peer"]["filter"],
                lock["live"].get("offset_y", 0.0),
                lock["peer"].get("offset_y", 0.0),
                lock["hold"]["filter"] if isinstance(lock.get("hold"), dict) else None,
            ]

    hold_src_sig = _file_mtime_sig(seg["hold_src"]) if seg.get("hold_src") else None
    hold_start, hold_end = _segment_hold_span(seg)
    audio_end_val = _segment_audio_end(seg)

    plan_loudness = float((plan or {}).get("audio", {}).get("loudness", DEFAULT_LOUDNESS_LUFS))
    payload = {
        "v": 20,
        "src": _file_mtime_sig(seg["src"]),
        "start": round(float(seg["start"]), 4),
        "end": round(float(seg["end"]), 4),
        "speed": round(spd, 4),
        "mute": bool(is_muted),
        "beat": bool(is_beat),
        "hold_tail": round(hold_tail, 4),
        "hold_src": hold_src_sig,
        "hold_start": hold_start,
        "hold_end": hold_end,
        "audio_end": audio_end_val,
        "layout": lay,
        "slot": seg.get("slot"),
        "active": active_panel,
        "offset_y": _parse_offset_y(seg.get("offset_y")),
        "framing": seg.get("framing"),
        "divider": seg.get("divider"),
        "zoom": seg.get("zoom"),
        "zoom_to": seg.get("zoom_to"),
        "zoom_end": seg.get("zoom_end"),
        "zoom_peak": seg.get("zoom_peak"),
        "zoom_style": seg.get("zoom_style"),
        "zoom_at": seg.get("zoom_at"),
        "zoom_dur": seg.get("zoom_dur"),
        "resolved_zoom": [
            round(rz0, 4),
            round(rz1, 4),
            round(rz_peak, 4) if rz_peak is not None else None,
            rz_style,
            round(rz_at, 4),
            round(rz_dur, 4),
        ],
        "baked_split": baked_sig,
        "split_lock": split_lock_sig,
        "split_flashcard_hr": bool(lay == "split_h" and _has_flashcards(seg, plan)),
        "page": bool(isinstance((plan or {}).get("page"), dict)),
        "focus": seg.get("focus"),
        "focus_end": seg.get("focus_end"),
        "color_match": bool(seg.get("color_match") is True or (plan or {}).get("color_match") is True),
        "lut": seg.get("lut") or (plan or {}).get("lut"),
        "gain_db": seg.get("gain_db"),
        "loudness": seg.get("loudness"),
        "plan_loudness": plan_loudness,
        "peer": peer_sig,
        "chat": seg.get("chat"),
        "banners": seg.get("banners") or seg.get("stop_saying"),
        "W": int(W),
        "H": int(H),
        "fps": int(fps),
        "draft": bool(draft),
    }
    raw = json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.md5(raw.encode("utf-8")).hexdigest()[:16]


def _build_speed_video_prefix(
    speed: float,
    fps: int,
    play_frames: int,
    src_dur: float,
) -> List[str]:
    """Build video retiming and motion-blended frame-synthesis prefix filters preceding scaling/zoompan."""
    if abs(speed - 1.0) <= 1e-3:
        return ["setpts=PTS-STARTPTS", f"fps={fps}"]
    # Retain slight lookahead past src_dur so `framerate` can blend cleanly through the final frame
    trim_span = max(0.08, float(src_dur) + 0.15)
    chain: List[str] = [
        "setpts=PTS-STARTPTS",
        f"trim=0:{trim_span:.6f}",
        f"setpts=(PTS-STARTPTS)/{speed:.6f}",
    ]
    if speed < 0.999:
        chain.append(f"framerate=fps={fps}:interp_start=0:interp_end=255:scene=100")
    else:
        chain.append(f"fps={fps}")
    chain.extend([
        f"trim=end_frame={max(1, int(play_frames))}",
        "settb=AVTB",
        f"setpts=N/({fps}*TB)",
    ])
    return chain


def _build_atempo_chain(speed: float) -> List[str]:
    """Build chained FFmpeg `atempo` filters (each stage in `[0.5, 2.0]`) for `speed != 1.0`."""
    if abs(speed - 1.0) <= 1e-3:
        return []
    spd = max(0.0625, min(16.0, float(speed)))
    if 0.5 <= spd <= 2.0:
        return [f"atempo={spd:.6f}"]
    if 0.25 <= spd < 0.5 or 2.0 < spd <= 4.0:
        stage = math.sqrt(spd)
        return [f"atempo={stage:.6f}", f"atempo={stage:.6f}"]
    stages: List[str] = []
    rem = spd
    while rem < 0.5:
        stages.append("atempo=0.500000")
        rem /= 0.5
    while rem > 2.0:
        stages.append("atempo=2.000000")
        rem /= 2.0
    if abs(rem - 1.0) > 1e-4:
        stages.append(f"atempo={rem:.6f}")
    return stages


def _build_slot_video_chain(
    input_label: str,
    output_label: str,
    src_w: int,
    src_h: int,
    slot_w: int,
    slot_h: int,
    fps: int,
    exact_dur: float,
    focus: Sequence[float],
    z0: float = 1.0,
    z1: float = 1.0,
    bbox_norm: Optional[Sequence[float]] = None,
    exact: bool = False,
    zoom_style: str = "smooth",
    zoom_at: float = 0.0,
    zoom_dur: float = 0.18,
    focus_end: Optional[Sequence[float]] = None,
    color_filter: Optional[str] = None,
    prebuilt_crop_filter: Optional[str] = None,
    headroom_pct: float = TARGET_HEADROOM_PCT,
    zoom_peak: Optional[float] = None,
    speed: float = 1.0,
    src_dur: Optional[float] = None,
    play_frames: Optional[int] = None,
    max_slot_fill: Optional[float] = None,
) -> str:
    """Build an FFmpeg video filter chain scaling and cropping `input_label` into
    `(slot_w, slot_h)` with eye-anchored headroom (`14%`), speed retiming, and sub-pixel zoom.
    """
    slot_w, slot_h = even(slot_w), even(slot_h)
    fx = max(0.05, min(0.95, float(focus[0]) if len(focus) >= 1 else 0.5))
    fy = max(0.08, min(0.85, float(focus[1]) if len(focus) >= 2 else 0.38))
    fx1 = float(focus_end[0]) if focus_end and len(focus_end) >= 1 else fx
    fy1 = float(focus_end[1]) if focus_end and len(focus_end) >= 2 else fy
    has_pan = math.hypot(fx1 - fx, fy1 - fy) > 0.04
    style_norm = str(zoom_style or "").strip().lower()
    is_bell_active = style_norm == "bell" and (
        zoom_peak is None
        or abs(float(zoom_peak) - float(z0)) > 0.004
        or abs(float(zoom_peak) - float(z1)) > 0.004
        or abs(float(zoom_peak) - 1.0) > 0.004
    )
    is_static_1x = (
        abs(z0 - 1.0) <= 0.004
        and abs(z1 - 1.0) <= 0.004
        and style_norm in ("none", "static", "")
        and not is_bell_active
        and not has_pan
    )
    pf = play_frames if play_frames is not None else max(1, int(round(exact_dur * fps)))
    sd = src_dur if src_dur is not None else max(0.04, exact_dur * speed)
    speed_prefix = _build_speed_video_prefix(speed, fps, pf, sd)

    if exact and abs((src_w / max(1, src_h)) / (slot_w / max(1, slot_h)) - 1.0) < 0.03:
        if is_static_1x:
            cf_part = f",{color_filter}" if color_filter else ""
            return (
                f"{input_label}{','.join(speed_prefix)},"
                f"scale={slot_w}:{slot_h}:flags=lanczos{cf_part},setsar=1,format=yuv420p{output_label}"
            )
        chain = [*speed_prefix, f"scale={slot_w}:{slot_h}:flags=lanczos"]
        z_fx, z_fy, z_fx1, z_fy1 = fx, fy, fx1, fy1
    else:
        chain = list(speed_prefix)
        if prebuilt_crop_filter:
            chain.append(prebuilt_crop_filter)
            z_fx, z_fy, z_fx1, z_fy1 = fx, fy, fx1, fy1
        else:
            slot_kw: Dict[str, Any] = {
                "src_w": src_w,
                "src_h": src_h,
                "slot_w": slot_w,
                "slot_h": slot_h,
                "bbox_norm": bbox_norm,
                "focus": [fx, fy],
                "headroom_pct": headroom_pct,
            }
            if max_slot_fill is not None:
                try:
                    sc_params = inspect.signature(compute_slot_crop_filter).parameters
                    if "max_slot_fill" in sc_params or any(
                        p.kind == inspect.Parameter.VAR_KEYWORD for p in sc_params.values()
                    ):
                        slot_kw["max_slot_fill"] = float(max_slot_fill)
                except (TypeError, ValueError):
                    pass
            crop_info = compute_slot_crop_filter(**slot_kw)
            chain.append(crop_info["filter"])
            sf = crop_info.get("slot_focus")
            if isinstance(sf, (list, tuple)) and len(sf) >= 2:
                z_fx, z_fy = float(sf[0]), float(sf[1])
                if has_pan:
                    sw_c, sh_c = crop_info.get("scale", (slot_w, slot_h))
                    cx_c, cy_c = crop_info.get("crop", (0, 0))
                    z_fx1 = max(0.15, min(0.85, (fx1 * float(sw_c) - float(cx_c)) / float(slot_w)))
                    z_fy1 = max(0.15, min(0.75, (fy1 * float(sh_c) - float(cy_c)) / float(slot_h)))
                else:
                    z_fx1, z_fy1 = z_fx, z_fy
            else:
                z_fx, z_fy, z_fx1, z_fy1 = fx, fy, fx1, fy1

    chain.extend(
        _build_subpixel_zoom_filter(
            slot_w=slot_w,
            slot_h=slot_h,
            fps=fps,
            exact_dur=exact_dur,
            fx=z_fx,
            fy=z_fy,
            z0=z0,
            z1=z1,
            zoom_style=zoom_style,
            zoom_at=zoom_at,
            zoom_dur=zoom_dur,
            fx1=z_fx1,
            fy1=z_fy1,
            zoom_peak=zoom_peak,
        )
    )
    if color_filter:
        chain.append(color_filter)
    chain.extend(["setsar=1", "format=yuv420p"])
    return f"{input_label}{','.join(chain)}{output_label}"


def _extract_peer_still_png(peer_src: Path, still_at: float) -> Path:
    """Extract and cache a single mouth-closed frame PNG from `peer_src` at `still_at`."""
    UI_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    sig = f"{_file_mtime_sig(peer_src)}|{still_at:.3f}"
    digest = hashlib.sha1(sig.encode("utf-8")).hexdigest()[:16]
    out_png = UI_CACHE_DIR / f"peer_still_{digest}.png"
    with _CACHE_LOCK:
        if out_png.is_file() and out_png.stat().st_size > 100:
            return out_png

        info = probe(peer_src)
        dur = float(info.get("duration") or 5.0)
        clamped_t = max(0.0, min(float(still_at), max(0.0, dur - 0.08)))
        tmp_png = UI_CACHE_DIR / f"peer_still_{digest}.{threading.get_ident()}.tmp.png"
        ffmpeg("-ss", f"{clamped_t:.3f}", "-i", peer_src, "-frames:v", "1", tmp_png)
        if tmp_png.is_file():
            os.replace(tmp_png, out_png)
    return out_png


def _append_peer_video_input(
    peer: Dict[str, Any],
    peer_src: Path,
    peer_dur: float,
    dur: float,
    exact: float,
    fps: int,
    inputs: List[Any],
    fc: List[str],
    peer_idx: int,
    default_still: bool = False,
) -> str:
    """Append FFmpeg input arguments and optional seamless loop filter for a listener `peer` clip.
    Returns the FFmpeg filtergraph input label (`'[1:v]'` or `'[peer_loop_in]'`).
    """
    still_at = float(peer.get("still_at") if peer.get("still_at") is not None else 0.5)
    idle_span = peer.get("idle_span")
    has_span = isinstance(idle_span, (list, tuple)) and len(idle_span) >= 2
    default_mode = "still" if (default_still and not has_span and not _SILENT_CLIP_NAME_RE.search(peer_src.stem)) else "loop"
    mode = str(peer.get("mode") or default_mode).lower()
    peer_loop_filter = ""

    if peer_src.is_file() and mode == "live":
        p_start = max(0.0, float(peer.get("start", 0.0) or 0.0))
        target_dur = max(float(dur), float(exact))
        avail_live = max(0.0, peer_dur - p_start)
        if avail_live >= target_dur - 0.10:
            inputs.extend(["-ss", f"{p_start:.3f}", "-t", f"{target_dur + 0.5:.3f}", "-i", peer_src])
        elif peer_dur >= 0.30:
            safe_s0 = p_start if avail_live >= 0.30 else 0.0
            safe_span = max(0.30, peer_dur - safe_s0)
            inputs.extend(["-ss", f"{safe_s0:.3f}", "-t", f"{safe_span:.3f}", "-i", peer_src])
            loop_frames = max(2, int(round(safe_span * fps)))
            peer_loop_filter = (
                f"fps={fps},loop=loop=-1:size={loop_frames}:start=0,"
                f"setpts=N/({fps}*TB),trim=0:{target_dur:.4f},setpts=PTS-STARTPTS"
            )
        else:
            inputs.extend([
                "-ss", f"{min(p_start, max(0.0, peer_dur - 0.08)):.3f}",
                "-t", f"{target_dur + 0.5:.3f}", "-i", peer_src,
            ])
    elif peer_src.is_file() and mode != "still":
        if has_span and (float(idle_span[1]) - float(idle_span[0])) >= 0.30:
            s0 = max(0.0, min(float(idle_span[0]), max(0.0, peer_dur - 0.30)))
            s1 = min(peer_dur, float(idle_span[1]))
            s_dur = max(0.30, s1 - s0)
        else:
            s0 = float(peer.get("start", 0.0) or 0.0) % max(0.5, peer_dur)
            s1 = peer_dur
            s_dur = max(0.30, peer_dur - s0)

        avail_from_s0 = max(0.0, peer_dur - s0)
        is_dedicated_silent = (s0 <= 0.15 and abs(s1 - peer_dur) <= 0.25) or bool(
            _SILENT_CLIP_NAME_RE.search(peer_src.stem)
        )
        if s_dur >= exact - 0.02 and avail_from_s0 >= exact + 0.05:
            inputs.extend(["-ss", f"{s0:.3f}", "-t", f"{exact + 0.5:.3f}", "-i", peer_src])
        elif is_dedicated_silent:
            inputs.extend(["-stream_loop", "-1", "-ss", f"{s0:.3f}", "-i", peer_src])
            peer_loop_filter = f"trim=0:{exact + 0.15:.4f},setpts=PTS-STARTPTS"
        else:
            inputs.extend(["-ss", f"{s0:.3f}", "-t", f"{s_dur:.3f}", "-i", peer_src])
            loop_frames = max(2, int(round(s_dur * fps)))
            peer_loop_filter = (
                f"fps={fps},loop=loop=-1:size={loop_frames}:start=0,"
                f"setpts=N/({fps}*TB),trim=0:{exact + 0.15:.4f},setpts=PTS-STARTPTS"
            )
    elif peer_src.is_file():
        still_png = _extract_peer_still_png(peer_src, still_at)
        inputs.extend(["-loop", "1", "-framerate", str(fps), "-t", f"{exact:.4f}", "-i", still_png])
    else:
        inputs.extend(["-ss", f"{still_at:.3f}", "-t", f"{exact:.4f}", "-i", peer_src])

    if peer_loop_filter:
        fc.append(f"[{peer_idx}:v]{peer_loop_filter}[peer_loop_in]")
        return "[peer_loop_in]"
    return f"[{peer_idx}:v]"


def _append_hold_video_input(
    hold_src: Path,
    hold_start: Optional[float],
    hold_end: Optional[float],
    hold_dur: float,
    hold_media_dur: float,
    fps: int,
    inputs: List[Any],
    fc: List[str],
    hold_idx: int,
) -> str:
    """Append FFmpeg input arguments for a live `hold_src` tail reaction clip and return its video label."""
    h_dur_target = max(0.08, float(hold_dur))
    h_start = max(0.0, float(hold_start) if hold_start is not None else 0.0)
    if h_start >= max(0.05, hold_media_dur - 0.10):
        h_start = max(0.0, hold_media_dur - max(0.30, h_dur_target))
    h_avail = max(0.04, hold_media_dur - h_start)
    has_bound = hold_end is not None and float(hold_end) > h_start + 0.08
    span_len = min(h_avail, float(hold_end) - h_start) if has_bound else h_avail

    if span_len >= h_dur_target - 0.02 and (h_start + h_dur_target <= hold_media_dur - 0.02 or span_len >= h_dur_target):
        inputs.extend(["-ss", f"{h_start:.3f}", "-t", f"{h_dur_target + 0.35:.3f}", "-i", hold_src])
        return f"[{hold_idx}:v]"
    if span_len >= 0.15:
        inputs.extend(["-ss", f"{h_start:.3f}", "-t", f"{span_len:.3f}", "-i", hold_src])
        loop_frames = max(2, int(round(span_len * fps)))
        fc.append(
            f"[{hold_idx}:v]fps={fps},loop=loop=-1:size={loop_frames}:start=0,"
            f"setpts=N/({fps}*TB),trim=0:{h_dur_target + 0.20:.4f},setpts=PTS-STARTPTS[hold_loop_in]"
        )
        return "[hold_loop_in]"
    safe_s0 = max(0.0, hold_media_dur - 0.5)
    inputs.extend(["-ss", f"{safe_s0:.3f}", "-t", "0.500", "-i", hold_src])
    return f"[{hold_idx}:v]"


def build_segment_ffmpeg_args(
    seg: Dict[str, Any],
    plan: Optional[Dict[str, Any]],
    W: int,
    H: int,
    fps: int,
    out: Union[str, Path],
    draft: bool = False,
) -> Tuple[List[Any], float, int]:
    """Construct the complete FFmpeg argument list for rendering `seg` in any layout mode.

    Returns `(ffmpeg_args, exact_duration_sec, frames_count)`.
    """
    plan = plan or {}
    W, H = even(W), even(H)
    fps = max(1, int(fps or 24))
    src_dur = max(0.04, float(seg["end"]) - float(seg["start"]))
    spd = _segment_speed(seg)
    is_retimed = abs(spd - 1.0) > 1e-3
    is_muted = _segment_is_muted(seg)
    dur = _segment_play_duration(seg) if is_retimed else max(0.08, src_dur)
    hold_tail = _segment_hold_tail(seg)
    play_frames, hold_frames, frames, play_exact, exact = _quantize_segment_frames(seg, fps)
    audio_end_val = _segment_audio_end(seg)
    has_audio_end = audio_end_val is not None and 0.04 < audio_end_val < play_exact

    lay = normalize_layout_name(seg.get("layout"))
    z0, z1, zoom_style, zoom_at, zoom_dur = resolve_segment_zoom_spec(seg, play_exact)
    zoom_peak = _resolve_zoom_peak(seg, z0, z1, zoom_style)
    focus = list(seg.get("focus") or [0.5, 0.38])
    focus_end = list(seg.get("focus_end") or focus)
    fx, fy = float(focus[0]), float(focus[1])
    fx1, fy1 = float(focus_end[0]), float(focus_end[1])

    src = resolve(seg["src"])
    src_info = probe(src) if src.is_file() else {"width": W, "height": H, "has_audio": True, "duration": src_dur + 1.0}
    src_w = int(src_info.get("width") or W)
    src_h = int(src_info.get("height") or H)
    has_audio = bool(src_info.get("has_audio", True))
    use_silence = is_muted or (not has_audio)
    color_filter = compute_face_color_match_filter(seg, plan)

    if not use_silence:
        seg_gain_db = float(seg.get("gain_db") or 0.0)
        clip_gain_db = _measure_clip_rms_gain_db(src)
        total_gain_db = round(clip_gain_db + seg_gain_db, 2)
        a_active_dur = min(play_exact, audio_end_val) if has_audio_end else play_exact
        a_fade_out = min(0.030, max(EDGE_FADE, a_active_dur * 0.25)) if has_audio_end else EDGE_FADE

        af_parts = [
            "asetpts=PTS-STARTPTS",
            f"atrim=0:{(src_dur if is_retimed else a_active_dur):.6f}",
            "asetpts=PTS-STARTPTS",
        ]
        if is_retimed:
            af_parts.extend(_build_atempo_chain(spd))
            af_parts.append("asetpts=PTS-STARTPTS")
        af_parts.append("highpass=f=70")
        if abs(total_gain_db) >= 0.05:
            af_parts.append(f"volume={total_gain_db:+.2f}dB")
        af_parts.extend([
            "alimiter=limit=0.891:level=false",
            "aresample=48000",
            "aformat=channel_layouts=stereo",
            "apad",
            f"atrim=0:{a_active_dur:.6f}",
            "asetpts=PTS-STARTPTS",
            f"afade=t=in:d={EDGE_FADE}",
            f"afade=t=out:st={max(0.0, a_active_dur - a_fade_out):.6f}:d={a_fade_out:.6f}",
        ])
        if hold_tail > 0.001 or has_audio_end:
            af_parts.extend([
                "apad",
                f"atrim=0:{exact:.6f}",
                "asetpts=PTS-STARTPTS",
            ])
        af_chain = ",".join(af_parts)
    else:
        af_chain = f"atrim=0:{exact:.6f},asetpts=PTS-STARTPTS"

    inputs: List[Any] = ["-ss", f"{float(seg['start']):.3f}", "-t", f"{src_dur + 0.5:.3f}", "-i", src]
    audio_input_idx: Optional[int] = 0 if not use_silence else None
    next_idx = 1

    fc: List[str] = []
    v_curr = "[vbase]"

    hold_src_raw = seg.get("hold_src")
    hold_src_path = resolve(hold_src_raw) if hold_src_raw else None
    has_live_hold = bool(
        hold_tail > 0.001
        and hold_frames > 0
        and hold_src_path is not None
        and hold_src_path.is_file()
    )
    hold_in_label: Optional[str] = None
    hold_w, hold_h = src_w, src_h
    hold_vis: Dict[str, Any] = {}
    if has_live_hold and hold_src_path is not None:
        hold_info = probe(hold_src_path)
        hold_w = int(hold_info.get("width") or src_w)
        hold_h = int(hold_info.get("height") or src_h)
        hold_media_dur = float(hold_info.get("duration") or 6.0)
        hold_s0, hold_s1 = _segment_hold_span(seg)
        hold_dur_exact = max(0.04, hold_frames / float(fps))
        hold_idx = next_idx
        next_idx += 1
        hold_in_label = _append_hold_video_input(
            hold_src_path, hold_s0, hold_s1, hold_dur_exact, hold_media_dur, fps, inputs, fc, hold_idx
        )
        try:
            hold_vis = analyze_clip_vision(hold_src_path) or {}
        except Exception:
            hold_vis = {}

    def _attach_live_hold(
        live_lbl: str,
        out_lbl: str,
        slot_w: int,
        slot_h: int,
        *,
        prebuilt_crop_filter: Optional[str] = None,
        use_slot_crop: bool = False,
        headroom_pct: float = TARGET_HEADROOM_PCT,
        hold_focus: Optional[Sequence[float]] = None,
        z_hold: float = 1.0,
        exact_fit: bool = False,
        apply_color: bool = True,
    ) -> None:
        if not has_live_hold or hold_in_label is None:
            if live_lbl != out_lbl:
                fc.append(f"{live_lbl}null{out_lbl}")
            return
        slot_w_e, slot_h_e = even(slot_w), even(slot_h)
        hold_dur_exact = max(0.04, hold_frames / float(fps))
        h_foc = list(hold_focus) if (hold_focus and len(hold_focus) >= 2) else (
            list(hold_vis.get("focus") or [fx1, fy1]) if hold_src_path != src else [fx1, fy1]
        )
        hfx = max(0.05, min(0.95, float(h_foc[0])))
        hfy = max(0.08, min(0.85, float(h_foc[1])))
        if prebuilt_crop_filter:
            h_ops = ["setpts=PTS-STARTPTS", f"fps={fps}", prebuilt_crop_filter]
            if apply_color and color_filter:
                h_ops.append(color_filter)
            h_ops.extend(["setsar=1", "format=yuv420p"])
            fc.append(f"{hold_in_label}{','.join(h_ops)}[hold_slot_raw]")
        elif use_slot_crop:
            fc.append(
                _build_slot_video_chain(
                    hold_in_label, "[hold_slot_raw]", hold_w, hold_h, slot_w_e, slot_h_e,
                    fps, hold_dur_exact, [hfx, hfy], 1.0, 1.0,
                    bbox_norm=(hold_vis.get("bbox_norm") if _vis_has_real_face_safe(hold_vis) else None),
                    exact=exact_fit,
                    zoom_style="none",
                    color_filter=(color_filter if apply_color else None),
                    headroom_pct=headroom_pct,
                )
            )
        else:
            h_ops = [
                "setpts=PTS-STARTPTS",
                f"fps={fps}",
                f"scale={slot_w_e}:{slot_h_e}:force_original_aspect_ratio=increase:flags=lanczos",
                f"crop={slot_w_e}:{slot_h_e}:x='(iw-{slot_w_e})*{hfx:.4f}':y='(ih-{slot_h_e})*{hfy:.4f}'",
            ]
            if z_hold > 1.004:
                zw_h, zh_h = even(slot_w_e * z_hold), even(slot_h_e * z_hold)
                h_ops.extend([
                    f"scale={zw_h}:{zh_h}:flags=lanczos",
                    f"crop={slot_w_e}:{slot_h_e}:x='(iw-{slot_w_e})*{hfx:.4f}':y='(ih-{slot_h_e})*{hfy:.4f}'",
                ])
            if apply_color and color_filter:
                h_ops.append(color_filter)
            h_ops.extend(["setsar=1", "format=yuv420p"])
            fc.append(f"{hold_in_label}{','.join(h_ops)}[hold_slot_raw]")

        fc.append(
            f"{live_lbl}tpad=stop_mode=clone:stop=4,trim=end_frame={play_frames},"
            f"setpts=PTS-STARTPTS,settb=AVTB,setpts=N/({fps}*TB),fps={fps},setsar=1,format=yuv420p[_hlv]"
        )
        fc.append(
            f"[hold_slot_raw]tpad=stop_mode=clone:stop={hold_frames + 4},trim=end_frame={hold_frames},"
            f"setpts=PTS-STARTPTS,settb=AVTB,setpts=N/({fps}*TB),fps={fps},setsar=1,format=yuv420p[_hhd]"
        )
        fc.append(
            f"[_hlv][_hhd]concat=n=2:v=1:a=0,settb=AVTB,setpts=N/({fps}*TB),fps={fps}{out_lbl}"
        )

    # ------------------------------------------------------------------
    # Layout 1: SINGLE (Full-frame or Pre-Stitched Baked Split Sub-Panel)
    # ------------------------------------------------------------------
    if lay == "single":
        single_out = "[vbase_live]" if has_live_hold else v_curr
        has_active_zoom = (
            abs(z0 - 1.0) > 0.004
            or abs(z1 - 1.0) > 0.004
            or zoom_style in ("flash_zoom", "bell")
        )
        baked_spec = _resolve_baked_split_spec(seg, src) if has_active_zoom else None
        speed_prefix = _build_speed_video_prefix(spd, fps, play_frames, src_dur)
        speed_prefix_str = ",".join(speed_prefix)
        if baked_spec is not None:
            bz0, bz1, b_style, b_at, b_dur = _clamp_split_zoom_spec(
                z0, z1, zoom_style, zoom_at, zoom_dur, play_exact
            )
            b_kind = str(baked_spec["kind"])
            b_active = str(baked_spec["active"])
            sf = float(baked_spec["seam_frac"])
            b_lo, b_hi = baked_spec["seam_band_frac"]
            b_vis = baked_spec.get("vis") or {}

            explicit_foc = seg.get("focus")
            has_explicit_foc = isinstance(explicit_foc, (list, tuple)) and len(explicit_foc) >= 2
            vis_faces = b_vis.get("faces") or []
            fx_p, fy_p = 0.50, 0.36
            if b_kind == "split_v":
                if has_explicit_foc:
                    fx_p = fx
                    fy_p = (fy / max(0.20, sf)) if (b_active == "top" and fy < sf) else (
                        ((fy - sf) / max(0.20, 1.0 - sf)) if (b_active == "bottom" and fy >= sf) else fy
                    )
                else:
                    for vf_item in vis_faces:
                        ec = vf_item.get("eye_center_norm")
                        if isinstance(ec, (list, tuple)) and len(ec) >= 2:
                            ex_v, ey_v = float(ec[0]), float(ec[1])
                            if b_active == "top" and ey_v < sf:
                                fx_p, fy_p = ex_v, ey_v / max(0.20, sf)
                                break
                            if b_active == "bottom" and ey_v >= sf:
                                fx_p, fy_p = ex_v, (ey_v - sf) / max(0.20, 1.0 - sf)
                                break
            else:
                if has_explicit_foc:
                    fy_p = fy
                    fx_p = (fx / max(0.20, sf)) if (b_active == "left" and fx < sf) else (
                        ((fx - sf) / max(0.20, 1.0 - sf)) if (b_active == "right" and fx >= sf) else fx
                    )
                else:
                    for vf_item in vis_faces:
                        ec = vf_item.get("eye_center_norm")
                        if isinstance(ec, (list, tuple)) and len(ec) >= 2:
                            ex_v, ey_v = float(ec[0]), float(ec[1])
                            if b_active == "left" and ex_v < sf:
                                fx_p, fy_p = ex_v / max(0.20, sf), ey_v
                                break
                            if b_active == "right" and ex_v >= sf:
                                fx_p, fy_p = (ex_v - sf) / max(0.20, 1.0 - sf), ey_v
                                break
            fx_p = round(min(0.75, max(0.25, fx_p)), 4)
            fy_p = round(min(0.55, max(0.22, fy_p)), 4)

            if b_kind == "split_v":
                split_frac = (b_lo if b_active == "top" else b_hi) if (b_hi > b_lo + 0.0005) else sf
                top_h = max(16, min(H - 16, even(H * split_frac)))
                bot_h = H - top_h
                fc.append(
                    f"[0:v]{speed_prefix_str},"
                    f"scale={W}:{H}:force_original_aspect_ratio=increase:flags=lanczos,"
                    f"crop={W}:{H}:x='(iw-{W})*0.5':y='(ih-{H})*0.5',split=2[vb_top_in][vb_bot_in]"
                )
                top_ops = [f"crop={W}:{top_h}:0:0"]
                bot_ops = [f"crop={W}:{bot_h}:0:{top_h}"]
                if b_active == "top":
                    top_ops.extend(
                        _build_subpixel_zoom_filter(
                            slot_w=W, slot_h=top_h, fps=fps, exact_dur=play_exact,
                            fx=fx_p, fy=fy_p, z0=bz0, z1=bz1,
                            zoom_style=b_style, zoom_at=b_at, zoom_dur=b_dur,
                        )
                    )
                else:
                    bot_ops.extend(
                        _build_subpixel_zoom_filter(
                            slot_w=W, slot_h=bot_h, fps=fps, exact_dur=play_exact,
                            fx=fx_p, fy=fy_p, z0=bz0, z1=bz1,
                            zoom_style=b_style, zoom_at=b_at, zoom_dur=b_dur,
                        )
                    )
                fc.append(f"[vb_top_in]{','.join(top_ops)},setsar=1,format=yuv420p[vb_top]")
                fc.append(f"[vb_bot_in]{','.join(bot_ops)},setsar=1,format=yuv420p[vb_bot]")
                post_ops = ["vstack=inputs=2:shortest=1"]
                if color_filter:
                    post_ops.append(color_filter)
                post_ops.extend(["setsar=1", "format=yuv420p"])
                fc.append(f"[vb_top][vb_bot]{','.join(post_ops)}{single_out}")
            else:
                split_frac = (b_lo if b_active == "left" else b_hi) if (b_hi > b_lo + 0.0005) else sf
                left_w = max(16, min(W - 16, even(W * split_frac)))
                right_w = W - left_w
                fc.append(
                    f"[0:v]{speed_prefix_str},"
                    f"scale={W}:{H}:force_original_aspect_ratio=increase:flags=lanczos,"
                    f"crop={W}:{H}:x='(iw-{W})*0.5':y='(ih-{H})*0.5',split=2[vb_left_in][vb_right_in]"
                )
                left_ops = [f"crop={left_w}:{H}:0:0"]
                right_ops = [f"crop={right_w}:{H}:{left_w}:0"]
                if b_active == "left":
                    left_ops.extend(
                        _build_subpixel_zoom_filter(
                            slot_w=left_w, slot_h=H, fps=fps, exact_dur=play_exact,
                            fx=fx_p, fy=fy_p, z0=bz0, z1=bz1,
                            zoom_style=b_style, zoom_at=b_at, zoom_dur=b_dur,
                        )
                    )
                else:
                    right_ops.extend(
                        _build_subpixel_zoom_filter(
                            slot_w=right_w, slot_h=H, fps=fps, exact_dur=play_exact,
                            fx=fx_p, fy=fy_p, z0=bz0, z1=bz1,
                            zoom_style=b_style, zoom_at=b_at, zoom_dur=b_dur,
                        )
                    )
                fc.append(f"[vb_left_in]{','.join(left_ops)},setsar=1,format=yuv420p[vb_left]")
                fc.append(f"[vb_right_in]{','.join(right_ops)},setsar=1,format=yuv420p[vb_right]")
                post_ops = ["hstack=inputs=2:shortest=1"]
                if color_filter:
                    post_ops.append(color_filter)
                post_ops.extend(["setsar=1", "format=yuv420p"])
                fc.append(f"[vb_left][vb_right]{','.join(post_ops)}{single_out}")
            if has_live_hold:
                _attach_live_hold(single_out, v_curr, W, H, hold_focus=[0.5, 0.5], z_hold=1.0)
        else:
            vf = [
                *speed_prefix,
                f"scale={W}:{H}:force_original_aspect_ratio=increase:flags=lanczos",
                f"crop={W}:{H}:x='(iw-{W})*{fx:.4f}':y='(ih-{H})*{fy:.4f}'",
            ]
            vf.extend(
                _build_subpixel_zoom_filter(
                    slot_w=W,
                    slot_h=H,
                    fps=fps,
                    exact_dur=play_exact,
                    fx=fx,
                    fy=fy,
                    z0=z0,
                    z1=z1,
                    zoom_style=zoom_style,
                    zoom_at=zoom_at,
                    zoom_dur=zoom_dur,
                    fx1=fx1,
                    fy1=fy1,
                    zoom_peak=zoom_peak,
                )
            )
            if color_filter:
                vf.append(color_filter)
            vf.extend(["setsar=1", "format=yuv420p"])
            fc.append(f"[0:v]{','.join(vf)}{single_out}")
            if has_live_hold:
                z_hold = z1
                if zoom_style in ("sudden_in", "flash_zoom") and play_exact > zoom_at + zoom_dur + 0.02 and z1 > 1.004:
                    z_hold = z1 + 0.015
                elif zoom_style == "none":
                    z_hold = 1.0
                _attach_live_hold(single_out, v_curr, W, H, z_hold=z_hold)

    # ------------------------------------------------------------------
    # Layout 2 & 3: SPLIT_V (`top_bottom`) & SPLIT_H (`left_right`)
    # ------------------------------------------------------------------
    elif lay in ("split_v", "split_h"):
        peer = seg.get("peer") if isinstance(seg.get("peer"), dict) else {}
        peer_src = resolve(peer.get("src") or seg["src"])
        peer_info = probe(peer_src) if peer_src.is_file() else {"width": src_w, "height": src_h, "duration": 6.0}
        peer_w = int(peer_info.get("width") or src_w)
        peer_h = int(peer_info.get("height") or src_h)
        peer_focus = list(peer.get("focus") or [0.5, 0.38])
        peer_dur = float(peer_info.get("duration") or 6.0)

        peer_idx = next_idx
        next_idx += 1
        peer_in_label = _append_peer_video_input(
            peer, peer_src, peer_dur, dur, exact, fps, inputs, fc, peer_idx, default_still=False
        )

        has_explicit_pan = seg.get("focus_end") is not None and math.hypot(fx1 - fx, fy1 - fy) > 0.04

        if lay == "split_v":
            slot_w = W
            slot_h_top = even(H // 2)
            slot_h_bot = H - slot_h_top
            active_top = _resolve_active_slot(seg, default="top") in ("top", "slot_a", "a", "0")
            live_h = slot_h_top if active_top else slot_h_bot
            peer_h_slot = slot_h_bot if active_top else slot_h_top

            _lock = split_panel_lock(seg, plan, W, H)
            if _lock:
                live_crop, peer_crop = _lock["live"], _lock["peer"]
                live_slot_focus, peer_slot_focus = list(live_crop["slot_focus"]), list(peer_crop["slot_focus"])
            else:
                live_crop, peer_crop, live_slot_focus, peer_slot_focus = _compute_paired_split_crop_filters_for_layout(
                    lay="split_v",
                    seg=seg,
                    plan=plan,
                    live_src=src,
                    peer_src=peer_src,
                    src_w=src_w,
                    src_h=src_h,
                    peer_w=peer_w,
                    peer_h=peer_h,
                    live_slot_w=slot_w,
                    live_slot_h=live_h,
                    peer_slot_w=slot_w,
                    peer_slot_h=peer_h_slot,
                    live_focus=[fx, fy],
                    peer_focus=peer_focus,
                    return_focus=True,
                    live_offset_y=_parse_offset_y(seg.get("offset_y")),
                    peer_offset_y=_parse_offset_y(peer.get("offset_y")),
                )
            live_slot_focus_end = (
                [
                    min(0.80, max(0.20, live_slot_focus[0] + (fx1 - fx))),
                    min(0.65, max(0.20, live_slot_focus[1] + (fy1 - fy))),
                ]
                if has_explicit_pan
                else None
            )
            live_out_lbl = "[live_slot_pre]" if has_live_hold else "[live_slot]"
            fc.append(
                _build_slot_video_chain(
                    "[0:v]", live_out_lbl, src_w, src_h, slot_w, live_h, fps, play_exact,
                    live_slot_focus, z0, z1,
                    zoom_style=zoom_style, zoom_at=zoom_at, zoom_dur=zoom_dur,
                    focus_end=live_slot_focus_end,
                    color_filter=color_filter, prebuilt_crop_filter=live_crop["filter"],
                    zoom_peak=zoom_peak, speed=spd, src_dur=src_dur, play_frames=play_frames,
                )
            )
            if has_live_hold:
                hold_crop_locked = (_lock or {}).get("hold") if _lock else None
                hold_prebuilt = (
                    hold_crop_locked["filter"]
                    if isinstance(hold_crop_locked, dict)
                    else (live_crop["filter"] if hold_src_path == src else None)
                )
                _attach_live_hold(
                    live_out_lbl, "[live_slot]", slot_w, live_h,
                    prebuilt_crop_filter=hold_prebuilt,
                    use_slot_crop=(hold_prebuilt is None),
                    hold_focus=live_slot_focus if hold_prebuilt else None,
                )
            fc.append(
                _build_slot_video_chain(
                    peer_in_label, "[peer_slot]", peer_w, peer_h, slot_w, peer_h_slot, fps, exact,
                    peer_slot_focus, 1.0, 1.0,
                    zoom_style="none",
                    prebuilt_crop_filter=peer_crop["filter"],
                )
            )

            fc.append(f"[peer_slot]tpad=stop_mode=clone:stop=4[peer_pad]")
            stack_order = "[live_slot][peer_pad]" if active_top else "[peer_pad][live_slot]"
            fc.append(
                f"{stack_order}vstack=inputs=2:shortest=1,"
                f"drawbox=x=0:y={slot_h_top - 2}:w={W}:h=4:color=white@0.85:t=fill,"
                f"format=yuv420p{v_curr}"
            )
        else:
            slot_w_left = even(W // 2)
            slot_w_right = W - slot_w_left
            slot_h = H
            active_left = _resolve_active_slot(seg, default="left") in ("left", "slot_a", "a", "0")
            live_w = slot_w_left if active_left else slot_w_right
            peer_w_slot = slot_w_right if active_left else slot_w_left

            exact_fit = str(seg.get("framing") or "").lower() == "exact"
            _lock = split_panel_lock(seg, plan, W, H)
            if _lock:
                live_crop, peer_crop = _lock["live"], _lock["peer"]
                live_slot_focus, peer_slot_focus = list(live_crop["slot_focus"]), list(peer_crop["slot_focus"])
            else:
                live_crop, peer_crop, live_slot_focus, peer_slot_focus = _compute_paired_split_crop_filters_for_layout(
                    lay="split_h",
                    seg=seg,
                    plan=plan,
                    live_src=src,
                    peer_src=peer_src,
                    src_w=src_w,
                    src_h=src_h,
                    peer_w=peer_w,
                    peer_h=peer_h,
                    live_slot_w=live_w,
                    live_slot_h=slot_h,
                    peer_slot_w=peer_w_slot,
                    peer_slot_h=slot_h,
                    live_focus=[fx, fy],
                    peer_focus=peer_focus,
                    return_focus=True,
                    live_offset_y=_parse_offset_y(seg.get("offset_y")),
                    peer_offset_y=_parse_offset_y(peer.get("offset_y")),
                )
            live_slot_focus_end = (
                [
                    min(0.80, max(0.20, live_slot_focus[0] + (fx1 - fx))),
                    min(0.65, max(0.20, live_slot_focus[1] + (fy1 - fy))),
                ]
                if has_explicit_pan
                else None
            )
            live_out_lbl = "[live_slot_pre]" if has_live_hold else "[live_slot]"
            fc.append(
                _build_slot_video_chain(
                    "[0:v]", live_out_lbl, src_w, src_h, live_w, slot_h, fps, play_exact,
                    live_slot_focus, z0, z1,
                    exact=exact_fit,
                    zoom_style=zoom_style, zoom_at=zoom_at, zoom_dur=zoom_dur,
                    focus_end=live_slot_focus_end,
                    color_filter=color_filter, prebuilt_crop_filter=live_crop["filter"],
                    zoom_peak=zoom_peak, speed=spd, src_dur=src_dur, play_frames=play_frames,
                )
            )
            if has_live_hold:
                hold_crop_locked = (_lock or {}).get("hold") if _lock else None
                hold_prebuilt = (
                    hold_crop_locked["filter"]
                    if isinstance(hold_crop_locked, dict)
                    else (live_crop["filter"] if hold_src_path == src else None)
                )
                _attach_live_hold(
                    live_out_lbl, "[live_slot]", live_w, slot_h,
                    prebuilt_crop_filter=hold_prebuilt,
                    use_slot_crop=(hold_prebuilt is None),
                    hold_focus=live_slot_focus if hold_prebuilt else None,
                    exact_fit=exact_fit,
                )
            fc.append(
                _build_slot_video_chain(
                    peer_in_label, "[peer_slot]", peer_w, peer_h, peer_w_slot, slot_h, fps, exact,
                    peer_slot_focus, 1.0, 1.0,
                    exact=exact_fit,
                    zoom_style="none",
                    prebuilt_crop_filter=peer_crop["filter"],
                )
            )

            fc.append(f"[peer_slot]tpad=stop_mode=clone:stop=4[peer_pad]")
            stack_order = "[live_slot][peer_pad]" if active_left else "[peer_pad][live_slot]"
            dv = seg.get("divider")
            if isinstance(dv, dict):
                dw = max(2, round(W * float(dv.get("w_frac", 0.004))))
                dx = round(W * float(dv.get("x_frac", 0.5)))
                divider = f"drawbox=x={dx}:y=0:w={dw}:h={H}:color=0x{dv.get('color', 'FFFFFF')}@{dv.get('alpha', 1.0)}:t=fill,"
            elif isinstance((plan or {}).get("page"), dict):
                divider = ""  # Scrolling-text page format stitches the two panels edge to edge
            else:
                divider = f"drawbox=x={slot_w_left - 2}:y=0:w=4:h={H}:color=white@0.85:t=fill,"
            fc.append(f"{stack_order}hstack=inputs=2:shortest=1,{divider}format=yuv420p{v_curr}")

    # ------------------------------------------------------------------
    # Layout 4: CHAT_BAR / TEXTING (`floating_overlay` or `docked`)
    # ------------------------------------------------------------------
    elif lay == "chat_bar":
        chat_spec = seg.get("chat") or {}
        mode = chat_spec.get("mode", "floating_overlay") if isinstance(chat_spec, dict) else "floating_overlay"
        dock_frac = 0.22
        chat_png = render_chat_overlay_png(chat_spec, width=W, height=H, mode=mode, docked_height_frac=dock_frac)

        chat_idx = next_idx
        next_idx += 1
        inputs.extend(["-loop", "1", "-framerate", str(fps), "-t", f"{exact:.4f}", "-i", chat_png])

        if str(mode).lower() in ("docked", "dock", "top_strip"):
            dock_h = even(H * dock_frac)
            vid_h = H - dock_h
            sp_lbl = "[v_sp_pre]" if has_live_hold else "[v_sp]"
            fc.append(
                _build_slot_video_chain(
                    "[0:v]", sp_lbl, src_w, src_h, W, vid_h, fps, play_exact, [fx, fy], z0, z1,
                    zoom_style=zoom_style, zoom_at=zoom_at, zoom_dur=zoom_dur,
                    focus_end=[fx1, fy1], color_filter=color_filter,
                    zoom_peak=zoom_peak, speed=spd, src_dur=src_dur, play_frames=play_frames,
                )
            )
            if has_live_hold:
                _attach_live_hold(sp_lbl, "[v_sp]", W, vid_h, use_slot_crop=True)
            fc.append(f"[v_sp]pad={W}:{H}:0:{dock_h}:color=#0f172a[v_padded]")
            fc.append(f"[{chat_idx}:v]scale={W}:{H},format=rgba[chat_ov]")
            fc.append(f"[v_padded][chat_ov]overlay=0:0:eof_action=pass,format=yuv420p{v_curr}")
        else:
            sp_lbl = "[v_sp_pre]" if has_live_hold else "[v_sp]"
            fc.append(
                _build_slot_video_chain(
                    "[0:v]", sp_lbl, src_w, src_h, W, H, fps, play_exact, [fx, fy], z0, z1,
                    zoom_style=zoom_style, zoom_at=zoom_at, zoom_dur=zoom_dur,
                    focus_end=[fx1, fy1], color_filter=color_filter,
                    zoom_peak=zoom_peak, speed=spd, src_dur=src_dur, play_frames=play_frames,
                )
            )
            if has_live_hold:
                _attach_live_hold(sp_lbl, "[v_sp]", W, H, use_slot_crop=True)
            fc.append(f"[{chat_idx}:v]scale={W}:{H},format=rgba,fade=t=in:st=0:d=0.15:alpha=1[chat_ov]")
            fc.append(f"[v_sp][chat_ov]overlay=0:0:eof_action=pass,format=yuv420p{v_curr}")

    # ------------------------------------------------------------------
    # Layout 5: VIDEO_CALL / PIP_CALL / GMEET_GRID
    # ------------------------------------------------------------------
    elif lay in ("video_call", "pip_call", "gmeet_grid"):
        peer = seg.get("peer") if isinstance(seg.get("peer"), dict) else {}
        peer_src = resolve(peer.get("src") or seg["src"])
        peer_info = probe(peer_src) if peer_src.is_file() else {"width": src_w, "height": src_h, "duration": 6.0}
        peer_w = int(peer_info.get("width") or src_w)
        peer_h = int(peer_info.get("height") or src_h)
        peer_focus = list(peer.get("focus") or [0.5, 0.38])
        peer_dur = float(peer_info.get("duration") or 6.0)

        peer_idx = next_idx
        next_idx += 1
        peer_in_label = _append_peer_video_input(
            peer, peer_src, peer_dur, dur, exact, fps, inputs, fc, peer_idx, default_still=True
        )

        pip_shape = str(seg.get("pip_shape") or peer.get("shape") or "rounded")
        variant = "gmeet_grid" if lay == "gmeet_grid" else "pip_call"
        geom = get_video_call_geometry(W, H, variant=variant, pip_shape=pip_shape)

        caller_name = _speaker_display_name(str(seg.get("speaker") or "nova"), plan)
        call_sec = int(round(float(seg.get("_timeline_start", 42.0))))
        timer_str = f"{call_sec // 60:02d}:{call_sec % 60:02d}"
        vc_live_vis = analyze_clip_vision(src) if src.is_file() else {}
        vc_peer_vis = analyze_clip_vision(peer_src) if peer_src.is_file() else {}
        vc_live_bb = vc_live_vis.get("bbox_norm") if _vis_has_real_face_safe(vc_live_vis) else None
        vc_peer_bb = vc_peer_vis.get("bbox_norm") if _vis_has_real_face_safe(vc_peer_vis) else None

        if variant == "gmeet_grid":
            t0_geom, t1_geom = geom["tiles"][0], geom["tiles"][1]
            tw, th, trad = t0_geom["w"], t0_geom["h"], t0_geom["radius"]
            mask_png = render_pip_mask_png(tw, th, radius=trad, shape="rounded")
            active_top = _resolve_active_slot(seg, default="top") in ("top", "slot_a", "a", "0")
            chrome_png = render_video_call_chrome_png(
                width=W, height=H, caller=caller_name, timer_text=timer_str,
                active_speaker="top" if active_top else "bottom", layout_variant="gmeet_grid",
            )

            mask_idx = next_idx
            chrome_idx = next_idx + 1
            next_idx += 2
            inputs.extend(["-loop", "1", "-framerate", str(fps), "-t", f"{exact:.4f}", "-i", mask_png])
            inputs.extend(["-loop", "1", "-framerate", str(fps), "-t", f"{exact:.4f}", "-i", chrome_png])

            fc.append(f"color=c=#14161c:s={W}x{H}:r={fps}:d={exact:.4f}[bg]")
            live_lbl = "[v_live_pre]" if has_live_hold else "[v_live]"
            fc.append(
                _build_slot_video_chain(
                    "[0:v]", live_lbl, src_w, src_h, tw, th, fps, play_exact, [fx, fy], z0, z1,
                    bbox_norm=vc_live_bb, max_slot_fill=0.52,
                    zoom_style=zoom_style, zoom_at=zoom_at, zoom_dur=zoom_dur,
                    focus_end=[fx1, fy1], color_filter=color_filter,
                    zoom_peak=zoom_peak, speed=spd, src_dur=src_dur, play_frames=play_frames,
                )
            )
            if has_live_hold:
                _attach_live_hold(live_lbl, "[v_live]", tw, th, use_slot_crop=True)
            fc.append(
                _build_slot_video_chain(
                    peer_in_label, "[v_peer]", peer_w, peer_h, tw, th, fps, exact, peer_focus, 1.0, 1.0,
                    bbox_norm=vc_peer_bb, max_slot_fill=0.52,
                    zoom_style="none",
                )
            )
            fc.append(f"[{mask_idx}:v]scale={tw}:{th},format=gray,split=2[m_a][m_b]")
            fc.append("[v_live]format=yuva420p[vl_a];[vl_a][m_a]alphamerge[tile_live]")
            fc.append("[v_peer]format=yuva420p[vp_a];[vp_a][m_b]alphamerge[tile_peer]")

            top_lbl = "[tile_live]" if active_top else "[tile_peer]"
            bot_lbl = "[tile_peer]" if active_top else "[tile_live]"
            fc.append(f"[bg]{top_lbl}overlay=x={t0_geom['x']}:y={t0_geom['y']}:eof_action=pass[bg1]")
            fc.append(f"[bg1]{bot_lbl}overlay=x={t1_geom['x']}:y={t1_geom['y']}:eof_action=pass[bg2]")
            fc.append(f"[{chrome_idx}:v]scale={W}:{H},format=rgba[chrome]")
            fc.append(f"[bg2][chrome]overlay=0:0:eof_action=pass,format=yuv420p{v_curr}")
        else:
            pip_g = geom["pip"]
            pw, ph, prad = pip_g["w"], pip_g["h"], pip_g["radius"]
            px, py = pip_g["x"], pip_g["y"]

            mask_png = render_pip_mask_png(pw, ph, radius=prad, shape=pip_g["shape"])
            chrome_png = render_video_call_chrome_png(
                width=W, height=H, caller=caller_name, timer_text=timer_str,
                pip_rect=pip_g, pip_shape=pip_g["shape"], active_speaker="main", layout_variant="pip_call",
            )

            mask_idx = next_idx
            chrome_idx = next_idx + 1
            next_idx += 2
            inputs.extend(["-loop", "1", "-framerate", str(fps), "-t", f"{exact:.4f}", "-i", mask_png])
            inputs.extend(["-loop", "1", "-framerate", str(fps), "-t", f"{exact:.4f}", "-i", chrome_png])

            main_lbl = "[v_main_pre]" if has_live_hold else "[v_main]"
            fc.append(
                _build_slot_video_chain(
                    "[0:v]", main_lbl, src_w, src_h, W, H, fps, play_exact, [fx, fy], z0, z1,
                    bbox_norm=vc_live_bb, max_slot_fill=0.58,
                    zoom_style=zoom_style, zoom_at=zoom_at, zoom_dur=zoom_dur,
                    focus_end=[fx1, fy1], color_filter=color_filter,
                    zoom_peak=zoom_peak, speed=spd, src_dur=src_dur, play_frames=play_frames,
                )
            )
            if has_live_hold:
                _attach_live_hold(main_lbl, "[v_main]", W, H, use_slot_crop=True)
            fc.append(
                _build_slot_video_chain(
                    peer_in_label, "[v_pip_raw]", peer_w, peer_h, pw, ph, fps, exact, peer_focus, 1.0, 1.0,
                    bbox_norm=vc_peer_bb, max_slot_fill=0.52,
                    zoom_style="none",
                )
            )
            fc.append(f"[{mask_idx}:v]scale={pw}:{ph},format=gray[pip_mask]")
            fc.append("[v_pip_raw]format=yuva420p[v_pip_a];[v_pip_a][pip_mask]alphamerge[v_pip]")
            fc.append(f"[v_main][v_pip]overlay=x={px}:y={py}:eof_action=pass[v_with_pip]")
            fc.append(f"[{chrome_idx}:v]scale={W}:{H},format=rgba[chrome]")
            fc.append(f"[v_with_pip][chrome]overlay=0:0:eof_action=pass,format=yuv420p{v_curr}")

    # ------------------------------------------------------------------
    # Layout 6: TOP_PIP (Top-Center Floating Rounded Rectangle Window)
    # ------------------------------------------------------------------
    elif lay == "top_pip":
        peer = seg.get("peer") if isinstance(seg.get("peer"), dict) else {}
        peer_src = resolve(peer.get("src") or seg["src"])
        peer_info = probe(peer_src) if peer_src.is_file() else {"width": src_w, "height": src_h, "duration": 6.0}
        peer_w = int(peer_info.get("width") or src_w)
        peer_h = int(peer_info.get("height") or src_h)
        peer_focus = list(peer.get("focus") or [0.5, 0.38])
        peer_dur = float(peer_info.get("duration") or 6.0)

        peer_idx = next_idx
        next_idx += 1
        peer_in_label = _append_peer_video_input(
            peer, peer_src, peer_dur, dur, exact, fps, inputs, fc, peer_idx, default_still=False
        )

        tpip_geo = get_layout_geometry("top_pip", W, H, seg=seg, plan=plan)
        pip_box = tpip_geo["pip"]
        pw, ph, px, py, prad = pip_box["w"], pip_box["h"], pip_box["x"], pip_box["y"], pip_box["radius"]

        mask_png = render_pip_mask_png(pw, ph, radius=prad, shape="rounded")
        mask_idx = next_idx
        next_idx += 1
        inputs.extend(["-loop", "1", "-framerate", str(fps), "-t", f"{exact:.4f}", "-i", mask_png])

        active_top = _resolve_active_slot(seg, default="top") in ("top", "slot_a", "a", "0", "pip")
        if active_top:
            pip_vis = analyze_clip_vision(src) if src.is_file() else {}
            bg_vis = analyze_clip_vision(peer_src) if peer_src.is_file() else {}
            pip_bb = pip_vis.get("bbox_norm") if _vis_has_real_face_safe(pip_vis) else None
            bg_bb = bg_vis.get("bbox_norm") if _vis_has_real_face_safe(bg_vis) else None
            fc.append(
                _build_slot_video_chain(
                    peer_in_label, "[v_bg]", peer_w, peer_h, W, H, fps, exact, peer_focus, 1.0, 1.0,
                    zoom_style="none",
                    bbox_norm=bg_bb, headroom_pct=25.8, max_slot_fill=0.58,
                )
            )
            pip_lbl = "[v_pip_pre]" if has_live_hold else "[v_pip_raw]"
            fc.append(
                _build_slot_video_chain(
                    "[0:v]", pip_lbl, src_w, src_h, pw, ph, fps, play_exact, [fx, fy], z0, z1,
                    zoom_style=zoom_style, zoom_at=zoom_at, zoom_dur=zoom_dur,
                    focus_end=[fx1, fy1],
                    bbox_norm=pip_bb, color_filter=color_filter, max_slot_fill=0.52,
                    zoom_peak=zoom_peak, speed=spd, src_dur=src_dur, play_frames=play_frames,
                )
            )
            if has_live_hold:
                _attach_live_hold(pip_lbl, "[v_pip_raw]", pw, ph, use_slot_crop=True)
        else:
            bg_vis = analyze_clip_vision(src) if src.is_file() else {}
            pip_vis = analyze_clip_vision(peer_src) if peer_src.is_file() else {}
            bg_bb = bg_vis.get("bbox_norm") if _vis_has_real_face_safe(bg_vis) else None
            pip_bb = pip_vis.get("bbox_norm") if _vis_has_real_face_safe(pip_vis) else None
            bg_lbl = "[v_bg_pre]" if has_live_hold else "[v_bg]"
            fc.append(
                _build_slot_video_chain(
                    "[0:v]", bg_lbl, src_w, src_h, W, H, fps, play_exact, [fx, fy], z0, z1,
                    zoom_style=zoom_style, zoom_at=zoom_at, zoom_dur=zoom_dur,
                    focus_end=[fx1, fy1],
                    bbox_norm=bg_bb, color_filter=color_filter, headroom_pct=25.8, max_slot_fill=0.58,
                    zoom_peak=zoom_peak, speed=spd, src_dur=src_dur, play_frames=play_frames,
                )
            )
            if has_live_hold:
                _attach_live_hold(bg_lbl, "[v_bg]", W, H, use_slot_crop=True, headroom_pct=25.8)
            fc.append(
                _build_slot_video_chain(
                    peer_in_label, "[v_pip_raw]", peer_w, peer_h, pw, ph, fps, exact, peer_focus, 1.0, 1.0,
                    zoom_style="none",
                    bbox_norm=pip_bb, max_slot_fill=0.52,
                )
            )

        fc.append(f"[{mask_idx}:v]scale={pw}:{ph},format=gray[pip_mask]")
        fc.append("[v_pip_raw]format=yuva420p[v_pip_a];[v_pip_a][pip_mask]alphamerge[v_pip]")
        fc.append(f"[v_bg][v_pip]overlay=x={px}:y={py}:eof_action=pass,format=yuv420p{v_curr}")

    # Freeze trailing speech frame across `hold_tail` ONLY when no live `hold_src` is present;
    # otherwise `v_curr` already carries live concatenated motion across `frames` before banner overlays.
    parsed_banners = parse_banners(seg, exact)
    freeze_pad_frames = max(3, int(round(fps * (hold_tail + 1.25))))
    pre_bn_label = "[vprebn]" if parsed_banners else "[vout]"
    if hold_tail > 0.001 and not has_live_hold:
        fc.append(
            f"{v_curr}trim=end_frame={play_frames},setpts=PTS-STARTPTS,"
            f"tpad=stop_mode=clone:stop={freeze_pad_frames},"
            f"settb=AVTB,setpts=N/({fps}*TB),fps={fps}{pre_bn_label}"
        )
    else:
        safety_pad = 3 if has_live_hold else freeze_pad_frames
        fc.append(
            f"{v_curr}tpad=stop_mode=clone:stop={safety_pad},"
            f"settb=AVTB,setpts=N/({fps}*TB),fps={fps}{pre_bn_label}"
        )
    v_curr = pre_bn_label

    # ------------------------------------------------------------------
    # Optional Stacked "Stop Saying" Hook Banners on Any Segment
    # ------------------------------------------------------------------
    if parsed_banners:
        stack_top, phrase_base = banner_stack_rows(seg, parsed_banners)
        phrase_idx = 0
        for b_idx, (b_text, b_at, b_dur, is_header) in enumerate(parsed_banners):
            b_end = min(exact, b_at + b_dur)
            bx_expr = f"trunc(({W}-overlay_w)/2)"
            if is_header:
                in_h = next_idx
                next_idx += 1
                hdr_png = render_stop_saying_banner_png(b_text, with_strike=False, cross_icon=True)
                inputs.extend(["-loop", "1", "-framerate", str(fps), "-t", f"{exact:.4f}", "-i", hdr_png])
                hy = int(round(H * stack_top))
                fc.append(
                    f"[{in_h}:v]scale=w='trunc(iw*{W}/1080/2)*2':h=-1,format=rgba,"
                    f"fade=t=in:st={b_at:.3f}:d=0.10:alpha=1,"
                    f"fade=t=out:st={max(b_at + 0.1, b_end - 0.18):.3f}:d=0.18:alpha=1[bn_h{b_idx}]"
                )
                fc.append(
                    f"{v_curr}[bn_h{b_idx}]overlay=x={bx_expr}:y={hy}:"
                    f"enable='between(t,{b_at:.3f},{b_end:.3f})':eof_action=pass,format=yuv420p[vb{b_idx}h]"
                )
                v_curr = f"[vb{b_idx}h]"
                continue

            if b_dur > 0.65:
                strike_at = max(b_at + 0.08, min(b_end - 0.25, b_at + b_dur * 0.68))
            else:
                strike_at = min(max(b_at + 0.04, b_end - 0.12), b_at + 0.08)
            png_plain = render_stop_saying_banner_png(b_text, with_strike=False)
            png_struck = render_stop_saying_banner_png(b_text, with_strike=True, strike_px=7)
            in_plain = next_idx
            in_struck = next_idx + 1
            next_idx += 2
            inputs.extend(["-loop", "1", "-framerate", str(fps), "-t", f"{exact:.4f}", "-i", png_plain])
            inputs.extend(["-loop", "1", "-framerate", str(fps), "-t", f"{exact:.4f}", "-i", png_struck])

            by_expr = int(round(H * (phrase_base + phrase_idx * 0.068)))
            phrase_idx += 1
            fade_out_st = max(strike_at + 0.08, b_end - 0.08)

            v_mid = f"[vb{b_idx}a]"
            v_next = f"[vb{b_idx}b]"
            fc.append(
                f"[{in_plain}:v]scale=w='trunc(iw*{W}/1080/2)*2':h=-1,format=rgba,"
                f"fade=t=in:st={b_at:.3f}:d=0.04:alpha=1[bn_p{b_idx}]"
            )
            fc.append(
                f"[{in_struck}:v]scale=w='trunc(iw*{W}/1080/2)*2':h=-1,format=rgba,"
                f"fade=t=out:st={fade_out_st:.3f}:d=0.08:alpha=1[bn_s{b_idx}]"
            )
            fc.append(
                f"{v_curr}[bn_p{b_idx}]overlay=x={bx_expr}:y={by_expr}:"
                f"enable='between(t,{b_at:.3f},{strike_at:.3f})':eof_action=pass{v_mid}"
            )
            fc.append(
                f"{v_mid}[bn_s{b_idx}]overlay=x={bx_expr}:y={by_expr}:"
                f"enable='between(t,{strike_at:.3f},{b_end:.3f})':eof_action=pass,format=yuv420p{v_next}"
            )
            v_curr = v_next

        fc.append(f"{v_curr}null[vout]")

    # Audio chain from active segment (or clean digital silence when muted or source clip lacks audio)
    if use_silence:
        fc.append(f"anullsrc=r=48000:cl=stereo,atrim=0:{exact:.6f},asetpts=PTS-STARTPTS[aout]")
    else:
        fc.append(f"[{audio_input_idx}:a]{af_chain}[aout]")

    args: List[Any] = [
        *inputs,
        "-filter_complex", ";".join(fc),
        "-map", "[vout]",
        "-map", "[aout]",
        "-r", str(fps),
        "-fps_mode", "cfr",
        "-frames:v", str(frames),
        "-c:v", "libx264",
        "-preset", "ultrafast" if draft else "superfast",
        "-crf", "12",
        "-pix_fmt", "yuv420p",
        "-c:a", "pcm_s16le",
        out,
    ]
    return args, exact, frames


_VT_AVAILABLE: Optional[bool] = None


def _has_videotoolbox() -> bool:
    """Return True if macOS `h264_videotoolbox` hardware encoding is available."""
    global _VT_AVAILABLE
    if _VT_AVAILABLE is not None:
        return _VT_AVAILABLE
    if sys.platform != "darwin":
        _VT_AVAILABLE = False
        return False
    try:
        res = subprocess.run([str(FFMPEG), "-hide_banner", "-encoders"], capture_output=True, text=True, timeout=3)
        _VT_AVAILABLE = "h264_videotoolbox" in (res.stdout or "")
    except Exception:
        _VT_AVAILABLE = False
    return _VT_AVAILABLE


def render_segment_layout(
    seg: Dict[str, Any],
    plan: Optional[Dict[str, Any]],
    W: int,
    H: int,
    fps: int,
    out: Union[str, Path],
    draft: bool = False,
) -> float:
    """Render `seg` with its configured layout into normalized intermediate `.mkv` `out`
    using CPU H.264 decode + GPU hardware encode (VideoToolbox / NVENC / QSV / AMF) with
    `libx264` fallback.
    """
    args, exact, _ = build_segment_ffmpeg_args(seg, plan, W, H, fps, out, draft)
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    use_hw = bool((plan or {}).get("use_hw_accel", True))
    use_hw_dec = use_hw and (hardware_encoder() == "h264_nvenc")
    ffmpeg_encode(*args, draft=draft, hardware=use_hw, hw_decode=use_hw_dec, allow_bframes=False)
    return exact


def place_mark_for_segment(
    mark: Union[Dict[str, Any], str, Sequence[float]],
    seg: Optional[Dict[str, Any]],
    W: int,
    H: int,
    size: int,
) -> Tuple[str, str]:
    """Compute layout-aware `(x, y)` FFmpeg overlay expressions for right/wrong marks so
    badges never collide with split-screen listener slots, top chat bars, or PiP windows.
    """
    pos = mark.get("position", "top-right") if isinstance(mark, dict) else (mark or "top-right")
    if isinstance(pos, (list, tuple)) and len(pos) >= 2:
        return f"{float(pos[0])}*{W}-{size}/2", f"{float(pos[1])}*{H}-{size}/2"

    pos_str = str(pos or "top-right").strip().lower()
    m = int(min(W, H) * 0.06)

    lay = normalize_layout_name(seg.get("layout") if isinstance(seg, dict) else "single")
    slot = _resolve_active_slot(seg, default="top" if lay != "single" else "") if isinstance(seg, dict) else "top"

    x0, y0, bw, bh = 0, 0, W, H
    if lay in ("split_v", "gmeet_grid") or (lay == "single" and slot in ("top", "bottom")):
        half_h = H // 2
        if slot in ("bottom", "slot_b", "b", "1"):
            y0, bh = half_h, H - half_h
        else:
            y0, bh = 0, half_h
    elif lay == "split_h" or (lay == "single" and slot in ("left", "right")):
        half_w = W // 2
        if slot in ("right", "slot_b", "b", "1"):
            x0, bw = half_w, W - half_w
        else:
            x0, bw = 0, half_w
    elif lay == "top_pip":
        tpip = get_layout_geometry("top_pip", W, H, seg=seg)["pip"]
        if slot in ("bottom", "slot_b", "b", "1", "bg", "main"):
            top_clear = tpip["y"] + tpip["h"] + max(8, m // 2)
            y0, bh = top_clear, max(size + 2 * m, H - top_clear)
        else:
            x0, y0, bw, bh = tpip["x"], tpip["y"], tpip["w"], tpip["h"]
    elif lay == "chat_bar":
        chat_top_clear = int(round(H * 0.25))
        y0, bh = chat_top_clear, H - chat_top_clear
    elif lay in ("video_call", "pip_call"):
        y0 = int(round(H * 0.09))
        bh = int(round(H * 0.68))
        if pos_str == "top-right":
            pos_str = "top-left"

    coords = {
        "center": (f"{x0}+({bw}-{size})/2", f"{y0}+({bh}-{size})/2"),
        "top-center": (f"{x0}+({bw}-{size})/2", str(y0 + m)),
        "top-right": (str(x0 + bw - size - m), str(y0 + m)),
        "top-left": (str(x0 + m), str(y0 + m)),
        "bottom-right": (str(x0 + bw - size - m), str(y0 + bh - size - m)),
        "bottom-left": (str(x0 + m), str(y0 + bh - size - m)),
    }
    return coords.get(pos_str, coords["top-right"])


def collect_layout_auto_sfx(
    plan: Dict[str, Any],
    seg_start: Union[Dict[str, float], Sequence[float]],
) -> List[Tuple[str, float, float]]:
    """Auto-inject layout sound effects when no explicit SFX or mark sits nearby:
      - `pop.wav` (`-2.5 dB`) when chat bubbles appear in `chat_bar` segments
      - `click.wav` (`-3.5 dB`) when video-call turns switch in `video_call` / `pip_call` / `gmeet_grid`
    Returns `[(sfx_filename, output_time_sec, gain_db), ...]`.
    """
    if plan.get("auto_sfx") is False or (plan.get("canvas") or {}).get("auto_sfx") is False:
        return []

    sfx_pack = (
        plan.get("sfx_pack")
        or (plan.get("canvas") or {}).get("sfx_pack")
        or (plan.get("audio") or {}).get("sfx_pack")
        or "default"
    )
    pop_file = resolve_sfx_asset("pop", pack=sfx_pack).name
    click_file = resolve_sfx_asset("click", pack=sfx_pack).name

    segs: List[Dict[str, Any]] = plan.get("segments") or []
    start_by_id: Dict[str, float] = {}
    start_by_idx: Dict[int, float] = {}
    if isinstance(seg_start, dict):
        for k, v in seg_start.items():
            start_by_id[str(k)] = float(v)
        for idx, s_item in enumerate(segs):
            sid_item = str(s_item.get("id") or f"S{idx + 1}")
            if sid_item in start_by_id:
                start_by_idx[idx] = start_by_id[sid_item]
            elif str(idx) in start_by_id:
                start_by_idx[idx] = start_by_id[str(idx)]
                start_by_id[sid_item] = start_by_idx[idx]
    else:
        for idx, v in enumerate(seg_start):
            t_v = float(v)
            start_by_idx[idx] = t_v
            if idx < len(segs):
                sid_item = str(segs[idx].get("id") or f"S{idx + 1}")
                start_by_id[sid_item] = t_v

    existing_times: List[float] = []
    for e in plan.get("sfx") or []:
        sid_e = str(e.get("segment") or "")
        if sid_e in start_by_id:
            existing_times.append(float(start_by_id[sid_e]) + float(e.get("at", 0.0)))
    for m in plan.get("marks") or []:
        if m.get("sfx", "default") is None or str(m.get("sfx", "default")).lower() == "none":
            continue
        sid_m = str(m.get("segment") or "")
        if sid_m in start_by_id:
            existing_times.append(float(start_by_id[sid_m]) + float(m.get("at", 0.0)))

    auto_sfx: List[Tuple[str, float, float]] = []

    prev_lay = None
    prev_speaker = None

    for idx, s in enumerate(segs):
        sid = str(s.get("id") or f"S{idx + 1}")
        t_base_opt = start_by_idx.get(idx, start_by_id.get(sid))
        if t_base_opt is None or s.get("auto_sfx") is False:
            continue
        t_base = float(t_base_opt)
        lay = normalize_layout_name(s.get("layout"))
        sp = str(s.get("speaker") or "")

        if lay == "chat_bar":
            chat_spec = s.get("chat") or {}
            msgs = (
                chat_spec.get("messages", [])
                if isinstance(chat_spec, dict)
                else (chat_spec if isinstance(chat_spec, list) else [])
            )
            if msgs:
                active_msgs = [m for m in msgs if isinstance(m, dict) and float(m.get("at", 0.0)) > 0.0] or msgs[-1:]
                for m in active_msgs:
                    at_rel = float(m.get("at", 0.08)) if isinstance(m, dict) else 0.08
                    t_pop = round(t_base + max(0.0, at_rel), 4)
                    if not any(abs(t_pop - et) < 0.12 for et in existing_times):
                        auto_sfx.append((pop_file, t_pop, -2.5))
                        existing_times.append(t_pop)
            else:
                t_pop = round(t_base + 0.08, 4)
                if not any(abs(t_pop - et) < 0.12 for et in existing_times):
                    auto_sfx.append((pop_file, t_pop, -2.5))
                    existing_times.append(t_pop)

        elif lay in ("video_call", "pip_call", "gmeet_grid"):
            if prev_lay not in ("video_call", "pip_call", "gmeet_grid") or (sp and prev_speaker and sp != prev_speaker):
                t_click = round(max(0.0, t_base), 4)
                if not any(abs(t_click - et) < 0.12 for et in existing_times):
                    auto_sfx.append((click_file, t_click, -3.5))
                    existing_times.append(t_click)

        prev_lay = lay
        prev_speaker = sp

    return auto_sfx
