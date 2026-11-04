#!/usr/bin/env python3
"""Split framing is one shared target for the whole job, not one per talker/listener pair."""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import vision_tracker  # noqa: E402

# (eye_x, eye_y, face_height) as fractions of the frame, from the W32 Malayalam raw clips
GEOM = {
    "woman_talk": (0.45, 0.36, 0.30),
    "woman_silent": (0.49, 0.41, 0.336),
    "teacher_talk": (0.52, 0.43, 0.25),
    "teacher_silent": (0.51, 0.435, 0.24),
}
SLOT_W, SLOT_H, W, H = 540, 1920, 1080, 1920


class TestSplitCanon(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.paths = {}
        for name in GEOM:
            p = Path(self.tmp.name) / f"{name}.mp4"
            p.write_bytes(b"x")
            self.paths[name] = p
        by_path = {str(p): GEOM[n] for n, p in self.paths.items()}
        self.patches = [
            mock.patch.object(vision_tracker, "_primary_face_geometry", lambda path: by_path[str(path)]),
            mock.patch.object(vision_tracker, "probe", lambda p: {"width": W, "height": H}),
        ]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        self.tmp.cleanup()

    def pair(self, live, peer):
        return vision_tracker.compute_paired_split_crop_filters(
            self.paths[live], self.paths[peer], W, H, W, H, SLOT_W, SLOT_H, SLOT_W, SLOT_H,
            canon_srcs=list(self.paths.values()),
        )

    def measured(self, name, crop):
        """Eye line and face height of that clip as they land in the slot, as fractions of the slot height."""
        _ex, ey, fh = GEOM[name]
        sw, sh = crop["scale"]
        return (ey * sh - crop["crop"][1]) / SLOT_H, fh * sh / SLOT_H

    def test_same_eye_line_and_face_size_for_every_pair(self):
        results = []
        for live, peer in (("woman_talk", "teacher_silent"), ("teacher_talk", "woman_silent")):
            c_live, c_peer = self.pair(live, peer)
            results += [self.measured(live, c_live), self.measured(peer, c_peer)]
        for eye, face in results:
            self.assertAlmostEqual(eye, 0.38, delta=0.005)
            self.assertAlmostEqual(face, results[0][1], delta=0.01)

    def test_framing_numbers_identical_across_pairs(self):
        a, _ = self.pair("woman_talk", "teacher_silent")
        b, _ = self.pair("teacher_talk", "woman_silent")
        self.assertEqual(a["common_face_pct"], b["common_face_pct"])
        self.assertEqual(a["common_headroom_pct"], b["common_headroom_pct"])

    def test_small_teacher_face_is_scaled_up_to_match(self):
        _, teacher = self.pair("woman_talk", "teacher_talk")
        self.assertGreater(teacher["extra_zoom"], vision_tracker.MAX_EXTRA_ZOOM)


if __name__ == "__main__":
    unittest.main()
