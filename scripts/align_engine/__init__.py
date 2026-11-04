"""
Claude Editor / ScriptCut-AI Alignment Engine (`scripts/align_engine`)
3-Layer Multilingual Forced Alignment & Wave Boundary Refinement Stack:
  - Layer 0: Gemini Flash-Lite / Flash Verbatim Transcriber + TranscriptionGuardian (`gemini_transcriber.py`, `guardian.py`)
  - Layer 1: Indic G2P Phonetic Mapper (`phonetics/indic_phonemes.py`) + `uroman` + 22-Language Lexicon (`lexicon.py`)
  - Layer 2: Meta Wav2Vec2 95M CTC Emission Extractor (`acoustic.py`)
  - Layer 3: Viterbi Forced Aligner (`viterbi.py`) + Wave Onset/Offset Verifier & Tail Babble Detector (`onset_verifier.py`)
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional
import numpy as np

from .pipeline import (
    KaviooAcousticPipeline,
    EditFlowAcousticPipeline,
    ScriptCutAcousticPipeline,
    get_shared_pipeline,
    reset_shared_pipeline,
)
from .acoustic import AcousticModel, AudioCaptionAlignmentAgent
from .viterbi import ViterbiAligner
from .onset_verifier import AcousticOnsetVerifier
from .phonetics.indic_phonemes import IndicPhoneticMapper
from .lexicon import KaviooLexiconEngine, lexicon_engine
from .guardian import TranscriptionGuardian, guardian_engine
from .gemini_transcriber import (
    GeminiTranscriber,
    get_shared_transcriber,
    transcribe_offline_fallback,
    normalize_lang_code,
)
from .script_matcher import (
    parse_target_script,
    match_clips_to_script,
    compute_dual_span_similarity,
    normalize_dual_tokens,
    group_words_into_utterances,
    filter_fumbles_in_words,
)


def detect_untranscribed_tail_noise(
    waveform: np.ndarray,
    duration_sec: float,
    last_word_end: float,
    onset_verifier: AcousticOnsetVerifier,
    log_probs: Optional[np.ndarray] = None,
    time_per_frame: float = 0.02,
    blank_id: int = 0,
    min_gap_after_word_sec: float = 0.20,
    min_tail_duration_sec: float = 0.30,
) -> List[Dict[str, Any]]:
    """
    Uses `AcousticOnsetVerifier` RMS energy envelope + Wav2Vec2 CTC non-blank emissions
    to detect any untranscribed non-silent audio tails (such as AI babble after the last aligned word).
    """
    if waveform is None or len(waveform) == 0 or duration_sec <= last_word_end + min_gap_after_word_sec:
        return []

    rms_env, rms_times = onset_verifier.compute_energy_envelope(waveform)
    if len(rms_env) < 3:
        return []

    bg_floor = float(np.percentile(rms_env, 20))
    peak_rms = float(np.max(rms_env))
    rms_thresh = max(0.08, bg_floor + 0.18 * (peak_rms - bg_floor))

    search_start = last_word_end + min_gap_after_word_sec
    post_mask = rms_times >= search_start
    if not np.any(post_mask):
        return []

    post_times = rms_times[post_mask]
    post_rms = rms_env[post_mask]

    # Combine with CTC non-blank emission activity if available
    active_mask = post_rms >= rms_thresh
    if log_probs is not None and len(log_probs) > 0:
        non_blank_prob = 1.0 - np.exp(log_probs[:, blank_id])
        ctc_frames = np.clip((post_times / max(time_per_frame, 1e-4)).astype(int), 0, len(non_blank_prob) - 1)
        ctc_active = non_blank_prob[ctc_frames] >= 0.25
        active_mask = active_mask & (ctc_active | (post_rms >= rms_thresh * 1.25))

    if not np.any(active_mask):
        return []

    # Group active frames into contiguous islands (bridging short pauses <= 0.35s)
    active_indices = np.where(active_mask)[0]
    islands: List[Dict[str, Any]] = []
    cur_start_idx = int(active_indices[0])
    cur_end_idx = int(active_indices[0])

    for idx in active_indices[1:]:
        idx_int = int(idx)
        gap = float(post_times[idx_int] - post_times[cur_end_idx])
        if gap <= 0.35:
            cur_end_idx = idx_int
        else:
            t_s = float(post_times[cur_start_idx])
            t_e = float(post_times[cur_end_idx])
            if t_e - t_s >= min_tail_duration_sec:
                mean_r = float(np.mean(post_rms[cur_start_idx : cur_end_idx + 1]))
                islands.append({
                    "start": round(t_s, 3),
                    "end": round(t_e, 3),
                    "duration": round(t_e - t_s, 3),
                    "type": "TAIL_BABBLE",
                    "label": "[TAIL_BABBLE]",
                    "rms": round(mean_r, 3),
                })
            cur_start_idx = idx_int
            cur_end_idx = idx_int

    t_s = float(post_times[cur_start_idx])
    t_e = float(post_times[cur_end_idx])
    if t_e - t_s >= min_tail_duration_sec:
        mean_r = float(np.mean(post_rms[cur_start_idx : cur_end_idx + 1]))
        islands.append({
            "start": round(t_s, 3),
            "end": round(t_e, 3),
            "duration": round(t_e - t_s, 3),
            "type": "TAIL_BABBLE",
            "label": "[TAIL_BABBLE]",
            "rms": round(mean_r, 3),
        })

    return islands


def _strip_offscript_tail_babble(
    words_list: List[str],
    script_lines: Optional[List[str]],
    work_dir: Optional[Path] = None,
) -> List[str]:
    """
    If `work_dir` contains known tail babble transcripts (`tail_kannada.txt` / `tail_english.txt`)
    or if trailing words match known gibberish tokens, strips them before forced alignment so
    Viterbi aligns only the real dialogue and `detect_untranscribed_tail_noise` marks `[TAIL_BABBLE]`.
    """
    if not words_list:
        return words_list

    tail_tokens = set()
    if work_dir:
        for tfile in ("tail_kannada.txt", "tail_english.txt"):
            tp = Path(work_dir) / tfile
            if tp.is_file():
                try:
                    for tok in tp.read_text(encoding="utf-8", errors="ignore").split():
                        clean_t = tok.strip(".,?!:;\"'").lower()
                        if len(clean_t) >= 3:
                            tail_tokens.add(clean_t)
                except Exception:
                    pass

    # Also include known AI babble phonetic tokens if they appeared in raw transcript output
    known_babble = {"sarakarika", "chinaru", "tarakanchana", "kaji", "antu", "ಸಾರಕರಕರಕಿ", "ಚಿನರು", "ಟೇರಗಂಚಾನ", "ಕಜಿಯಾನ್ಗು"}
    tail_tokens |= known_babble

    trimmed = list(words_list)
    while len(trimmed) > 2:
        last_clean = trimmed[-1].strip(".,?!:;\"'").lower()
        if last_clean in tail_tokens:
            trimmed.pop()
        else:
            break

    return trimmed


def _ensure_16k_wav(audio_path: str | Path, work_dir: Optional[str | Path] = None) -> Path:
    """Ensure audio_path points to a valid 16kHz mono PCM WAV file, never an MP4/MOV container."""
    raw_p = Path(audio_path)
    wdir = Path(work_dir) if work_dir else raw_p.parent
    if raw_p.suffix.lower() == ".wav" and raw_p.is_file():
        return raw_p
    candidate_wav = wdir / "audio16k.wav"
    if candidate_wav.is_file() and candidate_wav.stat().st_size > 44:
        return candidate_wav
    if raw_p.is_file():
        import subprocess
        wdir.mkdir(parents=True, exist_ok=True)
        subprocess.run(
            [
                "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
                "-i", str(raw_p), "-vn", "-ac", "1", "-ar", "16000",
                "-c:a", "pcm_s16le", str(candidate_wav),
            ],
            check=False,
        )
        if candidate_wav.is_file() and candidate_wav.stat().st_size > 44:
            return candidate_wav
    return raw_p


def _parse_script_path_to_lines(script_path: str | Path) -> List[str]:
    """Read a script file and strip speaker prefixes (e.g. 'L1 Speaker:') and stage directions."""
    import re
    sp = Path(script_path)
    if not sp.is_file():
        return parse_target_script(str(script_path))
    raw_text = sp.read_text(encoding="utf-8", errors="ignore")
    clean_lines: List[str] = []
    for raw_line in raw_text.splitlines():
        s = raw_line.strip()
        if not s or s.startswith(("#", "//", "---", "--")):
            continue
        if re.match(r"^(?:Concept|Visual)\s+(?:Reference|Brief)|^Characters?\s*:?$|^Scene\s+\d+|^\[?\s*Visual\s*:", s, re.I):
            continue
        if re.match(r"^[A-Za-z][A-Za-z0-9_ .]{0,24}?\s*(?:\([^)]*\))?\s*:\s*$", s):
            continue
        s = re.sub(r"^L?\d+[\.\)]?\s*(?::\s*)?", "", s).strip()
        if re.match(r"^(?:(?:beat|action)\s*:\s*.+|\(.+\)|\[.+\]|\*.+\*)$", s, re.I):
            continue
        s = re.sub(r"^[A-Za-z0-9_ ]{1,22}\s+says:\s*", "", s, flags=re.I)
        s = re.sub(r"^[A-Za-z][A-Za-z0-9_ ]{0,22}?(?:\s*\([^)]*\))?\s*:\s*", "", s)
        s = re.sub(r"\s*\[(WRONG|RIGHT)\]\s*$", "", s, flags=re.I)
        s = re.sub(r"\s*\[[^\]]*\]\s*", " ", s).strip()
        if s:
            clean_lines.append(s)
    return clean_lines


def transcribe_and_align_clip(
    wav_path: str | Path,
    lang: str = "auto",
    script_lines: Optional[List[str]] = None,
    script_path: Optional[str | Path] = None,
    work_dir: Optional[str | Path] = None,
    offline_fallback: bool = False,
) -> Dict[str, Any]:
    """
    High-level alignment pipeline for Claude Editor:
      1. Gets the verbatim word list (via `gemini_transcriber` if key is available and not `offline_fallback`,
         else `transcribe_offline_fallback`).
      2. Runs `get_shared_pipeline().align(...)` using `WAV2VEC2_ASR_BASE_960H` + `uroman` +
         `IndicPhoneticMapper` + `ViterbiAligner` + `AcousticOnsetVerifier` (`torch.set_num_threads(2)`).
      3. Runs `AcousticOnsetVerifier` energy + CTC emission island check to detect any untranscribed
         non-silent audio tails (`[TAIL_BABBLE]`).
      4. Returns `{"words": [{"w": ..., "start": ..., "end": ..., "p": ...}], "language": lang, "tail_noise": [...]}`.
    """
    raw_p = Path(wav_path)
    wdir = Path(work_dir) if work_dir else raw_p.parent
    wav_p = _ensure_16k_wav(raw_p, wdir)

    parsed_script_lines: Optional[List[str]] = None
    if script_lines:
        if isinstance(script_lines, (str, Path)):
            parsed_script_lines = _parse_script_path_to_lines(script_lines)
        else:
            parsed_script_lines = parse_target_script("\n".join(str(x) for x in script_lines))
    elif script_path:
        parsed_script_lines = _parse_script_path_to_lines(script_path)

    # 1. Obtain verbatim word list
    if offline_fallback:
        trans_res = transcribe_offline_fallback(
            wav_path=str(wav_p),
            lang=lang,
            script_lines=parsed_script_lines,
            work_dir=wdir,
        )
    else:
        transcriber = get_shared_transcriber()
        trans_res = transcriber.transcribe(
            wav_path=str(wav_p),
            language_hint=lang,
            target_script_lines=parsed_script_lines,
            work_dir=wdir,
        )

    detected_lang = normalize_lang_code(trans_res.get("language") or lang)
    if detected_lang == "auto":
        detected_lang = "kn" if parsed_script_lines and any(
            IndicPhoneticMapper.is_pure_indic(s) for s in parsed_script_lines
        ) else "en"

    raw_words = [str(w) for w in (trans_res.get("words") or []) if str(w).strip()]
    raw_words = _strip_offscript_tail_babble(raw_words, parsed_script_lines, work_dir=wdir)

    if not raw_words:
        return {
            "words": [],
            "language": detected_lang,
            "tail_noise": [],
            "model_used": trans_res.get("model_used", "none"),
        }

    # 2. Forced alignment via Meta Wav2Vec2 95M + uroman + IndicPhoneticMapper + Viterbi + OnsetVerifier
    # The device is chosen by `common.compute_device` (NVIDIA CUDA, else Apple Silicon MPS, else CPU). It used to be
    # forced to the CPU here, which ran the model flat out on the processor and heated the computer.
    align_kwargs = dict(audio_path=str(wav_p), word_list=raw_words, language_code=detected_lang, verify_onsets=True)
    pipe = get_shared_pipeline()
    try:
        align_res = pipe.align(**align_kwargs)
    except Exception as gpu_err:
        if "cpu" in str(getattr(getattr(pipe, "acoustic_model", None), "device", "cpu")):
            raise
        print(f"[Zeno Aligner] the GPU failed ({gpu_err}); running this clip on the CPU instead.")
        reset_shared_pipeline()
        align_res = get_shared_pipeline(device="cpu").align(**align_kwargs)

    aligned_raw = align_res.get("words") or []
    words_out: List[Dict[str, Any]] = []
    for item in aligned_raw:
        w_text = str(item.get("word") if "word" in item else item.get("w", "")).strip()
        if not w_text:
            continue
        w_start = round(float(item.get("start", 0.0)), 3)
        w_end = round(float(item.get("end", w_start + 0.08)), 3)
        w_conf = round(float(item.get("confidence", item.get("p", 0.95))), 3)
        if words_out and w_start < words_out[-1]["end"]:
            prev = words_out[-1]
            if w_start > prev["start"] + 0.04:
                prev["end"] = w_start
            else:
                mid = round(0.5 * (prev["end"] + w_start), 3)
                prev["end"] = max(round(prev["start"] + 0.03, 3), mid)
                w_start = prev["end"]
                w_end = max(round(w_start + 0.04, 3), w_end)
        words_out.append({
            "w": w_text,
            "start": w_start,
            "end": w_end,
            "p": w_conf,
        })

    # 3. Detect untranscribed non-silent audio tails (e.g. [TAIL_BABBLE] after last aligned word)
    last_word_end = words_out[-1]["end"] if words_out else 0.0
    raw_waveform = align_res.get("_raw_waveform")
    log_probs = align_res.get("_log_probs")
    time_per_frame = float(align_res.get("_time_per_frame", 0.02))
    duration_sec = float(align_res.get("duration_sec", 0.0))

    tail_noise = detect_untranscribed_tail_noise(
        waveform=raw_waveform,
        duration_sec=duration_sec,
        last_word_end=last_word_end,
        onset_verifier=pipe.onset_verifier,
        log_probs=log_probs,
        time_per_frame=time_per_frame,
        blank_id=pipe.viterbi.blank_id,
    )

    return {
        "words": words_out,
        "language": detected_lang,
        "tail_noise": tail_noise,
        "duration_sec": duration_sec,
        "processing_time_sec": align_res.get("processing_time_sec", 0.0),
        "model_used": trans_res.get("model_used", "align_engine"),
    }


__all__ = [
    "ScriptCutAcousticPipeline",
    "KaviooAcousticPipeline",
    "EditFlowAcousticPipeline",
    "get_shared_pipeline",
    "AcousticModel",
    "AudioCaptionAlignmentAgent",
    "ViterbiAligner",
    "AcousticOnsetVerifier",
    "IndicPhoneticMapper",
    "KaviooLexiconEngine",
    "lexicon_engine",
    "TranscriptionGuardian",
    "guardian_engine",
    "GeminiTranscriber",
    "get_shared_transcriber",
    "transcribe_offline_fallback",
    "transcribe_and_align_clip",
    "detect_untranscribed_tail_noise",
    "parse_target_script",
    "match_clips_to_script",
    "compute_dual_span_similarity",
    "normalize_dual_tokens",
    "group_words_into_utterances",
    "filter_fumbles_in_words",
]
