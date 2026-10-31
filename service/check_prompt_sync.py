#!/usr/bin/env python3
"""Fail if service/prompt.py differs from the repo's own prompt. Run from the repo root: python service/check_prompt_sync.py"""
import sys
from pathlib import Path

root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(root / "scripts"))
sys.path.insert(0, str(root / "service"))
from align_engine.gemini_transcriber import build_transcribe_prompt as repo_prompt  # noqa: E402
from prompt import build_transcribe_prompt as service_prompt  # noqa: E402

cases = [(None, "auto"), (["Woman: hello there", "നിനക്ക് ഇഷ്ടപ്പെട്ടോ?"], "ml"), (["a"] * 50, "ta")]
bad = [c for c in cases if repo_prompt(c[0], language_hint=c[1]) != service_prompt(c[0], language_hint=c[1])]
print("OK: service prompt matches the repo prompt" if not bad else f"MISMATCH in {len(bad)} case(s): copy the function again")
sys.exit(1 if bad else 0)
