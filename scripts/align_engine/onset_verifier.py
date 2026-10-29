"""
FlowAlign-v1 — Acoustic Onset Verification & Boundary Correction Layer
Analyzes local waveform energy, spectral flux, and CTC emission curves to lock
the exact physical vocal onset time and prevent early-trigger artifacts.
"""

import numpy as np
from numpy.lib.stride_tricks import sliding_window_view
from typing import List, Dict, Any, Tuple


class AcousticOnsetVerifier:
    """
    Acoustic Onset Detector & Boundary Refiner.
    Verifies candidate word boundaries against the raw audio waveform.
    """
    def __init__(self, sample_rate: int = 16000, hop_length: int = 160):
        self.sample_rate = sample_rate
        self.hop_length = hop_length  # 160 samples = 10ms resolution for onset detection

    def compute_energy_envelope(self, waveform: np.ndarray) -> Tuple[np.ndarray, np.ndarray]:
        """Computes short-time root-mean-square (RMS) energy envelope using fast vectorized NumPy."""
        frame_length = 320  # 20ms window
        y = np.asarray(waveform, dtype=np.float32)
        if len(y) == 0:
            return np.zeros(1, dtype=np.float32), np.zeros(1, dtype=np.float32)
        pad = frame_length // 2
        y_padded = np.pad(y, (pad, pad), mode="constant")
        if len(y_padded) < frame_length:
            rms = np.array([ float(np.sqrt(np.mean(y_padded ** 2))) ], dtype=np.float32)
        else:
            windows = sliding_window_view(y_padded, window_shape=frame_length)[::self.hop_length]
            rms = np.sqrt(np.mean(windows ** 2, axis=-1))
        max_rms = float(np.max(rms)) if len(rms) > 0 and np.max(rms) > 0 else 1.0
        norm_rms = rms / max_rms
        times = np.arange(len(rms), dtype=np.float64) * (float(self.hop_length) / float(self.sample_rate))
        return norm_rms, times

    def compute_spectral_onset(self, waveform: np.ndarray) -> Tuple[np.ndarray, np.ndarray]:
        """Computes fast first-order positive energy flux / onset strength envelope."""
        norm_rms, times = self.compute_energy_envelope(waveform)
        flux = np.maximum(0.0, np.diff(norm_rms, prepend=norm_rms[:1]))
        max_onset = float(np.max(flux)) if len(flux) > 0 and np.max(flux) > 0 else 1.0
        norm_onset = flux / max_onset
        return norm_onset, times

    def refine_word_onsets(
        self,
        words: List[Dict[str, Any]],
        waveform: np.ndarray,
        duration_sec: float
    ) -> List[Dict[str, Any]]:
        """
        Refines each candidate word's start timestamp by detecting the true acoustic onset.
        
        Rules:
          1. Examine search window: [candidate_start - 0.10s, candidate_start + 0.35s]
          2. Locate the steepest rise in vocal energy / spectral flux.
          3. If the candidate start falls in quiet silence before vocal attack, 
             snap the onset forward to the rising edge.
        """
        if not words or len(waveform) == 0:
            return words

        rms_env, rms_times = self.compute_energy_envelope(waveform)
        onset_env, onset_times = self.compute_spectral_onset(waveform)

        refined_words: List[Dict[str, Any]] = []

        for i, w in enumerate(words):
            raw_start = w["start"]
            raw_end = w["end"]
            
            # If word is at timeline origin or invalid, pass through
            if raw_start < 0.05:
                refined_words.append({
                    **w,
                    "onset_confidence": 0.95
                })
                continue

            # Define local search window around candidate start
            window_min = max(0.0, raw_start - 0.08)
            window_max = min(duration_sec, raw_start + 0.35)

            # Find frames in window
            mask = (rms_times >= window_min) & (rms_times <= window_max)
            win_times = rms_times[mask]
            win_rms = rms_env[mask]
            win_onset = onset_env[mask]

            if len(win_rms) < 3:
                refined_words.append({
                    **w,
                    "onset_confidence": 0.80
                })
                continue

            # Combined acoustic energy index: 0.6 * RMS + 0.4 * Spectral Flux
            combined_energy = 0.6 * win_rms + 0.4 * win_onset

            # Threshold for audible speech onset (relative to local background)
            bg_floor = np.percentile(win_rms, 20)
            voice_threshold = bg_floor + 0.15 * (np.max(win_rms) - bg_floor)

            # Find the first frame exceeding vocal threshold
            exceeding_indices = np.where(win_rms >= voice_threshold)[0]

            if len(exceeding_indices) > 0:
                onset_idx = exceeding_indices[0]
                detected_onset_sec = float(win_times[onset_idx])
                
                # Check energy gradient slope
                if onset_idx > 0:
                    gradient = win_rms[onset_idx] - win_rms[onset_idx - 1]
                    onset_conf = min(1.0, float(0.7 + gradient))
                else:
                    onset_conf = 0.85

                # Prevent pushing past end or dragging before previous word
                prev_start = refined_words[i - 1]["start"] if i > 0 else 0.0
                clamped_start = max(prev_start + 0.04, min(detected_onset_sec, raw_end - 0.05))
            else:
                clamped_start = raw_start
                onset_conf = 0.70

            refined_words.append({
                "word": w["word"],
                "start": round(clamped_start, 3),
                "end": round(raw_end, 3),
                "confidence": w.get("confidence", 0.90),
                "onset_confidence": round(onset_conf, 2),
                "raw_ctc_start": raw_start
            })

        return refined_words

    def refine_segment_boundaries(
        self,
        start_sec: float,
        end_sec: float,
        waveform: np.ndarray,
        duration_sec: float
    ) -> Tuple[float, float]:
        """
        Wave Model Acoustic Boundary Refiner for ScriptCut-AI segment cuts:
        Snaps both the start (vocal attack) and end (vocal decay) of a spoken take
        using 10ms RMS energy + spectral flux analysis so no phoneme or breath is clipped.
        """
        if len(waveform) == 0 or end_sec <= start_sec:
            return round(start_sec, 3), round(end_sec, 3)

        rms_env, rms_times = self.compute_energy_envelope(waveform)
        if len(rms_env) < 3:
            return round(start_sec, 3), round(end_sec, 3)

        # 1. Refine start (onset) in [start_sec - 0.12s, start_sec + 0.25s]
        s_min = max(0.0, start_sec - 0.12)
        s_max = min(duration_sec, start_sec + 0.25)
        s_mask = (rms_times >= s_min) & (rms_times <= s_max)
        s_rms = rms_env[s_mask]
        s_times = rms_times[s_mask]
        refined_start = start_sec
        if len(s_rms) >= 3:
            bg_floor = np.percentile(s_rms, 20)
            thresh = bg_floor + 0.15 * (np.max(s_rms) - bg_floor)
            idx = np.where(s_rms >= thresh)[0]
            if len(idx) > 0:
                refined_start = float(s_times[idx[0]])

        # 2. Refine end (offset/decay) in [end_sec - 0.20s, end_sec + 0.18s]
        e_min = max(refined_start + 0.08, end_sec - 0.20)
        e_max = min(duration_sec, end_sec + 0.18)
        e_mask = (rms_times >= e_min) & (rms_times <= e_max)
        e_rms = rms_env[e_mask]
        e_times = rms_times[e_mask]
        refined_end = end_sec
        if len(e_rms) >= 3:
            bg_floor = np.percentile(e_rms, 20)
            thresh = bg_floor + 0.12 * (np.max(e_rms) - bg_floor)
            idx = np.where(e_rms >= thresh)[0]
            if len(idx) > 0:
                refined_end = float(e_times[idx[-1]])

        refined_end = max(refined_start + 0.10, min(duration_sec, refined_end))
        return round(refined_start, 3), round(refined_end, 3)


Tuple_np = tuple
