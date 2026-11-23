#!/usr/bin/env python3
"""Unified compact Director Packet builder (`director_packet.md` & `editing_packet.md`).

Combines the segregated single-format `brief.md`, `plan.edl` headers, `line_map.md` candidates,
compacted `facts.md` (preserving exact face `focus=x,y` zoom values and word timestamps while
stripping redundant markdown headers and duplicate VAD/voiced interval lists), and `decisions.txt`
when non-empty.

Writes the unified packet atomically to both `work/<project>/director_packet.md` and
`work/<project>/editing_packet.md` alongside `editing_packet.json`.
Supports `--line <id>` CLI lookup to inspect all candidates and clip measurements for one line.
"""
from __future__ import annotations

import argparse
import hashlib
import inspect
import json
import os
import re
import sys
import time
import wave
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

SCRIPT_DIR = Path(__file__).resolve().parent
ROOT = SCRIPT_DIR.parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

try:
    from align_engine.vad import uncovered_speech_spans as _vad_uncovered_speech_spans  # type: ignore[attr-defined]
except Exception:
    _vad_uncovered_speech_spans = None

CHUNK_MAX_BYTES = 36000
CHUNK_MAX_LINES = 620
_CHUNK_TRIGGER_BYTES = 38000
_CHUNK_TRIGGER_LINES = 650
_AUDIO_EXTS = {".wav", ".mp3", ".m4a", ".aac", ".flac"}

# Normalize `focus=[0.4659, 0.1285]` into space-free EDL token form `focus=0.4659,0.1285` while keeping exact values
_BRACKET_FOCUS_RE = re.compile(r"\bfocus=\[\s*(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)\s*\]")
_EMPHASIS_VERBOSE_RE = re.compile(r"^(\s*)emphasis\s*\([^)]*\)\s*:")
_SPEECH_GAPS_VERBOSE_RE = re.compile(r"^(\s*)speech gaps\s*\([^)]*\)\s*:")
_LEGACY_PIPE_SYNTAX_RE = re.compile(r"Standalone\s+`#\s*MARKS:`\s*/\s*`#\s*SFX:`\s+pipe\s+rows.*", re.I)


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8") if path.is_file() else ""


def _utf8_len(text: str) -> int:
    return len(text.encode("utf-8"))


def _read_json_safe(path: Path) -> Any:
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return None


def _atomic_write(target: Path, text: str) -> None:
    temp = target.with_suffix(target.suffix + ".tmp")
    temp.write_text(text, encoding="utf-8")
    temp.replace(target)


_EMPH_ITEM_RE = re.compile(r"'[^']*'\s*@(\d+(?:\.\d+)?(?:\([^)]*\))?)")
_UNIFORM_SAFE_ZOOM_RE = re.compile(
    r"zoom that keeps every face in frame:\s*1\.05:\s*focus x\s*([0-9.\-]+);\s*1\.08:\s*focus x\s*\1;\s*1\.1(?:0)?:\s*focus x\s*\1"
)
_DUP_RULES_SECTION_RE = re.compile(
    r"(?ms)^## (?:Zoom, Transitions, Marks & Subtitles|Marks, SFX & Subtitles|SFX & Subtitles|Framing, Captions & Pacing|Cuts, Motion & Sound)\n.*?(?=^#{1,2} |\Z)"
)
_CAND_HEARD_RE = re.compile(r"\bheard=(\d+(?:\.\d+)?)")
_SCENE_TAG_RE = re.compile(r"\s+\(scene=Scene\s+1\)\s*$")


def _normalize_spans(spans: Any) -> List[Tuple[float, float]]:
    raw: List[Tuple[float, float]] = []
    if not isinstance(spans, (list, tuple)):
        return raw
    for item in spans:
        if not isinstance(item, (list, tuple)) or len(item) < 2:
            continue
        try:
            a, b = float(item[0]), float(item[1])
        except (TypeError, ValueError):
            continue
        if b > a:
            raw.append((a, b))
    if not raw:
        return []
    raw.sort()
    merged: List[List[float]] = []
    for a, b in raw:
        if merged and a <= merged[-1][1] + 1e-6:
            merged[-1][1] = max(merged[-1][1], b)
        else:
            merged.append([a, b])
    return [(round(a, 3), round(b, 3)) for a, b in merged]


def _word_intervals(words: Sequence[Dict[str, Any]], pad_sec: float = 0.25) -> List[Tuple[float, float]]:
    raw: List[Tuple[float, float]] = []
    for w in words:
        if not isinstance(w, dict) or w.get("no_sound"):
            continue
        try:
            a = float(w.get("start", 0.0))
            b = float(w.get("end", 0.0))
        except (TypeError, ValueError):
            continue
        if b <= a:
            continue
        raw.append((max(0.0, a - pad_sec), b + pad_sec))
    return _normalize_spans(raw)


def _subtract_intervals(
    base: Sequence[Tuple[float, float]],
    cover: Sequence[Tuple[float, float]],
) -> List[Tuple[float, float]]:
    out: List[Tuple[float, float]] = []
    for s0, s1 in base:
        cur = s0
        for c0, c1 in cover:
            if c1 <= cur:
                continue
            if c0 >= s1:
                break
            if c0 > cur:
                out.append((cur, c0))
            cur = max(cur, c1)
            if cur >= s1:
                break
        if cur < s1:
            out.append((cur, s1))
    return out


def uncovered_speech_spans(
    speech_spans: Sequence[Sequence[float]],
    words: Sequence[Dict[str, Any]],
    min_uncovered_sec: float = 1.2,
    pad_sec: float = 0.25,
) -> List[Tuple[float, float]]:
    """Return active VAD speech intervals >= `min_uncovered_sec` lacking transcribed words."""
    base = _normalize_spans(speech_spans)
    if not base:
        return []
    valid_words = [w for w in words if isinstance(w, dict) and not w.get("no_sound")]
    if _vad_uncovered_speech_spans is not None:
        try:
            sig = inspect.signature(_vad_uncovered_speech_spans)
            kwargs: Dict[str, Any] = {}
            for k in ("min_gap_sec", "min_gap", "min_sec", "min_uncovered_sec", "min_duration"):
                if k in sig.parameters:
                    kwargs[k] = min_uncovered_sec
                    break
            if kwargs or len(sig.parameters) <= 2:
                res = _vad_uncovered_speech_spans(base, valid_words, **kwargs)
                norm = _normalize_spans(res)
                return [(round(a, 3), round(b, 3)) for a, b in norm if (b - a) >= min_uncovered_sec]
        except Exception:
            pass
    covered = _word_intervals(valid_words, pad_sec=pad_sec)
    rem = _subtract_intervals(base, covered) if covered else list(base)
    return [(round(a, 3), round(b, 3)) for a, b in rem if (b - a) >= min_uncovered_sec]


def _covered_seconds_in_spans(
    speech_spans: Sequence[Sequence[float]],
    words: Sequence[Dict[str, Any]],
    pad_sec: float = 0.25,
) -> float:
    base = _normalize_spans(speech_spans)
    if not base:
        return 0.0
    covered = _word_intervals(words, pad_sec=pad_sec)
    if not covered:
        return 0.0
    total = 0.0
    for s0, s1 in base:
        for c0, c1 in covered:
            ov = min(s1, c1) - max(s0, c0)
            if ov > 0.0:
                total += ov
    return round(total, 3)


def _extract_facts_speech_and_words(raw_lines: Sequence[str]) -> Tuple[List[Tuple[float, float]], List[Dict[str, float]], bool]:
    speech_spans: List[Tuple[float, float]] = []
    word_runs: List[Dict[str, float]] = []
    is_silent = False
    for ln in raw_lines:
        s = ln.lstrip()
        if "! SILENT / AMBIENT CLIP" in s:
            is_silent = True
        elif s.startswith("speech (voice detector"):
            for a, b in re.findall(r"(\d+(?:\.\d+)?)-(\d+(?:\.\d+)?)", s):
                fa, fb = float(a), float(b)
                if fb > fa:
                    speech_spans.append((fa, fb))
        elif not s.startswith(("!", "pauses:", "voiced (", "emphasis", "faces ", "speech gaps", "speech_gaps:", "untranscribed_speech:")):
            m_start = re.match(r"^\s*(\d+(?:\.\d+)?)\s+\S+", ln)
            if m_start:
                st = float(m_start.group(1))
                m_end = re.search(r"\|(\d+(?:\.\d+)?)\s*$", ln)
                if m_end:
                    en = float(m_end.group(1))
                else:
                    ts_all = [float(x) for x in re.findall(r"(?:^|\s)(\d+(?:\.\d+)?)\s+\S+", ln)]
                    en = (max(ts_all) + 0.35) if ts_all else (st + 0.35)
                if en > st:
                    word_runs.append({"start": st, "end": en})
    return _normalize_spans(speech_spans), word_runs, is_silent


def _format_untranscribed_line(uncovered: Sequence[Tuple[float, float]]) -> str:
    if len(uncovered) == 1:
        a, b = uncovered[0]
        return f"  untranscribed_speech: {a:.2f}-{b:.2f}s ({b - a:.1f}s missed by transcriber — run --retry-incomplete)"
    parts = [f"{a:.2f}-{b:.2f}s ({b - a:.1f}s)" for a, b in uncovered]
    total = sum(max(0.0, b - a) for a, b in uncovered)
    return f"  untranscribed_speech: {', '.join(parts)} ({total:.1f}s missed by transcriber — run --retry-incomplete)"


def _compact_pauses_intervals(
    intervals: Sequence[Tuple[float, float]],
    min_dur: float = 0.30,
    max_items: int = 20,
) -> List[Tuple[float, float]]:
    """Keep genuine acoustic pauses >= `min_dur` seconds, capped at `max_items` (longest kept, chronological)."""
    norm = _normalize_spans(intervals)
    kept = [(a, b) for a, b in norm if round(b - a, 4) >= min_dur]
    if len(kept) > max_items:
        kept = sorted(sorted(kept, key=lambda ab: (-(ab[1] - ab[0]), ab[0]))[:max_items])
    return kept


def _facts_header_clip_name_and_stem(header_line: str) -> Tuple[str, str]:
    """Extract clip filename and stem from a `facts.md` `## <clip> (...)` section header."""
    line = header_line.strip()
    m = re.match(
        r"^##\s+(.+?\.(?:mp4|mov|m4v|mkv|webm|avi|wav|mp3|m4a|aac|flac))(?:\s*\(.*|\s+.*|$)",
        line,
        re.I,
    )
    if not m:
        m = re.match(r"^##\s+(.+?)\s*\([^()]*\)\s*$", line)
    if not m:
        m = re.match(r"^##\s+(\S+)", line)
    if not m:
        return "", ""
    name = m.group(1).strip().strip('"').strip("'")
    return name, Path(name).stem


def _add_active_clip(active: Set[str], raw_src: str) -> None:
    s = str(raw_src or "").strip().strip('"').strip("'")
    if not s:
        return
    p = Path(s.replace("\\", "/"))
    if p.name:
        active.add(p.name)
    if p.stem:
        active.add(p.stem)
    active.add(s)


def _extract_active_clips(line_map_text: str, plan_text: str) -> Set[str]:
    """Collect active clip names and stems referenced by `line_map.md` CANDs or `plan.edl` segments/peers."""
    active: Set[str] = set()
    for src in re.findall(r"(?m)^\s*CAND\s+(.+?)\s+\d+(?:\.\d+)?(?:-|\.\.)\d", line_map_text or ""):
        _add_active_clip(active, src)

    if not (plan_text or "").strip():
        return active

    try:
        import edl_parser

        edl_input = plan_text if "\n" in plan_text else (plan_text + "\n")
        parsed = edl_parser.parse_edl(edl_input)
        for seg in parsed.get("segments") or []:
            if not isinstance(seg, dict):
                continue
            if seg.get("src"):
                _add_active_clip(active, str(seg["src"]))
            peer = seg.get("peer")
            if isinstance(peer, dict) and peer.get("src"):
                _add_active_clip(active, str(peer["src"]))
            elif isinstance(peer, str) and peer.strip():
                peer_head = peer.split("@", 1)[0].strip().strip('"').strip("'")
                if ":" in peer_head and not re.match(r"^[A-Za-z]:[/\\]", peer_head):
                    peer_head = peer_head.split(":", 1)[1]
                _add_active_clip(active, peer_head)
    except Exception:
        pass

    # Fallback regex pass in case `plan.edl` is partially drafted or has a malformed line.
    for raw_ln in plan_text.splitlines():
        ln = raw_ln.strip()
        if not ln or ln.startswith("#"):
            continue
        low = ln.lower()
        if low.startswith(("sfx ", "mark ")):
            continue
        if "|" in ln:
            cols = [c.strip() for c in ln.split("|")]
            if len(cols) >= 3 and re.match(r"^\d+(?:\.\d+)?\s*(?:-|\.\.)\s*\d", cols[2]):
                _add_active_clip(active, cols[1])
        else:
            m_seg = re.match(
                r'^\S+\s+(?:"([^"]+)"|\'([^\']+)\'|(.+?))\s+\d+(?:\.\d+)?(?:-|\.\.)\d',
                ln,
            )
            if m_seg:
                _add_active_clip(active, m_seg.group(1) or m_seg.group(2) or m_seg.group(3) or "")
        for m_peer in re.finditer(r'\bpeer=(?:"([^"]+)"|\'([^\']+)\'|([^\s|]+))', ln, re.I):
            raw_peer = m_peer.group(1) or m_peer.group(2) or m_peer.group(3) or ""
            peer_head = raw_peer.split("@", 1)[0].strip().strip('"').strip("'")
            if ":" in peer_head and not re.match(r"^[A-Za-z]:[/\\]", peer_head):
                peer_head = peer_head.split(":", 1)[1]
            _add_active_clip(active, peer_head)
    return active


def compact_facts(
    text: str,
    active_clips: Optional[Set[str]] = None,
    min_clips_for_diet: int = 30,
) -> str:
    """Compact `facts.md` while preserving exact `focus=x,y` zoom values, safe two-shot spans, `pauses:`, beat times, and word timestamps.

    Strips redundant `# FACTS:` banners and duplicate VAD/voiced interval lists, while ALWAYS preserving compacted `pauses:`
    (`>=0.30s`, up to 20 intervals) on spoken clips so the Host AI sees genuine acoustic pauses vs continuous speech,
    and adding an explicit `untranscribed_speech:` warning whenever >=1.2s of active VAD speech lacks word timestamps.
    On large shoots (`>= min_clips_for_diet` clip blocks) with non-empty `active_clips`, collapses raw word-timestamp
    lines on zero-CAND unused spoken clips while keeping active, silent/ambient, uncovered-speech, and two-shot clips full.
    """
    if not text:
        return ""
    blocks = re.split(r"(?m)(?=^## )", text)
    total_clip_blocks = sum(1 for b in blocks if b.startswith("## "))
    active_set = {str(x).strip() for x in active_clips if str(x).strip()} if active_clips else set()
    diet_enabled = bool(active_set) and total_clip_blocks >= min_clips_for_diet
    out_blocks: List[str] = []
    for block in blocks:
        raw_lines = block.splitlines()
        if not raw_lines:
            continue
        if not raw_lines[0].startswith("## "):
            kept_pre = [
                ln for ln in raw_lines
                if ln.strip() and not ln.lstrip().startswith("# FACTS")
            ]
            if kept_pre:
                out_blocks.append("\n".join(kept_pre).strip())
            continue

        has_word_lines = any(re.match(r"^\s*\d+\.\d+\s+\S+", ln) for ln in raw_lines[1:])
        speech_spans, word_runs, is_silent_block = _extract_facts_speech_and_words(raw_lines[1:])
        uncovered = (
            uncovered_speech_spans(speech_spans, word_runs, min_uncovered_sec=1.2)
            if (speech_spans and not is_silent_block)
            else []
        )
        has_uncovered = bool(uncovered)
        vad_gap_pauses = (
            _compact_pauses_intervals(
                [(speech_spans[i][1], speech_spans[i + 1][0]) for i in range(len(speech_spans) - 1)]
            )
            if (not is_silent_block and len(speech_spans) >= 2)
            else []
        )
        skip_prefixes = ("speech (voice detector", "voiced (real sound") + (
            ("speech gaps", "speech_gaps:") if (has_word_lines and not has_uncovered) else ()
        )
        sec_lines: List[str] = []
        saw_pauses = False
        saw_speech_gaps = False
        saw_untranscribed = False
        for idx, ln in enumerate(raw_lines):
            if idx == 0:
                hdr = _BRACKET_FOCUS_RE.sub(r"focus=\1,\2", ln.rstrip())
                hdr = re.sub(r"\s+face_top-bottom=[0-9.\-]+", "", hdr)
                sec_lines.append(hdr)
                continue
            stripped = ln.lstrip()
            if not stripped or stripped.startswith(skip_prefixes):
                continue
            if stripped.startswith("pauses:"):
                raw_p = [
                    (float(a), float(b))
                    for a, b in re.findall(r"(\d+(?:\.\d+)?)-(\d+(?:\.\d+)?)", stripped)
                    if float(b) > float(a)
                ]
                comp_p = _compact_pauses_intervals(raw_p)
                if not comp_p and vad_gap_pauses:
                    comp_p = vad_gap_pauses
                if comp_p:
                    sec_lines.append("  pauses: " + " ".join(f"{a:.2f}-{b:.2f}" for a, b in comp_p))
                elif not is_silent_block and (has_word_lines or speech_spans):
                    sec_lines.append("  pauses: none")
                saw_pauses = True
                continue
            ln = _SPEECH_GAPS_VERBOSE_RE.sub(r"\1speech_gaps:", ln)
            ln = _EMPHASIS_VERBOSE_RE.sub(r"\1emphasis:", ln)
            stripped_after = ln.lstrip()
            if stripped_after.startswith("speech_gaps:"):
                saw_speech_gaps = True
            elif stripped_after.startswith("untranscribed_speech:"):
                saw_untranscribed = True
            if stripped.startswith("emphasis:"):
                beats = _EMPH_ITEM_RE.findall(stripped)
                if beats:
                    ln = f"  beats: {' '.join(beats[:3])}"
            ln = _UNIFORM_SAFE_ZOOM_RE.sub(r"safe_focus_x(1.05..1.10)=\1", ln)
            ln = _BRACKET_FOCUS_RE.sub(r"focus=\1,\2", ln)
            sec_lines.append(ln.rstrip())
        if not saw_pauses and not is_silent_block and (has_word_lines or speech_spans):
            if vad_gap_pauses:
                sec_lines.append("  pauses: " + " ".join(f"{a:.2f}-{b:.2f}" for a, b in vad_gap_pauses))
            else:
                sec_lines.append("  pauses: none")
        if has_uncovered and not saw_speech_gaps and len(speech_spans) >= 2:
            gaps = [
                (speech_spans[i][1], speech_spans[i + 1][0])
                for i in range(len(speech_spans) - 1)
                if speech_spans[i + 1][0] - speech_spans[i][1] >= 0.25
            ]
            if gaps:
                sec_lines.append("  speech_gaps: " + " ".join(f"{a:.2f}-{b:.2f}({b - a:.2f}s)" for a, b in gaps))
        if has_uncovered and not saw_untranscribed:
            sec_lines.append(_format_untranscribed_line(uncovered))
        if diet_enabled and sec_lines and has_word_lines:
            clip_name, clip_stem = _facts_header_clip_name_and_stem(raw_lines[0])
            block_lower = block.lower()
            keep_full = (
                (clip_name in active_set)
                or (clip_stem in active_set)
                or is_silent_block
                or bool(re.search(r"\bSILENT\b", raw_lines[0]))
                or has_uncovered
                or saw_untranscribed
                or ("two_shot" in block_lower)
                or bool(re.search(r"\b(?:faces|face_count)=[2-9]\d*\b", block_lower))
            )
            if not keep_full:
                pauses_lines = [ln for ln in sec_lines[1:] if ln.lstrip().startswith("pauses:")]
                clip_label = clip_name or "clip"
                sec_lines = [
                    sec_lines[0],
                    *pauses_lines,
                    f'  (unused take — 0 CAND matches; run edit_packet.py <project> --clip "{clip_label}" for full words)',
                ]
        if sec_lines:
            out_blocks.append("\n".join(sec_lines))
    return "\n\n".join(out_blocks).strip()


def compact_line_map(text: str) -> str:
    """Strip comment headers, `more takes` hints, and redundant low-match tail candidates while preserving top CANDs."""
    if not text:
        return ""
    out: List[str] = []
    in_legacy_brain = False
    cur_cands: List[str] = []

    def _flush_cands() -> None:
        if not cur_cands:
            return
        best = max((float(m.group(1)) for c in cur_cands if (m := _CAND_HEARD_RE.search(c))), default=0.0)
        limit = 3 if best >= 0.85 else len(cur_cands)
        kept = cur_cands[:limit]
        for c in cur_cands[limit:]:
            if "[REPEAT" in c and len(kept) < 4:
                kept.append(c)
        out.extend(kept)
        cur_cands.clear()

    for ln in text.splitlines():
        s = ln.strip()
        if s.startswith("# LINE MAP") or s.startswith("# Zeno chose nothing"):
            continue
        if s.startswith("# BRAIN MEMORY"):
            in_legacy_brain = True
            continue
        if in_legacy_brain:
            if not s or ln.startswith("  - ") or s.startswith("- ["):
                continue
            in_legacy_brain = False
        if s.startswith("+") and "alternate candidates" in s:
            continue
        if s.startswith("CAND "):
            cur_cands.append(ln.rstrip())
            continue
        _flush_cands()
        out.append(_SCENE_TAG_RE.sub("", ln.rstrip()))
    _flush_cands()
    return "\n".join(out).strip()


def compact_brief(text: str) -> str:
    """Clean any legacy `[DO NOT READ...]` banners or deprecated pipe-syntax notes from `brief.md`."""
    if not text:
        return ""
    out: List[str] = []
    for ln in text.splitlines():
        s = ln.strip()
        if s.startswith("[DO NOT"):
            continue
        if s.startswith("# EDIT BRIEF for this job"):
            out.append("# Edit Brief")
            continue
        cleaned = _LEGACY_PIPE_SYNTAX_RE.sub("", ln).rstrip()
        out.append(cleaned)
    text_out = "\n".join(out)
    # Token saver: per-format summary sections that duplicate Core Rules 4-7 are stripped from the unified packet.
    text_out = _DUP_RULES_SECTION_RE.sub("", text_out)
    return re.sub(r"\n{3,}", "\n\n", text_out).strip()


def refresh_line_map(project: Path, mapping: str) -> None:
    """Update `line_map.md`, archiving stale `decisions.txt` and `script_analysis.md` if script dialogue lines changed."""
    project = Path(project)
    path = project / "line_map.md"
    previous = read(path)
    line_headers = lambda txt: re.findall(r"(?m)^L\d+\b.*$", txt)
    if previous and line_headers(previous) != line_headers(mapping):
        history = project / "_history"
        history.mkdir(exist_ok=True)
        stamp = time.time_ns()
        for name in ("decisions.txt", "script_analysis.md"):
            old = project / name
            if old.exists():
                old.replace(history / f"before-script-change-{stamp}-{name}")
        print("[WARNING] Script lines changed; archived prior decisions.txt and script_analysis.md.")
    path.write_text(mapping, encoding="utf-8")


def line_detail(project: Path, line_id: str) -> str:
    """Return full `line_map.md` candidates and matching `facts.md` clip sections for one script line (`--line L12`)."""
    project = Path(project)
    clean_id = line_id.strip()
    m = re.fullmatch(r"(L\d+)[a-z]?", clean_id, re.I)
    if not m:
        raise ValueError("Specify a script line ID such as L12.")
    norm_id = m.group(1).upper()
    mapping = read(project / "line_map.md")
    sections = re.split(r"(?m)(?=^L\d+\b)", mapping)
    selected = next((s for s in sections if re.match(rf"^{norm_id}\b", s)), "")
    if not selected:
        raise ValueError(f"No {norm_id} found in {project / 'line_map.md'}")
    sources = re.findall(r"(?m)^\s*CAND\s+(.+?)\s+\d+(?:\.\d+)?-\d", selected)
    clean_sources = [s.strip().strip('"').strip("'") for s in sources]
    facts = compact_facts(read(project / "facts.md"))
    details = [
        s.strip()
        for s in re.split(r"(?m)(?=^## )", facts)
        if any(s.startswith(f"## {src} (") for src in clean_sources)
    ]
    if not details:
        return selected.strip()
    return selected.strip() + "\n\n" + "\n\n".join(details)


def lines_detail(
    project: Path,
    line_ids: Sequence[str],
    max_bytes: int = 0,
    extra_sources: Sequence[str] = (),
) -> str:
    """Batch form of `line_detail`: candidates for several script lines, with each clip's measurements printed once."""
    project = Path(project)
    norm_ids: List[str] = []
    for raw in line_ids:
        m = re.fullmatch(r"(L\d+)[a-z]?", str(raw).strip(), re.I)
        if m and m.group(1).upper() not in norm_ids:
            norm_ids.append(m.group(1).upper())
    if not norm_ids:
        raise ValueError("Specify script line IDs such as L12,L22a,L28.")
    mapping = read(project / "line_map.md")
    sections = re.split(r"(?m)(?=^L\d+\b)", mapping)
    picked: List[str] = []
    clean_sources: List[str] = []
    for norm_id in norm_ids:
        selected = next((s for s in sections if re.match(rf"^{norm_id}\b", s)), "")
        if not selected:
            continue
        selected = re.split(r"(?m)^## ", selected)[0]
        picked.append(selected.strip())
        for src in re.findall(r"(?m)^\s*CAND\s+(.+?)\s+\d+(?:\.\d+)?(?:-|\.\.)\d", selected):
            src = src.strip().strip('"').strip("'")
            if src not in clean_sources:
                clean_sources.append(src)
    extra_clean: List[str] = []
    for s in extra_sources:
        cs = str(s or "").strip().strip('"').strip("'")
        if cs:
            if "/" in cs or "\\" in cs:
                cs = Path(cs.replace("\\", "/")).name
            if cs and cs not in extra_clean:
                extra_clean.append(cs)
    # Clips the plan actually uses for these lines come first: a line with no candidate still gets its clip's words.
    clean_sources = extra_clean + [s for s in clean_sources if s not in extra_clean]
    if not picked:
        raise ValueError(f"None of {', '.join(norm_ids)} found in {project / 'line_map.md'}")
    facts = compact_facts(read(project / "facts.md"))
    blocks: Dict[str, str] = {}
    for s in re.split(r"(?m)(?=^## )", facts):
        if not s.startswith("## "):
            continue
        hdr_name, _ = _facts_header_clip_name_and_stem(s.splitlines()[0])
        hdr_base = Path(hdr_name.replace("\\", "/")).name if hdr_name else ""
        for src in clean_sources:
            src_base = Path(src.replace("\\", "/")).name
            if hdr_name == src or (hdr_base and hdr_base == src_base) or s.startswith(f"## {src} ("):
                blocks.setdefault(src, s.strip())
    details: List[str] = []
    seen_details: Set[str] = set()
    for src in clean_sources:
        blk = blocks.get(src)
        if blk and blk not in seen_details:
            seen_details.add(blk)
            details.append(blk)
    out = "\n\n".join(picked)
    if max_bytes:
        total_all = out + "".join("\n\n" + d for d in details)
        if len(total_all.encode("utf-8")) <= max_bytes:
            return total_all
    notice = '\n\n(more clip measurements left out to keep this short; run edit_packet.py <project> --clip "<name>" for one clip)'
    for d in details:
        cand = out + "\n\n" + d
        if max_bytes and len((cand + notice).encode("utf-8")) > max_bytes:
            if len((out + notice).encode("utf-8")) <= max_bytes:
                out += notice
            break
        out = cand
    return out


def _part_sort_key(path: Path) -> Tuple[int, str]:
    m = re.fullmatch(r"(?:director|editing)_packet_p(\d+)\.md", path.name)
    return (int(m.group(1)), path.name) if m else (10**9, path.name)


def _extract_src_dir(plan_text: str, project: Path) -> Optional[Path]:
    for line in plan_text.splitlines():
        m = re.match(r"^\s*#\s*src_dir\b\s*:?\s*(.+)$", line, re.I)
        if not m:
            continue
        raw_val = m.group(1).strip()
        if len(raw_val) >= 2 and raw_val[0] == raw_val[-1] and raw_val[0] in ('"', "'"):
            raw_val = raw_val[1:-1].strip()
        if not raw_val:
            return None
        cand = Path(raw_val).expanduser()
        if cand.is_dir():
            return cand
        if not cand.is_absolute():
            for base in (project.parent.parent, ROOT):
                rel = base / cand
                if rel.is_dir():
                    return rel
        return cand
    return None


def _resolve_cache_dir(project: Path, cache_name: str) -> Optional[Path]:
    if not cache_name:
        return None
    cand = Path(cache_name)
    candidates = [cand] if cand.is_absolute() else [
        project.parent / "_clips" / cache_name,
        project.parent / cache_name,
        project / cache_name,
        ROOT / "work" / "_clips" / cache_name,
    ]
    for d in candidates:
        if d.is_dir():
            return d
    return None


def _parse_clip_caches(raw: Any) -> List[Tuple[str, str, Dict[str, Any]]]:
    if isinstance(raw, dict) and isinstance(raw.get("clips"), (dict, list)):
        target = raw["clips"]
    else:
        target = raw

    entries: List[Tuple[str, str, Dict[str, Any]]] = []
    if isinstance(target, dict):
        for k, v in target.items():
            key_str = str(k).strip()
            if isinstance(v, str):
                entries.append((key_str, v.strip(), {}))
            elif isinstance(v, dict):
                cname = str(v.get("cache") or v.get("cache_name") or v.get("work_dir") or v.get("dir") or key_str).strip()
                raw_name = str(v.get("name") or v.get("clip") or v.get("src") or key_str).strip()
                dname = Path(raw_name).name if ("/" in raw_name or "\\" in raw_name) else raw_name
                entries.append((dname, cname, v))
    elif isinstance(target, list):
        for item in target:
            if isinstance(item, str):
                cname = item.strip()
                entries.append((Path(cname).name, cname, {}))
            elif isinstance(item, dict):
                raw_name = str(item.get("src") or item.get("clip") or item.get("name") or item.get("path") or "").strip()
                cname = str(item.get("cache") or item.get("cache_name") or item.get("work_dir") or item.get("dir") or raw_name).strip()
                dname = Path(raw_name).name if raw_name else Path(cname).name
                entries.append((dname, cname, item))

    dedup: Dict[str, Tuple[str, str, Dict[str, Any]]] = {}
    for dname, cname, meta in entries:
        key = cname or dname
        if not key:
            continue
        if key not in dedup:
            dedup[key] = (dname or Path(cname).name, cname, dict(meta))
        else:
            prev_name, prev_cname, prev_meta = dedup[key]
            better_name = dname if ("." in dname and "." not in prev_name) else prev_name
            dedup[key] = (better_name, prev_cname or cname, {**prev_meta, **meta})
    return list(dedup.values())


def _facts_clip_index(facts_text: str) -> Dict[str, Dict[str, Any]]:
    index: Dict[str, Dict[str, Any]] = {}
    for sec in re.split(r"(?m)(?=^## )", facts_text):
        lines = sec.splitlines()
        if not lines or not lines[0].startswith("## "):
            continue
        m = re.match(r"^##\s+(.+?)\s*\((.*)\)\s*$", lines[0])
        if not m:
            continue
        name = m.group(1).strip()
        tags = m.group(2)
        dur_m = re.search(r"\b(\d+(?:\.\d+)?)s\b", tags)
        dim_m = re.search(r"\b(\d+x\d+)\b", tags)
        silent = bool(re.search(r"\bSILENT\b", tags)) or any(
            "! SILENT / AMBIENT CLIP" in ln for ln in lines[1:]
        )
        speech_spans, word_runs, _ = _extract_facts_speech_and_words(lines[1:])
        vad_sec: Optional[float] = (
            round(sum(max(0.0, b - a) for a, b in speech_spans), 3) if speech_spans else None
        )
        pauses: List[Tuple[float, float]] = []
        words_count = 0
        for ln in lines[1:]:
            s = ln.lstrip()
            if s.startswith("pauses:"):
                for a, b in re.findall(r"(\d+(?:\.\d+)?)-(\d+(?:\.\d+)?)", s):
                    fa, fb = float(a), float(b)
                    if fb > fa:
                        pauses.append((fa, fb))
            elif not s.startswith(("!", "speech (voice detector", "voiced (", "emphasis", "faces ", "speech gaps", "speech_gaps:", "untranscribed_speech:")):
                words_count += len(re.findall(r"(?:^|\s)\d+\.\d+\s+\S+", ln))
        info = {
            "duration": float(dur_m.group(1)) if dur_m else None,
            "dims": dim_m.group(1) if dim_m else None,
            "silent": silent,
            "vad_sec": vad_sec,
            "speech_spans": speech_spans,
            "word_runs": word_runs,
            "pauses": _normalize_spans(pauses),
            "words_count": words_count,
            "raw_block": sec.strip(),
        }
        index[name] = info
        index.setdefault(Path(name).stem, info)
    return index


def _load_cache_words(cache_dir: Optional[Path]) -> Tuple[List[Dict[str, Any]], bool]:
    if not cache_dir:
        return [], False
    for fname in ("hq_words.json", "align.json", "words.json"):
        data = _read_json_safe(cache_dir / fname)
        raw_list = data.get("words") if isinstance(data, dict) else data
        if isinstance(raw_list, list):
            words = [w for w in raw_list if isinstance(w, dict)]
            return words, True
    return [], False


def _load_cache_pauses(cache_dir: Optional[Path], fact_info: Dict[str, Any]) -> List[Tuple[float, float]]:
    if cache_dir:
        sdata = _read_json_safe(cache_dir / "silences.json")
        raw = sdata.get("silences") if isinstance(sdata, dict) else sdata
        if isinstance(raw, list):
            pairs: List[Tuple[float, float]] = []
            for item in raw:
                if isinstance(item, dict) and "start" in item and "end" in item:
                    try:
                        pairs.append((float(item["start"]), float(item["end"])))
                    except (TypeError, ValueError):
                        continue
                elif isinstance(item, (list, tuple)) and len(item) >= 2:
                    try:
                        pairs.append((float(item[0]), float(item[1])))
                    except (TypeError, ValueError):
                        continue
            norm = _normalize_spans(pairs)
            if norm or raw == []:
                return norm
    return list(fact_info.get("pauses") or [])


def _py_cli_cmd() -> str:
    return r".venv\Scripts\python.exe" if os.name == "nt" else ".venv/bin/python"


def _rel_project_arg(project: Path) -> str:
    try:
        return project.resolve().relative_to(ROOT.resolve()).as_posix()
    except ValueError:
        return project.as_posix()


def _extract_cooldown_sec(tmeta: Dict[str, Any], warning: str) -> Optional[float]:
    for key in ("cooldown_sec", "retry_after_sec", "cooldown_seconds"):
        val = tmeta.get(key)
        if val is not None:
            try:
                f = float(val)
                if f > 0:
                    return f
            except (TypeError, ValueError):
                pass
    if warning:
        m = re.search(r"(?:cooldown|retry(?:\s+after|\s+in)?|wait)\s*[:~]?\s*(\d+(?:\.\d+)?)\s*s", warning, re.I)
        if m:
            try:
                return float(m.group(1))
            except ValueError:
                pass
    return None


def clip_diagnostics(project: Path, facts_text: Optional[str] = None) -> List[Dict[str, Any]]:
    """Collect per-clip coverage, VAD speech spans, word counts, and status for `project` (new & old caches)."""
    project = Path(project)
    if facts_text is None:
        facts_text = read(project / "facts.md")
    facts_idx = _facts_clip_index(facts_text)
    caches_raw = _read_json_safe(project / "clip_caches.json")
    entries = _parse_clip_caches(caches_raw)
    if not entries and facts_idx:
        seen_blocks = set()
        for sec in re.split(r"(?m)(?=^## )", facts_text):
            m = re.match(r"^##\s+(.+?)\s*\(", sec.splitlines()[0]) if sec.splitlines() else None
            if m:
                dname = m.group(1).strip()
                if dname not in seen_blocks:
                    seen_blocks.add(dname)
                    entries.append((dname, "", {}))

    diags: List[Dict[str, Any]] = []
    for display_name, cache_name, meta in entries:
        cache_dir = _resolve_cache_dir(project, cache_name) if cache_name else None
        info = _read_json_safe(cache_dir / "info.json") if cache_dir else None
        info = info if isinstance(info, dict) else {}
        tmeta = _read_json_safe(cache_dir / "transcript_meta.json") if cache_dir else None
        tmeta = tmeta if isinstance(tmeta, dict) else {}

        if "." not in display_name and info.get("path"):
            display_name = Path(str(info["path"])).name
        fact_info = facts_idx.get(display_name) or facts_idx.get(Path(display_name).stem) or {}

        w = info.get("width") or meta.get("width")
        h = info.get("height") or meta.get("height")
        dims = f"{int(w)}x{int(h)}" if (w and h) else (fact_info.get("dims") or "?x?")

        dur_val = tmeta.get("duration_sec")
        if dur_val is None:
            dur_val = info.get("duration")
        if dur_val is None:
            dur_val = meta.get("duration")
        if dur_val is None:
            dur_val = fact_info.get("duration")
        try:
            duration: Optional[float] = float(dur_val) if dur_val is not None else None
        except (TypeError, ValueError):
            duration = None

        speech_spans: List[Tuple[float, float]] = []
        spans_known = False
        if cache_dir:
            sdata = _read_json_safe(cache_dir / "speech_spans.json")
            raw_spans = sdata.get("spans") if isinstance(sdata, dict) else sdata
            if isinstance(raw_spans, list):
                speech_spans = _normalize_spans(raw_spans)
                spans_known = True
        if not spans_known and fact_info.get("speech_spans"):
            speech_spans = list(fact_info["speech_spans"])
            spans_known = True

        words, words_found = _load_cache_words(cache_dir)
        words_for_cov = words if words_found else list(fact_info.get("word_runs") or [])

        vad_speech_sec: Optional[float] = None
        if tmeta.get("active_speech_sec") is not None:
            try:
                vad_speech_sec = float(tmeta["active_speech_sec"])
            except (TypeError, ValueError):
                vad_speech_sec = None
        if vad_speech_sec is None and spans_known:
            vad_speech_sec = round(sum(max(0.0, b - a) for a, b in speech_spans), 3)
        if vad_speech_sec is None and meta.get("active_speech") is not None:
            try:
                vad_speech_sec = float(meta["active_speech"])
            except (TypeError, ValueError):
                vad_speech_sec = None
        if vad_speech_sec is None:
            vad_speech_sec = fact_info.get("vad_sec")

        words_count: int
        if tmeta.get("words_count") is not None:
            try:
                words_count = int(tmeta["words_count"])
            except (TypeError, ValueError):
                words_count = len(words) if words_found else int(fact_info.get("words_count") or 0)
        elif words_found:
            words_count = len(words)
        elif meta.get("words_count") is not None:
            try:
                words_count = int(meta["words_count"])
            except (TypeError, ValueError):
                words_count = int(fact_info.get("words_count") or 0)
        elif cache_dir and vad_speech_sec == 0.0:
            words_count = 0
        else:
            words_count = int(fact_info.get("words_count") or 0)

        silent = bool(fact_info.get("silent") or info.get("has_audio") is False or vad_speech_sec == 0.0)
        if isinstance(tmeta.get("uncovered_spans"), list):
            uncovered = _normalize_spans(tmeta["uncovered_spans"])
        elif speech_spans and not fact_info.get("silent"):
            uncovered = uncovered_speech_spans(speech_spans, words_for_cov, min_uncovered_sec=1.2)
        else:
            uncovered = []

        covered_speech_sec: Optional[float] = None
        speech_coverage: Optional[float] = None
        if tmeta.get("covered_speech_sec") is not None and tmeta.get("speech_coverage") is not None:
            try:
                covered_speech_sec = float(tmeta["covered_speech_sec"])
                speech_coverage = float(tmeta["speech_coverage"])
            except (TypeError, ValueError):
                covered_speech_sec = None
                speech_coverage = None
        if speech_coverage is None and vad_speech_sec is not None:
            if vad_speech_sec <= 0.0:
                covered_speech_sec = 0.0
                speech_coverage = 1.0
            elif speech_spans:
                covered_speech_sec = min(vad_speech_sec, _covered_seconds_in_spans(speech_spans, words_for_cov))
                speech_coverage = round(min(1.0, max(0.0, covered_speech_sec / vad_speech_sec)), 4)

        raw_status = str(tmeta.get("status") or "").strip().lower()
        if raw_status:
            status = raw_status
        elif info.get("has_audio") is False or vad_speech_sec == 0.0 or (fact_info.get("silent") and not speech_spans):
            status = "no_speech"
        elif uncovered or (vad_speech_sec is not None and vad_speech_sec >= 1.2 and words_count == 0):
            status = "incomplete"
        elif (vad_speech_sec is not None and vad_speech_sec > 0.0) or words_count > 0:
            status = "ok"
        else:
            status = "unknown"

        warning = str(tmeta.get("warning") or "").strip()
        chunks_done = tmeta.get("chunks_done")
        chunks_total = tmeta.get("chunks_total")
        pauses = _load_cache_pauses(cache_dir, fact_info)
        diags.append({
            "display_name": display_name,
            "cache_name": cache_name,
            "_cache_dir": cache_dir,
            "dims": dims,
            "duration": duration,
            "vad_speech_sec": vad_speech_sec,
            "covered_speech_sec": covered_speech_sec,
            "speech_coverage": speech_coverage,
            "words_count": words_count,
            "status": status,
            "speech_spans": speech_spans,
            "uncovered_spans": uncovered,
            "pauses": pauses,
            "chunks_done": chunks_done,
            "chunks_total": chunks_total,
            "warning": warning,
            "cooldown_sec": _extract_cooldown_sec(tmeta, warning),
            "silent": silent,
            "words": words,
            "facts_block": str(fact_info.get("raw_block") or ""),
        })
    return diags


def _format_audio_entry(path: Path) -> str:
    if path.suffix.lower() == ".wav":
        try:
            with wave.open(str(path), "rb") as wf:
                rate = wf.getframerate() or 0
                frames = wf.getnframes() or 0
                ch = wf.getnchannels() or 1
                if rate > 0:
                    return f"{path.name} ({frames / rate:.2f}s, {ch}ch, {rate}Hz)"
        except Exception:
            pass
    try:
        kb = path.stat().st_size / 1024.0
        size_str = f"{kb / 1024.0:.1f}MB" if kb >= 1024.0 else f"{kb:.0f}KB"
        return f"{path.name} ({size_str})"
    except OSError:
        return path.name


def _unmatched_line_ids(line_map_text: str) -> List[str]:
    missing: List[str] = []
    for section in re.split(r"(?m)(?=^L\d+\b)", line_map_text):
        lines = section.splitlines()
        if not lines:
            continue
        m = re.match(r"^(L\d+)\b", lines[0])
        if not m:
            continue
        body = "\n".join(lines[1:])
        if re.search(r"(?m)^\s*CAND\s+", body):
            continue
        if re.search(r"(?im)(?:^\s*(?:NO\b|MISSING\b|0\s+cand|unmatched\b)|\bNO\s+CLIP\b|\b0\s+cand|\bunmatched\b)", body):
            missing.append(m.group(1))
    return missing


def _raw_footage_inventory(project: Path, texts: Dict[str, str], sheets: List[str]) -> str:
    """Compact clip inventory, separate audio check, unmatched lines, and contact sheets."""
    lines_out: List[str] = []
    diags = clip_diagnostics(project, facts_text=texts.get("facts.md", ""))
    has_incomplete_warning = False
    for d in diags:
        dur_str = f"{d['duration']:.2f}s" if d["duration"] is not None else "?s"
        vad = d["vad_speech_sec"]
        if vad is not None and vad > 0.0:
            speech_col = f"speech={vad:.1f}s"
        elif d["status"] == "no_speech" or d["silent"] or d["words_count"] == 0:
            speech_col = "SILENT"
        else:
            speech_col = "speech=unknown"

        status = d["status"]
        uncovered = d["uncovered_spans"]
        flag_str = ""
        if status in ("incomplete", "quota_exceeded", "cloud_failed") or uncovered:
            tag = status.upper() if status in ("quota_exceeded", "cloud_failed") else "INCOMPLETE"
            if uncovered:
                shown_sp = ", ".join(f"{a:.1f}-{b:.1f}s" for a, b in uncovered[:4])
                more_sp = f" (+{len(uncovered) - 4} more)" if len(uncovered) > 4 else ""
                flag_str = f", {tag}: uncovered {shown_sp}{more_sp} — run --retry-incomplete"
            else:
                detail = d["warning"] or "incomplete transcript"
                flag_str = f", {tag}: {detail} — run --retry-incomplete"
            if (
                status in ("incomplete", "quota_exceeded", "cloud_failed")
                or any((b - a) >= 1.5 for a, b in uncovered)
                or sum(b - a for a, b in uncovered) >= 1.5
            ):
                has_incomplete_warning = True
        elif (
            vad is not None
            and vad < 0.6
            and (d["duration"] or 0.0) >= 2.0
            and not d["silent"]
        ):
            flag_str = " [non-speech/SFX audio — mute=1 if B-roll]"

        lines_out.append(f"- `{d['display_name']}`: {d['dims']}, {dur_str}, {speech_col}, {d['words_count']}w{flag_str}")

    src_dir = _extract_src_dir(texts.get("plan.edl", ""), project)
    if src_dir is not None and src_dir.is_dir():
        try:
            audio_files = sorted(
                (
                    p for p in src_dir.iterdir()
                    if p.is_file() and not p.name.startswith(".") and p.suffix.lower() in _AUDIO_EXTS
                ),
                key=lambda p: p.name.lower(),
            )
        except OSError:
            audio_files = []
        if audio_files:
            lines_out.append("- Separate audio in `#src_dir`: " + ", ".join(_format_audio_entry(p) for p in audio_files))

    missing_lines = _unmatched_line_ids(texts.get("line_map.md", ""))
    if missing_lines:
        lines_out.append(f"- Unmatched script lines (0 candidates): {', '.join(missing_lines)}")
    if sheets:
        lines_out.append(f"- Contact sheets: {', '.join(sheets)}")
    if has_incomplete_warning:
        rel_proj = _rel_project_arg(project)
        lines_out.append(
            f"- Action needed: Incomplete transcript coverage detected (>=1.5s uncovered speech). "
            f"Run `{_py_cli_cmd()} scripts/edit_packet.py {rel_proj} --diagnose` and "
            f"`{_py_cli_cmd()} scripts/zeno.py {rel_proj} --retry-incomplete --compact` before planning."
        )

    if not lines_out:
        return ""
    return "## Footage Summary\n" + "\n".join(lines_out)


def diagnose_project(project: Path) -> str:
    """Return per-clip speech coverage diagnostics and actionable CLI retry commands (zero raw cache paths)."""
    project = Path(project)
    if not project.is_dir():
        raise ValueError(f"Project directory not found: {project}")
    if not ((project / "clip_caches.json").is_file() or (project / "facts.md").is_file()):
        raise ValueError(f"Missing clip_caches.json / facts.md in {project}; run --inspect first.")

    diags = clip_diagnostics(project)
    rel_proj = _rel_project_arg(project)
    py_cmd = _py_cli_cmd()
    lines_out: List[str] = [f"Project: {project.name} ({len(diags)} clip(s))"]
    incomplete: List[Dict[str, Any]] = []

    for d in diags:
        dur_str = f"{d['duration']:.2f}s" if d["duration"] is not None else "?s"
        vad_str = f"{d['vad_speech_sec']:.1f}s" if d["vad_speech_sec"] is not None else "?s"
        cov_str = f"{round(d['speech_coverage'] * 100):.0f}%" if d["speech_coverage"] is not None else "?%"
        unc_list = d["uncovered_spans"]
        unc_str = ", ".join(f"{a:.1f}-{b:.1f}s" for a, b in unc_list) if unc_list else "none"
        parts = [
            f"status={d['status']}",
            f"duration={dur_str}",
            f"vad_speech_sec={vad_str}",
            f"words_count={d['words_count']}",
            f"speech_coverage={cov_str}",
            f"uncovered_spans={unc_str}",
        ]
        if d["chunks_done"] is not None and d["chunks_total"] is not None:
            parts.append(f"chunks={d['chunks_done']}/{d['chunks_total']}")
        if d["warning"]:
            parts.append(f"warning={d['warning']}")
        lines_out.append(f"- {d['display_name']}: " + " | ".join(parts))
        if d["status"] in ("incomplete", "quota_exceeded", "cloud_failed") or unc_list:
            incomplete.append(d)

    if incomplete:
        names = ", ".join(d["display_name"] for d in incomplete)
        first_name = incomplete[0]["display_name"]
        lines_out.append("")
        lines_out.append(f"Incomplete clips ({len(incomplete)}): {names}")
        lines_out.append(f"Retry command:       {py_cmd} scripts/zeno.py {rel_proj} --retry-incomplete --compact")
        lines_out.append(f"Inspect single clip: {py_cmd} scripts/edit_packet.py {rel_proj} --clip \"{first_name}\"")
        lines_out.append(f"Retry single clip:   {py_cmd} scripts/zeno.py {rel_proj} --clip \"{first_name}\" --compact")
    else:
        lines_out.append("")
        lines_out.append("All clips have complete transcript coverage.")
    return "\n".join(lines_out)


def _format_word_phrases(words: Sequence[Dict[str, Any]]) -> List[str]:
    out: List[str] = []
    phrase: List[str] = []
    valid = [w for w in words if isinstance(w, dict) and str(w.get("w") or w.get("word") or "").strip()]
    for idx, w in enumerate(valid):
        txt = str(w.get("w") or w.get("word") or "").strip()
        try:
            st = float(w.get("start", 0.0))
            en = float(w.get("end", st))
        except (TypeError, ValueError):
            continue
        phrase.append(f"{st:.2f} {txt}")
        core = txt.rstrip('"\'”’)]}')
        has_punct = bool(core) and core[-1] in ".?!।,;:"
        next_gap = False
        if idx + 1 < len(valid):
            try:
                next_gap = float(valid[idx + 1].get("start", 0.0)) - en >= 0.12
            except (TypeError, ValueError):
                next_gap = False
        if idx + 1 == len(valid) or next_gap or has_punct:
            out.append("  " + " ".join(phrase) + f" |{en:.2f}")
            phrase = []
    return out


def clip_detail(project: Path, clip_name: str) -> str:
    """Return structured single-clip diagnostics, spans, pauses, cues, and timestamped words (no raw cache paths)."""
    project = Path(project)
    if not project.is_dir():
        raise ValueError(f"Project directory not found: {project}")
    diags = clip_diagnostics(project)
    if not diags:
        raise ValueError(f"No clips recorded in {project}; run --inspect first.")

    target = clip_name.strip().strip('"').strip("'")
    low = target.lower()
    stem_low = Path(target).stem.lower()
    matched = [
        d for d in diags
        if d["display_name"].lower() == low
        or Path(d["display_name"]).stem.lower() == stem_low
        or (d["cache_name"] and d["cache_name"].lower() == low)
    ]
    if not matched:
        matched = [d for d in diags if low in d["display_name"].lower()]
    if not matched:
        avail = ", ".join(d["display_name"] for d in diags)
        raise ValueError(f"Clip '{clip_name}' not found in {project}. Available: {avail}")
    if len(matched) > 1:
        names = ", ".join(d["display_name"] for d in matched)
        raise ValueError(f"Ambiguous clip '{clip_name}'; matched: {names}")

    d = matched[0]
    dur_str = f"{d['duration']:.2f}s" if d["duration"] is not None else "?s"
    vad_str = f"{d['vad_speech_sec']:.2f}s" if d["vad_speech_sec"] is not None else "?s"
    cov_str = f"{d['covered_speech_sec']:.2f}s" if d["covered_speech_sec"] is not None else "?s"
    pct_str = f"{round(d['speech_coverage'] * 100):.0f}%" if d["speech_coverage"] is not None else "?%"
    chunks_str = (
        f"{d['chunks_done']}/{d['chunks_total']}"
        if (d["chunks_done"] is not None and d["chunks_total"] is not None)
        else "unknown"
    )
    speech_str = " ".join(f"{a:.2f}-{b:.2f}" for a, b in d["speech_spans"]) if d["speech_spans"] else "none"
    pauses_str = " ".join(f"{a:.2f}-{b:.2f}" for a, b in d["pauses"]) if d["pauses"] else "none"
    unc_str = (
        ", ".join(f"{a:.2f}-{b:.2f}s ({b - a:.1f}s)" for a, b in d["uncovered_spans"])
        if d["uncovered_spans"]
        else "none"
    )

    out: List[str] = [
        f"Clip: {d['display_name']}",
        f"status: {d['status']}",
        f"dimensions: {d['dims']}",
        f"duration: {dur_str}",
        f"vad_speech_sec: {vad_str}",
        f"covered_speech_sec: {cov_str} (speech_coverage: {pct_str})",
        f"words_count: {d['words_count']}",
        f"chunks: {chunks_str}",
        f"speech_spans: {speech_str}",
        f"pauses: {pauses_str}",
        f"uncovered_spans: {unc_str}",
    ]
    if d["warning"]:
        out.append(f"warning: {d['warning']}")

    cache_dir = d.get("_cache_dir")
    gdata = _read_json_safe(cache_dir / "gemini_transcript.json") if cache_dir else None
    cues = gdata.get("cues") if isinstance(gdata, dict) else None
    if isinstance(cues, list) and cues:
        out.append("")
        out.append("## Cues")
        for cue in cues:
            if isinstance(cue, dict) and str(cue.get("text") or "").strip():
                try:
                    cs = float(cue.get("start", 0.0))
                    ce = float(cue.get("end", cs))
                    out.append(f"  [{cs:.2f}-{ce:.2f}] {str(cue['text']).strip()}")
                except (TypeError, ValueError):
                    out.append(f"  {str(cue['text']).strip()}")

    word_lines = _format_word_phrases(d.get("words") or [])
    if word_lines:
        out.append("")
        out.append("## Timestamped Words")
        out.extend(word_lines)
    elif d.get("facts_block"):
        out.append("")
        out.append("## Facts Block")
        out.append(d["facts_block"])

    if d["status"] in ("incomplete", "quota_exceeded", "cloud_failed") or d["uncovered_spans"]:
        rel_proj = _rel_project_arg(project)
        out.append("")
        out.append(
            f"Retry command: {_py_cli_cmd()} scripts/zeno.py {rel_proj} --clip \"{d['display_name']}\" --compact"
        )
    return "\n".join(out)


def _chunk_header(index: int, total: int) -> str:
    return f"[PACKET PART {index}/{total}]\n\n"


def _chunk_packet(packet: str) -> List[str]:
    """Split oversized packets into `<= CHUNK_MAX_BYTES` / `<= CHUNK_MAX_LINES` parts at clean boundaries."""
    raw_lines = packet.splitlines()
    if _utf8_len(packet) <= _CHUNK_TRIGGER_BYTES and len(raw_lines) <= _CHUNK_TRIGGER_LINES:
        return [packet]

    reserve_bytes = 64
    reserve_lines = 2
    max_body_bytes = max(1024, CHUNK_MAX_BYTES - reserve_bytes)
    max_body_lines = max(10, CHUNK_MAX_LINES - reserve_lines)
    line_bytes = [_utf8_len(ln) + 1 for ln in raw_lines]

    ranges: List[Tuple[int, int]] = []
    start = 0
    n = len(raw_lines)
    while start < n:
        cur_bytes = 0
        end = start
        while end < n and (end - start) < max_body_lines:
            nxt = cur_bytes + line_bytes[end]
            if end > start and nxt > max_body_bytes:
                break
            cur_bytes = nxt
            end += 1
            if cur_bytes > max_body_bytes:
                break
        if end >= n:
            ranges.append((start, n))
            break

        min_bytes_soft = int(cur_bytes * 0.55)
        min_lines_soft = max(1, int((end - start) * 0.55))
        acc = [0] * (end - start + 1)
        for idx in range(end - start):
            acc[idx + 1] = acc[idx] + line_bytes[start + idx]

        chosen = end
        for k in range(end - 1, start, -1):
            offset = k - start
            if acc[offset] < int(cur_bytes * 0.40) and offset < max(1, int((end - start) * 0.40)):
                break
            if raw_lines[k].startswith(("## ", "# ")):
                chosen = k
                break
        else:
            for k in range(end - 1, start, -1):
                offset = k - start
                if acc[offset] < min_bytes_soft and offset < min_lines_soft:
                    break
                if re.match(r"^L\d+\b", raw_lines[k]):
                    chosen = k
                    break
        ranges.append((start, chosen))
        start = chosen

    total = len(ranges)
    return [
        _chunk_header(idx, total) + "\n".join(raw_lines[s:e]).rstrip("\n") + "\n"
        for idx, (s, e) in enumerate(ranges, 1)
    ]


def packet_parts(project: Path) -> List[Path]:
    """Return ordered packet part paths from `editing_packet.json`, chunk files, or `director_packet.md`."""
    project = Path(project)
    manifest = _read_json_safe(project / "editing_packet.json")
    if isinstance(manifest, dict) and isinstance(manifest.get("parts"), list):
        resolved = [project / str(name) for name in manifest["parts"] if str(name).strip()]
        existing = [p for p in resolved if p.is_file()]
        if existing and len(existing) == len(resolved):
            return existing
    chunks = sorted((p for p in project.glob("editing_packet_p*.md") if p.is_file()), key=_part_sort_key)
    if chunks:
        return chunks
    if (project / "director_packet.md").is_file():
        return [project / "director_packet.md"]
    return [project / "editing_packet.md"]


def _ensure_brief_text(project: Path, plan_text: str) -> str:
    """Read `brief.md` or generate it on-demand via `edit_brief` if missing."""
    brief_text = read(project / "brief.md")
    if brief_text.strip():
        return brief_text
    try:
        import edit_brief

        plan_dict = _read_json_safe(project / "plan.json")
        if not isinstance(plan_dict, dict):
            plan_dict = {"page": {"mode": "scroll"}} if "#page scroll" in plan_text else {}
        meta: Dict[str, Any] = {}
        script_rel = plan_dict.get("script")
        if script_rel:
            try:
                from common import resolve
                from prepare import parse_script_file

                sp = resolve(str(script_rel))
                if sp.is_file():
                    meta, _ = parse_script_file(sp)
            except Exception:
                meta = {}
        edit_brief.write(project, meta, plan_dict, style=str(plan_dict.get("style") or ""))
        return read(project / "brief.md")
    except Exception:
        return ""


def build(project: Path) -> Tuple[str, Dict[str, Any]]:
    """Assemble the unified compact packet string and its manifest dict for `project`."""
    project = Path(project)
    plan_text = read(project / "plan.edl")
    brief_raw = _ensure_brief_text(project, plan_text)
    texts: Dict[str, str] = {
        "brief.md": brief_raw,
        "line_map.md": read(project / "line_map.md"),
        "facts.md": read(project / "facts.md"),
        "plan.edl": plan_text,
        "decisions.txt": read(project / "decisions.txt"),
    }
    for req_name in ("brief.md", "line_map.md", "facts.md"):
        if not texts[req_name].strip():
            raise ValueError(f"Missing {req_name} in {project}; run --inspect first.")

    sheets = sorted(p.name for p in project.glob("clip_sheet_*.jpg"))
    sections: List[str] = ["# Zeno Director Packet"]

    # (1) Segregated 1-format brief
    sections.append(compact_brief(texts["brief.md"]))

    # Optional compact footage & contact-sheet inventory + Zeno-owned plan headers
    inventory = _raw_footage_inventory(project, texts, sheets)
    if inventory:
        sections.append(inventory)
    if texts["plan.edl"].strip():
        sections.append("## Current `plan.edl`\n" + texts["plan.edl"].strip())

    # (2) Script Line Map (`line_map.md`)
    sections.append("## Script Line Map (`line_map.md`)\n" + compact_line_map(texts["line_map.md"]))

    # (3) Measured Footage (`facts.md` with exact `focus=x,y` zoom values and word timestamps)
    active_clips = _extract_active_clips(texts["line_map.md"], texts["plan.edl"])
    sections.append("## Measured Footage (`facts.md`)\n" + compact_facts(texts["facts.md"], active_clips=active_clips))

    # (4) Recorded exceptions (`decisions.txt`) only when non-empty
    if texts["decisions.txt"].strip():
        sections.append("## Recorded Exceptions (`decisions.txt`)\n" + texts["decisions.txt"].strip())

    packet = "\n\n".join(s for s in sections if s).strip() + "\n"
    chunks = _chunk_packet(packet)
    part_names = (
        [f"editing_packet_p{i}.md" for i in range(1, len(chunks) + 1)]
        if len(chunks) > 1
        else ["director_packet.md"]
    )
    manifest: Dict[str, Any] = {
        "schema": 2,
        "director_packet": "director_packet.md",
        "editing_packet": "editing_packet.md",
        "sources": {name: hashlib.sha256(text.encode("utf-8")).hexdigest() for name, text in texts.items()},
        "clip_sheets": sheets,
        "parts": part_names,
        "source_bytes": sum(_utf8_len(t) for t in texts.values()),
        "packet_bytes": _utf8_len(packet),
    }
    return packet, manifest


def build_editing_packet(
    work_dir: Path | str,
    *,
    write_files: bool = True,
    return_text: bool = False,
    **_kwargs: Any,
) -> Path | str:
    """Build the unified compact Director Packet and write both `director_packet.md` and `editing_packet.md`."""
    project = Path(work_dir)
    packet, manifest = build(project)
    if not write_files:
        return packet

    chunks = _chunk_packet(packet)
    for stale in list(project.glob("editing_packet_p*.md*")) + list(project.glob("director_packet_p*.md*")):
        if re.fullmatch(r"(?:director|editing)_packet_p\d+\.md(?:\.tmp)?", stale.name):
            stale.unlink(missing_ok=True)

    director_path = project / "director_packet.md"
    editing_path = project / "editing_packet.md"
    _atomic_write(director_path, packet)
    _atomic_write(editing_path, packet)

    if len(chunks) > 1:
        for idx, chunk_text in enumerate(chunks, 1):
            _atomic_write(project / f"editing_packet_p{idx}.md", chunk_text)
            _atomic_write(project / f"director_packet_p{idx}.md", chunk_text)

    _atomic_write(project / "editing_packet.json", json.dumps(manifest, indent=2) + "\n")
    return packet if return_text else director_path


def write(project: Path) -> Path:
    """Write `director_packet.md`, `editing_packet.md`, and `editing_packet.json`; return `director_packet.md` path."""
    out = build_editing_packet(Path(project), write_files=True, return_text=False)
    return Path(out)


def write_director_packet(project: Path) -> Path:
    """Alias for `write(project)` ensuring `director_packet.md` and `editing_packet.md` stay synchronized."""
    return write(project)


def main() -> None:
    parser = argparse.ArgumentParser(description="Build Director Packet or inspect script lines / clip speech diagnostics")
    parser.add_argument("project", type=Path, help="Path to work/<project> directory")
    parser.add_argument("--line", help="Lookup candidates and measured clip details for a single line ID (e.g. L12)")
    parser.add_argument("--lines", help="Lookup several line IDs in one call, comma separated (e.g. L12,L22a,L28)")
    parser.add_argument("--diagnose", action="store_true", help="Print per-clip speech coverage, status, and uncovered speech spans")
    parser.add_argument("--clip", help="Inspect structured measurements, spans, pauses, cues, and words for one clip")
    args = parser.parse_args()
    try:
        if args.diagnose:
            print(diagnose_project(args.project))
        if args.clip:
            print(clip_detail(args.project, args.clip))
        if args.line:
            print(line_detail(args.project, args.line))
        if args.lines:
            print(lines_detail(args.project, [x for x in re.split(r"[,\s]+", args.lines) if x]))
        if not (args.diagnose or args.clip or args.line or args.lines):
            out_path = write(args.project)
            print(f"[PACKET] {out_path}")
            parts = packet_parts(args.project)
            if len(parts) > 1:
                print(f"[PACKET_PARTS] {' '.join(p.name for p in parts)}")
    except ValueError as exc:
        parser.error(str(exc))


if __name__ == "__main__":
    main()
