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

import hashlib
import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple, Union

from common import (
    MARKS_DIR,
    SFX_DIR,
    WORK,
    _normalize_mark_type,
    ffmpeg,
    probe,
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
    compute_slot_crop_filter,
    find_listening_idle_window,
)

EDGE_FADE: float = 0.012  # 12ms audio fade at segment edges to prevent clicks


def even(x: float) -> int:
    return max(2, int(round(float(x) / 2.0)) * 2)


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

        if lay in ("split_v", "split_h", "video_call", "pip_call", "gmeet_grid"):
            if not s.get("slot"):
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
            "still_at": peer.get("still_at"),
            "idle_span": peer.get("idle_span"),
            "focus": peer.get("focus"),
            "slot": peer.get("slot"),
        }

    payload = {
        "v": 2,
        "src": _file_mtime_sig(seg["src"]),
        "start": round(float(seg["start"]), 4),
        "end": round(float(seg["end"]), 4),
        "layout": lay,
        "slot": seg.get("slot"),
        "zoom": seg.get("zoom"),
        "zoom_to": seg.get("zoom_to"),
        "focus": seg.get("focus"),
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
) -> str:
    """Build an FFmpeg video filter chain that scales and crops `input_label` into
    `(slot_w, slot_h)` with eye-anchored headroom (`14%`) and optional zoom (`z0 -> z1`).
    """
    slot_w, slot_h = even(slot_w), even(slot_h)
    if exact and abs((src_w / src_h) / (slot_w / slot_h) - 1.0) < 0.03:
        # Source already has the slot's shape (within 3%): fit it as is, no headroom re-framing or crop.
        return f"{input_label}scale={slot_w}:{slot_h},setsar=1,fps={fps},format=yuv420p{output_label}"
    fx = max(0.05, min(0.95, float(focus[0]) if len(focus) >= 1 else 0.5))
    fy = max(0.08, min(0.85, float(focus[1]) if len(focus) >= 2 else 0.38))

    # Compute headroom-locked base scale & crop for this slot
    crop_info = compute_slot_crop_filter(
        src_w=src_w,
        src_h=src_h,
        slot_w=slot_w,
        slot_h=slot_h,
        bbox_norm=bbox_norm,
        focus=[fx, fy],
        headroom_pct=TARGET_HEADROOM_PCT,
    )
    chain = [crop_info["filter"]]

    if abs(z0 - 1.0) > 0.005 or abs(z1 - 1.0) > 0.005:
        if abs(z1 - z0) < 0.005:
            chain.append(f"scale={even(slot_w * z0)}:{even(slot_h * z0)}")
        else:
            p = f"(1-cos(PI*min(t/{exact_dur:.4f},1)))/2"
            z = f"({z0}+({z1}-{z0})*{p})"
            chain.append(f"scale=w='trunc({slot_w}*{z}/2)*2':h='trunc({slot_h}*{z}/2)*2':eval=frame")
        chain.append(f"crop={slot_w}:{slot_h}:x='(iw-{slot_w})*{fx:.4f}':y='(ih-{slot_h})*{fy:.4f}'")

    chain.extend(["setsar=1", f"fps={fps}", "format=yuv420p"])
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
    z0 = float(seg.get("zoom", 1.0) or 1.0)
    z1 = float(seg.get("zoom_to", z0) or z0)
    focus = list(seg.get("focus") or [0.5, 0.38])
    fx, fy = float(focus[0]), float(focus[1])

    src = resolve(seg["src"])
    src_info = probe(src) if src.is_file() else {"width": W, "height": H, "has_audio": True, "duration": dur + 1.0}
    src_w = int(src_info.get("width") or W)
    src_h = int(src_info.get("height") or H)
    has_audio = bool(src_info.get("has_audio", True))

    af_chain = ",".join([
        "aresample=48000",
        "aformat=channel_layouts=stereo",
        "apad",
        f"atrim=0:{exact:.6f}",
        f"afade=t=in:d={EDGE_FADE}",
        f"afade=t=out:st={max(0.0, exact - EDGE_FADE):.6f}:d={EDGE_FADE}",
    ])

    inputs: List[Any] = ["-ss", f"{float(seg['start']):.3f}", "-t", f"{dur + 0.5:.3f}", "-i", src]
    audio_input_idx: Optional[int] = 0 if has_audio else None
    next_idx = 1

    fc: List[str] = []
    v_curr = "[vbase]"

    # ------------------------------------------------------------------
    # Layout 1: SINGLE (Full-screen)
    # ------------------------------------------------------------------
    if lay == "single":
        vf = [f"scale={W}:{H}:force_original_aspect_ratio=increase", f"crop={W}:{H}"]
        if abs(z0 - 1.0) > 0.005 or abs(z1 - 1.0) > 0.005:
            if abs(z1 - z0) < 0.005:
                vf.append(f"scale={even(W * z0)}:{even(H * z0)}")
            else:
                p = f"(1-cos(PI*min(t/{exact:.4f},1)))/2"
                z = f"({z0}+({z1}-{z0})*{p})"
                vf.append(f"scale=w='trunc({W}*{z}/2)*2':h='trunc({H}*{z}/2)*2':eval=frame")
            vf.append(f"crop={W}:{H}:x='(iw-{W})*{fx:.4f}':y='(ih-{H})*{fy:.4f}'")
        vf.extend(["setsar=1", f"fps={fps}", "format=yuv420p"])
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

        # Use clean mouth-closed idle loop if peer clip exists and idle_span >= 0.45s, else still PNG
        peer_idx = next_idx
        next_idx += 1
        if peer_src.is_file() and peer.get("mode") == "live":
            # Both panels play their own clip in sync (micronarrative scene 3); audio still comes from the main clip.
            inputs.extend(["-ss", f"{float(peer.get('start', 0.0)):.3f}", "-t", f"{dur + 0.5:.3f}", "-i", peer_src])
        elif (
            peer_src.is_file()
            and isinstance(idle_span, (list, tuple))
            and len(idle_span) >= 2
            and (float(idle_span[1]) - float(idle_span[0])) >= 0.45
            and peer.get("mode", "still") == "loop"
        ):
            s0 = max(0.0, float(idle_span[0]))
            s_dur = max(0.45, float(idle_span[1]) - s0)
            inputs.extend(["-stream_loop", "-1", "-ss", f"{s0:.3f}", "-t", f"{s_dur:.3f}", "-i", peer_src])
        elif peer_src.is_file():
            still_png = _extract_peer_still_png(peer_src, still_at)
            inputs.extend(["-loop", "1", "-framerate", str(fps), "-t", f"{exact:.4f}", "-i", still_png])
        else:
            inputs.extend(["-ss", f"{still_at:.3f}", "-t", f"{exact:.4f}", "-i", peer_src])

        if lay == "split_v":
            slot_w = W
            slot_h_top = even(H // 2)
            slot_h_bot = H - slot_h_top
            active_top = str(seg.get("slot") or "top").lower() in ("top", "slot_a", "a", "0")
            live_h = slot_h_top if active_top else slot_h_bot
            peer_h_slot = slot_h_bot if active_top else slot_h_top

            fc.append(_build_slot_video_chain("[0:v]", "[live_slot]", src_w, src_h, slot_w, live_h, fps, exact, [fx, fy], z0, z1))
            fc.append(_build_slot_video_chain(f"[{peer_idx}:v]", "[peer_slot]", peer_w, peer_h, slot_w, peer_h_slot, fps, exact, peer_focus, 1.0, 1.0))

            stack_order = "[live_slot][peer_slot]" if active_top else "[peer_slot][live_slot]"
            fc.append(
                f"{stack_order}vstack=inputs=2,"
                f"drawbox=x=0:y={slot_h_top - 2}:w={W}:h=4:color=white@0.85:t=fill,"
                f"format=yuv420p{v_curr}"
            )
        else:
            slot_w_left = even(W // 2)
            slot_w_right = W - slot_w_left
            slot_h = H
            active_left = str(seg.get("slot") or "left").lower() in ("left", "slot_a", "a", "0")
            live_w = slot_w_left if active_left else slot_w_right
            peer_w_slot = slot_w_right if active_left else slot_w_left

            exact_fit = str(seg.get("framing") or "").lower() == "exact"
            fc.append(_build_slot_video_chain("[0:v]", "[live_slot]", src_w, src_h, live_w, slot_h, fps, exact, [fx, fy], z0, z1, exact=exact_fit))
            fc.append(_build_slot_video_chain(f"[{peer_idx}:v]", "[peer_slot]", peer_w, peer_h, peer_w_slot, slot_h, fps, exact, peer_focus, 1.0, 1.0, exact=exact_fit))

            stack_order = "[live_slot][peer_slot]" if active_left else "[peer_slot][live_slot]"
            dv = seg.get("divider")
            if isinstance(dv, dict):   # measured divider: thin line on the left slot's right edge
                dw = max(2, round(W * float(dv.get("w_frac", 0.004))))
                dx = round(W * float(dv.get("x_frac", 0.5)))
                divider = f"drawbox=x={dx}:y=0:w={dw}:h={H}:color=0x{dv.get('color', 'FFFFFF')}@{dv.get('alpha', 1.0)}:t=fill,"
            else:
                divider = f"drawbox=x={slot_w_left - 2}:y=0:w=4:h={H}:color=white@0.85:t=fill,"
            fc.append(f"{stack_order}hstack=inputs=2,{divider}format=yuv420p{v_curr}")

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
            fc.append(_build_slot_video_chain("[0:v]", "[v_sp]", src_w, src_h, W, vid_h, fps, exact, [fx, fy], z0, z1))
            fc.append(f"[v_sp]pad={W}:{H}:0:{dock_h}:color=#0f172a[v_padded]")
            fc.append(f"[{chat_idx}:v]scale={W}:{H},format=rgba[chat_ov]")
            fc.append(f"[v_padded][chat_ov]overlay=0:0:eof_action=pass,format=yuv420p{v_curr}")
        else:
            # Floating overlay: speaker fills W x H, chat bar overlays top with smooth fade-in
            fc.append(_build_slot_video_chain("[0:v]", "[v_sp]", src_w, src_h, W, H, fps, exact, [fx, fy], z0, z1))
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
            active_top = str(seg.get("slot") or "top").lower() in ("top", "slot_a", "a", "0")
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
            fc.append(_build_slot_video_chain("[0:v]", "[v_live]", src_w, src_h, tw, th, fps, exact, [fx, fy], z0, z1))
            fc.append(_build_slot_video_chain(f"[{peer_idx}:v]", "[v_peer]", peer_w, peer_h, tw, th, fps, exact, peer_focus, 1.0, 1.0))
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

            fc.append(_build_slot_video_chain("[0:v]", "[v_main]", src_w, src_h, W, H, fps, exact, [fx, fy], z0, z1))
            fc.append(_build_slot_video_chain(f"[{peer_idx}:v]", "[v_pip_raw]", peer_w, peer_h, pw, ph, fps, exact, peer_focus, 1.0, 1.0))
            fc.append(f"[{mask_idx}:v]scale={pw}:{ph},format=gray[pip_mask]")
            fc.append("[v_pip_raw]format=yuva420p[v_pip_a];[v_pip_a][pip_mask]alphamerge[v_pip]")
            fc.append(f"[v_main][v_pip]overlay=x={px}:y={py}:eof_action=pass[v_with_pip]")
            fc.append(f"[{chrome_idx}:v]scale={W}:{H},format=rgba[chrome]")
            fc.append(f"[v_with_pip][chrome]overlay=0:0:eof_action=pass,format=yuv420p{v_curr}")

    # ------------------------------------------------------------------
    # Optional "Stop Saying" Hook Banners on any segment
    # ------------------------------------------------------------------
    banners = seg.get("banners") or seg.get("stop_saying") or []
    if isinstance(banners, (str, dict)):
        banners = [banners]
    if banners:
        for b_idx, b_item in enumerate(banners):
            if isinstance(b_item, str):
                b_text, b_at, b_dur = b_item, b_idx * 0.45, exact
            elif isinstance(b_item, dict):
                b_text = str(b_item.get("text") or b_item.get("phrase") or "STOP SAYING")
                b_at = float(b_item.get("at", b_idx * 0.45))
                b_dur = float(b_item.get("duration", max(0.4, exact - b_at)))
            else:
                continue
            banner_png = render_stop_saying_banner_png(b_text)
            b_in_idx = next_idx
            next_idx += 1
            inputs.extend(["-loop", "1", "-framerate", str(fps), "-t", f"{exact:.4f}", "-i", banner_png])
            bx_expr = f"trunc({W}*0.68-overlay_w/2)"
            by_expr = int(round(H * (0.12 + b_idx * 0.065)))
            v_next = f"[vb{b_idx}]"
            fc.append(f"[{b_in_idx}:v]scale=w='trunc(iw*{W}/1080/2)*2':h=-1,format=rgba[bn{b_idx}]")
            fc.append(
                f"{v_curr}[bn{b_idx}]overlay=x={bx_expr}:y={by_expr}:"
                f"enable='between(t,{b_at:.3f},{min(exact, b_at + b_dur):.3f})':eof_action=pass,format=yuv420p{v_next}"
            )
            v_curr = v_next

    # Final video label -> [vout]
    # scale->fps can lose the last frame of a clip; hold it for up to 3 frames so -frames:v always reaches the exact count
    fc.append(f"{v_curr}tpad=stop_mode=clone:stop=3[vout]")

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


def render_segment_layout(
    seg: Dict[str, Any],
    plan: Optional[Dict[str, Any]],
    W: int,
    H: int,
    fps: int,
    out: Union[str, Path],
    draft: bool = False,
) -> float:
    """Render `seg` with its configured layout into normalized intermediate `.mkv` `out`."""
    args, exact, _ = build_segment_ffmpeg_args(seg, plan, W, H, fps, out, draft)
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    ffmpeg(*args)
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
    slot = str((seg or {}).get("slot") or "top").strip().lower()

    # Determine the active bounding box (x0, y0, box_w, box_h) for the badge
    x0, y0, bw, bh = 0, 0, W, H

    if lay == "split_v" or lay == "gmeet_grid":
        half_h = H // 2
        if slot in ("bottom", "slot_b", "b", "1"):
            y0, bh = half_h, H - half_h
        else:
            y0, bh = 0, half_h
    elif lay == "split_h":
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
    seg_start: Dict[str, float],
) -> List[Tuple[str, float, float]]:
    """Auto-inject layout sound effects:
      - `pop.wav` when chat bubbles appear in `chat_bar` segments
      - `click.wav` when video-call turns switch in `video_call` / `pip_call` / `gmeet_grid` segments.
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

    # Collect existing explicit SFX timestamps to avoid double-triggering at the same instant
    existing_times: List[float] = []
    for e in plan.get("sfx") or []:
        sid = e.get("segment")
        if sid in seg_start:
            existing_times.append(float(seg_start[sid]) + float(e.get("at", 0.0)))

    auto_sfx: List[Tuple[str, float, float]] = []
    segs: List[Dict[str, Any]] = plan.get("segments") or []

    prev_lay = None
    prev_speaker = None
    for s in segs:
        sid = str(s.get("id") or "")
        if sid not in seg_start or s.get("auto_sfx") is False:
            continue
        t_base = float(seg_start[sid])
        lay = normalize_layout_name(s.get("layout"))
        sp = str(s.get("speaker") or "")

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
                        auto_sfx.append((pop_file, t_pop, -10.0))
                        existing_times.append(t_pop)
            else:
                t_pop = round(t_base + 0.08, 4)
                if not any(abs(t_pop - et) < 0.12 for et in existing_times):
                    auto_sfx.append((pop_file, t_pop, -10.0))
                    existing_times.append(t_pop)

        elif lay in ("video_call", "pip_call", "gmeet_grid"):
            # Trigger subtle UI click when entering video_call or switching speaker turns inside video_call
            if prev_lay not in ("video_call", "pip_call", "gmeet_grid") or (sp and prev_speaker and sp != prev_speaker):
                t_click = round(max(0.0, t_base), 4)
                if not any(abs(t_click - et) < 0.12 for et in existing_times):
                    auto_sfx.append((click_file, t_click, -12.0))
                    existing_times.append(t_click)

        prev_lay = lay
        prev_speaker = sp

    return auto_sfx
