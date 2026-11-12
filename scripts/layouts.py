"""Multi-Layout Segment Compositor, Auto-Peer Resolver & Layout-Aware Mark/SFX Engine (`layouts.py`).

Supports all 5 layout families:
  1. `single`                      — Full-screen speaker with static or eased cosine push-in zoom
                                     and eye-anchored headroom focus (`14%` headroom, `38%` eyeline).
  2. `split_v` / `top_bottom`      — Vertical 50/50 split (`vstack`) with active speaker live in their
                                     slot and listener holding on a mouth-closed idle window (`MAR < 0.08`).
  3. `split_h` / `left_right`      — Horizontal 50/50 split (`hstack`) with active speaker live and
                                     listener holding on a mouth-closed idle window.
  4. `chat_bar` / `texting`        — Top texting/chat bar (`"floating_overlay"` or `"docked"` strip)
                                     with multi-script incoming/outgoing bubbles & ❌/✅ accents.
  5. `video_call` / `pip_call` /   — Mobile Video Call Chrome (status bar, caller header, live timer,
     `gmeet_grid`                    `alphamerge` rounded/circle PiP or dual grid tiles, and bottom call bar).
"""
from __future__ import annotations

import array
import hashlib
import json
import math
import re
import subprocess
import sys
import wave
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple, Union

from common import (
    DEFAULT_LOUDNESS_LUFS,
    DEFAULT_PEAK_CAP_DB,
    FFMPEG,
    MARKS_DIR,
    SFX_DIR,
    WORK,
    _normalize_mark_type,
    clip_work_dir,
    ffmpeg,
    ffmpeg_encode,
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
from vision_tracker import (
    TARGET_HEADROOM_PCT,
    analyze_clip_vision,
    compute_paired_split_crop_filters,
    compute_slot_crop_filter,
    find_listening_idle_window,
)

EDGE_FADE: float = 0.012  # 12ms audio fade at segment edges to prevent clicks
SPLIT_MAX_ZOOM: float = 1.08  # Safe in-panel zoom ceiling for split_h, split_v, top_pip & baked splits


def even(x: float) -> int:
    return max(2, int(round(float(x) / 2.0)) * 2)


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
    """Load vision metadata for `src`, merging any explicit `vision.json` sidecar override."""
    vis: Dict[str, Any] = {}
    if src is not None and src.is_file():
        try:
            vis = dict(analyze_clip_vision(src) or {})
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
    represents a pre-stitched / baked split clip with a known active panel; else `None`.
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
    """Normalize layout aliases into canonical layout identifiers."""
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
    quotes = re.findall(r"\"([^\"]{2,120})\"", note)
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


def resolve_auto_peers(plan: Dict[str, Any]) -> Dict[str, Any]:
    """Automatically pair `peer` from adjacent speaker turns when a segment uses
    `split_v`, `split_h`, `video_call`, `pip_call`, or `gmeet_grid` without an explicit
    `peer` dict, and auto-generate `chat` bubbles for `chat_bar` segments if omitted.
    """
    segs: List[Dict[str, Any]] = plan.get("segments") or []
    if not segs:
        return plan

    marks_by_seg: Dict[str, List[Dict[str, Any]]] = {}
    for m in plan.get("marks") or []:
        sid = m.get("segment")
        if sid:
            marks_by_seg.setdefault(str(sid), []).append(m)

    # First pass: normalize layouts and infer speakers
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

    # Assign stable default slot per speaker so speakers never flip-flop slots mid-scene
    # Coach / first speaker -> top (split_v) or left (split_h); second speaker -> bottom / right
    speaker_slot_v: Dict[str, str] = {}
    speaker_slot_h: Dict[str, str] = {}
    for idx, sp_low in enumerate(speaker_order):
        speaker_slot_v[sp_low] = "top" if idx == 0 else "bottom"
        speaker_slot_h[sp_low] = "left" if idx == 0 else "right"
    if "nova" in speaker_slot_v and "ravi" in speaker_slot_v:
        speaker_slot_v["nova"], speaker_slot_v["ravi"] = "top", "bottom"
        speaker_slot_h["nova"], speaker_slot_h["ravi"] = "left", "right"

    # Second pass: resolve auto-peers and auto-chat bubbles
    for i, s in enumerate(segs):
        lay = s["layout"]
        sp = str(s.get("speaker") or "").lower()

        if lay in ("split_v", "split_h", "video_call", "pip_call", "gmeet_grid", "top_pip"):
            resolved_slot = _resolve_active_slot(s)
            if resolved_slot:
                s["slot"] = resolved_slot
            else:
                if lay == "split_h":
                    s["slot"] = speaker_slot_h.get(sp, "left" if i % 2 == 0 else "right")
                else:
                    s["slot"] = speaker_slot_v.get(sp, "top" if i % 2 == 0 else "bottom")

            peer = s.get("peer")
            if not isinstance(peer, dict) or not peer.get("src"):
                # Search outward (i-1, i+1, i-2, i+2, ...) for the other speaker's turn
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

                # Find a clean mouth-closed listening window on peer_src
                # If peer_src is the same clip file as s["src"], exclude s's active speaking span
                avoid_spans = [(float(s.get("start", 0.0)), float(s.get("end", 5.0)))]
                if cand_seg is not s and cand_seg.get("src") == peer_src:
                    avoid_spans.append((float(cand_seg.get("start", 0.0)), float(cand_seg.get("end", 5.0))))

                idle_win = find_listening_idle_window(peer_src, speech_spans=avoid_spans)
                peer_focus = list(cand_seg.get("focus") or [0.5, 0.38])

                # If peer is in the same 2-shot frame, pick the opposite horizontal face focus
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
                merged_peer.setdefault("still_at", idle_win["still_at"])
                merged_peer.setdefault("idle_span", idle_win["idle_span"])
                merged_peer.setdefault("focus", peer_focus)
                merged_peer.setdefault("slot", opp_slot)
                s["peer"] = merged_peer
            else:
                # Ensure existing peer dict has still_at and focus filled in
                if peer.get("still_at") is None:
                    idle_win = find_listening_idle_window(peer["src"])
                    peer["still_at"] = idle_win["still_at"]
                    peer.setdefault("idle_span", idle_win["idle_span"])
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
                # Include previous turn as incoming context bubble if available
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
                cur_txt = _extract_dialogue_from_note(note_txt) or "..."
                pop_at = float(seg_marks[0].get("at", 0.15)) if seg_marks else 0.10
                pop_at = max(0.0, min(pop_at, max(0.0, (float(s["end"]) - float(s["start"])) * 0.4)))
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

    Masks out the center speaker region (`x in [0.22..0.78], y in [0.12..0.88]`) so a talking
    speaker on a locked tripod still registers as a static camera (`score < 2.8`).
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
    """Measure active-speech RMS of `src` and return a 1-number per-clip gain offset in dB
    so natural intra-sentence dynamics are preserved without double loudnorm/dynaudnorm.
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
    """Measure exposure (Y) and white balance (R/G, B/G) strictly inside the inner 60% of
    the speaker's face ROI and return a tightly clamped FFmpeg `eq` + `colorbalance` filter
    (plus optional `.cube` LUT) ONLY when explicitly requested (`color_match=True` or `lut` set).
    OFF by default so raw clip colours are never altered unintentionally.
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


def _clamp_split_zoom_spec(
    z0: float,
    z1: float,
    style: str,
    zoom_at: float,
    zoom_dur: float,
    exact_dur: float,
    max_zoom: float = SPLIT_MAX_ZOOM,
) -> Tuple[float, float, str, float, float]:
    """Clamp split-panel zoom magnitude to `[1.0, max_zoom]` (`1.12`) so active panels punch in
    cleanly without clipping ears or chins.
    """
    c0 = round(min(max_zoom, max(1.0, float(z0))), 4)
    c1 = round(min(max_zoom, max(1.0, float(z1))), 4)
    if style in ("sudden_in", "flash_zoom"):
        if abs(c1 - c0) < 0.005:
            if c1 <= 1.005:
                return 1.0, 1.0, "none", 0.0, exact_dur
            c0 = 1.0
        return c0, c1, style, zoom_at, zoom_dur
    if style == "sudden_out":
        if abs(c0 - c1) < 0.005:
            if c0 <= 1.005:
                return 1.0, 1.0, "none", 0.0, exact_dur
            c1 = 1.0
        return c0, c1, style, zoom_at, zoom_dur
    if abs(c1 - c0) < 0.005:
        if c0 > 1.005:
            return c0, c0, "static", 0.0, exact_dur
        return 1.0, 1.0, "none", 0.0, exact_dur
    return c0, c1, style, zoom_at, zoom_dur


def resolve_segment_zoom_spec(
    seg: Dict[str, Any],
    exact_dur: float,
) -> Tuple[float, float, str, float, float]:
    """Return `(z0, z1, zoom_style, zoom_at, zoom_dur)` for `seg`, applying transcript-intent
    zooms (push-ins on questions/hooks, pull-outs on resolutions/answers, and emphasis-word
    punch-ins) when no explicit zoom is set. Split layouts (`split_h`, `split_v`, `top_pip`)
    and baked-split sub-panels support active-panel zooms clamped to `[1.0, 1.12]`.
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
        return rz0, rz1, rstyle, rat, rdur

    z0_raw = seg.get("zoom")
    z1_raw = seg.get("zoom_to")
    style = str(seg.get("zoom_style") or "").strip().lower()
    zoom_at = float(seg.get("zoom_at") or 0.0)
    zoom_dur = float(seg.get("zoom_dur") or 0.0)

    if style in ("sudden_in", "punch_in"):
        z0 = float(z0_raw if z0_raw is not None else 1.0)
        z1 = float(z1_raw if z1_raw is not None else (z0 if z0 > 1.01 else 1.10))
        if abs(z1 - z0) < 0.005:
            z0, z1 = 1.0, max(1.08, z1)
        return _finish(z0, z1, "sudden_in", max(0.0, zoom_at), (zoom_dur if zoom_dur > 0.02 else 0.18))

    if style in ("sudden_out", "punch_out"):
        z0 = float(z0_raw if z0_raw is not None else 1.10)
        z1 = float(z1_raw if z1_raw is not None else 1.0)
        if abs(z0 - z1) < 0.005:
            z0, z1 = max(1.08, z0), 1.0
        return _finish(z0, z1, "sudden_out", max(0.0, zoom_at), (zoom_dur if zoom_dur > 0.02 else 0.20))

    if style == "flash_zoom":
        z0 = float(z0_raw if z0_raw is not None else 1.0)
        z1 = float(z1_raw if z1_raw is not None else (z0 if z0 > 1.01 else 1.12))
        if abs(z1 - z0) < 0.005:
            z0, z1 = 1.0, max(1.10, z1)
        return _finish(z0, z1, "flash_zoom", max(0.0, zoom_at), (zoom_dur if zoom_dur > 0.02 else 0.16))

    z0 = float(z0_raw if z0_raw is not None else 1.0)
    z1 = float(z1_raw if z1_raw is not None else z0)

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
                r"mistake|professional|fluent|confidence|ನಿಲ್ಲಿಸಿ|ಎರಡು\s*ವಾರ|ತೋರಿಸ್ತೀನಿ\s*ನೋಡಿ|ಅಭ್ಯಾಸ|ಸುಲಭ)",
                re.I,
            )
            # 1. High preference for the first rapid zoom-in (sudden_in) on opening single-character segments
            if sid_str in ("S1", "S1a", "L1", "L1a"):
                return _finish(1.0, 1.08, "sudden_in", 0.25, 0.18)
            # 2. Sudden zoom-in on Supernova callouts or strong teaching/value points inside segment word timings
            seg_words = seg.get("words") or []
            seg_start = float(seg.get("start", 0.0))
            for w in seg_words:
                wt = str(w.get("w") or "")
                rel_t = float(w.get("start", seg_start)) - seg_start
                if 0.08 <= rel_t <= exact_dur - 0.35 and (
                    callout_re.search(wt)
                    or re.search(r"\b(stop|never|wrong|don't|instead|correct|right|always|actually|remember|secret)\b", wt, re.I)
                ):
                    return _finish(1.0, 1.08, "sudden_in", round(rel_t, 3), 0.18)
            if callout_re.search(note):
                return _finish(1.0, 1.08, "sudden_in", 0.12, 0.18)
            # 3. Questions / Hooks -> smooth push-in (1.0 -> 1.06)
            if "?" in note or sid_str in ("S2", "S2a", "L2") or re.search(
                r"\b(why|what|how|when|where|who|stop\s+saying|mistake|wrong)\b", note, re.I
            ):
                return _finish(1.0, 1.06, "smooth", 0.0, exact_dur)
            # 4. Follow-up / Release beats -> rapid zoom-out (sudden_out) or smooth pull-out
            if sid_str.endswith(("b", "c")) and exact_dur <= 3.2:
                return _finish(1.08, 1.0, "sudden_out", 0.10, 0.20)
            if re.search(r"\[(?:right|correct)\]|\b(perfect|correct|exactly|instead|better|right\s+way|means|install|download)\b", note, re.I):
                return _finish(1.06, 1.0, "smooth", 0.0, exact_dur)
            # 5. Default by segment duration
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
) -> List[str]:
    """Build a supersampled sub-pixel `zoompan` filter chain free of 2px stair-stepping jitter."""
    fx1_v = fx if fx1 is None else max(0.05, min(0.95, float(fx1)))
    fy1_v = fy if fy1 is None else max(0.08, min(0.85, float(fy1)))
    has_pan = math.hypot(fx1_v - fx, fy1_v - fy) > 0.04

    if abs(z0 - 1.0) <= 0.004 and abs(z1 - 1.0) <= 0.004 and zoom_style in ("none", "static", "") and not has_pan:
        return []

    # Fast zero-heat path for static punch-in zooms (e.g. zoom=1.08 with no zoom_to and no pan):
    # uses direct lanczos scale + crop instead of per-frame 4K zoompan evaluation.
    if abs(z1 - z0) <= 0.004 and zoom_style in ("none", "static", "") and z0 > 1.004 and not has_pan:
        zw = even(slot_w * z0)
        zh = even(slot_h * z0)
        return [
            f"scale={zw}:{zh}:flags=lanczos",
            f"crop={slot_w}:{slot_h}:x='(iw-{slot_w})*{fx:.4f}':y='(ih-{slot_h})*{fy:.4f}'",
        ]

    total_frames = max(2, int(round(exact_dur * fps)))
    n_minus_1 = max(1, total_frames - 1)

    ss_factor = 2
    sw_super = even(slot_w * ss_factor)
    sh_super = even(slot_h * ss_factor)

    if zoom_style in ("sudden_in", "sudden_out", "flash_zoom"):
        t0 = max(0.0, min(exact_dur - 0.05, float(zoom_at)))
        d_snap = max(0.08, min(1.20, float(zoom_dur or 0.18)))
        u_expr = f"clip((on/{fps:.4f}-{t0:.4f})/{d_snap:.4f},0,1)"
        ease_expr = f"(1-pow(1-{u_expr},3))"
        drift_sign = "+" if z1 >= z0 else "-"
        post_u = f"clip((on/{fps:.4f}-{t0 + d_snap:.4f})/{max(0.2, exact_dur - t0 - d_snap):.4f},0,1)"
        z_expr = f"({z0:.4f}+({z1 - z0:+.4f})*{ease_expr}{drift_sign}0.015*{post_u})"
    else:
        p_expr = f"(1-cos(PI*min(on/{n_minus_1},1)))/2"
        z_expr = f"({z0:.4f}+({z1 - z0:+.4f})*{p_expr})"

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
    if zoom_style == "flash_zoom":
        t0 = max(0.0, float(zoom_at))
        pulse = f"max(0,1-abs((t-{t0 + 0.04:.4f})/0.09))"
        filters.append(f"eq=brightness='0.12*{pulse}':contrast='1+0.08*{pulse}':eval=frame")
    return filters


def banner_stack_top(seg: Dict[str, Any]) -> float:
    """Where the "Stop saying" stack starts, as a fraction of the frame height: just below the measured face (with the segment's own zoom),
    never on it. Falls back to the standard chest-level position when the face cannot be measured."""
    try:
        import vision_tracker

        vis = vision_tracker.analyze_clip_vision(resolve(seg["src"]))
        bb = vis.get("bbox_norm")
        if bb and len(bb) >= 4:
            fy = float(seg["focus"][1]) if isinstance(seg.get("focus"), (list, tuple)) and len(seg["focus"]) >= 2 else float((vis.get("focus") or [0.5, 0.4])[1])
            zmax = max(float(seg.get("zoom") or 1.0), float(seg.get("zoom_to") or 1.0))
            chin = fy + (float(bb[3]) - fy) * zmax          # the chin moves down when the picture is zoomed in around the focus point
            return round(min(0.66, max(0.445, chin + 0.03)), 3)
    except Exception:
        pass
    return 0.445


def parse_banners(seg: Dict[str, Any], exact: float) -> List[Tuple[str, float, float, bool]]:
    """The segment's banners as (text, start, duration, is_header), times relative to the cut. A banner `header:<text>` is a title above the
    stack that is never struck through."""
    banners = seg.get("banners") or seg.get("stop_saying") or []
    if isinstance(banners, (str, dict)):
        banners = [banners]
    out: List[Tuple[str, float, float, bool]] = []
    for b_idx, b_item in enumerate(banners):
        if isinstance(b_item, str):
            b_text, b_at, b_dur = b_item, round(0.15 + b_idx * 0.65, 3), exact
        elif isinstance(b_item, dict):
            b_text = str(b_item.get("text") or b_item.get("phrase") or "STOP SAYING")
            b_at = float(b_item.get("at", round(0.15 + b_idx * 0.65, 3)))
            b_dur = float(b_item.get("duration", max(0.4, exact - b_at)))
        else:
            continue
        is_header = b_text.lower().startswith("header:")
        out.append((b_text[7:].strip() if is_header else b_text, b_at, b_dur, is_header))
    return out


def banner_stack_rows(seg: Dict[str, Any], parsed: List[Tuple[str, float, float, bool]]) -> Tuple[float, float]:
    """(header row, first phrase row) as fractions of the frame height: the stack starts below the face; phrases follow the header."""
    stack_top = banner_stack_top(seg)
    has_header = any(pb[3] for pb in parsed)
    return stack_top, stack_top + (0.075 if has_header else max(0.0, 0.52 - 0.445))


def banner_boxes(seg: Dict[str, Any], W: int, H: int, exact: float) -> List[Dict[str, Any]]:
    """Where each banner pill sits on the finished frame, in pixels, and when (seconds relative to the cut). Same geometry as the renderer."""
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
        over = 36 * scale                                        # transparent margin on each side of the pill
        pill_w, pill_h = max(10.0, cw * scale - 2 * over), ch * scale
        x0 = (W - cw * scale) / 2.0 + over
        if is_header:
            y0 = int(round(H * stack_top))
        else:
            y0 = int(round(H * (phrase_base + phrase_idx * 0.068)))
            phrase_idx += 1
        boxes.append({"text": text, "header": is_header, "x0": x0, "y0": float(y0), "x1": x0 + pill_w, "y1": y0 + pill_h,
                      "t0": b_at, "t1": min(exact, b_at + b_dur)})
    return boxes


def build_segment_cache_key(
    seg: Dict[str, Any],
    plan: Optional[Dict[str, Any]],
    W: int,
    H: int,
    fps: int,
    draft: bool,
) -> str:
    """Compute a deterministic 16-char MD5 hex cache key for a segment and its layout."""
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
        }

    eff_fps = int(fps or 24)
    seg_dur = max(0.08, float(seg["end"]) - float(seg["start"]))
    exact_dur = max(1, round(seg_dur * eff_fps)) / float(eff_fps)
    rz0, rz1, rz_style, rz_at, rz_dur = resolve_segment_zoom_spec(seg, exact_dur)
    active_panel = _resolve_active_slot(seg)
    baked_sig = None
    if lay == "single" and (abs(rz0 - 1.0) > 0.004 or abs(rz1 - 1.0) > 0.004 or rz_style == "flash_zoom"):
        src_p = resolve(seg["src"]) if seg.get("src") else None
        bspec = _resolve_baked_split_spec(seg, src_p)
        if bspec is not None:
            baked_sig = {
                "kind": bspec["kind"],
                "active": bspec["active"],
                "seam_frac": bspec["seam_frac"],
                "seam_band_frac": bspec["seam_band_frac"],
            }

    plan_loudness = float((plan or {}).get("audio", {}).get("loudness", DEFAULT_LOUDNESS_LUFS))
    payload = {
        "v": 14,
        "src": _file_mtime_sig(seg["src"]),
        "start": round(float(seg["start"]), 4),
        "end": round(float(seg["end"]), 4),
        "layout": lay,
        "slot": seg.get("slot"),
        "active": active_panel,
        "framing": seg.get("framing"),
        "divider": seg.get("divider"),
        "zoom": seg.get("zoom"),
        "zoom_to": seg.get("zoom_to"),
        "zoom_style": seg.get("zoom_style"),
        "zoom_at": seg.get("zoom_at"),
        "zoom_dur": seg.get("zoom_dur"),
        "resolved_zoom": [round(rz0, 4), round(rz1, 4), rz_style, round(rz_at, 4), round(rz_dur, 4)],
        "baked_split": baked_sig,
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
) -> str:
    """Build an FFmpeg video filter chain that scales and crops `input_label` into
    `(slot_w, slot_h)` with eye-anchored headroom (`14%`) and sub-pixel supersampled zoom.
    """
    slot_w, slot_h = even(slot_w), even(slot_h)
    fx = max(0.05, min(0.95, float(focus[0]) if len(focus) >= 1 else 0.5))
    fy = max(0.08, min(0.85, float(focus[1]) if len(focus) >= 2 else 0.38))
    fx1 = float(focus_end[0]) if focus_end and len(focus_end) >= 1 else fx
    fy1 = float(focus_end[1]) if focus_end and len(focus_end) >= 2 else fy
    has_pan = math.hypot(fx1 - fx, fy1 - fy) > 0.04
    is_static_1x = (
        abs(z0 - 1.0) <= 0.004
        and abs(z1 - 1.0) <= 0.004
        and zoom_style in ("none", "static", "")
        and not has_pan
    )

    if exact and abs((src_w / max(1, src_h)) / (slot_w / max(1, slot_h)) - 1.0) < 0.03:
        if is_static_1x:
            cf_part = f",{color_filter}" if color_filter else ""
            return f"{input_label}setpts=PTS-STARTPTS,fps={fps},scale={slot_w}:{slot_h}:flags=lanczos{cf_part},setsar=1,format=yuv420p{output_label}"
        chain = ["setpts=PTS-STARTPTS", f"fps={fps}", f"scale={slot_w}:{slot_h}:flags=lanczos"]
        z_fx, z_fy, z_fx1, z_fy1 = fx, fy, fx1, fy1
    else:
        chain = ["setpts=PTS-STARTPTS", f"fps={fps}"]
        if prebuilt_crop_filter:
            chain.append(prebuilt_crop_filter)
            z_fx, z_fy, z_fx1, z_fy1 = fx, fy, fx1, fy1
        else:
            crop_info = compute_slot_crop_filter(
                src_w=src_w,
                src_h=src_h,
                slot_w=slot_w,
                slot_h=slot_h,
                bbox_norm=bbox_norm,
                focus=[fx, fy],
                headroom_pct=headroom_pct,
            )
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
    if out_png.is_file() and out_png.stat().st_size > 100:
        return out_png

    info = probe(peer_src)
    dur = float(info.get("duration") or 5.0)
    clamped_t = max(0.0, min(float(still_at), max(0.0, dur - 0.08)))
    ffmpeg("-ss", f"{clamped_t:.3f}", "-i", peer_src, "-frames:v", "1", out_png)
    return out_png


def build_segment_ffmpeg_args(
    seg: Dict[str, Any],
    plan: Optional[Dict[str, Any]],
    W: int,
    H: int,
    fps: int,
    out: Union[str, Path],
    draft: bool = False,
) -> Tuple[List[Any], float, int]:
    """Construct the complete FFmpeg argument list for rendering `seg` in any of the 5 layouts.

    Returns `(ffmpeg_args, exact_duration_sec, frames_count)`.
    """
    plan = plan or {}
    W, H = even(W), even(H)
    fps = int(fps or 24)
    dur = max(0.08, float(seg["end"]) - float(seg["start"]))
    frames = max(1, round(dur * fps))
    exact = frames / float(fps)

    lay = normalize_layout_name(seg.get("layout"))
    z0, z1, zoom_style, zoom_at, zoom_dur = resolve_segment_zoom_spec(seg, exact)
    focus = list(seg.get("focus") or [0.5, 0.38])
    focus_end = list(seg.get("focus_end") or focus)
    fx, fy = float(focus[0]), float(focus[1])
    fx1, fy1 = float(focus_end[0]), float(focus_end[1])

    src = resolve(seg["src"])
    src_info = probe(src) if src.is_file() else {"width": W, "height": H, "has_audio": True, "duration": dur + 1.0}
    src_w = int(src_info.get("width") or W)
    src_h = int(src_info.get("height") or H)
    has_audio = bool(src_info.get("has_audio", True))
    color_filter = compute_face_color_match_filter(seg, plan)

    plan_loud = float((plan or {}).get("audio", {}).get("loudness", DEFAULT_LOUDNESS_LUFS))
    seg_loud = float(seg.get("loudness") if seg.get("loudness") is not None else plan_loud)
    seg_gain_db = float(seg.get("gain_db") or 0.0)
    clip_gain_db = _measure_clip_rms_gain_db(src) if has_audio else 0.0
    total_gain_db = round(clip_gain_db + seg_gain_db, 2)

    af_parts = [
        f"atrim=0:{exact:.6f}",
        "asetpts=PTS-STARTPTS",
        "highpass=f=70",
    ]
    if abs(total_gain_db) >= 0.05:
        af_parts.append(f"volume={total_gain_db:+.2f}dB")
    af_parts.extend([
        "alimiter=limit=0.891:level=false",
        "aresample=48000",
        "aformat=channel_layouts=stereo",
        "apad",
        f"atrim=0:{exact:.6f}",
        "asetpts=PTS-STARTPTS",
        f"afade=t=in:d={EDGE_FADE}",
        f"afade=t=out:st={max(0.0, exact - EDGE_FADE):.6f}:d={EDGE_FADE}",
    ])
    af_chain = ",".join(af_parts)

    inputs: List[Any] = ["-ss", f"{float(seg['start']):.3f}", "-t", f"{dur + 0.5:.3f}", "-i", src]
    audio_input_idx: Optional[int] = 0 if has_audio else None
    next_idx = 1

    fc: List[str] = []
    v_curr = "[vbase]"

    # ------------------------------------------------------------------
    # Layout 1: SINGLE (Full-screen or Pre-Stitched Baked Split Sub-Panel)
    # ------------------------------------------------------------------
    if lay == "single":
        has_active_zoom = (
            abs(z0 - 1.0) > 0.004
            or abs(z1 - 1.0) > 0.004
            or zoom_style == "flash_zoom"
        )
        baked_spec = _resolve_baked_split_spec(seg, src) if has_active_zoom else None
        if baked_spec is not None:
            bz0, bz1, b_style, b_at, b_dur = _clamp_split_zoom_spec(
                z0, z1, zoom_style, zoom_at, zoom_dur, exact
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
                    f"[0:v]setpts=PTS-STARTPTS,fps={fps},"
                    f"scale={W}:{H}:force_original_aspect_ratio=increase:flags=lanczos,"
                    f"crop={W}:{H}:x='(iw-{W})*0.5':y='(ih-{H})*0.5',split=2[vb_top_in][vb_bot_in]"
                )
                top_ops = [f"crop={W}:{top_h}:0:0"]
                bot_ops = [f"crop={W}:{bot_h}:0:{top_h}"]
                if b_active == "top":
                    top_ops.extend(
                        _build_subpixel_zoom_filter(
                            slot_w=W, slot_h=top_h, fps=fps, exact_dur=exact,
                            fx=fx_p, fy=fy_p, z0=bz0, z1=bz1,
                            zoom_style=b_style, zoom_at=b_at, zoom_dur=b_dur,
                        )
                    )
                else:
                    bot_ops.extend(
                        _build_subpixel_zoom_filter(
                            slot_w=W, slot_h=bot_h, fps=fps, exact_dur=exact,
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
                fc.append(f"[vb_top][vb_bot]{','.join(post_ops)}{v_curr}")
            else:
                split_frac = (b_lo if b_active == "left" else b_hi) if (b_hi > b_lo + 0.0005) else sf
                left_w = max(16, min(W - 16, even(W * split_frac)))
                right_w = W - left_w
                fc.append(
                    f"[0:v]setpts=PTS-STARTPTS,fps={fps},"
                    f"scale={W}:{H}:force_original_aspect_ratio=increase:flags=lanczos,"
                    f"crop={W}:{H}:x='(iw-{W})*0.5':y='(ih-{H})*0.5',split=2[vb_left_in][vb_right_in]"
                )
                left_ops = [f"crop={left_w}:{H}:0:0"]
                right_ops = [f"crop={right_w}:{H}:{left_w}:0"]
                if b_active == "left":
                    left_ops.extend(
                        _build_subpixel_zoom_filter(
                            slot_w=left_w, slot_h=H, fps=fps, exact_dur=exact,
                            fx=fx_p, fy=fy_p, z0=bz0, z1=bz1,
                            zoom_style=b_style, zoom_at=b_at, zoom_dur=b_dur,
                        )
                    )
                else:
                    right_ops.extend(
                        _build_subpixel_zoom_filter(
                            slot_w=right_w, slot_h=H, fps=fps, exact_dur=exact,
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
                fc.append(f"[vb_left][vb_right]{','.join(post_ops)}{v_curr}")
        else:
            vf = [
                "setpts=PTS-STARTPTS",
                f"fps={fps}",
                f"scale={W}:{H}:force_original_aspect_ratio=increase:flags=lanczos",
                f"crop={W}:{H}:x='(iw-{W})*{fx:.4f}':y='(ih-{H})*{fy:.4f}'",
            ]
            vf.extend(
                _build_subpixel_zoom_filter(
                    slot_w=W,
                    slot_h=H,
                    fps=fps,
                    exact_dur=exact,
                    fx=fx,
                    fy=fy,
                    z0=z0,
                    z1=z1,
                    zoom_style=zoom_style,
                    zoom_at=zoom_at,
                    zoom_dur=zoom_dur,
                    fx1=fx1,
                    fy1=fy1,
                )
            )
            if color_filter:
                vf.append(color_filter)
            vf.extend(["setsar=1", "format=yuv420p"])
            fc.append(f"[0:v]{','.join(vf)}{v_curr}")

    # ------------------------------------------------------------------
    # Layout 2 & 3: SPLIT_V (top_bottom) & SPLIT_H (left_right)
    # ------------------------------------------------------------------
    elif lay in ("split_v", "split_h"):
        peer = seg.get("peer") or {}
        peer_src = resolve(peer.get("src") or seg["src"])
        peer_info = probe(peer_src) if peer_src.is_file() else {"width": src_w, "height": src_h, "duration": 6.0}
        peer_w = int(peer_info.get("width") or src_w)
        peer_h = int(peer_info.get("height") or src_h)
        peer_focus = list(peer.get("focus") or [0.5, 0.38])
        still_at = float(peer.get("still_at") if peer.get("still_at") is not None else 0.5)
        idle_span = peer.get("idle_span")

        # Always use live moving listening video when peer_src is a video file (never static screenshot unless mode == "still")
        peer_idx = next_idx
        next_idx += 1
        peer_loop_filter = ""
        peer_dur = float(peer_info.get("duration") or 6.0)
        if peer_src.is_file() and peer.get("mode") == "live":
            # Both panels play their own clip in sync; audio still comes from the main clip.
            inputs.extend(["-ss", f"{float(peer.get('start', 0.0)):.3f}", "-t", f"{dur + 0.5:.3f}", "-i", peer_src])
        elif peer_src.is_file() and peer.get("mode", "loop") != "still":
            if isinstance(idle_span, (list, tuple)) and len(idle_span) >= 2 and (float(idle_span[1]) - float(idle_span[0])) >= 0.30:
                s0 = max(0.0, float(idle_span[0]))
                s1 = float(idle_span[1])
                s_dur = max(0.30, s1 - s0)
            else:
                # Dedicated listening clip: play from 0.0 across full clip duration and wrap seamlessly
                s0 = float(peer.get("start", 0.0) or 0.0) % max(0.5, peer_dur)
                s1 = peer_dur
                s_dur = max(0.30, peer_dur - s0)

            is_full_listening_clip = (s0 <= 0.15 and abs(s1 - peer_dur) <= 0.25) or ("listen" in peer_src.stem.lower() or "idle" in peer_src.stem.lower())
            if s_dur >= exact - 0.15:
                inputs.extend(["-ss", f"{s0:.3f}", "-t", f"{exact + 0.5:.3f}", "-i", peer_src])
            elif is_full_listening_clip:
                # Dedicated listening video clip shorter than segment: native demuxer -stream_loop -1 + filter trim
                inputs.extend(["-stream_loop", "-1", "-ss", f"{s0:.3f}", "-i", peer_src])
                peer_loop_filter = f"trim=0:{exact:.4f},setpts=PTS-STARTPTS"
            else:
                # Sub-span inside a combined talking+listening clip: loop only [s0, s1] so mouth never opens
                inputs.extend(["-ss", f"{s0:.3f}", "-t", f"{s_dur:.3f}", "-i", peer_src])
                loop_frames = max(2, int(round(s_dur * fps)))
                peer_loop_filter = f"fps={fps},loop=loop=-1:size={loop_frames}:start=0,setpts=N/({fps}*TB),trim=0:{exact:.4f},setpts=PTS-STARTPTS"
        elif peer_src.is_file():
            still_png = _extract_peer_still_png(peer_src, still_at)
            inputs.extend(["-loop", "1", "-framerate", str(fps), "-t", f"{exact:.4f}", "-i", still_png])
        else:
            inputs.extend(["-ss", f"{still_at:.3f}", "-t", f"{exact:.4f}", "-i", peer_src])

        peer_in_label = f"[{peer_idx}:v]"
        if peer_loop_filter:
            fc.append(f"[{peer_idx}:v]{peer_loop_filter}[peer_loop_in]")
            peer_in_label = "[peer_loop_in]"

        has_explicit_pan = seg.get("focus_end") is not None and math.hypot(fx1 - fx, fy1 - fy) > 0.04

        if lay == "split_v":
            slot_w = W
            slot_h_top = even(H // 2)
            slot_h_bot = H - slot_h_top
            active_top = _resolve_active_slot(seg, default="top") in ("top", "slot_a", "a", "0")
            live_h = slot_h_top if active_top else slot_h_bot
            peer_h_slot = slot_h_bot if active_top else slot_h_top

            live_crop, peer_crop, live_slot_focus, peer_slot_focus = compute_paired_split_crop_filters(
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
            )
            live_slot_focus_end = (
                [
                    min(0.80, max(0.20, live_slot_focus[0] + (fx1 - fx))),
                    min(0.65, max(0.20, live_slot_focus[1] + (fy1 - fy))),
                ]
                if has_explicit_pan
                else None
            )
            fc.append(
                _build_slot_video_chain(
                    "[0:v]", "[live_slot]", src_w, src_h, slot_w, live_h, fps, exact,
                    live_slot_focus, z0, z1,
                    zoom_style=zoom_style, zoom_at=zoom_at, zoom_dur=zoom_dur,
                    focus_end=live_slot_focus_end,
                    color_filter=color_filter, prebuilt_crop_filter=live_crop["filter"],
                )
            )
            fc.append(
                _build_slot_video_chain(
                    peer_in_label, "[peer_slot]", peer_w, peer_h, slot_w, peer_h_slot, fps, exact,
                    peer_slot_focus, 1.0, 1.0,
                    zoom_style="none",
                    prebuilt_crop_filter=peer_crop["filter"],
                )
            )

            stack_order = "[live_slot][peer_slot]" if active_top else "[peer_slot][live_slot]"
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
            live_crop, peer_crop, live_slot_focus, peer_slot_focus = compute_paired_split_crop_filters(
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
            )
            live_slot_focus_end = (
                [
                    min(0.80, max(0.20, live_slot_focus[0] + (fx1 - fx))),
                    min(0.65, max(0.20, live_slot_focus[1] + (fy1 - fy))),
                ]
                if has_explicit_pan
                else None
            )
            fc.append(
                _build_slot_video_chain(
                    "[0:v]", "[live_slot]", src_w, src_h, live_w, slot_h, fps, exact,
                    live_slot_focus, z0, z1,
                    exact=exact_fit,
                    zoom_style=zoom_style, zoom_at=zoom_at, zoom_dur=zoom_dur,
                    focus_end=live_slot_focus_end,
                    color_filter=color_filter, prebuilt_crop_filter=live_crop["filter"],
                )
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

            stack_order = "[live_slot][peer_slot]" if active_left else "[peer_slot][live_slot]"
            dv = seg.get("divider")
            if isinstance(dv, dict):   # measured divider: thin line on the left slot's right edge
                dw = max(2, round(W * float(dv.get("w_frac", 0.004))))
                dx = round(W * float(dv.get("x_frac", 0.5)))
                divider = f"drawbox=x={dx}:y=0:w={dw}:h={H}:color=0x{dv.get('color', 'FFFFFF')}@{dv.get('alpha', 1.0)}:t=fill,"
            else:
                divider = f"drawbox=x={slot_w_left - 2}:y=0:w=4:h={H}:color=white@0.85:t=fill,"
            fc.append(f"{stack_order}hstack=inputs=2:shortest=1,{divider}format=yuv420p{v_curr}")

    # ------------------------------------------------------------------
    # Layout 4: CHAT_BAR / TEXTING (floating_overlay or docked)
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
            fc.append(
                _build_slot_video_chain(
                    "[0:v]", "[v_sp]", src_w, src_h, W, vid_h, fps, exact, [fx, fy], z0, z1,
                    zoom_style=zoom_style, zoom_at=zoom_at, zoom_dur=zoom_dur,
                    focus_end=[fx1, fy1], color_filter=color_filter,
                )
            )
            fc.append(f"[v_sp]pad={W}:{H}:0:{dock_h}:color=#0f172a[v_padded]")
            fc.append(f"[{chat_idx}:v]scale={W}:{H},format=rgba[chat_ov]")
            fc.append(f"[v_padded][chat_ov]overlay=0:0:eof_action=pass,format=yuv420p{v_curr}")
        else:
            # Floating overlay: speaker fills W x H, chat bar overlays top with smooth fade-in
            fc.append(
                _build_slot_video_chain(
                    "[0:v]", "[v_sp]", src_w, src_h, W, H, fps, exact, [fx, fy], z0, z1,
                    zoom_style=zoom_style, zoom_at=zoom_at, zoom_dur=zoom_dur,
                    focus_end=[fx1, fy1], color_filter=color_filter,
                )
            )
            fc.append(f"[{chat_idx}:v]scale={W}:{H},format=rgba,fade=t=in:st=0:d=0.15:alpha=1[chat_ov]")
            fc.append(f"[v_sp][chat_ov]overlay=0:0:eof_action=pass,format=yuv420p{v_curr}")

    # ------------------------------------------------------------------
    # Layout 5: VIDEO_CALL / PIP_CALL / GMEET_GRID
    # ------------------------------------------------------------------
    elif lay in ("video_call", "pip_call", "gmeet_grid"):
        peer = seg.get("peer") or {}
        peer_src = resolve(peer.get("src") or seg["src"])
        peer_info = probe(peer_src) if peer_src.is_file() else {"width": src_w, "height": src_h, "duration": 6.0}
        peer_w = int(peer_info.get("width") or src_w)
        peer_h = int(peer_info.get("height") or src_h)
        peer_focus = list(peer.get("focus") or [0.5, 0.38])
        still_at = float(peer.get("still_at") if peer.get("still_at") is not None else 0.5)

        peer_idx = next_idx
        next_idx += 1
        if peer_src.is_file():
            still_png = _extract_peer_still_png(peer_src, still_at)
            inputs.extend(["-loop", "1", "-framerate", str(fps), "-t", f"{exact:.4f}", "-i", still_png])
        else:
            inputs.extend(["-ss", f"{still_at:.3f}", "-t", f"{exact:.4f}", "-i", peer_src])

        pip_shape = str(seg.get("pip_shape") or peer.get("shape") or "rounded")
        variant = "gmeet_grid" if lay == "gmeet_grid" else "pip_call"
        geom = get_video_call_geometry(W, H, variant=variant, pip_shape=pip_shape)

        caller_name = _speaker_display_name(str(seg.get("speaker") or "nova"), plan)
        call_sec = int(round(float(seg.get("_timeline_start", 42.0))))
        timer_str = f"{call_sec // 60:02d}:{call_sec % 60:02d}"

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
            fc.append(
                _build_slot_video_chain(
                    "[0:v]", "[v_live]", src_w, src_h, tw, th, fps, exact, [fx, fy], z0, z1,
                    zoom_style=zoom_style, zoom_at=zoom_at, zoom_dur=zoom_dur,
                    focus_end=[fx1, fy1], color_filter=color_filter,
                )
            )
            fc.append(
                _build_slot_video_chain(
                    f"[{peer_idx}:v]", "[v_peer]", peer_w, peer_h, tw, th, fps, exact, peer_focus, 1.0, 1.0,
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

            fc.append(
                _build_slot_video_chain(
                    "[0:v]", "[v_main]", src_w, src_h, W, H, fps, exact, [fx, fy], z0, z1,
                    zoom_style=zoom_style, zoom_at=zoom_at, zoom_dur=zoom_dur,
                    focus_end=[fx1, fy1], color_filter=color_filter,
                )
            )
            fc.append(
                _build_slot_video_chain(
                    f"[{peer_idx}:v]", "[v_pip_raw]", peer_w, peer_h, pw, ph, fps, exact, peer_focus, 1.0, 1.0,
                    zoom_style="none",
                )
            )
            fc.append(f"[{mask_idx}:v]scale={pw}:{ph},format=gray[pip_mask]")
            fc.append("[v_pip_raw]format=yuva420p[v_pip_a];[v_pip_a][pip_mask]alphamerge[v_pip]")
            fc.append(f"[v_main][v_pip]overlay=x={px}:y={py}:eof_action=pass[v_with_pip]")
            fc.append(f"[{chrome_idx}:v]scale={W}:{H},format=rgba[chrome]")
            fc.append(f"[v_with_pip][chrome]overlay=0:0:eof_action=pass,format=yuv420p{v_curr}")

    # ------------------------------------------------------------------
    # Layout 6: TOP_PIP (Clean Top-Center Floating Rounded Rectangle Window)
    # ------------------------------------------------------------------
    elif lay == "top_pip":
        peer = seg.get("peer") or {}
        peer_src = resolve(peer.get("src") or seg["src"])
        peer_info = probe(peer_src) if peer_src.is_file() else {"width": src_w, "height": src_h, "duration": 6.0}
        peer_w = int(peer_info.get("width") or src_w)
        peer_h = int(peer_info.get("height") or src_h)
        peer_focus = list(peer.get("focus") or [0.5, 0.38])
        still_at = float(peer.get("still_at") if peer.get("still_at") is not None else 0.5)
        idle_span = peer.get("idle_span")

        peer_idx = next_idx
        next_idx += 1
        peer_loop_filter = ""
        peer_dur = float(peer_info.get("duration") or 6.0)
        if peer_src.is_file() and peer.get("mode") == "live":
            inputs.extend(["-ss", f"{float(peer.get('start', 0.0)):.3f}", "-t", f"{dur + 0.5:.3f}", "-i", peer_src])
        elif peer_src.is_file() and peer.get("mode", "loop") != "still":
            if isinstance(idle_span, (list, tuple)) and len(idle_span) >= 2 and (float(idle_span[1]) - float(idle_span[0])) >= 0.30:
                s0 = max(0.0, float(idle_span[0]))
                s1 = float(idle_span[1])
                s_dur = max(0.30, s1 - s0)
            else:
                s0 = float(peer.get("start", 0.0) or 0.0) % max(0.5, peer_dur)
                s1 = peer_dur
                s_dur = max(0.30, peer_dur - s0)

            is_full_listening_clip = (s0 <= 0.15 and abs(s1 - peer_dur) <= 0.25) or (
                "listen" in peer_src.stem.lower() or "idle" in peer_src.stem.lower() or "extra" in peer_src.stem.lower()
            )
            if s_dur >= exact - 0.15:
                inputs.extend(["-ss", f"{s0:.3f}", "-t", f"{exact + 0.5:.3f}", "-i", peer_src])
            elif is_full_listening_clip:
                inputs.extend(["-stream_loop", "-1", "-ss", f"{s0:.3f}", "-i", peer_src])
                peer_loop_filter = f"trim=0:{exact:.4f},setpts=PTS-STARTPTS"
            else:
                inputs.extend(["-ss", f"{s0:.3f}", "-t", f"{s_dur:.3f}", "-i", peer_src])
                loop_frames = max(2, int(round(s_dur * fps)))
                peer_loop_filter = f"fps={fps},loop=loop=-1:size={loop_frames}:start=0,setpts=N/({fps}*TB),trim=0:{exact:.4f},setpts=PTS-STARTPTS"
        elif peer_src.is_file():
            still_png = _extract_peer_still_png(peer_src, still_at)
            inputs.extend(["-loop", "1", "-framerate", str(fps), "-t", f"{exact:.4f}", "-i", still_png])
        else:
            inputs.extend(["-ss", f"{still_at:.3f}", "-t", f"{exact:.4f}", "-i", peer_src])

        peer_in_label = f"[{peer_idx}:v]"
        if peer_loop_filter:
            fc.append(f"[{peer_idx}:v]{peer_loop_filter}[peer_loop_in]")
            peer_in_label = "[peer_loop_in]"

        pw = even(W * 0.6944)
        ph = even(H * 0.2188)
        px = (W - pw) // 2
        py = int(round(H * 0.0104))
        prad = max(12, int(round(36 * (W / 1080.0))))

        mask_png = render_pip_mask_png(pw, ph, radius=prad, shape="rounded")
        mask_idx = next_idx
        next_idx += 1
        inputs.extend(["-loop", "1", "-framerate", str(fps), "-t", f"{exact:.4f}", "-i", mask_png])

        active_top = _resolve_active_slot(seg, default="top") in ("top", "slot_a", "a", "0", "pip")
        if active_top:
            pip_vis = analyze_clip_vision(src) if src.is_file() else {}
            bg_vis = analyze_clip_vision(peer_src) if peer_src.is_file() else {}
            fc.append(
                _build_slot_video_chain(
                    peer_in_label, "[v_bg]", peer_w, peer_h, W, H, fps, exact, peer_focus, 1.0, 1.0,
                    zoom_style="none",
                    bbox_norm=bg_vis.get("bbox_norm"), headroom_pct=25.8,
                )
            )
            fc.append(
                _build_slot_video_chain(
                    "[0:v]", "[v_pip_raw]", src_w, src_h, pw, ph, fps, exact, [fx, fy], z0, z1,
                    zoom_style=zoom_style, zoom_at=zoom_at, zoom_dur=zoom_dur,
                    focus_end=[fx1, fy1],
                    bbox_norm=pip_vis.get("bbox_norm"), color_filter=color_filter,
                )
            )
        else:
            bg_vis = analyze_clip_vision(src) if src.is_file() else {}
            pip_vis = analyze_clip_vision(peer_src) if peer_src.is_file() else {}
            fc.append(
                _build_slot_video_chain(
                    "[0:v]", "[v_bg]", src_w, src_h, W, H, fps, exact, [fx, fy], z0, z1,
                    zoom_style=zoom_style, zoom_at=zoom_at, zoom_dur=zoom_dur,
                    focus_end=[fx1, fy1],
                    bbox_norm=bg_vis.get("bbox_norm"), color_filter=color_filter, headroom_pct=25.8,
                )
            )
            fc.append(
                _build_slot_video_chain(
                    peer_in_label, "[v_pip_raw]", peer_w, peer_h, pw, ph, fps, exact, peer_focus, 1.0, 1.0,
                    zoom_style="none",
                    bbox_norm=pip_vis.get("bbox_norm"),
                )
            )

        fc.append(f"[{mask_idx}:v]scale={pw}:{ph},format=gray[pip_mask]")
        fc.append("[v_pip_raw]format=yuva420p[v_pip_a];[v_pip_a][pip_mask]alphamerge[v_pip]")
        fc.append(f"[v_bg][v_pip]overlay=x={px}:y={py}:eof_action=pass,format=yuv420p{v_curr}")

    # ------------------------------------------------------------------
    # Optional "Stop Saying" Hook Banners on any segment (21132 3-stack chest-centered style)
    # ------------------------------------------------------------------
    parsed_banners = parse_banners(seg, exact)
    if parsed_banners:
        stack_top, phrase_base = banner_stack_rows(seg, parsed_banners)
        phrase_idx = 0
        for b_idx, (b_text, b_at, b_dur, is_header) in enumerate(parsed_banners):
            b_end = min(exact, b_at + b_dur)
            bx_expr = f"trunc(({W}-overlay_w)/2)"
            if is_header:
                # Static "STOP SAYING X" title one row above the stacked phrases; fades out with them
                in_h = next_idx
                next_idx += 1
                inputs.extend(["-loop", "1", "-framerate", str(fps), "-t", f"{exact:.4f}", "-i", render_stop_saying_banner_png(b_text, with_strike=False, cross_icon=True)])
                hy = int(round(H * stack_top))
                fc.append(
                    f"[{in_h}:v]scale=w='trunc(iw*{W}/1080/2)*2':h=-1,format=rgba,"
                    f"fade=t=in:st={b_at:.3f}:d=0.10:alpha=1,fade=t=out:st={max(b_at + 0.1, b_end - 0.18):.3f}:d=0.18:alpha=1[bn_h{b_idx}]"
                )
                fc.append(f"{v_curr}[bn_h{b_idx}]overlay=x={bx_expr}:y={hy}:enable='between(t,{b_at:.3f},{b_end:.3f})':eof_action=pass,format=yuv420p[vb{b_idx}h]")
                v_curr = f"[vb{b_idx}h]"
                continue
            strike_at = min(b_end - 0.05, b_at + 0.16)
            # Phase 1: Un-struck red pill pops in when phrase begins (b_at -> strike_at)
            # Phase 2: Struck-through red pill holds from strike_at -> b_end (with fade-out)
            png_plain = render_stop_saying_banner_png(b_text, with_strike=False)
            png_struck = render_stop_saying_banner_png(b_text, with_strike=True)
            in_plain = next_idx
            in_struck = next_idx + 1
            next_idx += 2
            inputs.extend(["-loop", "1", "-framerate", str(fps), "-t", f"{exact:.4f}", "-i", png_plain])
            inputs.extend(["-loop", "1", "-framerate", str(fps), "-t", f"{exact:.4f}", "-i", png_struck])

            # Centered horizontally (x=0.50*W), stacked at lower-chest level (y=0.52*H + idx*0.068*H)
            by_expr = int(round(H * (phrase_base + phrase_idx * 0.068)))
            phrase_idx += 1
            fade_out_st = max(strike_at + 0.05, b_end - 0.18)

            v_mid = f"[vb{b_idx}a]"
            v_next = f"[vb{b_idx}b]"
            fc.append(
                f"[{in_plain}:v]scale=w='trunc(iw*{W}/1080/2)*2':h=-1,format=rgba,"
                f"fade=t=in:st={b_at:.3f}:d=0.08:alpha=1[bn_p{b_idx}]"
            )
            fc.append(
                f"[{in_struck}:v]scale=w='trunc(iw*{W}/1080/2)*2':h=-1,format=rgba,"
                f"fade=t=out:st={fade_out_st:.3f}:d=0.18:alpha=1[bn_s{b_idx}]"
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

    # Final video label -> [vout]
    # Clone trailing frame up to 1.25s if the cut holds an extra ~1s tail after speech for a wrong/right flash mark + SFX
    fc.append(f"{v_curr}tpad=stop_mode=clone:stop={max(3, int(round(fps * 1.25)))}[vout]")

    # Audio input & chain (always from active segment, or anullsrc if clip has no audio)
    if not has_audio:
        audio_input_idx = next_idx
        next_idx += 1
        inputs.extend(["-f", "lavfi", "-t", f"{exact:.4f}", "-i", "anullsrc=r=48000:cl=stereo"])
    fc.append(f"[{audio_input_idx}:a]{af_chain}[aout]")

    args: List[Any] = [
        *inputs,
        "-filter_complex", ";".join(fc),
        "-map", "[vout]",
        "-map", "[aout]",
        "-frames:v", str(frames),
        "-c:v", "libx264",
        "-preset", "ultrafast" if draft else "veryfast",
        "-crf", "12",
        "-pix_fmt", "yuv420p",
        "-c:a", "pcm_s16le",
        out,
    ]
    return args, exact, frames


_VT_AVAILABLE: Optional[bool] = None


def _has_videotoolbox() -> bool:
    """Return True if macOS Apple Silicon `h264_videotoolbox` hardware encoder is available."""
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
    """Render `seg` with its configured layout into normalized intermediate `.mkv` `out`.
    Uses Apple Silicon `h264_videotoolbox` hardware acceleration when enabled for 3-4x faster
    encoding and lower CPU thermal load, with automatic fallback to `libx264`.
    """
    args, exact, _ = build_segment_ffmpeg_args(seg, plan, W, H, fps, out, draft)
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    use_hw = bool((plan or {}).get("use_hw_accel", True))
    # GPU encoder for this computer (VideoToolbox, NVENC, Quick Sync, AMF) with automatic fallback to libx264.
    ffmpeg_encode(*args, draft=draft, hardware=use_hw)
    return exact


def place_mark_for_segment(
    mark: Union[Dict[str, Any], str, Sequence[float]],
    seg: Optional[Dict[str, Any]],
    W: int,
    H: int,
    size: int,
) -> Tuple[str, str]:
    """Layout-aware `(x, y)` placement for ✓ / ✗ marks so badges never collide with
    split-screen listener slots, top chat bars, or video-call PiP windows.
    """
    if isinstance(mark, dict):
        pos = mark.get("position", "top-right")
    else:
        pos = mark or "top-right"

    if isinstance(pos, (list, tuple)) and len(pos) >= 2:
        return f"{float(pos[0])}*{W}-{size}/2", f"{float(pos[1])}*{H}-{size}/2"

    pos_str = str(pos or "top-right").strip().lower()
    m = int(min(W, H) * 0.06)

    lay = normalize_layout_name(seg.get("layout") if isinstance(seg, dict) else "single")
    slot = _resolve_active_slot(seg, default="top" if lay != "single" else "") if isinstance(seg, dict) else "top"

    # Determine the active bounding box (x0, y0, box_w, box_h) for the badge
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
    elif lay == "chat_bar":
        # Push top margin below the top chat overlay / docked bar
        chat_top_clear = int(round(H * 0.25))
        y0, bh = chat_top_clear, H - chat_top_clear
    elif lay in ("video_call", "pip_call"):
        # Avoid top-right PiP window and top status bar
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
    """Auto-inject layout sound effects:
      - `pop.wav` (`-2.5 dB`) when chat bubbles appear in `chat_bar` segments
      - `click.wav` (`-3.5 dB`) when video-call turns switch in `video_call` / `pip_call` / `gmeet_grid`
      - `pop.wav` (`-3.0 dB`) on `split_h` / `split_v` / `top_pip` / baked-split punch-in beats
        (`sudden_in` or instant step zoom `>= 1.06`) when no explicit SFX or mark sits within `±0.20s`.
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

    # Collect existing explicit SFX and mark timestamps to avoid double-triggering
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
    whoosh_transitions = {
        "whip_left", "whip_right", "flash_zoom", "zoom_burst",
        "slide_up", "smoothleft", "smoothright", "warm_flash",
    }

    prev_lay = None
    prev_speaker = None
    prev_was_split = False
    prev_split_z_end = 1.0
    prev_active_slot = ""
    for idx, s in enumerate(segs):
        sid = str(s.get("id") or f"S{idx + 1}")
        t_base_opt = start_by_idx.get(idx, start_by_id.get(sid))
        if t_base_opt is None or s.get("auto_sfx") is False:
            continue
        t_base = float(t_base_opt)
        lay = normalize_layout_name(s.get("layout"))
        sp = str(s.get("speaker") or "")
        seg_dur = max(0.08, float(s.get("end", 1.0)) - float(s.get("start", 0.0)))
        z0, z1, z_style, z_at, _ = resolve_segment_zoom_spec(s, seg_dur)

        if lay == "chat_bar":
            chat_spec = s.get("chat") or {}
            msgs = chat_spec.get("messages", []) if isinstance(chat_spec, dict) else (chat_spec if isinstance(chat_spec, list) else [])
            if msgs:
                # Trigger pop on the latest bubble (or bubbles with at > 0)
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
            prev_was_split = False
            prev_split_z_end = 1.0
            prev_active_slot = ""

        elif lay in ("video_call", "pip_call", "gmeet_grid"):
            # Trigger subtle UI click when entering video_call or switching speaker turns inside video_call
            if prev_lay not in ("video_call", "pip_call", "gmeet_grid") or (sp and prev_speaker and sp != prev_speaker):
                t_click = round(max(0.0, t_base), 4)
                if not any(abs(t_click - et) < 0.12 for et in existing_times):
                    auto_sfx.append((click_file, t_click, -3.5))
                    existing_times.append(t_click)
            prev_was_split = False
            prev_split_z_end = 1.0
            prev_active_slot = ""

        else:
            is_split_seg = lay in ("split_h", "split_v", "top_pip")
            bspec_sfx = None
            if not is_split_seg and lay == "single" and (
                abs(z0 - 1.0) > 0.004 or abs(z1 - 1.0) > 0.004 or z_style == "flash_zoom"
            ):
                src_p = resolve(s["src"]) if s.get("src") else None
                bspec_sfx = _resolve_baked_split_spec(s, src_p)
                is_split_seg = bspec_sfx is not None

            if is_split_seg:
                if bspec_sfx is not None:
                    cur_active_slot = str(bspec_sfx.get("active") or "")
                else:
                    cur_active_slot = _resolve_active_slot(
                        s, default=("left" if lay == "split_h" else "top")
                    )
                t_zoom_beat: Optional[float] = None
                if z_style in ("sudden_in", "flash_zoom") and (z1 - z0) >= 0.02:
                    t_zoom_beat = round(max(0.0, t_base + float(z_at or 0.0)), 4)
                elif z_style == "static" and z0 >= 1.06 and (
                    not prev_was_split
                    or cur_active_slot != prev_active_slot
                    or abs(z0 - prev_split_z_end) >= 0.02
                ):
                    t_zoom_beat = round(max(0.0, t_base + float(z_at or 0.0)), 4)
                elif float(z_at or 0.0) > 0.0 and z1 >= 1.06 and (z1 - z0) >= 0.02:
                    t_zoom_beat = round(max(0.0, t_base + float(z_at)), 4)

                if t_zoom_beat is not None:
                    tr_name = str(s.get("transition") or "cut").strip().lower()
                    has_whoosh_tr = (
                        idx > 0
                        and tr_name in whoosh_transitions
                        and abs(t_zoom_beat - t_base) <= 0.15
                    )
                    if not has_whoosh_tr and not any(abs(t_zoom_beat - et) <= 0.20 for et in existing_times):
                        auto_sfx.append((pop_file, t_zoom_beat, -3.0))
                        existing_times.append(t_zoom_beat)
                prev_was_split = True
                prev_split_z_end = z1
                prev_active_slot = cur_active_slot
            else:
                prev_was_split = False
                prev_split_z_end = 1.0
                prev_active_slot = ""

        prev_lay = lay
        prev_speaker = sp

    return auto_sfx
