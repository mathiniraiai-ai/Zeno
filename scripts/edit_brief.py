"""The editor's brief for one job: `work/<project>/brief.md`.

It replaces reading `agent/EDIT_RULES.md` and `agent/FORMATS.md` in full. Every agent step re-sends the whole chat, so each
section that does not apply to this job (the zoom table on a still split, the scrolling page on a full-frame skit) is paid
for again on every later step. The brief keeps the script's own brief, the style's learned rules and only the sections of
the two rule files this job can use. The rule files stay the single source: the brief is cut from them on every inspect.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parent.parent
EDIT_RULES = ROOT / "agent" / "EDIT_RULES.md"
FORMATS = ROOT / "agent" / "FORMATS.md"
REFERENCE = ROOT / "agent" / "REFERENCE.md"   # layouts, mark packs, plan syntax, SFX (moved out of CLAUDE.md)

# EDIT_RULES.md sections by the start of their heading (case-insensitive).
_ALWAYS = ("craft principles", "reading the measurements", "emotion plan", "cuts", "layout", "plan.edl", "checks")
_MOTION = ("zoom", "ad tempo", "sound effects", "transitions, marks", "subtitles")   # full-frame cuts, marks, captions
_PAGE = ("scrolling-text page",)
# FORMATS.md sections never needed to edit (the code's recipe table).
_FORMATS_SKIP = ("the layout recipes in code",)

_FORMAT_MENU = "\n".join([
    "Format menu (`Format: <id> (<name>)` on line 1 of `script_analysis.md`):",
    "1. `skit` | `direct_hook` | `street_interview` | `stop_saying` — Micro-narrative (hook/vox-pop/3 struck phrases -> AI split/hologram)",
    "2. `live_conversation` — Robot/hologram/tablet in same `single` frame or `video_call` (no split)",
    "3. `split_screen` — Two characters in `split_h`/`split_v`/`video_call` throughout (`z=1`, silent `peer=`)",
    "4. `scrolling_text` — `#page scroll`: two still `split_h z=1` panels above scrolling dialogue page",
    "5. `translate_drill` — \"How do you say X?\" flashcard `banner:`/`chat_bar` drill + pitch",
    "6. `story_pitch` — `single` narration + `dissolve` flashbacks (no split/marks)",
    "7. `realistic_to_3d` — 3D-remade live story; edit by sub-format (`realistic_to_3d (as skit)`)",
    "8. `3d_to_2d` — Generation look only; name underlying sub-format (`skit (3D to 2D)`)",
])

_MICRONARRATIVE_STYLE_RULES = "\n".join([
    "# STYLE: micronarrative (non-duplicate style specifics; do not reopen styles/micronarrative/SKILL.md)",
    "- **Footage modes**: (A) Baked Flow split -> `layout=single slot=top|bottom|left|right z=1` (never double-composite); "
    "(B) Separate clips -> `split_h`/`split_v z=1` (14% headroom) + silent `peer=`, `tr=split_reveal@0.40` entry/exit only; "
    "(C) Hologram / edge two-shot (`none` in facts) -> `layout=single z=1`, safe two-shot `<=1.10 focus=x,y`; "
    "(D) `layout=top_pip` -> `750x420` top-center AI window (`x=165,y=20,r=36`) over full-screen human; `<stem>VO.mp4` auto-pairs.",
    "- **Style caps**: `~1 PEAK` per `12–18s`; `<=16` rapid zooms/min (no back-to-back `sudden_in`, max 1 `flash_zoom:1.12`); "
    "turns `>=2.5s` `1.0->1.06`, CTA `1.0->1.06`, calm beats `1.0`. No laugh/gasp/heartbeat/sad SFX in library; `color_match` off; no J/L offsets.",
])


def _sections(text: str, level: str) -> List[tuple]:
    """(heading, body) pairs for one heading level; the text before the first heading has heading ''."""
    out, head, buf = [], "", []
    for line in text.splitlines():
        if line.startswith(level + " ") and not line.startswith(level + "#"):
            out.append((head, "\n".join(buf).strip()))
            head, buf = line[len(level) + 1:].strip(), [line]
        else:
            buf.append(line)
    out.append((head, "\n".join(buf).strip()))
    return out


def _pick(text: str, keep: tuple) -> str:
    return "\n\n".join(body for head, body in _sections(text, "##") if head and head.lower().startswith(keep))


def _clean_transitions_section(body: str) -> str:
    lines = [ln for ln in body.splitlines() if not ln.strip().startswith("Editors' feedback (")]
    return "\n".join(lines).strip()


def _meta_blob(meta: Dict[str, Any], plan: Dict[str, Any], style: str) -> str:
    chars = meta.get("characters") or {}
    scenes = meta.get("scenes") or []
    parts = [
        style,
        str(plan.get("preset") or ""),
        str(meta.get("concept_brief") or ""),
        str(meta.get("header") or ""),
        str(meta.get("title") or ""),
        " ".join(f"{k} {v}" for k, v in chars.items()) if isinstance(chars, dict) else "",
        " ".join(str(sc.get("visual") or "") for sc in scenes if isinstance(sc, dict)),
        " ".join(str(x) for x in (meta.get("setup") or [])),
    ]
    return " ".join(parts).lower()


def _candidate_format_prefixes(meta: Dict[str, Any], plan: Dict[str, Any], style: str) -> tuple:
    if not meta and not style:
        return ("1.", "2.", "3.", "5.", "6.")
    chars = meta.get("characters") or {}
    scenes = meta.get("scenes") or []
    blob = _meta_blob(meta, plan, style)
    if not blob.strip():
        return ("1.", "2.", "3.", "5.", "6.")

    has_meta = bool(meta.get("concept_brief") or chars or scenes)
    if not has_meta:
        return ("1.", "2.", "3.", "5.", "6.")

    want = set()
    has_split = any(k in blob for k in ("split", "video call", "video_call", "top_pip", "floating window"))
    if (style.lower() == "micronarrative" or scenes
            or any(k in blob for k in ("skit", "hook", "interview", "vox", "stop saying", "never say", "strike"))):
        want.add("1.")
    if has_split or style.lower() == "micronarrative":
        want.add("3.")
    if not has_split or any(k in blob for k in ("hologram", "tablet", "live conversation", "live grammar", "interactive", "beside")):
        want.add("2.")
    if any(k in blob for k in ("flashcard", "flash card", "how do you say", "how would you", "translate_drill", "chat_bar")):
        want.add("5.")
    if any(k in blob for k in ("3d", "2d", "realistic", "avatar")):
        want.update({"7.", "8."})
    if (isinstance(chars, dict) and len(chars) <= 1 and not scenes) or str(plan.get("preset") or "") == "single":
        want.update({"1.", "2.", "6."})
    if any(k in blob for k in ("story", "pitch", "flashback", "narration", "monologue")):
        want.update({"1.", "6."})
    if not want:
        return ("1.", "2.", "3.", "5.", "6.")
    return tuple(sorted(want))


def _filter_micronarrative_body(body: str, meta: Dict[str, Any], plan: Dict[str, Any], style: str) -> str:
    has_meta = bool(meta.get("concept_brief") or meta.get("characters") or meta.get("scenes"))
    if not has_meta:
        return body
    blob = _meta_blob(meta, plan, style)
    kept = []
    for sub_h, sub_b in _sections(body, "###"):
        low = sub_h.lower()
        if not sub_h or "`skit`" in low or "`direct_hook`" in low:
            kept.append(sub_b)
        elif "`street_interview`" in low and any(k in blob for k in ("street", "interview", "vox", "mic")):
            kept.append(sub_b)
        elif "`stop_saying`" in low and any(k in blob for k in ("stop saying", "never say", "strike")):
            kept.append(sub_b)
    return "\n\n".join(p for p in kept if p)


def _style_section(style: str) -> str:
    if not style:
        return ""
    skill_path = ROOT / "styles" / style / "SKILL.md"
    if not skill_path.is_file():
        return ""
    if style.lower() == "micronarrative":
        return _MICRONARRATIVE_STYLE_RULES
    raw = skill_path.read_text(encoding="utf-8").strip()
    if not raw:
        return ""
    if len(raw) <= 2200:
        return f"# STYLE: {style}\n\n{raw}"
    kept = [body for head, body in _sections(raw, "##")
            if head and not head.lower().startswith(("1. related", "2. when to use", "2c. the formats", "5. who decides"))]
    return f"# STYLE: {style}\n\n" + "\n\n".join(kept).strip()


def build(meta: Dict[str, Any], plan: Dict[str, Any], style: str = "") -> str:
    """The brief for this job, from the script's meta, the plan header Zeno wrote and the style."""
    meta = meta or {}
    plan = plan or {}
    style = (style or str(plan.get("style") or "")).strip()
    page = bool(plan.get("page"))
    rules = EDIT_RULES.read_text(encoding="utf-8") if EDIT_RULES.is_file() else ""
    formats = FORMATS.read_text(encoding="utf-8") if FORMATS.is_file() else ""

    out = ["# EDIT BRIEF for this job: read this instead of agent/EDIT_RULES.md and agent/FORMATS.md (it is cut from them).", ""]
    cb = str(meta.get("concept_brief") or "").strip()
    if cb:
        out.append(f"Script brief: {cb}")
    if meta.get("characters") and not cb.startswith("Characters:"):
        out.append("Characters: " + "; ".join(f"{k} ({v})" if v else k for k, v in meta["characters"].items()))
    if page:
        out.append("Format: `scroll_page` (the script asks for scrolling text; `#page scroll` is in the plan header). "
                   "Captions, zooms, transitions, marks and SFX are not used in this format, so their rules are left out.")
    out.append("")

    if style:
        try:
            import brain_memory

            mem = brain_memory.format_brain_memory_for_brief(style)
            if mem:
                out += list(mem) + [""]
        except Exception:   # noqa: BLE001  (a missing style never stops the brief)
            pass

    keep = _ALWAYS + (_PAGE if page else _MOTION)
    picked_rules = _pick(rules, keep).replace(
        "Read `line_map.md`, `facts.md` and all clip sheets completely.",
        "Read `editing_packet.md` and all clip sheets once (do not reopen `line_map.md` or `facts.md` separately).",
    )
    out.append(picked_rules)

    if page:
        fmt = "\n\n".join(body for head, body in _sections(formats, "##") if head.lower().startswith("4. scrolling text"))
    else:
        fmt_parts = [_FORMAT_MENU]
        cand_prefixes = _candidate_format_prefixes(meta, plan, style)
        for head, body in _sections(formats, "##"):
            if not head or head.lower().startswith(_FORMATS_SKIP):
                continue
            hlow = head.lower()
            if hlow.startswith("transitions"):
                fmt_parts.append(_clean_transitions_section(body))
            elif hlow.startswith("1.") and hlow.startswith(cand_prefixes):
                fmt_parts.append(_filter_micronarrative_body(body, meta, plan, style))
            elif hlow.startswith(cand_prefixes):
                fmt_parts.append(body)
            elif hlow.startswith("things the examples show"):
                lines_ex = [ln for ln in body.splitlines() if not ln.strip().startswith("- The cards show lengths")]
                fmt_parts.append("\n".join(lines_ex).strip())
        fmt = "\n\n".join(p for p in fmt_parts if p)
    if fmt:
        out += ["", "# FORMATS (name the format on line 1 of script_analysis.md as `Format: <id> (<name>)`)", "", fmt]
    if style and not page:
        s_sec = _style_section(style)
        if s_sec:
            out += ["", s_sec]
    return "\n".join(out).rstrip() + "\n"


def write(proj_dir: Path, meta: Dict[str, Any], plan: Dict[str, Any], style: str = "") -> Optional[Path]:
    path = Path(proj_dir) / "brief.md"
    path.write_text(build(meta, plan, style), encoding="utf-8")
    return path
