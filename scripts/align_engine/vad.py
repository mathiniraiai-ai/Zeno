"""Voice activity detection over background noise using Silero VAD (MIT license).

Energy-based onset verification only detects silence when ambient noise drops between words.
In noisy recordings (street traffic, fans, outdoor filming), the RMS floor stays elevated
throughout the clip. Silero VAD identifies actual human speech spans on CPU in milliseconds.

All functions degrade gracefully: if `silero_vad` is unavailable or raises an error,
each helper returns an empty list and callers fall back to energy-only silence bounds.
"""

from __future__ import annotations

from typing import List, Tuple

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
            sampling_rate=sample_rate,
            return_seconds=True,
            min_silence_duration_ms=120,
            speech_pad_ms=30,
        )
        return [(round(float(t["start"]), 3), round(float(t["end"]), 3)) for t in timestamps]
    except Exception:
        return []


def nonspeech_runs(
    spans: List[Tuple[float, float]],
    total_sec: float,
    min_run: float = 0.30,
    shrink: float = 0.06,
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
