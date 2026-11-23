"""End-to-end multilingual forced alignment pipeline.

Combines native-script Meta omniASR-CTC-300M-v2 (Apache 2.0) emission extraction,
dual-script greedy-CTC phonetic bridging for code-switched speech, silence-masked
Viterbi decoding, and acoustic onset/offset boundary refinement.
"""

from __future__ import annotations

import os
import re
import time
import unicodedata
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# Ensure PyTorch hub model cache resolves to Zeno/models/torch before loading acoustic modules.
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

# Normalize typographic quotes, dashes, ellipses, and NBSPs before tokenization.
_SYMBOL_MAP = str.maketrans(
    {
        "\u2018": "'",
        "\u2019": "'",
        "\u201a": "'",
        "\u2032": "'",
        "\u02bc": "'",
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

_INDIC_SCRIPT_RE = re.compile(r"[\u0900-\u0D7F]")
_OMNIASR_INVISIBLE_CHARS: Tuple[str, ...] = tuple(
    dict.fromkeys(
        tuple(INVISIBLE_CHARS)
        + (
            "\u200b",  # zero-width space
            "\u200c",  # zero-width non-joiner
            "\u200d",  # zero-width joiner
            "\ufeff",  # BOM / zero-width no-break space
            "\u200e",  # left-to-right mark
            "\u200f",  # right-to-left mark
            "\u00ad",  # soft hyphen
            "\u2060",  # word joiner
        )
    )
)

_EN_PHONETIC_OVERRIDES: Dict[str, str] = {
    "i": "ai",
    "im": "aim",
    "you": "yu",
    "are": "ar",
    "do": "du",
    "to": "tu",
    "two": "tu",
    "too": "tu",
    "me": "mi",
    "we": "vi",
    "he": "hi",
    "she": "shi",
    "be": "bi",
    "my": "mai",
    "by": "bai",
    "why": "vai",
    "try": "trai",
    "nice": "nais",
    "like": "laik",
    "time": "taim",
    "fine": "fain",
    "five": "faiv",
    "nine": "nain",
    "line": "lain",
    "right": "rait",
    "night": "nait",
    "light": "lait",
    "high": "hai",
    "know": "no",
    "now": "nau",
    "how": "hau",
    "out": "aut",
    "one": "van",
    "once": "vans",
    "done": "dan",
    "come": "kam",
    "some": "sam",
    "love": "lav",
    "have": "hav",
    "give": "giv",
    "yes": "yes",
    "yeah": "ya",
    "please": "pliz",
    "speak": "spik",
    "read": "rid",
    "need": "nid",
    "see": "si",
    "feel": "fil",
    "real": "riyal",
    "want": "vant",
    "what": "vat",
    "when": "ven",
    "where": "ver",
    "which": "vich",
    "who": "hu",
    "use": "yuz",
    "new": "nyu",
    "view": "vyu",
    "sure": "shur",
    "for": "far",
    "work": "vark",
    "word": "vard",
    "world": "varld",
    "first": "farst",
    "sir": "sar",
    "learn": "larn",
}


def _lowercase_latin_only(text: str) -> str:
    """Lowercase ASCII and Latin-1 uppercase letters while keeping Indic/non-Latin scripts verbatim."""
    out: List[str] = []
    for ch in text:
        if "A" <= ch <= "Z":
            out.append(ch.lower())
        elif "\u00c0" <= ch <= "\u00de" and ch != "\u00d7":
            out.append(ch.lower())
        else:
            out.append(ch)
    return "".join(out)


def _is_word_char(ch: str) -> bool:
    """Return True for letters, combining marks (Indic matras/viramas), and digits."""
    cat = unicodedata.category(ch)
    return bool(cat) and cat[0] in ("L", "M", "N")


def _latin_g2p_segment(seg: str) -> str:
    """Convert an English/Latin segment into a lightweight phonetic spelling."""
    s = seg.lower().replace("'", "")
    if not s:
        return ""
    if s in _EN_PHONETIC_OVERRIDES:
        return _EN_PHONETIC_OVERRIDES[s]
    s = s.replace("tion", "shan").replace("sion", "shan")
    s = s.replace("igh", "ai")
    s = re.sub(r"^kn", "n", s)
    s = re.sub(r"^wr", "r", s)
    s = s.replace("wh", "v").replace("ph", "f").replace("ck", "k")
    s = re.sub(r"c([eiy])", r"s\1", s)
    s = re.sub(r"c(?!h)", "k", s)
    if len(s) >= 4:
        s = re.sub(r"a([^aeiou])e(s?)$", r"e\1\2", s)
        s = re.sub(r"i([^aeiou])e(s?)$", r"ai\1\2", s)
    return s


def _cross_script_phonetic_key(word: str) -> str:
    """Compute a script-invariant canonical phonetic key across Latin, Indic, and digit tokens."""
    text = unicodedata.normalize("NFC", str(word or "")).translate(_SYMBOL_MAP)
    for inv in _OMNIASR_INVISIBLE_CHARS:
        if inv in text:
            text = text.replace(inv, "")

    segments = re.findall(r"[A-Za-z']+|[0-9]+|[\u0900-\u0D7F]+", text)
    if not segments:
        return ""

    parts: List[str] = []
    for seg in segments:
        if _INDIC_SCRIPT_RE.search(seg):
            parts.append(IndicPhoneticMapper.transliterate_indic_script(seg).lower())
        elif seg.isdigit():
            num_words = IndicPhoneticMapper.number_to_words(seg).lower()
            for nw in num_words.split():
                parts.append(_latin_g2p_segment(nw))
        else:
            parts.append(_latin_g2p_segment(seg))

    key = "".join(parts)
    key = key.replace("oo", "u").replace("ee", "i")
    key = re.sub(r"chh|ch", "c", key)
    for src_pat, dst_pat in (
        ("sh", "s"),
        ("th", "t"),
        ("dh", "d"),
        ("bh", "b"),
        ("gh", "g"),
        ("kh", "k"),
        ("jh", "j"),
        ("zh", "l"),
        ("ph", "f"),
        ("w", "v"),
        ("z", "s"),
        ("q", "k"),
        ("x", "ks"),
    ):
        key = key.replace(src_pat, dst_pat)
    key = re.sub(r"[^a-z]", "", key)
    key = re.sub(r"[ao]+", "a", key)
    key = re.sub(r"[iey]+", "i", key)
    key = re.sub(r"u+", "u", key)
    key = re.sub(r"(.)\1+", r"\1", key)
    return key


class ZenoAcousticPipeline:
    """Orchestrates OmniASR CTC extraction, cross-script phonetic bridging, Viterbi alignment, and onset verification."""

    def __init__(self, device: Optional[str] = None):
        self.acoustic_model = AcousticModel(device=device)
        self.viterbi = ViterbiAligner(blank_id=self.acoustic_model.blank_id)
        self.onset_verifier = AcousticOnsetVerifier(sample_rate=self.acoustic_model.sample_rate)
        self._id_to_token: Dict[int, str] = {}
        self._id_to_token_vocab_len: int = -1

    def unload_model(self) -> None:
        """Unload acoustic neural weights from memory."""
        if self.acoustic_model is not None:
            self.acoustic_model.unload_model()

    def _get_id_to_token(self) -> Dict[int, str]:
        vocab: Dict[str, int] = getattr(self.acoustic_model, "dictionary", {}) or {}
        if len(vocab) != self._id_to_token_vocab_len:
            self._id_to_token = {int(v): str(k) for k, v in vocab.items()}
            self._id_to_token_vocab_len = len(vocab)
        return self._id_to_token

    def _word_to_omniasr_tokens(self, orig_w: str, clean_w: str) -> List[int]:
        """Tokenize a word directly into native-script OmniASR vocabulary IDs."""
        vocab: Dict[str, int] = getattr(self.acoustic_model, "dictionary", {}) or {}
        blank_id = int(getattr(self.acoustic_model, "blank_id", 0))
        if not vocab:
            return []

        raw_text = str(orig_w if orig_w else clean_w or "")
        text = unicodedata.normalize("NFC", raw_text).translate(_SYMBOL_MAP)
        for inv in _OMNIASR_INVISIBLE_CHARS:
            if inv in text:
                text = text.replace(inv, "")
        text = _lowercase_latin_only(text)

        n = len(text)
        token_ids: List[int] = []
        for idx, ch in enumerate(text):
            if ch.isspace():
                continue
            cat = unicodedata.category(ch)
            cat0 = cat[0] if cat else ""

            if cat0 == "C":
                continue

            # Strip punctuation/symbols so unpronounced marks never corrupt CTC trellises,
            # while keeping strictly word-internal apostrophes (e.g. "don't") if present in vocab.
            if cat0 in ("P", "S"):
                if (
                    ch == "'"
                    and ch in vocab
                    and vocab[ch] != blank_id
                    and idx > 0
                    and idx + 1 < n
                    and _is_word_char(text[idx - 1])
                    and _is_word_char(text[idx + 1])
                ):
                    token_ids.append(int(vocab[ch]))
                continue

            tok = vocab.get(ch)
            if tok is not None and tok != blank_id:
                token_ids.append(int(tok))
                continue

            # NFD decomposition fallback for precomposed characters absent from vocab.
            nfd = unicodedata.normalize("NFD", ch)
            if len(nfd) > 1 and all(
                (vocab.get(sub) is not None and vocab.get(sub) != blank_id) for sub in nfd
            ):
                for sub in nfd:
                    token_ids.append(int(vocab[sub]))

        return token_ids

    def _greedy_decode_words(self, log_probs: np.ndarray) -> List[Dict[str, Any]]:
        """Fast greedy CTC collapse over `log_probs` `[T, V]`, splitting on `' '` or >=6 blank frames."""
        T = int(log_probs.shape[0]) if log_probs is not None else 0
        if T <= 0:
            return []

        vocab: Dict[str, int] = getattr(self.acoustic_model, "dictionary", {}) or {}
        blank_id = int(getattr(self.acoustic_model, "blank_id", 0))
        id_to_tok = self._get_id_to_token()
        space_raw = vocab.get(" ")
        space_id = int(space_raw) if (space_raw is not None and int(space_raw) != blank_id) else None

        best_ids = np.argmax(log_probs, axis=-1)
        best_lp = log_probs[np.arange(T), best_ids]

        greedy_words: List[Dict[str, Any]] = []
        cur_toks: List[int] = []
        cur_lps: List[float] = []
        cur_f_start = 0
        cur_f_end = 0
        prev_id: Optional[int] = None
        blank_run = 0

        def _flush() -> None:
            nonlocal cur_toks, cur_lps
            if not cur_toks:
                return
            text = "".join(id_to_tok.get(tid, "") for tid in cur_toks)
            mean_prob = float(np.mean(np.exp(np.clip(np.asarray(cur_lps, dtype=np.float32), -20.0, 0.0))))
            phon = _cross_script_phonetic_key(text)
            if mean_prob >= 0.25 and len(cur_toks) >= 1 and len(phon) >= 1:
                greedy_words.append(
                    {
                        "text": text,
                        "token_ids": list(cur_toks),
                        "f_start": int(cur_f_start),
                        "f_end": int(cur_f_end),
                        "mean_prob": mean_prob,
                        "phon": phon,
                    }
                )
            cur_toks = []
            cur_lps = []

        for t in range(T):
            tid = int(best_ids[t])
            tok = id_to_tok.get(tid, "")
            is_control = tid == blank_id or (tok.startswith("<") and tok.endswith(">"))
            if is_control:
                prev_id = tid
                blank_run += 1
                if blank_run >= 6 and cur_toks:
                    _flush()
                continue

            blank_run = 0
            if (space_id is not None and tid == space_id) or (tok and tok.isspace()):
                prev_id = tid
                if cur_toks:
                    _flush()
                continue

            if tok and not _is_word_char(tok[0]) and tok != "'":
                prev_id = tid
                continue

            lp = float(best_lp[t])
            if tid == prev_id and cur_toks:
                if lp > cur_lps[-1]:
                    cur_lps[-1] = lp
                cur_f_end = t + 1
            else:
                if not cur_toks:
                    cur_f_start = t
                cur_toks.append(tid)
                cur_lps.append(lp)
                cur_f_end = t + 1
            prev_id = tid

        _flush()
        return greedy_words

    def _bridge_omniasr_words(
        self,
        words_info: List[Dict[str, Any]],
        log_probs: np.ndarray,
        language_code: str,
    ) -> List[List[int]]:
        """Bridge code-switched script mismatches using greedy CTC emissions and cross-script phonetic keys."""
        del language_code
        n_words = len(words_info)
        out_tokens = [list(w.get("direct_tokens") or []) for w in words_info]
        if n_words == 0 or log_probs is None or log_probs.ndim != 2:
            return out_tokens

        T, V = int(log_probs.shape[0]), int(log_probs.shape[1])
        if T <= 0 or V <= 0:
            return out_tokens

        greedy_words = self._greedy_decode_words(log_probs)
        if not greedy_words:
            return out_tokens

        id_to_tok = self._get_id_to_token()
        bands: List[Tuple[int, int]] = []
        direct_supports: List[float] = []
        w_phons: List[str] = []

        for i, w in enumerate(words_info):
            if T <= 400:
                b0, b1 = 0, T
            else:
                b0 = max(0, int((i / max(1, n_words) - 0.35) * T))
                b1 = min(T, int(((i + 1) / max(1, n_words) + 0.35) * T))
                if b1 <= b0:
                    b0, b1 = 0, T
            bands.append((b0, b1))

            valid_tids = [tid for tid in out_tokens[i] if 0 <= tid < V]
            if valid_tids:
                tok_probs = [
                    float(np.max(np.exp(np.clip(log_probs[b0:b1, tid], -20.0, 0.0))))
                    for tid in valid_tids
                ]
                sup = sum(tok_probs) / len(tok_probs)
                latin_probs = [
                    p
                    for tid, p in zip(valid_tids, tok_probs)
                    if "a" <= id_to_tok.get(tid, "").lower() <= "z"
                ]
                indic_probs = [
                    p
                    for tid, p in zip(valid_tids, tok_probs)
                    if _INDIC_SCRIPT_RE.search(id_to_tok.get(tid, ""))
                ]
                if latin_probs and indic_probs:
                    sup = min(
                        sup,
                        sum(latin_probs) / len(latin_probs),
                        sum(indic_probs) / len(indic_probs),
                    )
            else:
                sup = 0.0
            direct_supports.append(sup)
            w_phons.append(_cross_script_phonetic_key(w.get("orig") or w.get("clean") or ""))

        # 1) Monotonic DP anchoring strong-support words (>= 0.20) to greedy words.
        strong_indices = [i for i in range(n_words) if direct_supports[i] >= 0.20 and w_phons[i]]
        n_strong = len(strong_indices)
        n_greedy = len(greedy_words)
        anchors: List[Tuple[int, int]] = []

        if n_strong > 0 and n_greedy > 0:
            dp = np.zeros((n_strong + 1, n_greedy + 1), dtype=np.float64)
            choice = np.zeros((n_strong + 1, n_greedy + 1), dtype=np.int8)

            for si in range(1, n_strong + 1):
                i = strong_indices[si - 1]
                w_p = w_phons[i]
                len_w = len(w_p)
                b0_i, b1_i = bands[i]
                rel_w = i / max(1, n_words)

                for k in range(1, n_greedy + 1):
                    best = dp[si - 1, k]
                    ch = 0
                    if dp[si, k - 1] > best:
                        best = dp[si, k - 1]
                        ch = 1

                    gw = greedy_words[k - 1]
                    if gw["f_end"] > b0_i and gw["f_start"] < b1_i:
                        g_p = gw["phon"]
                        len_g = len(g_p)
                        mn, mx = (len_w, len_g) if len_w <= len_g else (len_g, len_w)
                        if mx > 0 and (2.0 * mn) / (mn + mx) >= 0.75:
                            sim = SequenceMatcher(None, w_p, g_p).ratio()
                            if sim >= 0.75:
                                rel_g = 0.5 * (gw["f_start"] + gw["f_end"]) / max(1, T)
                                cand = dp[si - 1, k - 1] + 1.0 + sim - 0.01 * abs(rel_w - rel_g)
                                if cand > best:
                                    best = cand
                                    ch = 2

                    dp[si, k] = best
                    choice[si, k] = ch

            si, k = n_strong, n_greedy
            while si > 0 and k > 0:
                ch = int(choice[si, k])
                if ch == 2:
                    anchors.append((strong_indices[si - 1], k - 1))
                    si -= 1
                    k -= 1
                elif ch == 0:
                    si -= 1
                else:
                    k -= 1
            anchors.reverse()

        # 2) Bridge weak-support words (< 0.20) monotonically inside each anchor-bounded span.
        bounds = [(-1, -1)] + anchors + [(n_words, n_greedy)]
        for (pw, pg), (nw, ng) in zip(bounds[:-1], bounds[1:]):
            gap_weak = [i for i in range(pw + 1, nw) if direct_supports[i] < 0.20 and w_phons[i]]
            gap_g_start = pg + 1
            gap_g_count = ng - gap_g_start
            if not gap_weak or gap_g_count <= 0:
                continue

            nw_gap = len(gap_weak)
            gap_dp = np.zeros((nw_gap + 1, gap_g_count + 1), dtype=np.float64)
            # Action codes: 0=skip weak, 1=skip greedy, 2=1:1, 3=1:2, 4=2:1
            gap_act = np.zeros((nw_gap + 1, gap_g_count + 1), dtype=np.int8)

            # Precompute best overlapping 1:1 ratio per gap weak word for 1:2 margin check.
            best_1to1_map: Dict[int, float] = {}
            for u in range(nw_gap):
                wi = gap_weak[u]
                w_p = w_phons[wi]
                b0_i, b1_i = bands[wi]
                best_r = 0.0
                for gk in range(gap_g_start, ng):
                    gw = greedy_words[gk]
                    if gw["f_end"] > b0_i and gw["f_start"] < b1_i:
                        r = SequenceMatcher(None, w_p, gw["phon"]).ratio()
                        if r > best_r:
                            best_r = r
                best_1to1_map[u] = best_r

            for u in range(nw_gap - 1, -1, -1):
                wi = gap_weak[u]
                w_p = w_phons[wi]
                b0_i, b1_i = bands[wi]
                rel_w = wi / max(1, n_words)
                best_1to1 = best_1to1_map[u]

                can_2to1 = (u + 1 < nw_gap) and (gap_weak[u + 1] == wi + 1)
                if can_2to1:
                    wi2 = gap_weak[u + 1]
                    b0_pair = min(b0_i, bands[wi2][0])
                    b1_pair = max(b1_i, bands[wi2][1])
                    p_pair = re.sub(r"(.)\1+", r"\1", w_p + w_phons[wi2])
                    rel_pair = 0.5 * (wi + wi2) / max(1, n_words)
                else:
                    b0_pair = b1_pair = 0
                    p_pair = ""
                    rel_pair = 0.0

                for g_off in range(gap_g_count - 1, -1, -1):
                    gk = gap_g_start + g_off
                    gw = greedy_words[gk]
                    rel_g = 0.5 * (gw["f_start"] + gw["f_end"]) / max(1, T)

                    best = gap_dp[u + 1, g_off]
                    act = 0
                    if gap_dp[u, g_off + 1] > best:
                        best = gap_dp[u, g_off + 1]
                        act = 1

                    if gw["f_end"] > b0_i and gw["f_start"] < b1_i:
                        g_p = gw["phon"]
                        min_1to1 = 0.80 if min(len(w_p), len(g_p)) <= 2 else 0.62
                        mn, mx = (len(w_p), len(g_p)) if len(w_p) <= len(g_p) else (len(g_p), len(w_p))
                        if mx > 0 and (2.0 * mn) / (mn + mx) >= min_1to1:
                            sim_1to1 = SequenceMatcher(None, w_p, g_p).ratio()
                            if sim_1to1 >= min_1to1:
                                cand = gap_dp[u + 1, g_off + 1] + 1.0 + sim_1to1 - 0.01 * abs(rel_w - rel_g)
                                if cand > best:
                                    best = cand
                                    act = 2

                    if gk + 1 < ng:
                        gw2 = greedy_words[gk + 1]
                        if (
                            gw["f_end"] > b0_i
                            and gw["f_start"] < b1_i
                            and gw2["f_end"] > b0_i
                            and gw2["f_start"] < b1_i
                            and (gw2["f_start"] - gw["f_end"]) <= 25
                        ):
                            g_comb = re.sub(r"(.)\1+", r"\1", gw["phon"] + gw2["phon"])
                            mn, mx = (len(w_p), len(g_comb)) if len(w_p) <= len(g_comb) else (len(g_comb), len(w_p))
                            if mx > 0 and (2.0 * mn) / (mn + mx) >= 0.72:
                                sim_1to2 = SequenceMatcher(None, w_p, g_comb).ratio()
                                if sim_1to2 >= 0.72 and sim_1to2 >= best_1to1 + 0.10:
                                    rel_g2 = 0.5 * (gw["f_start"] + gw2["f_end"]) / max(1, T)
                                    cand = gap_dp[u + 1, g_off + 2] + 1.0 + sim_1to2 - 0.01 * abs(rel_w - rel_g2)
                                    if cand > best:
                                        best = cand
                                        act = 3

                    if (
                        can_2to1
                        and len(gw["token_ids"]) >= 2
                        and gw["f_end"] > b0_pair
                        and gw["f_start"] < b1_pair
                    ):
                        g_p = gw["phon"]
                        mn, mx = (len(p_pair), len(g_p)) if len(p_pair) <= len(g_p) else (len(g_p), len(p_pair))
                        if mx > 0 and (2.0 * mn) / (mn + mx) >= 0.75:
                            sim_2to1 = SequenceMatcher(None, p_pair, g_p).ratio()
                            if sim_2to1 >= 0.75:
                                cand = gap_dp[u + 2, g_off + 1] + 2.0 + sim_2to1 - 0.01 * abs(rel_pair - rel_g)
                                if cand > best:
                                    best = cand
                                    act = 4

                    gap_dp[u, g_off] = best
                    gap_act[u, g_off] = act

            u, g_off = 0, 0
            while u < nw_gap and g_off < gap_g_count:
                act = int(gap_act[u, g_off])
                wi = gap_weak[u]
                gk = gap_g_start + g_off
                if act == 2:
                    out_tokens[wi] = list(greedy_words[gk]["token_ids"])
                    u += 1
                    g_off += 1
                elif act == 3:
                    out_tokens[wi] = list(greedy_words[gk]["token_ids"]) + list(greedy_words[gk + 1]["token_ids"])
                    u += 1
                    g_off += 2
                elif act == 4:
                    wi2 = gap_weak[u + 1]
                    g_toks = greedy_words[gk]["token_ids"]
                    len1 = max(1, len(w_phons[wi]))
                    len2 = max(1, len(w_phons[wi2]))
                    split_at = int(round(len(g_toks) * len1 / float(len1 + len2)))
                    split_at = max(1, min(len(g_toks) - 1, split_at))
                    out_tokens[wi] = list(g_toks[:split_at])
                    out_tokens[wi2] = list(g_toks[split_at:])
                    u += 2
                    g_off += 1
                elif act == 0:
                    u += 1
                else:
                    g_off += 1

        return out_tokens

    def _build_target_sequence_for_slice(
        self,
        words_info: List[Dict[str, Any]],
        log_probs: np.ndarray,
        language_code: str,
    ) -> Tuple[List[int], List[Dict[str, Any]]]:
        """Apply cross-script bridging and cross-word geminate `' '` separators for Viterbi alignment."""
        active_items: List[Dict[str, Any]] = []
        for w in words_info:
            dtoks = w.get("direct_tokens")
            if dtoks is None:
                dtoks = self._word_to_omniasr_tokens(str(w.get("orig") or ""), str(w.get("clean") or ""))
                w = {**w, "direct_tokens": dtoks}
            if dtoks:
                active_items.append(w)
        if not active_items:
            return [], []

        bridged_tokens = self._bridge_omniasr_words(active_items, log_probs, language_code)

        vocab: Dict[str, int] = getattr(self.acoustic_model, "dictionary", {}) or {}
        blank_id = int(getattr(self.acoustic_model, "blank_id", 0))
        space_raw = vocab.get(" ")
        space_id = int(space_raw) if (space_raw is not None and int(space_raw) != blank_id) else None
        id_to_tok = self._get_id_to_token()

        active_pairs = [(w, toks) for w, toks in zip(active_items, bridged_tokens) if toks]
        if not active_pairs:
            return [], []

        geminate_spaces = sum(
            1
            for k in range(1, len(active_pairs))
            if active_pairs[k - 1][1][-1] == active_pairs[k][1][0]
        )
        total_tokens = sum(len(toks) for _, toks in active_pairs) + geminate_spaces
        allow_geminate_space = (
            space_id is not None
            and log_probs is not None
            and len(log_probs) >= total_tokens + 6
        )

        token_ids: List[int] = []
        word_spans_meta: List[Dict[str, Any]] = []

        for w_item, w_toks in active_pairs:
            if allow_geminate_space and token_ids and token_ids[-1] == w_toks[0]:
                token_ids.append(space_id)

            tok_start = len(token_ids)
            token_ids.extend(int(t) for t in w_toks)
            tok_end = len(token_ids)
            valid_chars = tok_end - tok_start

            orig_w = str(w_item.get("orig") or "")
            is_indic = bool(_INDIC_SCRIPT_RE.search(orig_w)) or any(
                _INDIC_SCRIPT_RE.search(id_to_tok.get(int(t), "")) for t in w_toks
            )
            phonetic_len = max(valid_chars, int(round(valid_chars * 1.8))) if is_indic else valid_chars
            word_spans_meta.append(
                {
                    "word": orig_w,
                    "num_chars": valid_chars,
                    "phonetic_len": phonetic_len,
                    "tok_start": tok_start,
                    "tok_end": tok_end,
                    "orig_idx": int(w_item["orig_idx"]),
                }
            )

        return token_ids, word_spans_meta

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
        self.viterbi.blank_id = int(getattr(self.acoustic_model, "blank_id", 0))
        t0 = time.time()

        if word_list is not None and len(word_list) > 0:
            input_words = [str(w) for w in word_list]
        elif transcript_text:
            input_words = transcript_text.strip().split()
        else:
            return {"status": "error", "detail": "Empty transcript", "words": []}

        if not input_words:
            return {"status": "error", "detail": "Empty transcript", "words": []}

        words_info: List[Dict[str, Any]] = []
        clean_words: List[str] = []
        for idx, orig_w in enumerate(input_words):
            clean_w = re.sub(r"[^\w\s\u0900-\u0D7F'-]", "", str(orig_w)).strip().strip("-")
            if clean_w:
                clean_words.append(clean_w)
                direct_toks = self._word_to_omniasr_tokens(orig_w, clean_w)
                if direct_toks:
                    words_info.append(
                        {
                            "orig": orig_w,
                            "clean": clean_w,
                            "orig_idx": idx,
                            "direct_tokens": direct_toks,
                        }
                    )

        if not words_info:
            return {"status": "error", "detail": "No valid phonetic token targets found", "words": []}

        log_probs, raw_waveform, duration_sec, time_per_frame = self.acoustic_model.extract_emissions(audio_path)

        vad_spans: List[Tuple[float, float]] = []
        try:
            from . import vad

            vad_spans = vad.speech_spans(raw_waveform, getattr(self.acoustic_model, "sample_rate", 16000))
        except Exception:
            vad_spans = []

        min_keep_frame = 0
        if vad_spans and len(log_probs) > 0 and time_per_frame > 0:
            min_keep_sec = max(float(e) for _, e in vad_spans) + 1.0
            min_keep_frame = min(len(log_probs), int(min_keep_sec / time_per_frame))

        # Bound active speech window when short transcripts sit inside much longer audio buffers,
        # while NEVER truncating below Silero VAD's last detected speech span.
        if max_audio_sec is not None and max_audio_sec > 0 and len(log_probs) > 0:
            cutoff_frame = min(len(log_probs), max(1, min_keep_frame, int(max_audio_sec / time_per_frame)))
            log_probs_for_viterbi = log_probs[:cutoff_frame]
        else:
            expected_max_sec = len(clean_words) * 0.85 + 3.5
            if duration_sec > expected_max_sec * 1.5 and len(log_probs) > 0:
                bounded_frames = min(
                    len(log_probs),
                    max(min_keep_frame, int((expected_max_sec * 1.3) / time_per_frame)),
                )
                non_blank_prob = 1.0 - np.exp(log_probs[:, self.viterbi.blank_id])
                active_idx = np.where(non_blank_prob > 0.15)[0]
                if len(active_idx) > 0:
                    active_end_frame = int(np.percentile(active_idx, 98)) + int(1.5 / time_per_frame)
                    cutoff_frame = min(len(log_probs), max(bounded_frames, active_end_frame, min_keep_frame))
                    log_probs_for_viterbi = log_probs[:cutoff_frame]
                else:
                    log_probs_for_viterbi = log_probs[:bounded_frames]
            else:
                log_probs_for_viterbi = log_probs

        token_ids, word_spans_meta = self._build_target_sequence_for_slice(
            words_info,
            log_probs_for_viterbi,
            language_code,
        )
        if not token_ids or not word_spans_meta:
            return {"status": "error", "detail": "No valid phonetic token targets found", "words": []}

        silent = self.onset_verifier.silent_runs(raw_waveform)
        if vad_spans:
            try:
                from . import vad

                silent = vad.merge_runs(silent, vad.nonspeech_runs(vad_spans, duration_sec))
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
        active_map = {
            word_spans_meta[i]["orig_idx"]: (refined_active_words[i], word_spans_meta[i])
            for i in range(len(refined_active_words))
        }

        last_known_start = 0.0
        last_known_end = 0.1

        for idx, orig_w in enumerate(input_words):
            if idx in active_map:
                w_res, w_meta = active_map[idx]
                w_start = float(w_res["start"])
                w_end = float(w_res["end"])

                phonetic_len = int(w_meta.get("phonetic_len", w_meta.get("num_chars", len(orig_w))))
                max_natural_duration = min(3.5, max(1.20, phonetic_len * 0.14 + 0.65))
                if w_end - w_start > max_natural_duration:
                    capped_end = w_start + max_natural_duration
                    if w_res.get("energy_verified_end") is not None:
                        capped_end = max(capped_end, float(w_res["energy_verified_end"]))
                    w_end = min(w_end, capped_end)

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
        """Align multi-sentence clips locally within VAD-expanded cue windows without slicing active speech."""
        self.acoustic_model._ensure_model_loaded()
        self.viterbi.blank_id = int(getattr(self.acoustic_model, "blank_id", 0))
        t0 = time.time()

        if not cues:
            return {
                "status": "success",
                "engine": "Zeno Acoustic Engine (Anchor-Based)",
                "cues": [],
                "words": [],
                "processing_time_sec": 0.0,
            }

        cue_word_lists: List[List[Dict[str, Any]]] = []
        for cue in cues:
            raw_cw = cue.get("words", [])
            if not raw_cw and cue.get("text"):
                raw_cw = [w for w in str(cue["text"]).split() if w]

            c_words: List[Dict[str, Any]] = []
            for idx, w in enumerate(raw_cw):
                w_str = w.get("word", "") if isinstance(w, dict) else str(w)
                w_clean = re.sub(r"[^\w\s\u0900-\u0D7F'-]", "", w_str).strip().strip("-")
                direct_toks = self._word_to_omniasr_tokens(w_str, w_clean) if w_clean else []
                c_words.append(
                    {
                        "orig": w_str,
                        "clean": w_clean,
                        "orig_idx": idx,
                        "direct_tokens": direct_toks,
                    }
                )
            cue_word_lists.append(c_words)

        clip_sr = int(getattr(self.acoustic_model, "sample_rate", 16000) or 16000)
        probed_dur = float(total_duration_sec or 0.0)
        if probed_dur <= 0.0:
            try:
                import soundfile as sf

                probed_dur = float(sf.info(audio_path).duration)
            except Exception:
                probed_dur = 0.0

        # Extract full-clip emissions ONCE for normal clips (<= 180s) and slice in memory;
        # fall back to extract_emissions_range for > 180s clips or mocks without extract_emissions.
        full_log_probs: Optional[np.ndarray] = None
        full_waveform: Optional[np.ndarray] = None
        full_dur: float = 0.0
        full_tpf: float = 0.02
        if (probed_dur <= 0.0 or probed_dur <= 180.0) and callable(
            getattr(self.acoustic_model, "extract_emissions", None)
        ):
            try:
                em_res = self.acoustic_model.extract_emissions(audio_path)
                if (
                    isinstance(em_res, tuple)
                    and len(em_res) == 4
                    and isinstance(em_res[0], np.ndarray)
                    and isinstance(em_res[1], np.ndarray)
                ):
                    full_log_probs = em_res[0]
                    full_waveform = em_res[1]
                    full_dur = float(em_res[2])
                    full_tpf = float(em_res[3]) if float(em_res[3]) > 0 else 0.02
            except Exception:
                full_log_probs = None
                full_waveform = None

        total_dur = full_dur if full_dur > 0.0 else probed_dur

        # Compute Silero VAD speech_spans once across the clip waveform when available.
        clip_vad_spans: List[Tuple[float, float]] = []
        vad_wf = full_waveform
        if vad_wf is None and audio_path:
            try:
                import soundfile as sf

                if os.path.isfile(audio_path):
                    y_tmp, sr_tmp = sf.read(audio_path, dtype="float32")
                    if y_tmp.ndim > 1:
                        y_tmp = np.mean(y_tmp, axis=1)
                    if sr_tmp == clip_sr:
                        vad_wf = y_tmp
                        if total_dur <= 0.0:
                            total_dur = len(vad_wf) / float(clip_sr)
            except Exception:
                vad_wf = None
        if vad_wf is not None and len(vad_wf) > 0:
            try:
                from . import vad

                clip_vad_spans = vad.speech_spans(vad_wf, clip_sr)
            except Exception:
                clip_vad_spans = []

        def _get_slice(win_s: float, win_e: float) -> Tuple[np.ndarray, np.ndarray, float, float, float]:
            ws_clamped = max(0.0, float(win_s))
            we_clamped = min(total_dur, float(win_e)) if total_dur > 0.0 else float(win_e)
            if we_clamped <= ws_clamped + 0.10:
                we_clamped = ws_clamped + 0.50
                if total_dur > 0.50 and we_clamped > total_dur:
                    we_clamped = total_dur
                    ws_clamped = max(0.0, we_clamped - 0.50)

            if full_log_probs is not None and full_waveform is not None and len(full_log_probs) > 0:
                f0 = max(0, min(len(full_log_probs) - 1, int(ws_clamped / full_tpf)))
                f1 = max(f0 + 1, min(len(full_log_probs), int(np.ceil(we_clamped / full_tpf))))
                act_s = f0 * full_tpf
                act_e = min(full_dur, f1 * full_tpf)
                s0 = max(0, min(len(full_waveform), int(round(act_s * clip_sr))))
                s1 = max(s0, min(len(full_waveform), int(round(act_e * clip_sr))))
                wf_sl = full_waveform[s0:s1]
                sl_dur = len(wf_sl) / float(clip_sr) if len(wf_sl) > 0 else max(0.02, (f1 - f0) * full_tpf)
                return full_log_probs[f0:f1], wf_sl, sl_dur, full_tpf, act_s

            return self.acoustic_model.extract_emissions_range(
                audio_path=audio_path,
                start_sec=ws_clamped,
                end_sec=we_clamped,
                pad_sec=0.0,
                total_duration=total_dur if total_dur > 0.0 else total_duration_sec,
            )

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

            init_tokens: List[int] = [t for w in c_words_info for t in w["direct_tokens"]]
            if not init_tokens:
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

            c_start_bound = c_start
            c_end_bound = c_end
            if total_dur > 0.2:
                c_start_bound = max(0.0, min(c_start, total_dur - 0.15))
                c_end_bound = max(c_start_bound + 0.15, min(c_end, total_dur))

            prev_cue_end: Optional[float] = None
            if c_idx > 0:
                p_cue = cues[c_idx - 1]
                p_s = float(p_cue.get("start", p_cue.get("startTime", 0.0)))
                prev_cue_end = max(p_s, float(p_cue.get("end", p_cue.get("endTime", p_s))))

            next_cue_start: Optional[float] = None
            if c_idx + 1 < len(cues):
                n_cue = cues[c_idx + 1]
                next_cue_start = float(n_cue.get("start", n_cue.get("startTime", c_end_bound)))

            if cue.get("_vad_chunk"):
                # Chunk boundaries come directly from Silero VAD silence splits: keep tight +/-0.10s
                # margins inside the silence seam so adjacent chunks never steal words from one another.
                win_start = max(0.0, c_start_bound - 0.10)
                win_end = min(total_dur, c_end_bound + 0.10) if total_dur > 0.0 else (c_end_bound + 0.10)
            else:
                win_start = max(0.0, c_start_bound - 1.2)
                win_end = min(total_dur, c_end_bound + 2.0) if total_dur > 0.0 else (c_end_bound + 2.0)

                for sp0, sp1 in clip_vad_spans:
                    if sp0 - 0.35 <= c_end_bound <= sp1 + 0.35:
                        cand_end = sp1 + 0.35
                        if next_cue_start is not None:
                            cand_end = min(cand_end, max(c_end_bound + 1.2, next_cue_start + 1.2))
                        win_end = max(win_end, cand_end)
                    if sp0 <= c_start_bound <= sp1:
                        cand_start = max(0.0, sp0 - 0.25)
                        if prev_cue_end is not None:
                            cand_start = max(cand_start, min(c_start_bound - 0.6, prev_cue_end - 0.6))
                        win_start = min(win_start, cand_start)

                win_start = max(0.0, win_start)
                win_end = max(win_end, c_end_bound + 1.2)
                if total_dur > 0.0:
                    win_end = min(total_dur, win_end)

            req_frames = (
                len(init_tokens)
                + sum(1 for k in range(1, len(init_tokens)) if init_tokens[k] == init_tokens[k - 1])
                + 4
            )

            try:
                log_probs, raw_waveform, slice_dur, time_per_frame, actual_slice_start = _get_slice(win_start, win_end)
                if len(log_probs) < req_frames:
                    widen_s = max(0.0, min(win_start, c_start_bound - 3.5))
                    widen_e = max(win_end, c_end_bound + 3.5)
                    if total_dur > 0.0:
                        widen_e = min(total_dur, widen_e)
                    log_probs, raw_waveform, slice_dur, time_per_frame, actual_slice_start = _get_slice(
                        widen_s, widen_e
                    )
                if len(log_probs) < req_frames:
                    full_e = total_dur if total_dur > 0.0 else max(win_end, c_end_bound + 8.0)
                    log_probs, raw_waveform, slice_dur, time_per_frame, actual_slice_start = _get_slice(0.0, full_e)

                token_ids, word_spans_meta = self._build_target_sequence_for_slice(
                    c_words_info,
                    log_probs,
                    language_code,
                )
                bridged_req = (
                    len(token_ids)
                    + sum(1 for k in range(1, len(token_ids)) if token_ids[k] == token_ids[k - 1])
                    + 4
                )
                if len(log_probs) < bridged_req:
                    widen_s = max(0.0, min(actual_slice_start, c_start_bound - 3.5))
                    widen_e = max(actual_slice_start + slice_dur, c_end_bound + 3.5)
                    if total_dur > 0.0:
                        widen_e = min(total_dur, widen_e)
                    if widen_e - widen_s > slice_dur + 0.05:
                        log_probs, raw_waveform, slice_dur, time_per_frame, actual_slice_start = _get_slice(
                            widen_s, widen_e
                        )
                        token_ids, word_spans_meta = self._build_target_sequence_for_slice(
                            c_words_info,
                            log_probs,
                            language_code,
                        )

                silent = self.onset_verifier.silent_runs(raw_waveform) if len(raw_waveform) > 0 else []
                if clip_vad_spans:
                    try:
                        from . import vad

                        local_vad = [
                            (max(0.0, round(sp0 - actual_slice_start, 3)), min(slice_dur, round(sp1 - actual_slice_start, 3)))
                            for sp0, sp1 in clip_vad_spans
                            if sp1 > actual_slice_start and sp0 < actual_slice_start + slice_dur
                        ]
                        local_vad = [(a, b) for a, b in local_vad if b > a + 1e-4]
                        silent = vad.merge_runs(silent, vad.nonspeech_runs(local_vad, slice_dur))
                    except Exception:
                        pass
                elif len(raw_waveform) > 0:
                    try:
                        from . import vad

                        silent = vad.merge_runs(
                            silent,
                            vad.nonspeech_runs(vad.speech_spans(raw_waveform, clip_sr), slice_dur),
                        )
                    except Exception:
                        pass

                viterbi_words = self.viterbi.align(
                    log_probs,
                    token_ids,
                    word_spans_meta,
                    time_per_frame,
                    silent_spans=silent or None,
                )

                if verify_onsets and len(raw_waveform) > 0:
                    refined_active = self.onset_verifier.refine_word_onsets(viterbi_words, raw_waveform, slice_dur)
                    refined_active = self.onset_verifier.snap_starts_to_energy(refined_active, raw_waveform)
                    refined_active = self.onset_verifier.extend_ends_to_energy(refined_active, raw_waveform)
                else:
                    refined_active = viterbi_words

                if len(raw_waveform) > 0:
                    refined_active = self.onset_verifier.clamp_words_to_sound(
                        refined_active, raw_waveform, extra_runs=silent
                    )

                active_map = {
                    word_spans_meta[i]["orig_idx"]: (refined_active[i], word_spans_meta[i])
                    for i in range(len(refined_active))
                }

                last_w_start = c_start_bound
                last_w_end = c_start_bound + 0.1
                cue_aligned_words = []

                for idx, w_info in enumerate(c_words_info):
                    orig_w = w_info["orig"]
                    if idx in active_map:
                        w_res, w_meta = active_map[idx]
                        w_s = actual_slice_start + float(w_res["start"])
                        w_e = actual_slice_start + float(w_res["end"])

                        phonetic_len = int(w_meta.get("phonetic_len", w_meta.get("num_chars", len(orig_w))))
                        max_natural = min(3.5, max(1.20, phonetic_len * 0.14 + 0.65))
                        if w_e - w_s > max_natural:
                            capped_e = w_s + max_natural
                            if w_res.get("energy_verified_end") is not None:
                                capped_e = max(capped_e, actual_slice_start + float(w_res["energy_verified_end"]))
                            w_e = min(w_e, capped_e)

                        last_w_start = w_s
                        last_w_end = w_e
                        word_entry: Dict[str, Any] = {
                            "word": orig_w,
                            "start": round(w_s, 3),
                            "end": round(w_e, 3),
                            "confidence": round(w_res.get("confidence", 0.95), 3),
                        }
                        if w_res.get("no_sound"):
                            word_entry["no_sound"] = True
                        cue_aligned_words.append(word_entry)
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
                        if nxt_w["start"] > cur_w["start"] and cur_w["end"] > nxt_w["start"]:
                            cur_w["end"] = round(nxt_w["start"], 3)
                    cur_w["end"] = round(max(cur_w["start"] + 0.04, min(cur_w["end"], cur_w["start"] + 3.5)), 3)

                c_actual_start = cue_aligned_words[0]["start"] if cue_aligned_words else c_start_bound
                c_actual_end = cue_aligned_words[-1]["end"] if cue_aligned_words else c_end_bound

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
                dur = max(0.2, c_end_bound - c_start_bound)
                step = dur / max(1, len(c_words_info))
                fallback_words = []
                for idx, w_info in enumerate(c_words_info):
                    w_s = c_start_bound + idx * step
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
                cue_copy["start"] = round(c_start_bound, 3)
                cue_copy["end"] = round(c_end_bound, 3)
                cue_copy["startTime"] = round(c_start_bound, 3)
                cue_copy["endTime"] = round(c_end_bound, 3)
                cue_copy["words"] = fallback_words
                aligned_cues_out.append(cue_copy)
                all_aligned_words_out.extend(fallback_words)

        processing_time = round(time.time() - t0, 3)
        resolved_duration = float(total_dur or total_duration_sec or 0.0)
        if resolved_duration <= 0.0:
            resolved_duration = float(all_aligned_words_out[-1]["end"]) if all_aligned_words_out else 0.0
        out_payload: Dict[str, Any] = {
            "status": "success",
            "engine": "Zeno Acoustic Engine (Anchor-Based)",
            "language": language_code,
            "processing_time_sec": processing_time,
            "duration_sec": round(resolved_duration, 2),
            "cues": aligned_cues_out,
            "words": all_aligned_words_out,
        }
        if full_waveform is not None and full_log_probs is not None:
            out_payload["_raw_waveform"] = full_waveform
            out_payload["_log_probs"] = full_log_probs
            out_payload["_time_per_frame"] = full_tpf
        return out_payload


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
    if _shared_pipeline is not None:
        try:
            _shared_pipeline.unload_model()
        except Exception:
            pass
    _shared_pipeline = None


def unload_shared_pipeline() -> None:
    """Unload the shared acoustic model weights if already instantiated (no-op when never loaded)."""
    if _shared_pipeline is not None:
        _shared_pipeline.unload_model()
