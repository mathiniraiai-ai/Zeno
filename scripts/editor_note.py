#!/usr/bin/env python3
"""Post one line to the desktop app's Live feed while the agent edits, so the editor can follow each step.

    python3 scripts/editor_note.py <work folder name> "Placing the cuts for L2 to L15" [--kind stage|cut|info]
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import events  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("project")
    ap.add_argument("text")
    ap.add_argument("--kind", default="stage", choices=[k for k in events.KINDS])
    a = ap.parse_args()
    events.set_project(a.project)
    events.emit(a.kind, a.text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
