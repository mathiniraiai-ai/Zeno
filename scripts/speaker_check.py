"""Wrong-speaker detector: finds dialogue spoken by the wrong character and returns the spans to cut.

The script says who speaks each line (for example "Man:" and "Robot:"). Zeno measures each word's voice (pitch,
timbre, buzziness) and learns what each character sounds like in THIS job from the script itself: the median voice of
the words every character was supposed to say. Words whose voice is closer to a different character than the one the
script names are wrong-speaker words. Runs of them become cut spans; the good parts around them are joined.

Honest limits: it needs two or more named speakers in the script and voices that really differ (a man and a robot
do; two similar men may not). If the voices can't be told apart it says so and cuts nothing.
"""
from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np

SR = 16000
MIN_UNIT_SEC = 0.38          # catch 2-word wrong-speaker clauses (~0.4s+) while remaining steady
MIN_UNITS_PER_SPEAKER = 2
MIN_DPRIME = 2.0             # how far apart two voices must be (in units of spread) before we dare to cut
LONG_PAUSE = 0.38            # a pause >=0.38s starts a new phrase unit so mid-clip speaker swaps are isolated
MARGIN = 0.30                # a turn counts as wrong only if it is clearly closer to the other voice
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
    """Split a clip's words into turns: a new turn starts when the script's speaker changes (or after a very long pause).
    A whole turn is measured at once, which is far steadier than short phrases or single words."""
    units: List[Tuple[int, int]] = []
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
    """Voice vector for one stretch of speech: pitch level, pitch movement (robots are monotone), timbre, buzziness."""
    import librosa

    if t1 - t0 < MIN_UNIT_SEC:
        return None
    seg = y[max(0, int(t0 * SR)): min(len(y), int(t1 * SR))]
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
        act = rms > 0.4 * np.median(rms)       # speech frames only, so pauses do not blur the voice
        mf = librosa.feature.mfcc(y=seg, sr=SR, n_mfcc=13, n_fft=512, hop_length=hop)[1:9, :n][:, act[: min(n, 10 ** 9)]]
        cen_f = librosa.feature.spectral_centroid(y=seg, sr=SR, n_fft=512, hop_length=hop)[0][:n][act]
        flat_f = librosa.feature.spectral_flatness(y=seg, n_fft=512, hop_length=hop)[0][:n][act]
        if mf.shape[1] < 8:
            return None
        cen = float(np.log(cen_f.mean() + 1.0))
        flat = float(flat_f.mean())
    except Exception:
        return None
    # six steady measurements: with few phrases to learn from, more dimensions only add noise
    return np.array([float(np.median(lf)), float(lf.std()), float(mf[0].mean()), float(mf[1].mean()), cen, flat * 10.0])


def _reference_files(speaker: str) -> List[Path]:
    """Where a reference sample for this character may live: input/voices/<Speaker>.(wav|mp3|m4a)."""
    root = Path(__file__).resolve().parent.parent / "input" / "voices"
    name = speaker.strip().lower()
    out: List[Path] = []
    if root.is_dir():
        for q in sorted(root.iterdir()):
            if q.stem.strip().lower() == name and q.suffix.lower() in (".wav", ".flac", ".ogg"):
                out.append(q)
    return out


def _quietest(rms_env: Sequence[Tuple[float, float]], lo: float, hi: float) -> float:
    """Time of the lowest-energy frame between lo and hi (the cleanest place to cut)."""
    if hi < lo:
        lo, hi = hi, lo
    cands = [(t, db) for t, db in rms_env if lo <= t <= hi]
    if not cands:
        return round((lo + hi) / 2, 2)
    return round(min(cands, key=lambda x: x[1])[0], 2)


def analyze(items: List[Dict[str, Any]]) -> Dict[str, Any]:
    """items: [{"id", "wav": Path, "words": [{"w","start","end"}], "expected": [speaker or None per word], "rms_env"}].
    Returns {"ok", "reason", "speakers", "dprime", "agreement", "clips": {id: {"spans": [...]}}}."""
    speakers = sorted({sp for it in items for sp in it["expected"] if sp})
    result: Dict[str, Any] = {"ok": False, "reason": "", "speakers": speakers, "dprime": 0.0, "agreement": 0.0, "clips": {}}
    if len(speakers) < 2:
        result["reason"] = "script names fewer than 2 speakers"
        return result

    rows: List[Tuple[int, int, np.ndarray, str]] = []   # (item idx, unit idx, features, expected speaker)
    all_units: Dict[int, List[Tuple[int, int]]] = {}
    for k, it in enumerate(items):
        wav = Path(it["wav"])
        if not wav.is_file() or not it["words"]:
            continue
        y = _load_audio(wav)
        units = build_units(it["words"], it["expected"])
        all_units[k] = units
        for ui, (a, b) in enumerate(units):
            exp = Counter(sp for sp in it["expected"][a:b + 1] if sp).most_common(1)
            if not exp:
                continue
            f = unit_features(y, float(it["words"][a]["start"]), float(it["words"][b]["end"]))
            if f is not None:
                rows.append((k, ui, f, exp[0][0]))
    # Optional reference voices: input/voices/<Speaker>.wav (about 10 s of that character). They settle which voice is
    # which even when there are only a few clips, and they cannot be fooled by a wrong-voice line in the footage.
    refs_used: List[str] = []
    for sp in speakers:
        ref = next((q for q in _reference_files(sp) if q.is_file()), None)
        if ref is None:
            continue
        try:
            y_ref = _load_audio(ref)
            hop_sec = 3.0
            for t in np.arange(0.0, max(len(y_ref) / SR - 2.0, 0.1), hop_sec):   # 3-second windows of the sample
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

    # Learn each character's voice from the script labels using medians (robust to a few wrong lines), then refine
    # using only the phrases whose voice agrees with the script.
    cent = {s: np.median(Z[exp == s], axis=0) for s in counts0}
    for _ in range(8):
        names = list(cent)
        D = np.stack([np.linalg.norm(Z - cent[s], axis=1) for s in names], axis=1)
        pred = np.array(names)[D.argmin(axis=1)]
        trusted = pred == exp
        new = {s: (Z[trusted & (exp == s)].mean(axis=0) if (trusted & (exp == s)).sum() >= 2 else cent[s]) for s in names}
        if all(np.allclose(new[s], cent[s]) for s in names):
            cent = new
            break
        cent = new
    names = list(cent)

    D = np.stack([np.linalg.norm(Z - cent[s], axis=1) for s in names], axis=1)
    pred = np.array(names)[D.argmin(axis=1)]
    trusted = pred == exp
    real = np.array([r[0] >= 0 for r in rows])
    result["agreement"] = round(float(trusted[real].mean()), 3)

    # How well apart are the voices? Project the trusted phrases on the line between two voices (d-prime).
    dps = []
    for i, a in enumerate(names):
        for b in names[i + 1:]:
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

    # Which phrases are clearly the other character?
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
            spans.append({"first": first, "last": last, "start": start, "end": end, "expected": expected, "heard": heard,
                          "text": " ".join(str(words[x]["w"]) for x in range(first, last + 1))})
            ui += 1
        result["clips"][it["id"]] = {"spans": spans}
    return result
