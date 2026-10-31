#!/usr/bin/env python3
"""Generate assets/sfx/whoosh.wav: an original synthesized transition whoosh (no third-party sound, no licence to check).

    python3 scripts/make_whoosh.py [--out assets/sfx/whoosh.wav] [--seconds 0.70]

Recipe: white noise through a band-pass filter whose centre sweeps up and then down (the airy "whoosh"), plus a
low-passed body that swells a little earlier (the weight), shaped by a smooth attack/release envelope and panned
left to right with a little stereo width. 48 kHz, 16-bit stereo, peak -1.5 dBFS.
"""
import argparse
import math
from pathlib import Path

import numpy as np

SR = 48000


def svf_bandpass(x: np.ndarray, freqs: np.ndarray, q: np.ndarray) -> np.ndarray:
    """Time-varying state-variable band-pass filter (Chamberlin form), sample by sample."""
    out = np.zeros_like(x)
    low = band = 0.0
    for i in range(len(x)):
        f = 2.0 * math.sin(math.pi * min(freqs[i], SR * 0.45) / SR)
        low += f * band
        high = x[i] - low - band / q[i]
        band += f * high
        out[i] = band
    return out


def lowpass(x: np.ndarray, freqs: np.ndarray) -> np.ndarray:
    out = np.zeros_like(x)
    low = band = 0.0
    for i in range(len(x)):
        f = 2.0 * math.sin(math.pi * min(freqs[i], SR * 0.45) / SR)
        low += f * band
        high = x[i] - low - band / 0.9
        band += f * high
        out[i] = low
    return out


def smoothstep(t: np.ndarray) -> np.ndarray:
    t = np.clip(t, 0.0, 1.0)
    return t * t * (3 - 2 * t)


def make(seconds: float = 0.70, seed: int = 7) -> np.ndarray:
    n = int(seconds * SR)
    t = np.linspace(0.0, 1.0, n)
    rng = np.random.default_rng(seed)
    peak_at = 0.27   # the swell peaks about 0.19 s in, so it lands right after the cut it is placed on

    # envelope: smooth swell to the peak, then a longer release; soft edges so nothing clicks
    rise = smoothstep(t / peak_at) ** 1.4
    fall = np.exp(-4.2 * np.clip(t - peak_at, 0, None) / (1 - peak_at))
    env = np.where(t < peak_at, rise, fall)
    env *= smoothstep(t / 0.03) * smoothstep((1 - t) / 0.12)

    # air: band-pass centre sweeps 500 Hz -> 5.5 kHz at the peak -> 1.8 kHz at the end
    up = 500 + (5500 - 500) * smoothstep(t / peak_at) ** 1.6
    down = 5500 - (5500 - 1800) * smoothstep((t - peak_at) / (1 - peak_at))
    centre = np.where(t < peak_at, up, down)
    q = 1.1 + 1.4 * np.sin(np.pi * t)
    chans = []
    for _ in range(2):   # two decorrelated noise sources give natural stereo width
        air = svf_bandpass(rng.standard_normal(n), centre, q)
        air /= np.max(np.abs(air)) + 1e-9
        chans.append(air)

    # body: a soft low swell a touch before the peak
    body_env = smoothstep((t - 0.05) / (peak_at - 0.1)) * np.exp(-3.0 * np.clip(t - peak_at, 0, None) / (1 - peak_at))
    body = lowpass(rng.standard_normal(n), 220 + 700 * smoothstep(t / peak_at))
    body /= np.max(np.abs(body)) + 1e-9

    pan = np.clip(0.5 + 0.38 * (2 * smoothstep(t) - 1), 0.0, 1.0)   # pans left to right
    left_gain, right_gain = np.cos(pan * np.pi / 2), np.sin(pan * np.pi / 2)
    mono_mix = lambda a: a * env + 0.55 * body * body_env * env ** 0.5
    left = mono_mix(chans[0]) * (0.55 + 0.45 * left_gain)
    right = mono_mix(chans[1]) * (0.55 + 0.45 * right_gain)
    stereo = np.stack([left, right], axis=1)
    stereo /= np.max(np.abs(stereo)) + 1e-9
    stereo = np.tanh(2.4 * stereo) / np.tanh(2.4)                    # gentle compression: a fuller, punchier whoosh
    stereo *= 10 ** (-1.5 / 20) / (np.max(np.abs(stereo)) + 1e-9)    # peak -1.5 dBFS
    return stereo.astype(np.float32)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(Path(__file__).resolve().parent.parent / "assets" / "sfx" / "whoosh.wav"))
    ap.add_argument("--seconds", type=float, default=0.70)
    a = ap.parse_args()
    import soundfile as sf
    audio = make(a.seconds)
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    sf.write(a.out, audio, SR, subtype="PCM_16")
    print(f"wrote {a.out}: {len(audio) / SR:.2f}s stereo {SR} Hz, peak {20 * math.log10(float(np.max(np.abs(audio)))):.1f} dBFS")


if __name__ == "__main__":
    main()
