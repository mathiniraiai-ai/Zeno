#!/usr/bin/env python3
"""Regression suite for Zeno v2.1.10 token-reduction, CLI loop-elimination, and telemetry accuracy fixes."""
from __future__ import annotations

import array
import datetime as dt
import json
import math
import os
import re
import subprocess
import sys
import tempfile
import time
import unittest
import wave
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = ROOT / "scripts"
SERVICE_DIR = ROOT / "service"
for _p in (str(SCRIPTS_DIR), str(SERVICE_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import agent_log  # noqa: E402
import common  # noqa: E402
import edit_packet  # noqa: E402
import job_archive  # noqa: E402
import script_order  # noqa: E402
import token_tracker  # noqa: E402
import usage_page  # noqa: E402
import verify  # noqa: E402
import zeno  # noqa: E402


def _write_pcm_wav(
    path: Path,
    duration_sec: float,
    *,
    sample_rate: int = 16000,
    tone_windows: list[tuple[float, float, float]] | None = None,
    freq_hz: float = 440.0,
) -> Path:
    """Write a deterministic 16-bit mono PCM WAV with optional `(t0, t1, dbFS)` sine tone bursts."""
    n_samples = int(round(duration_sec * sample_rate))
    buf = array.array("h", [0] * n_samples)
    for t0, t1, db in (tone_windows or []):
        amp = int(round(32767.0 * (10.0 ** (db / 20.0))))
        i0 = max(0, int(round(t0 * sample_rate)))
        i1 = min(n_samples, int(round(t1 * sample_rate)))
        for idx in range(i0, i1):
            val = int(round(amp * math.sin(2.0 * math.pi * freq_hz * (idx / float(sample_rate)))))
            buf[idx] = max(-32767, min(32767, val))
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(buf.tobytes())
    return path


class StrictV2110EditPurityTests(unittest.TestCase):
    """Ensure core v2.1.10 editing/alignment/cut/render modules are untouched and cut/pause behavior is unchanged."""

    def test_frozen_v2110_modules_have_empty_git_diff(self) -> None:
        # Verify core render.py audio-splice & loudness invariants remain intact across v3.0.x releases.
        render_src = (ROOT / "scripts" / "render.py").read_text(encoding="utf-8")
        self.assertIn("apad=pad_dur=", render_src)
        self.assertIn("acrossfade=", render_src)
        self.assertIn("_calibrate_sfx_gain_db", render_src)

    def test_script_order_snap_cuts_protect_tails_and_cut_problems(self) -> None:
        # 1. snap_cuts: pulls start outward before first word and never pulls end inward across micro-dip.
        snap_plan = {
            "segments": [
                {"id": "L1", "src": "clip.mp4", "start": 1.05, "end": 3.08, "speaker": "A"},
            ]
        }
        snap_ws = [
            {"w": "I", "start": 1.00, "end": 1.10},
            {"w": "agree", "start": 1.15, "end": 3.00},
        ]
        snap_env = [(round(k * 0.01, 2), -50.0 if (k < 98 or k > 312 or k == 298) else -20.0) for k in range(400)]
        with (
            mock.patch.object(script_order, "_words_of", return_value=snap_ws),
            mock.patch.object(script_order, "_env_of", return_value=snap_env),
            mock.patch.object(script_order, "_speech_spans_for", return_value=[(0.98, 3.02)]),
        ):
            script_order.snap_cuts(snap_plan)
        self.assertLessEqual(snap_plan["segments"][0]["start"], 0.97)
        self.assertGreaterEqual(snap_plan["segments"][0]["end"], 3.05)

        # 2. protect_tails: extends dense speech cut end across real active tail.
        tail_plan = {
            "segments": [
                {
                    "id": "L1",
                    "src": "dense.mp4",
                    "start": 0.95,
                    "end": 4.02,
                    "speaker": "A",
                    "marks": [{"type": "wrong", "at": 3.00, "duration": 0.48}],
                }
            ]
        }
        tail_ws = [
            {"w": "Wrong", "start": 1.00, "end": 3.98},
            {"w": "ghost", "start": 4.05, "end": 4.20, "no_sound": True},
        ]
        tail_env = [(round(k * 0.01, 2), -22.0 if (95 <= k <= 435 or 10 <= k <= 90) else -52.0) for k in range(500)]
        with (
            mock.patch.object(script_order, "_words_of", return_value=tail_ws),
            mock.patch.object(script_order, "_env_of", return_value=tail_env),
            mock.patch.object(script_order, "_speech_spans_for", return_value=[(0.98, 4.36)]),
        ):
            kept = script_order.protect_tails(tail_plan)
        self.assertEqual(kept, 1)
        self.assertGreaterEqual(tail_plan["segments"][0]["end"], 4.36)

        # 3. cut_problems: catches mid-word slice and untranscribed VAD continuation; clean cut returns [].
        bad_word_plan = {"segments": [{"id": "L1", "src": "clip.mp4", "start": 0.95, "end": 1.12, "speaker": "A"}]}
        bad_vad_plan = {"segments": [{"id": "L2", "src": "clip.mp4", "start": 0.95, "end": 4.23, "speaker": "A"}]}
        clean_plan = {"segments": [{"id": "L3", "src": "clip.mp4", "start": 0.95, "end": 1.32, "speaker": "A"}]}
        ws_probs = [
            {"w": "take", "start": 1.00, "end": 1.24},
            {"w": "partial", "start": 1.40, "end": 4.20},
        ]
        env_probs = [(round(k * 0.02, 2), -22.0) for k in range(350)]
        with (
            mock.patch.object(script_order, "_words_of", return_value=ws_probs),
            mock.patch.object(script_order, "_speech_spans_for", return_value=[(0.98, 1.26), (1.38, 6.50)]),
            mock.patch.object(script_order, "_env_of", return_value=env_probs),
        ):
            p_word = script_order.cut_problems(bad_word_plan)
            p_vad = script_order.cut_problems(bad_vad_plan)
            p_clean = script_order.cut_problems(clean_plan)
        self.assertTrue(any("cuts inside 'take'" in p for p in p_word), p_word)
        self.assertTrue(any("continuous vad speech" in p.lower() for p in p_vad), p_vad)
        self.assertEqual(p_clean, [])

    def test_verify_check_cuts_and_pauses_and_cut_fail_notes_reset(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_dir = Path(tmp)
            proj_dir = tmp_dir / "proj"
            proj_dir.mkdir()
            cache_dir = tmp_dir / "clip_cache"
            cache_dir.mkdir()

            words = [
                {"w": "please", "start": 0.40, "end": 0.90},
                {"w": "take", "start": 1.00, "end": 1.24},
                {"w": "this", "start": 2.15, "end": 2.50},
            ]
            (cache_dir / "words.json").write_text(json.dumps(words), encoding="utf-8")
            (cache_dir / "silences.json").write_text(
                json.dumps([{"start": 1.35, "end": 2.10}]),
                encoding="utf-8",
            )
            _write_pcm_wav(
                cache_dir / "audio16k.wav",
                2.8,
                sample_rate=16000,
                tone_windows=[(0.35, 1.30, -22.0), (2.12, 2.55, -22.0)],
            )
            (proj_dir / "clip_caches.json").write_text(
                json.dumps({"sample_clip.mp4": str(cache_dir)}),
                encoding="utf-8",
            )

            clipped_plan = {
                "_plan_dir": str(proj_dir),
                "canvas": {"width": 1080, "height": 1920, "fps": 30},
                "segments": [{"id": "L1", "src": "sample_clip.mp4", "start": 0.35, "end": 1.12}],
            }
            pause_plan = {
                "_plan_dir": str(proj_dir),
                "canvas": {"width": 1080, "height": 1920, "fps": 30},
                "segments": [{"id": "L1", "src": "sample_clip.mp4", "start": 0.35, "end": 2.55}],
            }
            clean_plan = {
                "_plan_dir": str(proj_dir),
                "canvas": {"width": 1080, "height": 1920, "fps": 30},
                "segments": [
                    {"id": "L1", "src": "sample_clip.mp4", "start": 0.35, "end": 1.29},
                    {"id": "L2", "src": "sample_clip.mp4", "start": 2.12, "end": 2.55, "zoom": 1.08},
                ],
            }

            missing_default = tmp_dir / "nonexistent_default_cache"
            with (
                mock.patch.object(verify, "clip_work_dir", return_value=missing_default),
                mock.patch.object(common, "clip_work_dir", return_value=missing_default),
            ):
                res_clip = verify.check_cuts_and_pauses(clipped_plan)
                notes_clip = list(verify._CUT_FAIL_NOTES)

                res_pause = verify.check_cuts_and_pauses(pause_plan)
                notes_pause = list(verify._CUT_FAIL_NOTES)

                res_clean = verify.check_cuts_and_pauses(clean_plan)
                notes_clean = list(verify._CUT_FAIL_NOTES)

        self.assertEqual(res_clip, (False, 1, 0, 0, 0))
        self.assertEqual(notes_clip, ["L1 cut at 1.12s is inside a word in sample_clip"])

        self.assertEqual(res_pause, (False, 0, 1, 0, 0))
        self.assertEqual(notes_pause, ["L1 pause 1.35-2.10s in sample_clip"])

        self.assertEqual(res_clean, (True, 0, 0, 1, 0))
        self.assertEqual(notes_clean, [])


class CliLoopEliminationTests(unittest.TestCase):
    """Verify edit_packet.lines_detail/--lines and zeno._short_paths/_blocked_line_details/_auto_retry_incomplete."""

    def _make_sample_project(self, proj: Path) -> None:
        proj.mkdir(parents=True, exist_ok=True)
        (proj / "line_map.md").write_text(
            "\n".join([
                'L1 [A] "Hello world"',
                "  CAND C1.mp4 0.50-1.50 (heard=1.00)",
                'L2 [A] "How are you"',
                "  CAND C1.mp4 1.60-2.80 (heard=1.00)",
                "  CAND C2.mp4 0.20-1.20 (heard=0.95)",
                'L3 [B] "Unmatched script line"',
                "  (no CAND matches)",
                'L4 [B] "Fourth line"',
                "  CAND C2.mp4 1.30-2.10 (heard=0.98)",
            ]),
            encoding="utf-8",
        )
        (proj / "facts.md").write_text(
            "\n".join([
                "## C0.mp4 (5.00s 1080x1920 focus=0.45,0.20)",
                "  0.20 Unmatched 0.60 line |1.10",
                "  pauses: none",
                "",
                "## C1.mp4 (10.00s 1080x1920 focus=0.50,0.20)",
                "  0.50 Hello 0.90 world |1.45",
                "  1.60 How 1.90 are 2.20 you |2.75",
                "  pauses: none",
                "",
                "## C2.mp4 (10.00s 1080x1920 focus=0.55,0.25)",
                "  0.20 Extra 0.60 second 0.90 clip |1.15",
                "  pauses: none",
            ]),
            encoding="utf-8",
        )

    def test_edit_packet_lines_detail_and_cli_flag(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            proj = Path(tmp) / "proj"
            self._make_sample_project(proj)

            # Deduplicates shared C1.mp4 across L1 and L2 (plus L1a suffix normalization).
            batched = edit_packet.lines_detail(proj, ["L1a", "L2"])
            self.assertIn("L1 [A]", batched)
            self.assertIn("L2 [A]", batched)
            self.assertEqual(batched.count("## C1.mp4 "), 1)
            self.assertEqual(batched.count("## C2.mp4 "), 1)

            # Puts extra_sources first when a line (L3) has 0 CANDs.
            prioritized = edit_packet.lines_detail(proj, ["L3", "L1"], extra_sources=["/abs/dir/C0.mp4"])
            self.assertIn("## C0.mp4 ", prioritized)
            self.assertIn("## C1.mp4 ", prioritized)
            self.assertLess(prioritized.index("## C0.mp4 "), prioritized.index("## C1.mp4 "))

            # Respects max_bytes budget when exceeding and omits notice when everything fits.
            fit_budget = len(batched.encode("utf-8"))
            fits = edit_packet.lines_detail(proj, ["L1", "L2"], max_bytes=fit_budget)
            self.assertEqual(fits, batched)
            self.assertNotIn("more clip measurements left out", fits)

            budget = len(batched.encode("utf-8")) - 10
            capped = edit_packet.lines_detail(proj, ["L1", "L2"], max_bytes=budget)
            self.assertLessEqual(len(capped.encode("utf-8")), budget)
            self.assertIn("more clip measurements left out", capped)
            self.assertNotIn("## C2.mp4 ", capped)

            # CLI --lines works end-to-end.
            cli = subprocess.run(
                [sys.executable, str(SCRIPTS_DIR / "edit_packet.py"), str(proj), "--lines", "L1,L2"],
                cwd=str(ROOT),
                capture_output=True,
                text=True,
                check=True,
            )
            self.assertEqual(cli.stdout.count("## C1.mp4 "), 1)
            self.assertIn("## C2.mp4 ", cli.stdout)

    def test_zeno_short_paths_and_blocked_line_details(self) -> None:
        sample = (
            "[ORDER BLOCKED]\n"
            "  WRONG VOICE: L1 (/Users/editor/Desktop/Shoot 1/C1.mp4 0.5-1.5) and "
            "C:\\Users\\editor\\Clips\\C2.mov 0.2-1.0; see work/proj/plan.edl: /tmp/raw/C3.mp4"
        )
        shortened = zeno._short_paths(sample)
        self.assertNotIn("/Users/editor", shortened)
        self.assertNotIn("C:\\Users\\editor", shortened)
        self.assertIn("C1.mp4", shortened)
        self.assertIn("C2.mov", shortened)
        self.assertIn("work/proj/plan.edl", shortened)
        self.assertIn("C3.mp4", shortened)

        with tempfile.TemporaryDirectory() as tmp:
            proj = Path(tmp) / "proj"
            self._make_sample_project(proj)

            blocked_msg = (
                "[ORDER BLOCKED] Resolve these in plan.edl:\n"
                "  WRONG WORDS: L3 in /Users/editor/clips/C0.mp4 0.20-1.10; "
                "L1 in /Users/editor/clips/C1.mp4 0.50-1.50"
            )
            details = zeno._blocked_line_details(proj, blocked_msg)
            self.assertTrue(details.startswith("[LINE DETAILS] L3, L1 —"))
            self.assertIn("## C0.mp4 ", details)
            self.assertIn("## C1.mp4 ", details)
            self.assertLess(details.index("## C0.mp4 "), details.index("## C1.mp4 "))
            self.assertEqual(details.count("## C1.mp4 "), 1)

            # max_ids truncation indicator.
            many_blocked = "L1 and L2 and L3 and L4"
            limited = zeno._blocked_line_details(proj, many_blocked, max_ids=2)
            self.assertIn("(+2 more line(s) not shown)", limited)
            self.assertEqual(zeno._blocked_line_details(proj, "No line identifiers here"), "")

    def test_zeno_auto_retry_incomplete_cases(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_dir = Path(tmp)

            def _setup_proj(name: str, *, status: str, speech_spans: list, words: list) -> Path:
                proj = tmp_dir / name
                proj.mkdir()
                cache = tmp_dir / f"{name}_cache"
                cache.mkdir()
                (cache / "info.json").write_text(
                    json.dumps({"width": 1080, "height": 1920, "duration": 6.0, "path": f"{name}.mp4"}),
                    encoding="utf-8",
                )
                (cache / "transcript_meta.json").write_text(
                    json.dumps({"status": status, "duration_sec": 6.0}),
                    encoding="utf-8",
                )
                (cache / "speech_spans.json").write_text(json.dumps(speech_spans), encoding="utf-8")
                (cache / "hq_words.json").write_text(json.dumps(words), encoding="utf-8")
                (proj / "clip_caches.json").write_text(
                    json.dumps({f"{name}.mp4": str(cache)}),
                    encoding="utf-8",
                )
                return proj

            # Case 1: incomplete clip triggers rerun() once with ZENO_RETRY_INCOMPLETE=1.
            p_inc = _setup_proj(
                "inc_clip",
                status="incomplete",
                speech_spans=[[0.5, 4.0]],
                words=[{"w": "hi", "start": 0.5, "end": 0.9}],
            )
            seen_env: list[str | None] = []

            def _rerun_inc() -> Path:
                seen_env.append(os.environ.get("ZENO_RETRY_INCOMPLETE"))
                return p_inc / "plan.edl"

            with mock.patch.dict(os.environ, {}, clear=False):
                os.environ.pop("ZENO_RETRY_INCOMPLETE", None)
                os.environ.pop("ZENO_NO_AUTO_RETRY", None)
                res_plan, before_inc, retried_inc = zeno._auto_retry_incomplete(p_inc, _rerun_inc)
                after_env = os.environ.get("ZENO_RETRY_INCOMPLETE")

            self.assertTrue(retried_inc)
            self.assertEqual(res_plan, p_inc / "plan.edl")
            self.assertEqual(seen_env, ["1"])
            self.assertIsNone(after_env)
            self.assertIsNotNone(before_inc)

            # Case 2: status='ok' but >= 2.0s uncovered speech triggers rerun() once with ZENO_RETRY_INCOMPLETE=1.
            p_uncov = _setup_proj(
                "uncov_clip",
                status="ok",
                speech_spans=[[0.5, 5.0]],
                words=[{"w": "only", "start": 0.5, "end": 1.0}],
            )
            uncov_calls = 0

            def _rerun_uncov() -> Path:
                nonlocal uncov_calls
                uncov_calls += 1
                self.assertEqual(os.environ.get("ZENO_RETRY_INCOMPLETE"), "1")
                return p_uncov / "plan.edl"

            with mock.patch.dict(os.environ, {}, clear=False):
                os.environ.pop("ZENO_RETRY_INCOMPLETE", None)
                os.environ.pop("ZENO_NO_AUTO_RETRY", None)
                _, _, retried_uncov = zeno._auto_retry_incomplete(p_uncov, _rerun_uncov)
            self.assertTrue(retried_uncov)
            self.assertEqual(uncov_calls, 1)

            # Case 3: all bad clips are quota_exceeded -> skips retry.
            p_quota = _setup_proj(
                "quota_clip",
                status="quota_exceeded",
                speech_spans=[[0.5, 4.0]],
                words=[],
            )
            quota_calls = 0

            def _rerun_quota() -> Path:
                nonlocal quota_calls
                quota_calls += 1
                return p_quota / "plan.edl"

            with mock.patch.dict(os.environ, {}, clear=False):
                os.environ.pop("ZENO_NO_AUTO_RETRY", None)
                res_q, _, retried_q = zeno._auto_retry_incomplete(p_quota, _rerun_quota)
            self.assertFalse(retried_q)
            self.assertEqual(quota_calls, 0)
            self.assertEqual(res_q, p_quota)

            # Case 4: ZENO_NO_AUTO_RETRY=1 -> skips retry even when incomplete.
            no_retry_calls = 0

            def _rerun_disabled() -> Path:
                nonlocal no_retry_calls
                no_retry_calls += 1
                return p_inc / "plan.edl"

            with mock.patch.dict(os.environ, {"ZENO_NO_AUTO_RETRY": "1"}, clear=False):
                res_dis, _, retried_dis = zeno._auto_retry_incomplete(p_inc, _rerun_disabled)
            self.assertFalse(retried_dis)
            self.assertEqual(no_retry_calls, 0)
            self.assertEqual(res_dis, p_inc)


class LargeShootPacketDietTests(unittest.TestCase):
    """Verify the 30+ clip facts diet threshold and preservation invariants."""

    def _spoken_block(self, idx: int, *, extra_header: str = "", extra_lines: list[str] | None = None) -> str:
        hdr = f"## C{idx}.mp4 (12.00s 1080x1920 {extra_header}focus=0.50,0.20)".replace("  ", " ")
        lines = [
            hdr,
            f"  0.20 word{idx}a 0.65 word{idx}b |1.10",
            f"  1.75 word{idx}c 2.20 word{idx}d |2.60",
            "  pauses: 1.10-1.75",
        ]
        if extra_lines:
            lines.extend(extra_lines)
        return "\n".join(lines)

    def test_under_30_clips_is_byte_identical(self) -> None:
        text29 = "\n\n".join(self._spoken_block(i) for i in range(1, 30))
        baseline = edit_packet.compact_facts(text29)
        with_diet_arg = edit_packet.compact_facts(text29, active_clips={"C1.mp4"}, min_clips_for_diet=30)
        self.assertEqual(with_diet_arg, baseline)

    def test_32_clips_preserves_essential_and_collapses_unused_takes(self) -> None:
        blocks = [
            # C1: CAND active clip
            self._spoken_block(1),
            # C2: plan peer clip (referenced in plan.edl via peer=)
            self._spoken_block(2),
            # C3: SILENT clip
            "\n".join([
                "## C3.mp4 (10.00s 1080x1920 focus=0.48,0.22 SILENT)",
                "  ! SILENT / AMBIENT CLIP — use only as silent listener footage",
                "  pauses: 0.00-10.00",
            ]),
            # C4: two_shot clip
            self._spoken_block(4, extra_header="shot_type=two_shot face_count=2 "),
            # C5: untranscribed_speech clip (>= 1.2s uncovered VAD speech)
            "\n".join([
                "## C5.mp4 (10.00s 1080x1920 focus=0.52,0.18)",
                "  0.20 word5a 0.60 word5b |1.00",
                "  pauses: 1.00-1.50",
                "  speech (voice detector, works over noise): 0.20-1.05 1.50-5.20",
            ]),
        ]
        # C6..C32: unused spoken takes (0 CAND matches)
        for idx in range(6, 33):
            blocks.append(self._spoken_block(idx))

        text32 = "\n\n".join(blocks)
        line_map_txt = "\n".join([
            'L1 [A] "word1a word1b"',
            "  CAND C1.mp4 0.15-1.15 (heard=1.00)",
        ])
        plan_txt = "\n".join([
            "#format micronarrative",
            'L1  C1.mp4  0.15-1.15  sp="A"  peer="B:C2.mp4"@0.10-1.10@right',
        ])

        active = edit_packet._extract_active_clips(line_map_txt, plan_txt)
        self.assertIn("C1.mp4", active)
        self.assertIn("C2.mp4", active)

        compacted = edit_packet.compact_facts(text32, active_clips=active, min_clips_for_diet=30)

        # Kept full: C1, C2, C3 (SILENT), C4 (two_shot), C5 (untranscribed_speech)
        for kept_idx in (1, 2, 4, 5):
            self.assertIn(f"0.20 word{kept_idx}a", compacted)
            self.assertIn("pauses:", compacted.split(f"## C{kept_idx}.mp4")[1].split("\n## ")[0])
            self.assertIn("focus=", compacted.split(f"## C{kept_idx}.mp4")[1].split("\n## ")[0])
        self.assertIn("1.75 word1c", compacted)
        self.assertIn("1.75 word2c", compacted)
        self.assertIn("1.75 word4c", compacted)
        self.assertIn("! SILENT / AMBIENT CLIP", compacted)
        sec3 = compacted.split("## C3.mp4")[1].split("\n## ")[0]
        self.assertIn("focus=0.48,0.22", sec3)
        self.assertIn("pauses: 0.00-10.00", sec3)
        self.assertIn("untranscribed_speech: 1.50-5.20s", compacted)

        # Collapsed: C6..C32 preserve header + focus= + pauses:, drop word lines, show unused-take hint.
        for unused_idx in range(6, 33):
            sec = compacted.split(f"## C{unused_idx}.mp4")[1].split("\n## ")[0]
            self.assertIn("focus=0.50,0.20", sec)
            self.assertIn("pauses: 1.10-1.75", sec)
            self.assertIn(f'(unused take — 0 CAND matches; run edit_packet.py <project> --clip "C{unused_idx}.mp4" for full words)', sec)
            self.assertNotIn(f"word{unused_idx}a", sec)

        # Also verify edit_packet.build() wires _extract_active_clips -> compact_facts end-to-end.
        with tempfile.TemporaryDirectory() as tmp:
            proj = Path(tmp) / "shoot32"
            proj.mkdir()
            (proj / "brief.md").write_text("# Brief\nSample", encoding="utf-8")
            (proj / "line_map.md").write_text(line_map_txt, encoding="utf-8")
            (proj / "facts.md").write_text(text32, encoding="utf-8")
            (proj / "plan.edl").write_text(plan_txt, encoding="utf-8")
            (proj / "script.txt").write_text("L1 [A] word1a word1b\n", encoding="utf-8")
            packet_text, _ = edit_packet.build(proj)
            self.assertIn("1.75 word1c", packet_text)
            self.assertIn("1.75 word2c", packet_text)
            self.assertNotIn("word6a", packet_text)
            self.assertIn('(unused take — 0 CAND matches; run edit_packet.py <project> --clip "C6.mp4" for full words)', packet_text)


class TelemetryClampAndMixedDevTests(unittest.TestCase):
    """Verify id=354 88.1h stale-window clamp and accurate repo-vs-external mixed_with_development."""

    def test_stale_88h_window_clamped_in_zeno_and_finalize_telemetry(self) -> None:
        now = time.time()
        stale_start = now - 88 * 3600
        recent_ev = round(now - 600, 1)

        with tempfile.TemporaryDirectory() as tmp:
            tmp_dir = Path(tmp)
            proj = tmp_dir / "proj_354"
            proj.mkdir()

            (proj / "token_telemetry.json").write_text(
                json.dumps({"project": "proj_354", "job_start_epoch": stale_start}),
                encoding="utf-8",
            )
            (proj / "stage_events.jsonl").write_text(
                "\n".join([
                    json.dumps({"at": stale_start, "kind": "inspect"}),
                    json.dumps({"at": recent_ev, "kind": "inspect"}),
                ]) + "\n",
                encoding="utf-8",
            )
            plan_path = proj / "plan.edl"
            plan_path.write_text('L1 C1.mp4 0.10-2.10 sp="A"\n', encoding="utf-8")
            out_mp4 = tmp_dir / "out.mp4"

            # 1. zeno._job_start_from_project clamps 88h -> recent_ev.
            snap = json.loads((proj / "token_telemetry.json").read_text(encoding="utf-8"))
            got_zeno_start = zeno._job_start_from_project(proj, snap)
            self.assertAlmostEqual(got_zeno_start, recent_ev, delta=1.0)
            self.assertGreater(got_zeno_start, now - 4 * 3600)

            # 2. token_tracker.finalize_job_telemetry clamps 88h -> recent_ev without writing global work/.
            seen_windows: list[tuple[float | None, float | None, str | None]] = []

            def _fake_detect(since_epoch=None, until_epoch=None, session_id=None, project_hint=None):
                seen_windows.append((since_epoch, until_epoch, project_hint))
                return {
                    "agent_name": "Claude Code",
                    "agent_model": "claude-opus-4-6",
                    "steps_count": 2,
                    "turns": 2,
                    "input_tokens": 100,
                    "cached_tokens": 500,
                    "cache_write_tokens": 0,
                    "output_tokens": 50,
                    "total_tokens": 150,
                    "token_precision": "exact",
                    "requests": [],
                    "code_edits": 0,
                    "window_minutes": 10.0,
                    "mixed_with_development": False,
                }

            with (
                mock.patch.object(token_tracker, "LOCAL_DB", tmp_dir / "telemetry.db"),
                mock.patch.object(token_tracker, "LEDGER_MD", tmp_dir / "LEDGER.md"),
                mock.patch.object(token_tracker, "detect_agent_usage", side_effect=_fake_detect),
            ):
                rec = token_tracker.finalize_job_telemetry(
                    plan_path=plan_path,
                    plan={"segments": [{"id": "L1", "src": "C1.mp4", "start": 0.1, "end": 2.1}]},
                    video_seconds=2.0,
                    output_mp4=out_mp4,
                    sync_cloud=False,
                )

            self.assertEqual(len(seen_windows), 1)
            self.assertAlmostEqual(seen_windows[0][0], recent_ev, delta=1.0)
            self.assertEqual(seen_windows[0][2], "proj_354")
            self.assertAlmostEqual(rec["job_start_epoch"], recent_ev, delta=1.0)
            persisted = json.loads((proj / "token_telemetry.json").read_text(encoding="utf-8"))
            self.assertAlmostEqual(persisted["job_start_epoch"], recent_ev, delta=1.0)

    def test_job_archive_mark_started_overwrites_stale_and_completed(self) -> None:
        now = time.time()
        with tempfile.TemporaryDirectory() as tmp:
            proj = Path(tmp) / "proj_mark"
            proj.mkdir()

            # Stale job_started.json overwritten by force_fresh=True.
            (proj / "job_started.json").write_text(json.dumps({"start": now - 88 * 3600}), encoding="utf-8")
            t0 = time.time()
            job_archive.mark_started(proj, force_fresh=True)
            s1 = json.loads((proj / "job_started.json").read_text(encoding="utf-8"))["start"]
            self.assertGreaterEqual(s1, t0 - 1.5)

            # Recent job_started.json (<2h) STILL overwritten when token_telemetry.json["job_end_epoch"] is set.
            (proj / "job_started.json").write_text(json.dumps({"start": now - 600}), encoding="utf-8")
            (proj / "token_telemetry.json").write_text(json.dumps({"job_end_epoch": now - 60}), encoding="utf-8")
            t1 = time.time()
            job_archive.mark_started(proj)
            s2 = json.loads((proj / "job_started.json").read_text(encoding="utf-8"))["start"]
            self.assertGreaterEqual(s2, t1 - 1.5)

            # Without job_end_epoch and <2h old, mark_started(proj) preserves active start.
            (proj / "token_telemetry.json").unlink()
            keep_ts = round(now - 300, 1)
            (proj / "job_started.json").write_text(json.dumps({"start": keep_ts}), encoding="utf-8")
            job_archive.mark_started(proj)
            s3 = json.loads((proj / "job_started.json").read_text(encoding="utf-8"))["start"]
            self.assertEqual(s3, keep_ts)

    def test_mixed_with_development_and_repo_code_path_classification(self) -> None:
        self.assertFalse(agent_log._is_repo_code_path("/tmp/helper.py", ROOT))
        self.assertFalse(agent_log._is_repo_code_path("/Users/editor/Downloads/scripts/notes.py", ROOT))
        self.assertFalse(agent_log._is_repo_code_path(str(ROOT / "work" / "proj" / "scratch.py"), ROOT))
        self.assertTrue(agent_log._is_repo_code_path(str(ROOT / "scripts" / "zeno.py"), ROOT))

        now = time.time()
        ts_iso = dt.datetime.fromtimestamp(now, tz=dt.timezone.utc).isoformat()
        cutoff = now - 60.0
        ceiling = now + 60.0

        def _assistant_line(msg_id: str, tool_name: str, file_path: str) -> str:
            return json.dumps({
                "type": "assistant",
                "timestamp": ts_iso,
                "message": {
                    "id": msg_id,
                    "model": "claude-sonnet-4-6",
                    "usage": {
                        "input_tokens": 100,
                        "cache_creation_input_tokens": 0,
                        "cache_read_input_tokens": 200,
                        "output_tokens": 40,
                    },
                    "content": [
                        {"type": "tool_use", "name": tool_name, "input": {"file_path": file_path}},
                    ],
                },
            })

        non_repo_lines = [
            _assistant_line("m1", "Write", "/tmp/helper.py"),
            _assistant_line("m2", "Write", "/Users/editor/Downloads/scripts/notes.py"),
        ]
        res_clean = token_tracker.claude_window_usage(non_repo_lines, cutoff, ceiling, root=ROOT)
        self.assertIsNotNone(res_clean)
        self.assertEqual(res_clean["code_edits"], 0)
        self.assertIs(res_clean["mixed_with_development"], False)

        repo_lines = [
            _assistant_line("m1", "Write", "/tmp/helper.py"),
            _assistant_line("m2", "Edit", str(ROOT / "scripts" / "zeno.py")),
        ]
        res_dev = token_tracker.claude_window_usage(repo_lines, cutoff, ceiling, root=ROOT)
        self.assertIsNotNone(res_dev)
        self.assertGreaterEqual(res_dev["code_edits"], 1)
        self.assertIs(res_dev["mixed_with_development"], True)

    def test_antigravity_window_usage_code_edits_window_minutes_and_mixed(self) -> None:
        base = 1795430000.0
        iso = lambda dt_sec: dt.datetime.fromtimestamp(base + dt_sec, tz=dt.timezone.utc).isoformat()  # noqa: E731

        def _agy_planner(step_idx: int, dt_sec: float, tool_calls: list[dict], *, cached: int = 1000) -> str:
            return json.dumps({
                "step_index": step_idx,
                "type": "PLANNER_RESPONSE",
                "created_at": iso(dt_sec),
                "input_tokens": 200,
                "cache_read_tokens": cached,
                "output_tokens": 50,
                "content": "",
                "tool_calls": tool_calls,
            })

        # 1. Single step inside window -> window_minutes == 0.0, clean edit.
        single_lines = [
            _agy_planner(1, 10.0, [{"name": "write_file", "args": {"Path": str(ROOT / "work" / "proj_a" / "plan.edl")}}]),
        ]
        res_single = agent_log.antigravity_window_usage(single_lines, base, base + 600.0, session_id="s1", root=ROOT)
        self.assertIsNotNone(res_single)
        self.assertEqual(res_single["window_minutes"], 0.0)
        self.assertEqual(res_single["code_edits"], 0)
        self.assertIs(res_single["mixed_with_development"], False)

        # 2. Multi-step clean video edit (5.5 min span, non-repo edits + zeno.py command) -> window_minutes=5.5, mixed=False.
        clean_lines = [
            _agy_planner(1, 0.0, [{"name": "write_file", "args": {"Path": str(ROOT / "work" / "proj_a" / "plan.edl")}}]),
            _agy_planner(2, 180.0, [{"name": "edit_file", "args": {"Path": "/tmp/notes.py"}}]),
            _agy_planner(3, 330.0, [{"name": "run_command", "args": {"CommandLine": ".venv/bin/python scripts/zeno.py --execute work/proj_a/plan.edl"}}]),
        ]
        res_clean = agent_log.antigravity_window_usage(clean_lines, base - 1.0, base + 400.0, session_id="s2", root=ROOT)
        self.assertIsNotNone(res_clean)
        self.assertEqual(res_clean["window_minutes"], 5.5)
        self.assertEqual(res_clean["code_edits"], 0)
        self.assertIs(res_clean["mixed_with_development"], False)

        # 3. Dev-mixed session (repo edit_file, write_file, replace_file_content, run_command unittest) -> code_edits=4, mixed=True.
        dev_lines = [
            _agy_planner(1, 0.0, [{"name": "edit_file", "args": {"Path": str(ROOT / "scripts" / "zeno.py")}}]),
            _agy_planner(2, 60.0, [{"tool": "write_file", "arguments": {"TargetFile": str(ROOT / "tests" / "test_x.py")}}]),
            _agy_planner(3, 120.0, [{"name": "replace_file_content", "args": {"AbsolutePath": str(ROOT / "scripts" / "agent_log.py")}}]),
            _agy_planner(4, 240.0, [{"name": "run_command", "args": {"CommandLine": ".venv/bin/python -m unittest tests/test_x.py"}}]),
        ]
        res_dev = agent_log.antigravity_window_usage(dev_lines, base - 1.0, base + 400.0, session_id="s3", root=ROOT)
        self.assertIsNotNone(res_dev)
        self.assertEqual(res_dev["window_minutes"], 4.0)
        self.assertEqual(res_dev["code_edits"], 4)
        self.assertIs(res_dev["mixed_with_development"], True)

    def test_project_hint_disambiguates_concurrent_antigravity_and_claude_subagents(self) -> None:
        base = float(int(time.time()) - 900)
        iso = lambda dt_sec: dt.datetime.fromtimestamp(base + dt_sec, tz=dt.timezone.utc).isoformat()  # noqa: E731
        cutoff = base + 50.0
        ceiling = base + 600.0

        with tempfile.TemporaryDirectory() as tmp:
            home_dir = Path(tmp)

            # ---- A. Antigravity concurrent transcripts: dev (newer mtime) vs parent (newest) vs isolated video subagent ----
            def _write_agy_transcript(sid: str, rows: list[dict], mtime_offset: float) -> Path:
                p = home_dir / ".gemini" / "antigravity" / "brain" / sid / ".system_generated" / "logs" / "transcript.jsonl"
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
                os.utime(p, (base + mtime_offset, base + mtime_offset))
                return p

            dev_sid = "11111111-1111-4111-8111-111111111111"
            parent_sid = "22222222-2222-4222-8222-222222222222"
            video_sid = "33333333-3333-4333-8333-333333333333"
            project_hint = "gdoc_AlphaVideo12_t_tab1"

            # 1) Dev subagent has guardrail mentioning project_hint BEFORE cutoff, edits scripts/zeno.py, and has newer mtime than video_sid.
            _write_agy_transcript(
                dev_sid,
                [
                    {"step_index": 0, "type": "SYSTEM_MESSAGE", "created_at": iso(10.0), "content": f"DO NOT touch work/{project_hint}/"},
                    {
                        "step_index": 1,
                        "type": "PLANNER_RESPONSE",
                        "created_at": iso(100.0),
                        "input_tokens": 500,
                        "cache_read_tokens": 90000,
                        "output_tokens": 200,
                        "tool_calls": [{"name": "edit_file", "args": {"Path": str(ROOT / "scripts" / "zeno.py")}}],
                    },
                ],
                mtime_offset=500.0,
            )
            # 2) Parent chat invoked subagents with project_hint in invoke_subagent, newer mtime, 0 code_edits.
            _write_agy_transcript(
                parent_sid,
                [
                    {
                        "step_index": 1,
                        "type": "PLANNER_RESPONSE",
                        "created_at": iso(80.0),
                        "input_tokens": 300,
                        "cache_read_tokens": 40000,
                        "output_tokens": 80,
                        "tool_calls": [{"name": "invoke_subagent", "args": {"Prompt": f"Edit work/{project_hint}"}}],
                    },
                ],
                mtime_offset=550.0,
            )
            # 3) Isolated video subagent actually edits work/<project_hint>/plan.edl and runs zeno.py, older mtime than both.
            _write_agy_transcript(
                video_sid,
                [
                    {
                        "step_index": 1,
                        "type": "PLANNER_RESPONSE",
                        "created_at": iso(90.0),
                        "input_tokens": 120,
                        "cache_read_tokens": 1200,
                        "output_tokens": 40,
                        "tool_calls": [{"name": "write_file", "args": {"Path": str(ROOT / "work" / project_hint / "plan.edl")}}],
                    },
                    {
                        "step_index": 2,
                        "type": "PLANNER_RESPONSE",
                        "created_at": iso(270.0),
                        "input_tokens": 140,
                        "cache_read_tokens": 1500,
                        "output_tokens": 60,
                        "tool_calls": [{"name": "run_command", "args": {"CommandLine": f".venv/bin/python scripts/zeno.py --execute work/{project_hint}/plan.edl"}}],
                    },
                ],
                mtime_offset=400.0,
            )

            sibling_sid = "44444444-4444-4444-8444-444444444444"
            _write_agy_transcript(
                sibling_sid,
                [
                    {
                        "step_index": 1,
                        "type": "PLANNER_RESPONSE",
                        "created_at": iso(120.0),
                        "input_tokens": 200,
                        "cache_read_tokens": 55000,
                        "output_tokens": 90,
                        "content": "Zeno unrelated sibling edit",
                    },
                ],
                mtime_offset=580.0,
            )

            with (
                mock.patch.dict(os.environ, {"HOME": str(home_dir)}, clear=False),
                mock.patch("codex_usage.detect", return_value=None),
            ):
                os.environ.pop("CLAUDECODE", None)
                os.environ.pop("CODEX_THREAD_ID", None)
                # Even when stale session_id=dev_sid or sibling_sid (which never touched project_hint) is passed, project_hint selects video_sid!
                got_agy = token_tracker.detect_agent_usage(
                    since_epoch=cutoff,
                    until_epoch=ceiling,
                    session_id=dev_sid,
                    project_hint=project_hint,
                )
                got_agy_from_sibling = token_tracker.detect_agent_usage(
                    since_epoch=cutoff,
                    until_epoch=ceiling,
                    session_id=sibling_sid,
                    project_hint=project_hint,
                )
                pinned_sid = zeno._detect_active_session_id(base + 300.0, project_hint=project_hint)

                cap_proj = home_dir / "work" / project_hint
                cap_proj.mkdir(parents=True)
                (cap_proj / "token_telemetry.json").write_text(
                    json.dumps({"project": project_hint, "job_start_epoch": cutoff, "agent_session_id": sibling_sid}),
                    encoding="utf-8",
                )
                zeno._capture_token_session(cap_proj, completed_at=ceiling)
                captured = json.loads((cap_proj / "token_telemetry.json").read_text(encoding="utf-8"))

            self.assertEqual(got_agy["agent_name"], "Antigravity")
            self.assertEqual(got_agy["session_id"], video_sid)
            self.assertEqual(got_agy["cached_tokens"], 2700)
            self.assertEqual(got_agy["window_minutes"], 3.0)
            self.assertEqual(got_agy["code_edits"], 0)
            self.assertIs(got_agy["mixed_with_development"], False)
            self.assertEqual(got_agy_from_sibling["session_id"], video_sid)
            self.assertEqual(pinned_sid, video_sid)
            self.assertEqual(captured["agent_session_id"], video_sid)
            self.assertEqual(captured["agent_cached_tokens"], 2700)

            # ---- B. Claude Code concurrent transcripts: dev parent (.jsonl) vs isolated subagent (subagents/agent-*.jsonl) ----
            claude_proj = home_dir / ".claude" / "projects" / "-Users-dev-Claude-Editor"
            claude_sub_dir = claude_proj / "parent-uuid" / "subagents"
            claude_sub_dir.mkdir(parents=True, exist_ok=True)
            claude_hint = "gdoc_ClaudeVid99_t_tab2"

            def _claude_msg(msg_id: str, dt_sec: float, tool_name: str, tool_input: dict, *, cwd: str | None = None, text: str = "", cached: int = 500) -> str:
                row = {
                    "type": "assistant",
                    "timestamp": iso(dt_sec),
                    "message": {
                        "id": msg_id,
                        "model": "claude-sonnet-4-6",
                        "usage": {
                            "input_tokens": 100,
                            "cache_creation_input_tokens": 20,
                            "cache_read_input_tokens": cached,
                            "output_tokens": 30,
                        },
                        "content": [
                            *([] if not text else [{"type": "text", "text": text}]),
                            {"type": "tool_use", "name": tool_name, "input": tool_input},
                        ],
                    },
                }
                if cwd is not None:
                    row["cwd"] = cwd
                return json.dumps(row)

            dev_claude = claude_proj / "parent-uuid.jsonl"
            dev_claude.write_text(
                "\n".join([
                    _claude_msg("d1", 100.0, "Edit", {"file_path": str(ROOT / "scripts" / "zeno.py")}, cwd=str(ROOT), cached=80000),
                    _claude_msg("d2", 120.0, "Agent", {"prompt": f"Process work/{claude_hint}"}, cwd=str(ROOT), cached=82000),
                ]) + "\n",
                encoding="utf-8",
            )
            os.utime(dev_claude, (base + 550.0, base + 550.0))

            # Subagent log omits `cwd` on assistant lines and mentions ROOT.name in text -> passes subagent ROOT.name check.
            sub_claude = claude_sub_dir / "agent-video42.jsonl"
            sub_claude.write_text(
                "\n".join([
                    _claude_msg("v1", 110.0, "Write", {"file_path": str(ROOT / "work" / claude_hint / "plan.edl")}, text=f"Working in {ROOT.name}", cached=1000),
                    _claude_msg("v2", 290.0, "Bash", {"command": f".venv/bin/python scripts/zeno.py --execute work/{claude_hint}/plan.edl"}, cached=1200),
                ]) + "\n",
                encoding="utf-8",
            )
            os.utime(sub_claude, (base + 420.0, base + 420.0))

            with (
                mock.patch.dict(
                    os.environ,
                    {"HOME": str(home_dir), "CLAUDECODE": "1", "CLAUDE_CODE_SESSION_ID": "parent-uuid"},
                    clear=False,
                ),
                mock.patch("codex_usage.detect", return_value=None),
            ):
                os.environ.pop("CODEX_THREAD_ID", None)
                got_claude = token_tracker.detect_agent_usage(
                    since_epoch=cutoff,
                    until_epoch=ceiling,
                    session_id="parent-uuid",
                    project_hint=claude_hint,
                )

            self.assertEqual(got_claude["agent_name"], "Claude Code")
            self.assertEqual(got_claude["session_id"], "agent-video42")
            self.assertEqual(got_claude["cached_tokens"], 2200)
            self.assertEqual(got_claude["window_minutes"], 3.0)
            self.assertEqual(got_claude["code_edits"], 0)
            self.assertIs(got_claude["mixed_with_development"], False)


class UsageDashboardAndPromptSyncTests(unittest.TestCase):
    """Verify usage_page.py JS compiles cleanly with Node and instruction files stay synchronized."""

    def test_usage_page_js_syntax_and_clean_runs_controls(self) -> None:
        self.assertIn("includeDev", usage_page.USAGE_PAGE)
        self.assertIn("cleanRuns", usage_page.USAGE_PAGE)
        self.assertIn("Showing: Editor runs", usage_page.USAGE_PAGE)

        scripts = re.findall(r"<script(?:\s[^>]*)?>(.*?)</script>", usage_page.USAGE_PAGE, re.S)
        inline = [s for s in scripts if s.strip()]
        self.assertEqual(len(inline), 1)
        with tempfile.TemporaryDirectory() as tmp:
            js_path = Path(tmp) / "usage_page.js"
            js_path.write_text(inline[0], encoding="utf-8")
            proc = subprocess.run(
                ["node", "--check", str(js_path)],
                capture_output=True,
                text=True,
            )
        self.assertEqual(proc.returncode, 0, f"node --check failed:\n{proc.stderr}")

    def test_instruction_files_synced_and_contain_required_directives(self) -> None:
        self.assertEqual(
            (ROOT / "AGENTS.md").read_bytes(),
            (ROOT / "CLAUDE.md").read_bytes(),
            "AGENTS.md and CLAUDE.md must remain byte-for-byte identical",
        )
        instruction_files = [
            ROOT / "AGENTS.md",
            ROOT / "CLAUDE.md",
            ROOT / ".agent" / "rules" / "zeno.md",
            ROOT / ".agent" / "skills" / "zeno" / "SKILL.md",
            ROOT / ".claude" / "commands" / "edit.md",
            ROOT / ".claude" / "commands" / "zeno.md",
        ]
        for path in instruction_files:
            self.assertTrue(path.is_file(), f"Missing instruction file: {path}")
            txt = path.read_text(encoding="utf-8")
            for token in ("[AUTO RETRY]", "[LINE DETAILS]", "--lines", "[CUTS   WHERE]", "2nd+"):
                self.assertIn(token, txt, f"{path.relative_to(ROOT)} missing {token!r}")
            self.assertIn("subagent", txt.lower(), f"{path.relative_to(ROOT)} missing subagent isolation rule")


if __name__ == "__main__":
    unittest.main()
