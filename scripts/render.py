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
    DEFAULT_SFX,
    MARKS_DIR,
    SFX_DIR,
    WORK,
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


def even(x: float) -> int:
    return int(round(x / 2)) * 2


def render_segment(seg: Dict[str, Any], W: int, H: int, fps: int, out: Path, draft: bool) -> float:
    """Fallback single-layout segment renderer."""
    dur = float(seg["end"]) - float(seg["start"])
    frames = max(1, round(dur * fps))
    exact = frames / fps
    z0 = float(seg.get("zoom", 1.0))
    z1 = float(seg.get("zoom_to", z0))
    fx, fy = seg.get("focus", [0.5, 0.5])
    vf = [f"scale={W}:{H}:force_original_aspect_ratio=increase", f"crop={W}:{H}"]
    if z0 != 1.0 or z1 != 1.0:
        if z1 == z0:
            vf.append(f"scale={even(W * z0)}:{even(H * z0)}")
        else:  # eased push-in, evaluated per frame
            p = f"(1-cos(PI*min(t/{exact:.4f},1)))/2"
            z = f"({z0}+({z1}-{z0})*{p})"
            vf.append(f"scale=w='trunc({W}*{z}/2)*2':h='trunc({H}*{z}/2)*2':eval=frame")
        vf.append(f"crop={W}:{H}:x='(iw-{W})*{fx}':y='(ih-{H})*{fy}'")
    vf += ["setsar=1", f"fps={fps}", "format=yuv420p"]
    af = [
        "aresample=48000",
        "aformat=channel_layouts=stereo",
        "apad",
        f"atrim=0:{exact:.6f}",
        f"afade=t=in:d={EDGE_FADE}",
        f"afade=t=out:st={exact - EDGE_FADE:.6f}:d={EDGE_FADE}",
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
    a = ap.parse_args()

    plan_path, plan = _load_plan(a.plan)
    if a.preset:
        plan["preset"] = a.preset
        if a.preset != "auto" and a.preset != "hybrid":
            for s in plan.get("segments", []):
                s["layout"] = a.preset
    if a.mark_pack:
        plan["mark_pack"] = a.mark_pack

    segs = plan["segments"]
    first_src = resolve(segs[0]["src"])
    if first_src.is_file():
        first = probe(first_src)
    else:
        first_stem = Path(str(segs[0]["src"])).stem
        info_cache = WORK / first_stem / "info.json"
        first = read_json(info_cache) if info_cache.is_file() else {"width": 720, "height": 1280, "fps": 24}

    canvas = plan.get("canvas", {})
    W = even(canvas.get("width", first.get("width") or 720))
    H = even(canvas.get("height", first.get("height") or 1280))
    fps = int(canvas.get("fps", round(first.get("fps") or 24)))
    out_path = resolve(plan.get("output", f"output/{plan_path.parent.name}.mp4"))
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
        src_resolved = resolve(s["src"])
        mtime = src_resolved.stat().st_mtime if src_resolved.is_file() else 0.0
        lay = s.get("layout", "single")
        key_items = [
            s["src"], s["start"], s["end"], s.get("zoom"), s.get("zoom_to"), s.get("focus"),
            W, H, fps, a.draft, mtime,
        ]
        if lay and lay != "single":
            key_items.extend([lay, s.get("peer"), s.get("chat"), s.get("slot")])
        key = json.dumps(key_items, sort_keys=True)
        f = cache / f"{hashlib.md5(key.encode()).hexdigest()[:16]}.mkv"

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

    # 2) output timeline (transitions overlap neighbouring segments)
    starts: List[float] = []
    total = 0.0
    for i, s in enumerate(segs):
        tr = s.get("transition", "cut") if i else "cut"
        s.pop("_flash", None)
        if tr != "cut" and load_overlay_transition(tr) is not None:
            s["_flash"] = tr          # hard cut; the flash is drawn over it later
            tr = "cut"
        d = min(float(s.get("transition_duration", 0.3)), durs[i] / 2, durs[i - 1] / 2) if tr != "cut" else 0.0
        s["_tr"], s["_d"] = tr, d
        if s.get("_flash"):
            s["_flash_at"] = total
        starts.append(total - d)
        total += durs[i] - d
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
    for i in range(1, len(segs)):
        s = segs[i]
        nv, na = f"[v{i}]", f"[a{i}]"
        if s["_tr"] == "cut":
            fc.append(f"{v}{au}[sv{i}][sa{i}]concat=n=2:v=1:a=1{nv}{na}")
            acc += durs[i]
        else:
            fc.append(f"{v}fps={fps}[xa{i}]")
            fc.append(f"[sv{i}]fps={fps}[xb{i}]")
            v = f"[xa{i}]"
            fc.append(f"{v}[xb{i}]xfade=transition={s['_tr']}:duration={s['_d']:.3f}:offset={acc - s['_d']:.4f}{nv}")
            fc.append(f"{au}[sa{i}]acrossfade=d={s['_d']:.3f}{na}")
            acc += durs[i] - s["_d"]
        v, au = nv, na

    # 2b) overlay transitions (warm flash) on top of the hard cuts
    flash_chain: List[str] = []
    for s in segs:
        if s.get("_flash"):
            spec = load_overlay_transition(s["_flash"])
            swap = int(s.get("transition_swap", spec.get("swap_frame", 5)))
            flash_chain += flash_filters(spec, s["_flash_at"], swap)
    if flash_chain:
        fc.append(f"{v}{','.join(flash_chain)}[vflash]")
        v = "[vflash]"

    # 3) marks (✓ / ✗) and sound effects
    n = len(files)
    sfx_list: List[Tuple[Path, float, float]] = []
    log_fx: List[Tuple[float, str]] = []
    default_pack = plan.get("mark_pack") or "default"
    sfx_pack = plan.get("sfx_pack") or "default"

    for k, m in enumerate(plan.get("marks", [])):
        mtype = _normalize_mark_type(m.get("type", "right"))
        t = seg_start[m["segment"]] + float(m.get("at", 0.0))
        dur = float(m.get("duration", 1.3))
        size = even(min(W, H) * float(m.get("size", 0.22)))
        target_seg = seg_by_id.get(m["segment"])

        if layouts is not None and hasattr(layouts, "place_mark_for_segment"):
            x, y = layouts.place_mark_for_segment(m, target_seg, W, H, size)
        else:
            x, y = place(m.get("position", "center"), W, H, size)

        style_key = m.get("style") or m.get("pack") or default_pack
        png = resolve_mark_asset(mtype, style=style_key)
        if not png.is_file():
            png = MARKS_DIR / f"{mtype}.png"

        inputs += ["-loop", "1", "-framerate", str(fps), "-t", f"{dur:.3f}", "-i", png]
        fc.append(
            f"[{n}:v]scale={size}:{size},format=rgba,fade=in:st=0:d=0.12:alpha=1,"
            f"fade=out:st={dur - 0.2:.3f}:d=0.2:alpha=1,setpts=PTS-STARTPTS+{t:.4f}/TB[mk{k}]"
        )
        fc.append(f"{v}[mk{k}]overlay=x={x}:y={y}:eof_action=pass:enable='between(t,{t:.4f},{t + dur:.4f})'[vm{k}]")
        v = f"[vm{k}]"
        n += 1

        if m.get("sfx", "default") is not None and str(m.get("sfx", "default")).lower() != "none":
            sfx_path = resolve_mark_sfx(mtype, sfx=m.get("sfx", "default"), pack=sfx_pack)
            if sfx_path and sfx_path.is_file():
                sfx_list.append((sfx_path, t, float(m.get("sfx_gain_db", -4))))
        log_fx.append((t, f"{mtype.upper()} mark ({m.get('position', 'center')}, {dur:.1f}s)"))

    for e in plan.get("sfx", []):
        t = seg_start[e["segment"]] + float(e.get("at", 0.0))
        sfx_path = resolve_sfx_asset(e["file"], pack=sfx_pack)
        if sfx_path.is_file():
            sfx_list.append((sfx_path, t, float(e.get("gain_db", -8))))
            log_fx.append((t, f"SFX {sfx_path.name}"))

    if layouts is not None and hasattr(layouts, "collect_layout_auto_sfx"):
        for fname, t_auto, g_auto in layouts.collect_layout_auto_sfx(plan, seg_start):
            sfx_path = resolve_sfx_asset(fname, pack=sfx_pack)
            if sfx_path.is_file():
                sfx_list.append((sfx_path, t_auto, g_auto))
                log_fx.append((t_auto, f"SFX {sfx_path.name}"))

    if sfx_list:
        labels = []
        for k, (fpath, t, g) in enumerate(sfx_list):
            inputs += ["-i", fpath]
            ms = int(t * 1000)
            fc.append(f"[{n}:a]aresample=48000,aformat=channel_layouts=stereo,volume={g}dB,adelay={ms}|{ms}[sx{k}]")
            labels.append(f"[sx{k}]")
            n += 1
        fc.append(f"{au}{''.join(labels)}amix=inputs={len(labels) + 1}:normalize=0:duration=first[amx]")
        au = "[amx]"

    # 4) mix pass, then two-pass loudness normalisation into the final file
    loud = plan.get("audio", {}).get("loudness", -14)
    mix = WORK / "_segments" / "mix.mkv"
    fc.append(f"{v}null[vout]")
    fc.append(f"{au}anull[aout]")
    ffmpeg(
        *inputs,
        "-filter_complex", ";".join(fc),
        "-map", "[vout]",
        "-map", "[aout]",
        "-c:v", "libx264",
        "-preset", "ultrafast" if a.draft else "slow",
        "-crf", "23" if a.draft else "18",
        "-profile:v", "high",
        "-pix_fmt", "yuv420p",
        "-c:a", "pcm_s16le",
        mix,
    )
    r = ffmpeg("-i", mix, "-af", f"loudnorm=I={loud}:TP=-1.5:LRA=11:print_format=json", "-f", "null", "-")
    meas = json.loads(re.search(r"\{[^{}]*\"input_i\"[^{}]*\}", r.stderr).group(0))
    ln = (
        f"loudnorm=I={loud}:TP=-1.5:LRA=11:measured_I={meas['input_i']}:measured_TP={meas['input_tp']}:"
        f"measured_LRA={meas['input_lra']}:measured_thresh={meas['input_thresh']}:"
        f"offset={meas['target_offset']}:linear=true,aresample=48000"
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    ffmpeg("-i", mix, "-c:v", "copy", "-af", ln, "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", out_path)

    # 5) human-readable timeline
    lines = [f"{out_path.name}  {W}x{H} @ {fps} fps  length {tc(total)}", "", "CUTS"]
    for i, s in enumerate(segs):
        z = s.get("zoom", 1.0)
        zt = f" zoom {z}" + (f"->{s['zoom_to']}" if "zoom_to" in s else "") if z != 1.0 or "zoom_to" in s else ""
        trn = f" [{s['_tr']} {s['_d']:.2f}s]" if s["_tr"] != "cut" else (f" [{s['_flash']}]" if s.get("_flash") else "")
        lines.append(
            f"{tc(starts[i])}-{tc(starts[i] + durs[i])}  {s['id']:<5} {Path(str(s['src'])).name} "
            f"{float(s['start']):.2f}-{float(s['end']):.2f}{zt}{trn}  {s.get('note', '')}".rstrip()
        )
    if log_fx:
        lines += ["", "EFFECTS"] + [f"{tc(t)}  {txt}" for t, txt in sorted(log_fx)]
    tl = out_path.with_suffix(".timeline.txt")
    tl.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
    print(f"\nDone: {out_path}")

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
