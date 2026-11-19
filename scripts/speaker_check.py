"""Speaker voice verification using pitch (YIN F0), MFCC timbre, spectral centroid, and spectral flatness.

Given a script naming two or more speakers (e.g. `Man:` and `Robot:`), measures a 6-dimensional acoustic
voice vector per turn, robustly estimates each character's centroid via iterative median/mean refinement
(anchored by optional reference samples in `input/voices/<Speaker>.(wav|flac|ogg)`), verifies separability
via d-prime (`d' >= 2.0`) and label agreement (`>= 60%`), and returns wrong-speaker spans to cut.

Also provides `all_candidates_wrong_voice(line_candidates, voice_diag)` to flag script lines ONLY when
100% of available candidate takes fail the speaker check.
"""

from __future__ import annotations

from collections import Counter
from pathlib import Path
import re
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

import numpy as np

SR = 16000
MIN_UNIT_SEC = 0.38          # Minimum turn duration (~2 words) for stable F0 + MFCC estimation
MIN_UNITS_PER_SPEAKER = 2
MIN_DPRIME = 2.0             # Minimum inter-speaker separation (d') required to classify voices
LONG_PAUSE = 0.38            # Pause >= 0.38 s splits turns so mid-clip speaker swaps are isolated
MARGIN = 0.30                # Normalized distance margin required before flagging a wrong-voice turn
CUT_PAD = 0.04


def _load_audio(path: Path) -> np.ndarray:
    import soundfile as sf

    y, sr = sf.read(str(path), dtype="float32", always_2d=False)
    if y.ndim > 1:
        y = y.mean(axis=1)
    if sr != SR:
        import librosa

        y = librosa.resample(y, orig_sr=sr, target_sr=SR)
    return y


def build_units(words: Sequence[Dict[str, Any]], expected: Sequence[Optional[str]]) -> List[Tuple[int, int]]:
    """Group clip words into contiguous speaker turns separated by speaker changes or pauses `>= LONG_PAUSE`."""
    units: List[Tuple[int, int]] = []
    if not words:
        return units
    start = 0
    for i in range(1, len(words) + 1):
        if i == len(words):
            cut = True
        else:
            gap = float(words[i]["start"]) - float(words[i - 1]["end"])
            cut = gap >= LONG_PAUSE or (expected[i] != expected[i - 1])
        if cut:
            units.append((start, i - 1))
            start = i
    return units


def unit_features(y: np.ndarray, t0: float, t1: float) -> Optional[np.ndarray]:
    """Compute a 6D acoustic voice vector `[median(log F0), std(log F0), MFCC1, MFCC2, log(centroid), flatness*10]`."""
    import librosa

    if t1 - t0 < MIN_UNIT_SEC:
        return None
    seg = y[max(0, int(t0 * SR)) : min(len(y), int(t1 * SR))]
    if len(seg) < SR * MIN_UNIT_SEC:
        return None
    hop = 256
    try:
        f0 = librosa.yin(seg, fmin=60, fmax=450, sr=SR, frame_length=1024, hop_length=hop)
        rms = librosa.feature.rms(y=seg, frame_length=1024, hop_length=hop)[0][: len(f0)]
        f0 = f0[: len(rms)]
        voiced = (rms > 0.4 * np.median(rms)) & (f0 > 62) & (f0 < 440)
        if voiced.sum() < 8:
            return None
        lf = np.log(f0[voiced])
        n = len(rms)
        act = rms > 0.4 * np.median(rms)
        mf = librosa.feature.mfcc(y=seg, sr=SR, n_mfcc=13, n_fft=512, hop_length=hop)[1:9, :n][:, act[: min(n, 10 ** 9)]]
        cen_f = librosa.feature.spectral_centroid(y=seg, sr=SR, n_fft=512, hop_length=hop)[0][:n][act]
        flat_f = librosa.feature.spectral_flatness(y=seg, n_fft=512, hop_length=hop)[0][:n][act]
        if mf.shape[1] < 8:
            return None
        cen = float(np.log(cen_f.mean() + 1.0))
        flat = float(flat_f.mean())
    except Exception:
        return None
    return np.array(
        [
            float(np.median(lf)),
            float(lf.std()),
            float(mf[0].mean()),
            float(mf[1].mean()),
            cen,
            flat * 10.0,
        ]
    )


def _reference_files(speaker: str) -> List[Path]:
    """Locate optional speaker reference samples in `input/voices/<Speaker>.(wav|flac|ogg)`."""
    root = Path(__file__).resolve().parent.parent / "input" / "voices"
    name = speaker.strip().lower()
    out: List[Path] = []
    if root.is_dir():
        for q in sorted(root.iterdir()):
            if q.stem.strip().lower() == name and q.suffix.lower() in (".wav", ".flac", ".ogg"):
                out.append(q)
    return out


def _quietest(rms_env: Sequence[Tuple[float, float]], lo: float, hi: float) -> float:
    """Return the timestamp of the lowest-energy frame inside `[lo, hi]` for clean cut placement."""
    if hi < lo:
        lo, hi = hi, lo
    cands = [(t, db) for t, db in rms_env if lo <= t <= hi]
    if not cands:
        return round((lo + hi) / 2, 2)
    return round(min(cands, key=lambda x: x[1])[0], 2)


def analyze(items: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Cluster speaker voices across `items` and locate wrong-speaker word spans.

    Each item has `{"id", "wav": Path, "words": [{"w", "start", "end"}], "expected": [speaker|None], "rms_env"}`.
    Returns `{"ok", "reason", "speakers", "dprime", "agreement", "reference_voices", "clips": {id: {"spans": [...]}}}`.
    """
    speakers = sorted({sp for it in items for sp in it["expected"] if sp})
    result: Dict[str, Any] = {
        "ok": False,
        "reason": "",
        "speakers": speakers,
        "dprime": 0.0,
        "agreement": 0.0,
        "clips": {},
    }
    if len(speakers) < 2:
        result["reason"] = "script names fewer than 2 speakers"
        return result

    rows: List[Tuple[int, int, np.ndarray, str]] = []
    all_units: Dict[int, List[Tuple[int, int]]] = {}
    for k, it in enumerate(items):
        wav = Path(it["wav"])
        if not wav.is_file() or not it["words"]:
            continue
        y = _load_audio(wav)
        units = build_units(it["words"], it["expected"])
        all_units[k] = units
        for ui, (a, b) in enumerate(units):
            exp = Counter(sp for sp in it["expected"][a : b + 1] if sp).most_common(1)
            if not exp:
                continue
            f = unit_features(y, float(it["words"][a]["start"]), float(it["words"][b]["end"]))
            if f is not None:
                rows.append((k, ui, f, exp[0][0]))

    refs_used: List[str] = []
    for sp in speakers:
        ref = next((q for q in _reference_files(sp) if q.is_file()), None)
        if ref is None:
            continue
        try:
            y_ref = _load_audio(ref)
            hop_sec = 3.0
            for t in np.arange(0.0, max(len(y_ref) / SR - 2.0, 0.1), hop_sec):
                f = unit_features(y_ref, float(t), float(min(t + hop_sec, len(y_ref) / SR)))
                if f is not None:
                    for _ in range(3):
                        rows.append((-1, -1, f, sp))
            refs_used.append(sp)
        except Exception:
            pass
    result["reference_voices"] = refs_used

    counts_all = Counter(r[3] for r in rows)
    valid_sp = {s for s, c in counts_all.items() if c >= MIN_UNITS_PER_SPEAKER}
    if len(valid_sp) < 2:
        result["reason"] = f"too few measurable phrases per speaker {dict(counts_all)}"
        return result
    rows = [r for r in rows if r[3] in valid_sp]
    counts0 = Counter(r[3] for r in rows)

    X = np.array([r[2] for r in rows], dtype=float)
    mu, sd = X.mean(axis=0), X.std(axis=0) + 1e-6
    Z = (X - mu) / sd
    exp = np.array([r[3] for r in rows])

    cent = {s: np.median(Z[exp == s], axis=0) for s in counts0}
    for _ in range(8):
        names = list(cent)
        D = np.stack([np.linalg.norm(Z - cent[s], axis=1) for s in names], axis=1)
        pred = np.array(names)[D.argmin(axis=1)]
        trusted = pred == exp
        new = {
            s: (Z[trusted & (exp == s)].mean(axis=0) if (trusted & (exp == s)).sum() >= 2 else cent[s])
            for s in names
        }
        if all(np.allclose(new[s], cent[s]) for s in names):
            cent = new
            break
        cent = new
    names = list(cent)

    D = np.stack([np.linalg.norm(Z - cent[s], axis=1) for s in names], axis=1)
    pred = np.array(names)[D.argmin(axis=1)]
    trusted = pred == exp
    real = np.array([r[0] >= 0 for r in rows])
    result["agreement"] = round(float(trusted[real].mean()), 3) if real.any() else 0.0

    dps = []
    for i, a in enumerate(names):
        for b in names[i + 1 :]:
            axis = cent[b] - cent[a]
            n = np.linalg.norm(axis)
            ua, ub = Z[trusted & (exp == a)], Z[trusted & (exp == b)]
            if n < 1e-9 or len(ua) < 2 or len(ub) < 2:
                dps.append(0.0)
                continue
            axis = axis / n
            pa, pb = ua @ axis, ub @ axis
            pooled = np.sqrt((pa.var() + pb.var()) / 2 + 1e-9)
            dps.append(float(abs(pb.mean() - pa.mean()) / max(pooled, 0.25)))
    dprime = min(dps) if dps else 0.0
    result["dprime"] = round(dprime, 2)
    if result["agreement"] < 0.6:
        result["reason"] = f"voices do not match the script's speakers well enough (agreement {result['agreement']:.0%})"
        return result
    if dprime < MIN_DPRIME:
        result["reason"] = f"the voices sound too alike to tell apart (d' {dprime:.1f} < {MIN_DPRIME})"
        return result
    result["ok"] = True

    verdict: Dict[Tuple[int, int], Tuple[str, str, float]] = {}
    for (k, ui, _, sp), d_row, p in zip(rows, D, pred):
        if k < 0 or p == sp:
            continue
        d_exp, d_pred = d_row[names.index(sp)], d_row[names.index(str(p))]
        gap = float(np.linalg.norm(cent[sp] - cent[str(p)])) + 1e-9
        verdict[(k, ui)] = (str(p), sp, float((d_exp - d_pred) / gap))

    for k, it in enumerate(items):
        words, units = it["words"], all_units.get(k, [])
        env = it.get("rms_env") or []
        bad_units = [ui for ui in range(len(units)) if (k, ui) in verdict and verdict[(k, ui)][2] >= MARGIN]
        spans = []
        ui = 0
        while ui < len(bad_units):
            u0 = u1 = bad_units[ui]
            while ui + 1 < len(bad_units) and bad_units[ui + 1] == u1 + 1:
                ui += 1
                u1 = bad_units[ui]
            first, last = units[u0][0], units[u1][1]
            heard, expected = verdict[(k, u0)][0], verdict[(k, u0)][1]
            prev_end = float(words[first - 1]["end"]) if first > 0 else 0.0
            next_start = float(words[last + 1]["start"]) if last + 1 < len(words) else None
            start = _quietest(env, prev_end, float(words[first]["start"])) if first > 0 else 0.0
            end = _quietest(env, float(words[last]["end"]), next_start) if next_start is not None else None
            spans.append(
                {
                    "first": first,
                    "last": last,
                    "start": start,
                    "end": end,
                    "expected": expected,
                    "heard": heard,
                    "text": " ".join(str(words[x]["w"]) for x in range(first, last + 1)),
                }
            )
            ui += 1
        result["clips"][it["id"]] = {"spans": spans}
    return result


def _parse_line_map_candidates(text: str) -> List[Tuple[str, str, List[Dict[str, Any]]]]:
    """Parse `line_map.md` text into `[(line_id, expected_speaker, candidates), ...]`."""
    out: List[Tuple[str, str, List[Dict[str, Any]]]] = []
    cur_id = ""
    cur_spk = ""
    cur_cands: List[Dict[str, Any]] = []
    header_re = re.compile(r"^(L\d+)\b(?:\s*\[([^\]]+)\])?")
    cand_re = re.compile(
        r"^\s*CAND\s+(\S+)\s+(-?\d+(?:\.\d+)?)-(-?\d+(?:\.\d+)?)"
        r"(?:.*?\[voice=([^\]]+)\])?"
    )
    for raw in text.splitlines():
        hm = header_re.match(raw)
        if hm:
            if cur_id:
                out.append((cur_id, cur_spk, cur_cands))
            cur_id = hm.group(1)
            cur_spk = (hm.group(2) or "").strip()
            cur_cands = []
            continue
        if not cur_id:
            continue
        cm = cand_re.match(raw)
        if cm:
            voice_tag = (cm.group(4) or "").strip()
            voice_tok = voice_tag.split()[0] if voice_tag else ""
            uncertain = voice_tok.endswith("?")
            voice_clean = voice_tok.rstrip("?")
            cur_cands.append(
                {
                    "src": cm.group(1),
                    "start": float(cm.group(2)),
                    "end": float(cm.group(3)),
                    "heard": voice_clean,
                    "uncertain": uncertain,
                }
            )
    if cur_id:
        out.append((cur_id, cur_spk, cur_cands))
    return out


def _normalize_candidate_entry(cand: Any, default_expected: str = "") -> Dict[str, Any]:
    """Normalize dict, tuple (`script_order` candidate tuple), or `CAND ...` string into a uniform dict."""
    if isinstance(cand, dict):
        src = str(cand.get("src") or cand.get("source_clip") or cand.get("clip_name") or cand.get("name") or "")
        clip_obj = cand.get("clip") if isinstance(cand.get("clip"), dict) else None
        if not src and clip_obj:
            src = str(clip_obj.get("src") or "")
        return {
            "id": cand.get("id"),
            "src": src,
            "start": float(cand.get("start", cand.get("start_sec", 0.0)) or 0.0),
            "end": float(cand.get("end", cand.get("end_sec", 0.0)) or 0.0),
            "expected": str(cand.get("expected") or cand.get("speaker") or default_expected or "").strip(),
            "heard": str(cand.get("heard") or cand.get("voice") or "").strip(),
            "uncertain": bool(cand.get("uncertain", False)),
            "wrong_voice": cand.get("wrong_voice"),
            "voice_ok": cand.get("voice_ok"),
            "voice_fail": cand.get("voice_fail"),
            "clip_voice_hint": cand.get("clip_voice_hint") or (clip_obj.get("clip_voice_hint") if clip_obj else None),
        }
    if isinstance(cand, (tuple, list)):
        # script_order candidate tuple: (sort_key, r, name, w_start, w_end, reps, clip_dict)
        if len(cand) >= 7 and isinstance(cand[2], str):
            clip_obj = cand[6] if isinstance(cand[6], dict) else {}
            return {
                "id": None,
                "src": str(clip_obj.get("src") or cand[2]),
                "start": float(cand[3]),
                "end": float(cand[4]),
                "expected": default_expected,
                "heard": "",
                "uncertain": False,
                "wrong_voice": None,
                "voice_ok": None,
                "voice_fail": None,
                "clip_voice_hint": clip_obj.get("clip_voice_hint"),
            }
        if len(cand) >= 3 and isinstance(cand[0], str):
            return {
                "id": None,
                "src": str(cand[0]),
                "start": float(cand[1]),
                "end": float(cand[2]),
                "expected": default_expected,
                "heard": str(cand[3]).strip() if len(cand) >= 4 and cand[3] else "",
                "uncertain": False,
                "wrong_voice": None,
                "voice_ok": None,
                "voice_fail": None,
                "clip_voice_hint": None,
            }
    if isinstance(cand, str):
        parsed = _parse_line_map_candidates(f"L0 [{default_expected}]\n  {cand.strip()}")
        if parsed and parsed[0][2]:
            entry = parsed[0][2][0]
            entry["expected"] = default_expected
            return entry
    return {
        "id": None,
        "src": "",
        "start": 0.0,
        "end": 0.0,
        "expected": default_expected,
        "heard": "",
        "uncertain": False,
        "wrong_voice": None,
        "voice_ok": None,
        "voice_fail": None,
        "clip_voice_hint": None,
    }


def _lookup_clip_diag(clips_map: Mapping[Any, Any], src: str, cand_id: Any = None) -> Any:
    """Match a candidate's clip entry in `voice_diag` by id, full path, filename, or stem."""
    keys_to_try: List[Any] = []
    if cand_id is not None:
        keys_to_try.extend([cand_id, str(cand_id)])
    if src:
        p = Path(src)
        keys_to_try.extend([src, p.name, p.stem])
    for k in keys_to_try:
        if k in clips_map:
            return clips_map[k]
    if src:
        target_name = Path(src).name.lower()
        target_stem = Path(src).stem.lower()
        for k, v in clips_map.items():
            ks = Path(str(k)).name.lower()
            if ks == target_name or Path(str(k)).stem.lower() == target_stem:
                return v
    return None


def _hint_fails_speaker(hint: Mapping[str, Any], a: float, b: float, expected: str) -> Optional[bool]:
    """Evaluate a `clip_voice_hint` dict against `[a, b]` and `expected` speaker."""
    if not expected or " / " in expected:
        return False
    spans = hint.get("spans") or []
    ov = [
        (str(heard), bool(dis))
        for item in spans
        if isinstance(item, (list, tuple)) and len(item) >= 4
        for ws0, ws1, heard, dis in [item[:4]]
        if heard and min(float(ws1), b) > max(float(ws0), a) - 0.02
    ]
    dom_spk = str(hint.get("speaker") or "").strip()
    if ov:
        cand_voice = Counter(h for h, _ in ov).most_common(1)[0][0].strip()
        has_dis = any(d for _, d in ov)
    else:
        cand_voice = dom_spk
        has_dis = False
    if not cand_voice:
        return None
    dur = b - a
    if has_dis and dur < 2.5 and dom_spk.lower() == expected.lower():
        return False
    return cand_voice.lower() != expected.lower()


def _candidate_fails_voice(cand: Dict[str, Any], line_id: str, voice_diag: Any) -> bool:
    """Return True iff `cand` deterministically fails speaker verification."""
    # If voice_diag is an analyze() result reporting ok=False, voices could not be separated.
    if isinstance(voice_diag, Mapping) and "ok" in voice_diag and not voice_diag.get("ok"):
        return False

    if cand.get("voice_ok") is not None:
        return not bool(cand["voice_ok"])
    if cand.get("wrong_voice") is not None:
        return bool(cand["wrong_voice"])
    if cand.get("voice_fail") is not None:
        return bool(cand["voice_fail"])

    expected = str(cand.get("expected") or "").strip()
    if " / " in expected:
        return False

    heard = str(cand.get("heard") or "").rstrip("?").strip()
    if heard and expected:
        if cand.get("uncertain") and heard.lower() == expected.lower():
            return False
        return heard.lower() != expected.lower()

    a, b = float(cand.get("start") or 0.0), float(cand.get("end") or 0.0)

    vh = cand.get("clip_voice_hint")
    if isinstance(vh, Mapping) and expected:
        verdict = _hint_fails_speaker(vh, a, b, expected)
        if verdict is not None:
            return verdict

    if voice_diag is None:
        return False

    if isinstance(voice_diag, Mapping):
        clips_map = voice_diag.get("clips") if isinstance(voice_diag.get("clips"), Mapping) else voice_diag
        entry = _lookup_clip_diag(clips_map, str(cand.get("src") or ""), cand.get("id"))
        if entry is None and line_id in clips_map:
            entry = clips_map[line_id]

        if isinstance(entry, bool):
            return entry
        if isinstance(entry, Mapping):
            # Format 1: speaker_check.analyze() clip entry {"spans": [{"start", "end", "expected", "heard"}, ...]}
            spans = entry.get("spans")
            if isinstance(spans, list) and (not spans or isinstance(spans[0], Mapping)):
                for sp in spans:
                    s0 = float(sp.get("start") or 0.0)
                    s1_raw = sp.get("end")
                    s1 = float("inf") if s1_raw is None else float(s1_raw)
                    if b > a and min(s1, b) <= max(s0, a) + 0.02:
                        continue
                    sp_heard = str(sp.get("heard") or "").strip()
                    sp_exp = expected or str(sp.get("expected") or "").strip()
                    if sp_heard and sp_exp and sp_heard.lower() != sp_exp.lower():
                        return True
                return False
            # Format 2: prepare.py clip_voice_hint {"speaker": ..., "spans": [(ws0, ws1, heard, dis), ...]}
            if "speaker" in entry or (isinstance(spans, list) and spans and isinstance(spans[0], (list, tuple))):
                verdict = _hint_fails_speaker(entry, a, b, expected)
                return bool(verdict)

    if isinstance(voice_diag, (list, tuple, set)):
        cid = str(cand.get("id") or "")
        src_name = Path(str(cand.get("src") or "")).name
        for item in voice_diag:
            if isinstance(item, str):
                if "[SHORT VOICE REVIEW]" in item:
                    continue
                if (cid and item.startswith(cid + " ")) or (line_id and re.match(rf"^{re.escape(line_id)}\b", item)):
                    if not src_name or src_name in item or cid:
                        return True

    return False


def all_candidates_wrong_voice(line_candidates: Any, voice_diag: Any = None) -> List[str]:
    """Return script line IDs ONLY when 100% of a line's available candidate takes fail speaker check.

    Lines with zero candidates (missing lines), lines without a single expected speaker, or lines
    where at least one candidate take passes (or is unmeasured/uncertain) are NEVER returned.

    Supported `line_candidates` inputs:
      - `dict`: `{line_id: [cand, ...]}` or `{line_id: {"speaker": ..., "candidates": [...]}}`
      - `list`: `[{"id": "L1", "speaker": "Man", "candidates": [...]}, ...]`
      - `str`:  `line_map.md` text containing `L<n> [<Speaker>]` headers and `CAND ...` lines
    """
    if not line_candidates:
        return []
    if isinstance(voice_diag, Mapping) and "ok" in voice_diag and not voice_diag.get("ok"):
        return []

    normalized_lines: List[Tuple[str, str, List[Any]]] = []
    if isinstance(line_candidates, str):
        for lid, spk, cands in _parse_line_map_candidates(line_candidates):
            normalized_lines.append((lid, spk, cands))
    elif isinstance(line_candidates, Mapping):
        for lid, val in line_candidates.items():
            line_id = str(lid).strip()
            if isinstance(val, Mapping):
                spk = str(val.get("speaker") or val.get("expected") or "").strip()
                cands_raw = val.get("candidates") or val.get("takes") or val.get("cands") or []
                normalized_lines.append((line_id, spk, list(cands_raw)))
            elif isinstance(val, (list, tuple)):
                normalized_lines.append((line_id, "", list(val)))
    elif isinstance(line_candidates, Sequence):
        for entry in line_candidates:
            if isinstance(entry, Mapping):
                lid = str(entry.get("id") or entry.get("line_id") or "").strip()
                spk = str(entry.get("speaker") or entry.get("expected") or "").strip()
                cands_raw = entry.get("candidates") or entry.get("takes") or entry.get("cands") or []
                if lid:
                    normalized_lines.append((lid, spk, list(cands_raw)))

    failing_lines: List[str] = []
    for line_id, line_spk, raw_cands in normalized_lines:
        if not line_id or not raw_cands:
            continue
        if " / " in line_spk:
            continue
        norm_cands = [_normalize_candidate_entry(c, default_expected=line_spk) for c in raw_cands]
        if norm_cands and all(_candidate_fails_voice(c, line_id, voice_diag) for c in norm_cands):
            failing_lines.append(line_id)

    return failing_lines
