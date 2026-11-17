"""Small, lossless-in-scope editing input; detailed measurements remain on disk.

No editorial decisions or model calls. Whole-packet mode retains every spoken word,
candidate, warning and framing note; only duplicate speech interval lists are omitted.
Line mode retrieves candidate source sections without reopening the whole job.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import time
import wave

CHUNK_MAX_BYTES = 36000
CHUNK_MAX_LINES = 620
_CHUNK_TRIGGER_BYTES = 38000
_CHUNK_TRIGGER_LINES = 650
_AUDIO_EXTS = {".wav", ".mp3", ".m4a", ".aac", ".flac"}


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8") if path.is_file() else ""


def compact_facts(text: str) -> str:
    redundant = ("speech (voice detector", "voiced (real sound") + (("pauses:",) if "speech gaps" in text else ())
    return "\n".join(line for line in text.splitlines()
                     if not line.lstrip().startswith(redundant)).strip()


def refresh_line_map(project: Path, mapping: str) -> None:
    """Do not apply exceptions to different dialogue after parser/script changes."""
    path = project / "line_map.md"
    previous = read(path)
    headers = lambda text: re.findall(r"(?m)^L\d+\b.*$", text)
    if previous and headers(previous) != headers(mapping):
        history = project / "_history"
        history.mkdir(exist_ok=True)
        stamp = time.time_ns()
        for name in ("decisions.txt", "script_analysis.md"):
            old = project / name
            if old.exists():
                old.replace(history / f"before-script-change-{stamp}-{name}")
        print("[WARNING] Script line IDs/text changed; prior decisions and analysis were archived. Review exceptions afresh.")
    path.write_text(mapping, encoding="utf-8")


def line_detail(project: Path, line_id: str) -> str:
    if not re.fullmatch(r"L\d+", line_id):
        raise ValueError("Use a script line ID such as L12, without a cut suffix.")
    mapping = read(project / "line_map.md")
    sections = re.split(r"(?m)(?=^L\d+\b)", mapping)
    selected = next((s for s in sections if re.match(rf"^{line_id}\b", s)), "")
    if not selected:
        raise ValueError(f"No {line_id} in line_map.md")
    sources = re.findall(r"(?m)^  CAND (.+?) \d+(?:\.\d+)?-\d", selected)
    facts = read(project / "facts.md")
    details = [s for s in re.split(r"(?m)(?=^## )", facts)
               if any(s.startswith("## " + src + " (") for src in sources)]
    return selected.strip() + "\n\n" + "\n".join(details)


def _utf8_len(text: str) -> int:
    return len(text.encode("utf-8"))


def _read_json_safe(path: Path) -> object:
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


def _part_sort_key(path: Path) -> tuple[int, str]:
    m = re.fullmatch(r"editing_packet_p(\d+)\.md", path.name)
    return (int(m.group(1)), path.name) if m else (10**9, path.name)


def _extract_src_dir(plan_text: str, project: Path) -> Path | None:
    for line in plan_text.splitlines():
        m = re.match(r"^\s*#\s*src_dir\b\s*:?\s*(.+)$", line, re.I)
        if not m:
            continue
        raw_val = m.group(1).split("|")[0].strip()
        if len(raw_val) >= 2 and raw_val[0] == raw_val[-1] and raw_val[0] in ('"', "'"):
            raw_val = raw_val[1:-1].strip()
        if not raw_val:
            return None
        cand = Path(raw_val).expanduser()
        if cand.is_dir():
            return cand
        if not cand.is_absolute():
            for base in (project.parent.parent, Path(__file__).resolve().parent.parent):
                rel = base / cand
                if rel.is_dir():
                    return rel
        return cand
    return None


def _resolve_cache_dir(project: Path, cache_name: str) -> Path | None:
    if not cache_name:
        return None
    cand = Path(cache_name)
    candidates = [cand] if cand.is_absolute() else [
        project.parent / "_clips" / cache_name,
        project.parent / cache_name,
        project / cache_name,
        Path(__file__).resolve().parent.parent / "work" / "_clips" / cache_name,
    ]
    for d in candidates:
        if d.is_dir():
            return d
    return None


def _parse_clip_caches(raw: object) -> list[tuple[str, str, dict]]:
    """Return deduplicated [(display_name, cache_name, meta_dict), ...] from clip_caches.json."""
    if isinstance(raw, dict) and isinstance(raw.get("clips"), (dict, list)):
        target = raw["clips"]
    else:
        target = raw

    entries: list[tuple[str, str, dict]] = []
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

    dedup: dict[str, tuple[str, str, dict]] = {}
    for dname, cname, meta in entries:
        key = cname or dname
        if not key:
            continue
        if key not in dedup:
            dedup[key] = (dname or Path(cname).name, cname, dict(meta))
        else:
            prev_name, prev_cname, prev_meta = dedup[key]
            # Prefer filenames with an extension (e.g. "S1.mp4" over "S1").
            better_name = dname if ("." in dname and "." not in prev_name) else prev_name
            merged_meta = {**prev_meta, **meta}
            dedup[key] = (better_name, prev_cname or cname, merged_meta)
    return list(dedup.values())


def _facts_clip_index(facts_text: str) -> dict[str, dict]:
    index: dict[str, dict] = {}
    for sec in re.split(r"(?m)(?=^## )", facts_text):
        lines = sec.splitlines()
        if not lines or not lines[0].startswith("## "):
            continue
        m = re.match(r"^##\s+(.+?)\s*\(([^)]*)\)\s*$", lines[0])
        if not m:
            continue
        name = m.group(1).strip()
        tags = m.group(2)
        dur_m = re.search(r"\b(\d+(?:\.\d+)?)s\b", tags)
        dim_m = re.search(r"\b(\d+x\d+)\b", tags)
        silent = bool(re.search(r"\bSILENT\b", tags)) or any(
            "! SILENT / AMBIENT CLIP" in ln for ln in lines[1:]
        )
        vad_sec: float | None = None
        words_count = 0
        for ln in lines[1:]:
            s = ln.lstrip()
            if s.startswith("speech (voice detector"):
                spans = re.findall(r"(\d+(?:\.\d+)?)-(\d+(?:\.\d+)?)", s)
                if spans:
                    vad_sec = sum(max(0.0, float(b) - float(a)) for a, b in spans)
            elif not s.startswith(("!", "pauses:", "voiced (", "emphasis:", "faces ", "speech gaps")):
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


def _count_words_in_cache(cache_dir: Path) -> int | None:
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


def _unmatched_line_ids(line_map_text: str) -> list[str]:
    missing: list[str] = []
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


def _raw_footage_inventory(project: Path, texts: dict) -> str:
    """Compact per-clip inventory, #src_dir audio scan, and unmatched line summary."""
    lines_out: list[str] = []
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

        dur_val = info.get("duration") if info.get("duration") is not None else (
            meta.get("duration") if meta.get("duration") is not None else fact_info.get("duration")
        )
        try:
            dur_str = f"{float(dur_val):.2f}s" if dur_val is not None else "?s"
        except (TypeError, ValueError):
            dur_str = "?s"

        active_sec: float | None = None
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
            speech_col = f"active_speech={active_sec:.1f}s"
        elif fact_info.get("silent") or active_sec == 0.0 or info.get("has_audio") is False or words_count == 0:
            speech_col = "SILENT/zero words" if words_count == 0 else "SILENT/0.0s active"
        else:
            speech_col = "active_speech=unknown"

        lines_out.append(f"- {display_name} | {dims} | {dur_str} | {speech_col} | {words_count}w")

    src_dir = _extract_src_dir(texts.get("plan.edl", ""), project)
    if src_dir is not None:
        if src_dir.is_dir():
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
                formatted = ", ".join(_format_audio_entry(p) for p in audio_files)
                lines_out.append(f"- Separate audio in `#src_dir` ({src_dir}): {formatted}")
            else:
                lines_out.append(f"- Separate audio in `#src_dir` ({src_dir}): none")
        else:
            lines_out.append(f"- Separate audio in `#src_dir` ({src_dir}): directory not accessible locally")

    missing_lines = _unmatched_line_ids(texts.get("line_map.md", ""))
    if missing_lines:
        lines_out.append(f"- Unmatched/missing lines (0 candidates): {', '.join(missing_lines)}")
    elif lines_out:
        lines_out.append("- Unmatched/missing lines (0 candidates): none")

    if not lines_out:
        return ""
    return "## Raw Footage Inventory & Diagnostics\n" + "\n".join(lines_out)


def _chunk_header(index: int, total: int) -> str:
    return f"[PACKET PART {index}/{total} — read all parts in parallel]\n\n"


def _chunk_packet(packet: str) -> list[str]:
    """Split packet into <= CHUNK_MAX_BYTES / <= CHUNK_MAX_LINES parts at line boundaries."""
    raw_lines = packet.splitlines()
    if _utf8_len(packet) <= _CHUNK_TRIGGER_BYTES and len(raw_lines) <= _CHUNK_TRIGGER_LINES:
        return [packet]

    reserve_bytes = 96
    reserve_lines = 2
    max_body_bytes = max(1024, CHUNK_MAX_BYTES - reserve_bytes)
    max_body_lines = max(10, CHUNK_MAX_LINES - reserve_lines)
    line_bytes = [_utf8_len(ln) + 1 for ln in raw_lines]

    def _split_ranges(body_bytes_limit: int, body_lines_limit: int) -> list[tuple[int, int]]:
        ranges: list[tuple[int, int]] = []
        start = 0
        n = len(raw_lines)
        while start < n:
            cur_bytes = 0
            end = start
            while end < n and (end - start) < body_lines_limit:
                nxt = cur_bytes + line_bytes[end]
                if end > start and nxt > body_bytes_limit:
                    break
                cur_bytes = nxt
                end += 1
                if cur_bytes > body_bytes_limit:
                    break

            if end >= n:
                ranges.append((start, n))
                break

            min_bytes_section = int(cur_bytes * 0.40)
            min_lines_section = max(1, int((end - start) * 0.40))
            min_bytes_soft = int(cur_bytes * 0.60)
            min_lines_soft = max(1, int((end - start) * 0.60))

            acc = [0] * (end - start + 1)
            for idx in range(end - start):
                acc[idx + 1] = acc[idx] + line_bytes[start + idx]

            chosen = end
            for k in range(end - 1, start, -1):
                offset = k - start
                if acc[offset] < min_bytes_section and offset < min_lines_section:
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
                else:
                    for k in range(end - 1, start, -1):
                        offset = k - start + 1
                        if acc[offset] < min_bytes_soft and offset < min_lines_soft:
                            break
                        if not raw_lines[k].strip() and k + 1 < end:
                            chosen = k + 1
                            break

            ranges.append((start, chosen))
            start = chosen
        return ranges

    ranges = _split_ranges(max_body_bytes, max_body_lines)
    total = len(ranges)
    max_hdr_bytes = max(_utf8_len(_chunk_header(i + 1, total)) for i in range(total))
    if max_hdr_bytes > reserve_bytes:
        ranges = _split_ranges(max(1024, CHUNK_MAX_BYTES - max_hdr_bytes), max_body_lines)
        total = len(ranges)

    chunks: list[str] = []
    for idx, (s, e) in enumerate(ranges, 1):
        body = "\n".join(raw_lines[s:e]).rstrip("\n") + "\n"
        chunks.append(_chunk_header(idx, total) + body)
    return chunks


def packet_parts(project: Path) -> list[Path]:
    """Return existing packet part Paths from editing_packet.json, sorted chunk files, or editing_packet.md."""
    manifest = _read_json_safe(project / "editing_packet.json")
    if isinstance(manifest, dict) and isinstance(manifest.get("parts"), list):
        resolved = [project / str(name) for name in manifest["parts"] if str(name).strip()]
        existing = [p for p in resolved if p.is_file()]
        if existing and len(existing) == len(resolved):
            return existing
    chunks = sorted((p for p in project.glob("editing_packet_p*.md") if p.is_file()), key=_part_sort_key)
    if chunks:
        return chunks
    return [project / "editing_packet.md"]


def build(project: Path) -> tuple[str, dict]:
    names = ("brief.md", "line_map.md", "facts.md", "plan.edl", "decisions.txt")
    texts = {name: read(project / name) for name in names}
    for name in names[:4]:
        if not texts[name]:
            raise ValueError(f"Missing {name}; finish --inspect first.")
    sheets = sorted(p.name for p in project.glob("clip_sheet_*.jpg"))
    sheets_label = ", ".join(sheets) if sheets else "none (review source frames before approving framing)"
    playbook = (
        "# Batch Playbook\n"
        f"1. Read all listed packet part(s) + ALL listed `clip_sheet_*.jpg` in ONE parallel batch (clip sheets: {sheets_label}).\n"
        "2. Write `script_analysis.md`, `plan.edl` (+ `decisions.txt` if needed) in ONE parallel batch; "
        "choose takes from script, words, and pictures (candidate rank is not speaker identity).\n"
        "3. Run ONE combined check+render command: `<PY> scripts/zeno.py --execute <plan> --square --compact` "
        "(runs `--check-plan` + 9:16 & 1:1 render + `verify.py` + `review_sheet.jpg`; only run standalone `--check-plan` if a cut is flagged) "
        "and end your turn immediately to wait on async wakeup.\n"
        "[DO NOT] NEVER poll progress/stdin; NEVER read `.py`, `REFERENCE.md`, `CONCEPT.md`, `facts.md`, "
        "`line_map.md`, or `clip_caches.json`; NEVER run `dir`, `ffprobe`, or `wave.open` "
        "(every spoken word, candidate, warning, and raw footage check is retained here; "
        "use `<PY> scripts/edit_packet.py <project> --line L12` only when a single line needs extra takes)."
    )
    parts = ["# Zeno editing packet", playbook]
    inventory = _raw_footage_inventory(project, texts)
    if inventory:
        parts.append(inventory)
    parts.extend([
        texts["brief.md"],
        "## Current plan (headers are owned by Zeno)\n" + texts["plan.edl"],
        texts["line_map.md"],
        "## Measured footage\n" + compact_facts(texts["facts.md"]),
    ])
    if texts["decisions.txt"].strip():
        parts.append("## Recorded exceptions—review their scope\n" + texts["decisions.txt"])
    parts.append(
        "## Required picture review\n"
        + ("\n".join(sheets) or "MISSING: review source frames before approving framing.")
    )
    packet = "\n\n".join(parts).strip() + "\n"
    chunks = _chunk_packet(packet)
    part_names = (
        [f"editing_packet_p{i}.md" for i in range(1, len(chunks) + 1)]
        if len(chunks) > 1
        else ["editing_packet.md"]
    )
    manifest = {
        "schema": 1,
        "sources": {name: hashlib.sha256(text.encode()).hexdigest() for name, text in texts.items()},
        "clip_sheets": sheets,
        "parts": part_names,
        "source_bytes": sum(len(t.encode()) for t in texts.values()),
        "packet_bytes": len(packet.encode()),
        "tokens": "not measured; bytes are not tokens",
    }
    return packet, manifest


def write(project: Path) -> Path:
    packet, manifest = build(project)
    chunks = _chunk_packet(packet)
    for stale in project.glob("editing_packet_p*.md*"):
        if re.fullmatch(r"editing_packet_p\d+\.md(?:\.tmp)?", stale.name):
            stale.unlink(missing_ok=True)
    path = project / "editing_packet.md"
    _atomic_write(path, packet)
    if len(chunks) > 1:
        for idx, chunk_text in enumerate(chunks, 1):
            _atomic_write(project / f"editing_packet_p{idx}.md", chunk_text)
    _atomic_write(project / "editing_packet.json", json.dumps(manifest, indent=2))
    return path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("project", type=Path)
    parser.add_argument("--line")
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
