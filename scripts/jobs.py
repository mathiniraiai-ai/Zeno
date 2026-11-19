#!/usr/bin/env python3
"""Minimal backward-compatibility stub for legacy Zeno desktop callers."""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
WORK = ROOT / "work"


def list_jobs(limit: int = 40, user_email: str | None = None) -> list[dict[str, Any]]:
    return []

def detail(name: str = "", since: int = 0, user_email: str | None = None) -> dict[str, Any]:
    return {}

def zeno_info(check_updates: bool = True, force_check: bool = False) -> dict[str, Any]:
    return {}


def main(argv: list[str]) -> int:
    cmd = argv[1] if len(argv) > 1 else "list"
    if cmd == "list":
        out: Any = list_jobs()
    elif cmd in ("detail", "info"):
        out = detail() if cmd == "detail" else zeno_info()
    else:
        sys.stderr.write("Usage: jobs.py {list|detail|info}\n")
        return 2
    sys.stdout.write(json.dumps(out))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
