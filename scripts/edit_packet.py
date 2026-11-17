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


def build(project: Path) -> tuple[str, dict]:
    names = ("brief.md", "line_map.md", "facts.md", "plan.edl", "decisions.txt")
    texts = {name: read(project / name) for name in names}
    for name in names[:4]:
        if not texts[name]:
            raise ValueError(f"Missing {name}; finish --inspect first.")
    parts = ["# Zeno editing packet",
             "Read this packet and ALL listed clip sheets once. Do not reread its source files by default. "
             "Every word, candidate and warning is retained. Duplicate voice interval lists remain in facts.md. "
             "For a specific issue: <PY> scripts/edit_packet.py <project> --line L12. "
             "Choose takes from script, words and pictures; candidate rank is not speaker identity.",
             texts["brief.md"], "## Current plan (headers are owned by Zeno)\n" + texts["plan.edl"],
             texts["line_map.md"], "## Measured footage\n" + compact_facts(texts["facts.md"])]
    if texts["decisions.txt"].strip():
        parts.append("## Recorded exceptions—review their scope\n" + texts["decisions.txt"])
    sheets = sorted(p.name for p in project.glob("clip_sheet_*.jpg"))
    parts.append("## Required picture review\n" + ("\n".join(sheets) or
                 "MISSING: review source frames before approving framing."))
    packet = "\n\n".join(parts).strip() + "\n"
    manifest = {"schema": 1, "sources": {name: hashlib.sha256(text.encode()).hexdigest()
                                         for name, text in texts.items()}, "clip_sheets": sheets,
                "source_bytes": sum(len(t.encode()) for t in texts.values()),
                "packet_bytes": len(packet.encode()), "tokens": "not measured; bytes are not tokens"}
    return packet, manifest


def write(project: Path) -> Path:
    packet, manifest = build(project)
    path = project / "editing_packet.md"
    for target, text in ((path, packet), (project / "editing_packet.json", json.dumps(manifest, indent=2))):
        temp = target.with_suffix(target.suffix + ".tmp")
        temp.write_text(text, encoding="utf-8")
        temp.replace(target)
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
            print(f"[PACKET] {write(args.project)}")
    except ValueError as exc:
        parser.error(str(exc))


if __name__ == "__main__":
    main()
