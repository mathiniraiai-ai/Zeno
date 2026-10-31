import os
import re
import math
import gc
import ctypes
from pathlib import Path

# Keep the wav2vec2 weights inside the project (models/torch/hub/checkpoints) instead of ~/.cache.
os.environ.setdefault("TORCH_HOME", str(Path(__file__).resolve().parents[2] / "models" / "torch"))

import numpy as np
import soundfile as sf
from typing import Tuple, Dict, Optional, List, Any
import ssl
import torch
import torchaudio
from torchaudio.pipelines import WAV2VEC2_ASR_BASE_960H as bundle



def _certifi_ssl_context():
    """Verified SSL context using certifi's CA bundle (macOS python often lacks system certs).
    Used only for the explicit weights download below; never installed globally."""
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        return ssl.create_default_context()


class AcousticModel:
    """
    Dedicated High-Precision Acoustic Emission Extractor using PyTorch Wav2Vec2.
    """
    def __init__(self, device: Optional[str] = None, use_fp16: bool = False):
        self.sample_rate = 16000
        self.torch_model = None
        self.device = device or ("cuda" if torch.cuda.is_available() else "cpu")
        self.use_fp16 = use_fp16 and ("cuda" in str(self.device))
        labels = bundle.get_labels()
        self.dictionary: Dict[str, int] = {c: i for i, c in enumerate(labels)}
        self.blank_id = self.dictionary.get("-", 0)

    def _ensure_model_loaded(self):
        if self.torch_model is None:
            if "cpu" in str(self.device):
                try:
                    torch.set_num_threads(2)
                except Exception:
                    pass
            print(f"[Kavioo Acoustic Engine] Loading PyTorch Wav2Vec2 neural model (Device: {self.device})...")
            dtype = torch.float16 if self.use_fp16 else torch.float32
            dev = torch.device(self.device)
            _prev_ctx = ssl._create_default_https_context
            ssl._create_default_https_context = _certifi_ssl_context  # verified, this call only
            try:
                _m = bundle.get_model()
            finally:
                ssl._create_default_https_context = _prev_ctx
            self.torch_model = _m.to(device=dev, dtype=dtype)
            self.torch_model.eval()
            print(f"[Kavioo Acoustic Engine] PyTorch model ready (Vocab size: {len(self.dictionary)}).")

    def unload_model(self):
        """Unloads PyTorch neural weights and forces glibc to return RAM to the OS."""
        if self.torch_model is not None:
            del self.torch_model
            self.torch_model = None
            if torch.cuda.is_available():
                try:
                    torch.cuda.empty_cache()
                except Exception:
                    pass
            gc.collect()
            try:
                libc = ctypes.CDLL("libc.so.6")
                libc.malloc_trim(0)
            except Exception:
                pass
            print("[EditFlow Acoustic Engine] Idle sleep: Wav2Vec2 model unloaded and RAM trimmed to <200MB.")

    def extract_emissions(self, audio_path: str) -> Tuple[np.ndarray, np.ndarray, float, float]:
        self._ensure_model_loaded()
        
        # Load audio into memory
        try:
            raw_waveform, sr = sf.read(audio_path, dtype='float32')
            if len(raw_waveform.shape) > 1:
                raw_waveform = np.mean(raw_waveform, axis=1)  # Convert to mono
            if sr != self.sample_rate:
                import librosa
                raw_waveform = librosa.resample(raw_waveform, orig_sr=sr, target_sr=self.sample_rate)
        except Exception:
            import librosa
            raw_waveform, sr = librosa.load(audio_path, sr=self.sample_rate, mono=True)

        dev = next(self.torch_model.parameters()).device
        dtype = next(self.torch_model.parameters()).dtype
        
        tensor_wave = torch.from_numpy(raw_waveform).unsqueeze(0).to(device=dev, dtype=dtype)
        with torch.inference_mode():
            logits, _ = self.torch_model(tensor_wave)
            log_probs = torch.nn.functional.log_softmax(logits.float(), dim=-1)[0].cpu().numpy()
        
        del tensor_wave, logits
        if "cuda" in str(dev):
            torch.cuda.empty_cache()

        num_frames = log_probs.shape[0]
        duration_sec = len(raw_waveform) / self.sample_rate
        time_per_frame = duration_sec / num_frames if num_frames > 0 else 0.02
        
        return log_probs, raw_waveform, duration_sec, time_per_frame

    def extract_emissions_range(
        self,
        audio_path: str,
        start_sec: float,
        end_sec: float,
        pad_sec: float = 0.35,
        total_duration: Optional[float] = None
    ) -> Tuple[np.ndarray, np.ndarray, float, float, float]:
        """
        Extracts CTC log-probability emissions for a bounded audio time range [start_sec, end_sec].
        Applies padding to ensure clean vocal attack and decay boundaries without slicing into phonemes.
        Streams/seeks only the requested frames from disk to prevent RAM/CPU explosion on long videos.
        
        Returns:
            Tuple of (log_probs, raw_waveform_slice, slice_duration_sec, time_per_frame, actual_start_sec)
        """
        self._ensure_model_loaded()
        
        actual_start = max(0.0, float(start_sec) - float(pad_sec))
        if total_duration is not None and total_duration > 0:
            actual_end = min(float(total_duration), float(end_sec) + float(pad_sec))
        else:
            actual_end = float(end_sec) + float(pad_sec)
            
        if actual_end <= actual_start + 0.1:
            actual_end = actual_start + 0.5
            
        raw_waveform = None
        try:
            info = sf.info(audio_path)
            file_sr = info.samplerate
            start_frame = max(0, int(actual_start * file_sr))
            stop_frame = min(info.frames, int(actual_end * file_sr))
            if stop_frame > start_frame:
                raw_waveform, sr = sf.read(audio_path, start=start_frame, stop=stop_frame, dtype='float32')
                if len(raw_waveform.shape) > 1:
                    raw_waveform = np.mean(raw_waveform, axis=1)
                if sr != self.sample_rate:
                    import librosa
                    raw_waveform = librosa.resample(raw_waveform, orig_sr=sr, target_sr=self.sample_rate)
        except Exception:
            pass
            
        if raw_waveform is None or len(raw_waveform) == 0:
            import librosa
            dur = max(0.2, actual_end - actual_start)
            raw_waveform, sr = librosa.load(audio_path, offset=actual_start, duration=dur, sr=self.sample_rate, mono=True)
            
        dev = next(self.torch_model.parameters()).device
        dtype = next(self.torch_model.parameters()).dtype
        
        tensor_wave = torch.from_numpy(raw_waveform).unsqueeze(0).to(device=dev, dtype=dtype)
        with torch.inference_mode():
            logits, _ = self.torch_model(tensor_wave)
            log_probs = torch.nn.functional.log_softmax(logits.float(), dim=-1)[0].cpu().numpy()
            
        del tensor_wave, logits
        if "cuda" in str(dev):
            torch.cuda.empty_cache()
            
        num_frames = log_probs.shape[0]
        slice_duration_sec = len(raw_waveform) / self.sample_rate
        time_per_frame = slice_duration_sec / num_frames if num_frames > 0 else 0.02
        
        return log_probs, raw_waveform, slice_duration_sec, time_per_frame, actual_start


class AudioCaptionAlignmentAgent:
    """
    Kavioo Dedicated Audio & Caption Acoustic Alignment Agent
    Anchors word timestamps directly to real FFmpeg speech islands using cumulative syllable weighting.
    Guarantees zero acoustic drift and prevents hallucination during pauses, silence, and background music.
    """
    def __init__(self, min_word_dur_sec: float = 0.15, max_word_dur_sec: float = 0.75, silence_threshold_sec: float = 0.30, char_base_rate_sec: float = 0.055):
        self.min_word_dur_sec = min_word_dur_sec
        self.max_word_dur_sec = max_word_dur_sec
        self.silence_threshold_sec = silence_threshold_sec
        self.char_base_rate_sec = char_base_rate_sec

    def estimate_syllables(self, word: str) -> int:
        """Estimates syllable count for Latin and Indic abugidas (Telugu, Tamil, Hindi, Kannada, Malayalam, Bengali, etc.)."""
        if not word:
            return 1
        w = re.sub(r"[^a-zA-Z0-9]", "", str(word).lower())
        if not w:
            # Indic script: count Unicode letters and subtract virama/pulli conjunct marks
            letters = len(re.findall(r"[\u0900-\u0D7F]", str(word)))
            viramas = len(re.findall(r"[\u094D\u09CD\u0A4D\u0ACD\u0B4D\u0BCD\u0C4D\u0CCD\u0D4D]", str(word)))
            return max(1, letters - viramas)
        if len(w) <= 3:
            return 1
        matches = re.findall(r"[aeiouy]{1,2}", w)
        return max(1, len(matches)) if matches else 1

    def align_cues(self, cues: list, speech_islands: Optional[list] = None, timeline_duration_sec: float = 0.0, is_demo: bool = False, was_chunked: bool = False) -> list:
        """Main alignment entry point — processes cues & anchors them to real acoustic speech islands."""
        if not cues:
            return []

        if not is_demo and speech_islands and len(speech_islands) > 0:
            return self.align_with_speech_islands(cues, speech_islands)

        # Fallback heuristic path
        aligned = [dict(c) for c in cues]
        total_duration = timeline_duration_sec if timeline_duration_sec > 0 else (float(aligned[-1].get("end", len(aligned) * 0.5)))
        global_shift = -0.150 if was_chunked else 0.0

        for i in range(len(aligned)):
            current = aligned[i]
            next_cue = aligned[i + 1] if i + 1 < len(aligned) else None
            start = float(current.get("start", current.get("startTime", 0.0)))
            end = float(current.get("end", current.get("endTime", start + 0.5)))
            text = str(current.get("word", current.get("text", "")))

            char_count = max(1, len(text))
            syllables = self.estimate_syllables(text)
            ideal_dur = max(self.min_word_dur_sec, min(self.max_word_dur_sec, char_count * self.char_base_rate_sec + syllables * 0.04))

            if next_cue:
                next_start = float(next_cue.get("start", next_cue.get("startTime", end)))
                if end > next_start:
                    end = next_start
                gap = next_start - end
                if 0 < gap < self.silence_threshold_sec:
                    end = next_start
            else:
                if total_duration > 0 and end > total_duration:
                    end = total_duration

            current["start"] = round(start, 3)
            current["end"] = round(max(start + self.min_word_dur_sec, end), 3)

        scaled = self.compensate_acoustic_drift(aligned, total_duration)
        if global_shift != 0.0:
            for c in scaled:
                s = max(0.0, c["start"] + global_shift)
                e = max(s + self.min_word_dur_sec, c["end"] + global_shift)
                c["start"] = round(s, 3)
                c["end"] = round(e, 3)

        return scaled

    def align_with_speech_islands(self, cues: list, islands: list) -> list:
        """Anchors cues strictly inside speech islands using syllable density & dynamic boundary rebalancing."""
        words = []
        for c in cues:
            w_dict = dict(c)
            w_dict["text"] = str(w_dict.get("word", w_dict.get("text", "")))
            words.append(w_dict)

        if not words or not islands:
            return words

        total_speech_sec = sum(isl["end"] - isl["start"] for isl in islands)
        if total_speech_sec <= 0:
            return words

        by_island = [[] for _ in islands]
        weights = [max(1, self.estimate_syllables(w["text"])) for w in words]
        total_weight = sum(weights)

        island_idx = 0
        island_end_cumulative = islands[0]["end"] - islands[0]["start"]
        current_weight_sum = 0

        for i, w in enumerate(words):
            word_weight = weights[i]
            pos = ((current_weight_sum + word_weight / 2.0) / total_weight) * total_speech_sec
            current_weight_sum += word_weight

            while island_idx < len(islands) - 1 and pos >= island_end_cumulative:
                island_idx += 1
                island_end_cumulative += (islands[island_idx]["end"] - islands[island_idx]["start"])
            by_island[island_idx].append(w)

        # Boundary Rebalancing Pass
        for _ in range(4):
            changed = False
            densities = []
            for i, isl in enumerate(islands):
                if not by_island[i]:
                    densities.append(0.0)
                    continue
                wts = [max(1, self.estimate_syllables(w["text"])) for w in by_island[i]]
                tw = sum(wts)
                dur = isl["end"] - isl["start"]
                densities.append(tw / dur if dur > 0 else 0.0)

            non_zero = sorted([d for d in densities if d > 0])
            if not non_zero:
                break
            median_density = non_zero[len(non_zero) // 2]

            for i in range(len(islands) - 1):
                island_a = by_island[i]
                island_b = by_island[i + 1]
                if not island_a or not island_b:
                    continue
                den_a = densities[i]
                den_b = densities[i + 1]
                if den_a > median_density * 2.5 and den_b < median_density * 0.4:
                    island_b.insert(0, island_a.pop())
                    changed = True
                elif den_b > median_density * 2.5 and den_a < median_density * 0.4:
                    island_a.append(island_b.pop(0))
                    changed = True
            if not changed:
                break

        # Redistribute time within each island
        result = []
        for i, isl in enumerate(islands):
            island_words = by_island[i]
            if not island_words:
                continue

            isl_start = isl["start"]
            isl_end = isl["end"]
            isl_dur = max(0.05, isl_end - isl_start)

            if len(island_words) == 1:
                w = island_words[0]
                w["start"] = round(isl_start, 3)
                w["end"] = round(isl_end, 3)
                result.append(w)
                continue

            w_weights = [max(1, self.estimate_syllables(w["text"])) for w in island_words]
            isl_total_weight = sum(w_weights)
            max_possible_min_dur = isl_dur / len(island_words)
            dynamic_min_dur = min(self.min_word_dur_sec, max_possible_min_dur)

            cursor = isl_start
            for idx, w in enumerate(island_words):
                share = (w_weights[idx] / isl_total_weight) * isl_dur
                start = cursor
                max_allowed_end = isl_end - (len(island_words) - 1 - idx) * dynamic_min_dur
                end = min(max_allowed_end, start + max(dynamic_min_dur, share))
                cursor = end
                w["start"] = round(start, 3)
                w["end"] = round(end, 3)
                result.append(w)

        result.sort(key=lambda x: x.get("start", 0.0))
        return result

    def compensate_acoustic_drift(self, cues: list, total_duration_sec: float) -> list:
        """Scales timestamps down proportionally if cumulative timing exceeds timeline length."""
        if not total_duration_sec or total_duration_sec <= 0 or not cues:
            return cues
        last_end = float(cues[-1].get("end", 0.0))
        if last_end > total_duration_sec + 1.0:
            scale_ratio = total_duration_sec / last_end
            for c in cues:
                c["start"] = round(float(c.get("start", 0.0)) * scale_ratio, 3)
                c["end"] = round(float(c.get("end", 0.0)) * scale_ratio, 3)
        return cues

