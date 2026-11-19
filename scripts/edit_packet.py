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
import json
import re
import sys
import time
import wave
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

SCRIPT_DIR = Path(__file__).resolve().parent
ROOT = SCRIPT_DIR.parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

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


def compact_facts(text: str) -> str:
    """Compact `facts.md` while preserving exact `focus=x,y` zoom values, safe two-shot spans, beat times, and word timestamps.

    Strips redundant `# FACTS:` banners, duplicate VAD/voiced/pause/gap interval lists (already encoded by phrase `|end` markers),
    and repeated Indic/script word strings inside `emphasis:` lines (keeping top beat timestamps + dB).
    """
    if not text:
        return ""
    blocks = re.split(r"(?m)(?=^## )", text)
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
        skip_prefixes = ("speech (voice detector", "voiced (real sound") + (
            ("pauses:", "speech gaps", "speech_gaps:") if has_word_lines else ()
        )
        sec_lines: List[str] = []
        for idx, ln in enumerate(raw_lines):
            if idx == 0:
                hdr = _BRACKET_FOCUS_RE.sub(r"focus=\1,\2", ln.rstrip())
                hdr = re.sub(r"\s+face_top-bottom=[0-9.\-]+", "", hdr)
                sec_lines.append(hdr)
                continue
            stripped = ln.lstrip()
            if not stripped or stripped.startswith(skip_prefixes):
                continue
            ln = _SPEECH_GAPS_VERBOSE_RE.sub(r"\1speech_gaps:", ln)
            ln = _EMPHASIS_VERBOSE_RE.sub(r"\1emphasis:", ln)
            if stripped.startswith("emphasis:"):
                beats = _EMPH_ITEM_RE.findall(stripped)
                if beats:
                    ln = f"  beats: {' '.join(beats[:3])}"
            ln = _UNIFORM_SAFE_ZOOM_RE.sub(r"safe_focus_x(1.05..1.10)=\1", ln)
            ln = _BRACKET_FOCUS_RE.sub(r"focus=\1,\2", ln)
            sec_lines.append(ln.rstrip())
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
        vad_sec: Optional[float] = None
        words_count = 0
        for ln in lines[1:]:
            s = ln.lstrip()
            if s.startswith("speech (voice detector"):
                spans = re.findall(r"(\d+(?:\.\d+)?)-(\d+(?:\.\d+)?)", s)
                if spans:
                    vad_sec = sum(max(0.0, float(b) - float(a)) for a, b in spans)
            elif not s.startswith(("!", "pauses:", "voiced (", "emphasis", "faces ", "speech gaps", "speech_gaps:")):
                words_count += len(re.findall(r"(?:^|\s)\d+\.\d+\s+\S+", ln))
        info = {
            "duration": float(dur_m.group(1)) if dur_m else None,
            "dims": dim_m.group(1) if dim_m else None,
            "silent": silent,
            "vad_sec": vad_sec,
            "words_count": words_count,
        }
        index[name] = info
        index.setdefault(Path(name).stem, info)
    return index


def _count_words_in_cache(cache_dir: Path) -> Optional[int]:
    for fname in ("hq_words.json", "align.json", "words.json"):
        data = _read_json_safe(cache_dir / fname)
        if isinstance(data, dict) and isinstance(data.get("words"), list):
            return len(data["words"])
        if isinstance(data, list):
            return len(data)
    return None


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
    facts_idx = _facts_clip_index(texts.get("facts.md", ""))

    caches_raw = _read_json_safe(project / "clip_caches.json")
    for display_name, cache_name, meta in _parse_clip_caches(caches_raw):
        cache_dir = _resolve_cache_dir(project, cache_name)
        info = _read_json_safe(cache_dir / "info.json") if cache_dir else None
        info = info if isinstance(info, dict) else {}
        if "." not in display_name and info.get("path"):
            display_name = Path(str(info["path"])).name
        fact_info = facts_idx.get(display_name) or facts_idx.get(Path(display_name).stem) or {}

        w = info.get("width") or meta.get("width")
        h = info.get("height") or meta.get("height")
        dims = f"{int(w)}x{int(h)}" if (w and h) else (fact_info.get("dims") or "?x?")

        dur_val = (
            info.get("duration")
            if info.get("duration") is not None
            else (meta.get("duration") if meta.get("duration") is not None else fact_info.get("duration"))
        )
        try:
            dur_str = f"{float(dur_val):.2f}s" if dur_val is not None else "?s"
        except (TypeError, ValueError):
            dur_str = "?s"

        active_sec: Optional[float] = None
        if cache_dir:
            sdata = _read_json_safe(cache_dir / "speech_spans.json")
            spans = sdata.get("spans") if isinstance(sdata, dict) else sdata
            if isinstance(spans, list):
                try:
                    active_sec = sum(
                        max(0.0, float(pair[1]) - float(pair[0]))
                        for pair in spans
                        if isinstance(pair, (list, tuple)) and len(pair) >= 2
                    )
                except (TypeError, ValueError):
                    active_sec = None
        if active_sec is None and meta.get("active_speech") is not None:
            try:
                active_sec = float(meta["active_speech"])
            except (TypeError, ValueError):
                active_sec = None
        if active_sec is None:
            active_sec = fact_info.get("vad_sec")

        words_count = _count_words_in_cache(cache_dir) if cache_dir else None
        if words_count is None and meta.get("words_count") is not None:
            try:
                words_count = int(meta["words_count"])
            except (TypeError, ValueError):
                words_count = None
        if words_count is None:
            if cache_dir and active_sec == 0.0:
                words_count = 0
            else:
                words_count = int(fact_info.get("words_count") or 0)

        if active_sec is not None and active_sec > 0.0:
            speech_col = f"speech={active_sec:.1f}s"
        elif fact_info.get("silent") or active_sec == 0.0 or info.get("has_audio") is False or words_count == 0:
            speech_col = "SILENT"
        else:
            speech_col = "speech=unknown"

        lines_out.append(f"- `{display_name}`: {dims}, {dur_str}, {speech_col}, {words_count}w")

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

    if not lines_out:
        return ""
    return "## Footage Summary\n" + "\n".join(lines_out)


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
    sections.append("## Measured Footage (`facts.md`)\n" + compact_facts(texts["facts.md"]))

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
    parser = argparse.ArgumentParser(description="Build Director Packet or inspect a single script line")
    parser.add_argument("project", type=Path, help="Path to work/<project> directory")
    parser.add_argument("--line", help="Lookup candidates and measured clip details for a single line ID (e.g. L12)")
    args = parser.parse_args()
    try:
        if args.line:
            print(line_detail(args.project, args.line))
        else:
            out_path = write(args.project)
            print(f"[PACKET] {out_path}")
            parts = packet_parts(args.project)
            if len(parts) > 1:
                print(f"[PACKET_PARTS] {' '.join(p.name for p in parts)}")
    except ValueError as exc:
        parser.error(str(exc))


if __name__ == "__main__":
    main()
