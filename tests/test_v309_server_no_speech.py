#!/usr/bin/env python3
"""A valid zero-word Gemini answer (silent clip) is a 200 no-speech result, not a 502 retry loop."""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
for p in (str(ROOT / "service"), str(ROOT / "scripts"), str(ROOT / "tests")):
    if p not in sys.path:
        sys.path.insert(0, p)

from fastapi import HTTPException  # noqa: E402
import test_v303_fastapi_tracker as t303  # noqa: E402


class _Resp:
    def __init__(self, text):
        self.text = text
        self.usage_metadata = None


def _client_factory(behaviour):
    class _Models:
        def generate_content(self, model, contents, config):
            return behaviour(model)

    class _Client:
        def __init__(self, *a, **k):
            self.models = _Models()

    return _Client


class NoSpeechTests(unittest.TestCase):
    def setUp(self):
        self.svc = t303._make_test_service(tempfile.mkdtemp(prefix="zeno_nospeech_"))
        self.g = self.svc._Gemini()

    def _run(self, behaviour):
        from google import genai
        with patch.object(self.svc, "_gemini_keys", return_value=["k1"]), \
             patch.object(genai, "Client", _client_factory(behaviour)), \
             patch("time.sleep", lambda *_: None):
            return self.g.transcribe(b"RIFF", "p", 3.0, trace=[], speech_seconds=1.1)

    def test_all_models_empty_returns_no_speech(self):
        r = self._run(lambda m: _Resp(json.dumps({"words": [], "full_transcript": ""})))
        self.assertTrue(r["no_speech"])
        self.assertEqual(r["words"], [])
        self.assertEqual(r["full_transcript"], "")
        self.assertFalse(r["incomplete"])
        self.assertEqual(r["warning"], "no speech detected")

    def test_empty_beats_quota_from_other_models(self):
        seen = []

        def b(model):
            seen.append(model)
            if len(seen) == 1:
                return _Resp(json.dumps({"words": []}))
            raise RuntimeError("429 RESOURCE_EXHAUSTED quota")
        r = self._run(b)
        self.assertTrue(r["no_speech"])

    def test_real_words_unchanged(self):
        r = self._run(lambda m: _Resp(json.dumps({"words": ["hello", "there", "friend"], "full_transcript": "hello there friend"})))
        self.assertFalse(r.get("no_speech"))
        self.assertEqual(r["words"], ["hello", "there", "friend"])
        self.assertFalse(r["incomplete"])

    def test_all_transient_still_503(self):
        def b(model):
            raise RuntimeError("503 UNAVAILABLE overloaded")
        with self.assertRaises(HTTPException) as cm:
            self._run(b)
        self.assertEqual(cm.exception.status_code, 503)


if __name__ == "__main__":
    unittest.main()
