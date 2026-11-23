"""Voice activity detection over background noise using Silero VAD (MIT license).

Energy-based onset verification only detects silence when ambient noise drops between words.
In noisy recordings (street traffic, fans, outdoor filming), the RMS floor stays elevated
throughout the clip. Silero VAD identifies actual human speech spans on CPU in milliseconds.

All functions degrade gracefully: if `silero_vad` is unavailable or raises an error,
each helper returns an empty list and callers fall back to energy-only silence bounds.
"""

from __future__ import annotations

from typing import Any, Dict, List, Tuple

import numpy as np

_MODEL = None
_MODEL_FAILED = False


def _model():
    global _MODEL, _MODEL_FAILED
    if _MODEL is not None or _MODEL_FAILED:
        return _MODEL
    try:
        from silero_vad import load_silero_vad

        _MODEL = load_silero_vad(onnx=False)
    except Exception:
        _MODEL_FAILED = True
    return _MODEL


def available() -> bool:
    """Return True if the Silero VAD model loaded successfully."""
    return _model() is not None


def speech_spans(waveform: np.ndarray, sample_rate: int = 16000) -> List[Tuple[float, float]]:
    """Return `(start_sec, end_sec)` intervals containing human speech, or `[]` if unavailable."""
    model = _model()
    if model is None or waveform is None or len(waveform) < sample_rate // 4:
        return []
    try:
        import torch
        from silero_vad import get_speech_timestamps

        timestamps = get_speech_timestamps(
            torch.from_numpy(np.asarray(waveform, dtype=np.float32)),
            model,
            threshold=0.35,
            sampling_rate=sample_rate,
            return_seconds=True,
            min_silence_duration_ms=260,
            speech_pad_ms=80,
        )
        return [(round(float(t["start"]), 3), round(float(t["end"]), 3)) for t in timestamps]
    except Exception:
        return []


def nonspeech_runs(
    spans: List[Tuple[float, float]],
    total_sec: float,
    min_run: float = 0.40,
    shrink: float = 0.14,
) -> List[Tuple[float, float]]:
    """Return non-speech intervals between `spans`, inset by `shrink` seconds on each side.

    Insetting prevents words that begin or end right on a VAD boundary from being masked out.
    Only gaps of at least `min_run` seconds (and at least 0.12 s after shrinking) are returned.
    """
    if not spans:
        return []
    out: List[Tuple[float, float]] = []
    prev = 0.0
    for start, end in list(spans) + [(total_sec, total_sec)]:
        if start - prev >= min_run:
            lo, hi = prev + shrink, start - shrink
            if hi - lo >= 0.12:
                out.append((round(lo, 3), round(hi, 3)))
        prev = max(prev, end)
    return out


def merge_runs(*groups: List[Tuple[float, float]]) -> List[Tuple[float, float]]:
    """Return the sorted union of one or more lists of `(start_sec, end_sec)` intervals."""
    runs = sorted((a, b) for g in groups for a, b in g)
    merged: List[List[float]] = []
    for a, b in runs:
        if merged and a <= merged[-1][1] + 1e-6:
            merged[-1][1] = max(merged[-1][1], b)
        else:
            merged.append([a, b])
    return [(round(a, 3), round(b, 3)) for a, b in merged]


def _uniform_windows(
    lo: float,
    hi: float,
    target_chunk_sec: float,
    max_chunk_sec: float,
) -> List[Tuple[float, float]]:
    """Split `[lo, hi]` into overlapping uniform windows bounded by `max_chunk_sec`."""
    span = hi - lo
    if span <= 1e-4:
        return []
    if span <= max_chunk_sec:
        return [(round(lo, 3), round(hi, 3))]
    step = max(1.0, float(target_chunk_sec))
    overlap = min(0.8, 0.25 * step)
    out: List[Tuple[float, float]] = []
    cur = lo
    while cur < hi - 1e-6:
        if hi - cur <= max_chunk_sec:
            out.append((round(cur, 3), round(hi, 3)))
            break
        end = min(hi, cur + step + overlap)
        out.append((round(cur, 3), round(end, 3)))
        if end >= hi - 1e-6:
            break
        cur += step
    return out


def plan_speech_chunks(
    spans: List[Tuple[float, float]],
    total_sec: float,
    target_chunk_sec: float = 16.0,
    max_chunk_sec: float = 22.0,
    min_pause_split: float = 0.35,
    pad_sec: float = 0.25,
) -> List[Tuple[float, float]]:
    """Group Silero speech `spans` into pause-aligned chunks with midpoint-clamped padding."""
    total = float(total_sec or 0.0)
    if total <= 0.0:
        return []

    clean: List[Tuple[float, float]] = []
    for item in spans or []:
        try:
            s = max(0.0, float(item[0]))
            e = min(total, float(item[1]))
        except (TypeError, ValueError, IndexError):
            continue
        if e > s + 1e-4:
            if clean and s <= clean[-1][1] + 1e-6:
                clean[-1] = (clean[-1][0], max(clean[-1][1], e))
            else:
                clean.append((s, e))

    if not clean:
        return _uniform_windows(0.0, total, target_chunk_sec, max_chunk_sec)

    groups: List[List[Tuple[float, float]]] = []
    cur: List[Tuple[float, float]] = [clean[0]]
    for nxt in clean[1:]:
        raw_start = cur[0][0]
        prev_end = cur[-1][1]
        nxt_start, nxt_end = nxt
        gap = nxt_start - prev_end
        projected = nxt_end - raw_start

        if projected > target_chunk_sec and gap >= min_pause_split:
            groups.append(cur)
            cur = [nxt]
        elif projected > max_chunk_sec:
            if gap < min_pause_split and (prev_end - raw_start) >= 8.0 and len(cur) >= 2:
                candidates = [i for i in range(len(cur) - 1) if (cur[i][1] - raw_start) >= 4.0]
                if not candidates:
                    candidates = list(range(len(cur) - 1))
                best_i = max(candidates, key=lambda i: (round(cur[i + 1][0] - cur[i][1], 4), i))
                best_gap = cur[best_i + 1][0] - cur[best_i][1]
                if best_gap > gap and (nxt_end - cur[best_i + 1][0]) <= max_chunk_sec:
                    groups.append(cur[: best_i + 1])
                    cur = cur[best_i + 1 :] + [nxt]
                else:
                    groups.append(cur)
                    cur = [nxt]
            else:
                groups.append(cur)
                cur = [nxt]
        else:
            cur.append(nxt)
    if cur:
        groups.append(cur)

    chunks: List[Tuple[float, float]] = []
    for idx, grp in enumerate(groups):
        raw_s = grp[0][0]
        raw_e = grp[-1][1]
        prev_mid = 0.5 * (groups[idx - 1][-1][1] + raw_s) if idx > 0 else 0.0
        next_mid = 0.5 * (raw_e + groups[idx + 1][0][0]) if idx + 1 < len(groups) else total
        cs = max(0.0, prev_mid, raw_s - pad_sec)
        ce = min(total, next_mid, raw_e + pad_sec)
        if ce <= cs + 1e-4:
            continue
        if (raw_e - raw_s) > max_chunk_sec + 1.0:
            chunks.extend(_uniform_windows(cs, ce, target_chunk_sec, max_chunk_sec))
        else:
            chunks.append((round(cs, 3), round(ce, 3)))
    return chunks


def uncovered_speech_spans(
    spans: List[Tuple[float, float]],
    words: List[Dict[str, Any]],
    min_gap_sec: float = 1.2,
    word_pad_sec: float = 0.45,
) -> List[Tuple[float, float]]:
    """Return sorted merged speech intervals `>= min_gap_sec` not covered by padded `words`."""
    if not spans:
        return []
    clean_spans = merge_runs([
        (float(s), float(e))
        for s, e in spans
        if float(e) > float(s) + 1e-4
    ])
    if not clean_spans:
        return []

    pad = max(0.0, float(word_pad_sec))
    covered_raw: List[Tuple[float, float]] = []
    for w in words or []:
        if not isinstance(w, dict):
            continue
        ws_raw = w.get("start", w.get("startTime"))
        we_raw = w.get("end", w.get("endTime"))
        if ws_raw is None or we_raw is None:
            continue
        try:
            ws = float(ws_raw)
            we = float(we_raw)
        except (TypeError, ValueError):
            continue
        if we < ws:
            ws, we = we, ws
        covered_raw.append((max(0.0, ws - pad), we + pad))

    covered = merge_runs(covered_raw)
    residuals: List[Tuple[float, float]] = []
    for s, e in clean_spans:
        cur = s
        for cs, ce in covered:
            if ce <= cur:
                continue
            if cs >= e:
                break
            if cs > cur + 1e-4:
                residuals.append((cur, min(e, cs)))
            cur = max(cur, ce)
            if cur >= e - 1e-4:
                break
        if cur < e - 1e-4:
            residuals.append((cur, e))

    merged = merge_runs(residuals)
    return [(a, b) for a, b in merged if round(b - a, 3) >= float(min_gap_sec)]
