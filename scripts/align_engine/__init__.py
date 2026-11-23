"""Zeno Multilingual Forced Alignment Engine (`scripts/align_engine`).

4-Stage Transcription & Acoustic Boundary Alignment Stack:
  - Stage 0: Gemini / Zeno Cloud verbatim transcriber + orthographic guardian (`gemini_transcriber.py`, `guardian.py`)
  - Stage 1: Indic G2P phonetic mapper (`phonetics/indic_phonemes.py`), cross-script phonetic bridge, and 22-language lexicon (`lexicon.py`)
  - Stage 2: Meta omniASR-CTC-300M-v2 CTC emission extractor (`acoustic.py`)
  - Stage 3: Silence-masked Viterbi forced aligner (`viterbi.py`), Silero VAD (`vad.py`), and waveform onset/offset verifier (`onset_verifier.py`)
"""

from __future__ import annotations

import inspect
import json
import math
import os
import re
import subprocess
import wave
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

from . import vad
from .acoustic import AcousticModel, AudioCaptionAlignmentAgent
from .gemini_transcriber import (
    GeminiTranscriber,
    get_shared_transcriber,
    normalize_lang_code,
    transcribe_offline_fallback,
)
from .guardian import TranscriptionGuardian, guardian_engine
from .lexicon import KaviooLexiconEngine, ZenoLexiconEngine, lexicon_engine
from .onset_verifier import AcousticOnsetVerifier
from .pipeline import (
    EditFlowAcousticPipeline,
    IndicPhoneticMapper,
    KaviooAcousticPipeline,
    ScriptCutAcousticPipeline,
    ZenoAcousticPipeline,
    get_shared_pipeline,
    reset_shared_pipeline,
    unload_shared_pipeline,
)
from .script_matcher import (
    compute_dual_span_similarity,
    filter_fumbles_in_words,
    group_words_into_utterances,
    match_clips_to_script,
    normalize_dual_tokens,
    parse_target_script,
)
from .viterbi import ViterbiAligner


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
    """Detect non-silent untranscribed audio islands (such as AI tail babble) after `last_word_end`."""
    if waveform is None or len(waveform) == 0 or duration_sec <= last_word_end + min_gap_after_word_sec:
        return []

    try:
        env_res = onset_verifier.compute_energy_envelope(waveform)
    except Exception:
        return []
    if not isinstance(env_res, (tuple, list)) or len(env_res) != 2:
        return []
    rms_env, rms_times = env_res
    try:
        if len(rms_env) < 3:
            return []
    except TypeError:
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

    active_mask = post_rms >= rms_thresh
    if log_probs is not None and len(log_probs) > 0:
        non_blank_prob = 1.0 - np.exp(log_probs[:, blank_id])
        ctc_frames = np.clip((post_times / max(time_per_frame, 1e-4)).astype(int), 0, len(non_blank_prob) - 1)
        ctc_active = non_blank_prob[ctc_frames] >= 0.25
        active_mask = active_mask & (ctc_active | (post_rms >= rms_thresh * 1.25))

    if not np.any(active_mask):
        return []

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
                islands.append(
                    {
                        "start": round(t_s, 3),
                        "end": round(t_e, 3),
                        "duration": round(t_e - t_s, 3),
                        "type": "TAIL_BABBLE",
                        "label": "[TAIL_BABBLE]",
                        "rms": round(mean_r, 3),
                    }
                )
            cur_start_idx = idx_int
            cur_end_idx = idx_int

    t_s = float(post_times[cur_start_idx])
    t_e = float(post_times[cur_end_idx])
    if t_e - t_s >= min_tail_duration_sec:
        mean_r = float(np.mean(post_rms[cur_start_idx : cur_end_idx + 1]))
        islands.append(
            {
                "start": round(t_s, 3),
                "end": round(t_e, 3),
                "duration": round(t_e - t_s, 3),
                "type": "TAIL_BABBLE",
                "label": "[TAIL_BABBLE]",
                "rms": round(mean_r, 3),
            }
        )

    return islands


def _strip_offscript_tail_babble(
    words_list: List[str],
    script_lines: Optional[List[str]],
    work_dir: Optional[Path] = None,
) -> List[str]:
    """Strip known trailing AI-generated gibberish tokens prior to Viterbi forced alignment."""
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

    known_babble = {
        "sarakarika",
        "chinaru",
        "tarakanchana",
        "kaji",
        "ಸಾರಕರಕರಕಿ",
        "ಚಿನರು",
        "ಟೇರಗಂಚಾನ",
        "ಕಜಿಯಾನ್ಗು",
    }
    tail_tokens |= known_babble

    script_tokens: set[str] = set()
    if script_lines:
        for line in script_lines:
            line_str = str(line or "")
            for raw_tok in line_str.split():
                clean_s = raw_tok.strip(".,?!:;\"'()[]{}—–-।॥").lower()
                if clean_s:
                    script_tokens.add(clean_s)
            for u_tok, p_tok in normalize_dual_tokens(line_str):
                if u_tok:
                    script_tokens.add(u_tok)
                if p_tok:
                    script_tokens.add(p_tok)

    trimmed = list(words_list)
    while len(trimmed) > 2:
        last_clean = trimmed[-1].strip(".,?!:;\"'()[]{}—–-।॥").lower()
        if not last_clean:
            break
        if script_tokens:
            if last_clean in script_tokens:
                break
            last_dual = normalize_dual_tokens(last_clean)
            if any((u in script_tokens) or (p in script_tokens) for u, p in last_dual):
                break
        if last_clean in tail_tokens:
            trimmed.pop()
        else:
            break

    return trimmed


def _ensure_16k_wav(audio_path: str | Path, work_dir: Optional[str | Path] = None) -> Path:
    """Ensure `audio_path` points to a 16 kHz mono PCM WAV file, extracting from video if needed."""
    raw_p = Path(audio_path)
    wdir = Path(work_dir) if work_dir else raw_p.parent
    if raw_p.suffix.lower() == ".wav" and raw_p.is_file():
        return raw_p
    candidate_wav = wdir / "audio16k.wav"
    if candidate_wav.is_file() and candidate_wav.stat().st_size > 44:
        return candidate_wav
    if raw_p.is_file():
        wdir.mkdir(parents=True, exist_ok=True)
        ffmpeg_bin = "ffmpeg"
        try:
            from common import FFMPEG

            ffmpeg_bin = str(FFMPEG)
        except Exception:
            pass
        subprocess.run(
            [
                ffmpeg_bin,
                "-y",
                "-hide_banner",
                "-loglevel",
                "error",
                "-i",
                str(raw_p),
                "-vn",
                "-ac",
                "1",
                "-ar",
                "16000",
                "-c:a",
                "pcm_s16le",
                str(candidate_wav),
            ],
            check=False,
        )
        if candidate_wav.is_file() and candidate_wav.stat().st_size > 44:
            return candidate_wav
    return raw_p


def _parse_script_path_to_lines(script_path: str | Path) -> List[str]:
    """Read a script file into clean spoken-only reference lines (preserving speaker prefixes, stripping URLs/bios/stage notes)."""
    sp = Path(script_path)
    if not sp.is_file():
        return parse_target_script(str(script_path))
    try:
        import sys as _sys

        _scripts_dir = str(Path(__file__).resolve().parent.parent)
        if _scripts_dir not in _sys.path:
            _sys.path.insert(0, _scripts_dir)
        from prepare import parse_script_file as _prep_parse_script

        _, parsed = _prep_parse_script(sp)
        if parsed:
            has_speakers = any(str(ln.get("speaker") or "").strip() for ln in parsed if not ln.get("beat"))
            out: List[str] = []
            seen: set[str] = set()
            for ln in parsed:
                if ln.get("beat"):
                    continue
                txt = str(ln.get("text") or "").strip()
                if not txt or re.match(r"^(?:https?://|//)", txt, re.I):
                    continue
                spk = str(ln.get("speaker") or "").split("--")[0].strip()
                if has_speakers and not spk:
                    continue
                entry = f"{spk}: {txt}" if spk else txt
                k = entry.lower()
                if k not in seen:
                    seen.add(k)
                    out.append(entry)
            if out:
                return out
    except Exception:
        pass

    raw_text = sp.read_text(encoding="utf-8", errors="ignore")
    clean_lines: List[str] = []
    seen_fb: set[str] = set()
    for raw_line in raw_text.splitlines():
        s = raw_line.strip()
        if not s or s.startswith(("#", "//", "---", "--", "*")):
            continue
        if re.match(r"^https?://", s, re.I):
            continue
        if re.match(
            r"^(?:Reference|First\s+Frames|Concept|Visual|Scenes?\s+Breakdown)\b|^Characters?\s*:?$|^Scene\s+\d+|^\[?\s*Visual\s*:",
            s,
            re.I,
        ):
            continue
        if re.match(r"^[A-Za-z][A-Za-z0-9_ .]{0,24}?\s*(?:\([^)]*\))?\s*:\s*$", s):
            continue
        s = re.sub(r"^L?\d+[\.\)]?\s*(?::\s*)?", "", s).strip()
        if re.match(r"^(?:(?:beat|action)\s*:\s*.+|\(.+\)|\[.+\]|\*.+\*)$", s, re.I):
            continue
        s = re.sub(r"\s*\[(WRONG|RIGHT)\]\s*$", "", s, flags=re.I)
        s = re.sub(r"\s*\[[^\]]*\]\s*", " ", s).strip()
        if s and s.lower() not in seen_fb:
            seen_fb.add(s.lower())
            clean_lines.append(s)
    return clean_lines


def _load_16k_mono_waveform(wav_path: Path) -> Tuple[Optional[np.ndarray], float]:
    """Load 16 kHz mono float32 waveform and duration in seconds, or `(None, 0.0)` if unavailable."""
    if not wav_path.is_file():
        return None, 0.0
    try:
        import soundfile as sf

        y, sr = sf.read(str(wav_path), dtype="float32")
        if y.ndim > 1:
            y = np.mean(y, axis=1)
        if sr != 16000 and len(y) > 0:
            import librosa

            y = librosa.resample(y, orig_sr=sr, target_sr=16000)
            sr = 16000
        y = np.asarray(y, dtype=np.float32)
        return y, round(len(y) / float(sr or 16000), 3)
    except Exception:
        pass
    try:
        with wave.open(str(wav_path), "rb") as wf:
            sr = wf.getframerate() or 16000
            ch = wf.getnchannels() or 1
            sw = wf.getsampwidth()
            raw = wf.readframes(wf.getnframes())
        if sw == 2 and raw:
            arr = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
            if ch > 1:
                arr = arr.reshape(-1, ch).mean(axis=1)
            return arr, round(len(arr) / float(sr), 3)
    except Exception:
        pass
    return None, 0.0


def _write_pcm16_wav_slice(
    waveform: Optional[np.ndarray],
    wav_path: Path,
    start_sec: float,
    end_sec: float,
    out_wav: Path,
    sr: int = 16000,
) -> bool:
    """Write `[start_sec, end_sec]` 16 kHz mono PCM16 WAV slice, preserving existing valid cache."""
    if waveform is None or len(waveform) == 0:
        waveform, _ = _load_16k_mono_waveform(wav_path)
    if waveform is None or len(waveform) == 0:
        return False

    i0 = max(0, int(round(float(start_sec) * sr)))
    i1 = min(len(waveform), int(round(float(end_sec) * sr)))
    if i1 <= i0 + max(160, sr // 20):
        return False
    seg = waveform[i0:i1]
    expected_size = 44 + len(seg) * 2
    meta_file = out_wav.parent / "chunk_meta.json"
    bounds = [round(float(start_sec), 3), round(float(end_sec), 3)]
    if out_wav.is_file() and out_wav.stat().st_size == expected_size and meta_file.is_file():
        try:
            old_meta = json.loads(meta_file.read_text(encoding="utf-8"))
            if old_meta.get("bounds") == bounds:
                return True
        except Exception:
            pass

    out_wav.parent.mkdir(parents=True, exist_ok=True)
    # Invalidate stale chunk transcript if slice boundaries changed
    stale_gem = out_wav.parent / "gemini_transcript.json"
    if stale_gem.is_file():
        try:
            stale_gem.unlink()
        except OSError:
            pass

    pcm = np.clip(np.round(seg * 32767.0), -32768, 32767).astype("<i2").tobytes()
    tmp_wav = out_wav.with_name(out_wav.name + ".tmp")
    with wave.open(str(tmp_wav), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sr)
        wf.writeframes(pcm)
    os.replace(tmp_wav, out_wav)
    try:
        meta_file.write_text(json.dumps({"bounds": bounds}, indent=2), encoding="utf-8")
    except OSError:
        pass
    return out_wav.is_file() and out_wav.stat().st_size > 44


def _is_low_voiced_energy(waveform: Optional[np.ndarray], sr: int = 16000) -> bool:
    """Return True when audio has < 0.25 s above -34 dBFS or is steady stationary room noise."""
    if waveform is None or len(waveform) < max(160, sr // 10):
        return True
    hop = max(1, sr // 100)
    n_frames = len(waveform) // hop
    if n_frames <= 0:
        return True
    frames = np.asarray(waveform[: n_frames * hop], dtype=np.float32).reshape(n_frames, hop)
    rms = np.sqrt(np.mean(frames * frames, axis=1))
    db = 20.0 * np.log10(np.maximum(rms, 1e-7))
    voiced_34_sec = float(np.sum(db > -34.0)) * 0.01
    if voiced_34_sec < 0.25:
        return True
    dbs = np.sort(db)
    p50 = float(dbs[len(dbs) // 2])
    p90 = float(dbs[min(len(dbs) - 1, int(len(dbs) * 0.90))])
    return (p90 - p50) <= 4.2 and float(np.mean(db > -28.0)) >= 0.90


def _cache_speech_spans(wdir: Path, wav_path: Path, spans: List[Tuple[float, float]]) -> None:
    """Persist VAD speech spans to `wdir / 'speech_spans.json'` matching `prepare.py` cache schema."""
    if not wav_path.is_file():
        return
    try:
        st = wav_path.stat()
        sig = [st.st_size, int(st.st_mtime)]
        cache = wdir / "speech_spans.json"
        if cache.is_file():
            try:
                old = json.loads(cache.read_text(encoding="utf-8"))
                if old.get("sig") == sig:
                    return
            except Exception:
                pass
        wdir.mkdir(parents=True, exist_ok=True)
        tmp = cache.with_name(cache.name + ".tmp")
        tmp.write_text(json.dumps({"sig": sig, "spans": [list(sp) for sp in spans]}), encoding="utf-8")
        os.replace(tmp, cache)
    except OSError:
        pass


def _call_transcribe(
    transcriber: Any,
    wav_path: str,
    language_hint: str,
    target_script_lines: Optional[List[str]],
    work_dir: Path,
    use_cache: bool = True,
    speech_seconds: Optional[float] = None,
) -> Dict[str, Any]:
    """Call `transcriber.transcribe` passing optional kwargs only when accepted by its signature."""
    kwargs: Dict[str, Any] = {
        "wav_path": wav_path,
        "language_hint": language_hint,
        "target_script_lines": target_script_lines,
        "work_dir": work_dir,
    }
    try:
        params = inspect.signature(transcriber.transcribe).parameters
        if "use_cache" in params:
            kwargs["use_cache"] = use_cache
        if speech_seconds is not None and "speech_seconds" in params:
            kwargs["speech_seconds"] = round(float(speech_seconds), 3)
    except (TypeError, ValueError):
        pass
    return transcriber.transcribe(**kwargs)


def _is_result_incomplete(res: Any) -> bool:
    """Return True if a transcriber response indicates truncation or incomplete coverage."""
    if not isinstance(res, dict):
        return False
    if res.get("incomplete"):
        return True
    warn = str(res.get("warning") or "").strip().lower()
    return warn.startswith("incomplete") or str(res.get("status") or "").strip().lower() == "incomplete"


def _chunk_cache_is_incomplete(cdir: Path) -> bool:
    """Return True if a cached chunk transcript is missing words or flagged incomplete."""
    gf = cdir / "gemini_transcript.json"
    if not gf.is_file():
        return False
    try:
        data = json.loads(gf.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return True
        if not (data.get("words") or []):
            return True
        if data.get("incomplete"):
            return True
        return str(data.get("warning") or "").strip().lower().startswith("incomplete")
    except Exception:
        return True


def _extract_shifted_cues(
    res: Dict[str, Any],
    offset_sec: float,
    win_start: float,
    win_end: float,
    lang_code: str,
) -> Tuple[List[Dict[str, Any]], List[str]]:
    """Shift cues from a chunk/gap result by `+offset_sec`, clamp to `[win_start, win_end]`, and heal loanwords."""
    cues_out: List[Dict[str, Any]] = []
    words_out: List[str] = []
    raw_cues = res.get("cues") or []
    if isinstance(raw_cues, list) and raw_cues:
        for cue in raw_cues:
            if not isinstance(cue, dict):
                continue
            raw_cw = cue.get("words") or str(cue.get("text") or "").split()
            healed_items, _ = guardian_engine.heal_tier1(raw_cw, lang_code)
            cue_words = [str(item.get("word") or "").strip() for item in healed_items if str(item.get("word") or "").strip()]
            if not cue_words:
                continue
            raw_s = float(cue.get("start", cue.get("startTime", 0.0)) or 0.0)
            raw_e = float(cue.get("end", cue.get("endTime", raw_s + max(0.3, len(cue_words) * 0.25))) or 0.0)
            if raw_e <= raw_s:
                raw_e = raw_s + max(0.3, len(cue_words) * 0.25)
            c_s = round(max(win_start, min(max(win_start, win_end - 0.05), offset_sec + raw_s)), 3)
            c_e = round(max(c_s + 0.05, min(win_end, offset_sec + raw_e)), 3)
            cues_out.append({
                "start": c_s,
                "end": c_e,
                "text": " ".join(cue_words),
                "words": cue_words,
            })
            words_out.extend(cue_words)

    if not cues_out:
        raw_words = [str(w).strip() for w in (res.get("words") or []) if str(w).strip()]
        if raw_words:
            rough_end = res.get("rough_end_sec")
            c_s = round(max(0.0, win_start), 3)
            c_e = round(
                min(win_end, max(c_s + 0.4, offset_sec + float(rough_end)))
                if rough_end is not None
                else max(c_s + 0.4, win_end),
                3,
            )
            cues_out.append({
                "start": c_s,
                "end": c_e,
                "text": " ".join(raw_words),
                "words": list(raw_words),
            })
            words_out.extend(raw_words)
    return cues_out, words_out


def _cues_to_approx_words(cues: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Linearly distribute each cue's words across `[start, end]` for pre-alignment VAD coverage checks."""
    out: List[Dict[str, Any]] = []
    for cue in cues:
        ws = cue.get("words") or str(cue.get("text") or "").split()
        if not ws:
            continue
        cs = float(cue.get("start", 0.0))
        ce = max(cs + 0.1, float(cue.get("end", cs + 0.5)))
        step = (ce - cs) / max(1, len(ws))
        for idx, w in enumerate(ws):
            w_txt = str(w.get("word") if isinstance(w, dict) else w).strip()
            if not w_txt:
                continue
            out.append({
                "word": w_txt,
                "start": round(cs + idx * step, 3),
                "end": round(cs + (idx + 1) * step, 3),
            })
    return out


def _norm_cue_tokens(cue: Dict[str, Any]) -> List[str]:
    ws = cue.get("words") or str(cue.get("text") or "").split()
    return [re.sub(r"[^\w\u0900-\u0D7F]+", "", str(w).lower()) for w in ws if str(w).strip()]


def _merge_cues_dedup(base_cues: List[Dict[str, Any]], extra_cues: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Merge `extra_cues` into `base_cues` chronologically without duplicating overlapping boundary cues."""
    merged = [dict(c) for c in base_cues]
    for ec in extra_cues:
        es = float(ec.get("start", 0.0))
        ee = float(ec.get("end", es + 0.1))
        emid = 0.5 * (es + ee)
        etoks = _norm_cue_tokens(ec)
        is_dup = False
        for bc in merged:
            bs = float(bc.get("start", 0.0))
            be = float(bc.get("end", bs + 0.1))
            ov = min(ee, be) - max(es, bs)
            if ov <= 0.0:
                continue
            btoks = _norm_cue_tokens(bc)
            if etoks and btoks and (etoks == btoks or (len(etoks) <= len(btoks) and " ".join(etoks) in " ".join(btoks))):
                is_dup = True
                break
            if ov >= 0.6 * max(0.1, min(ee - es, be - bs)) and bs <= emid <= be:
                is_dup = True
                break
        if not is_dup:
            merged.append(dict(ec))
    merged.sort(key=lambda c: (float(c.get("start", 0.0)), float(c.get("end", 0.0))))
    return merged


def _strip_tail_babble_from_cues(
    cues: List[Dict[str, Any]],
    words: List[str],
    script_lines: Optional[List[str]],
    work_dir: Path,
) -> Tuple[List[Dict[str, Any]], List[str]]:
    """Apply `_strip_offscript_tail_babble` consistently across `words` and trailing `cues`."""
    trimmed_words = _strip_offscript_tail_babble(words, script_lines, work_dir=work_dir)
    drop_n = max(0, len(words) - len(trimmed_words))
    if drop_n <= 0 or not cues:
        return cues, trimmed_words
    out_cues = [dict(c, words=list(c.get("words") or str(c.get("text") or "").split())) for c in cues]
    rem = drop_n
    while rem > 0 and out_cues:
        cw = out_cues[-1]["words"]
        if len(cw) <= rem:
            rem -= len(cw)
            out_cues.pop()
        else:
            del cw[-rem:]
            out_cues[-1]["text"] = " ".join(cw)
            rem = 0
    return out_cues, trimmed_words


def _write_transcript_meta(wdir: Path, meta: Dict[str, Any]) -> None:
    """Atomically write `transcript_meta.json` inside `wdir`."""
    try:
        wdir.mkdir(parents=True, exist_ok=True)
        path = wdir / "transcript_meta.json"
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, path)
    except OSError:
        pass


def _build_transcript_meta(
    spoken_spans: List[Tuple[float, float]],
    duration_sec: float,
    words: List[Dict[str, Any]],
    chunks_total: int,
    chunks_done: int,
    vad_avail: bool,
    fail_status: str = "",
    any_incomplete: bool = False,
    warning_msg: str = "",
) -> Dict[str, Any]:
    """Compute post-alignment speech coverage and status metadata for `transcript_meta.json`."""
    active_speech_sec = round(sum(max(0.0, e - s) for s, e in spoken_spans), 3)
    words_count = len(words)
    if vad_avail and spoken_spans:
        uncov_12 = vad.uncovered_speech_spans(spoken_spans, words, min_gap_sec=1.2)
        uncov_all_sec = sum(e - s for s, e in vad.uncovered_speech_spans(spoken_spans, words, min_gap_sec=0.05))
        covered_speech_sec = round(max(0.0, active_speech_sec - uncov_all_sec), 3)
        speech_coverage = round(covered_speech_sec / active_speech_sec, 3) if active_speech_sec > 0 else (1.0 if words_count else 0.0)
    else:
        uncov_12 = []
        covered_speech_sec = 0.0
        speech_coverage = 1.0 if words_count else 0.0

    uncov_total = sum(e - s for s, e in uncov_12)
    has_big_uncov = any((e - s) >= 1.5 for s, e in uncov_12)

    if fail_status in ("quota_exceeded", "cloud_failed") and (words_count == 0 or has_big_uncov or speech_coverage < 0.70):
        status = fail_status
    elif vad_avail and active_speech_sec == 0.0 and words_count == 0 and not fail_status:
        status = "no_speech"
    elif words_count == 0 and active_speech_sec < 0.8 and not fail_status and not any_incomplete:
        status = "no_speech"
    elif (
        (active_speech_sec >= 0.8 and words_count == 0)
        or (active_speech_sec >= 4.0 and speech_coverage < 0.45)
        or (uncov_total >= 3.0)
        or (any_incomplete and (words_count == 0 or speech_coverage < 0.75))
    ):
        status = "incomplete"
    else:
        status = "ok"

    if status == "incomplete" and not warning_msg:
        warning_msg = (
            f"incomplete transcript: {words_count} words covering {covered_speech_sec:.1f}s/{active_speech_sec:.1f}s "
            f"({speech_coverage:.0%}) of detected speech"
        )

    return {
        "status": status,
        "duration_sec": round(float(duration_sec or 0.0), 3),
        "active_speech_sec": active_speech_sec,
        "covered_speech_sec": covered_speech_sec,
        "speech_coverage": speech_coverage,
        "words_count": words_count,
        "uncovered_spans": [[round(s, 3), round(e, 3)] for s, e in uncov_12],
        "chunks_total": int(chunks_total),
        "chunks_done": int(chunks_done),
        "warning": warning_msg,
    }


def transcribe_and_align_clip(
    wav_path: str | Path,
    lang: str = "auto",
    script_lines: Optional[List[str] | str | Path] = None,
    script_path: Optional[str | Path] = None,
    work_dir: Optional[str | Path] = None,
    offline_fallback: bool = False,
) -> Dict[str, Any]:
    """Transcribe and force-align a single clip using Silero VAD chunking, gap recovery, and cue-anchored alignment."""
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

    waveform, duration_sec = _load_16k_mono_waveform(wav_p)
    vad_avail = vad.available()
    spoken_spans = vad.speech_spans(waveform, 16000) if (vad_avail and waveform is not None) else []
    if vad_avail and wav_p.is_file():
        _cache_speech_spans(wdir, wav_p, spoken_spans)
    active_speech_sec = round(sum(max(0.0, e - s) for s, e in spoken_spans), 3)

    fallback_lang = normalize_lang_code(lang)
    if fallback_lang == "auto":
        fallback_lang = (
            "kn"
            if parsed_script_lines and any(IndicPhoneticMapper.is_pure_indic(s) for s in parsed_script_lines)
            else "en"
        )

    # Zero-speech fast skip: verified zero Silero speech with low voiced energy avoids wasting Gemini quota
    if (
        not offline_fallback
        and vad_avail
        and waveform is not None
        and len(waveform) >= 4000
        and active_speech_sec < 0.25
        and _is_low_voiced_energy(waveform)
    ):
        meta = _build_transcript_meta(
            spoken_spans=spoken_spans,
            duration_sec=duration_sec,
            words=[],
            chunks_total=0,
            chunks_done=0,
            vad_avail=vad_avail,
        )
        _write_transcript_meta(wdir, meta)
        return {
            "words": [],
            "language": fallback_lang,
            "tail_noise": [],
            "duration_sec": round(duration_sec, 2),
            "model_used": "vad_no_speech",
            "status": "no_speech",
            "incomplete": False,
            "warning": "",
            "transcript_meta": meta,
        }

    # 1. Verbatim word transcription (single-shot <= 22s, VAD pause-chunked > 22s, + gap recovery)
    planned_chunks = vad.plan_speech_chunks(spoken_spans, duration_sec) if duration_sec > 0.0 else []
    cues: List[Dict[str, Any]] = []
    raw_words: List[str] = []
    single_shot_no_cues = False
    single_shot_approx: List[Dict[str, Any]] = []
    models_used: List[str] = []
    detected_lang = normalize_lang_code(lang)
    chunks_total = len(planned_chunks) if len(planned_chunks) > 1 else 1
    chunks_done = 0
    any_incomplete = False
    warnings_seen: List[str] = []
    fail_status = ""
    fail_error = ""
    retry_incomplete_env = os.environ.get("ZENO_RETRY_INCOMPLETE", "").strip() == "1"

    # Suppress script-line prompt on clips with <0.6s Silero human speech so loud SFX/fails-reel B-roll never echoes script text
    script_lines_for_asr = (
        None
        if (vad_avail and duration_sec >= 2.0 and active_speech_sec < 0.6)
        else parsed_script_lines
    )

    if offline_fallback:
        trans_res = transcribe_offline_fallback(
            wav_path=str(wav_p),
            lang=lang,
            script_lines=script_lines_for_asr,
            work_dir=wdir,
        )
        detected_lang = normalize_lang_code(trans_res.get("language") or fallback_lang)
        if detected_lang == "auto":
            detected_lang = fallback_lang
        cues, raw_words = _extract_shifted_cues(trans_res, 0.0, 0.0, max(duration_sec, 10.0), detected_lang)
        if not (trans_res.get("cues") or []):
            single_shot_no_cues = True
            cues = []
        models_used.append(str(trans_res.get("model_used") or "offline"))
        fail_status = str(trans_res.get("status") or "").strip().lower()
        if fail_status in ("success", "ok", ""):
            fail_status = ""
            chunks_done = 1
        else:
            fail_error = str(trans_res.get("error") or fail_status)
    else:
        transcriber = get_shared_transcriber()
        if len(planned_chunks) <= 1 and duration_sec <= 22.0:
            trans_res = _call_transcribe(
                transcriber,
                wav_path=str(wav_p),
                language_hint=lang,
                target_script_lines=script_lines_for_asr,
                work_dir=wdir,
                use_cache=True,
                speech_seconds=active_speech_sec if vad_avail else None,
            )
            c_status = str(trans_res.get("status") or "").strip().lower()
            detected_lang = normalize_lang_code(trans_res.get("language") or fallback_lang)
            if detected_lang == "auto":
                detected_lang = fallback_lang
            models_used.append(str(trans_res.get("model_used") or "gemini"))
            if c_status in ("quota_exceeded", "cloud_failed", "offline", "no_transcript"):
                fail_status = c_status
                fail_error = str(trans_res.get("error") or c_status)
            else:
                chunks_done = 1
                if _is_result_incomplete(trans_res):
                    any_incomplete = True
                    if trans_res.get("warning"):
                        warnings_seen.append(str(trans_res["warning"]))
                if trans_res.get("cues"):
                    cues, raw_words = _extract_shifted_cues(
                        trans_res, 0.0, 0.0, max(duration_sec, 0.5), detected_lang
                    )
                else:
                    single_shot_no_cues = True
                    raw_words = [str(w).strip() for w in (trans_res.get("words") or []) if str(w).strip()]
                    if raw_words:
                        rough_end = float(
                            trans_res.get("rough_end_sec")
                            or min(max(duration_sec, 1.0), max(1.0, len(raw_words) * 0.55))
                        )
                        start_est = spoken_spans[0][0] if spoken_spans else 0.0
                        single_shot_approx = _cues_to_approx_words([{
                            "start": start_est,
                            "end": max(start_est + 0.4, min(max(duration_sec, start_est + 0.5), rough_end)),
                            "words": raw_words,
                        }])
        else:
            for idx, (cs, ce) in enumerate(planned_chunks):
                cdir = wdir / "chunks" / f"chunk_{idx:02d}"
                cwav = cdir / "chunk.wav"
                if not _write_pcm16_wav_slice(waveform, wav_p, cs, ce, cwav):
                    continue
                chunk_speech_sec = round(sum(max(0.0, min(e, ce) - max(s, cs)) for s, e in spoken_spans), 3)
                if vad_avail and spoken_spans and chunk_speech_sec <= 0.05:
                    chunks_done += 1
                    continue
                chunk_use_cache = not (retry_incomplete_env and _chunk_cache_is_incomplete(cdir))
                c_res = _call_transcribe(
                    transcriber,
                    wav_path=str(cwav),
                    language_hint=lang,
                    target_script_lines=script_lines_for_asr,
                    work_dir=cdir,
                    use_cache=chunk_use_cache,
                    speech_seconds=chunk_speech_sec if vad_avail else None,
                )
                c_status = str(c_res.get("status") or "").strip().lower()
                if c_status in ("quota_exceeded", "cloud_failed", "offline", "no_transcript"):
                    fail_status = c_status
                    fail_error = str(c_res.get("error") or c_status)
                    break
                chunks_done += 1
                models_used.append(str(c_res.get("model_used") or "gemini"))
                c_lang = normalize_lang_code(c_res.get("language") or "")
                if c_lang and c_lang != "auto":
                    if detected_lang == "auto" or (detected_lang == "en" and c_lang != "en"):
                        detected_lang = c_lang
                if _is_result_incomplete(c_res):
                    any_incomplete = True
                    if c_res.get("warning"):
                        warnings_seen.append(str(c_res["warning"]))
                shift_lang = detected_lang if detected_lang != "auto" else fallback_lang
                c_cues, _ = _extract_shifted_cues(c_res, cs, cs, ce, shift_lang)
                if c_cues:
                    cues = _merge_cues_dedup(cues, c_cues)
            if detected_lang == "auto":
                detected_lang = fallback_lang
            raw_words = [w for c in cues for w in (c.get("words") or str(c.get("text") or "").split()) if str(w).strip()]

        if fail_status in ("quota_exceeded", "cloud_failed", "offline", "no_transcript"):
            meta = _build_transcript_meta(
                spoken_spans=spoken_spans,
                duration_sec=duration_sec,
                words=[],
                chunks_total=chunks_total,
                chunks_done=chunks_done,
                vad_avail=vad_avail,
                fail_status=fail_status if fail_status in ("quota_exceeded", "cloud_failed") else "cloud_failed",
                warning_msg=fail_error,
            )
            _write_transcript_meta(wdir, meta)
            return {
                "words": [],
                "language": detected_lang if detected_lang != "auto" else fallback_lang,
                "tail_noise": [],
                "duration_sec": round(duration_sec, 2),
                "model_used": "cloud_error" if fail_status in ("quota_exceeded", "cloud_failed") else fail_status,
                "status": fail_status,
                "error": fail_error,
                "incomplete": meta["status"] == "incomplete",
                "warning": fail_error,
                "transcript_meta": meta,
            }

        # Targeted gap recovery for missed speech regions (multi-chunk OR single-shot clips > 10s)
        should_check_gaps = (
            vad_avail
            and bool(spoken_spans)
            and (len(planned_chunks) > 1 or duration_sec > 10.0)
            and (len(raw_words) > 0 or active_speech_sec >= 4.0)
        )
        if should_check_gaps:
            approx_words = _cues_to_approx_words(cues) if cues else single_shot_approx
            uncovered = vad.uncovered_speech_spans(spoken_spans, approx_words, min_gap_sec=1.5)
            uncov_total = sum(e - s for s, e in uncovered)
            max_uncov = max((e - s for s, e in uncovered), default=0.0)
            if uncov_total >= 2.0 or max_uncov >= 2.0:
                gap_groups = vad.plan_speech_chunks(
                    uncovered,
                    duration_sec,
                    target_chunk_sec=14.0,
                    max_chunk_sec=20.0,
                    min_pause_split=0.35,
                    pad_sec=0.35,
                )
                if len(gap_groups) > 3:
                    scored = sorted(
                        gap_groups,
                        key=lambda win: sum(max(0.0, min(e, win[1]) - max(s, win[0])) for s, e in uncovered),
                        reverse=True,
                    )[:3]
                    gap_groups = sorted(scored, key=lambda win: win[0])

                if single_shot_no_cues and raw_words:
                    start_est = spoken_spans[0][0] if spoken_spans else 0.0
                    end_est = single_shot_approx[-1]["end"] if single_shot_approx else min(duration_sec, start_est + 2.0)
                    cues = [{
                        "start": round(start_est, 3),
                        "end": round(end_est, 3),
                        "text": " ".join(raw_words),
                        "words": list(raw_words),
                    }]
                    single_shot_no_cues = False

                for gidx, (gs, ge) in enumerate(gap_groups):
                    gdir = wdir / "chunks" / f"gap_{gidx:02d}"
                    gwav = gdir / "chunk.wav"
                    if not _write_pcm16_wav_slice(waveform, wav_p, gs, ge, gwav):
                        continue
                    gap_speech_sec = round(sum(max(0.0, min(e, ge) - max(s, gs)) for s, e in uncovered), 3)
                    gap_use_cache = not (retry_incomplete_env and _chunk_cache_is_incomplete(gdir))
                    g_res = _call_transcribe(
                        transcriber,
                        wav_path=str(gwav),
                        language_hint=lang,
                        target_script_lines=parsed_script_lines,
                        work_dir=gdir,
                        use_cache=gap_use_cache,
                        speech_seconds=gap_speech_sec,
                    )
                    g_status = str(g_res.get("status") or "").strip().lower()
                    if g_status in ("quota_exceeded", "cloud_failed"):
                        fail_status = g_status
                        fail_error = str(g_res.get("error") or g_status)
                        warnings_seen.append(fail_error)
                        break
                    if _is_result_incomplete(g_res):
                        any_incomplete = True
                        if g_res.get("warning"):
                            warnings_seen.append(str(g_res["warning"]))
                    g_cues, _ = _extract_shifted_cues(g_res, gs, gs, ge, detected_lang)
                    if g_cues:
                        cues = _merge_cues_dedup(cues, g_cues)
                if cues:
                    raw_words = [w for c in cues for w in (c.get("words") or str(c.get("text") or "").split()) if str(w).strip()]

    if detected_lang == "auto":
        detected_lang = fallback_lang

    cues, raw_words = _strip_tail_babble_from_cues(cues, raw_words, parsed_script_lines, work_dir=wdir)

    if len(planned_chunks) > 1 and raw_words and wav_p.is_file() and not fail_status:
        try:
            st = wav_p.stat()
            merged_payload = {
                "wav_size": st.st_size,
                "wav_mtime": st.st_mtime_ns,
                "language": detected_lang,
                "model_used": models_used[0] if models_used else "gemini",
                "full_transcript": " ".join(raw_words),
                "words": raw_words,
                "cues": cues,
            }
            gem_out = wdir / "gemini_transcript.json"
            tmp_gem = gem_out.with_name(gem_out.name + ".tmp")
            tmp_gem.write_text(json.dumps(merged_payload, indent=2, ensure_ascii=False), encoding="utf-8")
            os.replace(tmp_gem, gem_out)
        except OSError:
            pass

    if not raw_words:
        meta = _build_transcript_meta(
            spoken_spans=spoken_spans,
            duration_sec=duration_sec,
            words=[],
            chunks_total=chunks_total,
            chunks_done=chunks_done,
            vad_avail=vad_avail,
            fail_status=fail_status if fail_status in ("quota_exceeded", "cloud_failed") else "",
            any_incomplete=any_incomplete,
            warning_msg="; ".join(dict.fromkeys(w for w in warnings_seen if w)),
        )
        _write_transcript_meta(wdir, meta)
        ret_status = "incomplete" if meta["status"] == "incomplete" else (fail_status or "success")
        return {
            "words": [],
            "language": detected_lang,
            "tail_noise": [],
            "duration_sec": round(duration_sec, 2),
            "model_used": models_used[0] if models_used else "none",
            "status": ret_status,
            "error": fail_error,
            "incomplete": meta["status"] == "incomplete",
            "warning": meta["warning"],
            "transcript_meta": meta,
        }

    # 2. Forced alignment via Meta omniASR-CTC-300M-v2 + cross-script phonetic bridge + Viterbi + OnsetVerifier
    timed_cues = [
        c for c in cues
        if isinstance(c, dict)
        and float(c.get("end", 0.0)) > float(c.get("start", 0.0))
        and (c.get("words") or str(c.get("text") or "").strip())
    ]
    # Coalesce Gemini's sub-cues (whose internal timestamps can drift 2-6s inside a 25s take)
    # into Silero VAD's exact acoustic silence-bounded chunk windows (`planned_chunks`).
    if timed_cues and len(planned_chunks) > 1:
        chunk_buckets: List[List[str]] = [[] for _ in planned_chunks]
        for cue in timed_cues:
            cw = [
                (w.get("word", "") if isinstance(w, dict) else str(w)).strip()
                for w in (cue.get("words") or str(cue.get("text") or "").split())
            ]
            cw = [w for w in cw if w]
            if not cw:
                continue
            c_mid = 0.5 * (float(cue.get("start", 0.0)) + float(cue.get("end", 0.0)))
            best_idx = min(
                range(len(planned_chunks)),
                key=lambda k: (
                    0.0
                    if planned_chunks[k][0] <= c_mid <= planned_chunks[k][1]
                    else min(abs(c_mid - planned_chunks[k][0]), abs(c_mid - planned_chunks[k][1]))
                ),
            )
            chunk_buckets[best_idx].extend(cw)
        coalesced_cues: List[Dict[str, Any]] = []
        for (cs, ce), b_words in zip(planned_chunks, chunk_buckets):
            if b_words:
                coalesced_cues.append(
                    {
                        "start": round(float(cs), 3),
                        "end": round(float(ce), 3),
                        "text": " ".join(b_words),
                        "words": b_words,
                        "_vad_chunk": True,
                    }
                )
        if coalesced_cues:
            timed_cues = coalesced_cues

    use_anchored = bool(timed_cues) and len(planned_chunks) > 1
    align_kwargs = dict(
        audio_path=str(wav_p),
        word_list=raw_words,
        language_code=detected_lang,
        verify_onsets=True,
    )
    pipe = get_shared_pipeline()
    try:
        if use_anchored:
            align_res = pipe.align_anchored_cues(
                audio_path=str(wav_p),
                cues=timed_cues,
                language_code=detected_lang,
                verify_onsets=True,
                total_duration_sec=duration_sec if duration_sec > 0.0 else None,
            )
        else:
            align_res = pipe.align(**align_kwargs)
    except Exception as gpu_err:
        if "cpu" in str(getattr(getattr(pipe, "acoustic_model", None), "device", "cpu")):
            raise
        print(f"[Zeno Aligner] GPU execution failed ({gpu_err}); retrying on CPU.")
        reset_shared_pipeline()
        cpu_pipe = get_shared_pipeline(device="cpu")
        if use_anchored:
            align_res = cpu_pipe.align_anchored_cues(
                audio_path=str(wav_p),
                cues=timed_cues,
                language_code=detected_lang,
                verify_onsets=True,
                total_duration_sec=duration_sec if duration_sec > 0.0 else None,
            )
        else:
            align_res = cpu_pipe.align(**align_kwargs)

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
            if w_start >= round(prev["start"] + 0.18, 3):
                prev["end"] = w_start
            else:
                w_start = prev["end"]
                w_end = max(round(w_start + 0.04, 3), w_end)
        words_out.append(
            {
                "w": w_text,
                "start": w_start,
                "end": w_end,
                "p": w_conf,
            }
        )

    # 3. Untranscribed tail noise / AI babble detection
    last_word_end = words_out[-1]["end"] if words_out else 0.0
    raw_waveform = align_res.get("_raw_waveform")
    if raw_waveform is None:
        raw_waveform = waveform
    log_probs = align_res.get("_log_probs")
    time_per_frame = float(align_res.get("_time_per_frame", 0.02))
    duration_sec = float(align_res.get("duration_sec") or duration_sec or 0.0)

    tail_noise = detect_untranscribed_tail_noise(
        waveform=raw_waveform,
        duration_sec=duration_sec,
        last_word_end=last_word_end,
        onset_verifier=pipe.onset_verifier,
        log_probs=log_probs,
        time_per_frame=time_per_frame,
        blank_id=pipe.viterbi.blank_id,
    )

    meta = _build_transcript_meta(
        spoken_spans=spoken_spans,
        duration_sec=duration_sec,
        words=words_out,
        chunks_total=chunks_total,
        chunks_done=chunks_done,
        vad_avail=vad_avail,
        fail_status=fail_status if fail_status in ("quota_exceeded", "cloud_failed") else "",
        any_incomplete=any_incomplete,
        warning_msg="; ".join(dict.fromkeys(w for w in warnings_seen if w)),
    )
    _write_transcript_meta(wdir, meta)

    return {
        "words": words_out,
        "language": detected_lang,
        "tail_noise": tail_noise,
        "duration_sec": duration_sec,
        "processing_time_sec": align_res.get("processing_time_sec", 0.0),
        "model_used": models_used[0] if models_used else "align_engine",
        "status": "incomplete" if meta["status"] == "incomplete" else "success",
        "incomplete": meta["status"] == "incomplete",
        "warning": meta["warning"],
        "transcript_meta": meta,
    }


__all__ = [
    "ZenoAcousticPipeline",
    "ScriptCutAcousticPipeline",
    "KaviooAcousticPipeline",
    "EditFlowAcousticPipeline",
    "get_shared_pipeline",
    "reset_shared_pipeline",
    "unload_shared_pipeline",
    "AcousticModel",
    "AudioCaptionAlignmentAgent",
    "ViterbiAligner",
    "AcousticOnsetVerifier",
    "IndicPhoneticMapper",
    "ZenoLexiconEngine",
    "KaviooLexiconEngine",
    "lexicon_engine",
    "TranscriptionGuardian",
    "guardian_engine",
    "GeminiTranscriber",
    "get_shared_transcriber",
    "transcribe_offline_fallback",
    "normalize_lang_code",
    "transcribe_and_align_clip",
    "detect_untranscribed_tail_noise",
    "parse_target_script",
    "match_clips_to_script",
    "compute_dual_span_similarity",
    "normalize_dual_tokens",
    "group_words_into_utterances",
    "filter_fumbles_in_words",
]
