#!/usr/bin/env python3
"""Measurements and safety checks for the Director's plan. This file decides nothing about the edit itself.

The script is the master timeline. This module measures, from the words actually inside each cut:
- which script line each segment says, and whether the plan follows script order,
- which lines are missing, repeated or not proven,
- every clip that could supply each line (`line_map.md`, the Director's main input).

`gate()` is what `zeno.py --execute` runs: a plan with an open problem does not render. The Director resolves each problem in
`plan.edl`, or records a deliberate exception in `work/<project>/decisions.txt`:

    accept L7 script says this line is dropped for time

    python3 scripts/zeno.py --check-plan work/<project>/plan.edl     # exit 2 if the plan would be blocked
"""
from __future__ import annotations

import re
import sys
from difflib import SequenceMatcher
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

sys.path.insert(0, str(Path(__file__).resolve().parent))

WORD_MATCH = 0.64     # same threshold the planner uses for "this is the same word"
FIT_MIN = 0.40        # below this a segment is not proven to say a line
TIE = 0.08            # lines this close to the best fit are treated as equally likely (repeated wording in a script)
HEARD_MIN = 0.50      # share of a line's words that must be heard for the line to count as present
STRONG = 0.55         # evidence needed to call a segment out of order from its words alone
TIGHT_JOIN_SEC = 0.18     # silence across a join (end of one cut + start of the next) above this is reported
TIGHT_PAUSE_SEC = 0.25    # a pause between two words inside one cut above this is reported
MARK_TAIL_MAX_SEC = 0.48  # tail after a mark onset above this is reported
RAPID_ZOOM_STYLES = ('sudden_in', 'flash_zoom')
MAX_RAPID_PER_MIN = 16       # rapid zooms a minute of full-frame live conversation (Meta-ad tempo, owner decision 2026-11-06)
AD_MIN_MOVING_SHARE = 0.60   # of the live conversation's cuts, at least this share should move (a zoom, or a transition into the cut)
AD_SECONDS_PER_TRANSITION = 8.5   # aim for a crisp transition about this often in the live conversation
MIN_STILL_SHARE = 0.25       # at least this share of single-speaker cuts should not move, so a zoom means something
SNAP_REACH_SEC = 0.12   # a cut may move at most this far to reach a quiet point
REPEAT_RECALL = 0.8   # two separate segments that each hold this much of a line say it twice

STUTTER_SIM_MIN = 0.82
STUTTER_GAP_MAX_SEC = 1.5
RESTART_AVG_SIM_MIN = 0.80
RESTART_MIN_WORD_SIM = 0.68
RESTART_GAP_MAX_SEC = 6.0
RESTART_GAP_MAX_TOKENS = 10
MAX_REPEAT_NGRAM = 6

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

_INDIC_LONG_TO_SHORT_MATRAS = str.maketrans({
    # Devanagari ी->ि, ू->ु
    "\u0940": "\u093F", "\u0942": "\u0941",
    # Bengali
    "\u09C0": "\u09BF", "\u09C2": "\u09C1",
    # Gujarati
    "\u0AC0": "\u0ABF", "\u0AC2": "\u0AC1",
    # Oriya
    "\u0B40": "\u0B3F", "\u0B42": "\u0B41",
    # Tamil ீ->ி, ூ->ு, ே->ெ, ோ->ொ
    "\u0BC0": "\u0BBF", "\u0BC2": "\u0BC1", "\u0BC7": "\u0BC6", "\u0BCB": "\u0BCA",
    # Telugu ీ->ి, ూ->ు, ే->ె, ో->ొ
    "\u0C40": "\u0C3F", "\u0C42": "\u0C41", "\u0C47": "\u0C46", "\u0C4B": "\u0C4A",
    # Kannada ೀ->ಿ, ೂ->ು, ೇ->ೆ, ೋ->ೊ
    "\u0CC0": "\u0CBF", "\u0CC2": "\u0CC1", "\u0CC7": "\u0CC6", "\u0CCB": "\u0CCA",
    # Malayalam ീ->ി, ൂ->ു, േ->െ, ോ->ൊ
    "\u0D40": "\u0D3F", "\u0D42": "\u0D41", "\u0D47": "\u0D46", "\u0D4B": "\u0D4A",
})

_INDIC_VOWEL_SIGN_RE = re.compile(
    r"^.(?:[\u093E-\u094C\u09BE-\u09CC\u0ABE-\u0ACC\u0B3E-\u0B4C\u0BBE-\u0BCC\u0C3E-\u0C4C\u0CBE-\u0CCC\u0D3E-\u0D4C])?"
)


@lru_cache(maxsize=200000)
def _sim(a: str, b: str) -> float:
    from prepare import _word_similarity

    return _word_similarity(a, b)


def clean(words: List[str]) -> List[str]:
    from prepare import _clean_token

    return [t for t in (_clean_token(w) for w in words) if t]


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
    """High-precision token similarity for repeat/stutter detection (avoids minimal-pair false positives like want/went or ನನ್ನ/ನಿನ್ನ)."""
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    if a.isascii() and b.isascii():
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
        ok = (sims[0] >= STUTTER_SIM_MIN) if k == 1 else (min(sims) >= RESTART_MIN_WORD_SIM and (sum(sims) / k) >= RESTART_AVG_SIM_MIN)
        if ok:
            cnt += 1
            idx += k
        else:
            idx += 1
    return cnt


def find_unscripted_repeats(
    words: List[Dict[str, Any]],
    script_line_text: Union[str, List[str], None] = "",
) -> List[Dict[str, Any]]:
    """Detect unscripted 1-word stutters and 2-6 word restarted/repeated n-grams in `words`.

    Returns structured dicts:
        {
            "kind": "stutter" | "restart",
            "phrase": str,
            "size": int,
            "first_start": float,
            "first_end": float,
            "second_start": float,
            "second_end": float,
            "clean_trim_start": float,
            "first_idx": int,
            "second_idx": int,
        }
    """
    items: List[Tuple[str, Dict[str, Any]]] = []
    for w in words or []:
        if not isinstance(w, dict):
            continue
        raw_txt = str(w.get("w") or w.get("word") or w.get("text") or "").strip()
        for t in clean(raw_txt.split()):
            items.append((t, w))
    n = len(items)
    if n < 2:
        return []

    if isinstance(script_line_text, (list, tuple)):
        ref_toks = clean([str(x) for x in script_line_text])
    else:
        ref_toks = clean(str(script_line_text or "").split())

    toks = [t for t, _ in items]
    found: List[Dict[str, Any]] = []
    covered_pairs: List[Tuple[int, int, int, int]] = []

    # 1) 2..6-word restarted/repeated n-grams (longest first so sub-grams inside a 4-word restart aren't emitted multiple times)
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

    # 2) Immediate 1-word stutters (sim >= 0.82, gap < 1.5s, excluding scripted doubles & natural reduplications)
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
        spoken_pairs = sum(
            1 for z in range(n - 1)
            if _repeat_tok_sim(toks[z], t1) >= STUTTER_SIM_MIN and _repeat_tok_sim(toks[z + 1], t1) >= STUTTER_SIM_MIN
        )
        script_pairs = sum(
            1 for z in range(max(0, len(ref_toks) - 1))
            if _repeat_tok_sim(ref_toks[z], t1) >= STUTTER_SIM_MIN and _repeat_tok_sim(ref_toks[z + 1], t1) >= STUTTER_SIM_MIN
        )
        extra_allowed = 1 if (t1 in NATURAL_REDUPLICATIONS or t2 in NATURAL_REDUPLICATIONS) else 0
        if spoken_pairs > script_pairs + extra_allowed:
            f_start = round(float(w1.get("start", 0.0)), 3)
            f_end = round(float(w1.get("end", 0.0)), 3)
            s_start = round(float(w2.get("start", 0.0)), 3)
            s_end = round(float(w2.get("end", 0.0)), 3)
            trim_start = round(min(s_start, max(f_end, s_start - 0.04)), 3)
            found.append({
                "kind": "stutter",
                "phrase": t1,
                "size": 1,
                "first_start": f_start,
                "first_end": f_end,
                "second_start": s_start,
                "second_end": s_end,
                "clean_trim_start": trim_start,
                "first_idx": i,
                "second_idx": i + 1,
            })

    found.sort(key=lambda d: (d["first_start"], d["second_start"]))
    return found


def _hits(need: List[str], have: List[str]) -> float:
    """Share of `need` found in `have`."""
    if not need or not have:
        return 0.0
    pool = set(have)
    found = 0
    for t in need:
        if t in pool or max(_sim(t, h) for h in pool) >= WORD_MATCH:
            found += 1
    return found / len(need)


def fit(tokens: List[str], line_tokens: List[str]) -> float:
    """F1 of shared words: 1.0 means the segment says exactly this line."""
    rec, prec = _hits(line_tokens, tokens), _hits(tokens, line_tokens)
    return 0.0 if rec + prec == 0 else 2 * rec * prec / (rec + prec)


def recall(tokens: List[str], line_tokens: List[str]) -> float:
    return _hits(line_tokens, tokens)


def part_fit(tokens: List[str], line_tokens: List[str]) -> float:
    """Like fit(), but a piece of a long line counts: half a line split into L7a/L7b is a good match, not a weak one."""
    f = fit(tokens, line_tokens)
    if len(tokens) >= 3 and len(tokens) < 0.8 * len(line_tokens):
        return max(f, 0.95 * _hits(tokens, line_tokens))
    return f


def place_in_script(tokens: List[str], lines: List[Dict[str, Any]]) -> Tuple[Optional[int], int, float]:
    """(script line number, word offset inside it, fit) of where this text starts in the script. None if it fits nowhere.
    Uses the earliest line the text really contains, so a clip holding L2+L3 sorts at L2."""
    if not tokens:
        return None, 0, 0.0
    scored = [(fit(tokens, l.get("tokens") or []), l) for l in lines if l.get("tokens")]
    if not scored:
        return None, 0, 0.0
    best_f, best_l = max(scored, key=lambda x: x[0])
    if best_f < 0.3:
        return None, 0, best_f
    # A clip holding L2+L3 sorts at L2, but only if it really opens with L2's words. Merely sharing common words with an
    # earlier line proves nothing: a long speech contains most of the words of any short line.
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


def _line_ids_of(seg_id: str) -> List[str]:
    """L6_L7_L8a -> [L6, L7, L8]; L14b -> [L14]; S1a or a free name -> []"""
    return re.findall(r"(?<![A-Za-z0-9])L\d+", str(seg_id or "").replace("_", " "))


def _line_id_of(seg_id: str) -> str:
    ids = _line_ids_of(seg_id)
    return ids[0] if ids else ""


def check_order(seg_rows: List[Tuple[str, List[str]]], lines: List[Dict[str, Any]]) -> Dict[str, Any]:
    """seg_rows: [(segment id, words inside the cut)] in plan order. lines: every script line, in script order."""
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
                out_of_order.append(f"{sid} comes after {lines[cur_id]['id']}, but the script has it earlier")
            cur_id = max(cur_id, max(claims))
        if not toks or (claims and len(toks) <= 2):      # silent beat or a short reaction: too few words to prove anything
            assigned.append({"id": sid, "line": lines[claims[0]]["id"] if claims else None, "fit": None})
            continue
        fits = [(part_fit(toks, l.get("tokens") or []) if l.get("tokens") else 0.0) for l in lines]
        # The plan's own label wins when the words agree with it. Two script lines can share most of their words.
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
                out_of_order.append(f"{sid} says {lines[chosen]['id']}, but it comes after {lines[cur]['id']}")
        said_by.setdefault(chosen, []).append(n)
        assigned.append({"id": sid, "line": lines[chosen]["id"], "fit": round(fits[chosen], 2)})
    for i, hits in said_by.items():      # said twice = two cuts that each hold nearly the whole line, not two halves of it
        whole = [n for n in hits if recall(seg_rows[n][1], lines[i].get("tokens") or []) >= REPEAT_RECALL]
        # In a long line with common words two different halves can each touch most of it; a true repeat says the same words.
        if len(whole) >= 2 and _hits(seg_rows[whole[0]][1], seg_rows[whole[1]][1]) >= 0.7:
            repeated.append(f"{lines[i]['id']} is said {len(whole)} times ({', '.join(seg_rows[n][0] for n in whole)})")
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
        carried = [t for sid, toks in seg_rows if l["id"] in _line_ids_of(sid) for t in toks]   # a long line split over several cuts (L6a, L6b, ...)
        heard = max(heard, recall(carried, lt))
        if heard >= HEARD_MIN:
            continue
        if l["id"] in ids_present:
            unproven.append(l["id"])
        else:
            missing.append(l["id"])
    return {"assigned": assigned, "out_of_order": out_of_order, "repeated": repeated, "missing": missing, "unproven": unproven, "unknown": unknown}


_ACTIVE_WORK_DIR: Optional[Path] = None


def _resolve_clip_cache_dir(src: Any, work_dir: Optional[Path] = None) -> Path:
    """Resolve a clip's cache folder: try `clip_work_dir(src)` first, then fall back to `clip_caches.json` if raw clips moved."""
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
            return clean([w.get("w", "") for w in ws if float(w.get("end", 0)) > st + 0.08 and float(w.get("start", 0)) < en - 0.08])
    return []


def read_accepted(work_dir: Path) -> Dict[str, str]:
    f = Path(work_dir) / "decisions.txt"
    out: Dict[str, str] = {}
    if f.is_file():
        for ln in f.read_text(encoding="utf-8", errors="ignore").splitlines():
            m = re.match(r"\s*accept\s+(\S+)\s+(\S.*)$", ln, re.I)
            if m:
                out[m.group(1)] = m.group(2).strip()
    return out


SRC_SWAP_MIN_SEC = 1.0    # a later script line cut from this much earlier in the same clip counts as swapped


def src_time_swaps(segs: List[Dict[str, Any]], lines: List[Dict[str, Any]]) -> List[str]:
    """Flow clips hold a whole conversation in script order. If a later script line is cut from EARLIER in the same clip than a
    previous line, the dialogues are swapped. check_order trusts the plan's own L-labels, so it cannot see this; the clip times can.
    Cleared by fixing the cut, or by `accept <id> <reason>` when the clip really has the lines in another order (retakes)."""
    pos = {l["id"]: i for i, l in enumerate(lines)}
    out: List[str] = []
    last: Dict[str, Tuple[int, float, str]] = {}      # src -> (script position, source start, segment id)
    for s in segs:
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
            out.append(f"{sid} is later in the script than {prev[2]} but is cut from earlier in {key} ({start:.1f}s vs {prev[1]:.1f}s): "
                       f"the dialogues look swapped; re-cut in clip order, or accept {sid} <reason> if the clip really has them this way")
        if prev is None or p >= prev[0]:
            last[key] = (max(ids), start, sid)
    return out


def gate(plan: Dict[str, Any], script_path: Any, work_dir: Path) -> Dict[str, Any]:
    """Every open problem in a plan. `blocked` is what stops a render. An item is cleared by fixing the plan, or by `accept <id> <reason>`."""
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
        "SOURCE ORDER (a later script line is cut from earlier in the same clip)": open_items(src_time_swaps(plan.get("segments", []), lines)),
        "MISSING": open_items(rep["missing"]),
        "SAID TWICE": open_items(rep["repeated"]),
        "NOT PROVEN (the words in the cut do not match this line)": open_items(rep["unproven"]),
        "UNKNOWN SEGMENT (says no script line)": open_items(rep["unknown"]),
    }
    problems = {k: v for k, v in problems.items() if v}
    return {"blocked": bool(problems), "problems": problems, "accepted": ok, "report": rep}


def unread_plan_lines(plan: Dict[str, Any], plan_path: Path) -> List[str]:
    """Lines of plan.edl that look like segments but were not read. The usual cause is a clip name with spaces that is not in quotes:
    the parser then drops the whole line without a word, and the order check would blame the wrong lines."""
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
        if (low.startswith("sfx ") or low.startswith("mark ")) and "|" not in raw:
            continue
        if "|" in raw:
            phead = raw.split("|", 1)[0].strip().strip('"').strip("'").lower()
            if phead in ("wrong", "right", "x", "check", "tick", "cross") or phead.endswith((".wav", ".mp3", ".ogg", ".m4a", ".aac")):
                continue
        first = raw.split()[0].strip('"')
        if first not in ids:
            out.append(f"line {n} ('{raw[:46]}...') was not read: put clip names that contain spaces in quotes, e.g. \"MISS NOVA FV.mp4\"")
    return out


def _is_seg_accepted(sid: str, ok: Dict[str, str]) -> bool:
    if not ok:
        return False
    if sid in ok or sid.rstrip("abcdefgh") in ok:
        return True
    return any(lid in ok for lid in _line_ids_of(sid))


def _live_words_in_cut(seg: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Non-ghost aligned words spoken inside `seg[start..end]`."""
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
        if (st - 0.02 <= mid <= en + 0.02) or (ws1 > st + 0.08 and ws0 < en - 0.08 and ov >= max(0.06, min(0.12, 0.45 * dur_w))):
            kept.append(w)
    return kept


def _script_tokens_for_segs(
    segs: List[Dict[str, Any]],
    words_slice: List[Dict[str, Any]],
    by_id: Dict[str, Dict[str, Any]],
    lines: List[Dict[str, Any]],
) -> List[str]:
    """Concatenate tokens of unique script lines mapped to `segs` (preserving multi-line drills while deduplicating sub-cuts like L23a+L23b)."""
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
    """Find unscripted repeated words/phrases (a) inside each segment and (b) across adjacent segment boundaries.

    Returns structured dicts (each with `message`, `segment`, `kind`, `phrase`, `clean_trim_start`, etc.).
    """
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
                    f"{rep['second_start']:.2f}-{rep['second_end']:.2f} — trim out stumble (clean start {rep['clean_trim_start']:.2f})"
                )
                problems.append({
                    **rep,
                    "scope": "intra",
                    "segment": sid,
                    "segments": [sid],
                    "message": msg,
                })

        # (b) Cross-segment boundary repeats (last 1..5 words of segment i repeated in first 1..5 words of segment i+1)
        boundary_window = 5
        for i in range(len(segs) - 1):
            s_a, s_b = segs[i], segs[i + 1]
            sid_a, sid_b = str(s_a.get("id", "?")), str(s_b.get("id", "?"))
            if _is_seg_accepted(sid_a, ok) or _is_seg_accepted(sid_b, ok):
                continue
            wa, wb = list(seg_words[i]), list(seg_words[i + 1])
            if not wa or not wb:
                continue
            # Ignore a single physical word straddling a non-overlapping cut on the same source clip
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
            t_cursor = 0.0
            for w in tail_a:
                w_dur = max(0.05, float(w.get("end", 0.0)) - float(w.get("start", 0.0)))
                nt = len(clean(str(w.get("w") or w.get("word") or "").split()))
                if nt == 0:
                    continue
                tail_tok_count += nt
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
                nt = len(clean(str(w.get("w") or w.get("word") or "").split()))
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
            if tail_tok_count == 0 or len(combined) <= len(tail_a):
                continue
            # Map token index back to `combined` word entry for exact clip-local timestamps
            tok_to_comb: List[Dict[str, Any]] = []
            for cw_item in combined:
                for _ in clean(str(cw_item.get("w") or "").split()):
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


def full_gate(plan: Dict[str, Any], script_path: Any, work_dir: Path, plan_path: Optional[Path] = None) -> Dict[str, Any]:
    """Order, coverage, cuts, repeats and voices. Used by `--check-plan` and before every render."""
    global _ACTIVE_WORK_DIR
    prev_wd = _ACTIVE_WORK_DIR
    _ACTIVE_WORK_DIR = Path(work_dir) if work_dir is not None else prev_wd
    try:
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
        unread = unread_plan_lines(plan, plan_path) if plan_path else []
        if unread:
            g["problems"] = {"PLAN LINES NOT READ (fix these first; the other messages may be a result of them)": unread, **g["problems"]}
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
        except Exception as exc:   # noqa: BLE001
            voices, note = [], f"voices not checked: {exc}"
        voices = still_open(voices)
        if voices:
            g["problems"]["WRONG VOICE"] = voices
        g["voice_note"] = note
        try:
            g["tighten"] = tight_report(plan)
        except Exception:       # advice only
            g["tighten"] = []
        try:
            g["style"] = style_report(plan, work_dir)
        except Exception:
            g["style"] = []
        try:
            g["style"] = list(g["style"]) + variety_report(plan)
        except Exception:
            pass
        g["blocked"] = bool(g["problems"])
        return g
    finally:
        _ACTIVE_WORK_DIR = prev_wd


def format_gate(g: Dict[str, Any]) -> str:
    if not g["problems"]:
        return "[ORDER PASS] The plan follows the script: every line present, once, in order" + (f" ({len(g['accepted'])} accepted exception(s))" if g["accepted"] else "")
    out = ["[ORDER BLOCKED] Decide these in plan.edl, or record a deliberate exception in decisions.txt (`accept <id> <reason>`):"]
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
    """Trim leading false starts (`clean_trim_start`) and trailing restarted repeats from `words[i..j]` when line recall holds."""
    cur_i, cur_j = i, j
    base_toks = [t for k in range(cur_i, cur_j + 1) for t in wtoks[k]]
    cur_r = recall(base_toks, lt)
    for _ in range(3):
        sub_ws = words[cur_i : cur_j + 1]
        reps = find_unscripted_repeats(sub_ws, ref_text)
        if not reps:
            return cur_r, cur_i, cur_j, []
        # Map flat token index inside sub_ws back to word offset
        tok_to_off: List[int] = []
        for off in range(len(sub_ws)):
            for _ in wtoks[cur_i + off]:
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
    """Return non-overlapping tight candidate windows `(recall, i, j, internal_repeats)` ranked cleanest & latest first."""
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
    """(recall, i, j): the tight, cleanest stretch of a clip that says this line best (preferring later clean retakes on tie)."""
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
    """Filter out zero-energy / silent ghost words before candidate windowing."""
    from prepare import _speech_spans_of, words_in_silence

    raw_ws = [w for w in (c.get("hq_words") or []) if isinstance(w, dict) and clean([str(w.get("w") or w.get("word") or "")])]
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
    """The script in full, and under each line every clip stretch that says it. A measurement, not a choice."""
    prep = []
    for c in clips:
        ws = _filter_live_clip_words(c)
        prep.append((c, ws, [t for w in ws for t in clean(str(w.get("w") or w.get("word") or "").split())]))
    named: set = set()
    out = ["# LINE MAP: the script is the timeline. CAND = a clip stretch that says this line; `heard` = share of the line's words found there.",
           "# Zeno chose nothing. Pick from these, check the words in facts.md, and write plan.edl."]
    if style:
        import brain_memory

        rules = brain_memory.format_brain_memory_for_brief(style)
        if rules:
            out += [""] + list(rules)
    out.append("")
    for l in lines:
        tags = [f"{k}={l[k]}" for k in ("scene", "mark", "layout_hint", "strike_text", "note") if l.get(k)]
        if l.get("panels"):
            tags.append(f"panels={l['panels']}")
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
            win_list = _candidate_windows(ws, lt, ref_text=ref_line, fallback_toks=toks)
            for r, i, j, reps in win_list:
                if r >= 0.5:
                    w_start = float(ws[i]["start"])
                    w_end = float(ws[min(j, len(ws) - 1)]["end"])
                    rep_pen = (0.06 + 0.02 * (len(reps) - 1)) if reps else 0.0
                    cands.append((
                        (round(r - rep_pen, 2), len(reps) == 0, round(r, 2), idx_c, w_start),
                        r,
                        Path(str(c["src"])).name,
                        w_start,
                        w_end,
                        reps,
                        c,
                    ))
        cands.sort(key=lambda x: (-x[0][0], not x[0][1], -x[0][2], x[0][3], -x[0][4]))
        for _sk, r, name, a, b, _reps, c in cands:
            named.add(id(c))
        for _sk, r, name, a, b, reps, _ in cands[:limit]:
            rep_note = ""
            if reps:
                rp0 = reps[0]
                rep_note = (
                    f"  [REPEAT @ {rp0['first_start']:.2f}s — split cut around stumble "
                    f"('{rp0['phrase']}' {rp0['first_start']:.2f}-{rp0['first_end']:.2f} & {rp0['second_start']:.2f}-{rp0['second_end']:.2f})]"
                )
            out.append(f"  CAND {name} {float(a):.2f}-{float(b):.2f} heard={r:.2f}{rep_note}")
        if len(cands) > limit:
            out.append(f"  +{len(cands) - limit} weaker")
        if not cands:
            out.append("  NO CLIP says this line (check facts.md, then tell the user to reshoot or accept the gap)")
    spare = [(c, ws) for c, ws, _ in prep if id(c) not in named and ws and not c.get("is_listening")]
    if spare:
        out += ["", "## SPEECH THAT FITS NO LINE (slate, ad-lib or a bad transcript: look at it)"]
        for c, ws in spare:
            out.append(f"{Path(str(c['src'])).name}: {' '.join(str(w.get('w', '')) for w in ws[:14])}")
    return "\n".join(out) + "\n"


def voice_problems(plan: Dict[str, Any]) -> Tuple[List[str], str]:
    """Claude cannot hear, so voices are measured here: does the voice in each cut match the speaker the plan names?
    Returns (problems, note). The note says when voices could not be told apart."""
    import speaker_check
    from common import read_json
    from prepare import compute_10ms_rms_envelope

    items, ids = [], {}
    for n, s in enumerate(plan.get("segments", [])):
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
            items.append({"id": n, "wav": wav, "words": ws, "expected": [who] * len(ws), "rms_env": compute_10ms_rms_envelope(wav)})
    if len({i["expected"][0] for i in items}) < 2:
        return [], ""
    res = speaker_check.analyze(items)
    if not res.get("ok"):
        return [], f"voices not checked: {res.get('reason')}"
    by_id = {i["id"]: i for i in items}
    src_of = {n: str(plan["segments"][n].get("src")) for n in ids}
    disagree = {n for n, info in res.get("clips", {}).items() if info.get("spans")}
    out, doubts = [], []
    for n, info in res.get("clips", {}).items():
        for sp in info.get("spans", []):
            ws = by_id[n]["words"][sp["first"]: sp["last"] + 1]
            span_sec = (float(ws[-1]["end"]) - float(ws[0]["start"])) if ws else 0.0
            same_file = [m for m in ids if m != n and src_of[m] == src_of[n]]
            msg = f"{ids.get(n, n)} is labelled {sp.get('expected')}, but the voice is {sp.get('heard', 'someone else')}"
            # A very short span is too little to tell voices apart. If every other turn from the same file agrees with its label,
            # it is far more likely a miss of the measurement than a wrong person: say so, but do not block.
            if span_sec < 2.5 and same_file and not any(m in disagree for m in same_file):
                doubts.append(f"{msg} (only {span_sec:.1f}s of speech; the file's other {len(same_file)} turn(s) match: check by ear)")
            else:
                out.append(msg)
    return out, ("voice doubt: " + "; ".join(doubts)) if doubts else ""


def _word_file(src: Any) -> Optional[Path]:
    """The cached aligned words of a clip. The cache stores them as words.json (hq_words.json in older caches)."""
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


def cut_problems(plan: Dict[str, Any]) -> List[str]:
    """Cuts that land inside a spoken word. A word counts only where there is real sound: an aligner time that sits in silence is wrong."""
    out = []
    for s in plan.get("segments", []):
        env = None
        spoken = _speech_spans_for(s)
        for w in _words_of(s):
            a, b = float(w["start"]), float(w["end"])
            for name, t in (("start", float(s["start"])), ("end", float(s["end"]))):
                if a + 0.05 < t < b - 0.05:
                    if spoken and not any(x0 - 0.03 <= t <= x1 + 0.03 for x0, x1 in spoken):
                        continue      # the voice detector hears no speech at this moment: a word the transcript put here is a ghost
                    if env is None:
                        env = _env_of(s)
                    loud = max((db for tt, db in env if t - 0.03 <= tt <= t + 0.03), default=-140.0)
                    if env and loud < -50.0:
                        continue      # the cut lands in silence: no sound is cut
                    out.append(f"{s.get('id')} {name} {t:.2f} is inside the word '{w.get('w')}' ({a:.2f}-{b:.2f})")
    return out


def tight_report(plan: Dict[str, Any]) -> List[str]:
    """Where the edit drags: silence at joins, pauses inside cuts, and excessive mark tails. Advice only; it never blocks a render."""
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
        ws = [w for w in _words_of(s) if float(w["end"]) > st + 0.05 and float(w["start"]) < en - 0.05]
        rows.append((s, st, en, ws))
    for k, (s, st, en, ws) in enumerate(rows):
        sid = str(s.get("id"))
        seg_dur = max(0.0, en - st)
        for m in marks_by_seg.get(sid, []):
            m_at = float(m.get("at", 0.0))
            post_mark = seg_dur - m_at
            if post_mark > MARK_TAIL_MAX_SEC + 0.02:
                out.append(
                    f"{sid}: {post_mark:.2f}s tail after mark onset at {m_at:.2f}s "
                    f"(keep <= {MARK_TAIL_MAX_SEC:.2f}s; end {sid} near {st + m_at + MARK_TAIL_MAX_SEC:.2f})"
                )
        if not ws:
            continue
        lead = max(0.0, float(ws[0]["start"]) - st)
        tail = max(0.0, en - float(ws[-1]["end"]))
        tail_free = max(0.0, en - max(float(ws[-1]["end"]), st + mark_end.get(sid, 0.0))) if sid in mark_end else tail
        nxt = rows[k + 1] if k + 1 < len(rows) else None
        if nxt and nxt[3]:
            nxt_lead = max(0.0, float(nxt[3][0]["start"]) - nxt[1])
            if tail_free + nxt_lead > TIGHT_JOIN_SEC:
                out.append(f"{sid} -> {nxt[0].get('id')}: {tail_free + nxt_lead:.2f}s of silence at the join (tail {tail_free:.2f}s + lead {nxt_lead:.2f}s): "
                           f"end {sid} at {en - max(0.0, tail_free - 0.06):.2f} or start {nxt[0].get('id')} later")
        for w0, w1 in zip(ws, ws[1:]):
            gap = float(w1["start"]) - float(w0["end"])
            if gap > TIGHT_PAUSE_SEC:
                out.append(f"{sid}: {gap:.2f}s pause between '{w0.get('w')}' and '{w1.get('w')}' at {float(w0['end']):.2f}-{float(w1['start']):.2f}: cut it (split the cut) or accept it as a breath")
        if k == 0 and lead > 0.20:
            out.append(f"{sid}: starts {lead:.2f}s before the first word")
    return out


VARIETY_MAX_SHARE = 0.40   # one sound effect or transition above this share of the plan sounds repetitive
VARIETY_MIN_USES = 4


def variety_report(plan: Dict[str, Any]) -> List[str]:
    """Advice only: editors said every video sounds and moves the same. Flags one SFX file or one transition used too often."""
    from collections import Counter

    def share(names: List[str], what: str, hint: str) -> List[str]:
        if len(names) < VARIETY_MIN_USES:
            return []
        name, n = Counter(names).most_common(1)[0]
        if n / len(names) > VARIETY_MAX_SHARE and n >= VARIETY_MIN_USES:
            return [f"{name} is {round(100 * n / len(names))}% of the {what} ({n} of {len(names)}); vary it: {hint}"]
        return []

    sfx = [str(e.get("file", "")).lower() for e in plan.get("sfx", []) or [] if e.get("file")]
    trs = [str(s.get("transition")).lower() for s in plan.get("segments", []) if s.get("transition") and s.get("transition") != "cut"]
    out = share(sfx, "sound effects", "pop, click, sn_punch_1/2, sn_swish_1, w42_swoosh, w42_transition, kn20228_*")
    out += share(trs, "transitions", "whip_left/right, slide_up/down, zoom_burst, flash_zoom, split_reveal, warm_flash, dip_to_black")
    if len(set(trs)) < 3 and len(trs) >= 4:
        out.append("only %d kind(s) of transition in %d; use at least 3 different ones in a reel of this length" % (len(set(trs)), len(trs)))
    return out


def style_report(plan: Dict[str, Any], work_dir: Optional[Path] = None) -> List[str]:
    """Advice on the emotion plan: whether the script was analysed, whether zooms and effects follow the rules. Never blocks a render."""
    out: List[str] = []
    segs = plan.get("segments", [])
    if work_dir is not None and not (Path(work_dir) / "script_analysis.md").is_file():
        out.append("no script_analysis.md: analyse the script first (emotion, beat, zoom and effect per line) and save it in the work folder; it is how the zooms and effects are planned")
    from common import resolve_sfx_asset

    rapid, flash, singles, still = [], 0, 0, 0
    prev_rapid = False
    live_total, live_cuts, live_moving, live_trans = 0.0, 0, 0, 0
    live_srcs: set = set()
    for s in segs:
        lay = str(s.get("layout") or "single").lower()
        style = str(s.get("zoom_style") or "")
        z0, z1 = float(s.get("zoom") or 1.0), float(s.get("zoom_to") or s.get("zoom") or 1.0)
        max_z = max(z0, z1)
        moves = style in RAPID_ZOOM_STYLES or style == "sudden_out" or abs(z1 - z0) > 1e-6          # animated zoom; a fixed crop stands still
        has_punch_crop = abs(max_z - 1.0) > 0.015
        dur = float(s["end"]) - float(s["start"])
        if lay in ("split_h", "split_v", "top_pip"):
            if moves or has_punch_crop:
                out.append(f"{s.get('id')}: {lay} has a zoom ({z0:.2f}->{z1:.2f}); split screens never zoom, write z=1 (the renderer holds the panel still anyway)")
        if lay == "single":
            if s.get("src"):
                live_srcs.add(str(s.get("src")))
            live_total += dur
            has_tr = bool(s.get("transition")) and str(s.get("transition")) != "cut"
            live_trans += 1 if has_tr else 0
            if dur >= 1.0:
                live_cuts += 1
                live_moving += 1 if (moves or has_punch_crop or has_tr) else 0
            if dur >= 1.5:
                singles += 1
                still += 0 if moves else 1
            is_rapid = style in RAPID_ZOOM_STYLES
            if is_rapid and prev_rapid:
                out.append(f"{s.get('id')}: a rapid zoom straight after another rapid zoom; make one of them slow or none")
            prev_rapid = is_rapid
            if is_rapid:
                rapid.append(s)
            if style == "flash_zoom":
                flash += 1
        else:
            prev_rapid = False
    if flash > 1:
        out.append(f"{flash} flash zooms: keep it to the single biggest beat of the video")
    per_min = len(rapid) / (max(live_total, 1.0) / 60.0)
    if per_min > MAX_RAPID_PER_MIN:
        out.append(f"{len(rapid)} rapid zooms in {live_total:.0f}s of live conversation ({per_min:.1f} a minute; starting limit {MAX_RAPID_PER_MIN}): thin them out so the peaks still stand out")
    if live_total >= 10.0 and live_cuts >= 3:
        if live_moving / live_cuts < AD_MIN_MOVING_SHARE:
            out.append(f"only {live_moving} of {live_cuts} live cuts move (a zoom or a transition): this is for a Meta ad, so aim for at least {int(AD_MIN_MOVING_SHARE * 100)}% (rapid zoom in on a stressed word, slow push or pull on longer cuts, a zoom out on release)")
        if live_total >= 25.0 or len(live_srcs) >= 2:
            want = max(1, int((live_total - 6.5) // AD_SECONDS_PER_TRANSITION))
            if live_trans < want:
                out.append(f"{live_trans} transition(s) in {live_total:.0f}s of live conversation: for a Meta ad aim for about {want} (whip_left/whip_right across clip changes, slide_up, zoom_burst on a reveal)")
    if singles >= 4 and still / singles < MIN_STILL_SHARE:
        out.append(f"only {still} of {singles} single-speaker cuts stand still (aim for at least {int(MIN_STILL_SHARE * 100)}%): stillness is what gives a zoom its weight")
    sp = plan.get("subs")
    if isinstance(sp, dict) and sp.get("style") and str(sp["style"]).strip().lower() != "auto":
        try:
            import subtitles

            named, last = subtitles.resolve_style(sp["style"]), subtitles.recent_styles(n=1)
            if named is not None and last and named["name"] == last[0]:
                out.append(f"subtitle style '{named['name']}' is the one used on the last edit; use `#subs auto` (or name another) so this edit gets a different look")
        except Exception:   # noqa: BLE001
            pass
    ids = {str(s.get("id")): s for s in segs}
    for e in plan.get("sfx", []):
        name, seg = str(e.get("file")), ids.get(str(e.get("segment")))
        try:
            known = resolve_sfx_asset(name).is_file()
        except Exception:
            known = False
        if not known:
            out.append(f"sound '{name}' on {e.get('segment')} is not in assets/sfx: it will not play")
        if float(e.get("gain_db", -8)) > 0:
            out.append(f"sound '{name}' on {e.get('segment')} is boosted above 0 dB: keep effects under the voice")
        if seg is not None and float(e.get("at", 0)) > float(seg["end"]) - float(seg["start"]):
            out.append(f"sound '{name}' on {e.get('segment')} starts after the cut ends")
    return out


def snap_cuts(plan: Dict[str, Any]) -> int:
    """Mechanical: a cut in the gap between two words moves to the quietest 10 ms of that gap, so it cannot click. Keeps the Director's words."""
    from prepare import compute_10ms_rms_envelope, find_rms_minimum

    moved = 0
    for s in plan.get("segments", []):
        ws = _words_of(s)
        wav = _resolve_clip_cache_dir(s.get("src", "")) / "audio16k.wav"
        if len(ws) < 2 or not wav.is_file():
            continue
        env = compute_10ms_rms_envelope(wav)
        for key in ("start", "end"):
            t = float(s[key])
            for w0, w1 in zip(ws, ws[1:]):
                a, b = float(w0["end"]), float(w1["start"])
                if a <= t <= b and b - a >= 0.03:
                    # only a small nudge: in a long gap (or digital silence) the quietest point can be seconds away, which adds dead air
                    lo, hi = max(a, t - SNAP_REACH_SEC), min(b, t + SNAP_REACH_SEC)
                    new = round(float(find_rms_minimum(env, lo, hi)[0]), 2)
                    if new != round(t, 2):
                        s[key] = new
                        moved += 1
                    break
    return moved


def main() -> None:
    import edl_parser
    from common import read_json, resolve

    p = resolve(sys.argv[1])
    plan = edl_parser.load_edl(p)
    side = p.parent / "plan.json"
    sp = plan.get("script") or (read_json(side).get("script") if side.is_file() else None)
    if not sp:
        print("[ORDER] This plan names no script, so order cannot be measured.")
        sys.exit(2)
    g = full_gate(plan, resolve(sp), p.parent, p)
    for a in g["report"]["assigned"]:
        print(f"  {a['id']:<8} says {a['line'] or '?':<6} fit={a['fit']}")
    print(format_gate(g))
    if g.get("voice_note"):
        print(f"[VOICE] {g['voice_note']}. Say so in the report.")
    sys.exit(2 if g["blocked"] else 0)


if __name__ == "__main__":
    main()
