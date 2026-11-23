#!/usr/bin/env python3
"""Regression suite for mid-sentence / mid-word cut prevention across align_engine, script_order, verify, and edit_packet."""
from __future__ import annotations

import json
import sys
import types
import unittest
from pathlib import Path
from unittest import mock

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = ROOT / "scripts"
SERVICE_DIR = ROOT / "service"
for p in (str(SCRIPTS_DIR), str(SERVICE_DIR)):
    if p not in sys.path:
        sys.path.insert(0, p)


class MidSentenceAlignmentFixesTests(unittest.TestCase):
    """Verify Wav2Vec2 word-boundary '|', IndicPhoneticMapper, geminate closure bridging, and cue-window expansion."""

    def test_indic_phonetic_mapper_preserves_inherent_a_and_collapses_geminates(self) -> None:
        from align_engine.phonetics.indic_phonemes import IndicPhoneticMapper

        def _map(word: str, lang: str) -> str:
            return IndicPhoneticMapper.map_to_acoustic_tokens(word, lang=lang, is_native_indic=True, orig_word=word)

        # Inherent 'a' before anusvara / tippi must NOT be deleted
        self.assertEqual(_map("മലയാളം", "ml"), "MALAYALAM")
        self.assertEqual(_map("പാഠം", "ml"), "PATAM")
        self.assertEqual(_map("ಬಂದಿದ್ದೇನೆ", "kn"), "BANDIDENE")
        self.assertEqual(_map("ਪੰਜਾਬੀ", "pa"), "PANJABE")

        # Indo-Aryan word-final vowel nasalization omits phantom bilabial 'M'
        self.assertEqual(_map("हैं", "hi"), "HAI")
        self.assertEqual(_map("नहीं", "hi"), "NAHE")

        # Geminate plosive closures collapse across all Indic languages so 1 burst != 2 CTC tokens
        self.assertEqual(_map("ಇಪ್ಪತ್ತು", "kn"), "IPATU")
        self.assertNotIn("PP", _map("ഇപ്പോൾ", "ml"))
        self.assertNotIn("TT", _map("പറ്റില്ല", "ml"))

    def test_viterbi_word_separator_and_short_slice_raises_instead_of_005_pileup(self) -> None:
        from align_engine.viterbi import ViterbiAligner, _numpy_vectorized_viterbi

        aligner = ViterbiAligner(blank_id=0)
        # 6 frames cannot hold 12 tokens: both _numpy_vectorized_viterbi and _align_core must raise ValueError
        tiny_lp = np.full((6, 29), -10.0, dtype=np.float32)
        tiny_lp[:, 0] = -0.01
        tok_ids = [2, 3, 4, 1, 5, 6, 7, 1, 8, 9, 10, 1]
        meta = [
            {"word": "one", "num_chars": 3, "tok_start": 0, "tok_end": 3},
            {"word": "two", "num_chars": 3, "tok_start": 4, "tok_end": 7},
            {"word": "six", "num_chars": 3, "tok_start": 8, "tok_end": 11},
        ]
        with self.assertRaises(ValueError):
            _numpy_vectorized_viterbi(tiny_lp, tok_ids, blank_id=0)
        with self.assertRaises(ValueError):
            aligner._align_core(tiny_lp, tok_ids, meta, 0.02)

        # Valid 30-frame emission with '|' (id=1) separators maps word bounds strictly to [tok_start:tok_end]
        lp = np.full((30, 29), -8.0, dtype=np.float32)
        lp[:, 0] = -2.0
        for f in range(2, 6):
            lp[f, 2] = -0.05
        for f in range(6, 9):
            lp[f, 3] = -0.05
        for f in range(9, 14):
            lp[f, 1] = -0.02  # '|' word separator absorbs post-word spike
        for f in range(15, 20):
            lp[f, 4] = -0.05
        for f in range(20, 24):
            lp[f, 5] = -0.05
        for f in range(24, 28):
            lp[f, 1] = -0.02
        res = aligner.align(
            lp,
            [2, 3, 1, 4, 5, 1],
            [
                {"word": "hi", "num_chars": 2, "tok_start": 0, "tok_end": 2},
                {"word": "go", "num_chars": 2, "tok_start": 3, "tok_end": 5},
            ],
            0.02,
        )
        self.assertEqual(len(res), 2)
        self.assertLessEqual(res[0]["end"], 0.22)
        self.assertGreaterEqual(res[1]["start"], 0.28)

    def test_onset_verifier_bridges_150ms_geminate_closure_and_preserves_final_syllable(self) -> None:
        from align_engine.onset_verifier import AcousticOnsetVerifier

        ov = AcousticOnsetVerifier(sample_rate=16000)
        # 2.0s waveform: syllable 1 at 0.30-0.55s, 140ms geminate stop closure 0.55-0.69s, syllable 2 at 0.69-0.96s
        sr = 16000
        t = np.arange(int(2.0 * sr), dtype=np.float32) / sr
        wf = np.random.RandomState(0).normal(0.0, 1e-4, size=t.shape).astype(np.float32)
        wf[(t >= 0.30) & (t <= 0.55)] = 0.25 * np.sin(2 * np.pi * 220 * t[(t >= 0.30) & (t <= 0.55)])
        wf[(t >= 0.69) & (t <= 0.96)] = 0.20 * np.sin(2 * np.pi * 200 * t[(t >= 0.69) & (t <= 0.96)])

        # 140ms closure must NOT be flagged as an inter-word silent_run (min_run_sec=0.28)
        s_runs = ov.silent_runs(wf)
        self.assertFalse(any(0.50 <= a and b <= 0.75 for a, b in s_runs), s_runs)

        # Word whose Viterbi end stopped before the geminate closure (0.54s) extends across the closure to ~0.96s!
        words = [{"word": "ഇപ്പോൾ", "start": 0.31, "end": 0.54, "confidence": 0.9}]
        ext = ov.extend_ends_to_energy(words, wf)
        clamped = ov.clamp_words_to_sound(ext, wf, extra_runs=s_runs)
        self.assertGreaterEqual(clamped[0]["end"], 0.92, clamped)
        self.assertFalse(clamped[0].get("no_sound", False))


class MidSentenceCutAndCandFixesTests(unittest.TestCase):
    """Verify CAND full-sentence preservation, outward snap_cuts, dense-speech protect_tails, and QC catches."""

    def test_word_similarity_rejects_shared_3char_indic_verb_suffixes(self) -> None:
        import prepare

        # Different Telugu & Malayalam verbs sharing only inflectional endings must NOT match >= 0.64
        self.assertLess(prepare._word_similarity("మెరుగవుతుంది", "చేయండి"), 0.64)
        self.assertLess(prepare._word_similarity("అయ్యాను", "చూశాను"), 0.64)
        self.assertLess(prepare._word_similarity("പറയുന്നു", "ചെയ്യുന്നു"), 0.64)
        self.assertLess(prepare._word_similarity("ಮಾట్లాಡಿ", "ಮಾತ್ರವೇ"), 0.64)

    def test_cand_preserves_repeated_closing_clause_and_contractions(self) -> None:
        import script_order

        # Sentence where closing 4 words repeat words from the first half (L23 pattern) + contraction at L12
        l1_txt = "I'm Rahul. I'm from Hyderabad."
        l2_txt = "అక్కడే మీరు మాట్లాడి practice చేయలేదు కాబట్టి మాట్లాడి మాత్రమే practice చేస్తారు."
        lines = [
            {
                "id": "L1",
                "speaker": "Rahul",
                "text": l1_txt,
                "tokens": script_order.clean(l1_txt.split()),
                "beat": False,
            },
            {
                "id": "L2",
                "speaker": "Robot",
                "text": l2_txt,
                "tokens": script_order.clean(l2_txt.split()),
                "beat": False,
            },
        ]
        rahul_ws = [
            {"w": "I", "start": 1.00, "end": 1.12},
            {"w": "am", "start": 1.13, "end": 1.28},
            {"w": "Rahul.", "start": 1.30, "end": 1.75},
            {"w": "I", "start": 1.85, "end": 1.95},
            {"w": "am", "start": 1.96, "end": 2.10},
            {"w": "from", "start": 2.12, "end": 2.35},
            {"w": "Hyderabad.", "start": 2.36, "end": 3.05},
        ]
        robot_ws = [
            {"w": "అక్కడే", "start": 10.00, "end": 10.45},
            {"w": "మీరు", "start": 10.46, "end": 10.80},
            {"w": "మాట్లాడి", "start": 10.82, "end": 11.30},
            {"w": "practice", "start": 11.32, "end": 11.80},
            {"w": "చేయలేదు", "start": 11.82, "end": 12.40},
            {"w": "కాబట్టి", "start": 12.45, "end": 12.95},
            {"w": "మాట్లాడి", "start": 13.00, "end": 13.45},
            {"w": "మాత్రమే", "start": 13.48, "end": 13.95},
            {"w": "practice", "start": 13.98, "end": 14.45},
            {"w": "చేస్తారు.", "start": 14.48, "end": 15.20},
        ]
        clips = [
            {"src": "Rahul.mp4", "hq_words": rahul_ws, "rms_env": [(t * 0.1, -20.0) for t in range(40)], "speech_spans": [(0.98, 3.08)]},
            {"src": "Robot.mp4", "hq_words": robot_ws, "rms_env": [(t * 0.1, -20.0) for t in range(160)], "speech_spans": [(9.98, 15.24)]},
        ]
        lmap = script_order.build_line_map(lines, clips)
        # L1 must start <= 1.00 (including "I am") and end >= 3.05
        self.assertIn("CAND Rahul.mp4 0.98-3.08", lmap, lmap)
        # L2 must NOT stop at 12.95/13.00 — must cover full sentence through 15.20+!
        self.assertTrue(
            any("CAND Robot.mp4 9.98-15.2" in ln or "CAND Robot.mp4 10.00-15.2" in ln for ln in lmap.splitlines()),
            lmap,
        )

    def test_snap_cuts_never_pulls_end_inward_or_pushes_start_past_word(self) -> None:
        import script_order

        plan = {
            "segments": [
                {"id": "L1", "src": "clip.mp4", "start": 1.05, "end": 3.08, "speaker": "A"},
            ]
        }
        ws = [
            {"w": "I", "start": 1.00, "end": 1.10},
            {"w": "agree", "start": 1.15, "end": 3.00},
        ]
        # Dip at 2.98s must NOT pull end earlier than 3.05s; start inside 'I' must snap BEFORE 1.00, never to 1.12!
        env = [(round(k * 0.01, 2), -50.0 if (k < 98 or k > 312 or k == 298) else -20.0) for k in range(400)]
        with (
            mock.patch.object(script_order, "_words_of", return_value=ws),
            mock.patch.object(script_order, "_env_of", return_value=env),
            mock.patch.object(script_order, "_speech_spans_for", return_value=[(0.98, 3.02)]),
        ):
            script_order.snap_cuts(plan)
        seg = plan["segments"][0]
        self.assertLessEqual(seg["start"], 0.97)
        self.assertGreaterEqual(seg["end"], 3.05)

    def test_protect_tails_extends_dense_speech_and_post_word_audio_end_keeps_tail(self) -> None:
        import script_order

        plan = {
            "segments": [
                {
                    "id": "L1",
                    "src": "dense.mp4",
                    "start": 0.95,
                    "end": 4.02,
                    "speaker": "Robot",
                    "marks": [{"type": "wrong", "at": 3.00, "duration": 0.48}],
                }
            ]
        }
        ws = [
            {"w": "Wrong", "start": 1.00, "end": 3.98},
            {"w": "ghost", "start": 4.05, "end": 4.20, "no_sound": True},
        ]
        # 98% active speech (-22 dBFS), with real spoken tail continuing up to 4.35s before dropping to -52 dBFS
        env = [(round(k * 0.01, 2), -22.0 if (95 <= k <= 435 or 10 <= k <= 90) else -52.0) for k in range(500)]
        with (
            mock.patch.object(script_order, "_words_of", return_value=ws),
            mock.patch.object(script_order, "_env_of", return_value=env),
            mock.patch.object(script_order, "_speech_spans_for", return_value=[(0.98, 4.36)]),
        ):
            kept = script_order.protect_tails(plan)
            self.assertEqual(kept, 1)
            self.assertGreaterEqual(plan["segments"][0]["end"], 4.36)

    def test_cut_problems_catches_at_word_edge_inside_untranscribed_vad_continuation(self) -> None:
        import script_order

        # Words transcribed up to 4.20s, cut at 4.23s (within +0.03s of w['end']), while VAD speech runs to 6.50s!
        plan = {"segments": [{"id": "L5", "src": "clip.mp4", "start": 0.95, "end": 4.23, "speaker": "A"}]}
        ws = [{"w": "partial", "start": 1.00, "end": 4.20}]
        env = [(round(k * 0.02, 2), -22.0) for k in range(350)]
        with (
            mock.patch.object(script_order, "_words_of", return_value=ws),
            mock.patch.object(script_order, "_speech_spans_for", return_value=[(1.00, 6.50)]),
            mock.patch.object(script_order, "_env_of", return_value=env),
        ):
            probs = script_order.cut_problems(plan)
        self.assertTrue(any("continuous vad speech" in p.lower() for p in probs), probs)

    def test_edit_packet_preserves_pauses_and_flash_lite_min_words_fails_over(self) -> None:
        import edit_packet
        import app as service_app

        raw_facts = "\n".join([
            "## Robot.mp4 1080x1920 20.00s focus=0.5,0.2",
            "  speech (voice detector): 0.20-4.10s 4.75-12.00s",
            "  pauses: 4.10-4.75s",
            "  0.25 Hello 0.80 world |4.05",
            "  4.80 Next 5.20 sentence |11.95",
        ])
        compacted = edit_packet.compact_facts(raw_facts)
        self.assertIn("pauses: 4.10-4.75", compacted)

        # Flash-lite returning 4 words on 10.0s of active speech (< 8 min_words) must fail over to 2.5-flash!
        gem = service_app._Gemini()
        models_tried = []

        class _FakeModels:
            def generate_content(self, model, contents, config):
                models_tried.append(model)
                txt = (
                    "one two three four"
                    if "lite" in model
                    else "one two three four five six seven eight nine ten"
                )
                return types.SimpleNamespace(
                    text=json.dumps({
                        "detected_language_code": "ml",
                        "detected_language_name": "Malayalam",
                        "full_transcript": txt,
                        "cues": [{"start": 0.0, "end": 9.8, "text": txt}],
                    }),
                    usage_metadata=None,
                )

        fake_genai = types.ModuleType("google.genai")
        fake_genai.Client = lambda api_key, http_options=None: types.SimpleNamespace(models=_FakeModels())
        fake_types = types.ModuleType("google.genai.types")
        fake_types.ThinkingConfig = lambda thinking_budget=0: types.SimpleNamespace(thinking_budget=thinking_budget)
        fake_types.GenerateContentConfig = lambda **kw: types.SimpleNamespace(**kw)
        fake_types.HttpOptions = lambda **kw: types.SimpleNamespace(**kw)
        fake_types.Part = types.SimpleNamespace(from_bytes=lambda data, mime_type: b"part")

        with (
            mock.patch.dict(sys.modules, {"google": types.ModuleType("google"), "google.genai": fake_genai, "google.genai.types": fake_types}),
            mock.patch.object(service_app, "_gemini_keys", return_value=["k1"]),
            mock.patch.object(gem, "candidate_models", return_value=["gemini-3.1-flash-lite", "gemini-2.5-flash"]),
            mock.patch("time.sleep"),
        ):
            res = gem.transcribe(b"RIFF" + b"\x00" * 100, "prompt", 12.0, speech_seconds=10.0)
        self.assertEqual(models_tried, ["gemini-3.1-flash-lite", "gemini-2.5-flash"])
        self.assertEqual(res["model_used"], "gemini-2.5-flash")
        self.assertEqual(len(res["words"]), 10)


if __name__ == "__main__":
    unittest.main()
