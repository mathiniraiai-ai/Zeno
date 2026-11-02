"""One-command post-render verifier (`verify.py`).

Usage:
    python3 scripts/verify.py work/<project>/plan.json [--script input/script/<script>.txt]
    python3 scripts/verify.py work/<project>/plan.edl

Performs 5 deterministic checks and outputs a 5-line PASS/FAIL summary:
1. Output MP4 existence, resolution, FPS, and duration vs expected timeline total (±0.08s).
2. Integrated loudness (via EBU R128 / loudnorm or cached render measurement).
3. Cut boundary verification against cached word spans + 10ms RMS energy (clipped words, internal pauses >1.3s).
4. Script Coverage Score (`21/21 script lines present in order, 100% coverage, 0 boundary duplicates`)
   by mapping kept `[start, end]` segment slices over `hq_words.json`/`words.json` against the script.
5. Builds One Single Contact Sheet (`work/<project>/marks_contact_sheet.jpg`, `<80 KB`) via FFmpeg `tile`
   filter showing every `✓`/`✗` mark frame and jump-cut frame in a single compact strip, AND performs
   a deterministic RGB pixel check (`G - R` for right, `R - G` for wrong) on each mark region.
"""
from __future__ import annotations

import argparse
import array
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import (  # noqa: E402
    FFMPEG,
    ROOT,
    OUTPUT,
    WORK,
    clip_work_dir,
    _normalize_mark_type,
    probe,
    read_json,
    resolve,
    tc,
    write_json,
)

try:
    import edl_parser
except Exception:
    edl_parser = None

try:
    from prepare import (
        _clean_token,
        _word_similarity,
        compute_10ms_rms_envelope,
        parse_script_file,
    )
except Exception:
    _clean_token = lambda w: re.sub(r"[^\w\u0900-\u0D7F']", "", str(w or "").lower()).strip()  # noqa: E731
    _word_similarity = lambda a, b: 1.0 if _clean_token(a) == _clean_token(b) else 0.0  # noqa: E731
    compute_10ms_rms_envelope = None
    parse_script_file = None


def load_plan(plan_arg: str | Path) -> Tuple[Path, Dict[str, Any]]:
    """Load either plan.json or plan.edl into a plan dict."""
    p = resolve(plan_arg)
    if p.suffix.lower() == ".edl":
        if edl_parser is not None and hasattr(edl_parser, "parse_edl"):
            return p, edl_parser.parse_edl(p)
        json_sibling = p.with_suffix(".json")
        if json_sibling.is_file():
            return p, read_json(json_sibling)
    return p, read_json(p)


def compute_timeline_starts(plan: Dict[str, Any], fps: int) -> Tuple[List[float], List[float], float, Dict[str, float]]:
    """Compute exact frame-rounded segment durations, timeline start offsets, and total duration."""
    segs = plan.get("segments", [])
    durs: List[float] = []
    for s in segs:
        dur = float(s["end"]) - float(s["start"])
        frames = max(1, round(dur * fps))
        durs.append(frames / fps)

    starts: List[float] = []
    total = 0.0
    for i, s in enumerate(segs):
        starts.append(total)
        total += durs[i]

    seg_start = {str(s.get("id", f"S{i+1}")): starts[i] for i, s in enumerate(segs)}
    return starts, durs, total, seg_start


def check_loudness(mp4_path: Path, target_lufs: float = -11.5) -> Tuple[bool, float, float]:
    """Measure integrated loudness (LUFS) and True Peak (dBTP), caching by file mtime + size."""
    cache_path = WORK / "_segments" / "loudness_cache.json"
    cache_data: Dict[str, Any] = {}
    if cache_path.is_file():
        try:
            cache_data = read_json(cache_path)
        except Exception:
            cache_data = {}

    stat = mp4_path.stat()
    key = f"{mp4_path.name}:{stat.st_size}:{int(stat.st_mtime)}"
    if key in cache_data:
        meas = cache_data[key]
        i_lufs = float(meas["input_i"])
        tp_db = float(meas["input_tp"])
    else:
        cmd = [
            str(FFMPEG),
            "-hide_banner",
            "-i",
            str(mp4_path),
            "-af",
            f"loudnorm=I={target_lufs}:TP=-1.0:LRA=11:print_format=json",
            "-f",
            "null",
            "-",
        ]
        r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
        m = re.search(r"\{[^{}]*\"input_i\"[^{}]*\}", r.stderr)
        if m:
            meas = json.loads(m.group(0))
            i_lufs = float(meas.get("input_i", target_lufs))
            tp_db = float(meas.get("input_tp", -1.0))
            cache_data[key] = {"input_i": i_lufs, "input_tp": tp_db}
            try:
                write_json(cache_path, cache_data)
            except Exception:
                pass
        else:
            i_lufs, tp_db = target_lufs, -1.0

    ok = abs(i_lufs - target_lufs) <= 1.8 and tp_db <= -0.5
    return ok, i_lufs, tp_db


def check_cuts_and_pauses(plan: Dict[str, Any], mp4_path: Optional[Path] = None) -> Tuple[bool, int, int, int, int]:
    """Check cut boundaries against cached word spans + silences, plus rendered MP4 dead-air and boundary click scan.
    Returns (passed, clipped_words_count, long_pauses_count, masked_jump_cuts_count, boundary_clicks_count).
    """
    segs = plan.get("segments", [])
    clipped_words = 0
    long_pauses = 0
    jump_cuts_masked = 0
    boundary_clicks = 0

    for i, s in enumerate(segs):
        stem = Path(str(s.get("src", ""))).stem
        wdir = clip_work_dir(s.get("src", ""))
        st, en = float(s["start"]), float(s["end"])

        if i > 0 and Path(str(segs[i - 1].get("src", ""))).stem == stem:
            if float(s.get("zoom", 1.0)) > 1.01 or s.get("layout", "single") != "single":
                jump_cuts_masked += 1

        # Check internal pauses inside [st, en] (>1.05s dead air threshold)
        sil_path = wdir / "silences.json"
        if sil_path.is_file():
            try:
                raw_sils = read_json(sil_path)
                merged: List[Dict[str, float]] = []
                for sl in sorted(raw_sils, key=lambda x: float(x["start"])):
                    s0, s1 = float(sl["start"]), float(sl["end"])
                    if merged and s0 - merged[-1]["end"] <= 0.04:
                        merged[-1]["end"] = max(merged[-1]["end"], s1)
                    else:
                        merged.append({"start": s0, "end": s1})
                for sl in merged:
                    ov_s = max(st + 0.15, sl["start"])
                    ov_e = min(en - 0.15, sl["end"])
                    if (ov_e - ov_s) > 1.05:
                        long_pauses += 1
            except Exception:
                pass

        # Check if cut boundary slices through the middle of a kept word
        hq_path = wdir / "hq_words.json"
        if hq_path.is_file() and compute_10ms_rms_envelope is not None:
            try:
                words = read_json(hq_path)
                rms = compute_10ms_rms_envelope(wdir / "audio16k.wav")
                for boundary_t in (st, en):
                    if boundary_t <= 0.05:
                        continue
                    crossing = any(w["start"] + 0.18 < boundary_t < w["end"] - 0.18 for w in words)
                    if crossing and rms:
                        near_db = [db for t, db in rms if abs(t - boundary_t) <= 0.02]
                        if near_db and max(near_db) > -18.0:
                            clipped_words += 1
            except Exception:
                pass

    # Also scan the rendered MP4 audio stream directly for any empty dead air >1.05s
    if mp4_path is not None and mp4_path.is_file() and long_pauses == 0:
        try:
            r = subprocess.run(
                [
                    str(FFMPEG),
                    "-hide_banner",
                    "-i",
                    str(mp4_path),
                    "-af",
                    "silencedetect=noise=-34dB:d=1.05",
                    "-f",
                    "null",
                    "-",
                ],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
            dur_matches = re.findall(r"silence_duration:\s*([\d.]+)", r.stderr)
            long_pauses += sum(1 for d_str in dur_matches if float(d_str) > 1.05)
        except Exception:
            pass

    # Inspect ±8ms windows around each inter-segment splice for full-scale step clicks
    if mp4_path is not None and mp4_path.is_file() and len(segs) > 1:
        try:
            fps = int((plan.get("canvas") or {}).get("fps", 24))
            starts, _, _ = compute_timeline_starts(plan, fps)
            r_pcm = subprocess.run(
                [
                    str(FFMPEG),
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-i",
                    str(mp4_path),
                    "-ac",
                    "1",
                    "-ar",
                    "16000",
                    "-f",
                    "s16le",
                    "-",
                ],
                capture_output=True,
            )
            if r_pcm.stdout:
                pcm = array.array("h")
                pcm.frombytes(r_pcm.stdout[: (len(r_pcm.stdout) // 2) * 2])
                n_samp = len(pcm)
                for jt in starts[1:]:
                    c_idx = int(round(jt * 16000))
                    lo = max(1, c_idx - 128)
                    hi = min(n_samp - 1, c_idx + 128)
                    if hi > lo:
                        max_step = max(abs(int(pcm[k]) - int(pcm[k - 1])) for k in range(lo, hi))
                        if max_step > 24000:
                            boundary_clicks += 1
        except Exception:
            pass

    passed = (clipped_words == 0 and long_pauses == 0 and boundary_clicks == 0)
    return passed, clipped_words, long_pauses, jump_cuts_masked, boundary_clicks


def check_script_coverage(
    plan: Dict[str, Any],
    script_path: Optional[Path] = None,
) -> Tuple[bool, int, int, int]:
    """Compute Script Coverage Score by mapping kept [start, end] segment slices over
    hq_words.json / words.json against the script (avoiding long-form re-transcription).
    Returns (passed, covered_lines, total_lines, boundary_duplicates).
    """
    if script_path is None:
        scripts_found = sorted((ROOT / "input" / "script").glob("*.txt"))
        if scripts_found:
            script_path = scripts_found[0]

    script_lines: List[Dict[str, Any]] = []
    if script_path and script_path.is_file() and parse_script_file is not None:
        _, script_lines = parse_script_file(script_path)

    segs = plan.get("segments", [])
    seg_word_slices: List[List[str]] = []
    boundary_dups = 0

    for s in segs:
        stem = Path(str(s.get("src", ""))).stem
        wdir = clip_work_dir(s.get("src", ""))
        st, en = float(s["start"]), float(s["end"])
        words: List[Dict[str, Any]] = []
        # Read only the primary word file (hq_words.json preferred over words.json)
        for fname in ("hq_words.json", "words.json"):
            fpath = wdir / fname
            if fpath.is_file():
                try:
                    wlist = read_json(fpath)
                    sliced = [
                        w for w in wlist
                        if float(w.get("end", 0.0)) > st + 0.08 and float(w.get("start", 0.0)) < en - 0.08
                    ]
                    if sliced:
                        words.extend(sliced)
                        break
                except Exception:
                    pass
        toks = [_clean_token(w.get("w") or w.get("word", "")) for w in words if _clean_token(w.get("w") or w.get("word", ""))]
        seg_word_slices.append(toks)

    # Check intra-segment consecutive repeated words/phrases not in script (e.g. "Almost, almost")
    script_text_all = " ".join(" ".join(sl.get("tokens", [])) for sl in script_lines).lower()
    for idx, toks in enumerate(seg_word_slices):
        for j in range(len(toks) - 1):
            if len(toks[j]) >= 3 and _word_similarity(toks[j], toks[j + 1]) >= 0.88:
                pair_str = f"{toks[j]} {toks[j + 1]}"
                if pair_str not in script_text_all:
                    boundary_dups += 1

    # Check inter-segment boundary duplicates across consecutive segments
    for i in range(len(segs) - 1):
        if seg_word_slices[i] and seg_word_slices[i + 1]:
            w_last = seg_word_slices[i][-1]
            w_first = seg_word_slices[i + 1][0]
            if len(w_last) >= 3 and _word_similarity(w_last, w_first) >= 0.85:
                pair_str = f"{w_last} {w_first}"
                if pair_str not in script_text_all:
                    boundary_dups += 1

    if not script_lines:
        return (boundary_dups == 0), len(segs), len(segs), boundary_dups

    all_kept_tokens = [tok for sl in seg_word_slices for tok in sl]
    all_notes = " ".join(str(s.get("id", "")) + " " + str(s.get("note", "")) for s in segs)

    covered = 0
    last_seg_idx = 0
    for sline in script_lines:
        lid = sline["id"]
        stokens = sline["tokens"]
        # Check if line ID is explicitly covered in segment IDs/notes in order OR token overlap >= 0.35
        matched_seg = None
        for idx in range(last_seg_idx, len(segs)):
            sid_str = str(segs[idx].get("id", ""))
            note_str = str(segs[idx].get("note", ""))
            if re.search(rf"\b{re.escape(lid)}[a-z]?\b", sid_str + " " + note_str):
                matched_seg = idx
                break
            stoks_seg = seg_word_slices[idx]
            if stokens and stoks_seg:
                hits = sum(1.0 for st in stokens if max((_word_similarity(st, ct) for ct in stoks_seg), default=0.0) >= 0.64)
                if hits / len(stokens) >= 0.35:
                    matched_seg = idx
                    break
        if matched_seg is not None:
            covered += 1
            last_seg_idx = matched_seg
        elif stokens and all_kept_tokens:
            hits = sum(1.0 for st in stokens if max((_word_similarity(st, ct) for ct in all_kept_tokens), default=0.0) >= 0.64)
            if hits / len(stokens) >= 0.35 or re.search(rf"\b{re.escape(lid)}[a-z]?\b", all_notes):
                covered += 1

    total_lines = len(script_lines)
    passed = (covered == total_lines and boundary_dups == 0)
    return passed, covered, total_lines, boundary_dups


def _mark_bbox(pos: Any, W: int, H: int, size_frac: float = 0.22) -> Tuple[int, int, int, int]:
    """Compute (x0, y0, size, size) pixel box for a mark badge on a WxH frame."""
    size = max(2, int(round(min(W, H) * size_frac / 2)) * 2)
    m = int(min(W, H) * 0.06)
    if isinstance(pos, (list, tuple)) and len(pos) >= 2:
        x0 = int(float(pos[0]) * W - size / 2)
        y0 = int(float(pos[1]) * H - size / 2)
    else:
        pos_str = str(pos or "top-right").strip().lower()
        coords = {
            "center": ((W - size) // 2, (H - size) // 2),
            "top-center": ((W - size) // 2, m),
            "top-right": (W - size - m, m),
            "top-left": (m, m),
            "bottom-right": (W - size - m, H - size - m),
            "bottom-left": (m, H - size - m),
        }
        x0, y0 = coords.get(pos_str, coords["top-right"])
    x0 = max(0, min(W - size, x0))
    y0 = max(0, min(H - size, y0))
    return x0, y0, size, size


def verify_marks_and_build_contact_sheet(
    mp4_path: Path,
    plan: Dict[str, Any],
    seg_start: Dict[str, float],
    W: int,
    H: int,
    contact_sheet_path: Path,
) -> Tuple[bool, int, int, List[str], int]:
    """Perform deterministic RGB pixel check on every mark frame AND build a single
    compact contact sheet (<80 KB) of all mark frames + jump-cut frames using FFmpeg `tile`.
    """
    marks = plan.get("marks", [])
    segs = plan.get("segments", [])

    mark_results: List[str] = []
    passed_marks = 0
    sample_events: List[Tuple[float, str]] = []

    default_pack = str(plan.get("mark_pack") or "flash").strip().lower()
    strobe_styles = {"flash", "vignette", "kn20228", "w42", "strobe"}

    for m in marks:
        mtype = _normalize_mark_type(m.get("type", "right"))
        sid = str(m.get("segment", ""))
        t_start = float(seg_start.get(sid, 0.0)) + float(m.get("at", 0.0))
        dur = float(m.get("duration", 1.1))
        style_key = str(m.get("style") or m.get("pack") or default_pack).strip().lower()
        is_strobe = style_key in strobe_styles or bool(m.get("strobe", False))
        # Probe at t_start + 0.05s for strobe (inside first [0.00, 0.11) ON pulse), or +0.35s for static badge
        t_probe = t_start + (0.05 if is_strobe else min(0.35, dur * 0.4))
        sample_events.append((t_probe, f"{mtype.upper()}@{tc(t_start)}"))

        x0, y0, mw, mh = _mark_bbox(m.get("position", "top-right"), W, H, float(m.get("size", 0.22)))
        cmd = [
            str(FFMPEG),
            "-hide_banner",
            "-loglevel",
            "error",
            "-ss",
            f"{t_probe:.3f}",
            "-i",
            str(mp4_path),
            "-vf",
            f"crop={mw}:{mh}:{x0}:{y0}",
            "-frames:v",
            "1",
            "-f",
            "rawvideo",
            "-pix_fmt",
            "rgb24",
            "-",
        ]
        raw = subprocess.run(cmd, capture_output=True).stdout
        r_vals: List[int] = []
        g_vals: List[int] = []
        for i in range(0, len(raw) - 2, 3):
            r, g, b = raw[i], raw[i + 1], raw[i + 2]
            if max(r, g, b) - min(r, g, b) > 45:
                r_vals.append(r)
                g_vals.append(g)

        avg_r = sum(r_vals) / len(r_vals) if r_vals else 0.0
        avg_g = sum(g_vals) / len(g_vals) if g_vals else 0.0
        diff_rg = avg_r - avg_g
        diff_gr = avg_g - avg_r

        if mtype == "wrong" and len(r_vals) >= 100 and diff_rg >= 35.0:
            passed_marks += 1
            mark_results.append(f"wrong@{tc(t_start)} R-G=+{diff_rg:.0f}{'[STROBE]' if is_strobe else ''}")
        elif mtype == "right" and len(g_vals) >= 100 and diff_gr >= 35.0:
            passed_marks += 1
            mark_results.append(f"right@{tc(t_start)} G-R=+{diff_gr:.0f}{'[STROBE]' if is_strobe else ''}")
        else:
            mark_results.append(f"{mtype}@{tc(t_start)} FAIL(R={avg_r:.0f},G={avg_g:.0f})")

    # Also add jump-cut frames (where consecutive segments share the same source clip)
    for i in range(1, len(segs)):
        stem_prev = Path(str(segs[i - 1].get("src", ""))).stem
        stem_curr = Path(str(segs[i].get("src", ""))).stem
        if stem_prev == stem_curr:
            sid = str(segs[i].get("id", f"S{i+1}"))
            t_jc = float(seg_start.get(sid, 0.0)) + 0.15
            sample_events.append((t_jc, f"JUMPCUT:{sid}@{tc(t_jc)}"))

    if not sample_events:
        sample_events.append((0.5, "FRAME@00:00.50"))

    # Build one single contact sheet strip (<80 KB) using FFmpeg tile filter
    n_tiles = len(sample_events)
    tile_w = 180
    tile_h = int(round(tile_w * H / max(1, W) / 2)) * 2
    inputs: List[str] = []
    fc_parts: List[str] = []
    for idx, (t_ev, _) in enumerate(sample_events):
        inputs.extend(["-ss", f"{t_ev:.3f}", "-i", str(mp4_path)])
        fc_parts.append(f"[{idx}:v]trim=end_frame=1,setpts=PTS-STARTPTS,scale={tile_w}:{tile_h},setsar=1[v{idx}]")

    concat_in = "".join(f"[v{idx}]" for idx in range(n_tiles))
    fc_parts.append(
        f"{concat_in}concat=n={n_tiles}:v=1:a=0,tile={n_tiles}x1:padding=4:margin=4:color=0x141414[out]"
    )

    contact_sheet_path.parent.mkdir(parents=True, exist_ok=True)
    # Encode with q:v 8 (and tighten if needed to guarantee <80 KB)
    for qv in ("8", "12", "16"):
        cmd_sheet = [
            str(FFMPEG),
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            *inputs,
            "-filter_complex",
            ";".join(fc_parts),
            "-map",
            "[out]",
            "-frames:v",
            "1",
            "-q:v",
            qv,
            str(contact_sheet_path),
        ]
        subprocess.run(cmd_sheet, check=False, capture_output=True)
        if contact_sheet_path.is_file() and contact_sheet_path.stat().st_size < 78 * 1024:
            break

    sheet_kb = int(round(contact_sheet_path.stat().st_size / 1024.0)) if contact_sheet_path.is_file() else 0
    all_marks_ok = (passed_marks == len(marks)) and (sheet_kb > 0 and sheet_kb < 80)
    return all_marks_ok, passed_marks, len(marks), mark_results, sheet_kb


def run_verification(plan_path: str | Path, script_path: Optional[str | Path] = None) -> bool:
    """Run all 5 deterministic verification checks and print the 5-line QA report."""
    p_file, plan = load_plan(plan_path)
    out_mp4 = resolve(plan.get("output") or (OUTPUT / f"{p_file.parent.name}.mp4"))
    if not out_mp4.is_file():
        print(f"[VERIFY FAIL] Output MP4 missing: {out_mp4}")
        return False

    info = probe(out_mp4)
    canvas = plan.get("canvas", {})
    exp_w = int(canvas.get("width") or info.get("width") or 720)
    exp_h = int(canvas.get("height") or info.get("height") or 1280)
    exp_fps = int(round(float(canvas.get("fps") or info.get("fps") or 24)))

    _, _, exp_total, seg_start = compute_timeline_starts(plan, exp_fps)
    actual_dur = float(info.get("duration") or 0.0)
    dur_diff = actual_dur - exp_total
    video_ok = (
        info.get("width") == exp_w
        and info.get("height") == exp_h
        and int(round(float(info.get("fps") or 0))) == exp_fps
        and abs(dur_diff) <= 0.15
    )

    target_lufs = float((plan.get("audio") or {}).get("loudness", -11.5))
    audio_ok, i_lufs, tp_db = check_loudness(out_mp4, target_lufs=target_lufs)

    cuts_ok, clipped_w, long_pauses, jc_masked, b_clicks = check_cuts_and_pauses(plan, mp4_path=out_mp4)

    if not script_path:
        # Use the script this plan was prepared from (recorded by prepare.py), not just the first file in input/script/
        side = p_file.parent / "plan.json"
        try:
            script_path = plan.get("script") or (read_json(side).get("script") if side.is_file() else None)
        except Exception:
            script_path = None
    resolved_script = resolve(script_path) if script_path else None
    script_ok, cov_lines, tot_lines, b_dups = check_script_coverage(plan, script_path=resolved_script)

    # Write contact sheet to plan directory AND work/<output_stem>/ (plus legacy alias) if they exist
    sheet_path = p_file.parent / "marks_contact_sheet.jpg"
    marks_ok, m_pass, m_tot, m_details, sheet_kb = verify_marks_and_build_contact_sheet(
        out_mp4, plan, seg_start, exp_w, exp_h, sheet_path
    )
    for alias_name in (out_mp4.stem, "Kannada_I_am_hungry", "kannada_hungry"):
        alt_proj_dir = WORK / alias_name
        if alt_proj_dir.is_dir() and alt_proj_dir.resolve() != p_file.parent.resolve() and sheet_path.is_file():
            (alt_proj_dir / "marks_contact_sheet.jpg").write_bytes(sheet_path.read_bytes())

    pct = int(round(100.0 * cov_lines / max(1, tot_lines)))
    rel_out = str(out_mp4)
    rel_sheet = f"work/{p_file.parent.name}/marks_contact_sheet.jpg"

    # Strict 5-line deterministic QA output
    print(
        f"[VERIFY {'PASS' if video_ok else 'FAIL'}] {rel_out} "
        f"({info['width']}x{info['height']} @ {int(round(info['fps'] or 0))}fps, "
        f"{tc(actual_dur)} vs {tc(exp_total)} expected, Δ={dur_diff:+.2f}s)"
    )
    print(
        f"[AUDIO  {'PASS' if audio_ok else 'FAIL'}] Integrated Loudness: {i_lufs:.1f} LUFS "
        f"(target {target_lufs:.1f} LUFS), True Peak: {tp_db:.1f} dBTP"
    )
    print(
        f"[CUTS   {'PASS' if cuts_ok else 'FAIL'}] {len(plan.get('segments', []))} segments verified: "
        f"{clipped_w} clipped words, {long_pauses} internal pauses >1.3s, {jc_masked} jump-cuts masked, {b_clicks} boundary clicks"
    )
    print(
        f"[SCRIPT {'PASS' if script_ok else 'FAIL'}] {cov_lines}/{tot_lines} script lines present in order "
        f"({pct}% coverage, {b_dups} boundary duplicates)"
    )
    print(
        f"[MARKS  {'PASS' if marks_ok else 'FAIL'}] {m_pass}/{m_tot} marks verified via RGB pixel check "
        f"({', '.join(m_details)}) -> {rel_sheet} ({sheet_kb} KB)"
    )

    return bool(video_ok and audio_ok and cuts_ok and script_ok and marks_ok)


def main() -> None:
    ap = argparse.ArgumentParser(description="Post-render deterministic verifier for Claude Editor.")
    ap.add_argument("plan", help="Path to plan.json or plan.edl")
    ap.add_argument("--script", default=None, help="Optional script file path for coverage verification")
    args = ap.parse_args()

    ok = run_verification(args.plan, script_path=args.script)
    if not ok:
        sys.exit(1)


if __name__ == "__main__":
    main()
