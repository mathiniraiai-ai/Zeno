#!/usr/bin/env python3
"""Format library and catalog selector for Zeno.

Loads measured and defined layout recipes from `formats/layouts/<id>.json`, matches script
stage directions to layout hints, detects the single best catalog format ID for a job, and
loads standalone editing cards from `formats/catalog/<format_id>.md`.

CLI usage:
    python3 scripts/format_library.py list
    python3 scripts/format_library.py search "horizontal split screen"
    python3 scripts/format_library.py show split_h
    python3 scripts/format_library.py card live_conversation
    python3 scripts/format_library.py sync
    python3 scripts/format_library.py extract <video> --id split_h --layout split_h --start 37 --end 93 \\
        --names "vertical split screen,side by side" --description "..." [--thumbs]
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))
try:
    import common  # type: ignore
    ROOT = common.ROOT
    WORK_DIR = getattr(common, "WORK", ROOT / "work")
except ImportError:
    common = None  # type: ignore
    ROOT = Path(__file__).resolve().parent.parent
    WORK_DIR = ROOT / "work"

LIB_DIR = ROOT / "formats" / "layouts"
CATALOG_DIR = ROOT / "formats" / "catalog"
REGISTRY_PATH = ROOT / "formats" / "registry.json"

ALIAS_SCORE = 3.0
MATCH_THRESHOLD = 3.0

CATALOG_FORMAT_IDS = (
    "skit",
    "direct_hook",
    "street_interview",
    "stop_saying",
    "live_conversation",
    "split_screen",
    "scrolling_text",
    "translate_drill",
    "story_pitch",
    "realistic_to_3d",
    "3d_to_2d",
)

_SPLIT_LAYOUT_SET = {"split_h", "split_v", "video_call", "top_pip"}
_SPLIT_EXPLICIT_STOP_CUES = (
    "stop saying",
    "never say",
    "never to say",
    "struck out",
    "strikethrough",
    "strike-through",
    "strike through",
    "red x",
    "x sign",
    "strike_text=",
)
_FULLSCREEN_TAKEOVER_RE = re.compile(
    r"\bfull[\s-]*screen\b|\btakes?\s+over(?:\s+the)?\s+(?:full\s+)?screen|\bgoes?\s+full[\s-]*screen\b"
    r"|\bselfie\b|\btalking\s+to\s+camera\b|\bstraight\s+to\s+camera\b|\btalking\s+head\b"
    r"|\baddresses\s+the\s+camera\b|\blooking\s+at\s+the\s+camera\b",
    re.I,
)
_STOP_SAYING_PHRASES = (
    "stop saying",
    "never say",
    "never to say",
    "don't say",
    "do not say",
    "struck out",
    "red x",
    "x sign",
    "(x sign",
    "strike_text=",
)
_NEVER_EVER_SAY_RE = re.compile(r"(?<![a-z0-9])never(?:,\s*ever|\s+ever)?\s+(?:to\s+)?say(?![a-z0-9])")
_STRIKE_CUES_RE = re.compile(r"strikethrough|strike[\s-]+through|struck[\s-]+out|red\s+x|x\s+sign|strike_text=")
_WRONG_MARK_TAG_RE = re.compile(r"(?:\[\s*wrong\s*\]|mark:wrong\b|\u274c)", re.I)
_TEACHING_CORRECTION_CUES = (
    "instead of",
    "better way",
    "vocabulary",
    "basic english",
    "ai teacher",
    "spoken english",
    "practice",
    "practise",
    "correct",
    "never",
    "don't",
    "try:",
    "say:",
    "ಹೇಳ್ಬೇಡ",
)
_STREET_INTERVIEW_PHRASES = (
    "street interview",
    "vox pop",
    "vox-pop",
    "public interview",
    "interview on street",
    "interview on the street",
    "asking strangers",
)
_STREET_INTERVIEW_CONTEXT_WORDS = (
    "street",
    "on the street",
    "mic",
    "microphone",
    "public",
    "vox pop",
    "vox-pop",
    "strangers",
    "passerby",
    "passersby",
)
_FLASHCARD_FORMAT_RE = re.compile(
    r"(?<![a-z0-9])(?:"
    r"(?:split[\s-]+screen\s+with\s+flash\s*cards?"
    r"|flash\s*cards?\s+split"
    r"|flash\s*card\s+text"
    r"|flash\s*cards?\s+drill"
    r"|translate\s+drill"
    r"|how\s+would\s+you\s+translate)(?![a-z0-9])"
    r"|flash\s*card(?:\s+(?:text|prompt|overlay))?\s*:"
    r"|card\s+text\s*:"
    r")",
    re.I,
)
_HEADER_FLASHCARD_RE = re.compile(r"(?<![a-z0-9])flash\s*cards?(?![a-z0-9])", re.I)


def load_recipes(lib_dir: Path = LIB_DIR) -> List[Dict[str, Any]]:
    """Load layout recipes from `formats/layouts/*.json` (plus optional `work/_knowledge/format/*.json` overrides)."""
    paths = sorted(lib_dir.rglob("*.json")) if lib_dir.is_dir() else []
    extra_dir = WORK_DIR / "_knowledge" / "format"
    if lib_dir == LIB_DIR and extra_dir.is_dir():
        paths += sorted(extra_dir.glob("*.json"))
    by_id: Dict[str, Dict[str, Any]] = {}
    for p in paths:
        try:
            r = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(r, dict):
            by_id[str(r.get("id") or p.stem)] = r
    return list(by_id.values())


def _words(text: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", (text or "").lower()))


def _has_phrase(blob: str, phrase: str) -> bool:
    p = (phrase or "").strip().lower()
    if not p:
        return False
    left = r"(?<![a-z0-9])" if p[0].isalnum() else ""
    right = r"(?![a-z0-9])" if p[-1].isalnum() else ""
    return bool(re.search(left + re.escape(p) + right, blob or "", re.I))


_SIDE_BY_SIDE_RE = re.compile(r"side[\s-]by[\s-]side", re.I)
# Words that make "side by side" a SCREEN layout ("side by side split", "two videos side by side", "panels side by side").
_SCREEN_LAYOUT_WORDS = frozenset({
    "split", "screen", "screens", "panel", "panels", "pane", "panes", "window", "windows",
    "layout", "layouts", "video", "videos", "clip", "clips",
})


def scrub_physical_side_by_side(text: str) -> str:
    """Blank out "side by side" when it describes where people/objects are (fly side by side in two jets, stand side by side)
    rather than a screen layout, so scene descriptions never trigger `split_h` or the split-screen formats.
    Kept when 3 words either side mention a screen/layout word (split, screen, panels, windows, videos, ...)."""
    src = str(text or "")

    def _repl(m: "re.Match[str]") -> str:
        before = re.findall(r"[a-z]+", src[: m.start()].lower())[-3:]
        after = re.findall(r"[a-z]+", src[m.end(): m.end() + 80].lower())[:3]
        return m.group(0) if any(w in _SCREEN_LAYOUT_WORDS for w in before + after) else " "

    return _SIDE_BY_SIDE_RE.sub(_repl, src)


def search(query: str, k: int = 3, lib_dir: Path = LIB_DIR) -> List[Dict[str, Any]]:
    """Rank layout recipes for a piece of script wording. Each result carries a numeric `score`."""
    q = scrub_physical_side_by_side(query or "").lower().strip()
    qw = _words(q)
    hits: List[Dict[str, Any]] = []
    for r in load_recipes(lib_dir):
        rid = str(r.get("id", "")).lower()
        score = 1.0 if q and q == rid else 0.0
        for alias in r.get("names", []):
            astr = str(alias)
            if _has_phrase(q, astr):
                score += ALIAS_SCORE + 0.05 * max(0, len(astr.split()) - 1)
        doc = _words(
            " ".join([
                rid,
                str(r.get("description", "")),
                str(r.get("when", "")),
                " ".join(str(x) for x in r.get("names", [])),
            ])
        )
        score += 0.2 * len(qw & doc)
        if score > 0:
            hits.append({**r, "score": round(score, 2)})
    return sorted(hits, key=lambda h: -h["score"])[:k]


def best_layout(text: str, lib_dir: Path = LIB_DIR) -> str:
    """Return the Zeno layout (`split_h`, `split_v`, `video_call`, `top_pip`, `chat_bar`, ...) matching `text`, or `''`."""
    hits = search(text, 1, lib_dir)
    return str(hits[0]["layout"]) if hits and hits[0]["score"] >= MATCH_THRESHOLD else ""


def recipe_text(r: Dict[str, Any]) -> str:
    """Format a compact single-paragraph summary of a layout recipe."""
    geo = r.get("geometry") or {}
    nums = "; ".join(f"{k}={v}" for k, v in geo.items())
    return (
        f"{r['id']} -> layout {r['layout']} [{r.get('confidence', 'defined')}]: {r.get('description', '')} "
        f"Use when: {r.get('when', '-')}. {nums}. Source: {r.get('source', '-')}."
    ).strip()


# ----------------------------------------------------------------------------- Catalog format detection & loading
def _load_registry_aliases() -> Dict[str, List[str]]:
    """Load optional format alias lists from `formats/registry.json` when available."""
    if not REGISTRY_PATH.is_file():
        return {}
    try:
        data = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    out: Dict[str, List[str]] = {}
    items = data.get("formats", []) if isinstance(data, dict) else []
    if isinstance(items, dict):
        for fid, entry in items.items():
            if fid in CATALOG_FORMAT_IDS and isinstance(entry, dict):
                aliases = entry.get("names") or entry.get("aliases") or entry.get("keywords") or []
                out[fid] = [str(a) for a in aliases if a]
    elif isinstance(items, list):
        for entry in items:
            if isinstance(entry, dict) and entry.get("id") in CATALOG_FORMAT_IDS:
                aliases = entry.get("names") or entry.get("aliases") or entry.get("keywords") or []
                out[str(entry["id"])] = [str(a) for a in aliases if a]
    return out


def _normalize_explicit_id(raw_val: Any) -> str:
    val = str(raw_val or "").strip().lower()
    if not val:
        return ""
    for fid in CATALOG_FORMAT_IDS:
        if re.search(r"(?<![a-z0-9_])" + re.escape(fid) + r"(?![a-z0-9_])", val):
            return fid
    return ""


def detect_catalog_format(
    meta: dict,
    plan: dict,
    style: str = "",
    script_text: str = "",
) -> str:
    """Detect and return the single best catalog `format_id` for a project."""
    meta = meta if isinstance(meta, dict) else {}
    plan = plan if isinstance(plan, dict) else {}

    # 1. Explicit page scroll directive or explicit format field in plan/meta
    if plan.get("page"):
        return "scrolling_text"
    for candidate in (plan.get("format"), meta.get("format")):
        explicit = _normalize_explicit_id(candidate)
        if explicit:
            return explicit

    chars = meta.get("characters") if isinstance(meta.get("characters"), dict) else {}
    scenes = [sc for sc in (meta.get("scenes") or []) if isinstance(sc, dict)]
    setup_notes = [str(x) for x in (meta.get("setup") or [])]

    header_blob = " ".join([
        str(style or ""),
        str(plan.get("style") or ""),
        str(plan.get("preset") or ""),
        str(meta.get("title") or ""),
        str(meta.get("project_name") or ""),
        str(meta.get("header") or ""),
        str(meta.get("concept_brief") or ""),
        str(meta.get("format_hint") or ""),
    ]).lower()

    chars_blob = " ".join(f"{k} {v}" for k, v in chars.items()).lower()
    scenes_blob = " ".join(
        f"{sc.get('name', '')} {sc.get('visual', '')} {sc.get('layout_hint', '')}"
        for sc in scenes
    ).lower()
    extra_meta_cues: List[str] = []
    if meta.get("strike_text"):
        st_val = meta["strike_text"]
        if isinstance(st_val, str):
            extra_meta_cues.append(f"strike_text={st_val}")
        elif isinstance(st_val, (list, tuple)):
            extra_meta_cues.extend(f"strike_text={x}" for x in st_val if x)
    if meta.get("script_text"):
        extra_meta_cues.append(str(meta["script_text"]))
    for ln_obj in meta.get("lines") or []:
        if isinstance(ln_obj, dict):
            if ln_obj.get("strike_text"):
                extra_meta_cues.append(f"strike_text={ln_obj['strike_text']}")
            if ln_obj.get("mark"):
                extra_meta_cues.append(f"[{ln_obj['mark']}]")
            if ln_obj.get("note"):
                extra_meta_cues.append(str(ln_obj["note"]))
            if ln_obj.get("text"):
                extra_meta_cues.append(str(ln_obj["text"]))

    body_blob = " ".join([" ".join(setup_notes), " ".join(extra_meta_cues), str(script_text or "")]).lower()
    blob = scrub_physical_side_by_side(" ".join([header_blob, chars_blob, scenes_blob, body_blob]).strip())

    # Check explicit snake_case format IDs written in header or script analysis tags
    for fid in CATALOG_FORMAT_IDS:
        if "_" in fid and re.search(r"(?<![a-z0-9_])" + re.escape(fid) + r"(?![a-z0-9_])", blob):
            return fid

    # 2. Scrolling text
    if any(_has_phrase(blob, p) for p in (
        "scrolling text",
        "scrolling subtitles",
        "scroll page",
        "scrolling transcript",
        "#page scroll",
    )):
        return "scrolling_text"

    # 3. Generation / animation remake wrappers when explicitly stated
    if any(_has_phrase(blob, p) for p in ("3d to 2d", "3d-to-2d", "remade as 2d", "2d remake")):
        return "3d_to_2d"
    if any(_has_phrase(blob, p) for p in (
        "realistic video to 3d",
        "realistic to 3d",
        "live to 3d",
        "live-action to 3d",
        "live action to 3d",
        "remade as 3d",
        "3d remake",
    )):
        return "realistic_to_3d"

    # Gather layout signals from plan segments and script wording
    segs = [s for s in (plan.get("segments") or []) if isinstance(s, dict)]
    seg_layouts = [str(s.get("layout") or "single").split(":")[0].strip().lower() for s in segs]
    has_split_segs = any(lay in _SPLIT_LAYOUT_SET for lay in seg_layouts)
    all_split_segs = bool(seg_layouts) and all(lay in _SPLIT_LAYOUT_SET for lay in seg_layouts)

    has_split_words = any(_has_phrase(blob, p) for p in (
        "split screen",
        "horizontal split",
        "vertical split",
        "left screen",
        "right screen",
        "top screen",
        "bottom screen",
        "side by side",
        "side-by-side",
        "top_pip",
        "top pip",
    )) or (
        bool(re.search(r"\btop\s*:", blob)) and bool(re.search(r"\bbottom\s*:", blob))
    ) or (
        bool(re.search(r"\bleft\s*:", blob)) and bool(re.search(r"\bright\s*:", blob))
    )
    has_split = has_split_segs or has_split_words

    # 4. Translate / Flashcard drill (both split-screen flashcard drills and solo translate drills)
    has_flashcards = (
        bool(meta.get("flashcards"))
        or any(isinstance(ln_obj, dict) and ln_obj.get("flashcard") for ln_obj in (meta.get("lines") or []))
        or any(isinstance(s, dict) and (s.get("flashcards") or s.get("cards")) for s in segs)
    )
    if (
        has_flashcards
        or bool(_FLASHCARD_FORMAT_RE.search(blob))
        or bool(_HEADER_FLASHCARD_RE.search(header_blob))
        or (
            (_has_phrase(blob, "how do you say") or _has_phrase(blob, "say it with me"))
            and not has_split
        )
    ):
        return "translate_drill"

    # 5. Stop saying / Never say micro-narrative
    strike_hits = len(_STRIKE_CUES_RE.findall(blob))
    cross_hits = blob.count("\u274c")
    wrong_mark_lines = sum(
        1
        for ln_obj in (meta.get("lines") or [])
        if isinstance(ln_obj, dict) and str(ln_obj.get("mark") or "").strip().lower() in ("wrong", "x", "cross")
    )
    wrong_indicators = max(wrong_mark_lines, len(_WRONG_MARK_TAG_RE.findall(blob)))
    has_teaching_cue = any(_has_phrase(blob, p) for p in _TEACHING_CORRECTION_CUES)
    if has_split:
        is_stop_saying = (
            any(_has_phrase(blob, p) for p in _SPLIT_EXPLICIT_STOP_CUES)
            or bool(_NEVER_EVER_SAY_RE.search(blob))
            or strike_hits >= 1
        )
    else:
        is_stop_saying = (
            any(_has_phrase(blob, p) for p in _STOP_SAYING_PHRASES)
            or bool(_NEVER_EVER_SAY_RE.search(blob))
            or strike_hits >= 2
            or cross_hits >= 2
            or wrong_mark_lines >= 3
            or (wrong_indicators >= 2 and has_teaching_cue)
        )
    if is_stop_saying:
        return "stop_saying"

    # 6. Street interview micro-narrative (never triggered by bare "interview" like "visa interview")
    if any(_has_phrase(blob, p) for p in _STREET_INTERVIEW_PHRASES) or (
        _has_phrase(chars_blob, "interviewer")
        or (
            _has_phrase(blob, "interview")
            and any(_has_phrase(blob, w) for w in _STREET_INTERVIEW_CONTEXT_WORDS)
        )
    ):
        return "street_interview"

    # 7. Story-based direct pitch
    if any(_has_phrase(blob, p) for p in ("story pitch", "direct pitch", "story based", "story-based")) or (
        _has_phrase(blob, "flashback") and not has_split
    ):
        return "story_pitch"

    # 8. Pure split screen (entire video in split / video_call without a full-frame story scene)
    scene_hints = [str(sc.get("layout_hint") or "").lower() for sc in scenes]
    scene_is_split = [
        h in _SPLIT_LAYOUT_SET or any(k in str(sc.get("visual") or "").lower() for k in ("split", "video call", "left:", "top:"))
        for sc, h in zip(scenes, scene_hints)
    ]
    all_scenes_split = bool(scenes) and all(scene_is_split)
    has_fullscreen_takeover = any(
        h == "single" or bool(_FULLSCREEN_TAKEOVER_RE.search(str(sc.get("visual") or "")))
        for sc, h in zip(scenes, scene_hints)
    ) or bool(re.search(r"\b(?:takes?\s+over(?:\s+the)?\s+(?:full\s+)?screen|goes?\s+full[\s-]*screen)\b", body_blob, re.I))
    has_non_split_opening = any(not is_sp for is_sp in scene_is_split[: scene_is_split.index(True)]) if any(scene_is_split) else False
    if (
        all_split_segs
        or all_scenes_split
        or (
            (_has_phrase(header_blob, "split screen") or (_has_phrase(blob, "split screen") and not has_non_split_opening))
            and not has_fullscreen_takeover
        )
        or (_has_phrase(blob, "video call") and not scenes and not has_split_words)
    ):
        return "split_screen"

    # 9. Live conversation (AI teacher & human share the same frame or tablet/hologram; never split)
    if not has_split and any(_has_phrase(blob, p) for p in (
        "live conversation",
        "live grammar correction",
        "grammar correction",
        "speak more politely",
        "hologram",
        "tablet",
        "giant phone",
        "interactive",
        "they continue talking",
        "same shot",
        "same frame",
        "stands beside",
    )):
        return "live_conversation"

    # 10. Direct hook micro-narrative (solo talking-head / selfie hook into AI split)
    first_scene_vis = str(scenes[0].get("visual") or "").lower() if scenes else header_blob
    if _has_phrase(blob, "direct hook") or (
        has_split
        and any(_has_phrase(first_scene_vis + " " + header_blob, p) for p in (
            "selfie",
            "talking to camera",
            "straight to camera",
            "talking head",
            "addresses the camera",
            "looking at the camera",
        ))
    ):
        return "direct_hook"

    # Optional registry header-alias match (excluding broad split_screen phrase on multi-scene skits
    # and bare "interview" on non-street skits)
    for fid, aliases in _load_registry_aliases().items():
        if fid in ("skit", "split_screen"):
            continue
        if any(
            _has_phrase(header_blob, a)
            for a in aliases
            if not (fid == "street_interview" and a.strip().lower() == "interview")
        ):
            return fid

    return "skit"


def load_catalog_card(format_id: str) -> str:
    """Read and return `formats/catalog/<format_id>.md` (or `''` if not found)."""
    clean_id = Path(str(format_id or "").strip()).stem
    if not clean_id:
        return ""
    card_path = CATALOG_DIR / f"{clean_id}.md"
    if not card_path.is_file():
        return ""
    return card_path.read_text(encoding="utf-8")


# ----------------------------------------------------------------------------- Local reference video measurement
def _pct(vals: List[float]) -> Optional[List[float]]:
    if not vals:
        return None
    v = sorted(vals)
    pick = lambda q: round(v[min(len(v) - 1, int(q * (len(v) - 1) + 0.5))], 3)
    return [pick(0.25), pick(0.5), pick(0.75)]


def _seam_position(frame: Any, kind: str) -> Optional[float]:
    """Locate a baked split seam as a fraction of frame width (`split_h_baked`) or height (`split_v_baked`)."""
    import numpy as np

    gray = frame.mean(axis=2).astype(np.float32)
    h, w = gray.shape
    if kind == "split_h_baked":
        d = np.mean(np.abs(np.diff(gray, axis=1)), axis=0)
        lo, hi = int(w * 0.35), int(w * 0.65)
        return round((lo + int(np.argmax(d[lo:hi]))) / w, 4)
    d = np.mean(np.abs(np.diff(gray, axis=0)), axis=1)
    lo, hi = int(h * 0.35), int(h * 0.65)
    return round((lo + int(np.argmax(d[lo:hi]))) / h, 4)


def measure_video(
    video: Path,
    start: float = 0.0,
    end: Optional[float] = None,
    n_frames: int = 24,
    thumbs_to: Optional[Path] = None,
) -> Dict[str, Any]:
    """Sample frames across `[start, end]` and measure split seams, faces, framing, and motion locally."""
    import cv2
    import numpy as np
    import vision_tracker  # type: ignore

    cap = cv2.VideoCapture(str(video))
    if not cap.isOpened():
        raise SystemExit(f"cannot open {video}")
    fps = cap.get(cv2.CAP_PROP_FPS) or 24.0
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    w, h = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)), int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    dur = total / fps if total else 0.0
    t0 = max(0.0, start)
    t1 = min(end if end else dur, dur) if dur else (end or 0.0)
    times = [t0 + (t1 - t0) * (i + 0.5) / n_frames for i in range(n_frames)]

    seams: List[str] = []
    seam_pos: List[float] = []
    counts: List[int] = []
    widths: List[float] = []
    eye_y: List[float] = []
    heads: List[float] = []
    cx: List[float] = []
    motion: List[float] = []
    prev = None
    saved = 0

    for t in times:
        cap.set(cv2.CAP_PROP_POS_MSEC, t * 1000.0)
        ok, frame = cap.read()
        if not ok:
            continue
        seam = vision_tracker.detect_baked_split_seam(frame)
        seams.append(seam or "none")
        if seam:
            pos = _seam_position(frame, seam)
            if pos is not None:
                seam_pos.append(pos)
        faces = vision_tracker.detect_faces_in_frame(frame)
        counts.append(len(faces))
        for f in faces:
            x0, y0, x1, y1 = f["bbox_norm"]
            widths.append(x1 - x0)
            heads.append(y0)
            eye_y.append(f["eye_center_norm"][1])
            cx.append((x0 + x1) / 2)
        small = cv2.resize(frame, (90, 160)).astype(np.float32)
        if prev is not None:
            motion.append(float(np.mean(np.abs(small - prev)) / 255.0))
        prev = small
        if thumbs_to is not None and saved < 3 and len(seams) % max(1, n_frames // 3) == 0:
            thumbs_to.parent.mkdir(parents=True, exist_ok=True)
            cv2.imwrite(
                str(thumbs_to.with_name(f"{thumbs_to.name}_{saved + 1}.jpg")),
                cv2.resize(frame, (180, 320)),
                [cv2.IMWRITE_JPEG_QUALITY, 70],
            )
            saved += 1
    cap.release()

    kinds = {k: seams.count(k) for k in set(seams)}
    top = max(kinds, key=kinds.get) if kinds else "none"
    return {
        "video": video.name,
        "size": f"{w}x{h}",
        "fps": round(fps, 2),
        "window_sec": [round(t0, 1), round(t1, 1)],
        "frames_sampled": len(seams),
        "seam": {
            "kind": top,
            "share_of_frames": round(kinds.get(top, 0) / max(1, len(seams)), 2),
            "position_frac": (_pct(seam_pos) or [None, None])[1],
        },
        "faces_per_frame_median": sorted(counts)[len(counts) // 2] if counts else 0,
        "face_width_frac_p25_p50_p75": _pct(widths),
        "eye_y_frac_p25_p50_p75": _pct(eye_y),
        "headroom_frac_p25_p50_p75": _pct(heads),
        "face_center_x_frac_p25_p50_p75": _pct(cx),
        "motion_mean": round(sum(motion) / len(motion), 4) if motion else 0.0,
    }


def write_recipe(recipe: Dict[str, Any], lib_dir: Path = LIB_DIR) -> Path:
    """Write a layout recipe JSON to `lib_dir/<id>.json`."""
    lib_dir.mkdir(parents=True, exist_ok=True)
    path = lib_dir / f"{recipe['id']}.json"
    path.write_text(json.dumps(recipe, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return path


def extract(
    video: Path,
    rid: str,
    layout: str,
    names: List[str],
    description: str,
    when: str,
    start: float,
    end: Optional[float],
    thumbs: bool,
    lib_dir: Path = LIB_DIR,
) -> Dict[str, Any]:
    """Measure a local reference video and merge measurements into `formats/layouts/<rid>.json`."""
    m = measure_video(video, start, end, thumbs_to=(lib_dir / "thumbs" / rid) if thumbs else None)
    path = lib_dir / f"{rid}.json"
    r = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {"id": rid}
    r["layout"] = layout or r.get("layout", "single")
    if names:
        r["names"] = names
    if description:
        r["description"] = description
    if when:
        r["when"] = when
    r["measured"] = m
    r["confidence"] = "measured"
    r["source"] = f"measured from {video.name} ({m['window_sec'][0]}-{m['window_sec'][1]} s, {m['frames_sampled']} frames) on {dt.date.today()}"
    write_recipe(r, lib_dir)
    return r


def sync_team(lib_dir: Path = LIB_DIR) -> Dict[str, Any]:
    """Local no-op check confirming the local format library is available."""
    _ = lib_dir
    return {"ok": True, "message": "local format library ready"}


def main() -> None:
    ap = argparse.ArgumentParser(description="Format library: inspect layout recipes and catalog cards")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list", help="list all layout recipes in formats/layouts")
    sub.add_parser("sync", help="verify local format library status")

    s = sub.add_parser("search", help="rank layout recipes against script wording")
    s.add_argument("query")

    sh = sub.add_parser("show", help="show compact summary of a layout recipe")
    sh.add_argument("id")

    cd = sub.add_parser("card", help="print a format catalog card from formats/catalog")
    cd.add_argument("format_id")

    dt_p = sub.add_parser("detect", help="detect catalog format_id from raw script text")
    dt_p.add_argument("text")

    e = sub.add_parser("extract", help="measure a reference video into a layout recipe")
    e.add_argument("video")
    e.add_argument("--id", required=True)
    e.add_argument("--layout", default="")
    e.add_argument("--names", default="")
    e.add_argument("--description", default="")
    e.add_argument("--when", default="")
    e.add_argument("--start", type=float, default=0.0)
    e.add_argument("--end", type=float, default=None)
    e.add_argument("--thumbs", action="store_true", help="save 3 preview JPEGs alongside measurements")

    a = ap.parse_args()
    if a.cmd == "sync":
        print(sync_team()["message"])
    elif a.cmd == "list":
        for r in load_recipes():
            print(f"{r['id']:<14} layout={r['layout']:<10} {r.get('confidence', 'defined'):<9} {r.get('description', '')[:70]}")
    elif a.cmd == "search":
        for h in search(a.query):
            print(f"{h['score']:>5}  {h['id']:<14} -> {h['layout']}")
        print("layout from script wording:", best_layout(a.query) or "none")
    elif a.cmd == "show":
        r = next((x for x in load_recipes() if x["id"] == a.id), None)
        if not r:
            sys.exit(f"no recipe named {a.id}")
        print(recipe_text(r))
    elif a.cmd == "card":
        card = load_catalog_card(a.format_id)
        if not card:
            sys.exit(f"no catalog card named {a.format_id}")
        print(card, end="" if card.endswith("\n") else "\n")
    elif a.cmd == "detect":
        print(detect_catalog_format({}, {}, script_text=a.text))
    elif a.cmd == "extract":
        r = extract(
            Path(a.video),
            a.id,
            a.layout,
            [x.strip() for x in a.names.split(",") if x.strip()],
            a.description,
            a.when,
            a.start,
            a.end,
            a.thumbs,
        )
        print(json.dumps(r["measured"], indent=2))


if __name__ == "__main__":
    main()
