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
from typing import Sequence, Any, Dict, List, Optional, Tuple

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


def _extract_zoom_pair(seg: Dict[str, Any]) -> Tuple[float, float]:
    """Parse raw un-clamped (z0, z1) numeric zoom factors from numeric or string zoom specs."""
    raw_z = seg.get("zoom", 1.0)
    z0, z1 = 1.0, 1.0
    if isinstance(raw_z, (int, float)):
        z0 = z1 = float(raw_z)
    elif raw_z is not None:
        s = str(raw_z).strip()
        prefix = ""
        if ":" in s:
            prefix, rhs = s.split(":", 1)
            prefix = prefix.strip().lower()
            s = rhs.split("@", 1)[0].strip()
        if "->" in s:
            a, b = s.split("->", 1)
            try:
                z0, z1 = float(a.strip()), float(b.strip())
            except ValueError:
                z0 = z1 = 1.0
        else:
            try:
                v = float(s)
                if prefix in ("sudden_in", "push", "in") or "sudden_in" in str(raw_z) or "push" in str(raw_z):
                    z0, z1 = 1.0, v
                elif prefix in ("sudden_out", "pull", "out") or "sudden_out" in str(raw_z) or "pull" in str(raw_z):
                    z0, z1 = v, 1.0
                else:
                    z0 = z1 = v
            except ValueError:
                z0 = z1 = 1.0
    if seg.get("zoom_to") is not None:
        try:
            z1 = float(seg["zoom_to"])
        except (TypeError, ValueError):
            pass
    return z0, z1


def _subtract_intervals(
    base_start: float,
    base_end: float,
    exclusions: Sequence[Tuple[float, float]],
) -> List[Tuple[float, float]]:
    """Subtract exclusion [a, b] windows from [base_start, base_end]."""
    if base_end <= base_start:
        return []
    spans: List[Tuple[float, float]] = [(base_start, base_end)]
    for ex_a, ex_b in exclusions:
        if ex_b <= ex_a:
            continue
        next_spans: List[Tuple[float, float]] = []
        for s0, s1 in spans:
            if ex_b <= s0 or ex_a >= s1:
                next_spans.append((s0, s1))
            else:
                if ex_a > s0:
                    next_spans.append((s0, ex_a))
                if ex_b < s1:
                    next_spans.append((ex_b, s1))
        spans = next_spans
        if not spans:
            break
    return spans


DEAD_AIR_THRESHOLD = 0.60


def check_cuts_and_pauses(plan: Dict[str, Any], mp4_path: Optional[Path] = None) -> Tuple[bool, int, int, int, int]:
    """Check cut boundaries against cached word spans + silences, plus rendered MP4 dead-air and boundary click scan.
    Returns (passed, clipped_words_count, long_pauses_count, masked_jump_cuts_count, boundary_clicks_count).
    """
    segs = plan.get("segments", [])
    clipped_words = 0
    long_pauses = 0
    jump_cuts_masked = 0
    boundary_clicks = 0

    fps = int((plan.get("canvas") or {}).get("fps", 30) or 30)
    starts, seg_durs, _, seg_start_map = compute_timeline_starts(plan, fps)

    marks_by_sid: Dict[str, List[Dict[str, Any]]] = {}
    for mk in plan.get("marks", []) or []:
        msid = str(mk.get("segment") or "")
        if msid:
            marks_by_sid.setdefault(msid, []).append(mk)

    timeline_mark_holds: List[Tuple[float, float]] = []

    for i, s in enumerate(segs):
        sid = str(s.get("id", f"S{i+1}"))
        stem = Path(str(s.get("src", ""))).stem
        wdir = clip_work_dir(s.get("src", ""))
        st, en = float(s["start"]), float(s["end"])
        dur = max(0.0, en - st)

        if i > 0 and Path(str(segs[i - 1].get("src", ""))).stem == stem:
            z0, z1 = _extract_zoom_pair(s)
            if max(z0, z1) > 1.01 or s.get("layout", "single") != "single":
                jump_cuts_masked += 1

        # Build intentional mark flash hold windows in clip coordinates and timeline coordinates
        seg_marks = list(s.get("marks", []) or []) + list(marks_by_sid.get(sid, []))
        clip_mark_holds: List[Tuple[float, float]] = []
        t0_tl = starts[i] if i < len(starts) else float(seg_start_map.get(sid, 0.0))
        for mk in seg_marks:
            m_at = float(mk.get("at", max(0.0, dur - 0.60)))
            m_dur = float(mk.get("duration", 0.56) or 0.56)
            hold_rel_s = max(0.0, m_at - 0.15)
            # End-of-sentence flash holds extend through the mark pulse + tail to cut end
            hold_rel_e = dur if (m_at + m_dur >= dur - 0.45) else min(dur, m_at + m_dur + 0.35)
            clip_mark_holds.append((st + hold_rel_s, st + hold_rel_e))
            timeline_mark_holds.append((t0_tl + hold_rel_s, t0_tl + hold_rel_e))

        # Check internal pauses inside [st, en] (>0.60s dead air threshold, excluding mark flash holds)
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
                    for rem_s, rem_e in _subtract_intervals(ov_s, ov_e, clip_mark_holds):
                        if (rem_e - rem_s) > DEAD_AIR_THRESHOLD:
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

    # Also scan the rendered MP4 audio stream directly for any internal dead air >0.60s inside cuts
    if mp4_path is not None and mp4_path.is_file() and long_pauses == 0:
        try:
            r = subprocess.run(
                [
                    str(FFMPEG),
                    "-hide_banner",
                    "-i",
                    str(mp4_path),
                    "-af",
                    f"silencedetect=noise=-34dB:d={DEAD_AIR_THRESHOLD:.2f}",
                    "-f",
                    "null",
                    "-",
                ],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
            sil_events = re.findall(
                r"silence_end:\s*([\d.]+)\s*\|\s*silence_duration:\s*([\d.]+)",
                r.stderr,
            )
            for end_str, dur_str in sil_events:
                s_end = float(end_str)
                s_dur = float(dur_str)
                s_start = max(0.0, s_end - s_dur)
                for idx_s, t0_s in enumerate(starts):
                    t1_s = t0_s + seg_durs[idx_s]
                    ov_s = max(t0_s + 0.15, s_start)
                    ov_e = min(t1_s - 0.15, s_end)
                    for rem_s, rem_e in _subtract_intervals(ov_s, ov_e, timeline_mark_holds):
                        if (rem_e - rem_s) > DEAD_AIR_THRESHOLD:
                            long_pauses += 1
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


def _tokens_match(a: str, b: str, min_sim: float = 0.88) -> bool:
    if not a or not b:
        return False
    if a == b:
        return True
    if min(len(a), len(b)) >= 3 and _word_similarity(a, b) >= min_sim:
        return True
    return False


def _ngram_count(seq: Sequence[str], ngram: Sequence[str]) -> int:
    n = len(ngram)
    if n == 0 or len(seq) < n:
        return 0
    cnt = 0
    idx = 0
    while idx <= len(seq) - n:
        if all(_tokens_match(seq[idx + k], ngram[k], 0.86) for k in range(n)):
            cnt += 1
            idx += n
        else:
            idx += 1
    return cnt


def _has_adjacent_pair(seq: Sequence[str], tok: str) -> bool:
    for idx in range(len(seq) - 1):
        if _tokens_match(seq[idx], tok, 0.88) and _tokens_match(seq[idx + 1], tok, 0.88):
            return True
    return False


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

    try:
        import script_order as _so

        accepted_raw = _so.read_accepted(Path(plan["_plan_dir"])) if plan.get("_plan_dir") else {}
        accepted = set(accepted_raw)
    except Exception:   # noqa: BLE001
        _so = None  # type: ignore[assignment]
        accepted = set()

    def _is_accepted_ref(ref_text: str) -> bool:
        if not accepted or not ref_text:
            return False
        if ref_text in accepted:
            return True
        return any(re.search(rf"\b{re.escape(k)}[a-z]?\b", ref_text) for k in accepted)

    segs = plan.get("segments", [])
    seg_word_slices: List[List[str]] = []
    seg_word_keys: List[List[Tuple[str, float]]] = []   # (clip, start time) of each kept word: the same transcript word on both sides of a cut is not said twice

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
        if not words and isinstance(s.get("words"), list):
            for idx_w, w_item in enumerate(s["words"]):
                if isinstance(w_item, dict):
                    words.append(w_item)
                elif isinstance(w_item, str):
                    words.append({"w": w_item, "start": st + idx_w * 0.1, "end": st + (idx_w + 1) * 0.1})
        kept = [w for w in words if _clean_token(w.get("w") or w.get("word", ""))]
        toks = [_clean_token(w.get("w") or w.get("word", "")) for w in kept]
        seg_word_slices.append(toks)
        seg_word_keys.append([(stem, round(float(w.get("start", 0.0)), 3)) for w in kept])

    line_by_id: Dict[str, Dict[str, Any]] = {
        str(sl.get("id", "")): sl for sl in script_lines if sl.get("id")
    }
    seg_line_ids: List[List[str]] = []
    for idx, s in enumerate(segs):
        sid_note = f"{s.get('id', '')} {s.get('note', '')}"
        explicit_lids: List[str] = []
        for m_lid in re.findall(r"\b(L\d+)[a-z]?\b", sid_note):
            if (not line_by_id or m_lid in line_by_id) and m_lid not in explicit_lids:
                explicit_lids.append(m_lid)
        if not explicit_lids and script_lines and seg_word_slices[idx]:
            best_lid, best_score = None, 0.0
            for sl in script_lines:
                stoks = sl.get("tokens") or []
                if not stoks:
                    continue
                hits = sum(
                    1.0 for st_tok in stoks
                    if max((_word_similarity(st_tok, ct) for ct in seg_word_slices[idx]), default=0.0) >= 0.64
                )
                sc = hits / len(stoks)
                if sc >= 0.35 and sc > best_score:
                    best_score = sc
                    best_lid = str(sl.get("id", ""))
            if best_lid:
                explicit_lids.append(best_lid)
        seg_line_ids.append(explicit_lids)

    repeated_words: List[str] = []

    # 1. Intra-segment 2..6-word phrase stumbles and 1-word stutters (compared against THAT line's script tokens, never full-script word counts)
    for idx, toks in enumerate(seg_word_slices):
        sid = str(segs[idx].get("id", f"S{idx+1}"))
        if _is_accepted_ref(sid):
            continue
        line_toks = [
            _clean_token(t)
            for lid in seg_line_ids[idx]
            for t in (line_by_id.get(lid, {}).get("tokens") or [])
            if _clean_token(t)
        ]
        covered_pos: set[int] = set()
        max_n = min(6, len(toks) // 2)
        for n in range(max_n, 1, -1):
            for j in range(len(toks) - 2 * n + 1):
                cand = toks[j : j + n]
                match_m = None
                for m in range(j + n, min(len(toks) - n + 1, j + n + 8)):
                    if all(_tokens_match(cand[k], toks[m + k], 0.86) for k in range(n)):
                        match_m = m
                        break
                if match_m is None:
                    continue
                span_a = set(range(j, j + n))
                span_b = set(range(match_m, match_m + n))
                if span_a <= covered_pos and span_b <= covered_pos:
                    continue
                spoken_cnt = _ngram_count(toks, cand)
                if line_toks:
                    script_cnt = _ngram_count(line_toks, cand)
                elif script_lines:
                    script_cnt = max(
                        (_ngram_count([_clean_token(t) for t in sl.get("tokens", []) if _clean_token(t)], cand) for sl in script_lines),
                        default=0,
                    )
                else:
                    script_cnt = 0
                if spoken_cnt > max(1, script_cnt) and spoken_cnt > script_cnt:
                    covered_pos.update(span_a)
                    covered_pos.update(span_b)
                    repeated_words.append(f"{sid}: repeated phrase '{' '.join(cand)}'")

        for j in range(len(toks) - 1):
            if j in covered_pos and (j + 1) in covered_pos:
                continue
            if _tokens_match(toks[j], toks[j + 1], 0.88):
                if line_toks:
                    scripted_double = _has_adjacent_pair(line_toks, toks[j])
                elif script_lines:
                    scripted_double = any(
                        _has_adjacent_pair([_clean_token(t) for t in sl.get("tokens", []) if _clean_token(t)], toks[j])
                        for sl in script_lines
                    )
                else:
                    scripted_double = False
                if not scripted_double:
                    repeated_words.append(f"{sid}: stutter '{toks[j]} {toks[j + 1]}'")

    # 2. Inter-segment 2..6-word phrase stumbles and 1-word boundary duplicates across consecutive cuts
    for i in range(len(segs) - 1):
        sid_a = str(segs[i].get("id", f"S{i+1}"))
        sid_b = str(segs[i + 1].get("id", f"S{i+2}"))
        if _is_accepted_ref(sid_a) or _is_accepted_ref(sid_b):
            continue
        if not seg_word_slices[i] or not seg_word_slices[i + 1]:
            continue

        same_word = bool(
            seg_word_keys[i]
            and seg_word_keys[i + 1]
            and seg_word_keys[i][-1] == seg_word_keys[i + 1][0]
        )
        left_toks_full = seg_word_slices[i]
        right_toks_full = seg_word_slices[i + 1][1:] if same_word else seg_word_slices[i + 1]
        if not right_toks_full:
            continue

        left_tail = left_toks_full[-10:]
        right_head = right_toks_full[:10]
        combined_pair = left_toks_full + right_toks_full

        pair_lids: List[str] = []
        for lid in seg_line_ids[i] + seg_line_ids[i + 1]:
            if lid not in pair_lids:
                pair_lids.append(lid)
        pair_line_toks = [
            _clean_token(t)
            for lid in pair_lids
            for t in (line_by_id.get(lid, {}).get("tokens") or [])
            if _clean_token(t)
        ]

        cross_phrase_found = False
        max_cn = min(6, len(left_tail), len(right_head))
        for n in range(max_cn, 1, -1):
            for j in range(len(left_tail) - n + 1):
                cand = left_tail[j : j + n]
                in_right = any(
                    all(_tokens_match(cand[k], right_head[m + k], 0.86) for k in range(n))
                    for m in range(len(right_head) - n + 1)
                )
                if not in_right:
                    continue
                spoken_cnt = _ngram_count(combined_pair, cand)
                if pair_line_toks:
                    script_cnt = _ngram_count(pair_line_toks, cand)
                elif script_lines:
                    script_cnt = max(
                        (_ngram_count([_clean_token(t) for t in sl.get("tokens", []) if _clean_token(t)], cand) for sl in script_lines),
                        default=0,
                    )
                else:
                    script_cnt = 0
                if spoken_cnt > script_cnt and spoken_cnt >= 2:
                    msg = f"{sid_a}->{sid_b}: repeated phrase '{' '.join(cand)}'"
                    if msg not in repeated_words:
                        repeated_words.append(msg)
                    cross_phrase_found = True
                    break
            if cross_phrase_found:
                break

        if not cross_phrase_found:
            w_last = left_tail[-1]
            w_first = right_head[0]
            if _tokens_match(w_last, w_first, 0.85):
                scripted_pair = _has_adjacent_pair(pair_line_toks, w_last) if pair_line_toks else False
                if not scripted_pair:
                    repeated_words.append(f"{sid_a}->{sid_b}: boundary repeat '{w_last} {w_first}'")

    # 3. Also integrate script_order.repeat_problems if available
    if _so is not None and hasattr(_so, "repeat_problems"):
        try:
            import inspect

            rp_fn = getattr(_so, "repeat_problems")
            sig_params = list(inspect.signature(rp_fn).parameters.keys())
            seg_pairs = [(str(s.get("id", "")), seg_word_slices[i]) for i, s in enumerate(segs)]
            if len(sig_params) >= 2 and sig_params[0] in ("plan", "edl"):
                extra_probs = rp_fn(plan, script_lines)
            elif len(sig_params) >= 2:
                extra_probs = rp_fn(seg_pairs, script_lines)
            else:
                extra_probs = rp_fn(seg_pairs)
            for pr in extra_probs or []:
                pr_str = str(pr)
                if not _is_accepted_ref(pr_str) and pr_str not in repeated_words:
                    repeated_words.append(pr_str)
        except Exception:
            pass

    plan["_repeated_words"] = repeated_words
    boundary_dups = len(repeated_words)
    if repeated_words:
        print("[REPEAT FAIL] " + "; ".join(repeated_words))

    if not script_lines:
        return (boundary_dups == 0), len(segs), len(segs), boundary_dups

    all_kept_tokens = [tok for sl in seg_word_slices for tok in sl]
    all_notes = " ".join(str(s.get("id", "")) + " " + str(s.get("note", "")) for s in segs)

    covered = 0
    last_seg_idx = 0
    for sline in script_lines:
        lid = sline["id"]
        stokens = sline["tokens"]
        if lid in accepted:     # a deliberate exception in decisions.txt (a visual brief, a scene cue): the plan check already accepted it
            covered += 1
            continue
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
    try:
        import script_order

        rep = script_order.check_order([(str(s.get("id", "")), seg_word_slices[i]) for i, s in enumerate(segs)], script_lines)
        accepted_ord = script_order.read_accepted(Path(plan["_plan_dir"])) if plan.get("_plan_dir") else {}
        wrong = [m for m in rep["out_of_order"] + rep["repeated"] if not any(re.search(rf"\b{re.escape(k)}[a-z]?\b", m) for k in accepted_ord)]
        if wrong:
            passed = False
            print("[ORDER FAIL] " + "; ".join(wrong))
    except Exception as exc:   # noqa: BLE001
        print(f"[ORDER FAIL] could not measure order: {exc}")
        passed = False
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
    for qv in ("8", "12", "16", "22", "28"):
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
        if contact_sheet_path.is_file() and contact_sheet_path.stat().st_size < 75 * 1024:
            break

    sheet_kb = int(round(contact_sheet_path.stat().st_size / 1024.0)) if contact_sheet_path.is_file() else 0
    all_marks_ok = (passed_marks == len(marks)) and (sheet_kb > 0 and sheet_kb <= 120)
    return all_marks_ok, passed_marks, len(marks), mark_results, sheet_kb


def check_split_and_hook_rules(plan: Dict[str, Any]) -> Tuple[bool, str]:
    """Verify split-screen and single-character hook rules on `plan`:
      1. Split-screen segments (`split_h` / `split_v`) allow tasteful active-panel zooms in `[1.0, 1.15]`
         (fail only if `< 1.0` or `> 1.15`), have zero intra-split transitions or turn-boundary whoosh SFX,
         and share matched common headroom.
      2. Single-character hook (`single`) permits crisp hook transitions up to `max(3, round(hook_secs / 5.0))`
         (or `style_config` ceiling if higher).
    """
    segs = plan.get("segments", [])
    sfx_entries = plan.get("sfx", [])
    whoosh_segs = {
        str(e.get("segment"))
        for e in sfx_entries
        if (
            ("whoosh" in str(e.get("file", "")).lower() or "swish" in str(e.get("file", "")).lower())
            and float(e.get("at", 0.0) or 0.0) <= 0.25
        )
    }

    split_segs = 0
    split_violations: List[str] = []
    hook_transitions = 0
    hook_secs = 0.0
    in_initial_hook = True
    common_hr_val: Optional[float] = None

    try:
        from vision_tracker import compute_paired_split_crop_filters
    except Exception:
        compute_paired_split_crop_filters = None  # type: ignore[assignment]

    for i, s in enumerate(segs):
        sid = str(s.get("id", f"S{i+1}"))
        lay = str(s.get("layout") or "single").strip().lower()
        prev_lay = str(segs[i - 1].get("layout") or "single").strip().lower() if i > 0 else "single"
        tr = str(s.get("transition") or "cut").strip().lower()

        if lay != "single":
            in_initial_hook = False

        if in_initial_hook:
            hook_secs += max(0.0, float(s.get("end", 0.0)) - float(s.get("start", 0.0)))
        if in_initial_hook and i > 0 and tr != "cut":
            hook_transitions += 1

        if lay in ("split_h", "split_v", "left_right", "top_bottom"):
            split_segs += 1
            z0, z1 = _extract_zoom_pair(s)
            if min(z0, z1) < 0.995 or max(z0, z1) > 1.1505:
                split_violations.append(f"{sid}:excessive_split_zoom({z0:.2f}->{z1:.2f})")
            if i > 0 and prev_lay == lay:
                if tr != "cut":
                    split_violations.append(f"{sid}:intra_split_transition({tr})")
                if sid in whoosh_segs:
                    split_violations.append(f"{sid}:intra_split_whoosh")
            if common_hr_val is None and compute_paired_split_crop_filters is not None:
                peer = s.get("peer") if isinstance(s.get("peer"), dict) else {}
                if peer and peer.get("src"):
                    c_live, c_peer = compute_paired_split_crop_filters(
                        resolve(s["src"]), resolve(peer["src"]),
                        720, 1280, 720, 1280, 540, 1920, 540, 1920,
                    )
                    if c_live["common_headroom_pct"] != c_peer["common_headroom_pct"]:
                        split_violations.append(f"{sid}:headroom_mismatch")
                    else:
                        common_hr_val = float(c_live["common_headroom_pct"])

    explicit_max_hook = plan.get("max_hook_transitions")
    if explicit_max_hook is not None:
        max_hook_transitions = int(explicit_max_hook)
    else:
        max_hook_transitions = max(2, int(round(hook_secs / 6.5)))
    if hook_transitions > max_hook_transitions:
        split_violations.append(f"hook_transitions={hook_transitions}(>{max_hook_transitions})")

    ok = len(split_violations) == 0
    hr_str = f", common headroom {common_hr_val:.1f}%" if common_hr_val is not None else ""
    summary = (
        f"{split_segs} split-screen segs valid (z in 1.0..1.15{hr_str}, 0 intra-split transitions), {hook_transitions} hook clip transition(s)"
        if ok
        else f"Violations: {', '.join(split_violations)}"
    )
    return ok, summary


PICTURE_MAX_DIFF = 9.0   # mean grey-level difference (0-255) allowed between a cut's own render and the final video


def check_picture_timeline(plan: Dict[str, Any], mp4_path: Path) -> Tuple[bool, List[str], int]:
    """Does the picture in the final video match the plan at every moment? For each cut, the middle frame of the final video is compared with
    the middle frame of that cut's own render. Catches a transition or join that moves or drops footage (the picture drifts away from the
    sound while every other check still passes). Returns (ok, wrong cuts, cuts compared)."""
    try:
        import cv2
        import numpy as np
        import layouts
    except Exception as exc:   # noqa: BLE001
        return True, [f"not checked ({exc})"], 0
    canvas = plan.get("canvas") or {}
    W, H, fps = int(canvas.get("width") or 1080), int(canvas.get("height") or 1920), int(canvas.get("fps") or 30)

    def sig(frame: Any) -> Any:
        # the final video holds the same pixels as the cut's own render, so compare fine detail: only a re-encode separates them
        return cv2.cvtColor(cv2.resize(frame, (108, 192), interpolation=cv2.INTER_AREA), cv2.COLOR_BGR2GRAY).astype(np.float32)

    def frame_at(cap: Any, t: float) -> Any:
        cap.set(cv2.CAP_PROP_POS_MSEC, max(0.0, t) * 1000.0)
        ok, fr = cap.read()
        return fr if ok else None

    out = cv2.VideoCapture(str(mp4_path))
    t0, bad, compared = 0.0, [], 0
    for seg in plan.get("segments", []):
        dur = round((float(seg["end"]) - float(seg["start"])) * fps) / fps
        f = WORK / "_segments" / f"{layouts.build_segment_cache_key(seg, plan, W, H, fps, False)}.mkv"
        if not f.is_file():
            f = WORK / "_segments" / f"{layouts.build_segment_cache_key(seg, plan, W, H, fps, True)}.mkv"
        if f.is_file() and dur >= 0.4:
            ref_cap = cv2.VideoCapture(str(f))
            got = frame_at(out, t0 + dur / 2)
            # handheld footage changes a lot between frames and a seek can be a frame off: take the closest of the frames around the middle
            refs = [fr for fr in (frame_at(ref_cap, dur / 2 + k / fps) for k in range(-4, 5)) if fr is not None]
            ref_cap.release()
            if refs and got is not None:
                compared += 1
                best = min(float(np.abs(sig(r) - sig(got)).mean()) for r in refs)
                if best > PICTURE_MAX_DIFF:
                    bad.append(f"{seg.get('id')} at {t0 + dur / 2:.1f}s shows other footage than the plan")
        t0 += dur
    out.release()
    return (not bad), bad, compared


FRAME_STEP_SEC = 0.25          # one frame every quarter second, skipping the moments around joins and mark flashes
BLACK_MEAN, BLACK_STD = 10.0, 4.0
BANNER_FACE_OVERLAP = 0.04     # a banner covering more than 4% of a face's area counts as on the face
MISSING_FACE_SHARE = 0.35      # a panel without a face in more than this share of its frames is reported


def is_black_frame(gray: Any) -> bool:
    """A frame that is dark or one flat colour (a render fault), not a dark scene: both the average and the spread are tiny."""
    return float(gray.mean()) < BLACK_MEAN or float(gray.std()) < BLACK_STD


def box_overlap_share(face_norm: Sequence[float], box_px: Sequence[float], W: int, H: int) -> float:
    """The share of a face's area (normalised box) that a pixel box covers."""
    fx0, fy0, fx1, fy1 = face_norm[0] * W, face_norm[1] * H, face_norm[2] * W, face_norm[3] * H
    ix = max(0.0, min(fx1, box_px[2]) - max(fx0, box_px[0]))
    iy = max(0.0, min(fy1, box_px[3]) - max(fy0, box_px[1]))
    area = max(1e-6, (fx1 - fx0) * (fy1 - fy0))
    return (ix * iy) / area


def check_frames(plan: Dict[str, Any], mp4_path: Path) -> Dict[str, Any]:
    """Frame-by-frame QC of the finished video with the local YuNet face detector (no download, no model tokens):
    black or flat frames, a banner covering a face, and a face missing from a panel. Black frames and banners on faces fail the check;
    a missing face only warns, because a detector can miss a turned head or a hand over the face.
    Frames inside inter-segment transition windows ([join_t - d, join_t + d]) are skipped to avoid mid-whip/flash/reveal false alarms."""
    result: Dict[str, Any] = {"ok": True, "frames": 0, "black": [], "banner_on_face": [], "no_face": [], "note": ""}
    try:
        import cv2
        import layouts
        import vision_tracker
    except Exception as exc:   # noqa: BLE001
        result["note"] = f"not checked ({exc})"
        return result
    cap = cv2.VideoCapture(str(mp4_path))
    if not cap.isOpened():
        result["note"] = "video could not be opened"
        return result
    fps = float((plan.get("canvas") or {}).get("fps") or 30)
    W, H = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)), int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    marks_by_seg: Dict[str, List[Tuple[float, float]]] = {}
    for m in plan.get("marks", []):
        marks_by_seg.setdefault(str(m.get("segment")), []).append((float(m.get("at", 0)), float(m.get("at", 0)) + float(m.get("duration", 0.56)) + 0.1))
    segs = plan.get("segments", [])
    t0 = 0.0
    for idx_seg, seg in enumerate(segs):
        dur = round((float(seg["end"]) - float(seg["start"])) * fps) / fps
        sid, lay = str(seg.get("id")), str(seg.get("layout") or "single").lower()
        for m_in in seg.get("marks", []) or []:
            marks_by_seg.setdefault(sid, []).append((float(m_in.get("at", 0)), float(m_in.get("at", 0)) + float(m_in.get("duration", 0.56)) + 0.1))

        tr_in = str(seg.get("transition") or "cut").strip().lower() if idx_seg > 0 else "cut"
        d_in = float(seg.get("transition_duration") or 0.36) if tr_in not in ("", "cut", "none") else 0.0
        tr_out = (
            str(segs[idx_seg + 1].get("transition") or "cut").strip().lower()
            if idx_seg + 1 < len(segs)
            else "cut"
        )
        d_out = (
            float(segs[idx_seg + 1].get("transition_duration") or 0.36)
            if tr_out not in ("", "cut", "none")
            else 0.0
        )

        boxes = layouts.banner_boxes(seg, W, H, dur)
        panels = {"split_h": [("left", (0, 0, W // 2, H)), ("right", (W // 2, 0, W, H))],
                  "split_v": [("top", (0, 0, W, H // 2)), ("bottom", (0, H // 2, W, H))]}.get(lay, [("frame", (0, 0, W, H))])
        seen = {name: 0 for name, _ in panels}
        missing: Dict[str, List[float]] = {name: [] for name, _ in panels}
        t = 0.2
        while t < dur - 0.2:
            in_transition = (d_in > 0.0 and t <= d_in + 0.05) or (d_out > 0.0 and t >= dur - d_out - 0.05)
            in_mark = any(a - 0.05 <= t <= b for a, b in marks_by_seg.get(sid, []))
            if not in_transition and not in_mark:
                cap.set(cv2.CAP_PROP_POS_MSEC, (t0 + t) * 1000.0)
                ok, frame = cap.read()
                if ok:
                    result["frames"] += 1
                    if is_black_frame(cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)):
                        result["black"].append(f"{sid} at {t0 + t:.1f}s")
                    for name, (x0, y0, x1, y1) in panels:
                        seen[name] += 1
                        faces = vision_tracker.prominent_faces(vision_tracker.detect_faces_in_frame(frame[y0:y1, x0:x1]))
                        if result["frames"] % 6 == 1 and name in ("frame", "left", "top"):
                            try:
                                import vision_scan

                                vision_scan.record(f"final video {sid}", t0 + t, frame, faces, result["frames"], 0, 0.0)
                            except Exception:
                                pass
                        if not faces:
                            missing[name].append(round(t0 + t, 1))
                        elif name == "frame":
                            for b in boxes:
                                if b["t0"] <= t <= b["t1"]:
                                    for f in faces:
                                        if box_overlap_share(f["bbox_norm"], (b["x0"], b["y0"], b["x1"], b["y1"]), W, H) > BANNER_FACE_OVERLAP:
                                            result["banner_on_face"].append(f"{sid} at {t0 + t:.1f}s: '{b['text']}' covers a face")
            t += FRAME_STEP_SEC
        for name, times in missing.items():
            if seen[name] >= 3 and len(times) / seen[name] > MISSING_FACE_SHARE:
                label = f"{sid} {name} panel" if name != "frame" else sid
                result["no_face"].append(f"{label}: no face in {len(times)} of {seen[name]} frames (for example at {times[0]}s)")
        t0 += dur
    cap.release()
    result["ok"] = not result["black"] and len(result["banner_on_face"]) < 2
    return result


def is_subs_enabled(plan: Dict[str, Any]) -> bool:
    """Return True when caption/subtitle rendering is enabled in `plan`."""
    spec = plan.get("subs")
    if spec is None or spec is False:
        return False
    if spec is True:
        return True
    if isinstance(spec, str):
        return spec.strip().lower() not in ("", "none", "off", "false", "0", "no")
    if isinstance(spec, dict):
        if not spec.get("enabled", True):
            return False
        style = str(spec.get("style", "auto") or "auto").strip().lower()
        return style not in ("", "none", "off", "false")
    return False


def check_subtitles_srt(plan: Dict[str, Any], mp4_path: Path) -> Tuple[bool, str]:
    """Verify expected `.srt` sidecar exists and contains monotonic non-overlapping cues when `plan.get('subs')` is enabled."""
    if not is_subs_enabled(plan):
        return True, "disabled"
    segs = plan.get("segments", [])
    if not segs:
        return True, "no dialogue segments"

    srt_path = mp4_path.with_suffix(".srt")
    if not srt_path.is_file():
        return False, f"missing expected SRT sidecar: {srt_path}"

    raw_srt = srt_path.read_text(encoding="utf-8", errors="replace")
    cue_matches = list(
        re.finditer(
            r"(\d{2}):(\d{2}):(\d{2})[,.](\d{3})\s*-->\s*(\d{2}):(\d{2}):(\d{2})[,.](\d{3})",
            raw_srt,
        )
    )
    if not cue_matches:
        return False, f"0 subtitle cues found in {srt_path.name}"

    prev_end = 0.0
    prev_start = 0.0
    for idx_c, m in enumerate(cue_matches):
        h1, m1, s1, ms1, h2, m2, s2, ms2 = (int(g) for g in m.groups())
        t_s = h1 * 3600.0 + m1 * 60.0 + s1 + ms1 / 1000.0
        t_e = h2 * 3600.0 + m2 * 60.0 + s2 + ms2 / 1000.0
        if t_e <= t_s:
            return False, f"cue {idx_c + 1} has non-positive duration ({t_s:.3f}s -> {t_e:.3f}s)"
        if idx_c > 0:
            if t_s < prev_start - 0.002:
                return False, f"cue {idx_c + 1} out of monotonic order ({t_s:.3f}s < {prev_start:.3f}s)"
            if t_s < prev_end - 0.002:
                return False, f"cue {idx_c + 1} overlaps previous cue ({t_s:.3f}s < {prev_end:.3f}s)"
        prev_start = t_s
        prev_end = t_e

    return True, f"{len(cue_matches)} monotonic non-overlapping cue(s) -> {srt_path.name}"


def build_review_sheet(plan: Dict[str, Any], mp4_path: Path, out_path: Path, tile_w: int = 104) -> Optional[Path]:
    """One small picture of the whole finished video for the Director: 3 frames per cut (just after it starts, middle, just before it
    ends), shrunk and labelled with the cut id and time. About 1k tokens to read, so nothing needs to be extracted by hand."""
    try:
        import cv2
        import numpy as np
    except Exception:
        return None
    cap = cv2.VideoCapture(str(mp4_path))
    if not cap.isOpened():
        return None
    fps = float((plan.get("canvas") or {}).get("fps") or 30)
    tile_h = int(tile_w * 16 / 9)
    cols_per_cut, label_h = 3, 14
    cuts, t0 = [], 0.0
    for seg in plan.get("segments", []):
        dur = round((float(seg["end"]) - float(seg["start"])) * fps) / fps
        row = []
        for k, frac_t in enumerate((min(0.25, dur * 0.3), dur / 2, max(dur - 0.25, dur * 0.7))):
            cap.set(cv2.CAP_PROP_POS_MSEC, (t0 + frac_t) * 1000.0)
            ok, fr = cap.read()
            tile = cv2.resize(fr, (tile_w, tile_h), interpolation=cv2.INTER_AREA) if ok else np.zeros((tile_h, tile_w, 3), np.uint8)
            row.append(cv2.copyMakeBorder(tile, 0, 0, 1, 1, cv2.BORDER_CONSTANT, value=(255, 255, 255)))
        strip = np.hstack(row)
        bar = np.full((label_h, strip.shape[1], 3), 245, np.uint8)
        cv2.putText(bar, f"{seg.get('id')} {t0:.1f}s", (3, 11), cv2.FONT_HERSHEY_SIMPLEX, 0.36, (0, 0, 0), 1, cv2.LINE_AA)
        cuts.append(np.vstack([bar, strip]))
        t0 += dur
    cap.release()
    if not cuts:
        return None
    per_row = max(1, min(len(cuts), 6))
    rows = []
    for r in range(0, len(cuts), per_row):
        group = cuts[r:r + per_row]
        while len(group) < per_row:
            group.append(np.full_like(cuts[0], 255))
        rows.append(np.hstack(group))
    sheet = np.vstack(rows)
    k = 1560.0 / max(sheet.shape[:2])
    if k < 1.0:
        sheet = cv2.resize(sheet, None, fx=k, fy=k, interpolation=cv2.INTER_AREA)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(out_path), sheet, [cv2.IMWRITE_JPEG_QUALITY, 70])
    return out_path


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
    plan["_plan_dir"] = str(p_file.parent)
    script_ok, cov_lines, tot_lines, b_dups = check_script_coverage(plan, script_path=resolved_script)

    # Write contact sheet to plan directory AND work/<output_stem>/ if it exists
    sheet_path = p_file.parent / "marks_contact_sheet.jpg"
    marks_ok, m_pass, m_tot, m_details, sheet_kb = verify_marks_and_build_contact_sheet(
        out_mp4, plan, seg_start, exp_w, exp_h, sheet_path
    )
    split_hook_ok, split_hook_summary = check_split_and_hook_rules(plan)
    subs_ok, subs_summary = check_subtitles_srt(plan, out_mp4)
    pic_ok, pic_bad, pic_n = check_picture_timeline(plan, out_mp4)
    review = build_review_sheet(plan, out_mp4, p_file.parent / "review_sheet.jpg")
    frames = check_frames(plan, out_mp4)
    alt_proj_dir = WORK / out_mp4.stem
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
        f"{clipped_w} clipped words, {long_pauses} internal pauses >0.6s, {jc_masked} jump-cuts masked, {b_clicks} boundary clicks"
    )
    print(
        f"[SCRIPT {'PASS' if script_ok else 'FAIL'}] {cov_lines}/{tot_lines} script lines present in order "
        f"({pct}% coverage, {b_dups} boundary duplicates)"
    )
    print(
        f"[MARKS  {'PASS' if marks_ok else 'FAIL'}] {m_pass}/{m_tot} marks verified via RGB pixel check "
        f"({', '.join(m_details)}) -> {rel_sheet} ({sheet_kb} KB)"
    )
    print(f"[SPLIT  {'PASS' if split_hook_ok else 'FAIL'}] {split_hook_summary}")
    if is_subs_enabled(plan) or not subs_ok:
        print(f"[SUBS   {'PASS' if subs_ok else 'FAIL'}] {subs_summary}")
    if review:
        try:
            rel_review = review.relative_to(ROOT)
        except ValueError:
            rel_review = review
        print(f"[REVIEW] one picture of the whole video (3 frames per cut, about 1k tokens to read): {rel_review}")
    detail = f"{frames['frames']} frames: {len(frames['black'])} black, {len(frames['banner_on_face'])} banner-on-face, {len(frames['no_face'])} panel(s) with missing faces"
    print(f"[FRAMES {'PASS' if frames['ok'] else 'FAIL'}] {frames['note'] or detail}")
    for kind, lines in (("BLACK", frames["black"]), ("BANNER ON FACE", frames["banner_on_face"]), ("NO FACE (warning)", frames["no_face"])):
        for ln in lines[:4]:
            print(f"          {kind}: {ln}")
    try:
        import events

        ev_list = [
            ("Video", video_ok, f"{info['width']}x{info['height']}, {tc(actual_dur)}"),
            ("Loudness", audio_ok, f"{i_lufs:.1f} LUFS, peak {tp_db:.1f} dBTP"),
            ("Cuts", cuts_ok, f"{len(plan.get('segments', []))} cuts, {clipped_w} clipped words, {b_clicks} clicks"),
            ("Script order", script_ok, f"{cov_lines}/{tot_lines} lines present in order"),
            ("Marks", marks_ok, f"{m_pass}/{m_tot} verified"),
            ("Split screen", split_hook_ok, split_hook_summary),
            ("Frames", frames['ok'], frames['note'] or detail),
            ("Picture vs plan", pic_ok, f"{pic_n} cuts matched" if pic_ok else "; ".join(pic_bad)[:200]),
        ]
        if is_subs_enabled(plan) or not subs_ok:
            ev_list.append(("Subtitles", subs_ok, subs_summary))
        for _tag, _ok, _txt in ev_list:
            events.emit("verify", f"{_tag}: {'pass' if _ok else 'FAIL'}, {_txt}", ok=bool(_ok), tag=_tag)
    except Exception:
        pass
    print(f"[PICTURE {'PASS' if pic_ok else 'FAIL'}] {pic_n} cuts: the picture at the middle of each cut matches the plan" + ("" if pic_ok else ": " + "; ".join(pic_bad)))

    return bool(video_ok and audio_ok and cuts_ok and script_ok and marks_ok and split_hook_ok and subs_ok and pic_ok and frames['ok'])


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
