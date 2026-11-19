#!/usr/bin/env python3
"""Adobe Premiere Pro Project (`.prproj`) and Final Cut Pro 7 XML (`.premiere.xml`) Exporter.

Generates two editable Premiere Pro project files alongside every rendered MP4:
  1. `<output_dir>/<name>.prproj`
     Native GZIP-compressed Adobe Premiere Pro project (`PremiereData Version="3"`)
     populated from `assets/premiere/base_1080x1920.prproj.gz` with all source clips,
     overlay PNGs, subtitle PNG frames, and WAV/MP3 SFX in the Root Bin plus a multi-track
     sequence with non-destructive In/Out trim points, Motion/Crop/Opacity keyframes, and V+A links.
  2. `<output_dir>/<name>.premiere.xml`
     Final Cut Pro 7 / Premiere Pro `xmeml version="4"` interchange timeline containing
     all video and audio tracks, eased Basic Motion Scale curves, seam-safe split-screen
     Crop keyframes, 2-pulse Opacity mark flashes, V7 bilingual flashcards, V8 rendered subtitle PNGs, Audio Levels,
     constant-power audio crossfades, and sequence markers.
"""
from __future__ import annotations

import argparse
import ast
import base64
import gzip
import json
import math
import operator
import os
import pathlib
import shutil
import subprocess
import sys
import urllib.parse
import uuid
import wave
import xml.etree.ElementTree as ET
from typing import Any
from xml.sax.saxutils import escape

_HERE = pathlib.Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import common as _common
from edl_parser import load_edl

try:
    import layouts
except Exception:
    layouts = None  # type: ignore[assignment]


def _get_layouts() -> Any:
    global layouts
    if layouts is None:
        try:
            import layouts as _lay
            layouts = _lay
        except Exception:
            return None
    return layouts

REPO = getattr(_common, "ROOT", pathlib.Path(__file__).resolve().parent.parent)
OUTPUT = getattr(_common, "OUTPUT", REPO / "output")


def segment_hold_tail(seg: dict) -> float:
    """Return clamped post-word freeze-hold tail seconds (`common.segment_hold_tail` with safe fallback)."""
    fn = getattr(_common, "segment_hold_tail", None)
    if callable(fn):
        return float(fn(seg))
    try:
        return max(0.0, min(1.5, float(seg.get("hold_tail") or seg.get("hold") or 0.0)))
    except (TypeError, ValueError):
        return 0.0


def segment_speed(seg: dict) -> float:
    """Return clamped playback speed factor (`common.segment_speed` with safe fallback)."""
    fn = getattr(_common, "segment_speed", None)
    if callable(fn):
        try:
            val = float(fn(seg))
            if math.isfinite(val) and val > 0.0:
                return val
        except (TypeError, ValueError):
            pass
    if not isinstance(seg, dict):
        return 1.0
    raw = seg.get("speed")
    if raw is None or isinstance(raw, bool):
        return 1.0
    try:
        val = float(str(raw).strip().rstrip("xX"))
        return max(0.25, min(4.0, val)) if math.isfinite(val) and val > 0.0 else 1.0
    except (TypeError, ValueError):
        return 1.0


def segment_is_muted(seg: dict) -> bool:
    """Return True when `seg` mutes source audio (`common.segment_is_muted` with safe fallback)."""
    fn = getattr(_common, "segment_is_muted", None)
    if callable(fn):
        return bool(fn(seg))
    if not isinstance(seg, dict):
        return False
    for key in ("mute", "muted"):
        raw = seg.get(key)
        if isinstance(raw, str):
            low = raw.strip().lower()
            if low in ("1", "true", "yes", "on"):
                return True
            if low in ("", "0", "false", "no", "off", "none", "null"):
                continue
            return True
        if raw:
            return True
    am = str(seg.get("audio_mode") or seg.get("audio") or "").strip().lower()
    return am in ("mute", "muted", "off", "none")


def segment_is_beat(seg: dict) -> bool:
    """Return True when `seg` is a non-spoken visual beat (`common.segment_is_beat` with safe fallback)."""
    fn = getattr(_common, "segment_is_beat", None)
    if callable(fn):
        return bool(fn(seg))
    if not isinstance(seg, dict):
        return False
    return bool(seg.get("beat") or seg.get("silent"))


def segment_play_duration(seg: dict) -> float:
    """Return moving playback duration in seconds (`common.segment_play_duration` with safe fallback)."""
    fn = getattr(_common, "segment_play_duration", None)
    if callable(fn):
        return max(0.033, float(fn(seg)))
    src_dur = max(0.033, float(seg.get("end", 0.0)) - float(seg.get("start", 0.0)))
    return max(0.033, src_dur / segment_speed(seg))


def segment_duration(seg: dict) -> float:
    """Return total rendered segment duration (`play_duration + hold_tail`)."""
    fn = getattr(_common, "segment_duration", None)
    if callable(fn) and (callable(getattr(_common, "segment_play_duration", None)) or abs(segment_speed(seg) - 1.0) < 1e-4):
        return max(0.033, float(fn(seg)))
    return max(0.033, segment_play_duration(seg) + segment_hold_tail(seg))


def src_to_seg_time(seg: dict, src_t: float) -> float:
    """Map absolute source timestamp to segment-relative playback seconds (`common.src_to_seg_time` fallback)."""
    fn = getattr(_common, "src_to_seg_time", None)
    if callable(fn):
        return float(fn(seg, src_t))
    st = float(seg.get("start", 0.0) or 0.0)
    return (float(src_t) - st) / segment_speed(seg)


_TRANSITION_DEFAULT_DUR: dict[str, float] = {
    "whip_left": 0.34,
    "smoothleft": 0.34,
    "whip_right": 0.34,
    "smoothright": 0.34,
    "slide_up": 0.36,
    "whip_up": 0.36,
    "smoothup": 0.36,
    "slide_down": 0.36,
    "whip_down": 0.36,
    "smoothdown": 0.36,
    "zoom_burst": 0.36,
    "zoom_in": 0.30,
    "zoom_out": 0.32,
    "zoom_blur": 0.36,
    "flash_zoom": 0.36,
    "split_reveal": 0.40,
    "warm_flash": 0.38,
    "warm_flash_punch": 0.34,
    "dip_to_black": 0.30,
    "dip_to_white": 0.30,
    "fade": 0.30,
    "dissolve": 0.30,
}

_WHOOSH_TRANSITIONS: set[str] = {
    "whip_left", "whip_right", "whip_up", "whip_down",
    "slide_up", "slide_down", "smoothleft", "smoothright", "smoothup", "smoothdown",
    "flash_zoom", "zoom_burst", "zoom_in", "zoom_out", "zoom_blur",
    "split_reveal", "warm_flash", "warm_flash_punch",
}


def _default_transition_dur(tr_type: str) -> float:
    """Return canonical transition duration synced with `render._META_TRANSITIONS` (fallback `0.28s`)."""
    key = str(tr_type or "").strip().lower()
    try:
        import render as _rnd

        meta = getattr(_rnd, "_META_TRANSITIONS", None)
        if isinstance(meta, dict) and key in meta and isinstance(meta[key], dict):
            d = float(meta[key].get("default_dur") or 0.0)
            if d > 0.0:
                return d
    except Exception:
        pass
    return _TRANSITION_DEFAULT_DUR.get(key, 0.28)


def _map_fcp7_transition(tr_type: str) -> tuple[str, str]:
    """Map canonical transition name to FCP7 XML `(effect_name, effect_id)` (checking `zoom` before `flash`)."""
    tr_t = str(tr_type or "fade").strip().lower()
    if "zoom" in tr_t:
        return "Cross Dissolve", "crossdissolve"
    if "white" in tr_t or "warm_flash" in tr_t:
        return "Dip to White", "dip2white"
    if "dip" in tr_t or "flash" in tr_t:
        return "Dip to Black", "dip2black"
    if any(k in tr_t for k in ("whip", "slide", "smooth")):
        return "Band Slide", "bandslide"
    return "Cross Dissolve", "crossdissolve"


def _eval_px_expr(expr: str, fallback: float) -> float:
    """Safely evaluate a numeric pixel arithmetic expression returned by `layouts.place_mark_for_segment`."""
    ops = {
        ast.Add: operator.add,
        ast.Sub: operator.sub,
        ast.Mult: operator.mul,
        ast.Div: operator.truediv,
        ast.FloorDiv: operator.floordiv,
        ast.USub: operator.neg,
        ast.UAdd: operator.pos,
    }

    def _ev(node: ast.AST) -> float:
        if isinstance(node, ast.Expression):
            return _ev(node.body)
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
            return float(node.value)
        if isinstance(node, ast.BinOp) and type(node.op) in ops:
            return float(ops[type(node.op)](_ev(node.left), _ev(node.right)))
        if isinstance(node, ast.UnaryOp) and type(node.op) in ops:
            return float(ops[type(node.op)](_ev(node.operand)))
        raise ValueError("Unsupported AST node")

    try:
        return _ev(ast.parse(str(expr).strip(), mode="eval"))
    except Exception:
        return float(fallback)


def _extract_hold_frame_png(clip_path: pathlib.Path, seek_sec: float, out_png: pathlib.Path) -> pathlib.Path | None:
    """Extract a single trailing frame PNG for Premiere `hold_tail` freeze-frames without exceeding `media_dur`."""
    ffmpeg_bin = getattr(_common, "FFMPEG", None)
    if not ffmpeg_bin or not clip_path.is_file():
        return None
    try:
        out_png.parent.mkdir(parents=True, exist_ok=True)
        for cand_t in (max(0.0, float(seek_sec)), max(0.0, float(seek_sec) - 0.15)):
            r = subprocess.run(
                [
                    str(ffmpeg_bin),
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-y",
                    "-ss",
                    f"{cand_t:.3f}",
                    "-i",
                    str(clip_path),
                    "-frames:v",
                    "1",
                    str(out_png),
                ],
                capture_output=True,
                timeout=15,
            )
            if r.returncode == 0 and out_png.is_file() and out_png.stat().st_size > 100:
                return out_png.resolve()
    except Exception:
        pass
    return None

_AUDIO_EXTS = {".wav", ".mp3", ".m4a", ".aac", ".ogg", ".flac"}
_SFX_ALIASES = {
    "ui_pop.wav": "pop.wav",
    "riser.wav": "sn_riser_1.wav",
}

PREMIERE_TICKS_PER_SEC = 254016000000
AUDIO_SAMPLE_RATE = 48000
AUDIO_TICKS_PER_SAMPLE = PREMIERE_TICKS_PER_SEC // AUDIO_SAMPLE_RATE  # 5292000


def _resolve_sfx_path(sx_ref: str | pathlib.Path, sfx_pack: str | None = None) -> pathlib.Path | None:
    """Resolve an SFX filename or path against `assets/sfx/`, `SFX_PACKS`, and `_SFX_ALIASES`."""
    if not sx_ref:
        return None
    raw_p = pathlib.Path(sx_ref)
    mapped_name = _SFX_ALIASES.get(raw_p.name.lower(), raw_p.name)
    if mapped_name != raw_p.name:
        cand_alias = (REPO / "assets" / "sfx" / mapped_name).resolve()
        if cand_alias.is_file():
            return cand_alias
    if hasattr(_common, "resolve_sfx_asset"):
        try:
            resolved = _common.resolve_sfx_asset(str(sx_ref), pack=sfx_pack)
            if resolved and pathlib.Path(resolved).is_file():
                return pathlib.Path(resolved).resolve()
        except Exception:
            pass
    if raw_p.is_absolute() and raw_p.is_file():
        return raw_p.resolve()
    cand_repo = (REPO / "assets" / "sfx" / mapped_name).resolve()
    if cand_repo.is_file():
        return cand_repo
    if raw_p.is_file():
        return raw_p.resolve()
    return None


def _split_seam_crop_pct(z_val: float) -> float:
    """Return symmetric clip-space crop percentage per side so a panel zoomed by `z_val` occupies exactly 50% of the canvas."""
    z_clamped = max(1.0, float(z_val))
    return round(max(0.0, min(49.5, 50.0 * (1.0 - 0.5 / z_clamped))), 4)


def probe_duration(p: pathlib.Path) -> float:
    """Probe media duration in seconds via `common.probe` (with WAV header fallback)."""
    p = pathlib.Path(p)
    is_audio = p.suffix.lower() in _AUDIO_EXTS
    fallback = 1.5 if is_audio else 60.0
    try:
        info = _common.probe(p)
        dur = float(info.get("duration") or 0.0)
        if dur > 0.01:
            return dur
    except Exception:
        pass
    if p.suffix.lower() == ".wav" and p.is_file():
        try:
            with wave.open(str(p), "rb") as wf:
                rate = wf.getframerate()
                if rate > 0:
                    return max(0.05, wf.getnframes() / float(rate))
        except Exception:
            pass
    return fallback


def load_plan(plan_path: pathlib.Path) -> dict:
    """Load an edit plan from `.json` or `.edl`."""
    p = pathlib.Path(plan_path)
    if p.suffix.lower() == ".json":
        return _common.read_json(p)
    return load_edl(p)


def parse_zoom_spec(spec: Any) -> dict:
    """Parse a segment zoom specification into `{'z0': float, 'z1': float, 'style': str}`."""
    _lay = _get_layouts()
    if _lay is not None and hasattr(_lay, "parse_zoom_spec"):
        try:
            return _lay.parse_zoom_spec(spec)
        except Exception:
            pass
    if isinstance(spec, (int, float)):
        v = float(spec)
        return {"z0": v, "z1": v, "style": "smooth"}
    s = str(spec or "1.0").strip()
    style = "smooth"
    if ":" in s:
        st_part, rhs = s.split(":", 1)
        style = st_part.strip().lower() or "smooth"
        s = rhs.split("@", 1)[0].strip()
    else:
        s = s.split("@", 1)[0].strip()
        if s.lower() == "bell":
            return {"z0": 1.20, "z1": 1.00, "zpeak": 1.55, "style": "bell"}
    if style == "bell":
        parts = [p.strip() for p in s.split("->")] if s else []
        try:
            if len(parts) >= 3:
                return {
                    "z0": float(parts[0]) if parts[0] else 1.20,
                    "zpeak": float(parts[1]) if parts[1] else 1.55,
                    "z1": float(parts[2]) if parts[2] else 1.00,
                    "style": "bell",
                }
            if len(parts) == 2:
                z0_v = float(parts[0]) if parts[0] else 1.20
                z1_v = float(parts[1]) if parts[1] else 1.00
                return {"z0": z0_v, "z1": z1_v, "zpeak": max(1.55, z0_v, z1_v), "style": "bell"}
            if len(parts) == 1 and parts[0]:
                return {"z0": 1.20, "z1": 1.00, "zpeak": float(parts[0]), "style": "bell"}
        except Exception:
            pass
        return {"z0": 1.20, "z1": 1.00, "zpeak": 1.55, "style": "bell"}
    if "->" in s:
        a, b = s.split("->", 1)
        try:
            out_st = "smooth" if style in ("zoom_in", "zoom_out", "mild_in", "mild_out") else style
            return {"z0": float(a), "z1": float(b), "style": out_st}
        except Exception:
            pass
    try:
        v = float(s)
        if style in ("sudden_in", "punch_in", "push", "in", "flash_zoom"):
            return {"z0": 1.0, "z1": v, "style": "flash_zoom" if style == "flash_zoom" else "sudden_in"}
        if style in ("sudden_out", "punch_out", "pull", "out"):
            return {"z0": v, "z1": 1.0, "style": "sudden_out"}
        if style in ("zoom_in", "mild_in"):
            return {"z0": 1.0, "z1": v, "style": "smooth"}
        if style in ("zoom_out", "mild_out"):
            return {"z0": v, "z1": 1.0, "style": "smooth"}
        return {"z0": v, "z1": v, "style": style}
    except Exception:
        return {"z0": 1.0, "z1": 1.0, "style": "smooth"}


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
    return round(max(0.0001, min(5.623413, 10.0 ** (float(db) / 20.0))), 6)


def _resolve_clip_path(
    clip_ref: str,
    plan_path: pathlib.Path,
    clip_map: dict[str, str] | None = None,
) -> pathlib.Path | None:
    if not clip_ref:
        return None
    if clip_map and clip_ref in clip_map:
        return pathlib.Path(clip_map[clip_ref]).resolve()
    p = pathlib.Path(clip_ref)
    if p.is_absolute() and p.exists():
        return p.resolve()
    for base in (plan_path.parent, REPO, REPO / "input" / "footage"):
        cand = base / clip_ref
        if cand.exists():
            return cand.resolve()
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
    """Map clip stem/name (e.g. `'21545_1'` or `'21545_1.mp4'`) to its resolved absolute path."""
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
    for cp_txt in plan_path.parent.glob("*/clip_path.txt"):
        try:
            raw_p = pathlib.Path(cp_txt.read_text(encoding="utf-8").strip())
            if raw_p.exists():
                lookup[raw_p.stem] = str(raw_p.resolve())
                lookup[raw_p.name] = str(raw_p.resolve())
                search_dirs.append(raw_p.parent)
        except Exception:
            pass
    seen_dirs: set[str] = set()
    for d in search_dirs:
        if not d.exists() or str(d) in seen_dirs:
            continue
        seen_dirs.add(str(d))
        for mp4 in d.glob("*.mp4"):
            lookup.setdefault(mp4.stem, str(mp4.resolve()))
            lookup.setdefault(mp4.name, str(mp4.resolve()))
    return lookup


def _ensure_overlay_pngs(plan: dict, work_dir: pathlib.Path) -> dict:
    """Generate persistent PNG overlays inside `work_dir/overlays/` so Premiere Pro can link to them."""
    ov_dir = work_dir / "overlays"
    ov_dir.mkdir(parents=True, exist_ok=True)
    generated: dict[str, dict] = {
        "strikethrough": {},
        "chat_bar": {},
        "video_call": {},
    }
    _lay = _get_layouts()
    if _lay is None or not hasattr(_lay, "_build_strikethrough_png"):
        return generated

    strike_items: list[str] = []
    for idx, seg in enumerate(plan.get("segments", [])):
        st_text = (seg.get("strikethrough_text") or "").strip()
        if st_text and st_text not in strike_items:
            strike_items.append(st_text)
        if strike_items and seg.get("layout", "single") in ("split_h", "split_v"):
            u_png: pathlib.Path | None = ov_dir / f"pr_strike_{idx:02d}_in.png"
            s_png = ov_dir / f"pr_strike_{idx:02d}_struck.png"
            try:
                if st_text and seg.get("strikethrough_initial_delay", 0):
                    _lay._build_strikethrough_png(strike_items, u_png, strike_last=False)
                else:
                    u_png = None
                _lay._build_strikethrough_png(strike_items, s_png, strike_last=True)
                generated["strikethrough"][idx] = (
                    u_png if (u_png and u_png.exists()) else None,
                    s_png if s_png.exists() else None,
                )
            except Exception:
                pass
    return generated


def _collect_timeline_model(
    plan: dict,
    plan_path: pathlib.Path,
    work_dir: pathlib.Path,
    fps: int = 30,
    width: int = 720,
    height: int = 1280,
) -> dict:
    """Build a unified multi-track timeline representation shared by the FCP7 XML and `.prproj` writers."""
    clip_lookup = _build_clip_lookup(plan, plan_path)
    ov_assets = _ensure_overlay_pngs(plan, work_dir)
    mark_pack = plan.get("mark_pack", "flash")
    sfx_pack = plan.get("sfx_pack")
    segments = plan.get("segments", [])

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
    #   V1 (0): Primary speaker clips
    #   V2 (1): Listening partner / PIP clips (split_h, split_v, video_call, top_pip)
    #   V3..V5 (2..4): Stacked "Stop Saying" strikethrough banners
    #   V6 (5): Right / Wrong 2-pulse mark overlays
    #   V7 (6): Bilingual flashcards (from overlays/flashcards_meta.json)
    #   V8 (7): Rendered subtitle PNG frames (from _subs/subs_meta.json or _subs/subs.ffconcat)
    v_tracks: list[list[dict]] = [[], [], [], [], [], [], [], []]

    # Audio tracks:
    #   A1 (0): Primary dialogue audio
    #   A2 (1): Reserved secondary dialogue audio
    #   A3..A8 (2..): Non-overlapping SFX tracks
    a_tracks: list[list[dict]] = [[], [], [], []]

    dur_cache: dict[str, float] = {}
    dims_cache: dict[str, tuple[int, int]] = {}

    def get_media_dur(p: pathlib.Path, is_still: bool = False) -> float:
        if is_still:
            return 3600.0
        s = str(p)
        if s not in dur_cache:
            is_aud = p.suffix.lower() in _AUDIO_EXTS
            try:
                info = _common.probe(p)
                if info.get("width") and info.get("height"):
                    dims_cache[s] = (int(info["width"]), int(info["height"]))
                probed = float(info.get("duration") or 0.0)
                if probed > 0.01:
                    dur_cache[s] = max(0.05, probed)
                else:
                    dur_cache[s] = probe_duration(p)
            except Exception:
                dur_cache[s] = probe_duration(p) if is_aud else 60.0
        return dur_cache[s]

    def get_media_dims(p: pathlib.Path, is_still: bool = False) -> tuple[int, int]:
        s = str(p)
        if s in dims_cache:
            return dims_cache[s]
        if is_still:
            try:
                with open(p, "rb") as fh:
                    hdr = fh.read(24)
                if len(hdr) >= 24 and hdr[:8] == b"\x89PNG\r\n\x1a\n":
                    iw = int.from_bytes(hdr[16:20], "big")
                    ih = int.from_bytes(hdr[20:24], "big")
                    if iw > 0 and ih > 0:
                        dims_cache[s] = (iw, ih)
                        return dims_cache[s]
            except Exception:
                pass
            dims_cache[s] = (1080, 1920)
            return dims_cache[s]
        try:
            info = _common.probe(p)
            if info.get("duration"):
                dur_cache.setdefault(s, max(0.1, float(info["duration"])))
            iw = int(info.get("width") or width)
            ih = int(info.get("height") or height)
            dims_cache[s] = (iw, ih)
        except Exception:
            dims_cache[s] = (width, height)
        return dims_cache[s]

    def _pip_transform(pw: int, ph: int) -> tuple[float, tuple[float, float], dict | None]:
        """Compute Scale, Center, and Crop for a `top_pip` clip of size `(pw, ph)` inside `(width, height)`."""
        pip_sc = round((width * 0.6944 / max(1, pw)) * 100.0, 2)
        pip_ctr = (0.0, -0.38)
        if pw >= ph:
            return pip_sc, pip_ctr, None
        return pip_sc, pip_ctr, {"left": 0.0, "right": 0.0, "top": 28.0, "bottom": 40.5}

    ov_scale_pct = round((width / 1080.0) * 100.0, 2)

    def place_sfx(sfx_path: pathlib.Path | str, start_sec: float, gain_db: float = 0.0, max_dur: float | None = None) -> None:
        resolved_sfx = _resolve_sfx_path(sfx_path, sfx_pack=sfx_pack)
        if not resolved_sfx or not resolved_sfx.exists():
            return
        raw_dur = get_media_dur(resolved_sfx, is_still=False)
        dur = min(raw_dur, max_dur) if max_dur else raw_dur
        if dur <= 0.02:
            return
        end_sec = start_sec + dur
        item = {
            "name": resolved_sfx.name,
            "path": resolved_sfx,
            "media_type": "audio",
            "t_start": start_sec,
            "t_end": end_sec,
            "src_in": 0.0,
            "src_out": dur,
            "media_dur": raw_dur,
            "gain_db": float(gain_db),
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
        raw_out = float(seg.get("end", src_in))
        raw_src_dur = max(0.033, raw_out - src_in)
        spd = segment_speed(seg)
        is_muted = segment_is_muted(seg)
        hold_tail = segment_hold_tail(seg)
        play_dur = segment_play_duration(seg)
        dur = max(play_dur + hold_tail, segment_duration(seg))
        t_start = cursor
        t_end = cursor + dur

        seg_id = str(seg.get("id") or f"S{idx + 1}")
        clip_ref = str(seg.get("src") or seg.get("clip") or "")
        clip_path = _resolve_clip_path(clip_ref, plan_path, clip_lookup)
        if not clip_path or not clip_path.exists():
            cursor = t_end
            continue

        media_dur = get_media_dur(clip_path, is_still=False)
        src_out = min(media_dur, raw_out)
        if src_out <= src_in:
            src_out = min(media_dur, src_in + 0.033)
        avail_src_dur = max(0.033, media_dur - src_in)
        clip_src_dur = min(raw_src_dur, avail_src_dur)
        clip_play_dur = max(0.033, clip_src_dur / spd)
        hold_src_ref = str(seg.get("hold_src") or "").strip()
        hold_path = _resolve_clip_path(hold_src_ref, plan_path, clip_lookup) if hold_src_ref else None
        has_live_hold = bool(hold_tail > 0.015 and hold_path is not None and hold_path.exists())
        hold_png = (
            _extract_hold_frame_png(
                clip_path,
                max(0.0, min(max(0.0, media_dur - 0.10), src_out - 0.04)),
                work_dir / "overlays" / f"hold_{seg_id}_{idx:02d}.png",
            )
            if (hold_tail > 0.015 and not has_live_hold)
            else None
        )
        if has_live_hold or hold_png is not None:
            v_safe_dur = clip_play_dur
            v_src_out = src_out
        else:
            v_safe_dur = min(dur, avail_src_dur / spd)
            v_src_out = min(media_dur, src_in + v_safe_dur * spd)
        v_t_end = t_start + v_safe_dur

        audio_end_val: float | None = None
        ae_raw = seg.get("audio_end")
        if ae_raw is not None and not isinstance(ae_raw, bool):
            try:
                ae_f = float(ae_raw)
                if math.isfinite(ae_f) and ae_f > 0.04:
                    audio_end_val = ae_f
            except (TypeError, ValueError):
                pass
        has_audio_end = audio_end_val is not None and audio_end_val < clip_play_dur - 0.005
        a_clip_dur = min(clip_play_dur, audio_end_val) if (has_audio_end and audio_end_val is not None) else clip_play_dur
        a_t_end = t_start + a_clip_dur
        a_src_out = min(src_out, src_in + a_clip_dur * spd)
        motion_dur = v_safe_dur
        clip_w, clip_h = get_media_dims(clip_path, is_still=False)
        base_scale = (width / float(clip_w)) if clip_w else 1.0
        min_scale_pct = round(base_scale * 100.0, 2)
        layout_mode = str(seg.get("layout", plan.get("preset", "single")) or "single")
        zoom_style = str(seg.get("zoom_style") or "smooth").strip().lower()
        zoom_at = 0.0
        zoom_dur = motion_dur
        resolved_z = False
        z_spec: dict[str, Any] = {}
        _lay = _get_layouts()
        if _lay is not None and hasattr(_lay, "resolve_segment_zoom_spec"):
            try:
                z0, z1, zoom_style, zoom_at, zoom_dur = _lay.resolve_segment_zoom_spec(seg, motion_dur)
                resolved_z = True
            except Exception:
                resolved_z = False
        if not resolved_z:
            z_spec = parse_zoom_spec(seg.get("zoom", 1.0))
            z0 = float(z_spec.get("z0", 1.0))
            z1_raw = seg.get("zoom_end") if seg.get("zoom_end") is not None else seg.get("zoom_to")
            z1 = float(z1_raw if z1_raw is not None else z_spec.get("z1", z0))
            if not seg.get("zoom_style") and z_spec.get("style"):
                zoom_style = str(z_spec["style"]).strip().lower()
        z0 = max(1.0, float(z0))
        z1 = max(1.0, float(z1))
        zpeak: float | None = None
        if zoom_style == "bell":
            if _lay is not None and hasattr(_lay, "_resolve_zoom_peak"):
                try:
                    zpeak = _lay._resolve_zoom_peak(seg, z0, z1, zoom_style)
                except Exception:
                    zpeak = None
            if zpeak is None:
                raw_pk = seg.get("zoom_peak") if seg.get("zoom_peak") is not None else z_spec.get("zpeak")
                if raw_pk is not None:
                    try:
                        zpeak = max(1.0, float(raw_pk))
                    except (TypeError, ValueError):
                        zpeak = None
            if zpeak is None:
                zpeak = max(1.0, 1.55, z0, z1)
            zpeak = max(1.0, float(zpeak))
        vol_db = float(seg.get("gain_db", 0.0) or 0.0)

        def _z_to_scale(zv: float) -> float:
            return max(min_scale_pct, round(max(1.0, float(zv)) * base_scale * 100.0, 2))

        v1_scale_start = _z_to_scale(z0)
        v1_scale_end = _z_to_scale(z1)
        scale_kfs: list[tuple[float, float]] = []
        if zoom_style == "bell" and zpeak is not None and (
            abs(zpeak - z0) > 0.004 or abs(zpeak - z1) > 0.004 or abs(z1 - z0) > 0.004
        ):
            scale_kfs = [
                (0.0, _z_to_scale(z0)),
                (round(0.30 * motion_dur, 4), _z_to_scale(zpeak)),
                (round(0.52 * motion_dur, 4), _z_to_scale(zpeak)),
                (round(motion_dur, 4), _z_to_scale(z1)),
            ]
        elif zoom_style in ("sudden_in", "sudden_out", "flash_zoom") and abs(z1 - z0) > 0.004:
            t0_z = max(0.0, min(motion_dur - 0.05, float(zoom_at)))
            d_snap = max(0.08, min(1.20, float(zoom_dur or 0.18)))
            mid_z = max(1.0, z0 + (z1 - z0) * 0.875)
            drift_z = max(1.0, z1) if zoom_style == "sudden_out" else max(1.0, z1 + (0.015 if z1 >= z0 else -0.015))
            scale_kfs = [
                (0.0, _z_to_scale(z0)),
                (t0_z, _z_to_scale(z0)),
                (min(motion_dur, t0_z + d_snap * 0.5), _z_to_scale(mid_z)),
                (min(motion_dur, t0_z + d_snap), _z_to_scale(z1)),
                (motion_dur, _z_to_scale(drift_z)),
            ]
        elif abs(z1 - z0) > 0.004:
            for frac in (0.0, 0.25, 0.5, 0.75, 1.0):
                p_ease = 0.5 * (1.0 - math.cos(math.pi * frac))
                scale_kfs.append((round(frac * motion_dur, 4), _z_to_scale(z0 + (z1 - z0) * p_ease)))

        focus_pt = seg.get("focus") or [0.5, 0.38]
        focus_end_pt = seg.get("focus_end")
        v1_center_kfs: list[tuple[float, tuple[float, float]]] | None = None
        if isinstance(focus_end_pt, (list, tuple)) and len(focus_end_pt) >= 2 and max(z0, z1) > 1.004:
            fx0 = float(focus_pt[0]) if isinstance(focus_pt, (list, tuple)) and len(focus_pt) >= 2 else 0.5
            fy0 = float(focus_pt[1]) if isinstance(focus_pt, (list, tuple)) and len(focus_pt) >= 2 else 0.38
            fx1, fy1 = float(focus_end_pt[0]), float(focus_end_pt[1])
            c_start = (
                round((0.5 - fx0) * max(0.0, z0 - 1.0), 4),
                round((0.5 - fy0) * max(0.0, z0 - 1.0), 4),
            )
            c_end = (
                round((0.5 - fx1) * max(0.0, z1 - 1.0), 4),
                round((0.5 - fy1) * max(0.0, z1 - 1.0), 4),
            )
            v1_center = c_start
            if abs(c_end[0] - c_start[0]) > 0.0005 or abs(c_end[1] - c_start[1]) > 0.0005:
                v1_center_kfs = [
                    (0.0, c_start),
                    (round(motion_dur, 4), c_end),
                ]
        else:
            z_mid = 0.5 * (z0 + z1)
            if isinstance(focus_pt, (list, tuple)) and len(focus_pt) >= 2 and z_mid > 1.005:
                cx_f = round((0.5 - float(focus_pt[0])) * max(0.0, z_mid - 1.0), 4)
                cy_f = round((0.38 - float(focus_pt[1])) * max(0.0, z_mid - 1.0), 4)
                v1_center = (cx_f, cy_f)
            else:
                v1_center = (0.0, 0.0)
        v1_crop = None
        v1_crop_kfs: dict[str, list[tuple[float, float]]] | None = None

        slot = str(seg.get("slot") or "").lower()
        speaker_name = str(seg.get("speaker", "") or "").lower()
        active_first = (
            slot in ("left", "top", "pip", "slot_a", "a", "0")
            if slot
            else (("robot" in speaker_name) or ("nova" in speaker_name))
        )
        if layout_mode in ("split_h", "split_v"):
            z0_sp = max(1.0, float(z0))
            z1_sp = max(1.0, float(z1))
            v1_scale_start = _z_to_scale(z0_sp)
            v1_scale_end = _z_to_scale(z1_sp)
            v1_center_kfs = None
            if scale_kfs:
                crop_kf_seq = [
                    (rel_s, _split_seam_crop_pct(sc_val / max(0.01, base_scale * 100.0)))
                    for rel_s, sc_val in scale_kfs
                ]
            elif abs(z1_sp - z0_sp) > 0.004:
                crop_kf_seq = [
                    (0.0, _split_seam_crop_pct(z0_sp)),
                    (motion_dur, _split_seam_crop_pct(z1_sp)),
                ]
            else:
                crop_kf_seq = []
            c_side = _split_seam_crop_pct(z0_sp)
            if layout_mode == "split_h":
                v1_crop = {"left": c_side, "right": c_side, "top": 0.0, "bottom": 0.0}
                v1_crop_kfs = {"left": crop_kf_seq, "right": crop_kf_seq} if crop_kf_seq else None
                v1_center = (-0.25 if active_first else 0.25, 0.0)
            else:
                v1_crop = {"left": 0.0, "right": 0.0, "top": c_side, "bottom": c_side}
                v1_crop_kfs = {"top": crop_kf_seq, "bottom": crop_kf_seq} if crop_kf_seq else None
                v1_center = (0.0, -0.25 if active_first else 0.25)

        tr_raw = seg.get("transition")
        tr_spec = None
        if tr_raw and str(tr_raw).lower() != "cut" and idx > 0:
            tr_kind = str(tr_raw).strip().lower()
            raw_tr_d = seg.get("transition_duration") or seg.get("transition_dur")
            tr_dur = float(raw_tr_d) if raw_tr_d is not None else _default_transition_dur(tr_kind)
            tr_spec = {"type": tr_kind, "dur": tr_dur}

        markers.append({
            "t": t_start,
            "name": f"{seg_id} ({seg.get('speaker', layout_mode)})",
            "comment": str(seg.get("note") or ""),
        })

        def _append_live_hold_track_items(
            track_idx: int,
            h_sc_val: float,
            h_ctr_val: tuple[float, float],
            h_crop_val: dict[str, float] | None,
            hw_px: int,
            hh_px: int,
        ) -> None:
            if not has_live_hold or hold_path is None or t_end <= v_t_end + 0.015:
                return
            hold_media_dur = get_media_dur(hold_path, is_still=False)
            h_span = seg.get("hold_span")
            if isinstance(h_span, (list, tuple)) and len(h_span) >= 2:
                h_in = max(0.0, float(h_span[0]))
                h_bound = min(hold_media_dur, max(h_in + 0.12, float(h_span[1])))
            elif seg.get("hold_start") is not None:
                h_in = max(0.0, float(seg["hold_start"]))
                h_end_raw = seg.get("hold_end")
                h_bound = min(hold_media_dur, max(h_in + 0.12, float(h_end_raw))) if h_end_raw is not None else hold_media_dur
            else:
                h_in = 0.0
                h_bound = hold_media_dur
            if h_in >= max(0.05, hold_media_dur - 0.10):
                h_in = max(0.0, hold_media_dur - max(0.25, t_end - v_t_end))
                h_bound = hold_media_dur
            span_avail = max(0.12, h_bound - h_in)
            rem_h = t_end - v_t_end
            cur_h = v_t_end
            while rem_h > 0.015:
                chunk_h = min(rem_h, span_avail)
                v_tracks[track_idx].append({
                    "seg_idx": idx,
                    "name": f"{seg_id}_Hold_{hold_path.stem}",
                    "path": hold_path,
                    "media_type": "video",
                    "t_start": cur_h,
                    "t_end": cur_h + chunk_h,
                    "src_in": h_in,
                    "src_out": min(hold_media_dur, h_in + chunk_h),
                    "media_dur": hold_media_dur,
                    "clip_w": hw_px,
                    "clip_h": hh_px,
                    "speed": 1.0,
                    "mute": True,
                    "scale_start": h_sc_val,
                    "scale_end": h_sc_val,
                    "scale_keyframes": [],
                    "center": h_ctr_val,
                    "center_keyframes": None,
                    "crop": h_crop_val,
                    "crop_keyframes": None,
                    "opacity_keyframes": None,
                    "transition": None,
                    "link_id": None,
                })
                cur_h += chunk_h
                rem_h -= chunk_h

        seg_link_id = None if (is_muted or has_audio_end) else f"link_seg_{idx}"
        if layout_mode == "top_pip" and active_first:
            pip_scale, pip_center, pip_crop = _pip_transform(clip_w, clip_h)
            v_tracks[1].append({
                "seg_idx": idx,
                "name": f"{seg_id}_{clip_path.stem}",
                "path": clip_path,
                "media_type": "video",
                "t_start": t_start,
                "t_end": v_t_end,
                "src_in": src_in,
                "src_out": v_src_out,
                "media_dur": media_dur,
                "clip_w": clip_w,
                "clip_h": clip_h,
                "speed": spd,
                "mute": is_muted,
                "scale_start": pip_scale,
                "scale_end": pip_scale,
                "scale_keyframes": [],
                "center": pip_center,
                "center_keyframes": None,
                "crop": pip_crop,
                "crop_keyframes": None,
                "opacity_keyframes": None,
                "transition": tr_spec,
                "link_id": seg_link_id,
            })
            if has_live_hold and hold_path is not None and t_end > v_t_end + 0.015:
                hw, hh = get_media_dims(hold_path, is_still=False)
                h_pip_sc, h_pip_ctr, h_pip_crop = _pip_transform(hw, hh)
                _append_live_hold_track_items(1, h_pip_sc, h_pip_ctr, h_pip_crop, hw, hh)
            elif hold_png is not None and t_end > v_t_end + 0.015:
                hw, hh = get_media_dims(hold_png, is_still=True)
                h_pip_sc, h_pip_ctr, h_pip_crop = _pip_transform(hw, hh)
                v_tracks[1].append({
                    "seg_idx": idx,
                    "name": f"{seg_id}_Hold",
                    "path": hold_png,
                    "media_type": "still",
                    "t_start": v_t_end,
                    "t_end": t_end,
                    "src_in": 0.0,
                    "src_out": t_end - v_t_end,
                    "media_dur": 3600.0,
                    "clip_w": hw,
                    "clip_h": hh,
                    "scale_start": h_pip_sc,
                    "scale_end": h_pip_sc,
                    "scale_keyframes": [],
                    "center": h_pip_ctr,
                    "center_keyframes": None,
                    "crop": h_pip_crop,
                    "crop_keyframes": None,
                    "opacity_keyframes": None,
                    "transition": None,
                    "link_id": None,
                })
        else:
            v_tracks[0].append({
                "seg_idx": idx,
                "name": f"{seg_id}_{clip_path.stem}",
                "path": clip_path,
                "media_type": "video",
                "t_start": t_start,
                "t_end": v_t_end,
                "src_in": src_in,
                "src_out": v_src_out,
                "media_dur": media_dur,
                "clip_w": clip_w,
                "clip_h": clip_h,
                "speed": spd,
                "mute": is_muted,
                "scale_start": v1_scale_start,
                "scale_end": v1_scale_end,
                "scale_keyframes": scale_kfs,
                "center": v1_center,
                "center_keyframes": v1_center_kfs,
                "crop": v1_crop,
                "crop_keyframes": v1_crop_kfs,
                "opacity_keyframes": None,
                "transition": tr_spec,
                "link_id": seg_link_id,
            })
            if has_live_hold and hold_path is not None and t_end > v_t_end + 0.015:
                hw, hh = get_media_dims(hold_path, is_still=False)
                h_base_sc = (width / float(hw)) if hw else base_scale
                h_sc = round((scale_kfs[-1][1] if scale_kfs else v1_scale_end) * (h_base_sc / max(1e-6, base_scale)), 2)
                h_ctr = v1_center_kfs[-1][1] if v1_center_kfs else v1_center
                h_crop = v1_crop
                if v1_crop and v1_crop_kfs:
                    h_crop = dict(v1_crop)
                    for side_k, kf_seq in v1_crop_kfs.items():
                        if kf_seq:
                            h_crop[side_k] = kf_seq[-1][1]
                _append_live_hold_track_items(0, h_sc, h_ctr, h_crop, hw, hh)
            elif hold_png is not None and t_end > v_t_end + 0.015:
                hw, hh = get_media_dims(hold_png, is_still=True)
                h_base_sc = (width / float(hw)) if hw else base_scale
                h_sc = round((scale_kfs[-1][1] if scale_kfs else v1_scale_end) * (h_base_sc / max(1e-6, base_scale)), 2)
                h_ctr = v1_center_kfs[-1][1] if v1_center_kfs else v1_center
                h_crop = v1_crop
                if v1_crop and v1_crop_kfs:
                    h_crop = dict(v1_crop)
                    for side_k, kf_seq in v1_crop_kfs.items():
                        if kf_seq:
                            h_crop[side_k] = kf_seq[-1][1]
                v_tracks[0].append({
                    "seg_idx": idx,
                    "name": f"{seg_id}_Hold",
                    "path": hold_png,
                    "media_type": "still",
                    "t_start": v_t_end,
                    "t_end": t_end,
                    "src_in": 0.0,
                    "src_out": t_end - v_t_end,
                    "media_dur": 3600.0,
                    "clip_w": hw,
                    "clip_h": hh,
                    "scale_start": h_sc,
                    "scale_end": h_sc,
                    "scale_keyframes": [],
                    "center": h_ctr,
                    "center_keyframes": None,
                    "crop": h_crop,
                    "crop_keyframes": None,
                    "opacity_keyframes": None,
                    "transition": None,
                    "link_id": None,
                })

        if not is_muted:
            a_tracks[0].append({
                "seg_idx": idx,
                "name": f"{seg_id}_{clip_path.stem}",
                "path": clip_path,
                "media_type": "audio_from_video",
                "t_start": t_start,
                "t_end": a_t_end,
                "src_in": src_in,
                "src_out": a_src_out,
                "media_dur": media_dur,
                "clip_w": clip_w,
                "clip_h": clip_h,
                "speed": spd,
                "gain_db": vol_db,
                "transition": None,
                "link_id": seg_link_id,
            })

        # Peer / listening partner clip for split_h, split_v, video_call, and top_pip
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
                peer_w, peer_h = get_media_dims(peer_path, is_still=False)
                peer_base_scale = (width / float(peer_w)) if peer_w else 1.0
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
                v2_scale = round(peer_base_scale * 100.0, 2)
                target_v_track = 1
                if layout_mode == "split_h":
                    v2_crop = {"left": 25.0, "right": 25.0, "top": 0.0, "bottom": 0.0}
                    v2_center = (0.25 if active_first else -0.25, 0.0)
                elif layout_mode == "split_v":
                    v2_crop = {"left": 0.0, "right": 0.0, "top": 25.0, "bottom": 25.0}
                    v2_center = (0.0, 0.25 if active_first else -0.25)
                elif layout_mode == "video_call":
                    v2_scale = round(peer_base_scale * 28.0, 2)
                    v2_center = (0.30, -0.32)
                elif layout_mode == "top_pip":
                    if active_first:
                        target_v_track = 0
                        v2_scale = round(peer_base_scale * 100.0, 2)
                        v2_center = (0.0, 0.0)
                        v2_crop = None
                    else:
                        target_v_track = 1
                        v2_scale, v2_center, v2_crop = _pip_transform(peer_w, peer_h)

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
                        "clip_w": peer_w,
                        "clip_h": peer_h,
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

        # V3..V5: Stacked "Stop Saying" strikethrough banners
        if (seg.get("banners") or seg.get("stop_saying")) and _lay is not None and hasattr(_lay, "render_stop_saying_banner_png"):
            if hasattr(_lay, "parse_banners") and hasattr(_lay, "banner_stack_rows"):
                parsed_banners = _lay.parse_banners(seg, dur)
            else:
                raw_b = seg.get("banners") or seg.get("stop_saying") or []
                if isinstance(raw_b, (str, dict)):
                    raw_b = [raw_b]
                parsed_banners = []
                for b_idx, b_item in enumerate(raw_b):
                    if isinstance(b_item, str):
                        bt, ba, bd = b_item, round(0.15 + b_idx * 0.65, 3), dur
                    elif isinstance(b_item, dict):
                        bt = str(b_item.get("text") or b_item.get("phrase") or "STOP SAYING")
                        ba = float(b_item.get("at", round(0.15 + b_idx * 0.65, 3)))
                        bd = float(b_item.get("duration", max(0.4, dur - ba)))
                    else:
                        continue
                    is_hdr = bt.lower().startswith("header:")
                    parsed_banners.append((bt[7:].strip() if is_hdr else bt, ba, bd, is_hdr))

            if parsed_banners:
                if hasattr(_lay, "banner_stack_rows"):
                    stack_top, phrase_base = _lay.banner_stack_rows(seg, parsed_banners)
                else:
                    has_hdr_fb = any(pb[3] for pb in parsed_banners)
                    stack_top = 0.445
                    phrase_base = stack_top + 0.075 if has_hdr_fb else 0.52
                has_hdr = any(pb[3] for pb in parsed_banners)
                phrase_idx = 0
                for b_text, b_at, b_dur, is_header in parsed_banners:
                    b_end = min(dur, b_at + b_dur)
                    if b_end <= b_at + 0.02:
                        continue
                    if is_header:
                        png_hdr = pathlib.Path(
                            _lay.render_stop_saying_banner_png(b_text, with_strike=False, cross_icon=True)
                        ).resolve()
                        if png_hdr.exists():
                            bn_w, bn_h = get_media_dims(png_hdr, is_still=True)
                            y_off = round(stack_top + 0.5 * (bn_h * (width / 1080.0) / max(1, height)) - 0.50, 4)
                            v_tracks[2].append({
                                "seg_idx": idx,
                                "name": f"Banner_Header_{b_text[:16]}",
                                "path": png_hdr,
                                "media_type": "still",
                                "t_start": t_start + b_at,
                                "t_end": t_start + b_end,
                                "src_in": 0.0,
                                "src_out": b_end - b_at,
                                "media_dur": 3600.0,
                                "clip_w": bn_w,
                                "clip_h": bn_h,
                                "scale_start": ov_scale_pct,
                                "scale_end": ov_scale_pct,
                                "center": (0.0, y_off),
                                "crop": None,
                                "opacity_keyframes": None,
                                "link_id": None,
                            })
                        continue

                    if phrase_idx >= 3:
                        continue
                    strike_at = max(b_at, min(b_end - 0.12, b_at + 0.08))
                    y_frac = phrase_base + phrase_idx * 0.068
                    v_slot = min((3 + phrase_idx) if has_hdr else (2 + phrase_idx), 4)
                    phrase_idx += 1
                    png_plain = pathlib.Path(_lay.render_stop_saying_banner_png(b_text, with_strike=False)).resolve()
                    png_struck = pathlib.Path(_lay.render_stop_saying_banner_png(b_text, with_strike=True)).resolve()
                    has_plain = png_plain.exists() and strike_at > b_at + 0.03
                    if has_plain:
                        bn_w, bn_h = get_media_dims(png_plain, is_still=True)
                        y_off = round(y_frac + 0.5 * (bn_h * (width / 1080.0) / max(1, height)) - 0.50, 4)
                        v_tracks[v_slot].append({
                            "seg_idx": idx,
                            "name": f"Banner_{phrase_idx}_In_{b_text[:16]}",
                            "path": png_plain,
                            "media_type": "still",
                            "t_start": t_start + b_at,
                            "t_end": t_start + strike_at,
                            "src_in": 0.0,
                            "src_out": strike_at - b_at,
                            "media_dur": 3600.0,
                            "clip_w": bn_w,
                            "clip_h": bn_h,
                            "scale_start": ov_scale_pct,
                            "scale_end": ov_scale_pct,
                            "center": (0.0, y_off),
                            "crop": None,
                            "opacity_keyframes": None,
                            "link_id": None,
                        })
                    strike_t0 = strike_at if has_plain else b_at
                    if png_struck.exists() and b_end > strike_t0 + 0.03:
                        bn_w, bn_h = get_media_dims(png_struck, is_still=True)
                        y_off = round(y_frac + 0.5 * (bn_h * (width / 1080.0) / max(1, height)) - 0.50, 4)
                        v_tracks[v_slot].append({
                            "seg_idx": idx,
                            "name": f"Banner_{phrase_idx}_Strike_{b_text[:16]}",
                            "path": png_struck,
                            "media_type": "still",
                            "t_start": t_start + strike_t0,
                            "t_end": t_start + b_end,
                            "src_in": 0.0,
                            "src_out": b_end - strike_t0,
                            "media_dur": 3600.0,
                            "clip_w": bn_w,
                            "clip_h": bn_h,
                            "scale_start": ov_scale_pct,
                            "scale_end": ov_scale_pct,
                            "center": (0.0, y_off),
                            "crop": None,
                            "opacity_keyframes": None,
                            "link_id": None,
                        })
        elif idx in ov_assets["strikethrough"]:
            u_png, s_png = ov_assets["strikethrough"][idx]
            delay = float(seg.get("strikethrough_initial_delay", 0.0) or 0.0)
            if u_png and 0.05 < delay < dur:
                u_w, u_h = get_media_dims(u_png.resolve(), is_still=True)
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
                    "clip_w": u_w,
                    "clip_h": u_h,
                    "scale_start": ov_scale_pct,
                    "scale_end": ov_scale_pct,
                    "center": (0.0, 0.0),
                    "crop": None,
                    "opacity_keyframes": None,
                    "link_id": None,
                })
                if s_png:
                    s_w, s_h = get_media_dims(s_png.resolve(), is_still=True)
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
                        "clip_w": s_w,
                        "clip_h": s_h,
                        "scale_start": ov_scale_pct,
                        "scale_end": ov_scale_pct,
                        "center": (0.0, 0.0),
                        "crop": None,
                        "opacity_keyframes": None,
                        "link_id": None,
                    })
            elif s_png:
                s_w, s_h = get_media_dims(s_png.resolve(), is_still=True)
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
                    "clip_w": s_w,
                    "clip_h": s_h,
                    "scale_start": ov_scale_pct,
                    "scale_end": ov_scale_pct,
                    "center": (0.0, 0.0),
                    "crop": None,
                    "opacity_keyframes": None,
                    "link_id": None,
                })

        # Segment SFX and automatic transition whoosh
        seg_sfx_list = list(sfx_by_seg.get(seg_id, []))
        _whoosh_trs = _WHOOSH_TRANSITIONS
        prev_lay = str(segments[idx - 1].get("layout") or "single").lower() if idx > 0 else "single"
        same_split = idx > 0 and prev_lay == layout_mode.lower() and layout_mode.lower() in ("split_h", "split_v", "left_right", "top_bottom")
        has_tr_sfx = any(
            any(k in str(x.get("file", "")).lower() for k in ("whoosh", "swish", "swoosh", "transition"))
            for x in seg_sfx_list
        )
        if not same_split and str(seg.get("transition") or "").lower() in _whoosh_trs and not has_tr_sfx:
            seg_sfx_list.append({"at": 0.0, "file": "transition"})
        for sx in seg_sfx_list:
            sx_file = str(sx.get("file") or "whoosh.wav")
            sx_at = float(sx.get("at") or 0.0)
            sx_path = _resolve_sfx_path(sx_file, sfx_pack=sfx_pack)
            if sx_path is not None:
                place_sfx(sx_path, max(0.0, t_start + sx_at), gain_db=float(sx.get("gain_db", -2.0)))

        # V6 (index 5): Right / Wrong 2-pulse or badge mark overlays + SFX
        raw_seg_marks = [m for m in marks_by_seg.get(seg_id, []) if isinstance(m, dict)]
        seen_seg_marks = {
            (
                "wrong" if str(m.get("kind") or m.get("type") or "right").lower() in ("x", "cross", "error", "wrong") else "right",
                round(float(m["at"]) if m.get("at") is not None else max(0.0, dur - 0.60), 3),
            )
            for m in raw_seg_marks
        }
        for im in seg.get("marks", []) or []:
            if not isinstance(im, dict):
                continue
            im_k = "wrong" if str(im.get("kind") or im.get("type") or "right").lower() in ("x", "cross", "error", "wrong") else "right"
            im_at = round(float(im["at"]) if im.get("at") is not None else max(0.0, dur - 0.60), 3)
            if (im_k, im_at) not in seen_seg_marks:
                seen_seg_marks.add((im_k, im_at))
                raw_seg_marks.append(im)

        _strobe_styles = {"flash", "vignette", "kn20228", "w42", "strobe"}
        _fullframe_names = {"red_flash.png", "green_flash.png", "w42_red_vignette.png", "w42_green_vignette.png"}
        try:
            import style_config as _sc
            _plan_tuning = _sc.get_tuning(plan=plan)
        except Exception:
            _plan_tuning = {}

        for mk in raw_seg_marks:
            kind = str(mk.get("kind") or mk.get("type") or "right").lower()
            kind = "wrong" if kind in ("x", "cross", "error", "wrong") else "right"
            style_key = str(mk.get("style") or mk.get("pack") or mark_pack or "flash").strip()

            if hasattr(_common, "resolve_mark_asset"):
                mk_png = pathlib.Path(_common.resolve_mark_asset(kind, style=style_key)).resolve()
            else:
                mk_png = (REPO / "assets" / "marks" / f"{kind}.png").resolve()
            if not mk_png.is_file():
                if style_key.lower() in ("vignette", "w42"):
                    png_name = "w42_green_vignette.png" if kind == "right" else "w42_red_vignette.png"
                elif style_key.lower() in ("default", "badge"):
                    png_name = f"{kind}.png"
                elif style_key.lower() in ("glossy", "supernova"):
                    png_name = f"glossy_{kind}.png"
                else:
                    png_name = "green_flash.png" if kind == "right" else "red_flash.png"
                mk_png = (REPO / "assets" / "marks" / png_name).resolve()

            is_fullframe_strobe = (
                style_key.lower() in _strobe_styles
                or mk_png.name.lower() in _fullframe_names
                or bool(mk.get("strobe", False))
            )

            mk_at = float(mk["at"]) if mk.get("at") is not None else max(0.0, dur - 0.60)
            raw_mk_dur = float(mk.get("duration") or 0.56)
            mk_dur = min(0.56, raw_mk_dur) if is_fullframe_strobe else raw_mk_dur
            mk_start = t_start + max(0.0, min(mk_at, max(0.0, dur - 0.1)))
            mk_end = min(t_end, mk_start + mk_dur)
            if mk_end - mk_start < 0.08:
                mk_start = max(t_start, t_end - min(mk_dur, dur))
                mk_end = t_end
            actual_mk_dur = max(0.08, mk_end - mk_start)
            if is_fullframe_strobe:
                actual_mk_dur = min(0.56, actual_mk_dur)

            if is_fullframe_strobe:
                raw_alpha = mk.get("alpha") if mk.get("alpha") is not None else _plan_tuning.get("mark_alpha", 0.70)
                alpha_pct = round(max(15.0, min(100.0, float(raw_alpha) * 100.0)), 2)
                if kind == "wrong" and actual_mk_dur >= 0.30:
                    if actual_mk_dur >= 0.46:
                        p1_dur, p2_start = 0.16, 0.22
                        p2_hold_end = round(max(0.24, actual_mk_dur - 0.01), 4)
                    else:
                        p1_dur = round(min(0.16, actual_mk_dur * 0.35), 4)
                        p2_start = round(min(0.22, actual_mk_dur * 0.48), 4)
                        p2_hold_end = round(max(p2_start + 0.02, actual_mk_dur - 0.01), 4)
                    op_kfs = [
                        (0.00, alpha_pct),
                        (p1_dur, alpha_pct),
                        (round(p1_dur + 0.01, 4), 0.0),
                        (round(p2_start - 0.01, 4), 0.0),
                        (p2_start, alpha_pct),
                        (p2_hold_end, alpha_pct),
                        (round(actual_mk_dur, 4), 0.0),
                    ]
                else:
                    fade_st = round(max(0.04, actual_mk_dur - 0.05), 4)
                    op_kfs = [
                        (0.00, alpha_pct),
                        (fade_st, alpha_pct),
                        (round(actual_mk_dur, 4), 0.0),
                    ]
                mk_center = (0.0, 0.0)
                mk_base_op = alpha_pct
                badge_size = 0
            else:
                badge_size = max(2, int(round(min(width, height) * float(mk.get("size", 0.22)) / 2.0)) * 2)
                if _lay is not None and hasattr(_lay, "place_mark_for_segment"):
                    x_expr, y_expr = _lay.place_mark_for_segment(mk, seg, width, height, badge_size)
                else:
                    m_pad = int(min(width, height) * 0.06)
                    pos = mk.get("position", "top-right")
                    if isinstance(pos, (list, tuple)) and len(pos) >= 2:
                        x_expr, y_expr = f"{float(pos[0])}*{width}-{badge_size}/2", f"{float(pos[1])}*{height}-{badge_size}/2"
                    else:
                        pos_str = str(pos or "top-right").strip().lower()
                        coords = {
                            "center": (str((width - badge_size) / 2.0), str((height - badge_size) / 2.0)),
                            "top-center": (str((width - badge_size) / 2.0), str(m_pad)),
                            "top-right": (str(width - badge_size - m_pad), str(m_pad)),
                            "top-left": (str(m_pad), str(m_pad)),
                            "bottom-right": (str(width - badge_size - m_pad), str(height - badge_size - m_pad)),
                            "bottom-left": (str(m_pad), str(height - badge_size - m_pad)),
                        }
                        x_expr, y_expr = coords.get(pos_str, coords["top-right"])
                x_px = _eval_px_expr(x_expr, (width - badge_size) / 2.0)
                y_px = _eval_px_expr(y_expr, (height - badge_size) / 2.0)
                mk_center = (
                    round(((x_px + 0.5 * badge_size) / max(1.0, float(width))) - 0.5, 4),
                    round(((y_px + 0.5 * badge_size) / max(1.0, float(height))) - 0.5, 4),
                )
                fade_in_end = round(min(0.12, actual_mk_dur * 0.35), 4)
                fade_out_st = round(max(fade_in_end + 0.02, actual_mk_dur - 0.20), 4)
                op_kfs = [
                    (0.00, 0.0),
                    (fade_in_end, 100.0),
                    (fade_out_st, 100.0),
                    (round(actual_mk_dur, 4), 0.0),
                ]
                mk_base_op = 100.0

            if mk_png.exists():
                mk_w, mk_h = get_media_dims(mk_png, is_still=True)
                eff_scale_pct = (
                    round((width / float(max(1, mk_w))) * 100.0, 2)
                    if is_fullframe_strobe
                    else round((badge_size / float(max(1, mk_w))) * 100.0, 2)
                )
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
                    "clip_w": mk_w,
                    "clip_h": mk_h,
                    "scale_start": eff_scale_pct,
                    "scale_end": eff_scale_pct,
                    "center": mk_center,
                    "crop": None,
                    "is_mark": True,
                    "opacity": mk_base_op,
                    "opacity_keyframes": op_kfs,
                    "link_id": None,
                })

            has_explicit_sfx = "sfx" in mk
            mk_sfx_req = mk.get("sfx")
            if has_explicit_sfx and (mk_sfx_req is None or str(mk_sfx_req).strip().lower() in ("none", "off", "silent")):
                pass
            elif mk_sfx_req and str(mk_sfx_req).strip().lower() != "default":
                mk_sfx_p = _resolve_sfx_path(str(mk_sfx_req), sfx_pack=sfx_pack)
                if mk_sfx_p is not None:
                    place_sfx(
                        mk_sfx_p,
                        mk_start,
                        gain_db=float(
                            mk.get("sfx_gain_db")
                            if mk.get("sfx_gain_db") is not None
                            else (
                                _plan_tuning.get("wrong_sfx_gain_db", 1.0)
                                if kind == "wrong"
                                else _plan_tuning.get("right_sfx_gain_db", 0.5)
                            )
                        ),
                    )
            elif kind == "wrong":
                wrong_req = str(plan.get("error_sfx") or "wrong")
                wrong_p = _resolve_sfx_path(wrong_req, sfx_pack=sfx_pack) or (REPO / "assets" / "sfx" / "sn_wrong.wav")
                place_sfx(wrong_p, mk_start, gain_db=float(_plan_tuning.get("wrong_sfx_gain_db", 1.0)))
            else:
                right_p = _resolve_sfx_path("right", sfx_pack=sfx_pack) or (REPO / "assets" / "sfx" / "correct_ding.wav")
                place_sfx(right_p, mk_start, gain_db=float(_plan_tuning.get("right_sfx_gain_db", 0.5)))
                if is_fullframe_strobe and (not sfx_pack or str(sfx_pack).lower() == "default"):
                    layer_name = str(_plan_tuning.get("right_sfx_layer", "sn_correct.wav"))
                    if layer_name and layer_name.lower() != "none":
                        layer_p = _resolve_sfx_path(layer_name, sfx_pack=sfx_pack) or (REPO / "assets" / "sfx" / "sn_correct.wav")
                        place_sfx(layer_p, mk_start, gain_db=max(-2.0, float(_plan_tuning.get("right_sfx_gain_db", 0.5)) - 2.0))

            markers.append({
                "t": mk_start,
                "name": f"{'✓ RIGHT' if kind == 'right' else '✗ WRONG'} ({seg_id})",
                "comment": f"{kind.upper()} {'2-pulse' if is_fullframe_strobe and kind == 'wrong' else ('glow' if is_fullframe_strobe else 'badge')} mark ({style_key})",
            })

        cursor = t_end

    # V7 (index 6): Bilingual flashcards from overlays/flashcards_meta.json
    cards_off_raw = plan.get("cards_off")
    cards_disabled = (
        cards_off_raw.strip().lower() in ("1", "true", "yes", "on")
        if isinstance(cards_off_raw, str)
        else bool(cards_off_raw)
    )
    if not cards_disabled:
        fc_meta_candidates: list[pathlib.Path] = [
            plan_path.parent / "overlays" / "flashcards_meta.json",
            work_dir / "overlays" / "flashcards_meta.json",
        ]
        fc_meta_file = next((c for c in fc_meta_candidates if c.is_file()), None)
        fc_entries: list[dict] = []
        fc_sfx_entries: list[dict] = []
        fc_meta_w = float(width)
        fc_meta_h = float(height)
        if fc_meta_file is not None:
            try:
                raw_fc = json.loads(fc_meta_file.read_text(encoding="utf-8"))
                if isinstance(raw_fc, dict):
                    fc_meta_w = float(raw_fc.get("canvas_w") or raw_fc.get("width") or width or 1080.0)
                    fc_meta_h = float(raw_fc.get("canvas_h") or raw_fc.get("height") or height or 1920.0)
                    if isinstance(raw_fc.get("cards"), list):
                        fc_entries = [c for c in raw_fc["cards"] if isinstance(c, dict)]
                    if isinstance(raw_fc.get("sfx"), list):
                        fc_sfx_entries = [s for s in raw_fc["sfx"] if isinstance(s, dict)]
                elif isinstance(raw_fc, list):
                    fc_entries = [c for c in raw_fc if isinstance(c, dict)]
            except Exception:
                fc_entries = []
                fc_sfx_entries = []

        if fc_entries:
            fc_base_dir = fc_meta_file.parent if fc_meta_file is not None else (plan_path.parent / "overlays")
            fc_entries.sort(key=lambda c: (float(c.get("start", 0.0) or 0.0), float(c.get("end", 0.0) or 0.0)))
            eff_fc_w = fc_meta_w if fc_meta_w > 0 else float(max(1, width))
            eff_fc_h = fc_meta_h if fc_meta_h > 0 else float(max(1, height))
            fc_scale_pct = round((float(width) / eff_fc_w) * 100.0, 2)

            for fc_idx, fc in enumerate(fc_entries):
                raw_p_str = fc.get("png") or fc.get("path") or fc.get("file")
                if not raw_p_str:
                    continue
                png_cand = pathlib.Path(str(raw_p_str))
                if not png_cand.is_absolute():
                    for b_dir in (fc_base_dir, plan_path.parent, work_dir, REPO):
                        if (b_dir / png_cand).is_file():
                            png_cand = (b_dir / png_cand).resolve()
                            break
                if not png_cand.is_file():
                    continue
                png_res = png_cand.resolve()

                try:
                    fc_t0 = max(0.0, float(fc.get("start", 0.0)))
                    fc_t1 = float(fc.get("end", 0.0))
                    x0 = float(fc.get("x0", 0.0))
                    y0 = float(fc.get("y0", 0.0))
                except (TypeError, ValueError):
                    continue

                if cursor > 0.0:
                    fc_t1 = min(cursor, fc_t1)
                if v_tracks[6]:
                    prev_end = float(v_tracks[6][-1]["t_end"])
                    if fc_t0 < prev_end:
                        fc_t0 = prev_end
                fc_dur = fc_t1 - fc_t0
                if fc_dur < 0.02:
                    continue

                fc_png_w, fc_png_h = get_media_dims(png_res, is_still=True)
                card_w = (
                    float(fc["w"])
                    if fc.get("w") is not None
                    else ((float(fc["x1"]) - x0) if fc.get("x1") is not None else float(fc_png_w))
                )
                card_h = (
                    float(fc["h"])
                    if fc.get("h") is not None
                    else ((float(fc["y1"]) - y0) if fc.get("y1") is not None else float(fc_png_h))
                )
                cx = round(((x0 + 0.5 * card_w) / eff_fc_w) - 0.5, 6)
                cy = round(((y0 + 0.5 * card_h) / eff_fc_h) - 0.5, 6)

                v_tracks[6].append({
                    "seg_idx": -1,
                    "name": f"Card_{fc_idx + 1:02d}_{png_res.stem}",
                    "path": png_res,
                    "media_type": "still",
                    "t_start": round(fc_t0, 4),
                    "t_end": round(fc_t1, 4),
                    "src_in": 0.0,
                    "src_out": round(fc_dur, 4),
                    "media_dur": 3600.0,
                    "clip_w": fc_png_w,
                    "clip_h": fc_png_h,
                    "scale_start": fc_scale_pct,
                    "scale_end": fc_scale_pct,
                    "scale_keyframes": [],
                    "center": (cx, cy),
                    "center_keyframes": None,
                    "crop": None,
                    "crop_keyframes": None,
                    "opacity_keyframes": None,
                    "link_id": None,
                })

                fc_en = str(fc.get("en") or fc.get("text") or "").strip()
                fc_nat = str(fc.get("native") or "").strip()
                fc_label = fc_en or fc_nat or f"Card {fc_idx + 1}"
                markers.append({
                    "t": round(fc_t0, 4),
                    "name": f"CARD: {fc_label[:28]}",
                    "comment": f"{fc_en} | {fc_nat}".strip(" |"),
                })

            for sx in fc_sfx_entries:
                sx_file = sx.get("file") or sx.get("path")
                if sx_file:
                    try:
                        sx_t = max(0.0, float(sx.get("t_play", sx.get("at", 0.0))))
                        sx_g = float(sx.get("gain_db", -2.5))
                        place_sfx(str(sx_file), sx_t, gain_db=sx_g)
                    except (TypeError, ValueError):
                        continue

    # V8 (index 7): Rendered subtitle PNG frames from _subs/subs_meta.json (with _subs/subs.ffconcat fallback)
    subs_meta_candidates: list[pathlib.Path] = [
        work_dir / "_subs" / "subs_meta.json",
        plan_path.parent / "_subs" / "subs_meta.json",
    ]
    if plan.get("subs"):
        global_work = getattr(_common, "WORK", REPO / "work")
        subs_meta_candidates.append(pathlib.Path(global_work) / "_subs" / "subs_meta.json")

    subs_meta_file = next((c for c in subs_meta_candidates if c.is_file()), None)
    sub_entries: list[dict] = []
    meta_canvas_w = 1080.0
    meta_canvas_h = 1920.0
    default_band_y0: float | None = None

    if subs_meta_file is not None:
        try:
            raw_meta = json.loads(subs_meta_file.read_text(encoding="utf-8"))
            if isinstance(raw_meta, dict):
                meta_canvas_w = float(raw_meta.get("canvas_w") or raw_meta.get("width") or raw_meta.get("render_w") or 1080.0)
                meta_canvas_h = float(raw_meta.get("canvas_h") or raw_meta.get("height") or raw_meta.get("render_h") or 1920.0)
                if raw_meta.get("band_y0") is not None:
                    default_band_y0 = float(raw_meta["band_y0"])
                elif raw_meta.get("y0") is not None:
                    default_band_y0 = float(raw_meta["y0"])
                for key_f in ("frames", "cues", "items", "pngs"):
                    if isinstance(raw_meta.get(key_f), list):
                        sub_entries = list(raw_meta[key_f])
                        break
            elif isinstance(raw_meta, list):
                sub_entries = list(raw_meta)
        except Exception:
            sub_entries = []
    elif plan.get("subs"):
        ffconcat_candidates = [
            work_dir / "_subs" / "subs.ffconcat",
            plan_path.parent / "_subs" / "subs.ffconcat",
        ]
        ffconcat_file = next((c for c in ffconcat_candidates if c.is_file()), None)
        if ffconcat_file is not None:
            try:
                t_acc = 0.0
                pending_file: str | None = None
                for ln in ffconcat_file.read_text(encoding="utf-8", errors="ignore").splitlines():
                    ln = ln.strip()
                    if ln.startswith("file "):
                        pending_file = ln[5:].strip().strip("'\"")
                    elif ln.startswith("duration ") and pending_file is not None:
                        d_sec = float(ln.split(None, 1)[1])
                        if pathlib.Path(pending_file).name != "blank.png":
                            sub_entries.append({"path": pending_file, "start": t_acc, "duration": d_sec})
                        t_acc += d_sec
                        pending_file = None
            except Exception:
                sub_entries = []

    if sub_entries:
        persist_sub_dir = work_dir / "overlays" / "subtitles"
        persist_sub_dir.mkdir(parents=True, exist_ok=True)
        subs_base_dir = subs_meta_file.parent if subs_meta_file is not None else (work_dir / "_subs")
        for s_idx, fr in enumerate(sub_entries):
            if not isinstance(fr, dict):
                continue
            raw_p_str = fr.get("path") or fr.get("png") or fr.get("file")
            if not raw_p_str:
                continue
            png_cand = pathlib.Path(str(raw_p_str))
            if not png_cand.is_absolute():
                for b_dir in (subs_base_dir, work_dir, plan_path.parent, REPO):
                    if (b_dir / png_cand).is_file():
                        png_cand = (b_dir / png_cand).resolve()
                        break
            if not png_cand.is_file() or png_cand.name == "blank.png":
                continue

            if "start_sec" in fr:
                sub_t0 = float(fr["start_sec"])
            elif "startSec" in fr:
                sub_t0 = float(fr["startSec"])
            elif "t_start" in fr:
                sub_t0 = float(fr["t_start"])
            elif "start" in fr:
                sub_t0 = float(fr["start"])
            elif "startMs" in fr or "start_ms" in fr:
                sub_t0 = float(fr.get("startMs") if "startMs" in fr else fr["start_ms"]) / 1000.0
            else:
                continue

            if "end_sec" in fr:
                sub_t1 = float(fr["end_sec"])
            elif "endSec" in fr:
                sub_t1 = float(fr["endSec"])
            elif "t_end" in fr:
                sub_t1 = float(fr["t_end"])
            elif "end" in fr:
                sub_t1 = float(fr["end"])
            elif "endMs" in fr or "end_ms" in fr:
                sub_t1 = float(fr.get("endMs") if "endMs" in fr else fr["end_ms"]) / 1000.0
            elif "duration" in fr or "dur" in fr:
                sub_t1 = sub_t0 + float(fr.get("duration") if "duration" in fr else fr["dur"])
            elif "durationMs" in fr or "duration_ms" in fr:
                sub_t1 = sub_t0 + float(fr.get("durationMs") if "durationMs" in fr else fr["duration_ms"]) / 1000.0
            else:
                continue

            if cursor > 0.0:
                sub_t1 = min(cursor, sub_t1)
            sub_dur = sub_t1 - sub_t0
            if sub_dur < 0.02:
                continue

            persisted_png = persist_sub_dir / f"sub_{s_idx:04d}_{png_cand.name}"
            if png_cand.resolve() != persisted_png.resolve():
                try:
                    shutil.copy2(png_cand, persisted_png)
                except Exception:
                    persisted_png = png_cand.resolve()

            sub_w, sub_h = get_media_dims(persisted_png, is_still=True)
            eff_meta_w = meta_canvas_w if meta_canvas_w > 0 else float(max(1, sub_w))
            eff_meta_h = meta_canvas_h if meta_canvas_h > 0 else float(max(1, sub_h))
            sub_scale_pct = round((width / eff_meta_w) * 100.0, 2)

            fr_band_y0 = fr.get("band_y0")
            if fr_band_y0 is None and fr.get("y0") is not None:
                fr_band_y0 = fr.get("y0")
            if fr_band_y0 is None and fr.get("bandY0") is not None:
                fr_band_y0 = fr.get("bandY0")
            if fr_band_y0 is None:
                fr_band_y0 = default_band_y0

            if fr_band_y0 is not None and (sub_h < eff_meta_h * 0.95 or float(fr_band_y0) > 0.5):
                cy_norm = (float(fr_band_y0) + 0.5 * float(sub_h)) / eff_meta_h
                sub_center = (0.0, round(cy_norm - 0.5, 6))
            else:
                sub_center = (0.0, 0.0)

            v_tracks[7].append({
                "seg_idx": -1,
                "name": f"Sub_{s_idx + 1:03d}_{persisted_png.stem}",
                "path": persisted_png.resolve(),
                "media_type": "still",
                "t_start": round(sub_t0, 4),
                "t_end": round(sub_t1, 4),
                "src_in": 0.0,
                "src_out": round(sub_dur, 4),
                "media_dur": 3600.0,
                "clip_w": sub_w,
                "clip_h": sub_h,
                "scale_start": sub_scale_pct,
                "scale_end": sub_scale_pct,
                "center": sub_center,
                "crop": None,
                "opacity_keyframes": None,
                "link_id": None,
            })

    # Keep V7 (index 6 = Flashcards) and V8 (index 7 = Subtitles) intact when either exists;
    # otherwise compact unused overlay tracks.
    if v_tracks[6] or v_tracks[7]:
        compacted_v = v_tracks
    else:
        compacted_v = v_tracks[:2] + [vt for vt in v_tracks[2:6] if vt]
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
    """Write a Final Cut Pro 7 / Adobe Premiere Pro `xmeml version='4'` interchange file (`.premiere.xml`)."""
    fps = model["fps"]
    width = model["width"]
    height = model["height"]
    total_frames = max(1, _sec_to_frames(model["total_duration"], fps))

    file_registry: dict[str, str] = {}
    next_file_idx = 1

    def get_file_xml(
        path: pathlib.Path,
        media_type: str,
        media_dur: float,
        clip_w: int = width,
        clip_h: int = height,
    ) -> str:
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
                    <width>{clip_w}</width>
                    <height>{clip_h}</height>
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
                    <width>{clip_w}</width>
                    <height>{clip_h}</height>
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
        '  <sequence id="sequence-1">',
        f'    <uuid>{uuid.uuid4()}</uuid>',
        f'    <name>{escape(seq_name)}</name>',
        f'    <duration>{total_frames}</duration>',
        f'    <rate><timebase>{fps}</timebase><ntsc>FALSE</ntsc></rate>',
        '    <timecode>',
        f'      <rate><timebase>{fps}</timebase><ntsc>FALSE</ntsc></rate>',
        '      <string>00:00:00:00</string>',
        '      <frame>0</frame>',
        '      <displayformat>NDF</displayformat>',
        '    </timecode>',
        '    <media>',
        '      <video>',
        '        <format>',
        '          <samplecharacteristics>',
        f'            <rate><timebase>{fps}</timebase><ntsc>FALSE</ntsc></rate>',
        f'            <width>{width}</width>',
        f'            <height>{height}</height>',
        '            <anamorphic>FALSE</anamorphic>',
        '            <pixelaspectratio>square</pixelaspectratio>',
        '            <fielddominance>none</fielddominance>',
        '          </samplecharacteristics>',
        '        </format>',
    ]

    clipitem_counter = 1
    link_map: dict[str, dict] = {}

    for vt_idx in range(min(2, len(model["v_tracks"]))):
        for vc_idx, v_item in enumerate(model["v_tracks"][vt_idx]):
            v_item["xml_id"] = f"clipitem-v{vt_idx + 1}-{clipitem_counter}"
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

    # Emit video tracks (preserve all 8 tracks V1..V8 when V7 flashcards or V8 subtitles exist
    # so V7 Flashcards and V8 Subtitles remain locked at their exact track indices)
    has_v7_or_v8 = len(model["v_tracks"]) >= 8 and (bool(model["v_tracks"][6]) or bool(model["v_tracks"][7]))
    for t_idx, v_track in enumerate(model["v_tracks"]):
        if not v_track and t_idx > 1 and not (has_v7_or_v8 and t_idx <= 7):
            continue
        lines.append("        <track>")
        for item in v_track:
            cid = item.get("xml_id") or f"clipitem-v{t_idx + 1}-{clipitem_counter}"
            clipitem_counter += 1
            start_f = _sec_to_frames(item["t_start"], fps)
            end_f = max(start_f + 1, _sec_to_frames(item["t_end"], fps))
            dur_f = end_f - start_f
            item_speed = float(item.get("speed", 1.0) or 1.0)
            has_speed = abs(item_speed - 1.0) > 1e-3
            in_f = _sec_to_frames(item["src_in"], fps)
            out_f = max(in_f + 1, _sec_to_frames(item["src_out"], fps)) if has_speed else (in_f + dur_f)

            def _kf_frame(rel_s: float) -> int:
                src_rel = float(rel_s) * item_speed if has_speed else float(rel_s)
                return max(in_f, min(out_f, in_f + _sec_to_frames(src_rel, fps)))

            file_xml = get_file_xml(
                item["path"],
                item["media_type"],
                item["media_dur"],
                int(item.get("clip_w") or width),
                int(item.get("clip_h") or height),
            )

            # Video transition on V1 strictly uses <mediatype>video</mediatype>
            if t_idx == 0 and item.get("transition"):
                tr_info = item["transition"]
                tr_t = str(tr_info.get("type") or "fade").lower()
                tr_d = float(tr_info.get("dur") or _default_transition_dur(tr_t))
                half_f = max(2, _sec_to_frames(tr_d * 0.5, fps))
                ef_name, ef_id = _map_fcp7_transition(tr_t)
                lines.append(f"""          <transitionitem>
            <rate><timebase>{fps}</timebase><ntsc>FALSE</ntsc></rate>
            <start>{max(0, start_f - half_f)}</start>
            <end>{start_f + half_f}</end>
            <alignment>center</alignment>
            <effect>
              <name>{ef_name}</name>
              <effectid>{ef_id}</effectid>
              <effectcategory>Dissolve</effectcategory>
              <effecttype>transition</effecttype>
              <mediatype>video</mediatype>
            </effect>
          </transitionitem>""")

            s0 = item["scale_start"]
            s1 = item["scale_end"]
            cx, cy = item["center"]
            scale_kfs = item.get("scale_keyframes") or []
            if scale_kfs:
                scale_kf = "".join(
                    f"\n                  <keyframe><when>{_kf_frame(rel_s)}</when><value>{val}</value></keyframe>"
                    for rel_s, val in scale_kfs
                )
            elif abs(s0 - s1) > 0.05:
                scale_kf = f"""
                  <keyframe><when>{in_f}</when><value>{s0}</value></keyframe>
                  <keyframe><when>{out_f}</when><value>{s1}</value></keyframe>"""
            else:
                scale_kf = ""

            center_kfs = item.get("center_keyframes") or []
            if center_kfs:
                center_kf = "".join(
                    f"\n                  <keyframe><when>{_kf_frame(rel_s)}</when><value><horiz>{kcx}</horiz><vert>{kcy}</vert></value></keyframe>"
                    for rel_s, (kcx, kcy) in center_kfs
                )
            else:
                center_kf = ""

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
                    <value><horiz>{cx}</horiz><vert>{cy}</vert></value>{center_kf}
                  </parameter>
                </effect>
              </filter>"""

            if has_speed:
                speed_pct = round(item_speed * 100.0, 2)
                fb_val = "TRUE" if item_speed < 0.999 else "FALSE"
                filters_xml += f"""
              <filter>
                <effect>
                  <name>Time Remap</name>
                  <effectid>timeremap</effectid>
                  <effectcategory>motion</effectcategory>
                  <effecttype>motion</effecttype>
                  <mediatype>video</mediatype>
                  <parameter>
                    <parameterid>variablespeed</parameterid>
                    <name>variablespeed</name>
                    <valuemin>0</valuemin>
                    <valuemax>1</valuemax>
                    <value>0</value>
                  </parameter>
                  <parameter>
                    <parameterid>speed</parameterid>
                    <name>speed</name>
                    <valuemin>-100000</valuemin>
                    <valuemax>100000</valuemax>
                    <value>{speed_pct}</value>
                  </parameter>
                  <parameter>
                    <parameterid>reverse</parameterid>
                    <name>reverse</name>
                    <value>FALSE</value>
                  </parameter>
                  <parameter>
                    <parameterid>frameblending</parameterid>
                    <name>frameblending</name>
                    <value>{fb_val}</value>
                  </parameter>
                </effect>
              </filter>"""

            if item.get("crop"):
                c = item["crop"]
                c_kfs = item.get("crop_keyframes") or {}
                crop_params_xml = ""
                for side_key in ("left", "right", "top", "bottom"):
                    side_kfs = c_kfs.get(side_key) or []
                    side_kf_str = "".join(
                        f"<keyframe><when>{_kf_frame(rel_s)}</when><value>{c_val}</value></keyframe>"
                        for rel_s, c_val in side_kfs
                    )
                    crop_params_xml += (
                        f"\n                  <parameter><parameterid>{side_key}</parameterid><name>{side_key}</name>"
                        f"<valuemin>0</valuemin><valuemax>100</valuemax><value>{c[side_key]}</value>{side_kf_str}</parameter>"
                    )
                filters_xml += f"""
              <filter>
                <effect>
                  <name>Crop</name>
                  <effectid>crop</effectid>
                  <effectcategory>motion</effectcategory>
                  <effecttype>motion</effecttype>
                  <mediatype>video</mediatype>{crop_params_xml}
                </effect>
              </filter>"""

            op_kfs = item.get("opacity_keyframes") or []
            op_static = item.get("opacity")
            if item.get("is_mark") and not op_kfs and op_static is None:
                op_static = 70.0
            if op_kfs or (op_static is not None and abs(float(op_static) - 100.0) > 0.05):
                kf_xml = ""
                prev_kf_f = -1
                for rel_s, val in op_kfs:
                    kf_f = _kf_frame(rel_s)
                    if kf_f <= prev_kf_f and prev_kf_f < out_f:
                        kf_f = prev_kf_f + 1
                    prev_kf_f = kf_f
                    kf_xml += f"\n                  <keyframe><when>{kf_f}</when><value>{val}</value></keyframe>"
                op_base_val = float(op_static) if op_static is not None else 70.0
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
                    <value>{op_base_val}</value>{kf_xml}
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
            <duration>{max(dur_f, out_f, _sec_to_frames(item['media_dur'], fps))}</duration>
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
            cid = item.get("xml_id") or f"clipitem-a{t_idx + 1}-{clipitem_counter}"
            clipitem_counter += 1
            start_f = _sec_to_frames(item["t_start"], fps)
            end_f = max(start_f + 1, _sec_to_frames(item["t_end"], fps))
            dur_f = end_f - start_f
            a_speed = float(item.get("speed", 1.0) or 1.0)
            a_has_speed = abs(a_speed - 1.0) > 1e-3
            in_f = _sec_to_frames(item["src_in"], fps)
            out_f = max(in_f + 1, _sec_to_frames(item["src_out"], fps)) if a_has_speed else (in_f + dur_f)
            a_enabled = "FALSE" if item.get("mute") else "TRUE"
            file_xml = get_file_xml(
                item["path"],
                "video" if item["media_type"] == "audio_from_video" else "audio",
                item["media_dur"],
                int(item.get("clip_w") or width),
                int(item.get("clip_h") or height),
            )
            lvl = _db_to_linear(item.get("gain_db", 0.0))
            a_speed_xml = ""
            if a_has_speed:
                a_speed_pct = round(a_speed * 100.0, 2)
                a_speed_xml = f"""
            <filter>
              <effect>
                <name>Time Remap</name>
                <effectid>timeremap</effectid>
                <effectcategory>motion</effectcategory>
                <effecttype>motion</effecttype>
                <mediatype>audio</mediatype>
                <parameter>
                  <parameterid>variablespeed</parameterid>
                  <name>variablespeed</name>
                  <valuemin>0</valuemin>
                  <valuemax>1</valuemax>
                  <value>0</value>
                </parameter>
                <parameter>
                  <parameterid>speed</parameterid>
                  <name>speed</name>
                  <valuemin>-100000</valuemin>
                  <valuemax>100000</valuemax>
                  <value>{a_speed_pct}</value>
                </parameter>
                <parameter>
                  <parameterid>reverse</parameterid>
                  <name>reverse</name>
                  <value>FALSE</value>
                </parameter>
              </effect>
            </filter>"""
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
            <enabled>{a_enabled}</enabled>
            <duration>{max(dur_f, out_f, _sec_to_frames(item['media_dur'], fps))}</duration>
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
                  <valuemax>5.623413</valuemax>
                  <value>{lvl}</value>
                </parameter>
              </effect>
            </filter>{a_speed_xml}{a_link_xml}
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
    """Write a native GZIP-compressed Adobe Premiere Pro project (`.prproj`) by populating
    `assets/premiere/base_1080x1920.prproj.gz` with Media, MasterClips, SubClips, TrackItems, and Links."""
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
        ("MZ.Sequence.PreviewFrameSizeWidth", str(width)),
        ("MZ.Sequence.PreviewFrameSizeHeight", str(height)),
    ):
        node = seq_el.find(f".//{tag_name}")
        if node is not None:
            node.text = val_str

    v_tg = by_id["167"]
    v_tg_rect = v_tg.find(".//FrameRect")
    if v_tg_rect is not None:
        v_tg_rect.text = f"0,0,{width},{height}"
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

    master_registry: dict[str, dict] = {}
    new_elements: list[ET.Element] = []

    def register_media(
        path: pathlib.Path,
        media_type: str,
        media_dur: float,
        clip_w: int = width,
        clip_h: int = height,
    ) -> dict:
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
                <FrameRect>0,0,{clip_w},{clip_h}</FrameRect>
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
                    <FrameRect>0,0,{clip_w},{clip_h}</FrameRect>
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
            new_elements.append(ET.fromstring(
                f'<SecondaryContent ObjectID="{sc0_id}" ClassID="f9d004b5-cb04-4e2f-af6f-64fadc2c4be9" Version="1"><Content ObjectRef="{ams_id}" /><ChannelIndex>0</ChannelIndex></SecondaryContent>'
            ))
            new_elements.append(ET.fromstring(
                f'<SecondaryContent ObjectID="{sc1_id}" ClassID="f9d004b5-cb04-4e2f-af6f-64fadc2c4be9" Version="1"><Content ObjectRef="{ams_id}" /><ChannelIndex>1</ChannelIndex></SecondaryContent>'
            ))

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
            new_elements.append(ET.fromstring(
                f'<ClipChannelSerializer ObjectID="{ccs0_id}" ClassID="5c89aa7a-89a6-4483-becd-f2b1def42316" Version="1"><SourceClipIndex>0</SourceClipIndex><mSourceChannelIndex>0</mSourceChannelIndex></ClipChannelSerializer>'
            ))
            new_elements.append(ET.fromstring(
                f'<ClipChannelSerializer ObjectID="{ccs1_id}" ClassID="5c89aa7a-89a6-4483-becd-f2b1def42316" Version="1"><SourceClipIndex>0</SourceClipIndex><mSourceChannelIndex>1</mSourceChannelIndex></ClipChannelSerializer>'
            ))
            new_elements.append(ET.fromstring(
                f'<ClipChannelVectorSerializer ObjectID="{ccv_id}" ClassID="333d203b-3a53-4195-8894-fc7523ff3dc7" Version="1"><ClipChannels Version="1"><ClipChannelItem Index="0" ObjectRef="{ccs0_id}" /><ClipChannelItem Index="1" ObjectRef="{ccs1_id}" /></ClipChannels><ChannelType>1</ChannelType></ClipChannelVectorSerializer>'
            ))
            new_elements.append(ET.fromstring(
                f'<ClipChannelGroupVectorSerializer ObjectID="{ccg_id}" ClassID="a3127a8c-95d4-456e-a7f5-171b3f922426" Version="1"><ClipChannelVectors Version="1"><ClipChannelVectorItem Index="0" ObjectRef="{ccv_id}" /></ClipChannelVectors></ClipChannelGroupVectorSerializer>'
            ))

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
        center_kfs = item.get("center_keyframes") or []
        crop = item.get("crop")
        crop_kfs = item.get("crop_keyframes") or {}
        opacity_kfs = item.get("opacity_keyframes") or []
        static_op = item.get("opacity")
        if item.get("is_mark") and not opacity_kfs and static_op is None:
            static_op = 70.0

        has_custom_motion = (
            abs(s0 - 100.0) > 0.05
            or abs(s1 - 100.0) > 0.05
            or bool(scale_kfs)
            or bool(center_kfs)
            or abs(cx) > 0.001
            or abs(cy) > 0.001
            or bool(crop)
            or bool(crop_kfs)
        )
        has_custom_opacity = bool(opacity_kfs) or (
            static_op is not None and abs(float(static_op) - 100.0) > 0.05
        )

        vcc_id = alloc_oid()
        if not has_custom_motion and not has_custom_opacity:
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

        chain_comp_refs: list[str] = []

        if has_custom_motion:
            pos_x = round(0.5 + float(cx), 6)
            pos_y = round(0.5 + float(cy), 6)
            c_left = float(crop.get("left", 0.0)) if crop else 0.0
            c_top = float(crop.get("top", 0.0)) if crop else 0.0
            c_right = float(crop.get("right", 0.0)) if crop else 0.0
            c_bottom = float(crop.get("bottom", 0.0)) if crop else 0.0

            p_ids = [alloc_oid() for _ in range(11)]
            # Param 0: Position
            pos_kf_tags = ""
            if center_kfs:
                pos_kf_entries = ";".join(
                    f"{min(out_ticks, in_ticks + _sec_to_ticks(rel_s))},{round(0.5 + float(kcx), 6)}:{round(0.5 + float(kcy), 6)},5,0,0,0.33333333333333331,0,0.33333333333333331,5,4,0,0,0,0"
                    for rel_s, (kcx, kcy) in center_kfs
                ) + ";"
                pos_kf_tags = f"<IsTimeVarying>true</IsTimeVarying><Keyframes>{pos_kf_entries}</Keyframes>"
            new_elements.append(ET.fromstring(f"""
            <PointComponentParam ObjectID="{p_ids[0]}" ClassID="ca81d347-309b-44d2-acc7-1c572efb973c" Version="4">
                <Name>Position</Name>
                <ParameterID>1</ParameterID>
                {pos_kf_tags}
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

            # Params 7..10: Intrinsic Motion Crop Left, Top, Right, Bottom
            for idx_c, (c_name, c_pid, c_val, c_key) in enumerate([
                ("Crop Left", 8, c_left, "left"),
                ("Crop Top", 9, c_top, "top"),
                ("Crop Right", 10, c_right, "right"),
                ("Crop Bottom", 11, c_bottom, "bottom"),
            ], start=7):
                side_kfs = crop_kfs.get(c_key) or []
                c_kf_tags = ""
                if side_kfs:
                    c_kf_entries = ";".join(
                        f"{min(out_ticks, in_ticks + _sec_to_ticks(rel_s))},{cv_val},5,0,0,0.33333333333333331,0,0.33333333333333331"
                        for rel_s, cv_val in side_kfs
                    ) + ";"
                    c_kf_tags = f"<IsTimeVarying>true</IsTimeVarying><Keyframes>{c_kf_entries}</Keyframes>"
                new_elements.append(ET.fromstring(f"""
                <VideoComponentParam ObjectID="{p_ids[idx_c]}" ClassID="fe47129e-6c94-4fc0-95d5-c056a517aaf3" Version="10">
                    <Name>{c_name}</Name>
                    <ParameterID>{c_pid}</ParameterID>
                    {c_kf_tags}
                    <StartKeyframe>-91445760000000000,{c_val},0,0,0,0,0,0</StartKeyframe>
                    <CurrentValue>{c_val}</CurrentValue>
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
            chain_comp_refs.append(motion_comp_id)

        if has_custom_opacity:
            op_param_id = alloc_oid()
            op0 = float(opacity_kfs[0][1]) if opacity_kfs else float(static_op if static_op is not None else 50.0)
            op_kf_tags = ""
            if opacity_kfs:
                op_entries = ";".join(
                    f"{min(out_ticks, in_ticks + _sec_to_ticks(rel_s))},{float(val)},5,0,0,0.33333333333333331,0,0.33333333333333331"
                    for rel_s, val in opacity_kfs
                ) + ";"
                op_kf_tags = f"<IsTimeVarying>true</IsTimeVarying><Keyframes>{op_entries}</Keyframes>"
            new_elements.append(ET.fromstring(f"""
            <VideoComponentParam ObjectID="{op_param_id}" ClassID="fe47129e-6c94-4fc0-95d5-c056a517aaf3" Version="10">
                <Name>Opacity</Name>
                <UpperUIBound>100</UpperUIBound>
                <ParameterID>1</ParameterID>
                {op_kf_tags}
                <StartKeyframe>-91445760000000000,{op0},0,0,0,0,0,0</StartKeyframe>
                <CurrentValue>{op0}</CurrentValue>
                <LowerBound>0</LowerBound>
                <UpperBound>100</UpperBound>
            </VideoComponentParam>"""))

            opacity_comp_id = alloc_oid()
            new_elements.append(ET.fromstring(f"""
            <VideoFilterComponent ObjectID="{opacity_comp_id}" ClassID="d10da199-beea-4dd1-b941-ed3a78766d50" Version="9">
                <Component Version="7">
                    <Params Version="1">
                        <Param Index="0" ObjectRef="{op_param_id}" />
                    </Params>
                    <ID>2</ID>
                    <DisplayName>Opacity</DisplayName>
                    <Intrinsic>true</Intrinsic>
                </Component>
                <MatchName>AE.ADBE Opacity</MatchName>
                <VideoFilterType>2</VideoFilterType>
            </VideoFilterComponent>"""))
            chain_comp_refs.append(opacity_comp_id)

        def_motion_tags = (
            "<DefaultMotion>true</DefaultMotion><DefaultMotionComponentID>1</DefaultMotionComponentID>"
            if not has_custom_motion
            else ""
        )
        def_opacity_tags = (
            "<DefaultOpacity>true</DefaultOpacity><DefaultOpacityComponentID>2</DefaultOpacityComponentID>"
            if not has_custom_opacity
            else ""
        )
        active_comp_id = "1" if has_custom_motion else "2"
        comps_xml = "".join(
            f'<Component Index="{ci}" ObjectRef="{cref}" />'
            for ci, cref in enumerate(chain_comp_refs)
        )
        new_elements.append(ET.fromstring(f"""
        <VideoComponentChain ObjectID="{vcc_id}" ClassID="0970e08a-f58f-4108-b29a-1a717b8e12e2" Version="3">
            {def_motion_tags}
            {def_opacity_tags}
            <ComponentChain Version="3">
                <Node Version="1"><Properties Version="1">
                    <MZ.ComponentChain.ActiveComponentID>{active_comp_id}</MZ.ComponentChain.ActiveComponentID>
                    <MZ.ComponentChain.ActiveComponentParamIndex>4294967295</MZ.ComponentChain.ActiveComponentParamIndex>
                </Properties></Node>
                <Components Version="1">
                    {comps_xml}
                </Components>
            </ComponentChain>
        </VideoComponentChain>"""))
        return vcc_id

    def _build_audio_component_chain(gain_db: float) -> str:
        acc_id = alloc_oid()
        gdb = float(gain_db or 0.0)
        if abs(gdb) <= 0.01:
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
            return acc_id

        lin_gain = round(max(0.0001, min(5.623413, 10.0 ** (gdb / 20.0))), 6)
        vol_param_id = alloc_oid()
        mute_param_id = alloc_oid()
        fader_id = alloc_oid()

        new_elements.append(ET.fromstring(f"""
        <AudioComponentParam ObjectID="{vol_param_id}" ClassID="a714635e-a628-4b27-9d59-77eba47dbc1a" Version="10">
            <StartKeyframe>-91445760000000000,{lin_gain},0,0,0,0,0,0</StartKeyframe>
            <CurrentValue>{lin_gain}</CurrentValue>
            <RangeLocked>false</RangeLocked>
            <UnitsString>dB</UnitsString>
            <UpperBound>5.6234130859375</UpperBound>
            <Name>Volume</Name>
        </AudioComponentParam>"""))

        new_elements.append(ET.fromstring(f"""
        <AudioComponentParam ObjectID="{mute_param_id}" ClassID="32657501-3aa4-445f-a49b-d09ecb9fa1ae" Version="10">
            <RangeLocked>false</RangeLocked>
            <Name>Mute</Name>
        </AudioComponentParam>"""))

        new_elements.append(ET.fromstring(f"""
        <AudioFader ObjectID="{fader_id}" ClassID="1a38c583-ed5c-11d5-abc4-c1cbf61ec590" Version="3">
            <AudioComponent Version="3">
                <Component Version="7">
                    <Params Version="1">
                        <Param Index="0" ObjectRef="{vol_param_id}" />
                        <Param Index="1" ObjectRef="{mute_param_id}" />
                    </Params>
                    <ID>1</ID>
                </Component>
                <AudioChannelLayout>[{{"channellabel":100}},{{"channellabel":101}}]</AudioChannelLayout>
                <AudioComponentType>1</AudioComponentType>
                <ChannelType>1</ChannelType>
                <FrameRate>{AUDIO_TICKS_PER_SAMPLE}</FrameRate>
            </AudioComponent>
        </AudioFader>"""))

        new_elements.append(ET.fromstring(f"""
        <AudioComponentChain ObjectID="{acc_id}" ClassID="3cb131d1-d3c0-47ae-a19a-bdf75ea11674" Version="4">
            <DefaultChannelVolumeComponentID>2</DefaultChannelVolumeComponentID>
            <ComponentChain Version="3">
                <Node Version="1"><Properties Version="1">
                    <MZ.ComponentChain.ActiveComponentID>1</MZ.ComponentChain.ActiveComponentID>
                    <MZ.ComponentChain.ActiveComponentParamIndex>4294967295</MZ.ComponentChain.ActiveComponentParamIndex>
                </Properties></Node>
                <Components Version="1">
                    <Component Index="0" ObjectRef="{fader_id}" />
                </Components>
            </ComponentChain>
            <AudioChannelLayout>[{{"channellabel":100}},{{"channellabel":101}}]</AudioChannelLayout>
            <ChannelType>1</ChannelType>
            <FrameRate>{AUDIO_TICKS_PER_SAMPLE}</FrameRate>
        </AudioComponentChain>"""))
        return acc_id

    for t_idx, v_track in enumerate(model["v_tracks"]):
        if t_idx >= len(v_track_els) or not v_track:
            continue
        ti_container = _ensure_track_items_el(v_track_els[t_idx])
        for item_idx, item in enumerate(v_track):
            item_w = int(item.get("clip_w") or width)
            item_h = int(item.get("clip_h") or height)
            reg = register_media(item["path"], item["media_type"], item["media_dur"], item_w, item_h)
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
                <FrameRect>0,0,{item_w},{item_h}</FrameRect>
                <PixelAspectRatio>1,1</PixelAspectRatio>
            </VideoClipTrackItem>"""))
            ti_container.append(ET.fromstring(f'<TrackItem Index="{item_idx}" ObjectRef="{vti_id}" />'))

            if item.get("link_id"):
                link_pairs.setdefault(item["link_id"], {})["v"] = vti_id

    for t_idx, a_track in enumerate(model["a_tracks"]):
        if t_idx >= len(a_track_els) or not a_track:
            continue
        ti_container = _ensure_track_items_el(a_track_els[t_idx])
        for item_idx, item in enumerate(a_track):
            item_w = int(item.get("clip_w") or width)
            item_h = int(item.get("clip_h") or height)
            reg = register_media(item["path"], item["media_type"], item["media_dur"], item_w, item_h)
            start_ticks = _sec_to_ticks(item["t_start"])
            end_ticks = max(start_ticks + fps_ticks, _sec_to_ticks(item["t_end"]))
            in_ticks = _sec_to_ticks(item["src_in"])
            out_ticks = in_ticks + (end_ticks - start_ticks)

            sc0_id, sc1_id = alloc_oid(), alloc_oid()
            ams_ref = reg["ams_id"]
            new_elements.append(ET.fromstring(
                f'<SecondaryContent ObjectID="{sc0_id}" ClassID="f9d004b5-cb04-4e2f-af6f-64fadc2c4be9" Version="1"><Content ObjectRef="{ams_ref}" /><ChannelIndex>0</ChannelIndex></SecondaryContent>'
            ))
            new_elements.append(ET.fromstring(
                f'<SecondaryContent ObjectID="{sc1_id}" ClassID="f9d004b5-cb04-4e2f-af6f-64fadc2c4be9" Version="1"><Content ObjectRef="{ams_ref}" /><ChannelIndex>1</ChannelIndex></SecondaryContent>'
            ))

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

            acc_id = _build_audio_component_chain(float(item.get("gain_db", 0.0) or 0.0))

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
    width: int = 720,
    height: int = 1280,
) -> dict[str, Any]:
    """Export `<stem>.prproj`, `<stem>.premiere.xml`, and optional `<stem>.srt` sidecar to `out_dir`."""
    plan_path = pathlib.Path(plan_path).resolve()
    work_dir = pathlib.Path(work_dir or plan_path.parent).resolve()
    out_dir = pathlib.Path(out_dir or OUTPUT).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    output_name = plan.get("output") or plan.get("output_name") or plan.get("project") or plan_path.parent.name
    raw_stem = pathlib.Path(str(output_name)).stem
    stem = _common.strip_ratio_prefix(raw_stem) if hasattr(_common, "strip_ratio_prefix") else raw_stem
    canvas = plan.get("canvas") or {}
    eff_fps = int(plan.get("fps") or canvas.get("fps") or fps)
    eff_w = int(plan.get("premiere_width") or 720)
    eff_h = int(plan.get("premiere_height") or 1280)
    if width != 1080 or height != 1920:
        eff_w, eff_h = int(width), int(height)
    else:
        clip_lookup = _build_clip_lookup(plan, plan_path)
        for seg in plan.get("segments", []) or []:
            cref = str(seg.get("src") or seg.get("clip") or "")
            cpath = _resolve_clip_path(cref, plan_path, clip_lookup)
            if cpath and cpath.exists():
                info = _common.probe(cpath)
                cw = int(info.get("width") or 0)
                ch = int(info.get("height") or 0)
                if cw > 0 and ch > cw:
                    eff_w, eff_h = cw, ch
                    break

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

    # Copy .srt sidecar alongside <stem>.prproj when present
    srt_candidates: list[pathlib.Path] = []
    if plan.get("output"):
        try:
            out_mp4_res = pathlib.Path(_common.resolve(plan["output"]))
            srt_candidates.append(out_mp4_res.with_suffix(".srt"))
            srt_candidates.append(out_dir / f"{out_mp4_res.stem}.srt")
        except Exception:
            pass
    srt_candidates.extend([
        out_dir / f"reel_{stem}.srt",
        out_dir / f"{raw_stem}.srt",
        work_dir / f"reel_{stem}.srt",
        work_dir / f"{stem}.srt",
        plan_path.with_suffix(".srt"),
    ])
    out_srt: pathlib.Path | None = None
    src_srt = next((c for c in srt_candidates if c.is_file()), None)
    if src_srt is not None:
        dst_srt = out_dir / f"{stem}.srt"
        if src_srt.resolve() != dst_srt.resolve():
            try:
                shutil.copy2(src_srt, dst_srt)
                out_srt = dst_srt
            except Exception:
                out_srt = src_srt
        else:
            out_srt = dst_srt

    result: dict[str, Any] = {
        "prproj": out_prproj,
        "xml": out_xml,
        "v_counts": [len(t) for t in model["v_tracks"]],
        "a_counts": [len(t) for t in model["a_tracks"]],
    }
    if out_srt is not None:
        result["srt"] = out_srt
    return result


def main() -> None:
    ap = argparse.ArgumentParser(description="Export plan.edl / plan.json to Adobe Premiere Pro (.prproj + .premiere.xml)")
    ap.add_argument("plan", help="Path to work/<project>/plan.edl or plan.json")
    ap.add_argument("--out-dir", default=None, help="Output directory (defaults to OUTPUT)")
    ap.add_argument("--fps", type=int, default=30)
    ap.add_argument("--width", type=int, default=720)
    ap.add_argument("--height", type=int, default=1280)
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
