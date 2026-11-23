#!/usr/bin/env python3
"""Segregated format router and brief builder (`work/<project>/brief.md`).

Instead of concatenating monolithic rulebooks or multiple format guides, this module:
1. Reads `formats/core_rules.md` (stripping zoom, transition, subtitle, mark, and SFX rules when `#page scroll` is active).
2. Resolves the single best catalog format ID for the job via `format_library.detect_catalog_format(meta, plan, style)`
   (with built-in fallback matching against `formats/registry.json`).
3. Appends ONLY that single segregated format card (`formats/catalog/<fmt_id>.md`), plus the underlying narrative
   sub-format card when `<fmt_id>` is a visual generation wrapper (`realistic_to_3d` or `3d_to_2d`).
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

SCRIPT_DIR = Path(__file__).resolve().parent
ROOT = SCRIPT_DIR.parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

FORMATS_DIR = ROOT / "formats"
CORE_RULES_PATH = FORMATS_DIR / "core_rules.md"
REGISTRY_PATH = FORMATS_DIR / "registry.json"
CATALOG_DIR = FORMATS_DIR / "catalog"

CATALOG_FORMAT_IDS: Tuple[str, ...] = (
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

WRAPPER_FORMAT_IDS = {"realistic_to_3d", "3d_to_2d"}

_WRAPPER_SCRUB_RE = re.compile(
    r"\b(?:3d[\s_-]*to[\s_-]*2d|realistic(?:\s+video)?[\s_-]*to[\s_-]*3d|live(?:-|\s+)action\s+to\s+3d|live\s+to\s+3d|remade\s+as\s+[23]d|[23]d\s+remake)\b",
    re.I,
)

# Section heading keywords omitted from `core_rules.md` when `#page scroll` (`scrolling_text`) is active
_PAGE_OMIT_SECTION_KEYWORDS = (
    "zoom",
    "transition",
    "marks",
    "calibrated sfx",
    "sound effect",
    "ad tempo",
)

# Line-level patterns omitted from remaining `core_rules.md` sections when `#page scroll` is active
_PAGE_OMIT_LINE_PATTERNS = (
    re.compile(r"`single`\s+pause\s+`?>0\.25s`?", re.I),
    re.compile(r"1\.08`?\s+punch-in", re.I),
    re.compile(r"^-\s*`#subs\s+auto\b", re.I),
    re.compile(r"^\s*`?\[focus=x,y\].*\[sfx:", re.I),
    re.compile(r"\bmark:(?:wrong|right)@", re.I),
    re.compile(r"\bsfx:[a-z0-9_.-]+@", re.I),
)

_FALLBACK_CORE_RULES = "\n".join([
    "# Core Editing Rules",
    "",
    "## 1. Script Order & Speakers",
    "- Play every spoken line once, in order.",
    "- Verify words in `line_map.md` & `facts.md`.",
    "- Set `sp=\"ExactSpeaker\"`; report missing takes.",
    "- Unspoken notes in `decisions.txt`: `accept L2 <why>`.",
    "",
    "## 2. Word Bounds, Sentence Integrity & Pauses",
    "- Keep every sentence complete (`start - 0.05s` to `end + 0.06-0.10s`, checked via `pauses:`); join turns `0.06-0.15s`.",
    "- NEVER cut `<0.45s` mid-sentence breaths/commas; trim `! REPEAT / STUMBLE` & keep scripted laughs/cries `<=1.2s`.",
    "- Trim only dead-air pauses `>=0.50s` (`>=0.55s` inside `split_h`/`split_v`, advancing `peer=`).",
    "- `single` `1.08` punch-in: use `z=sudden_in:1.08@t` inside the continuous cut or cut only at a `>=0.45s` `pauses:` gap.",
    "",
    "## 3. Split-Screen Invariants",
    "- `Left:`/`Right:` = `split_h`; `Top:`/`Bottom:` = `split_v`.",
    "- `split_h`, `split_v`, `top_pip`, baked splits: ALWAYS `z=1` (`14%` headroom).",
    "- Advance silent `peer=\"Name:clip.mp4\"@in-out@side`.",
    "- Baked split: `single` + `slot=left|right|top|bottom z=1` (no re-split).",
    "",
    "## 4. Full-Frame Zoom & Slo-Mo (`single`)",
    "- `1.0` on calm cuts (`>=25%`), pans, edge 2-shots, holograms; reset to `z=1` on speaker/clip cuts.",
    "- Smooth push/pull: `z=zoom_in[:1.08]` (or `1.0->1.06` on turns `>=2.5s`), `z=zoom_out[:1.10]`.",
    "- Punches (`0.05s` early): `z=sudden_in:1.08@t` (`<=16/min`, no repeat), `z=sudden_out:1.08@t` (recoil pullback).",
    "- Action/slo-mo reveal: `z=bell:1.20->1.55->1.0`; max 1 `flash_zoom:1.12@t`; pass `focus=x,y` from `facts.md`.",
    "- Slo-Mo Hook (script-cued): `speed=0.45 mute` (or `speed=0.5 mute beat`) + `z=bell:1.20->1.55->1.0` + whoosh SFX.",
    "",
    "## 5. Story-Only Transitions",
    "- Add `+1-2` beats/reel on real shifts; keep dialogue hard-cut; zero intra-split, same-shot, or mid-line transitions.",
    "- Scene/CTA: `whip_left`/`whip_right` (`0.34s`), `slide_up`/`slide_down` (`0.36s`), `zoom_burst` (`0.36s`); time/dissolve: `dip_to_black`, `dissolve`, `fade` (`0.30s`); hook/split: `zoom_in`, `zoom_out`, `zoom_blur`, `split_reveal@0.40`.",
    "- Act pivot/flash (max 1): `warm_flash_punch` (`0.34s`), `warm_flash` (`0.375s`), `dip_to_white` (`0.30s`), `flash_zoom` (`0.36s`). Kinetic/flash transitions auto-attach whoosh SFX.",
    "",
    "## 6. Marks, Audio & Calibrated SFX",
    "- Audio: zero continuous BGM by default (dry/diegetic dialogue + surgical SFX, `-10.5 LUFS`).",
    "- Packs: `#mark_pack flash|vignette|default|glossy|supernova|kn20228|w42`; `#sfx_pack default|supernova|kn20228|w42`.",
    "- Scripted `wrong`/`strike_text` & `right` lines auto-attach post-word `0.48s` outer vignette + strip slash + SFX over live non-frozen video visuals without dialogue overlap (`mark:wrong|right@t/0.48`, never freeze):",
    "  - Wrong (`+1dB`): `sn_wrong.wav`, `buzzer.wav`, `w42_wrong.wav`, `kn20228_wrong_core.mp3`.",
    "  - Right (`+0.5dB`): `correct_ding.wav`, `sn_correct.wav`, `w42_right.wav`, `kn20228_correct.mp3`, `ding.wav`.",
    "- SFX (`-4..0dB`, 1/beat, `<=40%`/file): UI `pop.wav`, `click.wav`, `sn_click.mp3`, `kn20228_click.wav`, `sn_punch_1.wav`, `sn_punch_2.wav`, `sn_riser_1.wav`; Whoosh `whoosh.wav`, `sn_swish_1.wav`, `w42_swoosh.wav`, `w42_transition.wav`, `kn20228_swoosh.wav`.",
    "",
    "## 7. Subtitles & CTA Safety",
    "- `#subs auto y=65` (strict single-line only, `54px` at `1080w`; `y=50` on `split_v`).",
    "- `#page scroll` replaces `#subs`, banners, marks, & SFX.",
    "- Flashcards: scripted `Flashcard Text:` auto-attaches `card:\"English | Native\"` (face-safe; never `banner:`, which is Stop-Saying strikethrough; opt out via `card:none`/`#cards off`).",
    "- 1:1 Square (`--square`): always wraps the completed 9:16 reel directly (2 layers: 1:1 center-cropped ~7% [5-10%] Gaussian blur background + centered full 9:16 reel on top; never generate separate 1:1 images, cards, timestamps, or subtitles).",
    "- Never burn in fake `\"Install Now\"` CTA buttons; keep bottom `20-25%` clean.",
    "",
    "## 8. `script_analysis.md` & `plan.edl`",
    "- `script_analysis.md` L1: `Format: <id> (<title>)`, 1 row/line.",
    "- `plan.edl` (`t` from start):",
    "  `L8 \"A.mp4\" 0.02-3.40 layout=split_h z=1 sp=\"A\" slot=right peer=\"B:idle.mp4\"@4.15-7.53@left`",
    "  `[focus=x,y] [speed=0.45 mute] [tr=k@d] [mark:wrong@t/0.48@style=flash] [banner:\"x\"@t] [card:\"En | Nat\"@pos=auto] [sfx:pop.wav@t/-3]`",
])

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


def _load_registry() -> List[Dict[str, Any]]:
    if not REGISTRY_PATH.is_file():
        return []
    try:
        data = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    raw_formats = data.get("formats", []) if isinstance(data, dict) else []
    if isinstance(raw_formats, list):
        return [f for f in raw_formats if isinstance(f, dict) and f.get("id")]
    if isinstance(raw_formats, dict):
        return [{"id": fid, **info} for fid, info in raw_formats.items() if isinstance(info, dict)]
    return []


def _format_title(fmt_id: str) -> str:
    for entry in _load_registry():
        if str(entry.get("id")) == fmt_id and entry.get("title"):
            return str(entry["title"])
    return fmt_id.replace("_", " ").title()


def _meta_blob(meta: Dict[str, Any], plan: Dict[str, Any], style: str, script_text: str = "") -> str:
    chars = meta.get("characters") if isinstance(meta.get("characters"), dict) else {}
    scenes = [sc for sc in (meta.get("scenes") or []) if isinstance(sc, dict)]
    setup = [str(x) for x in (meta.get("setup") or [])]
    extra_cues: List[str] = []
    st_val = meta.get("strike_text")
    if isinstance(st_val, str) and st_val.strip():
        extra_cues.append(f"strike_text={st_val.strip()}")
    elif isinstance(st_val, (list, tuple)):
        extra_cues.extend(f"strike_text={x}" for x in st_val if x)
    for k in ("script_text", "raw_script"):
        if meta.get(k):
            extra_cues.append(str(meta[k]))
    for ln_obj in meta.get("lines") or []:
        if isinstance(ln_obj, dict):
            if ln_obj.get("strike_text"):
                extra_cues.append(f"strike_text={ln_obj['strike_text']}")
            if ln_obj.get("mark"):
                extra_cues.append(f"[{ln_obj['mark']}]")
            if ln_obj.get("note"):
                extra_cues.append(str(ln_obj["note"]))
            if ln_obj.get("text"):
                extra_cues.append(str(ln_obj["text"]))
    parts = [
        str(style or ""),
        str(plan.get("style") or ""),
        str(plan.get("preset") or ""),
        str(plan.get("format") or ""),
        str(meta.get("format") or ""),
        str(meta.get("title") or ""),
        str(meta.get("project_name") or ""),
        str(meta.get("header") or ""),
        str(meta.get("concept_brief") or ""),
        str(meta.get("format_hint") or ""),
        " ".join(f"{k} {v}" for k, v in chars.items()),
        " ".join(f"{sc.get('name', '')} {sc.get('visual', '')} {sc.get('layout_hint', '')}" for sc in scenes),
        " ".join(setup),
        " ".join(extra_cues),
        str(script_text or ""),
    ]
    return " ".join(parts).lower()


def _has_phrase(blob: str, phrase: str) -> bool:
    p = (phrase or "").strip().lower()
    if not p:
        return False
    left = r"(?<![a-z0-9])" if p[0].isalnum() else ""
    right = r"(?![a-z0-9])" if p[-1].isalnum() else ""
    return bool(re.search(left + re.escape(p) + right, blob or "", re.I))


def _read_script_cue_file(path: Path) -> str:
    try:
        if path.suffix.lower() == ".docx":
            from common import read_script_text

            return read_script_text(path)
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _extract_line_map_cues(path: Path) -> str:
    try:
        raw = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    kept: List[str] = []
    for ln in raw.splitlines():
        s = ln.strip()
        if not s or s.startswith(("#", "CAND ", "+")):
            continue
        kept.append(s)
    return "\n".join(kept)


def _collect_script_signals(
    meta: Dict[str, Any],
    plan: Dict[str, Any],
    proj_dir: Optional[Path] = None,
    script_text: str = "",
) -> str:
    """Gather script cue and `strike_text` signals from `meta`, `line_map.md`, and `input/script/`."""
    parts: List[str] = []

    st_val = meta.get("strike_text")
    if isinstance(st_val, str) and st_val.strip():
        parts.append(f"strike_text={st_val.strip()}")
    elif isinstance(st_val, (list, tuple)):
        parts.extend(f"strike_text={x}" for x in st_val if x)
    for k in ("script_text", "raw_script", "format_hint"):
        if meta.get(k):
            parts.append(str(meta[k]))
    for ln_obj in meta.get("lines") or []:
        if isinstance(ln_obj, dict):
            if ln_obj.get("strike_text"):
                parts.append(f"strike_text={ln_obj['strike_text']}")
            if ln_obj.get("mark"):
                parts.append(f"[{ln_obj['mark']}]")
            if ln_obj.get("note"):
                parts.append(str(ln_obj["note"]))
            if ln_obj.get("text"):
                parts.append(str(ln_obj["text"]))

    if script_text and script_text.strip():
        parts.append(script_text.strip())
        return "\n".join(parts)

    script_ref = str(plan.get("script") or meta.get("script") or meta.get("script_path") or "").strip()

    # Check project `line_map.md` (explicit `proj_dir` or inferred under `work/<stem>`)
    line_map_candidates: List[Path] = []
    if proj_dir is not None:
        line_map_candidates.append(Path(proj_dir) / "line_map.md")
    else:
        for cand_name in (
            Path(script_ref).stem if script_ref else "",
            str(meta.get("project_name") or "").strip(),
            str(meta.get("title") or "").strip(),
        ):
            if cand_name and "/" not in cand_name and "\\" not in cand_name:
                line_map_candidates.append(ROOT / "work" / cand_name / "line_map.md")
    for lm_path in line_map_candidates:
        if lm_path.is_file():
            cues = _extract_line_map_cues(lm_path)
            if cues:
                parts.append(cues)
            break

    # Check script file on disk (`plan["script"]` or `input/script/<name>`)
    script_candidates: List[Path] = []
    if script_ref:
        ref_path = Path(script_ref)
        script_candidates.extend([ref_path, ROOT / script_ref, ROOT / "input" / "script" / ref_path.name])
    else:
        title_stem = str(meta.get("title") or "").strip()
        if title_stem and "/" not in title_stem and "\\" not in title_stem:
            for ext in (".txt", ".md", ".docx"):
                script_candidates.append(ROOT / "input" / "script" / f"{title_stem}{ext}")
    for sp in script_candidates:
        if sp.is_file():
            txt = _read_script_cue_file(sp)
            if txt.strip():
                parts.append(txt.strip())
            break

    return "\n".join(p for p in parts if p).strip()


def _builtin_detect_format(
    meta: Dict[str, Any],
    plan: Dict[str, Any],
    style: str = "",
    exclude_wrappers: bool = False,
    script_text: str = "",
) -> str:
    """Built-in fallback format matcher consulting `formats/registry.json` when `format_library` does not resolve one."""
    if plan.get("page"):
        return "scrolling_text"

    blob = _meta_blob(meta, plan, style, script_text=script_text)
    if Exclude := (WRAPPER_FORMAT_IDS if exclude_wrappers else set()):
        blob = _WRAPPER_SCRUB_RE.sub(" ", blob)

    for candidate in (plan.get("format"), meta.get("format")):
        raw = str(candidate or "").strip().lower()
        for fid in CATALOG_FORMAT_IDS:
            if fid not in Exclude and re.search(r"(?<![a-z0-9_])" + re.escape(fid) + r"(?![a-z0-9_])", raw):
                return fid

    if any(_has_phrase(blob, p) for p in ("scrolling text", "scroll page", "scrolling subtitles", "#page scroll")):
        return "scrolling_text"

    if not exclude_wrappers:
        if any(_has_phrase(blob, p) for p in ("3d to 2d", "3d-to-2d", "3d_to_2d")):
            return "3d_to_2d"
        if any(_has_phrase(blob, p) for p in ("realistic video to 3d", "realistic to 3d", "live to 3d", "realistic_to_3d")):
            return "realistic_to_3d"

    segs = [s for s in (plan.get("segments") or []) if isinstance(s, dict)]
    has_flashcards = (
        bool(meta.get("flashcards"))
        or any(isinstance(ln_obj, dict) and ln_obj.get("flashcard") for ln_obj in (meta.get("lines") or []))
        or any(isinstance(s, dict) and (s.get("flashcards") or s.get("cards")) for s in segs)
    )
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
    if "translate_drill" not in Exclude and (
        has_flashcards
        or bool(_FLASHCARD_FORMAT_RE.search(blob))
        or bool(_HEADER_FLASHCARD_RE.search(header_blob))
    ):
        return "translate_drill"

    strike_hits = len(_STRIKE_CUES_RE.findall(blob))
    cross_hits = blob.count("\u274c")
    wrong_mark_lines = sum(
        1
        for ln_obj in (meta.get("lines") or [])
        if isinstance(ln_obj, dict) and str(ln_obj.get("mark") or "").strip().lower() in ("wrong", "x", "cross")
    )
    wrong_indicators = max(wrong_mark_lines, len(_WRONG_MARK_TAG_RE.findall(blob)))
    has_teaching_cue = any(_has_phrase(blob, p) for p in _TEACHING_CORRECTION_CUES)
    if "stop_saying" not in Exclude and (
        any(_has_phrase(blob, p) for p in _STOP_SAYING_PHRASES)
        or bool(_NEVER_EVER_SAY_RE.search(blob))
        or strike_hits >= 2
        or cross_hits >= 2
        or wrong_mark_lines >= 3
        or (wrong_indicators >= 2 and has_teaching_cue)
    ):
        return "stop_saying"

    chars = meta.get("characters") if isinstance(meta.get("characters"), dict) else {}
    chars_blob = " ".join(f"{k} {v}" for k, v in chars.items()).lower()
    if "street_interview" not in Exclude and (
        any(_has_phrase(blob, p) for p in _STREET_INTERVIEW_PHRASES)
        or _has_phrase(chars_blob, "interviewer")
        or (
            _has_phrase(blob, "interview")
            and any(_has_phrase(blob, w) for w in _STREET_INTERVIEW_CONTEXT_WORDS)
        )
    ):
        return "street_interview"

    # Score against `formats/registry.json` names and keywords (never match bare "interview" -> street_interview)
    best_id = ""
    best_score = 0.0
    for entry in _load_registry():
        fid = str(entry.get("id") or "")
        if not fid or fid in Exclude or fid == "skit":
            continue
        score = 0.0
        for name in entry.get("names") or []:
            if fid == "street_interview" and str(name).strip().lower() == "interview":
                continue
            if _has_phrase(blob, str(name)):
                score += 4.0
        for kw in entry.get("keywords") or []:
            if fid == "street_interview" and str(kw).strip().lower() == "interview":
                continue
            if _has_phrase(blob, str(kw)):
                score += 1.5
        if score > best_score:
            best_score, best_id = score, fid

    if best_id and best_score >= 3.0:
        return best_id
    return "skit"


def _scrub_wrapper_cues(obj: Any) -> Any:
    if isinstance(obj, str):
        return _WRAPPER_SCRUB_RE.sub(" ", obj)
    if isinstance(obj, dict):
        return {k: ("" if k == "format" else _scrub_wrapper_cues(v)) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_scrub_wrapper_cues(x) for x in obj]
    return obj


def _call_format_library_detector(
    meta: Dict[str, Any],
    plan: Dict[str, Any],
    style: str,
    script_text: str = "",
) -> str:
    try:
        import format_library  # type: ignore

        fn = getattr(format_library, "detect_catalog_format", None)
        if callable(fn):
            try:
                res = fn(meta, plan, style, script_text=script_text)
            except TypeError:
                try:
                    res = fn(meta, plan, style)
                except TypeError:
                    res = fn(meta, plan)
            if isinstance(res, str) and res in CATALOG_FORMAT_IDS:
                return res
            if isinstance(res, dict):
                cand = str(res.get("id") or res.get("format") or "").strip()
                if cand in CATALOG_FORMAT_IDS:
                    return cand
    except Exception:
        pass
    return ""


def resolve_format(
    meta: Dict[str, Any],
    plan: Dict[str, Any],
    style: str = "",
    script_text: str = "",
    proj_dir: Optional[Path] = None,
) -> Tuple[str, Optional[str]]:
    """Return `(fmt_id, sub_fmt_id)` for this job.

    `sub_fmt_id` is populated only when `fmt_id` is `realistic_to_3d` or `3d_to_2d` so the underlying narrative
    sub-format card (e.g. `skit.md`) can be appended alongside the wrapper card.
    """
    meta = meta if isinstance(meta, dict) else {}
    plan = plan if isinstance(plan, dict) else {}
    style = (style or str(plan.get("style") or "")).strip()

    if plan.get("page"):
        return "scrolling_text", None

    eff_script = _collect_script_signals(meta, plan, proj_dir=proj_dir, script_text=script_text)
    fmt_id = _call_format_library_detector(meta, plan, style, script_text=eff_script)
    if not fmt_id:
        fmt_id = _builtin_detect_format(meta, plan, style, exclude_wrappers=False, script_text=eff_script)

    if fmt_id not in WRAPPER_FORMAT_IDS:
        return fmt_id, None

    scrubbed_meta = _scrub_wrapper_cues(meta)
    scrubbed_plan = _scrub_wrapper_cues(plan)
    scrubbed_script = _WRAPPER_SCRUB_RE.sub(" ", eff_script) if eff_script else ""
    sub_fmt = _call_format_library_detector(scrubbed_meta, scrubbed_plan, style, script_text=scrubbed_script)
    if not sub_fmt or sub_fmt in WRAPPER_FORMAT_IDS or sub_fmt == "scrolling_text":
        sub_fmt = _builtin_detect_format(
            scrubbed_meta,
            scrubbed_plan,
            style,
            exclude_wrappers=True,
            script_text=scrubbed_script,
        )
    if not sub_fmt or sub_fmt in WRAPPER_FORMAT_IDS:
        sub_fmt = "skit"
    return fmt_id, sub_fmt


def detect_format(
    meta: Dict[str, Any],
    plan: Dict[str, Any],
    style: str = "",
    script_text: str = "",
    proj_dir: Optional[Path] = None,
) -> str:
    """Return the primary catalog format ID for this job."""
    fmt_id, _ = resolve_format(meta, plan, style, script_text=script_text, proj_dir=proj_dir)
    return fmt_id


def _load_card(fmt_id: str) -> str:
    try:
        import format_library  # type: ignore

        loader = getattr(format_library, "load_catalog_card", None)
        if callable(loader):
            card = loader(fmt_id)
            if card and card.strip():
                return card.strip()
    except Exception:
        pass
    path = CATALOG_DIR / f"{fmt_id}.md"
    if path.is_file():
        return path.read_text(encoding="utf-8").strip()
    return f"# Format: {fmt_id} ({_format_title(fmt_id)})"


def _strip_non_applicable_for_page(core_text: str) -> str:
    """Strip non-applicable subtitle, zoom, transition, mark, and SFX sections/lines for `#page scroll`."""
    sections: List[Tuple[str, List[str]]] = []
    cur_head = ""
    cur_lines: List[str] = []
    for line in core_text.splitlines():
        if line.startswith("## "):
            sections.append((cur_head, cur_lines))
            cur_head = line[3:].strip()
            cur_lines = [line]
        else:
            cur_lines.append(line)
    sections.append((cur_head, cur_lines))

    kept_blocks: List[str] = []
    for head, lines in sections:
        hlow = head.lower()
        if head and any(kw in hlow for kw in _PAGE_OMIT_SECTION_KEYWORDS):
            continue
        filtered_lines: List[str] = []
        for ln in lines:
            if any(pat.search(ln) for pat in _PAGE_OMIT_LINE_PATTERNS):
                continue
            cleaned = ln.replace(" [tr=name@duration]", "").replace(" [tr=k@d]", "")
            filtered_lines.append(cleaned)
        block = "\n".join(filtered_lines).strip()
        if block:
            kept_blocks.append(block)
    return "\n\n".join(kept_blocks).strip()


def _read_core_rules(page: bool) -> str:
    raw = CORE_RULES_PATH.read_text(encoding="utf-8").strip() if CORE_RULES_PATH.is_file() else _FALLBACK_CORE_RULES
    return _strip_non_applicable_for_page(raw) if page else raw


def build(
    meta: dict,
    plan: dict,
    style: str = "",
    script_text: str = "",
    proj_dir: Optional[Path] = None,
) -> str:
    """Build the segregated 1-format editing brief (`brief.md`) for this job."""
    meta = meta if isinstance(meta, dict) else {}
    plan = plan if isinstance(plan, dict) else {}
    style = (style or str(plan.get("style") or "")).strip()

    fmt_id, sub_fmt_id = resolve_format(meta, plan, style, script_text=script_text, proj_dir=proj_dir)
    page = bool(plan.get("page")) or fmt_id == "scrolling_text"
    title = _format_title(fmt_id)

    header_lines: List[str] = ["# Edit Brief"]
    if fmt_id == "realistic_to_3d" and sub_fmt_id:
        header_lines.append(f"- **Format**: `realistic_to_3d (as {sub_fmt_id})` — {title}")
    elif fmt_id == "3d_to_2d" and sub_fmt_id:
        header_lines.append(f"- **Format**: `{sub_fmt_id} (3D to 2D)` — {title}")
    else:
        header_lines.append(f"- **Format**: `{fmt_id}` ({title})")

    cb = str(meta.get("concept_brief") or "").strip()
    if cb:
        header_lines.append(f"- **Script brief**: {cb}")

    chars = meta.get("characters")
    if isinstance(chars, dict) and chars and not cb.lower().startswith("characters:"):
        char_summary = "; ".join(f"{k} ({v})" if v else str(k) for k, v in chars.items())
        header_lines.append(f"- **Characters**: {char_summary}")

    if page:
        header_lines.append(
            "- **Mode**: `#page scroll` active — picture panels remain still (`split_h z=1`); captions, zooms, transitions, marks, and SFX are omitted."
        )

    blocks: List[str] = [
        "\n".join(header_lines),
        _read_core_rules(page=page),
        _load_card(fmt_id),
    ]
    if sub_fmt_id and sub_fmt_id != fmt_id:
        blocks.append(_load_card(sub_fmt_id))

    return "\n\n".join(b for b in blocks if b).rstrip() + "\n"


def write(proj_dir: Path, meta: Dict[str, Any], plan: Dict[str, Any], style: str = "", script_text: str = "") -> Path:
    """Write `work/<project>/brief.md` and return its path."""
    proj = Path(proj_dir)
    path = proj / "brief.md"
    path.write_text(build(meta, plan, style=style, script_text=script_text, proj_dir=proj), encoding="utf-8")
    return path


def main() -> None:
    ap = argparse.ArgumentParser(description="Build segregated 1-format brief.md for a Zeno project")
    ap.add_argument("project", type=Path, help="Project directory under work/<project>")
    ap.add_argument("--style", default="", help="Optional style override")
    args = ap.parse_args()

    proj = args.project
    plan_json = proj / "plan.json"
    plan: Dict[str, Any] = {}
    meta: Dict[str, Any] = {}
    if plan_json.is_file():
        try:
            plan = json.loads(plan_json.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            plan = {}
    script_rel = plan.get("script")
    if script_rel:
        try:
            from common import resolve
            from prepare import parse_script_file

            sp = resolve(str(script_rel))
            if sp.is_file():
                meta, _ = parse_script_file(sp)
        except Exception:
            meta = {}
    out_path = write(proj, meta, plan, style=args.style)
    print(f"[BRIEF] {out_path} ({out_path.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
