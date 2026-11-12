"""Burn-in subtitles for a rendered edit, only when the plan asks for them (`#subs <style> [y=72]` in plan.edl).

The words come from what the clips actually say (the aligned transcript, with each word's time), shifted onto the finished timeline,
with script-line + speech-span synthesis as a zero-blank safety net when an edit cut lacks word-level alignment.
They are never translated: Kannada stays Kannada, English stays English. The pictures are drawn by the Premiere-grade caption renderer
(`subtitles/caption-renderer.js`, eight curated styles in `subtitles/styles.json`) running on node-canvas, cropped to an optical horizontal
caption band with 3-subframe active-word karaoke pop. `render.py` overlays them in the final mix as one image sequence.
"""
from __future__ import annotations

import colorsys
import json
import re
import shutil
import subprocess
import time
import unicodedata
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

HERE = Path(__file__).resolve().parent
SUB_DIR = HERE.parent / "subtitles"
MAX_WORDS_PER_CUE = 4          # max words per cue for Latin scripts
MAX_INDIC_WORDS_PER_CUE = 3    # max words per cue for Indic scripts (wider conjuncts/matras)
MAX_VISUAL_UNITS = 22.0        # target visual character width per single-line cue
MAX_GAP_IN_CUE = 0.22          # speech pause > 0.22s starts a fresh caption card
BRIDGE_GAP_MAX = 0.18          # bridge tiny intra-sentence gaps <= 0.18s without overlap
SENTENCE_END = re.compile(r"[.?!।][\"'”’)\]]*$")
CLAUSE_END = re.compile(r"[,;:—–][\"'”’)\]]*$")
INDIC_RE = re.compile(r"[\u0900-\u0D7F]")

STYLE_ALIASES: Dict[str, int] = {
    "hormozi punch": 4,
    "hormozi": 4,
    "blue box": 4,
    "word box": 4,
    "karaoke": 4,
    "docu": 28,
    "docu bar": 28,
    "docu split bar": 28,
    "split bar": 28,
    "frosted": 25,
    "lyric": 25,
    "glass": 3,
    "glossy": 3,
    "gradient": 27,
    "cinema": 13,
    "yellow": 13,
    "comic": 24,
    "3d comic": 24,
    "sticker": 12,
    "cutout": 12,
}

CONTRACTIONS: Dict[str, List[str]] = {
    "i'm": ["i", "am"],
    "im": ["i", "am"],
    "don't": ["do", "not"],
    "dont": ["do", "not"],
    "can't": ["can", "not"],
    "cant": ["can", "not"],
    "cannot": ["can", "not"],
    "won't": ["will", "not"],
    "wont": ["will", "not"],
    "it's": ["it", "is"],
    "i've": ["i", "have"],
    "ive": ["i", "have"],
    "you're": ["you", "are"],
    "youre": ["you", "are"],
    "we're": ["we", "are"],
    "they're": ["they", "are"],
    "theyre": ["they", "are"],
    "that's": ["that", "is"],
    "thats": ["that", "is"],
    "what's": ["what", "is"],
    "whats": ["what", "is"],
    "let's": ["let", "us"],
    "lets": ["let", "us"],
    "didn't": ["did", "not"],
    "didnt": ["did", "not"],
    "doesn't": ["does", "not"],
    "doesnt": ["does", "not"],
    "isn't": ["is", "not"],
    "isnt": ["is", "not"],
    "aren't": ["are", "not"],
    "arent": ["are", "not"],
    "wasn't": ["was", "not"],
    "wasnt": ["was", "not"],
    "weren't": ["were", "not"],
    "werent": ["were", "not"],
    "couldn't": ["could", "not"],
    "couldnt": ["could", "not"],
    "wouldn't": ["would", "not"],
    "wouldnt": ["would", "not"],
    "shouldn't": ["should", "not"],
    "shouldnt": ["should", "not"],
    "haven't": ["have", "not"],
    "havent": ["have", "not"],
    "hasn't": ["has", "not"],
    "hasnt": ["has", "not"],
    "hadn't": ["had", "not"],
    "hadnt": ["had", "not"],
    "i'll": ["i", "will"],
    "you'll": ["you", "will"],
    "youll": ["you", "will"],
    "we'll": ["we", "will"],
    "they'll": ["they", "will"],
    "theyll": ["they", "will"],
    "he's": ["he", "is"],
    "she's": ["she", "is"],
    "there's": ["there", "is"],
    "theres": ["there", "is"],
    "here's": ["here", "is"],
    "heres": ["here", "is"],
    "who's": ["who", "is"],
    "whos": ["who", "is"],
    "we've": ["we", "have"],
    "weve": ["we", "have"],
    "you've": ["you", "have"],
    "youve": ["you", "have"],
    "they've": ["they", "have"],
    "theyve": ["they", "have"],
    "i'd": ["i", "would"],
    "you'd": ["you", "would"],
    "we'd": ["we", "would"],
    "they'd": ["they", "would"],
}


def node_path() -> str:
    return shutil.which("node") or ""


def available() -> bool:
    """Node is installed and the renderer's dependencies (node-canvas) are in place."""
    return bool(node_path()) and (SUB_DIR / "node_modules" / "canvas").is_dir() and (SUB_DIR / "styles.json").is_file()


def ensure_ready() -> bool:
    """Install the renderer's one dependency (node-canvas, a prebuilt binary of about 20 MB) the first time subtitles are used on this computer.
    Needs Node and an internet connection; returns whether subtitles can be drawn."""
    if available():
        return True
    npm = shutil.which("npm")
    if node_path() and npm and (SUB_DIR / "package.json").is_file():
        print("  [SUBS] first use on this computer: installing the caption renderer (about 20 MB)...")
        try:
            subprocess.run([npm, "install", "--no-audit", "--no-fund", "--silent"], cwd=str(SUB_DIR), capture_output=True, text=True, timeout=600)
        except Exception as exc:   # noqa: BLE001
            print(f"  [SUBS] install failed: {exc}")
    return available()


def styles() -> List[Dict[str, Any]]:
    return json.loads((SUB_DIR / "styles.json").read_text(encoding="utf-8"))


def resolve_style(token: Any) -> Optional[Dict[str, Any]]:
    """Resolve a subtitle style by id (`4`), exact name (`Blue Highlight`, any case), or known alias (`Hormozi Punch`); None otherwise."""
    if token is None:
        return None
    tok = str(token).strip().strip("\"'").lower()
    if not tok:
        return None
    pool = styles()
    for s in pool:
        if str(s.get("id")) == tok or str(s.get("name", "")).lower() == tok:
            return s
    norm = re.sub(r"[-_]+", " ", tok)
    norm = re.sub(r"\s+", " ", norm).strip()
    alias_id = STYLE_ALIASES.get(norm)
    if alias_id is not None:
        for s in pool:
            if int(s.get("id", -1)) == alias_id:
                return s
    return None


# ---------------------------------------------------------------- clip cache & word loading
def _safe_stem(stem: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", stem).strip("_") or "clip"


def _load_clip_caches_map(work_dir: Optional[Path]) -> Dict[str, str]:
    if work_dir is None:
        return {}
    f = Path(work_dir) / "clip_caches.json"
    if not f.is_file():
        return {}
    try:
        raw = json.loads(f.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            return {}
        out: Dict[str, str] = {}
        for k, v in raw.items():
            if isinstance(v, str) and v.strip():
                out[str(k)] = v.strip()
            elif isinstance(v, dict):
                cand = str(v.get("cache") or v.get("dir") or v.get("path") or "").strip()
                if cand:
                    out[str(k)] = cand
        return out
    except Exception:
        return {}


def _cache_dirs_for_stem(stem: str) -> List[Path]:
    from common import CLIP_CACHE

    if not CLIP_CACHE.is_dir():
        return []
    s_clean = _safe_stem(stem)
    matches = [d for d in CLIP_CACHE.glob(f"{s_clean}-??????????") if d.is_dir()]
    if not matches:
        prefix_low = f"{s_clean.lower()}-"
        matches = [
            d for d in CLIP_CACHE.iterdir()
            if d.is_dir() and d.name.lower().startswith(prefix_low) and len(d.name) == len(prefix_low) + 10
        ]

    def _dir_rank(d: Path) -> Tuple[int, float]:
        has_words = int(any((d / fn).is_file() for fn in ("hq_words.json", "align.json", "words.json")))
        try:
            mt = d.stat().st_mtime
        except OSError:
            mt = 0.0
        return (has_words, mt)

    return sorted(matches, key=_dir_rank, reverse=True)


def _resolve_clip_cache_dirs(src: Any, work_dir: Optional[Path] = None, include_vo_siblings: bool = False) -> List[Path]:
    from common import CLIP_CACHE, clip_work_dir

    if not src:
        return []
    p = Path(str(src))
    fname = p.name
    stem = p.stem
    seen: Set[Path] = set()
    dirs: List[Path] = []

    def _add(cand: Optional[Path]) -> None:
        if cand is None:
            return
        try:
            rc = cand.resolve()
        except OSError:
            rc = cand
        if rc not in seen and cand.is_dir():
            seen.add(rc)
            dirs.append(cand)

    cache_map = _load_clip_caches_map(work_dir)
    if cache_map:
        lookup_keys = [str(src), p.as_posix(), fname, stem]
        lower_map = {k.lower(): v for k, v in cache_map.items()}
        for key in lookup_keys:
            mapped = cache_map.get(key) or lower_map.get(key.lower())
            if mapped:
                mp = Path(mapped)
                _add(mp if mp.is_absolute() else (CLIP_CACHE / mapped))
                _add(Path(work_dir) / mapped if work_dir else None)

    try:
        wd = clip_work_dir(src)
        _add(wd)
    except Exception:
        wd = None

    if not p.exists() or wd is None or not wd.is_dir():
        for d in _cache_dirs_for_stem(stem):
            _add(d)

    if include_vo_siblings:
        vo_stems = [f"{stem}VO", f"{stem}_VO", f"{stem}-VO", f"{stem} VO", f"{stem}vo", f"{stem}_vo"]
        if cache_map:
            lower_map = {k.lower(): v for k, v in cache_map.items()}
            for vs in vo_stems:
                for key in (vs, f"{vs}{p.suffix}", f"{vs}.mp4", f"{vs}.mov", f"{vs}.wav"):
                    mapped = cache_map.get(key) or lower_map.get(key.lower())
                    if mapped:
                        mp = Path(mapped)
                        _add(mp if mp.is_absolute() else (CLIP_CACHE / mapped))
        if p.parent and str(p.parent) not in ("", "."):
            for vs in vo_stems:
                for ext in (p.suffix, ".mp4", ".mov", ".wav", ".m4a"):
                    if not ext:
                        continue
                    sib = p.parent / f"{vs}{ext}"
                    if sib.exists():
                        try:
                            _add(clip_work_dir(sib))
                        except Exception:
                            pass
        for vs in vo_stems:
            for d in _cache_dirs_for_stem(vs):
                _add(d)

    return dirs


def _normalize_word_list(raw: Any) -> List[Dict[str, Any]]:
    if isinstance(raw, dict):
        items = raw.get("words")
        if items is None and isinstance(raw.get("segments"), list):
            items = [w for seg in raw["segments"] if isinstance(seg, dict) for w in (seg.get("words") or [])]
    elif isinstance(raw, list):
        items = raw
    else:
        return []
    if not isinstance(items, list):
        return []
    out: List[Dict[str, Any]] = []
    for w in items:
        if not isinstance(w, dict):
            continue
        txt = str(w.get("word") or w.get("w") or w.get("text") or "").strip()
        if not txt:
            continue
        try:
            ws = float(w.get("start", 0.0))
            we = float(w.get("end", ws))
        except (TypeError, ValueError):
            continue
        if we < ws:
            continue
        norm = dict(w)
        norm["text"] = txt
        norm.setdefault("word", txt)
        norm.setdefault("w", txt)
        norm["start"] = ws
        norm["end"] = we if we > ws else ws + 0.04
        out.append(norm)
    return out


def _richest_words_in_dir(d: Path) -> List[Dict[str, Any]]:
    best_words: List[Dict[str, Any]] = []
    best_score = -1.0
    priorities = (("hq_words.json", 1.5), ("align.json", 0.5), ("words.json", 0.0))
    for fname, bonus in priorities:
        f = d / fname
        if not f.is_file():
            continue
        try:
            parsed = json.loads(f.read_text(encoding="utf-8"))
            wlist = _normalize_word_list(parsed)
            if wlist:
                sc = len(wlist) + bonus
                if sc > best_score:
                    best_score = sc
                    best_words = wlist
        except Exception:
            continue
    return best_words


def _words_of_clip(src: Any, work_dir: Optional[Path] = None) -> List[Dict[str, Any]]:
    """Return the richest aligned word list for `src`, checking `clip_caches.json`, `clip_work_dir`,
    offline `CLIP_CACHE` matches, `("hq_words.json", "align.json", "words.json")`, and `<stem>VO` siblings."""
    for d in _resolve_clip_cache_dirs(src, work_dir=work_dir, include_vo_siblings=False):
        words = _richest_words_in_dir(d)
        if words:
            return words
    for d in _resolve_clip_cache_dirs(src, work_dir=work_dir, include_vo_siblings=True):
        words = _richest_words_in_dir(d)
        if words:
            return words
    return []


def _speech_spans(src: Any, work_dir: Optional[Path] = None) -> List[Tuple[float, float]]:
    for d in _resolve_clip_cache_dirs(src, work_dir=work_dir, include_vo_siblings=True):
        f = d / "speech_spans.json"
        if f.is_file():
            try:
                spans = json.loads(f.read_text(encoding="utf-8")).get("spans", [])
                out = [(float(x[0]), float(x[1])) for x in spans if isinstance(x, (list, tuple)) and len(x) >= 2]
                if out:
                    return out
            except Exception:
                continue
    return []


def _call_clip_loader(fn: Callable[..., Any], src: Any, work_dir: Optional[Path]) -> Any:
    """Invoke `_words_of_clip` / `_speech_spans` while remaining 100% compatible with 1-arg test mocks."""
    if work_dir is None:
        return fn(src)
    try:
        return fn(src, work_dir)
    except TypeError:
        return fn(src)


# ---------------------------------------------------------------- script line IDs, contractions & foreign-word guard
def _base_line(seg_id: str) -> str:
    m = re.match(r"^(L\d+)", str(seg_id))
    return m.group(1) if m else str(seg_id)


def _seg_lines(seg_id: str) -> Set[str]:
    """Resolve `L1`, `L1b`, `L1_L2` (`{"L1", "L2"}`), ranges `L2_L5` (`{"L2", "L3", "L4", "L5"}`),
    or return `set()` for custom non-line IDs (`S1`, `HOOK1`) so custom IDs never filter out spoken words."""
    s = str(seg_id or "").strip()
    if not re.match(r"^L\d+", s, re.IGNORECASE):
        return set()
    m_range = re.match(r"^L(\d+)[a-z]*(?:_|-|\.\.)L(\d+)[a-z]*$", s, re.IGNORECASE)
    if m_range:
        a, b = int(m_range.group(1)), int(m_range.group(2))
        if a <= b and (b - a) <= 40:
            return {f"L{k}" for k in range(a, b + 1)}
    nums = [int(x) for x in re.findall(r"L(\d+)", s, re.IGNORECASE)]
    return {f"L{n}" for n in nums}


def _normalize_apostrophes(text: str) -> str:
    return text.replace("’", "'").replace("‘", "'").replace("`", "'")


def _expand_word_forms(word: str) -> List[List[str]]:
    """Return candidate tokenizations for `word`: `[cleaned_single]` and (if contraction) `[expanded_parts]`."""
    from script_order import clean

    w_norm = _normalize_apostrophes(str(word or "").strip().lower())
    w_trim = re.sub(r"^[^a-z0-9\u0900-\u0D7F']+|[^a-z0-9\u0900-\u0D7F']+$", "", w_norm)
    forms: List[List[str]] = []
    base = clean([w_norm])
    if base:
        forms.append(base)
    if w_trim and w_trim not in (base[0] if base else ""):
        forms.append([w_trim])
    no_ap = w_trim.replace("'", "")
    for key in (w_trim, no_ap):
        if key in CONTRACTIONS:
            exp = CONTRACTIONS[key]
            if exp not in forms:
                forms.append(list(exp))
    return forms


def _build_line_token_map(script_lines: Optional[List[Dict[str, Any]]]) -> Dict[str, List[str]]:
    from script_order import clean

    out: Dict[str, List[str]] = {}
    for l in script_lines or []:
        lid = str(l.get("id") or "").strip()
        if not lid:
            continue
        acc: List[str] = []
        seen: Set[str] = set()

        def _push(t: str) -> None:
            if t and t not in seen:
                seen.add(t)
                acc.append(t)

        for tok in l.get("tokens") or []:
            s_tok = _normalize_apostrophes(str(tok).strip().lower())
            _push(s_tok)
            for c_tok in clean([s_tok]):
                _push(c_tok)
            for key in (s_tok, s_tok.replace("'", "")):
                for exp_tok in CONTRACTIONS.get(key, ()):
                    _push(exp_tok)

        raw_text = str(l.get("text") or "").strip()
        if raw_text:
            for raw_w in raw_text.split():
                for form in _expand_word_forms(raw_w):
                    for part in form:
                        _push(part)

        if acc:
            out[lid] = acc
    return out


def _word_matches_tokens(word: str, target_tokens: List[str], similarity: Callable[[str, str], float], threshold: float = 0.78) -> bool:
    if not target_tokens:
        return False
    forms = _expand_word_forms(word)
    if not forms:
        return False
    for form in forms:
        if all(any(similarity(part, cand) >= threshold for cand in target_tokens) for part in form):
            return True
    return False


def _foreign_to_line(
    word: str,
    seg_line: Any,
    line_tokens: Dict[str, List[str]],
    similarity: Callable[[str, str], float],
    adjacent_lines: Optional[Set[str]] = None,
) -> bool:
    """True when `word` does not match the segment's target line(s) and strongly (`>= 0.80`) matches an adjacent script line."""
    if isinstance(seg_line, (set, frozenset, list, tuple)):
        target_ids = {str(x) for x in seg_line if str(x)}
    else:
        target_ids = _seg_lines(str(seg_line))
    if not target_ids or not line_tokens:
        return False

    target_toks = [tok for lid in target_ids for tok in line_tokens.get(lid, [])]
    if _word_matches_tokens(word, target_toks, similarity, threshold=0.78):
        return False

    check_ids = adjacent_lines if adjacent_lines is not None else (set(line_tokens.keys()) - target_ids)
    for lid in check_ids:
        if lid in target_ids:
            continue
        other_toks = line_tokens.get(lid, [])
        if other_toks and _word_matches_tokens(word, other_toks, similarity, threshold=0.80):
            return True
    return False


def _adjacent_line_ids(target_ids: Set[str], line_tokens: Dict[str, List[str]]) -> Tuple[Set[str], Set[str]]:
    nums = sorted(int(lid[1:]) for lid in target_ids if lid.startswith("L") and lid[1:].isdigit())
    if not nums:
        return set(), set()
    lo, hi = nums[0], nums[-1]
    avail = sorted((int(lid[1:]), lid) for lid in line_tokens if lid.startswith("L") and lid[1:].isdigit())
    prev_ids: Set[str] = {f"L{lo - 1}"} if lo > 1 else set()
    next_ids: Set[str] = {f"L{hi + 1}"}
    below = [lid for n, lid in avail if n < lo]
    above = [lid for n, lid in avail if n > hi]
    if below:
        prev_ids.add(below[-1])
    if above:
        next_ids.add(above[0])
    return prev_ids, next_ids


# ---------------------------------------------------------------- script-line + speech-span fallback
def _script_words_for_lines(target_ids: Set[str], script_lines: Optional[List[Dict[str, Any]]]) -> List[str]:
    if not target_ids or not script_lines:
        return []
    ordered_ids = sorted(target_ids, key=lambda x: int(x[1:]) if x.startswith("L") and x[1:].isdigit() else 999999)
    by_id = {str(l.get("id")): l for l in script_lines if l.get("id")}
    words: List[str] = []
    for lid in ordered_ids:
        entry = by_id.get(lid)
        if not entry:
            continue
        txt = str(entry.get("text") or "").strip()
        if txt:
            cleaned = re.sub(r"\[[^\]]*\]", " ", txt)
            words.extend(w for w in cleaned.split() if w.strip())
        elif entry.get("tokens"):
            words.extend(str(t) for t in entry["tokens"] if str(t).strip())
    return words


def _active_speech_windows(st: float, en: float, spoken: List[Tuple[float, float]]) -> Tuple[List[Tuple[float, float]], float]:
    dur = max(0.0, en - st)
    if dur <= 0.04:
        return [], 0.0
    if spoken:
        clipped = [(max(st, a), min(en, b)) for a, b in spoken if min(en, b) - max(st, a) >= 0.04]
        clipped.sort(key=lambda x: x[0])
        merged: List[Tuple[float, float]] = []
        for a, b in clipped:
            if merged and a - merged[-1][1] <= 0.14:
                merged[-1] = (merged[-1][0], max(merged[-1][1], b))
            else:
                merged.append((a, b))
        return merged, sum(b - a for a, b in merged)
    if dur >= 0.45:
        pad = min(0.05, dur * 0.08)
        win = (st + pad, en - pad)
        return [win], max(0.0, win[1] - win[0])
    return [], 0.0


def _synthesize_words_over_spans(
    words_text: List[str],
    tl_spans: List[Tuple[int, float, float]],
) -> Dict[int, List[Dict[str, Any]]]:
    """Distribute `words_text` proportionally across `(seg_idx, tl_start, tl_end)` speech spans."""
    result: Dict[int, List[Dict[str, Any]]] = {}
    if not words_text or not tl_spans:
        return result
    total_dur = sum(max(0.02, b - a) for _, a, b in tl_spans)
    if total_dur <= 0.02:
        return result

    weights: List[float] = []
    for w in words_text:
        base_w = max(2.0, float(sum(1 for ch in w if unicodedata.category(ch) != "Mn")))
        if SENTENCE_END.search(w):
            base_w += 1.6
        elif CLAUSE_END.search(w):
            base_w += 0.9
        weights.append(base_w)
    total_weight = sum(weights) or float(len(words_text))

    # Partition words across spans proportional to span duration
    n_words = len(words_text)
    n_spans = len(tl_spans)
    cum_dur = 0.0
    start_idx = 0
    for s_pos, (seg_idx, sp_a, sp_b) in enumerate(tl_spans):
        cum_dur += max(0.02, sp_b - sp_a)
        if s_pos == n_spans - 1:
            end_idx = n_words
        else:
            target_cum = round(n_words * (cum_dur / total_dur))
            end_idx = max(start_idx, min(n_words, int(target_cum)))
        chunk = words_text[start_idx:end_idx]
        chunk_weights = weights[start_idx:end_idx]
        start_idx = end_idx
        if not chunk:
            continue
        span_len = max(0.04, sp_b - sp_a)
        w_sum = sum(chunk_weights) or float(len(chunk))
        cursor = sp_a
        seg_out = result.setdefault(seg_idx, [])
        for k, w_txt in enumerate(chunk):
            w_dur = span_len * (chunk_weights[k] / w_sum)
            w_end = sp_b if k == len(chunk) - 1 else min(sp_b, cursor + max(0.04, w_dur))
            seg_out.append({"text": w_txt, "start": cursor, "end": max(cursor + 0.03, w_end)})
            cursor = w_end
    return result


# ---------------------------------------------------------------- phrase chunking & monotonic cues
def _visual_units(text: str) -> float:
    units = 0.0
    for ch in text:
        cat = unicodedata.category(ch)
        if cat == "Mn":
            continue
        if cat == "Mc":
            units += 0.5
        else:
            units += 1.0
    return units


def _chunk_continuous_phrase(run_words: List[Dict[str, Any]]) -> List[List[Dict[str, Any]]]:
    if not run_words:
        return []
    has_indic = any(INDIC_RE.search(w["text"]) for w in run_words)
    max_w = MAX_INDIC_WORDS_PER_CUE if has_indic else MAX_WORDS_PER_CUE

    chunks: List[List[Dict[str, Any]]] = []
    cur: List[Dict[str, Any]] = []
    cur_units = 0.0

    for w in run_words:
        w_units = _visual_units(w["text"])
        next_units = cur_units + (1.0 if cur else 0.0) + w_units
        if cur and (len(cur) >= max_w or next_units > MAX_VISUAL_UNITS):
            chunks.append(cur)
            cur = []
            cur_units = 0.0
        cur.append(w)
        cur_units += (1.0 if len(cur) > 1 else 0.0) + w_units
        if len(cur) >= 2 and CLAUSE_END.search(w["text"]):
            chunks.append(cur)
            cur = []
            cur_units = 0.0
    if cur:
        chunks.append(cur)

    # Rebalance orphan 1-word tails within continuous phrase runs ([4, 4, 1] -> [3, 3, 3]; [4, 1] -> [3, 2]; [3, 1] -> [2, 2])
    if len(chunks) >= 3 and [len(chunks[-3]), len(chunks[-2]), len(chunks[-1])] == [4, 4, 1]:
        tail9 = chunks[-3] + chunks[-2] + chunks[-1]
        if not any(CLAUSE_END.search(w["text"]) for w in tail9[:-1]):
            g1, g2, g3 = tail9[0:3], tail9[3:6], tail9[6:9]
            if all(sum(_visual_units(x["text"]) for x in g) + 2.0 <= MAX_VISUAL_UNITS + 4.0 for g in (g1, g2, g3)):
                chunks[-3:] = [g1, g2, g3]
    elif len(chunks) >= 2 and len(chunks[-1]) == 1 and len(chunks[-2]) >= 3:
        prev_c = chunks[-2]
        if not CLAUSE_END.search(prev_c[-1]["text"]) and not SENTENCE_END.search(prev_c[-1]["text"]):
            moved = prev_c[-1]
            cand_tail = [moved, chunks[-1][0]]
            if sum(_visual_units(x["text"]) for x in cand_tail) + 1.0 <= MAX_VISUAL_UNITS + 4.0:
                chunks[-2] = prev_c[:-1]
                chunks[-1] = cand_tail

    return [c for c in chunks if c]


def _group_segment_words(words: List[Dict[str, Any]]) -> List[List[Dict[str, Any]]]:
    if not words:
        return []
    runs: List[List[Dict[str, Any]]] = []
    cur_run: List[Dict[str, Any]] = []
    for w in words:
        if cur_run:
            gap = w["start"] - cur_run[-1]["end"]
            if gap > MAX_GAP_IN_CUE or SENTENCE_END.search(cur_run[-1]["text"]):
                runs.append(cur_run)
                cur_run = []
        cur_run.append(w)
    if cur_run:
        runs.append(cur_run)

    groups: List[List[Dict[str, Any]]] = []
    for r in runs:
        groups.extend(_chunk_continuous_phrase(r))
    return groups


def _cue(words: List[Dict[str, Any]], max_end: Optional[float] = None) -> Dict[str, Any]:
    start_t = words[0]["start"]
    raw_end = max(w["end"] for w in words) + 0.04
    if max_end is not None:
        raw_end = min(raw_end, max_end)
    end_t = max(start_t + 0.04, raw_end)
    cue_words = []
    for w in words:
        ws = max(start_t, w["start"])
        we = min(end_t, max(ws + 0.02, w["end"]))
        cue_words.append({"text": w["text"], "startTime": round(ws, 3), "endTime": round(we, 3)})
    return {
        "startTime": round(start_t, 3),
        "endTime": round(end_t, 3),
        "text": " ".join(w["text"] for w in words),
        "words": cue_words,
    }


def build_cues(
    segs: List[Dict[str, Any]],
    starts: List[float],
    script_lines: Optional[List[Dict[str, Any]]] = None,
    work_dir: Optional[Path] = None,
) -> List[Dict[str, Any]]:
    """Caption cues on the finished timeline (seconds): 2 to 4 words each (up to 3 for Indic scripts),
    strictly monotonic (`cue[k].endTime <= cue[k+1].startTime`), with zero blank dialogue segments."""
    try:
        from prepare import _word_similarity as similarity
    except Exception:   # noqa: BLE001
        similarity = lambda a, b: 1.0 if a == b else 0.0   # noqa: E731

    line_tokens = _build_line_token_map(script_lines)
    n_segs = len(segs)
    seg_words_list: List[List[Dict[str, Any]]] = [[] for _ in range(n_segs)]
    seg_spoken_list: List[List[Tuple[float, float]]] = [[] for _ in range(n_segs)]
    seg_tl_bounds: List[Tuple[float, float]] = []

    # Track straddling boundary words emitted at the end of a segment so an adjacent cut of the same clip doesn't duplicate them
    prev_clip_src: Optional[str] = None
    prev_clip_en: float = -999.0
    prev_emitted_keys: Dict[Tuple[str, float, float], float] = {}

    for i, s in enumerate(segs):
        st, en = float(s["start"]), float(s["end"])
        t0 = float(starts[i])
        tl_end = float(starts[i + 1]) if i + 1 < len(starts) else t0 + max(0.0, en - st)
        seg_tl_bounds.append((t0, tl_end))

        if s.get("beat"):
            prev_clip_src = None
            prev_emitted_keys = {}
            continue

        src = s.get("src")
        src_key = str(src or "")
        spoken = _call_clip_loader(_speech_spans, src, work_dir)
        seg_spoken_list[i] = spoken
        target_ids = _seg_lines(str(s.get("id") or ""))
        target_toks = [tok for lid in target_ids for tok in line_tokens.get(lid, [])]

        same_contiguous_clip = (
            bool(src_key)
            and src_key == prev_clip_src
            and st < prev_clip_en + 0.15
            and st >= prev_clip_en - 0.50
        )
        if not same_contiguous_clip:
            prev_emitted_keys = {}

        # Next segment lookahead if next segment shares the exact same contiguous clip
        next_same_clip = False
        next_st, next_en = 0.0, 0.0
        if i + 1 < n_segs and not segs[i + 1].get("beat") and str(segs[i + 1].get("src") or "") == src_key:
            next_st, next_en = float(segs[i + 1]["start"]), float(segs[i + 1]["end"])
            if next_st < en + 0.15 and next_st >= st:
                next_same_clip = True

        candidates: List[Dict[str, Any]] = []
        for w in _call_clip_loader(_words_of_clip, src, work_dir):
            text = str(w.get("w") or w.get("word") or w.get("text") or "").strip()
            if not text:
                continue
            ws_raw, we_raw = float(w.get("start", 0.0)), float(w.get("end", 0.0))
            if we_raw < ws_raw:
                continue
            dur_w = max(0.001, we_raw - ws_raw)
            mid = (ws_raw + we_raw) / 2.0
            ov = max(0.0, min(we_raw, en) - max(ws_raw, st))
            min_ov = max(0.06, min(0.12, 0.45 * dur_w))
            if not ((st <= mid <= en) or ov >= min_ov):
                continue

            raw_key = (text.lower(), round(ws_raw, 3), round(we_raw, 3))
            if same_contiguous_clip and raw_key in prev_emitted_keys:
                continue
            if next_same_clip and we_raw > next_st and ws_raw < en:
                ov_next = max(0.0, min(we_raw, next_en) - max(ws_raw, next_st))
                if ov_next > ov + 0.015:
                    continue

            matches_target = _word_matches_tokens(text, target_toks, similarity, threshold=0.78) if target_toks else False

            ws, we = ws_raw, we_raw
            if spoken:
                vad_ov = sum(max(0.0, min(we, b) - max(ws, a)) for a, b in spoken)
                near_vad = any(a - 0.05 <= mid <= b + 0.05 for a, b in spoken)
                if vad_ov < 0.04 and not near_vad:
                    # Rescue quiet syllables only when they match the segment's target script line AND sit near active speech
                    near_any_speech = any(a - 0.35 <= mid <= b + 0.35 for a, b in spoken)
                    if not (matches_target and near_any_speech):
                        continue
                # Clamp Viterbi silence-stretched word tails (> 0.65s) to speech_span_end + 0.08s
                if we - ws > 0.65:
                    ov_spans = sorted(((a, b) for a, b in spoken if min(we, b) > max(ws, a)), key=lambda x: x[0])
                    if ov_spans:
                        span_end = ov_spans[0][1]
                        for a_nx, b_nx in ov_spans[1:]:
                            if a_nx - span_end <= 0.12:
                                span_end = max(span_end, b_nx)
                            else:
                                break
                        if ws + 0.08 < span_end + 0.08 < we:
                            we = span_end + 0.08

            candidates.append({
                "text": text,
                "ws_raw": ws_raw,
                "we_raw": we_raw,
                "ws": ws,
                "we": we,
                "ov": ov,
                "raw_key": raw_key,
                "matches_target": matches_target,
            })

        candidates.sort(key=lambda x: (x["ws"], x["we"]))

        # Edge-only foreign-word guard: ONLY inspect leading/trailing cut-edge words outside [first_target..last_target]
        if candidates and target_ids and line_tokens:
            prev_ids, next_ids = _adjacent_line_ids(target_ids, line_tokens)
            active_wins, _ = _active_speech_windows(st, en, spoken)
            speech_start = active_wins[0][0] if active_wins else st
            speech_end = active_wins[-1][1] if active_wins else en

            target_positions = [idx for idx, c in enumerate(candidates) if c["matches_target"]]
            first_t = target_positions[0] if target_positions else len(candidates)
            last_t = target_positions[-1] if target_positions else -1

            filtered: List[Dict[str, Any]] = []
            for idx, c in enumerate(candidates):
                if first_t <= idx <= last_t:
                    # Interior word (or target-matching word): NEVER delete interior spoken words
                    filtered.append(c)
                    continue
                is_lead_edge = idx < first_t and ((c["ws_raw"] - st) < 0.65 or (c["ws_raw"] - speech_start) < 0.65)
                is_tail_edge = idx > last_t and ((en - c["we_raw"]) < 0.65 or (speech_end - c["we_raw"]) < 0.65)
                if is_lead_edge and prev_ids and _foreign_to_line(c["text"], target_ids, line_tokens, similarity, adjacent_lines=prev_ids):
                    continue
                if is_tail_edge and next_ids and _foreign_to_line(c["text"], target_ids, line_tokens, similarity, adjacent_lines=next_ids):
                    continue
                filtered.append(c)
            candidates = filtered

        emitted_keys: Dict[Tuple[str, float, float], float] = {}
        seg_out: List[Dict[str, Any]] = []
        seg_len = max(0.04, tl_end - t0)
        for c in candidates:
            rel_s = max(0.0, c["ws"] - st)
            rel_e = min(en - st, max(rel_s + 0.02, c["we"] - st))
            tl_s = min(t0 + seg_len - 0.02, t0 + rel_s)
            tl_e = min(t0 + seg_len, max(tl_s + 0.02, t0 + rel_e))
            seg_out.append({"text": c["text"], "start": tl_s, "end": tl_e, "seg_idx": i})
            emitted_keys[c["raw_key"]] = c["ov"]

        seg_words_list[i] = seg_out
        prev_clip_src = src_key
        prev_clip_en = en
        prev_emitted_keys = emitted_keys

    # Script-Line + Speech-Span Fallback: backfill dialogue segments whose aligned word coverage is missing (< 35% or 0 words)
    if script_lines:
        idx = 0
        while idx < n_segs:
            s = segs[idx]
            target_ids = _seg_lines(str(s.get("id") or ""))
            if s.get("beat") or not target_ids:
                idx += 1
                continue
            run_end = idx + 1
            while run_end < n_segs and not segs[run_end].get("beat") and _seg_lines(str(segs[run_end].get("id") or "")) == target_ids:
                run_end += 1

            expected = _script_words_for_lines(target_ids, script_lines)
            if expected:
                got_total = sum(len(seg_words_list[k]) for k in range(idx, run_end))
                tl_spans: List[Tuple[int, float, float]] = []
                total_speech = 0.0
                for k in range(idx, run_end):
                    st_k, en_k = float(segs[k]["start"]), float(segs[k]["end"])
                    t0_k, tl_end_k = seg_tl_bounds[k]
                    wins, sp_dur = _active_speech_windows(st_k, en_k, seg_spoken_list[k])
                    if sp_dur >= 0.25:
                        total_speech += sp_dur
                        for wa, wb in wins:
                            ta = t0_k + max(0.0, wa - st_k)
                            tb = min(tl_end_k, t0_k + max(0.04, wb - st_k))
                            if tb - ta >= 0.04:
                                tl_spans.append((k, ta, tb))

                needs_full_synth = (got_total == 0) or (len(expected) >= 3 and got_total < 0.35 * len(expected))
                if needs_full_synth and total_speech >= 0.25 and tl_spans:
                    synthesized = _synthesize_words_over_spans(expected, tl_spans)
                    for k in range(idx, run_end):
                        seg_words_list[k] = synthesized.get(k, [])
            idx = run_end

    # Group words into rhythmic cues and bridge tiny intra-sentence micro-gaps without ever overlapping
    cues: List[Dict[str, Any]] = []
    for i, words in enumerate(seg_words_list):
        if not words:
            continue
        t0, tl_end = seg_tl_bounds[i]
        words.sort(key=lambda x: (x["start"], x["end"]))
        groups = _group_segment_words(words)
        seg_cues = [_cue(g, max_end=tl_end) for g in groups if g]
        for k, c in enumerate(seg_cues):
            c["segIndex"] = i
            if k + 1 < len(seg_cues):
                next_st = seg_cues[k + 1]["startTime"]
                last_word_end = c["words"][-1]["endTime"]
                gap = next_st - last_word_end
                if 0.0 <= gap <= BRIDGE_GAP_MAX and not SENTENCE_END.search(c["words"][-1]["text"]):
                    c["endTime"] = round(next_st, 3)
                    c["words"][-1]["endTime"] = round(next_st, 3)
                elif c["endTime"] > next_st:
                    c["endTime"] = round(next_st, 3)
                    for cw in c["words"]:
                        if cw["endTime"] > c["endTime"]:
                            cw["endTime"] = c["endTime"]
            else:
                if c["endTime"] > round(tl_end, 3):
                    c["endTime"] = round(tl_end, 3)
                    for cw in c["words"]:
                        if cw["endTime"] > c["endTime"]:
                            cw["endTime"] = c["endTime"]
        cues.extend(seg_cues)

    # Global strict non-overlap invariant across segment boundaries
    for k in range(len(cues) - 1):
        if cues[k]["endTime"] > cues[k + 1]["startTime"]:
            cues[k]["endTime"] = cues[k + 1]["startTime"]
            for cw in cues[k]["words"]:
                if cw["endTime"] > cues[k]["endTime"]:
                    cw["endTime"] = cues[k]["endTime"]

    return cues


# ---------------------------------------------------------------- layout-aware safe Y placement
def _is_split_v_segment(seg: Dict[str, Any]) -> bool:
    lay = str(seg.get("layout") or "single").split(":")[0].strip().lower()
    if lay in ("split_v", "gmeet_grid", "top_bottom"):
        return True
    active = str(seg.get("active") or seg.get("slot") or "").strip().lower()
    if lay == "single" and active in ("top", "bottom"):
        return True
    return False


def resolve_safe_y_percent(
    spec: Dict[str, Any],
    segs: List[Dict[str, Any]],
    starts: List[float],
    total: float,
    width: int,
    height: int,
    cues: Optional[List[Dict[str, Any]]] = None,
) -> float:
    """Compute default `y_percent` (`72.0` on `9:16`, `82.0` on `1:1` square, `50.0` on `split_v` seam)
    and attach per-cue `style.positionY` overrides when segments switch between `split_v` and `single` or show banners."""
    raw_y = spec.get("y")
    explicit_y = raw_y is not None and str(raw_y).strip().lower() not in ("", "auto", "none")
    is_square = abs(width - height) <= width * 0.15
    base_single_y = 82.0 if is_square else 72.0

    split_v_dur = 0.0
    measured_dur = 0.0
    for i, s in enumerate(segs):
        t0 = float(starts[i]) if i < len(starts) else 0.0
        t1 = float(starts[i + 1]) if i + 1 < len(starts) else max(t0, float(total))
        d = max(0.0, t1 - t0)
        measured_dur += d
        if _is_split_v_segment(s):
            split_v_dur += d

    majority_split_v = measured_dur > 0.0 and (split_v_dur / measured_dur) >= 0.50
    default_y = float(raw_y) if explicit_y else (50.0 if majority_split_v else base_single_y)

    if not cues:
        return default_y

    banner_fn = None
    try:
        from layouts import banner_boxes as banner_fn
    except Exception:
        banner_fn = None

    for c in cues:
        seg_idx = c.get("segIndex")
        if seg_idx is None:
            mid_t = 0.5 * (float(c["startTime"]) + float(c["endTime"]))
            seg_idx = 0
            for idx in range(len(segs)):
                t0 = float(starts[idx]) if idx < len(starts) else 0.0
                t1 = float(starts[idx + 1]) if idx + 1 < len(starts) else float(total)
                if t0 - 0.05 <= mid_t <= t1 + 0.05:
                    seg_idx = idx
                    break
        if not (0 <= seg_idx < len(segs)):
            continue

        seg = segs[seg_idx]
        t0 = float(starts[seg_idx]) if seg_idx < len(starts) else 0.0
        cue_y = default_y if explicit_y else (50.0 if _is_split_v_segment(seg) else base_single_y)

        if banner_fn is not None and (seg.get("banners") or seg.get("stop_saying")):
            try:
                seg_dur = max(0.08, float(seg["end"]) - float(seg["start"]))
                boxes = banner_fn(seg, width, height, seg_dur)
                rel_a = max(0.0, float(c["startTime"]) - t0)
                rel_b = max(rel_a + 0.04, float(c["endTime"]) - t0)
                active_boxes = [b for b in boxes if float(b["t1"]) >= rel_a and float(b["t0"]) <= rel_b]
                if active_boxes and height > 0:
                    b_top = min(float(b["y0"]) for b in active_boxes) / height * 100.0
                    b_bot = max(float(b["y1"]) for b in active_boxes) / height * 100.0
                    if (cue_y + 5.8) >= (b_top - 1.5) and (cue_y - 5.8) <= (b_bot + 1.5):
                        below_y = b_bot + 7.5
                        above_y = b_top - 7.5
                        cue_y = below_y if below_y <= 86.0 else max(18.0, above_y)
            except Exception:
                pass

        if abs(cue_y - default_y) >= 0.5:
            c.setdefault("style", {})["positionY"] = round(cue_y, 2)

    return default_y


# ---------------------------------------------------------------- rendering & ffconcat
def render_frames(
    cues: List[Dict[str, Any]],
    style: Dict[str, Any],
    width: int,
    height: int,
    out_dir: Path,
    y_percent: float = 72.0,
    fps: int = 25,
    return_meta: bool = False,
) -> Any:
    """Draw every caption state to a transparent PNG with the Node renderer;
    returns `frames` list by default, or the full manifest dict `{frames, bandY0, bandHeight, ...}` when `return_meta=True`."""
    out_dir.mkdir(parents=True, exist_ok=True)
    clean_cues = [{k: v for k, v in c.items() if k != "segIndex"} for c in cues]
    job = {
        "width": width,
        "height": height,
        "fps": max(24, int(fps or 25)),
        "styleId": style["id"],
        "outDir": str(out_dir),
        "positionY": y_percent,
        "cues": clean_cues,
    }
    jf = out_dir / "job.json"
    jf.write_text(json.dumps(job, ensure_ascii=False), encoding="utf-8")
    r = subprocess.run([node_path(), str(SUB_DIR / "render_cues.js"), str(jf)], capture_output=True, text=True, encoding="utf-8", timeout=900)
    if r.returncode != 0:
        raise RuntimeError(f"subtitle renderer failed: {(r.stderr or r.stdout).strip()[-400:]}")
    line = [l for l in r.stdout.splitlines() if l.startswith("{")][-1]
    manifest = json.loads(line)
    return manifest if return_meta else manifest["frames"]


def write_concat_list(frames: List[Dict[str, Any]], total: float, width: int, height: int, out_dir: Path, fps: int = 25) -> Path:
    """An FFmpeg concat list covering `0..total`: each caption PNG for its duration, a transparent `blank.png` matching exact cue PNG dimensions in the gaps."""
    from PIL import Image

    out_dir.mkdir(parents=True, exist_ok=True)
    blank_w, blank_h = int(width), int(height)
    for f in frames:
        fp = Path(str(f.get("path") or ""))
        if fp.is_file():
            try:
                with Image.open(fp) as im:
                    blank_w, blank_h = im.size
                break
            except Exception:
                continue

    blank = out_dir / "blank.png"
    Image.new("RGBA", (max(2, blank_w), max(2, blank_h)), (0, 0, 0, 0)).save(blank, compress_level=1)

    min_dur = 1.0 / max(24, int(fps or 25))
    ordered = sorted(
        frames,
        key=lambda x: (
            float(x["startSec"]) if "startSec" in x else float(x["startMs"]) / 1000.0,
            int(x.get("captionIndex", 0)),
            int(x.get("stepIndex", 0)),
            int(x.get("subIndex", 0)),
        ),
    )

    lines = ["ffconcat version 1.0"]
    t = 0.0
    last = str(blank)
    for f in ordered:
        raw_a = float(f["startSec"]) if "startSec" in f else float(f["startMs"]) / 1000.0
        raw_b = float(f["endSec"]) if "endSec" in f else (float(f["startMs"]) + float(f["durationMs"])) / 1000.0
        a = max(t, raw_a)
        if a >= total - 0.001:
            break
        # Bridge <15ms subframe float rounding micro-gaps so captions never blink transparent mid-cue
        if t > 0.0 and 0.0 < (a - t) < 0.015:
            a = t
        elif a - t > 0.001:
            lines += [f"file '{blank.as_posix()}'", f"duration {a - t:.4f}"]
        b = min(total, max(a + min(min_dur, max(0.02, total - a)), raw_b))
        if b - a <= 0.0005:
            continue
        lines += [f"file '{Path(f['path']).as_posix()}'", f"duration {b - a:.4f}"]
        last = f["path"]
        t = b
    if total - t > 0.001:
        lines += [f"file '{blank.as_posix()}'", f"duration {total - t:.4f}"]
        last = str(blank)
    lines.append(f"file '{Path(last).as_posix()}'")     # the concat demuxer needs the last file repeated
    lst = out_dir / "subs.ffconcat"
    lst.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return lst


def write_srt(cues: List[Dict[str, Any]], path: Path) -> None:
    def ts(x: float) -> str:
        ms = int(round(max(0.0, x) * 1000))
        return f"{ms // 3600000:02d}:{ms // 60000 % 60:02d}:{ms // 1000 % 60:02d},{ms % 1000:03d}"

    path.parent.mkdir(parents=True, exist_ok=True)
    out = []
    for n, c in enumerate(cues, 1):
        out += [str(n), f"{ts(c['startTime'])} --> {ts(c['endTime'])}", c["text"], ""]
    path.write_text("\n".join(out), encoding="utf-8")


# ---------------------------------------------------------------- which style: rotate from job to job
HISTORY = "_subs_history.json"      # in work/, one list of the styles this computer used, newest last


def _history_file(work_dir: Path) -> Path:
    return Path(work_dir) / HISTORY


def recent_styles(work_dir: Optional[Path] = None, n: int = 3) -> List[str]:
    """The last `n` subtitle styles used on this computer, newest first."""
    from common import WORK

    try:
        d = json.loads(_history_file(work_dir or WORK).read_text(encoding="utf-8"))
        return [str(x.get("style")) for x in reversed(d.get("used", []))][:n]
    except Exception:   # noqa: BLE001
        return []


def remember_style(name: str, project: str, work_dir: Optional[Path] = None) -> None:
    from common import WORK

    f = _history_file(work_dir or WORK)
    try:
        d = json.loads(f.read_text(encoding="utf-8"))
    except Exception:   # noqa: BLE001
        d = {}
    used = [x for x in d.get("used", []) if x.get("project") != project]       # a re-render of the same edit does not count twice
    used.append({"style": name, "project": project, "at": time.strftime("%Y-%m-%d %H:%M")})
    try:
        f.write_text(json.dumps({"used": used[-20:]}, ensure_ascii=False), encoding="utf-8")
    except Exception:   # noqa: BLE001
        pass


def choose_style(project: str, work_dir: Optional[Path] = None, avoid: int = 2) -> Dict[str, Any]:
    """`#subs auto`: a style that is not one of the last `avoid` used on this computer, picked by a stable hash of the project name so a
    re-render of the same edit keeps its look while the next edit gets another one."""
    pool = styles()
    recent = set(recent_styles(work_dir, avoid))
    fresh = [s for s in pool if s["name"] not in recent] or pool
    h = sum(ord(c) * (k + 1) for k, c in enumerate(project or "zeno"))
    return fresh[h % len(fresh)]


# ---------------------------------------------------------------- colours: look at the picture behind each caption
SAFE_FILLS = {          # accents and box colours that stay readable (white text on them is 4.5:1 or better; bright ones sit on dark bars)
    "bright": ["#FACC15", "#22D3EE", "#A3E635", "#F472B6", "#FB923C", "#60A5FA", "#C084FC", "#FFFFFF"],
    "box": ["#2563EB", "#7C3AED", "#15803D", "#DC2626", "#DB2777", "#0F766E", "#C2410C"],
}


def _rgb(hexcol: str) -> Tuple[float, float, float]:
    h = hexcol.lstrip("#")
    return tuple(int(h[i:i + 2], 16) / 255.0 for i in (0, 2, 4))     # type: ignore[return-value]


def _lum(rgb: Tuple[float, float, float]) -> float:
    def ch(c: float) -> float:
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4

    r, g, b = rgb
    return 0.2126 * ch(r) + 0.7152 * ch(g) + 0.0722 * ch(b)


def _contrast(a: float, b: float) -> float:
    hi, lo = max(a, b), min(a, b)
    return (hi + 0.05) / (lo + 0.05)


def _hue(hexcol: str) -> float:
    return colorsys.rgb_to_hsv(*_rgb(hexcol))[0] * 360.0


def _hue_gap(a: float, b: float) -> float:
    d = abs(a - b) % 360.0
    return min(d, 360.0 - d)


def band_stats(img: Any, y_percent: float) -> Dict[str, float]:
    """What the caption will sit on: mean luminance, the dominant hue and how colourful the band is. `img` is a small PIL RGB frame."""
    import numpy as np

    w, h = img.size
    y0, y1 = int(h * max(0.0, y_percent - 7.0) / 100.0), int(h * min(100.0, y_percent + 7.0) / 100.0)
    x0, x1 = int(w * 0.08), int(w * 0.92)
    a = np.asarray(img.convert("RGB").crop((x0, y0, x1, max(y1, y0 + 1))), dtype="float32") / 255.0
    px = a.reshape(-1, 3)
    mx, mn = px.max(axis=1), px.min(axis=1)
    sat = np.where(mx > 1e-4, (mx - mn) / np.maximum(mx, 1e-4), 0.0)
    lum = 0.2126 * px[:, 0] + 0.7152 * px[:, 1] + 0.0722 * px[:, 2]
    colourful = sat > 0.25
    hue = 0.0
    if colourful.sum() > 0.04 * len(px):
        hsv = np.array([colorsys.rgb_to_hsv(*c) for c in px[colourful][:: max(1, int(colourful.sum() // 400))]])
        ang = hsv[:, 0] * 2 * np.pi
        weights = hsv[:, 1] * hsv[:, 2]
        hue = (np.degrees(np.arctan2((np.sin(ang) * weights).sum(), (np.cos(ang) * weights).sum())) % 360.0)
    return {"lum": float(_lum(tuple(px.mean(axis=0)))), "hue": float(hue), "colour": float(colourful.mean()), "dark": float((lum < 0.25).mean())}


def adapt_style(style: Dict[str, Any], st: Dict[str, float]) -> Dict[str, Any]:
    """Colour overrides for one caption so it reads on the picture behind it. Bars and boxes stay dark and take a tint of the scene's own hue
    (they match it); the accent on the spoken word is the colour furthest from the scene's hue (it contrasts); outlined text switches colour when
    it would blend into the picture. Returns {} for styles whose colours need no change."""
    kind_mode = style.get("highlightMode") or ""
    sat = st["colour"]
    bg_hue = st["hue"]

    def best(cands: List[str], vs_dark: bool = True) -> str:
        weight = max(0.25, min(1.0, sat * 2.0))

        def score(c: str) -> float:
            gap = _hue_gap(_hue(c), bg_hue) / 180.0 if sat > 0.12 else 0.5
            ctr = min(_contrast(_lum(_rgb(c)), 0.02 if vs_dark else st["lum"]), 9.0) / 9.0
            return gap * weight + ctr * 0.6

        return max(cands, key=score)

    out: Dict[str, Any] = {}

    def dark_tint(alpha: float) -> str:
        r, g, b = colorsys.hsv_to_rgb((bg_hue % 360.0) / 360.0, min(0.55, 0.15 + sat * 0.6), 0.14)
        return f"rgba({int(r * 255)}, {int(g * 255)}, {int(b * 255)}, {alpha})"

    if kind_mode == "docu-bar":
        out["bgColor"] = dark_tint(0.90)
        out["activeColor"] = best(SAFE_FILLS["bright"])
    elif kind_mode == "frosted-lyric":
        out["bgColor"] = dark_tint(0.82)
        out["activeColor"] = best(["#38BDF8", "#22D3EE", "#FACC15", "#A3E635", "#F472B6", "#C084FC"])
    elif style.get("isGlossy") or int(style.get("id", -1)) == 3:
        out["activeColor"] = best(SAFE_FILLS["bright"])
    elif kind_mode == "word-box":
        out["wordBoxColor"] = best(SAFE_FILLS["box"], vs_dark=False)
    elif kind_mode == "gradient-badge":
        c = best(SAFE_FILLS["box"], vs_dark=False)
        out["wordBoxColor"] = c
        out["activeColor"] = c
    elif kind_mode == "cinema":
        base_c = best(["#FFE600", "#FFFFFF", "#00E5FF", "#A3E635"], vs_dark=False)
        out["color"] = base_c
        out["activeColor"] = "#FFE600" if base_c.upper() == "#FFFFFF" else "#FFFFFF"
    elif kind_mode == "comic-slap":
        out["activeColor"] = best(["#FACC15", "#22D3EE", "#A3E635", "#F472B6"], vs_dark=False)
    return out


def _frames_for_cues(video: Path, cues: List[Dict[str, Any]], out_dir: Path) -> List[Tuple[float, Any]]:
    """Small frames of the rendered picture, four a second, for measuring colours."""
    from PIL import Image
    from common import ffmpeg

    d = out_dir / "probe"
    d.mkdir(parents=True, exist_ok=True)
    ffmpeg("-i", str(video), "-vf", "fps=4,scale=108:192", "-f", "image2", str(d / "f%05d.png"), timeout=600)
    files = sorted(d.glob("f*.png"))
    return [(i / 4.0, Image.open(f).convert("RGB")) for i, f in enumerate(files)]


def build_overlay(
    plan: Dict[str, Any],
    segs: List[Dict[str, Any]],
    starts: List[float],
    total: float,
    width: int,
    height: int,
    work_dir: Path,
    srt_path: Optional[Path] = None,
    video: Optional[Path] = None,
    report_dir: Optional[Path] = None,
    fps: int = 25,
    subdir: str = "_subs",
) -> Optional[Path]:
    """Everything `render.py` needs: the concat list of caption images for the whole video (`subs.ffconcat`), plus `subs_meta.json`
    recording the cropped horizontal band (`band_y0`, `band_h`) and exact frame timings inside `(report_dir or work_dir) / subdir`."""
    spec = plan.get("subs")
    if not isinstance(spec, dict) or not spec.get("style"):
        return None
    if not ensure_ready():
        print("  [SUBS] Node.js is not installed (get the LTS version from nodejs.org) or its caption package could not be installed. Rendering without subtitles.")
        return None
    project = Path(str(plan.get("output") or "zeno")).stem
    if str(spec["style"]).strip().lower() == "auto":
        style = choose_style(project, work_dir)
    else:
        style = resolve_style(spec["style"])
    if style is None:
        print(f"  [SUBS] unknown style '{spec['style']}'. Styles: auto, " + ", ".join(f"{s['id']} {s['name']}" for s in styles()) + ". Rendering without subtitles.")
        return None

    script_lines: List[Dict[str, Any]] = []
    try:
        from prepare import parse_script_file
        from common import resolve

        if plan.get("script"):
            _, script_lines = parse_script_file(resolve(plan["script"]))
    except Exception:   # noqa: BLE001
        script_lines = []

    cache_hint = Path(report_dir) if (report_dir is not None and (Path(report_dir) / "clip_caches.json").is_file()) else Path(work_dir)
    cues = build_cues(segs, starts, script_lines, work_dir=cache_hint)
    if not cues:
        print("  [SUBS] no spoken words to show.")
        return None

    base_out = Path(report_dir) if report_dir is not None else Path(work_dir)
    out_dir = base_out / subdir
    if out_dir.is_dir():
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    eff_fps = max(24, int(fps or (plan.get("canvas") or {}).get("fps") or 25))
    y_pct = resolve_safe_y_percent(spec, segs, starts, total, width, height, cues=cues)

    adapted = 0
    if video is not None and spec.get("adapt", True):
        try:
            probes = _frames_for_cues(Path(video), cues, out_dir)
            if probes:
                for c in cues:
                    c_y = float((c.get("style") or {}).get("positionY", y_pct))
                    near = [im for t, im in probes if c["startTime"] - 0.15 <= t <= c["endTime"] + 0.15] or [
                        min(probes, key=lambda p: abs(p[0] - c["startTime"]))[1]
                    ]
                    stats = [band_stats(im, c_y) for im in near]
                    avg = {k: sum(s[k] for s in stats) / len(stats) for k in stats[0]}
                    avg["hue"] = stats[len(stats) // 2]["hue"]
                    ov = adapt_style(style, avg)
                    if ov:
                        c["style"] = {**(c.get("style") or {}), **ov}
                        adapted += 1
            shutil.rmtree(out_dir / "probe", ignore_errors=True)
        except Exception as exc:   # noqa: BLE001  (the stored colours are always readable; adapting is a refinement)
            print(f"  [SUBS] could not read the picture's colours, using the style's own: {exc}")

    manifest = render_frames(cues, style, width, height, out_dir, y_pct, fps=eff_fps, return_meta=True)
    frames = manifest["frames"]
    band_y0 = int(manifest.get("bandY0", 0))
    band_h = int(manifest.get("bandHeight", height))

    if srt_path is not None:
        write_srt(cues, srt_path)
    remember_style(style["name"], project, work_dir)
    if report_dir is not None:
        try:
            import job_report

            job_report.record_subs(report_dir, style["name"], len(cues), adapted)
        except Exception:   # noqa: BLE001
            pass

    ffconcat_path = write_concat_list(frames, total, width, band_h, out_dir, fps=eff_fps)

    meta_frames = []
    for f in frames:
        st_s = float(f["startSec"]) if "startSec" in f else float(f["startMs"]) / 1000.0
        en_s = float(f["endSec"]) if "endSec" in f else (float(f["startMs"]) + float(f["durationMs"])) / 1000.0
        meta_frames.append({
            "file": str(Path(f["path"]).resolve()),
            "start": round(st_s, 4),
            "end": round(en_s, 4),
            "text": str(f.get("text") or ""),
            "cue": int(f.get("captionIndex", 0)),
            "word": int(f.get("stepIndex", 0)),
            "sub": int(f.get("subIndex", 0)),
        })

    subs_meta = {
        "band_y0": band_y0,
        "band_h": band_h,
        "width": int(width),
        "height": int(height),
        "fps": eff_fps,
        "y_percent": round(y_pct, 2),
        "style": style["name"],
        "style_id": style["id"],
        "cues_count": len(cues),
        "frames": meta_frames,
        "srt_path": str(srt_path) if srt_path else None,
    }
    (out_dir / "subs_meta.json").write_text(json.dumps(subs_meta, indent=2, ensure_ascii=False), encoding="utf-8")

    print(f"  [SUBS] {len(cues)} captions, {len(frames)} frames (band y={band_y0}..{band_y0 + band_h}), style '{style['name']}', colours adapted on {adapted} captions")
    try:
        import events

        events.emit("subs", f"Subtitles: {len(cues)} captions in '{style['name']}', colours adapted to the picture on {adapted}", style=style['name'], captions=len(cues))
    except Exception:
        pass
    return ffconcat_path
