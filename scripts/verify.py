"""One-command post-render Deep QC verifier (`verify.py`).

Usage:
    python3 scripts/verify.py work/<project>/plan.json [--script input/script/<script>.txt]
    python3 scripts/verify.py work/<project>/plan.edl [--script input/script/<script>.txt]

Performs deterministic post-render Deep QC checks:
1. Output MP4 existence, resolution, FPS, and duration vs expected timeline total (±0.15s).
2. Integrated loudness (EBU R128 / loudnorm) and True Peak ceiling.
3. Cut boundary verification against cached word spans + 10ms RMS energy, internal dead air (>0.60s), and splice clicks.
4. Script coverage, phrase/word repetition detection, and strict script line ordering.
5. Deterministic RGB pixel check on every mark frame + compact `marks_contact_sheet.jpg` (<95 KB).
6. Split-screen zoom/headroom/intra-split rules and hook transition limits.
7. Subtitle `.srt` sidecar cue ordering/non-overlap and canvas bounds safety.
8. 9:16 & 1:1 Square frame-level Deep QC: black frames, frozen frames, face crop safety, and banner-on-face overlap.
9. Picture-vs-plan frame correspondence across every cut + `review_sheet.jpg` contact sheet generation.
"""
from __future__ import annotations

import argparse
import array
import inspect
import json
import math
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common  # noqa: E402


def _fallback_normalize_mark_type(mark_type: str) -> str:
    t = str(mark_type or "right").strip().lower()
    if t in ("right", "correct", "check", "tick", "green", "ok", "true", "yes"):
        return "right"
    if t in ("wrong", "error", "cross", "x", "red", "false", "no", "incorrect"):
        return "wrong"
    return t


FFMPEG: Path = common.FFMPEG
ROOT: Path = getattr(common, "ROOT", Path(__file__).resolve().parent.parent)
WORK: Path = getattr(common, "WORK", ROOT / "work")
OUTPUT: Path = getattr(
    common,
    "OUTPUT",
    Path(os.environ.get("ZENO_OUTPUT_DIR", "").strip() or (Path.home() / "Downloads" / "Zeno Outs")).expanduser(),
)
DEFAULT_LOUDNESS_LUFS: float = float(getattr(common, "DEFAULT_LOUDNESS_LUFS", -10.5))

clip_work_dir = common.clip_work_dir
_normalize_mark_type = getattr(
    common,
    "_normalize_mark_type",
    getattr(common, "normalize_mark_type", _fallback_normalize_mark_type),
)
probe = common.probe
read_json = common.read_json
resolve = common.resolve
square_job_output = common.square_job_output
strip_ratio_prefix = getattr(common, "strip_ratio_prefix", lambda s: re.sub(r"^(?:reel|sq)[\s_-]+", "", str(s or "").strip(), flags=re.IGNORECASE).strip() or str(s or "").strip())
tc = common.tc
write_json = common.write_json

try:
    import edl_parser
except Exception:
    edl_parser = None  # type: ignore[assignment]

try:
    import prepare as _prepare_mod

    _clean_token = getattr(
        _prepare_mod,
        "_clean_token",
        getattr(_prepare_mod, "clean_token", lambda w: re.sub(r"[^\w\u0900-\u0D7F']", "", str(w or "").lower()).strip()),
    )
    _word_similarity = getattr(
        _prepare_mod,
        "_word_similarity",
        getattr(_prepare_mod, "word_similarity", lambda a, b: 1.0 if _clean_token(a) == _clean_token(b) else 0.0),
    )
    compute_10ms_rms_envelope = getattr(_prepare_mod, "compute_10ms_rms_envelope", None)
    _words_in_silence = getattr(_prepare_mod, "words_in_silence", None)
    parse_script_file = getattr(_prepare_mod, "parse_script_file", None)
except Exception:
    _clean_token = lambda w: re.sub(r"[^\w\u0900-\u0D7F']", "", str(w or "").lower()).strip()  # noqa: E731
    _word_similarity = lambda a, b: 1.0 if _clean_token(a) == _clean_token(b) else 0.0  # noqa: E731
    compute_10ms_rms_envelope = None
    _words_in_silence = None
    parse_script_file = None


def load_plan(plan_arg: str | Path) -> Tuple[Path, Dict[str, Any]]:
    """Load either plan.json or plan.edl into a plan dict."""
    p = resolve(plan_arg)
    if p.suffix.lower() == ".edl":
        if edl_parser is not None:
            parse_fn = getattr(edl_parser, "parse_edl", getattr(edl_parser, "load_edl", None))
            if callable(parse_fn):
                return p, parse_fn(p)
        json_sibling = p.with_suffix(".json")
        if json_sibling.is_file():
            return p, read_json(json_sibling)
    return p, read_json(p)


def _locate_output_mp4(plan: Dict[str, Any], p_file: Path) -> Path:
    """Resolve the rendered 9:16 master MP4 whether in `plan['output']`, `output/`, or `~/Downloads/Zeno Outs/<FolderName>/`."""
    raw_out = plan.get("output")
    primary = resolve(raw_out) if raw_out else (OUTPUT / f"{p_file.parent.name}.mp4")
    if primary.is_file():
        return primary

    stem = strip_ratio_prefix(Path(str(raw_out)).stem) if raw_out else strip_ratio_prefix(p_file.parent.name)
    if not stem:
        stem = p_file.parent.name
    dl_root = Path(os.environ.get("ZENO_OUTPUT_DIR", "").strip() or (Path.home() / "Downloads" / "Zeno Outs")).expanduser()
    candidates = [
        dl_root / stem / f"reel_{stem}.mp4",
        OUTPUT / stem / f"reel_{stem}.mp4",
        ROOT / "output" / stem / f"reel_{stem}.mp4",
        ROOT / "output" / f"reel_{stem}.mp4",
        ROOT / "output" / f"{stem}.mp4",
        OUTPUT / f"reel_{stem}.mp4",
    ]
    for cand in candidates:
        if cand.is_file():
            return cand
    return primary


def _segment_hold_tail(seg: Dict[str, Any]) -> float:
    """Return freeze-tail hold duration (seconds) after source clip end."""
    fn = getattr(common, "segment_hold_tail", None)
    if callable(fn):
        try:
            return max(0.0, float(fn(seg) or 0.0))
        except Exception:
            pass
    try:
        return max(0.0, float(seg.get("hold_tail") or seg.get("hold") or 0.0))
    except (TypeError, ValueError):
        return 0.0


def _segment_speed(seg: Dict[str, Any]) -> float:
    """Return clamped playback speed factor (`common.segment_speed` with safe fallback)."""
    fn = getattr(common, "segment_speed", None)
    if callable(fn):
        try:
            return float(fn(seg))
        except Exception:
            pass
    if not isinstance(seg, dict):
        return 1.0
    try:
        val = float(seg.get("speed") if seg.get("speed") is not None else 1.0)
    except (TypeError, ValueError):
        return 1.0
    if not math.isfinite(val) or val <= 0.0:
        return 1.0
    return max(0.15, min(4.0, val))


def _segment_is_muted(seg: Dict[str, Any]) -> bool:
    """Return True when segment mutes source audio (`common.segment_is_muted` with safe fallback)."""
    fn = getattr(common, "segment_is_muted", None)
    if callable(fn):
        try:
            return bool(fn(seg))
        except Exception:
            pass
    if not isinstance(seg, dict):
        return False
    if bool(seg.get("mute")):
        return True
    am = str(seg.get("audio_mode") or seg.get("audio") or "").strip().lower()
    return am in ("mute", "off", "none")


def _segment_is_beat(seg: Dict[str, Any]) -> bool:
    """Return True for non-spoken visual beats (`common.segment_is_beat` with safe fallback)."""
    fn = getattr(common, "segment_is_beat", None)
    if callable(fn):
        try:
            return bool(fn(seg))
        except Exception:
            pass
    if not isinstance(seg, dict):
        return False
    return bool(seg.get("beat"))


def _segment_play_duration(seg: Dict[str, Any]) -> float:
    """Return rendered moving playback duration excluding `hold_tail` (`common.segment_play_duration` fallback)."""
    fn = getattr(common, "segment_play_duration", None)
    if callable(fn):
        try:
            return max(0.0, float(fn(seg) or 0.0))
        except Exception:
            pass
    src_dur = max(0.0, float(seg.get("end", 0.0) or 0.0) - float(seg.get("start", 0.0) or 0.0))
    return max(0.0, src_dur / _segment_speed(seg))


def _src_to_seg_time(seg: Dict[str, Any], src_t: float) -> float:
    """Map absolute source timestamp to segment-relative rendered seconds (`common.src_to_seg_time` fallback)."""
    fn = getattr(common, "src_to_seg_time", None)
    if callable(fn):
        try:
            return float(fn(seg, src_t))
        except Exception:
            pass
    return max(0.0, (float(src_t) - float(seg.get("start", 0.0) or 0.0)) / _segment_speed(seg))


def _seg_to_src_time(seg: Dict[str, Any], seg_t: float) -> float:
    """Map segment-relative rendered seconds back to absolute source timestamp (`common.seg_to_src_time` fallback)."""
    fn = getattr(common, "seg_to_src_time", None)
    if callable(fn):
        try:
            return float(fn(seg, seg_t))
        except Exception:
            pass
    return float(seg.get("start", 0.0) or 0.0) + max(0.0, float(seg_t)) * _segment_speed(seg)


def _segment_duration(seg: Dict[str, Any]) -> float:
    """Return effective timeline duration handling `speed` + `hold_tail` via `common.segment_duration`."""
    fn = getattr(common, "segment_duration", None)
    if callable(fn):
        if callable(getattr(common, "segment_speed", None)) or abs(_segment_speed(seg) - 1.0) <= 1e-6:
            try:
                return max(0.0, float(fn(seg) or 0.0))
            except Exception:
                pass
    return _segment_play_duration(seg) + _segment_hold_tail(seg)


def compute_timeline_starts(plan: Dict[str, Any], fps: int) -> Tuple[List[float], List[float], float, Dict[str, float]]:
    """Compute exact frame-rounded segment durations, timeline start offsets, and total duration."""
    segs = plan.get("segments", [])
    durs: List[float] = []
    for s in segs:
        dur = _segment_duration(s)
        frames = max(1, round(dur * fps))
        durs.append(frames / fps)

    starts: List[float] = []
    total = 0.0
    for i, _s in enumerate(segs):
        starts.append(total)
        total += durs[i]

    seg_start = {str(s.get("id", f"S{i + 1}")): starts[i] for i, s in enumerate(segs)}
    return starts, durs, total, seg_start


def check_loudness(mp4_path: Path, target_lufs: Optional[float] = None) -> Tuple[bool, float, float]:
    """Measure integrated loudness (LUFS) and True Peak (dBTP), caching by file mtime + size."""
    eff_target = (
        float(getattr(common, "DEFAULT_LOUDNESS_LUFS", DEFAULT_LOUDNESS_LUFS))
        if target_lufs is None
        else float(target_lufs)
    )
    cache_path = WORK / "_segments" / "loudness_cache.json"
    cache_data: Dict[str, Any] = {}
    if cache_path.is_file():
        try:
            cache_data = read_json(cache_path)
        except Exception:
            cache_data = {}

    stat = mp4_path.stat()
    key = f"{mp4_path.resolve()}:{stat.st_size}:{stat.st_mtime_ns}"
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
            f"loudnorm=I={eff_target}:TP=-1.0:LRA=11:print_format=json",
            "-f",
            "null",
            "-",
        ]
        r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
        m = re.search(r"\{[^{}]*\"input_i\"[^{}]*\}", r.stderr)
        if r.returncode == 0 and m:
            meas = json.loads(m.group(0))
            i_lufs = float(meas.get("input_i", "nan"))
            tp_db = float(meas.get("input_tp", "nan"))
            cache_data[key] = {"input_i": i_lufs, "input_tp": tp_db}
            try:
                write_json(cache_path, cache_data)
            except Exception:
                pass
        else:
            i_lufs, tp_db = float("nan"), float("nan")

    ok = abs(i_lufs - eff_target) <= 1.8 and tp_db <= -0.5
    return ok, i_lufs, tp_db


def _extract_zoom_pair(seg: Dict[str, Any]) -> Tuple[float, float]:
    """Parse raw un-clamped (z0, z1) numeric zoom factors (accounting for `zoom_peak` on `bell` curves)."""
    raw_z = seg.get("zoom", 1.0)
    z0, z1 = 1.0, 1.0
    z_peak: Optional[float] = None
    if isinstance(raw_z, (int, float)):
        z0 = z1 = float(raw_z)
    elif raw_z is not None:
        s_full = str(raw_z).strip()
        s = s_full.split("@", 1)[0].strip()
        prefix = ""
        if ":" in s:
            prefix, rhs = s.split(":", 1)
            prefix = prefix.strip().lower()
            s = rhs.strip()
        elif s.lower() in (
            "sudden_in", "punch_in", "push", "in", "zoom_in", "mild_in", "flash_zoom",
            "sudden_out", "punch_out", "pull", "out", "zoom_out", "mild_out", "bell", "smooth",
        ):
            prefix = s.lower()
            s = ""
        if "->" in s:
            parts = [p.strip() for p in s.split("->") if p.strip()]
            try:
                vals = [float(p) for p in parts]
                if len(vals) >= 3:
                    z0, z_peak, z1 = vals[0], vals[1], vals[-1]
                elif len(vals) == 2:
                    z0, z1 = vals[0], vals[1]
                elif len(vals) == 1:
                    z0 = z1 = vals[0]
            except ValueError:
                z0 = z1 = 1.0
        elif not s:
            if prefix in ("zoom_in", "mild_in"):
                z0, z1 = 1.0, 1.08
            elif prefix in ("zoom_out", "mild_out"):
                z0, z1 = 1.08, 1.0
            elif prefix in ("sudden_in", "punch_in", "push", "in"):
                z0, z1 = 1.0, 1.10
            elif prefix in ("sudden_out", "punch_out", "pull", "out"):
                z0, z1 = 1.10, 1.0
            elif prefix == "flash_zoom":
                z0, z1 = 1.0, 1.12
            elif prefix == "bell":
                z0, z_peak, z1 = 1.20, 1.55, 1.0
        else:
            try:
                v = float(s)
                if prefix in ("sudden_in", "punch_in", "push", "in", "zoom_in", "mild_in", "flash_zoom") or any(
                    k in s_full.lower() for k in ("sudden_in", "zoom_in", "mild_in", "push", "flash_zoom")
                ):
                    z0, z1 = 1.0, v
                elif prefix in ("sudden_out", "punch_out", "pull", "out", "zoom_out", "mild_out") or any(
                    k in s_full.lower() for k in ("sudden_out", "zoom_out", "mild_out", "pull")
                ):
                    z0, z1 = v, 1.0
                elif prefix == "bell":
                    z0, z_peak, z1 = 1.20, v, 1.0
                else:
                    z0 = z1 = v
            except ValueError:
                z0 = z1 = 1.0
    if seg.get("zoom_to") is not None:
        try:
            z1 = float(seg["zoom_to"])
        except (TypeError, ValueError):
            pass
    if seg.get("zoom_peak") is not None:
        try:
            z_peak = float(seg["zoom_peak"])
        except (TypeError, ValueError):
            pass
    if z_peak is not None:
        return min(z0, z1, z_peak), max(z0, z1, z_peak)
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


DEAD_AIR_THRESHOLD = 0.48
CLIPPED_WORD_MARGIN_SEC = 0.035
CLIPPED_WORD_RMS_FLOOR_DB = -42.0


def _resolve_clip_cache_dir(src: Any, plan: Optional[Dict[str, Any]] = None) -> Path:
    """Resolve a clip's cache folder via `clip_work_dir(src)` or fallback `clip_caches.json`."""
    wdir = clip_work_dir(src)
    if (wdir / "hq_words.json").is_file() or (wdir / "words.json").is_file() or (wdir / "audio16k.wav").is_file():
        return wdir
    if common.clip_work_dir is not clip_work_dir:
        wdir_common = common.clip_work_dir(src)
        if (
            (wdir_common / "hq_words.json").is_file()
            or (wdir_common / "words.json").is_file()
            or (wdir_common / "audio16k.wav").is_file()
        ):
            return wdir_common
    clip_cache_root = getattr(common, "CLIP_CACHE", WORK / "_clips")
    cand_dirs: List[Path] = []
    if isinstance(plan, dict) and plan.get("_plan_dir"):
        cand_dirs.append(Path(str(plan["_plan_dir"])))
    cand_dirs.append(WORK)
    for proj in cand_dirs:
        cmap_file = proj / "clip_caches.json"
        if not cmap_file.is_file():
            continue
        try:
            cmap = read_json(cmap_file)
            if isinstance(cmap, dict):
                p = Path(str(src or ""))
                for key in (str(src or ""), p.name, p.stem):
                    mapped = cmap.get(key)
                    if mapped:
                        mapped_p = Path(str(mapped))
                        cands = (
                            [mapped_p]
                            if mapped_p.is_absolute()
                            else [clip_cache_root / mapped_p, WORK / mapped_p, proj / mapped_p]
                        )
                        for cand in cands:
                            if cand.is_dir():
                                return cand
        except Exception:
            pass
    return wdir


# Where the cut check found its problems, for the console only. Filled by `check_cuts_and_pauses`;
# it never affects the counts or the pass/fail result.
_CUT_FAIL_NOTES: List[str] = []
_LAST_JOIN_GAP_STATS: Dict[str, Any] = {}


def _cut_note(msg: str) -> None:
    try:
        if len(_CUT_FAIL_NOTES) < 40:
            _CUT_FAIL_NOTES.append(str(msg))
    except Exception:
        pass


def _measure_pcm_join_gaps(
    pcm: array.array,
    segs: List[Dict[str, Any]],
    starts: List[float],
    timeline_mark_holds: List[Tuple[float, float]],
    sample_rate: int = 16000,
    voice_db_floor: float = -38.0,
    max_normal_join_ms: int = 380,
) -> Dict[str, Any]:
    """Measure audible silence spanning each spoken-to-spoken cut join directly from rendered 16 kHz PCM.

    Separates normal dry dialogue-to-dialogue joins (`gaps_ms`, flagging `> max_normal_join_ms` as `excessive_joins`)
    from intentional visual-beat / mark / hold-tail / transition joins (`beat_gaps_ms`).
    """
    if not pcm or len(segs) < 2 or len(starts) < 2:
        return {}
    n_samp = len(pcm)
    frame = max(1, int(round(0.01 * sample_rate)))  # 10 ms frame

    def _frame_db(sample_idx: int) -> float:
        lo = max(0, sample_idx)
        hi = min(n_samp, lo + frame)
        if hi <= lo:
            return -140.0
        sq = 0.0
        for k in range(lo, hi):
            v = pcm[k] / 32768.0
            sq += v * v
        rms = math.sqrt(sq / float(hi - lo))
        return 20.0 * math.log10(max(rms, 1e-7))

    def _stats(vals: List[int]) -> Tuple[int, int, int]:
        ordered = sorted(vals)
        n_v = len(ordered)
        med_v = (
            ordered[n_v // 2]
            if (n_v % 2 == 1)
            else int(round(0.5 * (ordered[n_v // 2 - 1] + ordered[n_v // 2])))
        )
        p90_v = ordered[min(n_v - 1, int(math.ceil(0.90 * n_v)) - 1)]
        return med_v, p90_v, ordered[-1]

    gaps_ms: List[int] = []
    beat_gaps_ms: List[int] = []
    excessive_joins: List[Dict[str, Any]] = []
    for idx in range(1, min(len(segs), len(starts))):
        prev_s = segs[idx - 1]
        cur_s = segs[idx]
        if _segment_is_muted(prev_s) or _segment_is_beat(prev_s) or _segment_is_muted(cur_s) or _segment_is_beat(cur_s):
            continue
        jt = float(starts[idx])

        has_live_ae_tail = False
        ae_raw = prev_s.get("audio_end")
        if ae_raw is not None and not isinstance(ae_raw, bool):
            try:
                ae_f = float(ae_raw)
                if ae_f > 0.04 and (_segment_play_duration(prev_s) - ae_f) > 0.05:
                    has_live_ae_tail = True
            except (TypeError, ValueError):
                pass
        tr_cur = str(cur_s.get("transition") or "").split("@")[0].strip().lower()
        is_beat_join = (
            _segment_hold_tail(prev_s) > 0.01
            or has_live_ae_tail
            or tr_cur not in ("", "cut", "none")
            or any(a - 0.05 <= jt <= b + 0.05 for a, b in timeline_mark_holds)
        )

        j_samp = int(round(jt * sample_rate))
        # Walk backward from jt (up to 800 ms) until audible voice >= voice_db_floor
        pre_frames = 0
        for step_k in range(1, 81):
            s0 = j_samp - step_k * frame
            if s0 < 0 or _frame_db(s0) >= voice_db_floor:
                break
            pre_frames += 1
        # Walk forward from jt (up to 800 ms) until audible voice >= voice_db_floor
        post_frames = 0
        for step_k in range(0, 80):
            s0 = j_samp + step_k * frame
            if s0 + frame > n_samp or _frame_db(s0) >= voice_db_floor:
                break
            post_frames += 1
        gap_ms = (pre_frames + post_frames) * 10
        if is_beat_join:
            beat_gaps_ms.append(gap_ms)
        else:
            gaps_ms.append(gap_ms)
            if gap_ms > int(max_normal_join_ms):
                excessive_joins.append({
                    "prev_segment": str(prev_s.get("id") or f"S{idx}"),
                    "segment": str(cur_s.get("id") or f"S{idx + 1}"),
                    "at_sec": round(jt, 2),
                    "gap_ms": gap_ms,
                })

    if not gaps_ms and not beat_gaps_ms:
        return {}
    out: Dict[str, Any] = {
        "count": len(gaps_ms),
        "gaps_ms": gaps_ms,
        "excessive_joins": excessive_joins,
        "beat_count": len(beat_gaps_ms),
        "beat_gaps_ms": beat_gaps_ms,
    }
    if gaps_ms:
        med, p90, mx = _stats(gaps_ms)
        out["median_ms"] = med
        out["p90_ms"] = p90
        out["max_ms"] = mx
    if beat_gaps_ms:
        b_med, _, b_mx = _stats(beat_gaps_ms)
        out["beat_median_ms"] = b_med
        out["beat_max_ms"] = b_mx
    return out


def check_cuts_and_pauses(plan: Dict[str, Any], mp4_path: Optional[Path] = None) -> Tuple[bool, int, int, int, int]:
    """Check cut boundaries against cached word spans + silences, plus rendered MP4 dead-air and boundary click scan.
    Returns (passed, clipped_words_count, long_pauses_count, masked_jump_cuts_count, boundary_clicks_count).
    """
    segs = plan.get("segments", [])
    clipped_words = 0
    long_pauses = 0
    jump_cuts_masked = 0
    boundary_clicks = 0
    del _CUT_FAIL_NOTES[:]
    _LAST_JOIN_GAP_STATS.clear()

    fps = int((plan.get("canvas") or {}).get("fps", 30) or 30)
    starts, seg_durs, _, seg_start_map = compute_timeline_starts(plan, fps)

    marks_by_sid: Dict[str, List[Dict[str, Any]]] = {}
    for mk in plan.get("marks", []) or []:
        msid = str(mk.get("segment") or "")
        if msid:
            marks_by_sid.setdefault(msid, []).append(mk)

    timeline_mark_holds: List[Tuple[float, float]] = []
    seg_speeds: List[float] = []

    for i, s in enumerate(segs):
        sid = str(s.get("id", f"S{i + 1}"))
        stem = Path(str(s.get("src", ""))).stem
        wdir = _resolve_clip_cache_dir(s.get("src", ""), plan)
        st, en = float(s["start"]), float(s["end"])
        spd = _segment_speed(s)
        seg_speeds.append(spd)
        play_dur = _segment_play_duration(s)
        hold_tail = _segment_hold_tail(s)
        eff_dur = seg_durs[i] if i < len(seg_durs) else _segment_duration(s)
        is_muted_or_beat = _segment_is_muted(s) or _segment_is_beat(s)

        if i > 0 and Path(str(segs[i - 1].get("src", ""))).stem == stem:
            z0, z1 = _extract_zoom_pair(s)
            if max(z0, z1) > 1.01 or s.get("layout", "single") != "single":
                jump_cuts_masked += 1

        # Build intentional mark flash hold windows in clip coordinates and timeline coordinates
        seg_marks = list(s.get("marks", []) or []) + list(marks_by_sid.get(sid, []))
        clip_mark_holds: List[Tuple[float, float]] = []
        t0_tl = starts[i] if i < len(starts) else float(seg_start_map.get(sid, 0.0))
        for mk in seg_marks:
            m_at = float(mk.get("at", max(0.0, eff_dur - 0.60)))
            m_dur = float(mk.get("duration", 0.56) or 0.56)
            hold_rel_s = max(0.0, m_at - 0.15)
            tl_hold_e = eff_dur if (m_at + m_dur >= eff_dur - 0.45) else min(eff_dur, m_at + m_dur + 0.35)
            clip_hold_s = min(en, _seg_to_src_time(s, hold_rel_s))
            clip_hold_e = en if (m_at + m_dur >= play_dur - 0.45) else min(en, _seg_to_src_time(s, m_at + m_dur + 0.35))
            clip_mark_holds.append((clip_hold_s, clip_hold_e))
            timeline_mark_holds.append((t0_tl + hold_rel_s, t0_tl + tl_hold_e))
        if hold_tail > 0.0:
            # Exempt freeze-tail hold beat from rendered MP4 dead-air silence detection using play_dur (not raw end-start)
            timeline_mark_holds.append((t0_tl + max(0.0, play_dur - 0.10), t0_tl + eff_dur))

        if is_muted_or_beat:
            # Exempt muted / visual-beat hook windows from rendered MP4 silencedetect & skip spoken silence/word checks
            timeline_mark_holds.append((t0_tl, t0_tl + eff_dur))
            continue

        # Scale silence thresholds by 1.0 / speed on unmuted slowed segments
        seg_dead_air = (DEAD_AIR_THRESHOLD / max(0.25, spd)) if spd < 1.0 else DEAD_AIR_THRESHOLD
        en_audio = en
        if s.get("audio_end") is not None and not isinstance(s.get("audio_end"), bool):
            try:
                ae_val = float(s["audio_end"])
                if ae_val > 0.0:
                    en_audio = min(en, st + ae_val * spd)
            except (TypeError, ValueError):
                pass

        # Check internal pauses inside [st, en_audio] (>seg_dead_air threshold, excluding mark flash holds)
        sil_path = wdir / "silences.json"
        if sil_path.is_file():
            try:
                raw_sils = read_json(sil_path)
                merged: List[Dict[str, float]] = []
                for sl in sorted(raw_sils, key=lambda x: float(x["start"])):
                    s0, s1 = float(sl["start"]), float(sl["end"])
                    if merged and s0 - merged[-1]["end"] <= 0.08:
                        merged[-1]["end"] = max(merged[-1]["end"], s1)
                    else:
                        merged.append({"start": s0, "end": s1})
                for sl in merged:
                    ov_s = max(st + 0.06, sl["start"])
                    ov_e = min(en_audio - 0.06, sl["end"])
                    for rem_s, rem_e in _subtract_intervals(ov_s, ov_e, clip_mark_holds):
                        if (rem_e - rem_s) > seg_dead_air:
                            long_pauses += 1
                            _cut_note(f"{sid} pause {rem_s:.2f}-{rem_e:.2f}s in {stem}")
            except Exception:
                pass

        # Check if cut boundary (including active audio_end boundary) slices through a kept word or continuous VAD speech
        audio_bounds: List[float] = [st, en_audio]
        words_path = wdir / "hq_words.json"
        if not words_path.is_file():
            words_path = wdir / "words.json"
        sp_path = wdir / "speech_spans.json"
        wav_path = wdir / "audio16k.wav"
        if (words_path.is_file() or sp_path.is_file()) and compute_10ms_rms_envelope is not None and wav_path.is_file():
            try:
                rms = compute_10ms_rms_envelope(wav_path)
                words = read_json(words_path) if words_path.is_file() else []
                if not isinstance(words, list):
                    words = []
                ghost_word_ids = (
                    {id(gw) for gw in _words_in_silence(words, rms)}
                    if (_words_in_silence is not None and words and rms)
                    else set()
                )
                live_words = sorted(
                    (
                        w
                        for w in words
                        if isinstance(w, dict) and not w.get("no_sound") and id(w) not in ghost_word_ids
                    ),
                    key=lambda w: (float(w.get("start", 0.0)), float(w.get("end", 0.0))),
                )
                vad_spans: List[Tuple[float, float]] = []
                if sp_path.is_file():
                    sdata = read_json(sp_path)
                    raw_sp = sdata.get("spans") if isinstance(sdata, dict) else sdata
                    if isinstance(raw_sp, list):
                        vad_spans = [
                            (float(p[0]), float(p[1]))
                            for p in raw_sp
                            if isinstance(p, (list, tuple)) and len(p) >= 2 and float(p[1]) > float(p[0])
                        ]

                for boundary_t in audio_bounds:
                    if boundary_t <= 0.05:
                        continue
                    crossing = False
                    for w in live_words:
                        w_s = float(w.get("start", 0.0))
                        w_e = float(w.get("end", 0.0))
                        w_dur = max(0.0, w_e - w_s)
                        if w_dur <= 0.02:
                            continue
                        w_margin = (
                            min(CLIPPED_WORD_MARGIN_SEC, 0.25 * w_dur)
                            if w_dur < 0.14
                            else CLIPPED_WORD_MARGIN_SEC
                        )
                        if w_s + w_margin < boundary_t < w_e - w_margin:
                            crossing = True
                            break
                    if crossing and rms:
                        near_db = [db for t, db in rms if abs(t - boundary_t) <= 0.025]
                        if near_db and max(near_db) > CLIPPED_WORD_RMS_FLOOR_DB:
                            clipped_words += 1
                            _cut_note(f"{sid} cut at {boundary_t:.2f}s is inside a word in {stem}")
                            continue
                    if vad_spans and rms:
                        in_vad_interior = any(
                            sp0 + 0.12 < boundary_t < sp1 - 0.20
                            for sp0, sp1 in vad_spans
                        )
                        if in_vad_interior:
                            near_db_vad = [db for t, db in rms if abs(t - boundary_t) <= 0.025]
                            if near_db_vad and max(near_db_vad) > -38.0:
                                in_verified_gap = any(
                                    float(live_words[k]["end"]) - 0.02 <= boundary_t <= float(live_words[k + 1]["start"]) + 0.02
                                    and (float(live_words[k + 1]["start"]) - float(live_words[k]["end"])) <= 0.85
                                    for k in range(len(live_words) - 1)
                                )
                                if not in_verified_gap:
                                    clipped_words += 1
                                    _cut_note(f"{sid} cut at {boundary_t:.2f}s is inside continuous speech in {stem}")
            except Exception:
                pass

    # Scan the rendered MP4 audio stream directly for any internal dead air >0.60s inside cuts
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
                    ov_s = max(t0_s + 0.06, s_start)
                    ov_e = min(t1_s - 0.06, s_end)
                    spd_s = seg_speeds[idx_s] if idx_s < len(seg_speeds) else 1.0
                    seg_thresh = (DEAD_AIR_THRESHOLD / max(0.25, spd_s)) if spd_s < 1.0 else DEAD_AIR_THRESHOLD
                    for rem_s, rem_e in _subtract_intervals(ov_s, ov_e, timeline_mark_holds):
                        if (rem_e - rem_s) > seg_thresh:
                            long_pauses += 1
                            _cut_note(f"cut #{idx_s + 1} silence at {rem_s:.2f}-{rem_e:.2f}s of the finished video")
        except Exception:
            pass

    # Inspect ±8ms windows around each inter-segment splice for full-scale step clicks
    if mp4_path is not None and mp4_path.is_file() and len(segs) > 1:
        try:
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
                            _cut_note(f"click at the join at {jt:.2f}s of the finished video")
                _LAST_JOIN_GAP_STATS.update(
                    _measure_pcm_join_gaps(pcm, segs, starts, timeline_mark_holds, sample_rate=16000)
                )
        except Exception:
            pass

    excessive_joins = _LAST_JOIN_GAP_STATS.get("excessive_joins") or []
    for ej in excessive_joins:
        _cut_note(
            f"loose join {ej['prev_segment']} -> {ej['segment']} at {ej['at_sec']:.2f}s: "
            f"{ej['gap_ms']}ms (>380ms)"
        )
    passed = (
        clipped_words == 0
        and long_pauses == 0
        and boundary_clicks == 0
        and not excessive_joins
    )
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
    hq_words.json / words.json against the script (avoiding re-transcription).
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
    except Exception:  # noqa: BLE001
        _so = None  # type: ignore[assignment]
        accepted = set()

    def _is_accepted_ref(ref_text: str) -> bool:
        if not accepted or not ref_text:
            return False
        if ref_text in accepted:
            return True
        norm_ref = ref_text.replace("_", " ")
        return any(
            re.search(rf"\b{re.escape(k)}(?:[a-z]\d*)?\b", ref_text)
            or re.search(rf"\b{re.escape(k)}(?:[a-z]\d*)?\b", norm_ref)
            for k in accepted
        )

    def _extract_explicit_line_ids(text: str, valid_ids: Optional[set[str]] = None) -> List[str]:
        if not text:
            return []
        out_ids: List[str] = []

        def _add(lid_cand: str) -> None:
            if lid_cand and (not valid_ids or lid_cand in valid_ids) and lid_cand not in out_ids:
                out_ids.append(lid_cand)

        for m_rng in re.finditer(
            r"(?<![A-Za-z0-9_-])L(\d+)(?:[a-z]\d*)?[_-]L(\d+)(?:[a-z]\d*)?(?![A-Za-z0-9_-])",
            text,
        ):
            a_num, b_num = int(m_rng.group(1)), int(m_rng.group(2))
            if 0 < b_num - a_num <= 6:
                for n_lid in range(a_num, b_num + 1):
                    _add(f"L{n_lid}")

        if _so is not None and hasattr(_so, "_line_ids_of"):
            for m_lid in _so._line_ids_of(text):
                _add(m_lid)

        for m_lid in re.findall(r"\b(L\d+)(?:[a-z]\d*)?\b", text.replace("_", " ")):
            _add(m_lid)

        return out_ids

    segs = plan.get("segments", [])
    seg_word_slices: List[List[str]] = []
    seg_word_keys: List[List[Tuple[str, float]]] = []

    _env_cache: Dict[str, List[Tuple[float, float]]] = {}
    for s in segs:
        if _segment_is_muted(s) or _segment_is_beat(s):
            seg_word_slices.append([])
            seg_word_keys.append([])
            continue
        stem = Path(str(s.get("src", ""))).stem
        wdir = _resolve_clip_cache_dir(s.get("src", ""), plan)
        st, en = float(s["start"]), float(s["end"])
        en_eff = en
        if s.get("audio_end") is not None:
            try:
                ae_f = float(s["audio_end"])
                if ae_f > 0:
                    en_eff = min(en, st + ae_f * _segment_speed(s))
            except Exception:
                pass
        wdir_key = str(wdir)
        if wdir_key not in _env_cache:
            wav_p = wdir / "audio16k.wav"
            env_val: List[Tuple[float, float]] = []
            if compute_10ms_rms_envelope is not None and wav_p.is_file():
                try:
                    env_val = list(compute_10ms_rms_envelope(wav_p) or [])
                except Exception:
                    env_val = []
            _env_cache[wdir_key] = env_val
        env = _env_cache[wdir_key]
        words: List[Dict[str, Any]] = []
        for fname in ("hq_words.json", "words.json"):
            fpath = wdir / fname
            if fpath.is_file():
                try:
                    wlist = read_json(fpath)
                    sliced = [
                        w for w in wlist
                        if float(w.get("end", 0.0)) > st + 0.08 and float(w.get("start", 0.0)) < en_eff - 0.08
                    ]
                    if sliced:
                        words.extend(sliced)
                        break
                except Exception:
                    pass
        if not words and isinstance(s.get("words"), list):
            for idx_w, w_item in enumerate(s["words"]):
                if isinstance(w_item, dict):
                    if not w_item.get("no_sound"):
                        words.append(w_item)
                elif isinstance(w_item, str):
                    words.append({"w": w_item, "start": st + idx_w * 0.1, "end": st + (idx_w + 1) * 0.1})
        bad_ids = set()
        if _words_in_silence is not None and env and words:
            try:
                bad_ids = {id(w) for w in _words_in_silence(words, env)}
            except Exception:
                bad_ids = set()
        kept: List[Dict[str, Any]] = []
        for w in words:
            if not _clean_token(w.get("w") or w.get("word", "")):
                continue
            if w.get("no_sound") or id(w) in bad_ids:
                continue
            if env:
                ws0 = float(w.get("start", st))
                ws1 = float(w.get("end", en_eff))
                a0 = max(st, ws0)
                a1 = min(en_eff, ws1)
                if a1 > a0:
                    seg_db = [db for t_s, db in env if a0 - 0.01 <= t_s <= a1 + 0.01]
                    if seg_db and max(seg_db) < -55.0:
                        continue
            kept.append(w)
        toks = [_clean_token(w.get("w") or w.get("word", "")) for w in kept]
        seg_word_slices.append(toks)
        seg_word_keys.append([(stem, round(float(w.get("start", 0.0)), 3)) for w in kept])

    line_by_id: Dict[str, Dict[str, Any]] = {
        str(sl.get("id", "")): sl for sl in script_lines if sl.get("id")
    }
    valid_lids = set(line_by_id) if line_by_id else None
    explicit_by_seg: List[List[str]] = []
    seg_line_ids: List[List[str]] = []
    for idx, s in enumerate(segs):
        is_mb = _segment_is_muted(s) or _segment_is_beat(s)
        sid_note = str(s.get("id", "")) if is_mb else f"{s.get('id', '')} {s.get('note', '')}"
        explicit_lids = _extract_explicit_line_ids(sid_note, valid_ids=valid_lids)
        explicit_by_seg.append(list(explicit_lids))
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

    # 1. Intra-segment 2..6-word phrase stumbles and 1-word stutters
    for idx, toks in enumerate(seg_word_slices):
        sid = str(segs[idx].get("id", f"S{idx + 1}"))
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
        sid_a = str(segs[i].get("id", f"S{i + 1}"))
        sid_b = str(segs[i + 1].get("id", f"S{i + 2}"))
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

    # 3. Integrate script_order.repeat_problems if available
    if _so is not None and hasattr(_so, "repeat_problems"):
        try:
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
                pr_str = str(pr.get("message", str(pr))) if isinstance(pr, dict) else str(pr)
                pr_id = str(pr.get("id", "")) if isinstance(pr, dict) else ""
                if (
                    not _is_accepted_ref(pr_str)
                    and not (pr_id and _is_accepted_ref(pr_id))
                    and pr_str not in repeated_words
                ):
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
    all_notes = " ".join(
        str(s.get("id", ""))
        + ("" if (_segment_is_muted(s) or _segment_is_beat(s)) else (" " + str(s.get("note", ""))))
        for s in segs
    )
    all_notes_norm = all_notes.replace("_", " ")
    all_explicit_lids = {lid_item for grp in explicit_by_seg for lid_item in grp}

    def _calc_recall(have_toks: List[str], need_toks: List[str]) -> float:
        if not need_toks or not have_toks:
            return 0.0
        if _so is not None and hasattr(_so, "_multiset_recall"):
            try:
                return max(
                    float(_so._multiset_recall(have_toks, need_toks)),
                    float(_so.recall(have_toks, need_toks)),
                )
            except Exception:
                pass
        hits_val = sum(
            1.0
            for st in need_toks
            if max((_word_similarity(st, ct) for ct in have_toks), default=0.0) >= 0.64
        )
        return hits_val / len(need_toks)

    covered = 0
    last_seg_idx = 0
    for sline in script_lines:
        lid = sline["id"]
        stokens = sline["tokens"]
        if lid in accepted or _is_accepted_ref(lid):
            covered += 1
            continue
        matched_seg = None
        claiming_idxs: List[int] = []
        for idx in range(last_seg_idx, len(segs)):
            is_mb_idx = _segment_is_muted(segs[idx]) or _segment_is_beat(segs[idx])
            sid_str = str(segs[idx].get("id", ""))
            note_str = "" if is_mb_idx else str(segs[idx].get("note", ""))
            sid_note_norm = (sid_str + " " + note_str).replace("_", " ")
            if lid in explicit_by_seg[idx] or re.search(rf"\b{re.escape(lid)}(?:[a-z]\d*)?\b", sid_note_norm):
                claiming_idxs.append(idx)

        if claiming_idxs:
            if any(_is_accepted_ref(str(segs[j].get("id", ""))) for j in claiming_idxs):
                matched_seg = claiming_idxs[0]
            else:
                claimed_toks = [t for j in claiming_idxs for t in seg_word_slices[j]]
                if not stokens or not claimed_toks:
                    matched_seg = claiming_idxs[0]
                elif _calc_recall(claimed_toks, stokens) >= 0.50:
                    matched_seg = claiming_idxs[0]

        if matched_seg is None:
            claiming_set = set(claiming_idxs)
            for idx in range(last_seg_idx, len(segs)):
                if idx in claiming_set:
                    continue
                stoks_seg = seg_word_slices[idx]
                if stokens and stoks_seg and _calc_recall(stoks_seg, stokens) >= 0.50:
                    matched_seg = idx
                    break

        if matched_seg is not None:
            covered += 1
            last_seg_idx = matched_seg
        elif not claiming_idxs and stokens and all_kept_tokens:
            if _calc_recall(all_kept_tokens, stokens) >= 0.50:
                covered += 1

    total_lines = len(script_lines)
    passed = (covered == total_lines and boundary_dups == 0)
    try:
        import script_order

        rep = script_order.check_order([(str(s.get("id", "")), seg_word_slices[i]) for i, s in enumerate(segs)], script_lines)
        accepted_ord = script_order.read_accepted(Path(plan["_plan_dir"])) if plan.get("_plan_dir") else {}
        wrong = [
            m for m in rep["out_of_order"] + rep["repeated"] + rep["missing"]
            if not any(
                re.search(rf"\b{re.escape(k)}(?:[a-z]\d*)?\b", m)
                or re.search(rf"\b{re.escape(k)}(?:[a-z]\d*)?\b", m.replace("_", " "))
                for k in accepted_ord
            )
        ]
        if wrong:
            passed = False
            print("[ORDER FAIL] " + "; ".join(wrong))
    except Exception as exc:  # noqa: BLE001
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


def _read_rgb_frame(mp4_path: Path, t_sec: float, W: int, H: int) -> bytes:
    """Decode a single `W x H` RGB24 frame from `mp4_path` at `t_sec` seconds."""
    cmd = [
        str(FFMPEG),
        "-hide_banner",
        "-loglevel",
        "error",
        "-ss",
        f"{max(0.0, float(t_sec)):.3f}",
        "-i",
        str(mp4_path),
        "-vf",
        f"scale={int(W)}:{int(H)}",
        "-frames:v",
        "1",
        "-f",
        "rawvideo",
        "-pix_fmt",
        "rgb24",
        "-",
    ]
    return subprocess.run(cmd, capture_output=True).stdout or b""


def _collect_deduped_marks(plan: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Collect both top-level `plan['marks']` and inline `seg['marks']`, deduped by `(segment, round(at,2), type)`."""
    raw_marks = list(plan.get("marks", []) or [])
    for seg in plan.get("segments", []) or []:
        if not isinstance(seg, dict):
            continue
        sid_seg = str(seg.get("id", ""))
        for m_in in seg.get("marks", []) or []:
            if isinstance(m_in, dict):
                mc = dict(m_in)
                if not mc.get("segment") and sid_seg:
                    mc["segment"] = sid_seg
                raw_marks.append(mc)

    deduped: List[Dict[str, Any]] = []
    seen: set[Tuple[str, float, str]] = set()
    for m in raw_marks:
        if not isinstance(m, dict):
            continue
        mtype_k = _normalize_mark_type(m.get("type") or m.get("kind") or "right")
        sid_k = str(m.get("segment", ""))
        at_k = round(float(m.get("at", 0.0) or 0.0), 2)
        key = (sid_k, at_k, mtype_k)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(m)
    return deduped


def _evaluate_strobe_frame(
    mp4_path: Path,
    t_probe: float,
    W: int,
    H: int,
    mtype: str,
    exclude_boxes: Sequence[Tuple[float, float, float, float]],
    t_ref: Optional[float] = None,
) -> Tuple[bool, bool, float, float, int]:
    """Sample outer 12% perimeter border bands and verify inner 35% center remains clear.
    Returns `(edge_ok, center_clear, avg_r, avg_g, chroma_count)`.
    """
    expected_len = int(W) * int(H) * 3
    raw = _read_rgb_frame(mp4_path, t_probe, W, H)
    if expected_len <= 0 or len(raw) < expected_len:
        return False, False, 0.0, 0.0, 0

    try:
        import numpy as np
    except Exception:
        np = None  # type: ignore[assignment]

    if np is not None:
        frame = np.frombuffer(raw[:expected_len], dtype=np.uint8).reshape(H, W, 3)
        bh = max(1, int(round(H * 0.12)))
        bw = max(1, int(round(W * 0.12)))
        border_mask = np.zeros((H, W), dtype=bool)
        border_mask[:bh, :] = True
        border_mask[H - bh :, :] = True
        border_mask[:, :bw] = True
        border_mask[:, W - bw :] = True
        for bx0, by0, bx1, by1 in exclude_boxes:
            ex0 = max(0, int(math.floor(bx0 - 4.0)))
            ey0 = max(0, int(math.floor(by0 - 4.0)))
            ex1 = min(W, int(math.ceil(bx1 + 4.0)))
            ey1 = min(H, int(math.ceil(by1 + 4.0)))
            if ex1 > ex0 and ey1 > ey0:
                border_mask[ey0:ey1, ex0:ex1] = False

        edge_px = frame[border_mask]
        chroma = (edge_px.max(axis=1) - edge_px.min(axis=1)) > 30
        edge_count = int(chroma.sum())
        avg_r = float(edge_px[chroma, 0].mean()) if edge_count > 0 else 0.0
        avg_g = float(edge_px[chroma, 1].mean()) if edge_count > 0 else 0.0
        dom_diff = (avg_r - avg_g) if mtype == "wrong" else (avg_g - avg_r)
        edge_ok = edge_count >= 100 and dom_diff >= 20.0

        cx0, cx1 = int(round(W * 0.325)), int(round(W * 0.675))
        cy0, cy1 = int(round(H * 0.325)), int(round(H * 0.675))
        center_mask = np.ones((max(1, cy1 - cy0), max(1, cx1 - cx0)), dtype=bool)
        for bx0, by0, bx1, by1 in exclude_boxes:
            ix0 = max(cx0, int(math.floor(bx0 - 40.0))) - cx0
            iy0 = max(cy0, int(math.floor(by0 - 8.0))) - cy0
            ix1 = min(cx1, int(math.ceil(bx1 + 40.0))) - cx0
            iy1 = min(cy1, int(math.ceil(by1 + 8.0))) - cy0
            if ix1 > ix0 and iy1 > iy0:
                center_mask[iy0:iy1, ix0:ix1] = False

        center_px = frame[cy0:cy1, cx0:cx1][center_mask]
        center_clear = True
        if len(center_px) > 0:
            c_r = center_px[:, 0].astype(np.float32)
            c_g = center_px[:, 1].astype(np.float32)
            c_dom = (c_r - c_g) if mtype == "wrong" else (c_g - c_r)
            tinted_share = float((c_dom >= 18.0).mean())
            med_dom = float(np.median(c_dom))
            if tinted_share >= 0.65 and med_dom >= 18.0:
                if t_ref is not None:
                    ref_raw = _read_rgb_frame(mp4_path, t_ref, W, H)
                    if len(ref_raw) >= expected_len:
                        ref_frame = np.frombuffer(ref_raw[:expected_len], dtype=np.uint8).reshape(H, W, 3)
                        ref_px = ref_frame[cy0:cy1, cx0:cx1][center_mask]
                        ref_dom = (
                            (ref_px[:, 0].astype(np.float32) - ref_px[:, 1].astype(np.float32))
                            if mtype == "wrong"
                            else (ref_px[:, 1].astype(np.float32) - ref_px[:, 0].astype(np.float32))
                        )
                        if med_dom - float(np.median(ref_dom)) < 10.0:
                            center_clear = True
                        else:
                            center_clear = False
                    else:
                        center_clear = False
                else:
                    center_clear = False
        return edge_ok, center_clear, avg_r, avg_g, edge_count

    # Pure-Python strided fallback when numpy is unavailable
    bh = max(1, int(round(H * 0.12)))
    bw = max(1, int(round(W * 0.12)))
    r_vals: List[int] = []
    g_vals: List[int] = []
    for y in range(0, H, 2):
        row_off = y * W * 3
        in_y = y < bh or y >= H - bh
        for x in range(0, W, 2):
            if in_y or x < bw or x >= W - bw:
                idx = row_off + x * 3
                r, g, b = raw[idx], raw[idx + 1], raw[idx + 2]
                if max(r, g, b) - min(r, g, b) > 30:
                    r_vals.append(r)
                    g_vals.append(g)
    avg_r = sum(r_vals) / len(r_vals) if r_vals else 0.0
    avg_g = sum(g_vals) / len(g_vals) if g_vals else 0.0
    dom_diff = (avg_r - avg_g) if mtype == "wrong" else (avg_g - avg_r)
    edge_ok = len(r_vals) >= 25 and dom_diff >= 20.0
    return edge_ok, True, avg_r, avg_g, len(r_vals)


def verify_marks_and_build_contact_sheet(
    mp4_path: Path,
    plan: Dict[str, Any],
    seg_start: Dict[str, float],
    W: int,
    H: int,
    contact_sheet_path: Path,
) -> Tuple[bool, int, int, List[str], int]:
    """Perform deterministic RGB pixel check on every mark frame AND build a single
    compact contact sheet (<95 KB) of all mark frames + jump-cut frames using FFmpeg `tile`.
    """
    marks = _collect_deduped_marks(plan)
    segs = plan.get("segments", [])
    seg_by_id: Dict[str, Dict[str, Any]] = {
        str(s.get("id", f"S{i + 1}")): s for i, s in enumerate(segs) if isinstance(s, dict)
    }
    fps = int(round(float((plan.get("canvas") or {}).get("fps") or 30) or 30))

    try:
        import layouts as _layouts_mod
    except Exception:
        _layouts_mod = None  # type: ignore[assignment]

    marks_by_sid: Dict[str, List[Tuple[float, float]]] = {}
    for mk in marks:
        msid = str(mk.get("segment", ""))
        m_at_v = float(mk.get("at", 0.0) or 0.0)
        m_dur_v = float(mk.get("duration", 0.56) or 0.56)
        marks_by_sid.setdefault(msid, []).append((m_at_v - 0.05, m_at_v + m_dur_v + 0.05))

    mark_results: List[str] = []
    passed_marks = 0
    sample_events: List[Tuple[float, str]] = []

    default_pack = str(plan.get("mark_pack") or "flash").strip().lower()
    strobe_styles = {"flash", "vignette", "kn20228", "w42", "strobe"}
    fc_meta_m, _ = _load_flashcards_meta(plan, mp4_path, is_square=False, plan_dir=contact_sheet_path.parent)
    fc_cards_m = (fc_meta_m.get("cards") or []) if isinstance(fc_meta_m, dict) else []
    fc_cw_m = float((fc_meta_m or {}).get("canvas_w") or W or 1)
    fc_ch_m = float((fc_meta_m or {}).get("canvas_h") or H or 1)

    for m in marks:
        mtype = _normalize_mark_type(m.get("type") or m.get("kind") or "right")
        sid = str(m.get("segment", ""))
        seg_t0 = float(seg_start.get(sid, 0.0))
        m_at = float(m.get("at", 0.0) or 0.0)
        t_start = seg_t0 + m_at
        dur = float(m.get("duration", 1.1) or 1.1)
        style_key = str(m.get("style") or m.get("pack") or default_pack).strip().lower()
        is_strobe = style_key in strobe_styles or bool(m.get("strobe", False))
        t_probe = t_start + (min(0.06, max(0.02, dur * 0.35)) if is_strobe else min(0.35, dur * 0.4))
        sample_events.append((t_probe, f"{mtype.upper()}@{tc(t_start)}"))

        if is_strobe:
            exclude_boxes: List[Tuple[float, float, float, float]] = []
            t_ref: Optional[float] = None
            seg_obj = seg_by_id.get(sid)
            if seg_obj is not None:
                seg_eff = max(1, round(_segment_duration(seg_obj) * fps)) / fps
                if _layouts_mod is not None and hasattr(_layouts_mod, "banner_boxes"):
                    try:
                        for bx in _layouts_mod.banner_boxes(seg_obj, W, H, seg_eff):
                            if float(bx.get("t0", 0.0)) - 0.05 <= m_at + 0.06 <= float(bx.get("t1", 0.0)) + 0.05:
                                exclude_boxes.append((float(bx["x0"]), float(bx["y0"]), float(bx["x1"]), float(bx["y1"])))
                    except Exception:
                        pass
                seg_mark_wins = marks_by_sid.get(sid, [])
                for cand_rel in (m_at - 0.14, m_at + dur + 0.14):
                    if 0.03 <= cand_rel <= seg_eff - 0.05 and not any(a <= cand_rel <= b for a, b in seg_mark_wins):
                        t_ref = seg_t0 + cand_rel
                        break
            for fc_c in fc_cards_m:
                if not isinstance(fc_c, dict):
                    continue
                fc_s = float(fc_c.get("start") if fc_c.get("start") is not None else fc_c.get("t0", 0.0))
                fc_e = float(fc_c.get("end") if fc_c.get("end") is not None else fc_c.get("t1", 0.0))
                if fc_s - 0.05 <= t_probe <= fc_e + 0.05:
                    fx0 = float(fc_c.get("x0", 0.0)) * (W / max(1.0, fc_cw_m))
                    fy0 = float(fc_c.get("y0", 0.0)) * (H / max(1.0, fc_ch_m))
                    fx1 = float(fc_c.get("x1") if fc_c.get("x1") is not None else float(fc_c.get("x0", 0.0)) + float(fc_c.get("w", 0.0))) * (W / max(1.0, fc_cw_m))
                    fy1 = float(fc_c.get("y1") if fc_c.get("y1") is not None else float(fc_c.get("y0", 0.0)) + float(fc_c.get("h", 0.0))) * (H / max(1.0, fc_ch_m))
                    if fx1 > fx0 and fy1 > fy0:
                        exclude_boxes.append((fx0, fy0, fx1, fy1))

            edge_ok, center_clear, avg_r, avg_g, _cnt = _evaluate_strobe_frame(
                mp4_path, t_probe, W, H, mtype, exclude_boxes, t_ref=t_ref
            )
            diff_dom = (avg_r - avg_g) if mtype == "wrong" else (avg_g - avg_r)
            if edge_ok and center_clear:
                passed_marks += 1
                lbl_diff = "R-G" if mtype == "wrong" else "G-R"
                mark_results.append(f"{mtype}@{tc(t_start)} {lbl_diff}=+{diff_dom:.0f}[STROBE]")
            elif edge_ok and not center_clear:
                mark_results.append(f"{mtype}@{tc(t_start)} FAIL(center_not_clear,R={avg_r:.0f},G={avg_g:.0f})")
            else:
                mark_results.append(f"{mtype}@{tc(t_start)} FAIL(R={avg_r:.0f},G={avg_g:.0f})")
        else:
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
                mark_results.append(f"wrong@{tc(t_start)} R-G=+{diff_rg:.0f}")
            elif mtype == "right" and len(g_vals) >= 100 and diff_gr >= 35.0:
                passed_marks += 1
                mark_results.append(f"right@{tc(t_start)} G-R=+{diff_gr:.0f}")
            else:
                mark_results.append(f"{mtype}@{tc(t_start)} FAIL(R={avg_r:.0f},G={avg_g:.0f})")

    # Add jump-cut frames (where consecutive segments share the same source clip)
    for i in range(1, len(segs)):
        stem_prev = Path(str(segs[i - 1].get("src", ""))).stem
        stem_curr = Path(str(segs[i].get("src", ""))).stem
        if stem_prev == stem_curr:
            sid = str(segs[i].get("id", f"S{i + 1}"))
            t_jc = float(seg_start.get(sid, 0.0)) + 0.15
            sample_events.append((t_jc, f"JUMPCUT:{sid}@{tc(t_jc)}"))

    if not sample_events:
        sample_events.append((0.5, "FRAME@00:00.50"))

    # Build one single 2D contact sheet (max side <= 1024 px) using FFmpeg tile filter
    n_tiles = len(sample_events)
    cols = min(max(1, n_tiles), 6)
    rows = math.ceil(max(1, n_tiles) / cols)
    tile_w = 180
    tile_h = int(round(tile_w * H / max(1, W) / 2)) * 2
    inputs: List[str] = []
    fc_parts: List[str] = []
    for idx, (t_ev, _) in enumerate(sample_events):
        inputs.extend(["-ss", f"{t_ev:.3f}", "-i", str(mp4_path)])
        fc_parts.append(f"[{idx}:v]trim=end_frame=1,setpts=PTS-STARTPTS,scale={tile_w}:{tile_h},setsar=1[v{idx}]")

    concat_in = "".join(f"[v{idx}]" for idx in range(n_tiles))
    grid_w = cols * tile_w + max(0, cols - 1) * 4 + 8
    grid_h = rows * tile_h + max(0, rows - 1) * 4 + 8
    scale_tail = ""
    if grid_w > 1024 or grid_h > 1024:
        scale_tail = ",scale='min(1024,iw)':-2" if grid_w >= grid_h else ",scale=-2:'min(1024,ih)'"
    fc_parts.append(
        f"{concat_in}concat=n={n_tiles}:v=1:a=0,"
        f"tile={cols}x{rows}:padding=4:margin=4:color=0x141414{scale_tail}[out]"
    )

    contact_sheet_path.parent.mkdir(parents=True, exist_ok=True)
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
        if contact_sheet_path.is_file() and contact_sheet_path.stat().st_size < 95 * 1024:
            break

    sheet_exists = contact_sheet_path.is_file() and contact_sheet_path.stat().st_size > 0
    sheet_kb = max(1, int(round(contact_sheet_path.stat().st_size / 1024.0))) if sheet_exists else 0
    all_marks_ok = (passed_marks == len(marks)) and sheet_exists
    return all_marks_ok, passed_marks, len(marks), mark_results, sheet_kb


SPLIT_MAX_ZOOM = 1.0


def check_split_and_hook_rules(plan: Dict[str, Any]) -> Tuple[bool, str]:
    """Verify split-screen and single-character hook rules on `plan`."""
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

    try:
        from vision_tracker import compute_paired_split_crop_filters
    except Exception:
        compute_paired_split_crop_filters = None  # type: ignore[assignment]

    _paired_params: set[str] = set()
    if compute_paired_split_crop_filters is not None:
        try:
            _paired_params = set(inspect.signature(compute_paired_split_crop_filters).parameters.keys())
        except Exception:
            _paired_params = set()

    canvas = plan.get("canvas") or {}
    canvas_w = int(canvas.get("width") or 1080)
    canvas_h = int(canvas.get("height") or 1920)
    dim_cache: Dict[str, Tuple[int, int]] = {}

    def _clip_dims(path_obj: Path) -> Tuple[int, int]:
        key = str(path_obj)
        if key not in dim_cache:
            if path_obj.is_file():
                try:
                    inf = probe(path_obj)
                    w_i = int(inf.get("width") or 0)
                    h_i = int(inf.get("height") or 0)
                    dim_cache[key] = (w_i, h_i) if (w_i > 0 and h_i > 0) else (720, 1280)
                except Exception:
                    dim_cache[key] = (720, 1280)
            else:
                dim_cache[key] = (720, 1280)
        return dim_cache[key]

    observed_headrooms: List[float] = []
    slot_samples: Dict[Tuple[str, str], List[Tuple[str, float, float]]] = {}

    for i, s in enumerate(segs):
        sid = str(s.get("id", f"S{i + 1}"))
        lay = str(s.get("layout") or "single").strip().lower()
        prev_lay = str(segs[i - 1].get("layout") or "single").strip().lower() if i > 0 else "single"
        tr = str(s.get("transition") or "cut").strip().lower()

        if lay != "single":
            in_initial_hook = False

        if in_initial_hook:
            hook_secs += _segment_duration(s)
        if in_initial_hook and i > 0 and tr != "cut":
            hook_transitions += 1

        if lay in ("split_h", "split_v", "left_right", "top_bottom"):
            split_segs += 1
            z0, z1 = _extract_zoom_pair(s)
            if min(z0, z1) < 0.995 or max(z0, z1) > SPLIT_MAX_ZOOM + 0.005:
                split_violations.append(f"{sid}:excessive_split_zoom({z0:.2f}->{z1:.2f})")
            if i > 0 and prev_lay == lay:
                if tr != "cut":
                    split_violations.append(f"{sid}:intra_split_transition({tr})")
                if sid in whoosh_segs:
                    split_violations.append(f"{sid}:intra_split_whoosh")
            if compute_paired_split_crop_filters is not None:
                peer = s.get("peer") if isinstance(s.get("peer"), dict) else {}
                if peer and peer.get("src"):
                    lay_norm = "split_h" if lay in ("split_h", "left_right") else "split_v"
                    slot_w, slot_h = (canvas_w // 2, canvas_h) if lay_norm == "split_h" else (canvas_w, canvas_h // 2)
                    def_live = "left" if lay_norm == "split_h" else "top"
                    def_peer = "right" if lay_norm == "split_h" else "bottom"
                    live_slot = str(s.get("slot") or def_live).strip().lower()
                    peer_slot = str(peer.get("slot") or (def_peer if live_slot == def_live else def_live)).strip().lower()
                    try:
                        live_off_y = float(s.get("offset_y") or 0.0)
                    except (TypeError, ValueError):
                        live_off_y = 0.0
                    try:
                        peer_off_y = float(peer.get("offset_y") or 0.0)
                    except (TypeError, ValueError):
                        peer_off_y = 0.0

                    live_p = resolve(s["src"])
                    peer_p = resolve(peer["src"])
                    src_w, src_h = _clip_dims(live_p)
                    peer_w, peer_h = _clip_dims(peer_p)
                    extra_kw: Dict[str, Any] = {}
                    if "live_offset_y" in _paired_params:
                        extra_kw["live_offset_y"] = live_off_y
                    if "peer_offset_y" in _paired_params:
                        extra_kw["peer_offset_y"] = peer_off_y

                    c_live, c_peer = compute_paired_split_crop_filters(
                        live_p, peer_p,
                        src_w, src_h, peer_w, peer_h,
                        slot_w, slot_h, slot_w, slot_h,
                        **extra_kw,
                    )
                    hr_live = float(c_live.get("common_headroom_pct", 14.0)) + float(c_live.get("offset_y") or 0.0) * 100.0
                    hr_peer = float(c_peer.get("common_headroom_pct", 14.0)) + float(c_peer.get("offset_y") or 0.0) * 100.0
                    fy_live = float((c_live.get("slot_focus") or [0.5, 0.38])[1])
                    fy_peer = float((c_peer.get("slot_focus") or [0.5, 0.38])[1])
                    observed_headrooms.extend([hr_live, hr_peer])

                    if abs(live_off_y) <= 1e-4:
                        slot_samples.setdefault((lay_norm, live_slot), []).append((sid, hr_live, fy_live))
                    if abs(peer_off_y) <= 1e-4:
                        slot_samples.setdefault((lay_norm, peer_slot), []).append((sid, hr_peer, fy_peer))

    for (lay_norm, slot_name), entries in sorted(slot_samples.items()):
        if len(entries) < 2:
            continue
        hrs = [e[1] for e in entries]
        fys = [e[2] for e in entries]
        if max(hrs) - min(hrs) > 8.0:
            split_violations.append(
                f"{lay_norm}:{slot_name}:headroom_drift({min(hrs):.1f}%..{max(hrs):.1f}%)"
            )
        elif max(fys) - min(fys) > 0.14:
            split_violations.append(
                f"{lay_norm}:{slot_name}:eyeline_drift({min(fys):.2f}..{max(fys):.2f})"
            )

    try:
        import script_order as _so

        w_dir = Path(str(plan["_plan_dir"])) if plan.get("_plan_dir") else None
        scr_arg = plan.get("script")
        if not scr_arg and w_dir is not None and (w_dir / "plan.json").is_file():
            scr_arg = read_json(w_dir / "plan.json").get("script")
        accepted_map = _so.read_accepted(w_dir) if w_dir is not None else {}
        for prob in _so.split_continuity_problems(plan, script_lines=scr_arg, work_dir=w_dir):
            p_sid = (prob.split() or [""])[0]
            if p_sid and _so._is_seg_accepted(p_sid, accepted_map):
                continue
            split_violations.append(f"{p_sid or 'plan'}:split_continuity({prob})")
    except Exception:
        pass

    explicit_max_hook = plan.get("max_hook_transitions")
    if explicit_max_hook is not None:
        max_hook_transitions = int(explicit_max_hook)
    else:
        tuning_max = 4
        try:
            import style_config as _sc

            t_val = _sc.get_tuning(plan=plan).get("max_hook_transitions")
            if t_val is not None:
                tuning_max = int(t_val)
        except Exception:
            tuning_max = 4
        max_hook_transitions = max(tuning_max, int(round(hook_secs / 6.5)))
    if hook_transitions > max_hook_transitions:
        split_violations.append(f"hook_transitions={hook_transitions}(>{max_hook_transitions})")

    ok = len(split_violations) == 0
    if observed_headrooms:
        lo_hr, hi_hr = min(observed_headrooms), max(observed_headrooms)
        hr_str = (
            f", headroom {lo_hr:.1f}%"
            if (hi_hr - lo_hr) <= 0.05
            else f", headroom {lo_hr:.1f}..{hi_hr:.1f}%"
        )
    else:
        hr_str = ""
    summary = (
        f"{split_segs} split-screen segs valid (z=1.0{hr_str}, 0 intra-split transitions), {hook_transitions} hook clip transition(s)"
        if ok
        else f"Violations: {', '.join(split_violations)}"
    )
    return ok, summary


PICTURE_MAX_DIFF = 9.0


def check_picture_timeline(
    plan: Dict[str, Any],
    mp4_path: Path,
    *,
    plan_dir: Optional[Path] = None,
) -> Tuple[bool, List[str], int]:
    """Verify the middle frame of every cut in the final video matches that cut's cached intermediate render."""
    try:
        import cv2
        import numpy as np
        import layouts
    except Exception as exc:  # noqa: BLE001
        return True, [f"not checked ({exc})"], 0
    canvas = plan.get("canvas") or {}
    W, H, fps = int(canvas.get("width") or 1080), int(canvas.get("height") or 1920), int(canvas.get("fps") or 30)
    full_H = H
    page_top = None
    try:
        import scroll_page
        _pc = scroll_page.page_config(plan)
        if _pc:
            _geo = scroll_page.geometry(W, H, _pc)
            H, page_top = _geo["video_h"], _geo["bar_h"]
    except Exception:
        pass

    fc_meta, _ = _load_flashcards_meta(plan, mp4_path, is_square=False, plan_dir=plan_dir)
    fc_cards = (fc_meta.get("cards") or []) if isinstance(fc_meta, dict) else []
    fc_cw = float((fc_meta or {}).get("canvas_w") or W or 1)
    fc_ch = float((fc_meta or {}).get("canvas_h") or full_H or 1)

    def sig(frame: Any) -> Any:
        return cv2.cvtColor(cv2.resize(frame, (108, 192), interpolation=cv2.INTER_AREA), cv2.COLOR_BGR2GRAY).astype(np.float32)

    def frame_at(cap: Any, t: float) -> Any:
        cap.set(cv2.CAP_PROP_POS_MSEC, max(0.0, t) * 1000.0)
        ok, fr = cap.read()
        return fr if ok else None

    out = cv2.VideoCapture(str(mp4_path))
    t0, bad, compared = 0.0, [], 0
    for seg in plan.get("segments", []):
        play_dur = _segment_play_duration(seg)
        play_dur_q = round(play_dur * fps) / fps
        eff_dur = max(1, round(_segment_duration(seg) * fps)) / fps
        f = WORK / "_segments" / f"{layouts.build_segment_cache_key(seg, plan, W, H, fps, False)}.mkv"
        if not f.is_file():
            f = WORK / "_segments" / f"{layouts.build_segment_cache_key(seg, plan, W, H, fps, True)}.mkv"
        if f.is_file() and play_dur_q >= 0.4:
            ref_cap = cv2.VideoCapture(str(f))
            probe_mid = play_dur_q / 2.0
            t_probe = t0 + probe_mid
            src_mid = _seg_to_src_time(seg, probe_mid)
            got = frame_at(out, t_probe)
            if got is not None and page_top is not None:
                k = got.shape[0] / float(page_top + H + (int(canvas.get("height") or 1920) - page_top - H))
                got = got[int(round(page_top * k)) : int(round((page_top + H) * k))]
            refs = [fr for fr in (frame_at(ref_cap, probe_mid + k / fps) for k in range(-4, 5)) if fr is not None]
            ref_cap.release()
            if refs and got is not None:
                compared += 1
                active_masks: List[Tuple[int, int, int, int]] = []
                for fc_c in fc_cards:
                    if not isinstance(fc_c, dict):
                        continue
                    fc_s = float(fc_c.get("start") if fc_c.get("start") is not None else fc_c.get("t0", 0.0))
                    fc_e = float(fc_c.get("end") if fc_c.get("end") is not None else fc_c.get("t1", 0.0))
                    if fc_s - 0.05 <= t_probe <= fc_e + 0.05:
                        cx0 = float(fc_c.get("x0", 0.0))
                        cy0 = float(fc_c.get("y0", 0.0))
                        cx1 = float(fc_c.get("x1") if fc_c.get("x1") is not None else cx0 + float(fc_c.get("w", 0.0)))
                        cy1 = float(fc_c.get("y1") if fc_c.get("y1") is not None else cy0 + float(fc_c.get("h", 0.0)))
                        if page_top is not None:
                            scale_y_to_full = full_H / max(1.0, fc_ch)
                            cy0_adj = max(0.0, cy0 * scale_y_to_full - page_top)
                            cy1_adj = min(float(H), cy1 * scale_y_to_full - page_top)
                            ref_h_den = float(max(1, H))
                        else:
                            cy0_adj, cy1_adj = cy0, cy1
                            ref_h_den = max(1.0, fc_ch)
                        sx0 = max(0, int(math.floor((cx0 - 4.0) * 108.0 / max(1.0, fc_cw))))
                        sx1 = min(108, int(math.ceil((cx1 + 4.0) * 108.0 / max(1.0, fc_cw))))
                        sy0 = max(0, int(math.floor((cy0_adj - 4.0) * 192.0 / ref_h_den)))
                        sy1 = min(192, int(math.ceil((cy1_adj + 4.0) * 192.0 / ref_h_den)))
                        if sx1 > sx0 and sy1 > sy0:
                            active_masks.append((sx0, sy0, sx1, sy1))
                g_sig = sig(got)
                if active_masks:
                    g_sig = g_sig.copy()
                    for sx0, sy0, sx1, sy1 in active_masks:
                        g_sig[sy0:sy1, sx0:sx1] = 0.0
                best = float("inf")
                for r in refs:
                    r_sig = sig(r)
                    if active_masks:
                        r_sig = r_sig.copy()
                        for sx0, sy0, sx1, sy1 in active_masks:
                            r_sig[sy0:sy1, sx0:sx1] = 0.0
                    d_val = float(np.abs(r_sig - g_sig).mean())
                    if d_val < best:
                        best = d_val
                if best > PICTURE_MAX_DIFF:
                    bad.append(f"{seg.get('id')} at {t0 + probe_mid:.1f}s (src {src_mid:.2f}s) shows other footage than the plan")
        t0 += eff_dur
    out.release()
    return (not bad), bad, compared


FRAME_STEP_SEC = 0.25
BLACK_MEAN, BLACK_STD = 10.0, 4.0
BANNER_FACE_OVERLAP = 0.04
MISSING_FACE_SHARE = 0.35
FROZEN_DIFF_EPS = 0.08
FROZEN_MIN_STEPS = 6  # >= 1.50s continuous zero-motion digital freeze
_DARK_FADE_TRANSITIONS = frozenset({"dip_to_black", "fadeblack", "fade"})


def is_black_frame(gray: Any) -> bool:
    """Detect dark or flat single-color frames (render fault)."""
    return float(gray.mean()) < BLACK_MEAN or float(gray.std()) < BLACK_STD


def box_overlap_share(face_norm: Sequence[float], box_px: Sequence[float], W: int, H: int) -> float:
    """Return the fraction of a normalized face bbox covered by `box_px`."""
    fx0, fy0, fx1, fy1 = face_norm[0] * W, face_norm[1] * H, face_norm[2] * W, face_norm[3] * H
    ix = max(0.0, min(fx1, box_px[2]) - max(fx0, box_px[0]))
    iy = max(0.0, min(fy1, box_px[3]) - max(fy0, box_px[1]))
    area = max(1e-6, (fx1 - fx0) * (fy1 - fy0))
    return (ix * iy) / area


def _panel_bbox_to_canvas_norm(
    bbox_norm: Sequence[float],
    panel_rect: Tuple[int, int, int, int],
    W: int,
    H: int,
) -> Tuple[float, float, float, float]:
    """Map panel-relative normalized `[x0, y0, x1, y1]` to full-canvas normalized coordinates."""
    x0, y0, x1, y1 = panel_rect
    pw = max(1, x1 - x0)
    ph = max(1, y1 - y0)
    fx0, fy0, fx1, fy1 = float(bbox_norm[0]), float(bbox_norm[1]), float(bbox_norm[2]), float(bbox_norm[3])
    cw = float(max(1, W))
    ch = float(max(1, H))
    return (
        (x0 + fx0 * pw) / cw,
        (y0 + fy0 * ph) / ch,
        (x0 + fx1 * pw) / cw,
        (y0 + fy1 * ph) / ch,
    )


def check_frames(plan: Dict[str, Any], mp4_path: Path) -> Dict[str, Any]:
    """Frame-by-frame Deep QC of the 9:16 master video using the local YuNet face detector:
    black or flat frames, frozen video streams, banners covering faces, face crop boundary safety,
    and missing-face warnings across panels. Frames inside inter-segment transitions and mark flashes are skipped.
    """
    result: Dict[str, Any] = {
        "ok": True,
        "frames": 0,
        "black": [],
        "frozen": [],
        "banner_on_face": [],
        "lingering_banners": [],
        "face_crop": [],
        "no_face": [],
        "split_alignment": [],
        "split_alignment_warnings": [],
        "note": "",
    }
    try:
        import cv2
        import numpy as np
        import layouts
        import vision_tracker
    except Exception as exc:  # noqa: BLE001
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
        marks_by_seg.setdefault(str(m.get("segment")), []).append(
            (float(m.get("at", 0)), float(m.get("at", 0)) + float(m.get("duration", 0.56)) + 0.1)
        )
    segs = plan.get("segments", [])
    t0 = 0.0
    for idx_seg, seg in enumerate(segs):
        dur = max(1, round(_segment_duration(seg) * fps)) / fps
        play_dur = min(dur, max(1, round(_segment_play_duration(seg) * fps)) / fps)
        hold_tail = _segment_hold_tail(seg)
        spd = _segment_speed(seg)
        is_beat = _segment_is_beat(seg)
        explicit_freezes = seg.get("freezes") or []
        eff_frozen_steps = (
            max(FROZEN_MIN_STEPS, int(math.ceil(FROZEN_MIN_STEPS / max(0.15, spd))))
            if spd < 0.5
            else FROZEN_MIN_STEPS
        )
        sid, lay = str(seg.get("id")), str(seg.get("layout") or "single").lower()
        lay_norm = {
            "left_right": "split_h",
            "hstack": "split_h",
            "side_by_side": "split_h",
            "top_bottom": "split_v",
            "vstack": "split_v",
        }.get(lay, lay)
        try:
            _live_off_y = float(seg.get("offset_y") or 0.0)
        except (TypeError, ValueError):
            _live_off_y = 0.0
        _peer_obj = seg.get("peer") if isinstance(seg.get("peer"), dict) else {}
        try:
            _peer_off_y = float(_peer_obj.get("offset_y") or 0.0)
        except (TypeError, ValueError):
            _peer_off_y = 0.0
        has_asym_offset_y = abs(_live_off_y - _peer_off_y) > 0.02
        for m_in in seg.get("marks", []) or []:
            marks_by_seg.setdefault(sid, []).append(
                (float(m_in.get("at", 0)), float(m_in.get("at", 0)) + float(m_in.get("duration", 0.56)) + 0.1)
            )

        tr_in = str(seg.get("transition") or "cut").strip().lower() if idx_seg > 0 else "cut"
        tr_in_base = tr_in.split("@", 1)[0].strip()
        d_in = float(seg.get("transition_duration") or 0.36) if tr_in_base not in ("", "cut", "none") else 0.0
        tr_out = (
            str(segs[idx_seg + 1].get("transition") or "cut").strip().lower()
            if idx_seg + 1 < len(segs)
            else "cut"
        )
        tr_out_base = tr_out.split("@", 1)[0].strip()
        d_out = (
            float(segs[idx_seg + 1].get("transition_duration") or 0.36)
            if tr_out_base not in ("", "cut", "none")
            else 0.0
        )

        # Explicitly probe every cut seam (t_cut - 0.04s, t_cut, t_cut + 0.04s) for black flickers,
        # skipping ONLY intentional dark fades (dip_to_black / fadeblack / fade).
        if idx_seg >= 1 and tr_in_base not in _DARK_FADE_TRANSITIONS:
            for dt_seam in (-0.04, 0.0, 0.04):
                t_probe = t0 + dt_seam
                if t_probe < 0.0:
                    continue
                cap.set(cv2.CAP_PROP_POS_MSEC, t_probe * 1000.0)
                ok_seam, fr_seam = cap.read()
                if ok_seam and fr_seam is not None:
                    result["frames"] += 1
                    gray_seam = cv2.cvtColor(fr_seam, cv2.COLOR_BGR2GRAY)
                    if float(gray_seam.mean()) < BLACK_MEAN:
                        result["black"].append(f"{sid} cut seam at {t_probe:.2f}s")

        boxes = layouts.banner_boxes(seg, W, H, dur) if hasattr(layouts, "banner_boxes") else []
        for b in boxes:
            if not b.get("header"):
                b_span = float(b.get("t1", 0.0)) - float(b.get("t0", 0.0))
                if b_span > 1.5:
                    result["lingering_banners"].append(
                        f"{sid}: phrase banner '{b.get('text', '')}' lingers {b_span:.2f}s (>1.50s)"
                    )
        panels = {
            "split_h": [("left", (0, 0, W // 2, H)), ("right", (W // 2, 0, W, H))],
            "split_v": [("top", (0, 0, W, H // 2)), ("bottom", (0, H // 2, W, H))],
        }.get(lay_norm, [("frame", (0, 0, W, H))])
        seen = {name: 0 for name, _ in panels}
        missing: Dict[str, List[float]] = {name: [] for name, _ in panels}
        split_h_bad: List[str] = []
        split_v_bad: List[str] = []
        prev_gray: Optional[Any] = None
        run_anchor_gray: Optional[Any] = None
        frozen_run = 0
        freeze_start_t = 0.0
        frozen_reported = False

        t = 0.2
        while t < dur - 0.2:
            in_transition = (d_in > 0.0 and t <= d_in + 0.05) or (d_out > 0.0 and t >= dur - d_out - 0.05)
            in_mark = any(a - 0.05 <= t <= b for a, b in marks_by_seg.get(sid, []))
            in_hold_tail = (hold_tail > 0.0 and t >= play_dur - 0.05) or any(
                isinstance(fz, dict)
                and float(fz.get("at", 0.0)) - 0.05 <= t <= float(fz.get("at", 0.0)) + float(fz.get("duration", 0.0)) + 0.05
                for fz in explicit_freezes
            )
            if not in_transition and not in_mark:
                cap.set(cv2.CAP_PROP_POS_MSEC, (t0 + t) * 1000.0)
                ok, frame = cap.read()
                if ok:
                    result["frames"] += 1
                    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
                    if is_black_frame(gray):
                        result["black"].append(f"{sid} at {t0 + t:.1f}s")
                    if in_hold_tail:
                        # Exempt intentional hold_tail / explicit freeze windows from FROZEN alarms
                        prev_gray = None
                        run_anchor_gray = None
                        frozen_run = 0
                    else:
                        if prev_gray is not None and gray.shape == prev_gray.shape:
                            diff_mean = float(np.abs(gray.astype(np.float32) - prev_gray.astype(np.float32)).mean())
                            if diff_mean < FROZEN_DIFF_EPS:
                                if frozen_run == 0:
                                    freeze_start_t = t0 + t - FRAME_STEP_SEC
                                    run_anchor_gray = prev_gray
                                # On smooth slo-mo (speed < 0.5), tiny per-step deltas accumulate over multiple steps;
                                # genuine stuck frames stay identical to run_anchor_gray.
                                anchor_moved = (
                                    spd < 0.5
                                    and run_anchor_gray is not None
                                    and run_anchor_gray.shape == gray.shape
                                    and float(np.abs(gray.astype(np.float32) - run_anchor_gray.astype(np.float32)).mean())
                                    >= FROZEN_DIFF_EPS * 2.0
                                )
                                if anchor_moved:
                                    frozen_run = 0
                                    run_anchor_gray = gray
                                else:
                                    frozen_run += 1
                                    if frozen_run >= eff_frozen_steps and not frozen_reported:
                                        result["frozen"].append(f"{sid} frozen >=1.5s starting at {freeze_start_t:.1f}s")
                                        frozen_reported = True
                            else:
                                frozen_run = 0
                                run_anchor_gray = gray
                        prev_gray = gray

                    panel_faces: Dict[str, List[Dict[str, Any]]] = {}
                    for name, (x0, y0, x1, y1) in panels:
                        seen[name] += 1
                        faces = vision_tracker.prominent_faces(vision_tracker.detect_faces_in_frame(frame[y0:y1, x0:x1]))
                        panel_faces[name] = faces or []
                        if result["frames"] % 6 == 1 and name in ("frame", "left", "top"):
                            try:
                                import vision_scan

                                vision_scan.record(f"final video {sid}", t0 + t, frame, faces, result["frames"], 0, 0.0)
                            except Exception:
                                pass
                        if not faces:
                            missing[name].append(round(t0 + t, 1))
                        else:
                            for f in faces:
                                ex, ey = f.get("eye_center_norm", [0.5, 0.38])
                                if ex <= 0.015 or ex >= 0.985 or ey <= 0.015 or ey >= 0.96:
                                    lbl = f"{sid} ({name})" if name != "frame" else sid
                                    result["face_crop"].append(f"{lbl} at {t0 + t:.1f}s: eyeline near crop edge ({ex:.2f},{ey:.2f})")
                            for b in boxes:
                                if b["t0"] <= t <= b["t1"]:
                                    for f in faces:
                                        canvas_bbox = _panel_bbox_to_canvas_norm(
                                            f["bbox_norm"], (x0, y0, x1, y1), W, H
                                        )
                                        if box_overlap_share(canvas_bbox, (b["x0"], b["y0"], b["x1"], b["y1"]), W, H) > BANNER_FACE_OVERLAP:
                                            lbl = f"{sid} ({name})" if name != "frame" else sid
                                            result["banner_on_face"].append(f"{lbl} at {t0 + t:.1f}s: '{b['text']}' covers a face")

                    def _primary_panel_face(flist: List[Dict[str, Any]], min_fh: float) -> Optional[Dict[str, Any]]:
                        cands = []
                        for f_obj in flist:
                            if not isinstance(f_obj, dict) or not f_obj.get("bbox_norm"):
                                continue
                            if float(f_obj.get("confidence", 1.0)) <= 0.5:
                                continue
                            bb = f_obj["bbox_norm"]
                            fh = float(bb[3]) - float(bb[1])
                            if fh >= min_fh:
                                cands.append((fh, f_obj))
                        return max(cands, key=lambda x: x[0])[1] if cands else None

                    if lay_norm == "split_h":
                        f_l = _primary_panel_face(panel_faces.get("left", []), 0.10)
                        f_r = _primary_panel_face(panel_faces.get("right", []), 0.10)
                        if f_l is not None and f_r is not None:
                            fh_l = float(f_l["bbox_norm"][3]) - float(f_l["bbox_norm"][1])
                            fh_r = float(f_r["bbox_norm"][3]) - float(f_r["bbox_norm"][1])
                            ratio_lr = max(fh_l, fh_r) / max(1e-4, min(fh_l, fh_r))
                            ey_l = float((f_l.get("eye_center_norm") or [0.5, 0.5 * (float(f_l["bbox_norm"][1]) + float(f_l["bbox_norm"][3]))])[1])
                            ey_r = float((f_r.get("eye_center_norm") or [0.5, 0.5 * (float(f_r["bbox_norm"][1]) + float(f_r["bbox_norm"][3]))])[1])
                            if ratio_lr > 1.38 or ((not has_asym_offset_y) and abs(ey_l - ey_r) > 0.08):
                                split_h_bad.append(
                                    f"{sid} at {t0 + t:.1f}s: split_h alignment mismatch "
                                    f"(fh L={fh_l:.2f}/R={fh_r:.2f} ratio={ratio_lr:.2f}, ey L={ey_l:.2f}/R={ey_r:.2f})"
                                )
                    elif lay_norm == "split_v":
                        f_t = _primary_panel_face(panel_faces.get("top", []), 0.14)
                        f_b = _primary_panel_face(panel_faces.get("bottom", []), 0.14)
                        if f_t is not None and f_b is not None:
                            bb_t, bb_b = f_t["bbox_norm"], f_b["bbox_norm"]
                            fh_t = float(bb_t[3]) - float(bb_t[1])
                            fh_b = float(bb_b[3]) - float(bb_b[1])
                            ratio_tb = max(fh_t, fh_b) / max(1e-4, min(fh_t, fh_b))
                            cx_t = 0.5 * (float(bb_t[0]) + float(bb_t[2]))
                            cx_b = 0.5 * (float(bb_b[0]) + float(bb_b[2]))
                            if ratio_tb > 1.38 or abs(cx_t - cx_b) > 0.10:
                                split_v_bad.append(
                                    f"{sid} at {t0 + t:.1f}s: split_v alignment warning "
                                    f"(cx T={cx_t:.2f}/B={cx_b:.2f}, fh T={fh_t:.2f}/B={fh_b:.2f} ratio={ratio_tb:.2f})"
                                )
            else:
                prev_gray = None
                run_anchor_gray = None
                frozen_run = 0
            t += FRAME_STEP_SEC
        if len(split_h_bad) >= 2:
            result["split_alignment"].append(split_h_bad[0])
        if len(split_v_bad) >= 2:
            result["split_alignment_warnings"].append(split_v_bad[0])
        if not is_beat:
            for name, times in missing.items():
                if seen[name] >= 3 and len(times) / seen[name] > MISSING_FACE_SHARE:
                    label = f"{sid} {name} panel" if name != "frame" else sid
                    result["no_face"].append(f"{label}: no face in {len(times)} of {seen[name]} frames (for example at {times[0]}s)")
        t0 += dur
    cap.release()
    result["ok"] = (
        not result["black"]
        and not result["frozen"]
        and len(result["banner_on_face"]) < 2
        and not result["split_alignment"]
    )
    return result


def is_subs_enabled(plan: Dict[str, Any]) -> bool:
    """Return True when caption/subtitle rendering is enabled in `plan`."""
    if not isinstance(plan, dict):
        return False
    if plan.get("page"):
        return False
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
        mode = str(spec.get("mode", "") or "").strip().lower()
        if mode in ("off", "none", "false", "0", "no"):
            return False
        style = str(spec.get("style", "auto") or "auto").strip().lower()
        return style not in ("", "none", "off", "false")
    return False


def _segment_subs_suppressed(
    seg: Dict[str, Any],
    plan: Dict[str, Any],
    seg_start: float,
    seg_end: float,
) -> bool:
    """Return True when a segment intentionally skips bottom subtitles (muted, beat, `subs_off`, or active flashcard)."""
    if _segment_is_muted(seg) or _segment_is_beat(seg):
        return True
    if seg.get("subs_off"):
        return True
    seg_subs_raw = seg.get("subs")
    if seg_subs_raw is False:
        return True
    if isinstance(seg_subs_raw, str) and seg_subs_raw.strip().lower() in ("off", "none", "false", "0", "no"):
        return True

    subs_on = (
        bool(seg.get("subs_on"))
        or (seg_subs_raw is True)
        or (isinstance(seg_subs_raw, str) and seg_subs_raw.strip().lower() in ("on", "true", "1", "yes"))
    )
    if subs_on:
        return False

    plan_subs = plan.get("subs")
    if isinstance(plan_subs, dict) and plan_subs.get("with_cards"):
        return False

    if plan.get("cards_off") or plan.get("cards") is False or plan.get("flashcards") is False or seg.get("cards_off") or seg.get("cards") is False or seg.get("flashcards") is False:
        return False
    seg_fcs = seg.get("flashcards") or seg.get("cards")
    if isinstance(seg_fcs, list) and len(seg_fcs) > 0:
        return True
    sid = str(seg.get("id") or "")
    for fc in plan.get("flashcards") or []:
        if not isinstance(fc, dict):
            continue
        fc_seg = str(fc.get("segment") or "")
        if sid and fc_seg and fc_seg == sid:
            return True
        fc_segs = fc.get("segments")
        if sid and isinstance(fc_segs, (list, tuple, set)) and sid in {str(x) for x in fc_segs}:
            return True
        fc_s = fc.get("start") if fc.get("start") is not None else fc.get("t0")
        fc_e = fc.get("end") if fc.get("end") is not None else fc.get("t1")
        if fc_s is not None and fc_e is not None:
            try:
                if float(fc_e) > seg_start + 0.15 and float(fc_s) < seg_end - 0.15:
                    return True
            except (TypeError, ValueError):
                pass
    return False


_INDIC_TEXT_RE = re.compile(r"[\u0900-\u0D7F]")
_INDIC_SCRIPT_RANGES = (
    (0x0C80, 0x0CFF),  # Kannada
    (0x0900, 0x097F),  # Devanagari
    (0x0B80, 0x0BFF),  # Tamil
    (0x0C00, 0x0C7F),  # Telugu
    (0x0D00, 0x0D7F),  # Malayalam
    (0x0980, 0x09FF),  # Bengali
    (0x0A80, 0x0AFF),  # Gujarati
    (0x0A00, 0x0A7F),  # Gurmukhi
    (0x0B00, 0x0B7F),  # Odia
)


def _collect_indic_probe_text(texts: Sequence[str], max_words: int = 12) -> str:
    """Collect up to `max_words` unique Indic words, prioritizing coverage of every script present."""
    words_by_script: Dict[int, List[str]] = {}
    seen_words: set[str] = set()
    for chunk in texts:
        for tok in re.split(r"\s+", str(chunk or "")):
            w = tok.strip(".,!?\"'()[]{}:;—–-")
            if not w or w in seen_words or not _INDIC_TEXT_RE.search(w):
                continue
            seen_words.add(w)
            script_idx = -1
            for ch in w:
                cp = ord(ch)
                for idx_s, (lo, hi) in enumerate(_INDIC_SCRIPT_RANGES):
                    if lo <= cp <= hi:
                        script_idx = idx_s
                        break
                if script_idx >= 0:
                    break
            words_by_script.setdefault(script_idx, []).append(w)
    if not words_by_script:
        return ""
    picked: List[str] = []
    # Round-robin across scripts first so every language in mixed videos is probed
    buckets = [list(v) for v in words_by_script.values()]
    while buckets and len(picked) < max_words:
        next_buckets = []
        for b in buckets:
            if b and len(picked) < max_words:
                picked.append(b.pop(0))
            if b:
                next_buckets.append(b)
        buckets = next_buckets
    return " ".join(picked)


def _gather_work_pngs(
    plan: Dict[str, Any],
    mp4_path: Path,
    subdirs: Sequence[str],
    *,
    include_banners: bool = False,
) -> List[Path]:
    """Locate saved render/cache PNGs under project workdir, output dir, or `WORK`."""
    roots: List[Path] = []
    if isinstance(plan, dict) and plan.get("_plan_dir"):
        roots.append(Path(str(plan["_plan_dir"])))
    roots.append(mp4_path.parent)
    roots.append(WORK)

    found: List[Path] = []
    seen: set[Path] = set()
    for r in roots:
        for sub in subdirs:
            d = r / sub
            if not d.is_dir():
                continue
            for p in sorted(d.rglob("*.png")):
                if p.name == "blank.png":
                    continue
                rp = p.resolve()
                if rp not in seen and p.stat().st_size > 0:
                    seen.add(rp)
                    found.append(p)
        if include_banners:
            for bdir in (r, r / "overlays"):
                if not bdir.is_dir():
                    continue
                for p in sorted(bdir.glob("*.png")):
                    nm = p.name.lower()
                    if ("banner" in nm or "stop_say" in nm) and p.stat().st_size > 0:
                        rp = p.resolve()
                        if rp not in seen:
                            seen.add(rp)
                            found.append(p)
    return found


def _scan_pngs_for_tofu(
    png_paths: Sequence[Path],
    *,
    max_check: int = 24,
) -> Optional[Tuple[Path, Dict[str, Any]]]:
    """Scan up to `max_check` evenly-sampled PNGs for `.notdef` tofu rectangles."""
    if not png_paths:
        return None
    try:
        import subtitles as _subs
    except Exception:
        return None

    paths = list(png_paths)
    if len(paths) > max_check:
        step = len(paths) / float(max_check)
        paths = [paths[int(i * step)] for i in range(max_check)]

    for p in paths:
        qc = _subs.detect_tofu_boxes_in_png(p)
        if qc.get("tofu_count", 0) > 0:
            return p, qc
    return None


def _extract_srt_cue_texts(raw_srt: str) -> List[str]:
    """Extract spoken caption lines from raw `.srt` text."""
    lines: List[str] = []
    for ln in (raw_srt or "").splitlines():
        s = ln.strip()
        if not s or s.isdigit() or "-->" in s:
            continue
        lines.append(s)
    return lines


def _extract_plan_banner_texts(plan: Dict[str, Any]) -> List[str]:
    """Extract banner / overlay text strings from `plan` segments and top-level fields."""
    texts: List[str] = []
    if not isinstance(plan, dict):
        return texts
    for key in ("banners", "overlays"):
        for item in plan.get(key) or []:
            if isinstance(item, dict) and item.get("text"):
                texts.append(str(item["text"]))
    for seg in plan.get("segments") or []:
        if not isinstance(seg, dict):
            continue
        for key in ("banner", "banners", "overlays"):
            val = seg.get(key)
            if isinstance(val, str):
                texts.append(val)
            elif isinstance(val, dict) and val.get("text"):
                texts.append(str(val["text"]))
            elif isinstance(val, list):
                for item in val:
                    if isinstance(item, str):
                        texts.append(item)
                    elif isinstance(item, dict) and item.get("text"):
                        texts.append(str(item["text"]))
    return texts


def _segment_strikethrough_windows(
    seg: Dict[str, Any],
    seg_start: float,
    seg_dur: float,
    plan: Optional[Dict[str, Any]] = None,
) -> List[Tuple[float, float]]:
    """Return absolute timeline `(t0, t1)` windows suppressed by phrase strikethrough banners."""
    if not isinstance(seg, dict) or _segment_is_muted(seg) or _segment_is_beat(seg):
        return []
    subs_spec = (plan or {}).get("subs")
    if isinstance(subs_spec, dict) and subs_spec.get("with_banners"):
        return []
    seg_subs_raw = seg.get("subs")
    subs_on = (
        bool(seg.get("subs_on"))
        or (seg_subs_raw is True)
        or (isinstance(seg_subs_raw, str) and seg_subs_raw.strip().lower() in ("on", "true", "1", "yes"))
    )
    if subs_on:
        return []
    try:
        import subtitles as _subs

        fn = getattr(_subs, "segment_strikethrough_windows", None)
        if callable(fn):
            wins = fn(seg, seg_start, seg_dur, plan=plan)
            if isinstance(wins, list):
                return [(float(a), float(b)) for a, b in wins if float(b) > float(a)]
    except Exception:
        pass
    try:
        import layouts as _lay

        raw_banners = _lay.parse_banners(seg, seg_dur)
    except Exception:
        raw_banners = []
    wins: List[Tuple[float, float]] = []
    for txt, t0, t1, is_hdr in raw_banners:
        if is_hdr or not str(txt or "").strip():
            continue
        a = max(seg_start, seg_start + float(t0) - 0.04)
        b = min(seg_start + seg_dur, seg_start + float(t1) + 0.04)
        if b > a:
            wins.append((a, b))
    return wins


def check_subtitles_srt(plan: Dict[str, Any], mp4_path: Path) -> Tuple[bool, str]:
    """Verify expected `.srt` sidecar exists, contains monotonic non-overlapping cues,
    covers every eligible spoken segment (`>= 1.8s`), and respects canvas bounds.
    """
    if not is_subs_enabled(plan):
        return True, "disabled"
    segs = plan.get("segments", [])
    if not segs:
        return True, "no dialogue segments"

    fps = int(round(float((plan.get("canvas") or {}).get("fps") or 30))) or 30
    starts, durs, _, _ = compute_timeline_starts(plan, fps)

    seg_rem_spans: List[List[Tuple[float, float]]] = []
    all_suppressed = True
    for i, s in enumerate(segs):
        seg_s = starts[i]
        seg_d = durs[i]
        seg_e = seg_s + seg_d
        spoken_e = seg_e
        ae_raw = s.get("audio_end")
        if ae_raw is not None and not isinstance(ae_raw, bool):
            try:
                ae_f = float(ae_raw)
                if 0.04 < ae_f < seg_d:
                    spoken_e = seg_s + ae_f
            except (TypeError, ValueError):
                pass
        strike_wins = _segment_strikethrough_windows(s, seg_s, max(0.0, spoken_e - seg_s), plan=plan)
        rem_spans = _subtract_intervals(seg_s, spoken_e, strike_wins) if spoken_e > seg_s else []
        seg_rem_spans.append(rem_spans)
        rem_dur = sum(b - a for a, b in rem_spans)
        if not (_segment_subs_suppressed(s, plan, seg_s, seg_e) or rem_dur <= 0.25):
            all_suppressed = False

    srt_cands = [
        mp4_path.with_suffix(".srt"),
        mp4_path.parent / f"{strip_ratio_prefix(mp4_path.stem)}.srt",
    ]
    if plan.get("_plan_dir"):
        pdir = Path(str(plan["_plan_dir"]))
        srt_cands.extend([
            pdir / f"{mp4_path.stem}.srt",
            pdir / f"{strip_ratio_prefix(mp4_path.stem)}.srt",
        ])
    srt_path = next((c for c in srt_cands if c.is_file()), srt_cands[0])
    if not srt_path.is_file():
        if all_suppressed:
            return True, "all segments muted/suppressed"
        return False, f"missing expected SRT sidecar: {srt_path}"

    raw_srt = srt_path.read_text(encoding="utf-8", errors="replace")
    cue_matches = list(
        re.finditer(
            r"(\d{2}):(\d{2}):(\d{2})[,.](\d{3})\s*-->\s*(\d{2}):(\d{2}):(\d{2})[,.](\d{3})",
            raw_srt,
        )
    )
    if not cue_matches:
        if all_suppressed:
            return True, "all segments muted/suppressed"
        return False, f"0 subtitle cues found in {srt_path.name}"

    prev_end = 0.0
    prev_start = 0.0
    cue_spans: List[Tuple[float, float]] = []
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
        cue_spans.append((t_s, t_e))

    # Per-segment SRT coverage gate: every spoken, non-muted, non-beat, non-subs_off,
    # non-flashcard-suppressed segment >= 1.8s (with >0.25s remaining outside strikethrough banners)
    # must have >= 1 overlapping cue.
    if not all_suppressed:
        uncaptioned: List[str] = []
        for i, s in enumerate(segs):
            seg_s = starts[i]
            seg_d = durs[i]
            seg_e = seg_s + seg_d
            if seg_d < 1.8:
                continue
            if _segment_subs_suppressed(s, plan, seg_s, seg_e):
                continue
            rem_spans = seg_rem_spans[i]
            rem_dur = sum(b - a for a, b in rem_spans)
            if rem_dur <= 0.25:
                continue
            covered = any(
                (c_e > a + 0.25 and c_s < b - 0.25)
                or (c_s >= a - 0.02 and c_e <= b + 0.02)
                for a, b in rem_spans
                for c_s, c_e in cue_spans
            )
            if not covered:
                uncaptioned.append(str(s.get("id", f"S{i + 1}")))
        if uncaptioned:
            return False, f"uncaptioned spoken segment(s) >=1.8s: {', '.join(uncaptioned)} in {srt_path.name}"

    # Check subtitle band vertical bounds against canvas height if subs_meta.json exists
    canvas_h_raw = (plan.get("canvas") or {}).get("height")
    if not canvas_h_raw and mp4_path.is_file():
        canvas_h_raw = probe(mp4_path).get("height")
    canvas_h = int(canvas_h_raw or 1920)
    meta_cands = [WORK / "_subs" / "subs_meta.json"]
    if plan.get("_plan_dir"):
        meta_cands.insert(0, Path(str(plan["_plan_dir"])) / "_subs" / "subs_meta.json")
    subs_meta: Optional[Dict[str, Any]] = None
    for mc in meta_cands:
        if mc.is_file():
            try:
                mdata = read_json(mc)
                if isinstance(mdata, dict):
                    subs_meta = mdata
                    by0 = mdata.get("band_y0") if mdata.get("band_y0") is not None else mdata.get("bandTop")
                    bh = mdata.get("band_h") if mdata.get("band_h") is not None else mdata.get("bandHeight", 0)
                    if by0 is not None:
                        y0_i = int(round(float(by0)))
                        bh_i = int(round(float(bh or 0)))
                        if y0_i < 0 or y0_i >= canvas_h or (bh_i > 0 and y0_i + bh_i > canvas_h + 2):
                            return False, f"subtitle band out of canvas bounds (y0={y0_i}, h={bh_i}, canvas_h={canvas_h})"
                break
            except Exception:
                pass

    if subs_meta and subs_meta.get("pango_warnings"):
        warns = subs_meta["pango_warnings"]
        first_warn = warns[0] if isinstance(warns, list) and warns else str(warns)
        return False, f"Pango font warning during subtitle render: {first_warn}"

    # Tofu-box PNG scan on saved subtitle/page/banner PNGs (prioritizing Indic cues)
    png_cands: List[Path] = []
    if subs_meta and isinstance(subs_meta.get("frames"), list):
        indic_frames: List[Path] = []
        other_frames: List[Path] = []
        seen_cues: set[int] = set()
        for fr in subs_meta["frames"]:
            if not isinstance(fr, dict) or not fr.get("file"):
                continue
            ci = int(fr.get("captionIndex", fr.get("cue", -1)))
            if ci in seen_cues:
                continue
            fp = Path(str(fr["file"]))
            if not fp.is_file():
                continue
            seen_cues.add(ci)
            ftxt = str(fr.get("text") or "")
            if _INDIC_TEXT_RE.search(ftxt):
                indic_frames.append(fp)
            else:
                other_frames.append(fp)
        # Every Indic caption is checked, not a sample: a font that fails on one script shows up in only some cues.
        png_cands.extend(indic_frames[:400] + other_frames[:8])
    png_cands.extend(
        _gather_work_pngs(
            plan,
            mp4_path,
            ("_subs", "_subs_cache", "_page", "_scroll_page"),
            include_banners=True,
        )
    )
    bad_png = _scan_pngs_for_tofu(png_cands, max_check=max(24, min(len(png_cands), 450)))
    if bad_png is not None:
        bad_p, qc = bad_png
        return False, f"detected {qc['tofu_count']} hollow tofu box(es) in rendered PNG ({bad_p.name})"

    # Live Indic render probe on actual Indic words present in SRT/plan (<0.4s)
    probe_texts = _extract_srt_cue_texts(raw_srt) + _extract_plan_banner_texts(plan)
    for seg in segs:
        if isinstance(seg, dict) and seg.get("text"):
            probe_texts.append(str(seg["text"]))
    indic_probe = _collect_indic_probe_text(probe_texts, max_words=12)
    indic_note = ""
    if indic_probe:
        try:
            import subtitles as _subs

            if _subs.available():
                eff_style_id = int((subs_meta or {}).get("style_id") or 2)
                eff_font = (subs_meta or {}).get("font_family") or (
                    (plan.get("subs") or {}).get("font") if isinstance(plan.get("subs"), dict) else None
                )
                probe_ok, probe_detail = _subs.probe_text_render(
                    indic_probe,
                    style_id=eff_style_id,
                    font_family=eff_font,
                )
                if not probe_ok:
                    return False, f"Indic subtitle font check failed: {probe_detail}"
                indic_note = ", Indic render verified"
        except Exception as exc:  # noqa: BLE001
            return False, f"Indic subtitle font check error: {exc}"

    return True, f"{len(cue_matches)} monotonic non-overlapping cue(s) -> {srt_path.name}{indic_note}"


def _plan_has_flashcards(plan: Dict[str, Any]) -> bool:
    """Return True when `plan` expects at least one bilingual flashcard overlay."""
    if not isinstance(plan, dict):
        return False
    if plan.get("cards_off") or plan.get("cards") is False:
        return False
    if isinstance(plan.get("flashcards"), list) and len(plan["flashcards"]) > 0:
        return True
    for seg in plan.get("segments", []) or []:
        if not isinstance(seg, dict) or seg.get("cards_off"):
            continue
        fcs = seg.get("flashcards") or seg.get("cards")
        if isinstance(fcs, list) and len(fcs) > 0:
            return True
    return False


def _load_flashcards_meta(
    plan: Dict[str, Any],
    mp4_path: Path,
    *,
    is_square: bool = False,
    plan_dir: Optional[Path] = None,
) -> Tuple[Optional[Dict[str, Any]], Optional[Path]]:
    """Locate and read `overlays/flashcards_meta.json` (or `flashcards_sq_meta.json` for 1:1)."""
    fname = "flashcards_sq_meta.json" if is_square else "flashcards_meta.json"
    cands: List[Path] = []
    if plan_dir is not None:
        cands.append(Path(plan_dir) / "overlays" / fname)
    if isinstance(plan, dict) and plan.get("_plan_dir"):
        cands.append(Path(str(plan["_plan_dir"])) / "overlays" / fname)
    cands.append(mp4_path.parent / "overlays" / fname)
    stem_clean = strip_ratio_prefix(mp4_path.stem)
    if stem_clean:
        cands.append(WORK / stem_clean / "overlays" / fname)
    cands.append(WORK / mp4_path.stem / "overlays" / fname)
    cands.append(WORK / "overlays" / fname)

    seen: set = set()
    for cand in cands:
        try:
            key = str(cand.resolve())
        except OSError:
            key = str(cand)
        if key in seen:
            continue
        seen.add(key)
        if cand.is_file():
            try:
                data = read_json(cand)
                if isinstance(data, dict):
                    return data, cand
            except Exception:
                continue
    return None, None


def _norm_layout_for_verify(seg: Dict[str, Any], plan: Dict[str, Any]) -> str:
    raw = str(seg.get("layout") or plan.get("preset") or "single").split(":")[0].strip().lower()
    if raw in ("left_right", "hstack", "side_by_side"):
        return "split_h"
    if raw in ("top_bottom", "vstack"):
        return "split_v"
    if raw in ("", "auto", "none"):
        return "single"
    return raw


def _detect_shields_for_verify(frame_bgr: Any, layout: str = "single") -> List[Tuple[float, float, float, float]]:
    """Detect expanded head/hair/ears/chin face shields in canvas pixels via `flashcard_placer`
    (falling back to panel-aware `vision_tracker` if `flashcard_placer` is unavailable).
    """
    raw_shields: List[Any] = []
    try:
        import flashcard_placer  # type: ignore[import-not-found]

        if hasattr(flashcard_placer, "detect_expanded_face_shields"):
            raw_shields = list(flashcard_placer.detect_expanded_face_shields(frame_bgr, layout=layout) or [])
            out_fc: List[Tuple[float, float, float, float]] = []
            for item in raw_shields:
                if isinstance(item, dict):
                    out_fc.append((float(item["x0"]), float(item["y0"]), float(item["x1"]), float(item["y1"])))
                elif isinstance(item, (list, tuple)) and len(item) >= 4:
                    out_fc.append((float(item[0]), float(item[1]), float(item[2]), float(item[3])))
            return out_fc
    except Exception:
        pass

    # Fallback when `flashcard_placer` is not available
    try:
        import vision_tracker
    except Exception:
        return []
    fh_img, fw_img = frame_bgr.shape[:2]
    lay = str(layout or "single").strip().lower()
    if lay == "split_h":
        mid_x = (fw_img // 2) & ~1
        panels = [(0, 0, mid_x, fh_img), (mid_x, 0, fw_img, fh_img)]
    elif lay == "split_v":
        mid_y = (fh_img // 2) & ~1
        panels = [(0, 0, fw_img, mid_y), (0, mid_y, fw_img, fh_img)]
    else:
        panels = [(0, 0, fw_img, fh_img)]
    shields: List[Tuple[float, float, float, float]] = []
    m_px = max(12.0, round(0.012 * min(fw_img, fh_img)))
    for px0, py0, px1, py1 in panels:
        pw, ph = max(1, px1 - px0), max(1, py1 - py0)
        crop = frame_bgr[py0:py1, px0:px1]
        if crop.size == 0:
            continue
        faces = vision_tracker.detect_faces_in_frame(crop, min_fw_ratio=0.05) or []
        for f in faces:
            if float(f.get("confidence", 1.0)) < 0.58:
                continue
            bn = f.get("bbox_norm")
            if not bn or len(bn) < 4:
                continue
            bx0, by0, bx1, by1 = float(bn[0]) * pw, float(bn[1]) * ph, float(bn[2]) * pw, float(bn[3]) * ph
            bw_f, bh_f = max(1.0, bx1 - bx0), max(1.0, by1 - by0)
            sx0 = max(0.0, px0 + bx0 - 0.10 * bw_f - m_px)
            sy0 = max(0.0, py0 + by0 - 0.16 * bh_f - m_px)
            sx1 = min(float(fw_img), px0 + bx1 + 0.10 * bw_f + m_px)
            sy1 = min(float(fh_img), py0 + by1 + 0.10 * bh_f + m_px)
            shields.append((sx0, sy0, sx1, sy1))
    return shields


def _find_clean_mix_cap(
    mp4_path: Path,
    W: int,
    H: int,
    mp4_dur: float,
    *,
    is_square: bool = False,
    plan_dir: Optional[Path] = None,
) -> Tuple[Optional[Any], int, int, Optional[Path]]:
    """Open clean pre-overlay `mix.mkv` when present and verified to match `mp4_path`."""
    try:
        import cv2
    except Exception:
        return None, 0, 0, None

    cands: List[Tuple[Path, bool]] = []
    if plan_dir is not None:
        cands.append((Path(plan_dir) / "_segments" / "mix.mkv", False))
        cands.append((Path(plan_dir).parent / "_segments" / "mix.mkv", False))
    cands.append((mp4_path.parent / "_segments" / "mix.mkv", False))
    cands.append((WORK / "_segments" / "mix.mkv", True))

    try:
        mp4_mtime = mp4_path.stat().st_mtime
    except OSError:
        mp4_mtime = 0.0

    seen: set = set()
    for cand, is_global in cands:
        try:
            key = str(cand.resolve())
        except OSError:
            key = str(cand)
        if key in seen or not cand.is_file():
            continue
        seen.add(key)
        if is_global and mp4_mtime > 0.0:
            try:
                if abs(cand.stat().st_mtime - mp4_mtime) > 300.0:
                    continue
            except OSError:
                continue
        cap = cv2.VideoCapture(str(cand))
        if not cap.isOpened():
            cap.release()
            continue
        mw = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
        mh = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
        mfps = float(cap.get(cv2.CAP_PROP_FPS) or 30.0) or 30.0
        mframes = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        mdur = (mframes / mfps) if mframes > 0 else 0.0
        dims_ok = (mw == W and mh == H) if not is_square else (mw > 0 and mh > 0 and mh >= mw)
        dur_ok = (mp4_dur <= 0.0 or mdur <= 0.0 or abs(mdur - mp4_dur) <= 0.35)
        if dims_ok and dur_ok:
            return cap, mw, mh, cand
        cap.release()
    return None, 0, 0, None


def _load_subs_cues_for_verify(
    plan: Dict[str, Any],
    mp4_path: Path,
    W: int,
    H: int,
    *,
    is_square: bool = False,
    plan_dir: Optional[Path] = None,
) -> List[Tuple[float, float, float, float, float, float]]:
    """Return active subtitle cue boxes `(start, end, x0, y0, x1, y1)` in canvas pixels from `subs_meta.json`."""
    subdir = "_subs_sq" if is_square else "_subs"
    cands: List[Path] = []
    if plan_dir is not None:
        cands.append(Path(plan_dir) / subdir / "subs_meta.json")
    if isinstance(plan, dict) and plan.get("_plan_dir"):
        cands.append(Path(str(plan["_plan_dir"])) / subdir / "subs_meta.json")
    cands.append(mp4_path.parent / subdir / "subs_meta.json")
    cands.append(WORK / subdir / "subs_meta.json")

    mdata: Optional[Dict[str, Any]] = None
    for cand in cands:
        if cand.is_file():
            try:
                raw = read_json(cand)
                if isinstance(raw, dict):
                    mdata = raw
                    break
            except Exception:
                continue
    if not mdata:
        return []
    raw_cues = mdata.get("cues")
    if not isinstance(raw_cues, list) or not raw_cues:
        return []

    out: List[Tuple[float, float, float, float, float, float]] = []
    for c in raw_cues:
        if not isinstance(c, dict):
            continue
        cs = c.get("start")
        if cs is None:
            cs = c.get("startTime", c.get("t0"))
        ce = c.get("end")
        if ce is None:
            ce = c.get("endTime", c.get("t1"))
        if cs is None or ce is None:
            continue
        try:
            t_s, t_e = float(cs), float(ce)
        except (TypeError, ValueError):
            continue
        if t_e <= t_s:
            continue

        y0_raw = c.get("y0_px") if c.get("y0_px") is not None else c.get("y0")
        y1_raw = c.get("y1_px") if c.get("y1_px") is not None else c.get("y1")
        if y0_raw is not None and y1_raw is not None:
            try:
                sy0, sy1 = float(y0_raw), float(y1_raw)
            except (TypeError, ValueError):
                continue
        else:
            y_pct = c.get("y_percent") if c.get("y_percent") is not None else c.get("positionY")
            if y_pct is None:
                continue
            try:
                cy = (float(y_pct) / 100.0) * float(H)
                hh = float(c.get("height_px") or (H * 0.055)) * 0.5
                sy0, sy1 = max(0.0, cy - hh), min(float(H), cy + hh)
            except (TypeError, ValueError):
                continue
        if sy1 <= sy0:
            continue
        x0_raw = c.get("x0_px") if c.get("x0_px") is not None else c.get("x0")
        x1_raw = c.get("x1_px") if c.get("x1_px") is not None else c.get("x1")
        try:
            sx0 = float(x0_raw) if x0_raw is not None else (0.06 * float(W))
            sx1 = float(x1_raw) if x1_raw is not None else (0.94 * float(W))
        except (TypeError, ValueError):
            sx0, sx1 = 0.06 * float(W), 0.94 * float(W)
        out.append((t_s, t_e, sx0, sy0, sx1, sy1))
    return out


def check_flashcards(
    plan: Dict[str, Any],
    mp4_path: Path,
    *,
    is_square: bool = False,
    plan_dir: Optional[Path] = None,
) -> Tuple[bool, str]:
    """Strict dual-source anti-occlusion Deep QC gate for Stage 3b bilingual flashcards:
    - Bounds & PNG existence verification for every card.
    - Dual-source YuNet expanded face shield overlap check (`0` tolerance; `ix >= 2 and iy >= 2` hard-fails)
      on BOTH clean `mix.mkv` (defeating occlusion blindness) AND final rendered `mp4_path`.
    - Visible render delta verification vs clean `mix.mkv` (mean abs pixel diff `>= 6.0` on 9:16).
    - Vertical non-overlap check against active subtitle cues from `subs_meta.json`.
    """
    eff_plan_dir = plan_dir or (Path(str(plan["_plan_dir"])) if isinstance(plan, dict) and plan.get("_plan_dir") else None)
    meta, meta_path = _load_flashcards_meta(plan, mp4_path, is_square=is_square, plan_dir=eff_plan_dir)
    expect_cards = _plan_has_flashcards(plan)

    if meta is None:
        if is_square:
            # 1:1 Square wraps the completed 9:16 reel directly (2-layer blur + centered 9:16)
            # without generating separate 1:1 flashcard metadata or PNGs.
            return True, "none"
        if expect_cards:
            return False, "expected flashcards from plan, but overlays/flashcards_meta.json is missing"
        return True, "none"

    cards = meta.get("cards") if isinstance(meta, dict) else None
    if not isinstance(cards, list) or not cards:
        if expect_cards:
            return False, f"expected flashcards from plan, but 0 cards recorded in {meta_path.name if meta_path else 'flashcards_meta.json'}"
        return True, "none"

    try:
        import cv2
        import numpy as np
    except Exception as exc:  # noqa: BLE001
        return True, f"{len(cards)} card(s) (frame check skipped: {exc})"

    out_cap = cv2.VideoCapture(str(mp4_path))
    if not out_cap.isOpened():
        return False, f"could not open {mp4_path.name}"

    mix_cap: Optional[Any] = None
    try:
        W = int(out_cap.get(cv2.CAP_PROP_FRAME_WIDTH) or meta.get("canvas_w") or 0)
        H = int(out_cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or meta.get("canvas_h") or 0)
        fps_vid = float(out_cap.get(cv2.CAP_PROP_FPS) or (plan.get("canvas") or {}).get("fps") or 30.0) or 30.0
        n_frames = int(out_cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        mp4_dur = (n_frames / fps_vid) if n_frames > 0 else 0.0

        mix_cap, mix_w, mix_h, _mix_path = _find_clean_mix_cap(
            mp4_path, W, H, mp4_dur, is_square=is_square, plan_dir=eff_plan_dir
        )

        # 1:1 square foreground mapping parameters for projecting clean 9:16 `mix.mkv` shields
        if is_square and H > 0:
            if mix_cap is not None and mix_h > 0:
                fg_w = max(2, int(round(H * (mix_w / float(mix_h)) / 2.0)) * 2)
            else:
                fg_w = max(2, int(round(H * 9.0 / 16.0 / 2.0)) * 2)
            fg_w = min(W, fg_w)
            fg_x0 = max(0, (W - fg_w) // 2)
        else:
            fg_w, fg_x0 = W, 0

        # Build timeline segment -> layout map
        segs = (plan.get("segments") or []) if isinstance(plan, dict) else []
        seg_spans: List[Tuple[float, float, str]] = []
        seg_by_id: Dict[str, str] = {}
        if segs:
            starts, durs, _, _ = compute_timeline_starts(plan, max(1, int(round(fps_vid))))
            for s, st, d in zip(segs, starts, durs):
                if isinstance(s, dict):
                    lay = _norm_layout_for_verify(s, plan)
                    seg_spans.append((st, st + d, lay))
                    if s.get("id"):
                        seg_by_id[str(s["id"])] = lay

        def _layout_at(t_sec: float, card_seg_ids: Any) -> str:
            for s0, s1, lay in seg_spans:
                if s0 - 0.01 <= t_sec <= s1 + 0.01:
                    return lay
            if isinstance(card_seg_ids, list):
                for csid in card_seg_ids:
                    if str(csid) in seg_by_id:
                        return seg_by_id[str(csid)]
            return "single"

        def _resolve_png(png_val: Any) -> Optional[Path]:
            if not png_val:
                return None
            p = Path(str(png_val))
            if p.is_absolute():
                return p if p.is_file() and p.stat().st_size > 0 else None
            search_roots: List[Path] = []
            if meta_path is not None:
                search_roots.extend([meta_path.parent, meta_path.parent.parent])
            if eff_plan_dir is not None:
                search_roots.append(eff_plan_dir)
            search_roots.extend([mp4_path.parent, ROOT])
            for root_d in search_roots:
                cand_p = root_d / p
                if cand_p.is_file() and cand_p.stat().st_size > 0:
                    return cand_p
            return None

        sub_cues = (
            _load_subs_cues_for_verify(plan, mp4_path, W, H, is_square=is_square, plan_dir=eff_plan_dir)
            if is_subs_enabled(plan)
            else []
        )

        failures: List[str] = []
        tiers_used: List[str] = []
        if isinstance(meta, dict) and meta.get("pango_warnings"):
            warns = meta["pango_warnings"]
            first_w = warns[0] if isinstance(warns, list) and warns else str(warns)
            failures.append(f"Pango font warning during flashcard render: {first_w}")

        card_texts: List[str] = []
        for idx_c, card in enumerate(cards, 1):
            if not isinstance(card, dict):
                failures.append(f"card #{idx_c}: invalid metadata entry")
                continue
            label = str(
                card.get("text")
                or (f"{card.get('en', '')} | {card.get('native', '')}" if (card.get("en") or card.get("native")) else "")
                or f"card#{idx_c}"
            ).strip()
            card_texts.extend([str(card.get("en") or ""), str(card.get("native") or ""), str(card.get("text") or "")])
            t0 = float(card.get("start") if card.get("start") is not None else card.get("t0", 0.0))
            t1 = float(card.get("end") if card.get("end") is not None else card.get("t1", 0.0))
            x0 = float(card.get("x0", 0.0))
            y0 = float(card.get("y0", 0.0))
            x1 = float(card.get("x1") if card.get("x1") is not None else (x0 + float(card.get("w", 0.0))))
            y1 = float(card.get("y1") if card.get("y1") is not None else (y0 + float(card.get("h", 0.0))))
            tier = str(card.get("tier") or "unknown").strip() or "unknown"
            tiers_used.append(tier)

            # 1) Duration & canvas bounds check
            if t1 <= t0:
                failures.append(f"card #{idx_c} ('{label}'): non-positive span {t0:.2f}..{t1:.2f}s")
                continue
            bounds_ok = (
                x0 >= -0.5
                and y0 >= -0.5
                and x1 <= W + 0.5
                and y1 <= H + 0.5
                and x1 > x0
                and y1 > y0
            )
            if not bounds_ok:
                failures.append(
                    f"card #{idx_c} ('{label}'): out of bounds ({x0:.0f},{y0:.0f},{x1:.0f},{y1:.0f}) on {W}x{H}"
                )

            # 2) PNG asset existence + tofu-box check
            png_raw = card.get("png")
            resolved_png = _resolve_png(png_raw)
            if resolved_png is None:
                failures.append(f"card #{idx_c} ('{label}'): missing PNG ({png_raw})")
            else:
                try:
                    import subtitles as _subs

                    qc = _subs.detect_tofu_boxes_in_png(resolved_png)
                    if qc.get("tofu_count", 0) > 0:
                        failures.append(
                            f"card #{idx_c} ('{label}'): detected {qc['tofu_count']} hollow tofu box(es) in {resolved_png.name}"
                        )
                except Exception:
                    pass

            # 3) Timestamp sampling: every 0.25s + midpoint across [t0, t1]
            span = t1 - t0
            mid = 0.5 * (t0 + t1)
            inset = min(0.12, span * 0.25)
            s_lo = min(mid, t0 + inset)
            s_hi = max(mid, t1 - inset)
            raw_ts: List[float] = [mid]
            cur_t = s_lo
            while cur_t <= s_hi + 1e-6:
                raw_ts.append(cur_t)
                cur_t += 0.25
            if s_hi - raw_ts[-1] > 0.05:
                raw_ts.append(s_hi)
            max_valid_t = max(0.0, mp4_dur - 0.04) if mp4_dur > 0.0 else t1
            sample_ts = sorted({round(max(0.0, min(ts, max_valid_t)), 3) for ts in raw_ts})

            card_face_hits: List[str] = []
            vis_deltas: List[float] = []
            mid_delta: Optional[float] = None
            mid_rounded = round(max(0.0, min(mid, max_valid_t)), 3)

            ix0_roi = max(0, min(W, int(math.floor(x0))))
            iy0_roi = max(0, min(H, int(math.floor(y0))))
            ix1_roi = max(0, min(W, int(math.ceil(x1))))
            iy1_roi = max(0, min(H, int(math.ceil(y1))))

            for t_s in sample_ts:
                seg_layout = _layout_at(t_s, card.get("seg_ids"))

                out_cap.set(cv2.CAP_PROP_POS_MSEC, t_s * 1000.0)
                ok_out, out_fr = out_cap.read()
                if not ok_out:
                    out_fr = None

                mix_fr = None
                if mix_cap is not None:
                    mix_cap.set(cv2.CAP_PROP_POS_MSEC, t_s * 1000.0)
                    ok_mix, mf = mix_cap.read()
                    if ok_mix and mf is not None:
                        mix_fr = mf

                # Source A: clean `mix.mkv` (anti-occlusion ground truth)
                if mix_fr is not None:
                    if is_square and fg_w > 0 and H > 0:
                        mix_fg = (
                            cv2.resize(mix_fr, (fg_w, H), interpolation=cv2.INTER_AREA)
                            if (mix_fr.shape[1] != fg_w or mix_fr.shape[0] != H)
                            else mix_fr
                        )
                        clean_shields = [
                            (fg_x0 + fx0, fy0, fg_x0 + fx1, fy1)
                            for fx0, fy0, fx1, fy1 in _detect_shields_for_verify(mix_fg, layout=seg_layout)
                        ]
                    else:
                        clean_shields = _detect_shields_for_verify(mix_fr, layout=seg_layout)
                    for fx0, fy0, fx1, fy1 in clean_shields:
                        ov_x = min(x1, fx1) - max(x0, fx0)
                        ov_y = min(y1, fy1) - max(y0, fy0)
                        if ov_x >= 2.0 and ov_y >= 2.0:
                            card_face_hits.append(
                                f"card #{idx_c} ('{label}') @{t_s:.2f}s overlaps clean_mix face shield "
                                f"({fx0:.0f},{fy0:.0f},{fx1:.0f},{fy1:.0f}) by {ov_x:.0f}x{ov_y:.0f}px"
                            )
                            break

                # Source B: final rendered `out_mp4` (catches unoccluded faces & standalone runs;
                # on 1:1 pillarboxed square frames, inspect the central 9:16 foreground strip only
                # so blurred background sidebars never trigger false face overlaps).
                if out_fr is not None:
                    if is_square and fg_w > 0 and H > 0 and out_fr.shape[1] >= fg_x0 + fg_w:
                        out_fg = np.ascontiguousarray(out_fr[:, fg_x0 : fg_x0 + fg_w])
                        out_shields = [
                            (fg_x0 + fx0, fy0, fg_x0 + fx1, fy1)
                            for fx0, fy0, fx1, fy1 in _detect_shields_for_verify(out_fg, layout=seg_layout)
                        ]
                    else:
                        out_shields = _detect_shields_for_verify(out_fr, layout=seg_layout)
                    for fx0, fy0, fx1, fy1 in out_shields:
                        ov_x = min(x1, fx1) - max(x0, fx0)
                        ov_y = min(y1, fy1) - max(y0, fy0)
                        if ov_x >= 2.0 and ov_y >= 2.0:
                            card_face_hits.append(
                                f"card #{idx_c} ('{label}') @{t_s:.2f}s overlaps rendered face shield "
                                f"({fx0:.0f},{fy0:.0f},{fx1:.0f},{fy1:.0f}) by {ov_x:.0f}x{ov_y:.0f}px"
                            )
                            break

                # Visible render check vs clean `mix.mkv` (9:16)
                if (
                    not is_square
                    and mix_fr is not None
                    and out_fr is not None
                    and out_fr.shape == mix_fr.shape
                    and ix1_roi > ix0_roi
                    and iy1_roi > iy0_roi
                ):
                    out_roi = out_fr[iy0_roi:iy1_roi, ix0_roi:ix1_roi].astype(np.float32)
                    mix_roi = mix_fr[iy0_roi:iy1_roi, ix0_roi:ix1_roi].astype(np.float32)
                    delta = float(np.abs(out_roi - mix_roi).mean())
                    vis_deltas.append(delta)
                    if abs(t_s - mid_rounded) < 0.005:
                        mid_delta = delta

            if card_face_hits:
                failures.append(card_face_hits[0])

            if not is_square and mix_cap is not None and vis_deltas:
                vis_check = mid_delta if mid_delta is not None else max(vis_deltas)
                if vis_check < 6.0 and max(vis_deltas) < 6.0:
                    failures.append(
                        f"card #{idx_c} ('{label}') not visibly rendered inside ({ix0_roi},{iy0_roi},{ix1_roi},{iy1_roi}) "
                        f"(mean delta {vis_check:.1f} < 6.0 vs clean mix.mkv)"
                    )

            # 4) Subtitle cue overlap check
            for cs, ce, sx0, sy0, sx1, sy1 in sub_cues:
                if ce > t0 + 0.04 and cs < t1 - 0.04:
                    ix_sub = min(x1, sx1) - max(x0, sx0)
                    iy_sub = min(y1, sy1) - max(y0, sy0)
                    if ix_sub >= 2.0 and iy_sub >= 2.0:
                        failures.append(
                            f"card #{idx_c} ('{label}') [{t0:.2f}-{t1:.2f}s] y=[{y0:.0f},{y1:.0f}] "
                            f"overlaps subtitle cue [{cs:.2f}-{ce:.2f}s] y=[{sy0:.0f},{sy1:.0f}] by {iy_sub:.0f}px"
                        )
                        break

        # Also scan any cached flashcard PNGs & probe Indic words present in cards
        bad_card_cache = _scan_pngs_for_tofu(
            _gather_work_pngs(plan, mp4_path, ("_cards_cache", "overlays/flashcards"), include_banners=False),
            max_check=24,
        )
        if bad_card_cache is not None:
            bad_cp, qc_c = bad_card_cache
            failures.append(f"detected {qc_c['tofu_count']} hollow tofu box(es) in cached card PNG ({bad_cp.name})")

        card_indic_probe = _collect_indic_probe_text(card_texts, max_words=12)
        if card_indic_probe:
            try:
                import subtitles as _subs

                if _subs.available():
                    probe_ok, probe_detail = _subs.probe_text_render(card_indic_probe, style_id=2)
                    if not probe_ok:
                        failures.append(f"Indic card font probe failed: {probe_detail}")
            except Exception as exc:  # noqa: BLE001
                failures.append(f"Indic card font probe error: {exc}")

        tier_counts: Dict[str, int] = {}
        for tr in tiers_used:
            tier_counts[tr] = tier_counts.get(tr, 0) + 1
        pref_order = ["top_empty", "alt_empty", "body_center", "bottom_safe"]
        ordered_tiers = [k for k in pref_order if k in tier_counts] + [k for k in tier_counts if k not in pref_order]
        tiers_str = ", ".join(f"{k}={tier_counts[k]}" for k in ordered_tiers)
        src_note = ", dual-source clean mix.mkv" if mix_cap is not None else ""
        prefix = "1:1 " if is_square else ""
        if not failures:
            return True, f"{prefix}{len(cards)} card(s) verified (0 face overlap, tiers: {tiers_str}{src_note})"
        return False, f"{prefix}{len(cards)} card(s), {len(failures)} failure(s) (tiers: {tiers_str}): " + "; ".join(failures[:6])
    finally:
        out_cap.release()
        if mix_cap is not None:
            mix_cap.release()


REVIEW_MAX_SIDE = 1024


def check_square_output(
    sq_path: Path,
    sample_count: int = 5,
    *,
    plan: Optional[Dict[str, Any]] = None,
    plan_dir: Optional[Path] = None,
) -> Tuple[bool, str]:
    """1:1 resolution (W == H), black-frame, frozen-frame, and centered foreground face crop safety check on SQ_<name>.mp4."""
    if not sq_path.is_file():
        return False, f"missing {sq_path.name}"
    info = probe(sq_path)
    sq_w = int(info.get("width") or 0)
    sq_h = int(info.get("height") or 0)
    sq_dur = float(info.get("duration") or 0.0)
    try:
        import cv2
        import numpy as np
    except Exception:
        ok_dim = sq_w > 0 and sq_w == sq_h
        return ok_dim, f"1:1 {sq_w}x{sq_h}, {tc(sq_dur)} -> {sq_path.name}"

    try:
        import vision_tracker
    except Exception:
        vision_tracker = None  # type: ignore[assignment]

    cap = cv2.VideoCapture(str(sq_path))
    if not cap.isOpened():
        return False, f"could not open {sq_path.name}"
    if sq_w <= 0 or sq_h <= 0:
        sq_w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
        sq_h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
    fps = float(cap.get(cv2.CAP_PROP_FPS) or info.get("fps") or 30.0) or 30.0
    n_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    if sq_dur <= 0.0 and n_frames > 0:
        sq_dur = n_frames / fps

    n_samples = max(4, min(6, int(sample_count)))
    spots = [round(0.10 + 0.80 * k / max(1, n_samples - 1), 3) for k in range(n_samples)]
    checked = 0
    black_spots: List[str] = []
    frozen_pairs = 0
    prev_sq_gray: Optional[Any] = None
    fg_w = max(2, int(round(sq_h * 9.0 / 16.0 / 2)) * 2) if sq_h > 0 else 0
    fg_x0 = max(0, (sq_w - fg_w) // 2)
    fg_x1 = min(sq_w, fg_x0 + fg_w)
    clipped_faces = 0

    # Intentional dark-fade transition times from `plan` (only these may forgive a black sample within ±0.50s)
    dark_fade_times: List[float] = []
    if isinstance(plan, dict) and plan.get("segments"):
        plan_fps = int(round(float((plan.get("canvas") or {}).get("fps") or fps or 30))) or 30
        p_starts, _, _, _ = compute_timeline_starts(plan, plan_fps)
        for idx_s, seg in enumerate(plan.get("segments") or []):
            if idx_s == 0 or not isinstance(seg, dict):
                continue
            tr_base = str(seg.get("transition") or "cut").strip().lower().split("@", 1)[0].strip()
            if tr_base in _DARK_FADE_TRANSITIONS:
                dark_fade_times.append(p_starts[idx_s])

    for frac in spots:
        t_sec = max(0.0, sq_dur * frac) if sq_dur > 0 else 0.0
        cap.set(cv2.CAP_PROP_POS_MSEC, t_sec * 1000.0)
        ok_fr, fr = cap.read()
        if not ok_fr or fr is None:
            continue
        checked += 1
        gray = cv2.cvtColor(fr, cv2.COLOR_BGR2GRAY)
        if prev_sq_gray is not None and gray.shape == prev_sq_gray.shape:
            if float(np.abs(gray.astype(np.float32) - prev_sq_gray.astype(np.float32)).mean()) < FROZEN_DIFF_EPS:
                frozen_pairs += 1
        prev_sq_gray = gray

        if float(gray.mean()) < BLACK_MEAN:
            if any(abs(t_sec - dt_f) <= 0.50 for dt_f in dark_fade_times):
                continue
            black_spots.append(f"{t_sec:.1f}s")
        elif vision_tracker is not None and fg_x1 > fg_x0:
            fg_crop = fr[:, fg_x0:fg_x1]
            faces = vision_tracker.prominent_faces(vision_tracker.detect_faces_in_frame(fg_crop))
            for f in faces:
                _ex, ey = f.get("eye_center_norm", [0.5, 0.38])
                if ey <= 0.01 or ey >= 0.97:
                    clipped_faces += 1
    cap.release()

    issues: List[str] = []
    if sq_w <= 0 or sq_h <= 0 or sq_w != sq_h:
        issues.append(f"non-square {sq_w}x{sq_h}")
    if checked == 0:
        issues.append("0 readable frames")
    if black_spots:
        issues.append(f"black frames @ {', '.join(black_spots)}")
    if checked >= 4 and sq_dur >= 2.0 and frozen_pairs >= checked - 1:
        issues.append("frozen video stream")
    if clipped_faces >= 2:
        issues.append(f"clipped foreground faces ({clipped_faces})")
    sq_cards_note = ""
    if plan is not None or plan_dir is not None:
        sq_cards_ok, sq_cards_summary = check_flashcards(
            plan or {}, sq_path, is_square=True, plan_dir=plan_dir
        )
        if not sq_cards_ok:
            issues.append(f"flashcards: {sq_cards_summary}")
        elif sq_cards_summary != "none":
            sq_cards_note = f", {sq_cards_summary}"
    ok = len(issues) == 0
    if ok:
        return True, f"1:1 {sq_w}x{sq_h}, {checked} sampled frames clean{sq_cards_note} -> {sq_path.name}"
    return False, f"1:1 {sq_w}x{sq_h}, {'; '.join(issues)} -> {sq_path.name}"


def build_review_sheet(
    plan: Dict[str, Any],
    mp4_path: Path,
    out_path: Path,
    tile_w: int = 104,
    sq_path: Optional[Path] = None,
) -> Optional[Path]:
    """Build compact whole-video review contact sheet: 3 frames per cut (start, middle, end)
    labelled with cut ID and timestamp, plus a 4-frame 1:1 square strip when `SQ_<name>.mp4` exists.
    """
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
    label_h = 14
    cuts, t0 = [], 0.0
    seg_mids: List[float] = []
    for seg in plan.get("segments", []):
        dur = max(1, round(_segment_duration(seg) * fps)) / fps
        seg_mids.append(t0 + dur / 2.0)
        row = []
        for frac_t in (min(0.25, dur * 0.3), dur / 2, max(dur - 0.25, dur * 0.7)):
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
        group = cuts[r : r + per_row]
        while len(group) < per_row:
            group.append(np.full_like(cuts[0], 255))
        rows.append(np.hstack(group))
    sheet = np.vstack(rows)

    cand_sq = sq_path if sq_path is not None else square_job_output(mp4_path)
    if cand_sq.is_file() and cand_sq.resolve() != mp4_path.resolve():
        sq_cap = cv2.VideoCapture(str(cand_sq))
        if sq_cap.isOpened():
            sq_fps = float(sq_cap.get(cv2.CAP_PROP_FPS) or fps or 30.0) or 30.0
            sq_frames = int(sq_cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
            sq_w_raw = int(sq_cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
            sq_h_raw = int(sq_cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
            sq_dur = (sq_frames / sq_fps) if sq_frames > 0 else t0
            if len(seg_mids) >= 4:
                idxs = [int(round(m_idx * (len(seg_mids) - 1) / 3.0)) for m_idx in range(4)]
                sample_ts = [min(max(0.0, sq_dur - 0.05), seg_mids[idx]) if sq_dur > 0 else seg_mids[idx] for idx in idxs]
            else:
                sample_ts = [max(0.0, sq_dur * frac) for frac in (0.12, 0.37, 0.63, 0.88)]

            sq_side = min(220, max(120, (sheet.shape[1] - 24) // 4))
            sq_tiles = []
            for idx_sq, t_sq in enumerate(sample_ts):
                sq_cap.set(cv2.CAP_PROP_POS_MSEC, t_sq * 1000.0)
                ok_sq, fr_sq = sq_cap.read()
                tile_sq = (
                    cv2.resize(fr_sq, (sq_side, sq_side), interpolation=cv2.INTER_AREA)
                    if ok_sq and fr_sq is not None
                    else np.zeros((sq_side, sq_side, 3), np.uint8)
                )
                bordered_sq = cv2.copyMakeBorder(tile_sq, 1, 1, 2, 2, cv2.BORDER_CONSTANT, value=(220, 220, 220))
                lbl_sq = np.full((label_h, bordered_sq.shape[1], 3), 235, np.uint8)
                cv2.putText(
                    lbl_sq,
                    f"1:1 #{idx_sq + 1} @{t_sq:.1f}s",
                    (3, 11),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.35,
                    (20, 20, 20),
                    1,
                    cv2.LINE_AA,
                )
                sq_tiles.append(np.vstack([lbl_sq, bordered_sq]))
            sq_cap.release()

            sq_row = np.hstack(sq_tiles)
            target_w = max(sheet.shape[1], sq_row.shape[1])
            if sheet.shape[1] < target_w:
                sheet = cv2.copyMakeBorder(sheet, 0, 0, 0, target_w - sheet.shape[1], cv2.BORDER_CONSTANT, value=(255, 255, 255))
            if sq_row.shape[1] < target_w:
                sq_row = cv2.copyMakeBorder(sq_row, 0, 0, 0, target_w - sq_row.shape[1], cv2.BORDER_CONSTANT, value=(255, 255, 255))
            hdr = np.full((18, target_w, 3), 36, np.uint8)
            cv2.putText(
                hdr,
                f"1:1 SQUARE ({sq_w_raw}x{sq_h_raw}): {cand_sq.name[:48]}",
                (6, 13),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.40,
                (255, 255, 255),
                1,
                cv2.LINE_AA,
            )
            sheet = np.vstack([sheet, hdr, sq_row])

    h_sh, w_sh = sheet.shape[:2]
    k = float(REVIEW_MAX_SIDE) / max(h_sh, w_sh)
    if k < 1.0:
        nw = max(1, min(REVIEW_MAX_SIDE, int(round(w_sh * k))))
        nh = max(1, min(REVIEW_MAX_SIDE, int(round(h_sh * k))))
        sheet = cv2.resize(sheet, (nw, nh), interpolation=cv2.INTER_AREA)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(out_path), sheet, [cv2.IMWRITE_JPEG_QUALITY, 70])
    return out_path


def run_verification(
    plan_path: str | Path,
    script_path: Optional[str | Path] = None,
    target_lufs: Optional[float] = None,
) -> bool:
    """Run all deterministic Deep QC checks and print the structured QA summary."""
    p_file, plan = load_plan(plan_path)
    out_mp4 = _locate_output_mp4(plan, p_file)
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

    if target_lufs is None:
        audio_cfg = plan.get("audio") or {}
        base_loud = audio_cfg.get("loudness")
        if base_loud is None:
            base_loud = getattr(common, "DEFAULT_LOUDNESS_LUFS", DEFAULT_LOUDNESS_LUFS)
        gain_raw = audio_cfg.get("gain_db", 0.0)
        if isinstance(gain_raw, str):
            gain_raw = re.sub(r"\s*db\s*$", "", gain_raw.strip(), flags=re.IGNORECASE)
        eff_target_lufs = float(base_loud) + float(gain_raw or 0.0)
    else:
        eff_target_lufs = float(target_lufs)
    audio_ok, i_lufs, tp_db = check_loudness(out_mp4, target_lufs=eff_target_lufs)

    plan["_plan_dir"] = str(p_file.parent)
    cuts_ok, clipped_w, long_pauses, jc_masked, b_clicks = check_cuts_and_pauses(plan, mp4_path=out_mp4)

    if not script_path:
        side = p_file.parent / "plan.json"
        try:
            script_path = plan.get("script") or (read_json(side).get("script") if side.is_file() else None)
        except Exception:
            script_path = None
    resolved_script = resolve(script_path) if script_path else None
    script_ok, cov_lines, tot_lines, b_dups = check_script_coverage(plan, script_path=resolved_script)

    sheet_path = p_file.parent / "marks_contact_sheet.jpg"
    marks_ok, m_pass, m_tot, m_details, sheet_kb = verify_marks_and_build_contact_sheet(
        out_mp4, plan, seg_start, exp_w, exp_h, sheet_path
    )
    split_hook_ok, split_hook_summary = check_split_and_hook_rules(plan)
    subs_ok, subs_summary = check_subtitles_srt(plan, out_mp4)
    if subs_ok and not is_subs_enabled(plan):
        # Scroll-page (`#page scroll`) and banner-only edits still render text PNGs that need tofu/Pango QC
        page_meta_cands = [p_file.parent / "_page" / "page_meta.json", WORK / "_page" / "page_meta.json"]
        for pmc in page_meta_cands:
            if pmc.is_file():
                try:
                    pm = read_json(pmc)
                    if isinstance(pm, dict) and pm.get("pango_warnings"):
                        pw = pm["pango_warnings"]
                        subs_ok = False
                        subs_summary = f"Pango font warning during scroll page render: {pw[0] if isinstance(pw, list) else pw}"
                        break
                except Exception:
                    pass
        if subs_ok:
            bad_page_png = _scan_pngs_for_tofu(
                _gather_work_pngs(plan, out_mp4, ("_page", "_scroll_page"), include_banners=True),
                max_check=16,
            )
            if bad_page_png is not None:
                bp, bqc = bad_page_png
                subs_ok = False
                subs_summary = f"detected {bqc['tofu_count']} hollow tofu box(es) in {bp.name}"
        if subs_ok and plan.get("page"):
            page_texts = _extract_plan_banner_texts(plan) + [
                str(s.get("text") or "") for s in (plan.get("segments") or []) if isinstance(s, dict)
            ]
            page_indic = _collect_indic_probe_text(page_texts, max_words=12)
            if page_indic:
                try:
                    import subtitles as _subs
                    if _subs.available():
                        p_ok, p_det = _subs.probe_text_render(page_indic, style_id=2)
                        if not p_ok:
                            subs_ok = False
                            subs_summary = f"Indic scroll-page font check failed: {p_det}"
                except Exception as exc:  # noqa: BLE001
                    subs_ok = False
                    subs_summary = f"Indic scroll-page font check error: {exc}"
    cards_ok, cards_summary = check_flashcards(plan, out_mp4, is_square=False, plan_dir=p_file.parent)
    pic_ok, pic_bad, pic_n = check_picture_timeline(plan, out_mp4, plan_dir=p_file.parent)
    sq_mp4 = square_job_output(out_mp4)
    has_square = sq_mp4.is_file() and sq_mp4.resolve() != out_mp4.resolve()
    sq_ok, sq_summary = (
        check_square_output(sq_mp4, plan=plan, plan_dir=p_file.parent) if has_square else (True, "")
    )
    review = build_review_sheet(
        plan,
        out_mp4,
        p_file.parent / "review_sheet.jpg",
        sq_path=sq_mp4 if has_square else None,
    )
    frames = check_frames(plan, out_mp4)
    alt_proj_dir = WORK / out_mp4.stem
    if alt_proj_dir.is_dir() and alt_proj_dir.resolve() != p_file.parent.resolve() and sheet_path.is_file():
        (alt_proj_dir / "marks_contact_sheet.jpg").write_bytes(sheet_path.read_bytes())

    pct = int(round(100.0 * cov_lines / max(1, tot_lines)))
    rel_out = str(out_mp4)
    rel_sheet = f"work/{p_file.parent.name}/marks_contact_sheet.jpg"

    print(
        f"[VERIFY {'PASS' if video_ok else 'FAIL'}] {rel_out} "
        f"({info['width']}x{info['height']} @ {int(round(info['fps'] or 0))}fps, "
        f"{tc(actual_dur)} vs {tc(exp_total)} expected, Δ={dur_diff:+.2f}s)"
    )
    print(
        f"[AUDIO  {'PASS' if audio_ok else 'FAIL'}] Integrated Loudness: {i_lufs:.1f} LUFS "
        f"(target {eff_target_lufs:.1f} LUFS), True Peak: {tp_db:.1f} dBTP"
    )
    _jg_parts: List[str] = []
    if _LAST_JOIN_GAP_STATS.get("count"):
        _jg_parts.append(
            f"join gaps median={_LAST_JOIN_GAP_STATS['median_ms']}ms "
            f"p90={_LAST_JOIN_GAP_STATS['p90_ms']}ms max={_LAST_JOIN_GAP_STATS['max_ms']}ms"
        )
    if _LAST_JOIN_GAP_STATS.get("beat_count"):
        _jg_parts.append(
            f"post-beat gaps({_LAST_JOIN_GAP_STATS['beat_count']}) "
            f"median={_LAST_JOIN_GAP_STATS['beat_median_ms']}ms "
            f"max={_LAST_JOIN_GAP_STATS['beat_max_ms']}ms"
        )
    _jg_exc = _LAST_JOIN_GAP_STATS.get("excessive_joins") or []
    if _jg_exc:
        _jg_parts.append(f"{len(_jg_exc)} loose join(s) >380ms")
    _jg_txt = (", " + ", ".join(_jg_parts)) if _jg_parts else ""
    print(
        f"[CUTS   {'PASS' if cuts_ok else 'FAIL'}] {len(plan.get('segments', []))} segments verified: "
        f"{clipped_w} clipped words, {long_pauses} internal pauses >{DEAD_AIR_THRESHOLD:.2f}s{_jg_txt}, "
        f"{jc_masked} jump-cuts masked, {b_clicks} boundary clicks"
    )
    if not cuts_ok and _CUT_FAIL_NOTES:
        _shown = _CUT_FAIL_NOTES[:12]
        _more = f" (+{len(_CUT_FAIL_NOTES) - 12} more)" if len(_CUT_FAIL_NOTES) > 12 else ""
        print("[CUTS   WHERE] " + "; ".join(_shown) + _more)
    print(
        f"[SCRIPT {'PASS' if script_ok else 'FAIL'}] {cov_lines}/{tot_lines} script lines present in order "
        f"({pct}% coverage, {b_dups} boundary duplicates)"
    )
    print(
        f"[MARKS  {'PASS' if marks_ok else 'FAIL'}] {m_pass}/{m_tot} marks verified via RGB pixel check "
        f"({', '.join(m_details)}) -> {rel_sheet} ({sheet_kb} KB)"
    )
    print(f"[SPLIT  {'PASS' if split_hook_ok else 'FAIL'}] {split_hook_summary}")
    if cards_summary != "none" or not cards_ok:
        print(f"[CARDS  {'PASS' if cards_ok else 'FAIL'}] {cards_summary}")
    if is_subs_enabled(plan) or not subs_ok:
        print(f"[SUBS   {'PASS' if subs_ok else 'FAIL'}] {subs_summary}")
    if has_square:
        print(f"[SQUARE] {'PASS' if sq_ok else 'FAIL'} ({sq_summary})")
    if review:
        try:
            rel_review = review.relative_to(ROOT)
        except ValueError:
            rel_review = review
        sq_note = " + 4-frame 1:1 square strip" if has_square else ""
        print(f"[REVIEW] Whole-video review sheet (3 frames per cut{sq_note}, <=1024px): {rel_review}")
    detail = (
        f"{frames['frames']} frames: {len(frames['black'])} black, "
        f"{len(frames['frozen'])} frozen, "
        f"{len(frames['banner_on_face'])} banner-on-face, "
        f"{len(frames.get('split_alignment', []))} split-alignment, "
        f"{len(frames['no_face'])} panel(s) with missing faces"
    )
    if frames.get("lingering_banners"):
        detail += f", {len(frames['lingering_banners'])} lingering banner(s)"
    print(f"[FRAMES {'PASS' if frames['ok'] else 'FAIL'}] {frames['note'] or detail}")
    for kind, lines_list in (
        ("BLACK", frames["black"]),
        ("FROZEN", frames["frozen"]),
        ("BANNER ON FACE", frames["banner_on_face"]),
        ("SPLIT ALIGNMENT", frames.get("split_alignment", [])),
        ("SPLIT ALIGNMENT (warning)", frames.get("split_alignment_warnings", [])),
        ("LINGERING BANNER (warning)", frames.get("lingering_banners", [])),
        ("FACE CROP (warning)", frames["face_crop"]),
        ("NO FACE (warning)", frames["no_face"]),
    ):
        for ln in lines_list[:4]:
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
            ("Frames", frames["ok"], frames["note"] or detail),
            ("Picture vs plan", pic_ok, f"{pic_n} cuts matched" if pic_ok else "; ".join(pic_bad)[:200]),
        ]
        if cards_summary != "none" or not cards_ok:
            ev_list.append(("Flashcards", cards_ok, cards_summary))
        if is_subs_enabled(plan) or not subs_ok:
            ev_list.append(("Subtitles", subs_ok, subs_summary))
        if has_square:
            ev_list.append(("Square 1:1", sq_ok, sq_summary))
        for _tag, _ok, _txt in ev_list:
            events.emit("verify", f"{_tag}: {'pass' if _ok else 'FAIL'}, {_txt}", ok=bool(_ok), tag=_tag)
    except Exception:
        pass
    print(
        f"[PICTURE {'PASS' if pic_ok else 'FAIL'}] {pic_n} cuts: middle frame of each cut matches the plan"
        + ("" if pic_ok else ": " + "; ".join(pic_bad))
    )

    return bool(
        video_ok
        and audio_ok
        and cuts_ok
        and script_ok
        and marks_ok
        and split_hook_ok
        and cards_ok
        and subs_ok
        and pic_ok
        and frames["ok"]
        and sq_ok
    )


def main() -> None:
    ap = argparse.ArgumentParser(description="Post-render deterministic Deep QC verifier for Zeno.")
    ap.add_argument("plan", help="Path to plan.json or plan.edl")
    ap.add_argument("--script", default=None, help="Optional script file path for coverage verification")
    ap.add_argument("--loudness", type=float, default=None, help="Optional target LUFS override")
    args = ap.parse_args()

    ok = run_verification(args.plan, script_path=args.script, target_lufs=args.loudness)
    if not ok:
        sys.exit(1)


if __name__ == "__main__":
    main()
