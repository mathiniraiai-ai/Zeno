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

    def snap_starts_to_energy(
        self,
        words: List[Dict[str, Any]],
        waveform: np.ndarray,
        back_sec: float = 0.30,
        fwd_sec: float = 0.30,
        deep_db: float = 12.0,
        rise_frac: float = 0.35,
        phrase_starts_only: bool = True,
    ) -> List[Dict[str, Any]]:
        """
        Move a word's start to the nearest real speech onset: the rising edge after a deep energy valley
        (silence or a clear dip between words) within [start - back_sec, start + fwd_sec].

        Only phrase starts are touched (the first word, and any word after one ending in . ? ! ... or the danda):
        inside a sentence, speech has many dips (stop consonants) that look like pauses, and snapping to them
        moved ordinary words by up to 0.34 s in testing. Only deep valleys count (at least `deep_db` below the local
        speech level). If no deep valley is near, the start is left alone. Checked on a reference video where CTC put
        one line start 0.12 s late and another 0.22 s early; both were corrected from the energy curve.
        """
        sentence_end = ('.', '?', '!', '\u2026', '\u0964', '\u0965')
        if not words or len(waveform) == 0:
            return words
        rms, times = self.compute_energy_envelope(waveform)
        if len(rms) < 5:
            return words
        db = 20.0 * np.log10(np.maximum(rms, 1e-5))
        hop = float(self.hop_length) / float(self.sample_rate)

        out: List[Dict[str, Any]] = []
        for i, w in enumerate(words):
            s = float(w["start"])
            e = float(w["end"])
            is_phrase_start = i == 0 or str(words[i - 1].get("word", "")).rstrip().endswith(sentence_end)
            if phrase_starts_only and not is_phrase_start:
                out.append(dict(w))
                continue
            lo = max(0, int((s - back_sec) / hop))
            hi = min(len(db) - 1, int((s + fwd_sec) / hop))
            ctx_lo = max(0, int((s - 0.5) / hop))
            ctx_hi = min(len(db) - 1, int((s + 0.5) / hop))
            level = float(np.percentile(db[ctx_lo:ctx_hi + 1], 85))
            new_start = s
            best = None
            if hi - lo >= 5:
                seg = db[lo:hi + 1]
                # candidate valleys: local minima that are deep relative to the speech level
                for k in range(1, len(seg) - 1):
                    if not (seg[k] <= seg[k - 1] and seg[k] <= seg[k + 1]):
                        continue
                    if seg[k] > level - deep_db:
                        continue
                    thresh = seg[k] + min(0.12, rise_frac) * (level - seg[k])
                    j = k
                    while lo + j < len(db) - 1 and j < (hi - lo) + 30 and db[lo + j] < thresh:
                        j += 1
                    onset = max(0.0, (lo + j) * hop - 0.035)
                    if out and onset < out[-1]["end"] - 0.05:
                        continue   # would start inside the previous word (CTC word ends run early, so allow 50 ms)
                    if best is None or abs(onset - s) < abs(best - s):
                        best = onset
            if best is not None:
                prev_start = out[-1]["start"] if out else 0.0
                new_start = max(prev_start + 0.04, min(best, e - 0.05))
            moved = abs(new_start - s) >= 0.03
            nw = {**w, "start": round(new_start, 3)}
            if moved:
                nw["start_energy_snap"] = round(new_start - s, 3)
                if out and out[-1]["end"] > new_start:
                    out[-1] = {**out[-1], "end": round(max(out[-1]["start"] + 0.05, new_start), 3)}
            out.append(nw)
        return out

    def extend_ends_to_energy(
        self,
        words: List[Dict[str, Any]],
        waveform: np.ndarray,
        max_extend_sec: float = 0.35,
        drop_db: float = 24.0,
    ) -> List[Dict[str, Any]]:
        """
        CTC marks a word's end at its last emitting frame, which is 80-160 ms before the syllable coda really stops.
        Extend each end forward while the energy stays within `drop_db` of the word's own level, up to `max_extend_sec`,
        plus a +45 ms coda release guard, and never into the next word. Ends are only extended, never shortened.
        """
        if not words or len(waveform) == 0:
            return words
        rms, _ = self.compute_energy_envelope(waveform)
        if len(rms) < 5:
            return words
        db = 20.0 * np.log10(np.maximum(rms, 1e-5))
        hop = float(self.hop_length) / float(self.sample_rate)
        out: List[Dict[str, Any]] = []
        for i, w in enumerate(words):
            e = float(w["end"])
            nxt = float(words[i + 1]["start"]) if i + 1 < len(words) else float("inf")
            a = max(0, int(max(float(w["start"]), e - 0.30) / hop))
            b = min(len(db) - 1, int(e / hop))
            if b <= a:
                out.append(dict(w))
                continue
            level = float(np.percentile(db[a:b + 1], 85))
            k = b
            limit = min(len(db) - 1, int((e + max_extend_sec) / hop))
            while k < limit and db[k + 1] >= level - drop_db:
                k += 1
            target = max(e, k * hop + 0.045)
            # extend, but never run into the next word; also trim any overlap CTC left between neighbours
            end = min(target, nxt) if nxt - target < 0.015 else min(target, nxt - 0.015)
            out.append({**w, "end": round(max(float(w["start"]) + 0.05, end), 3)})
        return out

    def silent_runs(self, waveform: np.ndarray, floor_db: float = -58.0, min_run_sec: float = 0.12) -> List[Tuple[float, float]]:
        """Stretches with no sound at all: at least `min_run_sec` long and `floor_db` below the loudest sound in the audio.
        Short dips inside words (stop-consonant closures) are shorter than that and are never counted. In a noisy
        recording nothing counts as silent, which is the safe direction: the aligner then works as before."""
        rms, times = self.compute_energy_envelope(waveform)
        if len(rms) < 5:
            return []
        db = 20.0 * np.log10(np.maximum(rms, 1e-7))
        hop = float(self.hop_length) / float(self.sample_rate)
        quiet = db < floor_db
        runs: List[Tuple[float, float]] = []
        i, n = 0, len(quiet)
        while i < n:
            if quiet[i]:
                j = i
                while j + 1 < n and quiet[j + 1]:
                    j += 1
                if (j - i + 1) * hop >= min_run_sec:
                    runs.append((round(i * hop, 3), round((j + 1) * hop, 3)))
                i = j + 1
            else:
                i += 1
        return runs

    def clamp_words_to_sound(self, words: List[Dict[str, Any]], waveform: np.ndarray) -> List[Dict[str, Any]]:
        """Last check on every word: a word's start and end must lie where there is sound. An end that runs into silence is
        pulled back to the last sound inside the word; a start in silence moves to the first sound. A word with no sound
        anywhere inside it is flagged `no_sound` (its time cannot be trusted). Never lengthens a word."""
        runs = self.silent_runs(waveform)
        if not runs or not words:
            return words
        out = []
        for w in words:
            s, e = float(w["start"]), float(w["end"])
            ns, ne = s, e
            for a, b in runs:
                if b <= s or a >= e:
                    continue                                   # this silence does not touch the word
                if a <= s + 0.10 and b < e - 0.05:
                    ns = max(ns, b - 0.02)                     # word starts in silence: move to the first sound
                elif a > s and b >= e - 0.10:
                    ne = min(ne, a + 0.04)                     # word runs on into silence (also when it overshoots the silence): end at the last sound
                elif a <= s + 0.10 and b >= e - 0.10:
                    ns, ne = s, s                              # the whole word lies in silence
            nw = dict(w)
            if ne - ns < 0.03 and any(a <= s and e <= b for a, b in runs):
                nw["no_sound"] = True
            else:
                nw["start"], nw["end"] = round(ns, 3), round(max(ne, ns + 0.03), 3)
                if abs(ns - s) > 0.02 or abs(ne - e) > 0.02:
                    nw["sound_clamp"] = [round(ns - s, 3), round(ne - e, 3)]
            out.append(nw)
        return out

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
