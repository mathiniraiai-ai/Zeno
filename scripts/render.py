"""Render an edit plan (JSON or EDL) into the final broadcast-ready MP4 video.

Usage:
    python3 scripts/render.py work/<project>/plan.edl [--draft] [--verify]
        [--preset auto|single|split_v|split_h|chat_bar|video_call|hybrid]
        [--mark-pack default|glossy|flash|vignette]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import (  # noqa: E402
    DEFAULT_LOUDNESS_LUFS,
    DEFAULT_PEAK_CAP_DB,
    DEFAULT_SFX,
    MARKS_DIR,
    SFX_DIR,
    OUTPUT,
    WORK,
    clip_work_dir,
    _normalize_mark_type,
    ffmpeg,
    probe,
    read_json,
    resolve,
    resolve_mark_asset,
    resolve_mark_sfx,
    resolve_sfx_asset,
    tc,
    write_json,
)

try:
    import edl_parser
except Exception:
    edl_parser = None

try:
    import layouts
except Exception:
    layouts = None

try:
    import verify
except Exception:
    verify = None

EDGE_FADE = 0.012  # tiny audio fade at every cut to avoid clicks
TRANSITIONS_DIR = Path(__file__).resolve().parent.parent / "assets" / "transitions"


def load_overlay_transition(name: str) -> Optional[Dict[str, Any]]:
    """Measured overlay transitions live in assets/transitions/<name>.json (hard cut + a colour flash on top)."""
    path = TRANSITIONS_DIR / f"{name}.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None


def flash_filters(spec: Dict[str, Any], join_t: float, swap_frame: int) -> List[str]:
    """Screen-blend flash around a hard cut at `join_t`: one lutrgb per flash frame, switched on by time."""
    fps = float(spec.get("fps", 24))
    out = []
    for k, fr in enumerate(spec["frames"]):
        t0 = join_t + (k - swap_frame) / fps
        t1 = t0 + 1.0 / fps - 0.0005
        r, g, b = fr["screen_rgb"]
        expr = lambda c: f"val+(255-val)*{c}/255"
        out.append(f"lutrgb=r='{expr(r)}':g='{expr(g)}':b='{expr(b)}':enable='between(t,{t0:.4f},{t1:.4f})'")
    return out


_META_TRANSITIONS: Dict[str, Dict[str, Any]] = {
    "whip_left": {
        "xfade": "smoothleft",
        "default_dur": 0.22,
        "post": lambda jt, fps=24: [f"dblur=angle=0:radius=28:enable='between(t,{max(0.0, jt - 0.08):.4f},{jt + 0.08:.4f})'"],
    },
    "whip_right": {
        "xfade": "smoothright",
        "default_dur": 0.22,
        "post": lambda jt, fps=24: [f"dblur=angle=0:radius=28:enable='between(t,{max(0.0, jt - 0.08):.4f},{jt + 0.08:.4f})'"],
    },
    "zoom_burst": {
        "xfade": "zoomin",
        "default_dur": 0.25,
        "post": lambda jt, fps=24: [
            f"dblur=angle=45:radius=18:enable='between(t,{max(0.0, jt - 0.06):.4f},{jt + 0.06:.4f})'",
            f"rgbashift=rh=-6:bh=6:rv=-3:bv=3:edge=smear:enable='between(t,{max(0.0, jt - 0.06):.4f},{jt + 0.06:.4f})'",
        ],
    },
    "flash_zoom": {
        "xfade": "zoomin",
        "default_dur": 0.25,
        "post": lambda jt, fps=24: flash_filters(
            {
                "fps": fps,
                "frames": [
                    {"screen_rgb": [60, 70, 85]},
                    {"screen_rgb": [150, 165, 185]},
                    {"screen_rgb": [230, 240, 255]},
                    {"screen_rgb": [140, 155, 175]},
                    {"screen_rgb": [50, 60, 75]},
                ],
            },
            jt,
            swap_frame=2,
        ),
    },
    "slide_up": {
        "xfade": "smoothup",
        "default_dur": 0.25,
        "post": lambda jt, fps=24: [f"dblur=angle=90:radius=24:enable='between(t,{max(0.0, jt - 0.08):.4f},{jt + 0.08:.4f})'"],
    },
    "dip_to_black": {
        "xfade": "fadeblack",
        "default_dur": 0.30,
    },
    "dip_to_white": {
        "xfade": "fadewhite",
        "default_dur": 0.30,
    },
}


def even(x: float) -> int:
    return int(round(x / 2)) * 2


def render_square_1x1(
    src_mp4: Path,
    out_mp4: Optional[Path] = None,
    sq_size: int = 1080,
    blur_sigma: float = 18.0,
    draft: bool = False,
) -> Path:
    """Convert a 9:16 vertical MP4 into a 1:1 square MP4:
      - Layer 1 (Background): 1:1 center crop from the 9:16 clip with 5-10% Gaussian blur (gblur=sigma=18).
      - Layer 2 (Foreground): Full 9:16 vertical clip scaled to fit height and centered on top.
    """
    src_mp4 = resolve(src_mp4)
    if out_mp4 is None:
        out_mp4 = src_mp4.with_name(f"{src_mp4.stem}_1x1.mp4")
    out_mp4 = Path(out_mp4)
    out_mp4.parent.mkdir(parents=True, exist_ok=True)

    sq = even(sq_size)
    fg_w = even(sq * 9.0 / 16.0)
    bg_h = even(sq * 16.0 / 9.0)
    # 4x downscaled Gaussian blur (16x fewer pixels) -> upscale back to sq x sq: visually identical, 94% less CPU heat
    blur_scale = 4
    sq_small = even(sq // blur_scale)
    bg_h_small = even(bg_h // blur_scale)
    sigma_small = max(1.0, blur_sigma / float(blur_scale))
    fc = (
        f"[0:v]split=2[bg_in][fg_in];"
        f"[bg_in]scale={sq_small}:{bg_h_small}:force_original_aspect_ratio=increase:flags=bilinear,"
        f"crop={sq_small}:{sq_small}:(iw-{sq_small})/2:(ih-{sq_small})/2,"
        f"gblur=sigma={sigma_small:.2f},"
        f"scale={sq}:{sq}:flags=bicubic[bg];"
        f"[fg_in]scale={fg_w}:{sq}:flags=lanczos[fg];"
        f"[bg][fg]overlay=(W-w)/2:(H-h)/2,setsar=1,format=yuv420p[vout]"
    )
    ffmpeg(
        "-i", src_mp4,
        "-filter_complex", fc,
        "-map", "[vout]",
        "-map", "0:a?",
        "-c:v", "libx264",
        "-preset", "ultrafast" if draft else "veryfast",
        "-crf", "23" if draft else "18",
        "-profile:v", "high",
        "-pix_fmt", "yuv420p",
        "-c:a", "copy",
        "-movflags", "+faststart",
        out_mp4,
    )
    return out_mp4


def render_segment(seg: Dict[str, Any], W: int, H: int, fps: int, out: Path, draft: bool) -> float:
    """Fallback single-layout segment renderer with sub-pixel supersampled zoompan and PTS-safe audio."""
    dur = max(0.08, float(seg["end"]) - float(seg["start"]))
    frames = max(1, round(dur * fps))
    exact = frames / fps
    if layouts is not None and hasattr(layouts, "resolve_segment_zoom_spec"):
        z0, z1, zoom_style, zoom_at, zoom_dur = layouts.resolve_segment_zoom_spec(seg, exact)
    else:
        z0 = float(seg.get("zoom", 1.0) or 1.0)
        z1 = float(seg.get("zoom_to", z0) or z0)
        zoom_style = str(seg.get("zoom_style") or ("smooth" if abs(z1 - z0) >= 0.005 else "none"))
        zoom_at = float(seg.get("zoom_at") or 0.0)
        zoom_dur = float(seg.get("zoom_dur") or 0.18)
    fx, fy = seg.get("focus", [0.5, 0.38])
    vf = [
        "setpts=PTS-STARTPTS",
        f"fps={fps}",
        f"scale={W}:{H}:force_original_aspect_ratio=increase:flags=lanczos",
        f"crop={W}:{H}:x='(iw-{W})*{fx:.4f}':y='(ih-{H})*{fy:.4f}'",
    ]
    if layouts is not None and hasattr(layouts, "_build_subpixel_zoom_filter"):
        vf.extend(
            layouts._build_subpixel_zoom_filter(
                slot_w=W,
                slot_h=H,
                fps=fps,
                exact_dur=exact,
                fx=float(fx),
                fy=float(fy),
                z0=z0,
                z1=z1,
                zoom_style=zoom_style,
                zoom_at=zoom_at,
                zoom_dur=zoom_dur,
            )
        )
    vf += ["setsar=1", "format=yuv420p", "tpad=stop_mode=clone:stop=3"]
    seg_loud = float(seg.get("loudness") if seg.get("loudness") is not None else DEFAULT_LOUDNESS_LUFS)
    seg_gain_db = float(seg.get("gain_db") or 0.0)
    af = [
        "aresample=48000",
        "aformat=channel_layouts=stereo",
        "asetpts=PTS-STARTPTS",
        "apad",
        f"atrim=0:{exact:.6f}",
        "asetpts=PTS-STARTPTS",
        "highpass=f=70",
        f"loudnorm=I={seg_loud}:TP={DEFAULT_PEAK_CAP_DB}:LRA=9",
    ]
    if abs(seg_gain_db) >= 0.05:
        af.append(f"volume={seg_gain_db:+.2f}dB")
    af += [
        f"alimiter=limit={DEFAULT_PEAK_CAP_DB}dB:level=false",
        f"afade=t=in:d={EDGE_FADE}",
        f"afade=t=out:st={max(0.0, exact - EDGE_FADE):.6f}:d={EDGE_FADE}",
    ]
    src = resolve(seg["src"])
    has_audio = probe(src)["has_audio"]
    args = ["-ss", f"{float(seg['start']):.3f}", "-t", f"{dur + 0.5:.3f}", "-i", src]
    if not has_audio:
        args += ["-f", "lavfi", "-t", f"{exact:.3f}", "-i", "anullsrc=r=48000:cl=stereo"]
    args += [
        "-map", "0:v:0",
        "-map", "0:a:0" if has_audio else "1:a:0",
        "-vf", ",".join(vf),
        "-af", ",".join(af),
        "-frames:v", str(frames),
        "-c:v", "libx264",
        "-preset", "ultrafast" if draft else "veryfast",
        "-crf", "12",
        "-c:a", "pcm_s16le",
        out,
    ]
    ffmpeg(*args)
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
        if edl_parser is not None and hasattr(edl_parser, "parse_edl"):
            plan = edl_parser.parse_edl(plan_path)
            # Keep sibling plan.json synced
            write_json(plan_path.with_suffix(".json"), plan)
            return plan_path, plan
        json_sibling = plan_path.with_suffix(".json")
        if json_sibling.is_file():
            return plan_path, read_json(json_sibling)
        raise RuntimeError(f"Cannot parse {plan_path}: edl_parser module unavailable and no {json_sibling} found.")
    return plan_path, read_json(plan_path)


def _match_cached_segments_when_offline(
    segs: List[Dict[str, Any]],
    cache_dir: Path,
    fps: int,
) -> Optional[List[Path]]:
    """If raw footage files are offline (e.g. migrated from D:\\ drive), match existing
    rendered segment `.mkv` intermediates in `work/_segments/` by creation order & duration.
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
        exp_dur = round((float(s["end"]) - float(s["start"])) * fps) / fps
        best_i = None
        best_score = 999999.0
        for m_idx, (p, dur, mtime_s) in enumerate(meta):
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


def main() -> None:
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
        help="Override ✓/✗ mark badge pack (default, glossy, flash, vignette)",
    )
    ap.add_argument(
        "--loudness",
        type=float,
        default=None,
        help=f"Override target integrated loudness in LUFS (default: {DEFAULT_LOUDNESS_LUFS} LUFS for Meta Ads/Reels)",
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
        help="Skip automatic Adobe Premiere Pro (.prproj + .premiere.xml) project export",
    )
    a = ap.parse_args()

    # Direct MP4 -> 1:1 Square conversion if a .mp4 path is passed with --square
    if str(a.plan).lower().endswith(".mp4"):
        src_vid = resolve(a.plan)
        sq_out = OUTPUT / f"{src_vid.stem}_1x1.mp4"
        print(f"Rendering 1:1 Square version from {src_vid} -> {sq_out} ...")
        render_square_1x1(src_vid, sq_out, sq_size=1080, blur_sigma=a.blur_sigma, draft=a.draft)
        print(f"Done (1:1 Square): {sq_out}")
        return

    plan_path, plan = _load_plan(a.plan)
    if a.preset:
        plan["preset"] = a.preset
        if a.preset != "auto" and a.preset != "hybrid":
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
    fps = int(canvas.get("fps", round(first.get("fps") or 24)))
    out_path = resolve(plan.get("output") or (OUTPUT / f"{plan_path.parent.name}.mp4"))
    cache = WORK / "_segments"
    cache.mkdir(parents=True, exist_ok=True)

    # Check if raw source clips are offline and pre-rendered cached segments exist
    offline_matched = None
    if not first_src.is_file():
        offline_matched = _match_cached_segments_when_offline(segs, cache, fps)

    # 1) each segment -> normalized intermediate (cached by its settings)
    files: List[Path] = []
    durs: List[float] = []
    for i, s in enumerate(segs):
        s.setdefault("id", f"S{i + 1}")
        lay = s.get("layout", "single")
        if layouts is not None and hasattr(layouts, "build_segment_cache_key"):
            cache_hex = layouts.build_segment_cache_key(s, plan, W, H, fps, a.draft)
        else:
            src_resolved = resolve(s["src"])
            mtime = src_resolved.stat().st_mtime if src_resolved.is_file() else 0.0
            key_items = [
                s["src"], s["start"], s["end"], s.get("zoom"), s.get("zoom_to"),
                s.get("zoom_style"), s.get("zoom_at"), s.get("zoom_dur"), s.get("focus"),
                W, H, fps, a.draft, mtime,
            ]
            if lay and lay != "single":
                key_items.extend([lay, s.get("peer"), s.get("chat"), s.get("slot")])
            cache_hex = hashlib.md5(json.dumps(key_items, sort_keys=True).encode()).hexdigest()[:16]
        f = cache / f"{cache_hex}.mkv"

        if offline_matched is not None and not f.exists():
            f = offline_matched[i]

        if f.exists():
            durs.append(round((float(s["end"]) - float(s["start"])) * fps) / fps)
        else:
            print(f"  segment {s['id']}: {s['src']} {float(s['start']):.2f}-{float(s['end']):.2f} ({lay})")
            if layouts is not None and hasattr(layouts, "render_segment_layout"):
                durs.append(layouts.render_segment_layout(s, plan, W, H, fps, f, a.draft))
            else:
                durs.append(render_segment(s, W, H, fps, f, a.draft))
        files.append(f)

    # 2) output timeline (Zero-Audio-Loss transitions: video uses tpad+xfade+optical polish; audio uses concat=0.00s overlap!)
    starts: List[float] = []
    total = 0.0
    for i, s in enumerate(segs):
        tr = s.get("transition", "cut") if i else "cut"
        if i > 0:
            prev_lay = str(segs[i - 1].get("layout") or "single").lower()
            cur_lay = str(s.get("layout") or "single").lower()
            if prev_lay == cur_lay and cur_lay in ("split_h", "split_v", "left_right", "top_bottom"):
                tr = "cut"
        s.pop("_flash", None)
        s.pop("_meta_post", None)
        if tr != "cut" and load_overlay_transition(tr) is not None:
            s["_flash"] = tr          # hard cut; the flash is drawn over it later
            tr = "cut"
        meta_spec = _META_TRANSITIONS.get(tr)
        def_tr_dur = meta_spec["default_dur"] if meta_spec else 0.25
        d = min(float(s.get("transition_duration", def_tr_dur)), durs[i] / 2, durs[i - 1] / 2) if tr != "cut" else 0.0
        s["_tr"], s["_d"] = tr, d
        if s.get("_flash") or tr != "cut":
            s["_join_at"] = total
        if s.get("_flash"):
            s["_flash_at"] = total
        # Zero-audio-loss: tpad clones k frames on video for xfade while audio is joined via concat with 0.00s overlap!
        starts.append(total)
        total += durs[i]
    seg_start = {s["id"]: starts[i] for i, s in enumerate(segs)}
    seg_by_id = {s["id"]: s for s in segs}

    inputs: List[Any] = []
    fc: List[str] = []
    for i, f in enumerate(files):
        inputs += ["-i", f]
        fc.append(f"[{i}:v]settb=AVTB,setpts=PTS-STARTPTS[sv{i}]")
        fc.append(f"[{i}:a]asetpts=PTS-STARTPTS[sa{i}]")
    v, au = "[sv0]", "[sa0]"
    acc = durs[0]
    xf_audio_dur = 0.045  # 45ms constant-power/triangular audio crossfade (zero drift via apad)
    for i in range(1, len(segs)):
        s = segs[i]
        nv, na = f"[v{i}]", f"[a{i}]"
        if s["_tr"] == "cut":
            fc.append(f"{v}[sv{i}]concat=n=2:v=1:a=0{nv}")
            fc.append(f"{au}apad=pad_dur={xf_audio_dur:.4f}[ap{i}]")
            fc.append(f"[ap{i}][sa{i}]acrossfade=d={xf_audio_dur:.4f}:c1=tri:c2=tri{na}")
            acc += durs[i]
        else:
            tr_name = s["_tr"]
            meta_spec = _META_TRANSITIONS.get(tr_name)
            xfade_tr = meta_spec["xfade"] if meta_spec else tr_name
            k_half = max(2, round(s["_d"] * fps / 2.0))
            d_xfade = (2 * k_half) / float(fps)
            offset_xfade = max(0.0, acc - (k_half / float(fps)))
            fc.append(f"{v}tpad=stop_mode=clone:stop={k_half},fps={fps}[xa{i}]")
            fc.append(f"[sv{i}]tpad=start_mode=clone:start={k_half + 1},fps={fps}[xb{i}]")
            fc.append(f"[xa{i}][xb{i}]xfade=transition={xfade_tr}:duration={d_xfade:.4f}:offset={offset_xfade:.4f}{nv}")
            fc.append(f"{au}apad=pad_dur={xf_audio_dur:.4f}[ap{i}]")
            fc.append(f"[ap{i}][sa{i}]acrossfade=d={xf_audio_dur:.4f}:c1=tri:c2=tri{na}")
            acc += durs[i]
        v, au = nv, na

    # 2b) overlay transitions (warm flash) and Meta Creative optical motion/flash polish around join_t
    flash_chain: List[str] = []
    for s in segs:
        if s.get("_flash"):
            spec = load_overlay_transition(s["_flash"])
            swap = int(s.get("transition_swap", spec.get("swap_frame", 5)))
            flash_chain += flash_filters(spec, s["_flash_at"], swap)
        elif s.get("_tr") in _META_TRANSITIONS:
            meta_spec = _META_TRANSITIONS[s["_tr"]]
            post_fn = meta_spec.get("post")
            if callable(post_fn):
                flash_chain += post_fn(float(s.get("_join_at", 0.0)), fps)
    if flash_chain:
        fc.append(f"{v}{','.join(flash_chain)}[vflash]")
        v = "[vflash]"

    # 3) marks (✓ / ✗ or minimal 2-pulse end-of-sentence red/green overlays) and sound effects
    n = len(files)
    sfx_list: List[Tuple[Path, float, float]] = []
    log_fx: List[Tuple[float, str]] = []
    default_pack = plan.get("mark_pack") or "flash"
    sfx_pack = plan.get("sfx_pack") or "default"

    fullframe_names = {
        "red_flash.png",
        "green_flash.png",
        "w42_red_vignette.png",
        "w42_green_vignette.png",
    }
    strobe_styles = {"flash", "vignette", "kn20228", "w42", "strobe"}

    for k, m in enumerate(plan.get("marks", [])):
        mtype = _normalize_mark_type(m.get("type", "right"))
        t = seg_start[m["segment"]] + float(m.get("at", 0.0))
        dur = float(m.get("duration", 0.56))
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
            # Minimal, smooth "coming and going" 2-pulse flash (~0.56s total, 0.50 max opacity)
            dur = min(dur, 0.60)
            pulse_dur = max(0.24, min(0.30, dur / 2.0))
            pulse_frames = max(5, round(pulse_dur * fps))
            total_2pulse_dur = (2 * pulse_frames) / float(fps)
            ccm = "aa=0.50" if mtype == "wrong" else "rr=0.30:aa=0.50"
            inputs += ["-loop", "1", "-framerate", str(fps), "-t", f"{pulse_frames / float(fps):.4f}", "-i", png]
            fc.append(
                f"[{n}:v]scale={W}:{H},format=rgba,colorchannelmixer={ccm},"
                f"fade=t=in:st=0:d=0.04:alpha=1,fade=t=out:st=0.11:d=0.10:alpha=1,"
                f"loop=loop=1:size={pulse_frames}:start=0,"
                f"setpts=N/({fps}*TB)+{t:.4f}/TB[mk{k}]"
            )
            fc.append(
                f"{v}[mk{k}]overlay=x=0:y=0:eof_action=pass:"
                f"enable='between(t,{t:.4f},{t + total_2pulse_dur:.4f})'[vm{k}]"
            )
            pos_desc = f"minimal-2pulse:{png.name}"
        else:
            inputs += ["-loop", "1", "-framerate", str(fps), "-t", f"{dur:.3f}", "-i", png]
            size = even(min(W, H) * float(m.get("size", 0.22)))
            target_seg = seg_by_id.get(m["segment"])
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
            plan_tuning = style_config.get_tuning(plan=plan) if style_config is not None else _TUNING
            sfx_override = m.get("sfx", "default")
            if str(sfx_override).lower() == "default":
                sfx_override = str(
                    plan.get("error_sfx")
                    or plan_tuning.get("wrong_sfx_primary", "buzzer.wav")
                    if mtype == "wrong"
                    else plan_tuning.get("right_sfx_primary", "correct_ding.wav")
                )
            sfx_path = resolve_mark_sfx(mtype, sfx=sfx_override, pack=sfx_pack)
            default_gain = float(plan_tuning.get("wrong_sfx_gain_db", 4.0) if mtype == "wrong" else plan_tuning.get("right_sfx_gain_db", 2.5))
            raw_gain = m.get("sfx_gain_db")
            gain_db = float(raw_gain) if (raw_gain is not None and float(raw_gain) > -3.5) else default_gain
            if sfx_path and sfx_path.is_file():
                sfx_list.append((sfx_path, t, gain_db))
            # Layer a second punchy SFX for strong error/right feedback when using strobe overlays
            if is_fullframe_strobe:
                layer_name = str(plan_tuning.get("wrong_sfx_layer", "kn20228_wrong_core.mp3") if mtype == "wrong" else plan_tuning.get("right_sfx_layer", "sn_correct.wav"))
                extra_sfx = SFX_DIR / layer_name if layer_name and layer_name.lower() != "none" else None
                if extra_sfx and extra_sfx.is_file() and (not sfx_path or extra_sfx.resolve() != sfx_path.resolve()):
                    sfx_list.append((extra_sfx, t, default_gain if mtype == "wrong" else max(1.0, default_gain - 1.0)))
        log_fx.append((t, f"{mtype.upper()} mark ({pos_desc}, {dur:.2f}s)"))

    for e in plan.get("sfx", []):
        t = seg_start[e["segment"]] + float(e.get("at", 0.0))
        sfx_path = resolve_sfx_asset(e["file"], pack=sfx_pack)
        if sfx_path.is_file():
            raw_g = float(e.get("gain_db", -2.0))
            if "whoosh" in sfx_path.name.lower() and raw_g <= -6.0:
                raw_g = -2.0
            sfx_list.append((sfx_path, t, raw_g))
            log_fx.append((t, f"SFX {sfx_path.name} ({raw_g:+.1f}dB)"))

    # Auto-attach custom whoosh.wav strictly to kinetic/flash transitions (never fade/dip_to_black/dip_to_white/cut)
    _WHOOSH_TRANSITIONS = {
        "whip_left", "whip_right", "flash_zoom", "zoom_burst",
        "slide_up", "smoothleft", "smoothright", "warm_flash",
    }
    plan_tuning = style_config.get_tuning(plan=plan) if style_config is not None else _TUNING
    whoosh_name = str(plan_tuning.get("whoosh_sfx", "whoosh.wav"))
    whoosh_gain = float(plan_tuning.get("whoosh_sfx_gain_db", -2.0))
    whoosh_asset = resolve_sfx_asset(whoosh_name, pack=sfx_pack)
    if whoosh_asset.is_file():
        for i in range(1, len(segs)):
            s = segs[i]
            eff_tr = str(s.get("_flash") or s.get("_tr", "cut")).lower()
            if eff_tr in _WHOOSH_TRANSITIONS:
                t_tr = float(starts[i])
                already_has_whoosh = any(
                    abs(existing_t - t_tr) <= 0.35 and ("whoosh" in existing_p.name.lower() or "swish" in existing_p.name.lower() or "swoosh" in existing_p.name.lower())
                    for existing_p, existing_t, _ in sfx_list
                )
                if not already_has_whoosh:
                    sfx_list.append((whoosh_asset, t_tr, whoosh_gain))
                    log_fx.append((t_tr, f"SFX {whoosh_asset.name} ({whoosh_gain:+.1f}dB, transition)"))

    if layouts is not None and hasattr(layouts, "collect_layout_auto_sfx"):
        for fname, t_auto, g_auto in layouts.collect_layout_auto_sfx(plan, seg_start):
            sfx_path = resolve_sfx_asset(fname, pack=sfx_pack)
            if sfx_path.is_file():
                sfx_list.append((sfx_path, t_auto, g_auto))
                log_fx.append((t_auto, f"SFX {sfx_path.name}"))

    # Subtle continuous -48 dBFS room-tone floor bridge so take-to-take noise floor never drops to digital zero
    fc.append(
        f"anoisesrc=d={total:.4f}:c=pink:r=48000:a=0.00398,"
        f"highpass=f=80,lowpass=f=1800,aformat=channel_layouts=stereo[rtone]"
    )
    mix_inputs = [au, "[rtone]"]

    # Optional style-driven background music bed with -18 dB ducking under speech (off by default)
    music_spec = (plan.get("audio") or {}).get("music")
    if music_spec:
        music_path = resolve(str(music_spec))
        if music_path.is_file():
            music_gain = float((plan.get("audio") or {}).get("music_gain_db", -18.0))
            inputs += ["-stream_loop", "-1", "-t", f"{total:.4f}", "-i", music_path]
            fc.append(
                f"[{n}:a]aresample=48000,aformat=channel_layouts=stereo,"
                f"volume={music_gain:+.1f}dB,afade=t=in:d=0.5,afade=t=out:st={max(0.0, total - 0.8):.4f}:d=0.8[mbd]"
            )
            mix_inputs.append("[mbd]")
            n += 1

    if sfx_list:
        for k, (fpath, t, g) in enumerate(sfx_list):
            inputs += ["-i", fpath]
            ms = int(t * 1000)
            fc.append(f"[{n}:a]aresample=48000,aformat=channel_layouts=stereo,volume={g}dB,adelay={ms}|{ms}[sx{k}]")
            mix_inputs.append(f"[sx{k}]")
            n += 1

    fc.append(f"{''.join(mix_inputs)}amix=inputs={len(mix_inputs)}:normalize=0:duration=first[amx]")
    au = "[amx]"

    # 4) mix pass, then two-pass loudness normalisation into the final file
    loud = float(plan.get("audio", {}).get("loudness", DEFAULT_LOUDNESS_LUFS))
    master_gain_db = float(plan.get("audio", {}).get("gain_db", 0.0) or 0.0)
    mix = WORK / "_segments" / "mix.mkv"
    fc.append(f"{v}null[vout]")
    fc.append(f"{au}anull[aout]")
    ffmpeg(
        *inputs,
        "-filter_complex", ";".join(fc),
        "-map", "[vout]",
        "-map", "[aout]",
        "-c:v", "libx264",
        "-preset", "ultrafast" if a.draft else "veryfast",
        "-crf", "23" if a.draft else "18",
        "-profile:v", "high",
        "-pix_fmt", "yuv420p",
        "-c:a", "pcm_s16le",
        mix,
    )
    r = ffmpeg("-i", mix, "-af", f"loudnorm=I={loud}:TP={DEFAULT_PEAK_CAP_DB}:LRA=11:print_format=json", "-f", "null", "-")
    meas = json.loads(re.search(r"\{[^{}]*\"input_i\"[^{}]*\}", r.stderr).group(0))
    vol_extra = f",volume={master_gain_db:+.2f}dB" if abs(master_gain_db) >= 0.05 else ""
    ln = (
        f"loudnorm=I={loud}:TP={DEFAULT_PEAK_CAP_DB}:LRA=11:measured_I={meas['input_i']}:measured_TP={meas['input_tp']}:"
        f"measured_LRA={meas['input_lra']}:measured_thresh={meas['input_thresh']}:"
        f"offset={meas['target_offset']}:linear=true{vol_extra},alimiter=limit=0.75:level=false,aresample=48000"
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    ffmpeg("-i", mix, "-c:v", "copy", "-af", ln, "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", out_path)

    # 5) human-readable timeline (with mapped Concept Brief, Scene & Character Speakers)
    lines = [f"{out_path.name}  {W}x{H} @ {fps} fps  length {tc(total)}"]
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
        zt = f" zoom {z}" + (f"->{s['zoom_to']}" if "zoom_to" in s else "") + z_st + z_at if z != 1.0 or "zoom_to" in s or z_st else ""
        trn = f" [{s['_tr']} {s['_d']:.2f}s]" if s["_tr"] != "cut" else (f" [{s['_flash']}]" if s.get("_flash") else "")
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

    if not getattr(a, "no_premiere", False):
        try:
            import premiere_export
            pr_res = premiere_export.export_premiere_project(
                plan=plan,
                plan_path=plan_path,
                out_dir=out_path.parent,
                work_dir=plan_path.parent,
                fps=fps,
                width=W,
                height=H,
            )
            print(f"Done (Premiere .prproj): {pr_res['prproj']}")
            print(f"Done (Premiere .xml):    {pr_res['xml']}")
        except Exception as exc:
            print(f"[WARN] Premiere Pro export skipped: {exc}")

    if a.square or plan.get("square") or (plan.get("canvas") or {}).get("square"):
        sq_out = out_path.with_name(f"{out_path.stem}_1x1.mp4")
        print(f"Rendering 1:1 Square version -> {sq_out} ...")
        render_square_1x1(out_path, sq_out, sq_size=max(W, 1080), blur_sigma=a.blur_sigma, draft=a.draft)
        print(f"Done (1:1 Square): {sq_out}")

    # 6) optional post-render verification
    if a.verify and verify is not None and hasattr(verify, "run_verification"):
        print()
        ok = verify.run_verification(plan_path)
        if not ok:
            sys.exit(1)

    # 7) automated per-clip & per-agent token telemetry logging
    try:
        import token_tracker
        rec = token_tracker.finalize_job_telemetry(plan_path, plan, total, out_path)
        token_tracker.print_console_summary(rec)
    except Exception:
        pass


if __name__ == "__main__":
    main()
