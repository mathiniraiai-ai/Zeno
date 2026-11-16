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


def build(meta: Dict[str, Any], plan: Dict[str, Any], style: str = "") -> str:
    """The brief for this job, from the script's meta, the plan header Zeno wrote and the style."""
    page = bool(plan.get("page"))
    rules = EDIT_RULES.read_text(encoding="utf-8") if EDIT_RULES.is_file() else ""
    formats = FORMATS.read_text(encoding="utf-8") if FORMATS.is_file() else ""

    out = ["# EDIT BRIEF for this job: read this instead of agent/EDIT_RULES.md and agent/FORMATS.md (it is cut from them).", ""]
    if meta.get("concept_brief"):
        out.append(f"Script brief: {meta['concept_brief']}")
    if meta.get("characters"):
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
    out.append(_pick(rules, keep))

    if page:
        fmt = "\n\n".join(body for head, body in _sections(formats, "##") if head.lower().startswith("4. scrolling text"))
    else:
        fmt = "\n\n".join(body for head, body in _sections(formats, "##")
                          if head and not head.lower().startswith(_FORMATS_SKIP))
    if fmt:
        out += ["", "# FORMATS (name the format on line 1 of script_analysis.md as `Format: <id> (<name>)`)", "", fmt]
    return "\n".join(out).rstrip() + "\n"


def write(proj_dir: Path, meta: Dict[str, Any], plan: Dict[str, Any], style: str = "") -> Optional[Path]:
    path = Path(proj_dir) / "brief.md"
    path.write_text(build(meta, plan, style), encoding="utf-8")
    return path
