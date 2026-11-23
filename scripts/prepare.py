"""Stages 1-3: Footage Scan, Voiceover Mux, Cached Transcription/Alignment, Face Tracking, Line Matching, Clip Sheet & Segregated Packet Generation.

Measures the raw footage and prepares structured evidence for the Director without making editorial choices.

Usage:
    python3 scripts/prepare.py [clips...] --script <script> [--lang auto] [--project <name>] [--name <VideoName>]

Writes to work/<project>/:
    line_map.md          full script with candidate clip windows under each spoken line
    facts.md             word timestamps, pauses, VAD speech spans, faces, and silent clips
    brief.md             job-scoped rules and format quick card
    clip_sheet_<n>.jpg   contact sheets of every source clip
    plan.edl             plan header skeleton; the Director writes the cut lines
    plan.auto.edl        backup copy of the initial header skeleton
    plan.json            machine-readable header metadata
    director_packet.md   segregated Director packet combining rules, format card, line map, and facts
    editing_packet.md    editing packet written alongside director_packet.md
"""
from __future__ import annotations

import os
import sys

sys.dont_write_bytecode = True
os.environ.setdefault("PYTHONDONTWRITEBYTECODE", "1")

import argparse
import array
import glob
import json
import math
import re
import shutil
import subprocess
import time
import wave
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common  # noqa: E402
from common import (  # noqa: E402
    DEFAULT_LOUDNESS_LUFS,
    DEFAULT_SFX,
    FFMPEG,
    OUTPUT,
    ROOT,
    WORK,
    clip_work_dir,
    ensure_cache_current,
    ffmpeg,
    find_venv_python,
    probe,
    read_json,
    read_script_text,
    resolve,
    resolve_sfx_asset,
    tc,
    write_json,
)

# Optional Phonetic / Alignment / Vision / EDL modules
IndicPhoneticMapper = None
try:
    from align_engine.phonetics.indic_phonemes import IndicPhoneticMapper
except Exception:
    try:
        import importlib.util

        _ip_path = Path(__file__).resolve().parent / "align_engine" / "phonetics" / "indic_phonemes.py"
        if _ip_path.is_file():
            _spec = importlib.util.spec_from_file_location("indic_phonemes", str(_ip_path))
            if _spec and _spec.loader:
                _mod = importlib.util.module_from_spec(_spec)
                _spec.loader.exec_module(_mod)
                IndicPhoneticMapper = getattr(_mod, "IndicPhoneticMapper", None)
    except Exception:
        IndicPhoneticMapper = None

try:
    import align_engine
except Exception:
    align_engine = None

try:
    import vision_tracker
except Exception:
    vision_tracker = None

try:
    import edl_parser
except Exception:
    edl_parser = None

try:
    import style_config  # noqa: E402

    _TUNING = style_config.get_tuning("micronarrative")
except Exception:
    style_config = None  # type: ignore[assignment]
    _TUNING = {
        "macro_silence_db": -35.0,
        "macro_silence_min_sec": 0.25,
        "micro_dip_db": -28.0,
        "micro_dip_min_sec": 0.05,
    }

MACRO_SILENCE_DB = float(_TUNING.get("macro_silence_db", -35.0))
MACRO_SILENCE_MIN_SEC = float(_TUNING.get("macro_silence_min_sec", 0.25))
MICRO_DIP_DB = float(_TUNING.get("micro_dip_db", -28.0))
MICRO_DIP_MIN_SEC = float(_TUNING.get("micro_dip_min_sec", 0.05))


def _natural_clip_sort_key(p: Path) -> Tuple[int, int, str]:
    stem = p.stem
    nums = [int(x) for x in re.findall(r"\d+", stem)]
    return (nums[0] if nums else 999999, nums[1] if len(nums) > 1 else 0, stem)


def _clean_token(w: str) -> str:
    norm = str(w or "").replace("\u2019", "'").replace("\u2018", "'").lower()
    return re.sub(r"[^\w\u0900-\u0D7F']", "", norm).strip("'_")


def _phonetic_token(w: str) -> str:
    u = _clean_token(w)
    if not u:
        return ""
    rom = u
    if IndicPhoneticMapper is not None:
        try:
            if IndicPhoneticMapper.is_pure_indic(u):
                rom = IndicPhoneticMapper.map_to_acoustic_tokens(u, lang="kn", is_native_indic=True).lower()
                rom = re.sub(r"[^a-z0-9]", "", rom) or u
            else:
                rom_en = IndicPhoneticMapper.number_to_words(u)
                rom = re.sub(r"[^a-z0-9']", "", rom_en) or u
        except Exception:
            pass
    # Normalize long vowels and common transliteration variants for cross-script matching
    rom = re.sub(r"([aeiou])\1+", r"\1", rom)
    rom = (
        rom.replace("ph", "f")
        .replace("bh", "b")
        .replace("dh", "d")
        .replace("th", "t")
        .replace("kh", "k")
        .replace("gh", "g")
    )
    return rom


def _indic_skeleton(u: str) -> str:
    """Strip Indic dependent vowel signs (matras), virama, and length marks to compare consonant skeletons."""
    return re.sub(
        r"[\u093A-\u094F\u0951-\u0957\u09BC-\u09CD\u09D7\u0ABC-\u0ACD\u0B3C-\u0B4D\u0B56\u0B57"
        r"\u0BBC-\u0BCD\u0BD7\u0C3E-\u0C4D\u0C55\u0C56\u0CBC-\u0CCD\u0CD5\u0CD6\u0D3B-\u0D4D\u0D57]",
        "",
        u,
    )


def _word_similarity(w1: str, w2: str) -> float:
    u1, u2 = _clean_token(w1), _clean_token(w2)
    if not u1 or not u2:
        return 0.0
    if u1 == u2:
        return 1.0
    p1, p2 = _phonetic_token(u1), _phonetic_token(u2)
    if p1 and p2 and p1 == p2:
        return 0.96
    sim_u = SequenceMatcher(None, u1, u2).ratio()
    sim_p = SequenceMatcher(None, p1, p2).ratio() if (p1 and p2) else 0.0
    # Compare Indic consonant skeletons
    sk1, sk2 = _indic_skeleton(u1), _indic_skeleton(u2)
    sim_sk = (
        SequenceMatcher(None, sk1, sk2).ratio() * 0.92
        if (len(sk1) >= 2 and len(sk2) >= 2 and sk1 != u1)
        else 0.0
    )
    # Also compare vowel-skeleton lowered forms (only when leading phonetic consonant aligns)
    same_onset = bool(p1 and p2) and (
        p1[0] == p2[0]
        or (p1[0], p2[0]) in {
            ("k", "g"), ("g", "k"), ("c", "j"), ("j", "c"),
            ("t", "d"), ("d", "t"), ("p", "b"), ("b", "p"),
            ("p", "f"), ("f", "p"), ("v", "b"), ("b", "v"),
            ("v", "w"), ("w", "v"), ("s", "z"), ("z", "s"),
        }
    )
    c1 = re.sub(r"[aeiou]", "a", p1) if len(p1) >= 4 else p1
    c2 = re.sub(r"[aeiou]", "a", p2) if len(p2) >= 4 else p2
    cons1, cons2 = re.sub(r"[aeiou]", "", p1), re.sub(r"[aeiou]", "", p2)
    cons_sim = SequenceMatcher(None, cons1, cons2).ratio() if (cons1 and cons2) else 0.0
    sim_c = (
        SequenceMatcher(None, c1, c2).ratio() * (0.92 if same_onset else 0.78)
        if (c1 and c2 and cons_sim >= 0.75)
        else 0.0
    )
    both_indic_diff_root = (sk1 != u1 and sk2 != u2 and not same_onset and sk1[:1] != sk2[:1])
    u_weight = 0.90 if both_indic_diff_root else 1.0
    p_weight = 0.82 if both_indic_diff_root else 0.95
    best = max(sim_u * u_weight, sim_p * p_weight, sim_sk * u_weight, sim_c)
    # Stem-prefix check (require >=5 shared romanized prefix chars covering >=72% of longer word; never suffix)
    if best < 0.70 and len(p1) >= 5 and len(p2) >= 5:
        cp = 0
        for ch1, ch2 in zip(p1, p2):
            if ch1 != ch2:
                break
            cp += 1
        if cp >= 5 and cp >= 0.72 * max(len(p1), len(p2)):
            best = max(best, 0.70)
    return best


def compute_10ms_rms_envelope(wav_path: Path) -> List[Tuple[float, float]]:
    """Compute 10ms RMS energy envelope (time_sec, dbFS) using standard-library `wave`."""
    if not wav_path.is_file():
        return []
    with wave.open(str(wav_path), "rb") as wf:
        sr = wf.getframerate()
        nframes = wf.getnframes()
        nch = wf.getnchannels()
        sampwidth = wf.getsampwidth()
        raw = wf.readframes(nframes)

    if sampwidth != 2 or not raw:
        return []

    samples = array.array("h", raw)
    if nch > 1:
        samples = samples[::nch]

    win = max(1, sr // 100)  # 10ms window (160 samples at 16kHz)
    scale = 32768.0 * 32768.0
    envelope: List[Tuple[float, float]] = []
    for i in range(0, len(samples), win):
        chunk = samples[i : i + win]
        if not chunk:
            break
        mean_sq = sum(s * s for s in chunk) / len(chunk)
        db = 10.0 * math.log10(max(mean_sq / scale, 1e-14))
        envelope.append((round(i / sr, 3), round(db, 2)))
    return envelope


def detect_two_tier_silences(
    wav_path: Path,
    rms_env: List[Tuple[float, float]],
    cached_silences_path: Optional[Path] = None,
) -> Tuple[List[Dict[str, float]], List[Dict[str, float]]]:
    """Detect two-tier silences:
    - Tier 1 Macro-silences via FFmpeg `silencedetect` or cached `silences.json`
    - Tier 2 Micro-dips and 10ms RMS minima from the 10ms wave envelope
    """
    macro_raw: List[Dict[str, float]] = []
    if cached_silences_path and cached_silences_path.is_file():
        try:
            macro_raw = read_json(cached_silences_path)
        except Exception:
            macro_raw = []

    if not macro_raw and wav_path.is_file():
        try:
            r = ffmpeg(
                "-i",
                wav_path,
                "-af",
                f"silencedetect=noise={int(MACRO_SILENCE_DB)}dB:d={MACRO_SILENCE_MIN_SEC}",
                "-f",
                "null",
                "-",
            )
            starts = [float(x) for x in re.findall(r"silence_start: (-?[\d.]+)", r.stderr)]
            ends = [float(x) for x in re.findall(r"silence_end: ([\d.]+)", r.stderr)]
            macro_raw = [{"start": round(max(s, 0.0), 3), "end": round(e, 3)} for s, e in zip(starts, ends)]
            if cached_silences_path:
                write_json(cached_silences_path, macro_raw)
        except Exception:
            macro_raw = []

    # Merge contiguous or near-contiguous macro silences (gap <= 0.04s)
    macro_merged: List[Dict[str, float]] = []
    for s in sorted(macro_raw, key=lambda x: float(x["start"])):
        st, en = float(s["start"]), float(s["end"])
        if macro_merged and st - macro_merged[-1]["end"] <= 0.04:
            macro_merged[-1]["end"] = max(macro_merged[-1]["end"], en)
        else:
            macro_merged.append({"start": round(st, 3), "end": round(en, 3)})

    # Tier 2 Micro-dips (-28dB for >= MICRO_DIP_MIN_SEC)
    micro_dips: List[Dict[str, float]] = []
    min_frames = max(1, int(round(MICRO_DIP_MIN_SEC / 0.01)))
    cur_run: List[Tuple[float, float]] = []
    for t, db in rms_env:
        if db <= MICRO_DIP_DB:
            cur_run.append((t, db))
        else:
            if len(cur_run) >= min_frames:
                min_t, min_db = min(cur_run, key=lambda x: x[1])
                micro_dips.append({
                    "start": round(cur_run[0][0], 3),
                    "end": round(cur_run[-1][0] + 0.01, 3),
                    "min_t": round(min_t, 3),
                    "min_db": round(min_db, 2),
                })
            cur_run = []
    if len(cur_run) >= min_frames:
        min_t, min_db = min(cur_run, key=lambda x: x[1])
        micro_dips.append({
            "start": round(cur_run[0][0], 3),
            "end": round(cur_run[-1][0] + 0.01, 3),
            "min_t": round(min_t, 3),
            "min_db": round(min_db, 2),
        })

    return macro_merged, micro_dips


def find_rms_minimum(
    rms_env: List[Tuple[float, float]],
    t_start: float,
    t_end: float,
    prefer_center: bool = True,
) -> Tuple[float, float]:
    """Find the exact 10ms timestamp with minimum RMS energy inside [t_start, t_end]."""
    cands = [(t, db) for t, db in rms_env if t_start <= t <= t_end]
    if not cands:
        return round(0.5 * (t_start + t_end), 2), -40.0
    if prefer_center:
        floor_cands = [(t, db) for t, db in cands if db <= -42.0]
        if floor_cands:
            best_t, best_db = floor_cands[0]
        else:
            mid = 0.5 * (t_start + t_end)
            best_t, best_db = min(cands, key=lambda x: (round(x[1] / 2.0) * 2.0, abs(x[0] - mid)))
    else:
        best_t, best_db = min(cands, key=lambda x: x[1])
    return round(best_t, 2), best_db


# --- Script cues that are directions or on-screen text, not spoken dialogue ---------------------------------------
_URL_ONLY_RE = re.compile(r"^https?://\S+$", re.I)
_META_LINE_RE = re.compile(r"^(?:visuals?(?:\s+changes?)?|reference(?:\s+link)?|ref\s+link|concept)\s*:", re.I)
_PRODUCTION_NOTE_RE = re.compile(
    r"^(?:first\s+frames?\b.*|dialogue\s+scripts?\s*:?|scrolling\s+text\s*(?:\([^)]*\))?\s*:?)\s*$",
    re.I,
)
_SETUP_HEAD_RE = re.compile(r"^(?:set\s*-?\s*up|production\s+notes?)\s*:?\s*$", re.I)
_NUMBERED_NOTE_RE = re.compile(r"^\d{1,2}[.)]\s+\S")
_BULLET_RE = re.compile(r"^[*•●▪◦]\s+")
_CHAR_DEF_RE = re.compile(r"^Character\s*\d*\s*[-–—]\s*(.+)$", re.I)
_SPEAKER_BIO_RE = re.compile(
    r"^([A-Za-z](?:[A-Za-z0-9_ .']|(?<=\w)-(?=\w)){0,28}?)\s+[-–—]\s+(.+)$"
)


def _split_speaker_bio(text: str) -> Tuple[str, str]:
    """Split 'Wife - Early 30s, wearing a sari...' without touching hyphenated names such as 'Co-worker 3'."""
    m = _SPEAKER_BIO_RE.match((text or "").strip())
    if not m:
        return "", ""
    cname, cdesc = m.group(1).strip(), m.group(2).strip()
    if len(cdesc) <= 12 or cname.lower().startswith(
        ("http", "concept", "visual", "scene", "character", "ref", "back to", "cut to", "flashback")
    ):
        return "", ""
    return cname, cdesc


_SCENE_DIR_RE = re.compile(r"^(?:scene\s+chang(?:e|es|ed)|flash\s*back|(?:back|cut)\s+to)\b[\s:,\-–—]*(.*)$", re.I)


def _unwrap_bracketed(text: str) -> str:
    """Strip a single matching outer `(...)` or `[...]` pair without touching inner parentheses."""
    s = (text or "").strip()
    if len(s) >= 2 and ((s[0] == "[" and s[-1] == "]") or (s[0] == "(" and s[-1] == ")")):
        return s[1:-1].strip()
    return s


_LANG_BY_WORD = {"telugu": "te", "kannada": "kn", "malayalam": "ml", "tamil": "ta", "hindi": "hi", "bengali": "bn", "bangla": "bn",
                 "marathi": "mr", "gujarati": "gu", "punjabi": "pa", "odia": "or", "oriya": "or"}
_SCRIPT_BLOCKS = {"te": (0x0C00, 0x0C7F), "kn": (0x0C80, 0x0CFF), "ml": (0x0D00, 0x0D7F), "ta": (0x0B80, 0x0BFF), "hi": (0x0900, 0x097F),
                  "bn": (0x0980, 0x09FF), "gu": (0x0A80, 0x0AFF), "pa": (0x0A00, 0x0A7F), "or": (0x0B00, 0x0B7F)}


def infer_language(video_name: str = "", script_text: str = "") -> str:
    """The spoken language of a video, so the transcription prompt names it instead of guessing ("auto").
    Two clues: the language word in the Notion video name ("W47 - Telugu - ...") and the script the lines are written in.
    They must agree (Hindi and Marathi share one script); when they disagree, or neither says, the answer stays "auto"."""
    words = {_LANG_BY_WORD[w] for w in re.findall(r"[a-z]+", str(video_name or "").lower()) if w in _LANG_BY_WORD}
    from_name = next(iter(words)) if len(words) == 1 else ""
    counts = {code: 0 for code in _SCRIPT_BLOCKS}
    for ch in str(script_text or ""):
        o = ord(ch)
        for code, (lo, hi) in _SCRIPT_BLOCKS.items():
            if lo <= o <= hi:
                counts[code] += 1
                break
    total = sum(counts.values())
    top = max(counts, key=lambda c: counts[c])
    from_text = top if total >= 40 and counts[top] >= 0.8 * total else ""
    if from_name and from_text:
        same = from_name == from_text or {from_name, from_text} <= {"hi", "mr"}
        return from_name if same else "auto"
    return from_name or from_text or "auto"


def _apply_explicit_title(meta: Dict[str, Any], explicit_name: str) -> None:
    """Inject explicit `--name` video title into `meta['title']` so format detection sees Notion titles."""
    clean = str(explicit_name or "").strip()
    if not clean or not isinstance(meta, dict):
        return
    cur = str(meta.get("title") or "").strip()
    if not cur or cur.lower().startswith("gdoc_"):
        meta["title"] = clean
    elif clean.lower() not in cur.lower():
        meta["title"] = f"{clean} — {cur}"


_LAYOUT_CUE_RE = re.compile(
    r"^(?:rectangle\s+overlay|floating\s+window|(?:(?:horizontal|vertical)\s+)?split[\s-]*screen|flash\s*cards?\s+split[\s-]*screen|picture[\s-]*in[\s-]*picture)\b",
    re.I,
)
_FLASHCARD_CUE_RE = re.compile(
    r"^(?:flash\s*card(?:\s+(?:text|prompt|overlay))?|card\s+text)\s*[:\-–—]\s*(.+)$",
    re.I,
)
_SHORT_CARD_CUE_RE = re.compile(r"^card\s*:\s*(.+)$", re.I)
_FLASHCARD_END_RE = re.compile(r"^(?:end\s+flash\s*cards?|flash\s*cards?\s+off|no\s+flash\s*cards?)\s*$", re.I)
_BACKGROUND_CUE_RE = re.compile(
    r"^(?:background(?:\s+(?:location|setting|visual|scene))?|location|setting)\s*:\s*(.+)$",
    re.I,
)
_TAKEOVER_DIR_RE = re.compile(
    r"^(?:(?P<sp>[A-Za-z][A-Za-z0-9 .'-]{0,24}?)\s+)?(?:takes?\s+over(?:\s+the)?\s+(?:full\s+)?screen|goes?\s+full[\s-]*screen)\b(.*)$",
    re.I,
)
_FULLSCREEN_HINT_RE = re.compile(r"\bfull[\s-]*screen\b|\btakes?\s+over\b|\bselfie\s+style\b", re.I)
_LATIN_CHAR_RE = re.compile(r"[A-Za-z]")
_NON_LATIN_CHAR_RE = re.compile(r"[\u0900-\u0DFF\u0600-\u06FF\u0400-\u04FF\u4E00-\u9FFF]")
_FLASHCARD_FALLBACK_SEPS = (
    re.compile(r"\s*[—–]\s*"),
    re.compile(r"\s+/\s+"),
    re.compile(r"\s+-\s+"),
    re.compile(r"\s*/\s*"),
    re.compile(r"\s*-\s*"),
)
_LABELLED_URL_RE = re.compile(r"^[^:]{1,40}:\s*https?://\S+\s*$", re.I)
_PANELS_RE = re.compile(r"(top|left)\s*:\s*(.+?)\s*\|\s*(bottom|right)\s*:\s*(.+)$", re.I)
_SIDE_NAME_RE = re.compile(r"([A-Za-z][A-Za-z0-9 .'-]{0,30}?)\s*[-–—:]\s*(left|right|top|bottom)\s+(?:screen|side|panel)\b", re.I)
_CHAR_SIDE_RE = re.compile(r"\b(left|right|top|bottom)\s+(?:screen|side|panel)\b", re.I)
_WRONG_MARKS = "❌✗✘❎"
_RIGHT_MARKS = "✅✓✔☑"
_CONTEXT_DIR_RE = re.compile(r"\b(?:is|are)\s+(?:talking|speaking|saying)\b|\btalking\s+to\b|\bwhile\b", re.I)
_STRIKE_CUE_RE = re.compile(r"\s*[\(\[][^\)\]]*strike[^\)\]]*[\)\]]\s*$", re.I)
_SCENE_STRIKE_RE = re.compile(
    r"\bstruck[\s-]+out\b|\bstrike[\s-]*through\b|\bred\s+x\b|\bx\s+sign\b|\bcross(?:ed)?\s+out\b",
    re.I,
)
_WRONG_FEEDBACK_OPEN_RE = re.compile(
    r"^(?:no[,!.:]+(?:\s|$)|no\s+(?:that'?s|you\b|don'?t|do\s+not|say|actually|never|not)\b|"
    r"nope\b|not\s+quite\b|almost\b|wrong\b|"
    r"never(?:,?\s*ever)?\s+(?:to\s+)?say\b|don'?t\s+(?:ever\s+)?say\b|do\s+not\s+say\b|stop\s+saying\b)",
    re.I,
)
_WRONG_FEEDBACK_NATIVE_PREFIXES = (
    "ನೀವು ಸಿಂಪಲ್ಲಾಗಿ",
    "ಹೀಗೆ ಹೇಳಿ",
    "మీరు ఇలా చెప్పాలి",
    "ఇలా చెప్పండి",
    "అలా చెప్పకూడదు",
    "అలా కాదు",
    "ऐसे कहो",
    "ऐसे बोलो",
    "ऐसे बोलिए",
    "गलत",
    "नहीं,",
    "नहीं!",
    "ഇങ്ങനെ പറയണം",
    "ഇങ്ങനെ പറയൂ",
    "അങ്ങനെ പറയരുത്",
    "இப்படிச் சொல்லுங்கள்",
    "இப்படி சொல்லணும்",
    "அப்படி சொல்லக்கூடாது",
)
_TEXT_CUE_RE = re.compile(r"\s*\[\s*TEXT\s*:\s*(?P<body>[^\]]*)\]\s*$", re.I)
# "horizontal split screen" has a horizontal divider (top / bottom panels = split_v); "vertical split" is left / right (split_h)
SCRIPT_SPLIT_LAYOUT = {"horizontal": "split_v", "vertical": "split_h", "default": "split_v"}


def _strip_outer_quotes(text: str) -> str:
    s = (text or "").strip()
    if len(s) >= 2 and (
        (s[0] == '"' and s[-1] == '"')
        or (s[0] == "'" and s[-1] == "'")
        or (s[0] == "“" and s[-1] == "”")
    ):
        return s[1:-1].strip()
    return s


def _script_counts(text: str) -> Tuple[int, int]:
    return len(_LATIN_CHAR_RE.findall(text)), len(_NON_LATIN_CHAR_RE.findall(text))


def _is_pure_bilingual_pair(left: str, right: str) -> bool:
    l_lat, l_non = _script_counts(left)
    r_lat, r_non = _script_counts(right)
    return (l_non == 0 and l_lat > 0 and r_lat == 0 and r_non > 0) or (
        r_non == 0 and r_lat > 0 and l_lat == 0 and l_non > 0
    )


def _is_clearly_bilingual_pair(left: str, right: str) -> bool:
    l_lat, l_non = _script_counts(left)
    r_lat, r_non = _script_counts(right)
    if (l_non > 0 and r_non == 0 and r_lat > 0) or (r_non > 0 and l_non == 0 and l_lat > 0):
        return True
    return (l_non > l_lat and r_lat > r_non) or (r_non > r_lat and l_lat > l_non)


def parse_flashcard_text(raw: str) -> Dict[str, str]:
    """Parse a bilingual flashcard string and normalize English (`en`, Line 1 TOP) vs native (`native`, Line 2 BOTTOM)."""
    cleaned = _strip_outer_quotes(re.sub(r"\s+", " ", str(raw or "")).strip())
    if not cleaned:
        return {"text": "", "en": "", "native": ""}

    left = ""
    right = ""
    if "|" in cleaned:
        p0, p1 = cleaned.split("|", 1)
        left, right = _strip_outer_quotes(p0), _strip_outer_quotes(p1)
    else:
        for sep_re in _FLASHCARD_FALLBACK_SEPS:
            matches = list(sep_re.finditer(cleaned))
            if not matches:
                continue
            chosen: Optional[Tuple[str, str]] = None
            for m in matches:
                cl = _strip_outer_quotes(cleaned[: m.start()])
                cr = _strip_outer_quotes(cleaned[m.end() :])
                if cl and cr and _is_pure_bilingual_pair(cl, cr):
                    chosen = (cl, cr)
                    break
            if chosen is None:
                for m in matches:
                    cl = _strip_outer_quotes(cleaned[: m.start()])
                    cr = _strip_outer_quotes(cleaned[m.end() :])
                    if cl and cr and _is_clearly_bilingual_pair(cl, cr):
                        chosen = (cl, cr)
                        break
            if chosen is not None:
                left, right = chosen
                break

    if left and right:
        l_lat, l_non = _script_counts(left)
        r_lat, r_non = _script_counts(right)
        if (
            (l_non > 0 and r_non == 0 and r_lat > 0)
            or (l_non > l_lat and r_lat >= r_non)
            or (l_non > r_non and r_lat > l_lat)
        ):
            en, native = right, left
        elif (
            (r_non > 0 and l_non == 0 and l_lat > 0)
            or (r_non > r_lat and l_lat >= l_non)
            or (r_non > l_non and l_lat > r_lat)
        ):
            en, native = left, right
        else:
            en, native = left, right
    else:
        single = left or right or cleaned
        s_lat, s_non = _script_counts(single)
        if s_non > s_lat:
            en, native = "", single
        else:
            en, native = single, ""

    text = f"{en} | {native}" if (en and native) else (en or native)
    return {"text": text, "en": en, "native": native}


def _match_character_name(raw_name: str, characters: Dict[str, Any]) -> str:
    clean = str(raw_name or "").strip()
    if not clean:
        return ""
    low = clean.lower()
    for cname in characters:
        if cname.lower() == low:
            return cname
    words = set(low.split())
    matches = [cname for cname in characters if words and words <= set(cname.lower().split())]
    return matches[0] if len(matches) == 1 else clean


def panels_from_text(text: str) -> Dict[str, str]:
    """Extract panel slot assignments from script notes like 'Top: Robot | Bottom: Lady 1' or 'Woman - Right screen'."""
    panels: Dict[str, str] = {}
    m = _PANELS_RE.search(text or "")
    if m:
        panels[m.group(1).lower()] = m.group(2).strip()
        panels[m.group(3).lower()] = m.group(4).strip()
    for m in _SIDE_NAME_RE.finditer(text or ""):
        panels.setdefault(m.group(2).lower(), m.group(1).strip())
    return panels


def split_layout_from_panels(panels: Dict[str, str]) -> str:
    """Panels named left/right map to `split_h`; named top/bottom map to `split_v`; `''` when unnamed."""
    if "left" in panels or "right" in panels:
        return "split_h"
    if "top" in panels or "bottom" in panels:
        return "split_v"
    return ""


def layout_hint_from_direction(text: str) -> str:
    """Resolve layout hint (`split_v`, `split_h`, `video_call`, `top_pip`, `chat_bar`) from a script direction, or `''`."""
    t = (text or "").lower()
    if re.search(r"\bsplit\b", t):
        if re.search(r"\bflash\s*cards?\b", t) and not re.search(r"\bhorizontal\b", t):
            return "split_h"
        key = "vertical" if re.search(r"\bvertical", t) else "horizontal" if re.search(r"\bhorizontal", t) else "default"
        return SCRIPT_SPLIT_LAYOUT[key]
    try:
        import format_library

        lib = format_library.best_layout(t)
    except Exception:
        lib = ""
    if lib and lib != "single":
        return lib
    if re.search(r"floating\s+window|picture[\s-]*in[\s-]*picture|\bpip\b|rectangle\s+overlay", t):
        return "video_call"
    return ""


def _drop_repeated_dialogue(lines: List[Dict[str, Any]], min_run: int = 3) -> List[Dict[str, Any]]:
    """Deduplicate scripts that paste the dialogue list twice (e.g. scrolling-text preview followed by speaker-labelled lines).
    Retains the run with more named speakers, preserving scene metadata on its lines."""
    def key(l: Dict[str, Any]) -> Tuple[str, ...]:
        return tuple(l.get("tokens") or ())

    changed = True
    while changed:
        changed = False
        n = len(lines)
        for j in range(n):
            if not key(lines[j]):
                continue
            for i in range(j):
                k = 0
                while i + k < j and j + k < n and key(lines[i + k]) and key(lines[i + k]) == key(lines[j + k]):
                    k += 1
                if k >= min_run:
                    first, second = lines[i : i + k], lines[j : j + k]
                    named = lambda run: sum(1 for l in run if l.get("speaker"))  # noqa: E731
                    drop = range(i, i + k) if named(second) > named(first) else range(j, j + k)
                    if named(second) > named(first):
                        for a, b in zip(first, second):
                            b["scene"], b["scene_visual"] = a.get("scene"), a.get("scene_visual")
                    drop_set = set(drop)
                    lines = [l for idx, l in enumerate(lines) if idx not in drop_set]
                    changed = True
                    break
            if changed:
                break
    if all(l["id"] == f"L{l['index']}" for l in lines):
        for idx, l in enumerate(lines, 1):
            l["id"], l["index"] = f"L{idx}", idx
    else:
        for idx, l in enumerate(lines, 1):
            l["index"] = idx
    return lines


def parse_script_file(script_path: Path) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    """Parse script file into metadata header (`concept_brief`, `characters`, `scenes`) and structured lines `L1..LN`."""
    return _parse_script_raw(read_script_text(script_path), script_path.stem)


def _parse_script_raw(raw: str, title: str) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    """Core text parser shared by `parse_script_file` and reference format tools."""
    meta: Dict[str, Any] = {
        "title": title,
        "language": "auto",
        "header": "",
        "concept_brief": "",
        "format_hint": "",
        "characters": {},
        "scenes": [],
        "flashcards": [],
    }
    header_lines: List[str] = []
    lines: List[Dict[str, Any]] = []

    in_characters_block = False
    current_scene = "Scene 1"
    scene_no = 1
    current_visual = ""
    current_hint = ""
    current_panels: Dict[str, str] = {}
    current_flashcard: Optional[Dict[str, str]] = None
    card_started = False
    scene_default_speaker = ""
    pending_speaker = ""
    pending_note = ""
    in_setup_block = False

    for raw_line in raw.splitlines():
        s = _BULLET_RE.sub("", raw_line.strip()).strip()
        if not s:
            if in_characters_block and meta["characters"]:
                in_characters_block = False
            continue
        if _SETUP_HEAD_RE.match(s):
            in_setup_block, in_characters_block = True, False
            continue
        if in_setup_block and _NUMBERED_NOTE_RE.match(s):
            meta.setdefault("setup", []).append(s)
            continue
        in_setup_block = False
        if _PRODUCTION_NOTE_RE.match(s):
            continue
        if s.startswith("#"):
            header_lines.append(s.lstrip("#").strip())
            m_title = re.search(r"-\s*([^(]+)\(([^)]+)\)", s)
            if m_title:
                title_part = re.sub(r"[^A-Za-z0-9]+", "_", m_title.group(1).strip()).strip("_")
                lang_part = re.sub(r"[^A-Za-z0-9]+", "_", m_title.group(2).strip()).strip("_")
                if lang_part and title_part:
                    meta["project_name"] = f"{lang_part}_{title_part}"
                    meta["language"] = lang_part.lower()
            m_cbrief = re.match(r"^#\s*concept_brief\s*:\s*(.+)$", s, re.I)
            if m_cbrief:
                meta["concept_brief"] = m_cbrief.group(1).strip()
            continue

        # 1. Keep non-URL concept/visual brief notes
        m_concept = re.match(r"^(?:Concept|Visual)\s+(?:Reference|Brief)(?:\s+link)?[^:]*:\s*(.+)$", s, re.I)
        if m_concept:
            val_c = m_concept.group(1).strip()
            if not re.search(r"https?://", val_c, re.I):
                meta["concept_brief"] = (meta["concept_brief"] + " | " + val_c).strip(" |")
            continue

        # 1b. Skip standalone URLs or non-spoken header labels
        if _URL_ONLY_RE.match(s) or _LABELLED_URL_RE.match(s):
            continue
        if _META_LINE_RE.match(s):
            val_m = s.split(":", 1)[1].strip()
            if re.match(r"visuals?\b", s, re.I) and val_m and not re.search(r"https?://", val_m, re.I):
                meta["concept_brief"] = (meta["concept_brief"] + " | " + val_m).strip(" |")
                if not current_visual and not meta["scenes"]:
                    current_visual = val_m
            continue

        s_unwrapped = _unwrap_bracketed(s)

        # 2. Characters heading & definitions
        if re.match(r"^Characters?\s*:?\s*$", s, re.I):
            in_characters_block = True
            continue
        if (
            in_characters_block
            and not s.startswith("--")
            and not _CHAR_DEF_RE.match(s)
            and not re.match(r"^Scene\s+\d+", s, re.I)
            and not _SCENE_DIR_RE.match(s_unwrapped)
            and not _FLASHCARD_CUE_RE.match(s_unwrapped)
            and not _BACKGROUND_CUE_RE.match(s_unwrapped)
            and not _TAKEOVER_DIR_RE.match(s_unwrapped)
        ):
            b_name, b_desc = _split_speaker_bio(s)
            if b_name:
                meta["characters"][b_name[:40]] = b_desc
            else:
                cname, _, cdesc = s.partition(":")
                meta["characters"][cname.strip()[:40]] = cdesc.strip()
            continue
        m_char = re.match(r"^--\s*([^-]+?)\s*--\s*(.+)$", s)
        if m_char:
            cname = m_char.group(1).strip()
            cdesc = m_char.group(2).strip()
            meta["characters"][cname] = cdesc
            continue
        m_char_def = _CHAR_DEF_RE.match(s)
        if m_char_def:
            cdesc = m_char_def.group(1).strip()
            m_cname = re.search(r"\(([^)]+)\)", cdesc)
            cname = (m_cname.group(1).strip() if m_cname else re.split(r"[,(]", cdesc, 1)[0].strip()) or "Character"
            meta["characters"][cname] = cdesc
            continue

        # 3. Scene heading
        m_scene = re.match(r"^(Scene\s+\d+)(?:\s*[-:–—]\s*(.*))?$", s, re.I)
        if m_scene:
            in_characters_block = False
            current_scene = m_scene.group(1).strip()
            m_num = re.search(r"\d+", current_scene)
            scene_no = int(m_num.group()) if m_num else scene_no
            current_visual = (m_scene.group(2) or "").strip()
            current_panels = panels_from_text(current_visual)
            current_hint = split_layout_from_panels(current_panels) or layout_hint_from_direction(current_visual)
            current_flashcard = None
            card_started = False
            scene_default_speaker = ""
            meta["scenes"].append({"scene": current_scene, "visual": current_visual, "layout_hint": current_hint})
            continue

        # 3b. Scene-change direction ("Scene changes: ...", "Back to ...", "Cut to ...", "Flashback ...")
        m_scene_dir = _SCENE_DIR_RE.match(s_unwrapped)
        if m_scene_dir and not (re.match(r"^(?:back|cut)\s+to\b", s_unwrapped, re.I) and ":" in s_unwrapped):
            in_characters_block = False
            if not meta["scenes"] and (lines or current_visual or current_hint or meta.get("concept_brief")):
                s1_visual = current_visual or meta.get("concept_brief", "")
                s1_panels = current_panels or panels_from_text(s1_visual)
                s1_hint = current_hint or split_layout_from_panels(s1_panels) or layout_hint_from_direction(s1_visual)
                meta["scenes"].append({"scene": current_scene, "visual": s1_visual, "layout_hint": s1_hint})
            scene_no += 1
            current_scene = f"Scene {scene_no}"
            current_visual = m_scene_dir.group(1).strip(" :-–—") or s_unwrapped
            current_panels = panels_from_text(current_visual)
            current_hint = split_layout_from_panels(current_panels) or layout_hint_from_direction(current_visual)
            current_flashcard = None
            card_started = False
            scene_default_speaker = ""
            pending_speaker = pending_note = ""
            meta["scenes"].append({"scene": current_scene, "visual": current_visual, "layout_hint": current_hint})
            continue

        # 3b2. Full-screen takeover direction ("Robot Takes over the screen", "Miss Nova goes full screen")
        m_takeover = _TAKEOVER_DIR_RE.match(s_unwrapped)
        if m_takeover and (m_takeover.group("sp") or "").strip().lower() not in (
            "i",
            "we",
            "you",
            "they",
            "he",
            "she",
            "it",
        ):
            in_characters_block = False
            if not meta["scenes"] and (lines or current_visual or current_hint or meta.get("concept_brief")):
                s1_visual = current_visual or meta.get("concept_brief", "")
                s1_panels = current_panels or panels_from_text(s1_visual)
                s1_hint = current_hint or split_layout_from_panels(s1_panels) or layout_hint_from_direction(s1_visual)
                meta["scenes"].append({"scene": current_scene, "visual": s1_visual, "layout_hint": s1_hint})
            scene_no += 1
            current_scene = f"Scene {scene_no}"
            current_flashcard = None
            card_started = False
            current_hint = "single"
            current_panels = {}
            current_visual = s_unwrapped
            pending_speaker = pending_note = ""
            raw_sp = (m_takeover.group("sp") or "").strip()
            scene_default_speaker = _match_character_name(raw_sp, meta["characters"]) if raw_sp else ""
            meta["scenes"].append({"scene": current_scene, "visual": current_visual, "layout_hint": current_hint})
            continue

        # 3c. Layout cue note
        if _LAYOUT_CUE_RE.match(s) and not re.match(r"^[A-Za-z][A-Za-z0-9_ ]{0,22}:\s", s):
            hint_l = layout_hint_from_direction(s)
            if hint_l:
                if not meta["format_hint"] or re.search(r"\bflash\s*cards?\b", s, re.I):
                    meta["format_hint"] = s
                current_panels = panels_from_text(s)
                current_hint = split_layout_from_panels(current_panels) or hint_l
                if meta["scenes"]:
                    meta["scenes"][-1]["layout_hint"] = current_hint
                continue

        # 3d. Flashcard cues ("Flashcard Text: ...", "Flashcard: ...", "Flash card: ...")
        if _FLASHCARD_END_RE.match(s_unwrapped):
            in_characters_block = False
            current_flashcard = None
            card_started = False
            continue
        m_fc = _FLASHCARD_CUE_RE.match(s_unwrapped)
        if not m_fc:
            m_sc = _SHORT_CARD_CUE_RE.match(s_unwrapped)
            if m_sc and "card" not in {k.lower() for k in meta["characters"]}:
                sc_val = m_sc.group(1).strip()
                if "|" in sc_val or "—" in sc_val or "–" in sc_val or _NON_LATIN_CHAR_RE.search(sc_val):
                    m_fc = m_sc
        if m_fc:
            in_characters_block = False
            fc_parsed = parse_flashcard_text(m_fc.group(1))
            if fc_parsed["text"]:
                current_flashcard = fc_parsed
                card_started = False
                meta["flashcards"].append(dict(fc_parsed))
            continue

        # 3e. Background / location scene-context notes ("Background: ...", "Background Location: ...")
        m_bg = _BACKGROUND_CUE_RE.match(s_unwrapped)
        if m_bg:
            bg_label = s_unwrapped.split(":", 1)[0].strip().lower()
            if bg_label.startswith("background") or bg_label not in {k.lower() for k in meta["characters"]}:
                in_characters_block = False
                current_visual = m_bg.group(1).strip()
                continue

        # 4. Visual / camera direction block: "[Visual: ...]" or "(Visual: ...)"
        m_vis_dir = re.match(r"^Visuals?\s*:\s*(.+)$", s_unwrapped, re.I)
        if m_vis_dir:
            in_characters_block = False
            current_visual = m_vis_dir.group(1).strip()
            vis_panels = panels_from_text(current_visual)
            vis_hint = split_layout_from_panels(vis_panels) or layout_hint_from_direction(current_visual)
            if vis_hint:
                current_hint, current_panels = vis_hint, vis_panels
            elif _FULLSCREEN_HINT_RE.search(current_visual):
                current_hint, current_panels = "single", {}
                current_flashcard = None
                card_started = False
            if meta["scenes"]:
                meta["scenes"][-1]["visual"] = current_visual
                if current_hint:
                    meta["scenes"][-1]["layout_hint"] = current_hint
            else:
                meta["scenes"].append({"scene": current_scene, "visual": current_visual, "layout_hint": current_hint})
            continue

        # 5. Standalone speaker header line
        m_standalone_spk = re.match(r"^([A-Za-z](?:[A-Za-z0-9_ .']|(?<=\w)-(?=\w)){0,24}?)\s*(?:\(([^)]*)\))?\s*:\s*$", s)
        if m_standalone_spk:
            in_characters_block = False
            pending_speaker = m_standalone_spk.group(1).strip()
            pending_note = (m_standalone_spk.group(2) or "").strip()
            continue
        m_standalone_bio = re.match(r"^(.+?)\s*(?:\(([^)]*)\))?\s*:\s*$", s)
        if m_standalone_bio:
            sb_name, sb_desc = _split_speaker_bio(m_standalone_bio.group(1))
            if sb_name:
                in_characters_block = False
                pending_speaker = sb_name
                pending_note = (m_standalone_bio.group(2) or "").strip()
                meta["characters"].setdefault(sb_name, sb_desc)
                continue

        in_characters_block = False

        # 6. Spoken line or silent beat: L1 Speaker (optional note): text [WRONG|RIGHT]
        m_id = re.match(r"^(L\d+)\s*(?::\s*|\s+)(.*)$", s)
        if m_id:
            lid, rest = m_id.group(1), m_id.group(2).strip()
        else:
            lid = f"L{len(lines) + 1}"
            rest = s

        strike_cue = False
        strike_override = ""
        m_text_cue = _TEXT_CUE_RE.search(rest)
        if m_text_cue:
            parts_t = [p.strip() for p in m_text_cue.group("body").split("|")]
            strike_cue = any(re.fullmatch(r"strike(?:through|[\s-]*out)?", p, re.I) for p in parts_t[1:])
            quoted_t = re.match(r'^["“]([^"”]+)["”]$', parts_t[0]) if parts_t else None
            strike_override = quoted_t.group(1).strip() if quoted_t else ""
            rest = rest[: m_text_cue.start()].strip()
        else:
            m_strike = _STRIKE_CUE_RE.search(rest)
            if m_strike:
                strike_cue = True
                rest = rest[: m_strike.start()].strip()

        mark = None
        emoji_marks = [c for c in rest if c in _WRONG_MARKS + _RIGHT_MARKS]
        if emoji_marks:
            mark = "wrong" if emoji_marks[-1] in _WRONG_MARKS else "right"
            rest = re.sub("[" + _WRONG_MARKS + _RIGHT_MARKS + "]\ufe0f?", "", rest).strip()
        m_mark = re.search(r"\[(WRONG|RIGHT)\]\s*$", rest, re.I)
        if m_mark:
            mark = m_mark.group(1).lower()
            rest = rest[: m_mark.start()].strip()
        elif mark is None:
            m_inline = re.search(r"\[(WRONG|RIGHT)\]", rest, re.I)
            if m_inline:
                mark = m_inline.group(1).lower()

        speaker = pending_speaker
        note = pending_note
        pending_speaker = ""
        pending_note = ""
        beat = False

        m_beat = re.match(r"^(?:(?:beat|action)\s*:\s*(.+)|\((.+)\)|\[(.+)\]|\*(.+)\*)$", rest, re.I)
        if m_beat and not speaker:
            ctx_text = next(g for g in m_beat.groups() if g).strip()
            if _CONTEXT_DIR_RE.search(ctx_text):
                current_visual = ctx_text
                if meta["scenes"]:
                    meta["scenes"][-1]["visual"] = ctx_text
                continue
        if m_beat and not speaker:
            beat = True
            note = next(g for g in m_beat.groups() if g).strip()
            text = note
        else:
            m_spk = re.match(r"^([A-Za-z](?:[A-Za-z0-9_ .']|(?<=\w)-(?=\w)){0,22}?)\s*(?:\(([^)]*)\))?\s*:\s*(.+)$", rest)
            m_bio_spk = None if m_spk else re.match(r"^([^:\n]{16,140}?)\s*(?:\(([^)]*)\))?\s*:\s*(.+)$", rest)
            b_spk_name, b_spk_desc = _split_speaker_bio(m_bio_spk.group(1)) if m_bio_spk else ("", "")
            if m_spk and not m_spk.group(1).lower().startswith(("http", "concept", "visual", "scene")):
                speaker = m_spk.group(1).strip()
                note = (m_spk.group(2) or note or "").strip()
                text = m_spk.group(3).strip().lstrip(":").strip()
            elif b_spk_name and m_bio_spk:
                speaker = b_spk_name
                meta["characters"].setdefault(b_spk_name, b_spk_desc)
                note = (m_bio_spk.group(2) or note or "").strip()
                text = m_bio_spk.group(3).strip().lstrip(":").strip()
            else:
                text = rest
                if not speaker and scene_default_speaker:
                    speaker = scene_default_speaker
            m_inline_dir = re.match(r"^\(([^)]+)\)\s*(.+)$", text)
            if m_inline_dir:
                dir_note = m_inline_dir.group(1).strip()
                note = f"{note}; {dir_note}".strip("; ") if note else dir_note
                text = m_inline_dir.group(2).strip()

        quoted = re.findall(r'["“”]([^"“”]+)["“”]', text)
        tokens = [_clean_token(w) for w in re.findall(r"[\w\u0900-\u0D7F']+", text.lower()) if _clean_token(w)]
        if beat:
            tokens = []
        phonetic_tokens = [_phonetic_token(w) for w in tokens if _phonetic_token(w)]

        if (
            mark is None
            and not beat
            and speaker
            and tokens
            and len(tokens) <= 16
            and "?" not in text
            and _SCENE_STRIKE_RE.search(current_visual or "")
        ):
            mark = "wrong"

        line_fc = dict(current_flashcard) if (current_flashcard and not beat) else None
        line_fc_start = (not card_started) if line_fc else False
        if line_fc:
            card_started = True

        lines.append({
            "id": lid,
            "index": len(lines) + 1,
            "speaker": speaker,
            "scene": current_scene,
            "scene_visual": current_visual,
            "text": text,
            "mark": mark,
            "quoted": quoted,
            "tokens": tokens,
            "phonetic_tokens": phonetic_tokens,
            "note": note,
            "beat": beat,
            "layout_hint": current_hint,
            "panels": dict(current_panels),
            "strike_text": (strike_override or text) if (strike_cue and not beat) else "",
            "flashcard": line_fc,
            "flashcard_start": line_fc_start,
        })

    lines = _drop_repeated_dialogue(lines)

    char_side: Dict[str, str] = {}
    for cname, cdesc in meta["characters"].items():
        m_side = _CHAR_SIDE_RE.search(cdesc or "")
        if m_side:
            char_side[cname] = m_side.group(1).lower()
    for l in lines:
        if l.get("layout_hint") and char_side:
            for cname, side in char_side.items():
                if (
                    side not in l["panels"]
                    and cname not in l["panels"].values()
                    and split_layout_from_panels({side: cname}) == l["layout_hint"]
                ):
                    l["panels"][side] = cname
    panel_names = {v for l in lines for v in (l.get("panels") or {}).values()}
    names = sorted(
        {l["speaker"] for l in lines if l.get("speaker")} | set(meta["characters"]) | panel_names,
        key=lambda n: -len(n.split()),
    )
    canon: Dict[str, str] = {}
    for n in names:
        words = set(n.lower().split())
        longer = next(
            (m for m in names if m != n and len(m.split()) > len(n.split()) and words < set(m.lower().split())),
            None,
        )
        if longer:
            canon[n] = longer
    for l in lines:
        l["speaker"] = canon.get(l.get("speaker", ""), l.get("speaker", ""))
        l["panels"] = {k: canon.get(v, v) for k, v in l.get("panels", {}).items()}

    # Auto-infer [WRONG] / [RIGHT] on student lines when followed by teacher feedback
    for idx in range(len(lines) - 1):
        curr_l = lines[idx]
        next_l = lines[idx + 1]
        if curr_l.get("mark") or not curr_l.get("tokens") or len(curr_l.get("tokens", [])) > 16:
            continue
        if curr_l.get("flashcard") or next_l.get("flashcard"):
            continue
        if curr_l.get("speaker") and next_l.get("speaker") and curr_l["speaker"] != next_l["speaker"]:
            curr_txt = (curr_l.get("text") or "").strip()
            nxt_txt = next_l["text"].strip().lower()
            nxt_clean = re.sub(r'^[\s"\'“”‘’]+', "", nxt_txt)
            nxt_bare = re.sub(r"[^\w\u0900-\u0D7F\s]", "", nxt_clean).strip()
            curr_quoted_bare = {
                re.sub(r"[^\w\u0900-\u0D7F\s]", "", q.lower()).strip()
                for q in (curr_l.get("quoted") or [])
                if q and q.strip()
            }
            curr_quoted_bare.discard("")
            if curr_quoted_bare and any(
                nxt_bare == q or nxt_bare.startswith(q + " ") for q in curr_quoted_bare
            ):
                continue
            nxt_tok_count = len(next_l.get("tokens") or [])
            is_teach_prompt = "?" in curr_txt or bool(
                re.search(
                    r"english|translate|how\s+do\s+you\s+say|repeat\s+after|say\s+it\s+with\s+me|ಏನಂತಾರೆ|ಹಿಂದೇ?ನೆ\s*ಹೇಳಿ|ఎలా\s*చెప్పాలి|कैसे\s*बोलें|എങ്ങനെ\s*പറയും|எப்படி",
                    curr_txt,
                    re.I,
                )
            )
            if _WRONG_FEEDBACK_OPEN_RE.match(nxt_clean):
                if (nxt_clean.startswith("almost") and nxt_tok_count < 3) or is_teach_prompt:
                    pass
                else:
                    curr_l["mark"] = "wrong"
            elif nxt_txt.startswith(_WRONG_FEEDBACK_NATIVE_PREFIXES):
                if not is_teach_prompt:
                    curr_l["mark"] = "wrong"
            elif (
                not is_teach_prompt
                and next_l.get("quoted")
                and re.match(r"^(?:say|try\s+saying|instead[,:\s]|better\s+way|you\s+(?:can|should)\s+say)\b", nxt_clean, re.I)
            ):
                curr_l["mark"] = "wrong"
            elif nxt_txt.startswith(("perfect", "nice", "great", "correct", "ನಿಜ", "ಸರಿ")):
                if not is_teach_prompt:
                    curr_l["mark"] = "right"

    brief_parts: List[str] = []
    if meta["characters"]:
        brief_parts.append("Characters: " + "; ".join(f"{k} ({v})" for k, v in meta["characters"].items()))
    if meta["scenes"]:
        brief_parts.append(
            " | ".join(f"{sc['scene']}: {sc['visual']}" if sc.get("visual") else sc["scene"] for sc in meta["scenes"])
        )
    if brief_parts and not meta["concept_brief"]:
        meta["concept_brief"] = " — ".join(brief_parts)
    if meta.get("setup"):
        meta["concept_brief"] = (meta["concept_brief"] + " | Set up: " + " ".join(meta["setup"])).strip(" |")
    meta["header"] = " | ".join(header_lines)
    return meta, lines


def _is_quota_error_text(text: str) -> bool:
    """Return True when an error string indicates HTTP 429 or Gemini quota exhaustion."""
    low = str(text or "").lower()
    return (
        "quota_exceeded" in low
        or "resource_exhausted" in low
        or "429" in low
        or "quota" in low
        or "too many requests" in low
    )


_VAD_MOD: Any = None
_VAD_MOD_LOADED = False


def _load_vad_module() -> Any:
    """Load `align_engine.vad` safely even when optional heavy alignment packages are absent."""
    global _VAD_MOD, _VAD_MOD_LOADED
    if _VAD_MOD_LOADED:
        return _VAD_MOD
    _VAD_MOD_LOADED = True
    try:
        from align_engine import vad as _vad_mod

        _VAD_MOD = _vad_mod
        return _VAD_MOD
    except Exception:
        pass
    try:
        import importlib.util

        vad_path = ROOT / "scripts" / "align_engine" / "vad.py"
        if vad_path.is_file():
            spec = importlib.util.spec_from_file_location("align_engine_vad_standalone", str(vad_path))
            if spec and spec.loader:
                mod = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(mod)
                _VAD_MOD = mod
    except Exception:
        _VAD_MOD = None
    return _VAD_MOD


def _clear_incomplete_clip_caches(work_dir: Path) -> None:
    """Clear clip-level stale transcript outputs while preserving completed chunk caches."""
    for fname in (
        "gemini_transcript.json",
        "words.json",
        "hq_words.json",
        "align.json",
        "alignment.json",
        "transcript.txt",
        "facts_pending",
    ):
        fp = work_dir / fname
        if fp.is_file():
            try:
                fp.unlink()
            except OSError:
                pass
    chunks_dir = work_dir / "chunks"
    if chunks_dir.is_dir():
        for sub in chunks_dir.iterdir():
            if not sub.is_dir():
                continue
            gf = sub / "gemini_transcript.json"
            drop_sub = not gf.is_file()
            if not drop_sub:
                try:
                    data = json.loads(gf.read_text(encoding="utf-8"))
                    drop_sub = (
                        not isinstance(data, dict)
                        or not (data.get("words") or [])
                        or bool(data.get("incomplete"))
                        or str(data.get("warning") or "").strip().lower().startswith("incomplete")
                    )
                except Exception:
                    drop_sub = True
            if drop_sub:
                shutil.rmtree(sub, ignore_errors=True)


def _compute_clip_transcript_meta(
    work_dir: Path,
    duration_sec: float,
    spoken_spans: List[Tuple[float, float]],
    words_list: List[Dict[str, Any]],
    existing_meta: Optional[Dict[str, Any]] = None,
    override_status: str = "",
    warning_msg: str = "",
) -> Dict[str, Any]:
    """Compute and normalize `transcript_meta.json` fields for a clip cache directory."""
    vad_mod = _load_vad_module()
    active_speech_sec = round(sum(max(0.0, float(b) - float(a)) for a, b in spoken_spans), 3)
    words_count = len(words_list)
    if vad_mod is not None and spoken_spans:
        uncov_12 = vad_mod.uncovered_speech_spans(spoken_spans, words_list, min_gap_sec=1.2)
        uncov_all_sec = sum(
            e - s for s, e in vad_mod.uncovered_speech_spans(spoken_spans, words_list, min_gap_sec=0.05)
        )
        covered_speech_sec = round(max(0.0, active_speech_sec - uncov_all_sec), 3)
        speech_coverage = round(covered_speech_sec / active_speech_sec, 3) if active_speech_sec > 0 else (1.0 if words_count else 0.0)
    else:
        uncov_12 = [[round(float(a), 3), round(float(b), 3)] for a, b in spoken_spans] if (spoken_spans and not words_list) else []
        covered_speech_sec = 0.0
        speech_coverage = 1.0 if words_count else 0.0

    ex = existing_meta if isinstance(existing_meta, dict) else {}
    chunks_total = int(ex.get("chunks_total") or (1 if words_count else 0))
    chunks_done = int(ex.get("chunks_done") or (1 if words_count else 0))
    uncov_total = sum(float(u[1]) - float(u[0]) for u in uncov_12)
    has_big_uncov = any((float(u[1]) - float(u[0])) >= 1.5 for u in uncov_12)

    if override_status in ("quota_exceeded", "cloud_failed", "no_speech", "incomplete"):
        status = override_status
    elif words_count == 0 and active_speech_sec >= 0.8:
        status = "incomplete"
    elif words_count == 0:
        status = "no_speech"
    elif (active_speech_sec >= 4.0 and speech_coverage < 0.45) or uncov_total >= 3.0 or (has_big_uncov and speech_coverage < 0.70):
        status = "incomplete"
    else:
        status = "ok"

    warn_out = str(warning_msg or ex.get("warning") or "").strip()
    if status == "incomplete" and not warn_out:
        warn_out = (
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
        "uncovered_spans": [[round(float(u[0]), 3), round(float(u[1]), 3)] for u in uncov_12],
        "chunks_total": chunks_total,
        "chunks_done": chunks_done,
        "warning": warn_out,
    }


def ensure_clip_transcribed(
    clip_stem: str,
    clip_src: str,
    work_dir: Path,
    lang: str,
    script_path: Optional[Path],
    clean_raw_json: bool = True,
    skip_remote_transcription: bool = False,
    skip_reason: str = "",
) -> Tuple[Dict[str, Any], List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Ensure `info.json`, `audio16k.wav`, and `hq_words.json` / `words.json` exist for a clip."""
    work_dir.mkdir(parents=True, exist_ok=True)
    stale = ensure_cache_current(work_dir)
    if stale:
        print(f"  [cache] {clip_stem}: rebuilding {', '.join(stale)} (code changed or cache invalid)")
    info_path = work_dir / "info.json"
    wav_path = work_dir / "audio16k.wav"
    hq_path = work_dir / "hq_words.json"
    words_path = work_dir / "words.json"
    align_path = work_dir / "align.json"
    meta_path = work_dir / "transcript_meta.json"

    resolved_src = resolve(clip_src)
    if info_path.is_file():
        info = read_json(info_path)
    elif resolved_src.is_file():
        info = probe(resolved_src)
        write_json(info_path, info)
    else:
        info = {
            "path": str(clip_src),
            "duration": 10.0,
            "width": 720,
            "height": 1280,
            "fps": 24.0,
            "has_audio": True,
        }
    if isinstance(info, dict):
        info.pop("_transcript_error", None)
        info.pop("_transcript_status", None)
        info.pop("_transcript_warning", None)

    hq_words: List[Dict[str, Any]] = []
    base_words: List[Dict[str, Any]] = []
    if align_path.is_file():
        try:
            ad = read_json(align_path)
            hq_words = ad.get("words", ad) if isinstance(ad, dict) else ad
        except Exception:
            pass
    if not hq_words and hq_path.is_file():
        try:
            hq_words = read_json(hq_path)
        except Exception:
            pass
    if words_path.is_file():
        try:
            base_words = read_json(words_path)
        except Exception:
            pass

    if skip_remote_transcription and not hq_words and not base_words:
        if not info.get("has_audio", True):
            return info, [], []
        if wav_path.is_file() and clip_has_no_sound(wav_path):
            print(f"  [{clip_stem}] no sound in this clip: skipping transcription")
            return info, [], []
        info["_transcript_status"] = "quota_skipped"
        info["_transcript_error"] = skip_reason or "skipped by quota circuit-breaker (Gemini quota exceeded)"
        print(f"  [{clip_stem}] skipped remote transcription (quota circuit-breaker active)")
        return info, [], []

    if not wav_path.is_file() and resolved_src.is_file() and info.get("has_audio", True):
        ffmpeg("-i", resolved_src, "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", wav_path)

    # Measure VAD speech spans up front so incomplete or empty caches on real speech are never trusted
    spoken_spans = _speech_spans_of({"work_dir": work_dir}) if wav_path.is_file() else []
    active_speech_sec = sum(max(0.0, float(b) - float(a)) for a, b in spoken_spans)
    vad_mod = _load_vad_module()
    existing_meta: Optional[Dict[str, Any]] = None
    if meta_path.is_file():
        try:
            mdata = read_json(meta_path)
            if isinstance(mdata, dict):
                existing_meta = mdata
        except Exception:
            existing_meta = None

    retry_incomplete = os.environ.get("ZENO_RETRY_INCOMPLETE", "").strip() == "1"
    retry_clips_raw = os.environ.get("ZENO_RETRY_CLIPS", "").strip()
    retry_clips = {t.strip().lower() for t in retry_clips_raw.split(",") if t.strip()} if retry_clips_raw else set()
    clip_names_low = {
        clip_stem.strip().lower(),
        Path(str(clip_src)).name.strip().lower(),
        Path(str(clip_src)).stem.strip().lower(),
    }
    clip_matches_retry = bool(retry_clips & clip_names_low) if retry_clips else False
    skip_due_to_retry_filter = bool(retry_clips and not clip_matches_retry)

    force_retry_clip = False
    if clip_matches_retry:
        force_retry_clip = True
    elif retry_incomplete and not skip_due_to_retry_filter:
        cached_words = hq_words or base_words
        meta_status = str((existing_meta or {}).get("status") or "").strip().lower()
        meta_uncov = (existing_meta or {}).get("uncovered_spans") or []
        has_meta_uncov = any(
            (float(u[1]) - float(u[0])) >= 1.5
            for u in meta_uncov
            if isinstance(u, (list, tuple)) and len(u) >= 2
        )
        live_uncov = (
            vad_mod.uncovered_speech_spans(spoken_spans, cached_words, min_gap_sec=1.5)
            if (vad_mod is not None and spoken_spans and cached_words)
            else []
        )
        if vad_mod is not None and spoken_spans and active_speech_sec > 0.0:
            uncov_sec = (
                sum(e - s for s, e in vad_mod.uncovered_speech_spans(spoken_spans, cached_words, min_gap_sec=0.05))
                if cached_words
                else active_speech_sec
            )
            live_cov = max(0.0, active_speech_sec - uncov_sec) / active_speech_sec
        else:
            live_cov = 1.0 if cached_words else 0.0
        ev_marker: Dict[str, Any] = {}
        marker_path = work_dir / "engine_version.json"
        if marker_path.is_file():
            try:
                ev_raw = read_json(marker_path)
                if isinstance(ev_raw, dict):
                    ev_marker = ev_raw
            except Exception:
                pass
        force_retry_clip = (
            meta_status in ("incomplete", "quota_exceeded", "cloud_failed")
            or has_meta_uncov
            or bool(live_uncov)
            or (active_speech_sec >= 0.8 and not cached_words)
            or (active_speech_sec >= 4.0 and live_cov < 0.45)
            or ev_marker.get("transcript") == "incomplete"
        )

    if force_retry_clip:
        print(f"  [{clip_stem}] retrying incomplete/targeted clip (preserving completed chunks)")
        _clear_incomplete_clip_caches(work_dir)
        hq_words = []
        base_words = []
        existing_meta = None

    if skip_remote_transcription and not hq_words and not base_words:
        if not info.get("has_audio", True):
            return info, [], []
        if wav_path.is_file() and clip_has_no_sound(wav_path):
            print(f"  [{clip_stem}] no sound in this clip: skipping transcription")
            return info, [], []
        info["_transcript_status"] = "quota_skipped"
        info["_transcript_error"] = skip_reason or "skipped by quota circuit-breaker (Gemini quota exceeded)"
        print(f"  [{clip_stem}] skipped remote transcription (quota circuit-breaker active)")
        return info, [], []

    gem_cache_path = work_dir / "gemini_transcript.json"
    if not hq_words and not base_words and active_speech_sec >= 0.8 and not skip_due_to_retry_filter:
        for stale_empty in (align_path, hq_path, words_path, gem_cache_path):
            if stale_empty.is_file():
                try:
                    stale_empty.unlink()
                except OSError:
                    pass

    # Skip transcription on clips with verified zero speech + low voiced energy or no sound
    rms_env_check = (
        compute_10ms_rms_envelope(wav_path)
        if (not hq_words and not base_words and wav_path.is_file() and active_speech_sec < 0.8)
        else []
    )
    vad_avail = bool(vad_mod is not None and vad_mod.available())
    low_voiced_zero_speech = (
        vad_avail
        and active_speech_sec == 0.0
        and not voiced_spans(rms_env_check, floor_db=-34.0)
    )
    no_sound = (
        not hq_words
        and not base_words
        and wav_path.is_file()
        and active_speech_sec < 0.8
        and (low_voiced_zero_speech or clip_has_no_sound(wav_path, rms_env_check))
    )
    if no_sound:
        print(f"  [{clip_stem}] no sound in this clip: skipping transcription")

    transcript_err = ""
    transcript_status = ""
    transcript_warn = ""
    confirmed_no_speech = no_sound
    res_meta: Optional[Dict[str, Any]] = None
    res_incomplete = False
    align_engine_attempted = False

    if (
        not hq_words
        and not base_words
        and wav_path.is_file()
        and not no_sound
        and not skip_due_to_retry_filter
    ):
        max_tries = 2 if active_speech_sec >= 0.8 else 1
        for attempt in range(max_tries):
            if align_engine is not None and hasattr(align_engine, "transcribe_and_align_clip"):
                align_engine_attempted = True
                try:
                    if attempt > 0 and gem_cache_path.is_file():
                        try:
                            gem_cache_path.unlink()
                        except OSError:
                            pass
                    effective_script_path = (
                        None
                        if (vad_avail and float(info.get("duration") or 0.0) >= 2.0 and active_speech_sec < 0.6)
                        else script_path
                    )
                    res = align_engine.transcribe_and_align_clip(
                        wav_path,
                        work_dir=work_dir,
                        lang=lang,
                        script_path=effective_script_path,
                    )
                    if isinstance(res, list):
                        hq_words = res
                    elif isinstance(res, dict):
                        res_status = str(res.get("status") or "").strip().lower()
                        transcript_warn = str(res.get("warning") or "").strip()
                        res_incomplete = (
                            bool(res.get("incomplete"))
                            or transcript_warn.lower().startswith("incomplete")
                            or res_status == "incomplete"
                        )
                        if isinstance(res.get("transcript_meta"), dict):
                            res_meta = dict(res["transcript_meta"])
                        if "words" in res:
                            hq_words = res["words"] or []
                        if res_status in ("cloud_failed", "quota_exceeded"):
                            transcript_err = str(res.get("error") or res_status)
                            transcript_status = (
                                "quota_exceeded"
                                if (res_status == "quota_exceeded" or _is_quota_error_text(transcript_err))
                                else "cloud_failed"
                            )
                            break
                        if res_status in ("success", "no_speech") and not hq_words and active_speech_sec < 0.8 and not res_incomplete:
                            confirmed_no_speech = True
                            transcript_status = "success"
                            print(f"  [{clip_stem}] confirmed non-speech / zero spoken words (skipping redundant retries)")
                            break
                    if hq_words:
                        transcript_err = ""
                        transcript_status = "incomplete" if res_incomplete else "success"
                        write_json(hq_path, hq_words)
                        write_json(words_path, hq_words)
                        break
                except Exception as exc:
                    transcript_err = f"{type(exc).__name__}: {exc}"
                    if _is_quota_error_text(transcript_err):
                        transcript_status = "quota_exceeded"
                        print(f"  [WARN] align_engine quota failure on {clip_stem}: {exc}")
                        break
                    print(f"  [WARN] align_engine fallback on {clip_stem}: {exc}")

            if attempt == 0 and active_speech_sec >= 0.8 and not hq_words and not confirmed_no_speech:
                print(
                    f"  [WARN] {clip_stem}: empty words returned despite {active_speech_sec:.2f}s of active speech; retrying transcription..."
                )

        if not hq_words and not base_words and not transcript_err and not confirmed_no_speech and not align_engine_attempted:
            transcribe_script = ROOT / "scripts" / "transcribe.py"
            if transcribe_script.is_file():
                venv_py = find_venv_python()
                cmd = [str(venv_py), str(transcribe_script), str(resolved_src), "--lang", lang]
                if script_path:
                    cmd += ["--script", str(script_path)]
                try:
                    proc = subprocess.run(cmd, check=False, capture_output=True, text=True)
                    if words_path.is_file():
                        base_words = read_json(words_path)
                    elif proc.returncode != 0:
                        transcript_err = (proc.stderr or proc.stdout or f"transcribe.py exit {proc.returncode}").strip()[:250]
                        transcript_status = "quota_exceeded" if _is_quota_error_text(transcript_err) else "cloud_failed"
                except Exception as exc:
                    transcript_err = f"{type(exc).__name__}: {exc}"
                    transcript_status = "quota_exceeded" if _is_quota_error_text(transcript_err) else "cloud_failed"

        if not hq_words and not base_words and not confirmed_no_speech and (active_speech_sec >= 0.8 or transcript_err or res_incomplete):
            print(
                f"  [WARN] {clip_stem}: 0 words transcribed"
                + (f" ({transcript_err[:120]})" if transcript_err else f" despite {active_speech_sec:.2f}s of active speech")
                + "; marking transcript cache incomplete so next run retries automatically"
            )
            for empty_f in (align_path, hq_path, words_path, gem_cache_path):
                if empty_f.is_file():
                    try:
                        empty_f.unlink()
                    except OSError:
                        pass
            marker = work_dir / "engine_version.json"
            try:
                ev = read_json(marker) if marker.is_file() else {}
            except Exception:
                ev = {}
            if isinstance(ev, dict):
                ev["transcript"] = "incomplete"
                write_json(marker, ev)
            if not transcript_status:
                transcript_status = "incomplete"

    if not hq_words:
        hq_words = base_words
    try:
        import events

        events.emit(
            "speech",
            f"{clip_stem}: {len(hq_words)} words transcribed and aligned" if hq_words else f"{clip_stem}: no speech found",
            clip=clip_stem,
            words=len(hq_words),
        )
    except Exception:
        pass
    if not base_words:
        base_words = hq_words

    def _norm_w(wlist: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        out = []
        for item in wlist:
            if isinstance(item, dict):
                txt = str(item.get("w") or item.get("word") or item.get("text") or "").strip()
                st = float(item.get("start") or 0.0)
                en = float(item.get("end") or st)
            else:
                txt = str(item or "").strip()
                st, en = 0.0, 0.0
            if not txt:
                continue
            out.append({"w": txt, "start": round(st, 3), "end": round(en, 3)})
        return out

    hq_words = _norm_w(hq_words)
    base_words = _norm_w(base_words)

    # Persist / verify `transcript_meta.json` and propagate status cleanly
    dur_val = float((info or {}).get("duration") or 0.0)
    if res_meta is not None:
        final_meta = res_meta
    else:
        meta_override = ""
        if transcript_status in ("quota_exceeded", "cloud_failed"):
            meta_override = transcript_status
        elif no_sound or (confirmed_no_speech and active_speech_sec < 0.8):
            meta_override = "no_speech"
        elif res_incomplete:
            meta_override = "incomplete"
        final_meta = _compute_clip_transcript_meta(
            work_dir=work_dir,
            duration_sec=dur_val,
            spoken_spans=spoken_spans,
            words_list=hq_words,
            existing_meta=existing_meta,
            override_status=meta_override,
            warning_msg=transcript_err or transcript_warn,
        )
    if wav_path.is_file() or res_meta is not None or transcript_status or existing_meta is not None:
        write_json(meta_path, final_meta)

    if isinstance(info, dict):
        if transcript_err:
            if not transcript_status:
                transcript_status = "quota_exceeded" if _is_quota_error_text(transcript_err) else "cloud_failed"
            info["_transcript_status"] = transcript_status
            info["_transcript_error"] = transcript_err
        elif final_meta["status"] == "incomplete":
            info["_transcript_status"] = "incomplete"
            if final_meta.get("warning"):
                info["_transcript_warning"] = final_meta["warning"]
            if hq_words:
                print(
                    f"  [WARN] {clip_stem}: sparse/incomplete transcript ({len(hq_words)} words, "
                    f"coverage={final_meta['speech_coverage']:.0%}); set ZENO_RETRY_INCOMPLETE=1 to recover missing speech"
                )
        elif confirmed_no_speech or final_meta["status"] in ("ok", "no_speech"):
            info["_transcript_status"] = "success"

    return info, hq_words, base_words


def _clip_tokens(clip: Dict[str, Any]) -> List[str]:
    return [_clean_token(w["w"]) for w in clip.get("hq_words", []) if _clean_token(w["w"])]


SAME_TAKE_THRESHOLD = 0.72

REACTION_RE = re.compile(
    r"laugh|chuckl|giggl|smirk|cry|cries|crying|sob|tears|sigh|gasp|shock|smile|grin|react|scream|hug|cheer",
    re.I,
)
EMOTION_RE = re.compile(
    r"cry|cries|crying|sob|tears|sad|heartbroken|emotional|overwhelm|longing|lost in thought|hurt|embarrass|hides? (?:his|her|their)? ?face|looks? down",
    re.I,
)
PAUSE_NOTE_RE = re.compile(r"pause|silen|stare|staring|awkward", re.I)

LISTENER_LOOK_MAX_DISTANCE = 0.40
LISTENER_LOOK_MARGIN = 0.15


def clip_is_silent(c: Dict[str, Any]) -> bool:
    """Return True when a clip contains no meaningful speech (including stationary ambient noise)."""
    ws = c.get("hq_words") or []
    dur = float((c.get("info") or {}).get("duration") or 1.0)
    active_speech_sec = 0.0
    if c.get("active_speech_sec") is not None:
        try:
            active_speech_sec = float(c["active_speech_sec"])
        except (TypeError, ValueError):
            active_speech_sec = 0.0
    else:
        spans = c.get("speech_spans")
        if spans is None:
            wdir_raw = c.get("work_dir")
            wdir = Path(wdir_raw) if wdir_raw else (clip_work_dir(resolve(c["src"])) if c.get("src") else None)
            if wdir and (wdir / "speech_spans.json").is_file():
                try:
                    sdata = read_json(wdir / "speech_spans.json")
                    spans = sdata.get("spans") if isinstance(sdata, dict) else sdata
                except Exception:
                    spans = None
            if spans is None and wdir and (wdir / "audio16k.wav").is_file():
                spans = _speech_spans_of({"work_dir": wdir})
            if spans is None and wdir and (wdir / "transcript_meta.json").is_file():
                try:
                    tmeta = read_json(wdir / "transcript_meta.json")
                    if isinstance(tmeta, dict) and tmeta.get("active_speech_sec") is not None:
                        active_speech_sec = float(tmeta["active_speech_sec"])
                except Exception:
                    pass
        if isinstance(spans, (list, tuple)):
            active_speech_sec = sum(
                max(0.0, float(sp[1]) - float(sp[0]))
                for sp in spans
                if isinstance(sp, (list, tuple)) and len(sp) >= 2
            )
    if active_speech_sec >= 1.5:
        return False
    dbs = sorted(db for _, db in (c.get("rms_env") or []))
    if dbs:
        p50, p90 = dbs[len(dbs) // 2], dbs[min(len(dbs) - 1, int(len(dbs) * 0.90))]
        if (p90 - p50) <= 4.2 and sum(1 for db in dbs if db > -28.0) / len(dbs) >= 0.90:
            return True
    if not ws:
        return True
    return sum(float(w["end"]) - float(w["start"]) for w in ws) / max(0.1, dur) < 0.08


def voiced_spans(
    rms_env: List[Tuple[float, float]],
    floor_db: float = -48.0,
    merge_gap: float = 0.25,
) -> List[Tuple[float, float]]:
    """Measured stretches of real audio energy above `floor_db`, merged across gaps shorter than `merge_gap`."""
    spans: List[List[float]] = []
    for t, db in rms_env:
        if db > floor_db:
            if spans and t - spans[-1][1] <= merge_gap:
                spans[-1][1] = t
            else:
                spans.append([t, t])
    return [(round(a, 2), round(b + 0.01, 2)) for a, b in spans if b - a >= 0.08]


def clip_has_no_sound(wav_path: Path, rms_env: Optional[List[Tuple[float, float]]] = None) -> bool:
    """Return True only when the audio holds neither voiced energy spans nor VAD speech spans."""
    env = rms_env if rms_env is not None else compute_10ms_rms_envelope(wav_path)
    if not env or voiced_spans(env):
        return False
    return not _speech_spans_of({"work_dir": Path(wav_path).parent})


def silent_word_runs(words: List[Dict[str, Any]], flagged: List[Dict[str, Any]]) -> List[str]:
    """Summarize each unbroken run of aligned words sitting in measured silence."""
    bad = {id(w) for w in flagged}
    lines: List[str] = []
    run: List[Dict[str, Any]] = []
    for w in list(words) + [None]:
        if w is not None and id(w) in bad:
            run.append(w)
            continue
        if run:
            a, b = run[0], run[-1]
            who = (
                f"'{a['w']}' at {float(a['start']):.2f}-{float(a['end']):.2f}"
                if len(run) == 1
                else f"{len(run)} words from '{a['w']}' ({float(a['start']):.2f}) to '{b['w']}' ({float(b['end']):.2f})"
            )
            lines.append(f"  ! TIMING WRONG: {who}: that stretch is silent; the real place is in a nearby voiced span")
            run = []
    return lines


def no_speech_word_runs(words: List[Dict[str, Any]], flagged: List[Dict[str, Any]]) -> List[str]:
    """Group consecutive words outside VAD speech spans into summary warnings."""
    bad = {id(w) for w in flagged}
    lines: List[str] = []
    run: List[Dict[str, Any]] = []
    for w in list(words) + [None]:
        if w is not None and id(w) in bad:
            run.append(w)
            continue
        if run:
            a, b = run[0], run[-1]
            if len(run) == 1:
                lines.append(
                    f"  ! NO SPEECH under '{a['w']}' ({float(a['start']):.2f}-{float(a['end']):.2f}): "
                    "likely a transcription ghost or a mistimed word; do not build a cut on it"
                )
            else:
                lines.append(
                    f"  ! NO SPEECH under {len(run)} words from '{a['w']}' ({float(a['start']):.2f}) to "
                    f"'{b['w']}' ({float(b['end']):.2f}): likely transcription ghosts or mistimed words; do not build a cut on them"
                )
            run = []
    return lines


def words_in_silence(
    words: List[Dict[str, Any]],
    rms_env: List[Tuple[float, float]],
    floor_db: float = -55.0,
) -> List[Dict[str, Any]]:
    """Return words whose aligned span holds no audio energy above `floor_db`."""
    out = []
    for w in words:
        a, b = float(w["start"]), float(w["end"])
        loud = max((db for t, db in rms_env if a <= t <= b), default=-140.0)
        if loud < floor_db:
            out.append(w)
    return out


def _speech_spans_of(c: Dict[str, Any]) -> List[Tuple[float, float]]:
    """VAD speech intervals in a clip, cached in `speech_spans.json` alongside `audio16k.wav`."""
    wav = Path(c.get("work_dir") or "") / "audio16k.wav"
    if not wav.is_file():
        return []
    cache = wav.parent / "speech_spans.json"
    sig = [wav.stat().st_size, int(wav.stat().st_mtime)]
    try:
        d = json.loads(cache.read_text(encoding="utf-8"))
        if d.get("sig") == sig:
            return [tuple(x) for x in d["spans"]]
    except (OSError, ValueError, KeyError):
        pass
    try:
        import soundfile as sf
        from align_engine import vad

        if not vad.available():
            return []
        y, _ = sf.read(str(wav), dtype="float32")
        spans = vad.speech_spans(y)
        write_json(cache, {"sig": sig, "spans": spans})
        return spans
    except Exception:
        return []


def _silent_clip_owner(c: Dict[str, Any], clips_data: List[Dict[str, Any]]) -> str:
    """Measure visual colour-layout distance from a silent clip to speaking clips."""
    if vision_tracker is None:
        return ""
    try:
        sig = vision_tracker.clip_visual_signature(resolve(c["src"]))
        if sig is None:
            return ""
        ranked = []
        for o in clips_data:
            if o is c or clip_is_silent(o):
                continue
            osig = vision_tracker.clip_visual_signature(resolve(o["src"]))
            if osig is not None:
                ranked.append((float(vision_tracker.signature_distance(sig, osig)), Path(str(o["src"])).name))
        ranked.sort()
        if not ranked:
            return ""
        near = f"looks like {ranked[0][1]} (distance {ranked[0][0]:.2f}"
        return near + (f"; next {ranked[1][1]} {ranked[1][0]:.2f})" if len(ranked) > 1 else ")")
    except Exception:
        return ""


def emphasis_words(
    rms_env: List[Tuple[float, float]],
    words: List[Dict[str, Any]],
    max_n: int = 6,
    min_delta_db: float = 4.0,
) -> List[Dict[str, Any]]:
    """Identify vocally stressed words whose peak dB exceeds the clip's median voiced speech by `min_delta_db`."""
    voiced = sorted(db for _, db in rms_env if db > -34.0)
    if not voiced or not words:
        return []
    median = voiced[len(voiced) // 2]
    scored = []
    for w in words:
        a, b = float(w["start"]), float(w["end"])
        text = str(w.get("w") or "").strip()
        if b - a < 0.12 or len(_clean_token(text)) < 2:
            continue
        dbs = [db for t, db in rms_env if a <= t <= b and db > -36.0]
        if dbs and max(dbs) - median >= min_delta_db:
            scored.append((round(max(dbs) - median, 1), round(a, 2), text))
    scored.sort(reverse=True)
    return [{"w": t, "start": a, "delta_db": d} for d, a, t in scored[:max_n]]


def safe_zoom_focus(spans: List[Tuple[float, float]], z: float) -> Optional[Tuple[float, float]]:
    """Compute the focus-x range `[lo, hi]` that keeps every face span `(x0, x1)` inside the frame at zoom `z`."""
    k = 1.0 - 1.0 / z
    if k <= 0 or not spans:
        return None
    lo = max(0.05, (max(x1 for _, x1 in spans) - 1.0 / z) / k)
    hi = min(0.95, min(x0 for x0, _ in spans) / k)
    return (round(lo, 2), round(hi, 2)) if lo <= hi else None


_SCAN_QUEUE: List[str] = []
_FACE_SEEN: set = set()
_SCAN_LAUNCHED: set = set()


def _start_background_scan() -> None:
    """Launch background frame scan if `vision_scan.py` and `events.py` are enabled."""
    try:
        if os.environ.get("ZENO_EVENTS", "").lower() == "off" or not _SCAN_QUEUE:
            return
        scan_script = Path(__file__).resolve().parent / "vision_scan.py"
        if not scan_script.is_file():
            return
        import events

        project = events._project_dir()
        if project is None:
            return
        paths = [p for p in dict.fromkeys(_SCAN_QUEUE) if p not in _SCAN_LAUNCHED]
        if not paths:
            return
        _SCAN_LAUNCHED.update(paths)
        cmd = [
            sys.executable,
            str(scan_script),
            *paths,
            "--step",
            "0.5",
            "--max",
            "24",
            "--project",
            project.name,
            "--thumb",
            "220",
        ]
        kw: Dict[str, Any] = {
            "stdout": subprocess.DEVNULL,
            "stderr": subprocess.DEVNULL,
            "stdin": subprocess.DEVNULL,
        }
        if os.name == "nt":
            kw["creationflags"] = 0x00000008 | 0x00004000
        else:
            kw["start_new_session"] = True
            kw["preexec_fn"] = lambda: os.nice(10)
        subprocess.Popen(cmd, **kw)
    except Exception:
        pass


def format_facts(clips_data: List[Dict[str, Any]], script_lines: Optional[List[Dict[str, Any]]] = None) -> str:
    """Format all per-clip measurements (`start word`, pauses, VAD spans, emphasis, face geometry) into `facts.md`."""
    import script_order

    out = [
        "# FACTS: measured by Zeno. Times are seconds in the clip. `1.20 word` = the word starts at 1.20; `|2.31` = speech stops at 2.31.",
        "",
    ]
    for c in clips_data:
        info = c.get("info") or {}
        name = Path(str(c["src"])).name
        vis: Dict[str, Any] = {}
        if vision_tracker is not None:
            try:
                vis = vision_tracker.analyze_clip_vision(resolve(c["src"]))
            except Exception:
                vis = {}
        tags = [f"{float(info.get('duration') or 0):.2f}s", f"{info.get('width')}x{info.get('height')}"]
        for key in ("shot_type", "face_count", "focus"):
            if vis.get(key) not in (None, ""):
                tags.append(f"{key}={vis[key]}")
        bb = vis.get("bbox_norm")
        if bb and len(bb) >= 4:
            tags.append(f"face_top-bottom={float(bb[1]):.2f}-{float(bb[3]):.2f}")
        if clip_is_silent(c):
            tags.append("SILENT")
            owner = _silent_clip_owner(c, clips_data)
            if owner:
                tags.append(owner)
        out.append(f"## {name} ({' '.join(tags)})")
        try:
            import events

            _fs = [
                (round(float(f["bbox_norm"][0]), 2), round(float(f["bbox_norm"][2]), 2))
                for f in (vis.get("faces") or [])
                if f.get("bbox_norm") and len(f["bbox_norm"]) >= 4
            ]
            if (name, vis.get("face_count"), vis.get("shot_type")) not in _FACE_SEEN:
                _FACE_SEEN.add((name, vis.get("face_count"), vis.get("shot_type")))
                events.emit(
                    "face",
                    f"{name}: {vis.get('face_count', 0)} face(s), {vis.get('shot_type', 'unknown')}"
                    + (", silent clip" if clip_is_silent(c) else ""),
                    clip=name,
                    faces=_fs,
                    shot=vis.get("shot_type"),
                    silent=clip_is_silent(c),
                )
        except Exception:
            pass
        _SCAN_QUEUE.append(str(resolve(c["src"])))
        spans = [
            (float(f["bbox_norm"][0]), float(f["bbox_norm"][2]))
            for f in (vis.get("faces") or [])
            if f.get("bbox_norm") and len(f["bbox_norm"]) >= 4
        ]
        if len(spans) >= 2:
            parts = []
            for z in (1.05, 1.08, 1.10):
                r = safe_zoom_focus(spans, z)
                parts.append(f"{z:g}: focus x {r[0]:.2f}-{r[1]:.2f}" if r else f"{z:g}: none (keep z=1)")
            out.append(
                "  faces x "
                + ", ".join(f"{a:.2f}-{b:.2f}" for a, b in spans)
                + " | zoom that keeps every face in frame: "
                + "; ".join(parts)
            )
        phrase: List[str] = []
        ws = c.get("hq_words") or []
        env = c.get("rms_env") or []
        spoken = _speech_spans_of(c)
        is_sil = clip_is_silent(c)
        silent_flagged = words_in_silence(ws, env) if env else []
        no_speech_flagged = (
            [
                w
                for w in ws
                if not any(
                    float(w["start"]) <= float(b) + 0.10 and float(w["end"]) >= float(a) - 0.10
                    for a, b in spoken
                )
                and (
                    not env
                    or max(
                        (
                            db
                            for tt, db in env
                            if float(w["start"]) - 0.05 <= tt <= float(w["end"]) + 0.05
                        ),
                        default=-140.0,
                    )
                    < -45.0
                )
            ]
            if spoken
            else []
        )
        bad_ids = {id(w) for w in silent_flagged} | {id(w) for w in no_speech_flagged}
        dbs = sorted(db for _, db in env)
        stationary_noise = bool(dbs) and (
            (dbs[min(len(dbs) - 1, int(len(dbs) * 0.90))] - dbs[len(dbs) // 2]) <= 4.2
            and sum(1 for db in dbs if db > -28.0) / len(dbs) >= 0.90
        )
        suppress_hallucinated = bool(ws) and (
            stationary_noise
            or (len(ws) >= 8 and len(bad_ids) >= 0.80 * len(ws))
            or (is_sil and (len(ws) >= 8 or (bad_ids and len(bad_ids) == len(ws))))
        )
        if suppress_hallucinated or (is_sil and not ws):
            out.append("  ! SILENT / AMBIENT CLIP — use only as silent listener footage")
            pauses = [f"{float(s['start']):.2f}-{float(s['end']):.2f}" for s in c.get("macro_silences") or []]
            if pauses:
                out.append("  pauses: " + " ".join(pauses))
        else:
            for i, w in enumerate(ws):
                w_txt = str(w.get("w") or "")
                w_end = float(w["end"])
                phrase.append(f"{float(w['start']):.2f} {w_txt}")
                if i + 1 == len(ws) or float(ws[i + 1]["start"]) - w_end >= 0.30:
                    out.append("  " + " ".join(phrase) + f" |{w_end:.2f}")
                    phrase = []
            pauses = [f"{float(s['start']):.2f}-{float(s['end']):.2f}" for s in c.get("macro_silences") or []]
            if pauses:
                out.append("  pauses: " + " ".join(pauses))
            if spoken:
                out.append("  speech (voice detector, works over noise): " + " ".join(f"{a:.2f}-{b:.2f}" for a, b in spoken))
                gaps = [
                    (spoken[i][1], spoken[i + 1][0])
                    for i in range(len(spoken) - 1)
                    if spoken[i + 1][0] - spoken[i][1] >= 0.25
                ]
                if gaps:
                    out.append("  speech gaps: " + " ".join(f"{a:.2f}-{b:.2f}({b - a:.2f}s)" for a, b in gaps))
                out.extend(no_speech_word_runs(ws, no_speech_flagged))
            stress = emphasis_words(env, ws) if env else []
            if stress:
                out.append("  emphasis: " + " ".join(f"'{x['w']}'@{x['start']:.2f}(+{x['delta_db']}dB)" for x in stress))
            if env:
                out.append("  voiced (real sound, measured from the audio): " + " ".join(f"{a:.2f}-{b:.2f}" for a, b in voiced_spans(env)))
                out.extend(silent_word_runs(ws, silent_flagged))
        live_ws = [] if suppress_hallucinated else [w for w in ws if id(w) not in bad_ids and not w.get("no_sound")]
        if len(live_ws) >= 2:
            allowed_script_text = ""
            if script_lines:
                clip_toks = [_clean_token(w.get("w", "")) for w in live_ws if _clean_token(w.get("w", ""))]
                matched_texts = [
                    str(l.get("text") or "")
                    for l in script_lines
                    if (l.get("tokens") or [])
                    and (
                        script_order.recall(clip_toks, l.get("tokens") or []) >= 0.45
                        or script_order.part_fit(clip_toks, l.get("tokens") or []) >= 0.45
                    )
                ]
                allowed_script_text = " ".join(matched_texts)
            for rep in script_order.find_unscripted_repeats(live_ws, allowed_script_text):
                out.append(
                    f"  ! REPEAT / STUMBLE ({rep['kind']}): '{rep['phrase']}' spoken twice "
                    f"({rep['first_start']:.2f}-{rep['first_end']:.2f} and {rep['second_start']:.2f}-{rep['second_end']:.2f}) "
                    f"— clean trim start: {rep['clean_trim_start']:.2f} (cut out {rep['first_start']:.2f}-{rep['clean_trim_start']:.2f} or split around one copy)"
                )
        for a in c.get("anomalies") or []:
            out.append(f"  ! {a}")
        out.append("")
    _start_background_scan()
    return "\n".join(out)


def discover_clips(raw_args: List[str]) -> List[Tuple[str, str, Path]]:
    """Resolve input clip arguments, footage directories, or `input/footage/*.mp4` into `(stem, src_str, work_dir)` tuples."""
    expanded: List[str] = []
    for pat in raw_args:
        matches = glob.glob(str(resolve(pat))) or glob.glob(pat)
        if matches:
            expanded.extend(sorted(matches, key=lambda x: _natural_clip_sort_key(Path(x))))
        elif "*" not in pat and "?" not in pat:
            expanded.append(pat)

    items: List[Tuple[str, str, Path]] = []
    video_exts = (".mp4", ".mov", ".m4v", ".mkv", ".webm")
    for item in expanded:
        p = Path(item)
        rp = resolve(item)
        if p.is_file():
            items.append((p.stem, str(p), clip_work_dir(p)))
        elif rp.is_file():
            items.append((rp.stem, str(rp), clip_work_dir(rp)))
        elif rp.is_dir() and not (rp / "info.json").is_file():
            dir_vids = sorted(
                [f for f in rp.iterdir() if f.is_file() and f.suffix.lower() in video_exts],
                key=_natural_clip_sort_key,
            )
            for vf in dir_vids:
                items.append((vf.stem, str(vf), clip_work_dir(vf)))
        else:
            stem = p.stem if p.suffix else p.name
            wdir = WORK / stem
            if wdir.is_dir() and (wdir / "info.json").is_file():
                info = read_json(wdir / "info.json")
                src_str = str(info.get("path") or f"input/footage/{stem}.mp4").replace("\\", "/")
                items.append((stem, src_str, wdir))

    if not items:
        footage_dir = ROOT / "input" / "footage"
        footage_files = (
            sorted(
                [f for f in footage_dir.iterdir() if f.is_file() and f.suffix.lower() in video_exts],
                key=_natural_clip_sort_key,
            )
            if footage_dir.is_dir()
            else []
        )
        if footage_files:
            for fp in footage_files:
                items.append((fp.stem, f"input/footage/{fp.name}", clip_work_dir(fp)))
        elif WORK.is_dir():
            work_dirs = sorted(
                [d for d in WORK.iterdir() if d.is_dir() and (d / "info.json").is_file() and re.match(r"^S\d+$", d.name)],
                key=_natural_clip_sort_key,
            )
            for wd in work_dirs:
                info = read_json(wd / "info.json")
                src_str = str(info.get("path") or f"input/footage/{wd.name}.mp4").replace("\\", "/")
                items.append((wd.name, src_str, wd))

    # Deduplicate clip stems when clips from different folders share the same filename
    seen: Dict[str, int] = {}
    unique: List[Tuple[str, str, Path]] = []
    for stem, src_str, wdir in items:
        seen[stem] = seen.get(stem, 0) + 1
        unique.append((stem if seen[stem] == 1 else f"{stem}_{seen[stem]}", src_str, wdir))
    return unique


def annotate_clip_voice_hints(clips_data: List[Dict[str, Any]], script_lines: List[Dict[str, Any]]) -> None:
    """Fast O(N) pre-inspect voice clustering so `build_line_map` tags each candidate with `[voice=...]`."""
    from collections import Counter
    import script_order
    import speaker_check

    speaking = [c for c in clips_data if not c.get("is_listening") and not c.get("dropped_take") and (c.get("hq_words") or [])]
    if len(speaking) < 2 or not script_lines:
        return
    speakers_named = {
        str(l.get("speaker") or "").strip()
        for l in script_lines
        if l.get("speaker") and " / " not in str(l.get("speaker"))
    }
    if len(speakers_named) < 2:
        return

    items: List[Dict[str, Any]] = []
    for min_toks in (4, 2):
        line_sets = [
            (str(l["speaker"]).strip(), set(l["tokens"]), len(l["tokens"]))
            for l in script_lines
            if l.get("speaker") and " / " not in str(l.get("speaker")) and len(l.get("tokens") or []) >= min_toks
        ]
        if not line_sets:
            continue
        cand_items: List[Dict[str, Any]] = []
        for idx_c, c in enumerate(speaking):
            ws = script_order._filter_live_clip_words(c)
            wav = Path(c.get("work_dir") or "") / "audio16k.wav"
            if not ws or not wav.is_file():
                continue
            turns = speaker_check.build_units(ws, [None] * len(ws))
            exp: List[Optional[str]] = [None] * len(ws)
            for a, b in turns:
                utoks = [
                    _clean_token(t)
                    for k in range(a, b + 1)
                    for t in str(ws[k].get("w") or "").split()
                    if _clean_token(t)
                ]
                if len(utoks) < min(3, min_toks):
                    continue
                uset = set(utoks)
                best_sp, best_sc = None, 0.0
                for sp, lset, _llen in line_sets:
                    inter = len(uset & lset)
                    if not inter:
                        continue
                    rec = inter / len(lset)
                    prec = inter / len(uset)
                    f1 = 2.0 * rec * prec / (rec + prec)
                    if f1 > best_sc and f1 >= 0.40:
                        best_sc, best_sp = f1, sp
                if best_sp:
                    for k in range(a, b + 1):
                        exp[k] = best_sp
            anchored = Counter(s for s in exp if s)
            if anchored:
                dom, cnt = anchored.most_common(1)[0]
                if cnt / sum(anchored.values()) >= 0.75:
                    exp = [s if (s and anchored[s] >= 2) else dom for s in exp]
                cand_items.append({
                    "id": idx_c,
                    "clip": c,
                    "wav": wav,
                    "words": ws,
                    "expected": exp,
                    "rms_env": c.get("rms_env") or [],
                })
        if len({sp for it in cand_items for sp in it["expected"] if sp}) >= 2:
            items = cand_items
            break
    if not items:
        return
    res = speaker_check.analyze(items)
    if not res.get("ok"):
        return
    for it in items:
        c = it["clip"]
        ws = it["words"]
        exp = list(it["expected"])
        bad_spans = ((res.get("clips") or {}).get(it["id"]) or {}).get("spans") or []
        word_voices: List[Tuple[Optional[str], bool]] = [(sp, False) for sp in exp]
        for sp_info in bad_spans:
            for k in range(int(sp_info["first"]), min(len(ws), int(sp_info["last"]) + 1)):
                word_voices[k] = (str(sp_info.get("heard") or ""), True)
        counts = Counter(h for h, _ in word_voices if h)
        dom_heard = counts.most_common(1)[0][0] if counts else ""
        c["clip_voice_hint"] = {
            "speaker": dom_heard,
            "spans": [
                (float(ws[k]["start"]), float(ws[k]["end"]), h, dis)
                for k, (h, dis) in enumerate(word_voices)
                if h
            ],
        }


def common_unique_output(name: str) -> Path:
    """Resolve the job's output reel MP4 path: `~/Downloads/Zeno Outs/<Name>/reel_<Name>.mp4` (or `ZENO_OUTPUT_DIR` if overridden)."""
    if hasattr(common, "unique_job_output"):
        return Path(common.unique_job_output(name))
    raw_override = os.environ.get("ZENO_OUTPUT_DIR", "").strip()
    base_dir = Path(raw_override).expanduser() if raw_override else (Path.home() / "Downloads" / "Zeno Outs").expanduser()
    clean_fn = getattr(common, "job_folder_name", None)
    if callable(clean_fn):
        clean_name = str(clean_fn(name))
    else:
        s = re.sub(r"^(?:reel|sq)[\s_-]+", "", str(name or "").strip(), flags=re.IGNORECASE).strip() or str(name or "").strip()
        clean_name = re.sub(r'[\\/:*?"<>|\x00-\x1f]+', " ", s).strip().strip(".")
        clean_name = re.sub(r"\s+", " ", clean_name)[:180].strip() or "Zeno Edit"
    folder, n = base_dir / clean_name, 2
    while folder.is_dir() and any(f.suffix.lower() in (".mp4", ".prproj") for f in folder.iterdir()):
        folder = base_dir / f"{clean_name} ({n})"
        n += 1
    return folder / f"reel_{folder.name}.mp4"


def common_job_start(identity: str = "") -> float:
    if identity and hasattr(common, "job_begin"):
        return float(common.job_begin(reuse_minutes=30.0, identity=identity))
    if hasattr(common, "job_start_epoch"):
        return float(common.job_start_epoch())
    return time.time()


def _atomic_write_text(target: Path, text: str) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(target.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, target)


def _format_edl_fallback(plan: Dict[str, Any]) -> str:
    """Minimal header formatter used only if `edl_parser` is not yet importable."""
    canvas = plan.get("canvas") or {}
    lines = [
        f'#output "{plan.get("output", "")}"',
        f'#script "{plan.get("script", "")}"',
        f'#canvas {canvas.get("width", 1080)}x{canvas.get("height", 1920)}@{canvas.get("fps", 30)}',
        f'#preset {plan.get("preset", "hybrid")}',
        f'#mark_pack {plan.get("mark_pack", "flash")}',
    ]
    if plan.get("style"):
        lines.append(f'#style {plan["style"]}')
    if isinstance(plan.get("page"), dict) and plan["page"].get("mode"):
        lines.append(f'#page {plan["page"]["mode"]}')
    elif isinstance(plan.get("subs"), dict):
        subs = plan["subs"]
        lines.append(f'#subs {subs.get("style", "auto")} y={subs.get("y", 65)}')
    audio = plan.get("audio") or {}
    lines.append(f'#audio loudness={audio.get("loudness", DEFAULT_LOUDNESS_LUFS)}')
    lines.append("")
    return "\n".join(lines)


def _write_fallback_brief(proj_dir: Path, meta: Dict[str, Any], plan: Dict[str, Any], style: str) -> None:
    """Ensure `brief.md` exists even when `edit_brief` is unavailable."""
    brief_path = proj_dir / "brief.md"
    if brief_path.is_file():
        return
    parts = [f"# Brief — {meta.get('title') or proj_dir.name}"]
    if style:
        parts.append(f"- Style: `{style}`")
    if meta.get("concept_brief"):
        parts.append(f"- Concept & Set-up: {meta['concept_brief']}")
    if meta.get("characters"):
        chars = "; ".join(f"{k}: {v}" for k, v in meta["characters"].items())
        parts.append(f"- Characters: {chars}")
    if meta.get("scenes"):
        sc_txt = " | ".join(
            f"{sc['scene']}: {sc['visual']}" if sc.get("visual") else sc["scene"]
            for sc in meta["scenes"]
        )
        parts.append(f"- Scenes: {sc_txt}")
    parts.append(f"- Preset: `{plan.get('preset', 'hybrid')}` | Mark pack: `{plan.get('mark_pack', 'flash')}`")
    _atomic_write_text(brief_path, "\n".join(parts) + "\n")


def _select_canvas_fps(clips_data: List[Dict[str, Any]]) -> int:
    """Select the modal standard canvas FPS (`24`/`25`/`30`) across valid clips,
    breaking exact ties in favor of the higher standard FPS so mixed-FPS shoots stay smooth.
    """
    std_rates = (24, 25, 30)
    valid_clips = [c for c in clips_data if not c.get("dropped_take")] or list(clips_data)
    counts: Dict[int, int] = {}
    for c in valid_clips:
        raw_fps = (c.get("info") or {}).get("fps")
        try:
            fps_f = float(raw_fps)
        except (TypeError, ValueError):
            continue
        if not math.isfinite(fps_f) or fps_f <= 0.0:
            continue
        snapped = min(std_rates, key=lambda r: (abs(r - fps_f), -r))
        counts[snapped] = counts.get(snapped, 0) + 1
    if not counts:
        return 24
    return max(counts.items(), key=lambda kv: (kv[1], kv[0]))[0]


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Stages 1-3 pre-processor: footage scan, cached transcription/alignment, face tracking, line matching, clip sheets, and Director packet."
    )
    ap.add_argument("clips", nargs="*", help="Input footage clips, footage directory, or work/<clip> directories")
    ap.add_argument("--script", required=False, help="Path to script file in input/script/ or local disk")
    ap.add_argument("--lang", default="auto", help="Spoken language code (auto, en, kn, ta, hi, te, ml)")
    ap.add_argument("--project", default=None, help="Project folder name under work/<project>")
    ap.add_argument(
        "--preset",
        default="auto",
        choices=["auto", "single", "split_v", "split_h", "chat_bar", "video_call", "top_pip", "hybrid"],
        help="Layout preset mode",
    )
    ap.add_argument(
        "--mark-pack",
        default="flash",
        help="Swappable right/wrong mark overlay pack (flash, vignette, default, glossy)",
    )
    ap.add_argument(
        "--name",
        default="",
        help="Video delivery name; outputs go to ~/Downloads/Zeno Outs/<name>/reel_<name>.mp4 (or ZENO_OUTPUT_DIR/<name>/)",
    )
    ap.add_argument(
        "--style",
        default="",
        help="Format or style name written into the plan header as '#style <name>'",
    )
    ap.add_argument("--subs", default=None, help="Burn-in subtitle style (default: 'auto' at y=65; pass 'off' to disable)")
    ap.add_argument("--no-subs", action="store_true", help="Do not write default '#subs auto y=65' into initial plan.edl")
    ap.add_argument(
        "--fresh",
        action="store_true",
        help="Rebuild cached transcripts and alignments for these clips",
    )
    ap.add_argument(
        "--clean-raw-json",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Keep work/ lean by avoiding raw intermediate JSON clutter",
    )
    args = ap.parse_args()
    if args.fresh:
        os.environ["ZENO_FRESH"] = "1"

    # Resolve script file
    if args.script:
        script_path = resolve(args.script)
    else:
        scripts_found = sorted((ROOT / "input" / "script").glob("*.txt"))
        if not scripts_found:
            raise SystemExit("No script file specified and none found in input/script/*.txt")
        script_path = scripts_found[0]

    # Convert non-plain-text scripts (.docx, etc.) to UTF-8 .txt for downstream aligners/transcribers
    if script_path.suffix.lower() not in (".txt", ".md"):
        cache_dir = WORK / "_script_cache"
        cache_dir.mkdir(parents=True, exist_ok=True)
        converted = cache_dir / f"{script_path.stem}.txt"
        converted.write_text(read_script_text(script_path), encoding="utf-8")
        rel_label = converted.relative_to(ROOT) if converted.is_relative_to(ROOT) else converted
        print(f"  Script: converted {script_path.name} -> {rel_label}")
        script_path = converted

    meta, script_lines = parse_script_file(script_path)
    if args.name:
        _apply_explicit_title(meta, args.name)
    if args.lang == "auto" and str(meta.get("language") or "auto").lower() == "auto":
        try:
            guessed = infer_language(args.name or meta.get("title") or "", script_path.read_text(encoding="utf-8", errors="ignore"))
        except Exception:
            guessed = "auto"
        if guessed != "auto":
            meta["language"] = guessed
            print(f"  Language: {guessed} (from the video name and script), so the transcription prompt names it")
    resolve_proj_fn = getattr(common, "resolve_user_project_name", None)
    raw_proj = meta.get("project_name") or script_path.stem
    project_name = args.project or (resolve_proj_fn(raw_proj) if callable(resolve_proj_fn) else raw_proj)
    common_job_start(project_name)
    lang = args.lang if args.lang != "auto" else meta.get("language", "auto")

    clip_entries = discover_clips(args.clips)
    if not clip_entries:
        raise SystemExit("No input clips or cached work/S* directories found.")

    clips_data: List[Dict[str, Any]] = []
    quota_breaker_tripped = False
    quota_breaker_clip = ""
    quota_breaker_reason = ""
    for stem, src_str, wdir in clip_entries:
        transcript_path = wdir / "gemini_transcript.json"
        before = transcript_path.stat().st_mtime_ns if transcript_path.exists() else None
        info, hq_words, base_words = ensure_clip_transcribed(
            stem,
            src_str,
            wdir,
            lang=lang,
            script_path=script_path,
            clean_raw_json=args.clean_raw_json,
            skip_remote_transcription=quota_breaker_tripped,
            skip_reason=(
                f"skipped by quota circuit-breaker after {quota_breaker_clip}"
                + (f" ({quota_breaker_reason[:120]})" if quota_breaker_reason else "")
            )
            if quota_breaker_tripped
            else "",
        )
        if not quota_breaker_tripped and not hq_words:
            t_status = str((info or {}).get("_transcript_status") or "").strip().lower()
            t_err = str((info or {}).get("_transcript_error") or "").strip()
            if t_status in ("quota_exceeded", "cloud_failed") or _is_quota_error_text(t_err):
                quota_breaker_tripped = True
                quota_breaker_clip = stem
                quota_breaker_reason = t_err or t_status or "quota_exceeded"
                print(
                    f"  [circuit-breaker] stopping further uncached Gemini uploads after {stem} "
                    f"({t_status or 'quota_exceeded'}); already-cached clips will still be loaded.",
                    file=sys.stderr,
                )
        wav_path = wdir / "audio16k.wav"
        rms_env = compute_10ms_rms_envelope(wav_path)
        macro_sil, micro_dips = detect_two_tier_silences(wav_path, rms_env, cached_silences_path=wdir / "silences.json")
        if hq_words and wav_path.is_file() and clip_has_no_sound(wav_path, rms_env):
            print(f"  [{stem}] no sound in this clip: ignoring {len(hq_words)} transcribed words")
            hq_words, base_words = [], []
        clips_data.append({
            "stem": stem,
            "transcription_reused": (before == transcript_path.stat().st_mtime_ns) if transcript_path.exists() else True,
            "src": src_str,
            "work_dir": wdir,
            "info": info,
            "hq_words": hq_words,
            "base_words": base_words,
            "rms_env": rms_env,
            "macro_silences": macro_sil,
            "micro_dips": micro_dips,
            "anomalies": [],
        })

    if align_engine is not None and hasattr(align_engine, "unload_shared_pipeline"):
        try:
            align_engine.unload_shared_pipeline()
        except Exception:
            pass

    # Auto-pair <stem>.mp4 video with <stem>VO.mp4 voiceover audio when both exist
    by_stem_upper = {c["stem"].upper(): c for c in clips_data}
    for c in clips_data:
        st_up = c["stem"].upper()
        if st_up.endswith("VO") and len(st_up) > 2:
            base_up = st_up[:-2].rstrip("_- ")
            base_c = by_stem_upper.get(base_up)
            if base_c is not None and len(c.get("hq_words") or []) > len(base_c.get("hq_words") or []):
                base_had_speech = bool(base_c.get("hq_words")) and not clip_is_silent(base_c)
                if isinstance(base_c.get("info"), dict):
                    base_c["info"].pop("_transcript_error", None)
                    base_c["info"].pop("_transcript_status", None)
                base_c["hq_words"] = c["hq_words"]
                base_c["base_words"] = c["base_words"]
                base_c["rms_env"] = c["rms_env"]
                base_c["macro_silences"] = c["macro_silences"]
                base_c["micro_dips"] = c["micro_dips"]
                c["dropped_take"] = True
                c["anomalies"].append(f"[VO_PAIRED] paired voiceover audio/words into {base_c['stem']}")
                base_wdir = Path(base_c["work_dir"])
                vo_wav = Path(c["work_dir"]) / "audio16k.wav"
                if base_wdir.is_dir() and not base_had_speech:
                    base_wav = base_wdir / "audio16k.wav"
                    if vo_wav.is_file():
                        try:
                            shutil.copy2(vo_wav, base_wav)
                        except OSError:
                            pass
                    write_json(base_wdir / "hq_words.json", c["hq_words"])
                    write_json(base_wdir / "words.json", c["hq_words"])
                    write_json(base_wdir / "silences.json", c["macro_silences"])
                    vo_spans = _speech_spans_of(c)
                    if vo_spans:
                        sig = [base_wav.stat().st_size, int(base_wav.stat().st_mtime)] if base_wav.is_file() else [0, 0]
                        write_json(base_wdir / "speech_spans.json", {"sig": sig, "spans": [list(sp) for sp in vo_spans]})

    failed_transcripts = [
        (c["stem"], str((c.get("info") or {}).get("_transcript_error") or "transcription failed"))
        for c in clips_data
        if not c.get("hq_words") and (c.get("info") or {}).get("_transcript_error")
    ]
    if failed_transcripts:
        direct_failures = [
            (c["stem"], str((c.get("info") or {}).get("_transcript_error") or "transcription failed"))
            for c in clips_data
            if not c.get("hq_words")
            and (c.get("info") or {}).get("_transcript_error")
            and str((c.get("info") or {}).get("_transcript_status") or "") != "quota_skipped"
        ]
        skipped_clips = [
            c["stem"]
            for c in clips_data
            if not c.get("hq_words")
            and str((c.get("info") or {}).get("_transcript_status") or "") == "quota_skipped"
        ]
        cached_ok = sum(
            1
            for c in clips_data
            if not (c.get("info") or {}).get("_transcript_error")
        )
        print("\n[BLOCKED] Safe transcription guard stopped before cutting/planning:", file=sys.stderr)
        for fst, ferr in direct_failures:
            print(f"  - {fst}: {ferr}", file=sys.stderr)
        if skipped_clips:
            print(
                f"  - Skipped {len(skipped_clips)} remaining uncached clip(s) via quota circuit-breaker "
                f"({', '.join(skipped_clips)}) to avoid burning retries.",
                file=sys.stderr,
            )
        print(
            f"  Progress saved: {cached_ok}/{len(clips_data)} clip(s) already cached on disk "
            f"({len(failed_transcripts)} remaining). "
            "Wait 1-2 minutes for the Gemini Free Tier quota window to reset (or check Zeno Cloud) "
            "and re-run --inspect.",
            file=sys.stderr,
        )
        raise SystemExit(2)

    for c in clips_data:
        c["is_listening"] = clip_is_silent(c)
        c["matched_lines"] = []

    first_info = clips_data[0]["info"]
    canvas = {
        "width": int(first_info.get("width") or 720),
        "height": int(first_info.get("height") or 1280),
        "fps": _select_canvas_fps(clips_data),
    }
    no_subs_env = os.environ.get("ZENO_NO_SUBS", "").strip().lower() in ("1", "true", "yes", "on")
    subs_arg = str(args.subs).strip() if getattr(args, "subs", None) is not None else ""
    subs_disabled = (
        bool(getattr(args, "no_subs", False))
        or no_subs_env
        or subs_arg.lower() in ("off", "none", "false", "no", "0")
    )
    subs_header: Optional[Dict[str, Any]] = None
    if not subs_disabled:
        subs_style = subs_arg if (subs_arg and subs_arg.lower() not in ("on", "true", "yes", "1")) else "auto"
        subs_header = {"style": subs_style, "y": 65}

    page_header: Optional[Dict[str, Any]] = None
    try:
        if re.search(
            r"scroll(?:ing)?[\s-]*(?:text|subtitle|caption|transcript)",
            script_path.read_text(encoding="utf-8", errors="ignore"),
            re.I,
        ):
            page_header = {"mode": "scroll"}
            print(
                "  Script asks for scrolling text: the plan starts with `#page scroll` "
                "(two characters on top, the dialogue scrolling on a white page below)."
            )
    except OSError:
        page_header = None

    plan: Dict[str, Any] = {
        "output": common_unique_output(args.name or project_name).as_posix(),
        "script": str(script_path.relative_to(ROOT)) if script_path.is_relative_to(ROOT) else str(script_path),
        "canvas": canvas,
        "preset": args.preset if args.preset != "auto" else "hybrid",
        "mark_pack": args.mark_pack,
        **({"style": re.sub(r"[^a-z0-9_-]", "", args.style.lower())} if getattr(args, "style", "") else {}),
        **({"subs": subs_header} if subs_header is not None else {}),
        **({"page": page_header} if page_header is not None else {}),
        "segments": [],
        "marks": [],
        "audio": {"loudness": DEFAULT_LOUDNESS_LUFS},
    }
    proj_dir = WORK / project_name
    proj_dir.mkdir(parents=True, exist_ok=True)
    stamp_fn = getattr(common, "stamp_job_owner", None)
    if callable(stamp_fn):
        stamp_fn(proj_dir)

    # Persist per-clip cache directory mapping so subtitles.py, script_order.py, and verify.py
    # resolve exact caches even when generic filenames (S1.mp4, v1.mp4) are moved later.
    clip_cache_map: Dict[str, str] = {}
    for c in clips_data:
        c_path = Path(str(c["src"]))
        c_wdir = Path(c["work_dir"])
        cache_name = c_wdir.name
        if c_path.name:
            clip_cache_map[c_path.name] = cache_name
        if c_path.stem and c_path.stem not in clip_cache_map:
            clip_cache_map[c_path.stem] = cache_name
        if c.get("stem") and str(c["stem"]) not in clip_cache_map:
            clip_cache_map[str(c["stem"])] = cache_name
    write_json(proj_dir / "clip_caches.json", clip_cache_map)

    skeleton = (
        edl_parser.format_edl(plan)
        if (edl_parser is not None and hasattr(edl_parser, "format_edl"))
        else _format_edl_fallback(plan)
    )
    folders = {str(Path(str(c["src"])).parent) for c in clips_data}
    if len(folders) == 1 and "#src_dir" not in skeleton:
        sk_lines = skeleton.split("\n")
        sk_lines.insert(1, f'#src_dir "{folders.pop()}"')
        skeleton = "\n".join(sk_lines)
    for name in ("plan.edl", "plan.auto.edl"):
        (proj_dir / name).write_text(skeleton, encoding="utf-8")
    write_json(proj_dir / "plan.json", plan)

    import script_order

    facts = format_facts(clips_data, script_lines)
    (proj_dir / "facts.md").write_text(facts, encoding="utf-8")
    try:
        annotate_clip_voice_hints(clips_data, script_lines)
    except Exception:
        pass

    line_map_text = script_order.build_line_map(script_lines, clips_data)
    try:
        import edit_packet as _ep_mod
    except Exception:
        _ep_mod = None  # type: ignore[assignment]

    if _ep_mod is not None and hasattr(_ep_mod, "refresh_line_map"):
        _ep_mod.refresh_line_map(proj_dir, line_map_text)
    else:
        (proj_dir / "line_map.md").write_text(line_map_text, encoding="utf-8")

    try:
        import edit_brief

        edit_brief.write(proj_dir, meta, plan, style=args.style or "")
    except Exception as exc:
        print(f"  [BRIEF] fallback brief.md written ({exc})")
        _write_fallback_brief(proj_dir, meta, plan, style=args.style or "")

    try:
        import clip_sheet

        sheets = clip_sheet.build(proj_dir, [str(resolve(c["src"])) for c in clips_data])
    except Exception as exc:
        sheets = []
        print(f"  ! clip sheet not made: {exc}")

    dir_packet_path = proj_dir / "director_packet.md"
    edit_packet_path = proj_dir / "editing_packet.md"
    if _ep_mod is not None and hasattr(_ep_mod, "write"):
        dir_before_ns = dir_packet_path.stat().st_mtime_ns if dir_packet_path.is_file() else None
        edt_before_ns = edit_packet_path.stat().st_mtime_ns if edit_packet_path.is_file() else None

        pkt_written = Path(_ep_mod.write(proj_dir))
        if hasattr(_ep_mod, "write_director_packet"):
            dir_changed = dir_packet_path.is_file() and dir_packet_path.stat().st_mtime_ns != dir_before_ns
            if not dir_changed:
                _ep_mod.write_director_packet(proj_dir)

        dir_changed = dir_packet_path.is_file() and dir_packet_path.stat().st_mtime_ns != dir_before_ns
        edt_changed = edit_packet_path.is_file() and edit_packet_path.stat().st_mtime_ns != edt_before_ns
        if not dir_changed and edit_packet_path.is_file():
            _atomic_write_text(dir_packet_path, edit_packet_path.read_text(encoding="utf-8"))
        elif not edt_changed and dir_packet_path.is_file():
            _atomic_write_text(edit_packet_path, dir_packet_path.read_text(encoding="utf-8"))

        primary_pkt = dir_packet_path if dir_packet_path.is_file() else pkt_written
        print(f"[PACKET] {primary_pkt}")
        if hasattr(_ep_mod, "packet_parts"):
            parts = _ep_mod.packet_parts(proj_dir)
            if len(parts) > 1:
                print(f"[PACKET_PARTS] {' '.join(p.name for p in parts)}")
    else:
        fallback_packet = "\n\n".join(
            p.read_text(encoding="utf-8")
            for p in (proj_dir / "brief.md", proj_dir / "plan.edl", proj_dir / "line_map.md", proj_dir / "facts.md")
            if p.is_file()
        )
        _atomic_write_text(edit_packet_path, fallback_packet + "\n")
        _atomic_write_text(dir_packet_path, fallback_packet + "\n")
        print(f"[PACKET] {dir_packet_path}")

    print(f"Measured {len(clips_data)} clips. Nothing is decided yet: the Director writes plan.edl.")
    print(f"  Director packet        : {dir_packet_path}")
    print(f"  Editing packet         : {edit_packet_path}")
    print(f"  Rules for this job     : {proj_dir / 'brief.md'}")
    print(f"  Script + clips per line: {proj_dir / 'line_map.md'}")
    print(f"  Every word, pause, face: {proj_dir / 'facts.md'}")
    print(f"  Clip pictures          : {len(sheets)} sheet(s) in {proj_dir}")
    print(f"  Plan to write          : {(proj_dir / 'plan.edl').as_posix()}")


if __name__ == "__main__":
    main()
