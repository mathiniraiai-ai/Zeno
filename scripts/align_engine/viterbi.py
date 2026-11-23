"""Accelerated Viterbi CTC forced alignment engine with silence masking."""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

import numpy as np

try:
    import torch
    import torchaudio.functional as F

    HAS_TORCH_ALIGN = True
except ImportError:
    HAS_TORCH_ALIGN = False


def _numpy_vectorized_viterbi(
    log_probs: np.ndarray,
    targets: List[int],
    blank_id: int = 0,
) -> List[Dict[str, Any]]:
    """Vectorized NumPy CTC Viterbi trellis alignment over `log_probs` `[T, VocabSize]`."""
    T = log_probs.shape[0]
    U = len(targets)
    if U == 0:
        return []
    if T < U:
        raise ValueError("Unreachable CTC trellis for slice")

    L = 2 * U + 1
    ext_targets = np.full(L, blank_id, dtype=np.int32)
    for i, t in enumerate(targets):
        ext_targets[2 * i + 1] = t

    can_skip = np.zeros(L, dtype=bool)
    if L > 2:
        can_skip[2:] = (ext_targets[2:] != blank_id) & (ext_targets[2:] != ext_targets[:-2])

    trellis = np.full((T, L), -np.inf, dtype=np.float32)
    backtrack = np.zeros((T, L), dtype=np.int8)

    trellis[0, 0] = log_probs[0, ext_targets[0]]
    if L > 1:
        trellis[0, 1] = log_probs[0, ext_targets[1]]

    v_prev = np.full(L, -np.inf, dtype=np.float32)
    v_skip = np.full(L, -np.inf, dtype=np.float32)

    for t in range(1, T):
        prev_t = trellis[t - 1]
        v_prev[1:] = prev_t[:-1]
        v_skip[2:] = np.where(can_skip[2:], prev_t[:-2], -np.inf)

        max_v = prev_t.copy()
        best_from = np.zeros(L, dtype=np.int8)

        mask_prev = v_prev > max_v
        max_v[mask_prev] = v_prev[mask_prev]
        best_from[mask_prev] = 1

        mask_skip = v_skip > max_v
        max_v[mask_skip] = v_skip[mask_skip]
        best_from[mask_skip] = 2

        trellis[t] = max_v + log_probs[t, ext_targets]
        backtrack[t] = best_from

    final_states = [L - 1, L - 2] if L > 1 else [0]
    best_final_s = final_states[int(np.argmax([trellis[T - 1, s] for s in final_states]))]
    if not np.isfinite(trellis[T - 1, best_final_s]):
        raise ValueError("Unreachable CTC trellis for slice")

    path = np.zeros(T, dtype=np.int32)
    curr_s = best_final_s
    for t in range(T - 1, -1, -1):
        path[t] = curr_s
        step = backtrack[t, curr_s]
        if step == 1:
            curr_s -= 1
        elif step == 2:
            curr_s -= 2

    char_spans: List[Dict[str, Any]] = []
    for u in range(U):
        s_idx = 2 * u + 1
        frames = np.where(path == s_idx)[0]
        if len(frames) > 0:
            char_spans.append(
                {
                    "start": int(frames[0]),
                    "end": int(frames[-1] + 1),
                    "score": float(np.mean(log_probs[frames, ext_targets[s_idx]])),
                }
            )
        else:
            char_spans.append({"start": 0, "end": 0, "score": -1.0})

    return char_spans


class ViterbiAligner:
    """CTC forced aligner combining `torchaudio.functional.forced_align` with NumPy fallback."""

    def __init__(self, blank_id: int = 0):
        self.blank_id = blank_id

    def align(
        self,
        log_probs: np.ndarray,
        token_ids: List[int],
        word_spans_meta: List[Dict[str, Any]],
        time_per_frame: float,
        silent_spans: Optional[List[Tuple[float, float]]] = None,
    ) -> List[Dict[str, Any]]:
        """Align `token_ids` against frame-level `log_probs`.

        When `silent_spans` are provided, non-blank emissions inside silent intervals are suppressed
        so uncertain tokens (short interjections, trailing Indic words) cannot drift into silence.
        """
        U = len(token_ids)
        if U == 0 or len(word_spans_meta) == 0:
            return []
        if silent_spans:
            n = log_probs.shape[0]
            dead = np.zeros(n, dtype=bool)
            for a, b in silent_spans:
                dead[max(0, int(a / time_per_frame)) : min(n, int(b / time_per_frame) + 1)] = True
            repeats = sum(1 for k in range(1, U) if token_ids[k] == token_ids[k - 1])
            if dead.any() and int((~dead).sum()) >= U + repeats + 2:
                masked = log_probs.copy()
                masked[dead, :] = -1.0e4
                masked[dead, self.blank_id] = 0.0
                try:
                    return self._align_core(masked, token_ids, word_spans_meta, time_per_frame)
                except Exception:
                    pass
        return self._align_core(log_probs, token_ids, word_spans_meta, time_per_frame)

    def _align_core(
        self,
        log_probs: np.ndarray,
        token_ids: List[int],
        word_spans_meta: List[Dict[str, Any]],
        time_per_frame: float,
    ) -> List[Dict[str, Any]]:
        if log_probs.shape[0] < len(token_ids):
            raise ValueError("Unreachable CTC trellis for slice")

        token_spans = None
        if HAS_TORCH_ALIGN:
            try:
                lp_tensor = torch.from_numpy(log_probs).unsqueeze(0).float()
                targets = torch.tensor([token_ids], dtype=torch.int32)
                aligned_tokens, scores = F.forced_align(lp_tensor.cpu(), targets, blank=self.blank_id)
                spans_raw = F.merge_tokens(aligned_tokens[0], scores[0], blank=self.blank_id)
                cand_spans = [{"start": sp.start, "end": sp.end, "score": sp.score} for sp in spans_raw]
                if len(cand_spans) == len(token_ids) and all(np.isfinite(sp["score"]) for sp in cand_spans):
                    token_spans = cand_spans
            except Exception:
                token_spans = None

        if token_spans is None:
            token_spans = _numpy_vectorized_viterbi(log_probs, token_ids, blank_id=self.blank_id)

        words: List[Dict[str, Any]] = []
        token_idx = 0

        for w_meta in word_spans_meta:
            w_str = w_meta["word"]
            if "tok_start" in w_meta and "tok_end" in w_meta:
                t_start = int(w_meta["tok_start"])
                t_end = int(w_meta["tok_end"])
                if t_end <= t_start:
                    continue
                word_spans = token_spans[t_start:t_end]
                token_idx = max(token_idx, t_end)
            else:
                num_tokens = int(w_meta.get("num_chars", 0))
                if num_tokens <= 0:
                    continue
                word_spans = token_spans[token_idx : token_idx + num_tokens]
                token_idx += num_tokens

            if word_spans:
                first_sp = word_spans[0]
                last_sp = word_spans[-1]
                w_start_frame = first_sp["start"] if isinstance(first_sp, dict) else first_sp.start
                w_end_frame = last_sp["end"] if isinstance(last_sp, dict) else last_sp.end

                start_sec = max(0.0, w_start_frame * time_per_frame)
                end_sec = max(start_sec + 0.05, w_end_frame * time_per_frame)

                score_sum = sum(sp["score"] if isinstance(sp, dict) else sp.score for sp in word_spans)
                score_val = score_sum / len(word_spans)
                if score_val <= 0.0:
                    conf = float(np.exp(score_val))
                elif score_val <= 1.0:
                    conf = float(score_val)
                else:
                    conf = 0.95

                words.append(
                    {
                        "word": w_str,
                        "start": round(start_sec, 3),
                        "end": round(end_sec, 3),
                        "confidence": round(min(1.0, max(0.0, conf)), 3),
                    }
                )
            else:
                words.append(
                    {
                        "word": w_str,
                        "start": 0.0,
                        "end": 0.1,
                        "confidence": 0.85,
                    }
                )

        return words
