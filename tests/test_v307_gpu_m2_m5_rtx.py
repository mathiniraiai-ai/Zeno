"""Regression tests for Zeno v3.0.7 M2/M5 & RTX 3060+ GPU optimizations."""
from __future__ import annotations

import concurrent.futures
import sys
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import common  # noqa: E402
import layouts  # noqa: E402
import scroll_page  # noqa: E402
from align_engine import acoustic, pipeline  # noqa: E402


class TestV307GpuM2M5Rtx(unittest.TestCase):
    def test_get_shared_pipeline_thread_safe_singleton(self) -> None:
        orig_shared = pipeline._shared_pipeline
        pipeline._shared_pipeline = None
        init_count = 0

        class _FakePipeline:
            def __init__(self, device=None):
                nonlocal init_count
                time.sleep(0.02)
                init_count += 1
                self.device = device

        try:
            with mock.patch.object(pipeline, "ZenoAcousticPipeline", _FakePipeline):
                with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
                    results = list(pool.map(lambda _i: pipeline.get_shared_pipeline(), range(6)))
            self.assertEqual(init_count, 1)
            self.assertTrue(all(r is results[0] for r in results))
        finally:
            pipeline._shared_pipeline = orig_shared

    def test_acoustic_model_forward_chunk_serialized(self) -> None:
        am = acoustic.AcousticModel.__new__(acoustic.AcousticModel)
        am._infer_lock = threading.RLock()
        am.device = "mps"
        am.use_fp16 = True
        am._torch_dtype = acoustic.torch.float16
        am.thermal_profile = "mac_fanless"

        param = acoustic.torch.zeros((1,), dtype=acoustic.torch.float32)

        class _FakeModel:
            def parameters(self):
                return iter([param])

            def __call__(self, input_values=None):
                # Non-zero valid finite logits [1, 4, 6]
                return SimpleNamespace(logits=acoustic.torch.ones((1, 4, 6), dtype=acoustic.torch.float32))

        am.torch_model = _FakeModel()

        active = 0
        max_active = 0
        gate = threading.Lock()
        orig_log_softmax = acoustic.torch.nn.functional.log_softmax

        def _slow_log_softmax(tensor, dim=-1):
            nonlocal active, max_active
            with gate:
                active += 1
                max_active = max(max_active, active)
            time.sleep(0.02)
            try:
                return orig_log_softmax(tensor, dim=dim)
            finally:
                with gate:
                    active -= 1

        chunk = np.zeros(800, dtype=np.float32)
        with mock.patch.object(acoustic.torch.nn.functional, "log_softmax", side_effect=_slow_log_softmax):
            with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
                outs = list(pool.map(lambda _i: am._forward_chunk(chunk), range(4)))

        self.assertEqual(max_active, 1)
        self.assertEqual(am.device, "mps")
        self.assertEqual(len(outs), 4)

    def test_nvenc_uses_p3_for_intermediate_crf12_and_p5_for_final(self) -> None:
        args_inter = common.hw_video_args(
            "h264_nvenc", crf=12, draft=False, nvenc_presets=("p5", "p3")
        )
        args_final = common.hw_video_args(
            "h264_nvenc", crf=18, draft=False, nvenc_presets=("p5", "p3")
        )
        self.assertEqual(args_inter[args_inter.index("-preset") + 1], "p3")
        self.assertEqual(args_final[args_final.index("-preset") + 1], "p5")

    def test_videotoolbox_uses_prio_speed_for_intermediate_crf12_and_omits_for_final(self) -> None:
        args_inter = common.hw_video_args(
            "h264_videotoolbox", crf=12, draft=False, vt_prio_speed=True
        )
        args_final = common.hw_video_args(
            "h264_videotoolbox", crf=18, draft=False, vt_prio_speed=True
        )
        self.assertIn("-prio_speed", args_inter)
        self.assertEqual(args_inter[args_inter.index("-prio_speed") + 1], "1")
        self.assertNotIn("-prio_speed", args_final)

    def test_gpu_report_rosetta_hint_on_x64_apple_silicon(self) -> None:
        fake_proc = SimpleNamespace(stdout="1\n")
        with (
            mock.patch.object(common, "IS_MAC", True),
            mock.patch.object(common, "IS_WIN", False),
            mock.patch.object(common, "compute_device", return_value="cpu"),
            mock.patch.object(common, "hardware_encoder", return_value="h264_videotoolbox"),
            mock.patch.object(common.subprocess, "run", return_value=fake_proc),
            mock.patch.dict("os.environ", {"ZENO_DEVICE": "auto"}, clear=False),
        ):
            rep = common.gpu_report()
        self.assertEqual(rep["device"], "cpu")
        self.assertIn("arm64 DMG", rep["hint"])

    def test_render_segment_layout_enables_hw_decode_only_on_nvenc(self) -> None:
        captured: list[dict] = []

        def _fake_encode(*args, **kwargs):
            captured.append(kwargs)
            return SimpleNamespace(returncode=0)

        with (
            mock.patch.object(
                layouts,
                "build_segment_ffmpeg_args",
                return_value=(["-i", "in.mp4", "-c:v", "libx264", "out.mkv"], 2.0, 60),
            ),
            mock.patch.object(layouts, "ffmpeg_encode", side_effect=_fake_encode),
        ):
            with mock.patch.object(layouts, "hardware_encoder", return_value="h264_nvenc"):
                layouts.render_segment_layout({}, {"use_hw_accel": True}, 1080, 1920, 30, ROOT / "work" / "_tmp_nv.mkv")
            with mock.patch.object(layouts, "hardware_encoder", return_value="h264_videotoolbox"):
                layouts.render_segment_layout({}, {"use_hw_accel": True}, 1080, 1920, 30, ROOT / "work" / "_tmp_vt.mkv")

        self.assertTrue(captured[0]["hw_decode"])
        self.assertFalse(captured[1]["hw_decode"])

    def test_scroll_page_encode_args_gpu_and_cpu_fallback(self) -> None:
        with (
            mock.patch.object(scroll_page, "hardware_encoder", return_value="h264_videotoolbox"),
            mock.patch.object(scroll_page, "hw_video_args", return_value=["-c:v", "h264_videotoolbox", "-prio_speed", "1"]),
        ):
            hw_args = scroll_page._page_video_encode_args(draft=False, use_hw=True)
        self.assertIn("h264_videotoolbox", hw_args)

        cpu_args = scroll_page._page_video_encode_args(draft=False, use_hw=False)
        self.assertIn("libx264", cpu_args)

        with mock.patch.object(scroll_page, "hardware_encoder", return_value=""):
            no_hw_args = scroll_page._page_video_encode_args(draft=False, use_hw=True)
        self.assertIn("libx264", no_hw_args)


if __name__ == "__main__":
    unittest.main()
