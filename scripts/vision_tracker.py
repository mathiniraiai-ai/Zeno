"""Computer Vision Face/Headroom Tracker, MAR Idle-Window Detector & Layout Recommender.

Uses OpenCV (`cv2` + YuNet 5-landmark ONNX) and `numpy` when available, with a graceful
fallback when `cv2` is unavailable so every pipeline stage works in any Python environment.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple, Union

from common import (
    MODELS_DIR,
    ROOT,
    SCRIPTCUT_ROOT,
    WORK,
    clip_work_dir,
    YUNET_MODEL,
    probe,
    read_json,
    resolve,
    write_json,
)

try:
    import numpy as np
except ImportError:  # pragma: no cover
    np = None  # type: ignore[assignment]

try:
    import cv2
except ImportError:  # pragma: no cover
    cv2 = None  # type: ignore[assignment]

TARGET_HEADROOM_PCT: float = 14.0
TARGET_EYELINE_PCT: float = 38.0
MIN_SLOT_FILL: float = 0.55
MAX_EXTRA_ZOOM: float = 1.35

_YUNET_LOCK = threading.Lock()
_YUNET_DETECTOR: Any = None
_VISION_CACHE: Dict[str, Dict[str, Any]] = {}
_IDLE_CACHE: Dict[str, Dict[str, Any]] = {}


def _even(v: float) -> int:
    n = int(round(v))
    return n if n % 2 == 0 else n + 1


def resolve_yunet_model() -> Optional[Path]:
    """Locate `face_detection_yunet_2023mar.onnx` in workspace or sibling ScriptCut-AI."""
    candidates = [
        YUNET_MODEL,
        MODELS_DIR / "face_detection_yunet_2023mar.onnx",
        SCRIPTCUT_ROOT / "models" / "face_detection_yunet_2023mar.onnx",
    ]
    for cand in candidates:
        if cand and cand.is_file():
            return cand
    return None


def get_yunet_detector() -> Any:
    """Return a cached OpenCV FaceDetectorYN instance, or None if unavailable."""
    global _YUNET_DETECTOR
    if _YUNET_DETECTOR is not None:
        return _YUNET_DETECTOR
    if cv2 is None or not hasattr(cv2, "FaceDetectorYN"):
        return None
    model_path = resolve_yunet_model()
    if model_path is None:
        return None
    try:
        _YUNET_DETECTOR = cv2.FaceDetectorYN.create(
            str(model_path), "", (320, 320), 0.55, 0.3, 5000
        )
    except Exception:
        _YUNET_DETECTOR = None
    return _YUNET_DETECTOR


def compute_eye_anchored_focus(
    bbox_norm: Sequence[float],
    eye_center_norm: Optional[Sequence[float]] = None,
    target_headroom: float = TARGET_HEADROOM_PCT / 100.0,
    target_eyeline: float = TARGET_EYELINE_PCT / 100.0,
) -> List[float]:
    """Compute normalized `[fx, fy]` focus point anchored to the eyes (`38%` eyeline)
    and hair crown (`14%` headroom) so zoom/split crops never decapitate the speaker.
    """
    if not bbox_norm or len(bbox_norm) < 4:
        return [0.5, 0.38]

    x0, y0, x1, y1 = [float(v) for v in bbox_norm[:4]]
    cx = 0.5 * (x0 + x1)
    head_h = max(0.08, y1 - y0)

    if eye_center_norm and len(eye_center_norm) >= 2:
        ex, ey = float(eye_center_norm[0]), float(eye_center_norm[1])
    else:
        ex = cx
        ey = y0 + 0.42 * head_h

    # Blend horizontal center toward eye midpoint, clamped to safe crop range
    fx = max(0.08, min(0.92, 0.7 * ex + 0.3 * cx))

    # Anchor vertical focus so eyes sit at target_eyeline (0.38) while keeping
    # top of head (y0) at or below target_headroom (0.14) from top of frame.
    desired_top = min(y0 - target_headroom * 0.65, ey - target_eyeline * 0.72)
    fy = max(0.12, min(0.78, ey - (target_eyeline - 0.32) + 0.15 * (desired_top - y0)))
    # Ensure head crown is protected when zooming in
    max_safe_fy = max(0.14, min(0.75, y0 + head_h * 0.36))
    fy = min(fy, max_safe_fy)
    return [round(fx, 4), round(fy, 4)]


def _measure_mouth_mar(
    arr_rgb: "np.ndarray",
    mouth_roi_norm: Sequence[float],
    prev_mouth_gray: Optional["np.ndarray"] = None,
) -> Tuple[float, float, Optional["np.ndarray"]]:
    """Compute Mouth Aspect Ratio (`MAR`) and frame-to-frame mouth pixel delta."""
    if np is None or arr_rgb is None or arr_rgb.size == 0:
        return 0.04, 0.004, None

    h, w = arr_rgb.shape[:2]
    mx0, my0, mx1, my1 = [float(v) for v in mouth_roi_norm[:4]]
    x0_px = max(0, min(w - 4, int(round(mx0 * w))))
    y0_px = max(0, min(h - 4, int(round(my0 * h))))
    x1_px = max(x0_px + 4, min(w, int(round(mx1 * w))))
    y1_px = max(y0_px + 4, min(h, int(round(my1 * h))))

    patch = arr_rgb[y0_px:y1_px, x0_px:x1_px]
    if patch.size == 0:
        return 0.04, 0.004, None

    if cv2 is not None:
        gray = cv2.cvtColor(patch, cv2.COLOR_RGB2GRAY)
        gray = cv2.resize(gray, (48, 28), interpolation=cv2.INTER_AREA)
        sobel_y = cv2.Sobel(gray, cv2.CV_32F, 0, 1, ksize=3)
        grad_ratio = float(np.mean(np.abs(sobel_y)) / 255.0)
    else:
        luma_f = 0.299 * patch[:, :, 0] + 0.587 * patch[:, :, 1] + 0.114 * patch[:, :, 2]
        gray = luma_f.astype(np.uint8)
        grad_ratio = float(np.mean(np.abs(np.diff(luma_f, axis=0))) / 42.0) if luma_f.shape[0] > 1 else 0.0

    p80 = float(np.percentile(gray, 80)) if gray.size > 0 else 120.0
    p25 = float(np.percentile(gray, 25)) if gray.size > 0 else 60.0
    dark_thresh = max(34.0, min(p80 * 0.52, p25 * 0.88))
    dark_cavity = float(np.mean(gray <= dark_thresh)) if gray.size > 0 else 0.0
    mar_score = float(np.clip(dark_cavity * 0.90 + grad_ratio * 0.45, 0.02, 0.58))

    if prev_mouth_gray is not None and prev_mouth_gray.shape == gray.shape:
        if cv2 is not None:
            diff = cv2.absdiff(gray, prev_mouth_gray)
        else:
            diff = np.abs(gray.astype(np.int16) - prev_mouth_gray.astype(np.int16))
        delta = float(np.mean(diff) / 255.0)
    else:
        delta = 0.004

    return round(mar_score, 4), round(delta, 4), gray


def detect_faces_in_frame(
    frame: "np.ndarray",
    is_bgr: bool = True,
    min_fw_ratio: float = 0.065,
) -> List[Dict[str, Any]]:
    """Detect all foreground faces in `frame` using YuNet 5-landmark ONNX.

    Returns faces sorted Left-to-Right (or Top-to-Bottom if stacked vertically) with:
      - `bbox_norm`: `[x0, y0, x1, y1]` (hair crown to chin)
      - `eye_center_norm`: `[ex, ey]`
      - `mouth_center_norm`: `[mx, my]`
      - `mouth_roi_norm`: `[mx0, my0, mx1, my1]`
      - `headroom_pct`: `y0 * 100.0`
      - `eyeline_pct`: `ey * 100.0`
      - `focus`: `[fx, fy]` (eye-anchored for 14% headroom / 38% eyeline)
      - `mar_score`: float
      - `confidence`: float
    """
    if np is None or cv2 is None or frame is None or frame.size == 0:
        return []
    h, w = frame.shape[:2]
    if h < 16 or w < 16:
        return []

    detector = get_yunet_detector()
    if detector is None:
        return []

    arr_bgr = np.ascontiguousarray(frame if is_bgr else frame[:, :, ::-1])
    arr_rgb = np.ascontiguousarray(frame[:, :, ::-1] if is_bgr else frame)

    with _YUNET_LOCK:
        try:
            detector.setInputSize((w, h))
            _, raw_faces = detector.detect(arr_bgr)
        except Exception:
            raw_faces = None

    if raw_faces is None or len(raw_faces) == 0:
        return []

    fg_raw = []
    for f in raw_faces:
        fw_norm = float(f[2]) / float(w)
        fh_norm = float(f[3]) / float(h)
        conf = float(f[14])
        if fw_norm >= min_fw_ratio and fh_norm >= 0.055 and conf >= 0.62:
            fg_raw.append(f)

    if not fg_raw:
        best_f = max(raw_faces, key=lambda item: float(item[14]))
        if float(best_f[14]) >= 0.55 and (float(best_f[2]) / float(w)) >= 0.045:
            fg_raw = [best_f]

    if not fg_raw:
        return []

    # Sort left-to-right by center X (or top-to-bottom if horizontally aligned)
    fg_raw.sort(key=lambda item: (float(item[0]) + 0.5 * float(item[2])) / float(w))

    faces: List[Dict[str, Any]] = []
    for idx, f in enumerate(fg_raw):
        fx = float(f[0]) / float(w)
        fy = float(f[1]) / float(h)
        fw = float(f[2]) / float(w)
        fh = float(f[3]) / float(h)
        re_x, re_y = float(f[4]) / float(w), float(f[5]) / float(h)
        le_x, le_y = float(f[6]) / float(w), float(f[7]) / float(h)
        nt_x, nt_y = float(f[8]) / float(w), float(f[9]) / float(h)
        rm_x, rm_y = float(f[10]) / float(w), float(f[11]) / float(h)
        lm_x, lm_y = float(f[12]) / float(w), float(f[13]) / float(h)
        conf = round(float(f[14]), 3)

        eye_cx = float(np.clip(0.5 * (re_x + le_x), 0.02, 0.98))
        eye_cy = float(np.clip(0.5 * (re_y + le_y), 0.02, 0.98))

        mouth_cx = float(np.clip(0.5 * (rm_x + lm_x), 0.02, 0.98))
        mouth_cy = float(np.clip(0.5 * (rm_y + lm_y), 0.02, 0.98))
        mouth_span_x = abs(lm_x - rm_x)
        mw = float(np.clip(max(mouth_span_x * 1.45, fw * 0.46, 0.065), 0.055, 0.28))
        mh = float(np.clip(max(fh * 0.23, 0.036), 0.032, 0.14))
        mx0 = float(np.clip(mouth_cx - 0.5 * mw, 0.01, 0.99 - mw))
        my0 = float(np.clip(mouth_cy - 0.48 * mh, 0.01, 0.99 - mh))
        mx1 = float(np.clip(mx0 + mw, mx0 + 0.02, 0.99))
        my1 = float(np.clip(my0 + mh, my0 + 0.02, 0.99))

        # Expand top by 18% of face height to cover hair crown
        hx0 = float(np.clip(fx - 0.08 * fw, 0.01, 0.95))
        hy0 = float(np.clip(fy - 0.18 * fh, 0.01, 0.92))
        hx1 = float(np.clip(fx + fw * 1.08, hx0 + 0.06, 0.99))
        hy1 = float(np.clip(fy + fh * 1.04, hy0 + 0.08, 0.99))

        bbox_norm = [round(hx0, 4), round(hy0, 4), round(hx1, 4), round(hy1, 4)]
        eye_center = [round(eye_cx, 4), round(eye_cy, 4)]
        mouth_roi = [round(mx0, 4), round(my0, 4), round(mx1, 4), round(my1, 4)]

        mar_score, _, _ = _measure_mouth_mar(arr_rgb, mouth_roi, None)
        focus = compute_eye_anchored_focus(bbox_norm, eye_center)

        cx = 0.5 * (hx0 + hx1)
        cy = 0.5 * (hy0 + hy1)
        pos = "left" if cx < 0.38 else ("right" if cx > 0.62 else "center")
        vert = "top" if cy < 0.38 else ("bottom" if cy > 0.62 else "middle")

        faces.append({
            "id": f"face_{idx + 1}",
            "confidence": conf,
            "bbox_norm": bbox_norm,
            "eye_center_norm": eye_center,
            "nose_tip_norm": [round(nt_x, 4), round(nt_y, 4)],
            "mouth_center_norm": [round(mouth_cx, 4), round(mouth_cy, 4)],
            "mouth_roi_norm": mouth_roi,
            "headroom_pct": round(hy0 * 100.0, 2),
            "eyeline_pct": round(eye_cy * 100.0, 2),
            "focus": focus,
            "mar_score": mar_score,
            "is_speaking": bool(mar_score >= 0.11),
            "position": pos,
            "vertical": vert,
        })

    return faces


def detect_baked_split_seam(frame: "np.ndarray") -> Optional[str]:
    """Check if `frame` already has a baked-in horizontal (`split_v_baked`) or
    vertical (`split_h_baked`) split-screen seam along its center line.
    """
    if np is None or frame is None or frame.size == 0:
        return None
    h, w = frame.shape[:2]
    if h < 64 or w < 64:
        return None

    gray = frame.mean(axis=2).astype(np.float32) if frame.ndim == 3 else frame.astype(np.float32)

    # Check horizontal seam near y = h // 2
    mid_y = h // 2
    row_diffs = np.mean(np.abs(np.diff(gray[max(0, mid_y - 6): min(h, mid_y + 6), :], axis=0)), axis=1)
    bg_row_diff = float(np.median(np.mean(np.abs(np.diff(gray, axis=0)), axis=1)) + 1e-4)
    if row_diffs.size > 0 and float(np.max(row_diffs)) > max(28.0, bg_row_diff * 5.2):
        return "split_v_baked"

    # Check vertical seam near x = w // 2
    mid_x = w // 2
    col_diffs = np.mean(np.abs(np.diff(gray[:, max(0, mid_x - 6): min(w, mid_x + 6)], axis=1)), axis=0)
    bg_col_diff = float(np.median(np.mean(np.abs(np.diff(gray, axis=1)), axis=0)) + 1e-4)
    if col_diffs.size > 0 and float(np.max(col_diffs)) > max(28.0, bg_col_diff * 5.2):
        return "split_h_baked"

    return None


def compute_slot_crop_filter(
    src_w: int,
    src_h: int,
    slot_w: int,
    slot_h: int,
    bbox_norm: Optional[Sequence[float]] = None,
    focus: Optional[Sequence[float]] = None,
    headroom_pct: float = TARGET_HEADROOM_PCT,
    extra_zoom: float = 1.0,
) -> Dict[str, Any]:
    """Compute an exact FFmpeg `scale=sw:sh:flags=lanczos,crop=slot_w:slot_h:cx:cy` filter
    that centers the speaker horizontally and anchors their head crown at `headroom_pct`
    (`14%`) and eye line at `38%` inside any target `(slot_w, slot_h)`.
    """
    src_w = max(16, int(src_w or slot_w))
    src_h = max(16, int(src_h or slot_h))
    slot_w = _even(max(16, slot_w))
    slot_h = _even(max(16, slot_h))

    if bbox_norm and len(bbox_norm) >= 4:
        x0, y0, x1, y1 = [min(1.0, max(0.0, float(v))) for v in bbox_norm[:4]]
        if x1 <= x0 or y1 <= y0:
            x0, y0, x1, y1 = 0.25, 0.12, 0.75, 0.88
    elif focus and len(focus) >= 2:
        fx, fy = float(focus[0]), float(focus[1])
        x0 = max(0.02, fx - 0.22)
        x1 = min(0.98, fx + 0.22)
        y0 = max(0.02, fy - 0.22)
        y1 = min(0.98, fy + 0.50)
    else:
        x0, y0, x1, y1 = 0.25, 0.14, 0.75, 0.88

    s0 = max(slot_w / float(src_w), slot_h / float(src_h))
    fill_now = (y1 - y0) * src_h * s0 / float(slot_h)
    k = 1.0
    if fill_now < MIN_SLOT_FILL:
        k = min(MAX_EXTRA_ZOOM, MIN_SLOT_FILL / max(fill_now, 1e-3))

    if y1 > y0 and y0 < 0.95:
        sh_needed_bot = (1.0 - headroom_pct / 100.0) * slot_h / max(0.1, 1.0 - y0)
        sh_needed_top = (headroom_pct / 100.0) * slot_h / max(0.05, y0)
        k_head = max(sh_needed_bot, sh_needed_top) / max(1.0, src_h * s0)
        if k_head > k:
            k = min(MAX_EXTRA_ZOOM, k_head)

    s = s0 * k * max(1.0, float(extra_zoom or 1.0))
    sw = max(slot_w, _even(src_w * s))
    sh = max(slot_h, _even(src_h * s))

    cx_norm = float(focus[0]) if (focus and len(focus) >= 2) else 0.5 * (x0 + x1)
    cx_px = cx_norm * sw
    crop_x = int(round(min(max(cx_px - slot_w / 2.0, 0), sw - slot_w)))

    head_top_px = y0 * sh
    crop_y = int(round(min(max(head_top_px - (headroom_pct / 100.0) * slot_h, 0), sh - slot_h)))

    return {
        "filter": f"scale={sw}:{sh}:flags=lanczos,crop={slot_w}:{slot_h}:{crop_x}:{crop_y}",
        "scale": (sw, sh),
        "crop": (crop_x, crop_y),
        "slot_size": (slot_w, slot_h),
        "extra_zoom": round(k * max(1.0, float(extra_zoom or 1.0)), 3),
    }


def compute_paired_split_crop_filters(
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
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """Compute matched FFmpeg `scale=sw:sh:flags=lanczos,crop=slot_w:slot_h:cx:cy` filters
    for a two-character split screen (`split_h` or `split_v`) so that BOTH characters share:
      1. The exact same common headroom (`common_hr` fraction from top of slot to head crown),
      2. Matched face zoom size (`common_fh` fraction of slot height), and
      3. Horizontal face centering (`fx`) inside their respective slot.
    Because the pair calculation is symmetric over `{live_src, peer_src}`, swapping active
    speaker and listener across dialogue turns produces zero framing shift.
    """
    live_slot_w, live_slot_h = _even(max(16, live_slot_w)), _even(max(16, live_slot_h))
    peer_slot_w, peer_slot_h = _even(max(16, peer_slot_w)), _even(max(16, peer_slot_h))
    src_w, src_h = max(16, int(src_w or live_slot_w)), max(16, int(src_h or live_slot_h))
    peer_w, peer_h = max(16, int(peer_w or peer_slot_w)), max(16, int(peer_h or peer_slot_h))

    def _extract_geom(
        clip_path: Union[str, Path],
        fallback_focus: Optional[Sequence[float]],
    ) -> Tuple[float, float, float]:
        vis: Dict[str, Any] = {}
        try:
            p = Path(clip_path)
            if p.is_file():
                vis = analyze_clip_vision(p) or {}
        except Exception:
            vis = {}
        bbox = vis.get("bbox_norm")
        v_foc = vis.get("focus")
        if bbox and len(bbox) >= 4:
            x0, y0, x1, y1 = [min(1.0, max(0.0, float(v))) for v in bbox[:4]]
            if x1 > x0 and y1 > y0:
                fx = float(v_foc[0]) if (v_foc and len(v_foc) >= 2) else 0.5 * (x0 + x1)
                return max(0.05, min(0.95, fx)), max(0.04, min(0.65, y0)), max(0.10, min(0.65, y1 - y0))
        if fallback_focus and len(fallback_focus) >= 2:
            fx = max(0.05, min(0.95, float(fallback_focus[0])))
            fy = max(0.10, min(0.75, float(fallback_focus[1])))
            y0 = max(0.06, fy - 0.14)
            return fx, y0, 0.26
        return 0.5, 0.18, 0.26

    fx_a, y0_a, fh_a = _extract_geom(live_src, live_focus)
    fx_b, y0_b, fh_b = _extract_geom(peer_src, peer_focus)

    # Dynamic common headroom (~15%-20%) shared identically by both characters
    common_hr = round(max(0.14, min(0.20, min(y0_a, y0_b) * 0.85)), 4)

    s0_a = max(live_slot_w / float(src_w), live_slot_h / float(src_h))
    s0_b = max(peer_slot_w / float(peer_w), peer_slot_h / float(peer_h))
    hr_ratio_a = (src_h * s0_a) / float(live_slot_h)
    hr_ratio_b = (peer_h * s0_b) / float(peer_slot_h)

    def _min_scale_for_headroom(y0: float, h_ratio: float) -> float:
        k_top = common_hr / max(0.05, y0 * h_ratio)
        k_bot = (1.0 - common_hr) / max(0.10, (1.0 - y0) * h_ratio)
        return min(MAX_EXTRA_ZOOM, max(1.0, k_top, k_bot))

    k_hr_a = _min_scale_for_headroom(y0_a, hr_ratio_a)
    k_hr_b = _min_scale_for_headroom(y0_b, hr_ratio_b)

    # Common face height fraction in slot so both characters have the same face zoom level
    common_fh = max(fh_a * hr_ratio_a * k_hr_a, fh_b * hr_ratio_b * k_hr_b, MIN_SLOT_FILL * 0.52)

    def _build_one(
        w_in: int,
        h_in: int,
        sw_slot: int,
        sh_slot: int,
        s0: float,
        h_ratio: float,
        fx: float,
        y0: float,
        fh: float,
        k_hr: float,
    ) -> Dict[str, Any]:
        k_face = common_fh / max(0.08, fh * h_ratio)
        k = min(MAX_EXTRA_ZOOM, max(1.0, k_hr, k_face))
        s = s0 * k
        sw = max(sw_slot, _even(w_in * s))
        sh = max(sh_slot, _even(h_in * s))
        crop_x = int(round(min(max(fx * sw - sw_slot / 2.0, 0), sw - sw_slot)))
        crop_y = int(round(min(max(y0 * sh - common_hr * sh_slot, 0), sh - sh_slot)))
        return {
            "filter": f"scale={sw}:{sh}:flags=lanczos,crop={sw_slot}:{sh_slot}:{crop_x}:{crop_y}",
            "scale": (sw, sh),
            "crop": (crop_x, crop_y),
            "slot_size": (sw_slot, sh_slot),
            "common_headroom_pct": round(common_hr * 100.0, 2),
            "extra_zoom": round(k, 3),
        }

    live_crop = _build_one(src_w, src_h, live_slot_w, live_slot_h, s0_a, hr_ratio_a, fx_a, y0_a, fh_a, k_hr_a)
    peer_crop = _build_one(peer_w, peer_h, peer_slot_w, peer_slot_h, s0_b, hr_ratio_b, fx_b, y0_b, fh_b, k_hr_b)
    return live_crop, peer_crop


def _load_clip_speech_spans(video_path: Path) -> List[Tuple[float, float]]:
    """Auto-load speech spans from `work/<stem>/hq_words.json` or `words.json` if present."""
    work_dir = clip_work_dir(video_path)
    for fname in ("hq_words.json", "words.json"):
        f = work_dir / fname
        if f.is_file():
            try:
                words = read_json(f)
                spans = []
                for w in words:
                    s = w.get("start")
                    e = w.get("end")
                    if s is not None and e is not None and float(e) > float(s):
                        spans.append((float(s), float(e)))
                if spans:
                    return _merge_spans(spans, gap_tol=0.25)
            except Exception:
                pass
    return []


def _normalize_speech_spans(
    speech_spans: Optional[Sequence[Any]],
    video_path: Optional[Path] = None,
) -> List[Tuple[float, float]]:
    if not speech_spans and video_path is not None:
        return _load_clip_speech_spans(video_path)
    if not speech_spans:
        return []
    raw: List[Tuple[float, float]] = []
    for item in speech_spans:
        if isinstance(item, dict):
            s = item.get("start", item.get("start_sec"))
            e = item.get("end", item.get("end_sec"))
            if s is not None and e is not None and float(e) > float(s):
                raw.append((float(s), float(e)))
        elif isinstance(item, (list, tuple)) and len(item) >= 2:
            s, e = float(item[0]), float(item[1])
            if e > s:
                raw.append((s, e))
    return _merge_spans(raw, gap_tol=0.20)


def _merge_spans(spans: List[Tuple[float, float]], gap_tol: float = 0.20) -> List[Tuple[float, float]]:
    if not spans:
        return []
    sorted_spans = sorted(spans, key=lambda x: x[0])
    merged = [sorted_spans[0]]
    for s, e in sorted_spans[1:]:
        ps, pe = merged[-1]
        if s <= pe + gap_tol:
            merged[-1] = (ps, max(pe, e))
        else:
            merged.append((s, e))
    return merged


def _non_speaking_intervals(
    duration: float,
    speech_spans: List[Tuple[float, float]],
    margin: float = 0.08,
) -> List[Tuple[float, float]]:
    """Compute non-speaking intervals `[t0, t1]` outside `speech_spans`."""
    if duration <= 0.1:
        return [(0.0, max(0.5, duration))]
    if not speech_spans:
        return [(0.0, duration)]

    intervals: List[Tuple[float, float]] = []
    cursor = 0.0
    for s, e in speech_spans:
        ns_end = max(0.0, s - margin)
        if ns_end - cursor >= 0.18:
            intervals.append((round(cursor, 3), round(ns_end, 3)))
        cursor = min(duration, e + margin)
    if duration - cursor >= 0.18:
        intervals.append((round(cursor, 3), round(duration, 3)))

    return intervals


def find_listening_idle_window(
    video_path: Union[str, Path],
    speech_spans: Optional[Sequence[Any]] = None,
    duration: Optional[float] = None,
    sample_fps: float = 8.0,
) -> Dict[str, Any]:
    """Find the cleanest mouth-closed listening window (`still_at` and `idle_span`) in a clip.

    Tracks Mouth Aspect Ratio (`MAR`) and mouth pixel delta (`cv2.absdiff`) across
    non-speaking intervals so that in `split_v`, `split_h`, and `video_call` modes,
    the non-speaking listener holds on a clean mouth-closed frame/loop with zero
    double-mouth movement.
    """
    resolved = resolve(video_path)
    spans = _normalize_speech_spans(speech_spans, resolved if resolved.is_file() else None)
    cache_key = f"{resolved}|{json.dumps(spans)}"
    if cache_key in _IDLE_CACHE:
        return dict(_IDLE_CACHE[cache_key])

    dur = float(duration or 0.0)
    if dur <= 0.0 and resolved.is_file():
        info = probe(resolved)
        dur = float(info.get("duration") or 6.0)
    if dur <= 0.0:
        dur = 6.0

    quiet_wins = _non_speaking_intervals(dur, spans)

    # Default fallback if cv2 is unavailable or clip does not exist on disk
    if cv2 is None or np is None or not resolved.is_file():
        if quiet_wins:
            best_q = max(quiet_wins, key=lambda w: w[1] - w[0])
            s0, s1 = best_q[0], best_q[1]
        else:
            s0 = round(max(0.0, dur - min(1.2, dur * 0.25)), 3)
            s1 = round(dur, 3)
        mid = round(0.5 * (s0 + s1), 3)
        res = {
            "still_at": mid,
            "idle_span": [round(s0, 3), round(s1, 3)],
            "start": round(s0, 3),
            "end": round(s1, 3),
            "duration": round(max(0.1, s1 - s0), 3),
            "mar_score": 0.035,
            "motion_delta": 0.004,
        }
        _IDLE_CACHE[cache_key] = res
        return dict(res)

    cap = cv2.VideoCapture(str(resolved))
    if not cap.isOpened():
        s0, s1 = quiet_wins[0] if quiet_wins else (0.0, min(1.5, dur))
        res = {
            "still_at": round(0.5 * (s0 + s1), 3),
            "idle_span": [round(s0, 3), round(s1, 3)],
            "start": round(s0, 3),
            "end": round(s1, 3),
            "duration": round(max(0.1, s1 - s0), 3),
            "mar_score": 0.035,
            "motion_delta": 0.004,
        }
        _IDLE_CACHE[cache_key] = res
        return dict(res)

    fps = float(cap.get(cv2.CAP_PROP_FPS) or 24.0)
    if fps <= 1.0:
        fps = 24.0
    total_frames = max(1, int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or round(dur * fps)))
    dur = max(dur, total_frames / fps)

    # Detect initial mouth ROI on midpoint frame
    cap.set(cv2.CAP_PROP_POS_FRAMES, total_frames // 2)
    ok, mid_frame = cap.read()
    mouth_roi = [0.38, 0.48, 0.62, 0.62]
    if ok and mid_frame is not None:
        faces = detect_faces_in_frame(mid_frame, is_bgr=True)
        if faces:
            mouth_roi = faces[0]["mouth_roi_norm"]

    # Sample frames in non-speaking windows first; if none, sample across entire clip
    search_windows = quiet_wins if quiet_wins else [(0.0, dur)]
    step_sec = 1.0 / max(2.0, min(sample_fps, fps))

    samples: List[Dict[str, float]] = []
    for w_start, w_end in search_windows:
        t = w_start
        prev_gray = None
        while t <= w_end + 1e-4:
            f_idx = min(total_frames - 1, max(0, int(round(t * fps))))
            cap.set(cv2.CAP_PROP_POS_FRAMES, f_idx)
            ok, frame = cap.read()
            if ok and frame is not None:
                arr_rgb = frame[:, :, ::-1]
                mar, delta, prev_gray = _measure_mouth_mar(arr_rgb, mouth_roi, prev_gray)
                in_quiet = any(qs <= t <= qe for qs, qe in quiet_wins)
                # Combined cost penalizes open mouth (MAR) and lip motion (delta)
                cost = mar * 1.4 + delta * 3.5 + (0.0 if in_quiet else 0.25)
                samples.append({
                    "t": round(f_idx / fps, 3),
                    "mar": mar,
                    "delta": delta,
                    "cost": round(cost, 5),
                    "idle": 1.0 if (mar < 0.085 and delta <= 0.015) else 0.0,
                })
            t += step_sec

    cap.release()

    if not samples:
        s0, s1 = search_windows[0]
        res = {
            "still_at": round(0.5 * (s0 + s1), 3),
            "idle_span": [round(s0, 3), round(s1, 3)],
            "start": round(s0, 3),
            "end": round(s1, 3),
            "duration": round(max(0.1, s1 - s0), 3),
            "mar_score": 0.04,
            "motion_delta": 0.005,
        }
        _IDLE_CACHE[cache_key] = res
        return dict(res)

    best_sample = min(samples, key=lambda s: s["cost"])
    still_at = float(best_sample["t"])

    # Find longest contiguous run of clean idle frames (or lowest cost window >= 0.4s)
    best_run: List[Dict[str, float]] = []
    cur_run: List[Dict[str, float]] = []
    for s in samples:
        if s["idle"] > 0.5 and (not cur_run or (s["t"] - cur_run[-1]["t"]) <= step_sec * 1.8):
            cur_run.append(s)
            if len(cur_run) > len(best_run):
                best_run = list(cur_run)
        else:
            cur_run = [s] if s["idle"] > 0.5 else []

    if len(best_run) >= 2 and (best_run[-1]["t"] - best_run[0]["t"]) >= 0.25:
        span_start = best_run[0]["t"]
        span_end = best_run[-1]["t"]
        mid_sample = min(best_run, key=lambda s: s["cost"])
        still_at = float(mid_sample["t"])
        avg_mar = sum(s["mar"] for s in best_run) / len(best_run)
        avg_delta = sum(s["delta"] for s in best_run) / len(best_run)
    else:
        span_start = max(0.0, still_at - 0.35)
        span_end = min(dur, still_at + 0.45)
        if span_end <= span_start + 0.1:
            span_start = max(0.0, min(still_at, dur - 0.5))
            span_end = min(dur, span_start + 0.5)
        avg_mar = best_sample["mar"]
        avg_delta = best_sample["delta"]

    res = {
        "still_at": round(still_at, 3),
        "idle_span": [round(span_start, 3), round(span_end, 3)],
        "start": round(span_start, 3),
        "end": round(span_end, 3),
        "duration": round(max(0.1, span_end - span_start), 3),
        "mar_score": round(float(avg_mar), 4),
        "motion_delta": round(float(avg_delta), 4),
    }
    _IDLE_CACHE[cache_key] = res
    return dict(res)


def _clip_file_hash8(resolved: Path) -> str:
    """Return an 8-char content/mtime signature so disk vision caches never collide across projects."""
    try:
        st = resolved.stat()
        raw = f"{resolved.resolve()}|{st.st_size}|{int(st.st_mtime)}"
    except OSError:
        raw = str(resolved)
    return hashlib.md5(raw.encode("utf-8")).hexdigest()[:8]


def analyze_clip_vision(
    video_path: Union[str, Path],
    speech_spans: Optional[Sequence[Any]] = None,
    sample_count: int = 5,
) -> Dict[str, Any]:
    """Sample frames from `video_path` to determine face count, shot type, eye-anchored
    `focus = [fx, fy]` (14% headroom / 38% eyeline), and clean mouth-closed idle window.
    Persists results to `work/<stem>/vision_<hash8>.json` so vision data survives process exits.
    """
    resolved = resolve(video_path)
    cache_key = str(resolved)
    if cache_key in _VISION_CACHE and speech_spans is None:
        return dict(_VISION_CACHE[cache_key])

    disk_cache_file: Optional[Path] = None
    if resolved.is_file() and speech_spans is None:
        disk_cache_file = clip_work_dir(resolved) / f"vision_{_clip_file_hash8(resolved)}.json"
        if disk_cache_file.is_file():
            try:
                cached = read_json(disk_cache_file)
                if isinstance(cached, dict) and cached.get("bbox_norm"):
                    _VISION_CACHE[cache_key] = cached
                    return dict(cached)
            except Exception:
                pass

    info = probe(resolved) if resolved.is_file() else {
        "path": str(resolved), "duration": 6.0, "width": 720, "height": 1280, "fps": 24.0, "has_audio": True
    }
    w = int(info.get("width") or 720)
    h = int(info.get("height") or 1280)
    dur = float(info.get("duration") or 6.0)
    aspect = round(w / float(max(1, h)), 4)
    orientation = "portrait" if h >= w else "landscape"

    sampled_faces: List[List[Dict[str, Any]]] = []
    baked_seams: List[str] = []

    if cv2 is not None and np is not None and resolved.is_file():
        cap = cv2.VideoCapture(str(resolved))
        if cap.isOpened():
            fps = float(cap.get(cv2.CAP_PROP_FPS) or info.get("fps") or 24.0)
            total_frames = max(1, int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or round(dur * fps)))
            n_samples = max(1, min(sample_count, total_frames))
            indices = [
                min(total_frames - 1, max(0, int(round((i + 0.5) / n_samples * total_frames))))
                for i in range(n_samples)
            ]
            for f_idx in indices:
                cap.set(cv2.CAP_PROP_POS_FRAMES, f_idx)
                ok, frame = cap.read()
                if not ok or frame is None:
                    continue
                seam = detect_baked_split_seam(frame)
                if seam:
                    baked_seams.append(seam)
                faces = detect_faces_in_frame(frame, is_bgr=True)
                if faces:
                    sampled_faces.append(faces)
            cap.release()

    # Aggregate face count and geometry across sampled frames
    if sampled_faces:
        # Choose representative frame with modal (or maximum reliable) face count
        counts = [len(fl) for fl in sampled_faces]
        two_face_frames = [fl for fl in sampled_faces if len(fl) >= 2]
        if len(two_face_frames) >= max(1, len(sampled_faces) // 2):
            rep_faces = two_face_frames[len(two_face_frames) // 2][:2]
            face_count = 2
        else:
            one_face_frames = [fl for fl in sampled_faces if len(fl) >= 1]
            # Average primary face bbox and eye center across frames for stability
            avg_bbox = [
                round(sum(fl[0]["bbox_norm"][k] for fl in one_face_frames) / len(one_face_frames), 4)
                for k in range(4)
            ]
            avg_eye = [
                round(sum(fl[0]["eye_center_norm"][k] for fl in one_face_frames) / len(one_face_frames), 4)
                for k in range(2)
            ]
            avg_focus = compute_eye_anchored_focus(avg_bbox, avg_eye)
            # Deadband-smoothed focus path (first half vs second half)
            half_idx = max(1, len(one_face_frames) // 2)
            first_h = one_face_frames[:half_idx]
            last_h = one_face_frames[half_idx:] or first_h
            bbox_0 = [round(sum(fl[0]["bbox_norm"][k] for fl in first_h) / len(first_h), 4) for k in range(4)]
            eye_0 = [round(sum(fl[0]["eye_center_norm"][k] for fl in first_h) / len(first_h), 4) for k in range(2)]
            bbox_1 = [round(sum(fl[0]["bbox_norm"][k] for fl in last_h) / len(last_h), 4) for k in range(4)]
            eye_1 = [round(sum(fl[0]["eye_center_norm"][k] for fl in last_h) / len(last_h), 4) for k in range(2)]
            f0 = compute_eye_anchored_focus(bbox_0, eye_0)
            f1 = compute_eye_anchored_focus(bbox_1, eye_1)
            if math.hypot(f1[0] - f0[0], f1[1] - f0[1]) > 0.04:
                focus_start, focus_end = f0, f1
            else:
                focus_start, focus_end = avg_focus, avg_focus
            primary = dict(one_face_frames[len(one_face_frames) // 2][0])
            primary["bbox_norm"] = avg_bbox
            primary["eye_center_norm"] = avg_eye
            primary["focus"] = focus_start
            primary["focus_end"] = focus_end
            primary["headroom_pct"] = round(avg_bbox[1] * 100.0, 2)
            primary["eyeline_pct"] = round(avg_eye[1] * 100.0, 2)
            rep_faces = [primary]
            face_count = 1
    else:
        # Graceful geometric default with 14% headroom / 38% eyeline
        default_bbox = [0.24, 0.14, 0.76, 0.86]
        default_eye = [0.5, 0.38]
        rep_faces = [{
            "id": "face_1",
            "confidence": 0.0,
            "bbox_norm": default_bbox,
            "eye_center_norm": default_eye,
            "mouth_center_norm": [0.5, 0.54],
            "mouth_roi_norm": [0.38, 0.48, 0.62, 0.60],
            "headroom_pct": TARGET_HEADROOM_PCT,
            "eyeline_pct": TARGET_EYELINE_PCT,
            "focus": [0.5, 0.38],
            "mar_score": 0.05,
            "is_speaking": True,
            "position": "center",
            "vertical": "middle",
        }]
        face_count = 1

    # Determine shot_type: baked split vs in-frame two_shot vs single
    if baked_seams and len(baked_seams) >= max(1, len(sampled_faces) // 2):
        shot_type = baked_seams[0]
    elif face_count >= 2:
        f0, f1 = rep_faces[0], rep_faces[1]
        dx = abs(f0["eye_center_norm"][0] - f1["eye_center_norm"][0])
        dy = abs(f0["eye_center_norm"][1] - f1["eye_center_norm"][1])
        if dy > 0.35 and dx < 0.25:
            shot_type = "split_v_baked"
        elif dx >= 0.18:
            shot_type = "two_shot"
        else:
            shot_type = "single"
    else:
        shot_type = "single"

    primary_face = rep_faces[0]
    idle_info = find_listening_idle_window(resolved, speech_spans=speech_spans, duration=dur)

    result = {
        "path": str(resolved),
        "width": w,
        "height": h,
        "duration": dur,
        "fps": info.get("fps") or 24.0,
        "aspect_ratio": aspect,
        "orientation": orientation,
        "face_count": face_count,
        "shot_type": shot_type,
        "focus": primary_face["focus"],
        "focus_end": primary_face.get("focus_end", primary_face["focus"]),
        "bbox_norm": primary_face["bbox_norm"],
        "eye_center_norm": primary_face["eye_center_norm"],
        "headroom_pct": primary_face["headroom_pct"],
        "eyeline_pct": primary_face["eyeline_pct"],
        "faces": rep_faces,
        "idle_window": idle_info,
        "still_at": idle_info["still_at"],
        "idle_span": idle_info["idle_span"],
    }
    if speech_spans is None:
        _VISION_CACHE[cache_key] = result
        if disk_cache_file is not None:
            try:
                write_json(disk_cache_file, result)
            except Exception:
                pass
    return dict(result)


def detect_actor_readiness_bounds(
    video_path: Union[str, Path],
    seg_start: float,
    seg_end: float,
    first_word_start: Optional[float] = None,
    last_word_end: Optional[float] = None,
) -> Dict[str, Any]:
    """Compute clean `[ready_start, ready_end]` cut bounds that trim pre-speech 'actor settling'
    (>0.22s before first word) and post-speech 'end-of-take freeze' (>0.24s after last word).
    """
    s0 = max(0.0, float(seg_start))
    s1 = max(s0 + 0.10, float(seg_end))
    fw = float(first_word_start) if first_word_start is not None else s0
    lw = float(last_word_end) if last_word_end is not None else s1

    ready_start = round(max(s0, fw - 0.18), 2) if (fw - s0) > 0.22 else round(s0, 2)
    ready_end = round(min(s1, lw + 0.18), 2) if (s1 - lw) > 0.24 else round(s1, 2)
    if ready_end <= ready_start + 0.20:
        ready_start, ready_end = round(s0, 2), round(s1, 2)

    return {
        "ready_start": ready_start,
        "ready_end": ready_end,
        "trimmed_lead_sec": round(max(0.0, ready_start - s0), 2),
        "trimmed_tail_sec": round(max(0.0, s1 - ready_end), 2),
    }


# Convenient aliases
analyze_clip = analyze_clip_vision
track_clip = analyze_clip_vision


def _parse_script_lines(script_lines: Any) -> List[Dict[str, Any]]:
    """Normalize `script_lines` (str, list of str, or list of dicts) into structured dicts."""
    if not script_lines:
        return []
    if isinstance(script_lines, (str, Path)):
        p = Path(str(script_lines))
        if p.is_file():
            raw_text = p.read_text(encoding="utf-8", errors="replace")
        else:
            raw_text = str(script_lines)
        lines_in = raw_text.splitlines()
    elif isinstance(script_lines, list):
        lines_in = script_lines
    else:
        return []

    parsed: List[Dict[str, Any]] = []
    header_cues: List[str] = []

    for idx, item in enumerate(lines_in):
        if isinstance(item, dict):
            lid = str(item.get("id") or f"L{idx + 1}")
            sp = str(item.get("speaker") or "")
            txt = str(item.get("text") or item.get("note") or item.get("line") or "")
            dur = float(item.get("duration") or (float(item.get("end", 0)) - float(item.get("start", 0))) or 0.0)
            mark = item.get("mark")
            if not mark:
                if re.search(r"\[WRONG\]", txt, re.I):
                    mark = "wrong"
                elif re.search(r"\[RIGHT\]|\[CORRECT\]", txt, re.I):
                    mark = "right"
            parsed.append({
                "id": lid,
                "speaker": sp,
                "text": txt,
                "duration": dur,
                "mark": mark,
                "src": item.get("src"),
                "raw": item,
            })
            continue

        line = str(item).strip()
        if not line:
            continue
        if line.startswith("#"):
            header_cues.append(line)
            continue

        m = re.match(r"^(L\d+[a-z]?)\s+([^:]+):\s*(.+)$", line)
        if m:
            lid, sp, body = m.group(1), m.group(2).strip(), m.group(3).strip()
        else:
            lid = f"L{len(parsed) + 1}"
            sp_m = re.match(r"^([A-Za-z][A-Za-z0-9 _()-]{1,28}):\s*(.+)$", line)
            if sp_m:
                sp, body = sp_m.group(1).strip(), sp_m.group(2).strip()
            else:
                sp, body = "", line

        # Clean parenthetical stage directions from speaker name e.g. "Ravi (slow, with pauses)" -> "Ravi"
        sp_clean = re.sub(r"\s*\([^)]*\)\s*", "", sp).strip()
        mark = None
        if re.search(r"\[WRONG\]", body, re.I):
            mark = "wrong"
        elif re.search(r"\[RIGHT\]|\[CORRECT\]", body, re.I):
            mark = "right"

        # Estimate spoken duration from word/char count when not attached to a segment yet
        word_cnt = max(1, len(body.split()))
        est_dur = round(max(1.5, word_cnt * 0.42), 2)
        parsed.append({
            "id": lid,
            "speaker": sp_clean,
            "text": body,
            "duration": est_dur,
            "mark": mark,
            "header_context": " ".join(header_cues),
        })

    if header_cues and parsed and "header_context" not in parsed[0]:
        for p in parsed:
            p["header_context"] = " ".join(header_cues)
    return parsed


def recommend_project_layouts(
    clips_info: Any,
    script_lines: Any = None,
    preset: str = "auto",
) -> Dict[str, Any]:
    """Recommend layout modes (`single`, `split_v`, `split_h`, `chat_bar`, `video_call`)
    per segment/line based on:
      1. Explicit `preset` (`single`, `split_v`, `split_h`, `chat_bar`, `video_call`, `hybrid`, `auto`)
      2. Script cues (`[Video Call]`, `[Split Screen]`, `[Texting]`, `on phone`, `on tablet`)
      3. Face geometry (`face_count == 2` separated faces in-frame vs `1` face vs baked split seam)
      4. Source aspect ratio (portrait `9:16` vs landscape `16:9`)
      5. Turn-taking structure (rapid Q&A / `[WRONG]` / `[RIGHT]` grammar corrections vs long monologues -> `hybrid`)
    """
    preset_norm = str(preset or "auto").strip().lower()
    alias_map = {
        "top_bottom": "split_v",
        "vstack": "split_v",
        "left_right": "split_h",
        "hstack": "split_h",
        "texting": "chat_bar",
        "chat": "chat_bar",
        "pip_call": "video_call",
        "gmeet_grid": "video_call",
    }
    preset_norm = alias_map.get(preset_norm, preset_norm)

    # Normalize clips_info into a dict of clip_key -> vision/probe dict
    clip_map: Dict[str, Dict[str, Any]] = {}
    if isinstance(clips_info, dict):
        for k, v in clips_info.items():
            if isinstance(v, dict):
                clip_map[str(k)] = v
                if v.get("path"):
                    clip_map[Path(str(v["path"])).stem] = v
                    clip_map[Path(str(v["path"])).name] = v
    elif isinstance(clips_info, list):
        for idx, v in enumerate(clips_info):
            if isinstance(v, dict):
                key = str(v.get("id") or Path(str(v.get("path") or v.get("src") or f"C{idx+1}")).stem)
                clip_map[key] = v

    parsed_lines = _parse_script_lines(script_lines)
    if not parsed_lines and isinstance(clips_info, list):
        # If clips_info was passed as a list of segments without script_lines
        parsed_lines = _parse_script_lines(clips_info)

    all_text = " ".join(
        [p.get("header_context", "") for p in parsed_lines[:1]]
        + [f"{p.get('speaker', '')}: {p.get('text', '')}" for p in parsed_lines]
    )

    has_video_call_cue = bool(
        re.search(r"\[video\s*call\]|\[pip[_\s]*call\]|video\s*call|facetime|google\s*meet|on\s+phone|on\s+tablet", all_text, re.I)
    )
    has_split_cue = bool(
        re.search(r"\[split\s*screen\]|\[split[_\s]*v\]|\[split[_\s]*h\]|split[\s-]*screen|side[\s-]*by[\s-]*side", all_text, re.I)
    )
    has_chat_cue = bool(
        re.search(r"\[texting\]|\[chat(?:[_\s]*bar)?\]|text\s*message|whatsapp|chat\s*bubble", all_text, re.I)
    )
    has_inframe_skit_cue = bool(
        re.search(r"projection\s+pops\s+off|hologram|talking\s+in\s+the\s+galley|two[- ]?shot", all_text, re.I)
    )

    # Inspect clip geometry across clip_map
    unique_clips = list({id(v): v for v in clip_map.values()}.values())
    two_shot_count = sum(1 for c in unique_clips if int(c.get("face_count") or 1) >= 2 and c.get("shot_type") == "two_shot")
    baked_split_count = sum(1 for c in unique_clips if str(c.get("shot_type") or "").endswith("_baked"))
    landscape_count = sum(1 for c in unique_clips if int(c.get("width") or 720) > int(c.get("height") or 1280))

    speakers = [p["speaker"].lower() for p in parsed_lines if p.get("speaker")]
    unique_speakers = set(speakers)
    speaker_switches = sum(1 for i in range(1, len(speakers)) if speakers[i] != speakers[i - 1])
    correction_marks = sum(1 for p in parsed_lines if p.get("mark") in ("wrong", "right"))

    # Determine overall project preset when preset == "auto"
    if preset_norm in ("single", "split_v", "split_h", "chat_bar", "video_call"):
        resolved_preset = preset_norm
        primary_multi = preset_norm
    else:
        # "auto" or "hybrid"
        if (
            (baked_split_count > len(unique_clips) // 2 or (has_inframe_skit_cue and not has_split_cue) or (two_shot_count >= 3 and landscape_count == 0 and not has_split_cue))
            and preset_norm == "auto"
        ):
            resolved_preset = "single"
            primary_multi = "single"
        elif has_chat_cue and not (has_video_call_cue or has_split_cue):
            resolved_preset = "hybrid" if len(parsed_lines) > 6 else "chat_bar"
            primary_multi = "chat_bar"
        elif has_split_cue or (two_shot_count > 0 and landscape_count > 0):
            resolved_preset = "hybrid" if len(parsed_lines) > 6 else "split_v"
            primary_multi = "split_v"
        elif has_video_call_cue:
            resolved_preset = "hybrid"
            primary_multi = "video_call"
        elif len(unique_speakers) >= 2 and (speaker_switches >= 3 or correction_marks >= 2):
            resolved_preset = "hybrid"
            primary_multi = "split_v"
        else:
            resolved_preset = "single" if preset_norm == "auto" else "hybrid"
            primary_multi = "single" if resolved_preset == "single" else "split_v"

    segment_layouts: Dict[str, str] = {}
    recommendations: List[Dict[str, Any]] = []

    n_lines = len(parsed_lines)
    for idx, item in enumerate(parsed_lines):
        lid = item["id"]
        txt = item.get("text", "")
        dur = float(item.get("duration") or 4.0)
        mark = item.get("mark")

        # Line-level explicit override cues
        if re.search(r"\[video\s*call\]|\[pip[_\s]*call\]", txt, re.I):
            lay, reason = "video_call", "explicit [Video Call] cue on line"
        elif re.search(r"\[split\s*h\]|\[left[_\s]*right\]", txt, re.I):
            lay, reason = "split_h", "explicit [Split H] cue on line"
        elif re.search(r"\[split\s*screen\]|\[split[_\s]*v\]|\[top[_\s]*bottom\]", txt, re.I):
            lay, reason = "split_v", "explicit [Split Screen] cue on line"
        elif re.search(r"\[texting\]|\[chat(?:[_\s]*bar)?\]", txt, re.I):
            lay, reason = "chat_bar", "explicit [Texting/Chat] cue on line"
        elif re.search(r"\[single\]|\[full\s*screen\]", txt, re.I):
            lay, reason = "single", "explicit [Single] cue on line"
        elif resolved_preset in ("single", "split_v", "split_h", "chat_bar", "video_call"):
            lay = resolved_preset
            reason = f"fixed preset={resolved_preset}"
        else:
            # Hybrid multi-layout pacing:
            # - Rapid Q&A / grammar correction turns ([WRONG]/[RIGHT] or quoted practice phrases in early/mid turns)
            #   use engaging multi-layouts (chat_bar / split_v / video_call)
            # - Long explanation monologues (> 7.2s) or final CTA install pitch use clean full-screen `single`
            is_cta = bool(re.search(r"install\s+now|download|ಕ್ಲಿಕ್\s+ಮಾಡಿ", txt, re.I)) or (idx == n_lines - 1 and n_lines > 4)
            has_quoted_practice = bool(re.search(r"\"[^\"]{2,45}\"", txt))
            is_rapid_qa = (mark in ("wrong", "right")) or (has_quoted_practice and idx <= max(8, n_lines // 2))

            if is_cta:
                lay = "single"
                reason = "closing CTA full-screen focus"
            elif is_rapid_qa:
                if mark in ("wrong", "right") and has_quoted_practice and primary_multi in ("video_call", "split_v"):
                    # Mix split_v / video_call / chat_bar during correction drills
                    lay = "split_v" if idx < 4 else primary_multi
                else:
                    lay = primary_multi
                reason = "rapid Q&A / grammar correction turn"
            elif dur >= 7.2:
                lay = "single"
                reason = f"long monologue ({dur:.1f}s) -> full-screen single"
            elif idx > 0 and idx + 1 < n_lines and dur <= 5.5:
                # Short interactive question/reaction turn between two speakers
                lay = "video_call" if has_video_call_cue else primary_multi
                reason = "short interactive dialogue turn"
            else:
                lay = "single"
                reason = "narrative explanation turn"

        segment_layouts[lid] = lay
        recommendations.append({
            "index": idx,
            "id": lid,
            "speaker": item.get("speaker", ""),
            "layout": lay,
            "reason": reason,
        })

    return {
        "preset": resolved_preset,
        "default_layout": primary_multi,
        "segment_layouts": segment_layouts,
        "recommendations": recommendations,
    }


def main() -> None:
    ap = argparse.ArgumentParser(description="Analyze clip face/headroom geometry, idle windows, and recommend layouts.")
    ap.add_argument("clips", nargs="*", help="Video clip paths to analyze")
    ap.add_argument("--script", help="Optional script file for layout recommendation")
    ap.add_argument("--preset", default="auto", help="Layout preset: auto | hybrid | single | split_v | split_h | chat_bar | video_call")
    ap.add_argument("--out", help="Optional JSON output path")
    args = ap.parse_args()

    clips_data: Dict[str, Any] = {}
    for c in args.clips:
        p = resolve(c)
        info = analyze_clip_vision(p)
        clips_data[p.stem] = info
        # Also persist vision.json in work/<stem>/ if work/<stem> exists
        work_dir = clip_work_dir(p)
        if work_dir.is_dir():
            write_json(work_dir / "vision.json", info)

    script_text = resolve(args.script) if args.script else None
    rec = recommend_project_layouts(clips_data, script_lines=script_text, preset=args.preset)
    output_payload = {"clips": clips_data, "layouts": rec}

    if args.out:
        write_json(resolve(args.out), output_payload)
    print(json.dumps(output_payload, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
