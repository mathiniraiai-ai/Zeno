import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import prepare  # noqa: E402
import preflight  # noqa: E402


class SilentClipRule(unittest.TestCase):
    def test_low_voice_is_silent(self):
        self.assertFalse(prepare._is_real_speech(1.1, 10.0))
        self.assertFalse(prepare._is_real_speech(1.99, 5.0))
        self.assertTrue(prepare._is_real_speech(1.5, 2.0))
        self.assertFalse(prepare._is_real_speech(3.0, 30.0))  # ratio 0.10

    def test_real_speech(self):
        self.assertTrue(prepare._is_real_speech(2.0, 10.0))
        self.assertTrue(prepare._is_real_speech(8.0, 20.0))


class GuardCounter(unittest.TestCase):
    def test_counter_caps_and_resets(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d)
            self.assertEqual([prepare._bump_guard_counter(p, "a") for _ in range(3)], [1, 2, 3])
            self.assertEqual(prepare._bump_guard_counter(p, "b"), 1)
            prepare._clear_guard_counter(p)
            self.assertEqual(prepare._bump_guard_counter(p, "b"), 1)

    def test_cooldown_parse(self):
        self.assertEqual(prepare._guard_cooldown_secs(["x (cooldown 45s)", "y"]), 45)


class PreflightCooldown(unittest.TestCase):
    def test_short_cooldown_waits_once(self):
        seq = [[(False, "G", "Gemini models temporarily in cooldown (~40s)")], [(True, "ok", "fine")]]
        with mock.patch.object(preflight, "_check_transcription_auth_once", side_effect=seq) as m, \
                mock.patch.object(preflight.time, "sleep") as sl:
            self.assertTrue(preflight.check_transcription_auth()[0][0])
            self.assertEqual(m.call_count, 2)
            sl.assert_called_once_with(42)

    def test_long_cooldown_blocks_with_message(self):
        res = [(False, "G", "Gemini models temporarily in cooldown (~970s)")]
        with mock.patch.object(preflight, "_check_transcription_auth_once", return_value=res), \
                mock.patch.object(preflight.time, "sleep") as sl:
            ok, _n, d = preflight.check_transcription_auth()[0]
            self.assertFalse(ok)
            self.assertIn("970s", d)
            self.assertIn("server Gemini limit", d)
            sl.assert_not_called()


if __name__ == "__main__":
    unittest.main()
