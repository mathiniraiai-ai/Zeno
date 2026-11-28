"""Render an edit plan (JSON or EDL) into the final broadcast-ready MP4 video.

Usage:
    python3 scripts/render.py work/<project>/plan.edl [--draft] [--verify] [--square]
        [--preset auto|single|split_v|split_h|chat_bar|video_call|hybrid]
        [--mark-pack default|glossy|flash|vignette]
        [--deliver] [--name <FolderName>]
"""
from __future__ import annotations

import argparse
from array import array
import contextlib
import hashlib
import inspect
import io
import json
import math
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common  # noqa: E402


def _fallback_normalize_mark_type(mark_type: str) -> str:
    t = str(mark_type or "right").strip().lower()
    if t in ("right", "correct", "check", "tick", "green", "ok", "true", "yes"):
        return "right"
    if t in ("wrong", "error", "cross", "x", "red", "false", "no", "incorrect"):
        return "wrong"
    return t


ROOT: Path = getattr(common, "ROOT", Path(__file__).resolve().parent.parent)
WORK: Path = getattr(common, "WORK", ROOT / "work")
OUTPUT: Path = getattr(
    common,
    "OUTPUT",
    Path(os.environ.get("ZENO_OUTPUT_DIR", "").strip() or (Path.home() / "Downloads" / "Zeno Outs")).expanduser(),
)
FFMPEG: Path = common.FFMPEG
MARKS_DIR: Path = getattr(common, "MARKS_DIR", ROOT / "assets" / "marks")
SFX_DIR: Path = getattr(common, "SFX_DIR", ROOT / "assets" / "sfx")
DEFAULT_LOUDNESS_LUFS: float = float(getattr(common, "DEFAULT_LOUDNESS_LUFS", -10.5))
DEFAULT_PEAK_CAP_DB: float = float(getattr(common, "DEFAULT_PEAK_CAP_DB", -1.0))
DEFAULT_SFX: Dict[str, str] = getattr(common, "DEFAULT_SFX", {"right": "correct_ding.wav", "wrong": "sn_wrong.wav"})

clip_work_dir = common.clip_work_dir
_normalize_mark_type = getattr(
    common,
    "_normalize_mark_type",
    getattr(common, "normalize_mark_type", _fallback_normalize_mark_type),
)
ffmpeg = common.ffmpeg
ffmpeg_encode = common.ffmpeg_encode
final_bitrate_kbps = common.final_bitrate_kbps
probe = common.probe
read_json = common.read_json
resolve = common.resolve
resolve_mark_asset = common.resolve_mark_asset
resolve_mark_sfx = common.resolve_mark_sfx
resolve_sfx_asset = common.resolve_sfx_asset
square_job_output = common.square_job_output
strip_ratio_prefix = common.strip_ratio_prefix
tc = common.tc
write_json = common.write_json
thermal_pause = getattr(common, "thermal_pause", lambda *a, **kw: None)
job_done = getattr(common, "job_done", lambda: None)


def segment_hold_tail(seg: Dict[str, Any]) -> float:
    """Return clamped post-word freeze-hold tail seconds (`common.segment_hold_tail` with safe fallback)."""
    fn = getattr(common, "segment_hold_tail", None)
    if callable(fn):
        return float(fn(seg))
    try:
        return max(0.0, min(1.5, float(seg.get("hold_tail") or 0.0)))
    except (TypeError, ValueError):
        return 0.0


def _segment_speed(seg: Dict[str, Any]) -> float:
    """Return clamped playback speed multiplier (`common.segment_speed` with safe fallback)."""
    fn = getattr(common, "segment_speed", None)
    if callable(fn):
        return float(fn(seg))
    try:
        val = float(seg.get("speed") or 1.0)
    except (TypeError, ValueError):
        return 1.0
    if not math.isfinite(val) or val <= 0.0:
        return 1.0
    return max(0.5, min(2.0, val))


def segment_duration(seg: Dict[str, Any]) -> float:
    """Return total segment duration including `speed` and `hold_tail` (`common.segment_duration` with safe fallback)."""
    fn = getattr(common, "segment_duration", None)
    if callable(fn):
        return float(fn(seg))
    raw = max(0.0, float(seg.get("end", 0.0)) - float(seg.get("start", 0.0)))
    return max(0.08, raw / _segment_speed(seg) + segment_hold_tail(seg))


_seg_duration = segment_duration

try:
    import edl_parser
except Exception:
    edl_parser = None  # type: ignore[assignment]

try:
    import layouts
except Exception:
    layouts = None  # type: ignore[assignment]

try:
    import verify
except Exception:
    verify = None  # type: ignore[assignment]

try:
    import style_config
    _TUNING = style_config.get_tuning()
except Exception:
    style_config = None  # type: ignore[assignment]
    _TUNING = {}

EDGE_FADE = 0.012  # tiny audio fade at every cut to avoid clicks
TRANSITIONS_DIR = ROOT / "assets" / "transitions"
_SPLIT_LAYOUTS = {"split_h", "split_v", "left_right", "top_bottom"}
_SFX_STATS_CACHE: Dict[str, Dict[str, Any]] = {}


def _redirect_broken_sfx(fpath: Path) -> Path:
    """Redirect broken riser.wav (0.70s digital silence + 40ms tick) -> sn_riser_1.wav."""
    p = Path(fpath)
    if p.name.lower() in ("riser", "riser.wav"):
        cand = SFX_DIR / "sn_riser_1.wav"
        if cand.is_file():
            return cand
    return p


def _sfx_audio_stats(fpath: Path) -> Dict[str, Any]:
    """Measure raw stereo/dual-mono peak dBFS, input channels, duration, and 20ms RMS envelope `peak_sec`."""
    p = _redirect_broken_sfx(Path(fpath))
    try:
        st = p.stat()
        cache_key = f"{p.resolve()}:{st.st_size}:{st.st_mtime_ns}"
    except OSError:
        cache_key = str(p)
    if cache_key in _SFX_STATS_CACHE:
        return _SFX_STATS_CACHE[cache_key]

    ch = 2
    dur = 0.35
    raw_peak_db = -1.0
    peak_sec = 0.10
    try:
        r_info = subprocess.run(
            [str(FFMPEG), "-hide_banner", "-i", str(p)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=15,
        )
        aline = next((ln for ln in r_info.stderr.splitlines() if "Audio:" in ln), "")
        if ", mono" in aline or ", 1 channels" in aline:
            ch = 1
        pan_f = "pan=stereo|c0=c0|c1=c0" if ch == 1 else "aformat=channel_layouts=stereo"
        r_pcm = subprocess.run(
            [
                str(FFMPEG),
                "-hide_banner",
                "-v",
                "error",
                "-i",
                str(p),
                "-af",
                f"aresample=48000,aformat=sample_fmts=fltp:sample_rates=48000,{pan_f}",
                "-f",
                "f32le",
                "-acodec",
                "pcm_f32le",
                "-",
            ],
            capture_output=True,
            timeout=20,
        )
        n_floats = len(r_pcm.stdout) // 4
        n_frames = n_floats // 2
        if n_frames > 0:
            dur = round(n_frames / 48000.0, 4)
            try:
                import numpy as np

                arr = np.frombuffer(r_pcm.stdout[: n_frames * 8], dtype=np.float32).reshape(-1, 2)
                pk = float(np.max(np.abs(arr)))
                raw_peak_db = round(20.0 * math.log10(max(pk, 1e-6)), 2)
                mono_sq = np.mean(arr * arr, axis=1)
                win = min(960, len(mono_sq))
                if win >= 8:
                    cs = np.concatenate([[0.0], np.cumsum(mono_sq)])
                    w_sum = cs[win:] - cs[:-win]
                    best_i = int(np.argmax(w_sum))
                    peak_sec = round((best_i + win * 0.5) / 48000.0, 4)
                else:
                    peak_sec = round(dur * 0.5, 4)
            except Exception:
                samples = array("f")
                samples.frombytes(r_pcm.stdout[: n_frames * 8])
                pk = max((abs(v) for v in samples), default=1e-6)
                raw_peak_db = round(20.0 * math.log10(max(pk, 1e-6)), 2)
                step = 480  # 10ms hop
                best_e = -1.0
                best_frame = 0
                for f0 in range(0, max(1, n_frames - step), step):
                    idx0 = f0 * 2
                    idx1 = min(len(samples), (f0 + 960) * 2)
                    seg_s = samples[idx0:idx1]
                    e = sum(v * v for v in seg_s) / max(1, len(seg_s))
                    if e > best_e:
                        best_e = e
                        best_frame = f0 + (idx1 - idx0) // 4
                peak_sec = round(best_frame / 48000.0, 4)
    except Exception:
        pass

    norm_db = round(max(-6.0, min(14.0, -1.0 - raw_peak_db)), 2)
    stats = {
        "max_volume_db": raw_peak_db,
        "raw_peak_db": raw_peak_db,
        "norm_db": norm_db,
        "channels": ch,
        "duration": dur,
        "peak_sec": peak_sec,
    }
    _SFX_STATS_CACHE[cache_key] = stats
    return stats


def _mark_sfx_preroll(sfx_path: Path) -> float:
    """Clamped attack pre-roll for mark SFX so leading silence is trimmed and the audible punch lands on the beat."""
    pk = float(_sfx_audio_stats(sfx_path).get("peak_sec", 0.0) or 0.0)
    if pk <= 0.04:
        return 0.0
    return min(0.12, max(0.0, pk * 0.50))


_STANDARD_FPS = (24, 25, 30, 50, 60)


def _snap_standard_fps(raw_fps: float) -> int:
    """Snap a probed clip frame rate to the nearest standard CFR rate (24/25/30/50/60) when within 1.5 fps."""
    val = float(raw_fps or 0.0)
    if val <= 0.0:
        return 0
    nearest = min(_STANDARD_FPS, key=lambda std: abs(val - std))
    if abs(val - nearest) <= 1.5:
        return int(nearest)
    return max(1, int(round(val)))


def _detect_modal_fps(
    segs: List[Dict[str, Any]],
    first_info: Optional[Dict[str, Any]] = None,
) -> int:
    """Pick the modal (most frequent) rounded standard FPS across all segments, tie-breaking toward higher standard FPS."""
    counts: Dict[int, int] = {}
    probe_cache: Dict[str, int] = {}
    if segs and first_info:
        first_src_key = str(segs[0].get("src") or "")
        if first_src_key:
            first_snapped = _snap_standard_fps(float(first_info.get("fps") or 0.0))
            if first_snapped > 0:
                probe_cache[first_src_key] = first_snapped
    for s in segs:
        raw_src = str(s.get("src") or "")
        if not raw_src:
            continue
        if raw_src not in probe_cache:
            src_p = resolve(raw_src)
            info: Optional[Dict[str, Any]] = None
            if src_p.is_file():
                try:
                    info = probe(src_p)
                except Exception:
                    info = None
            else:
                info_cache = clip_work_dir(raw_src) / "info.json"
                if info_cache.is_file():
                    try:
                        info = read_json(info_cache)
                    except Exception:
                        info = None
            probe_cache[raw_src] = _snap_standard_fps(float((info or {}).get("fps") or 0.0))
        seg_fps = probe_cache[raw_src]
        if seg_fps > 0:
            counts[seg_fps] = counts.get(seg_fps, 0) + 1
    if not counts:
        fb = float((first_info or {}).get("fps") or 24)
        return _snap_standard_fps(fb) or 24
    return max(
        counts.keys(),
        key=lambda f: (counts[f], 1 if f in _STANDARD_FPS else 0, f),
    )


def _calibrate_sfx_gain_db(
    sfx_path: Path,
    raw_gain_db: Optional[float],
    category: Optional[str] = None,
    default_gain_db: Optional[float] = None,
) -> float:
    """Enforce category defaults, gain floors, and safe ceilings after -1.0 dBFS peak calibration."""
    nm = Path(sfx_path).name.lower()
    cat = (category or "").strip().lower()
    if cat == "mark":
        def_db = default_gain_db if default_gain_db is not None else 0.5
        floor_db, ceil_db = -7.0, 3.0
        if raw_gain_db is None or float(raw_gain_db) <= -9.0:
            return round(max(floor_db, min(ceil_db, float(def_db))), 2)
        return round(max(floor_db, min(ceil_db, float(raw_gain_db))), 2)

    if cat == "transition" or any(k in nm for k in ("whoosh", "swish", "swoosh", "transition")):
        def_db = default_gain_db if default_gain_db is not None else -0.5
        floor_db, ceil_db = -3.5, 2.5
        if raw_gain_db is None:
            return round(max(floor_db, min(ceil_db, float(def_db))), 2)
        g = float(raw_gain_db)
        if g <= -6.0:
            g = -1.5
        return round(max(floor_db, min(ceil_db, g)), 2)

    if "riser" in nm:
        def_db = default_gain_db if default_gain_db is not None else -2.0
        floor_db, ceil_db = -4.0, 2.0
        g = float(def_db if raw_gain_db is None else raw_gain_db)
        if g <= -8.0:
            g = -2.5
        return round(max(floor_db, min(ceil_db, g)), 2)

    if any(k in nm for k in ("wrong", "buzzer")):
        def_db = default_gain_db if default_gain_db is not None else -4.5
        floor_db, ceil_db = -7.0, 2.5
        g = float(def_db if raw_gain_db is None else raw_gain_db)
        if g <= -9.0:
            g = -4.5
        return round(max(floor_db, min(ceil_db, g)), 2)

    if any(k in nm for k in ("punch", "right", "correct", "ding")):
        def_db = default_gain_db if default_gain_db is not None else -1.5
        floor_db, ceil_db = -3.5, 2.5
        g = float(def_db if raw_gain_db is None else raw_gain_db)
        if g <= -8.0:
            g = -2.0
        return round(max(floor_db, min(ceil_db, g)), 2)

    if "pop" in nm:
        def_db = default_gain_db if default_gain_db is not None else -2.0
        floor_db, ceil_db = -4.5, 2.0
        g = float(def_db if raw_gain_db is None else raw_gain_db)
        if g <= -8.0:
            g = -3.0
        return round(max(floor_db, min(ceil_db, g)), 2)

    if "click" in nm:
        def_db = default_gain_db if default_gain_db is not None else -3.0
        floor_db, ceil_db = -4.5, 1.5
        g = float(def_db if raw_gain_db is None else raw_gain_db)
        if g <= -8.0:
            g = -3.5
        return round(max(floor_db, min(ceil_db, g)), 2)

    def_db = default_gain_db if default_gain_db is not None else -2.0
    g = float(def_db if raw_gain_db is None else raw_gain_db)
    return round(max(-4.5, min(3.0, g)), 2)


def load_overlay_transition(name: str) -> Optional[Dict[str, Any]]:
    """Load measured screen-blend flash envelope from assets/transitions/<name>.json."""
    path = TRANSITIONS_DIR / f"{name}.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None


def flash_filters(
    spec: Dict[str, Any],
    join_t: float,
    swap_frame: int,
    canvas_fps: Optional[float] = None,
) -> List[str]:
    """Screen-blend flash around `join_t` using exact integer frame indices `between(n,f,f)` so 0 frames drop at 24/25/30/60fps."""
    frames_spec = spec.get("frames") or []
    if not frames_spec:
        return []
    spec_fps = max(1.0, float(spec.get("fps", 24) or 24))
    eff_fps = max(1.0, float(canvas_fps or spec_fps))
    n_spec = len(frames_spec)
    total_frames = max(n_spec, int(round(n_spec * eff_fps / spec_fps)))
    eff_swap = int(round(swap_frame * eff_fps / spec_fps))
    join_frame = int(round(join_t * eff_fps))
    out: List[str] = []
    for m in range(total_frames):
        f_idx = join_frame + (m - eff_swap)
        if f_idx < 0:
            continue
        src_k = min(n_spec - 1, int(m * n_spec / max(1, total_frames)))
        r, g, b = frames_spec[src_k]["screen_rgb"]
        expr = lambda c: f"val+(255-val)*{c}/255"  # noqa: E731
        out.append(f"lutrgb=r='{expr(r)}':g='{expr(g)}':b='{expr(b)}':enable='between(n,{f_idx},{f_idx})'")
    return out


_META_TRANSITIONS: Dict[str, Dict[str, Any]] = {
    "whip_left": {"kind": "whip_left", "default_dur": 0.34},
    "smoothleft": {"kind": "whip_left", "default_dur": 0.34},
    "whip_right": {"kind": "whip_right", "default_dur": 0.34},
    "smoothright": {"kind": "whip_right", "default_dur": 0.34},
    "slide_up": {"kind": "slide_up", "default_dur": 0.36},
    "whip_up": {"kind": "slide_up", "default_dur": 0.36},
    "smoothup": {"kind": "slide_up", "default_dur": 0.36},
    "slide_down": {"kind": "slide_down", "default_dur": 0.36},
    "whip_down": {"kind": "slide_down", "default_dur": 0.36},
    "smoothdown": {"kind": "slide_down", "default_dur": 0.36},
    "zoom_burst": {"kind": "zoom_burst", "default_dur": 0.36},
    "zoom_in": {"kind": "zoom_in", "default_dur": 0.30},
    "zoom_out": {"kind": "zoom_out", "default_dur": 0.32},
    "zoom_blur": {"kind": "zoom_blur", "default_dur": 0.36},
    "flash_zoom": {
        "kind": "flash_zoom",
        "default_dur": 0.36,
        "post": lambda jt, fps=30: flash_filters(
            {
                "fps": 24,
                "frames": [
                    {"screen_rgb": [42, 50, 62]},
                    {"screen_rgb": [105, 118, 138]},
                    {"screen_rgb": [175, 190, 212]},
                    {"screen_rgb": [100, 112, 132]},
                    {"screen_rgb": [36, 44, 56]},
                ],
            },
            jt,
            swap_frame=2,
            canvas_fps=fps,
        ),
    },
    "split_reveal": {"kind": "split_reveal", "default_dur": 0.40},
    "warm_flash_punch": {"kind": "warm_flash_punch", "default_dur": 0.34},
    "dip_to_black": {"kind": "dip_to_black", "default_dur": 0.30},
    "dip_to_white": {"kind": "dip_to_white", "default_dur": 0.30},
    "fade": {"kind": "fade", "default_dur": 0.30},
    "dissolve": {"kind": "fade", "default_dur": 0.30},
}

_WHOOSH_TRANSITIONS = {
    "whip_left", "whip_right", "whip_up", "whip_down",
    "slide_up", "slide_down", "smoothleft", "smoothright", "smoothup", "smoothdown",
    "flash_zoom", "zoom_burst", "zoom_in", "zoom_out", "zoom_blur",
    "split_reveal", "warm_flash", "warm_flash_punch",
}
_whoosh_trs = _WHOOSH_TRANSITIONS


def even(x: float) -> int:
    return int(round(x / 2)) * 2


def _build_physical_transition_fc(
    tr_name: str,
    idx: int,
    W: int,
    H: int,
    fps: int,
    n_tr: int,
    prev_lay: str = "single",
    cur_lay: str = "single",
) -> str:
    """Build smooth S-curve physical transition filtergraph mapping `[oa{idx}]` + `[ib{idx}]` -> `[x{idx}]` (`n_tr` frames)."""
    meta = _META_TRANSITIONS.get(tr_name)
    kind = meta["kind"] if meta else tr_name
    oa = f"[oa{idx}]"
    ib = f"[ib{idx}]"
    out = f"[x{idx}]"

    # Smooth S-curve progress P in (0, 1) across frames n = 0 .. n_tr - 1 (zero double-still stutter at entry/exit)
    S = f"0.5*(1-cos(PI*(n+1)/{n_tr + 1}))"
    SB = f"0.5*(1-cos(PI*(N+1)/{n_tr + 1}))"
    E = f"(1-pow(1-(n+1)/{n_tr + 1},3))"
    s0 = max(0, int(round(0.12 * n_tr)))
    s1 = max(s0, min(n_tr - 1, int(round(0.88 * n_tr)) - 1))
    c0 = max(0, int(round(0.28 * n_tr)))
    c1 = max(c0, min(n_tr - 1, int(round(0.72 * n_tr)) - 1))

    if kind == "whip_left":
        r_sh = max(14, int(round(W * 0.032)))
        r_pk = max(24, int(round(W * 0.058)))
        return (
            f"color=c=black:s={W}x{H}:r={fps},trim=start_frame=0:end_frame={n_tr},setpts=N/({fps}*TB),format=yuv420p[wb_{idx}];"
            f"[wb_{idx}]{oa}overlay=x='round(-{W}*({S}))':y=0[w1_{idx}];"
            f"[w1_{idx}]{ib}overlay=x='round({W}-{W}*({S}))':y=0,"
            f"dblur=angle=0:radius={r_sh}:enable='between(n,{s0},{s1})',"
            f"dblur=angle=0:radius={r_pk}:enable='between(n,{c0},{c1})',"
            f"eq=brightness='0.045*sin(PI*(n+1)/{n_tr + 1})':saturation='1+0.10*sin(PI*(n+1)/{n_tr + 1})':eval=frame,"
            f"colorbalance=rs=0.05:gs=0.02:bs=-0.03:enable='between(n,{c0},{c1})',"
            f"setpts=N/({fps}*TB),format=yuv420p{out}"
        )

    if kind == "whip_right":
        r_sh = max(14, int(round(W * 0.032)))
        r_pk = max(24, int(round(W * 0.058)))
        return (
            f"color=c=black:s={W}x{H}:r={fps},trim=start_frame=0:end_frame={n_tr},setpts=N/({fps}*TB),format=yuv420p[wb_{idx}];"
            f"[wb_{idx}]{oa}overlay=x='round({W}*({S}))':y=0[w1_{idx}];"
            f"[w1_{idx}]{ib}overlay=x='round(-{W}+{W}*({S}))':y=0,"
            f"dblur=angle=0:radius={r_sh}:enable='between(n,{s0},{s1})',"
            f"dblur=angle=0:radius={r_pk}:enable='between(n,{c0},{c1})',"
            f"eq=brightness='0.045*sin(PI*(n+1)/{n_tr + 1})':saturation='1+0.10*sin(PI*(n+1)/{n_tr + 1})':eval=frame,"
            f"colorbalance=rs=0.05:gs=0.02:bs=-0.03:enable='between(n,{c0},{c1})',"
            f"setpts=N/({fps}*TB),format=yuv420p{out}"
        )

    if kind == "slide_up":
        r_sh = max(14, int(round(H * 0.018)))
        r_pk = max(26, int(round(H * 0.034)))
        return (
            f"color=c=black:s={W}x{H}:r={fps},trim=start_frame=0:end_frame={n_tr},setpts=N/({fps}*TB),format=yuv420p[sb_{idx}];"
            f"[sb_{idx}]{oa}overlay=x=0:y='round(-{H}*({S}))'[s1_{idx}];"
            f"[s1_{idx}]{ib}overlay=x=0:y='round({H}-{H}*({S}))',"
            f"dblur=angle=90:radius={r_sh}:enable='between(n,{s0},{s1})',"
            f"dblur=angle=90:radius={r_pk}:enable='between(n,{c0},{c1})',"
            f"eq=brightness='0.04*sin(PI*(n+1)/{n_tr + 1})':saturation='1+0.08*sin(PI*(n+1)/{n_tr + 1})':eval=frame,"
            f"setpts=N/({fps}*TB),format=yuv420p{out}"
        )

    if kind == "slide_down":
        r_sh = max(14, int(round(H * 0.018)))
        r_pk = max(26, int(round(H * 0.034)))
        return (
            f"color=c=black:s={W}x{H}:r={fps},trim=start_frame=0:end_frame={n_tr},setpts=N/({fps}*TB),format=yuv420p[sb_{idx}];"
            f"[sb_{idx}]{oa}overlay=x=0:y='round({H}*({S}))'[s1_{idx}];"
            f"[s1_{idx}]{ib}overlay=x=0:y='round(-{H}+{H}*({S}))',"
            f"dblur=angle=90:radius={r_sh}:enable='between(n,{s0},{s1})',"
            f"dblur=angle=90:radius={r_pk}:enable='between(n,{c0},{c1})',"
            f"eq=brightness='0.04*sin(PI*(n+1)/{n_tr + 1})':saturation='1+0.08*sin(PI*(n+1)/{n_tr + 1})':eval=frame,"
            f"setpts=N/({fps}*TB),format=yuv420p{out}"
        )

    W_ss = W * 2
    H_ss = H * 2

    def _ss2x_zoom(z_expr: str) -> str:
        """2x supersampled dynamic zoom in yuv444p + Lanczos downscale (0.5px sub-pixel steps, zero black borders)."""
        return (
            f"format=yuv444p,"
            f"scale=w='max({W_ss},round({W_ss}*({z_expr})/2)*2)':h='max({H_ss},round({H_ss}*({z_expr})/2)*2)':eval=frame:flags=bicubic,"
            f"crop=w={W_ss}:h={H_ss}:x='(iw-{W_ss})/2':y='(ih-{H_ss})/2',"
            f"scale=w={W}:h={H}:flags=lanczos,setsar=1,format=yuv420p"
        )

    if kind in ("zoom_burst", "flash_zoom"):
        z_a = f"1+0.35*({S})"
        z_b = f"1.18-0.18*({S})"
        r_diag = max(12, int(round(min(W, H) * 0.018)))
        br_pop = "0.10" if kind == "flash_zoom" else "0.06"
        return (
            f"{oa}{_ss2x_zoom(z_a)},setpts=N/({fps}*TB)[za{idx}];"
            f"{ib}{_ss2x_zoom(z_b)},setpts=N/({fps}*TB)[zb{idx}];"
            f"[za{idx}][zb{idx}]blend=all_expr='A*(1-({SB}))+B*({SB})',"
            f"dblur=angle=45:radius={r_diag}:enable='between(n,{c0},{c1})',"
            f"dblur=angle=135:radius={r_diag}:enable='between(n,{c0},{c1})',"
            f"rgbashift=rh=-5:bh=5:rv=-3:bv=3:edge=smear:enable='between(n,{c0},{c1})',"
            f"eq=brightness='{br_pop}*pow(sin(PI*(n+1)/{n_tr + 1}),2)':saturation='1+0.10*sin(PI*(n+1)/{n_tr + 1})':eval=frame,"
            f"setpts=N/({fps}*TB),format=yuv420p{out}"
        )

    if kind == "zoom_in":
        z_a = f"1+0.24*({S})"
        z_b = f"1.12-0.12*({S})"
        r_soft = max(4, int(round(min(W, H) * 0.008)))
        r_avg = max(2, int(round(min(W, H) * 0.004)))
        return (
            f"{oa}{_ss2x_zoom(z_a)},"
            f"dblur=angle=45:radius={r_soft}:enable='between(n,{s0},{s1})',"
            f"avgblur=sizeX={r_avg}:sizeY={r_avg}:enable='between(n,{c0},{c1})',"
            f"setpts=N/({fps}*TB),format=yuv420p[za{idx}];"
            f"{ib}{_ss2x_zoom(z_b)},setpts=N/({fps}*TB)[zb{idx}];"
            f"[za{idx}][zb{idx}]blend=all_expr='A*(1-({SB}))+B*({SB})',"
            f"eq=brightness='0.03*sin(PI*(n+1)/{n_tr + 1})':eval=frame,"
            f"setpts=N/({fps}*TB),format=yuv420p{out}"
        )

    if kind == "zoom_out":
        z_a = f"1.0-0.16*({S})"
        z_b = f"1.24-0.24*({S})"
        return (
            f"{ib}format=yuv444p,"
            f"scale=w='max({W_ss},round({W_ss}*({z_b})/2)*2)':h='max({H_ss},round({H_ss}*({z_b})/2)*2)':eval=frame:flags=bicubic,"
            f"crop=w={W_ss}:h={H_ss}:x='(iw-{W_ss})/2':y='(ih-{H_ss})/2',setsar=1,setpts=N/({fps}*TB)[zb{idx}];"
            f"{oa}format=yuv444p,"
            f"scale=w='min({W_ss},max(2,round({W_ss}*({z_a})/2)*2))':h='min({H_ss},max(2,round({H_ss}*({z_a})/2)*2))':eval=frame:flags=bicubic,"
            f"format=yuva444p,fade=t=out:s=0:n={n_tr}:alpha=1,setsar=1,setpts=N/({fps}*TB)[za{idx}];"
            f"[zb{idx}][za{idx}]overlay=x='(W-w)/2':y='(H-h)/2':eval=frame:shortest=1:format=yuv444,"
            f"scale=w={W}:h={H}:flags=lanczos,setsar=1,"
            f"eq=brightness='0.025*sin(PI*(n+1)/{n_tr + 1})':eval=frame,"
            f"setpts=N/({fps}*TB),format=yuv420p{out}"
        )

    if kind == "zoom_blur":
        z_a = f"1+0.32*({S})"
        z_b = f"1.26-0.26*({S})"
        r_sh = max(8, int(round(min(W, H) * 0.012)))
        r_pk = max(16, int(round(min(W, H) * 0.024)))
        return (
            f"{oa}{_ss2x_zoom(z_a)},"
            f"dblur=angle=45:radius={r_sh}:enable='between(n,{s0},{n_tr - 1})',"
            f"dblur=angle=135:radius={r_pk}:enable='between(n,{c0},{n_tr - 1})',"
            f"setpts=N/({fps}*TB),format=yuv420p[za{idx}];"
            f"{ib}{_ss2x_zoom(z_b)},"
            f"dblur=angle=45:radius={r_pk}:enable='between(n,0,{c1})',"
            f"dblur=angle=135:radius={r_sh}:enable='between(n,0,{s1})',"
            f"setpts=N/({fps}*TB),format=yuv420p[zb{idx}];"
            f"[za{idx}][zb{idx}]blend=all_expr='A*(1-({SB}))+B*({SB})',"
            f"eq=brightness='0.05*pow(sin(PI*(n+1)/{n_tr + 1}),2)':saturation='1+0.08*sin(PI*(n+1)/{n_tr + 1})':eval=frame,"
            f"setpts=N/({fps}*TB),format=yuv420p{out}"
        )

    if kind == "split_reveal":
        entering_split = cur_lay in _SPLIT_LAYOUTS and prev_lay not in _SPLIT_LAYOUTS
        if entering_split:
            if cur_lay in ("split_v", "top_bottom"):
                H2 = even(H // 2)
                HB = H - H2
                r_v = max(12, int(round(H * 0.015)))
                return (
                    f"{ib}split=2[bT_in{idx}][bB_in{idx}];"
                    f"[bT_in{idx}]crop=w={W}:h={H2}:x=0:y=0[bT{idx}];"
                    f"[bB_in{idx}]crop=w={W}:h={HB}:x=0:y={H2}[bB{idx}];"
                    f"{oa}eq=brightness='-0.16*({E})':eval=frame[bg{idx}];"
                    f"[bg{idx}][bT{idx}]overlay=x=0:y='round(-{H2}+{H2}*({E}))'[sr1_{idx}];"
                    f"[sr1_{idx}][bB{idx}]overlay=x=0:y='round({H}-{HB}*({E}))',"
                    f"dblur=angle=90:radius={r_v}:enable='between(n,0,{max(1, n_tr // 2)})',"
                    f"setpts=N/({fps}*TB),format=yuv420p{out}"
                )
            W2 = even(W // 2)
            WR = W - W2
            r_h = max(12, int(round(W * 0.022)))
            return (
                f"{ib}split=2[bL_in{idx}][bR_in{idx}];"
                f"[bL_in{idx}]crop=w={W2}:h={H}:x=0:y=0[bL{idx}];"
                f"[bR_in{idx}]crop=w={WR}:h={H}:x={W2}:y=0[bR{idx}];"
                f"{oa}eq=brightness='-0.16*({E})':eval=frame[bg{idx}];"
                f"[bg{idx}][bL{idx}]overlay=x='round(-{W2}+{W2}*({E}))':y=0[sr1_{idx}];"
                f"[sr1_{idx}][bR{idx}]overlay=x='round({W}-{WR}*({E}))':y=0,"
                f"dblur=angle=0:radius={r_h}:enable='between(n,0,{max(1, n_tr // 2)})',"
                f"setpts=N/({fps}*TB),format=yuv420p{out}"
            )
        # Exiting split screen (split_h/split_v -> single) or standalone opposing-panel curtain reveal
        z_in = f"1.06-0.06*({E})"
        if prev_lay in ("split_v", "top_bottom"):
            H2 = even(H // 2)
            HB = H - H2
            r_v = max(12, int(round(H * 0.015)))
            return (
                f"{oa}split=2[aT_in{idx}][aB_in{idx}];"
                f"[aT_in{idx}]crop=w={W}:h={H2}:x=0:y=0[aT{idx}];"
                f"[aB_in{idx}]crop=w={W}:h={HB}:x=0:y={H2}[aB{idx}];"
                f"{ib}{_ss2x_zoom(z_in)},"
                f"eq=brightness='-0.12*(1-({E}))':eval=frame,setpts=N/({fps}*TB),format=yuv420p[bg{idx}];"
                f"[bg{idx}][aT{idx}]overlay=x=0:y='round(-{H2}*({S}))'[sr1_{idx}];"
                f"[sr1_{idx}][aB{idx}]overlay=x=0:y='round({H2}+{HB}*({S}))',"
                f"dblur=angle=90:radius={r_v}:enable='between(n,{c0},{c1})',"
                f"setpts=N/({fps}*TB),format=yuv420p{out}"
            )
        W2 = even(W // 2)
        WR = W - W2
        r_h = max(12, int(round(W * 0.022)))
        return (
            f"{oa}split=2[aL_in{idx}][aR_in{idx}];"
            f"[aL_in{idx}]crop=w={W2}:h={H}:x=0:y=0[aL{idx}];"
            f"[aR_in{idx}]crop=w={WR}:h={H}:x={W2}:y=0[aR{idx}];"
            f"{ib}{_ss2x_zoom(z_in)},"
            f"eq=brightness='-0.12*(1-({E}))':eval=frame,setpts=N/({fps}*TB),format=yuv420p[bg{idx}];"
            f"[bg{idx}][aL{idx}]overlay=x='round(-{W2}*({S}))':y=0[sr1_{idx}];"
            f"[sr1_{idx}][aR{idx}]overlay=x='round({W2}+{WR}*({S}))':y=0,"
            f"dblur=angle=0:radius={r_h}:enable='between(n,{c0},{c1})',"
            f"setpts=N/({fps}*TB),format=yuv420p{out}"
        )

    if kind == "warm_flash_punch":
        z_a = f"1+0.06*({S})"
        z_b = f"1.06-0.06*({S})"
        return (
            f"{oa}{_ss2x_zoom(z_a)},setpts=N/({fps}*TB)[za{idx}];"
            f"{ib}{_ss2x_zoom(z_b)},setpts=N/({fps}*TB)[zb{idx}];"
            f"[za{idx}][zb{idx}]blend=all_expr='A*(1-({SB}))+B*({SB})',setpts=N/({fps}*TB),format=yuv420p{out}"
        )

    if kind == "fade":
        return f"{oa}{ib}blend=all_expr='A*(1-({SB}))+B*({SB})',setpts=N/({fps}*TB),format=yuv420p{out}"

    if kind in ("dip_to_black", "dip_to_white"):
        xf_name = "fadeblack" if kind == "dip_to_black" else "fadewhite"
        return (
            f"{oa}tpad=stop_mode=clone:stop=2,fps={fps}[oa_xf{idx}];"
            f"{ib}tpad=stop_mode=clone:stop=2,fps={fps}[ib_xf{idx}];"
            f"[oa_xf{idx}][ib_xf{idx}]xfade=transition={xf_name}:duration={n_tr / float(fps):.6f}:offset=0,"
            f"trim=start_frame=0:end_frame={n_tr},setpts=N/({fps}*TB),format=yuv420p{out}"
        )

    # Generic CFR-safe fallback for any other FFmpeg xfade transition name
    return (
        f"{oa}tpad=stop_mode=clone:stop=2,fps={fps}[oa_xf{idx}];"
        f"{ib}tpad=stop_mode=clone:stop=2,fps={fps}[ib_xf{idx}];"
        f"[oa_xf{idx}][ib_xf{idx}]xfade=transition={kind}:duration={n_tr / float(fps):.6f}:offset=0,"
        f"trim=start_frame=0:end_frame={n_tr},setpts=N/({fps}*TB),format=yuv420p{out}"
    )


def _call_build_overlay(
    subtitles_mod: Any,
    plan: Dict[str, Any],
    segs: List[Dict[str, Any]],
    starts: List[float],
    total: float,
    width: int,
    height: int,
    work_dir: Path,
    *,
    srt_path: Optional[Path] = None,
    video: Optional[Path] = None,
    report_dir: Optional[Path] = None,
    fps: int = 30,
    subdir: str = "_subs",
    plan_path: Optional[Path] = None,
) -> Tuple[Optional[Path], int]:
    """Invoke `subtitles.build_overlay` with signature-safe `fps`/`subdir` forwarding and resolve `band_y0`."""
    sig = inspect.signature(subtitles_mod.build_overlay)
    params = sig.parameters
    has_var_kw = any(p.kind == inspect.Parameter.VAR_KEYWORD for p in params.values())
    kw: Dict[str, Any] = {
        "srt_path": srt_path,
        "video": video,
        "report_dir": report_dir,
    }
    if "fps" in params or has_var_kw:
        kw["fps"] = fps
    eff_work_dir = work_dir
    if subdir != "_subs":
        if "subdir" in params or has_var_kw:
            kw["subdir"] = subdir
        else:
            eff_work_dir = work_dir / subdir
            eff_work_dir.mkdir(parents=True, exist_ok=True)

    res = subtitles_mod.build_overlay(plan, segs, starts, total, width, height, eff_work_dir, **kw)
    if res is None:
        return None, 0

    sub_list: Optional[Path] = None
    band_y0: Optional[int] = None
    if isinstance(res, tuple):
        sub_list = Path(res[0]) if res[0] else None
        if len(res) >= 2 and res[1] is not None:
            band_y0 = int(res[1])
    elif isinstance(res, dict):
        sub_list = Path(res["path"]) if res.get("path") else None
        if res.get("band_y0") is not None:
            band_y0 = int(res["band_y0"])
    else:
        sub_list = Path(res)

    if sub_list is None or not sub_list.is_file():
        return None, 0

    if band_y0 is None:
        meta_cands = [sub_list.parent / "subs_meta.json"]
        if plan_path is not None:
            meta_cands.append(plan_path.parent / subdir / "subs_meta.json")
        meta_cands.append(work_dir / subdir / "subs_meta.json")
        for mc in meta_cands:
            if mc.is_file():
                try:
                    mdata = json.loads(mc.read_text(encoding="utf-8"))
                    if isinstance(mdata, dict):
                        for k_name in ("band_y0", "bandTop", "offset_y"):
                            if mdata.get(k_name) is not None:
                                band_y0 = int(round(float(mdata[k_name])))
                                break
                except Exception:
                    pass
                if band_y0 is not None:
                    break

    return sub_list, int(band_y0 or 0)


def _flag_true(val: Any) -> bool:
    """Parse boolean/flag values cleanly, handling string forms like `'true'`/`'false'`."""
    if isinstance(val, str):
        return val.strip().lower() in ("1", "true", "yes", "on")
    return bool(val)


def _plan_has_flashcards(plan: Dict[str, Any], segs: List[Dict[str, Any]]) -> bool:
    """Return True when bilingual flashcards are enabled and present on the plan or segments."""
    if _flag_true(plan.get("cards_off")):
        return False
    pf = plan.get("flashcards")
    if isinstance(pf, str) and pf.strip().lower() in ("off", "none", "false", "0"):
        return False
    if isinstance(pf, list) and len(pf) > 0:
        return True
    return any(bool(s.get("flashcards")) and not _flag_true(s.get("cards_off")) for s in segs)


def _valid_flashcards(cards: Optional[List[Dict[str, Any]]]) -> List[Dict[str, Any]]:
    """Filter placed flashcard metadata dicts to entries with an existing PNG and positive span."""
    valid: List[Dict[str, Any]] = []
    for card in cards or []:
        if not isinstance(card, dict):
            continue
        png_raw = card.get("png") or card.get("path") or card.get("file")
        if not png_raw or not Path(str(png_raw)).is_file():
            continue
        try:
            t_st = max(0.0, float(card.get("start", 0.0)))
            t_en = float(card.get("end", 0.0))
        except (TypeError, ValueError):
            continue
        if t_en <= t_st + 0.01:
            continue
        valid.append(card)
    return valid


def _build_overlay_composite_graph(
    base_video: Path,
    cards: Optional[List[Dict[str, Any]]],
    sub_list: Optional[Path],
    sub_band_y0: int,
    fps: int,
    extra_fc: str = "",
) -> Tuple[List[Any], str, int]:
    """Build FFmpeg inputs and filter_complex compositing `base_video -> flashcards -> subtitles -> [vsub]`.

    Uses sharp crisp hard-cut card swaps (`-loop 1 -framerate <fps> -i <png>` +
    `overlay=x=...:y=...:enable='between(t,start,end)':shortest=1:eof_action=pass`)
    with subtitles layered cleanly on top of flashcards.
    """
    in_args: List[Any] = ["-i", base_video]
    next_idx = 1
    fc_nodes: List[str] = []
    cur_v = "[0:v:0]"
    applied_cards = 0

    for k, card in enumerate(_valid_flashcards(cards)):
        png_path = Path(str(card.get("png") or card.get("path") or card.get("file")))
        t_st = max(0.0, float(card.get("start", 0.0)))
        t_en = float(card.get("end", 0.0))
        x0 = int(round(float(card.get("x0", 0))))
        y0 = int(round(float(card.get("y0", 0))))

        in_args += ["-loop", "1", "-framerate", str(fps), "-i", str(png_path)]
        fc_lbl = f"[fc{k}]"
        out_lbl = f"[vfc{k}]"
        fc_nodes.append(f"[{next_idx}:v]format=rgba{fc_lbl}")
        fc_nodes.append(
            f"{cur_v}{fc_lbl}overlay=x={x0}:y={y0}:"
            f"enable='between(t,{t_st:.4f},{t_en:.4f})':shortest=1:eof_action=pass{out_lbl}"
        )
        cur_v = out_lbl
        next_idx += 1
        applied_cards += 1

    if sub_list is not None and Path(sub_list).is_file():
        in_args += ["-f", "concat", "-safe", "0", "-i", str(sub_list)]
        fc_nodes.append(f"[{next_idx}:v]fps={fps}[vcap]")
        fc_nodes.append(
            f"{cur_v}[vcap]overlay=0:{int(sub_band_y0)}:format=auto:eof_action=pass,format=yuv420p[vsub]"
        )
    else:
        fc_nodes.append(f"{cur_v}format=yuv420p[vsub]")

    if extra_fc:
        fc_nodes.append(extra_fc)

    return in_args, ";".join(fc_nodes), applied_cards


def _say(kind: str, text: str, **data: Any) -> None:
    try:
        import events

        events.emit(kind, text, **data)
    except Exception:
        pass


def render_square_1x1(
    src_mp4: Path,
    out_mp4: Optional[Path] = None,
    sq_size: int = 1080,
    blur_sigma: float = 18.0,
    draft: bool = False,
    audio_src: Optional[Path] = None,
    sub_list: Optional[Path] = None,
    sub_band_y0: int = 0,
    fps: Optional[int] = None,
    bitrate_kbps: Optional[int] = None,
) -> Path:
    """Convert a completed 9:16 vertical reel into a 1:1 square MP4 with 1 video stream and 1 stereo AAC audio stream:
      - Split the finished 9:16 reel into 2 layers (no separate 1:1 images, flashcards, timestamps, or subtitles).
      - Layer 1 (Background): 1:1 center crop from the 9:16 reel with 4x downscaled Gaussian blur (`blur_sigma=18.0`, ~7%, in 5–10% range).
      - Layer 2 (Foreground): Full 9:16 reel scaled cleanly to fit height (`608x1080` at `1080x1080`) and centered on top.
    """
    src_mp4 = resolve(src_mp4)
    if out_mp4 is None:
        out_mp4 = square_job_output(src_mp4)
    out_mp4 = Path(out_mp4)
    out_mp4.parent.mkdir(parents=True, exist_ok=True)

    src_info = probe(src_mp4)
    eff_fps = int(round(fps or src_info.get("fps") or 30))
    sq = even(sq_size)
    fg_w = even(sq * 9.0 / 16.0)
    bg_h = even(sq * 16.0 / 9.0)
    # 4x downscaled Gaussian blur (16x fewer pixels) -> upscale back to sq x sq: visually identical, 94% less CPU load
    blur_scale = 4
    sq_small = even(sq // blur_scale)
    bg_h_small = even(bg_h // blur_scale)
    sigma_small = max(1.0, blur_sigma / float(blur_scale))

    in_args: List[Any] = ["-i", src_mp4]
    next_idx = 1
    aud_input_idx: Optional[int] = None
    aud_copy_ok = False
    if audio_src is not None and Path(audio_src).is_file():
        in_args += ["-i", Path(audio_src)]
        aud_input_idx = next_idx
        aud_copy_ok = Path(audio_src).suffix.lower() in (".mp4", ".m4a", ".mov")
        next_idx += 1
    elif src_info.get("has_audio"):
        aud_input_idx = 0
        aud_copy_ok = src_mp4.suffix.lower() in (".mp4", ".m4a")

    sub_input_idx: Optional[int] = None
    if sub_list is not None and Path(sub_list).is_file():
        in_args += ["-f", "concat", "-safe", "0", "-i", str(sub_list)]
        sub_input_idx = next_idx
        next_idx += 1

    fc_parts = [
        "[0:v:0]split=2[bg_in][fg_in]",
        f"[bg_in]scale={sq_small}:{bg_h_small}:force_original_aspect_ratio=increase:flags=bilinear,"
        f"crop={sq_small}:{sq_small}:(iw-{sq_small})/2:(ih-{sq_small})/2,"
        f"gblur=sigma={sigma_small:.2f},"
        f"scale={sq}:{sq}:flags=bicubic[bg]",
        f"[fg_in]scale={fg_w}:{sq}:flags=lanczos[fg]",
    ]
    if sub_input_idx is not None:
        fc_parts.append("[bg][fg]overlay=(W-w)/2:(H-h)/2,setsar=1[vsq]")
        fc_parts.append(f"[{sub_input_idx}:v]fps={eff_fps}[vcap]")
        fc_parts.append(f"[vsq][vcap]overlay=0:{int(sub_band_y0)}:format=auto:eof_action=pass,format=yuv420p[vout]")
    else:
        fc_parts.append("[bg][fg]overlay=(W-w)/2:(H-h)/2,setsar=1,format=yuv420p[vout]")

    map_aud: List[Any] = []
    codec_aud: List[Any] = []
    if aud_input_idx is not None:
        map_aud = ["-map", f"{aud_input_idx}:a:0?"]
        if aud_copy_ok:
            codec_aud = ["-c:a", "copy"]
        else:
            codec_aud = ["-c:a", "aac", "-ac", "2", "-b:a", "192k"]

    eff_br = bitrate_kbps or final_bitrate_kbps(src_info.get("duration") or 60.0, scale=0.65)
    ffmpeg_encode(
        *in_args,
        "-filter_complex", ";".join(fc_parts),
        "-map", "[vout]",
        *map_aud,
        "-c:v", "libx264",
        "-preset", "ultrafast" if draft else "veryfast",
        "-crf", "23" if draft else "18",
        "-profile:v", "high",
        "-pix_fmt", "yuv420p",
        *codec_aud,
        "-movflags", "+faststart",
        out_mp4,
        draft=draft,
        bitrate_kbps=eff_br,
    )
    return out_mp4


def render_segment(seg: Dict[str, Any], W: int, H: int, fps: int, out: Path, draft: bool) -> float:
    """Fallback single-layout segment renderer with sub-pixel supersampled zoompan and PTS-safe audio."""
    src_dur = max(0.08, float(seg["end"]) - float(seg["start"]))
    speed = _segment_speed(seg)
    has_speed = abs(speed - 1.0) >= 0.005
    is_muted = bool(seg.get("mute") or seg.get("muted"))
    raw_dur = max(0.08, src_dur / speed)
    hold_tail = segment_hold_tail(seg)
    dur = max(0.08, segment_duration(seg))
    raw_frames = max(1, round(raw_dur * fps))
    raw_exact = raw_frames / fps
    frames = max(1, round(dur * fps))
    hold_frames = max(0, frames - raw_frames)
    exact = frames / fps
    zoom_span = raw_exact if hold_tail > 0.01 else exact

    audio_end_val: Optional[float] = None
    ae_raw = seg.get("audio_end")
    if ae_raw is not None and not isinstance(ae_raw, bool):
        try:
            ae_f = float(ae_raw)
            if math.isfinite(ae_f) and ae_f > 0.04:
                audio_end_val = ae_f
        except (TypeError, ValueError):
            pass
    has_audio_end = audio_end_val is not None and audio_end_val < raw_exact

    if layouts is not None and hasattr(layouts, "resolve_segment_zoom_spec"):
        z_spec = layouts.resolve_segment_zoom_spec(seg, zoom_span)
        z0, z1, zoom_style, zoom_at, zoom_dur = z_spec[:5]
    else:
        z0 = float(seg.get("zoom", 1.0) or 1.0)
        z1 = float(seg.get("zoom_to", z0) or z0)
        zoom_style = str(seg.get("zoom_style") or ("smooth" if abs(z1 - z0) >= 0.005 else "none"))
        zoom_at = float(seg.get("zoom_at") or 0.0)
        zoom_dur = float(seg.get("zoom_dur") or 0.18)
    fx, fy = seg.get("focus", [0.5, 0.38])
    vf = [
        f"setpts=(PTS-STARTPTS)/{speed:.6f}" if has_speed else "setpts=PTS-STARTPTS",
        f"fps={fps}",
        f"scale={W}:{H}:force_original_aspect_ratio=increase:flags=lanczos",
        f"crop={W}:{H}:x='(iw-{W})*{fx:.4f}':y='(ih-{H})*{fy:.4f}'",
    ]
    if hold_tail > 0.01:
        vf.append(f"trim=end_frame={raw_frames}")
    if layouts is not None and hasattr(layouts, "_build_subpixel_zoom_filter"):
        z_kw: Dict[str, Any] = {
            "slot_w": W,
            "slot_h": H,
            "fps": fps,
            "exact_dur": zoom_span,
            "fx": float(fx),
            "fy": float(fy),
            "z0": z0,
            "z1": z1,
            "zoom_style": zoom_style,
            "zoom_at": zoom_at,
            "zoom_dur": zoom_dur,
        }
        try:
            z_params = inspect.signature(layouts._build_subpixel_zoom_filter).parameters
            if "zoom_peak" in z_params and seg.get("zoom_peak") is not None:
                z_kw["zoom_peak"] = float(seg["zoom_peak"])
            if "zoom_end" in z_params and seg.get("zoom_end") is not None:
                z_kw["zoom_end"] = float(seg["zoom_end"])
        except (TypeError, ValueError):
            pass
        vf.extend(layouts._build_subpixel_zoom_filter(**z_kw))

    seg_loud = float(seg.get("loudness") if seg.get("loudness") is not None else DEFAULT_LOUDNESS_LUFS)
    seg_gain_db = float(seg.get("gain_db") or 0.0)
    if is_muted:
        af = [
            "aresample=48000",
            "aformat=channel_layouts=stereo",
            "asetpts=PTS-STARTPTS",
            "volume=0",
            "apad",
            f"atrim=0:{exact:.6f}",
            "asetpts=PTS-STARTPTS",
        ]
    else:
        a_active_dur = (
            min(raw_exact, audio_end_val)
            if (has_audio_end and audio_end_val is not None)
            else (raw_exact if hold_tail > 0.01 else exact)
        )
        a_fade_out = min(0.030, max(EDGE_FADE, a_active_dur * 0.25)) if has_audio_end else EDGE_FADE
        af = [
            "aresample=48000",
            "aformat=channel_layouts=stereo",
            "asetpts=PTS-STARTPTS",
        ]
        if has_speed:
            af.append(f"atempo={speed:.6f}")
        af += [
            "apad",
            f"atrim=0:{a_active_dur:.6f}",
            "asetpts=PTS-STARTPTS",
            "highpass=f=70",
            f"loudnorm=I={seg_loud}:TP={DEFAULT_PEAK_CAP_DB}:LRA=9",
        ]
        if abs(seg_gain_db) >= 0.05:
            af.append(f"volume={seg_gain_db:+.2f}dB")
        af += [
            f"alimiter=limit={DEFAULT_PEAK_CAP_DB}dB:level=false",
            f"afade=t=in:d={EDGE_FADE}",
            f"afade=t=out:st={max(0.0, a_active_dur - a_fade_out):.6f}:d={a_fade_out:.6f}",
        ]
        if hold_tail > 0.01 or has_audio_end:
            af += [
                "apad",
                f"atrim=0:{exact:.6f}",
                "asetpts=PTS-STARTPTS",
            ]

    src = resolve(seg["src"])
    has_audio = probe(src)["has_audio"]
    read_dur = src_dur

    hold_src_raw = seg.get("hold_src")
    hold_src_path = resolve(hold_src_raw) if hold_src_raw else None
    has_live_hold = bool(
        hold_tail > 0.01
        and hold_frames > 0
        and hold_src_path is not None
        and hold_src_path.is_file()
    )

    if has_live_hold and hold_src_path is not None:
        hold_info = probe(hold_src_path)
        hold_media_dur = float(hold_info.get("duration") or 6.0)
        h_span = seg.get("hold_span")
        if isinstance(h_span, (list, tuple)) and len(h_span) >= 2:
            h_start, h_end = float(h_span[0]), float(h_span[1])
        else:
            h_start = float(seg.get("hold_start") or 0.0)
            h_end = float(seg["hold_end"]) if seg.get("hold_end") is not None else None
        h_start = max(0.0, h_start)
        if h_start >= max(0.05, hold_media_dur - 0.10):
            h_start = max(0.0, hold_media_dur - max(0.30, hold_frames / float(fps)))
        hold_dur_target = max(0.04, hold_frames / float(fps))
        h_avail = max(0.04, hold_media_dur - h_start)
        span_len = min(h_avail, h_end - h_start) if (h_end is not None and h_end > h_start + 0.08) else h_avail

        args: List[Any] = ["-ss", f"{float(seg['start']):.3f}", "-t", f"{read_dur + 0.5:.3f}", "-i", src]
        hold_ops = ["setpts=PTS-STARTPTS", f"fps={fps}"]
        if span_len >= hold_dur_target - 0.02:
            args += ["-ss", f"{h_start:.3f}", "-t", f"{hold_dur_target + 0.35:.3f}", "-i", hold_src_path]
        elif span_len >= 0.15:
            args += ["-ss", f"{h_start:.3f}", "-t", f"{span_len:.3f}", "-i", hold_src_path]
            loop_frames = max(2, int(round(span_len * fps)))
            hold_ops.extend([f"loop=loop=-1:size={loop_frames}:start=0", f"setpts=N/({fps}*TB)"])
        else:
            args += ["-ss", f"{max(0.0, hold_media_dur - 0.5):.3f}", "-t", "0.500", "-i", hold_src_path]

        a_idx = 0 if has_audio else 2
        if not has_audio:
            args += ["-f", "lavfi", "-t", f"{exact:.3f}", "-i", "anullsrc=r=48000:cl=stereo"]

        focus_end = seg.get("focus_end") or [fx, fy]
        hfx = float(focus_end[0]) if hold_src_path == src else float(fx)
        hfy = float(focus_end[1]) if hold_src_path == src else float(fy)
        hold_ops.extend([
            f"scale={W}:{H}:force_original_aspect_ratio=increase:flags=lanczos",
            f"crop={W}:{H}:x='(iw-{W})*{hfx:.4f}':y='(ih-{H})*{hfy:.4f}'",
        ])
        z_hold = z1 if zoom_style != "none" else 1.0
        if z_hold > 1.004:
            zw_h, zh_h = even(W * z_hold), even(H * z_hold)
            hold_ops.extend([
                f"scale={zw_h}:{zh_h}:flags=lanczos",
                f"crop={W}:{H}:x='(iw-{W})*{hfx:.4f}':y='(ih-{H})*{hfy:.4f}'",
            ])
        hold_ops.extend([
            "setsar=1",
            "format=yuv420p",
            f"tpad=stop_mode=clone:stop={hold_frames + 4}",
            f"trim=end_frame={hold_frames}",
            "setpts=PTS-STARTPTS",
            "settb=AVTB",
            f"setpts=N/({fps}*TB)",
            f"fps={fps}",
        ])
        live_chain = vf + [
            "setsar=1",
            "format=yuv420p",
            "tpad=stop_mode=clone:stop=4",
            f"trim=end_frame={raw_frames}",
            "setpts=PTS-STARTPTS",
            "settb=AVTB",
            f"setpts=N/({fps}*TB)",
            f"fps={fps}",
        ]
        fc_parts = [
            f"[0:v]{','.join(live_chain)}[vlive]",
            f"[1:v]{','.join(hold_ops)}[vhold]",
            f"[vlive][vhold]concat=n=2:v=1:a=0,settb=AVTB,setpts=N/({fps}*TB),fps={fps}[vout]",
            f"[{a_idx}:a]{','.join(af)}[aout]",
        ]
        args += [
            "-filter_complex", ";".join(fc_parts),
            "-map", "[vout]",
            "-map", "[aout]",
            "-frames:v", str(frames),
            "-c:v", "libx264",
            "-preset", "ultrafast" if draft else "superfast",
            "-crf", "12",
            "-c:a", "pcm_s16le",
            out,
        ]
    else:
        clone_frames = max(3, frames - raw_frames + 3) if hold_tail > 0.01 else 3
        vf += ["setsar=1", "format=yuv420p", f"tpad=stop_mode=clone:stop={clone_frames}"]
        args = ["-ss", f"{float(seg['start']):.3f}", "-t", f"{read_dur + 0.5:.3f}", "-i", src]
        if not has_audio:
            args += ["-f", "lavfi", "-t", f"{exact:.3f}", "-i", "anullsrc=r=48000:cl=stereo"]
        args += [
            "-map", "0:v:0",
            "-map", "0:a:0" if has_audio else "1:a:0",
            "-vf", ",".join(vf),
            "-af", ",".join(af),
            "-frames:v", str(frames),
            "-c:v", "libx264",
            "-preset", "ultrafast" if draft else "superfast",
            "-crf", "12",
            "-c:a", "pcm_s16le",
            out,
        ]
    ffmpeg_encode(*args, draft=draft)
    return exact


def place(pos: Any, W: int, H: int, size: int) -> Tuple[str, str]:
    m = int(min(W, H) * 0.06)
    if isinstance(pos, list):
        return f"{pos[0]}*{W}-{size}/2", f"{pos[1]}*{H}-{size}/2"
    return {
        "center": (f"({W}-{size})/2", f"({H}-{size})/2"),
        "top-center": (f"({W}-{size})/2", str(m)),
        "top-right": (str(W - size - m), str(m)),
        "top-left": (str(m), str(m)),
        "bottom-right": (str(W - size - m), str(H - size - m)),
        "bottom-left": (str(m), str(H - size - m)),
    }.get(str(pos or "top-right"), (str(W - size - m), str(m)))


def _load_plan(plan_arg: str) -> Tuple[Path, Dict[str, Any]]:
    plan_path = resolve(plan_arg)
    if plan_path.suffix.lower() == ".edl":
        if edl_parser is not None:
            parse_fn = getattr(edl_parser, "parse_edl", getattr(edl_parser, "load_edl", None))
            if callable(parse_fn):
                plan = parse_fn(plan_path)
                write_json(plan_path.with_suffix(".json"), plan)
                return plan_path, plan
        json_sibling = plan_path.with_suffix(".json")
        if json_sibling.is_file():
            return plan_path, read_json(json_sibling)
        raise RuntimeError(f"Cannot parse {plan_path}: edl_parser module unavailable and no {json_sibling} found.")
    return plan_path, read_json(plan_path)


def _default_output_path(plan: Dict[str, Any], plan_path: Path) -> Path:
    if plan.get("output"):
        return resolve(plan["output"])
    if hasattr(common, "unique_job_output"):
        return Path(common.unique_job_output(plan_path.parent.name))
    folder_nm = strip_ratio_prefix(plan_path.parent.name) or plan_path.parent.name
    return OUTPUT / folder_nm / f"reel_{folder_nm}.mp4"


def _match_cached_segments_when_offline(
    segs: List[Dict[str, Any]],
    cache_dir: Path,
    fps: int,
) -> Optional[List[Path]]:
    """If raw footage files are offline, match existing rendered segment `.mkv` intermediates
    in `work/_segments/` by creation order and frame-accurate duration.
    """
    cand_mkvs = [
        p for p in cache_dir.glob("*.mkv")
        if p.name != "mix.mkv" and len(p.stem) == 16
    ]
    if len(cand_mkvs) < len(segs):
        return None

    cand_mkvs.sort(key=lambda p: p.stat().st_mtime_ns)
    meta = [(p, float(probe(p).get("duration") or 0.0), p.stat().st_mtime_ns // 1_000_000_000) for p in cand_mkvs]
    used = set()
    matched: List[Path] = []

    for idx, s in enumerate(segs):
        exp_dur = max(1, round(max(0.08, segment_duration(s)) * fps)) / float(fps)
        best_i = None
        best_score = 999999.0
        for m_idx, (p, dur, _mtime_s) in enumerate(meta):
            if m_idx in used:
                continue
            dur_err = abs(dur - exp_dur)
            if dur_err <= 0.08:
                order_err = abs(m_idx - idx) * 0.02
                score = dur_err + order_err
                if score < best_score:
                    best_score = score
                    best_i = m_idx
        if best_i is None:
            return None
        used.add(best_i)
        matched.append(meta[best_i][0])

    return matched


def _export_premiere(
    plan: Dict[str, Any],
    plan_path: Path,
    out_dir: Path,
    work_dir: Path,
    fps: int,
    width: int,
    height: int,
) -> Optional[Dict[str, Any]]:
    pr_mod = None
    for mod_name in ("premiere_export", "premiere"):
        try:
            pr_mod = __import__(mod_name)
            break
        except ImportError:
            continue
    if pr_mod is None:
        return None
    export_fn = getattr(pr_mod, "export_premiere_project", getattr(pr_mod, "export_project", None))
    if not callable(export_fn):
        return None
    return export_fn(
        plan=plan,
        plan_path=plan_path,
        out_dir=out_dir,
        work_dir=work_dir,
        fps=fps,
        width=width,
        height=height,
    )


def _extract_qc_failure_summary(text: str, limit: int = 900) -> str:
    """Return concise failure/location lines from Deep QC output, stripping trailing PASS lines."""
    raw = str(text or "").strip()
    if not raw:
        return "Deep QC verification failed"
    fail_lines: List[str] = []
    for ln in raw.splitlines():
        s = ln.strip()
        if not s:
            continue
        up = s.upper()
        has_fail = any(tok in up for tok in ("FAIL", "BLOCKED", "ERROR", "[STROBE]", "MISMATCH", "MISSING", "SHOWS OTHER FOOTAGE", "WHERE "))
        is_pass_only = bool(re.search(r"\bPASS\b", up)) and not has_fail
        if has_fail or (s.startswith("[") and not is_pass_only):
            fail_lines.append(s)
    if fail_lines:
        return "\n".join(fail_lines)[:limit]
    return raw[-limit:]


def main() -> None:
    with contextlib.ExitStack() as _render_q_stack:
        _main_with_queue(_render_q_stack)


def _main_with_queue(_render_q_stack: contextlib.ExitStack) -> None:
    ap = argparse.ArgumentParser(description="Render an edit plan (.json or .edl) into the final MP4.")
    ap.add_argument("plan", help="Path to plan.json or plan.edl")
    ap.add_argument("--draft", action="store_true", help="Fast low-quality encode for checking the cut")
    ap.add_argument("--verify", action="store_true", help="Automatically run verify.py after rendering")
    ap.add_argument(
        "--preset",
        default=None,
        help="Override layout preset across segments (single, split_v, split_h, chat_bar, video_call, hybrid)",
    )
    ap.add_argument(
        "--mark-pack",
        default=None,
        help="Override check/cross mark badge pack (default, glossy, flash, vignette)",
    )
    ap.add_argument(
        "--loudness",
        type=float,
        default=None,
        help=f"Override target integrated loudness in LUFS (default: {DEFAULT_LOUDNESS_LUFS:.1f} LUFS for Meta Ads/Reels)",
    )
    ap.add_argument(
        "--gain-db",
        type=float,
        default=None,
        help="Apply an exact global dB boost/cut on top of loudness normalization (e.g. +2.0 or -1.5)",
    )
    ap.add_argument(
        "--square",
        action="store_true",
        help="Also render a 1:1 (1080x1080) Square Meta Ads version with a 1:1 center-cropped Gaussian blurred background and centered 9:16 foreground",
    )
    ap.add_argument(
        "--blur-sigma",
        type=float,
        default=18.0,
        help="Gaussian blur sigma for the 1:1 background layer (default: 18.0, ~7%% blur)",
    )
    ap.add_argument(
        "--no-premiere",
        action="store_true",
        help="Skip automatic Adobe Premiere Pro (.prproj + .xml) project export",
    )
    ap.add_argument(
        "--deliver",
        action="store_true",
        help="Automatically deliver finished files into ~/Downloads/Zeno Outs/<FolderName>/ after rendering",
    )
    ap.add_argument(
        "--name",
        default="",
        help="Optional target folder/deliverable name override when using --deliver",
    )
    a = ap.parse_args()

    # Direct MP4 -> 1:1 Square conversion if a .mp4 path is passed
    if str(a.plan).lower().endswith(".mp4"):
        src_vid = resolve(a.plan)
        if src_vid.stem.lower().startswith("reel_"):
            sq_out = square_job_output(src_vid)
        else:
            sq_out = OUTPUT / f"SQ_{strip_ratio_prefix(src_vid.stem)}.mp4"
        print(f"Rendering 1:1 Square version from {src_vid} -> {sq_out} ...")
        render_square_1x1(src_vid, sq_out, sq_size=1080, blur_sigma=a.blur_sigma, draft=a.draft)
        print(f"Done (1:1 Square): {sq_out}")
        return

    plan_path, plan = _load_plan(a.plan)
    if verify is not None and plan_path.suffix.lower() == ".edl":
        # Fail fast (before any encode) when the declared format's catalog forbids split screens.
        if a.verify and hasattr(verify, "check_layout_vs_format"):
            try:
                _lay_ok, _lay_bad, _lay_msg = verify.check_layout_vs_format(plan, plan_path.parent)
            except Exception:
                _lay_ok, _lay_msg = True, ""
            if not _lay_ok:
                print(_lay_msg, flush=True)
                try:
                    (plan_path.parent / "verify_report.json").write_text(
                        json.dumps({"ok": False, "text": _lay_msg + "\n"}, ensure_ascii=False),
                        encoding="utf-8",
                    )
                except Exception:
                    pass
                sys.exit(1)
        # Opening dead air hidden by engine/wind noise (RMS cannot see it): trim to first word - 0.05 s.
        _segs0 = plan.get("segments") or []
        if _segs0 and hasattr(verify, "trim_leading_dead_air") and resolve(_segs0[0].get("src", "")).is_file():
            try:
                _open_note = verify.trim_leading_dead_air(plan, plan_path)
            except Exception:
                _open_note = ""
            if _open_note:
                print(_open_note, flush=True)
    if a.preset:
        plan["preset"] = a.preset
        if a.preset not in ("auto", "hybrid"):
            for s in plan.get("segments", []):
                s["layout"] = a.preset
    if a.mark_pack:
        plan["mark_pack"] = a.mark_pack
    if a.loudness is not None:
        plan.setdefault("audio", {})["loudness"] = a.loudness
    if a.gain_db is not None:
        plan.setdefault("audio", {})["gain_db"] = a.gain_db

    segs = plan["segments"]
    first_src = resolve(segs[0]["src"])
    if first_src.is_file():
        first = probe(first_src)
    else:
        info_cache = clip_work_dir(segs[0]["src"]) / "info.json"
        first = read_json(info_cache) if info_cache.is_file() else {"width": 720, "height": 1280, "fps": 24}

    canvas = plan.get("canvas", {})
    W = even(canvas.get("width", first.get("width") or 720))
    H = even(canvas.get("height", first.get("height") or 1280))
    fps = (
        int(round(float(canvas["fps"])))
        if canvas.get("fps")
        else _detect_modal_fps(segs, first_info=first)
    )

    # Scrolling-text page format (`#page scroll`): render segments into the top picture area,
    # then stack the progress bar and scrolling page below in the final step.
    FULL_H = H
    page_cfg, page_geo, scroll_page = None, None, None
    try:
        import scroll_page  # type: ignore[no-redef]
        page_cfg = scroll_page.page_config(plan)
    except Exception as exc:  # noqa: BLE001
        if plan.get("page"):
            print(f"  [PAGE] Scrolling page module could not be loaded: {exc}")
        page_cfg = None
    if page_cfg and scroll_page is not None:
        page_geo = scroll_page.geometry(W, H, page_cfg)
        H = page_geo["video_h"]
        print(f"Scrolling-text page format: picture {W}x{H} on top, page {W}x{page_geo['page_h']} below.")

    out_path = _default_output_path(plan, plan_path)
    _q_vname = out_path.stem.replace("reel_", "", 1) or plan_path.parent.name
    try:
        from stage_queue import stage_slot as _stage_slot
    except Exception:
        _stage_slot = contextlib.nullcontext
    _s4_stack = contextlib.ExitStack()
    _render_q_stack.callback(_s4_stack.close)
    _s4_stack.enter_context(_stage_slot("stage4_editing", project_dir=plan_path.parent, video_name=_q_vname))

    cache = plan_path.parent / "_segments"
    cache.mkdir(parents=True, exist_ok=True)

    # Check if raw source clips are offline and pre-rendered cached segments exist
    offline_matched = None
    if not first_src.is_file():
        offline_matched = _match_cached_segments_when_offline(segs, cache, fps)
        if not offline_matched and (WORK / "_segments").is_dir():
            offline_matched = _match_cached_segments_when_offline(segs, WORK / "_segments", fps)

    # 1) Each segment -> normalized intermediate (cached by exact parameters)
    if layouts is not None and hasattr(layouts, "split_lock_report") and first_src.is_file():
        try:
            lock_lines = layouts.split_lock_report(plan, W, H)
        except Exception:
            lock_lines = []
        if lock_lines:
            try:
                (plan_path.parent / "render_notes.json").write_text(
                    json.dumps({"split_panels": lock_lines}, ensure_ascii=False),
                    encoding="utf-8",
                )
            except Exception:
                pass
            print("Split panels (one fixed frame per panel, no zoom):")
            for ln in lock_lines:
                print("  " + ln)

    import concurrent.futures

    cool_mode = os.environ.get("ZENO_COOL", "0").strip().lower() in ("1", "on", "true", "yes")
    seg_specs: List[Tuple[int, Dict[str, Any], str, Path, float]] = []
    unique_uncached: Dict[str, Tuple[int, Dict[str, Any], Path]] = {}
    for i, s in enumerate(segs):
        s.setdefault("id", f"S{i + 1}")
        lay = s.get("layout", "single")
        if layouts is not None and hasattr(layouts, "build_segment_cache_key"):
            cache_hex = layouts.build_segment_cache_key(s, plan, W, H, fps, a.draft)
        else:
            src_resolved = resolve(s["src"])
            mtime = src_resolved.stat().st_mtime if src_resolved.is_file() else 0.0
            hold_tail_val = round(segment_hold_tail(s), 4)
            key_items = [
                s["src"], s["start"], s["end"], s.get("zoom"), s.get("zoom_to"),
                s.get("zoom_style"), s.get("zoom_at"), s.get("zoom_dur"),
                s.get("zoom_peak"), s.get("zoom_end"),
                s.get("focus"), s.get("focus_end"),
                round(_segment_speed(s), 4), bool(s.get("mute") or s.get("muted")),
                s.get("gain_db"), s.get("loudness"),
                W, H, fps, a.draft, mtime,
            ]
            if hold_tail_val > 0.0:
                key_items.append(hold_tail_val)
            if s.get("hold_src") or s.get("audio_end") is not None:
                key_items.extend([
                    s.get("hold_src"),
                    s.get("hold_span"),
                    s.get("hold_start"),
                    s.get("hold_end"),
                    s.get("audio_end"),
                ])
            if lay and lay != "single":
                key_items.extend([lay, s.get("peer"), s.get("chat"), s.get("slot")])
            cache_hex = hashlib.md5(json.dumps(key_items, sort_keys=True).encode()).hexdigest()[:16]
        f = cache / f"{cache_hex}.mkv"
        if offline_matched is not None and not f.exists():
            f = offline_matched[i]
        expected_dur = max(1, round(max(0.08, segment_duration(s)) * fps)) / float(fps)
        seg_specs.append((i, s, lay, f, expected_dur))
        if not f.exists() and str(f) not in unique_uncached:
            unique_uncached[str(f)] = (i, s, f)

    rendered_durs: Dict[str, float] = {}
    if unique_uncached:
        def _render_one_seg(item: Tuple[int, Dict[str, Any], Path]) -> Tuple[str, float]:
            if cool_mode:
                thermal_pause()
            _idx_s, seg_obj, out_f = item
            if layouts is not None and hasattr(layouts, "render_segment_layout"):
                dur_val = layouts.render_segment_layout(seg_obj, plan, W, H, fps, out_f, a.draft)
            else:
                dur_val = render_segment(seg_obj, W, H, fps, out_f, a.draft)
            return str(out_f), float(dur_val)

        tasks = list(unique_uncached.values())
        enc_name = common.hardware_encoder() or ""
        cpu_n = os.cpu_count() or 4
        if enc_name == "h264_nvenc":
            max_auto = min(3, max(2, cpu_n // 2))
        elif enc_name == "h264_videotoolbox":
            max_auto = min(6, max(2, cpu_n // 2))
        else:
            max_auto = min(4, max(2, cpu_n // 2))
        seg_workers = max(1, min(int(os.environ.get("ZENO_RENDER_WORKERS", str(max_auto)) or str(max_auto)), len(tasks)))
        if cool_mode:
            seg_workers = 1
        if seg_workers > 1 and len(tasks) > 1:
            for _idx_s, seg_obj, _out_f in tasks:
                lay_lbl = seg_obj.get("layout", "single")
                print(f"  segment {seg_obj['id']}: {seg_obj['src']} {float(seg_obj['start']):.2f}-{float(seg_obj['end']):.2f} ({lay_lbl})")
            prev_ff_thr = os.environ.get("ZENO_FFMPEG_THREADS")
            per_w_thr = max(2, int(getattr(common, "ZENO_MAX_THREADS", 4)) // seg_workers)
            os.environ["ZENO_FFMPEG_THREADS"] = str(per_w_thr)
            try:
                with concurrent.futures.ThreadPoolExecutor(max_workers=seg_workers) as pool:
                    for out_key, dur_val in pool.map(_render_one_seg, tasks):
                        rendered_durs[out_key] = dur_val
            finally:
                if prev_ff_thr is None:
                    os.environ.pop("ZENO_FFMPEG_THREADS", None)
                else:
                    os.environ["ZENO_FFMPEG_THREADS"] = prev_ff_thr
        else:
            for item in tasks:
                _idx_s, seg_obj, _out_f = item
                lay_lbl = seg_obj.get("layout", "single")
                print(f"  segment {seg_obj['id']}: {seg_obj['src']} {float(seg_obj['start']):.2f}-{float(seg_obj['end']):.2f} ({lay_lbl})")
                out_key, dur_val = _render_one_seg(item)
                rendered_durs[out_key] = dur_val

    files: List[Path] = []
    durs: List[float] = []
    for i, s, lay, f, expected_dur in seg_specs:
        if str(f) in rendered_durs:
            durs.append(rendered_durs[str(f)])
            _say(
                "render",
                f"Cutting {s['id']}: {Path(str(s['src'])).name} {float(s['start']):.2f}-{float(s['end']):.2f}, {lay}",
                cut=s["id"],
                i=i + 1,
                n=len(segs),
            )
        else:
            durs.append(expected_dur)
            _say(
                "render",
                f"Cut {s['id']} ready (from cache): {Path(str(s['src'])).name} {float(s['start']):.2f}-{float(s['end']):.2f}, {lay}",
                cut=s["id"],
                i=i + 1,
                n=len(segs),
            )
        files.append(f)

    # 2) Output timeline (Zero-Audio-Loss physical transitions: video uses tpad+S-curve motion; audio uses 0.00s-drift joins)
    starts: List[float] = []
    total = 0.0
    for i, s in enumerate(segs):
        raw_tr = str(s.get("transition", "cut") or "cut").strip() if i else "cut"
        tr = raw_tr.lower()
        prev_lay = str(segs[i - 1].get("layout") or "single").lower() if i > 0 else "single"
        cur_lay = str(s.get("layout") or "single").lower()
        if i > 0 and prev_lay == cur_lay and cur_lay in _SPLIT_LAYOUTS:
            tr = "cut"
        s.pop("_flash", None)
        s.pop("_meta_post", None)
        s.pop("_tr_label", None)

        if tr != "cut":
            entering_or_exiting_split = i > 0 and ((prev_lay in _SPLIT_LAYOUTS) != (cur_lay in _SPLIT_LAYOUTS))
            if entering_or_exiting_split and tr in ("warm_flash", "split_reveal"):
                if tr == "warm_flash" and load_overlay_transition("warm_flash") is not None:
                    s["_flash"] = "warm_flash"
                    s["_tr_label"] = "warm_flash+split_reveal"
                else:
                    s["_tr_label"] = "split_reveal"
                tr = "split_reveal"
            elif tr == "warm_flash" and load_overlay_transition("warm_flash") is not None:
                s["_flash"] = "warm_flash"
                s["_tr_label"] = "warm_flash"
                tr = "warm_flash_punch"
            elif load_overlay_transition(tr) is not None:
                s["_flash"] = tr
                s["_tr_label"] = tr
                tr = "cut"

        meta_spec = _META_TRANSITIONS.get(tr)
        def_tr_dur = meta_spec["default_dur"] if meta_spec else 0.30
        if tr != "cut":
            raw_d_val = s.get("transition_duration")
            if raw_d_val is None:
                req_d = def_tr_dur
            else:
                raw_d = float(raw_d_val)
                if raw_d <= 0.0:
                    req_d = def_tr_dur
                elif raw_d < 0.16:
                    req_d = 0.16
                else:
                    req_d = raw_d
            d = min(req_d, durs[i] / 2.0, durs[i - 1] / 2.0)
        else:
            d = 0.0
        s["_tr"], s["_d"] = tr, d
        if s.get("_flash") or tr != "cut":
            s["_join_at"] = total
        if s.get("_flash"):
            s["_flash_at"] = total
        starts.append(total)
        total += durs[i]
    seg_start = {s["id"]: starts[i] for i, s in enumerate(segs)}
    seg_by_id = {s["id"]: s for s in segs}

    inputs: List[Any] = []
    fc: List[str] = []
    for i, f in enumerate(files):
        inputs += ["-i", f]
        fc.append(f"[{i}:v]fps={fps},setpts=PTS-STARTPTS[sv{i}]")
        fc.append(f"[{i}:a]atrim=0:{durs[i]:.6f},asetpts=PTS-STARTPTS[sa{i}]")
    au = "[sa0]"
    xf_audio_dur = 0.016  # 16ms triangular audio crossfade (zero drift via apad, preserves cut B opening consonant)
    for i in range(1, len(segs)):
        na = f"[a{i}]"
        fc.append(f"{au}apad=pad_dur={xf_audio_dur:.4f}[ap{i}]")
        fc.append(f"[ap{i}][sa{i}]acrossfade=d={xf_audio_dur:.4f}:c1=tri:c2=tri{na}")
        au = na

    # Video: exact-frame concat of every cut. Each transition processes ONLY the 2*half[i] join window
    # (`[oa{i}]` from tail of cut i-1 + `[ib{i}]` from head of cut i -> `[x{i}]`) with smooth S-curve motion,
    # keeping segment midpoints untouched and preserving total frame count down to the exact frame.
    nsegs = len(segs)
    frames = [max(1, round(durs[i] * fps)) for i in range(nsegs)]
    half = [0] * nsegs
    for i in range(1, nsegs):
        if segs[i]["_tr"] != "cut" and min(frames[i - 1], frames[i]) >= 5:
            max_prev = frames[i - 1] // 3 if frames[i - 1] >= 9 else (frames[i - 1] - 1) // 2
            max_curr = frames[i] // 3 if frames[i] >= 9 else (frames[i] - 1) // 2
            max_half = min(max_prev, max_curr)
            if max_half >= 2:
                half[i] = max(2, min(round(segs[i]["_d"] * fps / 2.0), max_half))

    for i in range(nsegs):
        head = half[i]
        tail = half[i + 1] if i + 1 < nsegs else 0
        uses = ["bd"] + (["hd"] if head else []) + (["tl"] if tail else [])
        if len(uses) == 1:
            fc.append(f"[sv{i}]null[bd{i}]")
        else:
            fc.append(f"[sv{i}]split={len(uses)}" + "".join(f"[{u}{i}]" for u in uses))
        if tail:
            fc.append(
                f"[tl{i}]tpad=stop_mode=clone:stop={tail + 2},"
                f"trim=start_frame={frames[i] - tail}:end_frame={frames[i] + tail},"
                f"setpts=N/({fps}*TB),format=yuv420p[oa{i + 1}]"
            )
        if i and head:
            fc.append(
                f"[hd{i}]tpad=start_mode=clone:start={head}:stop_mode=clone:stop=2,"
                f"trim=start_frame=0:end_frame={2 * head},"
                f"setpts=N/({fps}*TB),format=yuv420p[ib{i}]"
            )
            prev_lay_i = str(segs[i - 1].get("layout") or "single").lower()
            cur_lay_i = str(segs[i].get("layout") or "single").lower()
            fc.append(
                _build_physical_transition_fc(
                    segs[i]["_tr"],
                    i,
                    W,
                    H,
                    fps,
                    2 * head,
                    prev_lay=prev_lay_i,
                    cur_lay=cur_lay_i,
                )
            )
        fc.append(
            f"[bd{i}]tpad=stop_mode=clone:stop=2,"
            f"trim=start_frame={head}:end_frame={frames[i] - tail},"
            f"setpts=N/({fps}*TB),format=yuv420p[body{i}]"
        )

    ordered: List[str] = []
    for i in range(nsegs):
        if i and half[i]:
            ordered.append(f"[x{i}]")
        ordered.append(f"[body{i}]")
    fc.append("".join(ordered) + f"concat=n={len(ordered)}:v=1:a=0,settb=AVTB,setpts=N/({fps}*TB)[vcat]")
    v = "[vcat]"

    # 2b) Frame-exact overlay transitions (warm flash) and optical flash polish around join_t
    flash_chain: List[str] = []
    for s in segs:
        if s.get("_flash"):
            spec = load_overlay_transition(s["_flash"])
            if spec is not None:
                swap = int(s.get("transition_swap", spec.get("swap_frame", 5)))
                flash_chain += flash_filters(spec, float(s["_flash_at"]), swap, canvas_fps=fps)
        if s.get("_tr") in _META_TRANSITIONS:
            meta_spec = _META_TRANSITIONS[s["_tr"]]
            post_fn = meta_spec.get("post")
            if callable(post_fn):
                flash_chain += post_fn(float(s.get("_join_at", 0.0)), fps)
    if flash_chain:
        fc.append(f"{v}{','.join(flash_chain)},format=yuv420p[vflash]")
        v = "[vflash]"

    # 3) Marks (check / cross or minimal 2-pulse end-of-sentence red/green overlays) and peak-calibrated SFX
    n = len(files)
    sfx_list: List[Tuple[Path, float, float]] = []
    explicit_tr_times: List[float] = []
    log_fx: List[Tuple[float, str]] = []
    default_pack = plan.get("mark_pack") or "flash"
    sfx_pack = plan.get("sfx_pack") or "default"
    plan_tuning = style_config.get_tuning(plan=plan) if style_config is not None else _TUNING

    fullframe_names = {
        "red_flash.png",
        "green_flash.png",
        "w42_red_vignette.png",
        "w42_green_vignette.png",
    }
    strobe_styles = {"flash", "vignette", "kn20228", "w42", "strobe"}

    def _mark_target_idx(m_obj: Dict[str, Any]) -> Optional[int]:
        raw_si = m_obj.get("seg_index")
        sid_m = str(m_obj.get("segment") or "")
        if raw_si is not None:
            try:
                si = int(raw_si)
                if 0 <= si < len(segs) and (not sid_m or str(segs[si].get("id") or "") == sid_m):
                    return si
            except (TypeError, ValueError):
                pass
        for k_s in range(len(segs) - 1, -1, -1):
            if str(segs[k_s].get("id") or "") == sid_m:
                return k_s
        return None

    all_marks: List[Dict[str, Any]] = []
    seen_marks = set()
    for m in plan.get("marks", []) or []:
        if not isinstance(m, dict):
            continue
        t_idx = _mark_target_idx(m)
        if t_idx is None:
            continue
        m_copy = dict(m)
        m_copy["seg_index"] = t_idx
        mt = _normalize_mark_type(m_copy.get("type") or m_copy.get("kind") or "right")
        m_at = round(float(m_copy.get("at", 0.0) or 0.0), 2)
        st_k = str(m_copy.get("style") or m_copy.get("pack") or default_pack).strip().lower()
        m_key = (t_idx, mt, m_at, st_k)
        if m_key not in seen_marks:
            seen_marks.add(m_key)
            all_marks.append(m_copy)
    for i_seg, s in enumerate(segs):
        sid = str(s.get("id") or "")
        if sid not in seg_start:
            continue
        for im in s.get("marks", []) or []:
            if not isinstance(im, dict):
                continue
            m_copy = dict(im)
            m_copy.setdefault("segment", sid)
            m_copy["seg_index"] = i_seg
            mt = _normalize_mark_type(m_copy.get("type") or m_copy.get("kind") or "right")
            m_at = round(float(m_copy.get("at", 0.0) or 0.0), 2)
            st_k = str(m_copy.get("style") or m_copy.get("pack") or default_pack).strip().lower()
            key = (i_seg, mt, m_at, st_k)
            if key not in seen_marks:
                seen_marks.add(key)
                all_marks.append(m_copy)

    for k, m in enumerate(all_marks):
        mtype = _normalize_mark_type(m.get("type") or m.get("kind") or "right")
        m_idx = m.get("seg_index")
        m_seg_start = starts[m_idx] if (m_idx is not None and 0 <= m_idx < len(starts)) else seg_start[m["segment"]]
        m_target_seg = segs[m_idx] if (m_idx is not None and 0 <= m_idx < len(segs)) else seg_by_id.get(m["segment"])
        t = max(0.0, m_seg_start + float(m.get("at", 0.0) or 0.0))
        dur = max(0.08, float(m.get("duration") if m.get("duration") is not None else 0.56))
        style_key = str(m.get("style") or m.get("pack") or default_pack).strip()
        png = resolve_mark_asset(mtype, style=style_key)
        if not png.is_file():
            png = MARKS_DIR / ("red_flash.png" if mtype == "wrong" else "green_flash.png")
            if not png.is_file():
                png = MARKS_DIR / f"{mtype}.png"

        is_fullframe_strobe = (
            style_key.lower() in strobe_styles
            or png.name.lower() in fullframe_names
            or bool(m.get("strobe", False))
        )

        if is_fullframe_strobe:
            alpha = max(
                0.15,
                min(
                    1.0,
                    float(
                        m.get("alpha")
                        if m.get("alpha") is not None
                        else plan_tuning.get("mark_alpha", 0.70)
                    ),
                ),
            )
            dur = min(dur, 0.56)
            inputs += ["-loop", "1", "-framerate", str(fps), "-t", f"{dur + 0.08:.4f}", "-i", png]
            if mtype == "wrong" and dur >= 0.30:
                p1_dur = round(min(0.16, dur * 0.35), 4)
                p2_start = round(min(0.22, dur * 0.48), 4)
                fc.append(
                    f"[{n}:v]scale={W}:{H},format=rgba,colorchannelmixer=aa={alpha:.3f},"
                    f"setpts=PTS-STARTPTS+{t:.4f}/TB[mk{k}]"
                )
                fc.append(
                    f"{v}[mk{k}]overlay=x=0:y=0:eof_action=pass:"
                    f"enable='between(t,{t:.4f},{t + p1_dur:.4f})+between(t,{t + p2_start:.4f},{t + dur:.4f})'[vm{k}]"
                )
                pos_desc = f"2pulse:{png.name}"
            else:
                fade_st = max(t + 0.04, t + dur - 0.05)
                fc.append(
                    f"[{n}:v]scale={W}:{H},format=rgba,colorchannelmixer=aa={alpha:.3f},"
                    f"setpts=PTS-STARTPTS+{t:.4f}/TB,"
                    f"fade=t=out:st={fade_st:.4f}:d=0.05:alpha=1[mk{k}]"
                )
                fc.append(
                    f"{v}[mk{k}]overlay=x=0:y=0:eof_action=pass:"
                    f"enable='between(t,{t:.4f},{t + dur:.4f})'[vm{k}]"
                )
                pos_desc = f"glow:{png.name}"
        else:
            inputs += ["-loop", "1", "-framerate", str(fps), "-t", f"{dur:.3f}", "-i", png]
            size = even(min(W, H) * float(m.get("size", 0.22)))
            target_seg = m_target_seg
            if layouts is not None and hasattr(layouts, "place_mark_for_segment"):
                x, y = layouts.place_mark_for_segment(m, target_seg, W, H, size)
            else:
                x, y = place(m.get("position", "center"), W, H, size)
            fc.append(
                f"[{n}:v]scale={size}:{size},format=rgba,fade=in:st=0:d=0.12:alpha=1,"
                f"fade=out:st={max(0.05, dur - 0.2):.3f}:d=0.2:alpha=1,setpts=PTS-STARTPTS+{t:.4f}/TB[mk{k}]"
            )
            fc.append(f"{v}[mk{k}]overlay=x={x}:y={y}:eof_action=pass:enable='between(t,{t:.4f},{t + dur:.4f})'[vm{k}]")
            pos_desc = str(m.get("position", "center"))
        v = f"[vm{k}]"
        n += 1

        if m.get("sfx", "default") is not None and str(m.get("sfx", "default")).lower() != "none":
            sfx_override = m.get("sfx", "default")
            if str(sfx_override).lower() == "default":
                sfx_override = str(
                    plan.get("error_sfx")
                    or plan_tuning.get("wrong_sfx_primary", "sn_wrong.wav")
                    if mtype == "wrong"
                    else plan_tuning.get("right_sfx_primary", "correct_ding.wav")
                )
            sfx_path = resolve_mark_sfx(mtype, sfx=sfx_override, pack=sfx_pack)
            if sfx_path:
                sfx_path = _redirect_broken_sfx(sfx_path)
            explicit_wrong_gain = (
                (plan.get("tuning") or {}).get("wrong_sfx_gain_db")
                if isinstance(plan.get("tuning"), dict)
                else None
            )
            wrong_default_gain = (
                float(explicit_wrong_gain)
                if explicit_wrong_gain is not None
                else float(plan_tuning.get("wrong_sfx_gain_db", 1.0)) - 5.5
            )
            default_gain = float(
                wrong_default_gain
                if mtype == "wrong"
                else plan_tuning.get("right_sfx_gain_db", 0.5)
            )
            seg_for_mk = m_target_seg
            seg_has_speech = not (
                seg_for_mk is not None
                and (
                    bool(getattr(common, "segment_is_muted", lambda s: bool(s.get("mute")))(seg_for_mk))
                    or bool(getattr(common, "segment_is_beat", lambda s: bool(s.get("beat")))(seg_for_mk))
                )
            )
            min_sfx_t = (
                max(0.0, m_seg_start + float(m.get("at", 0.0) or 0.0))
                if seg_has_speech
                else max(0.0, m_seg_start)
            )
            if sfx_path and sfx_path.is_file():
                gain_db = _calibrate_sfx_gain_db(
                    sfx_path,
                    m.get("sfx_gain_db"),
                    category="mark",
                    default_gain_db=default_gain,
                )
                t_sfx = max(min_sfx_t, t - _mark_sfx_preroll(sfx_path))
                sfx_list.append((sfx_path, t_sfx, gain_db))
            if is_fullframe_strobe:
                layer_name = str(
                    plan_tuning.get("wrong_sfx_layer", "none")
                    if mtype == "wrong"
                    else plan_tuning.get("right_sfx_layer", "sn_correct.wav")
                )
                extra_sfx = resolve_sfx_asset(layer_name, pack=sfx_pack) if layer_name and layer_name.lower() != "none" else None
                if extra_sfx:
                    extra_sfx = _redirect_broken_sfx(extra_sfx)
                if extra_sfx and extra_sfx.is_file() and (not sfx_path or extra_sfx.resolve() != sfx_path.resolve()):
                    layer_gain = default_gain if mtype == "wrong" else max(-2.0, default_gain - 2.0)
                    t_layer = max(min_sfx_t, t - _mark_sfx_preroll(extra_sfx))
                    sfx_list.append((extra_sfx, t_layer, layer_gain))
        log_fx.append((t, f"{mtype.upper()} mark ({pos_desc}, {dur:.2f}s)"))

    whoosh_gain = float(plan_tuning.get("whoosh_sfx_gain_db", -0.5))
    for e in plan.get("sfx", []):
        seg_id = e["segment"]
        e_idx = e.get("seg_index")
        e_seg_start = starts[e_idx] if (e_idx is not None and 0 <= e_idx < len(starts) and str(segs[e_idx].get("id") or "") == seg_id) else seg_start[seg_id]
        at_rel = float(e.get("at", 0.0))
        t_raw = e_seg_start + at_rel
        sfx_path = _redirect_broken_sfx(resolve_sfx_asset(e["file"], pack=sfx_pack))
        if sfx_path.is_file():
            nm_low = sfx_path.name.lower()
            is_tr_whoosh = any(k in nm_low for k in ("whoosh", "swish", "swoosh", "transition"))
            cal_g = _calibrate_sfx_gain_db(
                sfx_path,
                e.get("gain_db"),
                category="transition" if is_tr_whoosh else None,
                default_gain_db=whoosh_gain if is_tr_whoosh else None,
            )
            t_play = t_raw
            if is_tr_whoosh and e_seg_start > 0.01 and abs(at_rel) <= 0.05:
                pk_s = float(_sfx_audio_stats(sfx_path).get("peak_sec", 0.10))
                t_play = max(0.0, t_raw - pk_s)
                explicit_tr_times.append(t_raw)
            elif is_tr_whoosh:
                explicit_tr_times.append(t_raw)
            sfx_list.append((sfx_path, t_play, cal_g))
            log_fx.append((t_play, f"SFX {sfx_path.name} ({cal_g:+.1f}dB)"))

    # Auto-attach peak-aligned, varied transition SFX to physical/kinetic transitions
    whoosh_name = str(plan_tuning.get("whoosh_sfx", "whoosh.wav"))
    custom_whoosh = (
        isinstance(plan.get("tuning"), dict)
        and plan["tuning"].get("whoosh_sfx") is not None
        and str(plan["tuning"].get("whoosh_sfx")).strip().lower() not in ("", "whoosh.wav", "default")
    )
    primary_whoosh = _redirect_broken_sfx(resolve_sfx_asset(whoosh_name, pack=sfx_pack))
    last_tr_sfx_name = ""
    tr_rot_idx = 0

    for i in range(1, len(segs)):
        s = segs[i]
        eff_tr = str(s.get("_tr") if s.get("_tr", "cut") != "cut" else (s.get("_flash") or "cut")).lower()
        if eff_tr not in _WHOOSH_TRANSITIONS:
            continue
        t_tr = float(starts[i])
        already_has_whoosh = any(abs(ex_t - t_tr) <= 0.40 for ex_t in explicit_tr_times) or any(
            abs(existing_t - t_tr) <= 0.35
            and any(w in existing_p.name.lower() for w in ("whoosh", "swish", "swoosh", "transition"))
            for existing_p, existing_t, _ in sfx_list
        )
        if already_has_whoosh:
            continue

        if custom_whoosh and primary_whoosh.is_file():
            pool_names = [primary_whoosh.name]
        elif eff_tr == "split_reveal":
            pool_names = ["w42_swoosh.wav", "sn_swish_1.wav", "w42_transition.wav"]
        elif eff_tr in ("zoom_burst", "zoom_in", "zoom_out", "zoom_blur", "flash_zoom", "warm_flash", "warm_flash_punch"):
            pool_names = ["w42_transition.wav", "whoosh.wav", "sn_swish_1.wav"]
        else:
            pool_names = ["whoosh.wav", "sn_swish_1.wav", "kn20228_swoosh.wav"]

        pool_paths = [
            _redirect_broken_sfx(resolve_sfx_asset(nm, pack=sfx_pack))
            for nm in pool_names
        ]
        pool_paths = [p for p in pool_paths if p.is_file()]
        if not pool_paths and primary_whoosh.is_file():
            pool_paths = [primary_whoosh]
        if not pool_paths:
            continue

        chosen = pool_paths[tr_rot_idx % len(pool_paths)]
        if len(pool_paths) > 1 and chosen.name == last_tr_sfx_name:
            tr_rot_idx += 1
            chosen = pool_paths[tr_rot_idx % len(pool_paths)]
        tr_rot_idx += 1
        last_tr_sfx_name = chosen.name

        pk_s = float(_sfx_audio_stats(chosen).get("peak_sec", 0.10))
        t_play = max(0.0, t_tr - pk_s)
        cal_tr_g = _calibrate_sfx_gain_db(chosen, None, category="transition", default_gain_db=whoosh_gain)
        sfx_list.append((chosen, t_play, cal_tr_g))
        log_fx.append((t_play, f"SFX {chosen.name} ({cal_tr_g:+.1f}dB, transition@{t_tr:.2f}s)"))

    if layouts is not None and hasattr(layouts, "collect_layout_auto_sfx"):
        for fname, t_auto, g_auto in layouts.collect_layout_auto_sfx(plan, seg_start):
            sfx_path = _redirect_broken_sfx(resolve_sfx_asset(fname, pack=sfx_pack))
            if sfx_path.is_file():
                cal_auto_g = _calibrate_sfx_gain_db(sfx_path, g_auto)
                sfx_list.append((sfx_path, float(t_auto), cal_auto_g))
                log_fx.append((float(t_auto), f"SFX {sfx_path.name} ({cal_auto_g:+.1f}dB)"))

    # Stem 0 (`[adlg]`): Dialogue + subtle continuous -48 dBFS pink room-tone floor (+ optional music bed)
    fc.append(
        f"anoisesrc=d={total:.4f}:c=pink:r=48000:a=0.00398:seed=1,"
        f"highpass=f=80,lowpass=f=1800,aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo[rtone]"
    )
    fc.append(f"{au}aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo[adlg_raw]")
    dlg_inputs = ["[adlg_raw]", "[rtone]"]

    music_spec = (plan.get("audio") or {}).get("music")
    if music_spec:
        music_path = resolve(str(music_spec))
        if music_path.is_file():
            music_gain = float((plan.get("audio") or {}).get("music_gain_db", -18.0))
            inputs += ["-stream_loop", "-1", "-t", f"{total:.4f}", "-i", music_path]
            fc.append(
                f"[{n}:a]aresample=48000,aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo,"
                f"volume={music_gain:+.1f}dB,afade=t=in:d=0.5,afade=t=out:st={max(0.0, total - 0.8):.4f}:d=0.8[mbd]"
            )
            dlg_inputs.append("[mbd]")
            n += 1

    fc.append(
        f"{''.join(dlg_inputs)}amix=inputs={len(dlg_inputs)}:normalize=0:dropout_transition=0:duration=first,"
        f"atrim=0:{total:.4f},asetpts=PTS-STARTPTS[adlg]"
    )

    # Deduplicate overlapping identical SFX (keeping the first/higher-priority entry within 80ms)
    if sfx_list:
        deduped_sfx: List[Tuple[Path, float, float]] = []
        for fpath, t_sfx, g_sfx in sfx_list:
            fname_low = _redirect_broken_sfx(fpath).name.lower()
            t_val = float(t_sfx)
            if any(
                _redirect_broken_sfx(kf).name.lower() == fname_low and abs(t_val - float(kt)) <= 0.08
                for kf, kt, _ in deduped_sfx
            ):
                continue
            deduped_sfx.append((fpath, t_val, float(g_sfx)))
        sfx_list = deduped_sfx

    # Stem 1 (`[asfx]`): 32-bit float peak-calibrated SFX stem (unity mono->stereo pan, zero pcm_s16le pre-clipping)
    if sfx_list:
        fc.append(
            f"anullsrc=r=48000:cl=stereo,atrim=0:{total:.4f},"
            f"aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo[sfx_base]"
        )
        sfx_nodes: List[str] = ["[sfx_base]"]
        for k, (fpath, t, g) in enumerate(sfx_list):
            fpath_clean = _redirect_broken_sfx(fpath)
            st_sfx = _sfx_audio_stats(fpath_clean)
            pan_f = "pan=stereo|c0=c0|c1=c0" if int(st_sfx.get("channels", 2)) == 1 else "aformat=channel_layouts=stereo"
            eff_g = float(g) + float(st_sfx.get("norm_db", 0.0))
            inputs += ["-i", fpath_clean]
            ms = max(0, int(round(float(t) * 1000.0)))
            fc.append(
                f"[{n}:a]aresample=48000,aformat=sample_fmts=fltp:sample_rates=48000,{pan_f},"
                f"volume={eff_g:+.2f}dB,adelay={ms}|{ms}[sx{k}]"
            )
            sfx_nodes.append(f"[sx{k}]")
            n += 1
        fc.append(
            f"{''.join(sfx_nodes)}amix=inputs={len(sfx_nodes)}:normalize=0:dropout_transition=0:duration=first,"
            f"atrim=0:{total:.4f},asetpts=PTS-STARTPTS[asfx]"
        )
    else:
        fc.append(
            f"anullsrc=r=48000:cl=stereo,atrim=0:{total:.4f},"
            f"aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo,asetpts=PTS-STARTPTS[asfx]"
        )

    # 4) Dual-stem float mix pass, dialogue-only EBU R128 loudnorm measurement, then post-loudnorm SFX float summing
    audio_cfg = plan.get("audio") or {}
    raw_loud = audio_cfg.get("loudness")
    if isinstance(raw_loud, str):
        raw_loud = re.sub(r"(?i)lufs$", "", raw_loud.strip())
    loud = float(raw_loud) if raw_loud not in (None, "") else DEFAULT_LOUDNESS_LUFS
    raw_mg = audio_cfg.get("gain_db", 0.0)
    if isinstance(raw_mg, str):
        raw_mg = re.sub(r"(?i)db$", "", raw_mg.strip())
    master_gain_db = float(raw_mg or 0.0)
    mix = cache / "mix.mkv"
    fc.append(f"{v}null[vout]")
    subs_cfg = plan.get("subs")
    subs_enabled = (
        bool(subs_cfg)
        and not (isinstance(subs_cfg, str) and subs_cfg.strip().lower() in ("off", "none", "false", "0"))
        and not (isinstance(subs_cfg, dict) and str(subs_cfg.get("mode", "")).strip().lower() in ("off", "none"))
    )
    has_flashcards = _plan_has_flashcards(plan, segs)
    if page_cfg:
        subs_enabled = False
        has_flashcards = False
    ffmpeg_encode(
        *inputs,
        "-filter_complex", ";".join(fc),
        "-map", "[vout]",
        "-map", "[adlg]",
        "-map", "[asfx]",
        "-r", str(fps),
        "-c:v", "libx264",
        "-preset", "ultrafast" if a.draft else "veryfast",
        "-crf", "23" if a.draft else "18",
        "-profile:v", "high",
        "-pix_fmt", "yuv420p",
        "-c:a", "pcm_f32le",
        mix,
        draft=bool(a.draft),
        bitrate_kbps=max(final_bitrate_kbps(total) * 3, 30000) if (subs_enabled or page_cfg or has_flashcards) else final_bitrate_kbps(total),
    )
    _say("render", "Picture and sound mixed; measuring dialogue loudness")
    r = ffmpeg(
        "-i", mix,
        "-map", "0:a:0",
        "-af", f"loudnorm=I={loud}:TP=-1.5:LRA=11:print_format=json",
        "-f", "null", "-",
    )
    m_json = re.search(r"\{[^{}]*\"input_i\"[^{}]*\}", r.stderr)
    meas = json.loads(m_json.group(0)) if m_json else {}
    vol_extra = f",volume={master_gain_db:+.2f}dB" if abs(master_gain_db) >= 0.05 else ""
    in_i_str = str(meas.get("input_i", "-inf")).strip().lower()
    if not meas or "inf" in in_i_str or "nan" in in_i_str:
        dlg_ln_chain = f"loudnorm=I={loud}:TP=-1.5:LRA=11{vol_extra}"
    else:
        dlg_ln_chain = (
            f"loudnorm=I={loud}:TP=-1.5:LRA=11:measured_I={meas['input_i']}:measured_TP={meas['input_tp']}:"
            f"measured_LRA={meas['input_lra']}:measured_thresh={meas['input_thresh']}:"
            f"offset={meas['target_offset']}:linear=true{vol_extra}"
        )
    master_audio_fc = (
        f"[0:a:0]{dlg_ln_chain}[dlg_ln];"
        f"[dlg_ln][0:a:1]amix=inputs=2:weights=1 1:normalize=0:dropout_transition=0:duration=first,"
        f"aresample=192000,alimiter=limit=0.76:attack=5:release=50:asc=true:level=false:latency=true,"
        f"aresample=48000,aformat=channel_layouts=stereo[aout]"
    )
    plan["_plan_dir"] = str(plan_path.parent)
    plan["_plan_path"] = str(plan_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    # Stage 3b: Bilingual Flashcards (9:16 placement on clean `mix` BEFORE subtitles so captions dodge cards)
    fc_cards: List[Dict[str, Any]] = []
    if has_flashcards:
        try:
            import flashcard_placer

            fc_meta = flashcard_placer.place_timeline_flashcards(
                plan,
                segs,
                starts,
                durs,
                total,
                mix,
                W,
                H,
                plan_path.parent / "overlays",
                is_square=False,
            )
            if isinstance(fc_meta, dict):
                fc_cards = list(fc_meta.get("cards") or [])
            if fc_cards:
                plan["_flashcard_boxes"] = fc_cards
                for c_item in fc_cards:
                    c_lbl = str(c_item.get("en") or c_item.get("text") or "card")[:40]
                    c_st = float(c_item.get("start", 0.0))
                    c_en = float(c_item.get("end", c_st))
                    log_fx.append((c_st, f"CARD {c_lbl} ({max(0.0, c_en - c_st):.2f}s)"))
        except Exception as exc:  # noqa: BLE001
            print(f"  [CARDS] Could not place 9:16 flashcards: {exc}")
            fc_cards = []
    valid_fc_cards = _valid_flashcards(fc_cards)

    # Subtitles (`#subs ...`): measure picture contrast on un-subtitled `mix`, dodging active flashcards, then composite at canvas fps.
    sub_list = None
    sub_band_y0 = 0
    if subs_enabled:
        try:
            import subtitles
            sub_list, sub_band_y0 = _call_build_overlay(
                subtitles,
                plan,
                segs,
                starts,
                total,
                W,
                H,
                WORK,
                srt_path=out_path.with_suffix(".srt"),
                video=mix,
                report_dir=plan_path.parent,
                fps=fps,
                subdir="_subs",
                plan_path=plan_path,
            )
        except Exception as exc:  # noqa: BLE001
            print(f"  [SUBS] Could not build subtitles: {exc}")
            sub_list = None

    if page_cfg and page_geo is not None and scroll_page is not None:
        page_video = None
        try:
            built = scroll_page.build(
                plan, segs, starts, total, W, FULL_H, fps, WORK,
                report_dir=plan_path.parent,
                srt_path=out_path.with_suffix(".srt"),
                draft=bool(a.draft),
            )
            page_video = built[0] if built else None
        except Exception as exc:  # noqa: BLE001
            print(f"  [PAGE] Could not build scrolling page: {exc}")
        bar_h, page_h = page_geo["bar_h"], page_geo["below_h"]
        if page_video:
            page_clock = (
                f"[0:v:0]fps={fps},settb=1/{fps},setpts=N[picture];"
                f"[1:v]fps={fps},settb=1/{fps},setpts=N[page];"
            )
            if bar_h:
                page_fc = page_clock + (
                    f"[page]split[pa][pb];[pa]crop={W}:{bar_h}:0:0[pbar];[pb]crop={W}:{page_h}:0:{bar_h}[ppage];"
                    f"[pbar][picture][ppage]vstack=inputs=3,format=yuv420p[vsub];"
                )
            else:
                page_fc = page_clock + "[picture][page]vstack=inputs=2,format=yuv420p[vsub];"
            page_inputs: List[Any] = ["-i", mix, "-i", str(page_video)]
        else:
            page_fc = f"[0:v:0]pad={W}:{FULL_H}:0:{bar_h}:color=white,format=yuv420p[vsub];"
            page_inputs = ["-i", mix]
        ffmpeg_encode(
            *page_inputs,
            "-filter_complex", page_fc + master_audio_fc,
            "-map", "[vsub]", "-map", "[aout]",
            "-r", str(fps),
            "-c:v", "libx264", "-preset", "ultrafast" if a.draft else "veryfast",
            "-crf", "23" if a.draft else "18", "-profile:v", "high", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-ac", "2", "-b:a", "192k", "-movflags", "+faststart", out_path,
            draft=bool(a.draft),
            bitrate_kbps=final_bitrate_kbps(total),
            hw_decode=False,  # CPU decode prevents green vstack frames on Apple VideoToolbox
        )
    elif sub_list or valid_fc_cards:
        comp_inputs, comp_fc, _ = _build_overlay_composite_graph(
            mix,
            valid_fc_cards,
            sub_list,
            sub_band_y0,
            fps,
            extra_fc=master_audio_fc,
        )
        ffmpeg_encode(
            *comp_inputs,
            "-filter_complex", comp_fc,
            "-map", "[vsub]", "-map", "[aout]",
            "-r", str(fps),
            "-c:v", "libx264", "-preset", "ultrafast" if a.draft else "veryfast",
            "-crf", "23" if a.draft else "18", "-profile:v", "high", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-ac", "2", "-b:a", "192k", "-movflags", "+faststart", out_path,
            draft=bool(a.draft),
            bitrate_kbps=final_bitrate_kbps(total),
        )
    else:
        ffmpeg(
            "-i", mix,
            "-filter_complex", master_audio_fc,
            "-map", "0:v:0", "-map", "[aout]",
            "-c:v", "copy",
            "-c:a", "aac", "-ac", "2", "-b:a", "192k",
            "-movflags", "+faststart", out_path,
        )

    # 5) Human-readable timeline log
    lines = [f"{out_path.name}  {W}x{FULL_H} @ {fps} fps  length {tc(total)}"]
    if plan.get("concept_brief"):
        lines.append(f"Concept Brief    : {plan['concept_brief']}")
    if plan.get("characters"):
        lines.append(f"Characters       : {plan['characters']}")
    for k_sc in sorted(k for k in plan.keys() if str(k).startswith("scene_")):
        lines.append(f"{k_sc.upper().replace('_', ' '):<17}: {plan[k_sc]}")
    lines += ["", "CUTS (Timecode | Seg | Clip | In-Out | Scene & Shot | Speaker | Zoom/Trans | Dialogue)"]
    for i, s in enumerate(segs):
        z = s.get("zoom", 1.0)
        z_st = f":{s['zoom_style']}" if s.get("zoom_style") else ""
        z_at = f"@{s['zoom_at']}s" if s.get("zoom_at") is not None else ""
        zt = (
            f" zoom {z}" + (f"->{s['zoom_to']}" if "zoom_to" in s else "") + z_st + z_at
            if z != 1.0 or "zoom_to" in s or z_st
            else ""
        )
        tr_disp = s.get("_tr_label") or s["_tr"]
        trn = f" [{tr_disp} {s['_d']:.2f}s]" if s["_tr"] != "cut" else (f" [{s['_flash']}]" if s.get("_flash") else "")
        sc_tag = s.get("scene") or ""
        sh_tag = s.get("shot") or ""
        sp_tag = s.get("speaker") or ""
        meta_parts = [p for p in (sc_tag, sh_tag, f"Speaker: {sp_tag}" if sp_tag else "") if p]
        meta_str = f"  [{', '.join(meta_parts)}]" if meta_parts else ""
        lines.append(
            f"{tc(starts[i])}-{tc(starts[i] + durs[i])}  {s['id']:<5} {Path(str(s['src'])).name} "
            f"{float(s['start']):.2f}-{float(s['end']):.2f}{meta_str}{zt}{trn}  {s.get('note', '')}".rstrip()
        )
    if log_fx:
        lines += ["", "EFFECTS"] + [f"{tc(t)}  {txt}" for t, txt in sorted(log_fx)]
    tl = out_path.with_suffix(".timeline.txt")
    tl.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
    print(f"\nDone: {out_path}")

    # 5b) 1:1 Square Meta Ads version (`SQ_<Name>.mp4`) — still Stage 4 Editing
    # Always stitch directly from the completed 9:16 reel (`out_path`): split into 2 layers,
    # center-crop bottom layer to 1:1 with ~7% (5–10%) Gaussian blur, and place the full 9:16
    # reel centered on top. Never generate separate 1:1 images, flashcards, timestamps, or subtitles.
    if a.square or plan.get("square") or (plan.get("canvas") or {}).get("square"):
        sq_out = square_job_output(out_path)
        sq_sz = even(max(W, 1080))
        print(f"Rendering 1:1 Square version -> {sq_out} ...")
        render_square_1x1(
            out_path,
            sq_out,
            sq_size=sq_sz,
            blur_sigma=a.blur_sigma,
            draft=bool(a.draft),
            fps=fps,
        )
        print(f"Done (1:1 Square): {sq_out}")

    try:
        import zeno_tracker as _zt
        _vid_sec = sum(max(0.0, float(s.get("end", 0.0)) - float(s.get("start", 0.0))) for s in (plan.get("segments") or []))
        _zt.record_rendered(
            plan_path.parent,
            plan_path,
            plan,
            output_mp4=out_path,
            video_seconds=_vid_sec,
            has_square=bool(a.square or plan.get("square") or (plan.get("canvas") or {}).get("square")),
            has_premiere=not getattr(a, "no_premiere", False),
        )
    except BaseException:
        pass

    # Transition Stage 4 (Editing) -> Stage 5 (Mastering: Premiere export, Deep QC & delivery)
    _s4_stack.close()
    _s5_stack = contextlib.ExitStack()
    _render_q_stack.callback(_s5_stack.close)
    _s5_stack.enter_context(_stage_slot("stage5_mastering", project_dir=plan_path.parent, video_name=_q_vname))

    # 5c) Adobe Premiere Pro (.prproj + .xml) export
    if not getattr(a, "no_premiere", False):
        try:
            pr_res = _export_premiere(
                plan=plan,
                plan_path=plan_path,
                out_dir=out_path.parent,
                work_dir=plan_path.parent,
                fps=fps,
                width=W,
                height=H,
            )
            if pr_res:
                print(f"Done (Premiere .prproj): {pr_res['prproj']}")
                print(f"Done (Premiere .xml):    {pr_res['xml']}")
        except Exception as exc:
            print(f"[WARN] Premiere Pro export skipped: {exc}")

    # 6) Optional post-render Deep QC verification
    if a.verify and verify is not None and hasattr(verify, "run_verification"):
        print()

        class _Tee(io.StringIO):
            def write(self, text: str) -> int:
                sys.__stdout__.write(text)
                return super().write(text)

        _buf = _Tee()
        eff_target_lufs = round(loud + master_gain_db, 2)
        _ver_kw: Dict[str, Any] = {}
        try:
            _ver_params = inspect.signature(verify.run_verification).parameters
            if "target_lufs" in _ver_params or any(
                p.kind == inspect.Parameter.VAR_KEYWORD for p in _ver_params.values()
            ):
                _ver_kw["target_lufs"] = eff_target_lufs
        except (TypeError, ValueError):
            pass
        with contextlib.redirect_stdout(_buf):
            ok = verify.run_verification(plan_path, **_ver_kw)
        try:
            (plan_path.parent / "verify_report.json").write_text(
                json.dumps({"ok": bool(ok), "text": _buf.getvalue()[-60000:]}, ensure_ascii=False),
                encoding="utf-8",
            )
        except Exception:
            pass
        try:
            import zeno_tracker as _zt
            _zt.record_stage(
                plan_path.parent,
                "qc_passed" if ok else "qc_failed",
                stage="qc",
                error_category="" if ok else "qc_plan_block",
                error_message="" if ok else _extract_qc_failure_summary(_buf.getvalue()),
                passed=bool(ok),
            )
        except BaseException:
            pass
        if not ok:
            sys.exit(1)

    # 6b) Automatic cleanup of render segment cache and transient project scratch
    try:
        import clean
        clean_res = clean.cleanup_after_job(project_dir=plan_path.parent)
        try:
            import job_report
            job_report.record_cleanup(plan_path.parent, clean_res)
        except Exception:
            pass
    except Exception:
        pass

    # 6c) Optional automatic delivery to ~/Downloads/Zeno Outs/<FolderName>/
    if getattr(a, "deliver", False):
        try:
            import deliver
            deliv = deliver.deliver_to_downloads(plan_path, name=a.name)
            print(f"Delivered to Downloads: {deliv['folder']} ({len(deliv['files'])} files)")
        except Exception as exc:
            print(f"[WARN] Delivery to Downloads failed: {exc}")

    try:
        job_done()
    except Exception:
        pass


if __name__ == "__main__":
    main()
