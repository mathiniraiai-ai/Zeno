#!/usr/bin/env python3
"""Script timeline validator, candidate line mapper, and pre-render safety gate.

The script is the master timeline. From the aligned words inside each cut, this module measures:
- Which script line each segment delivers, and whether the plan follows script order.
- Missing, repeated, unproven, or out-of-order script lines.
- Word-boundary grazes, mid-word cuts, and unscripted stumbles/restarts (intra-cut and cross-cut).
- Speaker voice consistency (`speaker_check`) and story-driven transition discipline (`formats/registry.json`).
- Candidate clip stretches for every script line (`line_map.md`).

`full_gate()` runs during `--check-plan` and before every render. Any open blocker stops execution until fixed in
`plan.edl` or explicitly accepted in `work/<project>/decisions.txt` (`accept <id> <reason>`).
"""
from __future__ import annotations

import json
import re
import sys
from collections import Counter
from difflib import SequenceMatcher
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

SCRIPT_DIR = Path(__file__).resolve().parent
ROOT = SCRIPT_DIR.parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

WORD_MATCH = 0.64          # Minimum similarity to treat two tokens as the same word
FIT_MIN = 0.40             # Minimum F1 / part-fit to prove a segment speaks a line
TIE = 0.08                 # Fit margin for ambiguous repeated lines in a script
HEARD_MIN = 0.50           # Minimum word recall for a script line to count as present
STRONG = 0.55              # Minimum fit to flag an unlabelled cut as out of order from words alone
TIGHT_JOIN_SEC = 0.18      # Max combined tail+lead silence across a dialogue join before reporting
TIGHT_PAUSE_SEC = 0.25     # Max internal pause inside a single-frame cut before reporting
MARK_TAIL_MAX_SEC = 0.48   # Max tail after a mark onset before reporting
RAPID_ZOOM_STYLES = ("sudden_in", "flash_zoom")
MAX_RAPID_PER_MIN = 16     # Max rapid zooms per minute of full-frame dialogue
AD_MIN_MOVING_SHARE = 0.60 # Target moving share for full-frame cuts (>= 1.0s)
MIN_STILL_SHARE = 0.25     # Minimum share of single-speaker cuts (>= 1.5s) kept still for contrast
SNAP_REACH_SEC = 0.12      # Max distance a cut boundary may snap to reach noise floor
EDGE_GRAZE_MAX_SEC = 0.08  # Max word-edge graze auto-nudged into adjacent silence
REPEAT_RECALL = 0.80       # Recall threshold for two cuts independently speaking a full line
SRC_SWAP_MIN_SEC = 1.0     # Minimum backward jump in the same source clip to flag swapped dialogue

VARIETY_MAX_SHARE = 0.40
VARIETY_MIN_USES = 3
TR_VARIETY_MIN_USES = 3
TR_VARIETY_MAX_SHARE = 0.50

STUTTER_SIM_MIN = 0.82
STUTTER_GAP_MAX_SEC = 1.5
RESTART_AVG_SIM_MIN = 0.80
RESTART_MIN_WORD_SIM = 0.68
RESTART_GAP_MAX_SEC = 6.0
RESTART_GAP_MAX_TOKENS = 10
MAX_REPEAT_NGRAM = 6

# Registry path: segregated format registry in Zeno
REGISTRY_FILE = ROOT / "formats" / "registry.json"
SPLIT_FAMILY = ("split_h", "split_v", "top_pip", "video_call", "chat_bar")

_DEFAULT_REGISTRY: Dict[str, Any] = {
    "formats": [
        {
            "id": "skit",
            "title": "Skit based micro narrative",
            "names": ["skit", "skit based"],
            "fallback_names": ["micro narrative", "micronarrative", "macro narrative"],
            "layouts": ["single", "split_h", "split_v", "top_pip"],
            "max_transitions": None,
            "kinds": [
                "whip_left", "whip_right", "slide_up", "slide_down", "split_reveal",
                "dip_to_black", "dissolve", "fade", "zoom_burst",
                "zoom_in", "zoom_out", "zoom_blur", "warm_flash_punch",
            ],
        },
        {
            "id": "direct_hook",
            "title": "Direct hook micro narrative",
            "names": ["direct hook"],
            "layouts": ["single", "split_h", "split_v"],
            "max_transitions": 4,
            "kinds": [
                "split_reveal", "whip_left", "whip_right", "slide_up", "slide_down",
                "zoom_in", "zoom_out", "zoom_blur", "dip_to_black", "warm_flash_punch",
            ],
        },
        {
            "id": "street_interview",
            "title": "Street interview micro narrative",
            "names": ["street interview", "public interview", "vox pop", "asking strangers"],
            "layouts": ["single", "split_h", "split_v"],
            "max_transitions": 4,
            "kinds": [
                "split_reveal", "whip_left", "whip_right", "slide_up", "slide_down",
                "zoom_in", "zoom_out", "zoom_blur", "dip_to_black",
            ],
        },
        {
            "id": "stop_saying",
            "title": "Stop saying / Never say micro narrative",
            "names": ["stop saying", "never say"],
            "layouts": ["single", "split_h", "split_v"],
            "max_transitions": 5,
            "kinds": [
                "split_reveal", "whip_left", "whip_right", "slide_up", "slide_down",
                "zoom_in", "zoom_out", "zoom_blur", "dip_to_black", "warm_flash_punch",
            ],
        },
        {
            "id": "live_conversation",
            "title": "Live conversation",
            "names": ["live conversation", "live grammar correction", "grammar correction", "hologram", "tablet", "interactive"],
            "layouts": ["single", "video_call"],
            "max_transitions": None,
            "kinds": [
                "whip_left", "whip_right", "slide_up", "slide_down", "dip_to_black",
                "dissolve", "fade", "zoom_burst", "zoom_in", "zoom_out", "zoom_blur",
                "warm_flash_punch",
            ],
        },
        {
            "id": "story_pitch",
            "title": "Story based direct pitch",
            "names": ["story pitch", "story based", "direct pitch", "story"],
            "layouts": ["single"],
            "max_transitions": None,
            "kinds": [
                "dissolve", "fade", "dip_to_black", "whip_left", "whip_right",
                "slide_up", "slide_down", "zoom_in", "zoom_out", "zoom_blur",
                "warm_flash_punch",
            ],
        },
        {
            "id": "split_screen",
            "title": "Split screen",
            "names": ["split screen", "video call"],
            "layouts": ["split_h", "split_v", "video_call", "chat_bar"],
            "max_transitions": 4,
            "kinds": [
                "split_reveal", "whip_left", "whip_right", "slide_up", "slide_down",
                "zoom_in", "zoom_out", "zoom_blur", "dip_to_black",
            ],
        },
        {
            "id": "scrolling_text",
            "title": "Scrolling text",
            "names": ["scrolling text", "scroll page", "scrolling"],
            "layouts": ["split_h"],
            "max_transitions": 0,
            "kinds": [],
        },
        {
            "id": "translate_drill",
            "title": "How would you translate / flashcard drill (solo & split screen)",
            "names": [
                "translate drill", "how would you translate", "translate",
                "flash card drill", "flashcard drill", "flashcard", "flash card",
                "split screen with flash card", "split screen with flash cards",
                "flash card split", "flashcard split",
            ],
            "layouts": ["single", "split_h", "split_v", "chat_bar"],
            "max_transitions": 3,
            "kinds": [
                "split_reveal", "whip_left", "whip_right", "slide_up", "slide_down",
                "zoom_in", "zoom_out", "dip_to_black",
            ],
        },
        {
            "id": "realistic_to_3d",
            "title": "Realistic video to 3D",
            "names": ["realistic to 3d", "realistic video to 3d", "live to 3d"],
            "layouts": ["single", "split_h", "split_v"],
            "max_transitions": None,
            "kinds": [
                "whip_left", "whip_right", "slide_up", "slide_down", "split_reveal",
                "dip_to_black", "dissolve", "fade", "zoom_burst",
                "zoom_in", "zoom_out", "zoom_blur", "warm_flash_punch",
            ],
        },
        {
            "id": "3d_to_2d",
            "title": "3D to 2D",
            "names": ["3d to 2d"],
            "generation_only": True,
            "layouts": ["single", "split_h", "split_v"],
            "max_transitions": None,
            "kinds": [],
        },
    ],
    "flash_kinds": ["warm_flash", "warm_flash_punch", "dip_to_white", "flash_zoom"],
    "same_shot_structure": 0.70,
}

# Common natural 2x reduplications across English, Kannada, Hindi, Tamil, Telugu, and Malayalam
NATURAL_REDUPLICATIONS = {
    # English
    "very", "had", "that", "bye", "no", "yes", "ha", "haha", "ok", "okay",
    "so", "really", "many", "long", "far", "step", "day", "little", "slowly",
    "wait", "blah", "knock", "ring", "tick",
    # Kannada
    "ಬೇಗ", "ಮತ್ತೆ", "ಪದೇ", "ನಿಧಾನ", "ನಿಧಾನವಾಗಿ", "ಸ್ವಲ್ಪ", "ಚಿಕ್ಕ", "ದೊಡ್ಡ",
    "ಹೊಸ", "ಬೇರೆ", "ಸಣ್ಣ", "ಜೋರಾಗಿ", "ಒಂದೊಂದೇ", "ಆಗಾಗ", "ಹೌದು", "ಬನ್ನಿ", "ಹೋಗಿ",
    # Hindi
    "धीरे", "बार", "जल्दी", "थोड़ा", "थोड़ी", "छोटे", "बड़े", "अलग", "साथ",
    "कभी", "आगे", "पीछे", "हाँ", "नहीं", "चलो", "रुको", "सच",
    # Tamil
    "மெல்ல", "வேகமாக", "கொஞ்சம்", "சின்ன", "பெரிய", "தனி", "அடிக்கடி", "திரும்ப", "சரி",
    # Telugu
    "మెల్లగా", "త్వరగా", "కొంచెం", "చిన్న", "పెద్ద", "మళ్ళీ", "వేరు", "సరే",
    # Malayalam
    "പതുക്കെ", "വേഗം", "കുറച്ച്", "ചെറിയ", "വലിയ", "വീണ്ടും", "വേറെ", "ശരി",
}

_EN_STOP_BIGRAM_WORDS = {
    "in", "on", "at", "to", "of", "for", "by", "the", "a", "an", "and", "or",
}

_STUTTER_FILLERS = {"uh", "um", "er", "erm", "ah", "hmm", "like"}

_MID_CLAUSE_CONJUNCTIONS = {
    "and", "but", "or", "so", "because", "if", "when", "while", "that", "which",
    "who", "where", "although", "though", "unless", "until", "since", "then",
    "than", "as", "with", "for", "to", "of", "in", "on", "at", "from", "into",
    # Common Indic mid-clause conjunctions
    "और", "लेकिन", "क्योंकि", "तो", "कि", "या", "पर",
    "ಮತ್ತು", "ಆದರೆ", "ಅಥವಾ", "ಯಾಕೆಂದರೆ", "ಅಂದ್ರೆ",
    "மற்றும்", "ஆனால்", "அல்லது", "ஏனென்றால்",
    "మరియు", "కానీ", "లేదా", "ఎందుకంటే",
    "കൂടാതെ", "പക്ഷേ", "അല്ലെങ്കിൽ",
}

_INDIC_LONG_TO_SHORT_MATRAS = str.maketrans({
    # Devanagari
    "\u0940": "\u093F", "\u0942": "\u0941",
    # Bengali
    "\u09C0": "\u09BF", "\u09C2": "\u09C1",
    # Gujarati
    "\u0AC0": "\u0ABF", "\u0AC2": "\u0AC1",
    # Oriya
    "\u0B40": "\u0B3F", "\u0B42": "\u0B41",
    # Tamil
    "\u0BC0": "\u0BBF", "\u0BC2": "\u0BC1", "\u0BC7": "\u0BC6", "\u0BCB": "\u0BCA",
    # Telugu
    "\u0C40": "\u0C3F", "\u0C42": "\u0C41", "\u0C47": "\u0C46", "\u0C4B": "\u0C4A",
    # Kannada
    "\u0CC0": "\u0CBF", "\u0CC2": "\u0CC1", "\u0CC7": "\u0CC6", "\u0CCB": "\u0CCA",
    # Malayalam
    "\u0D40": "\u0D3F", "\u0D42": "\u0D41", "\u0D47": "\u0D46", "\u0D4B": "\u0D4A",
})

_INDIC_VOWEL_SIGN_RE = re.compile(
    r"^.(?:[\u093E-\u094C\u09BE-\u09CC\u0ABE-\u0ACC\u0B3E-\u0B4C\u0BBE-\u0BCC\u0C3E-\u0C4C\u0CBE-\u0CCC\u0D3E-\u0D4C])?"
)
_FALLBACK_CLEAN_RE = re.compile(r"[^\w\u0900-\u0D7F']+", re.UNICODE)

_ACTIVE_WORK_DIR: Optional[Path] = None


def _fallback_clean_token(word: str) -> str:
    return _FALLBACK_CLEAN_RE.sub("", str(word or "").strip().lower()).strip("'_")


@lru_cache(maxsize=200000)
def _sim(a: str, b: str) -> float:
    if a == b:
        return 1.0
    if not a or not b:
        return 0.0
    try:
        from prepare import _word_similarity

        return float(_word_similarity(a, b))
    except Exception:
        return SequenceMatcher(None, a, b).ratio()


def clean(words: List[str]) -> List[str]:
    try:
        from prepare import _clean_token

        cleaner = _clean_token
    except Exception:
        cleaner = _fallback_clean_token
    return [t for t in (cleaner(w) for w in words) if t]


def _norm_indic_len(s: str) -> str:
    s2 = s.translate(_INDIC_LONG_TO_SHORT_MATRAS)
    return re.sub(r"[\u0955\u09D7\u0B56\u0B57\u0BD7\u0C55\u0C56\u0CD5\u0CD6\u0D57]", "", s2)


def _first_akshar(s: str) -> str:
    if not s:
        return ""
    m = _INDIC_VOWEL_SIGN_RE.match(s)
    return m.group(0) if m else s[0]


@lru_cache(maxsize=200000)
def _repeat_tok_sim(a: str, b: str) -> float:
    """High-precision token similarity for repeat/stutter detection without minimal-pair false positives."""
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    if a.isascii() and b.isascii():
        if a.endswith("-"):
            stem = a.rstrip("-").lower()
            b_low = b.rstrip("-").lower()
            if len(stem) >= 2 and stem.isalpha() and b_low.startswith(stem) and len(b_low) >= len(stem):
                return 0.90
        if max(len(a), len(b)) <= 4:
            return 0.0
        if a[:3] != b[:3]:
            return 0.0
        return SequenceMatcher(None, a, b).ratio()
    na, nb = _norm_indic_len(a), _norm_indic_len(b)
    if na == nb:
        return 1.0
    if _first_akshar(na) != _first_akshar(nb):
        return 0.0
    seq_r = SequenceMatcher(None, na, nb).ratio()
    if max(len(na), len(nb)) <= 4:
        return seq_r
    return max(seq_r, _sim(a, b) if na[:2] == nb[:2] else 0.0)


def _ngram_occurrences(gram: List[str], toks: List[str]) -> int:
    """Count non-overlapping occurrences of `gram` inside `toks` using repeat-grade similarity."""
    k, n = len(gram), len(toks)
    if k == 0 or n < k:
        return 0
    cnt, idx = 0, 0
    while idx <= n - k:
        sims = [_repeat_tok_sim(gram[m], toks[idx + m]) for m in range(k)]
        ok = (
            sims[0] >= STUTTER_SIM_MIN
            if k == 1
            else (min(sims) >= RESTART_MIN_WORD_SIM and (sum(sims) / k) >= RESTART_AVG_SIM_MIN)
        )
        if ok:
            cnt += 1
            idx += k
        else:
            idx += 1
    return cnt


def _repeat_tokens(words: List[str]) -> List[str]:
    """Normalize common spoken contractions for repeat comparison without changing alignment timestamps."""
    out: List[str] = []
    for raw_w in words:
        s_raw = str(raw_w or "").strip()
        s_strip = s_raw.rstrip(".,!?;:\"'”’)]")
        is_trunc = s_strip.endswith("-") and not s_strip.endswith("--") and len(s_strip.rstrip("-")) >= 2
        cleaned = clean([s_raw])
        for idx_c, token in enumerate(cleaned):
            if is_trunc and idx_c == len(cleaned) - 1 and token.isascii() and token.isalpha() and len(token) >= 2:
                out.append(token + "-")
            else:
                for part in {"gotta": ("got", "to")}.get(token, (token,)):
                    out.append(part)
    return out


def find_unscripted_repeats(
    words: List[Dict[str, Any]],
    script_line_text: Union[str, List[str], None] = "",
) -> List[Dict[str, Any]]:
    """Detect unscripted 1-word stutters and 2-6 word restarted/repeated n-grams in `words`."""
    items: List[Tuple[str, Dict[str, Any]]] = []
    for w in words or []:
        if not isinstance(w, dict):
            continue
        raw_txt = str(w.get("w") or w.get("word") or w.get("text") or "").strip()
        for t in _repeat_tokens(raw_txt.split()):
            items.append((t, w))
    n = len(items)
    if n < 2:
        return []

    if isinstance(script_line_text, (list, tuple)):
        ref_toks = _repeat_tokens([str(x) for x in script_line_text])
    else:
        ref_toks = _repeat_tokens(str(script_line_text or "").split())

    toks = [t for t, _ in items]
    found: List[Dict[str, Any]] = []
    covered_pairs: List[Tuple[int, int, int, int]] = []

    # 1) 2..6-word restarted/repeated n-grams (longest first to suppress sub-gram duplicates)
    for k in range(min(MAX_REPEAT_NGRAM, n // 2), 1, -1):
        i = 0
        while i <= n - 2 * k:
            gram = toks[i : i + k]
            p_max = min(n - k, i + k + RESTART_GAP_MAX_TOKENS)
            matched_p: Optional[int] = None
            for p in range(i + k, p_max + 1):
                if k == 2 and (p - (i + k)) >= 2 and all(t in _EN_STOP_BIGRAM_WORDS for t in gram):
                    continue
                sims = [_repeat_tok_sim(gram[m], toks[p + m]) for m in range(k)]
                if min(sims) >= RESTART_MIN_WORD_SIM and (sum(sims) / k) >= RESTART_AVG_SIM_MIN:
                    gap_sec = float(items[p][1].get("start", 0.0)) - float(items[i + k - 1][1].get("end", 0.0))
                    if gap_sec <= RESTART_GAP_MAX_SEC:
                        matched_p = p
                        break
            if matched_p is not None:
                already = any(
                    a1 <= i and i + k <= b1 and a2 <= matched_p and matched_p + k <= b2
                    for a1, b1, a2, b2 in covered_pairs
                )
                if not already:
                    spoken_cnt = _ngram_occurrences(gram, toks)
                    script_cnt = _ngram_occurrences(gram, ref_toks) if ref_toks else 0
                    if spoken_cnt > script_cnt:
                        covered_pairs.append((i, i + k, matched_p, matched_p + k))
                        f_start = round(float(items[i][1].get("start", 0.0)), 3)
                        f_end = round(float(items[i + k - 1][1].get("end", 0.0)), 3)
                        s_start = round(float(items[matched_p][1].get("start", 0.0)), 3)
                        s_end = round(float(items[matched_p + k - 1][1].get("end", 0.0)), 3)
                        prev_end = float(items[matched_p - 1][1].get("end", f_end))
                        trim_start = round(min(s_start, max(prev_end, s_start - 0.04)), 3)
                        found.append({
                            "kind": "restart",
                            "phrase": " ".join(gram),
                            "size": k,
                            "first_start": f_start,
                            "first_end": f_end,
                            "second_start": s_start,
                            "second_end": s_end,
                            "clean_trim_start": trim_start,
                            "first_idx": i,
                            "second_idx": matched_p,
                        })
                        i = matched_p
                        continue
            i += 1

    # 2) Immediate 1-word stutters (excluding scripted doubles and natural reduplications)
    immediate_stutter_idxs: set = set()
    for i in range(n - 1):
        if any((a1 <= i and i + 1 <= b1) or (a2 <= i and i + 1 <= b2) for a1, b1, a2, b2 in covered_pairs):
            continue
        t1, w1 = items[i]
        t2, w2 = items[i + 1]
        if w1 is w2:
            continue
        gap_sec = float(w2.get("start", 0.0)) - float(w1.get("end", 0.0))
        if gap_sec >= STUTTER_GAP_MAX_SEC:
            continue
        if _repeat_tok_sim(t1, t2) < STUTTER_SIM_MIN:
            continue
        is_trunc_stutter = t1.endswith("-")
        ref_t = t2 if is_trunc_stutter else t1
        spoken_pairs = sum(
            1
            for z in range(n - 1)
            if _repeat_tok_sim(toks[z], ref_t) >= STUTTER_SIM_MIN and _repeat_tok_sim(toks[z + 1], ref_t) >= STUTTER_SIM_MIN
        )
        script_pairs = sum(
            1
            for z in range(max(0, len(ref_toks) - 1))
            if _repeat_tok_sim(ref_toks[z], ref_t) >= STUTTER_SIM_MIN and _repeat_tok_sim(ref_toks[z + 1], ref_t) >= STUTTER_SIM_MIN
        )
        extra_allowed = 0 if is_trunc_stutter else (1 if (t1 in NATURAL_REDUPLICATIONS or t2 in NATURAL_REDUPLICATIONS) else 0)
        if spoken_pairs > script_pairs + extra_allowed:
            f_start = round(float(w1.get("start", 0.0)), 3)
            f_end = round(float(w1.get("end", 0.0)), 3)
            s_start = round(float(w2.get("start", 0.0)), 3)
            s_end = round(float(w2.get("end", 0.0)), 3)
            trim_start = round(min(s_start, max(f_end, s_start - 0.04)), 3)
            immediate_stutter_idxs.add(i)
            found.append({
                "kind": "stutter",
                "phrase": f"{t1} {t2}" if is_trunc_stutter else t1,
                "size": 1,
                "first_start": f_start,
                "first_end": f_end,
                "second_start": s_start,
                "second_end": s_end,
                "clean_trim_start": trim_start,
                "first_idx": i,
                "second_idx": i + 1,
            })

    # 3) Filler-separated 1-word stutters (e.g. "I uh I think", "so um so", "th- uh the")
    for i in range(n - 2):
        if any((a1 <= i and i + 2 <= b1) or (a2 <= i and i + 2 <= b2) for a1, b1, a2, b2 in covered_pairs):
            continue
        if i in immediate_stutter_idxs or (i + 1) in immediate_stutter_idxs:
            continue
        t1, w1 = items[i]
        t_mid, w_mid = items[i + 1]
        t3, w3 = items[i + 2]
        if w1 is w3 or t_mid not in _STUTTER_FILLERS:
            continue
        if _repeat_tok_sim(t1, t_mid) >= STUTTER_SIM_MIN:
            continue
        if _repeat_tok_sim(t1, t3) < STUTTER_SIM_MIN:
            continue
        gap_1 = float(w_mid.get("start", 0.0)) - float(w1.get("end", 0.0))
        gap_2 = float(w3.get("start", 0.0)) - float(w_mid.get("end", 0.0))
        total_gap = float(w3.get("start", 0.0)) - float(w1.get("end", 0.0))
        if gap_1 >= STUTTER_GAP_MAX_SEC or gap_2 >= STUTTER_GAP_MAX_SEC or total_gap >= 2.0 * STUTTER_GAP_MAX_SEC:
            continue
        ref_t = t3 if t1.endswith("-") else t1
        spoken_triples = sum(
            1
            for z in range(n - 2)
            if toks[z + 1] in _STUTTER_FILLERS
            and _repeat_tok_sim(toks[z], ref_t) >= STUTTER_SIM_MIN
            and _repeat_tok_sim(toks[z + 2], ref_t) >= STUTTER_SIM_MIN
        )
        script_triples = sum(
            1
            for z in range(max(0, len(ref_toks) - 2))
            if ref_toks[z + 1] in _STUTTER_FILLERS
            and _repeat_tok_sim(ref_toks[z], ref_t) >= STUTTER_SIM_MIN
            and _repeat_tok_sim(ref_toks[z + 2], ref_t) >= STUTTER_SIM_MIN
        )
        if spoken_triples > script_triples:
            f_start = round(float(w1.get("start", 0.0)), 3)
            f_end = round(float(w1.get("end", 0.0)), 3)
            s_start = round(float(w3.get("start", 0.0)), 3)
            s_end = round(float(w3.get("end", 0.0)), 3)
            mid_end = float(w_mid.get("end", f_end))
            trim_start = round(min(s_start, max(mid_end, s_start - 0.04)), 3)
            found.append({
                "kind": "stutter",
                "phrase": f"{t1} {t_mid} {t3}",
                "size": 1,
                "first_start": f_start,
                "first_end": f_end,
                "second_start": s_start,
                "second_end": s_end,
                "clean_trim_start": trim_start,
                "first_idx": i,
                "second_idx": i + 2,
            })

    found.sort(key=lambda d: (d["first_start"], d["second_start"]))
    return found


def _hits(need: List[str], have: List[str]) -> float:
    """Share of tokens in `need` matched in `have`."""
    if not need or not have:
        return 0.0
    pool = set(have)
    found = 0
    for t in need:
        if t in pool or max(_sim(t, h) for h in pool) >= WORD_MATCH:
            found += 1
    return found / len(need)


def fit(tokens: List[str], line_tokens: List[str]) -> float:
    """Word-level F1 score between a spoken slice and a script line."""
    rec, prec = _hits(line_tokens, tokens), _hits(tokens, line_tokens)
    return 0.0 if rec + prec == 0 else 2 * rec * prec / (rec + prec)


def recall(tokens: List[str], line_tokens: List[str]) -> float:
    """Share of `line_tokens` heard in `tokens`."""
    return _hits(line_tokens, tokens)


def part_fit(tokens: List[str], line_tokens: List[str]) -> float:
    """Fit score allowing a clean sub-cut (e.g. L7a / L7b) of a longer script line."""
    f = fit(tokens, line_tokens)
    if len(tokens) >= 3 and len(tokens) < 0.8 * len(line_tokens):
        return max(f, 0.95 * _hits(tokens, line_tokens))
    return f


def place_in_script(tokens: List[str], lines: List[Dict[str, Any]]) -> Tuple[Optional[int], int, float]:
    """Return `(line_index, word_offset, best_fit)` where `tokens` begins in `lines`."""
    if not tokens:
        return None, 0, 0.0
    scored = [(fit(tokens, l.get("tokens") or []), l) for l in lines if l.get("tokens")]
    if not scored:
        return None, 0, 0.0
    best_f, best_l = max(scored, key=lambda x: x[0])
    if best_f < 0.3:
        return None, 0, best_f
    by_no = {int(l["index"]): l for l in lines if l.get("tokens")}
    start = best_l
    for back in range(1, 4):
        prev = by_no.get(int(best_l["index"]) - back)
        if prev is None:
            continue
        lt = prev["tokens"]
        if len(lt) >= 3 and recall(tokens[: len(lt) + 2], lt) >= 0.7 and fit(tokens[: len(lt) + 2], lt) >= 0.6:
            start = prev
    offset = 0
    head = tokens[: min(3, len(tokens))]
    for i, t in enumerate(start["tokens"]):
        if any(_sim(t, h) >= WORD_MATCH for h in head):
            offset = i
            break
    return int(start["index"]), offset, best_f


POST_WORD_BEAT_DURATION: float = 0.48


def _line_ids_of(seg_id: str) -> List[str]:
    """Extract script line IDs from a segment ID: `L6_L7_L8a` -> `['L6', 'L7', 'L8']`, `L14b` -> `['L14']`."""
    return re.findall(r"(?<![A-Za-z0-9])L\d+", str(seg_id or "").replace("_", " "))


def _line_id_of(seg_id: str) -> str:
    ids = _line_ids_of(seg_id)
    return ids[0] if ids else ""


def _seg_lines(seg_id: str) -> List[str]:
    """Resolve segment ID (`L1`, `L1b`, `L6_L8`, `L6_L7_L8a`) into ordered unique script line IDs."""
    s = str(seg_id or "").strip()
    m_range = re.match(r"^L(\d+)[a-z]*(?:_|-|\.\.)L(\d+)[a-z]*$", s, re.IGNORECASE)
    if m_range:
        a, b = int(m_range.group(1)), int(m_range.group(2))
        if a <= b and (b - a) <= 40:
            return [f"L{k}" for k in range(a, b + 1)]
    seen: List[str] = []
    for lid in _line_ids_of(s):
        up = lid.upper()
        if up not in seen:
            seen.append(up)
    return seen


def _segment_hold_tail(seg: Optional[Dict[str, Any]]) -> float:
    """Return frozen post-speech tail duration (seconds) via `common.segment_hold_tail` with fallback."""
    import common as _common

    fn = getattr(_common, "segment_hold_tail", None)
    if callable(fn):
        return float(fn(seg))
    if not isinstance(seg, dict):
        return 0.0
    try:
        return max(0.0, float(seg.get("hold_tail") or seg.get("hold") or 0.0))
    except (TypeError, ValueError):
        return 0.0


def _segment_speed(seg: Optional[Dict[str, Any]]) -> float:
    """Return clamped playback speed factor (`common.segment_speed` with safe fallback)."""
    import common as _common

    fn = getattr(_common, "segment_speed", None)
    if callable(fn):
        try:
            return float(fn(seg))
        except Exception:
            pass
    if not isinstance(seg, dict):
        return 1.0
    try:
        val = float(seg.get("speed") if seg.get("speed") is not None else 1.0)
    except (TypeError, ValueError):
        return 1.0
    if val <= 0.0:
        return 1.0
    return max(0.15, min(4.0, val))


def _segment_is_muted(seg: Optional[Dict[str, Any]]) -> bool:
    """Return True when segment explicitly mutes source audio (`common.segment_is_muted` with safe fallback)."""
    import common as _common

    fn = getattr(_common, "segment_is_muted", None)
    if callable(fn):
        try:
            return bool(fn(seg))
        except Exception:
            pass
    if not isinstance(seg, dict):
        return False
    if bool(seg.get("mute")):
        return True
    am = str(seg.get("audio_mode") or seg.get("audio") or "").strip().lower()
    return am in ("mute", "off", "none")


def _segment_is_beat(seg: Optional[Dict[str, Any]]) -> bool:
    """Return True for non-spoken visual beat segments (`common.segment_is_beat` with safe fallback)."""
    import common as _common

    fn = getattr(_common, "segment_is_beat", None)
    if callable(fn):
        try:
            return bool(fn(seg))
        except Exception:
            pass
    if not isinstance(seg, dict):
        return False
    return bool(seg.get("beat"))


def _segment_play_duration(seg: Dict[str, Any]) -> float:
    """Return rendered moving duration excluding `hold_tail` (`common.segment_play_duration` with safe fallback)."""
    import common as _common

    fn = getattr(_common, "segment_play_duration", None)
    if callable(fn):
        try:
            return float(fn(seg))
        except Exception:
            pass
    src_dur = max(0.04, float(seg["end"]) - float(seg["start"]))
    return max(0.04, src_dur / _segment_speed(seg))


def _src_to_seg_time(seg: Dict[str, Any], src_t: float) -> float:
    """Map absolute source timestamp to segment-relative rendered seconds (`common.src_to_seg_time` fallback)."""
    import common as _common

    fn = getattr(_common, "src_to_seg_time", None)
    if callable(fn):
        try:
            return float(fn(seg, src_t))
        except Exception:
            pass
    return max(0.0, (float(src_t) - float(seg["start"])) / _segment_speed(seg))


def _seg_to_src_time(seg: Dict[str, Any], seg_t: float) -> float:
    """Map segment-relative rendered seconds back to absolute source timestamp (`common.seg_to_src_time` fallback)."""
    import common as _common

    fn = getattr(_common, "seg_to_src_time", None)
    if callable(fn):
        try:
            return float(fn(seg, seg_t))
        except Exception:
            pass
    return float(seg["start"]) + max(0.0, float(seg_t)) * _segment_speed(seg)


def _segment_duration(seg: Dict[str, Any]) -> float:
    """Return full rendered segment duration handling `speed` + `hold_tail` via `common.segment_duration`."""
    import common as _common

    fn = getattr(_common, "segment_duration", None)
    if callable(fn):
        if callable(getattr(_common, "segment_speed", None)) or abs(_segment_speed(seg) - 1.0) <= 1e-6:
            return float(fn(seg))
    return max(0.08, _segment_play_duration(seg) + _segment_hold_tail(seg))


def check_order(seg_rows: List[Tuple[str, List[str]]], lines: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Verify segment-to-script-line order, duplicates, missing lines, and unproven cuts."""
    pos_of = {l["id"]: i for i, l in enumerate(lines)}
    out_of_order: List[str] = []
    repeated: List[str] = []
    unknown: List[str] = []
    said_by: Dict[int, List[int]] = {}
    assigned: List[Dict[str, Any]] = []
    cur, cur_id = -1, -1
    for n, (sid, toks) in enumerate(seg_rows):
        claims = [pos_of[i] for i in _line_ids_of(sid) if i in pos_of]
        if claims:
            if min(claims) < cur_id:
                out_of_order.append(f"{sid} comes after {lines[cur_id]['id']}, but appears earlier in the script")
            cur_id = max(cur_id, max(claims))
        if not toks or (claims and len(toks) <= 2):
            assigned.append({"id": sid, "line": lines[claims[0]]["id"] if claims else None, "fit": None})
            continue
        fits = [(part_fit(toks, l.get("tokens") or []) if l.get("tokens") else 0.0) for l in lines]
        agreed = [i for i in claims if fits[i] >= FIT_MIN]
        if agreed:
            for i in agreed:
                said_by.setdefault(i, []).append(n)
            cur = max(cur, max(agreed))
            assigned.append({"id": sid, "line": lines[agreed[0]]["id"], "fit": round(fits[agreed[0]], 2)})
            continue
        best = max(fits) if fits else 0.0
        cands = [i for i, f in enumerate(fits) if f >= FIT_MIN and f >= best - TIE]
        if not cands:
            unknown.append(sid)
            assigned.append({"id": sid, "line": None, "fit": round(best, 2)})
            continue
        ahead = [i for i in cands if i >= cur]
        if ahead:
            chosen = ahead[0]
            cur = chosen
        else:
            chosen = max(cands, key=lambda i: fits[i])
            if best >= STRONG and len(toks) >= 3 and not any(m.startswith(sid + " ") for m in out_of_order):
                out_of_order.append(f"{sid} speaks {lines[chosen]['id']}, which belongs before {lines[cur]['id']}")
        said_by.setdefault(chosen, []).append(n)
        assigned.append({"id": sid, "line": lines[chosen]["id"], "fit": round(fits[chosen], 2)})

    for i, hits in said_by.items():
        whole = [n for n in hits if recall(seg_rows[n][1], lines[i].get("tokens") or []) >= REPEAT_RECALL]
        if len(whole) >= 2 and _hits(seg_rows[whole[0]][1], seg_rows[whole[1]][1]) >= 0.7:
            repeated.append(f"{lines[i]['id']} is spoken {len(whole)} times ({', '.join(seg_rows[n][0] for n in whole)})")

    missing: List[str] = []
    unproven: List[str] = []
    ids_present = {i for s, _ in seg_rows for i in _line_ids_of(s)}
    for l in lines:
        lt = l.get("tokens") or []
        if not lt:
            if l["id"] not in ids_present:
                missing.append(l["id"])
            continue
        heard = 0.0
        for n in range(len(seg_rows)):
            near = seg_rows[n][1] + (seg_rows[n + 1][1] if n + 1 < len(seg_rows) else [])
            heard = max(heard, recall(near, lt))
        carried = [t for sid, toks in seg_rows if l["id"] in _line_ids_of(sid) for t in toks]
        heard = max(heard, recall(carried, lt))
        if heard >= HEARD_MIN:
            continue
        if l["id"] in ids_present:
            unproven.append(l["id"])
        else:
            missing.append(l["id"])

    return {
        "assigned": assigned,
        "out_of_order": out_of_order,
        "repeated": repeated,
        "missing": missing,
        "unproven": unproven,
        "unknown": unknown,
    }


def _resolve_clip_cache_dir(src: Any, work_dir: Optional[Path] = None) -> Path:
    """Resolve a clip's cache folder via `clip_work_dir(src)` or fallback `clip_caches.json`."""
    from common import CLIP_CACHE, WORK, clip_work_dir, read_json

    wdir = clip_work_dir(src)
    if (wdir / "hq_words.json").is_file() or (wdir / "words.json").is_file() or (wdir / "audio16k.wav").is_file():
        return wdir
    proj = Path(work_dir) if work_dir is not None else _ACTIVE_WORK_DIR
    if proj is not None:
        cmap_file = proj / "clip_caches.json"
        if cmap_file.is_file():
            try:
                cmap = read_json(cmap_file)
                if isinstance(cmap, dict):
                    p = Path(str(src or ""))
                    for key in (str(src or ""), p.name, p.stem):
                        mapped = cmap.get(key)
                        if mapped:
                            for cand in (CLIP_CACHE / str(mapped), WORK / str(mapped)):
                                if cand.is_dir():
                                    return cand
            except Exception:
                pass
    return wdir


def seg_tokens(seg: Dict[str, Any]) -> List[str]:
    if _segment_is_muted(seg) or _segment_is_beat(seg):
        return []
    from common import read_json

    wdir = _resolve_clip_cache_dir(seg.get("src", ""))
    st, en = float(seg["start"]), float(seg["end"])
    for name in ("hq_words.json", "words.json"):
        f = wdir / name
        if f.is_file():
            try:
                ws = read_json(f)
            except Exception:
                continue
            return clean([
                w.get("w", "")
                for w in ws
                if float(w.get("end", 0)) > st + 0.08 and float(w.get("start", 0)) < en - 0.08
            ])
    return []


def read_accepted(work_dir: Path) -> Dict[str, str]:
    """Read `accept <id> <reason>` entries from `work/<project>/decisions.txt`."""
    f = Path(work_dir) / "decisions.txt"
    out: Dict[str, str] = {}
    if f.is_file():
        for ln in f.read_text(encoding="utf-8", errors="ignore").splitlines():
            m = re.match(r"\s*accept\s+(\S+)\s+(\S.*)$", ln, re.I)
            if m:
                out[m.group(1)] = m.group(2).strip()
    return out


def src_time_swaps(segs: List[Dict[str, Any]], lines: List[Dict[str, Any]]) -> List[str]:
    """Detect later script lines cut from earlier timestamps inside the same continuous source clip."""
    pos = {l["id"]: i for i, l in enumerate(lines)}
    out: List[str] = []
    last: Dict[str, Tuple[int, float, str]] = {}
    for s in segs:
        if _segment_is_muted(s) or _segment_is_beat(s):
            continue
        sid = str(s.get("id", ""))
        ids = [pos[i] for i in _line_ids_of(sid) if i in pos]
        try:
            start = float(s.get("start"))
        except (TypeError, ValueError):
            continue
        if not ids:
            continue
        key, p = str(s.get("src", "")), min(ids)
        prev = last.get(key)
        if prev and p > prev[0] and start < prev[1] - SRC_SWAP_MIN_SEC:
            out.append(
                f"{sid} is later in the script than {prev[2]} but cut from earlier in {key} "
                f"({start:.1f}s vs {prev[1]:.1f}s): re-cut in clip order, or record `accept {sid} <reason>` if retakes reversed them"
            )
        if prev is None or p >= prev[0]:
            last[key] = (max(ids), start, sid)
    return out


def gate(plan: Dict[str, Any], script_path: Any, work_dir: Path) -> Dict[str, Any]:
    """Check script order, line coverage, and source clip ordering against `decisions.txt`."""
    global _ACTIVE_WORK_DIR
    from prepare import parse_script_file

    prev_wd = _ACTIVE_WORK_DIR
    _ACTIVE_WORK_DIR = Path(work_dir) if work_dir is not None else prev_wd
    try:
        _, lines = parse_script_file(Path(script_path)) if not isinstance(script_path, list) else ({}, script_path)
        rows = [(str(s.get("id", "")), seg_tokens(s)) for s in plan.get("segments", [])]
    finally:
        _ACTIVE_WORK_DIR = prev_wd
    rep = check_order(rows, lines)
    ok = read_accepted(work_dir)

    def open_items(items: List[str]) -> List[str]:
        return [i for i in items if not any(re.search(rf"\b{re.escape(k)}[a-z]?\b", i) for k in ok)]

    problems = {
        "OUT OF ORDER": open_items(rep["out_of_order"]),
        "SOURCE ORDER (later script line cut from earlier in same clip)": open_items(
            src_time_swaps(plan.get("segments", []), lines)
        ),
        "MISSING": open_items(rep["missing"]),
        "SAID TWICE": open_items(rep["repeated"]),
        "NOT PROVEN (spoken words in cut do not match line)": open_items(rep["unproven"]),
        "UNKNOWN SEGMENT (matches no script line)": open_items(rep["unknown"]),
    }
    problems = {k: v for k, v in problems.items() if v}
    return {"blocked": bool(problems), "problems": problems, "accepted": ok, "report": rep}


def _split_top_level_pipes(text: str) -> List[str]:
    """Split `text` by `|` only outside single/double quotes (respecting backslash escapes)."""
    parts: List[str] = []
    cur: List[str] = []
    in_quote: Optional[str] = None
    escape = False
    for ch in text:
        if escape:
            cur.append(ch)
            escape = False
            continue
        if ch == "\\":
            escape = True
            cur.append(ch)
            continue
        if in_quote:
            cur.append(ch)
            if ch == in_quote:
                in_quote = None
        else:
            if ch in ('"', "'"):
                in_quote = ch
                cur.append(ch)
            elif ch == "|":
                parts.append("".join(cur))
                cur = []
            else:
                cur.append(ch)
    parts.append("".join(cur))
    return parts


def unread_plan_lines(plan: Dict[str, Any], plan_path: Path) -> List[str]:
    """Identify lines in `plan.edl` that look like cuts but were skipped by the EDL parser (e.g. unquoted spaces)."""
    ids = {str(s.get("id")) for s in plan.get("segments", [])}
    out: List[str] = []
    try:
        text = Path(plan_path).read_text(encoding="utf-8")
    except OSError:
        return out
    for n, line in enumerate(text.splitlines(), 1):
        raw = line.strip()
        if not raw or raw.startswith("#"):
            continue
        low = raw.lower()
        top_pipes = _split_top_level_pipes(raw)
        has_pipe = len(top_pipes) > 1
        if (low.startswith("sfx ") or low.startswith("mark ")) and not has_pipe:
            continue
        if has_pipe:
            phead = top_pipes[0].strip().strip('"').strip("'").lower()
            if phead in ("wrong", "right", "x", "check", "tick", "cross") or phead.endswith(
                (".wav", ".mp3", ".ogg", ".m4a", ".aac")
            ):
                continue
        first = (top_pipes[0].strip().split()[0] if has_pipe and top_pipes[0].strip() else raw.split()[0]).strip('"')
        if first not in ids:
            out.append(
                f"line {n} ('{raw[:46]}...') was not parsed: quote clip filenames containing spaces, e.g. \"MISS NOVA FV.mp4\""
            )
    return out


def _is_seg_accepted(sid: str, ok: Dict[str, str]) -> bool:
    if not ok:
        return False
    if sid in ok or sid.rstrip("abcdefgh") in ok:
        return True
    return any(lid in ok for lid in _line_ids_of(sid))


def _word_file(src: Any) -> Optional[Path]:
    wdir = _resolve_clip_cache_dir(src)
    for name in ("hq_words.json", "words.json"):
        if (wdir / name).is_file():
            return wdir / name
    return None


def _words_of(seg: Dict[str, Any]) -> List[Dict[str, Any]]:
    from common import read_json

    f = _word_file(seg.get("src", ""))
    return read_json(f) if f else []


def _env_of(seg: Dict[str, Any]) -> List[Tuple[float, float]]:
    from prepare import compute_10ms_rms_envelope

    wav = _resolve_clip_cache_dir(seg.get("src", "")) / "audio16k.wav"
    return compute_10ms_rms_envelope(wav) if wav.is_file() else []


def _speech_spans_for(seg: Dict[str, Any]) -> List[Tuple[float, float]]:
    from common import read_json

    f = _resolve_clip_cache_dir(seg.get("src", "")) / "speech_spans.json"
    try:
        return [tuple(x) for x in read_json(f).get("spans", [])] if f.is_file() else []
    except Exception:
        return []


def _live_words_in_cut(seg: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Return non-ghost aligned words actually spoken within `seg[start..end]`."""
    if _segment_is_muted(seg) or _segment_is_beat(seg):
        return []
    from prepare import words_in_silence

    st, en = float(seg.get("start", 0.0)), float(seg.get("end", 0.0))
    ws = _words_of(seg)
    if not ws:
        return []
    spoken = _speech_spans_for(seg)
    env = _env_of(seg)
    bad_ids = {id(w) for w in words_in_silence(ws, env)} if env else set()
    kept: List[Dict[str, Any]] = []
    for w in ws:
        if id(w) in bad_ids or w.get("no_sound"):
            continue
        ws0, ws1 = float(w.get("start", 0.0)), float(w.get("end", 0.0))
        if spoken and not any(min(ws1, b + 0.08) > max(ws0, a - 0.06) for a, b in spoken):
            continue
        mid = 0.5 * (ws0 + ws1)
        ov = min(ws1, en) - max(ws0, st)
        dur_w = max(0.04, ws1 - ws0)
        if (st - 0.02 <= mid <= en + 0.02) or (
            ws1 > st + 0.08 and ws0 < en - 0.08 and ov >= max(0.06, min(0.12, 0.45 * dur_w))
        ):
            kept.append(w)
    return kept


def _script_tokens_for_segs(
    segs: List[Dict[str, Any]],
    words_slice: List[Dict[str, Any]],
    by_id: Dict[str, Dict[str, Any]],
    lines: List[Dict[str, Any]],
) -> List[str]:
    """Concatenate tokens of unique script lines mapped to `segs` without double-counting sub-cuts (e.g. L23a+L23b)."""
    seen_lids: List[str] = []
    for seg in segs:
        sid = str(seg.get("id", ""))
        nums = [int(x) for x in re.findall(r"(?<![A-Za-z0-9])L(\d+)", sid.replace("_", " "))]
        lids = [f"L{n}" for n in nums if f"L{n}" in by_id]
        if len(nums) == 2 and "_" in sid and 0 < nums[1] - nums[0] <= 6:
            lids = [f"L{n}" for n in range(nums[0], nums[1] + 1) if f"L{n}" in by_id]
        if not lids and lines and words_slice:
            ctoks = clean([str(w.get("w") or w.get("word") or "") for w in words_slice])
            idx_no, _, sc = place_in_script(ctoks, lines)
            if idx_no is not None and sc >= FIT_MIN:
                for l in lines:
                    if int(l.get("index", -1)) == idx_no and l.get("id") in by_id:
                        lids = [l["id"]]
                        break
        for lid in lids:
            if lid not in seen_lids:
                seen_lids.append(lid)
    out: List[str] = []
    for lid in seen_lids:
        l_obj = by_id[lid]
        if l_obj.get("text"):
            out.extend(clean(str(l_obj["text"]).split()))
        else:
            out.extend(l_obj.get("tokens") or [])
    return out


def repeat_problems(
    plan: Dict[str, Any],
    script_path: Any = None,
    work_dir: Any = None,
    accepted: Optional[Dict[str, str]] = None,
) -> List[Dict[str, Any]]:
    """Find unscripted repeated words/phrases inside each segment and across adjacent segment boundaries."""
    global _ACTIVE_WORK_DIR
    from common import resolve
    from prepare import parse_script_file

    lines: List[Dict[str, Any]] = []
    if isinstance(script_path, list):
        lines = script_path
    else:
        sp = script_path or plan.get("script")
        if sp:
            try:
                sp_p = Path(sp) if Path(str(sp)).is_file() else resolve(str(sp))
                if sp_p.is_file():
                    _, lines = parse_script_file(sp_p)
            except Exception:
                lines = []

    ok: Dict[str, str] = dict(accepted or {})
    wdir_path: Optional[Path] = None
    if isinstance(work_dir, dict):
        ok.update(work_dir)
    elif work_dir is not None:
        wdir_path = Path(work_dir)
        ok.update(read_accepted(wdir_path))
    if wdir_path is None and plan.get("_plan_dir"):
        wdir_path = Path(str(plan["_plan_dir"]))
        ok.update(read_accepted(wdir_path))

    prev_wd = _ACTIVE_WORK_DIR
    if wdir_path is not None:
        _ACTIVE_WORK_DIR = wdir_path
    try:
        segs = plan.get("segments") or []
        by_id = {str(l["id"]): l for l in lines if l.get("id")}
        seg_words = [_live_words_in_cut(s) for s in segs]
        problems: List[Dict[str, Any]] = []

        # (a) Intra-segment repeats
        for s, cw in zip(segs, seg_words):
            sid = str(s.get("id", "?"))
            if _is_seg_accepted(sid, ok) or len(cw) < 2:
                continue
            allowed = _script_tokens_for_segs([s], cw, by_id, lines)
            for rep in find_unscripted_repeats(cw, allowed):
                msg = (
                    f"{sid} ({s.get('src')} {float(s.get('start', 0)):.2f}-{float(s.get('end', 0)):.2f}): "
                    f"repeats '{rep['phrase']}' at {rep['first_start']:.2f}-{rep['first_end']:.2f} and "
                    f"{rep['second_start']:.2f}-{rep['second_end']:.2f} — trim stumble (clean start {rep['clean_trim_start']:.2f})"
                )
                problems.append({
                    **rep,
                    "scope": "intra",
                    "segment": sid,
                    "segments": [sid],
                    "message": msg,
                })

        # (b) Cross-segment boundary repeats (compare each spoken segment with the next non-muted/non-beat spoken segment)
        boundary_window = 5
        spoken_indices = [
            idx
            for idx, seg in enumerate(segs)
            if not _segment_is_muted(seg) and not _segment_is_beat(seg) and seg_words[idx]
        ]
        for idx_a, idx_b in zip(spoken_indices, spoken_indices[1:]):
            s_a, s_b = segs[idx_a], segs[idx_b]
            sid_a, sid_b = str(s_a.get("id", "?")), str(s_b.get("id", "?"))
            if _is_seg_accepted(sid_a, ok) or _is_seg_accepted(sid_b, ok):
                continue
            wa, wb = list(seg_words[idx_a]), list(seg_words[idx_b])
            if not wa or not wb:
                continue
            if (
                s_a.get("src") == s_b.get("src")
                and float(s_b.get("start", 0.0)) >= float(s_a.get("end", 0.0)) - 0.08
                and abs(float(wa[-1].get("start", 0.0)) - float(wb[0].get("start", 0.0))) < 0.04
                and abs(float(wa[-1].get("end", 0.0)) - float(wb[0].get("end", 0.0))) < 0.04
            ):
                wb = wb[1:]
            if not wb:
                continue
            tail_a = wa[-boundary_window:]
            head_b = wb[:boundary_window]
            combined: List[Dict[str, Any]] = []
            tail_tok_count = 0
            tail_word_count = 0
            t_cursor = 0.0
            for w in tail_a:
                w_dur = max(0.05, float(w.get("end", 0.0)) - float(w.get("start", 0.0)))
                nt = len(_repeat_tokens(str(w.get("w") or w.get("word") or "").split()))
                if nt == 0:
                    continue
                tail_tok_count += nt
                tail_word_count += 1
                combined.append({
                    "w": w.get("w") or w.get("word") or "",
                    "start": round(t_cursor, 3),
                    "end": round(t_cursor + w_dur, 3),
                    "_orig_start": float(w.get("start", 0.0)),
                    "_orig_end": float(w.get("end", 0.0)),
                })
                t_cursor += w_dur + 0.05
            t_cursor += 0.10
            for w in head_b:
                w_dur = max(0.05, float(w.get("end", 0.0)) - float(w.get("start", 0.0)))
                nt = len(_repeat_tokens(str(w.get("w") or w.get("word") or "").split()))
                if nt == 0:
                    continue
                combined.append({
                    "w": w.get("w") or w.get("word") or "",
                    "start": round(t_cursor, 3),
                    "end": round(t_cursor + w_dur, 3),
                    "_orig_start": float(w.get("start", 0.0)),
                    "_orig_end": float(w.get("end", 0.0)),
                })
                t_cursor += w_dur + 0.05
            if tail_tok_count == 0 or len(combined) <= tail_word_count:
                continue
            tok_to_comb: List[Dict[str, Any]] = []
            for cw_item in combined:
                for _ in _repeat_tokens(str(cw_item.get("w") or "").split()):
                    tok_to_comb.append(cw_item)

            allowed_pair = _script_tokens_for_segs([s_a, s_b], tail_a + head_b, by_id, lines)
            for rep in find_unscripted_repeats(combined, allowed_pair):
                if rep["first_idx"] < tail_tok_count <= rep["second_idx"]:
                    f_w0 = tok_to_comb[rep["first_idx"]]
                    f_w1 = tok_to_comb[min(len(tok_to_comb) - 1, rep["first_idx"] + rep["size"] - 1)]
                    s_w0 = tok_to_comb[rep["second_idx"]]
                    s_w1 = tok_to_comb[min(len(tok_to_comb) - 1, rep["second_idx"] + rep["size"] - 1)]
                    fs_orig = round(float(f_w0["_orig_start"]), 3)
                    fe_orig = round(float(f_w1["_orig_end"]), 3)
                    ss_orig = round(float(s_w0["_orig_start"]), 3)
                    se_orig = round(float(s_w1["_orig_end"]), 3)
                    msg = (
                        f"{sid_a} -> {sid_b}: '{rep['phrase']}' spoken at end of {sid_a} "
                        f"({fs_orig:.2f}-{fe_orig:.2f}) and repeated at start of {sid_b} "
                        f"({ss_orig:.2f}-{se_orig:.2f}) — trim one copy"
                    )
                    problems.append({
                        "kind": rep["kind"],
                        "scope": "boundary",
                        "segment": f"{sid_a} -> {sid_b}",
                        "segments": [sid_a, sid_b],
                        "phrase": rep["phrase"],
                        "size": rep["size"],
                        "first_start": fs_orig,
                        "first_end": fe_orig,
                        "second_start": ss_orig,
                        "second_end": se_orig,
                        "clean_trim_start": round(se_orig, 3),
                        "message": msg,
                    })
        return problems
    finally:
        _ACTIVE_WORK_DIR = prev_wd


def _norm_mark_kind(mark_type: Any) -> str:
    low = str(mark_type or "").strip().lower()
    if low in ("right", "check", "tick", "correct", "ok", "green"):
        return "right"
    if low in ("wrong", "x", "cross", "red", "bad"):
        return "wrong"
    return low


def _is_header_banner_text(text: Any) -> bool:
    return str(text or "").strip().lower().startswith("header:")


def _normalize_banners_list(seg: Dict[str, Any]) -> Tuple[List[Dict[str, Any]], bool]:
    """Return `(normalized_banner_dicts, used_stop_saying_key)` without mutating `seg`."""
    if "banners" in seg and seg["banners"] is not None:
        raw, from_legacy = seg["banners"], False
    elif "stop_saying" in seg and seg["stop_saying"] is not None:
        raw, from_legacy = seg["stop_saying"], True
    else:
        return [], False
    if isinstance(raw, (str, dict)):
        items = [raw]
    elif isinstance(raw, list):
        items = list(raw)
    else:
        return [], from_legacy
    out: List[Dict[str, Any]] = []
    for item in items:
        if isinstance(item, str):
            out.append({"text": item, "at": 0.0})
        elif isinstance(item, dict):
            out.append(dict(item))
    return out, from_legacy


def _cut_words_for_beat(seg: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Return aligned words inside `seg[start..end]`, falling back to `_words_of(seg)` near boundaries."""
    words = _live_words_in_cut(seg)
    if words:
        return words
    st, en = float(seg.get("start", 0.0)), float(seg.get("end", 0.0))
    fallback: List[Dict[str, Any]] = []
    for w in _words_of(seg):
        if w.get("no_sound"):
            continue
        ws0 = float(w.get("start", 0.0))
        ws1 = float(w.get("end", 0.0))
        mid = 0.5 * (ws0 + ws1)
        ov = min(ws1, en) - max(ws0, st)
        if (st - 0.08 <= mid <= en + 0.08 and ws1 > st and ws0 < en) or ov >= 0.06:
            fallback.append(w)
    return fallback


_FC_LATIN_RE = re.compile(r"[A-Za-z]")
_FC_NON_LATIN_RE = re.compile(r"[\u0900-\u0D7F\u0D80-\u0DFF\u0600-\u06FF\u0400-\u04FF\u4E00-\u9FFF]")


def _parse_flashcard_text_safe(raw: str) -> Dict[str, str]:
    """Parse bilingual flashcard text via `prepare.parse_flashcard_text` (with local fallback)
    so English/Latin is always `en` (top) and Indic/non-Latin is always `native` (bottom).
    """
    try:
        from prepare import parse_flashcard_text

        res = parse_flashcard_text(str(raw or ""))
        if isinstance(res, dict):
            en = str(res.get("en") or "").strip()
            native = str(res.get("native") or "").strip()
            text = str(res.get("text") or "").strip()
            if not text:
                text = f"{en} | {native}" if (en and native) else (en or native)
            return {"text": text, "en": en, "native": native}
    except Exception:
        pass

    s = re.sub(r"\s+", " ", str(raw or "").strip()).strip("\"'")
    if not s:
        return {"text": "", "en": "", "native": ""}
    parts: List[str] = []
    for sep in ("|", "—", "–", " - ", " = ", " / "):
        if sep in s:
            parts = [p.strip().strip("\"'") for p in s.split(sep, 1)]
            break
    if len(parts) == 2 and parts[0] and parts[1]:
        left, right = parts[0], parts[1]
        l_lat, l_non = len(_FC_LATIN_RE.findall(left)), len(_FC_NON_LATIN_RE.findall(left))
        r_lat, r_non = len(_FC_LATIN_RE.findall(right)), len(_FC_NON_LATIN_RE.findall(right))
        if l_non > l_lat and r_lat >= r_non:
            en, native = right, left
        elif r_non > r_lat and l_lat >= l_non:
            en, native = left, right
        else:
            en, native = left, right
        return {"text": f"{en} | {native}", "en": en, "native": native}
    lat = len(_FC_LATIN_RE.findall(s))
    non = len(_FC_NON_LATIN_RE.findall(s))
    if non > lat:
        return {"text": s, "en": "", "native": s}
    return {"text": s, "en": s, "native": ""}


def _normalize_fc_entry(entry: Any) -> Optional[Dict[str, str]]:
    """Normalize a script or segment flashcard dict/string into `{'text', 'en', 'native'}`."""
    if isinstance(entry, str):
        parsed = _parse_flashcard_text_safe(entry)
        return parsed if parsed["text"] else None
    if isinstance(entry, dict):
        raw_txt = str(entry.get("text") or "").strip()
        if not raw_txt:
            a = str(entry.get("en") or "").strip()
            b = str(entry.get("native") or "").strip()
            raw_txt = f"{a} | {b}" if (a and b) else (a or b)
        if not raw_txt:
            return None
        parsed = _parse_flashcard_text_safe(raw_txt)
        return parsed if parsed["text"] else None
    return None


def _banner_matches_flashcard(
    b_txt: str,
    seg_line_fcs: List[Dict[str, str]],
    all_fc_phrases: Dict[str, Dict[str, str]],
    is_flashcard_script: bool,
) -> Optional[Dict[str, str]]:
    """Return canonical flashcard dict if a non-header `banner:` is actually a flashcard, else None."""
    txt = str(b_txt or "").strip()
    if not txt or _is_header_banner_text(txt):
        return None
    if "|" in txt:
        parsed = _parse_flashcard_text_safe(txt)
        if parsed["text"]:
            return parsed
    if not is_flashcard_script:
        return None
    b_low = re.sub(r"\s+", " ", txt.lower()).strip("\"'.,!?")
    b_toks = clean(txt.split())
    for fc in seg_line_fcs:
        for k in ("text", "en", "native"):
            cand = str(fc.get(k) or "").strip()
            if not cand:
                continue
            c_low = re.sub(r"\s+", " ", cand.lower()).strip("\"'.,!?")
            if b_low == c_low:
                return dict(fc)
            c_toks = clean(cand.split())
            if b_toks and c_toks and fit(b_toks, c_toks) >= 0.70:
                return dict(fc)
    if b_low in all_fc_phrases:
        return dict(all_fc_phrases[b_low])
    return None


def calibrate_flashcards(plan: Dict[str, Any], script_path: Any, work_dir: Path) -> int:
    """Deterministically attach script-driven bilingual flashcards (`seg['flashcards']`),
    rescue accidental flashcard `banner:` entries before `calibrate_post_word_beats()`,
    and normalize all flashcard text so English is always top (`en`) and native script is bottom (`native`).
    """
    global _ACTIVE_WORK_DIR
    if not isinstance(plan, dict) or not plan.get("segments"):
        return 0
    if plan.get("cards_off") or plan.get("cards") is False:
        return 0

    prev_wd = _ACTIVE_WORK_DIR
    _ACTIVE_WORK_DIR = Path(work_dir) if work_dir is not None else prev_wd
    try:
        s_meta: Dict[str, Any] = {}
        s_lines: List[Dict[str, Any]] = []
        try:
            from prepare import parse_script_file

            if isinstance(script_path, list):
                s_lines = script_path
            elif script_path:
                sp = Path(script_path)
                if sp.is_file():
                    s_meta, s_lines = parse_script_file(sp)
        except Exception:
            s_meta, s_lines = {}, []

        by_id: Dict[str, Dict[str, Any]] = {
            str(l["id"]).upper(): l for l in s_lines if isinstance(l, dict) and l.get("id")
        }
        line_fc_map: Dict[str, Dict[str, str]] = {}
        all_fc_phrases: Dict[str, Dict[str, str]] = {}

        def _register_fc(fc_obj: Dict[str, str]) -> None:
            for k in ("text", "en", "native"):
                v = re.sub(r"\s+", " ", str(fc_obj.get(k) or "").strip().lower()).strip("\"'.,!?")
                if v and v not in all_fc_phrases:
                    all_fc_phrases[v] = fc_obj

        for lid, line in by_id.items():
            norm_fc = _normalize_fc_entry(line.get("flashcard"))
            if norm_fc:
                line_fc_map[lid] = norm_fc
                _register_fc(norm_fc)

        if isinstance(s_meta, dict):
            for meta_fc in (s_meta.get("flashcards") or []):
                norm_fc = _normalize_fc_entry(meta_fc)
                if norm_fc:
                    _register_fc(norm_fc)

        is_flashcard_script = bool(line_fc_map or all_fc_phrases)
        accepted = read_accepted(work_dir) if work_dir is not None else {}
        default_style = str(plan.get("card_pack") or "clean").strip() or "clean"
        segs = [s for s in plan.get("segments", []) if isinstance(s, dict)]
        changed = 0

        for seg in segs:
            sid = str(seg.get("id", ""))
            lids = _seg_lines(sid)
            seg_lines_objs = [by_id[lid] for lid in lids if lid in by_id]
            seg_line_fcs: List[Dict[str, str]] = []
            seg_fc_seen: set = set()
            for lid in lids:
                fc = line_fc_map.get(lid)
                if fc:
                    key = (fc["en"].lower(), fc["native"].lower())
                    if key not in seg_fc_seen:
                        seg_fc_seen.add(key)
                        seg_line_fcs.append(fc)

            no_card_accepted = any(
                re.search(r"\bno[-_ ]?cards?\b", str(accepted.get(k) or ""), re.I)
                for k in ([sid, sid.rstrip("abcdefgh")] + lids)
                if k in accepted
            )
            seg_cards_off = bool(seg.get("cards_off")) or no_card_accepted

            # Migrate any legacy `seg["cards"]` key into `seg["flashcards"]`
            if "flashcards" not in seg and isinstance(seg.get("cards"), list) and seg["cards"]:
                seg["flashcards"] = seg.pop("cards")
                changed += 1

            # Normalize any existing `seg["flashcards"]` entries via `parse_flashcard_text`
            raw_cards = seg.get("flashcards")
            norm_cards: List[Dict[str, Any]] = []
            existing_keys: set = set()
            if isinstance(raw_cards, list):
                seg_dur = max(0.08, _segment_play_duration(seg)) if ("start" in seg and "end" in seg) else None
                for item in raw_cards:
                    if isinstance(item, str):
                        parsed = _parse_flashcard_text_safe(item)
                        if not parsed["text"]:
                            continue
                        card_d: Dict[str, Any] = {
                            "text": parsed["text"],
                            "en": parsed["en"],
                            "native": parsed["native"],
                            "pos": "auto",
                            "style": default_style,
                        }
                        changed += 1
                    elif isinstance(item, dict):
                        card_d = dict(item)
                        parsed = _normalize_fc_entry(card_d)
                        if not parsed or not parsed["text"]:
                            continue
                        if (
                            str(card_d.get("text") or "").strip() != parsed["text"]
                            or str(card_d.get("en") or "").strip() != parsed["en"]
                            or str(card_d.get("native") or "").strip() != parsed["native"]
                        ):
                            card_d["text"] = parsed["text"]
                            card_d["en"] = parsed["en"]
                            card_d["native"] = parsed["native"]
                            changed += 1
                        if not card_d.get("pos"):
                            card_d["pos"] = "auto"
                        if not card_d.get("style"):
                            card_d["style"] = default_style
                        if seg_dur is not None and card_d.get("at") is not None:
                            try:
                                at_v = float(card_d["at"])
                                clamped = round(max(0.0, min(at_v, seg_dur)), 3)
                                if abs(clamped - at_v) > 0.002:
                                    card_d["at"] = clamped
                                    changed += 1
                            except (TypeError, ValueError):
                                pass
                    else:
                        continue
                    k_exist = (str(card_d.get("en") or "").lower(), str(card_d.get("native") or "").lower(), card_d.get("at"))
                    if k_exist not in existing_keys:
                        existing_keys.add(k_exist)
                        norm_cards.append(card_d)
                if norm_cards or raw_cards:
                    seg["flashcards"] = norm_cards

            # 1) Rescue accidental flashcard banners BEFORE `calibrate_post_word_beats()`
            has_explicit_wrong_line = any(
                bool(l.get("strike_text")) or str(l.get("mark") or "").strip().lower() == "wrong"
                for l in seg_lines_objs
            )
            b_list, from_legacy_banners = _normalize_banners_list(seg)
            if b_list and not has_explicit_wrong_line:
                kept_banners: List[Dict[str, Any]] = []
                rescued_any = False
                for b in b_list:
                    b_txt = str(b.get("text") or b.get("phrase") or "").strip()
                    matched_fc = _banner_matches_flashcard(b_txt, seg_line_fcs, all_fc_phrases, is_flashcard_script)
                    if matched_fc is not None:
                        rescued_any = True
                        if not seg_cards_off:
                            fc_item: Dict[str, Any] = {
                                "text": matched_fc["text"],
                                "en": matched_fc["en"],
                                "native": matched_fc["native"],
                                "pos": "auto",
                                "style": default_style,
                            }
                            if b.get("at") is not None:
                                try:
                                    at_b = float(b["at"])
                                    if at_b > 0.001:
                                        fc_item["at"] = round(at_b, 3)
                                except (TypeError, ValueError):
                                    pass
                            if b.get("duration") is not None:
                                try:
                                    dur_b = float(b["duration"])
                                    if dur_b > 0.05:
                                        fc_item["duration"] = round(dur_b, 3)
                                except (TypeError, ValueError):
                                    pass
                            k_fc = (fc_item["en"].lower(), fc_item["native"].lower(), fc_item.get("at"))
                            if k_fc not in existing_keys:
                                existing_keys.add(k_fc)
                                norm_cards.append(fc_item)
                        changed += 1
                    else:
                        kept_banners.append(b)
                if rescued_any:
                    if kept_banners:
                        seg["banners"] = kept_banners
                    else:
                        seg.pop("banners", None)
                    if from_legacy_banners and "stop_saying" in seg:
                        del seg["stop_saying"]
                    if norm_cards:
                        seg["flashcards"] = norm_cards

            # 2) Deterministically auto-attach script flashcards when missing and not opted out
            if seg_cards_off or norm_cards or not seg_line_fcs:
                continue

            if len(seg_line_fcs) == 1:
                fc0 = seg_line_fcs[0]
                seg["flashcards"] = [{
                    "text": fc0["text"],
                    "en": fc0["en"],
                    "native": fc0["native"],
                    "pos": "auto",
                    "style": default_style,
                }]
                changed += 1
            else:
                raw_dur = max(0.08, _segment_play_duration(seg)) if ("start" in seg and "end" in seg) else float(len(seg_line_fcs))
                words = _cut_words_for_beat(seg) if ("start" in seg and "end" in seg) else []
                fc_first_lids: List[Tuple[Dict[str, str], str]] = []
                seen_m: set = set()
                for lid in lids:
                    fc = line_fc_map.get(lid)
                    if fc:
                        k_m = (fc["en"].lower(), fc["native"].lower())
                        if k_m not in seen_m:
                            seen_m.add(k_m)
                            fc_first_lids.append((fc, lid))
                switches: List[float] = [0.0]
                prev_t = 0.0
                for idx_fc in range(1, len(fc_first_lids)):
                    _, flid = fc_first_lids[idx_fc]
                    ltoks = list(by_id.get(flid, {}).get("tokens") or [])
                    found_t: Optional[float] = None
                    if words and ltoks:
                        lt_pool = set(ltoks[:4])
                        for w in words:
                            w_rel = _src_to_seg_time(seg, float(w.get("start", 0.0)))
                            if w_rel <= prev_t + 0.10:
                                continue
                            wt = clean(str(w.get("w") or w.get("word") or "").split())
                            if wt and _word_matches_line(wt, lt_pool):
                                found_t = round(max(prev_t + 0.05, min(w_rel, raw_dur - 0.05)), 3)
                                break
                    if found_t is None:
                        found_t = round(raw_dur * (idx_fc / float(len(fc_first_lids))), 3)
                    switches.append(found_t)
                    prev_t = found_t
                multi_cards: List[Dict[str, Any]] = []
                for idx_fc, (fc_item_obj, _) in enumerate(fc_first_lids):
                    t_s = switches[idx_fc]
                    entry_m: Dict[str, Any] = {
                        "text": fc_item_obj["text"],
                        "en": fc_item_obj["en"],
                        "native": fc_item_obj["native"],
                        "at": t_s,
                        "pos": "auto",
                        "style": default_style,
                    }
                    if idx_fc + 1 < len(switches):
                        entry_m["duration"] = round(max(0.10, switches[idx_fc + 1] - t_s), 3)
                    multi_cards.append(entry_m)
                seg["flashcards"] = multi_cards
                changed += 1

        return changed
    finally:
        _ACTIVE_WORK_DIR = prev_wd


_PHRASE_SPLIT_RE = re.compile(r"[\s\-/|—–]+")
_PHRASE_CLEAN_RE = re.compile(r"[^\w\u0900-\u0D7F]+", re.UNICODE)
_INDIC_DANDA_RE = re.compile(r"[\u0964\u0965]+")


def _phrase_tokens(text: Any) -> List[str]:
    """Normalize a phrase or word string into clean lowercase alphanumeric/Indic tokens."""
    raw = _INDIC_DANDA_RE.sub(" ", str(text or "").strip().lower())
    if not raw:
        return []
    out: List[str] = []
    for piece in _PHRASE_SPLIT_RE.split(raw):
        tok = _PHRASE_CLEAN_RE.sub("", piece).strip("_")
        if tok:
            out.append(tok)
    return out


def _phrase_tok_sim(a: str, b: str) -> float:
    """Token similarity for phrase localization that avoids short-ASCII false positives ('i' vs 'in')."""
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    if a.isascii() and b.isascii():
        if min(len(a), len(b)) <= 2:
            return 0.0
        return _sim(a, b)
    if _norm_indic_len(a) == _norm_indic_len(b):
        return 1.0
    return _sim(a, b)


def _find_phrase_end_in_words(
    words: List[Dict[str, Any]],
    target_phrase: str,
    seg_start: float,
    seg: Optional[Dict[str, Any]] = None,
) -> Optional[float]:
    """Find the best contiguous (or tight fuzzy) match of `target_phrase` inside `words` and return
    the segment-relative end timestamp `(words[match_end]["end"] - seg_start)` of that specific phrase.
    """
    target_toks = _phrase_tokens(target_phrase)
    if not target_toks or not words:
        return None

    w_toks: List[Tuple[str, int, float]] = []
    for idx_w, w in enumerate(words):
        if not isinstance(w, dict):
            continue
        raw_w = str(w.get("w") or w.get("word") or w.get("text") or "").strip()
        w_end = float(w.get("end", 0.0))
        for tok in _phrase_tokens(raw_w):
            w_toks.append((tok, idx_w, w_end))

    m = len(target_toks)
    n = len(w_toks)
    if m == 0 or n == 0:
        return None

    sim_mat = [[_phrase_tok_sim(target_toks[ti], w_toks[wi][0]) for wi in range(n)] for ti in range(m)]

    best_key: Optional[Tuple[Any, ...]] = None
    best_end_tok: Optional[int] = None

    if m == 1:
        for i in range(n):
            sv = sim_mat[0][i]
            if sv >= 0.75:
                key = (round(sv, 3), 1, round(sv, 3), 0, -i)
                if best_key is None or key > best_key:
                    best_key = key
                    best_end_tok = i
    else:
        max_span = min(n, max(m + 2, int(m * 1.5) + 1))
        for i in range(n):
            if max(sim_mat[ti][i] for ti in range(min(2, m))) < WORD_MATCH:
                continue
            j_lim = min(n, i + max_span)
            for j in range(i, j_lim):
                end_exact_sim = sim_mat[m - 1][j]
                if end_exact_sim < WORD_MATCH and (m < 3 or sim_mat[m - 2][j] < WORD_MATCH):
                    continue
                w_len = j - i + 1
                dp_cnt = [[0] * (w_len + 1) for _ in range(m + 1)]
                dp_sum = [[0.0] * (w_len + 1) for _ in range(m + 1)]
                for a in range(1, m + 1):
                    row_s = sim_mat[a - 1]
                    for b in range(1, w_len + 1):
                        best_c = dp_cnt[a - 1][b]
                        best_s = dp_sum[a - 1][b]
                        if (dp_cnt[a][b - 1], dp_sum[a][b - 1]) > (best_c, best_s):
                            best_c = dp_cnt[a][b - 1]
                            best_s = dp_sum[a][b - 1]
                        sv = row_s[i + b - 1]
                        if sv >= WORD_MATCH:
                            cand_c = dp_cnt[a - 1][b - 1] + 1
                            cand_s = dp_sum[a - 1][b - 1] + sv
                            if (cand_c, cand_s) > (best_c, best_s):
                                best_c, best_s = cand_c, cand_s
                        dp_cnt[a][b] = best_c
                        dp_sum[a][b] = best_s
                matched_cnt = dp_cnt[m][w_len]
                sim_sum = dp_sum[m][w_len]
                rec = matched_cnt / float(m)
                prec = matched_cnt / float(w_len)
                f1 = (2.0 * rec * prec / (rec + prec)) if (rec + prec) > 0.0 else 0.0
                avg_sim = sim_sum / float(m)
                if m == 2:
                    if not (matched_cnt == 2 and f1 >= 0.75 and avg_sim >= 0.70):
                        continue
                else:
                    if not (rec >= 0.65 and f1 >= 0.65 and avg_sim >= 0.55):
                        continue
                end_matched = 1 if end_exact_sim >= WORD_MATCH else 0
                key = (round(f1, 3), end_matched, round(avg_sim, 3), -abs(w_len - m), -i)
                if best_key is None or key > best_key:
                    best_key = key
                    best_end_tok = j

    if best_end_tok is None:
        return None

    raw_end = float(w_toks[best_end_tok][2])
    if seg is not None:
        en = float(seg.get("end", raw_end))
        return round(max(0.0, _src_to_seg_time(seg, min(en, raw_end))), 3)
    return round(max(0.0, raw_end - float(seg_start)), 3)


def calibrate_post_word_beats(plan: Dict[str, Any], script_path: Any, work_dir: Path) -> int:
    """Deterministically snap Stop Saying phrase banners and wrong/right marks to exact post-word
    timestamps (`word_end_rel` or phrase-matched end), enforce `0.48s` beat durations, ensure
    wrong-phrase beats carry a gradient red outer mark, and extend `hold_tail` when contiguous cuts
    leave `< 0.48s` trailing room.
    """
    global _ACTIVE_WORK_DIR
    if not isinstance(plan, dict) or not plan.get("segments"):
        return 0

    prev_wd = _ACTIVE_WORK_DIR
    _ACTIVE_WORK_DIR = Path(work_dir) if work_dir is not None else prev_wd
    try:
        try:
            from prepare import parse_script_file

            if isinstance(script_path, list):
                s_lines = script_path
            elif script_path:
                _, s_lines = parse_script_file(Path(script_path))
            else:
                s_lines = []
        except Exception:
            s_lines = []

        by_id: Dict[str, Dict[str, Any]] = {
            str(l["id"]).upper(): l for l in s_lines if isinstance(l, dict) and l.get("id")
        }
        segs = [s for s in plan.get("segments", []) if isinstance(s, dict) and "start" in s and "end" in s]
        marks = plan.setdefault("marks", [])
        if not isinstance(marks, list):
            marks = []
            plan["marks"] = marks

        last_seg_for_line: Dict[str, int] = {}
        line_has_explicit_banner: set = set()
        line_has_explicit_mark: Dict[str, set] = {}
        for idx, seg in enumerate(segs):
            sid = str(seg.get("id", ""))
            lids = _seg_lines(sid)
            for lid in lids:
                last_seg_for_line[lid] = idx
            b_list, _ = _normalize_banners_list(seg)
            if any(not _is_header_banner_text(b.get("text") or b.get("phrase")) for b in b_list):
                for lid in lids:
                    line_has_explicit_banner.add(lid)
            for m in marks:
                if isinstance(m, dict) and str(m.get("segment") or "") == sid:
                    mk = _norm_mark_kind(m.get("type"))
                    if mk:
                        for lid in lids:
                            line_has_explicit_mark.setdefault(lid, set()).add(mk)
            if isinstance(seg.get("marks"), list):
                for m in seg["marks"]:
                    if isinstance(m, dict):
                        mk = _norm_mark_kind(m.get("type"))
                        if mk:
                            for lid in lids:
                                line_has_explicit_mark.setdefault(lid, set()).add(mk)

        pack = str(plan.get("mark_pack") or "").strip()
        wrong_style = pack if pack.lower() in ("flash", "vignette", "kn20228", "w42", "strobe") else "vignette"
        beat_dur = POST_WORD_BEAT_DURATION
        changed = 0

        for idx, seg in enumerate(segs):
            sid = str(seg.get("id", ""))
            st = float(seg["start"])
            en = float(seg["end"])
            raw_dur = max(0.08, _segment_play_duration(seg))

            lids = _seg_lines(sid)
            finishing_lines = [by_id[lid] for lid in lids if lid in by_id and last_seg_for_line.get(lid) == idx]

            b_list, from_legacy = _normalize_banners_list(seg)
            header_banners = [b for b in b_list if _is_header_banner_text(b.get("text") or b.get("phrase"))]
            phrase_banners = [b for b in b_list if not _is_header_banner_text(b.get("text") or b.get("phrase"))]

            plan_seg_marks = [m for m in marks if isinstance(m, dict) and str(m.get("segment") or "") == sid]
            inline_seg_marks = [m for m in seg["marks"] if isinstance(m, dict)] if isinstance(seg.get("marks"), list) else []
            seg_marks = plan_seg_marks + inline_seg_marks
            wrong_marks = [m for m in seg_marks if _norm_mark_kind(m.get("type")) == "wrong"]
            right_marks = [m for m in seg_marks if _norm_mark_kind(m.get("type")) == "right"]

            line_strike_texts: List[str] = []
            for l in finishing_lines:
                s_txt = str(l.get("strike_text") or "").strip()
                if s_txt and not (set(lids) & line_has_explicit_banner) and s_txt not in line_strike_texts:
                    line_strike_texts.append(s_txt)

            line_has_wrong = any(
                str(l.get("mark") or "").strip().lower() == "wrong"
                and "wrong" not in line_has_explicit_mark.get(str(l.get("id", "")).upper(), set())
                and str(l.get("id", "")).upper() not in line_has_explicit_banner
                for l in finishing_lines
            )
            line_has_right = any(
                str(l.get("mark") or "").strip().lower() == "right"
                and "right" not in line_has_explicit_mark.get(str(l.get("id", "")).upper(), set())
                for l in finishing_lines
            )

            is_wrong_beat = bool(line_strike_texts or line_has_wrong or phrase_banners or wrong_marks)
            is_right_beat = bool(right_marks or line_has_right) and not is_wrong_beat
            if not is_wrong_beat and not is_right_beat:
                continue

            words = _cut_words_for_beat(seg)
            if words:
                first_word_end_rel = max(0.0, _src_to_seg_time(seg, min(en, float(words[0].get("end", en)))))
                last_word_end_rel = max(0.0, _src_to_seg_time(seg, min(en, float(words[-1].get("end", en)))))
            else:
                first_word_end_rel = 0.0
                last_word_end_rel = round(raw_dur * 0.75, 3)

            matched_end: Optional[float] = None
            if is_wrong_beat and words:
                target_candidates: List[str] = []
                if phrase_banners:
                    pb0 = str(phrase_banners[0].get("text") or phrase_banners[0].get("phrase") or "").strip()
                    if pb0:
                        target_candidates.append(pb0)
                for s_txt in line_strike_texts:
                    if s_txt and s_txt not in target_candidates:
                        target_candidates.append(s_txt)
                for lid in lids:
                    if lid in by_id:
                        s_txt = str(by_id[lid].get("strike_text") or "").strip()
                        if s_txt and s_txt not in target_candidates:
                            target_candidates.append(s_txt)
                for cand in target_candidates:
                    me = _find_phrase_end_in_words(words, cand, st, seg=seg)
                    if me is not None:
                        matched_end = me
                        break

            word_end_rel = matched_end if matched_end is not None else last_word_end_rel
            beat_at = round(max(0.0, word_end_rel), 3)

            def _is_explicit_mid_cut(at_raw: Any) -> bool:
                if not words or at_raw is None:
                    return False
                at_v = float(at_raw)
                return (
                    at_v > 0.05
                    and last_word_end_rel > at_v + 0.25
                    and at_v >= first_word_end_rel - 0.08
                )

            if is_wrong_beat:
                # If no narrower phrase matched, check whether an explicit mid-cut mark/banner exists
                # so an untimed companion banner/mark aligns to it instead of drifting to words[-1].
                fallback_anchor_at = beat_at
                if matched_end is None:
                    explicit_wm_ats = [
                        round(float(wm["at"]), 3)
                        for wm in wrong_marks
                        if _is_explicit_mid_cut(wm.get("at"))
                    ]
                    explicit_b_ats = [
                        round(float(b["at"]), 3)
                        for b in phrase_banners
                        if _is_explicit_mid_cut(b.get("at"))
                    ]
                    if explicit_wm_ats:
                        fallback_anchor_at = explicit_wm_ats[0]
                    elif explicit_b_ats:
                        fallback_anchor_at = explicit_b_ats[0]

                banners_modified = False
                if len(phrase_banners) == 0:
                    if line_strike_texts:
                        for st_i, s_txt in enumerate(line_strike_texts):
                            per_end = (
                                _find_phrase_end_in_words(words, s_txt, st, seg=seg)
                                if words and len(line_strike_texts) > 1
                                else None
                            )
                            b_target = (
                                round(max(0.0, per_end), 3)
                                if per_end is not None
                                else round(fallback_anchor_at + st_i * (beat_dur + 0.04), 3)
                            )
                            phrase_banners.append({
                                "text": s_txt,
                                "at": b_target,
                                "duration": beat_dur,
                            })
                        banners_modified = True
                        changed += 1
                elif len(phrase_banners) == 1:
                    b = phrase_banners[0]
                    b_at_raw = b.get("at")
                    b_at = float(b_at_raw) if b_at_raw is not None else 0.0
                    b_dur_raw = b.get("duration")
                    has_custom_dur = b_dur_raw is not None and abs(float(b_dur_raw) - beat_dur) > 0.01
                    if matched_end is not None:
                        is_too_early = b_at_raw is None or b_at < max(0.0, beat_at - 0.08)
                        is_too_late = b_at_raw is not None and b_at > beat_at + 0.15
                        if is_too_early or is_too_late:
                            if b_at_raw is None or abs(b_at - beat_at) > 0.002:
                                b["at"] = beat_at
                                banners_modified = True
                                changed += 1
                            if b_dur_raw is None or (not has_custom_dur and abs(float(b_dur_raw) - beat_dur) > 0.002):
                                b["duration"] = beat_dur
                                banners_modified = True
                                changed += 1
                        elif b_dur_raw is None:
                            b["duration"] = beat_dur
                            banners_modified = True
                            changed += 1
                    elif _is_explicit_mid_cut(b_at_raw):
                        # Preserve explicit earlier banner placement on multi-phrase cuts
                        if b_dur_raw is None:
                            b["duration"] = beat_dur
                            banners_modified = True
                            changed += 1
                    else:
                        target_b_at = fallback_anchor_at
                        is_post_word = b_at_raw is not None and b_at >= max(0.0, target_b_at - 0.08)
                        if not (is_post_word and has_custom_dur):
                            if not is_post_word and (b_at_raw is None or abs(b_at - target_b_at) > 0.002):
                                b["at"] = target_b_at
                                banners_modified = True
                                changed += 1
                            if b_dur_raw is None or (not is_post_word and abs(float(b_dur_raw) - beat_dur) > 0.002):
                                b["duration"] = beat_dur
                                banners_modified = True
                                changed += 1
                else:
                    all_explicit_dur = all(b.get("duration") is not None for b in phrase_banners)
                    all_nonneg = all(b.get("at") is not None and float(b.get("at", -1.0)) >= 0.0 for b in phrase_banners)
                    ats = [round(float(b.get("at")) if b.get("at") is not None else 0.0, 3) for b in phrase_banners]
                    staggered = len(set(ats)) == len(ats)
                    if not (all_explicit_dur and all_nonneg and staggered):
                        untimed = all(b.get("at") is None or float(b.get("at", 0.0)) <= 0.05 for b in phrase_banners)
                        for b_i, b in enumerate(phrase_banners):
                            if untimed:
                                b_txt = str(b.get("text") or b.get("phrase") or "").strip()
                                per_end = _find_phrase_end_in_words(words, b_txt, st, seg=seg) if (words and b_txt) else None
                                target_at = (
                                    round(max(0.0, per_end), 3)
                                    if per_end is not None
                                    else round(fallback_anchor_at + b_i * (beat_dur + 0.04), 3)
                                )
                                cur_b_at = float(b.get("at")) if b.get("at") is not None else -1.0
                                if abs(cur_b_at - target_at) > 0.002:
                                    b["at"] = target_at
                                    banners_modified = True
                                    changed += 1
                            if b.get("duration") is None:
                                b["duration"] = beat_dur
                                banners_modified = True
                                changed += 1

                if banners_modified:
                    seg["banners"] = header_banners + phrase_banners
                    if from_legacy and "stop_saying" in seg:
                        del seg["stop_saying"]
                    for lid in lids:
                        line_has_explicit_banner.add(lid)

                if not wrong_marks:
                    new_m = {
                        "type": "wrong",
                        "segment": sid,
                        "at": fallback_anchor_at if matched_end is None else beat_at,
                        "duration": beat_dur,
                        "style": wrong_style,
                    }
                    marks.append(new_m)
                    wrong_marks.append(new_m)
                    changed += 1
                else:
                    for wm in wrong_marks:
                        wm_at_raw = wm.get("at")
                        wm_at = float(wm_at_raw) if wm_at_raw is not None else 0.0
                        if matched_end is not None:
                            if wm_at_raw is None or wm_at < beat_at - 0.08:
                                wm["at"] = beat_at
                                wm["duration"] = beat_dur
                                changed += 1
                            elif wm_at > beat_at + 0.15:
                                # Pull overly late beep back onto the matched phrase end
                                wm["at"] = beat_at
                                if wm.get("duration") is None:
                                    wm["duration"] = beat_dur
                                changed += 1
                            elif wm.get("duration") is None:
                                wm["duration"] = beat_dur
                                changed += 1
                        elif _is_explicit_mid_cut(wm_at_raw):
                            # Never overwrite an explicit earlier mid-cut wrong mark with words[-1]
                            if wm.get("duration") is None:
                                wm["duration"] = beat_dur
                                changed += 1
                        else:
                            target_wm_at = fallback_anchor_at
                            if wm_at_raw is None or wm_at < target_wm_at - 0.08:
                                wm["at"] = target_wm_at
                                wm["duration"] = beat_dur
                                changed += 1
                            elif wm.get("duration") is None:
                                wm["duration"] = beat_dur
                                changed += 1
                for lid in lids:
                    line_has_explicit_mark.setdefault(lid, set()).add("wrong")

            elif is_right_beat:
                if not right_marks and line_has_right:
                    new_rm: Dict[str, Any] = {
                        "type": "right",
                        "segment": sid,
                        "at": beat_at,
                        "duration": beat_dur,
                    }
                    if pack.lower() in ("flash", "vignette", "kn20228", "w42", "strobe"):
                        new_rm["style"] = pack
                    marks.append(new_rm)
                    right_marks.append(new_rm)
                    changed += 1
                else:
                    for rm in right_marks:
                        if rm.get("duration") is None:
                            rm["duration"] = beat_dur
                            changed += 1
                for lid in lids:
                    line_has_explicit_mark.setdefault(lid, set()).add("right")

            beat_ends: List[float] = []
            for b in phrase_banners:
                b_at_v = float(b.get("at")) if b.get("at") is not None else 0.0
                b_dur_v = float(b.get("duration") if b.get("duration") is not None else beat_dur)
                beat_ends.append(b_at_v + b_dur_v)
            for m in (wrong_marks + right_marks):
                m_at_v = float(m.get("at")) if m.get("at") is not None else 0.0
                m_dur_v = float(m.get("duration") if m.get("duration") is not None else beat_dur)
                beat_ends.append(m_at_v + m_dur_v)

            if beat_ends:
                latest_beat_end = max(beat_ends)
                needed_hold = round(max(0.0, latest_beat_end - raw_dur), 3)
                existing_hold = _segment_hold_tail(seg)
                if needed_hold > 0.015 and existing_hold < needed_hold - 0.005:
                    seg["hold_tail"] = needed_hold
                    changed += 1

        return changed
    finally:
        _ACTIVE_WORK_DIR = prev_wd


def full_gate(plan: Dict[str, Any], script_path: Any, work_dir: Path, plan_path: Optional[Path] = None) -> Dict[str, Any]:
    """Complete pre-render validation: order, coverage, word boundaries, repeats, voices, pacing, and transitions."""
    global _ACTIVE_WORK_DIR
    prev_wd = _ACTIVE_WORK_DIR
    _ACTIVE_WORK_DIR = Path(work_dir) if work_dir is not None else prev_wd
    try:
        unread = unread_plan_lines(plan, plan_path) if plan_path else []
        if plan.get("segments") and not unread:
            moved = 0
            cards_calibrated = 0
            calibrated = 0
            try:
                moved = snap_cuts(plan)
            except Exception:
                pass
            try:
                cards_calibrated = calibrate_flashcards(plan, script_path, work_dir)
            except Exception:
                pass
            try:
                calibrated = calibrate_post_word_beats(plan, script_path, work_dir)
            except Exception:
                pass
            if (moved or cards_calibrated or calibrated) and plan_path and Path(plan_path).is_file():
                try:
                    import edl_parser

                    Path(plan_path).write_text(edl_parser.format_edl(plan), encoding="utf-8")
                except Exception:
                    pass
        g = gate(plan, script_path, work_dir)
        ok = g["accepted"]

        def still_open(items: List[str]) -> List[str]:
            kept = []
            for i in items:
                parts = i.split()
                head = parts[0] if parts else ""
                if _is_seg_accepted(head, ok):
                    continue
                if len(parts) >= 3 and parts[1] == "->" and _is_seg_accepted(parts[2].rstrip(":"), ok):
                    continue
                kept.append(i)
            return kept

        if not plan.get("segments"):
            g["problems"] = {"EMPTY PLAN": ["no segments yet"]}
        if unread:
            g["problems"] = {
                "PLAN LINES NOT READ (fix these first; downstream messages may result from them)": unread,
                **g["problems"],
            }
        cuts = still_open(cut_problems(plan))
        if cuts:
            g["problems"]["CUT INSIDE A WORD"] = cuts
        rep_items = repeat_problems(plan, script_path, work_dir, accepted=ok)
        rep_msgs = still_open([r["message"] for r in rep_items])
        if rep_msgs:
            g["problems"]["REPEATED WORDS IN CUT"] = rep_msgs
        g["repeats"] = rep_items
        try:
            voices, note = voice_problems(plan)
        except Exception as exc:  # noqa: BLE001
            voices, note = [], f"voices not checked: {exc}"
        voices = still_open(voices)
        uncertain = [v for v in voices if "[SHORT VOICE REVIEW]" in v]
        definite = [v for v in voices if v not in uncertain]
        if definite:
            g["problems"]["WRONG VOICE"] = definite
        if uncertain:
            g["problems"]["VOICE REVIEW REQUIRED"] = uncertain
        g["voice_note"] = note
        try:
            g["tighten"] = tight_report(plan)
        except Exception:
            g["tighten"] = []
        try:
            g["style"] = style_report(plan, work_dir)
        except Exception:
            g["style"] = []
        try:
            g["style"] = list(g["style"]) + variety_report(plan)
        except Exception:
            pass
        try:
            from prepare import parse_script_file

            _, s_lines = parse_script_file(Path(script_path)) if not isinstance(script_path, list) else ({}, script_path)
        except Exception:
            s_lines = []
        try:
            g["style"] = list(g["style"]) + ["TRANSITION " + x for x in transition_report(plan, s_lines, work_dir)]
        except Exception:
            pass
        try:
            mid_tr = still_open(mid_sentence_transition_problems(plan))
            if mid_tr:
                g["problems"]["MID-SENTENCE TRANSITION"] = mid_tr
        except Exception:
            pass
        g["blocked"] = bool(g["problems"])
        return g
    finally:
        _ACTIVE_WORK_DIR = prev_wd


def format_gate(g: Dict[str, Any]) -> str:
    """Format `full_gate` / `gate` output for CLI and render logs."""
    if not g["problems"]:
        suffix = f" ({len(g['accepted'])} accepted exception(s))" if g.get("accepted") else ""
        return "[ORDER PASS] Plan follows the script: every line present, once, in order" + suffix
    out = [
        "[ORDER BLOCKED] Resolve these in plan.edl, or record an intentional exception in decisions.txt (`accept <id> <reason>`):"
    ]
    for k, v in g["problems"].items():
        out.append(f"  {k}: " + "; ".join(v))
    return "\n".join(out)


def _word_matches_line(wtoks_item: List[str], lt_pool: set) -> bool:
    if not wtoks_item or not lt_pool:
        return False
    for t in wtoks_item:
        if t in lt_pool or max((_sim(t, p) for p in lt_pool), default=0.0) >= WORD_MATCH:
            return True
    return False


def _trim_edge_stumbles(
    words: List[Dict[str, Any]],
    wtoks: List[List[str]],
    i: int,
    j: int,
    lt: List[str],
    ref_text: Union[str, List[str]],
) -> Tuple[float, int, int, List[Dict[str, Any]]]:
    """Trim leading false starts and trailing restarted repeats from `words[i..j]` while maintaining line recall."""
    cur_i, cur_j = i, j
    base_toks = [t for k in range(cur_i, cur_j + 1) for t in wtoks[k]]
    cur_r = recall(base_toks, lt)
    for _ in range(3):
        sub_ws = words[cur_i : cur_j + 1]
        reps = find_unscripted_repeats(sub_ws, ref_text)
        if not reps:
            return cur_r, cur_i, cur_j, []
        tok_to_off: List[int] = []
        for off in range(len(sub_ws)):
            for _ in _repeat_tokens(wtoks[cur_i + off]):
                tok_to_off.append(off)
        if not tok_to_off:
            return cur_r, cur_i, cur_j, reps
        changed = False
        first_rep = reps[0]
        if first_rep["first_idx"] == 0 and first_rep["second_idx"] < len(tok_to_off):
            cand_i = cur_i + tok_to_off[first_rep["second_idx"]]
            if cand_i <= cur_j:
                cand_r = recall([t for k in range(cand_i, cur_j + 1) for t in wtoks[k]], lt)
                if cand_r >= cur_r - 0.05 and cand_r >= 0.5:
                    cur_i, cur_r, changed = cand_i, cand_r, True
        if not changed:
            last_rep = reps[-1]
            end_tok = last_rep["second_idx"] + last_rep["size"]
            if end_tok >= len(tok_to_off) and last_rep["second_idx"] > 0:
                cand_j = cur_i + tok_to_off[last_rep["second_idx"] - 1]
                if cur_i <= cand_j < cur_j:
                    cand_r = recall([t for k in range(cur_i, cand_j + 1) for t in wtoks[k]], lt)
                    if cand_r >= cur_r - 0.05 and cand_r >= 0.5:
                        cur_j, cur_r, changed = cand_j, cand_r, True
        if not changed:
            return cur_r, cur_i, cur_j, reps
    sub_ws = words[cur_i : cur_j + 1]
    return cur_r, cur_i, cur_j, find_unscripted_repeats(sub_ws, ref_text)


def _candidate_windows(
    words: List[Dict[str, Any]],
    lt: List[str],
    ref_text: Union[str, List[str], None] = None,
    fallback_toks: Optional[List[str]] = None,
) -> List[Tuple[float, int, int, List[Dict[str, Any]]]]:
    """Return non-overlapping tight candidate windows `(recall, i, j, internal_repeats)` ranked cleanest and latest first."""
    n = len(words)
    if not n or not lt:
        return []
    wtoks: List[List[str]] = []
    for idx, w in enumerate(words):
        wt = clean(str(w.get("w") or w.get("word") or w.get("text") or "").split()) if isinstance(w, dict) else []
        if not wt and fallback_toks and len(fallback_toks) == n:
            wt = [fallback_toks[idx]] if fallback_toks[idx] else []
        wtoks.append(wt)
    lt_pool = set(lt)
    flat_match = [_word_matches_line(wt, lt_pool) for wt in wtoks]
    ref_arg: Union[str, List[str]] = ref_text if ref_text else lt
    max_span = max(4, int(len(lt) * 2.0) + 3)

    scored: List[Tuple[Tuple[float, bool, float, int, int], float, int, int, List[Dict[str, Any]]]] = []
    seen_spans: set = set()
    for i in range(n):
        if not flat_match[i]:
            continue
        acc: List[str] = []
        j_lim = min(n, i + max_span)
        for j in range(i, j_lim):
            acc.extend(wtoks[j])
            if not flat_match[j]:
                continue
            r = recall(acc, lt)
            if r < 0.5:
                continue
            tr_r, tr_i, tr_j, reps = _trim_edge_stumbles(words, wtoks, i, j, lt, ref_arg)
            if (tr_i, tr_j) in seen_spans:
                continue
            seen_spans.add((tr_i, tr_j))
            tok_cnt = sum(len(wtoks[k]) for k in range(tr_i, tr_j + 1))
            excess = max(0, tok_cnt - len(lt))
            rep_pen = (0.06 + 0.02 * (len(reps) - 1)) if reps else 0.0
            eff_r = tr_r - rep_pen
            key = (round(eff_r, 2), len(reps) == 0, round(tr_r, 2), -excess, tr_i)
            scored.append((key, tr_r, tr_i, tr_j, reps))

    if not scored:
        return []
    scored.sort(key=lambda x: x[0], reverse=True)
    chosen: List[Tuple[float, int, int, List[Dict[str, Any]]]] = []
    for _key, r, i, j, reps in scored:
        if any(not (j < ci or i > cj) for _, ci, cj, _ in chosen):
            continue
        chosen.append((r, i, j, reps))
        if len(chosen) >= 3:
            break
    return chosen


def _best_window(words: List[Dict[str, Any]], toks: List[str], lt: List[str]) -> Tuple[float, int, int]:
    """Return `(recall, start_idx, end_idx)` for the cleanest clip stretch matching `lt`."""
    if words and lt:
        cands = _candidate_windows(words, lt, ref_text=lt, fallback_toks=toks)
        if cands:
            r, i, j, _ = cands[0]
            return r, i, j
    n = len(toks)
    if n == 0 or not lt:
        return (0.0, 0, 0)
    size = min(n, max(1, len(lt) + 1))
    best = (0.0, 0, max(0, size - 1))
    for i in range(0, max(1, n - size + 1)):
        r = recall(toks[i : i + size], lt)
        if r >= best[0] - 1e-9 and (r > best[0] + 1e-9 or r > 0.0):
            best = (r, i, i + size - 1)
    return best


def _filter_live_clip_words(c: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Filter out zero-energy and VAD-silent ghost words before candidate windowing."""
    from prepare import _speech_spans_of, words_in_silence

    raw_ws = [
        w
        for w in (c.get("hq_words") or [])
        if isinstance(w, dict) and clean([str(w.get("w") or w.get("word") or "")])
    ]
    if not raw_ws:
        return []
    env = c.get("rms_env") or []
    bad_ids = {id(w) for w in words_in_silence(raw_ws, env)} if env else set()
    spoken = _speech_spans_of(c) if c.get("work_dir") else []
    kept: List[Dict[str, Any]] = []
    for w in raw_ws:
        if id(w) in bad_ids or w.get("no_sound"):
            continue
        if spoken:
            ws0, ws1 = float(w.get("start", 0.0)), float(w.get("end", 0.0))
            if not any(min(ws1, b + 0.08) > max(ws0, a - 0.06) for a, b in spoken):
                continue
        kept.append(w)
    return kept


def build_line_map(lines: List[Dict[str, Any]], clips: List[Dict[str, Any]], limit: int = 4, style: str = "") -> str:
    """Build `line_map.md` listing every script line in order and its ranked `CAND` source stretches."""
    _ = style  # Preserved for signature compatibility; style routing lives in edit_brief.py
    prep = []
    for c in clips:
        ws = _filter_live_clip_words(c)
        prep.append((c, ws, [t for w in ws for t in clean(str(w.get("w") or w.get("word") or "").split())]))
    named: set = set()
    out = [
        "# LINE MAP: script lines in order with candidate source windows (`CAND <clip> <start>-<end> heard=<recall>`).",
        "",
    ]
    for l in lines:
        tags = [f"{k}={l[k]}" for k in ("scene", "mark", "layout_hint", "strike_text", "note") if l.get(k)]
        if l.get("panels"):
            tags.append(f"panels={l['panels']}")
        fc_meta = l.get("flashcard")
        if isinstance(fc_meta, dict) and fc_meta.get("text"):
            tags.append(f"card={fc_meta['text']}")
        elif isinstance(fc_meta, str) and fc_meta.strip():
            tags.append(f"card={fc_meta.strip()}")
        who = f" [{l['speaker']}]" if l.get("speaker") else ""
        out.append(f"{l['id']}{who} {l.get('text', '')}" + (f"  ({'; '.join(tags)})" if tags else ""))
        lt = l.get("tokens") or []
        if not lt:
            out.append("  silent beat: choose a silent clip from the clip sheet")
            continue
        cands = []
        ref_line = l.get("text") or lt
        for idx_c, (c, ws, toks) in enumerate(prep):
            if not ws:
                continue
            wtoks_c = [clean(str(w.get("w") or w.get("word") or "").split()) for w in ws]
            win_list = _candidate_windows(ws, lt, ref_text=ref_line, fallback_toks=toks)
            for r, i, j, reps in win_list:
                if r >= 0.5:
                    j_clamped = min(j, len(ws) - 1)
                    w_start = float(ws[i]["start"])
                    w_end = float(ws[j_clamped]["end"])
                    rep_pen = (0.06 + 0.02 * (len(reps) - 1)) if reps else 0.0
                    stoks = [t for k in range(i, j_clamped + 1) for t in wtoks_c[k]]
                    f_sc = fit(stoks, lt)
                    cands.append((
                        (round(r - rep_pen, 2), len(reps) == 0, round(f_sc, 2), round(r, 2), idx_c, w_start),
                        r,
                        Path(str(c["src"])).name,
                        w_start,
                        w_end,
                        reps,
                        c,
                    ))
        cands.sort(key=lambda x: (-x[0][0], not x[0][1], -x[0][2], -x[0][3], x[0][4], -x[0][5]))
        for _sk, r, name, a, b, _reps, c in cands:
            named.add(id(c))
        # Surface each source file's best take first before secondary retakes in the same file
        diverse, seen_sources = [], set()
        for candidate in cands:
            if candidate[2] not in seen_sources:
                diverse.append(candidate)
                seen_sources.add(candidate[2])
        selected = diverse + [c for c in cands if c not in diverse]
        shown = selected[: max(limit, len(diverse))]
        for _sk, r, name, a, b, reps, c_obj in shown:
            voice_note = ""
            vh = c_obj.get("clip_voice_hint") if isinstance(c_obj, dict) else None
            if isinstance(vh, dict):
                ov = [
                    (heard, dis)
                    for ws0, ws1, heard, dis in (vh.get("spans") or [])
                    if min(ws1, float(b)) > max(ws0, float(a)) - 0.02
                ]
                if ov:
                    cand_voice = Counter(h for h, _ in ov).most_common(1)[0][0]
                    has_dis = any(dis for _, dis in ov)
                else:
                    cand_voice = str(vh.get("speaker") or "")
                    has_dis = False
                if cand_voice:
                    line_spk = str(l.get("speaker") or "").strip()
                    dur_cand = float(b) - float(a)
                    if has_dis and dur_cand < 2.5 and vh.get("speaker") == line_spk:
                        voice_note = f" [voice={line_spk}? SHORT_VOICE]"
                    elif line_spk and cand_voice != line_spk and dur_cand < 2.5 and vh.get("speaker") != cand_voice:
                        voice_note = f" [voice={cand_voice} SHORT_VOICE]"
                    else:
                        voice_note = f" [voice={cand_voice}]"
            rep_note = ""
            if reps:
                rp0 = reps[0]
                rep_note = (
                    f"  [REPEAT @ {rp0['first_start']:.2f}s — split cut around stumble "
                    f"('{rp0['phrase']}' {rp0['first_start']:.2f}-{rp0['first_end']:.2f} & {rp0['second_start']:.2f}-{rp0['second_end']:.2f})]"
                )
            out.append(f"  CAND {name} {float(a):.2f}-{float(b):.2f} heard={r:.2f}{voice_note}{rep_note}")
        if len(cands) > len(shown):
            out.append(f"  +{len(cands) - len(shown)} alternate candidates (use `edit_packet.py <project> --line {l['id']}`)")
        if not cands:
            out.append("  NO CLIP says this line (inspect measured footage; report reshoot need or record `accept` if non-spoken)")
    spare = [(c, ws) for c, ws, _ in prep if id(c) not in named and ws and not c.get("is_listening")]
    if spare:
        out += ["", "## UNMATCHED SPEECH (slate, ad-lib, or noisy transcript)"]
        for c, ws in spare:
            out.append(f"{Path(str(c['src'])).name}: {' '.join(str(w.get('w', '')) for w in ws[:14])}")
    return "\n".join(out) + "\n"


def voice_problems(plan: Dict[str, Any]) -> Tuple[List[str], str]:
    """Verify that the acoustic voice inside each cut matches its `sp=\"...\"` label."""
    import speaker_check
    from common import read_json
    from prepare import compute_10ms_rms_envelope

    items, ids = [], {}
    for n, s in enumerate(plan.get("segments", [])):
        if _segment_is_muted(s) or _segment_is_beat(s):
            continue
        who = str(s.get("speaker") or "")
        wdir = _resolve_clip_cache_dir(s.get("src", ""))
        f = _word_file(s.get("src", ""))
        wav = wdir / "audio16k.wav"
        if not who or " / " in who or f is None or not wav.is_file():
            continue
        st, en = float(s["start"]), float(s["end"])
        ws = [w for w in read_json(f) if float(w.get("end", 0)) > st + 0.08 and float(w.get("start", 0)) < en - 0.08]
        if ws:
            ids[n] = str(s.get("id"))
            items.append({
                "id": n,
                "wav": wav,
                "words": ws,
                "expected": [who] * len(ws),
                "rms_env": compute_10ms_rms_envelope(wav),
            })
    if len({i["expected"][0] for i in items}) < 2:
        return [], ""
    res = speaker_check.analyze(items)
    if not res.get("ok"):
        return [], f"voices not checked: {res.get('reason')}"
    by_id = {i["id"]: i for i in items}
    src_of = {n: str(plan["segments"][n].get("src")) for n in ids}
    disagree = {n for n, info in res.get("clips", {}).items() if info.get("spans")}
    long_disagree = {
        n
        for n, info in res.get("clips", {}).items()
        for sp_item in (info.get("spans") or [])
        if by_id.get(n)
        and by_id[n]["words"][sp_item["first"] : sp_item["last"] + 1]
        and (float(by_id[n]["words"][sp_item["last"]]["end"]) - float(by_id[n]["words"][sp_item["first"]]["start"])) >= 2.5
    }
    out, doubts = [], []
    for n, info in res.get("clips", {}).items():
        for sp in info.get("spans", []):
            ws = by_id[n]["words"][sp["first"] : sp["last"] + 1]
            span_sec = (float(ws[-1]["end"]) - float(ws[0]["start"])) if ws else 0.0
            same_file = [m for m in ids if m != n and src_of[m] == src_of[n]]
            agreed_same = [m for m in same_file if m not in disagree and by_id[m]["expected"][0] == sp.get("expected")]
            msg = f"{ids.get(n, n)} is labelled {sp.get('expected')}, but the voice matches {sp.get('heard', 'another speaker')}"
            if (
                span_sec < 2.5
                and agreed_same
                and len(agreed_same) * 2 >= len(same_file)
                and not any(m in long_disagree for m in same_file)
            ):
                if len(agreed_same) == len(same_file):
                    doubts.append(
                        f"{msg} (only {span_sec:.1f}s of speech; the file's other {len(same_file)} turn(s) match: verify by ear)"
                    )
                else:
                    doubts.append(
                        f"{msg} (only {span_sec:.1f}s of speech; {len(agreed_same)} of {len(same_file)} other turn(s) in this file match: verify by ear)"
                    )
            else:
                if span_sec < 2.5:
                    out.append(
                        f"{ids.get(n, n)} [SHORT VOICE REVIEW] {span_sec:.2f}s: expected "
                        f"{sp.get('expected')}, classifier suggests {sp.get('heard')}. "
                        "Verify this source once by ear/picture before accepting or changing takes."
                    )
                else:
                    out.append(msg)
    return out, ("voice doubt: " + "; ".join(doubts)) if doubts else ""


def cut_problems(plan: Dict[str, Any]) -> List[str]:
    """Flag cut start/end boundaries that slice through active spoken words."""
    from prepare import words_in_silence

    out: List[str] = []
    for s in plan.get("segments", []):
        if _segment_is_muted(s) or _segment_is_beat(s):
            continue
        ws = _words_of(s)
        if not ws:
            continue
        env = None
        ghost_ids: Optional[set] = None
        spoken = _speech_spans_for(s)
        for w in ws:
            if w.get("no_sound"):
                continue
            a, b = float(w["start"]), float(w["end"])
            for name, t in (("start", float(s["start"])), ("end", float(s["end"]))):
                if a + 0.035 < t < b - 0.035:
                    if spoken and not any(x0 - 0.03 <= t <= x1 + 0.03 for x0, x1 in spoken):
                        continue
                    if env is None:
                        env = _env_of(s)
                        ghost_ids = {id(gw) for gw in words_in_silence(ws, env)} if env else set()
                    if ghost_ids and id(w) in ghost_ids:
                        continue
                    loud = max((db for tt, db in env if t - 0.03 <= tt <= t + 0.03), default=-140.0)
                    if env and loud < -50.0:
                        continue
                    out.append(f"{s.get('id')} {name} {t:.2f} cuts inside '{w.get('w')}' ({a:.2f}-{b:.2f})")
    return out


def tight_report(plan: Dict[str, Any]) -> List[str]:
    """Advisory report on loose dialogue joins, internal pauses, and excessive post-mark tails."""
    out: List[str] = []
    segs = plan.get("segments", [])
    mark_end: Dict[str, float] = {}
    marks_by_seg: Dict[str, List[Dict[str, Any]]] = {}
    for m in plan.get("marks", []):
        msid = str(m.get("segment"))
        m_at = float(m.get("at", 0.0))
        m_dur = float(m["duration"]) if m.get("duration") is not None else MARK_TAIL_MAX_SEC
        mark_end[msid] = max(mark_end.get(msid, 0.0), m_at + m_dur)
        marks_by_seg.setdefault(msid, []).append(m)
    rows = []
    for s in segs:
        st, en = float(s["start"]), float(s["end"])
        if _segment_is_muted(s) or _segment_is_beat(s):
            ws = []
        else:
            ws = [w for w in _words_of(s) if float(w["end"]) > st + 0.05 and float(w["start"]) < en - 0.05]
        rows.append((s, st, en, ws))
    for k, (s, st, en, ws) in enumerate(rows):
        sid = str(s.get("id"))
        seg_dur = _segment_duration(s)
        play_dur = _segment_play_duration(s)
        for m in marks_by_seg.get(sid, []):
            m_at = float(m.get("at", 0.0))
            post_mark = seg_dur - m_at
            if post_mark > MARK_TAIL_MAX_SEC + 0.02:
                out.append(
                    f"{sid}: {post_mark:.2f}s tail after mark onset at {m_at:.2f}s "
                    f"(keep <= {MARK_TAIL_MAX_SEC:.2f}s; end {sid} near {_seg_to_src_time(s, m_at + MARK_TAIL_MAX_SEC):.2f})"
                )
        if not ws:
            continue
        lead = max(0.0, _src_to_seg_time(s, float(ws[0]["start"])))
        w_last_seg = _src_to_seg_time(s, float(ws[-1]["end"]))
        tail = max(0.0, play_dur - w_last_seg)
        tail_free = max(0.0, play_dur - max(w_last_seg, mark_end.get(sid, 0.0))) if sid in mark_end else tail
        nxt = rows[k + 1] if k + 1 < len(rows) else None
        if nxt and nxt[3]:
            nxt_lead = max(0.0, _src_to_seg_time(nxt[0], float(nxt[3][0]["start"])))
            if tail_free + nxt_lead > TIGHT_JOIN_SEC:
                out.append(
                    f"{sid} -> {nxt[0].get('id')}: {tail_free + nxt_lead:.2f}s silence at join "
                    f"(tail {tail_free:.2f}s + lead {nxt_lead:.2f}s): end {sid} at {en - max(0.0, tail_free - 0.06):.2f} or trim {nxt[0].get('id')} start"
                )
        lay = str(s.get("layout") or plan.get("preset") or "single").split(":")[0].strip().lower()
        pause_lim = 0.50 if (lay in ("split_h", "split_v", "top_pip") or plan.get("page")) else TIGHT_PAUSE_SEC
        for w0, w1 in zip(ws, ws[1:]):
            gap = _src_to_seg_time(s, float(w1["start"])) - _src_to_seg_time(s, float(w0["end"]))
            if gap > pause_lim:
                out.append(
                    f"{sid}: {gap:.2f}s pause between '{w0.get('w')}' and '{w1.get('w')}' at "
                    f"{float(w0['end']):.2f}-{float(w1['start']):.2f}: split the cut or accept as a natural beat"
                )
        if k == 0 and lead > 0.20:
            out.append(f"{sid}: starts {lead:.2f}s before the first spoken word")
    return out


def variety_report(plan: Dict[str, Any]) -> List[str]:
    """Advisory check against overusing a single SFX asset or repeating one transition kind across the edit."""
    out: List[str] = []
    sfx = [
        str(e.get("file", "")).lower()
        for e in (plan.get("sfx", []) or [])
        if isinstance(e, dict) and e.get("file")
    ]
    if len(sfx) >= VARIETY_MIN_USES:
        name, n = Counter(sfx).most_common(1)[0]
        if n / len(sfx) > VARIETY_MAX_SHARE and n >= VARIETY_MIN_USES:
            out.append(
                f"{name} accounts for {round(100 * n / len(sfx))}% of sound effects ({n}/{len(sfx)}); "
                "mix in pop, click, sn_punch_1/2, sn_swish_1, w42_swoosh, or kn20228_*"
            )

    tr_kinds = [
        k
        for k in (_tr_kind(s) for s in (plan.get("segments", []) or []) if isinstance(s, dict))
        if k not in ("", "cut")
    ]
    total_tr = len(tr_kinds)
    if total_tr >= TR_VARIETY_MIN_USES:
        tr_counts = Counter(tr_kinds)
        top_tr, top_n = tr_counts.most_common(1)[0]
        distinct_tr = len(tr_counts)
        if top_n / total_tr > TR_VARIETY_MAX_SHARE or distinct_tr < 2:
            out.append(
                f"transition '{top_tr}' accounts for {round(100 * top_n / total_tr)}% of transitions "
                f"({top_n}/{total_tr}, {distinct_tr} distinct kind(s)); vary transition kinds across the edit"
            )
    return out


def _normalize_registry(raw: Any) -> Dict[str, Any]:
    """Normalize `formats/registry.json` regardless of list-based or dict-based `formats` schema."""
    if not isinstance(raw, dict):
        return dict(_DEFAULT_REGISTRY)
    raw_formats = raw.get("formats")
    norm_list: List[Dict[str, Any]] = []
    if isinstance(raw_formats, list):
        for item in raw_formats:
            if isinstance(item, dict) and item.get("id"):
                entry = dict(item)
                if "kinds" not in entry:
                    entry["kinds"] = list(entry.get("transitions") or entry.get("allowed_transitions") or [])
                if "names" not in entry:
                    entry["names"] = list(entry.get("aliases") or entry.get("keywords") or [])
                norm_list.append(entry)
    elif isinstance(raw_formats, dict):
        for fid, item in raw_formats.items():
            if isinstance(item, dict):
                entry = {"id": str(item.get("id") or fid), **item}
                if "kinds" not in entry:
                    entry["kinds"] = list(entry.get("transitions") or entry.get("allowed_transitions") or [])
                if "names" not in entry:
                    entry["names"] = list(entry.get("aliases") or entry.get("keywords") or [])
                norm_list.append(entry)
    if not norm_list:
        norm_list = list(_DEFAULT_REGISTRY["formats"])
    return {
        "formats": norm_list,
        "flash_kinds": list(raw.get("flash_kinds") or _DEFAULT_REGISTRY["flash_kinds"]),
        "same_shot_structure": float(raw.get("same_shot_structure", _DEFAULT_REGISTRY["same_shot_structure"])),
    }


@lru_cache(maxsize=1)
def load_edit_formats() -> Dict[str, Any]:
    """Load format transition rules from `formats/registry.json` (falling back to built-in defaults)."""
    if REGISTRY_FILE.is_file():
        try:
            return _normalize_registry(json.loads(REGISTRY_FILE.read_text(encoding="utf-8")))
        except Exception:
            pass
    return dict(_DEFAULT_REGISTRY)


def match_edit_format(text: str) -> Optional[Dict[str, Any]]:
    """Resolve the catalog format named on line 1 of `script_analysis.md` (`Format: <id> (...)`)."""
    low = (text or "").lower()
    fmts = [f for f in load_edit_formats().get("formats", []) if not f.get("generation_only")]
    for f in fmts:
        if re.search(r"(?<![a-z0-9_])" + re.escape(str(f["id"]).lower()) + r"(?![a-z0-9_])", low):
            return f

    def found(n: str) -> bool:
        return bool(re.search(r"(?<![a-z0-9])" + re.escape(n.lower()) + r"(?![a-z0-9])", low))

    for key in ("names", "fallback_names"):
        best, best_len = None, 0
        for f in fmts:
            for n in f.get(key) or []:
                if len(n) > best_len and found(str(n)):
                    best, best_len = f, len(n)
        if best:
            return best
    return None


def plan_edit_format(work_dir: Optional[Path]) -> Optional[Dict[str, Any]]:
    """Read the active edit format declared on the first non-empty line of `script_analysis.md`."""
    if work_dir is None:
        return None
    try:
        for line in (Path(work_dir) / "script_analysis.md").read_text(encoding="utf-8").splitlines():
            line = line.strip().lstrip("#").strip()
            if line:
                return match_edit_format(line) if line.lower().startswith("format") else None
    except Exception:
        return None
    return None


_TRANSITION_ALIASES = {
    "smoothleft": "whip_left",
    "smoothright": "whip_right",
    "whip_up": "slide_up",
    "smoothup": "slide_up",
    "whip_down": "slide_down",
    "smoothdown": "slide_down",
}


def _tr_kind(seg: Dict[str, Any]) -> str:
    raw = str(seg.get("transition") or "cut").split("@")[0].strip().lower()
    return _TRANSITION_ALIASES.get(raw, raw)


def _layout_family(seg: Dict[str, Any]) -> str:
    lay = str(seg.get("layout") or "single").split(":")[0].strip().lower()
    return "split" if lay in SPLIT_FAMILY else lay


def _shot_similarity(a: Dict[str, Any], b: Dict[str, Any]) -> Optional[float]:
    """Normalized greyscale correlation between the tail frame of `a` and head frame of `b`."""
    try:
        import cv2
        import numpy as np
        from common import resolve
        from vision_tracker import _read_gray_frame

        fa = _read_gray_frame(resolve(str(a["src"])), max(0.0, float(a["end"]) - 0.08))
        fb = _read_gray_frame(resolve(str(b["src"])), float(b["start"]) + 0.08)
        if fa is None or fb is None:
            return None
        ga = cv2.GaussianBlur(cv2.resize(fa, (72, 128), interpolation=cv2.INTER_AREA), (9, 9), 0).astype(np.float32)
        gb = cv2.GaussianBlur(cv2.resize(fb, (72, 128), interpolation=cv2.INTER_AREA), (9, 9), 0).astype(np.float32)
        za = (ga - ga.mean()) / (ga.std() + 1e-6)
        zb = (gb - gb.mean()) / (gb.std() + 1e-6)
        return float((za * zb).mean())
    except Exception:
        return None


def mid_sentence_transition_problems(plan: Dict[str, Any]) -> List[str]:
    """Flag non-cut transitions landing between two segments sharing the same script line ID (hard block)."""
    segs = [s for s in plan.get("segments", []) if isinstance(s, dict)]
    out: List[str] = []
    for i in range(1, len(segs)):
        prev, s = segs[i - 1], segs[i]
        kind = _tr_kind(s)
        if kind in ("", "cut"):
            continue
        prev_sid = str(prev.get("id") or "")
        sid = str(s.get("id") or "")
        prev_lids = _line_ids_of(prev_sid)
        cur_lids = _line_ids_of(sid)
        la = prev_lids[-1].upper() if prev_lids else ""
        lb = cur_lids[0].upper() if cur_lids else ""
        if not la or la != lb:
            continue
        fam_a, fam_b = _layout_family(prev), _layout_family(s)
        if fam_a != fam_b and kind == "split_reveal":
            continue
        out.append(
            f"{prev_sid} -> {sid}: {kind} inside line {lb} (mid-sentence pause cut); use tr=cut instead"
        )
    return out


def transition_report(
    plan: Dict[str, Any],
    lines: Optional[List[Dict[str, Any]]] = None,
    work_dir: Optional[Path] = None,
    similarity: Any = None,
) -> List[str]:
    """Validate transitions against story/scene changes and the format's transition budget in `formats/registry.json`."""
    cfg = load_edit_formats()
    fmt = plan_edit_format(work_dir)
    flash_kinds = set(cfg.get("flash_kinds") or [])
    same_cut = float(cfg.get("same_shot_structure", 0.70))
    sim_fn = similarity or _shot_similarity
    scene_of = {str(l.get("id")): str(l.get("scene") or "") for l in (lines or []) if isinstance(l, dict)}
    text_of = {
        str(l.get("id")).upper(): str(l.get("text") or "").strip()
        for l in (lines or [])
        if isinstance(l, dict) and l.get("id")
    }
    segs = [s for s in plan.get("segments", []) if isinstance(s, dict)]
    out: List[str] = []
    trans: List[Tuple[str, str]] = []
    for i in range(1, len(segs)):
        prev, s = segs[i - 1], segs[i]
        kind = _tr_kind(s)
        if kind in ("", "cut"):
            continue
        sid = str(s.get("id"))
        trans.append((sid, kind))
        fam_a, fam_b = _layout_family(prev), _layout_family(s)
        if fam_a != fam_b:
            continue
        if fam_b == "split":
            continue
        prev_lids = _line_ids_of(str(prev.get("id")))
        cur_lids = _line_ids_of(sid)
        la = prev_lids[-1] if prev_lids else _line_id_of(str(prev.get("id")))
        lb = cur_lids[0] if cur_lids else _line_id_of(sid)
        if la and la.upper() == lb.upper():
            out.append(
                f"{sid}: {kind} inside line {lb} (pause cut); use tr=cut with a 1.08 step zoom instead"
            )
            continue
        # Advisory when a non-cut transition lands between consecutive lines L_n -> L_{n+1} mid-sentence
        m_a = re.fullmatch(r"L(\d+)", la, re.IGNORECASE)
        m_b = re.fullmatch(r"L(\d+)", lb, re.IGNORECASE)
        if m_a and m_b and int(m_b.group(1)) == int(m_a.group(1)) + 1:
            prev_text = text_of.get(la.upper(), "")
            stripped = prev_text.rstrip(" \t\r\n\"'”’)]")
            if stripped and stripped[-1] not in ".?!।॥":
                ends_comma = stripped.endswith((",", ";", ":", "—", "–", "-"))
                parts = stripped.split()
                last_tok = _fallback_clean_token(parts[-1]) if parts else ""
                ends_conj = last_tok in _MID_CLAUSE_CONJUNCTIONS
                if ends_comma or ends_conj:
                    out.append(
                        f"{sid}: {kind} between mid-sentence lines {la} -> {lb} "
                        f"({la} ends without terminal punctuation); prefer tr=cut inside a continuous sentence"
                    )
                    continue
        sc_a, sc_b = scene_of.get(la, ""), scene_of.get(lb, "")
        is_hook_a = _segment_is_muted(prev) or _segment_is_beat(prev) or abs(_segment_speed(prev) - 1.0) > 1e-3
        is_hook_b = _segment_is_muted(s) or _segment_is_beat(s) or abs(_segment_speed(s) - 1.0) > 1e-3
        if is_hook_a != is_hook_b:
            continue
        same_src = str(prev.get("src")) == str(s.get("src"))
        sim = None if same_src else sim_fn(prev, s)
        same_shot = same_src or (sim is not None and sim >= same_cut)
        if sc_a and sc_b and sc_a != sc_b and not same_shot:
            continue
        if same_shot:
            how = "the same clip" if same_src else f"the same shot (similarity {sim:.2f})"
            where = f" in {sc_b}" if sc_b and sc_a == sc_b else ""
            out.append(f"{sid}: {kind} between two cuts of {how}{where}; use tr=cut")
    flashes = [sid for sid, k in trans if k in flash_kinds]
    if len(flashes) > 1:
        out.append(f"{len(flashes)} flash transitions ({', '.join(flashes)}): keep at most one on an explicit script cue")
    if fmt:
        kinds = set(fmt.get("kinds") or [])
        odd = [f"{sid}={k}" for sid, k in trans if kinds and k not in kinds and k not in flash_kinds]
        if odd:
            allowed_str = ", ".join(sorted(kinds))
            out.append(
                f"format {fmt['id']} does not use {', '.join(odd)} (allowed in formats/catalog/{fmt['id']}.md: {allowed_str})"
            )
        cap = fmt.get("max_transitions")
        if cap is not None and len(trans) > int(cap):
            out.append(
                f"{len(trans)} transitions in a {fmt['id']} edit (max {int(cap)} in formats/catalog/{fmt['id']}.md): use hard cuts for the rest"
            )
    return out


def style_report(plan: Dict[str, Any], work_dir: Optional[Path] = None) -> List[str]:
    """Advisory check on `script_analysis.md`, zoom cadence, stillness contrast, subtitle diversity, and SFX assets."""
    out: List[str] = []
    segs = plan.get("segments", [])
    if work_dir is not None and not (Path(work_dir) / "script_analysis.md").is_file():
        out.append(
            "missing script_analysis.md: write the emotion and beat plan in the project work folder before finalizing plan.edl"
        )
    from common import resolve_sfx_asset

    rapid, flash, singles, still = [], 0, 0, 0
    prev_rapid = False
    live_total, live_cuts, live_moving = 0.0, 0, 0
    for s in segs:
        lay = str(s.get("layout") or "single").lower()
        zstyle = str(s.get("zoom_style") or "").strip().lower()
        z0 = float(s.get("zoom") if s.get("zoom") is not None else 1.0)
        z1 = float(s.get("zoom_to") if s.get("zoom_to") is not None else z0)
        zpeak = float(s.get("zoom_peak") or 0.0)
        max_z = max(z0, z1, zpeak)
        moves = (
            zstyle in RAPID_ZOOM_STYLES
            or zstyle in ("bell", "sudden_out")
            or (zstyle == "smooth" and abs(z1 - z0) > 1e-6)
            or abs(z1 - z0) > 1e-6
            or (zpeak > 1.0 + 1e-6 and abs(zpeak - z0) > 1e-6)
        )
        has_punch_crop = abs(max_z - 1.0) > 0.015
        dur = _segment_duration(s)
        if lay in ("split_h", "split_v", "top_pip"):
            if moves or has_punch_crop:
                z_desc = f"{z0:.2f}->{zpeak:.2f}->{z1:.2f}" if zstyle == "bell" and zpeak > 0 else f"{z0:.2f}->{z1:.2f}"
                out.append(f"{s.get('id')}: {lay} specifies zoom ({z_desc}); split layouts stay locked at z=1")
        if lay == "single":
            live_total += dur
            if dur >= 1.0:
                live_cuts += 1
                live_moving += 1 if (moves or has_punch_crop) else 0
            if dur >= 1.5:
                singles += 1
                # Count only calm z=1 cuts as still; do not double-count static punch-in (z0==z1>1.0) as both moving and still.
                still += 0 if (moves or has_punch_crop) else 1
            is_rapid = zstyle in RAPID_ZOOM_STYLES
            if is_rapid and prev_rapid:
                out.append(f"{s.get('id')}: back-to-back rapid zoom; change one cut to a slow push or z=1")
            prev_rapid = is_rapid
            if is_rapid:
                rapid.append(s)
            if zstyle == "flash_zoom":
                flash += 1
        else:
            prev_rapid = False

    if flash > 1:
        out.append(f"{flash} flash zooms: reserve flash_zoom for the single strongest reveal")
    per_min = len(rapid) / (max(live_total, 1.0) / 60.0)
    if per_min > MAX_RAPID_PER_MIN:
        out.append(
            f"{len(rapid)} rapid zooms across {live_total:.0f}s of full-frame speech ({per_min:.1f}/min; cap {MAX_RAPID_PER_MIN}/min)"
        )
    if live_total >= 10.0 and live_cuts >= 3:
        if live_moving / live_cuts < AD_MIN_MOVING_SHARE:
            out.append(
                f"only {live_moving}/{live_cuts} full-frame cuts move: aim for >= {int(AD_MIN_MOVING_SHARE * 100)}% motion on single-frame dialogue"
            )
    if singles >= 4 and still / singles < MIN_STILL_SHARE:
        out.append(
            f"only {still}/{singles} single-speaker cuts stay still: keep >= {int(MIN_STILL_SHARE * 100)}% at z=1 for contrast"
        )

    sp = plan.get("subs")
    if isinstance(sp, dict) and sp.get("style") and str(sp["style"]).strip().lower() != "auto":
        try:
            import subtitles

            named, last = subtitles.resolve_style(sp["style"]), subtitles.recent_styles(n=1)
            if named is not None and last and named["name"] == last[0]:
                out.append(
                    f"subtitle style '{named['name']}' matches the previous render; use `#subs auto` or select another preset"
                )
        except Exception:  # noqa: BLE001
            pass

    ids = {str(s.get("id")): s for s in segs}
    sfx_pack = plan.get("sfx_pack")
    for e in plan.get("sfx", []):
        name, seg = str(e.get("file")), ids.get(str(e.get("segment")))
        try:
            known = resolve_sfx_asset(name, pack=sfx_pack).is_file()
        except Exception:
            known = False
        if not known:
            out.append(f"SFX '{name}' on {e.get('segment')} is missing from assets/sfx")
        if float(e.get("gain_db", -8)) > 0:
            out.append(f"SFX '{name}' on {e.get('segment')} exceeds 0 dB: keep effects below voice level")
        if seg is not None and float(e.get("at", 0)) > _segment_duration(seg):
            out.append(f"SFX '{name}' on {e.get('segment')} starts after the cut ends")
    return out


def _snap_target(env: List[Tuple[float, float]], lo: float, hi: float, anchor: float) -> Tuple[float, float]:
    """Return `(best_t, min_db)` in `[lo, hi]`, preferring the `-42 dB` noise-floor frame closest to `anchor`."""
    from prepare import find_rms_minimum

    floor_cands = [(tt, db) for tt, db in env if lo <= tt <= hi and db <= -42.0]
    if floor_cands:
        return min(floor_cands, key=lambda x: (abs(x[0] - anchor), x[1]))
    return find_rms_minimum(env, lo, hi)


def snap_cuts(plan: Dict[str, Any]) -> int:
    """Nudge inter-word cuts and minor word-edge grazes (<=0.08s) onto the quietest 10ms point in the adjacent gap."""
    from prepare import compute_10ms_rms_envelope

    moved = 0
    for s in plan.get("segments", []):
        if not isinstance(s, dict) or "start" not in s or "end" not in s:
            continue
        if _segment_is_muted(s) or _segment_is_beat(s):
            continue
        ws = _words_of(s)
        wav = _resolve_clip_cache_dir(s.get("src", "")) / "audio16k.wav"
        if not ws or not wav.is_file():
            continue
        env = compute_10ms_rms_envelope(wav)
        spoken = _speech_spans_for(s)
        dur_clip = max((t for t, _ in env), default=float(ws[-1]["end"]) + 0.5)
        for key in ("start", "end"):
            t = float(s[key])
            local_cands = [db for tt, db in env if t - 0.02 <= tt <= t + 0.02]
            local_db = max(local_cands) if local_cands else None
            snapped = False
            for w0, w1 in zip(ws, ws[1:]):
                a, b = float(w0["end"]), float(w1["start"])
                if a <= t <= b and b - a >= 0.03:
                    if local_db is not None and local_db <= -42.0:
                        snapped = True
                        break
                    mid_ab = 0.5 * (a + b)
                    gap_lo = min(mid_ab, a + 0.03)
                    gap_hi = max(mid_ab, b - 0.03)
                    lo, hi = max(gap_lo, t - SNAP_REACH_SEC), min(gap_hi, t + SNAP_REACH_SEC)
                    if lo > hi:
                        lo = hi = mid_ab
                    new_t, min_db = _snap_target(env, lo, hi, t)
                    new = round(float(new_t), 2)
                    if new != round(t, 2) and (local_db is None or min_db < local_db - 2.0):
                        s[key] = new
                        moved += 1
                    snapped = True
                    break
            if snapped:
                continue
            for idx_w, w in enumerate(ws):
                wa, wb = float(w["start"]), float(w["end"])
                if not (wa < t < wb) or (wb - wa) < EDGE_GRAZE_MAX_SEC:
                    continue
                if spoken and not any(x0 - 0.03 <= t <= x1 + 0.03 for x0, x1 in spoken):
                    continue
                if env and max((db for tt, db in env if t - 0.03 <= tt <= t + 0.03), default=-140.0) < -50.0:
                    continue
                other = float(s["end"]) if key == "start" else float(s["start"])
                dist_a = t - wa
                dist_b = wb - t
                near_left = dist_a <= EDGE_GRAZE_MAX_SEC
                near_right = dist_b <= EDGE_GRAZE_MAX_SEC
                if near_left and (not near_right or dist_a <= dist_b):
                    prev_end = float(ws[idx_w - 1]["end"]) if idx_w > 0 else 0.0
                    if prev_end <= wa:
                        mid_gap = 0.5 * (prev_end + wa) if idx_w > 0 else 0.0
                        hi = max(mid_gap, wa - 0.03)
                        safe_prev = min(mid_gap, prev_end + 0.03) if idx_w > 0 else 0.0
                        lo = min(hi, max(safe_prev, wa - SNAP_REACH_SEC))
                        new = round(float(_snap_target(env, lo, hi, t)[0]) if env and hi > lo else hi, 2)
                        if (key == "start" and new < other - 0.05) or (key == "end" and new > other + 0.05):
                            if new != round(t, 2):
                                s[key] = new
                                moved += 1
                    break
                if near_right:
                    next_start = float(ws[idx_w + 1]["start"]) if idx_w + 1 < len(ws) else dur_clip
                    if wb <= next_start:
                        mid_gap = 0.5 * (wb + next_start) if idx_w + 1 < len(ws) else dur_clip
                        lo = min(mid_gap, wb + 0.03)
                        safe_next = max(mid_gap, next_start - 0.03) if idx_w + 1 < len(ws) else dur_clip
                        hi = max(lo, min(safe_next, wb + SNAP_REACH_SEC))
                        new = round(float(_snap_target(env, lo, hi, t)[0]) if env and hi > lo else lo, 2)
                        if (key == "start" and new < other - 0.05) or (key == "end" and new > other + 0.05):
                            if new != round(t, 2):
                                s[key] = new
                                moved += 1
                    break
    return moved


def main() -> None:
    import edl_parser
    from common import read_json, resolve

    if len(sys.argv) < 2:
        print("Usage: python scripts/script_order.py <work/project/plan.edl>", file=sys.stderr)
        sys.exit(2)
    p = resolve(sys.argv[1])
    plan = edl_parser.load_edl(p)
    side = p.parent / "plan.json"
    sp = plan.get("script") or (read_json(side).get("script") if side.is_file() else None)
    if not sp:
        print("[ORDER] Plan specifies no #script header; cannot validate script order.")
        sys.exit(2)
    g = full_gate(plan, resolve(sp), p.parent, p)
    for a in g["report"]["assigned"]:
        print(f"  {a['id']:<8} -> {a['line'] or '?':<6} fit={a['fit']}")
    print(format_gate(g))
    if g.get("voice_note"):
        print(f"[VOICE] {g['voice_note']}")
    sys.exit(2 if g["blocked"] else 0)


if __name__ == "__main__":
    main()
