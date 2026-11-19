"""End-to-end multilingual forced alignment pipeline.

Combines Indic G2P phonetics + `uroman`, Meta Wav2Vec2 95M CTC emission extraction,
silence-masked Viterbi decoding, and acoustic onset/offset boundary refinement.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import time
import unicodedata
from pathlib import Path
from typing import Any, Dict, List, Optional

# Ensure PyTorch Hub model cache resolves to Zeno/models/torch before loading acoustic modules.
_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_TORCH_HOME = _PROJECT_ROOT / "models" / "torch"
os.environ.setdefault("TORCH_HOME", str(_TORCH_HOME))
os.environ.setdefault("PYTORCH_ENABLE_MPS_FALLBACK", "1")

import numpy as np

from .acoustic import AcousticModel
from .onset_verifier import AcousticOnsetVerifier
from .viterbi import ViterbiAligner

try:
    from .phonetics.indic_phonemes import INVISIBLE_CHARS, IndicPhoneticMapper
except ImportError:
    import types as _types

    _local_ip = Path(__file__).resolve().parent / "phonetics" / "indic_phonemes.py"
    if _local_ip.is_file():
        _ip_mod = _types.ModuleType("align_engine._indic_phonemes_fallback")
        exec(compile(_local_ip.read_text(encoding="utf-8"), str(_local_ip), "exec"), _ip_mod.__dict__)
        IndicPhoneticMapper = _ip_mod.IndicPhoneticMapper
        INVISIBLE_CHARS = _ip_mod.INVISIBLE_CHARS
    else:
        raise

# Normalize typographic quotes, dashes, ellipses, and NBSPs before romanization.
_SYMBOL_MAP = str.maketrans(
    {
        "\u2018": "'",
        "\u2019": "'",
        "\u201a": "'",
        "\u2032": "'",
        "\u201c": '"',
        "\u201d": '"',
        "\u201e": '"',
        "\u2013": "-",
        "\u2014": "-",
        "\u2212": "-",
        "\u2026": "...",
        "\u00a0": " ",
    }
)


def _clean_symbols(word: str) -> str:
    """NFC-normalize and strip zero-width joiners/typographic punctuation prior to romanization."""
    w = unicodedata.normalize("NFC", word).translate(_SYMBOL_MAP)
    for ch in INVISIBLE_CHARS:
        w = w.replace(ch, "")
    return w or word


class ZenoAcousticPipeline:
    """Orchestrates multilingual G2P romanization, Wav2Vec2 CTC extraction, Viterbi alignment, and onset verification."""

    def __init__(self, device: Optional[str] = None):
        self.acoustic_model = AcousticModel(device=device)
        self.viterbi = ViterbiAligner(blank_id=self.acoustic_model.blank_id)
        self.onset_verifier = AcousticOnsetVerifier(sample_rate=self.acoustic_model.sample_rate)
        self._uroman_cache: Dict[str, str] = {}
        self._warned_unromanized: bool = False

    def unload_model(self) -> None:
        """Unload acoustic neural weights from memory."""
        if self.acoustic_model is not None:
            self.acoustic_model.unload_model()

    def _warn_unromanized(self, words: List[str], reason: str) -> None:
        """Emit a single warning per pipeline instance if words cannot be romanized."""
        if self._warned_unromanized:
            return
        self._warned_unromanized = True
        sample = ", ".join(words[:5])
        print(f"[align] WARNING: {len(words)} word(s) left unromanized ({reason}): {sample}", file=sys.stderr)

    def batch_romanize_words(self, words: List[str]) -> List[str]:
        """Convert words to Roman script using `IndicPhoneticMapper` fast-path and batched `uroman` fallback."""
        if not words:
            return []

        results: List[Optional[str]] = [None] * len(words)
        to_query_indices: List[int] = []
        to_query_words: List[str] = []

        for idx, w in enumerate(words):
            w_clean = _clean_symbols(w)
            if all(ord(c) < 128 for c in w_clean):
                results[idx] = w_clean
            elif w_clean in self._uroman_cache:
                results[idx] = self._uroman_cache[w_clean]
            elif IndicPhoneticMapper.is_pure_indic(w_clean):
                trans = IndicPhoneticMapper.transliterate_indic_script(w_clean)
                alpha = re.sub(r"[^a-zA-Z]", "", trans)
                if len(alpha) >= 2:
                    results[idx] = trans
                    self._uroman_cache[w_clean] = trans
                else:
                    to_query_indices.append(idx)
                    to_query_words.append(w_clean)
            else:
                to_query_indices.append(idx)
                to_query_words.append(w_clean)

        if not to_query_words:
            return [r if r is not None else words[i] for i, r in enumerate(results)]

        here = Path(__file__).resolve().parent
        candidate_paths = [
            here / "uroman" / "uroman" / "uroman.pl",
            here / "uroman" / "bin" / "uroman.pl",
            here / "uroman" / "uroman.pl",
        ]
        uroman_script = next((str(p) for p in candidate_paths if p.is_file()), None)

        if not uroman_script:
            self._warn_unromanized(to_query_words, "uroman.pl not found")
            return [r if r is not None else words[i] for i, r in enumerate(results)]

        uroman_dir = os.path.dirname(uroman_script)
        uroman_root = os.path.dirname(uroman_dir) if os.path.basename(uroman_dir) in ("uroman", "bin") else uroman_dir
        lib_dir = os.path.join(uroman_root, "lib")

        joined = "\n".join(to_query_words)
        try:
            cmd = ["perl"]
            if os.path.exists(lib_dir):
                cmd.extend(["-I", lib_dir])
            cmd.extend([uroman_script, "-l", "xxx"])
            proc = subprocess.run(
                cmd,
                input=joined.encode("utf-8"),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=True,
            )
            romanized_lines = proc.stdout.decode("utf-8").split("\n")
            for q_i, orig_idx in enumerate(to_query_indices):
                rom_val = romanized_lines[q_i].strip() if q_i < len(romanized_lines) else to_query_words[q_i]
                results[orig_idx] = rom_val
                self._uroman_cache[to_query_words[q_i]] = rom_val
        except Exception as exc:
            self._warn_unromanized(to_query_words, f"perl/uroman failed: {type(exc).__name__}")
            for q_i, orig_idx in enumerate(to_query_indices):
                results[orig_idx] = to_query_words[q_i]

        return [r if r is not None else words[i] for i, r in enumerate(results)]

    def _word_to_phonetic_tokens(self, orig_w: str, clean_w: str, rw: str, language_code: str) -> str:
        """Map a word (including bilingual Latin-Indic compounds) to Wav2Vec2 acoustic character tokens."""
        has_indic = IndicPhoneticMapper.is_pure_indic(clean_w)
        has_latin = bool(re.search(r"[A-Za-z0-9]", clean_w))

        if has_indic and has_latin:
            segments = re.findall(r"[A-Za-z0-9']+|[\u0900-\u0D7F]+", clean_w)
            phonetic_parts: List[str] = []
            for seg in segments:
                if IndicPhoneticMapper.is_pure_indic(seg):
                    phonetic_parts.append(
                        IndicPhoneticMapper.map_to_acoustic_tokens(
                            seg,
                            lang=language_code,
                            is_native_indic=True,
                            orig_word=seg,
                        )
                    )
                else:
                    phonetic_parts.append(
                        IndicPhoneticMapper.map_to_acoustic_tokens(
                            seg,
                            lang="en",
                            is_native_indic=False,
                            orig_word=seg,
                        )
                    )
            return "".join(phonetic_parts)

        is_word_indic = has_indic
        is_pure_english_word = not is_word_indic and bool(re.match(r"^[a-zA-Z0-9'-]+$", rw))
        return IndicPhoneticMapper.map_to_acoustic_tokens(
            rw,
            lang="en" if is_pure_english_word else language_code,
            is_native_indic=is_word_indic,
            orig_word=clean_w,
        )

    def align(
        self,
        audio_path: str,
        transcript_text: Optional[str] = None,
        word_list: Optional[List[str]] = None,
        cues: Optional[List[Dict[str, Any]]] = None,
        language_code: str = "en",
        verify_onsets: bool = True,
        max_audio_sec: Optional[float] = None,
    ) -> Dict[str, Any]:
        """Run forced alignment on `audio_path`, preserving 1:1 correspondence with input words."""
        if cues is not None and len(cues) > 0:
            return self.align_anchored_cues(
                audio_path=audio_path,
                cues=cues,
                language_code=language_code,
                verify_onsets=verify_onsets,
            )

        self.acoustic_model._ensure_model_loaded()
        t0 = time.time()

        if word_list is not None and len(word_list) > 0:
            input_words = [str(w) for w in word_list]
        elif transcript_text:
            input_words = transcript_text.strip().split()
        else:
            return {"status": "error", "detail": "Empty transcript", "words": []}

        if not input_words:
            return {"status": "error", "detail": "Empty transcript", "words": []}

        clean_words = []
        clean_indices = []
        for idx, orig_w in enumerate(input_words):
            clean_w = re.sub(r"[^\w\s\u0900-\u0D7F'-]", "", str(orig_w)).strip().strip("-")
            if clean_w:
                clean_words.append(clean_w)
                clean_indices.append(idx)

        romanized_words = self.batch_romanize_words(clean_words)

        token_ids: List[int] = []
        word_spans_meta: List[Dict[str, Any]] = []

        for clean_idx, orig_idx in enumerate(clean_indices):
            orig_w = input_words[orig_idx]
            clean_w = clean_words[clean_idx]
            rw = romanized_words[clean_idx] if clean_idx < len(romanized_words) else clean_w

            phonetic_tokens = self._word_to_phonetic_tokens(orig_w, clean_w, rw, language_code)
            valid_chars = 0
            for c in phonetic_tokens:
                if c in self.acoustic_model.dictionary:
                    token_ids.append(self.acoustic_model.dictionary[c])
                    valid_chars += 1

            if valid_chars > 0:
                word_spans_meta.append(
                    {
                        "word": orig_w,
                        "num_chars": valid_chars,
                        "orig_idx": orig_idx,
                    }
                )

        if not token_ids or not word_spans_meta:
            return {"status": "error", "detail": "No valid phonetic token targets found", "words": []}

        log_probs, raw_waveform, duration_sec, time_per_frame = self.acoustic_model.extract_emissions(audio_path)

        # Bound active speech window when short transcripts sit inside much longer audio buffers
        if max_audio_sec is not None and max_audio_sec > 0 and len(log_probs) > 0:
            cutoff_frame = min(len(log_probs), max(1, int(max_audio_sec / time_per_frame)))
            log_probs_for_viterbi = log_probs[:cutoff_frame]
        else:
            expected_max_sec = len(clean_words) * 0.85 + 3.5
            if duration_sec > expected_max_sec * 1.5 and len(log_probs) > 0:
                bounded_frames = min(len(log_probs), int((expected_max_sec * 1.3) / time_per_frame))
                non_blank_prob = 1.0 - np.exp(log_probs[:, self.viterbi.blank_id])
                active_idx = np.where(non_blank_prob > 0.15)[0]
                if len(active_idx) > 0:
                    active_end_frame = int(np.percentile(active_idx, 98)) + int(1.5 / time_per_frame)
                    cutoff_frame = min(len(log_probs), max(bounded_frames, active_end_frame))
                    log_probs_for_viterbi = log_probs[:cutoff_frame]
                else:
                    log_probs_for_viterbi = log_probs[:bounded_frames]
            else:
                log_probs_for_viterbi = log_probs

        silent = self.onset_verifier.silent_runs(raw_waveform)
        try:
            from . import vad

            silent = vad.merge_runs(silent, vad.nonspeech_runs(vad.speech_spans(raw_waveform), duration_sec))
        except Exception:
            pass
        viterbi_words = self.viterbi.align(
            log_probs_for_viterbi,
            token_ids,
            word_spans_meta,
            time_per_frame,
            silent_spans=silent,
        )

        if verify_onsets:
            refined_active_words = self.onset_verifier.refine_word_onsets(viterbi_words, raw_waveform, duration_sec)
            refined_active_words = self.onset_verifier.snap_starts_to_energy(refined_active_words, raw_waveform)
            refined_active_words = self.onset_verifier.extend_ends_to_energy(refined_active_words, raw_waveform)
        else:
            refined_active_words = viterbi_words
        refined_active_words = self.onset_verifier.clamp_words_to_sound(
            refined_active_words, raw_waveform, extra_runs=silent
        )

        final_words: List[Dict[str, Any]] = []
        active_map = {word_spans_meta[i]["orig_idx"]: refined_active_words[i] for i in range(len(refined_active_words))}

        last_known_start = 0.0
        last_known_end = 0.1

        for idx, orig_w in enumerate(input_words):
            if idx in active_map:
                w_res = active_map[idx]
                w_start = float(w_res["start"])
                w_end = float(w_res["end"])

                max_natural_duration = min(2.5, max(0.35, len(orig_w) * 0.08 + 0.35))
                if w_end - w_start > max_natural_duration:
                    w_end = w_start + max_natural_duration

                last_known_start = w_start
                last_known_end = w_end
                final_words.append(
                    {
                        "word": orig_w,
                        "start": round(w_start, 3),
                        "end": round(w_end, 3),
                        "confidence": round(w_res.get("confidence", 0.95), 3),
                    }
                )
            else:
                final_words.append(
                    {
                        "word": orig_w,
                        "start": round(last_known_start, 3),
                        "end": round(last_known_end, 3),
                        "confidence": 0.90,
                    }
                )

        processing_time = round(time.time() - t0, 3)

        return {
            "status": "success",
            "engine": "Zeno Acoustic Engine",
            "language": language_code,
            "processing_time_sec": processing_time,
            "duration_sec": round(duration_sec, 2),
            "words": final_words,
            "_raw_waveform": raw_waveform,
            "_log_probs": log_probs,
            "_time_per_frame": time_per_frame,
        }

    def align_anchored_cues(
        self,
        audio_path: str,
        cues: List[Dict[str, Any]],
        language_code: str = "en",
        verify_onsets: bool = True,
        total_duration_sec: Optional[float] = None,
    ) -> Dict[str, Any]:
        """Align multi-sentence clips locally within each cue's `[start - 0.35s, end + 0.35s]` window."""
        self.acoustic_model._ensure_model_loaded()
        t0 = time.time()

        if not cues:
            return {
                "status": "success",
                "engine": "Zeno Acoustic Engine (Anchor-Based)",
                "cues": [],
                "words": [],
                "processing_time_sec": 0.0,
            }

        cue_word_lists = []
        flat_clean_words = []
        for cue in cues:
            raw_cw = cue.get("words", [])
            if not raw_cw and cue.get("text"):
                raw_cw = [w for w in str(cue["text"]).split() if w]

            c_words = []
            for w in raw_cw:
                w_str = w.get("word", "") if isinstance(w, dict) else str(w)
                w_clean = re.sub(r"[^\w\s\u0900-\u0D7F'-]", "", w_str).strip().strip("-")
                c_words.append({"orig": w_str, "clean": w_clean})
                if w_clean:
                    flat_clean_words.append(w_clean)
            cue_word_lists.append(c_words)

        unique_clean = list(dict.fromkeys(flat_clean_words))
        unique_romanized = self.batch_romanize_words(unique_clean) if unique_clean else []
        rom_map = dict(zip(unique_clean, unique_romanized))

        aligned_cues_out: List[Dict[str, Any]] = []
        all_aligned_words_out: List[Dict[str, Any]] = []

        for c_idx, cue in enumerate(cues):
            c_start = float(cue.get("start", cue.get("startTime", 0.0)))
            c_end = float(cue.get("end", cue.get("endTime", c_start + 1.5)))
            if c_end <= c_start:
                c_end = c_start + 1.5

            c_words_info = cue_word_lists[c_idx]
            if not c_words_info:
                continue

            token_ids: List[int] = []
            word_spans_meta: List[Dict[str, Any]] = []

            for idx, w_info in enumerate(c_words_info):
                orig_w = w_info["orig"]
                clean_w = w_info["clean"]
                if not clean_w:
                    continue

                rw = rom_map.get(clean_w, clean_w)
                phonetic_tokens = self._word_to_phonetic_tokens(orig_w, clean_w, rw, language_code)
                valid_chars = 0
                for c in phonetic_tokens:
                    if c in self.acoustic_model.dictionary:
                        token_ids.append(self.acoustic_model.dictionary[c])
                        valid_chars += 1

                if valid_chars > 0:
                    word_spans_meta.append(
                        {
                            "word": orig_w,
                            "num_chars": valid_chars,
                            "orig_idx": idx,
                        }
                    )

            if not token_ids or not word_spans_meta:
                dur = max(0.2, c_end - c_start)
                step = dur / max(1, len(c_words_info))
                fallback_words = []
                for idx, w_info in enumerate(c_words_info):
                    w_s = c_start + idx * step
                    w_e = w_s + step
                    fallback_words.append(
                        {
                            "word": w_info["orig"],
                            "start": round(w_s, 3),
                            "end": round(w_e, 3),
                            "confidence": 0.85,
                        }
                    )
                cue_copy = dict(cue)
                cue_copy["start"] = round(c_start, 3)
                cue_copy["end"] = round(c_end, 3)
                cue_copy["startTime"] = round(c_start, 3)
                cue_copy["endTime"] = round(c_end, 3)
                cue_copy["words"] = fallback_words
                aligned_cues_out.append(cue_copy)
                all_aligned_words_out.extend(fallback_words)
                continue

            try:
                log_probs, raw_waveform, slice_dur, time_per_frame, actual_slice_start = (
                    self.acoustic_model.extract_emissions_range(
                        audio_path=audio_path,
                        start_sec=c_start,
                        end_sec=c_end,
                        pad_sec=0.35,
                        total_duration=total_duration_sec,
                    )
                )

                viterbi_words = self.viterbi.align(
                    log_probs,
                    token_ids,
                    word_spans_meta,
                    time_per_frame,
                    silent_spans=self.onset_verifier.silent_runs(raw_waveform) if len(raw_waveform) > 0 else None,
                )

                if verify_onsets and len(raw_waveform) > 0:
                    refined_active = self.onset_verifier.refine_word_onsets(viterbi_words, raw_waveform, slice_dur)
                    refined_active = self.onset_verifier.snap_starts_to_energy(refined_active, raw_waveform)
                    refined_active = self.onset_verifier.extend_ends_to_energy(refined_active, raw_waveform)
                else:
                    refined_active = viterbi_words

                active_map = {word_spans_meta[i]["orig_idx"]: refined_active[i] for i in range(len(refined_active))}

                last_w_start = c_start
                last_w_end = c_start + 0.1
                cue_aligned_words = []

                for idx, w_info in enumerate(c_words_info):
                    orig_w = w_info["orig"]
                    if idx in active_map:
                        w_res = active_map[idx]
                        w_s = actual_slice_start + float(w_res["start"])
                        w_e = actual_slice_start + float(w_res["end"])

                        max_natural = min(2.5, max(0.35, len(orig_w) * 0.08 + 0.35))
                        if w_e - w_s > max_natural:
                            w_e = w_s + max_natural

                        last_w_start = w_s
                        last_w_end = w_e
                        cue_aligned_words.append(
                            {
                                "word": orig_w,
                                "start": round(w_s, 3),
                                "end": round(w_e, 3),
                                "confidence": round(w_res.get("confidence", 0.95), 3),
                            }
                        )
                    else:
                        cue_aligned_words.append(
                            {
                                "word": orig_w,
                                "start": round(last_w_start, 3),
                                "end": round(last_w_end, 3),
                                "confidence": 0.90,
                            }
                        )

                for w_i in range(len(cue_aligned_words)):
                    cur_w = cue_aligned_words[w_i]
                    if w_i + 1 < len(cue_aligned_words):
                        nxt_w = cue_aligned_words[w_i + 1]
                        gap = nxt_w["start"] - cur_w["start"]
                        if gap > 7.0:
                            cur_w["end"] = round(cur_w["start"] + 7.0, 3)
                        elif nxt_w["start"] > cur_w["start"]:
                            cur_w["end"] = round(nxt_w["start"], 3)
                        else:
                            cur_w["end"] = round(max(cur_w["end"], cur_w["start"] + 0.1), 3)
                    else:
                        cur_w["end"] = round(
                            min(c_end if c_end > cur_w["start"] else (cur_w["start"] + 0.6), cur_w["start"] + 7.0),
                            3,
                        )

                c_actual_start = cue_aligned_words[0]["start"] if cue_aligned_words else c_start
                c_actual_end = cue_aligned_words[-1]["end"] if cue_aligned_words else c_end

                cue_copy = dict(cue)
                cue_copy["start"] = c_actual_start
                cue_copy["end"] = c_actual_end
                cue_copy["startTime"] = c_actual_start
                cue_copy["endTime"] = c_actual_end
                cue_copy["words"] = cue_aligned_words
                aligned_cues_out.append(cue_copy)
                all_aligned_words_out.extend(cue_aligned_words)

            except Exception as cue_err:
                print(f"[Zeno Aligner] Cue {c_idx + 1} local alignment fallback: {cue_err}")
                dur = max(0.2, c_end - c_start)
                step = dur / max(1, len(c_words_info))
                fallback_words = []
                for idx, w_info in enumerate(c_words_info):
                    w_s = c_start + idx * step
                    w_e = w_s + step
                    fallback_words.append(
                        {
                            "word": w_info["orig"],
                            "start": round(w_s, 3),
                            "end": round(w_e, 3),
                            "confidence": 0.85,
                        }
                    )
                cue_copy = dict(cue)
                cue_copy["start"] = round(c_start, 3)
                cue_copy["end"] = round(c_end, 3)
                cue_copy["startTime"] = round(c_start, 3)
                cue_copy["endTime"] = round(c_end, 3)
                cue_copy["words"] = fallback_words
                aligned_cues_out.append(cue_copy)
                all_aligned_words_out.extend(fallback_words)

        processing_time = round(time.time() - t0, 3)
        return {
            "status": "success",
            "engine": "Zeno Acoustic Engine (Anchor-Based)",
            "language": language_code,
            "processing_time_sec": processing_time,
            "cues": aligned_cues_out,
            "words": all_aligned_words_out,
        }


# Backward-compatible aliases
KaviooAcousticPipeline = ZenoAcousticPipeline
KaviyoAcousticPipeline = ZenoAcousticPipeline
EditFlowAcousticPipeline = ZenoAcousticPipeline
ScriptCutAcousticPipeline = ZenoAcousticPipeline

_shared_pipeline: Optional[ZenoAcousticPipeline] = None


def get_shared_pipeline(device: Optional[str] = None) -> ZenoAcousticPipeline:
    """Return the shared singleton `ZenoAcousticPipeline` instance."""
    global _shared_pipeline
    if _shared_pipeline is None:
        _shared_pipeline = ZenoAcousticPipeline(device=device)
    return _shared_pipeline


def reset_shared_pipeline() -> None:
    """Reset the shared pipeline singleton (e.g., to reinitialize on CPU after a GPU error)."""
    global _shared_pipeline
    _shared_pipeline = None
