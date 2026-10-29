"""
ScriptCut-AI / Claude Editor — Precision Script-to-Take Word-Window Aligner,
Retake/Fumble Eliminator & Zoom/SFX Planner.
Standalone adaptation with zero external dependencies on `src.config` or `src.schemas`.
"""

import re
import json
import functools
from dataclasses import dataclass, field, asdict
from difflib import SequenceMatcher
from typing import List, Dict, Any, Optional, Tuple

from .phonetics.indic_phonemes import IndicPhoneticMapper


PAUSE_SPLIT_SEC = 0.35
FUMBLE_MAX_GAP_SEC = 1.0


@dataclass
class _DefaultMatcherConfig:
    enable_zoom: bool = True
    enable_transitions: bool = True
    punch_in_scale: float = 1.15
    smooth_zoom_start: float = 1.0
    smooth_zoom_end: float = 1.18
    default_focus_x: float = 0.5
    default_focus_y: float = 0.42
    default_sfx_volume_db: float = -10.0
    default_sfx_offset_sec: float = -0.08
    jump_cut_gap_threshold_sec: float = 1.2
    jump_cut_transition_type: str = "dissolve"
    jump_cut_transition_sec: float = 0.14
    scene_transition_type: str = "dissolve"
    scene_transition_sec: float = 0.25
    dialogue_audio_crossfade_sec: float = 0.05
    scene_transition_sfx_volume_db: float = -18.0


CONFIG = _DefaultMatcherConfig()


@dataclass
class ZoomSpec:
    type: str = "none"
    scale_start: float = 1.0
    scale_end: float = 1.0
    focus_x: float = 0.5
    focus_y: float = 0.42

    def model_dump(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class TransitionSpec:
    type: str = "cut"
    duration_sec: float = 0.0
    audio_crossfade_sec: float = 0.05
    is_jump_cut: bool = False
    is_scene_change: bool = False

    def model_dump(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class SfxSpec:
    file: str = "assets/sfx/whoosh.wav"
    offset_sec: float = -0.08
    volume_db: float = -10.0

    def model_dump(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class WordTimestamp:
    word: str
    start: float
    end: float
    confidence: float = 0.95
    onset_confidence: Optional[float] = None
    raw_ctc_start: Optional[float] = None

    def model_dump(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class SegmentPlan:
    id: int
    source_clip: str
    script_line: str
    spoken_text: Optional[str] = None
    language: str = "en"
    start_sec: float = 0.0
    end_sec: float = 0.0
    take_score: float = 0.95
    zoom: ZoomSpec = field(default_factory=ZoomSpec)
    transition: TransitionSpec = field(default_factory=TransitionSpec)
    sfx: Optional[SfxSpec] = None
    words: List[WordTimestamp] = field(default_factory=list)

    def model_dump(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class DroppedTake:
    source_clip: str
    start_sec: float
    end_sec: float
    spoken_text: str
    reason: str = "retake"

    def model_dump(self) -> Dict[str, Any]:
        return asdict(self)


def _w_str(w: Dict[str, Any]) -> str:
    """Extracts word text whether key is 'word' or 'w'."""
    val = w.get("word") if "word" in w else w.get("w", "")
    return str(val or "").strip()


def parse_target_script(script_text_or_path: str) -> List[str]:
    """
    Parses a target script from either a file path (.txt, .md, .json) or raw multiline string
    into an ordered list of clean dialogue lines.
    """
    raw = str(script_text_or_path)
    if "\n" not in raw and len(raw) < 4096:
        try:
            with open(raw, "r", encoding="utf-8") as f:
                raw = f.read()
        except OSError:
            pass

    raw_stripped = raw.strip()
    if raw_stripped.startswith("[") or raw_stripped.startswith("{"):
        try:
            data = json.loads(raw_stripped)
            if isinstance(data, list):
                return [
                    str(item.get("line", item.get("script_line", item))).strip()
                    if isinstance(item, dict)
                    else str(item).strip()
                    for item in data
                    if str(item).strip()
                ]
            elif isinstance(data, dict) and "lines" in data:
                return [str(x).strip() for x in data["lines"] if str(x).strip()]
            elif isinstance(data, dict) and "segments" in data:
                return [str(s.get("script_line", "")).strip() for s in data["segments"] if s.get("script_line")]
        except Exception:
            pass

    lines: List[str] = []
    for line in raw.splitlines():
        cleaned = line.strip()
        if not cleaned or cleaned.startswith("#") or cleaned.startswith("//") or cleaned.startswith("---"):
            continue
        cleaned = re.sub(r"^L?\d+[\.\)]?\s*", "", cleaned)
        cleaned = re.sub(r"^[A-Za-z0-9_ ]{1,20}\s+says:\s*", "", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"^[A-Za-z][A-Za-z0-9_ ]{0,22}(?:\([^)]*\))?\s*:\s*", "", cleaned)
        cleaned = re.sub(r"\s*\[(WRONG|RIGHT)\]\s*$", "", cleaned, flags=re.IGNORECASE)
        cleaned = cleaned.strip()
        if cleaned:
            lines.append(cleaned)
    return lines


def _clean_unicode_token(word: str) -> str:
    """Preserves all Unicode letters/marks (Devanagari, Tamil, Telugu, Kannada, Malayalam, Latin, digits) in lowercase."""
    return re.sub(r"[^\w\u0900-\u0D7F']", "", str(word).lower()).strip()


def _phonetic_token(word: str) -> str:
    """Maps any word (Native Indic or Latin) to its normalized Roman phonetic representation."""
    u = _clean_unicode_token(word)
    if not u:
        return ""
    if IndicPhoneticMapper.is_pure_indic(u):
        rom = IndicPhoneticMapper.map_to_acoustic_tokens(u, lang="kn", is_native_indic=True).lower()
        return re.sub(r"[^a-z0-9]", "", rom) or u
    rom_en = IndicPhoneticMapper.number_to_words(u)
    return re.sub(r"[^a-z0-9']", "", rom_en) or u


def normalize_phonetic_tokens(text: str, lang: str = "en") -> List[str]:
    raw_words = re.findall(r"[\w\u0900-\u0D7F']+", str(text).lower())
    return [_phonetic_token(w) for w in raw_words if _phonetic_token(w)]


def normalize_dual_tokens(text: str) -> List[Tuple[str, str]]:
    """
    Returns a list of `(unicode_token, phonetic_token)` pairs for each word in `text`.
    Guarantees 1.000 matching when both script and transcript share the same script
    AND seamless cross-script matching when one is Romanized and the other is Native script.
    """
    raw_words = re.findall(r"[\w\u0900-\u0D7F']+", str(text).lower())
    out: List[Tuple[str, str]] = []
    for w in raw_words:
        u = _clean_unicode_token(w)
        p = _phonetic_token(w)
        if u or p:
            out.append((u, p))
    return out


@functools.lru_cache(maxsize=65536)
def _dual_word_sim(a: Tuple[str, str], b: Tuple[str, str]) -> float:
    """Computes word-to-word similarity using both exact Unicode and phonetic representations."""
    ua, pa = a
    ub, pb = b
    if ua and ub and ua == ub:
        return 1.0
    if pa and pb and pa == pb:
        return 0.96
    sim_u = SequenceMatcher(None, ua, ub).ratio() if (ua and ub) else 0.0
    sim_p = SequenceMatcher(None, pa, pb).ratio() if (pa and pb) else 0.0
    return max(sim_u, sim_p * 0.95)


def compute_dual_span_similarity(
    span_tokens: List[Tuple[str, str]],
    script_tokens: List[Tuple[str, str]],
) -> float:
    """
    Computes sequence-aware similarity between a candidate word span and a target script line.
    """
    if not span_tokens or not script_tokens:
        return 0.0

    m = len(span_tokens)
    n = len(script_tokens)

    if span_tokens == script_tokens:
        return 1.0

    u_span = " ".join(t[0] for t in span_tokens)
    u_script = " ".join(t[0] for t in script_tokens)
    p_span = " ".join(t[1] for t in span_tokens)
    p_script = " ".join(t[1] for t in script_tokens)

    if u_span == u_script:
        seq_sim = 1.0
    elif p_span and p_script and p_span == p_script:
        seq_sim = 0.96
    else:
        seq_sim = max(
            SequenceMatcher(None, u_span, u_script).ratio(),
            SequenceMatcher(None, p_span, p_script).ratio() * 0.96,
        )

    span_token_set = set(span_tokens)
    recall_sum = 0.0
    for st in script_tokens:
        if st in span_token_set:
            recall_sum += 1.0
        else:
            recall_sum += max(_dual_word_sim(st, wt) for wt in span_tokens)
    recall = recall_sum / n

    script_token_set = set(script_tokens)
    prec_sum = 0.0
    for wt in span_tokens:
        if wt in script_token_set:
            prec_sum += 1.0
        else:
            prec_sum += max(_dual_word_sim(wt, st) for st in script_tokens)
    precision = prec_sum / m

    first_sim = _dual_word_sim(span_tokens[0], script_tokens[0])
    last_sim = _dual_word_sim(span_tokens[-1], script_tokens[-1])
    boundary_anchor = 0.5 * first_sim + 0.5 * last_sim

    len_ratio = min(m, n) / max(m, n)

    score = (
        0.35 * seq_sim
        + 0.22 * recall
        + 0.18 * precision
        + 0.15 * boundary_anchor
        + 0.10 * len_ratio
    )
    return round(float(score), 4)


def compute_take_similarity(take_tokens: List[str], script_tokens: List[str]) -> float:
    """Backward-compatible helper for single-string phonetic token lists."""
    dual_a = [(t, t) for t in take_tokens if t]
    dual_b = [(t, t) for t in script_tokens if t]
    return compute_dual_span_similarity(dual_a, dual_b)


def filter_fumbles_in_words(words: List[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """
    Detects rapid-fire immediate word repeats ("the the", "so so") within 1.0s and keeps only the last occurrence.
    """
    kept: List[Dict[str, Any]] = []
    fumbles: List[Dict[str, Any]] = []
    i = 0
    n = len(words)
    while i < n:
        tok_i = _clean_unicode_token(_w_str(words[i]))
        if not tok_i:
            kept.append(words[i])
            i += 1
            continue

        j = i + 1
        while j < n:
            tok_j = _clean_unicode_token(_w_str(words[j]))
            gap = float(words[j].get("start", 0.0)) - float(words[j - 1].get("end", 0.0))
            if tok_j == tok_i and gap <= FUMBLE_MAX_GAP_SEC:
                j += 1
            else:
                break

        if j - i >= 2:
            fumbles.append({
                "start": float(words[i].get("start", 0.0)),
                "end": float(words[j - 2].get("end", 0.0)),
                "text": " ".join(_w_str(w) for w in words[i:j]),
            })
            kept.append(words[j - 1])
        else:
            kept.append(words[i])
        i = j

    return kept, fumbles


def group_words_into_utterances(
    words: List[Dict[str, Any]],
    pause_split_sec: float = PAUSE_SPLIT_SEC,
) -> List[Dict[str, Any]]:
    """
    Splits aligned words into natural sentence/clause utterances on pauses >= `pause_split_sec`
    or sentence-ending punctuation (`.?!।`).
    """
    utterances: List[Dict[str, Any]] = []
    current: List[Dict[str, Any]] = []

    for i, w in enumerate(words):
        current.append(w)
        nxt = words[i + 1] if i + 1 < len(words) else None
        w_text = _w_str(w)
        ends_sentence = bool(re.search(r"[.?!।]$", w_text))
        gap = (float(nxt.get("start", 0.0)) - float(w.get("end", 0.0))) if nxt else 999.0

        if not nxt or gap >= pause_split_sec or ends_sentence:
            text_str = " ".join(_w_str(x) for x in current)
            utterances.append({
                "words": current,
                "start": float(current[0].get("start", 0.0)),
                "end": float(current[-1].get("end", 0.0)),
                "text": text_str,
                "tokens": normalize_phonetic_tokens(text_str),
                "dual_tokens": normalize_dual_tokens(text_str),
            })
            current = []

    return utterances


def assign_transition_spec(
    seg_idx: int,
    same_clip_as_prev: bool,
    time_gap_sec: float = 0.0,
    enable_transitions: bool = True,
) -> TransitionSpec:
    if seg_idx == 0:
        return TransitionSpec(
            type="cut",
            duration_sec=0.0,
            audio_crossfade_sec=0.0,
            is_jump_cut=False,
            is_scene_change=False,
        )

    is_scene_change = not same_clip_as_prev
    is_jump_cut = bool(
        same_clip_as_prev
        and (time_gap_sec > CONFIG.jump_cut_gap_threshold_sec or time_gap_sec < -0.25)
    )

    if not enable_transitions:
        return TransitionSpec(
            type="cut",
            duration_sec=0.0,
            audio_crossfade_sec=0.0,
            is_jump_cut=is_jump_cut,
            is_scene_change=is_scene_change,
        )

    if is_scene_change:
        return TransitionSpec(
            type=CONFIG.scene_transition_type,
            duration_sec=CONFIG.scene_transition_sec,
            audio_crossfade_sec=CONFIG.scene_transition_sec,
            is_jump_cut=False,
            is_scene_change=True,
        )

    if is_jump_cut:
        return TransitionSpec(
            type=CONFIG.jump_cut_transition_type,
            duration_sec=CONFIG.jump_cut_transition_sec,
            audio_crossfade_sec=CONFIG.jump_cut_transition_sec,
            is_jump_cut=True,
            is_scene_change=False,
        )

    return TransitionSpec(
        type="cut",
        duration_sec=0.0,
        audio_crossfade_sec=CONFIG.dialogue_audio_crossfade_sec,
        is_jump_cut=False,
        is_scene_change=False,
    )


def assign_zoom_and_sfx(
    seg_idx: int,
    total_segments: int,
    script_line: str,
    same_clip_as_prev: bool,
    prev_zoom_type: str,
    enable_zoom: bool = True,
) -> Tuple[ZoomSpec, Optional[SfxSpec]]:
    if not enable_zoom:
        is_scene_change = seg_idx > 0 and not same_clip_as_prev
        sfx = (
            SfxSpec(
                file="assets/sfx/whoosh.wav",
                offset_sec=-0.06,
                volume_db=CONFIG.scene_transition_sfx_volume_db,
            )
            if is_scene_change
            else None
        )
        return (
            ZoomSpec(
                type="none",
                scale_start=1.0,
                scale_end=1.0,
                focus_x=CONFIG.default_focus_x,
                focus_y=CONFIG.default_focus_y,
            ),
            sfx,
        )

    is_hook = seg_idx == 0
    is_climax = seg_idx == total_segments - 1 and total_segments > 2

    if is_hook:
        return (
            ZoomSpec(
                type="smooth_in",
                scale_start=CONFIG.smooth_zoom_start,
                scale_end=1.16,
                focus_x=CONFIG.default_focus_x,
                focus_y=CONFIG.default_focus_y,
            ),
            SfxSpec(
                file="assets/sfx/whoosh.wav",
                offset_sec=0.0,
                volume_db=CONFIG.default_sfx_volume_db,
            ),
        )

    if same_clip_as_prev and prev_zoom_type != "punch_in":
        return (
            ZoomSpec(
                type="punch_in",
                scale_start=CONFIG.punch_in_scale,
                scale_end=CONFIG.punch_in_scale,
                focus_x=CONFIG.default_focus_x,
                focus_y=CONFIG.default_focus_y,
            ),
            SfxSpec(
                file="assets/sfx/pop.wav",
                offset_sec=CONFIG.default_sfx_offset_sec,
                volume_db=CONFIG.default_sfx_volume_db,
            ),
        )

    if is_climax and prev_zoom_type == "none":
        return (
            ZoomSpec(
                type="smooth_in",
                scale_start=CONFIG.smooth_zoom_start,
                scale_end=1.16,
                focus_x=CONFIG.default_focus_x,
                focus_y=CONFIG.default_focus_y,
            ),
            SfxSpec(
                file="assets/sfx/ding.wav",
                offset_sec=CONFIG.default_sfx_offset_sec,
                volume_db=CONFIG.default_sfx_volume_db,
            ),
        )

    return (
        ZoomSpec(
            type="none",
            scale_start=1.0,
            scale_end=1.0,
            focus_x=CONFIG.default_focus_x,
            focus_y=CONFIG.default_focus_y,
        ),
        None,
    )


def match_clips_to_script(
    clip_transcripts: List[Dict[str, Any]],
    target_script_lines: List[str],
    min_similarity: float = 0.52,
    enable_zoom: Optional[bool] = None,
    enable_transitions: Optional[bool] = None,
) -> Tuple[List[SegmentPlan], List[DroppedTake]]:
    """
    Precision Word-Window & Utterance Script Aligner.
    """
    dropped_takes: List[DroppedTake] = []
    clip_data_list: List[Dict[str, Any]] = []

    for clip_info in clip_transcripts:
        clip_path = clip_info["source_clip"]
        lang = clip_info.get("language", "en")
        raw_words = clip_info.get("words", [])

        clean_words, fumbles = filter_fumbles_in_words(raw_words)
        clean_words = [dict(w) for w in clean_words]
        for wi in range(1, len(clean_words)):
            prev_end_w = float(clean_words[wi - 1].get("end", 0.0))
            curr_start_w = float(clean_words[wi].get("start", 0.0))
            if curr_start_w < prev_end_w:
                mid_w = round(0.5 * (prev_end_w + curr_start_w), 3)
                clean_words[wi - 1]["end"] = mid_w
                clean_words[wi]["start"] = mid_w
        for fum in fumbles:
            dropped_takes.append(
                DroppedTake(
                    source_clip=clip_path,
                    start_sec=round(fum["start"], 3),
                    end_sec=round(fum["end"], 3),
                    spoken_text=fum["text"],
                    reason="fumble",
                )
            )

        utterances = group_words_into_utterances(clean_words)
        candidates: List[Dict[str, Any]] = []

        for idx in range(len(utterances)):
            for span_len in range(1, min(7, len(utterances) - idx + 1)):
                if idx + span_len > len(utterances):
                    continue
                sub_utts = utterances[idx : idx + span_len]
                merged_words: List[Dict[str, Any]] = []
                for u in sub_utts:
                    merged_words.extend(u["words"])
                if len(merged_words) > 40:
                    break
                merged_text = " ".join(_w_str(w) for w in merged_words)
                dual_toks = normalize_dual_tokens(merged_text)
                word_keys = {t[0] for t in dual_toks if t[0]} | {t[1] for t in dual_toks if t[1]}
                candidates.append({
                    "source_clip": clip_path,
                    "language": lang,
                    "utt_range": (idx, idx + span_len - 1),
                    "start": sub_utts[0]["start"],
                    "end": sub_utts[-1]["end"],
                    "text": merged_text,
                    "dual_tokens": dual_toks,
                    "word_keys": word_keys,
                    "words": merged_words,
                    "order_index": idx,
                    "is_full_clip_utt": span_len == len(utterances),
                })

        clip_data_list.append({
            "source_clip": clip_path,
            "language": lang,
            "clean_words": clean_words,
            "utterances": utterances,
            "candidates": candidates,
        })

    if not target_script_lines:
        auto_lines: List[str] = []
        for cd in clip_data_list:
            utts = cd["utterances"]
            i = 0
            while i < len(utts):
                curr = utts[i]
                if i + 1 < len(utts):
                    nxt = utts[i + 1]
                    sim = compute_dual_span_similarity(curr["dual_tokens"], nxt["dual_tokens"])
                    if sim >= 0.65:
                        dropped_takes.append(
                            DroppedTake(
                                source_clip=cd["source_clip"],
                                start_sec=round(curr["start"], 3),
                                end_sec=round(curr["end"], 3),
                                spoken_text=curr["text"],
                                reason="retake",
                            )
                        )
                        i += 1
                        continue
                auto_lines.append(curr["text"])
                i += 1
        target_script_lines = auto_lines

    all_candidates = [cand for cd in clip_data_list for cand in cd["candidates"]]
    script_dual_lines = [normalize_dual_tokens(line) for line in target_script_lines]

    use_zoom = CONFIG.enable_zoom if enable_zoom is None else bool(enable_zoom)
    use_transitions = CONFIG.enable_transitions if enable_transitions is None else bool(enable_transitions)

    segments: List[SegmentPlan] = []
    used_intervals: List[Tuple[str, float, float]] = []
    pending_retakes: List[Tuple[int, float, Dict[str, Any]]] = []
    last_matched_end_by_clip: Dict[str, float] = {}
    prev_clip: Optional[str] = None
    prev_end_sec: float = 0.0
    prev_zoom_type: str = "none"

    for line_idx, script_line in enumerate(target_script_lines):
        script_tokens = script_dual_lines[line_idx]
        if not script_tokens:
            continue

        script_keys = {t[0] for t in script_tokens if t[0]} | {t[1] for t in script_tokens if t[1]}
        len_script = len(script_tokens)
        min_len = max(1, int(len_script * 0.2))
        max_len = int(len_script * 4.0) + 4

        scored_matches: List[Tuple[float, float, Dict[str, Any]]] = []
        avail_cands: List[Dict[str, Any]] = []
        for cand in all_candidates:
            overlaps = any(
                c_path == cand["source_clip"] and not (cand["end"] <= s_start + 0.02 or cand["start"] >= s_end - 0.02)
                for c_path, s_start, s_end in used_intervals
            )
            if overlaps:
                continue
            avail_cands.append(cand)

            cand_len = len(cand["dual_tokens"])
            if cand_len < min_len or cand_len > max_len:
                continue

            cand_keys = cand.get("word_keys")
            if script_keys and cand_keys is not None and not (cand_keys & script_keys):
                continue

            raw_sim = compute_dual_span_similarity(cand["dual_tokens"], script_tokens)
            if raw_sim < min_similarity:
                continue

            last_end = last_matched_end_by_clip.get(cand["source_clip"], -1.0)
            chrono_bonus = 0.025 if cand["start"] >= last_end - 0.05 else -0.04
            retake_bonus = min(0.02, cand["order_index"] * 0.002)
            dedicated_clip_bonus = 0.008 if cand.get("is_full_clip_utt") else 0.0
            rank_score = raw_sim + chrono_bonus + retake_bonus + dedicated_clip_bonus
            scored_matches.append((rank_score, raw_sim, cand))

        if not scored_matches:
            continue

        scored_matches.sort(key=lambda x: x[0], reverse=True)
        _, best_raw_sim, best_cand = scored_matches[0]

        cand_words = best_cand.get("words", [])
        if len(cand_words) >= 4 and (best_raw_sim < 0.88 or len(cand_words) > len(script_tokens) * 1.25):
            target_len = len(script_tokens)
            min_k = max(2, target_len // 2)
            max_k = min(len(cand_words), int(target_len * 1.6) + 1)
            best_k = None
            best_prefix_sim = best_raw_sim
            for k in range(min_k, max_k):
                sub_w = cand_words[:k]
                sub_txt = " ".join(_w_str(w) for w in sub_w)
                s = compute_dual_span_similarity(normalize_dual_tokens(sub_txt), script_tokens)
                if s >= 0.75 and s > best_prefix_sim + 0.05:
                    best_prefix_sim = s
                    best_k = k
            if best_k is not None:
                sub_words = cand_words[:best_k]
                rem_words = cand_words[best_k:]
                sub_text = " ".join(_w_str(w) for w in sub_words)
                sub_dual = normalize_dual_tokens(sub_text)
                best_cand = {
                    "source_clip": best_cand["source_clip"],
                    "language": best_cand["language"],
                    "utt_range": best_cand.get("utt_range", (0, 0)),
                    "start": float(sub_words[0].get("start", best_cand["start"])),
                    "end": float(sub_words[-1].get("end", best_cand["end"])),
                    "text": sub_text,
                    "dual_tokens": sub_dual,
                    "word_keys": {t[0] for t in sub_dual if t[0]} | {t[1] for t in sub_dual if t[1]},
                    "words": sub_words,
                    "order_index": best_cand.get("order_index", 0),
                    "is_full_clip_utt": False,
                }
                best_raw_sim = best_prefix_sim
                if rem_words:
                    rem_text = " ".join(_w_str(w) for w in rem_words)
                    rem_dual = normalize_dual_tokens(rem_text)
                    rem_cand = {
                        "source_clip": best_cand["source_clip"],
                        "language": best_cand["language"],
                        "utt_range": best_cand.get("utt_range", (0, 0)),
                        "start": float(rem_words[0].get("start", sub_words[-1].get("end", 0.0))),
                        "end": float(rem_words[-1].get("end", best_cand["end"])),
                        "text": rem_text,
                        "dual_tokens": rem_dual,
                        "word_keys": {t[0] for t in rem_dual if t[0]} | {t[1] for t in rem_dual if t[1]},
                        "words": rem_words,
                        "order_index": best_cand.get("order_index", 0) + 1,
                        "is_full_clip_utt": False,
                    }
                    all_candidates.append(rem_cand)

        chosen_cands: List[Tuple[float, Dict[str, Any]]] = [(best_raw_sim, best_cand)]
        chosen_id_set = {(c["source_clip"], c["start"], c["end"]) for _, c in chosen_cands}

        for _, alt_raw_sim, alt_cand in scored_matches[1:]:
            if (alt_cand["source_clip"], alt_cand["start"], alt_cand["end"]) in chosen_id_set:
                continue
            if alt_cand["utt_range"][0] == alt_cand["utt_range"][1] and alt_raw_sim >= 0.72:
                overlaps_best = any(
                    alt_cand["source_clip"] == ch_c["source_clip"]
                    and not (alt_cand["end"] <= ch_c["start"] or alt_cand["start"] >= ch_c["end"])
                    for _, ch_c in chosen_cands
                )
                if not overlaps_best:
                    pending_retakes.append((line_idx, alt_raw_sim, alt_cand))

        for sub_sim, sub_cand in chosen_cands:
            used_intervals.append((sub_cand["source_clip"], sub_cand["start"], sub_cand["end"]))
            last_matched_end_by_clip[sub_cand["source_clip"]] = sub_cand["end"]

            cand_words = [dict(w) for w in sub_cand["words"]]
            if len(cand_words) >= 2:
                first_gap = float(cand_words[1].get("start", 0.0)) - float(cand_words[0].get("end", 0.0))
                first_dur = float(cand_words[0].get("end", 0.0)) - float(cand_words[0].get("start", 0.0))
                if first_gap >= 0.8 and first_dur < 0.25:
                    new_w0_end = round(max(float(cand_words[0].get("end", 0.0)), float(cand_words[1].get("start", 0.0)) - 0.12), 3)
                    new_w0_start = round(max(float(cand_words[0].get("start", 0.0)), new_w0_end - 0.35), 3)
                    cand_words[0]["start"] = new_w0_start
                    cand_words[0]["end"] = new_w0_end

            max_internal_gap = 1.6 if ("repeat" in script_line.lower() or "రిపీట్" in script_line.lower()) else 1.0
            word_groups: List[List[Dict[str, Any]]] = []
            cur_group: List[Dict[str, Any]] = []
            for wi, w in enumerate(cand_words):
                cur_group.append(w)
                if wi + 1 < len(cand_words):
                    gap_w = float(cand_words[wi + 1].get("start", 0.0)) - float(w.get("end", 0.0))
                    left_dur = float(cur_group[-1].get("end", 0.0)) - float(cur_group[0].get("start", 0.0))
                    rem_words = cand_words[wi + 1 :]
                    right_dur = float(rem_words[-1].get("end", 0.0)) - float(rem_words[0].get("start", 0.0))
                    if gap_w >= max_internal_gap and len(cur_group) >= 2 and left_dur >= 0.8 and len(rem_words) >= 2 and right_dur >= 0.8:
                        word_groups.append(cur_group)
                        cur_group = []
            if cur_group:
                word_groups.append(cur_group)

            for wg in word_groups:
                wg_start = float(wg[0].get("start", sub_cand["start"]))
                wg_end = float(wg[-1].get("end", sub_cand["end"]))
                wg_text = " ".join(_w_str(w) for w in wg)

                same_clip = prev_clip == sub_cand["source_clip"]
                time_gap_sec = (wg_start - prev_end_sec) if (same_clip and len(segments) > 0) else 0.0
                transition_spec = assign_transition_spec(
                    seg_idx=len(segments),
                    same_clip_as_prev=same_clip,
                    time_gap_sec=time_gap_sec,
                    enable_transitions=use_transitions,
                )
                zoom_spec, sfx_spec = assign_zoom_and_sfx(
                    seg_idx=len(segments),
                    total_segments=len(target_script_lines),
                    script_line=script_line,
                    same_clip_as_prev=same_clip,
                    prev_zoom_type=prev_zoom_type,
                    enable_zoom=use_zoom,
                )

                word_models = [
                    WordTimestamp(
                        word=_w_str(w),
                        start=float(w.get("start", 0.0)),
                        end=float(w.get("end", 0.0)),
                        confidence=float(w.get("confidence", w.get("p", 0.95) or 0.95)),
                        onset_confidence=w.get("onset_confidence"),
                        raw_ctc_start=w.get("raw_ctc_start"),
                    )
                    for w in wg
                ]

                segments.append(
                    SegmentPlan(
                        id=len(segments) + 1,
                        source_clip=sub_cand["source_clip"],
                        script_line=script_line,
                        spoken_text=wg_text,
                        language=sub_cand.get("language", "en"),
                        start_sec=round(wg_start, 3),
                        end_sec=round(wg_end, 3),
                        take_score=round(min(1.0, sub_sim), 3),
                        zoom=zoom_spec,
                        transition=transition_spec,
                        sfx=sfx_spec,
                        words=word_models,
                    )
                )
                prev_clip = sub_cand["source_clip"]
                prev_end_sec = wg_end
                prev_zoom_type = zoom_spec.type

    for line_idx, alt_raw_sim, alt_cand in pending_retakes:
        already_used = any(
            c_path == alt_cand["source_clip"]
            and not (alt_cand["end"] <= s_start + 0.02 or alt_cand["start"] >= s_end - 0.02)
            for c_path, s_start, s_end in used_intervals
        )
        if not already_used:
            dropped_takes.append(
                DroppedTake(
                    source_clip=alt_cand["source_clip"],
                    start_sec=round(alt_cand["start"], 3),
                    end_sec=round(alt_cand["end"], 3),
                    spoken_text=alt_cand["text"],
                    reason="retake",
                )
            )
            used_intervals.append((alt_cand["source_clip"], alt_cand["start"], alt_cand["end"]))

    for cd in clip_data_list:
        for utt in cd["utterances"]:
            is_covered = any(
                c_path == cd["source_clip"] and not (utt["end"] <= s_start + 0.05 or utt["start"] >= s_end - 0.05)
                for c_path, s_start, s_end in used_intervals
            )
            if not is_covered:
                dropped_takes.append(
                    DroppedTake(
                        source_clip=cd["source_clip"],
                        start_sec=round(utt["start"], 3),
                        end_sec=round(utt["end"], 3),
                        spoken_text=utt["text"],
                        reason="off_script",
                    )
                )

    dropped_takes.sort(key=lambda d: (d.source_clip, d.start_sec))
    return segments, dropped_takes
