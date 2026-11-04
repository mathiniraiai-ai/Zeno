#!/usr/bin/env python3
"""GPU use and keeping the computer cool: the aligner is not forced onto the CPU, and a hot Mac gets time to cool."""
from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import common  # noqa: E402


class TestThermalPause(unittest.TestCase):
    def test_waits_while_the_cpu_is_slowed_then_goes_on(self):
        hot = SimpleNamespace(stdout="CPU_Scheduler_Limit = 100\nCPU_Speed_Limit = 70\n")
        cool = SimpleNamespace(stdout="CPU_Scheduler_Limit = 100\nCPU_Speed_Limit = 100\n")
        with mock.patch.object(common, "IS_MAC", True), \
                mock.patch.object(common.subprocess, "run", side_effect=[hot, hot, cool]), \
                mock.patch("time.sleep") as slept:
            common.thermal_pause(max_wait=60, step=10)
        self.assertEqual(slept.call_count, 2)

    def test_no_wait_when_the_mac_reports_no_limit(self):
        quiet = SimpleNamespace(stdout="Note: No thermal warning level has been recorded\n")
        with mock.patch.object(common, "IS_MAC", True), \
                mock.patch.object(common.subprocess, "run", return_value=quiet), \
                mock.patch("time.sleep") as slept:
            common.thermal_pause()
        slept.assert_not_called()

    def test_nothing_happens_off_the_mac(self):
        with mock.patch.object(common, "IS_MAC", False), mock.patch.object(common.subprocess, "run") as run:
            common.thermal_pause()
        run.assert_not_called()


class TestRunCool(unittest.TestCase):
    def test_off_switch_and_second_call_do_nothing(self):
        with mock.patch.dict(os.environ, {"ZENO_COOL": "0"}, clear=False), mock.patch.object(common.os, "execv") as ex:
            common.run_cool()
        ex.assert_not_called()
        with mock.patch.dict(os.environ, {"_ZENO_COOL": "1"}, clear=False), mock.patch.object(common.os, "execv") as ex:
            common.run_cool()
        ex.assert_not_called()


class TestAlignerDevice(unittest.TestCase):
    def test_the_aligner_is_not_forced_onto_the_cpu(self):
        src = (ROOT / "scripts" / "align_engine" / "__init__.py").read_text(encoding="utf-8")
        self.assertNotIn('get_shared_pipeline(device="cpu")\n    align_res', src)
        self.assertIn("pipe = get_shared_pipeline()", src)


if __name__ == "__main__":
    unittest.main()
