"""Single-command deterministic pre-processor for script-led video editing.

Usage:
    python3 scripts/prepare.py [clips...] --script input/script/<script>.txt [--lang auto]
        [--project <name>] [--preset auto|single|split_v|split_h|chat_bar|video_call|hybrid]
        [--mark-pack default] [--clean-raw-json]

Performs:
1. Audio extraction & 2-tier silence + 10ms RMS energy-dip detection
   (-35dB / 0.30s macro-silences + -28dB / 0.05s micro-dips + standard-library wave 10ms RMS minima).
2. Transcription & Wav2Vec2 + uroman Forced Alignment (via align_engine or cached hq_words.json / words.json).
3. Deterministic Anomaly Detection & Auto-Cutting:
   - Leading/Trailing Silence Trim (0.12s-0.18s natural breath padding)
   - Untranscribed Tail-Babble Amputation ([TAIL_BABBLE_CUT])
   - Intra-Clip Retake & Repeat Pruning ([RETAKE], [TAIL_REPEAT]) snapped to 10ms RMS minima
   - Inter-Clip Boundary Overlap Pruning ([BOUNDARY_DUP])
   - Mid-Clip Long Pause Tightening ([PAUSE_SPLIT]) with zoom=1.08 jump-cut masking
4. Script Line Alignment & Auto-Marks ([WRONG] / [RIGHT]) + Layout Recommendation (via vision_tracker).
5. Compact Output Generation:
   - work/<project>/brief.txt (strictly <1,100 tokens)
   - work/<project>/plan.edl (compact 1-line-per-segment EDL)
   - work/<project>/plan.json (full JSON mirror)
"""
from __future__ import annotations

import argparse
import array
import glob
import json
import math
import os
import re
import subprocess
import sys
import time
import wave
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import (  # noqa: E402
    DEFAULT_SFX,
    FFMPEG,
    OUTPUT,
    ROOT,
    WORK,
    clip_work_dir,
    ffmpeg,
    find_venv_python,
    read_script_text,
    probe,
    read_json,
    resolve,
    resolve_sfx_asset,
    tc,
    write_json,
)

# Optional Phonetic / Alignment / Vision / EDL modules from sibling agents
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

import brain_memory  # noqa: E402
import style_config  # noqa: E402

_TUNING = style_config.get_tuning("micronarrative")
MACRO_SILENCE_DB = float(_TUNING["macro_silence_db"])
MACRO_SILENCE_MIN_SEC = float(_TUNING["macro_silence_min_sec"])
MICRO_DIP_DB = float(_TUNING["micro_dip_db"])
MICRO_DIP_MIN_SEC = float(_TUNING["micro_dip_min_sec"])
PAUSE_SPLIT_MONOLOGUE_SEC = float(_TUNING["pause_split_monologue_sec"])
PAUSE_SPLIT_ABSOLUTE_SEC = float(_TUNING["pause_split_dialogue_sec"])
LONG_MONOLOGUE_ZOOM_SEC = float(_TUNING["long_monologue_zoom_sec"])
MAX_ALT_TAKES_IN_BRIEF = int(_TUNING["max_alt_takes_in_brief"])
MAX_BRIEF_CHARS = int(_TUNING["max_brief_chars"])

# Common intentional Kannada/Indic reduplications (not retakes)
INTENTIONAL_REDUPLICATIONS = {
    "ಪದೆ", "ಪದೇ", "ಮತ್ತೆ", "ಮಾತಾಡ್ತ", "ಮಾತಾಡ್ತಾ", "ಎಲ್ಲೆಲ್ಲಿ", "ಯಲ್ಲಲಿ",
    "ದಿನಾಲೂ", "ಬೇಗ", "ಮೆಲ್ಲಗೆ", "ಸ್ವಲ್ಪ", "padeh", "matthe", "mathadtha",
}


def _natural_clip_sort_key(p: Path) -> Tuple[int, int, str]:
    stem = p.stem
    nums = [int(x) for x in re.findall(r"\d+", stem)]
    return (nums[0] if nums else 999999, nums[1] if len(nums) > 1 else 0, stem)


def _clean_token(w: str) -> str:
    return re.sub(r"[^\w\u0900-\u0D7F']", "", str(w or "").lower()).strip()


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
    rom = rom.replace("ph", "f").replace("bh", "b").replace("dh", "d").replace("th", "t").replace("kh", "k").replace("gh", "g")
    return rom


def _indic_skeleton(u: str) -> str:
    """Strip Indic dependent vowel signs (matras), virama, and length marks to compare consonant skeletons."""
    return re.sub(
        r"[\u093A-\u094F\u0951-\u0957\u09BC-\u09CD\u09D7\u0ABC-\u0ACD\u0B3C-\u0B4D\u0B56\u0B57\u0BBC-\u0BCD\u0BD7\u0C3E-\u0C4D\u0C55\u0C56\u0CBC-\u0CCD\u0CD5\u0CD6\u0D3B-\u0D4D\u0D57]",
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
    # Compare Indic consonant skeletons (e.g. ಹಿಗನೆ vs ಹೀಗೆನೆ -> ಹಗನ == ಹಗನ)
    sk1, sk2 = _indic_skeleton(u1), _indic_skeleton(u2)
    sim_sk = SequenceMatcher(None, sk1, sk2).ratio() * 0.92 if (len(sk1) >= 2 and len(sk2) >= 2 and sk1 != u1) else 0.0
    # Also compare vowel-skeleton lowered forms (e.g. higane vs higene)
    c1 = re.sub(r"[aeiou]", "a", p1) if len(p1) >= 4 else p1
    c2 = re.sub(r"[aeiou]", "a", p2) if len(p2) >= 4 else p2
    sim_c = SequenceMatcher(None, c1, c2).ratio() * 0.92 if (c1 and c2) else 0.0
    best = max(sim_u, sim_p * 0.95, sim_sk, sim_c)
    # Compound word check (e.g. ನಿಲ್ಸೋಧೇಗೆ containing ನಿಲ್ಲಿಸೋದು / ಹೇಗೆ)
    if best < 0.72 and len(p1) >= 3 and len(p2) >= 3 and (len(p1) >= 7 or len(p2) >= 7):
        short_p, long_p = (p1, p2) if len(p1) <= len(p2) else (p2, p1)
        if len(short_p) >= 3 and (long_p.startswith(short_p[:3]) or long_p.endswith(short_p[-3:])):
            best = max(best, 0.74)
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
    """Detect 2-tier silences:
    - Tier 1 Macro-silences (-35dB / 0.30s) via ffmpeg silencedetect or cached silences.json
    - Tier 2 Micro-dips (-28dB / 0.05s) + 10ms RMS minima from the 10ms wave envelope
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
                "-i", wav_path,
                "-af", f"silencedetect=noise={int(MACRO_SILENCE_DB)}dB:d={MACRO_SILENCE_MIN_SEC}",
                "-f", "null", "-",
            )
            starts = [float(x) for x in re.findall(r"silence_start: (-?[\d.]+)", r.stderr)]
            ends = [float(x) for x in re.findall(r"silence_end: ([\d.]+)", r.stderr)]
            macro_raw = [{"start": round(max(s, 0.0), 3), "end": round(e, 3)} for s, e in zip(starts, ends)]
            if cached_silences_path:
                write_json(cached_silences_path, macro_raw)
        except Exception:
            macro_raw = []

    # Merge contiguous or near-contiguous macro silences (gap <= 0.04s, e.g. S2 1.512-2.759 & 2.759-3.493)
    macro_merged: List[Dict[str, float]] = []
    for s in sorted(macro_raw, key=lambda x: float(x["start"])):
        st, en = float(s["start"]), float(s["end"])
        if macro_merged and st - macro_merged[-1]["end"] <= 0.04:
            macro_merged[-1]["end"] = max(macro_merged[-1]["end"], en)
        else:
            macro_merged.append({"start": round(st, 3), "end": round(en, 3)})

    # Tier 2 Micro-dips (-28dB for >= 0.05s = 5 frames of 10ms)
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
        # Once inside the noise floor (<= -42 dB), pick the first frame reaching the noise floor
        floor_cands = [(t, db) for t, db in cands if db <= -42.0]
        if floor_cands:
            best_t, best_db = floor_cands[0]
        else:
            mid = 0.5 * (t_start + t_end)
            best_t, best_db = min(cands, key=lambda x: (round(x[1] / 2.0) * 2.0, abs(x[0] - mid)))
    else:
        best_t, best_db = min(cands, key=lambda x: x[1])
    return round(best_t, 2), best_db


def detect_vocal_stress_words(
    rms_env: List[Tuple[float, float]],
    words: List[Dict[str, Any]],
    seg_start: float,
    seg_end: float,
    max_peaks: int = 2,
) -> List[Dict[str, Any]]:
    """Measure per-word 10ms RMS energy relative to the segment's median speech energy
    and return the top `max_peaks` vocal emphasis words (`delta_db >= +1.5 dB`) so the
    Editorial Brain knows the exact millisecond the actor stressed a word in any language.
    """
    if not rms_env or not words:
        return []
    seg_frames = [db for t, db in rms_env if seg_start <= t <= seg_end and db > -34.0]
    if not seg_frames:
        return []
    sorted_db = sorted(seg_frames)
    median_db = sorted_db[len(sorted_db) // 2]

    scored: List[Dict[str, Any]] = []
    for w in words:
        ws = float(w.get("start", 0.0))
        we = float(w.get("end", ws))
        wt = str(w.get("w") or "").strip()
        if we <= seg_start or ws >= seg_end or (we - ws) < 0.12 or len(_clean_token(wt)) < 2:
            continue
        w_dbs = [db for t, db in rms_env if ws <= t <= we and db > -36.0]
        if not w_dbs:
            continue
        peak_db = max(w_dbs)
        delta_db = round(peak_db - median_db, 1)
        if delta_db >= 1.5:
            scored.append({
                "w": wt,
                "start": round(ws, 2),
                "rel_t": round(max(0.0, ws - seg_start), 2),
                "delta_db": delta_db,
            })

    scored.sort(key=lambda x: x["delta_db"], reverse=True)
    return scored[:max_peaks]


# --- Script cues that are directions or on-screen text, not spoken dialogue ---------------------------------------
_URL_ONLY_RE = re.compile(r"^https?://\S+$", re.I)
_META_LINE_RE = re.compile(r"^(?:visuals?|reference(?:\s+link)?|ref\s+link|concept)\s*:", re.I)
_CHAR_DEF_RE = re.compile(r"^Character\s*\d*\s*[-–—]\s*(.+)$", re.I)
# "Scene changes: ...", "Scene changes - ...", "Back to the site, ...", "Cut to ...", "Flashback ..."
_SCENE_DIR_RE = re.compile(r"^(?:scene\s+chang(?:e|es|ed)|flash\s*back|(?:back|cut)\s+to)\b[\s:,\-–—]*(.*)$", re.I)
_LAYOUT_CUE_RE = re.compile(r"^(?:rectangle\s+overlay|floating\s+window|split[\s-]*screen|picture[\s-]*in[\s-]*picture)\b", re.I)
# a parenthetical that only describes what is happening while people talk, so it is not a silent beat
_CONTEXT_DIR_RE = re.compile(r"\b(?:is|are)\s+(?:talking|speaking|saying)\b|\btalking\s+to\b|\bwhile\b", re.I)
# "(X Sign and Text Strikethrough)" after a spoken line: show that line's text struck through in red
_STRIKE_CUE_RE = re.compile(r"\s*[\(\[][^\)\]]*strike[^\)\]]*[\)\]]\s*$", re.I)
# "[TEXT: \"phrase\" | strike | x]": explicit on-screen text cue (only the strike option is rendered so far)
_TEXT_CUE_RE = re.compile(r"\s*\[\s*TEXT\s*:\s*(?P<body>[^\]]*)\]\s*$", re.I)
# "horizontal split screen" means the divider line runs horizontally (top / bottom panels); "vertical split" is left / right
SCRIPT_SPLIT_LAYOUT = {"horizontal": "split_v", "vertical": "split_h", "default": "split_v"}


def layout_hint_from_direction(text: str) -> str:
    """Layout named in a script direction (`split_v`, `split_h`, `video_call`, `chat_bar`) or '' when it names none.
    "split" wording wins ("floating window ... more like a horizontal split screen" is a split); other wording is looked up in
    the format library (styles/formats/), and the older keyword rule stays as a fallback."""
    t = (text or "").lower()
    if re.search(r"\bsplit\b", t):
        key = "vertical" if re.search(r"\bvertical", t) else "horizontal" if re.search(r"\bhorizontal", t) else "default"
        return SCRIPT_SPLIT_LAYOUT[key]
    try:
        import format_library

        lib = format_library.best_layout(t)
    except Exception:   # a broken recipe file must never stop an edit
        lib = ""
    if lib and lib != "single":
        return lib
    if re.search(r"floating\s+window|picture[\s-]*in[\s-]*picture|\bpip\b|rectangle\s+overlay", t):
        return "video_call"
    return ""


def parse_script_file(script_path: Path) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    """Parse script file into metadata header (concept_ref, visual_ref, characters, scenes) and structured script lines L1..LN.

    Only spoken lines (and silent beats) become script lines. Reference/visual notes, character definitions, scene changes
    ("Scene changes: ...", "Back to ...") and layout notes ("floating window ... split screen") are read as directions:
    scene changes start a new scene (so a transition is placed), layout notes set `layout_hint` on the lines that follow,
    and a trailing "(X Sign and Text Strikethrough)" strips the cue and sets `strike_text` on that line."""
    raw = read_script_text(script_path)
    meta: Dict[str, Any] = {
        "title": script_path.stem,
        "language": "auto",
        "header": "",
        "concept_brief": "",
        "characters": {},
        "scenes": [],
    }
    header_lines: List[str] = []
    lines: List[Dict[str, Any]] = []

    in_characters_block = False
    current_scene = "Scene 1"
    scene_no = 1
    current_visual = ""
    current_hint = ""
    pending_speaker = ""
    pending_note = ""

    for raw_line in raw.splitlines():
        s = raw_line.strip()
        if not s:
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

        # 1. Ignore external Concept Reference / Visual Reference video URLs; only keep text concept descriptions
        m_concept = re.match(r"^(?:Concept|Visual)\s+(?:Reference|Brief)(?:\s+link)?[^:]*:\s*(.+)$", s, re.I)
        if m_concept:
            val_c = m_concept.group(1).strip()
            if not re.search(r"https?://", val_c, re.I):
                meta["concept_brief"] = (meta["concept_brief"] + " | " + val_c).strip(" |")
            continue

        # 1b. Other non-spoken header lines: a bare URL, "Reference link : ...", "Visuals: ..."
        if _URL_ONLY_RE.match(s):
            continue
        if _META_LINE_RE.match(s):
            val_m = s.split(":", 1)[1].strip()
            if re.match(r"visuals?\b", s, re.I) and val_m and not re.search(r"https?://", val_m, re.I):
                meta["concept_brief"] = (meta["concept_brief"] + " | " + val_m).strip(" |")
            continue

        # 2. Characters heading & character definitions (-- Name -- Description)
        if re.match(r"^Characters?\s*:?\s*$", s, re.I):
            in_characters_block = True
            continue
        m_char = re.match(r"^--\s*([^-]+?)\s*--\s*(.+)$", s)
        if m_char:
            cname = m_char.group(1).strip()
            cdesc = m_char.group(2).strip()
            meta["characters"][cname] = cdesc
            continue
        m_char_def = _CHAR_DEF_RE.match(s)
        if m_char_def:   # "Character 1 - A man(Suresh) in his late 20s ..." / "Character 2 - Boss, middle aged man"
            cdesc = m_char_def.group(1).strip()
            m_cname = re.search(r"\(([^)]+)\)", cdesc)
            cname = (m_cname.group(1).strip() if m_cname else re.split(r"[,(]", cdesc, 1)[0].strip()) or "Character"
            meta["characters"][cname] = cdesc
            continue

        # 3. Scene heading (e.g. "Scene 1", "SCENE 2 - Canteen", "Scene 1: An Indian man...")
        m_scene = re.match(r"^(Scene\s+\d+)(?:\s*[-:]\s*(.*))?$", s, re.I)
        if m_scene:
            in_characters_block = False
            current_scene = m_scene.group(1).strip()
            scene_no = int(re.search(r"\d+", current_scene).group())
            current_visual = (m_scene.group(2) or "").strip()
            current_hint = layout_hint_from_direction(current_visual)
            meta["scenes"].append({"scene": current_scene, "visual": current_visual})
            continue

        # 3b. Scene-change direction ("Scene changes: ...", "Back to the site, ...", "Cut to ...", "Flashback ...") starts a new scene
        m_scene_dir = _SCENE_DIR_RE.match(s)
        if m_scene_dir and not (re.match(r"^(?:back|cut)\s+to\b", s, re.I) and ":" in s):
            in_characters_block = False
            scene_no += 1
            current_scene = f"Scene {scene_no}"
            current_visual = m_scene_dir.group(1).strip(" :-–—") or s
            current_hint = layout_hint_from_direction(current_visual)
            pending_speaker = pending_note = ""
            meta["scenes"].append({"scene": current_scene, "visual": current_visual})
            continue

        # 3c. Layout note ("Rectangle overlay, floating window ..., horizontal split screen ...") applies to the lines that follow
        if _LAYOUT_CUE_RE.match(s) and not re.match(r"^[A-Za-z][A-Za-z0-9_ ]{0,22}:\s", s):
            hint_l = layout_hint_from_direction(s)
            if hint_l:
                current_hint = hint_l
                continue

        # 4. Scene visual / camera direction block: "[Visual: ...]"
        m_vis_dir = re.match(r"^\[?\s*Visual\s*:\s*(.+?)\]?\s*$", s, re.I)
        if m_vis_dir:
            in_characters_block = False
            current_visual = m_vis_dir.group(1).strip()
            if meta["scenes"]:
                meta["scenes"][-1]["visual"] = current_visual
            else:
                meta["scenes"].append({"scene": current_scene, "visual": current_visual})
            continue

        # 5. Standalone speaker line (e.g. "Miss Nova:" or "Ravi (He's speaking in pauses...):")
        m_standalone_spk = re.match(r"^([A-Za-z][A-Za-z0-9_ .]{0,24}?)\s*(?:\(([^)]*)\))?\s*:\s*$", s)
        if m_standalone_spk:
            in_characters_block = False
            pending_speaker = m_standalone_spk.group(1).strip()
            pending_note = (m_standalone_spk.group(2) or "").strip()
            continue

        in_characters_block = False

        # 6. Match L1 Speaker (optional note): text [WRONG|RIGHT]
        m_id = re.match(r"^(L\d+)\s*(?::\s*|\s+)(.*)$", s)
        if m_id:
            lid, rest = m_id.group(1), m_id.group(2).strip()
        else:
            lid = f"L{len(lines) + 1}"
            rest = s

        # On-screen text cue at the end of a spoken line: strip it so the spoken text matches the transcript
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
        m_mark = re.search(r"\[(WRONG|RIGHT)\]\s*$", rest, re.I)
        if m_mark:
            mark = m_mark.group(1).lower()
            rest = rest[: m_mark.start()].strip()
        else:
            # Also check inline [WRONG] or [RIGHT] inside a composite line
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
                # "(Suresh is talking to his Partner on his phone)" describes the scene while people talk; it is not a silent beat
                current_visual = ctx_text
                if meta["scenes"]:
                    meta["scenes"][-1]["visual"] = ctx_text
                continue
        if m_beat and not speaker:
            beat = True
            note = next(g for g in m_beat.groups() if g).strip()
            text = note
        else:
            m_spk = re.match(r"^([A-Za-z][A-Za-z0-9_ ]{0,22}?)\s*(?:\(([^)]*)\))?\s*:\s*(.+)$", rest)
            if m_spk and not m_spk.group(1).lower().startswith(("http", "concept", "visual", "scene")):
                speaker = m_spk.group(1).strip()
                note = (m_spk.group(2) or note or "").strip()
                text = m_spk.group(3).strip()
            else:
                text = rest
            # Extract inline stage directions like "(laughing)" after speaker colon into note
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
            "strike_text": (strike_override or text) if (strike_cue and not beat) else "",
        })

    # Auto-infer [WRONG] / [RIGHT] on student lines when followed by teacher feedback ("Almost", "Perfect!", "Nice!", "ನೀವು ಸಿಂಪಲ್ಲಾಗಿ")
    for idx in range(len(lines) - 1):
        curr_l = lines[idx]
        next_l = lines[idx + 1]
        if curr_l.get("mark") or len(curr_l.get("tokens", [])) > 12:
            continue
        if curr_l.get("speaker") and next_l.get("speaker") and curr_l["speaker"] != next_l["speaker"]:
            nxt_txt = next_l["text"].strip().lower()
            if nxt_txt.startswith(("almost", "ನೀವು ಸಿಂಪಲ್ಲಾಗಿ", "ಹೀಗೆ ಹೇಳಿ", "no,", "not quite")):
                curr_l["mark"] = "wrong"
            elif nxt_txt.startswith(("perfect", "nice", "great", "correct", "ನಿಜ", "ಸರಿ")):
                curr_l["mark"] = "right"

    brief_parts: List[str] = []
    if meta["characters"]:
        brief_parts.append("Characters: " + "; ".join(f"{k} ({v})" for k, v in meta["characters"].items()))
    if meta["scenes"]:
        brief_parts.append(" | ".join(f"{sc['scene']}: {sc['visual']}" if sc.get("visual") else sc["scene"] for sc in meta["scenes"]))
    if brief_parts and not meta["concept_brief"]:
        meta["concept_brief"] = " — ".join(brief_parts)
    meta["header"] = " | ".join(header_lines)
    return meta, lines


def ensure_clip_transcribed(
    clip_stem: str,
    clip_src: str,
    work_dir: Path,
    lang: str,
    script_path: Optional[Path],
    clean_raw_json: bool = True,
) -> Tuple[Dict[str, Any], List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Ensure info.json, audio16k.wav, and hq_words.json / words.json exist for a clip."""
    work_dir.mkdir(parents=True, exist_ok=True)
    info_path = work_dir / "info.json"
    wav_path = work_dir / "audio16k.wav"
    hq_path = work_dir / "hq_words.json"
    words_path = work_dir / "words.json"
    align_path = work_dir / "align.json"

    resolved_src = resolve(clip_src)
    if info_path.is_file():
        info = read_json(info_path)
    elif resolved_src.is_file():
        info = probe(resolved_src)
        write_json(info_path, info)
    else:
        info = {"path": str(clip_src), "duration": 10.0, "width": 720, "height": 1280, "fps": 24.0, "has_audio": True}

    if not wav_path.is_file() and resolved_src.is_file() and info.get("has_audio", True):
        ffmpeg("-i", resolved_src, "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", wav_path)

    # Check if alignment/transcription already exists
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

    # If neither hq_words nor base_words is cached, invoke align_engine or transcribe scripts
    if not hq_words and not base_words and wav_path.is_file():
        if align_engine is not None and hasattr(align_engine, "transcribe_and_align_clip"):
            try:
                res = align_engine.transcribe_and_align_clip(
                    wav_path,
                    work_dir=work_dir,
                    lang=lang,
                    script_path=script_path,
                )
                if isinstance(res, list):
                    hq_words = res
                elif isinstance(res, dict) and "words" in res:
                    hq_words = res["words"]
                if hq_words:
                    write_json(hq_path, hq_words)
                    write_json(words_path, hq_words)
            except Exception as exc:
                print(f"  [WARN] align_engine fallback on {clip_stem}: {exc}")

        if not hq_words and not base_words:
            # Fallback to transcribe.py
            venv_py = find_venv_python()
            cmd = [str(venv_py), str(ROOT / "scripts" / "transcribe.py"), str(resolved_src), "--lang", lang]
            if script_path:
                cmd += ["--script", str(script_path)]
            try:
                subprocess.run(cmd, check=False, capture_output=True)
                if words_path.is_file():
                    base_words = read_json(words_path)
            except Exception:
                pass

    if not hq_words:
        hq_words = base_words
    if not base_words:
        base_words = hq_words

    # Normalize word entries
    def _norm_w(wlist: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        out = []
        for item in wlist:
            txt = str(item.get("w") if "w" in item else item.get("word", "")).strip()
            if not txt:
                continue
            st = float(item.get("start") or 0.0)
            en = float(item.get("end") or st)
            out.append({"w": txt, "start": round(st, 3), "end": round(en, 3)})
        return out

    hq_words = _norm_w(hq_words)
    base_words = _norm_w(base_words)

    return info, hq_words, base_words


def _clip_tokens(clip: Dict[str, Any]) -> List[str]:
    return [_clean_token(w["w"]) for w in clip.get("hq_words", []) if _clean_token(w["w"])]


def _same_content(a: List[str], b: List[str]) -> float:
    """How much of the shorter clip's speech appears, in the same order, in the longer one (0..1).
    Order matters: a retake repeats the same words in the same order, while two different lines that merely share
    some words ('the', 'charts', 'bigger') do not. File names are never used."""
    if not a or not b:
        return 0.0
    short, long_ = (a, b) if len(a) <= len(b) else (b, a)
    # canonical word ids: spellings that sound alike get one id, so small transcription differences do not matter
    canon: List[str] = []
    ids: Dict[str, int] = {}
    def wid(tok: str) -> int:
        for k, rep in enumerate(canon):
            if tok == rep or _word_similarity(tok, rep) >= 0.82:
                return k
        canon.append(tok)
        return len(canon) - 1
    sa, sb = [wid(t) for t in short], [wid(t) for t in long_]
    blocks = SequenceMatcher(None, sa, sb, autojunk=False).get_matching_blocks()
    # tiny scattered matches (a lone 'the') should not count: only runs of 2+ words, or the whole clip
    matched = sum(bl.size for bl in blocks if bl.size >= 2) if len(short) >= 4 else sum(bl.size for bl in blocks)
    return matched / len(short)


SAME_TAKE_THRESHOLD = 0.72


def group_takes(clips_data: List[Dict[str, Any]]) -> None:
    """Group clips that say the same thing, in any order and under any file names, so only the best one is used.
    Every clip is compared with every other clip (not just its neighbour). Sets clip["take_group"] to the index of
    the first clip of its group."""
    n = len(clips_data)
    parent = list(range(n))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    toks = [_clip_tokens(c) for c in clips_data]
    for i in range(n):
        for j in range(i + 1, n):
            if min(len(toks[i]), len(toks[j])) >= 2 and _same_content(toks[i], toks[j]) >= SAME_TAKE_THRESHOLD:
                ri, rj = find(i), find(j)
                if ri != rj:
                    parent[max(ri, rj)] = min(ri, rj)
    for i, c in enumerate(clips_data):
        c["take_group"] = find(i)


def match_clips_to_script_lines(
    clips_data: List[Dict[str, Any]],
    script_lines: List[Dict[str, Any]],
) -> None:
    """Group takes by content, then map the distinct groups, in order, to script lines L1..LM.
    Every take in a group gets the group's script lines; select_best_takes() later keeps only the best one."""
    if not clips_data:
        return
    beat_lines = [l for l in script_lines if l.get("beat")]
    speech_lines = [l for l in script_lines if not l.get("beat")]
    short_speech_lines = [l for l in speech_lines if 1 <= len(l.get("tokens", [])) <= 3]
    # clips with (almost) no speech carry the script's silent beats, or serve as listening reaction clips (`peer=`)
    def _is_silent(c: Dict[str, Any]) -> bool:
        ws = c.get("hq_words") or []
        dur = float((c.get("info") or {}).get("duration") or 1.0)
        rms_env = c.get("rms_env") or []
        if rms_env:
            dbs = sorted(db for _, db in rms_env)
            n_db = len(dbs)
            rms_p50 = dbs[n_db // 2]
            rms_p90 = dbs[min(n_db - 1, int(n_db * 0.90))]
            active_frac = sum(1 for db in dbs if db > -28.0) / max(1, n_db)
            # Stationary ambient noise guard (e.g. 6.mp4 airplane cabin hum: p90-p50=3.3dB <= 4.2dB, active_frac=0.99 >= 0.90)
            if (rms_p90 - rms_p50) <= 4.2 and active_frac >= 0.90:
                if ws:
                    c["anomalies"].append(
                        f"[AMBIENT_NOISE_BEAT] {c['stem']}: stationary noise (p90-p50={rms_p90 - rms_p50:.1f}dB, "
                        f"active={active_frac:.2f}) -> cleared {len(ws)} echoed word(s)"
                    )
                    c["hq_words"] = []
                    c["base_words"] = []
                    wdir_c = c.get("work_dir")
                    if wdir_c:
                        for fn in ("hq_words.json", "words.json"):
                            try:
                                write_json(Path(wdir_c) / fn, [])
                            except Exception:
                                pass
                return True
        if not ws:
            return True
        speech_sec = sum(float(w["end"]) - float(w["start"]) for w in ws)
        speech_ratio = speech_sec / max(0.1, dur)
        if speech_ratio < 0.08:
            return True
        # Protect 1-2 word spoken script lines (e.g. 4.mp4 "Whatever!", 0.79s, matches L4)
        clip_toks = [_clean_token(w["w"]) for w in ws if _clean_token(w["w"])]
        if clip_toks and any(_token_recall(sl["tokens"], clip_toks) >= 0.70 for sl in short_speech_lines):
            return False
        return len(ws) <= 2 and speech_sec < 0.35

    all_silent = [c for c in clips_data if _is_silent(c)]
    for c in all_silent:
        c["is_listening"] = True
    silent = all_silent[:len(beat_lines)] if beat_lines else []
    for c, bl in zip(silent, beat_lines):
        c["matched_lines"] = [bl]
        c["is_beat"] = True
        c["take_group"] = id(c)
    for c in all_silent[len(beat_lines):]:
        c["matched_lines"] = []
        c["dropped_take"] = True
        c["take_group"] = id(c)
        c["anomalies"].append(f"[LISTENING_CLIP] {c['stem']} detected as silent listening reaction clip (auto-paired via peer=)")
    for bl in beat_lines[len(silent):]:
        if clips_data:
            clips_data[0]["anomalies"].append(f"[BEAT_NO_CLIP] {bl['id']} ({bl['text'][:40]}): no silent clip for this beat; add the clip or accept the gap")
    clips_data = [c for c in clips_data if not c.get("is_beat") and not c.get("is_listening")]
    script_lines = speech_lines
    if not clips_data:
        return
    group_takes(clips_data)
    if not script_lines:
        for c in clips_data:
            c["matched_lines"] = []
        return
    groups: Dict[int, List[Dict[str, Any]]] = {}
    for c in clips_data:
        groups.setdefault(c["take_group"], []).append(c)
    # One stand-in per group, placed where the group first appears. The most complete take stands in for it,
    # because the fullest transcript shows best which script lines the group covers.
    stand_ins = []
    for gid in sorted(groups):
        members = groups[gid]
        best = max(members, key=lambda m: len(_clip_tokens(m)))
        stand_ins.append(best)
    _match_sequence(stand_ins, script_lines)
    for gid, members in groups.items():
        rep = next(m for m in members if m in stand_ins)
        for m in members:
            m["matched_lines"] = list(rep.get("matched_lines", []))


def _match_sequence(
    clips_data: List[Dict[str, Any]],
    script_lines: List[Dict[str, Any]],
) -> None:
    """Deterministically map ordered clips (one per distinct take group) to ordered script lines L1..LM,
    supporting multi-speaker clips that contain [prev line tail] + [short line(s)] + [next line head]."""
    if not script_lines or not clips_data:
        return

    line_ptr = 0
    n_lines = len(script_lines)
    # Track remaining unconsumed tokens of each script line for multi-clip lines
    remaining_tokens: Dict[int, List[str]] = {
        idx: list(sline["tokens"]) for idx, sline in enumerate(script_lines)
    }
    for c_idx, clip in enumerate(clips_data):
        words = clip["hq_words"]
        bwords = clip["base_words"]
        clip_tokens = [_clean_token(w["w"]) for w in words if _clean_token(w["w"])]
        # Strip immediate phrase/word repeats from clip_tokens when estimating how many lines fit in clip
        for plen in (5, 4, 3, 1):
            if len(clip_tokens) >= 2 * plen and clip_tokens[-2 * plen : -plen] == clip_tokens[-plen:]:
                clip_tokens = clip_tokens[:-plen]
                break
        base_tokens = [_clean_token(w["w"]) for w in bwords if _clean_token(w["w"]) and w["start"] <= (words[-1]["end"] if words else 10.0)]
        all_clip_tokens = clip_tokens + [t for t in base_tokens if t not in clip_tokens]

        matched_lines: List[Dict[str, Any]] = []
        consumed_words_est = 0

        # 1. Check if previous multi-clip line still has significant unconsumed tail tokens in this clip
        if line_ptr > 0:
            prev_idx = line_ptr - 1
            rem = remaining_tokens.get(prev_idx, [])
            if len(rem) >= 4:
                nxt_toks = script_lines[line_ptr]["tokens"] if line_ptr < n_lines else []
                distinct_rem = [rt for rt in rem if max((_word_similarity(rt, nt) for nt in nxt_toks), default=0.0) < 0.75] or rem
                rem_hits = sum(
                    1.0 for rt in distinct_rem if max((_word_similarity(rt, ct) for ct in all_clip_tokens), default=0.0) >= 0.62
                )
                if rem_hits / max(1, len(distinct_rem)) >= 0.35:
                    matched_lines.append(script_lines[prev_idx])
                    consumed_words_est += int(round(rem_hits))
                    prev_window = clip_tokens[: min(len(clip_tokens), int(round(rem_hits)) + 3)] or all_clip_tokens
                    new_rem = [
                        rt for rt in rem if max((_word_similarity(rt, ct) for ct in prev_window), default=0.0) < 0.65
                    ]
                    remaining_tokens[prev_idx] = new_rem
                    if len(new_rem) >= 5 or len(clip_tokens) < consumed_words_est + 2:
                        clip["matched_lines"] = matched_lines
                        continue

        # 2. Match one or more script lines starting at line_ptr
        for probe_idx in range(line_ptr, min(n_lines, line_ptr + 4)):
            sline = script_lines[probe_idx]
            stokens = remaining_tokens.get(probe_idx) or sline["tokens"]
            if not stokens:
                continue

            rem_clip_tokens = clip_tokens[max(0, consumed_words_est - 1) :] if consumed_words_est > 0 else all_clip_tokens
            if not rem_clip_tokens:
                break

            hits_s = sum(
                max((_word_similarity(st, ct) for ct in rem_clip_tokens), default=0.0)
                for st in stokens
                if max((_word_similarity(st, ct) for ct in rem_clip_tokens), default=0.0) >= 0.64
            )
            recall_s = hits_s / max(1, len(stokens))

            head_tokens = stokens[: min(5, len(stokens))]
            head_hits = sum(
                1.0 for ht in head_tokens if max((_word_similarity(ht, ct) for ct in rem_clip_tokens), default=0.0) >= 0.65
            )
            head_recall = head_hits / max(1, len(head_tokens))

            if matched_lines:
                if len(clip_tokens) < consumed_words_est + 2:
                    break
                if not (recall_s >= 0.45 or (head_recall >= 0.60 and hits_s >= 4.0)):
                    break

            if (
                recall_s >= 0.40
                or (head_recall >= 0.50 and len(stokens) >= 8)
                or (matched_lines and head_recall >= 0.60 and hits_s >= 4.0)
            ):
                matched_lines.append(sline)
                unmatched = [
                    st for st in stokens if max((_word_similarity(st, ct) for ct in rem_clip_tokens), default=0.0) < 0.66
                ]
                remaining_tokens[probe_idx] = unmatched
                consumed_words_est += max(1, len(stokens) - len(unmatched))
                line_ptr = probe_idx + 1
                if len(unmatched) >= 5:
                    break
            else:
                break

        if not matched_lines:
            # Clips may not be in script order (any file names): look for the best-matching line anywhere in the script
            best_i, best_r = -1, 0.0
            for li, sl in enumerate(script_lines):
                r = _token_recall(sl["tokens"], all_clip_tokens)
                if r > best_r:
                    best_i, best_r = li, r
            if best_i >= 0 and best_r >= 0.45:
                matched_lines = [script_lines[best_i]]
                remaining_tokens[best_i] = []
                if best_i >= line_ptr:
                    line_ptr = best_i + 1
        if not matched_lines and line_ptr < n_lines:
            matched_lines = [script_lines[line_ptr]]
            remaining_tokens[line_ptr] = []
            line_ptr += 1
        elif not matched_lines and n_lines > 0:
            matched_lines = [script_lines[-1]]

        clip["matched_lines"] = matched_lines



def _token_recall(script_tokens: List[str], clip_tokens: List[str], threshold: float = 0.64) -> float:
    """Share of script tokens heard in the clip (similarity-weighted)."""
    if not script_tokens:
        return 0.0
    total = 0.0
    for st in script_tokens:
        best = max((_word_similarity(st, ct) for ct in clip_tokens), default=0.0)
        if best >= threshold:
            total += best
    return total / len(script_tokens)


def _take_score(clip: Dict[str, Any]) -> Tuple[float, float, float]:
    """(score, recall, precision) of one take against its script line(s).
    recall    = how much of the script line was said
    precision = how much of what was said belongs to the script (fumbles, ad-libs and extra words lower it).
    Also rewards cleaner acoustic SNR (p90 - p10) and penalizes EOF tail-clipped takes."""
    script_tokens = [t for l in clip.get("matched_lines", []) for t in l["tokens"]]
    wrong = clip.get("wrong_word_idx") or set()   # words the wrong character says will be cut, so they earn no credit
    words = clip.get("hq_words", [])
    clip_tokens = [_clean_token(w["w"]) for i, w in enumerate(words) if i not in wrong and _clean_token(w["w"])]
    if not clip_tokens:
        return 0.0, 0.0, 0.0
    if not script_tokens:   # no script to compare with: judge by how much clean speech the take has
        uniq = len(set(clip_tokens)) / max(1, len(clip_tokens))
        return round(len(clip_tokens) * uniq / 100.0, 4), 0.0, round(uniq, 4)
    recall = _token_recall(script_tokens, clip_tokens)
    precision = _token_recall(clip_tokens, script_tokens)
    bonus = 0.0
    rms_env = clip.get("rms_env") or []
    if len(rms_env) >= 20:
        dbs = sorted(db for _, db in rms_env)
        snr = dbs[int(len(dbs) * 0.90)] - dbs[int(len(dbs) * 0.10)]
        bonus += min(0.05, max(0.0, (snr - 20.0) * 0.003))
    dur = float((clip.get("info") or {}).get("duration") or 0.0)
    if dur > 0 and words and float(words[-1]["end"]) >= dur - 0.03:
        bonus -= 0.04
    return round(recall + 0.35 * precision + bonus, 4), recall, precision


def _speech_density(clip: Dict[str, Any]) -> float:
    """Words per second of clip time: higher means tighter delivery with less dead air."""
    dur = float((clip.get("info") or {}).get("duration") or 0.0)
    return len(clip.get("hq_words", [])) / dur if dur > 0 else 0.0


def select_best_takes(clips_data: List[Dict[str, Any]]) -> None:
    """When two or more clips say the same thing (takes of one line, or the same audio in several files, whatever
    the files are called and wherever they sit in the list), keep exactly ONE: the best.
    Every clip has already been transcribed and is scored against the script. If scores are within 0.02 the
    tighter delivery wins (more words per second), then the later take. Unused takes stay in the brief with their
    scores, so the agent can swap one in by editing plan.edl."""
    groups: Dict[int, List[Dict[str, Any]]] = {}
    for c in clips_data:
        groups.setdefault(c.get("take_group", id(c)), []).append(c)
    for members in groups.values():
        if len(members) < 2:
            continue
        scored = [(c, *_take_score(c)) for c in members]
        top = max(s[1] for s in scored)
        contenders = [s for s in scored if s[1] >= top - 0.02]
        best = max(contenders, key=lambda s: (_speech_density(s[0]) if len(contenders) > 1 else 0.0, clips_data.index(s[0])))
        lines = ",".join(l["id"] for l in best[0].get("matched_lines", [])) or "-"
        summary = ", ".join(f"{c['stem']} {r * 100:.0f}% said/{p * 100:.0f}% on-script" for c, _, r, p in scored)
        for c, sc, r, p in scored:
            if c is best[0]:
                c["anomalies"].append(f"[BEST_TAKE] {lines}: kept {c['stem']} of {len(scored)} takes ({summary})")
            else:
                c["dropped_take"] = True
                c["anomalies"].append(
                    f"[ALT_TAKE] {lines}: not used, {best[0]['stem']} is the best of {len(scored)} ({summary}). "
                    f"To use this take instead, put {c['stem']} in that line's segment in plan.edl"
                )


def _expected_speakers(clip: Dict[str, Any]) -> List[Optional[str]]:
    """Which character the script says speaks each word of this clip (words follow the clip's matched lines in order)."""
    lines = clip.get("matched_lines") or []
    words = clip.get("hq_words") or []
    if not lines:
        return [None] * len(words)
    if len(lines) == 1:
        return [lines[0].get("speaker") or None] * len(words)
    out: List[Optional[str]] = []
    ptr = 0
    cur_line_hits = 0
    for wi, w in enumerate(words):
        tok = _clean_token(w["w"])
        gap = (float(w["start"]) - float(words[wi - 1]["end"])) if wi > 0 else 0.0
        if ptr + 1 < len(lines):
            nxt_line = lines[ptr + 1]
            nxt_head = nxt_line["tokens"][: min(4, len(nxt_line["tokens"]))]
            nxt_head_sim = max((_word_similarity(tok, st) for st in nxt_head), default=0.0)
            cur_tail = lines[ptr]["tokens"][-min(6, len(lines[ptr]["tokens"])) :]
            cur_tail_sim = max((_word_similarity(tok, st) for st in cur_tail), default=0.0)
            if nxt_head_sim >= 0.72 and (gap >= 0.25 or cur_line_hits >= 2 or nxt_head_sim > cur_tail_sim + 0.05):
                ptr += 1
                cur_line_hits = 0
        cur_line_hits += 1
        out.append(lines[ptr].get("speaker") or None)
    return out


def apply_speaker_check(clips_data: List[Dict[str, Any]], script_lines: List[Dict[str, Any]]) -> None:
    """Find dialogue spoken by the wrong character (script says Man, voice is Robot, or the reverse) and mark it to be cut.
    Sets clip["exclusions"] (time spans to cut) and clip["wrong_word_idx"]. Needs 2+ named speakers in the script.
    Never amputates legitimate second-speaker turns inside multi-speaker two-shot clips."""
    speakers = {l.get("speaker") for l in script_lines if l.get("speaker")}
    if len(speakers) < 2:
        return
    try:
        import speaker_check
    except Exception as exc:   # librosa/soundfile missing: say so, never fail the job
        print(f"  Speaker check skipped: {exc}")
        return
    items = []
    for i, c in enumerate(clips_data):
        wav = Path(c["work_dir"]) / "audio16k.wav"
        if wav.is_file() and c.get("hq_words") and c.get("matched_lines"):
            items.append({"id": i, "wav": wav, "words": c["hq_words"], "expected": _expected_speakers(c), "rms_env": c.get("rms_env") or []})
    res = speaker_check.analyze(items)
    if not res["ok"]:
        msg = f"[SPEAKER_CHECK] not applied: {res['reason']}"
        print(f"  Speaker check: {res['reason']}")
        if clips_data:
            clips_data[0]["anomalies"].append(msg)
        return
    n_cut = 0
    for i, info in res["clips"].items():
        c = clips_data[i]
        matched = c.get("matched_lines") or []
        clip_speakers = {l.get("speaker") for l in matched if l.get("speaker")}
        # Guard 1: If clip legitimately covers 2+ speakers, never amputate either speaker's words
        if len(clip_speakers) >= 2:
            continue
        dur = float(c["info"].get("duration") or 10.0)
        for sp in info["spans"]:
            # Guard 1b: Never amputate the entire clip when its words match its assigned script line (>=60% recall)
            if sp["first"] == 0 and sp["last"] >= len(c["hq_words"]) - 1 and matched:
                c_toks = [_clean_token(w["w"]) for w in c["hq_words"] if _clean_token(w["w"])]
                if _token_recall(matched[0]["tokens"], c_toks) >= 0.60:
                    continue
            # Guard 2: Check if the heard speaker's span actually matches a nearby script line of another speaker
            sp_words = c["hq_words"][sp["first"] : sp["last"] + 1]
            sp_toks = [_clean_token(w["w"]) for w in sp_words if _clean_token(w["w"])]
            matched_other_line = next(
                (
                    sl for sl in script_lines
                    if sl.get("speaker") and sl.get("speaker") != sp["expected"]
                    and _token_recall(sl["tokens"], sp_toks) >= 0.45
                ),
                None,
            )
            if matched_other_line is not None:
                if matched_other_line not in matched:
                    matched.append(matched_other_line)
                    matched.sort(key=lambda l: int(l.get("index", 0)))
                continue

            end = dur if sp["end"] is None else sp["end"]
            lid = ",".join(l["id"] for l in matched) or "-"
            c.setdefault("exclusions", []).append((float(sp["start"]), float(end)))
            c.setdefault("wrong_word_idx", set()).update(range(sp["first"], sp["last"] + 1))
            c["anomalies"].append(
                f"[WRONG_SPEAKER] {lid}: script says {sp['expected']}, but {sp['heard']} speaks '{sp['text'][:48]}' "
                f"-> cut {sp['start']:.2f}-{end:.2f}s, joined to the next good part"
            )
            n_cut += 1
    print(f"  Speaker check: {len(speakers)} voices told apart (d'={res['dprime']}, {res['agreement']:.0%} match the script); {n_cut} wrong-speaker span(s) cut")


def cut_clips(clips: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """detect_clip_anomalies_and_cuts, then remove wrong-speaker spans from the kept segments and join the good parts."""
    segs = detect_clip_anomalies_and_cuts(clips)
    out: List[Dict[str, Any]] = []
    for seg in segs:
        ex = sorted((seg.get("clip") or {}).get("exclusions") or [])
        if not ex:
            out.append(seg)
            continue
        pieces = [(float(seg["start"]), float(seg["end"]))]
        for xs, xe in ex:
            nxt = []
            for a, b in pieces:
                if xe <= a or xs >= b:
                    nxt.append((a, b))
                    continue
                if xs - a >= 0.3:
                    nxt.append((a, xs))
                if b - xe >= 0.3:
                    nxt.append((xe, b))
            pieces = nxt
        for n, (a, b) in enumerate(pieces):
            part = dict(seg)
            part["start"], part["end"] = round(a, 2), round(b, 2)
            part["words"] = [w for w in seg.get("words", []) if a - 0.05 <= float(w["start"]) <= b + 0.05]
            if (a, b) != (float(seg["start"]), float(seg["end"])):
                part["reason"] = f"{seg.get('reason', 'keep')}+wrong_speaker_cut"
                if n > 0 and float(part.get("zoom", 1.0)) == 1.0:
                    part["zoom"] = 1.08          # hides the jump cut where the good parts are joined
                    part.setdefault("focus", [0.5, 0.4])
            out.append(part)
    return out


def _seg_tokens(seg: Dict[str, Any]) -> List[str]:
    ws = seg.get("words")
    if ws is None:
        ws = [w for w in seg["clip"].get("hq_words", []) if float(seg["start"]) - 0.05 <= float(w["start"]) <= float(seg["end"]) + 0.05]
    return [_clean_token(w["w"]) for w in ws if _clean_token(w["w"])]


def fill_lines_lost_to_wrong_speaker(
    raw_segments: List[Dict[str, Any]],
    clips_data: List[Dict[str, Any]],
    script_lines: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """After wrong-speaker parts were cut, some script lines may have no good audio left in the kept clips
    (the Robot's line was only ever spoken by the Man there). Look in EVERY clip, including unused takes, for that line
    spoken by the right character, cut it out and splice it in at the right place in the script order.
    Returns the clips that were used as fill-ins."""
    cut_clips_ = [c for c in clips_data if c.get("exclusions")]
    if not cut_clips_:
        return []
    # which script lines does each kept segment really contain?
    seg_lines: List[List[int]] = []
    covered: set = set()
    for seg in raw_segments:
        toks = _seg_tokens(seg)
        got = [l for l in seg["clip"].get("matched_lines", []) if _token_recall(l["tokens"], toks) >= 0.5]
        seg_lines.append([l["index"] for l in got])
        covered.update(l["index"] for l in got)
        if seg["clip"].get("exclusions"):
            seg["lines_override"] = got
    wanted = {}
    for c in cut_clips_:
        for l in c.get("matched_lines", []):
            if l["index"] not in covered:
                wanted[l["index"]] = l
    used: List[Dict[str, Any]] = []
    for idx in sorted(wanted):
        line = wanted[idx]
        best = None   # (score, clip, i0, j0)
        for c in clips_data:
            words = c.get("hq_words") or []
            bad = c.get("wrong_word_idx") or set()
            toks = [_clean_token(w["w"]) for w in words]
            lt = line["tokens"]
            if len(lt) < 2 or not words:
                continue
            head, tail = lt[: min(3, len(lt))], lt[-min(3, len(lt)):]
            starts = [i for i in range(len(words)) if toks[i] and max((_word_similarity(toks[i], h) for h in head), default=0) >= 0.7]
            ends = [j for j in range(len(words)) if toks[j] and max((_word_similarity(toks[j], t) for t in tail), default=0) >= 0.7]
            for i0 in starts:
                js = [j for j in ends if j > i0 and j - i0 + 1 <= int(len(lt) * 1.6) + 2]
                if not js:
                    continue
                j0 = max(js)
                if any(k in bad for k in range(i0, j0 + 1)):
                    continue
                win = [t for t in toks[i0:j0 + 1] if t]
                rec, prec = _token_recall(lt, win), _token_recall(win, lt)
                if rec < 0.6:
                    continue
                score = rec + 0.35 * prec
                if best is None or score > best[0] + 1e-9 or (abs(score - best[0]) <= 0.02 and clips_data.index(c) > clips_data.index(best[1])):
                    best = (score, c, i0, j0)
        if best is None:
            who = line.get("speaker") or "the right character"
            for c in cut_clips_:
                if any(l["index"] == idx for l in c.get("matched_lines", [])):
                    c["anomalies"].append(f"[WRONG_SPEAKER_NO_FIX] {line['id']}: only the wrong voice says it; no clip has {who} saying it. Reshoot or accept the gap")
                    break
            print(f"  ! {line['id']} ({line.get('speaker') or '?'}) has no correct take anywhere in the footage")
            continue
        _, donor, i0, j0 = best
        words = donor["hq_words"]
        env = donor.get("rms_env") or []
        dur = float(donor["info"].get("duration") or 10.0)
        a = round(max(0.0, words[i0]["start"] - 0.12), 2) if i0 == 0 else find_rms_minimum(env, float(words[i0 - 1]["end"]), float(words[i0]["start"]))[0]
        b = round(min(dur, words[j0]["end"] + 0.15), 2) if j0 == len(words) - 1 else find_rms_minimum(env, float(words[j0]["end"]), float(words[j0 + 1]["start"]))[0]
        seg = {"clip": donor, "src": donor["src"], "stem": donor["stem"], "start": a, "end": max(b, a + 0.3),
               "words": words[i0:j0 + 1], "zoom": 1.0, "reason": "fill_wrong_speaker", "lines_override": [line]}
        pos = len(raw_segments)
        for si, sl in enumerate(seg_lines):
            if sl and min(sl) > idx:
                pos = si
                break
        raw_segments.insert(pos, seg)
        seg_lines.insert(pos, [idx])
        covered.add(idx)
        donor["anomalies"].append(f"[FILL] {line['id']} ({line.get('speaker') or '?'}): {donor['stem']} {a:.2f}-{seg['end']:.2f}s spliced in because the wrong character spoke it elsewhere")
        if donor not in used:
            used.append(donor)
    return used


REACTION_RE = re.compile(r"laugh|chuckl|giggl|smirk|cry|cries|crying|sob|tears|sigh|gasp|shock|smile|grin|react|scream|hug|cheer", re.I)
EMOTION_RE = re.compile(r"cry|cries|crying|sob|tears|sad|heartbroken|emotional|overwhelm|longing|lost in thought|hurt|embarrass|hides? (?:his|her|their)? ?face|looks? down", re.I)
PAUSE_NOTE_RE = re.compile(r"pause|silen|stare|staring|awkward", re.I)


def _beat_segment(clip: Dict[str, Any]) -> Dict[str, Any]:
    """Pick the part of a silent clip that carries the script's beat: the most active stretch by picture movement.
    Emotional moments keep only their peak (about 2 s) with a slow push-in; other beats keep up to 3 s."""
    line = (clip.get("matched_lines") or [{}])[0]
    note = line.get("note") or line.get("text") or ""
    dur = float(clip["info"].get("duration") or 3.0)
    emotional = bool(EMOTION_RE.search(note))
    length = 2.0 if emotional else 3.0
    try:
        import motion
        src = resolve(clip["src"])
        curve = motion.motion_curve(src) if src.is_file() else []
        start, end = motion.best_window(curve, length, dur)
    except Exception:
        start, end = 0.0, round(min(dur, length), 2)
    clip["anomalies"].append(
        f"[BEAT] {line.get('id', '?')} '{note[:36]}': kept {start:.2f}-{end:.2f}s of {dur:.1f}s (most movement)"
        + (", slow push-in" if emotional else "")
    )
    seg = {"clip": clip, "src": clip["src"], "stem": clip["stem"], "start": start, "end": end,
           "words": [], "zoom": 1.0, "reason": "beat"}
    if emotional:
        seg["zoom_to"] = 1.08
    return seg


def detect_clip_anomalies_and_cuts(
    clips_data: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Run all 5 deterministic anomaly detectors across clips and return segment dicts."""

    # 1. Inter-Clip Boundary Overlap Pruning ([BOUNDARY_DUP])
    for i in range(len(clips_data) - 1):
        curr_c = clips_data[i]
        next_c = clips_data[i + 1]
        w_curr = curr_c["hq_words"]
        w_next = next_c["hq_words"]
        if not w_curr or not w_next:
            continue
        last_w = w_curr[-1]["w"]
        first_w = w_next[0]["w"]
        sim = _word_similarity(last_w, first_w)
        if sim >= 0.80 and len(_clean_token(last_w)) >= 3:
            dup_end = w_next[0]["end"]
            next_c["anomalies"].append(
                f"[BOUNDARY_DUP] '{first_w}' (0.00-{dup_end:.2f}s duplicates {curr_c['stem']} tail '{last_w}')"
            )
            next_c["boundary_dup_until"] = dup_end

    # 2. Per-clip leading/trailing trim, [TAIL_BABBLE_CUT], [RETAKE], [TAIL_REPEAT], [PAUSE_SPLIT]
    raw_segments: List[Dict[str, Any]] = []

    for clip in clips_data:
        stem = clip["stem"]
        src = clip["src"]
        dur = float(clip["info"].get("duration") or 10.0)
        words = list(clip["hq_words"])
        base_words = list(clip["base_words"])
        silences = clip["macro_silences"]
        rms_env = clip["rms_env"]
        matched_lines = clip.get("matched_lines", [])
        script_text_combined = " ".join(l["text"] for l in matched_lines).lower()

        # Apply [BOUNDARY_DUP] trim if flagged at start of clip
        dup_until = float(clip.get("boundary_dup_until") or 0.0)
        if dup_until > 0.0:
            words = [w for w in words if w["start"] >= dup_until - 0.05]

        if clip.get("is_beat"):
            raw_segments.append(_beat_segment(clip))
            continue

        if not words:
            continue

        # Check for Intra-Clip Head Phrase Repeat ([HEAD_REPEAT] e.g. 9.mp4 "నాకు అర్థమైంది. నాకు అర్థమైంది,")
        head_repeat_cut: Optional[float] = None
        for plen in (5, 4, 3, 2):
            if len(words) >= 2 * plen:
                head_p1 = [_clean_token(w["w"]) for w in words[:plen]]
                head_p2 = [_clean_token(w["w"]) for w in words[plen : 2 * plen]]
                if head_p1 == head_p2 and all(head_p1):
                    p1_end = float(words[plen - 1]["end"])
                    p2_start = float(words[plen]["start"])
                    min_t, _ = find_rms_minimum(rms_env, max(0.0, p1_end - 0.08), p2_start + 0.05, prefer_center=False)
                    head_repeat_cut = round(max(p1_end, min(p2_start - 0.12, min_t)), 2)
                    rep_str = " ".join(w["w"] for w in words[:plen])
                    clip["anomalies"].append(
                        f"[HEAD_REPEAT] '{rep_str}' (0.00-{p1_end:.2f}s false-start cut at {head_repeat_cut:.2f}s)"
                    )
                    words = words[plen:]
                    break

        # Check for stray 1-word boundary bleed / false-start at head ([HEAD_BLEED_CUT] e.g. 10.mp4 'నండి.', 12.mp4 'చాలు.')
        if head_repeat_cut is None and dup_until == 0.0 and len(words) >= 3 and matched_lines:
            w0_tok = _clean_token(words[0]["w"])
            w1_tok = _clean_token(words[1]["w"])
            first_line_toks = matched_lines[0].get("tokens", [])
            all_line_toks = [t for ml in matched_lines for t in ml.get("tokens", [])]
            w0_sim_all = max((_word_similarity(w0_tok, st) for st in all_line_toks), default=0.0)
            w0_sim_head = max((_word_similarity(w0_tok, st) for st in first_line_toks[:3]), default=0.0)
            w1_sim_head = max((_word_similarity(w1_tok, st) for st in first_line_toks[:3]), default=0.0)
            head_gap = float(words[1]["start"]) - float(words[0]["end"])
            if float(words[0]["start"]) <= 0.35 and (
                (w0_sim_all < 0.50 and float(words[1]["start"]) >= 0.55)
                or (head_gap >= 0.45 and w0_sim_head < 0.55 and w1_sim_head >= 0.80)
            ):
                min_t, _ = find_rms_minimum(
                    rms_env, float(words[0]["end"]), float(words[1]["start"]), prefer_center=False
                )
                head_repeat_cut = round(max(float(words[0]["end"]), min(float(words[1]["start"]) - 0.12, min_t)), 2)
                clip["anomalies"].append(
                    f"[HEAD_BLEED_CUT] '{words[0]['w']}' ({words[0]['start']:.2f}-{words[0]['end']:.2f}s cut at {head_repeat_cut:.2f}s)"
                )
                words = words[1:]

        # Determine initial clip start (Leading Silence Trim)
        clip_start = 0.0
        if head_repeat_cut is not None:
            clip_start = head_repeat_cut
        elif dup_until > 0.0:
            # Find silence immediately after the boundary duplicate word
            post_dup_sil = next(
                (s for s in silences if s["start"] <= dup_until + 0.15 and s["end"] >= dup_until),
                None,
            )
            if post_dup_sil:
                clip_start = round(max(dup_until, post_dup_sil["end"] - 0.11), 2)
            else:
                clip_start = round(max(dup_until, words[0]["start"] - 0.12), 2)
        else:
            # Check for leading silence starting near 0.0 (start <= 0.35s and end >= 0.38s)
            lead_sil = next(
                (s for s in silences if s["start"] <= 0.35 and s["end"] >= 0.38 and s["end"] <= words[0]["end"] + 0.25),
                None,
            )
            if lead_sil:
                # Derive natural breath lead-in dynamically from the clip's own 10ms RMS envelope
                min_t, _ = find_rms_minimum(
                    rms_env,
                    max(0.0, lead_sil["end"] - 0.25),
                    words[0]["start"],
                    prefer_center=False,
                )
                onset_t = next((t for t, db in rms_env if t >= min_t and db > -32.0), lead_sil["end"])
                clip_start = round(max(0.0, min(onset_t - 0.14, min_t, lead_sil["end"] - 0.11)), 2)

        # Check for Intra-Clip Phrase Repeat at Tail ([TAIL_REPEAT] e.g. S2 "Do you want coffee? Do you want coffee?")
        phrase_repeat_cut: Optional[float] = None
        n_w = len(words)
        for plen in (5, 4, 3):
            if n_w >= 2 * plen:
                tail_p1 = [_clean_token(w["w"]) for w in words[n_w - 2 * plen : n_w - plen]]
                tail_p2 = [_clean_token(w["w"]) for w in words[n_w - plen :]]
                if tail_p1 == tail_p2 and all(tail_p1):
                    p1_end_word = words[n_w - plen - 1]
                    p2_start_word = words[n_w - plen]
                    # Snap to silence between phrase 1 and phrase 2
                    mid_sil = next(
                        (s for s in silences if p1_end_word["start"] <= s["start"] <= p2_start_word["start"] + 0.1),
                        None,
                    )
                    if mid_sil:
                        phrase_repeat_cut = round(mid_sil["start"] + 0.22, 2)
                    else:
                        min_t, _ = find_rms_minimum(rms_env, p1_end_word["end"] - 0.1, p2_start_word["start"] + 0.1)
                        phrase_repeat_cut = round(min_t, 2)
                    rep_str = " ".join(w["w"] for w in words[n_w - plen :])
                    clip["anomalies"].append(
                        f"[TAIL_REPEAT] '{rep_str}' ({p2_start_word['start']:.2f}-{words[-1]['end']:.2f}s cut at {phrase_repeat_cut:.2f}s)"
                    )
                    words = words[: n_w - plen]
                    break

        # Check for Single-Word Tail Repeat ([TAIL_REPEAT] e.g. S5 "ಇರ್ತಿನಿ ಇರ್ತಿನಿ.")
        word_repeat_cut: Optional[float] = None
        if len(words) >= 2 and phrase_repeat_cut is None:
            w_last = words[-1]
            w_prev = words[-2]
            tok_last = _clean_token(w_last["w"])
            if (
                _word_similarity(w_last["w"], w_prev["w"]) >= 0.88
                and tok_last not in INTENTIONAL_REDUPLICATIONS
                and script_text_combined.count(tok_last) < 2
            ):
                # Snap to exact 10ms RMS energy minimum between first and second repeat
                search_lo = max(w_prev["start"] + 0.25, w_prev["end"] - 0.05)
                search_hi = min(w_last["end"] - 0.15, w_last["start"] + 0.22)
                min_t, min_db = find_rms_minimum(rms_env, search_lo, search_hi)
                word_repeat_cut = round(min_t, 2)
                clip["anomalies"].append(
                    f"[TAIL_REPEAT] '{w_last['w']}' ({w_last['start']:.2f}-{w_last['end']:.2f}s snapped to 10ms RMS min {word_repeat_cut:.2f}s @ {min_db:.1f}dB)"
                )
                words = words[:-1]

        # Determine clip end & check for Untranscribed Tail-Babble ([TAIL_BABBLE_CUT])
        last_word_end = words[-1]["end"]
        clip_end = round(min(dur, last_word_end + 0.15), 2)

        if phrase_repeat_cut is not None:
            clip_end = phrase_repeat_cut
        elif word_repeat_cut is not None:
            clip_end = word_repeat_cut
        else:
            # Check for trailing silence starting near last_word_end
            tail_sil = next(
                (s for s in silences if last_word_end - 0.35 <= s["start"] <= last_word_end + 0.25),
                None,
            )
            # Check for non-silent audio islands after last_word_end + 0.35s
            active_tail_frames = [
                t for t, db in rms_env if t > max(last_word_end + 0.35, (tail_sil["end"] if tail_sil else last_word_end) + 0.05) and db > -30.0
            ]
            extra_base_words = [
                bw for bw in base_words if bw["start"] >= last_word_end - 0.15 and bw["end"] > last_word_end + 0.45
            ]
            unaligned_tail_gap = (
                tail_sil is not None and tail_sil["end"] < dur - 2.5 and not any(s["end"] >= dur - 0.2 for s in silences)
            )

            if len(active_tail_frames) >= 15 or extra_base_words or unaligned_tail_gap:
                if tail_sil:
                    min_t, _ = find_rms_minimum(
                        rms_env,
                        max(last_word_end, tail_sil["start"]),
                        min(dur, tail_sil["start"] + 0.24),
                        prefer_center=True,
                    )
                    clip_end = round(min(dur, max(last_word_end + 0.08, min_t)), 2)
                else:
                    clip_end = round(min(dur, last_word_end + 0.14), 2)
                babble_start = active_tail_frames[0] if active_tail_frames else (tail_sil["end"] if tail_sil else clip_end)
                clip["anomalies"].append(
                    f"[TAIL_BABBLE_CUT] trimmed at {clip_end:.2f}s (untranscribed tail audio {babble_start:.2f}-{dur:.2f}s)"
                )
            elif tail_sil:
                anchor = max(last_word_end, tail_sil["start"])
                min_t, _ = find_rms_minimum(rms_env, anchor, min(dur, anchor + 0.26), prefer_center=True)
                clip_end = round(min(dur, max(last_word_end + 0.08, min_t)), 2)
            else:
                min_t, _ = find_rms_minimum(rms_env, last_word_end, min(dur, last_word_end + 0.22), prefer_center=True)
                clip_end = round(min(dur, max(last_word_end + 0.08, min_t)), 2)

        # A note like "(laughs mockingly)" or "(cries)" on this line: keep the reaction after the last word (up to 1.2 s)
        note_text = " ".join(str(l.get("note") or "") for l in matched_lines)
        if note_text and REACTION_RE.search(note_text) and phrase_repeat_cut is None and word_repeat_cut is None:
            held = round(min(dur, last_word_end + 1.2), 2)
            if held > clip_end + 0.05:
                clip["anomalies"].append(f"[BEAT_HOLD] '{note_text.strip()[:30]}': kept {held - clip_end:.1f}s of reaction after the last word")
                clip_end = held

        # Check for Intra-Clip Mid-Sentence Retake ([RETAKE] e.g. S1 "Almost" take 1 vs take 2)
        retake_split_done = False
        for wi in range(1, len(words) - 1):
            w1 = words[wi]
            w2 = words[wi + 1]
            tok = _clean_token(w1["w"])
            if (
                _word_similarity(w1["w"], w2["w"]) >= 0.88
                and tok not in INTENTIONAL_REDUPLICATIONS
                and script_text_combined.count(tok) < 2
            ):
                # Found mid-clip retake (e.g. S1 w1="ಅಲ್ಮೋಸ್ಟ" 5.54-6.26, w2="ಅಲ್ಮೋಸ್ಟ" 6.26-7.16)
                prev_w = words[wi - 1]
                pre_sil = next(
                    (s for s in silences if prev_w["start"] <= s["start"] <= w1["start"] + 0.15),
                    None,
                )
                seg1_end = round(pre_sil["start"] + 0.17, 2) if pre_sil else round(prev_w["end"] + 0.12, 2)

                # Find exact acoustic onset of Take 2 using 10ms RMS dip between w1 and w2
                dip_lo = w1["start"] + 0.35
                dip_hi = w2["end"] - 0.20
                min_t, _ = find_rms_minimum(rms_env, dip_lo, dip_hi, prefer_center=False)
                # Find first active frame (> -25dB) after min_t
                take2_onset = next(
                    (t for t, db in rms_env if t >= min_t and db > -25.0),
                    w2["start"],
                )
                seg2_start = round(take2_onset - 0.12, 2)

                clip["anomalies"].append(
                    f"[RETAKE] '{w1['w']}' take 1 ({seg1_end:.2f}-{seg2_start:.2f}s dropped; kept take 2 from {seg2_start:.2f}s with zoom=1.08)"
                )
                raw_segments.append({
                    "clip": clip,
                    "src": src,
                    "stem": stem,
                    "start": clip_start,
                    "end": seg1_end,
                    "words": words[:wi],
                    "zoom": 1.0,
                    "reason": "pre_retake",
                })
                raw_segments.append({
                    "clip": clip,
                    "src": src,
                    "stem": stem,
                    "start": seg2_start,
                    "end": clip_end,
                    "words": words[wi + 1 :],
                    "zoom": 1.08,
                    "focus": [0.5, 0.4],
                    "reason": "post_retake",
                })
                retake_split_done = True
                break

        if retake_split_done:
            continue

        # Check for Mid-Clip Long Pause Tightening ([PAUSE_SPLIT] e.g. S2 1.98s gap & S9 1.39s gap)
        # Note: speaker-turn pauses between two different characters (like S5 1.13s or S6 1.28s) are natural
        # conversational turns, whereas pauses >= 1.35s (S9 1.39s, S2 1.98s) are dead air that must be tightened!
        num_speakers_in_clip = len({l["speaker"] for l in matched_lines if l.get("speaker")})
        pause_thresh = PAUSE_SPLIT_MONOLOGUE_SEC if num_speakers_in_clip <= 1 else PAUSE_SPLIT_ABSOLUTE_SEC

        if note_text and PAUSE_NOTE_RE.search(note_text):
            pause_thresh = 99.0        # the script asks for this pause (stare, silence, awkward pause): leave it
        internal_pauses = [
            s for s in silences
            if s["start"] >= clip_start + 0.60
            and s["end"] <= clip_end - 0.60
            and (s["end"] - s["start"]) >= pause_thresh
        ]

        if internal_pauses:
            p_sil = internal_pauses[0]
            p_dur = p_sil["end"] - p_sil["start"]
            w_part1 = [w for w in words if w["start"] < p_sil["start"] + 0.15]
            w_part2 = [w for w in words if w["end"] > p_sil["end"] - 0.15]
            pre_txt = " ".join(w["w"] for w in w_part1)
            is_beat_turn = any(p in pre_txt for p in ("?", "!")) or any(l.get("mark") for l in matched_lines)
            target_gap = 0.28 if is_beat_turn else 0.17
            out_min, _ = find_rms_minimum(
                rms_env, p_sil["start"], min(p_sil["end"], p_sil["start"] + 0.26), prefer_center=True
            )
            in_min, _ = find_rms_minimum(
                rms_env, max(p_sil["start"], p_sil["end"] - 0.26), p_sil["end"], prefer_center=False
            )
            seg1_end = round(min(p_sil["end"], max(p_sil["start"] + 0.14, out_min + target_gap * 0.55)), 2)
            seg2_start = round(max(seg1_end, min(p_sil["end"] - 0.12, in_min - target_gap * 0.45)), 2)

            clip["anomalies"].append(
                f"[PAUSE_SPLIT] {p_dur:.2f}s internal pause ({p_sil['start']:.2f}-{p_sil['end']:.2f}s tightened to {seg1_end:.2f}/{seg2_start:.2f}s with zoom=1.08)"
            )
            raw_segments.append({
                "clip": clip,
                "src": src,
                "stem": stem,
                "start": clip_start,
                "end": seg1_end,
                "words": w_part1,
                "zoom": 1.0,
                "reason": "pre_pause_split",
            })
            raw_segments.append({
                "clip": clip,
                "src": src,
                "stem": stem,
                "start": seg2_start,
                "end": clip_end,
                "words": w_part2,
                "zoom": 1.08,
                "focus": [0.5, 0.4],
                "reason": "post_pause_split",
            })
        else:
            raw_segments.append({
                "clip": clip,
                "src": src,
                "stem": stem,
                "start": clip_start,
                "end": clip_end,
                "words": words,
                "zoom": 1.0,
                "reason": "full_clean",
            })

    # Identify scenes that contain multi-speaker in-frame clips or explicit two-shot / galley / projection staging
    two_shot_scene_re = re.compile(
        r"(two[- ]?shot|galley|projection|pops\s+off|talking\s+in\s+the|both|couple|together|side\s+by\s+side)",
        re.I,
    )
    multi_speaker_scenes = {
        l.get("scene")
        for c in clips_data
        if len({l.get("speaker") for l in c.get("matched_lines", []) if l.get("speaker")}) >= 2
        for l in c.get("matched_lines", [])
        if l.get("scene")
    }

    # Pacing Pass: flag / re-energize long static single-speaker shots (>6.0s); never zoom two-shots
    for s_idx, rseg in enumerate(raw_segments):
        seg_dur = round(float(rseg["end"]) - float(rseg["start"]), 2)
        c_lines = rseg["clip"].get("matched_lines", [])
        c_spks = {l.get("speaker") for l in c_lines if l.get("speaker")}
        c_info = rseg["clip"].get("info") or {}
        is_two_shot = (
            len(c_spks) >= 2
            or int(c_info.get("face_count") or 1) >= 2
            or c_info.get("shot_type") == "two_shot"
            or any(
                l.get("scene") in multi_speaker_scenes
                or two_shot_scene_re.search(f"{l.get('scene', '')} {l.get('scene_visual', '')} {l.get('speaker', '')}")
                for l in c_lines
            )
        )
        rseg["is_two_shot"] = is_two_shot
        if is_two_shot:
            rseg["zoom"] = 1.0
            rseg.pop("zoom_to", None)
        elif seg_dur > 6.0 and float(rseg.get("zoom", 1.0)) == 1.0 and not rseg.get("zoom_to"):
            rseg["zoom_to"] = 1.06
            rseg["clip"]["anomalies"].append(
                f"[PACING_PASS] {rseg['stem']} ({seg_dur:.2f}s > 6.0s): added dynamic zoom motion so shot never stays static"
            )

    return raw_segments


def assign_segment_ids_marks_and_layouts(
    raw_segments: List[Dict[str, Any]],
    script_lines: List[Dict[str, Any]],
    clips_data: List[Dict[str, Any]],
    preset: str = "auto",
    mark_pack: str = "default",
    all_clips: Optional[List[Dict[str, Any]]] = None,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], str]:
    """Assign segment IDs (L1..L21, with a/b/c suffixes for multi-part lines),
    auto-place WRONG/RIGHT marks from script tags, and apply layouts & slow-push zooms.
    """
    pool_clips = all_clips if all_clips is not None else clips_data
    listening_pool = [
        c for c in pool_clips
        if c.get("is_listening") or "listen" in str(c.get("stem", "")).lower() or "idle" in str(c.get("stem", "")).lower()
    ]
    all_speakers: List[str] = []
    for l in script_lines:
        sp = l.get("speaker")
        if sp and sp not in all_speakers:
            all_speakers.append(sp)
    # Map each raw segment to the script line(s) it covers
    for seg in raw_segments:
        clip = seg["clip"]
        c_lines = clip.get("matched_lines", [])
        if seg.get("lines_override") is not None:   # set when wrong-speaker parts were cut or a fill-in take was spliced
            seg["lines"] = seg["lines_override"]
        elif len(c_lines) <= 1:
            seg["lines"] = c_lines
        else:
            # If clip was split into 2 segments (like S1 -> L1+L2 and L3, or S2 -> L4 and L5)
            # partition matched_lines between the split segments
            Sibs = [s for s in raw_segments if s["stem"] == seg["stem"]]
            if len(Sibs) == 2:
                if seg is Sibs[0]:
                    seg["lines"] = c_lines[:-1]
                else:
                    seg["lines"] = [c_lines[-1]]
            else:
                seg["lines"] = c_lines

    # Count how many segments share the same primary script line ID (to add a/b/c suffixes like L13a, L13b)
    primary_counts: Dict[str, int] = {}
    for seg in raw_segments:
        lid = seg["lines"][0]["id"] if seg.get("lines") else seg["stem"]
        primary_counts[lid] = primary_counts.get(lid, 0) + 1

    primary_seen: Dict[str, int] = {}
    segments_out: List[Dict[str, Any]] = []
    marks_out: List[Dict[str, Any]] = []
    hook_transition_count = 0
    has_done_first_zoom_in = False
    prev_single_zoom_style = ""
    supernova_keypoint_re = re.compile(
        r"(supernova|miss\s*nova|\bnova\b|ai\s*tutor|spoken\s*english|improve|two\s*weeks|2\s*weeks|"
        r"15\s*minutes|fifteen|daily\s*speaking|daily\s*practice|stop\s*saying|secret|important|"
        r"mistake|professional|fluent|confidence|ನಿಲ್ಲಿಸಿ|ಎರಡು\s*ವಾರ|ತೋರಿಸ್ತೀನಿ\s*ನೋಡಿ|ಅಭ್ಯಾಸ|ಸುಲಭ)",
        re.I,
    )

    # Query vision_tracker for layout recommendations when available
    resolved_preset = preset if preset != "auto" else "single"
    seg_layout_map: Dict[str, str] = {}
    if vision_tracker is not None and hasattr(vision_tracker, "recommend_project_layouts"):
        try:
            clips_info_map = {c["stem"]: c["info"] for c in clips_data}
            rec = vision_tracker.recommend_project_layouts(
                clips_info_map,
                script_lines=script_lines,
                preset=preset,
            )
            if preset != "auto":
                resolved_preset = rec.get("preset", preset)
                seg_layout_map = rec.get("segment_layouts", {})
        except Exception:
            pass

    for idx, seg in enumerate(raw_segments):
        lines = seg.get("lines", [])
        base_lid = lines[0]["id"] if lines else f"S{idx + 1}"
        if primary_counts.get(base_lid, 1) > 1:
            sub_idx = primary_seen.get(base_lid, 0)
            primary_seen[base_lid] = sub_idx + 1
            seg_id = f"{base_lid}{chr(ord('a') + sub_idx)}"
        else:
            seg_id = base_lid

        dur = round(seg["end"] - seg["start"], 2)
        zoom = float(seg.get("zoom", 1.0))

        # Concise editorial note summarizing covered script lines, speaker & scene
        spk_list = []
        scene_name = ""
        if lines:
            line_summaries = []
            for l in lines:
                if l.get("speaker") and l["speaker"] not in spk_list:
                    spk_list.append(l["speaker"])
                if l.get("scene") and not scene_name:
                    scene_name = l["scene"]
                q_str = f" '{l['quoted'][0]}'" if l.get("quoted") else ""
                snippet = l["text"][:32].strip()
                spk_prefix = f"[{l['speaker']}] " if l.get("speaker") else ""
                line_summaries.append(f"{l['id']} {spk_prefix}{q_str}: {snippet}".replace("  ", " "))
            note = " / ".join(line_summaries)
        else:
            note = f"{seg['stem']} {seg['start']:.2f}-{seg['end']:.2f}"

        seg_dict: Dict[str, Any] = {
            "id": seg_id,
            "src": seg["src"],
            "start": seg["start"],
            "end": seg["end"],
            "note": note,
        }
        if spk_list:
            seg_dict["speaker"] = " / ".join(spk_list)
        if scene_name:
            seg_dict["scene"] = scene_name

        layout_mode = seg_layout_map.get(base_lid, resolved_preset if resolved_preset != "auto" else "single")
        # A layout named in the script ("floating window ... horizontal split screen") applies unless the clip already is a split
        script_hint = next((l.get("layout_hint") for l in lines if l.get("layout_hint")), "")
        if script_hint and layout_mode == "single" and str(preset).lower() in ("auto", "hybrid"):
            c_shot = str(((seg.get("clip") or {}).get("info") or {}).get("shot_type") or "")
            if "baked" not in c_shot and c_shot != "two_shot":
                layout_mode = script_hint
                seg_dict["note"] = f"{seg_dict['note']} [LAYOUT_HINT {script_hint} from script]"
        if layout_mode and layout_mode != "single":
            seg_dict["layout"] = layout_mode

        # Auto-populate peer= and alternating speaker slots (left <-> right or top <-> bottom) for split_h / split_v / video_call
        if layout_mode in ("split_h", "split_v", "video_call") and not seg_dict.get("peer"):
            cur_spk = spk_list[0] if spk_list else ""
            other_spk = next((s for s in all_speakers if s != cur_spk), cur_spk or "Peer")
            if layout_mode in ("split_h", "split_v") and not seg_dict.get("slot"):
                slot_a, slot_b = ("left", "right") if layout_mode == "split_h" else ("top", "bottom")
                spk_idx = all_speakers.index(cur_spk) if (cur_spk and cur_spk in all_speakers) else (idx % 2)
                active_slot = slot_a if (spk_idx % 2 == 0) else slot_b
                peer_slot = slot_b if active_slot == slot_a else slot_a
                seg_dict["slot"] = active_slot
            else:
                peer_slot = None
            peer_clip = None
            for lc in listening_pool:
                if lc["src"] != seg["src"] and (not other_spk or other_spk.lower() in str(lc["stem"]).lower()):
                    peer_clip = lc
                    break
            if peer_clip is None and listening_pool:
                peer_clip = next((lc for lc in listening_pool if lc["src"] != seg["src"]), listening_pool[0])
            if peer_clip is not None:
                l_dur = float((peer_clip.get("info") or {}).get("duration") or dur)
                seg_dict["peer"] = {
                    "speaker": other_spk,
                    "src": peer_clip["src"],
                    "idle_span": [0.0, round(min(max(2.0, dur), l_dur), 2)],
                    "mode": "loop",
                }
                if peer_slot:
                    seg_dict["peer"]["slot"] = peer_slot
            else:
                # Fallback: find a silent span in any clip spoken by other_spk
                other_clip = next(
                    (c for c in pool_clips if c["src"] != seg["src"] and any(l.get("speaker") == other_spk for l in c.get("matched_lines", []))),
                    None,
                )
                if other_clip is not None:
                    sils = other_clip.get("macro_silences") or []
                    best_sil = max(sils, key=lambda s: float(s["end"]) - float(s["start"]), default=None)
                    c_dur = float((other_clip.get("info") or {}).get("duration") or 3.0)
                    span = [round(float(best_sil["start"]), 2), round(float(best_sil["end"]), 2)] if best_sil and (best_sil["end"] - best_sil["start"]) >= 0.8 else [0.0, round(min(2.5, c_dur), 2)]
                    seg_dict["peer"] = {
                        "speaker": other_spk,
                        "src": other_clip["src"],
                        "idle_span": span,
                        "mode": "loop",
                    }
                    if peer_slot:
                        seg_dict["peer"]["slot"] = peer_slot

        # Auto-extract stacked "Stop Saying" banner on Scene 1 / Hook when script opens with "Stop saying ..."
        if idx == 0 and lines:
            first_txt = lines[0].get("text", "")
            m_stop = re.search(r"stop\s+saying\s+['\"“”‘’]?([^'\"“”‘’.,!?]+)['\"“”‘’]?", first_txt, re.I)
            if m_stop:
                phrase = m_stop.group(1).strip()
                if phrase:
                    seg_dict["banners"] = [{
                        "kind": "stop_saying",
                        "title": "Stop Saying",
                        "phrase": f'"{phrase}"',
                        "at": 0.0,
                        "duration": round(min(dur, 4.2), 2),
                    }]

        # "(X Sign and Text Strikethrough)" lines: show each phrase as a red pill that pops in and is struck through
        strike_lines = [l for l in lines if l.get("strike_text")]
        if strike_lines and not seg_dict.get("banners"):
            seg_ws = seg.get("words") or []
            strike_banners = []
            for k, l in enumerate(strike_lines):
                at_k = round(0.15 + k * 0.65, 2)
                if len(strike_lines) == 1 and seg_ws:
                    at_k = round(max(0.05, float(seg_ws[0]["start"]) - float(seg["start"])), 2)
                strike_banners.append({"kind": "strike_text", "text": str(l["strike_text"]).strip(), "at": at_k})
            seg_dict["banners"] = strike_banners

        # Auto-assign intentional transitions:
        # 1. Up to 1-2 crisp transitions (whip_left / whip_right) across raw clip boundaries inside the initial single-character hook
        # 2. Macro layout transition (warm_flash / smoothright) when switching between single and split_h/split_v
        # 3. NEVER place transitions inside two-character split_h / split_v dialogue turns
        distinct_script_scenes = {l.get("scene") for l in script_lines if l.get("scene")}
        if segments_out:
            prev_seg = segments_out[-1]
            prev_lay = prev_seg.get("layout", "single")
            cur_lay = seg_dict.get("layout", "single")
            prev_sc = prev_seg.get("scene", "")
            cur_sc = seg_dict.get("scene", "")
            if prev_lay != cur_lay:
                seg_dict["transition"] = "warm_flash" if cur_lay in ("split_h", "split_v") else "smoothright"
                seg_dict["transition_duration"] = 0.35 if cur_lay in ("split_h", "split_v") else 0.28
            elif len(distinct_script_scenes) >= 2:
                if prev_sc and cur_sc and prev_sc != cur_sc:
                    seg_dict["transition"] = "whip_left"
                    seg_dict["transition_duration"] = 0.28
                    hook_transition_count += 1
            elif cur_lay == "single" and prev_lay == "single" and hook_transition_count < 2:
                if Path(str(prev_seg.get("src", ""))).name != Path(str(seg_dict.get("src", ""))).name:
                    seg_dict["transition"] = "whip_left" if hook_transition_count == 0 else "whip_right"
                    seg_dict["transition_duration"] = 0.28
                    hook_transition_count += 1

        # Split screens (split_h / split_v) and two-shots are 100% static (z=1.0 locked, no zoom-in or zoom-out).
        # Single-character live segments (layout == "single") prioritize:
        #   - High preference for the FIRST rapid zoom-in (sudden_in) on the opening segment
        #   - Sudden zoom-in (sudden_in) on Supernova callouts, strong teaching points, or vocal [STRESS] peaks
        #   - Rapid zoom-out (sudden_out) or smooth push-in (1.0->1.06) on follow-up single segments
        seg_words = seg.get("words") or []
        stress_words = detect_vocal_stress_words(
            seg["clip"].get("rms_env", []),
            seg_words,
            float(seg["start"]),
            float(seg["end"]),
        )
        if stress_words:
            seg_dict["stress_words"] = stress_words
        if vision_tracker is not None and hasattr(vision_tracker, "detect_actor_readiness_bounds") and seg_words:
            try:
                seg_dict["face_ready"] = vision_tracker.detect_actor_readiness_bounds(
                    seg["src"],
                    float(seg["start"]),
                    float(seg["end"]),
                    first_word_start=float(seg_words[0]["start"]),
                    last_word_end=float(seg_words[-1]["end"]),
                )
            except Exception:
                pass

        two_shot_re = re.compile(
            r"(two[- ]?shot|galley|projection|pops\s+off|talking\s+in\s+the|both|couple|together|side\s+by\s+side)",
            re.I,
        )
        c_info = (seg.get("clip") or {}).get("info") or {}
        is_two_shot = bool(
            seg.get("is_two_shot")
            or len(spk_list) >= 2
            or int(c_info.get("face_count") or 1) >= 2
            or c_info.get("shot_type") == "two_shot"
            or any(
                two_shot_re.search(f"{l.get('scene', '')} {l.get('scene_visual', '')} {l.get('speaker', '')}")
                for l in lines
            )
        )

        if layout_mode in ("split_h", "split_v") or is_two_shot:
            seg_dict["zoom"] = 1.0
            seg_dict.pop("zoom_to", None)
            seg_dict.pop("zoom_style", None)
            seg_dict.pop("zoom_at", None)
        else:
            full_line_text = " ".join(l.get("text", "") for l in lines) if lines else note
            callout_at = None
            for w in seg_words:
                wt = str(w.get("w") or "")
                if supernova_keypoint_re.search(wt):
                    rel_w = round(float(w.get("start", seg["start"])) - float(seg["start"]), 2)
                    if 0.05 <= rel_w <= max(0.15, dur - 0.35):
                        callout_at = rel_w
                        break
            if callout_at is None and stress_words:
                cand_rel = float(stress_words[0]["rel_t"])
                if 0.08 <= cand_rel <= max(0.15, dur - 0.35):
                    callout_at = cand_rel

            if not has_done_first_zoom_in:
                seg_dict["zoom"] = 1.0
                seg_dict["zoom_to"] = 1.08
                seg_dict["zoom_style"] = "sudden_in"
                seg_dict["zoom_at"] = callout_at if callout_at is not None else 0.25
                seg_dict["focus"] = seg.get("focus", [0.5, 0.42])
                has_done_first_zoom_in = True
                prev_single_zoom_style = "sudden_in"
            elif supernova_keypoint_re.search(full_line_text) and prev_single_zoom_style != "sudden_in":
                seg_dict["zoom"] = 1.0
                seg_dict["zoom_to"] = 1.08
                seg_dict["zoom_style"] = "sudden_in"
                seg_dict["zoom_at"] = callout_at if callout_at is not None else 0.12
                seg_dict["focus"] = seg.get("focus", [0.5, 0.42])
                prev_single_zoom_style = "sudden_in"
            elif prev_single_zoom_style == "sudden_in" and dur >= 1.5:
                seg_dict["zoom"] = 1.08
                seg_dict["zoom_to"] = 1.0
                seg_dict["zoom_style"] = "sudden_out"
                seg_dict["zoom_at"] = 0.10
                seg_dict["focus"] = seg.get("focus", [0.5, 0.42])
                prev_single_zoom_style = "sudden_out"
            elif zoom != 1.0:
                seg_dict["zoom"] = zoom
                seg_dict["focus"] = seg.get("focus", [0.5, 0.4])
                prev_single_zoom_style = "static"
            elif seg.get("zoom_to"):
                seg_dict["zoom"] = 1.0
                seg_dict["zoom_to"] = float(seg["zoom_to"])
                seg_dict["focus"] = seg.get("focus", [0.5, 0.42])
                prev_single_zoom_style = "smooth"
            elif dur >= LONG_MONOLOGUE_ZOOM_SEC and len(lines) == 1:
                is_closing_cta = (idx == len(raw_segments) - 1)
                seg_dict["zoom_to"] = 1.05 if is_closing_cta else 1.06
                seg_dict["focus"] = [0.5, 0.45]
                prev_single_zoom_style = "smooth"

        segments_out.append(seg_dict)

        # Auto-place [WRONG] / [RIGHT] marks at the END of the marked sentence (2-pulse flash = 0.56s)
        for l in lines:
            mtype = l.get("mark")
            if mtype not in ("wrong", "right"):
                continue

            seg_words = seg.get("words", [])
            m_dur = 0.56
            if seg_words:
                sentence_end_rel = round(max(0.25, float(seg_words[-1]["end"]) - float(seg["start"])), 2)
                # Ensure segment has enough tail padding so the 0.56s 2-pulse flash completes cleanly
                if float(seg["end"]) < float(seg["start"]) + sentence_end_rel + m_dur:
                    seg_dict["end"] = round(float(seg["start"]) + sentence_end_rel + m_dur + 0.04, 2)
                at_offset = sentence_end_rel
            else:
                at_offset = round(max(0.25, dur - m_dur), 2)

            mark_entry: Dict[str, Any] = {
                "type": mtype,
                "segment": seg_id,
                "at": at_offset,
                "duration": m_dur,
                "position": "top-right",
            }
            if mark_pack and mark_pack != "default":
                mark_entry["style"] = mark_pack
            marks_out.append(mark_entry)

    return segments_out, marks_out, resolved_preset


def format_plan_edl(plan: Dict[str, Any]) -> str:
    """Format a plan dict into the compact 1-line-per-segment `plan.edl` syntax."""
    if edl_parser is not None:
        for fn_name in ("format_edl", "to_edl", "dump_edl", "plan_to_edl"):
            if hasattr(edl_parser, fn_name):
                try:
                    return getattr(edl_parser, fn_name)(plan)
                except Exception:
                    pass

    canvas = plan.get("canvas", {})
    W = canvas.get("width", 720)
    H = canvas.get("height", 1280)
    fps = canvas.get("fps", 24)
    preset = plan.get("preset", "single")
    mark_pack = plan.get("mark_pack", "default")
    loudness = plan.get("audio", {}).get("loudness", -11.5)

    lines = [
        f"# output: {plan.get('output', 'output/out.mp4')}",
        f"# canvas: {W}x{H}@{fps}",
        f"# preset: {preset}",
        f"# mark_pack: {mark_pack}",
        f"# loudness: {loudness}",
        "",
        "# SEGMENTS: id | src | start-end | layout | zoom | transition | extras | note",
    ]
    for s in plan.get("segments", []):
        sid = s.get("id", "S1")
        src_stem = Path(str(s.get("src", ""))).stem or str(s.get("src", ""))
        st_en = f"{float(s['start']):.2f}-{float(s['end']):.2f}"
        lay = s.get("layout", "single")
        z0 = float(s.get("zoom", 1.0))
        z1 = float(s.get("zoom_to", z0))
        if z1 != z0:
            z_str = f"{z0:g}->{z1:g}"
        else:
            z_str = f"{z0:g}" if z0 != 1.0 else "1.0"
        tr = s.get("transition", "cut")
        if tr != "cut" and s.get("transition_duration"):
            tr = f"{tr}:{float(s['transition_duration']):g}"
        extras = []
        if "focus" in s and isinstance(s["focus"], list) and len(s["focus"]) == 2:
            extras.append(f"focus={s['focus'][0]:g},{s['focus'][1]:g}")
        extras_str = ",".join(extras) if extras else "-"
        note = str(s.get("note", "")).replace("\n", " ").strip()
        lines.append(f"{sid:<5} | {src_stem:<3} | {st_en:<9} | {lay:<6} | {z_str:<9} | {tr:<3} | {extras_str:<14} | {note}")

    if plan.get("marks"):
        lines += ["", "# MARKS: type | segment | at | duration | pos | size | sfx | gain_db"]
        for m in plan["marks"]:
            mtype = m["type"]
            seg_id = m["segment"]
            at = f"{float(m.get('at', 0.0)):.2f}"
            dur = f"{float(m.get('duration', 1.2)):.1f}"
            pos = m.get("position", "top-right")
            sz = f"{float(m.get('size', 0.22)):.2f}"
            sfx = m.get("sfx") or DEFAULT_SFX.get(mtype, "correct_ding.wav")
            gain = m.get("sfx_gain_db", -4)
            lines.append(f"{mtype:<5} | {seg_id:<4} | {at:<4} | {dur:<3} | {pos:<9} | {sz:<4} | {sfx} | {gain}")

    if plan.get("sfx"):
        lines += ["", "# SFX: file | segment | at | gain_db"]
        for e in plan["sfx"]:
            lines.append(f"{e['file']} | {e['segment']} | {float(e.get('at', 0.0)):.2f} | {e.get('gain_db', -8)}")

    return "\n".join(lines) + "\n"


def format_brief_txt(
    project_name: str,
    script_path: Path,
    clips_data: List[Dict[str, Any]],
    segments: List[Dict[str, Any]],
    marks: List[Dict[str, Any]],
    script_lines: List[Dict[str, Any]],
    preset: str,
    mark_pack: str,
    meta: Optional[Dict[str, Any]] = None,
) -> str:
    """Build compact `work/<project>/brief.txt` strictly < 1,100 tokens (with Concept/Visual Refs, Characters & Scene Framing)."""
    total_dur = sum(round(s["end"] - s["start"], 2) for s in segments)
    anomaly_count = sum(len(c.get("anomalies", [])) for c in clips_data)
    meta = meta or {}

    out_lines = [
        f"# BRIEF: {project_name} | Script: {script_path.name} ({len(script_lines)} lines) | Clips: {len(clips_data)} -> {len(segments)} segs ({tc(total_dur)})",
        f"# Preset: {preset} | Mark Pack: {mark_pack} | Auto-Marks: {len(marks)} | Anomalies Fixed: {anomaly_count}",
    ]
    if meta.get("concept_brief"):
        out_lines.append(f"# Concept Brief    : {meta['concept_brief']}")
    if meta.get("characters"):
        chars_str = " | ".join(f"{k}: {v[:80]}" for k, v in meta["characters"].items())
        out_lines.append(f"# Characters       : {chars_str}")
    if meta.get("scenes"):
        for sc in meta["scenes"]:
            out_lines.append(f"# {sc['scene']:<17}: {sc.get('visual', '')}")

    mem_lines = brain_memory.format_brain_memory_for_brief("micronarrative")
    if mem_lines:
        out_lines.extend(mem_lines)

    out_lines += [
        "",
        "## CLIPS, CHARACTER SPEAKERS & AUTO-CUTS",
    ]

    # Cap [ALT_TAKE] entries in brief.txt so messy shoots with 5-15 retakes never bloat Director tokens
    alt_seen_by_group: Dict[Any, int] = {}
    omitted_by_group: Dict[Any, List[str]] = {}
    for c in clips_data:
        if c.get("dropped_take") and not c.get("is_listening"):
            gid = c.get("take_group", id(c))
            cnt = alt_seen_by_group.get(gid, 0) + 1
            alt_seen_by_group[gid] = cnt
            if cnt > MAX_ALT_TAKES_IN_BRIEF:
                c["_omit_from_brief"] = True
                omitted_by_group.setdefault(gid, []).append(str(c["stem"]))

    for c in clips_data:
        if c.get("_omit_from_brief"):
            continue
        stem = c["stem"]
        dur = float(c["info"].get("duration") or 0.0)
        l_ids = ",".join(
            f"{l['id']}({l['speaker']})" if l.get("speaker") else l["id"]
            for l in c.get("matched_lines", [])
        ) or "-"
        c_segs = [s for s in segments if str(s["src"]) == str(c["src"])]
        cuts_str = " + ".join(
            f"{s['id']}[{s['start']:.2f}-{s['end']:.2f}s"
            + (f" {s['speaker']}" if s.get("speaker") else "")
            + (f" z={s['zoom']}" if s.get("zoom", 1.0) != 1.0 else (f" z->{s['zoom_to']}" if "zoom_to" in s else ""))
            + "]"
            for s in c_segs
        )
        sils = c.get("macro_silences", [])
        sil_str = ",".join(f"{s['start']:.2f}-{s['end']:.2f}" for s in sils[:4]) or "none"
        hq_w = c.get("hq_words", [])
        first_w = hq_w[0]["w"] if hq_w else ""
        last_w = hq_w[-1]["w"] if hq_w else ""
        out_lines.append(
            f"{stem:<3} ({dur:4.1f}s) -> {l_ids:<18} | keep: {cuts_str:<34} | sil: {sil_str} | \"{first_w} ... {last_w}\""
        )
        for anom in c.get("anomalies", []):
            out_lines.append(f"  ! {anom}")
        for s in c_segs:
            sw = s.get("stress_words") or []
            fr = s.get("face_ready") or {}
            tags = []
            if sw:
                tags.append("STRESS: " + ", ".join(f"'{w['w']}'@+{w['rel_t']:.2f}s(+{w['delta_db']:.1f}dB)" for w in sw))
            if fr and (fr.get("trimmed_lead_sec", 0.0) > 0 or fr.get("trimmed_tail_sec", 0.0) > 0):
                tags.append(f"FACE_READY: {fr['ready_start']:.2f}-{fr['ready_end']:.2f}s")
            if tags:
                out_lines.append(f"  * [{s['id']}] {' | '.join(tags)}")
        gid = c.get("take_group", id(c))
        if not c.get("dropped_take") and gid in omitted_by_group:
            om_list = omitted_by_group[gid]
            out_lines.append(f"  ! [ALT_TAKES_OMITTED] +{len(om_list)} lower-scored takes omitted ({', '.join(om_list)})")

    out_lines += ["", "## MARKS (✓/✗)"]
    for m in marks:
        out_lines.append(
            f"- {m['type'].upper():<5} on {m['segment']:<4} @ +{m['at']:.2f}s ({m.get('duration', 1.2):.1f}s, {m.get('position', 'top-right')})"
        )

    text = "\n".join(out_lines) + "\n"
    # Guarantee strictly < 1,100 tokens (~3,900 chars)
    if len(text) > MAX_BRIEF_CHARS:
        text = text[:MAX_BRIEF_CHARS] + "\n# [truncated to stay <1,100 tokens]\n"
    return text


def discover_clips(raw_args: List[str]) -> List[Tuple[str, str, Path]]:
    """Resolve input clip arguments or auto-discover work/S1..SN into (stem, src_str, work_dir) tuples."""
    expanded: List[str] = []
    for pat in raw_args:
        matches = glob.glob(str(resolve(pat))) or glob.glob(pat)
        if matches:
            expanded.extend(sorted(matches, key=lambda x: _natural_clip_sort_key(Path(x))))
        elif "*" not in pat and "?" not in pat:
            expanded.append(pat)

    items: List[Tuple[str, str, Path]] = []
    for item in expanded:
        p = Path(item)
        stem = p.stem if p.suffix else p.name
        wdir = clip_work_dir(p) if p.is_file() else WORK / stem
        if p.is_file():
            items.append((stem, str(p), wdir))
        elif wdir.is_dir() and (wdir / "info.json").is_file():
            info = read_json(wdir / "info.json")
            src_str = str(info.get("path") or f"input/footage/{stem}.mp4").replace("\\", "/")
            items.append((stem, src_str, wdir))

    if not items:
        # Auto-discover from input/footage/*.mp4 first, then work/S*
        footage_files = sorted((ROOT / "input" / "footage").glob("*.mp4"), key=_natural_clip_sort_key)
        if footage_files:
            for fp in footage_files:
                items.append((fp.stem, f"input/footage/{fp.name}", clip_work_dir(fp)))
        else:
            work_dirs = sorted(
                [d for d in WORK.iterdir() if d.is_dir() and (d / "info.json").is_file() and re.match(r"^S\d+$", d.name)],
                key=_natural_clip_sort_key,
            )
            for wd in work_dirs:
                info = read_json(wd / "info.json")
                src_str = str(info.get("path") or f"input/footage/{wd.name}.mp4").replace("\\", "/")
                items.append((wd.name, src_str, wd))

    # Two different clips with the same file name (S1.mp4 from two folders) both stay in the job under distinct names
    seen: Dict[str, int] = {}
    unique: List[Tuple[str, str, Path]] = []
    for stem, src_str, wdir in items:
        seen[stem] = seen.get(stem, 0) + 1
        unique.append((stem if seen[stem] == 1 else f"{stem}_{seen[stem]}", src_str, wdir))
    return unique


def main() -> None:
    ap = argparse.ArgumentParser(description="Deterministic token-saving pre-processor for Claude Editor.")
    ap.add_argument("clips", nargs="*", help="Input footage clips or work/<clip> directories")
    ap.add_argument("--script", required=False, help="Path to script file in input/script/")
    ap.add_argument("--lang", default="auto", help="Spoken language code (auto, en, kn, ta, hi, te, ml)")
    ap.add_argument("--project", default=None, help="Project folder name under work/<project>")
    ap.add_argument(
        "--preset",
        default="auto",
        choices=["auto", "single", "split_v", "split_h", "chat_bar", "video_call", "hybrid"],
        help="Layout preset mode",
    )
    ap.add_argument("--mark-pack", default="flash", help="Swappable ✓/✗ mark overlay pack (flash, vignette, default, glossy)")
    ap.add_argument(
        "--clean-raw-json",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Keep work/ lean by preventing raw intermediate JSON bloat",
    )
    args = ap.parse_args()
    job_start_epoch = time.time()

    # Auto git pull once per new job (throttled to 15 min so if zeno.py --inspect just pulled, it doesn't repeat)
    try:
        import auto_updater
        auto_updater.ensure_latest_update(force=False, min_interval_hours=0.25, quiet=False)
    except Exception:
        pass

    # Resolve script file
    if args.script:
        script_path = resolve(args.script)
    else:
        scripts_found = sorted((ROOT / "input" / "script").glob("*.txt"))
        if not scripts_found:
            raise SystemExit("No script file specified and none found in input/script/*.txt")
        script_path = scripts_found[0]

    # Downstream tools (transcribe.py, aligner) read plain text, so hand them a normalised .txt copy of non-text scripts.
    if script_path.suffix.lower() not in (".txt", ".md"):
        cache_dir = WORK / "_script_cache"
        cache_dir.mkdir(parents=True, exist_ok=True)
        converted = cache_dir / f"{script_path.stem}.txt"
        converted.write_text(read_script_text(script_path), encoding="utf-8")
        print(f"  Script: converted {script_path.name} -> {converted.relative_to(ROOT)}")
        script_path = converted

    meta, script_lines = parse_script_file(script_path)
    project_name = args.project or meta.get("project_name") or script_path.stem
    lang = args.lang if args.lang != "auto" else meta.get("language", "auto")

    clip_entries = discover_clips(args.clips)
    if not clip_entries:
        raise SystemExit("No input clips or cached work/S* directories found.")

    clips_data: List[Dict[str, Any]] = []
    for stem, src_str, wdir in clip_entries:
        info, hq_words, base_words = ensure_clip_transcribed(
            stem, src_str, wdir, lang=lang, script_path=script_path, clean_raw_json=args.clean_raw_json
        )
        wav_path = wdir / "audio16k.wav"
        rms_env = compute_10ms_rms_envelope(wav_path)
        macro_sil, micro_dips = detect_two_tier_silences(wav_path, rms_env, cached_silences_path=wdir / "silences.json")
        clips_data.append({
            "stem": stem,
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

    # Auto-pair <stem>.mp4 video with <stem>VO.mp4 voiceover audio when both exist
    by_stem_upper = {c["stem"].upper(): c for c in clips_data}
    for c in clips_data:
        st_up = c["stem"].upper()
        if st_up.endswith("VO") and len(st_up) > 2:
            base_up = st_up[:-2].rstrip("_- ")
            base_c = by_stem_upper.get(base_up)
            if base_c is not None and len(c.get("hq_words") or []) > len(base_c.get("hq_words") or []):
                base_c["hq_words"] = c["hq_words"]
                base_c["base_words"] = c["base_words"]
                base_c["rms_env"] = c["rms_env"]
                base_c["macro_silences"] = c["macro_silences"]
                base_c["micro_dips"] = c["micro_dips"]
                c["dropped_take"] = True
                c["anomalies"].append(f"[VO_PAIRED] paired voiceover audio/words into {base_c['stem']}")

    # Align clips to script lines & run deterministic anomaly detection + auto-cutting
    match_clips_to_script_lines(clips_data, script_lines)
    apply_speaker_check(clips_data, script_lines)   # before choosing takes: a take with the wrong voice scores lower
    select_best_takes(clips_data)
    kept_clips = [c for c in clips_data if not c.get("dropped_take")]
    raw_segments = cut_clips(kept_clips)
    fill_clips = fill_lines_lost_to_wrong_speaker(raw_segments, clips_data, script_lines)
    kept_clips = kept_clips + [c for c in fill_clips if c not in kept_clips]
    for c in clips_data:
        if c.get("dropped_take") and c not in fill_clips:   # trims for the unused take, so swapping it in is a one-line edit
            alt = cut_clips([c])
            if alt:
                c["anomalies"].append(f"  unused take would keep {alt[0]['start']:.2f}-{alt[-1]['end']:.2f}s of {c['src']}")
    segments, marks, resolved_preset = assign_segment_ids_marks_and_layouts(
        raw_segments,
        script_lines,
        kept_clips,
        preset=args.preset,
        mark_pack=args.mark_pack,
        all_clips=clips_data,
    )

    first_info = clips_data[0]["info"]
    canvas = {
        "width": int(first_info.get("width") or 720),
        "height": int(first_info.get("height") or 1280),
        "fps": int(round(float(first_info.get("fps") or 24))),
    }

    output_mp4 = (OUTPUT / f"{project_name}.mp4").as_posix()
    plan: Dict[str, Any] = {
        "output": output_mp4,
        "script": str(script_path.relative_to(ROOT)) if script_path.is_relative_to(ROOT) else str(script_path),
        "concept_brief": meta.get("concept_brief", ""),
        "characters": meta.get("characters", {}),
        "scenes": meta.get("scenes", []),
        "canvas": canvas,
        "preset": resolved_preset,
        "mark_pack": args.mark_pack,
        "segments": segments,
        "marks": marks,
        "audio": {"loudness": -11.5},
    }

    edl_text = format_plan_edl(plan)
    brief_text = format_brief_txt(
        project_name=project_name,
        script_path=script_path,
        clips_data=clips_data,
        segments=segments,
        marks=marks,
        script_lines=script_lines,
        preset=resolved_preset,
        mark_pack=args.mark_pack,
        meta=meta,
    )

    # Write to work/<project>/
    proj_dir = WORK / project_name
    proj_dir.mkdir(parents=True, exist_ok=True)
    (proj_dir / "brief.txt").write_text(brief_text, encoding="utf-8")
    (proj_dir / "plan.edl").write_text(edl_text, encoding="utf-8")
    (proj_dir / "plan.auto.edl").write_text(edl_text, encoding="utf-8")
    write_json(proj_dir / "plan.json", plan)
    write_json(proj_dir / "plan.auto.json", plan)

    est_tokens = len(brief_text) // 4
    print(f"Prepared {len(clips_data)} clips -> {len(segments)} segments, {len(marks)} marks")
    print(f"  Brief : {proj_dir / 'brief.txt'} (~{est_tokens} tokens, {len(brief_text.splitlines())} lines)")
    print(f"  EDL   : {proj_dir / 'plan.edl'}")
    print(f"  JSON  : {proj_dir / 'plan.json'}")
    try:
        import token_tracker
        snap = token_tracker.save_prepare_snapshot(
            project_name=project_name,
            lang=lang,
            clips_data=clips_data,
            segments=segments,
            marks=marks,
            job_start_epoch=job_start_epoch,
        )
        print(
            f"  Tokens: {snap['transcribe_in_tokens']:,} in / {snap['transcribe_out_tokens']:,} out "
            f"({snap['transcribe_total_tokens']:,} transcription total) | Agent: {snap['agent_name']} ({snap['agent_model']})"
        )
    except Exception:
        pass


if __name__ == "__main__":
    main()
