"""Measure the footage for the Director. This file makes no editing decision.

Usage:
    python3 scripts/prepare.py [clips...] --script <script> [--lang auto] [--project <name>]

Writes to work/<project>/:
    line_map.md          the script in full; under each line, the clip stretches that say it
    facts.md             every word with its time, pauses, faces, silent clips
    clip_sheet_<n>.jpg   pictures of every clip
    plan.edl             header only; the Director writes the segments (agent/EDIT_RULES.md)
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
    ensure_cache_current,
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

import style_config  # noqa: E402

_TUNING = style_config.get_tuning("micronarrative")
MACRO_SILENCE_DB = float(_TUNING["macro_silence_db"])
MACRO_SILENCE_MIN_SEC = float(_TUNING["macro_silence_min_sec"])
MICRO_DIP_DB = float(_TUNING["micro_dip_db"])
MICRO_DIP_MIN_SEC = float(_TUNING["micro_dip_min_sec"])


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




# --- Script cues that are directions or on-screen text, not spoken dialogue ---------------------------------------
_URL_ONLY_RE = re.compile(r"^https?://\S+$", re.I)
_META_LINE_RE = re.compile(r"^(?:visuals?|reference(?:\s+link)?|ref\s+link|concept)\s*:", re.I)
_CHAR_DEF_RE = re.compile(r"^Character\s*\d*\s*[-–—]\s*(.+)$", re.I)
# "Scene changes: ...", "Scene changes - ...", "Back to the site, ...", "Cut to ...", "Flashback ..."
_SCENE_DIR_RE = re.compile(r"^(?:scene\s+chang(?:e|es|ed)|flash\s*back|(?:back|cut)\s+to)\b[\s:,\-–—]*(.*)$", re.I)
_LAYOUT_CUE_RE = re.compile(r"^(?:rectangle\s+overlay|floating\s+window|(?:(?:horizontal|vertical)\s+)?split[\s-]*screen|picture[\s-]*in[\s-]*picture)\b", re.I)
# "Ref for visual set up: https://..." / "Reference link : https://...": a label and a link, nothing to say
_LABELLED_URL_RE = re.compile(r"^[^:]{1,40}:\s*https?://\S+\s*$", re.I)
# "Top: Robot | Bottom: Lady 1" or "Left: A | Right: B" after a split-screen note
_PANELS_RE = re.compile(r"(top|left)\s*:\s*(.+?)\s*\|\s*(bottom|right)\s*:\s*(.+)$", re.I)
_SIDE_NAME_RE = re.compile(r"([A-Za-z][A-Za-z0-9 .'-]{0,30}?)\s*[-–—:]\s*(left|right|top|bottom)\s+(?:screen|side|panel)\b", re.I)
_CHAR_SIDE_RE = re.compile(r"\b(left|right|top|bottom)\s+(?:screen|side|panel)\b", re.I)
_WRONG_MARKS = "❌✗✘❎"
_RIGHT_MARKS = "✅✓✔☑"
# a parenthetical that only describes what is happening while people talk, so it is not a silent beat
_CONTEXT_DIR_RE = re.compile(r"\b(?:is|are)\s+(?:talking|speaking|saying)\b|\btalking\s+to\b|\bwhile\b", re.I)
# "(X Sign and Text Strikethrough)" after a spoken line: show that line's text struck through in red
_STRIKE_CUE_RE = re.compile(r"\s*[\(\[][^\)\]]*strike[^\)\]]*[\)\]]\s*$", re.I)
# "[TEXT: \"phrase\" | strike | x]": explicit on-screen text cue (only the strike option is rendered so far)
_TEXT_CUE_RE = re.compile(r"\s*\[\s*TEXT\s*:\s*(?P<body>[^\]]*)\]\s*$", re.I)
# "horizontal split screen" means the divider line runs horizontally (top / bottom panels); "vertical split" is left / right
SCRIPT_SPLIT_LAYOUT = {"horizontal": "split_v", "vertical": "split_h", "default": "split_v"}


def panels_from_text(text: str) -> Dict[str, str]:
    """Who sits where, from any wording a script uses: "Top: Robot | Bottom: Lady 1" or "Woman - Right screen | Miss Nova - Left screen"."""
    panels: Dict[str, str] = {}
    m = _PANELS_RE.search(text or "")
    if m:
        panels[m.group(1).lower()] = m.group(2).strip()
        panels[m.group(3).lower()] = m.group(4).strip()
    for m in _SIDE_NAME_RE.finditer(text or ""):
        panels.setdefault(m.group(2).lower(), m.group(1).strip())
    return panels


def split_layout_from_panels(panels: Dict[str, str]) -> str:
    """Panels named left/right make a `split_h`; named top/bottom make a `split_v`; '' when nothing is named."""
    if "left" in panels or "right" in panels:
        return "split_h"
    if "top" in panels or "bottom" in panels:
        return "split_v"
    return ""


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
    return _parse_script_raw(read_script_text(script_path), script_path.stem)


def _parse_script_raw(raw: str, title: str) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    """The parser behind parse_script_file, working on text so reference learning reads scripts exactly like a job does."""
    meta: Dict[str, Any] = {
        "title": title,
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
    current_panels: Dict[str, str] = {}
    pending_speaker = ""
    pending_note = ""

    for raw_line in raw.splitlines():
        s = raw_line.strip()
        if not s:
            if in_characters_block and meta["characters"]:
                in_characters_block = False   # the character list ends at the first blank line after it
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
        if _URL_ONLY_RE.match(s) or _LABELLED_URL_RE.match(s):
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
        if (in_characters_block and not s.startswith("--") and not _CHAR_DEF_RE.match(s)
                and not re.match(r"^Scene\s+\d+", s, re.I) and not _SCENE_DIR_RE.match(s)):   # "Lady 1: A lady in her 60s ..." or just "Female Humanoid Robot"
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
            current_panels = panels_from_text(current_visual)
            current_hint = split_layout_from_panels(current_panels) or layout_hint_from_direction(current_visual)
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
            current_panels = {}
            pending_speaker = pending_note = ""
            meta["scenes"].append({"scene": current_scene, "visual": current_visual})
            continue

        # 3c. Layout note ("Rectangle overlay, floating window ..., horizontal split screen ...") applies to the lines that follow
        if _LAYOUT_CUE_RE.match(s) and not re.match(r"^[A-Za-z][A-Za-z0-9_ ]{0,22}:\s", s):
            hint_l = layout_hint_from_direction(s)
            if hint_l:
                current_panels = panels_from_text(s)
                current_hint = split_layout_from_panels(current_panels) or hint_l
                continue

        # 4. Scene visual / camera direction block: "[Visual: ...]"
        m_vis_dir = re.match(r"^\[?\s*Visual\s*:\s*(.+?)\]?\s*$", s, re.I)
        if m_vis_dir:
            in_characters_block = False
            current_visual = m_vis_dir.group(1).strip()
            vis_panels = panels_from_text(current_visual)
            vis_hint = split_layout_from_panels(vis_panels) or layout_hint_from_direction(current_visual)
            if vis_hint:   # "[Visual: SCENE 3 - SPLIT SCREEN | Woman - Right screen | Miss Nova - Left screen]"
                current_hint, current_panels = vis_hint, vis_panels
            if meta["scenes"]:
                meta["scenes"][-1]["visual"] = current_visual
            else:
                meta["scenes"].append({"scene": current_scene, "visual": current_visual})
            continue

        # 5. Standalone speaker line (e.g. "Miss Nova:" or "Ravi (He's speaking in pauses...):")
        m_standalone_spk = re.match(r"^([A-Za-z](?:[A-Za-z0-9_ .']|(?<=\w)-(?=\w)){0,24}?)\s*(?:\(([^)]*)\))?\s*:\s*$", s)
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
        emoji_marks = [c for c in rest if c in _WRONG_MARKS + _RIGHT_MARKS]
        if emoji_marks:   # "I am Delhi. ❌" / "I am from Delhi. ✅": the mark the line carries, removed from the spoken text
            mark = "wrong" if emoji_marks[-1] in _WRONG_MARKS else "right"
            rest = re.sub("[" + _WRONG_MARKS + _RIGHT_MARKS + "]\ufe0f?", "", rest).strip()
        m_mark = re.search(r"\[(WRONG|RIGHT)\]\s*$", rest, re.I)
        if m_mark:
            mark = m_mark.group(1).lower()
            rest = rest[: m_mark.start()].strip()
        elif mark is None:
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
            m_spk = re.match(r"^([A-Za-z](?:[A-Za-z0-9_ .']|(?<=\w)-(?=\w)){0,22}?)\s*(?:\(([^)]*)\))?\s*:\s*(.+)$", rest)
            if m_spk and not m_spk.group(1).lower().startswith(("http", "concept", "visual", "scene")):
                speaker = m_spk.group(1).strip()
                note = (m_spk.group(2) or note or "").strip()
                text = m_spk.group(3).strip().lstrip(":").strip()
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
            "panels": dict(current_panels),
            "strike_text": (strike_override or text) if (strike_cue and not beat) else "",
        })

    char_side = {}
    for cname, cdesc in meta["characters"].items():
        m_side = _CHAR_SIDE_RE.search(cdesc or "")
        if m_side:
            char_side[cname] = m_side.group(1).lower()
    for l in lines:
        if l.get("layout_hint") and char_side:
            for cname, side in char_side.items():
                if side not in l["panels"] and cname not in l["panels"].values() and split_layout_from_panels({side: cname}) == l["layout_hint"]:
                    l["panels"][side] = cname
    panel_names = {v for l in lines for v in (l.get("panels") or {}).values()}   # "Top: Robot" names a character too
    names = sorted({l["speaker"] for l in lines if l.get("speaker")} | set(meta["characters"]) | panel_names, key=lambda n: -len(n.split()))
    canon = {}
    for n in names:
        words = set(n.lower().split())
        longer = next((m for m in names if m != n and len(m.split()) > len(n.split()) and words < set(m.lower().split())), None)
        if longer:
            canon[n] = longer
    for l in lines:
        l["speaker"] = canon.get(l.get("speaker", ""), l.get("speaker", ""))
        l["panels"] = {k: canon.get(v, v) for k, v in l.get("panels", {}).items()}

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
    stale = ensure_cache_current(work_dir)
    if stale:
        print(f"  [cache] {clip_stem}: rebuilding {', '.join(stale)} (code changed or file damaged)")
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

    # A clip with no sound at all has nothing to transcribe: Gemini would only invent words from the script reference.
    no_sound = not hq_words and not base_words and wav_path.is_file() and clip_has_no_sound(wav_path)
    if no_sound:
        print(f"  [{clip_stem}] no sound in this clip: not sent for transcription")

    # If neither hq_words nor base_words is cached, invoke align_engine or transcribe scripts
    if not hq_words and not base_words and wav_path.is_file() and not no_sound:
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
    try:
        import events

        events.emit("speech", f"{clip_stem}: {len(hq_words)} words transcribed and aligned" if hq_words else f"{clip_stem}: no speech found", clip=clip_stem, words=len(hq_words))
    except Exception:
        pass
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




SAME_TAKE_THRESHOLD = 0.72



































REACTION_RE = re.compile(r"laugh|chuckl|giggl|smirk|cry|cries|crying|sob|tears|sigh|gasp|shock|smile|grin|react|scream|hug|cheer", re.I)
EMOTION_RE = re.compile(r"cry|cries|crying|sob|tears|sad|heartbroken|emotional|overwhelm|longing|lost in thought|hurt|embarrass|hides? (?:his|her|their)? ?face|looks? down", re.I)
PAUSE_NOTE_RE = re.compile(r"pause|silen|stare|staring|awkward", re.I)










LISTENER_LOOK_MAX_DISTANCE = 0.40   # colour-signature distance (0 same look, about 0.1-0.25 same character, 1 clearly different)
LISTENER_LOOK_MARGIN = 0.15         # the best match must beat the next character by this much












def clip_is_silent(c: Dict[str, Any]) -> bool:
    """A measurement: this clip has (almost) no speech. Steady room noise that Gemini heard as words counts as silent."""
    ws = c.get("hq_words") or []
    dur = float((c.get("info") or {}).get("duration") or 1.0)
    dbs = sorted(db for _, db in (c.get("rms_env") or []))
    if dbs:
        p50, p90 = dbs[len(dbs) // 2], dbs[min(len(dbs) - 1, int(len(dbs) * 0.90))]
        if (p90 - p50) <= 4.2 and sum(1 for db in dbs if db > -28.0) / len(dbs) >= 0.90:
            return True
    if not ws:
        return True
    return sum(float(w["end"]) - float(w["start"]) for w in ws) / max(0.1, dur) < 0.08


def voiced_spans(rms_env: List[Tuple[float, float]], floor_db: float = -48.0, merge_gap: float = 0.25) -> List[Tuple[float, float]]:
    """Where there is real sound: stretches louder than `floor_db`, merged across gaps shorter than `merge_gap`. Measured from the audio,
    so it stays right when a word time from the aligner is off."""
    spans: List[List[float]] = []
    for t, db in rms_env:
        if db > floor_db:
            if spans and t - spans[-1][1] <= merge_gap:
                spans[-1][1] = t
            else:
                spans.append([t, t])
    return [(round(a, 2), round(b + 0.01, 2)) for a, b in spans if b - a >= 0.08]


def clip_has_no_sound(wav_path: Path, rms_env: Optional[List[Tuple[float, float]]] = None) -> bool:
    """True only when the audio holds nothing: no stretch of real sound, and the voice detector (when installed) hears no speech. Quiet speech
    still counts as sound, so a real take is never treated as empty."""
    env = rms_env if rms_env is not None else compute_10ms_rms_envelope(wav_path)
    if not env or voiced_spans(env):
        return False
    return not _speech_spans_of({"work_dir": Path(wav_path).parent})


def silent_word_runs(words: List[Dict[str, Any]], flagged: List[Dict[str, Any]]) -> List[str]:
    """One line per unbroken run of words sitting in silence. The words and their times are already listed above, so naming the first
    and the last word of each run says exactly which ones are wrong."""
    bad = {id(w) for w in flagged}
    lines: List[str] = []
    run: List[Dict[str, Any]] = []
    for w in list(words) + [None]:
        if w is not None and id(w) in bad:
            run.append(w)
            continue
        if run:
            a, b = run[0], run[-1]
            who = f"'{a['w']}' at {float(a['start']):.2f}-{float(a['end']):.2f}" if len(run) == 1 else (
                f"{len(run)} words from '{a['w']}' ({float(a['start']):.2f}) to '{b['w']}' ({float(b['end']):.2f})")
            lines.append(f"  ! TIMING WRONG: {who}: that stretch is silent; the real place is in a nearby voiced span")
            run = []
    return lines


def words_in_silence(words: List[Dict[str, Any]], rms_env: List[Tuple[float, float]], floor_db: float = -55.0) -> List[Dict[str, Any]]:
    """Words whose aligned time holds no sound at all: the aligner put them in the wrong place."""
    out = []
    for w in words:
        a, b = float(w["start"]), float(w["end"])
        loud = max((db for t, db in rms_env if a <= t <= b), default=-140.0)
        if loud < floor_db:
            out.append(w)
    return out


def _speech_spans_of(c: Dict[str, Any]) -> List[Tuple[float, float]]:
    """Where someone talks in this clip, cached next to its audio. [] when the voice detector is not installed."""
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
    """For a silent clip: which speaking clip shows the same person and place, by colour layout (a measurement, not a decision)."""
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


def emphasis_words(rms_env: List[Tuple[float, float]], words: List[Dict[str, Any]], max_n: int = 6, min_delta_db: float = 4.0) -> List[Dict[str, Any]]:
    """The words the speaker stressed: each word's peak loudness against the median loudness of the clip's speech. A measurement the
    Director uses to place a rapid zoom or an effect on the exact word. In a noisy clip the median is high, so weak peaks do not show."""
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
    """For a zoom of `z` the visible window is [(1-1/z)*fx, (1-1/z)*fx + 1/z] of the frame width. The focus x range that keeps every face span
    (x0, x1) inside it, clamped to the 0.05-0.95 the renderer allows; None when no focus keeps them all (use z=1)."""
    k = 1.0 - 1.0 / z
    if k <= 0 or not spans:
        return None
    lo = max(0.05, (max(x1 for _, x1 in spans) - 1.0 / z) / k)
    hi = min(0.95, min(x0 for x0, _ in spans) / k)
    return (round(lo, 2), round(hi, 2)) if lo <= hi else None


def format_facts(clips_data: List[Dict[str, Any]]) -> str:
    """Everything measured about each clip, as text for the Director: `start word` for every word, a line break at each gap."""
    out = ["# FACTS: measured by Zeno. Times are seconds in the clip. `1.20 word` = the word starts at 1.20; `|2.31` = speech stops at 2.31.", ""]
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
            tags.append(f"face_top-bottom={float(bb[1]):.2f}-{float(bb[3]):.2f}")     # keep text and graphics below this line
        if clip_is_silent(c):
            tags.append("SILENT")
            owner = _silent_clip_owner(c, clips_data)
            if owner:
                tags.append(owner)
        out.append(f"## {name} ({' '.join(tags)})")
        try:
            import events

            _fs = [(round(float(f['bbox_norm'][0]), 2), round(float(f['bbox_norm'][2]), 2)) for f in (vis.get('faces') or []) if f.get('bbox_norm') and len(f['bbox_norm']) >= 4]
            events.emit("face", f"{name}: {vis.get('face_count', 0)} face(s), {vis.get('shot_type', 'unknown')}" + (", silent clip" if clip_is_silent(c) else ""), clip=name, faces=_fs, shot=vis.get('shot_type'), silent=clip_is_silent(c))
        except Exception:
            pass
        spans = [(float(f["bbox_norm"][0]), float(f["bbox_norm"][2])) for f in (vis.get("faces") or []) if f.get("bbox_norm") and len(f["bbox_norm"]) >= 4]
        if len(spans) >= 2:
            parts = []
            for z in (1.05, 1.08, 1.10):
                r = safe_zoom_focus(spans, z)
                parts.append(f"{z:g}: focus x {r[0]:.2f}-{r[1]:.2f}" if r else f"{z:g}: none (keep z=1)")
            out.append("  faces x " + ", ".join(f"{a:.2f}-{b:.2f}" for a, b in spans) + " | zoom that keeps every face in frame: " + "; ".join(parts))
        phrase: List[str] = []
        ws = c.get("hq_words") or []
        for i, w in enumerate(ws):
            phrase.append(f"{float(w['start']):.2f} {w['w']}")
            if i + 1 == len(ws) or float(ws[i + 1]["start"]) - float(w["end"]) >= 0.2:
                out.append("  " + " ".join(phrase) + f" |{float(w['end']):.2f}")
                phrase = []
        pauses = [f"{float(s['start']):.2f}-{float(s['end']):.2f}" for s in c.get("macro_silences") or []]
        if pauses:
            out.append("  pauses: " + " ".join(pauses))
        spoken = _speech_spans_of(c)
        if spoken:
            out.append("  speech (voice detector, works over noise): " + " ".join(f"{a:.2f}-{b:.2f}" for a, b in spoken))
            gaps = [(spoken[i][1], spoken[i + 1][0]) for i in range(len(spoken) - 1) if spoken[i + 1][0] - spoken[i][1] >= 0.25]
            if gaps:
                out.append("  speech gaps (pauses a cut can remove, also in noisy clips): " + " ".join(f"{a:.2f}-{b:.2f}({b - a:.2f}s)" for a, b in gaps))
            for w in ws:
                mid = (float(w["start"]) + float(w["end"])) / 2
                if not any(a - 0.08 <= mid <= b + 0.08 for a, b in spoken):
                    out.append(f"  ! NO SPEECH under '{w['w']}' ({float(w['start']):.2f}-{float(w['end']):.2f}): likely a transcription ghost or a mistimed word; do not build a cut on it")
        env = c.get("rms_env") or []
        stress = emphasis_words(env, ws) if env else []
        if stress:
            out.append("  emphasis (louder than this speaker's usual; put a rapid zoom or an effect on these words): " + " ".join(f"'{x['w']}'@{x['start']:.2f}(+{x['delta_db']}dB)" for x in stress))
        if env:
            out.append("  voiced (real sound, measured from the audio): " + " ".join(f"{a:.2f}-{b:.2f}" for a, b in voiced_spans(env)))
            out.extend(silent_word_runs(ws, words_in_silence(ws, env)))
        for a in c.get("anomalies") or []:
            out.append(f"  ! {a}")
        out.append("")
    return "\n".join(out)


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


def common_unique_output(name: str) -> Path:
    from common import unique_job_output

    return unique_job_output(name)


def common_job_start() -> float:
    from common import job_start_epoch

    return job_start_epoch()


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
    ap.add_argument("--name", default="", help="Notion name for this video; its outputs go to Zeno Outputs/<name>/ (reel_<name>.mp4 and SQ_<name>.mp4)")
    ap.add_argument("--style", default="", help="Style this job follows (a folder under styles/, for example micronarrative). Written into the plan as '#style <name>' so the job counts toward that style's readiness and the team's learning")
    ap.add_argument(
        "--fresh",
        action="store_true",
        help="Rebuild every cached transcript and alignment for these clips (rarely needed: caches update themselves when the code changes)",
    )
    ap.add_argument(
        "--clean-raw-json",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Keep work/ lean by preventing raw intermediate JSON bloat",
    )
    args = ap.parse_args()
    if args.fresh:
        os.environ["ZENO_FRESH"] = "1"
    job_start_epoch = common_job_start()

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
        if hq_words and wav_path.is_file() and clip_has_no_sound(wav_path, rms_env):
            print(f"  [{stem}] no sound in this clip: ignoring {len(hq_words)} transcribed words")
            hq_words, base_words = [], []
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

    # Zeno stops here: it has measured. Every editing decision is the Director's (see agent/EDIT_RULES.md).
    for c in clips_data:
        c["is_listening"] = clip_is_silent(c)
        c["matched_lines"] = []

    first_info = clips_data[0]["info"]
    canvas = {
        "width": int(first_info.get("width") or 720),
        "height": int(first_info.get("height") or 1280),
        "fps": int(round(float(first_info.get("fps") or 24))),
    }
    plan: Dict[str, Any] = {
        "output": common_unique_output(args.name or project_name).as_posix(),
        "script": str(script_path.relative_to(ROOT)) if script_path.is_relative_to(ROOT) else str(script_path),
        "canvas": canvas,
        "preset": args.preset if args.preset != "auto" else "hybrid",
        "mark_pack": args.mark_pack,
        **({"style": re.sub(r"[^a-z0-9_-]", "", args.style.lower())} if getattr(args, "style", "") else {}),
        "segments": [],
        "marks": [],
        "audio": {"loudness": -11.5},
    }
    proj_dir = WORK / project_name
    proj_dir.mkdir(parents=True, exist_ok=True)
    skeleton = edl_parser.format_edl(plan)
    folders = {str(Path(str(c["src"])).parent) for c in clips_data}
    if len(folders) == 1 and "#src_dir" not in skeleton:
        lines = skeleton.split("\n")
        lines.insert(1, f'#src_dir "{folders.pop()}"')
        skeleton = "\n".join(lines)
    for name in ("plan.edl", "plan.auto.edl"):
        (proj_dir / name).write_text(skeleton, encoding="utf-8")
    write_json(proj_dir / "plan.json", plan)

    import clip_sheet
    import script_order

    facts = format_facts(clips_data)
    (proj_dir / "facts.md").write_text(facts, encoding="utf-8")
    (proj_dir / "line_map.md").write_text(script_order.build_line_map(script_lines, clips_data, style=args.style or "micronarrative"), encoding="utf-8")
    try:
        sheets = clip_sheet.build(proj_dir, [str(resolve(c["src"])) for c in clips_data])
    except Exception as exc:   # noqa: BLE001  (a picture is a help, not a requirement)
        sheets = []
        print(f"  ! clip sheet not made: {exc}")

    segments: List[Dict[str, Any]] = []
    marks: List[Dict[str, Any]] = []
    print(f"Measured {len(clips_data)} clips. Nothing is decided yet: the Director writes plan.edl.")
    print(f"  Script + clips per line: {proj_dir / 'line_map.md'}")
    print(f"  Every word, pause, face : {proj_dir / 'facts.md'} (~{len(facts) // 3} tokens)")
    print(f"  Clip pictures           : {len(sheets)} sheet(s) in {proj_dir}")
    print(f"  Plan to write           : {proj_dir / 'plan.edl'}")
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
