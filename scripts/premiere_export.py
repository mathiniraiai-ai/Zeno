#!/usr/bin/env python3
"""
Adobe Premiere Pro Project (.prproj) & Timeline XML (.premiere.xml) Exporter for Zeno.

Generates two editable Premiere Pro project files alongside every rendered MP4:
  1. ~/Downloads/Zeno Outputs/<name>.prproj
     Native GZIP-compressed Adobe Premiere Pro project file (PremiereData Version="3")
     containing all raw MP4 clips, PNG overlays, and WAV/MP3 SFX in the Root Bin plus
     a multi-track 1080x1920 @ 30fps sequence with non-destructive In/Out cut points.
  2. ~/Downloads/Zeno Outputs/<name>.premiere.xml
     Native Final Cut Pro 7 / Premiere Pro xmeml version="4" interchange file containing
     the full multi-track timeline PLUS keyframed Basic Motion (Scale zoom z0->z1,
     split-screen Center offset), Crop (25% left/right for split_h, 25% top/bottom for
     split_v), 2-pulse Opacity keyframes on ✓/✗ marks, and Audio Levels.
"""
from __future__ import annotations

import argparse
import base64
import gzip
import math
import os
import pathlib
import urllib.parse
import uuid
import xml.etree.ElementTree as ET
from xml.sax.saxutils import escape

import common as _common

REPO = getattr(_common, "ROOT", pathlib.Path(__file__).resolve().parent.parent)
OUTPUT = getattr(_common, "OUTPUT", pathlib.Path.home() / "Downloads" / "Zeno Outputs")


def probe_duration(p: pathlib.Path) -> float:
    info = _common.probe(p)
    return float(info.get("duration") or 60.0)
from edl_parser import load_edl
import layouts


def load_plan(plan_path: pathlib.Path) -> dict:
    p = pathlib.Path(plan_path)
    if p.suffix.lower() == ".json":
        return _common.read_json(p)
    return load_edl(p)


def parse_zoom_spec(spec) -> dict:
    if hasattr(layouts, "parse_zoom_spec"):
        return layouts.parse_zoom_spec(spec)
    if isinstance(spec, (int, float)):
        v = float(spec)
        return {"z0": v, "z1": v}
    s = str(spec or "1.0").strip()
    if "->" in s:
        a, b = s.split("->", 1)
        try:
            return {"z0": float(a), "z1": float(b)}
        except Exception:
            pass
    try:
        v = float(s)
        return {"z0": v, "z1": v}
    except Exception:
        return {"z0": 1.0, "z1": 1.0}

PREMIERE_TICKS_PER_SEC = 254016000000
AUDIO_SAMPLE_RATE = 48000
AUDIO_TICKS_PER_SAMPLE = PREMIERE_TICKS_PER_SEC // AUDIO_SAMPLE_RATE  # 5292000


def _sec_to_ticks(sec: float) -> int:
    return max(0, int(round(float(sec) * PREMIERE_TICKS_PER_SEC)))


def _sec_to_frames(sec: float, fps: int = 30) -> int:
    return max(0, int(round(float(sec) * fps)))


def _path_to_url(path: pathlib.Path | str) -> str:
    abs_str = os.path.abspath(str(path)).replace("\\", "/")
    if not abs_str.startswith("/"):
        abs_str = "/" + abs_str
    return "file://localhost" + urllib.parse.quote(abs_str, safe="/:")


def _db_to_linear(db: float) -> float:
    return round(10.0 ** (float(db) / 20.0), 4)


def _resolve_clip_path(clip_ref: str, plan_path: pathlib.Path, clip_map: dict[str, str] | None = None) -> pathlib.Path | None:
    if not clip_ref:
        return None
    if clip_map and clip_ref in clip_map:
        return pathlib.Path(clip_map[clip_ref]).resolve()
    p = pathlib.Path(clip_ref)
    if p.is_Absolute() if hasattr(p, "is_Absolute") else p.is_absolute():
        if p.exists():
            return p.resolve()
    for base in (plan_path.parent, REPO, REPO / "input" / "footage"):
        cand = base / clip_ref
        if cand.exists():
            return cand.resolve()
    # Search in footage dirs referenced by plan or work brief
    brief_file = plan_path.parent / "brief.txt"
    if brief_file.exists():
        try:
            for line in brief_file.read_text(encoding="utf-8", errors="ignore").splitlines():
                if clip_ref in line and "/" in line:
                    for tok in line.split():
                        tok_clean = tok.strip("\"'(),[]")
                        if tok_clean.endswith(".mp4") and os.path.exists(tok_clean):
                            return pathlib.Path(tok_clean).resolve()
        except Exception:
            pass
    return p.resolve()


def _build_clip_lookup(plan: dict, plan_path: pathlib.Path) -> dict[str, str]:
    """Map clip stem/id (e.g. '21545_1') to its resolved absolute file path."""
    lookup: dict[str, str] = {}
    search_dirs: list[pathlib.Path] = [
        REPO / "input" / "footage",
        plan_path.parent,
    ]
    src_dir_raw = plan.get("src_dir")
    if src_dir_raw:
        sd = pathlib.Path(str(src_dir_raw))
        if not sd.is_absolute():
            sd = (REPO / sd).resolve()
        if sd.exists():
            search_dirs.insert(0, sd)
    # Also inspect brief.txt or plan.json if present for footage paths
    brief_file = plan_path.parent / "brief.txt"
    if brief_file.exists():
        try:
            txt = brief_file.read_text(encoding="utf-8", errors="ignore")
            for line in txt.splitlines():
                if "footage_dir:" in line or "/Downloads/" in line or "/input/" in line:
                    for part in line.split("\t"):
                        part = part.strip()
                        if os.path.isdir(part):
                            search_dirs.append(pathlib.Path(part))
                        elif os.path.isfile(part):
                            search_dirs.append(pathlib.Path(part).parent)
        except Exception:
            pass
    # Also scan work/<project>/*/clip_path.txt if cached
    for cp_txt in plan_path.parent.glob("*/clip_path.txt"):
        try:
            raw_p = pathlib.Path(cp_txt.read_text(encoding="utf-8").strip())
            if raw_p.exists():
                lookup[raw_p.stem] = str(raw_p.resolve())
                lookup[raw_p.name] = str(raw_p.resolve())
                search_dirs.append(raw_p.parent)
        except Exception:
            pass
    seen_dirs = set()
    for d in search_dirs:
        if not d.exists() or str(d) in seen_dirs:
            continue
        seen_dirs.add(str(d))
        for mp4 in d.glob("*.mp4"):
            lookup.setdefault(mp4.stem, str(mp4.resolve()))
            lookup.setdefault(mp4.name, str(mp4.resolve()))
    return lookup


def _ensure_overlay_pngs(plan: dict, work_dir: pathlib.Path) -> dict:
    """
    Ensure all dynamic PNG overlays (strikethrough banners, chat_bar, video_call)
    exist inside work_dir/overlays/ so Premiere Pro can link to them directly.
    """
    ov_dir = work_dir / "overlays"
    ov_dir.mkdir(parents=True, exist_ok=True)
    generated = {
        "strikethrough": {},  # seg_idx -> (unstruck_png, struck_png)
        "chat_bar": {},       # seg_idx -> png_path
        "video_call": {},     # seg_idx -> png_path
    }
    strike_items: list[str] = []
    for idx, seg in enumerate(plan.get("segments", [])):
        st_text = (seg.get("strikethrough_text") or "").strip()
        if st_text and st_text not in strike_items:
            strike_items.append(st_text)
        if strike_items and seg.get("layout", "single") in ("split_h", "split_v"):
            u_png = ov_dir / f"pr_strike_{idx:02d}_in.png"
            s_png = ov_dir / f"pr_strike_{idx:02d}_struck.png"
            try:
                if st_text and seg.get("strikethrough_initial_delay", 0):
                    layouts._build_strikethrough_png(
                        strike_items, u_png, strike_last=False
                    )
                else:
                    u_png = None
                layouts._build_strikethrough_png(
                    strike_items, s_png, strike_last=True
                )
                generated["strikethrough"][idx] = (u_png if (u_png and u_png.exists()) else None, s_png if s_png.exists() else None)
            except Exception:
                pass
    return generated


def _collect_timeline_model(
    plan: dict,
    plan_path: pathlib.Path,
    work_dir: pathlib.Path,
    fps: int = 30,
    width: int = 1080,
    height: int = 1920,
) -> dict:
    """
    Build a unified multi-track timeline representation from plan.edl / plan dict
    used by both the .premiere.xml generator and the .prproj generator.
    """
    clip_lookup = _build_clip_lookup(plan, plan_path)
    ov_assets = _ensure_overlay_pngs(plan, work_dir)
    mark_pack = plan.get("mark_pack", "flash")
    segments = plan.get("segments", [])

    # Group top-level plan["marks"] and plan["sfx"] by segment ID
    marks_by_seg: dict[str, list[dict]] = {}
    for mk in plan.get("marks", []) or []:
        sid = str(mk.get("segment") or "")
        if sid:
            marks_by_seg.setdefault(sid, []).append(mk)

    sfx_by_seg: dict[str, list[dict]] = {}
    for sx in plan.get("sfx", []) or []:
        sid = str(sx.get("segment") or "")
        if sid:
            sfx_by_seg.setdefault(sid, []).append(sx)

    # Video tracks:
    # V1 (index 0): Primary speaker clips
    # V2 (index 1): Listening partner clips (for split_h / split_v / video_call)
    # V3..V5 (index 2..4): Strikethrough "Stop Saying" banners (up to 3 stacked phrases)
    # V6 (index 5): ✓ / ✗ mark PNG overlays (2-pulse end-of-sentence flash)
    v_tracks: list[list[dict]] = [[], [], [], [], [], []]
    # Audio tracks:
    # A1 (index 0): Primary dialogue audio (Stereo)
    # A2 (index 1): Secondary audio if needed
    # A3 (index 2): SFX Track 1 (whoosh, pop, strike, buzzer, ding)
    # A4 (index 3): SFX Track 2 (layered SFX / non-overlapping overflow)
    a_tracks: list[list[dict]] = [[], [], [], []]

    dur_cache: dict[str, float] = {}

    def get_media_dur(p: pathlib.Path, is_still: bool = False) -> float:
        if is_still:
            return 3600.0
        s = str(p)
        if s not in dur_cache:
            try:
                dur_cache[s] = max(0.1, float(probe_duration(p)))
            except Exception:
                dur_cache[s] = 60.0
        return dur_cache[s]

    def place_sfx(sfx_path: pathlib.Path, start_sec: float, gain_db: float = 0.0, max_dur: float | None = None):
        if not sfx_path or not sfx_path.exists():
            return
        raw_dur = get_media_dur(sfx_path, is_still=False)
        dur = min(raw_dur, max_dur) if max_dur else raw_dur
        if dur <= 0.02:
            return
        end_sec = start_sec + dur
        item = {
            "name": sfx_path.name,
            "path": sfx_path.resolve(),
            "media_type": "audio",
            "t_start": start_sec,
            "t_end": end_sec,
            "src_in": 0.0,
            "src_out": dur,
            "media_dur": raw_dur,
            "gain_db": gain_db,
        }
        for ti in range(2, len(a_tracks)):
            track = a_tracks[ti]
            if not track or track[-1]["t_end"] <= start_sec + 0.005:
                track.append(item)
                return
        if len(a_tracks) < 8:
            a_tracks.append([item])
        else:
            a_tracks[-1].append(item)

    cursor = 0.0
    markers: list[dict] = []
    for idx, seg in enumerate(segments):
        src_in = float(seg.get("start", 0.0))
        src_out = float(seg.get("end", src_in))
        dur = max(0.033, src_out - src_in)
        t_start = cursor
        t_end = cursor + dur

        seg_id = str(seg.get("id") or f"S{idx+1}")
        clip_ref = str(seg.get("src") or seg.get("clip") or "")
        clip_path = _resolve_clip_path(clip_ref, plan_path, clip_lookup)
        if not clip_path or not clip_path.exists():
            cursor = t_end
            continue

        media_dur = get_media_dur(clip_path, is_still=False)
        layout_mode = str(seg.get("layout", plan.get("preset", "single")) or "single")
        zoom_style = "smooth"
        zoom_at = 0.0
        zoom_dur = dur
        if hasattr(layouts, "resolve_segment_zoom_spec"):
            z0, z1, zoom_style, zoom_at, zoom_dur = layouts.resolve_segment_zoom_spec(seg, dur)
        else:
            z_spec = parse_zoom_spec(seg.get("zoom", 1.0))
            z0 = float(z_spec.get("z0", 1.0))
            z1 = float(seg.get("zoom_to") or z_spec.get("z1", z0))
        vol_db = float(seg.get("gain_db", 0.0) or 0.0)

        # Motion & Crop parameters for V1 (with multi-keyframe eased Scale curves & focus Center offset)
        v1_scale_start = round(z0 * 100.0, 2)
        v1_scale_end = round(z1 * 100.0, 2)
        scale_kfs: list[tuple[float, float]] = []
        if zoom_style in ("sudden_in", "sudden_out", "flash_zoom") and abs(z1 - z0) > 0.004:
            t0_z = max(0.0, min(dur - 0.05, float(zoom_at)))
            d_snap = max(0.08, min(1.20, float(zoom_dur or 0.18)))
            mid_z = z0 + (z1 - z0) * 0.875
            drift_z = z1 + (0.015 if z1 >= z0 else -0.015)
            scale_kfs = [
                (0.0, round(z0 * 100.0, 2)),
                (t0_z, round(z0 * 100.0, 2)),
                (min(dur, t0_z + d_snap * 0.5), round(mid_z * 100.0, 2)),
                (min(dur, t0_z + d_snap), round(z1 * 100.0, 2)),
                (dur, round(drift_z * 100.0, 2)),
            ]
        elif abs(z1 - z0) > 0.004:
            for frac in (0.0, 0.25, 0.5, 0.75, 1.0):
                p_ease = 0.5 * (1.0 - math.cos(math.pi * frac))
                scale_kfs.append((round(frac * dur, 4), round((z0 + (z1 - z0) * p_ease) * 100.0, 2)))

        focus_pt = seg.get("focus") or [0.5, 0.38]
        z_mid = 0.5 * (z0 + z1)
        if isinstance(focus_pt, (list, tuple)) and len(focus_pt) >= 2 and z_mid > 1.005:
            cx_f = round((0.5 - float(focus_pt[0])) * max(0.0, z_mid - 1.0), 4)
            cy_f = round((0.38 - float(focus_pt[1])) * max(0.0, z_mid - 1.0), 4)
            v1_center = (cx_f, cy_f)
        else:
            v1_center = (0.0, 0.0)
        v1_crop = None

        slot = str(seg.get("slot") or "").lower()
        speaker_name = str(seg.get("speaker", "") or "").lower()
        active_first = (
            slot in ("left", "top", "pip", "slot_a", "a", "0")
            if slot
            else (("robot" in speaker_name) or ("nova" in speaker_name))
        )
        if layout_mode == "split_h":
            v1_scale_start = v1_scale_end = 100.0
            scale_kfs = []
            v1_crop = {"left": 25.0, "right": 25.0, "top": 0.0, "bottom": 0.0}
            v1_center = (-0.25 if active_first else 0.25, 0.0)
        elif layout_mode == "split_v":
            v1_scale_start = v1_scale_end = 100.0
            scale_kfs = []
            v1_crop = {"left": 0.0, "right": 0.0, "top": 25.0, "bottom": 25.0}
            v1_center = (0.0, -0.25 if active_first else 0.25)

        tr_raw = seg.get("transition")
        tr_spec = None
        if tr_raw and str(tr_raw).lower() != "cut" and idx > 0:
            tr_dur = float(seg.get("transition_duration") or 0.28)
            tr_spec = {"type": str(tr_raw).lower(), "dur": tr_dur}

        markers.append({
            "t": t_start,
            "name": f"{seg_id} ({seg.get('speaker', layout_mode)})",
            "comment": str(seg.get("note") or ""),
        })

        # In top_pip, V1 is always the full-screen background and V2 is always the top-center PIP window
        pip_scale = 69.44
        pip_center = (0.0, -0.38)
        pip_crop = {"left": 0.0, "right": 0.0, "top": 28.0, "bottom": 40.5}

        if layout_mode == "top_pip" and active_first:
            # Speaking clip is the top PIP window on V2 (linked to A1)
            v2_active_item = {
                "seg_idx": idx,
                "name": f"{seg_id}_{clip_path.stem}",
                "path": clip_path,
                "media_type": "video",
                "t_start": t_start,
                "t_end": t_end,
                "src_in": src_in,
                "src_out": src_out,
                "media_dur": media_dur,
                "scale_start": pip_scale,
                "scale_end": pip_scale,
                "scale_keyframes": [],
                "center": pip_center,
                "crop": pip_crop,
                "opacity_keyframes": None,
                "transition": tr_spec,
                "link_id": f"link_seg_{idx}",
            }
            v_tracks[1].append(v2_active_item)
        else:
            v1_item = {
                "seg_idx": idx,
                "name": f"{seg_id}_{clip_path.stem}",
                "path": clip_path,
                "media_type": "video",
                "t_start": t_start,
                "t_end": t_end,
                "src_in": src_in,
                "src_out": src_out,
                "media_dur": media_dur,
                "scale_start": v1_scale_start,
                "scale_end": v1_scale_end,
                "scale_keyframes": scale_kfs,
                "center": v1_center,
                "crop": v1_crop,
                "opacity_keyframes": None,
                "transition": tr_spec,
                "link_id": f"link_seg_{idx}",
            }
            v_tracks[0].append(v1_item)

        a1_item = {
            "seg_idx": idx,
            "name": f"{seg_id}_{clip_path.stem}",
            "path": clip_path,
            "media_type": "audio_from_video",
            "t_start": t_start,
            "t_end": t_end,
            "src_in": src_in,
            "src_out": src_out,
            "media_dur": media_dur,
            "gain_db": vol_db,
            "transition": {"type": "crossfade", "dur": 0.045} if idx > 0 else None,
            "link_id": f"link_seg_{idx}",
        }
        a_tracks[0].append(a1_item)

        # Peer / Listening Partner clip for split_h / split_v / video_call / top_pip
        peer_spec = seg.get("peer")
        peer_clip_ref = None
        if isinstance(peer_spec, dict):
            peer_clip_ref = peer_spec.get("src") or peer_spec.get("clip")
        elif isinstance(peer_spec, str) and peer_spec.strip():
            peer_clip_ref = peer_spec.split("@")[0].strip()
            if ":" in peer_clip_ref and len(peer_clip_ref.split(":", 1)[0]) > 1:
                peer_clip_ref = peer_clip_ref.split(":", 1)[1].strip()

        if layout_mode in ("split_h", "split_v", "video_call", "top_pip") and peer_clip_ref:
            peer_path = _resolve_clip_path(str(peer_clip_ref), plan_path, clip_lookup)
            if peer_path and peer_path.exists():
                peer_media_dur = get_media_dur(peer_path, is_still=False)
                p_in = 0.0
                p_out = min(peer_media_dur, dur)
                if isinstance(peer_spec, dict):
                    idle_span = peer_spec.get("idle_span")
                    if isinstance(idle_span, (list, tuple)) and len(idle_span) >= 2:
                        p_in = max(0.0, float(idle_span[0]))
                        p_out = min(peer_media_dur, max(p_in + 0.2, float(idle_span[1])))
                    elif peer_spec.get("end", 0) > peer_spec.get("start", 0):
                        p_in = float(peer_spec["start"])
                        p_out = min(peer_media_dur, p_in + dur)
                v2_center = (0.0, 0.0)
                v2_crop = None
                v2_scale = 100.0
                target_v_track = 1
                if layout_mode == "split_h":
                    v2_crop = {"left": 25.0, "right": 25.0, "top": 0.0, "bottom": 0.0}
                    v2_center = (0.25 if active_first else -0.25, 0.0)
                elif layout_mode == "split_v":
                    v2_crop = {"left": 0.0, "right": 0.0, "top": 25.0, "bottom": 25.0}
                    v2_center = (0.0, 0.25 if active_first else -0.25)
                elif layout_mode == "video_call":
                    v2_scale = 28.0
                    v2_center = (0.30, -0.32)
                elif layout_mode == "top_pip":
                    if active_first:
                        # Speaking clip is on V2 (top PIP); listening human goes on V1 (full-screen bg)
                        target_v_track = 0
                        v2_scale = 100.0
                        v2_center = (0.0, 0.0)
                        v2_crop = None
                    else:
                        # Speaking human is on V1 (full-screen bg); listening robot goes on V2 (top PIP)
                        target_v_track = 1
                        v2_scale = pip_scale
                        v2_center = pip_center
                        v2_crop = pip_crop

                rem = dur
                cur_v2 = t_start
                span = max(0.2, p_out - p_in)
                while rem > 0.04:
                    chunk = min(rem, span)
                    v_tracks[target_v_track].append({
                        "seg_idx": idx,
                        "name": f"Listen_{seg_id}_{peer_path.stem}",
                        "path": peer_path,
                        "media_type": "video",
                        "t_start": cur_v2,
                        "t_end": cur_v2 + chunk,
                        "src_in": p_in,
                        "src_out": p_in + chunk,
                        "media_dur": peer_media_dur,
                        "scale_start": v2_scale,
                        "scale_end": v2_scale,
                        "scale_keyframes": [],
                        "center": v2_center,
                        "crop": v2_crop,
                        "opacity_keyframes": None,
                        "link_id": None,
                    })
                    cur_v2 += chunk
                    rem -= chunk

        # V3..V5: Stacked "Stop Saying" Banners (from seg["banners"] or ov_assets["strikethrough"])
        banners = seg.get("banners") or seg.get("stop_saying") or []
        if isinstance(banners, (str, dict)):
            banners = [banners]
        if banners and hasattr(layouts, "render_stop_saying_banner_png"):
            for b_idx, b_item in enumerate(banners[:3]):
                if isinstance(b_item, str):
                    b_text, b_at, b_dur = b_item, round(0.15 + b_idx * 0.65, 3), dur
                elif isinstance(b_item, dict):
                    b_text = str(b_item.get("text") or b_item.get("phrase") or "STOP SAYING")
                    b_at = float(b_item.get("at", round(0.15 + b_idx * 0.65, 3)))
                    b_dur = float(b_item.get("duration", max(0.4, dur - b_at)))
                else:
                    continue
                b_end = min(dur, b_at + b_dur)
                strike_at = min(b_end - 0.05, b_at + 0.16)
                png_plain = pathlib.Path(layouts.render_stop_saying_banner_png(b_text, with_strike=False)).resolve()
                png_struck = pathlib.Path(layouts.render_stop_saying_banner_png(b_text, with_strike=True)).resolve()
                v_slot = min(2 + b_idx, 4)
                y_off = round((0.52 + b_idx * 0.068) - 0.50, 4)
                if png_plain.exists() and strike_at > b_at + 0.03:
                    v_tracks[v_slot].append({
                        "seg_idx": idx,
                        "name": f"Banner_{b_idx+1}_In_{b_text[:16]}",
                        "path": png_plain,
                        "media_type": "still",
                        "t_start": t_start + b_at,
                        "t_end": t_start + strike_at,
                        "src_in": 0.0,
                        "src_out": strike_at - b_at,
                        "media_dur": 3600.0,
                        "scale_start": 100.0,
                        "scale_end": 100.0,
                        "center": (0.0, y_off),
                        "crop": None,
                        "opacity_keyframes": None,
                        "link_id": None,
                    })
                if png_struck.exists() and b_end > strike_at + 0.03:
                    v_tracks[v_slot].append({
                        "seg_idx": idx,
                        "name": f"Banner_{b_idx+1}_Strike_{b_text[:16]}",
                        "path": png_struck,
                        "media_type": "still",
                        "t_start": t_start + strike_at,
                        "t_end": t_start + b_end,
                        "src_in": 0.0,
                        "src_out": b_end - strike_at,
                        "media_dur": 3600.0,
                        "scale_start": 100.0,
                        "scale_end": 100.0,
                        "center": (0.0, y_off),
                        "crop": None,
                        "opacity_keyframes": None,
                        "link_id": None,
                    })
                place_sfx(REPO / "assets" / "sfx" / "ui_pop.wav", t_start + b_at, gain_db=-2.0, max_dur=0.35)
                place_sfx(REPO / "assets" / "sfx" / "pen_strike.wav", t_start + strike_at, gain_db=-1.0, max_dur=0.45)
        elif idx in ov_assets["strikethrough"]:
            u_png, s_png = ov_assets["strikethrough"][idx]
            delay = float(seg.get("strikethrough_initial_delay", 0.0) or 0.0)
            if u_png and delay > 0.05 and delay < dur:
                v_tracks[2].append({
                    "seg_idx": idx,
                    "name": u_png.name,
                    "path": u_png.resolve(),
                    "media_type": "still",
                    "t_start": t_start,
                    "t_end": t_start + delay,
                    "src_in": 0.0,
                    "src_out": delay,
                    "media_dur": 3600.0,
                    "scale_start": 100.0,
                    "scale_end": 100.0,
                    "center": (0.0, 0.0),
                    "crop": None,
                    "opacity_keyframes": None,
                    "link_id": None,
                })
                if s_png:
                    v_tracks[2].append({
                        "seg_idx": idx,
                        "name": s_png.name,
                        "path": s_png.resolve(),
                        "media_type": "still",
                        "t_start": t_start + delay,
                        "t_end": t_end,
                        "src_in": 0.0,
                        "src_out": max(0.04, dur - delay),
                        "media_dur": 3600.0,
                        "scale_start": 100.0,
                        "scale_end": 100.0,
                        "center": (0.0, 0.0),
                        "crop": None,
                        "opacity_keyframes": None,
                        "link_id": None,
                    })
            elif s_png:
                v_tracks[2].append({
                    "seg_idx": idx,
                    "name": s_png.name,
                    "path": s_png.resolve(),
                    "media_type": "still",
                    "t_start": t_start,
                    "t_end": t_end,
                    "src_in": 0.0,
                    "src_out": dur,
                    "media_dur": 3600.0,
                    "scale_start": 100.0,
                    "scale_end": 100.0,
                    "center": (0.0, 0.0),
                    "crop": None,
                    "opacity_keyframes": None,
                    "link_id": None,
                })

        # Explicit SFX for this segment from plan["sfx"] or kinetic transition whoosh
        seg_sfx_list = list(sfx_by_seg.get(seg_id, []))
        _whoosh_trs = {"whip_left", "whip_right", "flash_zoom", "zoom_burst", "slide_up", "smoothleft", "smoothright", "warm_flash"}
        if str(seg.get("transition") or "").lower() in _whoosh_trs and not any("whoosh" in str(x.get("file", "")) for x in seg_sfx_list):
            seg_sfx_list.append({"at": 0.0, "file": "whoosh.wav"})
        for sx in seg_sfx_list:
            sx_file = str(sx.get("file") or "whoosh.wav")
            sx_at = float(sx.get("at") or 0.0)
            sx_path = (REPO / "assets" / "sfx" / sx_file).resolve()
            if sx_path.exists():
                place_sfx(sx_path, max(0.0, t_start + sx_at), gain_db=float(sx.get("gain_db", -2.0)), max_dur=0.65)

        # V6 (index 5): ✓ / ✗ Marks + Buzzer / Ding SFX
        seg_marks = list(seg.get("marks", []) or []) + list(marks_by_seg.get(seg_id, []))
        for mk in seg_marks:
            kind = str(mk.get("kind") or mk.get("type") or "right").lower()
            if kind in ("x", "cross", "error", "wrong"):
                kind = "wrong"
            else:
                kind = "right"
            mk_at = float(mk.get("at", max(0.0, dur - 0.60)))
            mk_dur = float(mk.get("duration", 0.56) or 0.56)
            mk_start = t_start + max(0.0, min(mk_at, max(0.0, dur - 0.1)))
            mk_end = min(t_end, mk_start + mk_dur)
            if mk_end - mk_start < 0.08:
                mk_start = max(t_start, t_end - 0.56)
                mk_end = t_end
            actual_mk_dur = max(0.08, mk_end - mk_start)

            if mark_pack == "vignette":
                png_name = "w42_green_vignette.png" if kind == "right" else "w42_red_vignette.png"
            else:
                png_name = "green_flash.png" if kind == "right" else "red_flash.png"
            mk_png = (REPO / "assets" / "marks" / png_name).resolve()
            if mk_png.exists():
                v_tracks[5].append({
                    "seg_idx": idx,
                    "name": f"{kind.upper()}_{seg_id}_{mk_png.stem}",
                    "path": mk_png,
                    "media_type": "still",
                    "t_start": mk_start,
                    "t_end": mk_end,
                    "src_in": 0.0,
                    "src_out": actual_mk_dur,
                    "media_dur": 3600.0,
                    "scale_start": 100.0,
                    "scale_end": 100.0,
                    "center": (0.0, 0.0),
                    "crop": None,
                    "opacity_keyframes": [
                        (0.00, 0.0),
                        (0.03, 50.0),
                        (0.17, 50.0),
                        (0.20, 0.0),
                        (0.30, 0.0),
                        (0.33, 50.0),
                        (0.50, 50.0),
                        (0.56, 0.0),
                    ],
                    "link_id": None,
                })

            if kind == "wrong":
                place_sfx(REPO / "assets" / "sfx" / "buzzer.wav", mk_start, gain_db=4.0, max_dur=0.65)
                place_sfx(REPO / "assets" / "sfx" / "kn20228_wrong_core.mp3", mk_start, gain_db=3.0, max_dur=0.65)
            else:
                place_sfx(REPO / "assets" / "sfx" / "correct_ding.wav", mk_start, gain_db=2.5, max_dur=0.65)
                place_sfx(REPO / "assets" / "sfx" / "sn_correct.wav", mk_start, gain_db=2.0, max_dur=0.65)

            markers.append({
                "t": mk_start,
                "name": f"{'✓ RIGHT' if kind == 'right' else '✗ WRONG'} ({seg_id})",
                "comment": f"{kind.upper()} 2-pulse mark ({mark_pack})",
            })

        cursor = t_end

    # Compact empty overlay video tracks while preserving V1 and V2
    compacted_v = v_tracks[:2] + [vt for vt in v_tracks[2:] if vt]
    while len(compacted_v) < 4:
        compacted_v.append([])

    return {
        "total_duration": cursor,
        "fps": fps,
        "width": width,
        "height": height,
        "v_tracks": compacted_v,
        "a_tracks": a_tracks,
        "markers": markers,
    }


def _write_premiere_xml(model: dict, seq_name: str, out_xml: pathlib.Path) -> pathlib.Path:
    """Write Final Cut Pro 7 / Adobe Premiere Pro xmeml version='4' (.premiere.xml)."""
    fps = model["fps"]
    width = model["width"]
    height = model["height"]
    total_frames = max(1, _sec_to_frames(model["total_duration"], fps))

    file_registry: dict[str, str] = {}
    next_file_idx = 1

    def get_file_xml(path: pathlib.Path, media_type: str, media_dur: float) -> str:
        nonlocal next_file_idx
        key = str(path.resolve())
        if key in file_registry:
            return f'<file id="{file_registry[key]}"/>'
        fid = f"file-{next_file_idx}"
        next_file_idx += 1
        file_registry[key] = fid
        dur_frames = max(1, _sec_to_frames(media_dur, fps))
        url = escape(_path_to_url(path))
        fname = escape(path.name)
        if media_type == "still":
            media_block = f"""
              <media>
                <video>
                  <duration>{dur_frames}</duration>
                  <samplecharacteristics>
                    <rate><timebase>{fps}</timebase><ntsc>FALSE</ntsc></rate>
                    <width>{width}</width>
                    <height>{height}</height>
                    <anamorphic>FALSE</anamorphic>
                    <pixelaspectratio>square</pixelaspectratio>
                    <fielddominance>none</fielddominance>
                  </samplecharacteristics>
                </video>
              </media>"""
        elif media_type == "audio":
            media_block = f"""
              <media>
                <audio>
                  <samplecharacteristics>
                    <depth>16</depth>
                    <samplerate>{AUDIO_SAMPLE_RATE}</samplerate>
                  </samplecharacteristics>
                  <channelcount>2</channelcount>
                </audio>
              </media>"""
        else:
            media_block = f"""
              <media>
                <video>
                  <duration>{dur_frames}</duration>
                  <samplecharacteristics>
                    <rate><timebase>{fps}</timebase><ntsc>FALSE</ntsc></rate>
                    <width>{width}</width>
                    <height>{height}</height>
                    <anamorphic>FALSE</anamorphic>
                    <pixelaspectratio>square</pixelaspectratio>
                    <fielddominance>none</fielddominance>
                  </samplecharacteristics>
                </video>
                <audio>
                  <samplecharacteristics>
                    <depth>16</depth>
                    <samplerate>{AUDIO_SAMPLE_RATE}</samplerate>
                  </samplecharacteristics>
                  <channelcount>2</channelcount>
                </audio>
              </media>"""
        return f"""<file id="{fid}">
              <name>{fname}</name>
              <pathurl>{url}</pathurl>
              <rate><timebase>{fps}</timebase><ntsc>FALSE</ntsc></rate>
              <duration>{dur_frames}</duration>{media_block}
            </file>"""

    lines: list[str] = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<!DOCTYPE xmeml>',
        '<xmeml version="4">',
        f'  <sequence id="sequence-1">',
        f'    <uuid>{uuid.uuid4()}</uuid>',
        f'    <name>{escape(seq_name)}</name>',
        f'    <duration>{total_frames}</duration>',
        f'    <rate><timebase>{fps}</timebase><ntsc>FALSE</ntsc></rate>',
        f'    <timecode>',
        f'      <rate><timebase>{fps}</timebase><ntsc>FALSE</ntsc></rate>',
        f'      <string>00:00:00:00</string>',
        f'      <frame>0</frame>',
        f'      <displayformat>NDF</displayformat>',
        f'    </timecode>',
        f'    <media>',
        f'      <video>',
        f'        <format>',
        f'          <samplecharacteristics>',
        f'            <rate><timebase>{fps}</timebase><ntsc>FALSE</ntsc></rate>',
        f'            <width>{width}</width>',
        f'            <height>{height}</height>',
        f'            <anamorphic>FALSE</anamorphic>',
        f'            <pixelaspectratio>square</pixelaspectratio>',
        f'            <fielddominance>none</fielddominance>',
        f'          </samplecharacteristics>',
        f'        </format>',
    ]

    clipitem_counter = 1
    link_map: dict[str, dict] = {}

    # Pre-assign IDs for linked V1/V2 + A1 items
    for vt_idx in range(min(2, len(model["v_tracks"]))):
        for vc_idx, v_item in enumerate(model["v_tracks"][vt_idx]):
            v_item["xml_id"] = f"clipitem-v{vt_idx+1}-{clipitem_counter}"
            clipitem_counter += 1
            if v_item.get("link_id"):
                info = link_map.setdefault(v_item["link_id"], {})
                info["v"] = v_item["xml_id"]
                info["v_track"] = vt_idx + 1
                info["v_index"] = vc_idx + 1
    for ac_idx, a_item in enumerate(model["a_tracks"][0]):
        a_item["xml_id"] = f"clipitem-a1-{clipitem_counter}"
        clipitem_counter += 1
        if a_item.get("link_id"):
            info = link_map.setdefault(a_item["link_id"], {})
            info["a"] = a_item["xml_id"]
            info["a_track"] = 1
            info["a_index"] = ac_idx + 1

    # Emit Video Tracks
    for t_idx, v_track in enumerate(model["v_tracks"]):
        if not v_track and t_idx > 1:
            continue
        lines.append("        <track>")
        for item in v_track:
            cid = item.get("xml_id") or f"clipitem-v{t_idx+1}-{clipitem_counter}"
            clipitem_counter += 1
            start_f = _sec_to_frames(item["t_start"], fps)
            end_f = max(start_f + 1, _sec_to_frames(item["t_end"], fps))
            dur_f = end_f - start_f
            in_f = _sec_to_frames(item["src_in"], fps)
            out_f = in_f + dur_f
            file_xml = get_file_xml(item["path"], item["media_type"], item["media_dur"])

            # Basic Motion filter (with multi-keyframe eased Scale curve)
            s0 = item["scale_start"]
            s1 = item["scale_end"]
            cx, cy = item["center"]
            scale_kfs = item.get("scale_keyframes") or []
            if scale_kfs:
                scale_kf = "".join(
                    f"\n                  <keyframe><when>{min(out_f, in_f + _sec_to_frames(rel_s, fps))}</when><value>{val}</value></keyframe>"
                    for rel_s, val in scale_kfs
                )
            elif abs(s0 - s1) > 0.05:
                scale_kf = f"""
                  <keyframe><when>{ in_f }</when><value>{s0}</value></keyframe>
                  <keyframe><when>{ out_f }</when><value>{s1}</value></keyframe>"""
            else:
                scale_kf = ""

            filters_xml = f"""
              <filter>
                <effect>
                  <name>Basic Motion</name>
                  <effectid>basic</effectid>
                  <effectcategory>motion</effectcategory>
                  <effecttype>motion</effecttype>
                  <mediatype>video</mediatype>
                  <parameter>
                    <parameterid>scale</parameterid>
                    <name>Scale</name>
                    <valuemin>0</valuemin>
                    <valuemax>1000</valuemax>
                    <value>{s0}</value>{scale_kf}
                  </parameter>
                  <parameter>
                    <parameterid>center</parameterid>
                    <name>Center</name>
                    <value><horiz>{cx}</horiz><vert>{cy}</vert></value>
                  </parameter>
                </effect>
              </filter>"""

            if item.get("crop"):
                c = item["crop"]
                filters_xml += f"""
              <filter>
                <effect>
                  <name>Crop</name>
                  <effectid>crop</effectid>
                  <effectcategory>motion</effectcategory>
                  <effecttype>motion</effecttype>
                  <mediatype>video</mediatype>
                  <parameter><parameterid>left</parameterid><name>left</name><valuemin>0</valuemin><valuemax>100</valuemax><value>{c['left']}</value></parameter>
                  <parameter><parameterid>right</parameterid><name>right</name><valuemin>0</valuemin><valuemax>100</valuemax><value>{c['right']}</value></parameter>
                  <parameter><parameterid>top</parameterid><name>top</name><valuemin>0</valuemin><valuemax>100</valuemax><value>{c['top']}</value></parameter>
                  <parameter><parameterid>bottom</parameterid><name>bottom</name><valuemin>0</valuemin><valuemax>100</valuemax><value>{c['bottom']}</value></parameter>
                </effect>
              </filter>"""

            if item.get("opacity_keyframes"):
                kf_xml = ""
                for rel_s, val in item["opacity_keyframes"]:
                    kf_f = min(out_f, in_f + _sec_to_frames(rel_s, fps))
                    kf_xml += f"\n                  <keyframe><when>{kf_f}</when><value>{val}</value></keyframe>"
                filters_xml += f"""
              <filter>
                <effect>
                  <name>Opacity</name>
                  <effectid>opacity</effectid>
                  <effectcategory>motion</effectcategory>
                  <effecttype>motion</effecttype>
                  <mediatype>video</mediatype>
                  <parameter>
                    <parameterid>opacity</parameterid>
                    <name>opacity</name>
                    <valuemin>0</valuemin>
                    <valuemax>100</valuemax>
                    <value>50</value>{kf_xml}
                  </parameter>
                </effect>
              </filter>"""

            link_xml = ""
            lid = item.get("link_id")
            if lid and lid in link_map and "a" in link_map[lid] and "v" in link_map[lid]:
                linfo = link_map[lid]
                link_xml = f"""
              <link>
                <linkclipref>{linfo['v']}</linkclipref>
                <mediatype>video</mediatype>
                <trackindex>{linfo['v_track']}</trackindex>
                <clipindex>{linfo['v_index']}</clipindex>
              </link>
              <link>
                <linkclipref>{linfo['a']}</linkclipref>
                <mediatype>audio</mediatype>
                <trackindex>{linfo['a_track']}</trackindex>
                <clipindex>{linfo['a_index']}</clipindex>
                <groupindex>1</groupindex>
              </link>"""

            alpha_tag = "<alphatype>straight</alphatype>" if item["media_type"] == "still" else ""
            lines.append(f"""          <clipitem id="{cid}">
            <name>{escape(item['name'])}</name>
            <enabled>TRUE</enabled>
            <duration>{max(dur_f, _sec_to_frames(item['media_dur'], fps))}</duration>
            <rate><timebase>{fps}</timebase><ntsc>FALSE</ntsc></rate>
            <start>{start_f}</start>
            <end>{end_f}</end>
            <in>{in_f}</in>
            <out>{out_f}</out>
            {alpha_tag}
            {file_xml}{filters_xml}{link_xml}
          </clipitem>""")
        lines.append("        </track>")

    lines.append("      </video>")
    lines.append("      <audio>")
    lines.append("        <numOutputChannels>2</numOutputChannels>")
    lines.append("        <format><samplecharacteristics><depth>16</depth><samplerate>48000</samplerate></samplecharacteristics></format>")

    for t_idx, a_track in enumerate(model["a_tracks"]):
        if not a_track and t_idx > 0:
            continue
        lines.append("        <track>")
        for item in a_track:
            cid = item.get("xml_id") or f"clipitem-a{t_idx+1}-{clipitem_counter}"
            clipitem_counter += 1
            start_f = _sec_to_frames(item["t_start"], fps)
            end_f = max(start_f + 1, _sec_to_frames(item["t_end"], fps))
            dur_f = end_f - start_f
            in_f = _sec_to_frames(item["src_in"], fps)
            out_f = in_f + dur_f
            file_xml = get_file_xml(
                item["path"],
                "video" if item["media_type"] == "audio_from_video" else "audio",
                item["media_dur"],
            )
            lvl = _db_to_linear(item.get("gain_db", 0.0))
            a_link_xml = ""
            lid = item.get("link_id")
            if lid and lid in link_map and "a" in link_map[lid] and "v" in link_map[lid]:
                linfo = link_map[lid]
                a_link_xml = f"""
            <link>
              <linkclipref>{linfo['v']}</linkclipref>
              <mediatype>video</mediatype>
              <trackindex>{linfo['v_track']}</trackindex>
              <clipindex>{linfo['v_index']}</clipindex>
            </link>
            <link>
              <linkclipref>{linfo['a']}</linkclipref>
              <mediatype>audio</mediatype>
              <trackindex>{linfo['a_track']}</trackindex>
              <clipindex>{linfo['a_index']}</clipindex>
              <groupindex>1</groupindex>
            </link>"""
            lines.append(f"""          <clipitem id="{cid}">
            <name>{escape(item['name'])}</name>
            <enabled>TRUE</enabled>
            <duration>{max(dur_f, _sec_to_frames(item['media_dur'], fps))}</duration>
            <rate><timebase>{fps}</timebase><ntsc>FALSE</ntsc></rate>
            <start>{start_f}</start>
            <end>{end_f}</end>
            <in>{in_f}</in>
            <out>{out_f}</out>
            {file_xml}
            <sourcetrack>
              <mediatype>audio</mediatype>
              <trackindex>1</trackindex>
            </sourcetrack>
            <filter>
              <effect>
                <name>Audio Levels</name>
                <effectid>audiolevels</effectid>
                <effectcategory>audiolevels</effectcategory>
                <effecttype>audiolevels</effecttype>
                <mediatype>audio</mediatype>
                <parameter>
                  <parameterid>level</parameterid>
                  <name>Level</name>
                  <valuemin>0</valuemin>
                  <valuemax>3.98109</valuemax>
                  <value>{lvl}</value>
                </parameter>
              </effect>
            </filter>{a_link_xml}
          </clipitem>""")
        lines.append("        </track>")

    lines.extend([
        "      </audio>",
        "    </media>",
    ])
    for mk in model.get("markers", []):
        mk_f = _sec_to_frames(float(mk.get("t", 0.0)), fps)
        lines.append(f"""    <marker>
      <name>{escape(str(mk.get('name', '')))}</name>
      <comment>{escape(str(mk.get('comment', '')))}</comment>
      <in>{mk_f}</in>
      <out>-1</out>
    </marker>""")
    lines.extend([
        "  </sequence>",
        "</xmeml>",
    ])

    out_xml.parent.mkdir(parents=True, exist_ok=True)
    out_xml.write_text("\n".join(lines), encoding="utf-8")
    return out_xml


def _write_native_prproj(model: dict, seq_name: str, out_prproj: pathlib.Path) -> pathlib.Path:
    """
    Write a native GZIP-compressed Adobe Premiere Pro 2026 project file (.prproj)
    by populating assets/premiere/base_1080x1920.prproj.gz with the Media, MasterClips,
    SubClips, VideoClipTrackItems, AudioClipTrackItems, and Links from `model`.
    """
    base_gz = REPO / "assets" / "premiere" / "base_1080x1920.prproj.gz"
    if not base_gz.exists():
        raise FileNotFoundError(f"Missing base Premiere template: {base_gz}")

    with gzip.open(base_gz, "rb") as f:
        root = ET.fromstring(f.read())

    by_id: dict[str, ET.Element] = {}
    max_int_id = 100000
    for el in root:
        if "ObjectID" in el.attrib:
            oid = el.attrib["ObjectID"]
            by_id[oid] = el
            if oid.isdigit():
                max_int_id = max(max_int_id, int(oid))
        if "ObjectUID" in el.attrib:
            by_id[el.attrib["ObjectUID"]] = el

    next_oid = max_int_id + 100
    next_node_id = 2000000

    def alloc_oid() -> str:
        nonlocal next_oid
        val = str(next_oid)
        next_oid += 1
        return val

    def alloc_node_id() -> str:
        nonlocal next_node_id
        val = str(next_node_id)
        next_node_id += 1
        return val

    fps = model["fps"]
    fps_ticks = PREMIERE_TICKS_PER_SEC // fps
    width = model["width"]
    height = model["height"]
    total_ticks = max(fps_ticks, _sec_to_ticks(model["total_duration"]))

    # Update Sequence name & duration
    seq_el = by_id["e897a169-5f00-4dbd-bb6a-311c4a933f39"]
    for name_owner_id in (
        "e897a169-5f00-4dbd-bb6a-311c4a933f39",
        "716f71ca-7fdb-457a-9db5-967729ec496c",
        "f35f8b89-5a44-4ce3-a064-4dc3c575131e",
    ):
        owner = by_id.get(name_owner_id)
        if owner is not None:
            for nm in owner.findall(".//Name"):
                nm.text = seq_name
    for tag_name in ("MZ.WorkOutPoint", "MZ.OutPoint", "Monitor.ProgramZoomOut"):
        node = seq_el.find(f".//{tag_name}")
        if node is not None:
            node.text = str(total_ticks)
    for tag_name, val_str in (
        ("MZ.EditLine", "0"),
        ("TL.SQVisibleBaseTime", "0"),
        ("TL.SQTimePerPixel", "0.09"),
        ("MZ.Sequence.VideoTimeDisplayFormat", "101" if fps == 25 else "104"),
    ):
        node = seq_el.find(f".//{tag_name}")
        if node is not None:
            node.text = val_str

    # Locate VideoClipTracks (0..7) and AudioClipTracks (0..7) and set Sequence FrameRate
    v_tg = by_id["167"]
    for tg_id in ("167", "169"):
        tg_obj = by_id.get(tg_id)
        if tg_obj is not None:
            fr_el = tg_obj.find(".//FrameRate")
            if fr_el is not None:
                fr_el.text = str(fps_ticks)
    v_track_els = [
        by_id[tr.attrib.get("ObjectRef") or tr.attrib.get("ObjectURef")]
        for tr in v_tg.findall(".//Track")
    ]
    a_tg = by_id["168"]
    a_track_els = [
        by_id[tr.attrib.get("ObjectRef") or tr.attrib.get("ObjectURef")]
        for tr in a_tg.findall(".//Track")
    ]
    links_el = seq_el.find(".//Links")
    if links_el is not None:
        links_el.set("Version", "1")
    for tag_v in ("TrackItems", "Links", "PreviewItems", "TransitionItems"):
        for el_v in root.iter(tag_v):
            if "Version" not in el_v.attrib:
                el_v.set("Version", "1")
    rpi_items_el = by_id["7bac5d15-810e-4df0-a243-6024225b5bf3"].find(".//Items")
    grid_order = 1

    # Register unique media files as MasterClips in the Root Bin
    master_registry: dict[str, dict] = {}
    new_elements: list[ET.Element] = []

    def register_media(path: pathlib.Path, media_type: str, media_dur: float) -> dict:
        nonlocal grid_order
        key = str(path.resolve())
        if key in master_registry:
            return master_registry[key]

        abs_str = str(path.resolve())
        fname = path.name
        dur_ticks = 10973491200000000 if media_type == "still" else max(fps_ticks, _sec_to_ticks(media_dur))
        media_uid = str(uuid.uuid4())
        mc_uid = str(uuid.uuid4())
        cpi_uid = str(uuid.uuid4())
        state_guid = str(uuid.uuid4())
        mod_b64 = base64.b64encode(state_guid.encode("utf-16le")).decode("ascii")

        markers_id = alloc_oid()
        new_elements.append(ET.fromstring(f"""
        <Markers ObjectID="{markers_id}" ClassID="bee50706-b524-416c-9f03-b596ce5f6866" Version="4">
            <ByGUID>byGUID</ByGUID>
            <LastMetadataState>00000000-0000-0000-0000-000000000000</LastMetadataState>
            <LastContentState>{state_guid}</LastContentState>
        </Markers>"""))

        vms_id = None
        ams_id = None
        master_vc_id = None
        master_ac_id = None

        if media_type == "still":
            vs_id = alloc_oid()
            new_elements.append(ET.fromstring(f"""
            <VideoStream ObjectID="{vs_id}" ClassID="a36e4719-3ec6-4a0c-ab11-8b4aab377aa5" Version="22">
                <CodecType>1886283552</CodecType>
                <IsStill>true</IsStill>
                <OriginalColorSpace>{{"baseColorProfile":{{"colorProfileName":"BT.709 RGB Full"}},"baseProfileType":1}}</OriginalColorSpace>
                <FieldTypeIsUncertain>true</FieldTypeIsUncertain>
                <FrameRate>{fps_ticks}</FrameRate>
                <Duration>{dur_ticks}</Duration>
                <FrameRect>0,0,{width},{height}</FrameRect>
                <AlphaType>1</AlphaType>
            </VideoStream>"""))
            new_elements.append(ET.fromstring(f"""
            <Media ObjectUID="{media_uid}" ClassID="7a5c103e-f3ac-4391-b6b4-7cc3d2f9a7ff" Version="30">
                <VideoStream ObjectRef="{vs_id}" />
                <ModificationState Encoding="base64" BinaryHash="{uuid.uuid4()}">{mod_b64}</ModificationState>
                <FilePath>{escape(abs_str)}</FilePath>
                <ImplementationID>1fa18bfa-255c-44b1-ad73-56bcd99fceaf</ImplementationID>
                <FileKey>{uuid.uuid4()}</FileKey>
                <ConformedAudioRate>5292000</ConformedAudioRate>
                <ContentAndMetadataState>{state_guid}</ContentAndMetadataState>
                <Infinite>true</Infinite>
                <Title>{escape(fname)}</Title>
                <ActualMediaFilePath>{escape(abs_str)}</ActualMediaFilePath>
            </Media>"""))
            vms_id = alloc_oid()
            new_elements.append(ET.fromstring(f"""
            <VideoMediaSource ObjectID="{vms_id}" ClassID="e64ddf74-8fac-4682-8aa8-0e0ca2248949" Version="2">
                <MediaSource Version="4">
                    <Content Version="10"></Content>
                    <Media ObjectURef="{media_uid}" />
                </MediaSource>
                <OriginalDuration>{dur_ticks}</OriginalDuration>
            </VideoMediaSource>"""))
            master_vc_id = alloc_oid()
            new_elements.append(ET.fromstring(f"""
            <VideoClip ObjectID="{master_vc_id}" ClassID="9308dbef-2440-4acb-9ab2-953b9a4e82ec" Version="11">
                <Clip Version="18">
                    <Node Version="1"><Properties Version="1">
                        <asl.clip.label.name>BE.Prefs.LabelColors.3</asl.clip.label.name>
                        <asl.clip.label.color>8851829</asl.clip.label.color>
                    </Properties></Node>
                    <MarkerOwner Version="1"><Markers ObjectRef="{markers_id}" /></MarkerOwner>
                    <Source ObjectRef="{vms_id}" />
                    <ClipID>{uuid.uuid4()}</ClipID>
                </Clip>
                <PosterFrame>0</PosterFrame>
            </VideoClip>"""))
            log_id = alloc_oid()
            new_elements.append(ET.fromstring(f"""
            <ClipLoggingInfo ObjectID="{log_id}" ClassID="77ab7fdd-dcdf-465d-9906-7a330ca1e738" Version="10">
                <ClipName>{escape(fname)}</ClipName>
                <MediaInPoint>0</MediaInPoint>
                <MediaOutPoint>{fps_ticks * fps * 5}</MediaOutPoint>
                <MediaFrameRate>{fps_ticks}</MediaFrameRate>
                <TimecodeFormat>106</TimecodeFormat>
            </ClipLoggingInfo>"""))
            ccg_id = alloc_oid()
            new_elements.append(ET.fromstring(f"""
            <ClipChannelGroupVectorSerializer ObjectID="{ccg_id}" ClassID="a3127a8c-95d4-456e-a7f5-171b3f922426" Version="1" />"""))
            new_elements.append(ET.fromstring(f"""
            <MasterClip ObjectUID="{mc_uid}" ClassID="fb11c33a-b0a9-4465-aa94-b6d5db2628cf" Version="12">
                <LoggingInfo ObjectRef="{log_id}" />
                <Clips Version="1">
                    <Clip Index="0" ObjectRef="{master_vc_id}" />
                </Clips>
                <AudioClipChannelGroups ObjectRef="{ccg_id}" />
                <Name>{escape(fname)}</Name>
                <MasterClipChangeVersion>3</MasterClipChangeVersion>
            </MasterClip>"""))
        else:
            has_video = media_type in ("video", "audio_from_video")
            as_id = alloc_oid()
            new_elements.append(ET.fromstring(f"""
            <AudioStream ObjectID="{as_id}" ClassID="0b5cf52f-2b85-4863-890b-8844b64ecfe9" Version="8">
                <AudioChannelLayout>[{{"channellabel":100}},{{"channellabel":101}}]</AudioChannelLayout>
                <SampleType>7</SampleType>
                <FrameRate>{AUDIO_TICKS_PER_SAMPLE}</FrameRate>
                <Duration>{dur_ticks}</Duration>
            </AudioStream>"""))
            vs_xml = ""
            if has_video:
                vs_id = alloc_oid()
                new_elements.append(ET.fromstring(f"""
                <VideoStream ObjectID="{vs_id}" ClassID="a36e4719-3ec6-4a0c-ab11-8b4aab377aa5" Version="22">
                    <CodecType>1635148593</CodecType>
                    <OriginalColorSpace>{{"baseColorProfile":{{"colorProfileData":"AQAAAP////8=","colorProfileName":"BT.709,8-bit,Display-Referred"}},"baseProfileType":1}}</OriginalColorSpace>
                    <FieldTypeIsUncertain>true</FieldTypeIsUncertain>
                    <OriginalImageOrientationType>1</OriginalImageOrientationType>
                    <FrameRate>{fps_ticks}</FrameRate>
                    <Duration>{dur_ticks}</Duration>
                    <FrameRect>0,0,{width},{height}</FrameRect>
                    <AlphaType>3</AlphaType>
                    <IgnoreAlpha>true</IgnoreAlpha>
                </VideoStream>"""))
                vs_xml = f'<VideoStream ObjectRef="{vs_id}" />'

            new_elements.append(ET.fromstring(f"""
            <Media ObjectUID="{media_uid}" ClassID="7a5c103e-f3ac-4391-b6b4-7cc3d2f9a7ff" Version="30">
                <AudioStream ObjectRef="{as_id}" />
                {vs_xml}
                <ModificationState Encoding="base64" BinaryHash="{uuid.uuid4()}">{mod_b64}</ModificationState>
                <FilePath>{escape(abs_str)}</FilePath>
                <ImplementationID>1fa18bfa-255c-44b1-ad73-56bcd99fceaf</ImplementationID>
                <FileKey>{uuid.uuid4()}</FileKey>
                <ConformedAudioRate>{AUDIO_TICKS_PER_SAMPLE}</ConformedAudioRate>
                <ContentAndMetadataState>{state_guid}</ContentAndMetadataState>
                <Title>{escape(fname)}</Title>
                <ActualMediaFilePath>{escape(abs_str)}</ActualMediaFilePath>
            </Media>"""))

            ams_id = alloc_oid()
            new_elements.append(ET.fromstring(f"""
            <AudioMediaSource ObjectID="{ams_id}" ClassID="f588da05-fc2a-4fbc-9383-74d653b379e3" Version="2">
                <MediaSource Version="4">
                    <Content Version="10"></Content>
                    <Media ObjectURef="{media_uid}" />
                </MediaSource>
                <OriginalDuration>{dur_ticks}</OriginalDuration>
            </AudioMediaSource>"""))
            sc0_id, sc1_id = alloc_oid(), alloc_oid()
            new_elements.append(ET.fromstring(f'<SecondaryContent ObjectID="{sc0_id}" ClassID="f9d004b5-cb04-4e2f-af6f-64fadc2c4be9" Version="1"><Content ObjectRef="{ams_id}" /><ChannelIndex>0</ChannelIndex></SecondaryContent>'))
            new_elements.append(ET.fromstring(f'<SecondaryContent ObjectID="{sc1_id}" ClassID="f9d004b5-cb04-4e2f-af6f-64fadc2c4be9" Version="1"><Content ObjectRef="{ams_id}" /><ChannelIndex>1</ChannelIndex></SecondaryContent>'))

            master_ac_id = alloc_oid()
            new_elements.append(ET.fromstring(f"""
            <AudioClip ObjectID="{master_ac_id}" ClassID="b8830d03-de02-41ee-84ec-fe566dc70cd9" Version="8">
                <Clip Version="18">
                    <Node Version="1"><Properties Version="1">
                        <asl.clip.label.name>BE.Prefs.LabelColors.1</asl.clip.label.name>
                        <asl.clip.label.color>6769408</asl.clip.label.color>
                    </Properties></Node>
                    <MarkerOwner Version="1"><Markers ObjectRef="{markers_id}" /></MarkerOwner>
                    <Source ObjectRef="{ams_id}" />
                    <ClipID>{uuid.uuid4()}</ClipID>
                    <InUse>false</InUse>
                </Clip>
                <SecondaryContents Version="1">
                    <SecondaryContentItem Index="0" ObjectRef="{sc0_id}" />
                    <SecondaryContentItem Index="1" ObjectRef="{sc1_id}" />
                </SecondaryContents>
                <AudioChannelLayout>[{{"channellabel":100}},{{"channellabel":101}}]</AudioChannelLayout>
            </AudioClip>"""))

            clips_block = f'<Clip Index="0" ObjectRef="{master_ac_id}" />'
            if has_video:
                vms_id = alloc_oid()
                new_elements.append(ET.fromstring(f"""
                <VideoMediaSource ObjectID="{vms_id}" ClassID="e64ddf74-8fac-4682-8aa8-0e0ca2248949" Version="2">
                    <MediaSource Version="4">
                        <Content Version="10"></Content>
                        <Media ObjectURef="{media_uid}" />
                    </MediaSource>
                    <OriginalDuration>{dur_ticks}</OriginalDuration>
                </VideoMediaSource>"""))
                master_vc_id = alloc_oid()
                new_elements.append(ET.fromstring(f"""
                <VideoClip ObjectID="{master_vc_id}" ClassID="9308dbef-2440-4acb-9ab2-953b9a4e82ec" Version="11">
                    <Clip Version="18">
                        <Node Version="1"><Properties Version="1">
                            <asl.clip.label.name>BE.Prefs.LabelColors.1</asl.clip.label.name>
                            <asl.clip.label.color>6769408</asl.clip.label.color>
                        </Properties></Node>
                        <MarkerOwner Version="1"><Markers ObjectRef="{markers_id}" /></MarkerOwner>
                        <Source ObjectRef="{vms_id}" />
                        <ClipID>{uuid.uuid4()}</ClipID>
                        <InUse>false</InUse>
                    </Clip>
                </VideoClip>"""))
                clips_block = f'<Clip Index="0" ObjectRef="{master_vc_id}" /><Clip Index="1" ObjectRef="{master_ac_id}" />'

            log_id = alloc_oid()
            new_elements.append(ET.fromstring(f"""
            <ClipLoggingInfo ObjectID="{log_id}" ClassID="77ab7fdd-dcdf-465d-9906-7a330ca1e738" Version="10">
                <ClipName>{escape(fname)}</ClipName>
                <MediaInPoint>0</MediaInPoint>
                <MediaOutPoint>{dur_ticks}</MediaOutPoint>
                <MediaFrameRate>{fps_ticks}</MediaFrameRate>
                <TimecodeFormat>101</TimecodeFormat>
            </ClipLoggingInfo>"""))
            acc_id = alloc_oid()
            new_elements.append(ET.fromstring(f"""
            <AudioComponentChain ObjectID="{acc_id}" ClassID="3cb131d1-d3c0-47ae-a19a-bdf75ea11674" Version="4">
                <DefaultVol>true</DefaultVol>
                <DefaultVolumeComponentID>1</DefaultVolumeComponentID>
                <DefaultChannelVolumeComponentID>2</DefaultChannelVolumeComponentID>
                <ComponentChain Version="3"></ComponentChain>
                <AudioChannelLayout>[{{"channellabel":100}},{{"channellabel":101}}]</AudioChannelLayout>
                <ChannelType>1</ChannelType>
                <FrameRate>{AUDIO_TICKS_PER_SAMPLE}</FrameRate>
            </AudioComponentChain>"""))
            ccs0_id, ccs1_id, ccv_id, ccg_id = alloc_oid(), alloc_oid(), alloc_oid(), alloc_oid()
            new_elements.append(ET.fromstring(f'<ClipChannelSerializer ObjectID="{ccs0_id}" ClassID="5c89aa7a-89a6-4483-becd-f2b1def42316" Version="1"><SourceClipIndex>0</SourceClipIndex><mSourceChannelIndex>0</mSourceChannelIndex></ClipChannelSerializer>'))
            new_elements.append(ET.fromstring(f'<ClipChannelSerializer ObjectID="{ccs1_id}" ClassID="5c89aa7a-89a6-4483-becd-f2b1def42316" Version="1"><SourceClipIndex>0</SourceClipIndex><mSourceChannelIndex>1</mSourceChannelIndex></ClipChannelSerializer>'))
            new_elements.append(ET.fromstring(f'<ClipChannelVectorSerializer ObjectID="{ccv_id}" ClassID="333d203b-3a53-4195-8894-fc7523ff3dc7" Version="1"><ClipChannels Version="1"><ClipChannelItem Index="0" ObjectRef="{ccs0_id}" /><ClipChannelItem Index="1" ObjectRef="{ccs1_id}" /></ClipChannels><ChannelType>1</ChannelType></ClipChannelVectorSerializer>'))
            new_elements.append(ET.fromstring(f'<ClipChannelGroupVectorSerializer ObjectID="{ccg_id}" ClassID="a3127a8c-95d4-456e-a7f5-171b3f922426" Version="1"><ClipChannelVectors Version="1"><ClipChannelVectorItem Index="0" ObjectRef="{ccv_id}" /></ClipChannelVectors></ClipChannelGroupVectorSerializer>'))

            new_elements.append(ET.fromstring(f"""
            <MasterClip ObjectUID="{mc_uid}" ClassID="fb11c33a-b0a9-4465-aa94-b6d5db2628cf" Version="12">
                <Node Version="1"><ID>{alloc_node_id()}</ID></Node>
                <LoggingInfo ObjectRef="{log_id}" />
                <AudioComponentChains Version="1"><AudioComponentChain Index="0" ObjectRef="{acc_id}" /></AudioComponentChains>
                <Clips Version="1">{clips_block}</Clips>
                <AudioClipChannelGroups ObjectRef="{ccg_id}" />
                <DefMappingID>0551e2a0-6da1-4691-800e-b3ded5ca94f7</DefMappingID>
                <Name>{escape(fname)}</Name>
                <MasterClipChangeVersion>8</MasterClipChangeVersion>
            </MasterClip>"""))

        new_elements.append(ET.fromstring(f"""
        <ClipProjectItem ObjectUID="{cpi_uid}" ClassID="cb4e0ed7-aca1-4171-8525-e3658dec06dd" Version="1">
            <ProjectItem Version="1">
                <Node Version="1">
                    <Properties Version="1">
                        <Column.PropertyText.Label>BE.Prefs.LabelColors.1</Column.PropertyText.Label>
                        <project.icon.view.grid.order>{grid_order}</project.icon.view.grid.order>
                    </Properties>
                    <ID>{alloc_node_id()}</ID>
                </Node>
                <Name>{escape(fname)}</Name>
            </ProjectItem>
            <MasterClip ObjectURef="{mc_uid}" />
        </ClipProjectItem>"""))
        rpi_items_el.append(ET.fromstring(f'<Item Index="{grid_order}" ObjectURef="{cpi_uid}" />'))
        grid_order += 1

        info = {
            "mc_uid": mc_uid,
            "vms_id": vms_id,
            "ams_id": ams_id,
            "markers_id": markers_id,
            "fname": fname,
        }
        master_registry[key] = info
        return info

    link_pairs: dict[str, dict[str, str]] = {}

    def _ensure_track_items_el(track_el: ET.Element) -> ET.Element:
        ti = track_el.find(".//ClipItems/TrackItems")
        if ti is None:
            ci = track_el.find(".//ClipItems")
            ti = ET.Element("TrackItems", {"Version": "1"})
            ci.insert(0, ti)
        else:
            ti.set("Version", "1")
        return ti

    def _build_video_component_chain(item: dict, in_ticks: int, out_ticks: int) -> str:
        s0 = float(item.get("scale_start", 100.0))
        s1 = float(item.get("scale_end", s0))
        cx, cy = item.get("center") or (0.0, 0.0)
        scale_kfs = item.get("scale_keyframes") or []
        crop = item.get("crop")
        has_custom_motion = (
            abs(s0 - 100.0) > 0.05
            or abs(s1 - 100.0) > 0.05
            or bool(scale_kfs)
            or abs(cx) > 0.001
            or abs(cy) > 0.001
            or bool(crop)
        )
        vcc_id = alloc_oid()
        if not has_custom_motion:
            new_elements.append(ET.fromstring(f"""
            <VideoComponentChain ObjectID="{vcc_id}" ClassID="0970e08a-f58f-4108-b29a-1a717b8e12e2" Version="3">
                <DefaultMotion>true</DefaultMotion>
                <DefaultOpacity>true</DefaultOpacity>
                <DefaultMotionComponentID>1</DefaultMotionComponentID>
                <DefaultOpacityComponentID>2</DefaultOpacityComponentID>
                <ComponentChain Version="3">
                    <Node Version="1"><Properties Version="1">
                        <MZ.ComponentChain.ActiveComponentID>2</MZ.ComponentChain.ActiveComponentID>
                        <MZ.ComponentChain.ActiveComponentParamIndex>4294967295</MZ.ComponentChain.ActiveComponentParamIndex>
                    </Properties></Node>
                </ComponentChain>
            </VideoComponentChain>"""))
            return vcc_id

        pos_x = round(0.5 + float(cx), 6)
        pos_y = round(0.5 + float(cy), 6)
        c_left = float(crop.get("left", 0.0)) if crop else 0.0
        c_top = float(crop.get("top", 0.0)) if crop else 0.0
        c_right = float(crop.get("right", 0.0)) if crop else 0.0
        c_bottom = float(crop.get("bottom", 0.0)) if crop else 0.0

        p_ids = [alloc_oid() for _ in range(11)]
        # Param 0: Position
        new_elements.append(ET.fromstring(f"""
        <PointComponentParam ObjectID="{p_ids[0]}" ClassID="ca81d347-309b-44d2-acc7-1c572efb973c" Version="4">
            <Name>Position</Name>
            <ParameterID>1</ParameterID>
            <StartKeyframe>-91445760000000000,{pos_x}:{pos_y},0,0,0,0,0,0,5,4,0,0,0,0</StartKeyframe>
        </PointComponentParam>"""))

        # Param 1: Scale
        kf_tags = ""
        if scale_kfs:
            kf_entries = ";".join(
                f"{min(out_ticks, in_ticks + _sec_to_ticks(rel_s))},{val},5,0,0,0.33333333333333331,0,0.33333333333333331"
                for rel_s, val in scale_kfs
            ) + ";"
            kf_tags = f"<IsTimeVarying>true</IsTimeVarying><Keyframes>{kf_entries}</Keyframes>"
        elif abs(s1 - s0) > 0.05:
            kf_entries = (
                f"{in_ticks},{s0},5,0,0,0.33333333333333331,0,0.33333333333333331;"
                f"{out_ticks},{s1},5,0,0,0.33333333333333331,0,0.33333333333333331;"
            )
            kf_tags = f"<IsTimeVarying>true</IsTimeVarying><Keyframes>{kf_entries}</Keyframes>"

        new_elements.append(ET.fromstring(f"""
        <VideoComponentParam ObjectID="{p_ids[1]}" ClassID="fe47129e-6c94-4fc0-95d5-c056a517aaf3" Version="10">
            <Name>Scale</Name>
            <UpperUIBound>200</UpperUIBound>
            <ParameterID>2</ParameterID>
            {kf_tags}
            <StartKeyframe>-91445760000000000,{s0},0,0,0,0,0,0</StartKeyframe>
            <CurrentValue>{s0}</CurrentValue>
            <LowerBound>0</LowerBound>
            <UpperBound>10000</UpperBound>
        </VideoComponentParam>"""))

        # Param 2: Scale Width
        new_elements.append(ET.fromstring(f"""
        <VideoComponentParam ObjectID="{p_ids[2]}" ClassID="fe47129e-6c94-4fc0-95d5-c056a517aaf3" Version="10">
            <Name>Scale Width</Name>
            <UpperUIBound>200</UpperUIBound>
            <ParameterID>3</ParameterID>
            <StartKeyframe>-91445760000000000,{s0},0,0,0,0,0,0</StartKeyframe>
            <LowerBound>0</LowerBound>
            <UpperBound>10000</UpperBound>
        </VideoComponentParam>"""))

        # Param 3: Uniform Scale
        new_elements.append(ET.fromstring(f"""
        <VideoComponentParam ObjectID="{p_ids[3]}" ClassID="cc12343e-f113-4d3b-ae05-b287db77d461" Version="10">
            <Name> </Name>
            <ParameterID>4</ParameterID>
            <StartKeyframe>-91445760000000000,true,0,0,0,0,0,0</StartKeyframe>
        </VideoComponentParam>"""))

        # Param 4: Rotation
        new_elements.append(ET.fromstring(f"""
        <VideoComponentParam ObjectID="{p_ids[4]}" ClassID="fe47129e-6c94-4fc0-95d5-c056a517aaf3" Version="10">
            <Name>Rotation</Name>
            <ParameterControlType>3</ParameterControlType>
            <ParameterID>5</ParameterID>
            <StartKeyframe>-91445760000000000,0.,0,0,0,0,0,0</StartKeyframe>
            <LowerBound>-32768</LowerBound>
            <UpperBound>32767</UpperBound>
        </VideoComponentParam>"""))

        # Param 5: Anchor Point
        new_elements.append(ET.fromstring(f"""
        <PointComponentParam ObjectID="{p_ids[5]}" ClassID="ca81d347-309b-44d2-acc7-1c572efb973c" Version="4">
            <Name>Anchor Point</Name>
            <ParameterID>6</ParameterID>
            <StartKeyframe>-91445760000000000,0.5:0.5,0,0,0,0,0,0,5,4,0,0,0,0</StartKeyframe>
        </PointComponentParam>"""))

        # Param 6: Anti-flicker Filter
        new_elements.append(ET.fromstring(f"""
        <VideoComponentParam ObjectID="{p_ids[6]}" ClassID="a4ff2d6e-7ac2-44f8-9d52-17d9ca50e542" Version="10">
            <Name>Anti-flicker Filter</Name>
            <ParameterID>7</ParameterID>
            <StartKeyframe>-91445760000000000,0.,0,0,0,0,0,0</StartKeyframe>
            <LowerBound>0</LowerBound>
            <UpperBound>1</UpperBound>
        </VideoComponentParam>"""))

        # Params 7..10: Built-in Motion Crop Left, Top, Right, Bottom
        for idx_c, (c_name, c_pid, c_val) in enumerate([
            ("Crop Left", 8, c_left),
            ("Crop Top", 9, c_top),
            ("Crop Right", 10, c_right),
            ("Crop Bottom", 11, c_bottom),
        ], start=7):
            new_elements.append(ET.fromstring(f"""
            <VideoComponentParam ObjectID="{p_ids[idx_c]}" ClassID="fe47129e-6c94-4fc0-95d5-c056a517aaf3" Version="10">
                <Name>{c_name}</Name>
                <ParameterID>{c_pid}</ParameterID>
                <StartKeyframe>-91445760000000000,{c_val},0,0,0,0,0,0</StartKeyframe>
                <LowerBound>0</LowerBound>
                <UpperBound>100</UpperBound>
            </VideoComponentParam>"""))

        motion_comp_id = alloc_oid()
        params_xml = "".join(f'<Param Index="{i}" ObjectRef="{p_ids[i]}" />' for i in range(11))
        new_elements.append(ET.fromstring(f"""
        <VideoFilterComponent ObjectID="{motion_comp_id}" ClassID="d10da199-beea-4dd1-b941-ed3a78766d50" Version="9">
            <Component Version="7">
                <Params Version="1">{params_xml}</Params>
                <ID>1</ID>
                <DisplayName>Motion</DisplayName>
                <Intrinsic>true</Intrinsic>
            </Component>
            <PremiereFilterPrivateData Encoding="base64" BinaryHash="3805854c-243a-c4be-bd40-a8a40000000e">AQA=</PremiereFilterPrivateData>
            <MatchName>AE.ADBE Motion</MatchName>
            <VideoFilterType>2</VideoFilterType>
        </VideoFilterComponent>"""))

        new_elements.append(ET.fromstring(f"""
        <VideoComponentChain ObjectID="{vcc_id}" ClassID="0970e08a-f58f-4108-b29a-1a717b8e12e2" Version="3">
            <DefaultOpacity>true</DefaultOpacity>
            <DefaultOpacityComponentID>2</DefaultOpacityComponentID>
            <ComponentChain Version="3">
                <Node Version="1"><Properties Version="1">
                    <MZ.ComponentChain.ActiveComponentID>1</MZ.ComponentChain.ActiveComponentID>
                    <MZ.ComponentChain.ActiveComponentParamIndex>4294967295</MZ.ComponentChain.ActiveComponentParamIndex>
                </Properties></Node>
                <Components Version="1">
                    <Component Index="0" ObjectRef="{motion_comp_id}" />
                </Components>
            </ComponentChain>
        </VideoComponentChain>"""))
        return vcc_id

    # Populate VideoClipTracks
    for t_idx, v_track in enumerate(model["v_tracks"]):
        if t_idx >= len(v_track_els) or not v_track:
            continue
        ti_container = _ensure_track_items_el(v_track_els[t_idx])
        for item_idx, item in enumerate(v_track):
            reg = register_media(item["path"], item["media_type"], item["media_dur"])
            start_ticks = _sec_to_ticks(item["t_start"])
            end_ticks = max(start_ticks + fps_ticks, _sec_to_ticks(item["t_end"]))
            in_ticks = _sec_to_ticks(item["src_in"])
            out_ticks = in_ticks + (end_ticks - start_ticks)

            tl_vc_id = alloc_oid()
            new_elements.append(ET.fromstring(f"""
            <VideoClip ObjectID="{tl_vc_id}" ClassID="9308dbef-2440-4acb-9ab2-953b9a4e82ec" Version="11">
                <Clip Version="18">
                    <Node Version="1"><Properties Version="1">
                        <asl.clip.label.name>BE.Prefs.LabelColors.1</asl.clip.label.name>
                        <asl.clip.label.color>6769408</asl.clip.label.color>
                    </Properties></Node>
                    <MarkerOwner Version="1"><Markers ObjectRef="{reg['markers_id']}" /></MarkerOwner>
                    <Source ObjectRef="{reg['vms_id']}" />
                    <ClipID>{uuid.uuid4()}</ClipID>
                    <InPoint>{in_ticks}</InPoint>
                    <OutPoint>{out_ticks}</OutPoint>
                </Clip>
            </VideoClip>"""))

            sc_id = alloc_oid()
            new_elements.append(ET.fromstring(f"""
            <SubClip ObjectID="{sc_id}" ClassID="e0c58dc9-dbdd-4166-aef7-5db7e3f22e84" Version="6">
                <Clip ObjectRef="{tl_vc_id}" />
                <MasterClip ObjectURef="{reg['mc_uid']}" />
                <OrigChGrp>0</OrigChGrp>
                <Name>{escape(item['name'])}</Name>
            </SubClip>"""))

            vcc_id = _build_video_component_chain(item, in_ticks, out_ticks)

            vti_id = alloc_oid()
            start_tag = f"<Start>{start_ticks}</Start>" if start_ticks > 0 else ""
            new_elements.append(ET.fromstring(f"""
            <VideoClipTrackItem ObjectID="{vti_id}" ClassID="368b0406-29e3-4923-9fcd-094fbf9a1089" Version="8">
                <ClipTrackItem Version="8">
                    <ComponentOwner Version="1"><Components ObjectRef="{vcc_id}" /></ComponentOwner>
                    <TrackItem Version="4">
                        <Node Version="1"><ID>{alloc_node_id()}</ID></Node>
                        {start_tag}
                        <End>{end_ticks}</End>
                    </TrackItem>
                    <SubClip ObjectRef="{sc_id}" />
                </ClipTrackItem>
                <ToneMapSettings>{{"peak":-1,"version":3}}</ToneMapSettings>
                <FrameRect>0,0,{width},{height}</FrameRect>
                <PixelAspectRatio>1,1</PixelAspectRatio>
            </VideoClipTrackItem>"""))
            ti_container.append(ET.fromstring(f'<TrackItem Index="{item_idx}" ObjectRef="{vti_id}" />'))

            if item.get("link_id"):
                link_pairs.setdefault(item["link_id"], {})["v"] = vti_id

    # Populate AudioClipTracks
    for t_idx, a_track in enumerate(model["a_tracks"]):
        if t_idx >= len(a_track_els) or not a_track:
            continue
        ti_container = _ensure_track_items_el(a_track_els[t_idx])
        for item_idx, item in enumerate(a_track):
            reg = register_media(item["path"], item["media_type"], item["media_dur"])
            start_ticks = _sec_to_ticks(item["t_start"])
            end_ticks = max(start_ticks + fps_ticks, _sec_to_ticks(item["t_end"]))
            in_ticks = _sec_to_ticks(item["src_in"])
            out_ticks = in_ticks + (end_ticks - start_ticks)

            sc0_id, sc1_id = alloc_oid(), alloc_oid()
            ams_ref = reg["ams_id"]
            new_elements.append(ET.fromstring(f'<SecondaryContent ObjectID="{sc0_id}" ClassID="f9d004b5-cb04-4e2f-af6f-64fadc2c4be9" Version="1"><Content ObjectRef="{ams_ref}" /><ChannelIndex>0</ChannelIndex></SecondaryContent>'))
            new_elements.append(ET.fromstring(f'<SecondaryContent ObjectID="{sc1_id}" ClassID="f9d004b5-cb04-4e2f-af6f-64fadc2c4be9" Version="1"><Content ObjectRef="{ams_ref}" /><ChannelIndex>1</ChannelIndex></SecondaryContent>'))

            tl_ac_id = alloc_oid()
            new_elements.append(ET.fromstring(f"""
            <AudioClip ObjectID="{tl_ac_id}" ClassID="b8830d03-de02-41ee-84ec-fe566dc70cd9" Version="8">
                <Clip Version="18">
                    <Node Version="1"><Properties Version="1">
                        <asl.clip.label.name>BE.Prefs.LabelColors.1</asl.clip.label.name>
                        <asl.clip.label.color>6769408</asl.clip.label.color>
                    </Properties></Node>
                    <MarkerOwner Version="1"><Markers ObjectRef="{reg['markers_id']}" /></MarkerOwner>
                    <Source ObjectRef="{reg['ams_id']}" />
                    <ClipID>{uuid.uuid4()}</ClipID>
                    <InPoint>{in_ticks}</InPoint>
                    <OutPoint>{out_ticks}</OutPoint>
                </Clip>
                <SecondaryContents Version="1">
                    <SecondaryContentItem Index="0" ObjectRef="{sc0_id}" />
                    <SecondaryContentItem Index="1" ObjectRef="{sc1_id}" />
                </SecondaryContents>
                <AudioChannelLayout>[{{"channellabel":100}},{{"channellabel":101}}]</AudioChannelLayout>
            </AudioClip>"""))

            sc_id = alloc_oid()
            new_elements.append(ET.fromstring(f"""
            <SubClip ObjectID="{sc_id}" ClassID="e0c58dc9-dbdd-4166-aef7-5db7e3f22e84" Version="6">
                <Clip ObjectRef="{tl_ac_id}" />
                <MasterClip ObjectURef="{reg['mc_uid']}" />
                <OrigChGrp>0</OrigChGrp>
                <Name>{escape(item['name'])}</Name>
            </SubClip>"""))

            acc_id = alloc_oid()
            new_elements.append(ET.fromstring(f"""
            <AudioComponentChain ObjectID="{acc_id}" ClassID="3cb131d1-d3c0-47ae-a19a-bdf75ea11674" Version="4">
                <DefaultVol>true</DefaultVol>
                <DefaultVolumeComponentID>1</DefaultVolumeComponentID>
                <DefaultChannelVolumeComponentID>2</DefaultChannelVolumeComponentID>
                <ComponentChain Version="3">
                    <Node Version="1"><Properties Version="1">
                        <MZ.ComponentChain.ActiveComponentID>1</MZ.ComponentChain.ActiveComponentID>
                        <MZ.ComponentChain.ActiveComponentParamIndex>4294967295</MZ.ComponentChain.ActiveComponentParamIndex>
                    </Properties></Node>
                </ComponentChain>
                <AudioChannelLayout>[{{"channellabel":100}},{{"channellabel":101}}]</AudioChannelLayout>
                <ChannelType>1</ChannelType>
                <FrameRate>{AUDIO_TICKS_PER_SAMPLE}</FrameRate>
            </AudioComponentChain>"""))

            ati_id = alloc_oid()
            start_tag = f"<Start>{start_ticks}</Start>" if start_ticks > 0 else ""
            new_elements.append(ET.fromstring(f"""
            <AudioClipTrackItem ObjectID="{ati_id}" ClassID="064ec682-9ba6-11d5-af2d-9ca32c7d6164" Version="11">
                <ClipTrackItem Version="8">
                    <ComponentOwner Version="1"><Components ObjectRef="{acc_id}" /></ComponentOwner>
                    <TrackItem Version="4">
                        <Node Version="1"><ID>{alloc_node_id()}</ID></Node>
                        {start_tag}
                        <End>{end_ticks}</End>
                    </TrackItem>
                    <SubClip ObjectRef="{sc_id}" />
                </ClipTrackItem>
                <ID>{uuid.uuid4()}</ID>
                <PreRenderComponentChainHashVersion>1</PreRenderComponentChainHashVersion>
            </AudioClipTrackItem>"""))
            ti_container.append(ET.fromstring(f'<TrackItem Index="{item_idx}" ObjectRef="{ati_id}" />'))

            if item.get("link_id"):
                link_pairs.setdefault(item["link_id"], {})["a"] = ati_id

    # Create Link objects for V1 + A1 pairs
    link_idx = 0
    for lid, pair in link_pairs.items():
        if "v" in pair and "a" in pair:
            link_oid = alloc_oid()
            new_elements.append(ET.fromstring(f"""
            <Link ObjectID="{link_oid}" ClassID="149d4ea5-a7d4-4b34-9bb7-16d783904bf2" Version="1">
                <TrackItemGroup Version="1">
                    <TrackItems Version="1">
                        <TrackItem Index="0" ObjectRef="{pair['v']}" />
                        <TrackItem Index="1" ObjectRef="{pair['a']}" />
                    </TrackItems>
                </TrackItemGroup>
            </Link>"""))
            links_el.append(ET.fromstring(f'<Link Index="{link_idx}" ObjectRef="{link_oid}" />'))
            link_idx += 1

    for nel in new_elements:
        root.append(nel)

    out_prproj.parent.mkdir(parents=True, exist_ok=True)
    xml_bytes = b'<?xml version="1.0" encoding="UTF-8" ?>\n' + ET.tostring(root, encoding="utf-8")
    with gzip.open(out_prproj, "wb", compresslevel=6) as f:
        f.write(xml_bytes)
    return out_prproj


def export_premiere_project(
    plan: dict,
    plan_path: pathlib.Path,
    out_dir: pathlib.Path | None = None,
    work_dir: pathlib.Path | None = None,
    fps: int = 30,
    width: int = 1080,
    height: int = 1920,
) -> dict[str, pathlib.Path]:
    """
    Export both `<output_name>.prproj` and `<output_name>.premiere.xml` to `out_dir`
    (defaulting to `~/Downloads/Zeno Outputs/`).
    """
    plan_path = pathlib.Path(plan_path).resolve()
    work_dir = pathlib.Path(work_dir or plan_path.parent).resolve()
    out_dir = pathlib.Path(out_dir or OUTPUT).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    output_name = plan.get("output") or plan.get("output_name") or plan.get("project") or plan_path.parent.name
    stem = pathlib.Path(str(output_name)).stem
    eff_fps = int(plan.get("fps") or fps)
    eff_w = int(plan.get("width") or width)
    eff_h = int(plan.get("height") or height)

    model = _collect_timeline_model(
        plan=plan,
        plan_path=plan_path,
        work_dir=work_dir,
        fps=eff_fps,
        width=eff_w,
        height=eff_h,
    )

    out_xml = out_dir / f"{stem}.premiere.xml"
    out_prproj = out_dir / f"{stem}.prproj"

    _write_premiere_xml(model, seq_name=stem, out_xml=out_xml)
    _write_native_prproj(model, seq_name=stem, out_prproj=out_prproj)

    return {
        "prproj": out_prproj,
        "xml": out_xml,
        "v_counts": [len(t) for t in model["v_tracks"]],
        "a_counts": [len(t) for t in model["a_tracks"]],
    }


def main():
    ap = argparse.ArgumentParser(description="Export Zeno plan.edl/plan.json to Adobe Premiere Pro (.prproj + .premiere.xml)")
    ap.add_argument("plan", help="Path to work/<project>/plan.edl or plan.json")
    ap.add_argument("--out-dir", default=None, help="Output directory (defaults to ~/Downloads/Zeno Outputs)")
    ap.add_argument("--fps", type=int, default=30)
    ap.add_argument("--width", type=int, default=1080)
    ap.add_argument("--height", type=int, default=1920)
    args = ap.parse_args()

    plan_path = pathlib.Path(args.plan).resolve()
    plan = load_plan(plan_path)
    res = export_premiere_project(
        plan=plan,
        plan_path=plan_path,
        out_dir=pathlib.Path(args.out_dir) if args.out_dir else None,
        work_dir=plan_path.parent,
        fps=args.fps,
        width=args.width,
        height=args.height,
    )
    print(f"[PREMIERE] .prproj -> {res['prproj']}")
    print(f"[PREMIERE] .xml    -> {res['xml']}")
    print(f"[PREMIERE] Tracks  -> V:{res['v_counts']} A:{res['a_counts']}")


if __name__ == "__main__":
    main()
