#!/usr/bin/env python3
"""Offline regression suite for Indic fonts/tofu QC, VAD-chunked transcription, Gemini cooldowns, and --retry-incomplete."""
from __future__ import annotations

import json
import os
import struct
import sys
import tempfile
import types
import unittest
import wave
from pathlib import Path
from unittest import mock

import numpy as np
from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = ROOT / "scripts"
SERVICE_DIR = ROOT / "service"
for p in (str(SCRIPTS_DIR), str(SERVICE_DIR)):
    if p not in sys.path:
        sys.path.insert(0, p)


def _write_silence_wav(path: Path, dur_sec: float = 2.0) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    n = int(round(dur_sec * 16000))
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(16000)
        wf.writeframes(b"\x00\x00" * max(1, n))


class Track1IndicFontsAndTofuQCTests(unittest.TestCase):
    """Verify Indic-before-Latin font priority, Nirmala UI fallbacks, ui_generator coverage, and tofu QC."""

    def test_js_font_stacks_prefer_indic_and_nirmala_before_montserrat(self) -> None:
        cap_js = (ROOT / "subtitles" / "caption-renderer.js").read_text(encoding="utf-8")
        fc_js = (ROOT / "subtitles" / "render_flashcard.js").read_text(encoding="utf-8")
        page_js = (ROOT / "subtitles" / "render_page.js").read_text(encoding="utf-8")

        for fname, src in (
            ("caption-renderer.js", cap_js),
            ("render_flashcard.js", fc_js),
            ("render_page.js", page_js),
        ):
            self.assertIn("Nirmala UI", src, f"{fname} missing Nirmala UI fallback")
            self.assertIn("Noto Sans Kannada", src, f"{fname} missing Noto Sans Kannada")

        # Verify Flashcard buildFontSpec puts indicFams ahead of Montserrat when Indic text is rendered
        m_idx = fc_js.find("if (hasIndic)")
        self.assertGreater(m_idx, 0)
        snippet = fc_js[m_idx : m_idx + 520]
        self.assertIn("indicOrdered", snippet)
        self.assertIn("'Nirmala UI'", snippet)
        self.assertNotIn("['Montserrat', ...indicFams", fc_js)

    def test_ui_generator_9_scripts_and_nirmala_cache_bump(self) -> None:
        import ui_generator

        for script_key in (
            "devanagari", "kannada", "telugu", "tamil",
            "malayalam", "bengali", "gujarati", "gurmukhi", "odia",
        ):
            cands = ui_generator._SCRIPT_FONT_CANDIDATES.get(script_key, [])
            joined = " | ".join(cands)
            self.assertIn("Nirmala.ttc", joined, f"{script_key} missing Nirmala.ttc fallback")
            self.assertIn("Nirmala.ttf", joined, f"{script_key} missing Nirmala.ttf fallback")
            self.assertGreaterEqual(len(cands), 4, f"{script_key} fallback chain too short")
        self.assertIn("v3_indic_nirmala", ui_generator._CACHE_VERSION)

    def test_tofu_box_detector_true_positive_and_zero_false_positives(self) -> None:
        import subtitles

        with tempfile.TemporaryDirectory() as td:
            td_p = Path(td)
            # 1) Synthetic hollow tofu boxes (.notdef): 4 sharp horizontal rectangles
            tofu_png = td_p / "tofu.png"
            img = Image.new("RGBA", (320, 90), (0, 0, 0, 0))
            draw = ImageDraw.Draw(img)
            for k in range(4):
                x0 = 30 + k * 40
                draw.rectangle([x0, 22, x0 + 24, 62], outline=(255, 255, 255, 255), width=2)
            img.save(tofu_png)
            det_tofu = subtitles.detect_tofu_boxes_in_png(tofu_png)
            self.assertGreaterEqual(det_tofu.get("tofu_count", 0), 3, det_tofu)

            # 2) Clean UI rounded pill + solid circular glyphs should NOT trigger false-positive tofu
            clean_png = td_p / "clean.png"
            img2 = Image.new("RGBA", (320, 90), (0, 0, 0, 0))
            draw2 = ImageDraw.Draw(img2)
            for k in range(4):
                x0 = 30 + k * 42
                draw2.ellipse([x0, 22, x0 + 26, 54], outline=(255, 255, 255, 255), width=3)
            img2.save(clean_png)
            det_clean = subtitles.detect_tofu_boxes_in_png(clean_png)
            self.assertEqual(det_clean.get("tofu_count", 0), 0, det_clean)

    def test_live_node_kannada_and_indic_render_if_available(self) -> None:
        import subtitles

        if not subtitles.available():
            self.skipTest("node-canvas not installed")
        ok, detail = subtitles.probe_text_render("ನನ್ನನ್ನು ನಂಬು please wait", style_id=4)
        self.assertTrue(ok, detail)

    def test_sfnt_variable_font_helper_skips_fvar(self) -> None:
        import subtitles

        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "var_test.ttf"
            # Minimal SFNT header with 1 table tagged 'fvar'
            hdr = b"\x00\x01\x00\x00" + struct.pack(">HHHH", 1, 16, 0, 0)
            rec = b"fvar" + struct.pack(">III", 0, 28, 4)
            p.write_bytes(hdr + rec + b"\x00\x00\x00\x00")
            is_var, _full = subtitles._sfnt_is_variable_and_full_name(p)
            self.assertTrue(is_var)


class Track2VadChunkingAndAlignmentTests(unittest.TestCase):
    """Verify Silero VAD silence chunking, uncovered gap calculation, anchored alignment, and clip_is_silent."""

    def test_plan_speech_chunks_splits_long_multi_pause_clips_and_midpoints(self) -> None:
        from align_engine import vad

        spans = [(0.5, 8.0), (10.5, 19.0), (22.0, 32.0), (35.0, 48.0)]
        chunks = vad.plan_speech_chunks(spans, total_sec=50.0, target_chunk_sec=16.0, max_chunk_sec=22.0)
        self.assertGreaterEqual(len(chunks), 3, chunks)
        for i, (cs, ce) in enumerate(chunks):
            self.assertLess(cs, ce)
            if i > 0:
                self.assertLessEqual(chunks[i - 1][1], cs + 1e-6, f"Chunks overlap across midpoint: {chunks}")

        # Short clip <= max_chunk_sec stays 1 single request
        short_chunks = vad.plan_speech_chunks([(0.4, 8.5)], total_sec=10.0)
        self.assertEqual(len(short_chunks), 1)

    def test_uncovered_speech_spans_flags_untranscribed_gaps(self) -> None:
        from align_engine import vad

        spans = [(0.4, 9.2), (11.6, 40.0), (42.0, 55.0)]
        words = [{"w": "hello", "start": 0.5, "end": 1.2}, {"w": "there", "start": 1.3, "end": 8.9}]
        gaps = vad.uncovered_speech_spans(spans, words, min_gap_sec=1.2)
        self.assertEqual(len(gaps), 2, gaps)
        self.assertGreaterEqual(gaps[0][0], 11.5)
        self.assertGreaterEqual(gaps[1][1] - gaps[1][0], 12.0)

    def test_clip_is_silent_never_silences_active_vad_speech(self) -> None:
        import prepare

        with tempfile.TemporaryDirectory() as td:
            wdir = Path(td)
            (wdir / "speech_spans.json").write_text(json.dumps({"spans": [[1.0, 12.0], [14.0, 28.0]]}), encoding="utf-8")
            clip = {"duration": 30.0, "has_audio": True, "work_dir": str(wdir), "words": []}
            self.assertFalse(prepare.clip_is_silent(clip))

    def test_common_transcript_digest_ignores_backoff_only_comments_and_non_prompt_edits(self) -> None:
        import common

        src1 = common._extract_prompt_source(SCRIPTS_DIR / "align_engine" / "gemini_transcriber.py")
        self.assertIn("build_transcribe_prompt", src1)
        self.assertIn("normalize_lang_code", src1)
        self.assertNotIn("GeminiRateLimiter", src1)

    def test_long_clip_triggers_vad_chunking_and_align_anchored_cues(self) -> None:
        import align_engine

        with tempfile.TemporaryDirectory() as td:
            wdir = Path(td)
            wav_p = wdir / "audio16k.wav"
            _write_silence_wav(wav_p, 26.0)

            calls = []

            def _fake_transcribe(wav_path, language_hint="auto", target_script_lines=None, work_dir=None, use_cache=True, speech_seconds=None):
                calls.append((Path(wav_path).name, speech_seconds))
                # Always cover audio inside slice so gap recovery isn't needed
                return {
                    "status": "success",
                    "detected_language_code": "kn",
                    "model_used": "gemini-test",
                    "full_transcript": "ಒಂದು ಎರಡು",
                    "cues": [{"start": 0.2, "end": 4.0, "text": "ಒಂದು ಎರಡು"}],
                    "words": [],
                }

            fake_transcriber = types.SimpleNamespace(transcribe=_fake_transcribe)
            fake_pipe = mock.Mock()
            fake_pipe.onset_verifier.compute_energy_envelope.return_value = (np.zeros(10), np.zeros(10))
            fake_pipe.viterbi.blank_id = 0
            fake_pipe.align_anchored_cues.return_value = {
                "status": "success",
                "duration_sec": 26.0,
                "words": [
                    {"word": "ಒಂದು", "start": 0.6, "end": 1.2, "confidence": 0.95},
                    {"word": "ಎರಡು", "start": 18.2, "end": 19.0, "confidence": 0.95},
                ],
                "cues": [],
            }

            with (
                mock.patch.object(align_engine.vad, "available", return_value=True),
                mock.patch.object(align_engine.vad, "speech_spans", return_value=[(0.4, 9.0), (17.5, 25.0)]),
                mock.patch.object(align_engine, "get_shared_transcriber", return_value=fake_transcriber),
                mock.patch.object(align_engine, "get_shared_pipeline", return_value=fake_pipe),
                mock.patch.object(align_engine, "_is_low_voiced_energy", return_value=False),
            ):
                res = align_engine.transcribe_and_align_clip(str(wav_p), work_dir=wdir, lang="kn")

            self.assertGreaterEqual(len(calls), 2, f"Expected multi-chunk transcription calls: {calls}")
            self.assertEqual(res["transcript_meta"]["chunks_total"], 2)
            self.assertEqual(res["transcript_meta"]["chunks_done"], 2)
            fake_pipe.align_anchored_cues.assert_called_once()
            fake_pipe.align.assert_not_called()
            self.assertTrue((wdir / "transcript_meta.json").is_file())

    def test_loud_broll_sfx_clip_suppresses_script_echo_and_mixed_framing_flag(self) -> None:
        import align_engine

        with tempfile.TemporaryDirectory() as td:
            wdir = Path(td)
            wav_p = wdir / "broll_sfx.wav"
            _write_silence_wav(wav_p, 8.0)

            seen_script_lines = []

            def _fake_transcribe(wav_path, language_hint="auto", target_script_lines=None, work_dir=None, use_cache=True, speech_seconds=None):
                seen_script_lines.append(target_script_lines)
                return {"status": "success", "detected_language_code": "en", "model_used": "gemini-test", "full_transcript": "", "cues": [], "words": []}

            with (
                mock.patch.object(align_engine.vad, "available", return_value=True),
                mock.patch.object(align_engine.vad, "speech_spans", return_value=[(0.2, 0.5)]),
                mock.patch.object(align_engine, "get_shared_transcriber", return_value=types.SimpleNamespace(transcribe=_fake_transcribe)),
                mock.patch.object(align_engine, "_is_low_voiced_energy", return_value=False),
            ):
                align_engine.transcribe_and_align_clip(
                    str(wav_p),
                    work_dir=wdir,
                    lang="kn",
                    script_lines=["A man is running through glass"],
                )
            self.assertEqual(seen_script_lines, [None])


class Track3GeminiCooldownAndPromptSyncTests(unittest.TestCase):
    """Verify 400 thinking_budget memory, 404 multi-key skip, wait>25s fast-fail, daily Retry-After, and incomplete propagation."""

    def test_server_remember_400_thinking_budget_and_404_skips_remaining_keys(self) -> None:
        import app as service_app

        gem = service_app._Gemini()
        called = []

        class _FakeModels:
            def generate_content(self, model, contents, config):
                called.append((model, getattr(config, "thinking_config", None) is not None))
                if model == "m404":
                    raise RuntimeError("404 NOT_FOUND: model retired")
                if model == "m400" and getattr(config, "thinking_config", None) is not None:
                    raise RuntimeError("400 INVALID_ARGUMENT: thinking_budget=0 unsupported")
                return types.SimpleNamespace(
                    text=json.dumps({
                        "detected_language_code": "kn",
                        "detected_language_name": "Kannada",
                        "full_transcript": "hello world",
                        "cues": [{"start": 0.0, "end": 1.5, "text": "hello world"}],
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
            mock.patch.object(service_app, "_gemini_keys", return_value=["k1", "k2", "k3"]),
            mock.patch.object(gem, "candidate_models", return_value=["m404", "m400"]),
            mock.patch("time.sleep"),
        ):
            out1 = gem.transcribe(b"RIFF" + b"\x00" * 100, "prompt", 2.0)
            self.assertEqual(out1["model_used"], "m400")
            # m404 must be tried ONCE (not 3 keys!)
            self.assertEqual(sum(1 for m, _ in called if m == "m404"), 1, called)
            self.assertIn("m400", gem._no_thinking_budget_models)

            called.clear()
            out2 = gem.transcribe(b"RIFF" + b"\x00" * 100, "prompt", 2.0)
            self.assertEqual(out2["model_used"], "m400")
            # m400 must go straight to no-thinking config on second call!
            self.assertEqual(called, [("m400", False)], called)

    def test_cloud_daily_quota_fails_fast_and_incomplete_preserves_healthy_cache(self) -> None:
        import httpx
        from align_engine.gemini_transcriber import GeminiTranscriber

        with tempfile.TemporaryDirectory() as td:
            wdir = Path(td)
            wav_p = wdir / "clip.wav"
            _write_silence_wav(wav_p, 10.0)
            with mock.patch.dict(os.environ, {"ZENO_CLOUD_URL": "https://example.invalid", "ZENO_CLOUD_TOKEN": "tok"}):
                gt = GeminiTranscriber(api_keys=["local_key_1"])

            # Daily exhaustion from server -> NO 8-retry loop and NO local key double-dip!
            fake_resp = types.SimpleNamespace(
                status_code=429,
                headers={"Retry-After": "786", "X-Zeno-Cooldown-Seconds": "786", "X-Zeno-Daily-Exhausted": "1"},
                text='{"detail": "daily quota exhausted"}',
                json=lambda: {"detail": "daily quota exhausted"},
            )
            fake_client_inst = mock.Mock()
            fake_client_inst.post.return_value = fake_resp
            fake_client_cm = mock.MagicMock()
            fake_client_cm.__enter__.return_value = fake_client_inst
            fake_client_cm.__exit__.return_value = False
            with (
                mock.patch.object(httpx, "Client", return_value=fake_client_cm),
                mock.patch.object(gt, "_get_client", side_effect=AssertionError("must not fall back to local keys on daily exhaustion")),
            ):
                res = gt.transcribe(str(wav_p), work_dir=wdir, speech_seconds=8.0)
            self.assertEqual(res["status"], "quota_exceeded")
            self.assertEqual(fake_client_inst.post.call_count, 1)

            # Incomplete response must NOT overwrite existing healthy complete cache
            cache_f = wdir / "gemini_transcript.json"
            cache_f.write_text(
                json.dumps({"wav_size": wav_p.stat().st_size, "words": ["a", "b", "c", "d"], "incomplete": False, "warning": ""}),
                encoding="utf-8",
            )
            gt._save_cache(cache_f, wav_p, {"words": ["a"], "incomplete": True, "warning": "incomplete: 1 word"})
            kept = json.loads(cache_f.read_text(encoding="utf-8"))
            self.assertEqual(kept["words"], ["a", "b", "c", "d"])
            self.assertFalse(kept["incomplete"])


class Track4RetryDiagnoseCutVadAndRulesTests(unittest.TestCase):
    """Verify .claude Windows allowlist, edit_packet --diagnose/--clip, Cut VAD guard, and Zero-Yes/No rules."""

    def test_claude_settings_allows_windows_backslashes_while_denying_clips(self) -> None:
        cfg = json.loads((ROOT / ".claude" / "settings.json").read_text(encoding="utf-8"))
        allow = cfg["permissions"]["allow"]
        deny = cfg["permissions"]["deny"]
        self.assertTrue(any(r"scripts\zeno.py" in a for a in allow), allow)
        self.assertTrue(any(r"scripts\edit_packet.py" in a for a in allow), allow)
        self.assertIn("Read(work/_clips/**)", deny)

    def test_compact_facts_preserves_untranscribed_speech_and_diagnose_reports_gaps(self) -> None:
        import edit_packet

        raw_facts = "\n".join([
            "## clip1.mp4 1280x720 45.00s focus=0.5,0.2",
            "  speech (voice detector): 0.40-4.00s 10.00-44.00s",
            "  pauses: 4.00-10.00s",
            "  0.50 Hello (kn)",
            "  1.10 there (kn)",
        ])
        compacted = edit_packet.compact_facts(raw_facts)
        self.assertIn("untranscribed_speech:", compacted)
        self.assertIn("--retry-incomplete", compacted)

        with tempfile.TemporaryDirectory() as td:
            proj = Path(td) / "proj"
            proj.mkdir()
            cdir = proj / "_clip"
            cdir.mkdir()
            (proj / "plan.edl").write_text('C1 clip1.mp4 0.40-2.00 sp="A"\n', encoding="utf-8")
            (proj / "facts.md").write_text(raw_facts, encoding="utf-8")
            # Simulate transcript_meta in cache dir referenced by plan/facts
            c_hash_dir = proj / "clip_cache"
            c_hash_dir.mkdir()
            (c_hash_dir / "speech_spans.json").write_text(json.dumps({"spans": [[0.4, 4.0], [10.0, 44.0]]}), encoding="utf-8")
            (c_hash_dir / "words.json").write_text(json.dumps([{"w": "Hello", "start": 0.5, "end": 1.2}]), encoding="utf-8")
            (c_hash_dir / "transcript_meta.json").write_text(
                json.dumps({"status": "incomplete", "duration_sec": 45.0, "active_speech_sec": 37.6, "covered_speech_sec": 1.6, "speech_coverage": 0.04, "words_count": 1, "uncovered_spans": [[10.0, 44.0]], "chunks_total": 3, "chunks_done": 1}),
                encoding="utf-8",
            )
            (proj / "clip_caches.json").write_text(json.dumps({"clip1.mp4": str(c_hash_dir)}), encoding="utf-8")
            with mock.patch.object(edit_packet, "_resolve_cache_dir", return_value=c_hash_dir):
                out_diag = edit_packet.diagnose_project(proj)
                out_clip = edit_packet.clip_detail(proj, "clip1.mp4")
            self.assertIn("incomplete", out_diag.lower())
            self.assertIn("10.0-44.0s", out_diag)
            self.assertNotIn("work/_clips/", out_diag)
            self.assertIn("10.0", out_clip)

    def test_cut_problems_blocks_cutting_through_untranscribed_vad_speech(self) -> None:
        import script_order

        with tempfile.TemporaryDirectory() as td:
            wav_p = Path(td) / "a.wav"
            _write_silence_wav(wav_p, 10.0)
            seg_cut_mid_vad = {
                "id": "C1",
                "src": "video1.mp4",
                "start": 2.0,
                "end": 5.0,
                "speaker": "A",
            }
            plan = {"segments": [seg_cut_mid_vad], "cuts": [seg_cut_mid_vad]}
            with (
                mock.patch.object(script_order, "_words_of", return_value=[{"w": "Only", "start": 0.4, "end": 0.9}]),
                mock.patch.object(script_order, "_speech_spans_for", return_value=[(0.4, 8.0)]),
                mock.patch.object(script_order, "_env_of", return_value=[(t * 0.1, -20.0) for t in range(100)]),
            ):
                probs = script_order.cut_problems(plan)
            self.assertTrue(any("untranscribed" in p.lower() for p in probs), probs)

    def test_zeno_help_and_zero_yes_no_docs_synced(self) -> None:
        import service.check_prompt_sync as cps  # noqa: F401
        import subprocess as sp

        agents = (ROOT / "AGENTS.md").read_bytes()
        claude = (ROOT / "CLAUDE.md").read_bytes()
        self.assertEqual(agents, claude)
        txt = agents.decode("utf-8")
        self.assertIn("--retry-incomplete", txt)
        self.assertIn("--diagnose", txt)
        self.assertIn("Zero Yes/No", txt)
        help_out = sp.check_output([sys.executable, str(SCRIPTS_DIR / "zeno.py"), "--help"], text=True)
        self.assertIn("--retry-incomplete", help_out)
        self.assertIn("--clip", help_out)


if __name__ == "__main__":
    unittest.main()
