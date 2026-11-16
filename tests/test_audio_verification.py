"""Loudness verification must measure the right file and fail closed."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import verify


class TestAudioVerification(unittest.TestCase):
    def test_measurement_failure_cannot_pass(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            clip = root / "bad.mp4"
            clip.write_bytes(b"not a video")
            with patch.object(verify, "WORK", root), patch.object(
                verify.subprocess, "run", return_value=subprocess.CompletedProcess([], 1, "", "decoder failed")
            ):
                self.assertFalse(verify.check_loudness(clip)[0])

    def test_same_named_files_do_not_share_measurements(self):
        import os
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            clips = [root / name / "reel.mp4" for name in ("first", "second")]
            for clip in clips:
                clip.parent.mkdir()
                clip.write_bytes(b"same size")
                os.utime(clip, ns=(1000000000, 1000000000))
            responses = [subprocess.CompletedProcess([], 0, "", json.dumps({"input_i": -11.5, "input_tp": peak}))
                         for peak in (-1.5, 1.0)]
            with patch.object(verify, "WORK", root), patch.object(verify.subprocess, "run", side_effect=responses) as run:
                self.assertTrue(verify.check_loudness(clips[0])[0])
                self.assertFalse(verify.check_loudness(clips[1])[0])
                self.assertTrue(verify.check_loudness(clips[0])[0])
                self.assertEqual(run.call_count, 2)


if __name__ == "__main__":
    unittest.main()
