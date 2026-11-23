#!/usr/bin/env python3
"""Regression suite for Zeno v3.0.0 Meta omniASR-CTC-300M-v2 engine, dual-script bridging, and OTA cleanup."""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = ROOT / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))


class ZenoV3OmniAsrEngineTests(unittest.TestCase):
    """Verify legacy Wav2Vec2/uroman removal, bundled vocab, dual-script bridging, cache preservation, and OTA prune."""

    def test_zero_wav2vec2_mms_fa_and_uroman_in_scripts(self) -> None:
        self.assertFalse(
            (SCRIPTS_DIR / "align_engine" / "uroman").exists(),
            "Legacy Perl scripts/align_engine/uroman directory must be deleted in v3.0.0",
        )
        forbidden = (
            "WAV2VEC2_ASR_BASE_960H",
            "torchaudio.pipelines",
            "uroman.pl",
            "batch_romanize_words",
            "offline_wav2vec2",
        )
        for py_file in sorted(SCRIPTS_DIR.rglob("*.py")):
            src = py_file.read_text(encoding="utf-8")
            for needle in forbidden:
                self.assertNotIn(
                    needle,
                    src,
                    f"Forbidden v2 legacy symbol {needle!r} found in {py_file.relative_to(ROOT)}",
                )

    def test_offline_bundled_vocab_initializes_without_model_weights(self) -> None:
        from align_engine import acoustic

        with tempfile.TemporaryDirectory() as tmp:
            fake_root = Path(tmp) / "empty_zeno"
            fake_root.mkdir(parents=True)
            with (
                mock.patch.object(acoustic, "_ROOT", fake_root),
                mock.patch.dict(
                    "os.environ",
                    {"ZENO_OMNIASR_DIR": "", "ZENO_HF_CACHE": "", "HF_HOME": ""},
                    clear=False,
                ),
            ):
                model = acoustic.AcousticModel(device="cpu")
                self.assertEqual(model.sample_rate, 16000)
                self.assertEqual(model.blank_id, 0)
                self.assertEqual(len(model.dictionary), 10288)
                self.assertEqual(model.dictionary.get(" "), 4)
                self.assertIsNone(model.torch_model)

    def test_native_multilingual_tokenization_across_10_indic_and_latin_scripts(self) -> None:
        from align_engine.pipeline import ZenoAcousticPipeline

        pipe = ZenoAcousticPipeline(device="cpu")
        vocab = pipe.acoustic_model.dictionary
        samples = [
            "don't",
            "Nice!",
            "ಕನ್ನಡ\u200cದಲ್ಲಿ",
            "తెలుగులో",
            "தமிழில்",
            "മലയാളം",
            "हिन्दी।",
            "मराठी",
            "বাংলা",
            "ગુજરાતી",
            "ਪੰਜਾਬੀ",
            "ଓଡ଼ିଆ",
        ]
        for word in samples:
            toks = pipe._word_to_omniasr_tokens(word, word)
            self.assertGreater(len(toks), 0, f"Expected non-empty OmniASR tokens for {word!r}")
            self.assertNotIn(0, toks, f"Blank token ID 0 must never appear in {word!r}")
            for Punct in ("!", ".", "।", "\u200c"):
                if Punct in vocab:
                    self.assertNotIn(vocab[Punct], toks)
        # Word-internal apostrophe in "don't" is preserved if in vocab, while trailing '!' is stripped
        if "'" in vocab:
            self.assertIn(vocab["'"], pipe._word_to_omniasr_tokens("don't", "don't"))
        self.assertEqual(
            pipe._word_to_omniasr_tokens("Nice!", "Nice"),
            pipe._word_to_omniasr_tokens("nice", "nice"),
        )

    def test_dual_script_greedy_ctc_bridge_rescues_code_switched_english_and_compounds(self) -> None:
        from align_engine.pipeline import ZenoAcousticPipeline

        pipe = ZenoAcousticPipeline(device="cpu")
        vocab = pipe.acoustic_model.dictionary
        V = len(vocab)
        space_id = vocab[" "]

        # Simulate a 160-frame (3.20s) Kannada clip where the speaker says:
        #   "Nice! ನಾನು job interview ಆಫೀಸ್"
        # and omniASR emits pure Kannada CTC tokens:
        #   "ನೈಸ್ ನಾನು ಜಾಬ್ ಇಂಟ್ರವಿ ಆಫೀಸ್" (with ~0.0001 prob on Latin n-i-c-e / j-o-b / i-n-t-e-r-v-i-e-w)
        spoken_native = [
            ("ನೈಸ್", 6, 22),
            ("ನಾನು", 28, 46),
            ("ಜಾಬ್", 54, 70),
            ("ಇಂಟ್ರವಿ", 78, 108),
            ("ಆಫೀಸ್", 116, 142),
        ]
        T = 160
        lp = np.full((T, V), -11.5, dtype=np.float32)
        lp[:, 0] = -0.002  # blank everywhere by default

        for word_txt, f0, f1 in spoken_native:
            w_tids = pipe._word_to_omniasr_tokens(word_txt, word_txt)
            self.assertTrue(w_tids)
            span = max(1, (f1 - f0) // len(w_tids))
            cur = f0
            for tid in w_tids:
                end_f = min(f1, cur + span)
                lp[cur:end_f, :] = -11.5
                lp[cur:end_f, tid] = -0.02  # ~0.98 prob on native Kannada token
                cur = end_f
            if f1 + 2 < T:
                lp[f1 : f1 + 2, :] = -11.5
                lp[f1 : f1 + 2, space_id] = -0.05

        sr = 16000
        wf = np.zeros(int(T * 0.02 * sr), dtype=np.float32)
        t_axis = np.arange(len(wf), dtype=np.float32) / sr
        for _, f0, f1 in spoken_native:
            mask = (t_axis >= f0 * 0.02) & (t_axis <= f1 * 0.02)
            wf[mask] = 0.2 * np.sin(2.0 * np.pi * 220.0 * t_axis[mask])

        input_mixed = ["Nice!", "ನಾನು", "job", "interview", "office"]
        with (
            mock.patch.object(pipe.acoustic_model, "_ensure_model_loaded", return_value=None),
            mock.patch.object(
                pipe.acoustic_model,
                "extract_emissions",
                return_value=(lp, wf, T * 0.02, 0.02),
            ),
        ):
            res = pipe.align(
                audio_path="dummy.wav",
                word_list=input_mixed,
                language_code="kn",
                verify_onsets=False,
            )

        self.assertEqual(res["status"], "success")
        words = res["words"]
        self.assertEqual([w["word"] for w in words], input_mixed)

        # Every bridged English word ("Nice!", "job", "interview", "office") must lock onto its Kannada acoustic frames
        expected_bounds = [
            (0.12, 0.44),  # "Nice!" -> "ನೈಸ್" (frames 6..22)
            (0.56, 0.92),  # "ನಾನು"  -> "ನಾನು" (frames 28..46)
            (1.08, 1.40),  # "job"   -> "ಜಾಬ್" (frames 54..70)
            (1.56, 2.16),  # "interview" -> "ಇಂಟ್ರವಿ" (frames 78..108)
            (2.32, 2.84),  # "office" -> "ಆಫೀಸ್" (frames 116..142)
        ]
        for w_out, (exp_s, exp_e) in zip(words, expected_bounds):
            self.assertAlmostEqual(w_out["start"], exp_s, delta=0.08, msg=w_out)
            self.assertAlmostEqual(w_out["end"], exp_e, delta=0.10, msg=w_out)
            self.assertGreaterEqual(w_out["confidence"], 0.85, msg=w_out)

    def test_cross_word_geminate_space_separator_inserted_between_identical_boundary_chars(self) -> None:
        from align_engine.pipeline import ZenoAcousticPipeline

        pipe = ZenoAcousticPipeline(device="cpu")
        vocab = pipe.acoustic_model.dictionary
        lp = np.full((60, len(vocab)), -10.0, dtype=np.float32)
        lp[:, 0] = -0.01
        words_info = [
            {"orig": "good", "clean": "good", "orig_idx": 0},
            {"orig": "day", "clean": "day", "orig_idx": 1},
        ]
        token_ids, meta = pipe._build_target_sequence_for_slice(words_info, lp, "en")
        # "good" ends with 'd' and "day" starts with 'd' -> space_id (4) sits strictly between meta[0].tok_end and meta[1].tok_start
        self.assertEqual(meta[0]["tok_end"] + 1, meta[1]["tok_start"])
        self.assertEqual(token_ids[meta[0]["tok_end"]], vocab[" "])

    def test_align_digest_bump_keeps_gemini_transcript_and_invalidates_only_words(self) -> None:
        import common

        with tempfile.TemporaryDirectory() as tmp:
            wdir = Path(tmp) / "clip_cache"
            wdir.mkdir(parents=True)
            now_ver = common.engine_versions()
            old_ver = dict(now_ver)
            old_ver["align"] = "legacy_v2111_wav2vec2_digest"

            (wdir / "engine_version.json").write_text(json.dumps(old_ver), encoding="utf-8")
            (wdir / "gemini_transcript.json").write_text('{"text":"ನಮಸ್ಕಾರ","words":["ನಮಸ್ಕಾರ"]}', encoding="utf-8")
            (wdir / "words.json").write_text('[{"w":"ನಮಸ್ಕಾರ","start":0.1,"end":0.5}]', encoding="utf-8")
            (wdir / "hq_words.json").write_text('[{"w":"ನಮಸ್ಕಾರ","start":0.1,"end":0.5}]', encoding="utf-8")

            dropped = common.ensure_cache_current(wdir)
            self.assertIn("words.json", dropped)
            self.assertIn("hq_words.json", dropped)
            self.assertNotIn("gemini_transcript.json", dropped)
            self.assertTrue((wdir / "gemini_transcript.json").is_file())
            self.assertFalse((wdir / "words.json").exists())
            self.assertFalse((wdir / "hq_words.json").exists())

    def test_ota_v2111_to_v300_prunes_uroman_and_wav2vec2_pth_while_preserving_clips_and_yunet(self) -> None:
        import auto_updater
        import release

        with tempfile.TemporaryDirectory(prefix="zeno_v2_to_v3_") as tmp:
            root = Path(tmp) / "Zeno"
            (root / "scripts" / "align_engine" / "uroman").mkdir(parents=True)
            (root / "scripts" / "align_engine" / "uroman" / "uroman.pl").write_text("#!/usr/bin/perl\n", encoding="utf-8")
            (root / "models" / "torch" / "hub" / "checkpoints").mkdir(parents=True)
            legacy_pth = root / "models" / "torch" / "hub" / "checkpoints" / "wav2vec2_fairseq_base_ls960_asr_ls960.pth"
            legacy_pth.write_bytes(b"legacy-360mb-wav2vec2-weights")
            yunet = root / "models" / "face_detection_yunet_2023mar.onnx"
            yunet.write_bytes(b"yunet-onnx-bytes")
            gem_cache = root / "work" / "_clips" / "S1_hash" / "gemini_transcript.json"
            gem_cache.parent.mkdir(parents=True)
            gem_cache.write_text('{"text":"keep me"}', encoding="utf-8")
            (root / "VERSION").write_text("2.1.11\n", encoding="utf-8")
            (root / ".env").write_text("ZENO_TOKEN=keep\n", encoding="utf-8")

            expected_ver = (ROOT / "VERSION").read_text(encoding="utf-8").strip()
            blob, _count, sha = release.build_bundle_bytes(version=expected_ver, clear_work_projects=False)
            with (
                mock.patch.object(Path, "home", return_value=Path(tmp) / "home"),
                mock.patch.object(auto_updater, "ROOT", root),
                mock.patch.object(auto_updater, "WORK", root / "work"),
                mock.patch.object(auto_updater, "RELEASE_STATE_FILE", root / "work" / ".cloud_release_state.json"),
                mock.patch.object(auto_updater.subprocess, "run"),
            ):
                res = auto_updater.apply_bundle_bytes(blob, expected_sha256=sha, version=expected_ver, sha="v300sha")

            self.assertTrue(res["ok"], res)
            self.assertFalse((root / "scripts" / "align_engine" / "uroman").exists())
            self.assertFalse(legacy_pth.exists())
            self.assertTrue((root / "scripts" / "align_engine" / "data" / "omniasr_vocab.json").is_file())
            self.assertTrue(yunet.is_file())
            self.assertTrue(gem_cache.is_file())
            self.assertEqual((root / "VERSION").read_text(encoding="utf-8").strip(), expected_ver)

    def test_protect_tails_ignores_silent_vad_padding_but_still_rescues_active_rms_voice(self) -> None:
        import script_order

        # 1. Clean cut already sitting in silence (-52 dBFS) while Silero VAD pad extends +220 ms past word end & -110 ms before start
        plan_clean = {
            "segments": [
                {"id": "L1", "src": "clean.mp4", "start": 0.95, "end": 2.08, "speaker": "A"},
            ]
        }
        ws_clean = [{"w": "hello", "start": 1.00, "end": 2.00}]
        env_clean = [
            (round(k * 0.01, 2), -20.0 if (100 <= k <= 201) else -52.0)
            for k in range(320)
        ]
        with (
            mock.patch.object(script_order, "_words_of", return_value=ws_clean),
            mock.patch.object(script_order, "_env_of", return_value=env_clean),
            mock.patch.object(script_order, "_speech_spans_for", return_value=[(0.88, 2.25)]),
        ):
            moved = script_order.protect_tails(plan_clean)
        self.assertEqual(moved, 0)
        self.assertEqual(plan_clean["segments"][0]["start"], 0.95)
        self.assertEqual(plan_clean["segments"][0]["end"], 2.08)

        # 2. Cut where real voice (-24 dBFS) continues 180 ms past word end -> MUST rescue through 2.18 + 45 ms cushion
        plan_active = {
            "segments": [
                {"id": "L2", "src": "active.mp4", "start": 0.95, "end": 2.04, "speaker": "A"},
            ]
        }
        env_active = [
            (round(k * 0.01, 2), -24.0 if (100 <= k <= 218) else -52.0)
            for k in range(320)
        ]
        with (
            mock.patch.object(script_order, "_words_of", return_value=ws_clean),
            mock.patch.object(script_order, "_env_of", return_value=env_active),
            mock.patch.object(script_order, "_speech_spans_for", return_value=[(0.90, 2.40)]),
        ):
            moved_a = script_order.protect_tails(plan_active)
        self.assertEqual(moved_a, 1)
        self.assertGreaterEqual(plan_active["segments"][0]["end"], 2.21)
        self.assertLessEqual(plan_active["segments"][0]["end"], 2.26)

    def test_tighten_joins_trims_loose_dead_air_preserves_marks_peer_and_cues(self) -> None:
        import script_order

        # 3 segments:
        #   L1 (A): voice 1.00-2.00, cut 0.78-2.32 (220ms hook lead, 320ms dead tail) -> same speaker as L2
        #   L2 (A): voice 1.00-2.00, cut 0.80-2.30 (200ms head, 300ms tail + peer + sfx@0.50 + zoom@0.60) -> turn change to L3
        #   L3 (B): voice 1.00-2.00, cut 0.80-2.55 with mark:wrong at 1.25/0.48 (near end -> tail MUST stay untouched!)
        plan = {
            "segments": [
                {"id": "L1", "src": "a.mp4", "start": 0.78, "end": 2.32, "speaker": "A"},
                {
                    "id": "L2",
                    "src": "a2.mp4",
                    "start": 0.80,
                    "end": 2.30,
                    "speaker": "A",
                    "peer": {"src": "p.mp4", "start": 4.00, "end": 5.50, "side": "left"},
                    "sfx": [{"file": "pop.wav", "at": 0.50}],
                    "zoom": "sudden_in:1.08@0.60",
                },
                {
                    "id": "L3",
                    "src": "b.mp4",
                    "start": 0.80,
                    "end": 2.55,
                    "speaker": "B",
                    "marks": [{"type": "wrong", "at": 1.25, "duration": 0.48}],
                },
            ]
        }
        ws = [{"w": "word", "start": 1.00, "end": 2.00}]
        env = [(round(k * 0.01, 2), -20.0 if (100 <= k <= 200) else -52.0) for k in range(300)]
        with (
            mock.patch.object(script_order, "_words_of", return_value=ws),
            mock.patch.object(script_order, "_env_of", return_value=env),
        ):
            changed = script_order.tighten_joins(plan)
        self.assertGreaterEqual(changed, 2)
        s1, s2, s3 = plan["segments"]
        # Hook lead trimmed near ~50ms
        self.assertGreaterEqual(s1["start"], 0.93)
        self.assertLessEqual(s1["start"], 0.965)
        # Same-speaker join L1 -> L2: audible silence (s1.end - 2.0) + (1.0 - s2.start) - 0.016 <= ~110ms, never slicing speech
        gap_12 = (s1["end"] - 2.00) + (1.00 - s2["start"]) - 0.016
        self.assertGreaterEqual(s1["end"], 2.045)
        self.assertLessEqual(s2["start"], 0.965)
        self.assertLessEqual(gap_12, 0.115)
        # L2 peer, sfx, and zoom@t shifted cleanly by delta_start = s2["start"] - 0.80
        delta_s2 = round(s2["start"] - 0.80, 3)
        self.assertGreater(delta_s2, 0.10)
        self.assertAlmostEqual(s2["peer"]["start"], 4.00 + delta_s2, places=2)
        self.assertAlmostEqual(s2["sfx"][0]["at"], 0.50 - delta_s2, places=2)
        self.assertIn(f"@{0.60 - delta_s2:.2f}", s2["zoom"])
        # Turn-change join L2 -> L3 <= ~150ms
        gap_23 = (s2["end"] - 2.00) + (1.00 - s3["start"]) - 0.016
        self.assertLessEqual(gap_23, 0.155)
        # L3 mark tail untouched (mark ends at 1.25+0.48=1.73s near end of 1.75s segment!)
        self.assertEqual(s3["end"], 2.55)

    def test_verify_measure_pcm_join_gaps_measures_exact_audible_silence(self) -> None:
        import array
        import math
        import verify

        sr = 16000
        # 2.0s PCM: tone 0.00-0.94s, silence 0.94-1.06s (120 ms gap across join at 1.00s), tone 1.06-2.00s
        n = 2 * sr
        pcm = array.array("h", [0] * n)
        for k in range(n):
            t = k / float(sr)
            if t < 0.94 or t >= 1.06:
                pcm[k] = int(round(12000.0 * math.sin(2.0 * math.pi * 300.0 * t)))
        segs = [
            {"id": "L1", "start": 0.0, "end": 1.0},
            {"id": "L2", "start": 0.0, "end": 1.0},
        ]
        stats = verify._measure_pcm_join_gaps(pcm, segs, [0.0, 1.0], [], sample_rate=sr)
        self.assertEqual(stats["count"], 1)
        self.assertAlmostEqual(stats["median_ms"], 120, delta=20)


if __name__ == "__main__":
    unittest.main()
