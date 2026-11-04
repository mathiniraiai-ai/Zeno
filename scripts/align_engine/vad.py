"""Speech detection that works over background noise (Silero VAD, MIT licence, about 2 MB, runs on the CPU in milliseconds).

The energy method in onset_verifier only sees silence when the recording is quiet between words. In a noisy recording (a selfie filmed on
a scooter, a street, a fan) the whole clip is "loud" and it finds nothing. This finds where a person is actually talking.

Everything here is optional: if `silero_vad` is not installed or fails, every function returns an empty list and the callers behave
exactly as before.
"""
from __future__ import annotations

from typing import List, Optional, Tuple

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
    return _model() is not None


def speech_spans(waveform: np.ndarray, sample_rate: int = 16000) -> List[Tuple[float, float]]:
    """(start, end) seconds where someone speaks. [] when the detector is unavailable."""
    m = _model()
    if m is None or waveform is None or len(waveform) < sample_rate // 4:
        return []
    try:
        import torch
        from silero_vad import get_speech_timestamps

        ts = get_speech_timestamps(torch.from_numpy(np.asarray(waveform, dtype=np.float32)), m, sampling_rate=sample_rate,
                                   return_seconds=True, min_silence_duration_ms=120, speech_pad_ms=30)
        return [(round(float(t["start"]), 3), round(float(t["end"]), 3)) for t in ts]
    except Exception:
        return []


def nonspeech_runs(spans: List[Tuple[float, float]], total_sec: float, min_run: float = 0.30, shrink: float = 0.06) -> List[Tuple[float, float]]:
    """The stretches between speech spans, each pulled in by `shrink` on both sides so a word that starts or ends right at a detected
    edge is never forced out of its place. Only runs of at least `min_run` seconds count."""
    if not spans:
        return []
    out: List[Tuple[float, float]] = []
    prev = 0.0
    for a, b in list(spans) + [(total_sec, total_sec)]:
        if a - prev >= min_run:
            lo, hi = prev + shrink, a - shrink
            if hi - lo >= 0.12:
                out.append((round(lo, 3), round(hi, 3)))
        prev = max(prev, b)
    return out


def merge_runs(*groups: List[Tuple[float, float]]) -> List[Tuple[float, float]]:
    """Union of several lists of (start, end) runs."""
    runs = sorted((a, b) for g in groups for a, b in g)
    out: List[List[float]] = []
    for a, b in runs:
        if out and a <= out[-1][1] + 1e-6:
            out[-1][1] = max(out[-1][1], b)
        else:
            out.append([a, b])
    return [(round(a, 3), round(b, 3)) for a, b in out]
