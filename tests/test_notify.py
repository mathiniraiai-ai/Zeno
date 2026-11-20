#!/usr/bin/env python3
"""Regression tests for Zeno delivery notifications (`scripts/notify.py` & `scripts/deliver.py`).

Verifies:
1. Bundled `assets/sfx/zeno_notification.mp3` and `assets/sfx/zeno_notification.wav` exist and are non-empty.
2. Strict delivery gate: `notify_delivery` does NOT fire when no finished `.mp4` exists in the output folder.
3. Fires once when a finished `.mp4` lands in the output folder, writing both the per-project stamp
   (`_delivery_notified.json`) and global frontend handoff (`work/_last_delivery.json`).
4. Anti-duplicate lock prevents double-firing for the same delivered video, and re-fires when a new render
   updates the `.mp4` file.
5. Frontend heartbeat (`work/_frontend_alive.json`) cleanly delegates audio/pop-up to the Electron UI
   so desktop users never hear a double chime.
6. `deliver.deliver_to_downloads()` automatically invokes `notify.notify_delivery()` once the video lands
   in `~/Downloads/Zeno Outs/<FolderName>/` (or `$ZENO_OUTPUT_DIR/<FolderName>/`).
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))

import common  # noqa: E402
import deliver  # noqa: E402
import notify  # noqa: E402
import notify_popup  # noqa: E402


class DeliveryNotificationTests(unittest.TestCase):
    def test_01_bundled_notification_audio_assets_exist(self) -> None:
        """Both MP3 (macOS/Electron) and WAV (Windows) notification assets exist and are valid."""
        self.assertTrue(notify.NOTIFICATION_MP3.is_file(), "Missing assets/sfx/zeno_notification.mp3")
        self.assertTrue(notify.NOTIFICATION_WAV.is_file(), "Missing assets/sfx/zeno_notification.wav")
        self.assertGreater(notify.NOTIFICATION_MP3.stat().st_size, 10_000)
        self.assertGreater(notify.NOTIFICATION_WAV.stat().st_size, 10_000)
        self.assertEqual(notify.NOTIFICATION_WAV.read_bytes()[:4], b"RIFF")

    def test_02_strict_gate_skips_when_no_mp4_delivered(self) -> None:
        """Notification must NOT start until a real non-empty `.mp4` exists in the output folder."""
        with tempfile.TemporaryDirectory(prefix="zeno_notify_empty_") as td:
            td_p = Path(td)
            out_dir = td_p / "out" / "Sample_Edit"
            out_dir.mkdir(parents=True)
            work_dir = td_p / "work" / "sample"
            work_dir.mkdir(parents=True)
            (out_dir / "review_sheet.jpg").write_bytes(b"fakejpg")
            (out_dir / "reel_Sample_Edit.mp4").write_bytes(b"")  # 0-byte incomplete file

            with mock.patch.object(notify, "play_notification_sound") as m_snd, \
                 mock.patch.object(notify, "show_desktop_popup") as m_pop:
                res = notify.notify_delivery(out_dir, folder_name="Sample_Edit", work_dir=work_dir, work_root=td_p / "work", force=True)
                self.assertFalse(res["notified"])
                self.assertEqual(res["reason"], "no_video_in_output_folder")
                m_snd.assert_not_called()
                m_pop.assert_not_called()

    def test_03_fires_once_on_video_delivery_and_dedups_via_lock(self) -> None:
        """Once a real `.mp4` lands in the output folder, notification fires once, writes stamps,
        skips duplicate calls for the same build, and re-fires on a fresh render.
        """
        with tempfile.TemporaryDirectory(prefix="zeno_notify_ok_") as td:
            td_p = Path(td)
            out_dir = td_p / "out" / "My_Reel"
            out_dir.mkdir(parents=True)
            work_root = td_p / "work"
            work_dir = work_root / "my_reel"
            work_dir.mkdir(parents=True)
            video = out_dir / "reel_My_Reel.mp4"
            video.write_bytes(b"\x00\x00\x00\x20ftypisom" + b"0" * 1024)

            with mock.patch.object(notify, "play_notification_sound", return_value=True) as m_snd, \
                 mock.patch.object(notify, "show_desktop_popup", return_value=True) as m_pop:
                # 1st call -> fires
                r1 = notify.notify_delivery(
                    out_dir, folder_name="My_Reel", work_dir=work_dir, work_root=work_root, force=True
                )
                self.assertTrue(r1["notified"])
                self.assertTrue(r1["sound_played"])
                self.assertTrue(r1["popup_shown"])
                self.assertEqual(m_snd.call_count, 1)
                self.assertEqual(m_pop.call_count, 1)

                # Verify both per-job stamp and frontend handoff JSON were written
                last_deliv = json.loads((work_root / notify.LAST_DELIVERY_FILE).read_text(encoding="utf-8"))
                self.assertEqual(last_deliv["folder_name"], "My_Reel")
                self.assertEqual(last_deliv["delivery_id"], r1["delivery_id"])

                # 2nd call without force on identical video -> deduplicated by stamp (never double chimes)
                with mock.patch.object(notify, "_is_suppressed", return_value=False):
                    r2 = notify.notify_delivery(
                        out_dir, folder_name="My_Reel", work_dir=work_dir, work_root=work_root, force=False
                    )
                self.assertFalse(r2["notified"])
                self.assertEqual(r2["reason"], "already_notified")
                self.assertEqual(m_snd.call_count, 1)

                # Fresh render updates the video size -> new delivery_id -> fires cleanly again!
                video.write_bytes(b"\x00\x00\x00\x20ftypisom" + b"1" * 2048)
                with mock.patch.object(notify, "_is_suppressed", return_value=False):
                    r3 = notify.notify_delivery(
                        out_dir, folder_name="My_Reel", work_dir=work_dir, work_root=work_root, force=False
                    )
                self.assertTrue(r3["notified"])
                self.assertNotEqual(r3["delivery_id"], r1["delivery_id"])
                self.assertEqual(m_snd.call_count, 2)

    def test_04_frontend_alive_delegates_without_double_chime(self) -> None:
        """When `work/_frontend_alive.json` is fresh, Python writes `_last_delivery.json` and
        delegates playback/popup to the desktop frontend (preventing double notification).
        """
        with tempfile.TemporaryDirectory(prefix="zeno_notify_fe_") as td:
            td_p = Path(td)
            out_dir = td_p / "out" / "FE_Reel"
            out_dir.mkdir(parents=True)
            work_root = td_p / "work"
            work_dir = work_root / "fe_reel"
            work_dir.mkdir(parents=True)
            (out_dir / "reel_FE_Reel.mp4").write_bytes(b"\x00\x00\x00\x20ftypisom" + b"A" * 512)
            (work_root / notify.FRONTEND_HEARTBEAT_FILE).write_text(
                json.dumps({"ts": time.time(), "pid": 12345}), encoding="utf-8"
            )

            with mock.patch.object(notify, "play_notification_sound") as m_snd, \
                 mock.patch.object(notify, "show_desktop_popup") as m_pop:
                res = notify.notify_delivery(
                    out_dir, folder_name="FE_Reel", work_dir=work_dir, work_root=work_root, force=True
                )
                self.assertTrue(res["notified"])
                self.assertTrue(res["delegated_to_frontend"])
                self.assertEqual(res["reason"], "delegated_to_frontend")
                m_snd.assert_not_called()
                m_pop.assert_not_called()
                handoff = json.loads((work_root / notify.LAST_DELIVERY_FILE).read_text(encoding="utf-8"))
                self.assertEqual(handoff["notified_by"], "frontend")

    def test_05_deliver_to_downloads_triggers_notification_after_video_moves(self) -> None:
        """End-to-end check: `deliver.deliver_to_downloads` moves the rendered video into the
        output folder and invokes `notify.notify_delivery` with the delivered folder & file list.
        """
        with tempfile.TemporaryDirectory(prefix="zeno_deliver_notify_") as td:
            td_p = Path(td)
            work_dir = td_p / "work" / "proj1"
            work_dir.mkdir(parents=True)
            staging_dir = td_p / "staging"
            staging_dir.mkdir(parents=True)
            out_root = td_p / "Zeno Outs"
            out_root.mkdir(parents=True)

            staged_mp4 = staging_dir / "reel_Demo_Edit.mp4"
            staged_mp4.write_bytes(b"\x00\x00\x00\x20ftypisom" + b"Z" * 1024)
            plan_json = work_dir / "plan.json"
            plan_json.write_text(json.dumps({"output": str(staged_mp4), "segments": []}), encoding="utf-8")

            with mock.patch.dict(os.environ, {"ZENO_OUTPUT_DIR": str(out_root)}), \
                 mock.patch.object(deliver, "_export_premiere_if_missing"), \
                 mock.patch.object(notify, "notify_delivery", return_value={"notified": True, "reason": "ok"}) as m_not:
                res = deliver.deliver_to_downloads(plan_json, name="Demo Edit")

            expected_folder = (out_root / "Demo Edit").resolve()
            self.assertEqual(Path(res["folder"]).resolve(), expected_folder)
            self.assertIn("reel_Demo Edit.mp4", res["files"])
            self.assertTrue((expected_folder / "reel_Demo Edit.mp4").is_file())
            m_not.assert_called_once()
            call_args, call_kwargs = m_not.call_args
            self.assertEqual(Path(call_args[0]).resolve(), expected_folder)
            self.assertIn("reel_Demo Edit.mp4", call_kwargs["files"])

    def test_06_zeno_themed_popup_html_and_dispatch(self) -> None:
        """`notify_popup.build_toast_html` renders the Zeno `#bacf65` dark theme with escaped
        title, deliverable chips, and `Play Reel` button, and `notify.show_desktop_popup`
        dispatches to `notify_popup.show_themed_popup` when a delivered folder is supplied.
        """
        with tempfile.TemporaryDirectory(prefix="zeno_toast_html_") as td:
            td_p = Path(td)
            vid = td_p / "reel_Test.mp4"
            vid.write_bytes(b"mp4data")
            rendered = notify_popup.build_toast_html(
                title='Reel <Test> & "Demo"',
                folder=td_p,
                video_path=vid,
                files=["reel_Test.mp4", "SQ_Test.mp4", "Test.prproj", "review_sheet.jpg"],
                duration_sec=8.0,
            )
            self.assertIn("#bacf65", rendered)
            self.assertIn("Video ready", rendered)
            self.assertIn("Reel &lt;Test&gt; &amp; &quot;Demo&quot;", rendered)
            self.assertIn("9:16 reel", rendered)
            self.assertIn("1:1 square", rendered)
            self.assertIn("Premiere project", rendered)
            self.assertIn("Saved in", rendered)
            self.assertNotIn("Open Folder", rendered)
            self.assertNotIn("Play Reel", rendered)

            with mock.patch.object(notify_popup, "show_themed_popup", return_value=True) as m_pop:
                ok = notify.show_desktop_popup(
                    "Zeno — Video Ready",
                    "Test Edit",
                    "Saved",
                    folder=td_p,
                    video_path=vid,
                    files=["reel_Test.mp4"],
                )
                self.assertTrue(ok)
                m_pop.assert_called_once()

    def test_07_default_delivery_root_uses_zeno_outs(self) -> None:
        """When `ZENO_OUTPUT_DIR` is unset, deliveries target `~/Downloads/Zeno Outs/<Name>/`."""
        with tempfile.TemporaryDirectory(prefix="zeno_home_outs_") as td:
            td_p = Path(td)
            fake_home = td_p / "home"
            fake_home.mkdir()
            work_dir = td_p / "work" / "proj_outs"
            work_dir.mkdir(parents=True)
            staging_dir = td_p / "staging"
            staging_dir.mkdir()

            staged_mp4 = staging_dir / "reel_Default_Out.mp4"
            staged_mp4.write_bytes(b"\x00\x00\x00\x20ftypisom" + b"Q" * 1024)
            plan_json = work_dir / "plan.json"
            plan_json.write_text(json.dumps({"output": str(staged_mp4), "segments": []}), encoding="utf-8")
            # Seed review_sheet.jpg so deliver doesn't invoke OpenCV on synthetic bytes
            (work_dir / "review_sheet.jpg").write_bytes(b"fakejpg")

            with mock.patch.dict(os.environ, {"ZENO_OUTPUT_DIR": ""}), \
                 mock.patch.object(Path, "home", return_value=fake_home), \
                 mock.patch.object(common, "DOWNLOADS_DIR", fake_home / "Downloads"), \
                 mock.patch.object(deliver, "_export_premiere_if_missing"), \
                 mock.patch.object(notify, "notify_delivery", return_value={"notified": True, "reason": "ok"}):
                expected_base = fake_home / "Downloads" / "Zeno Outs"
                self.assertEqual(common._delivery_base_dir(), expected_base)
                self.assertEqual(deliver._delivery_base_dir(), expected_base)
                self.assertEqual(notify._default_output_root(), expected_base)
                self.assertEqual(notify_popup._default_output_root(), expected_base)

                res = deliver.deliver_to_downloads(plan_json, name="Default Out")

            expected_folder = (fake_home / "Downloads" / "Zeno Outs" / "Default Out").resolve()
            self.assertEqual(Path(res["folder"]).resolve(), expected_folder)
            self.assertTrue((expected_folder / "reel_Default Out.mp4").is_file())
            self.assertFalse((fake_home / "Downloads" / "Default Out").exists())

    def test_08_windows_sound_uses_winsound_create_no_window_and_themed_popup_chimes(self) -> None:
        """Windows chime uses stdlib winsound with CREATE_NO_WINDOW (not flaky DETACHED_PROCESS+PowerShell),
        folderless `show_desktop_popup` routes to `notify_popup.show_themed_popup`, and `show_themed_popup`
        plays the chime by default unless `play_sound=False` (preventing double chime from `notify_delivery`).
        """
        with mock.patch.object(sys, "platform", "win32"), \
             mock.patch.object(notify.subprocess, "Popen") as m_popen, \
             mock.patch.object(notify.subprocess, "CREATE_NO_WINDOW", 0x08000000, create=True), \
             mock.patch.object(notify.subprocess, "DETACHED_PROCESS", 0x00000008, create=True):
            ok = notify.play_notification_sound(wait=False)
            self.assertTrue(ok)
            m_popen.assert_called_once()
            cmd = m_popen.call_args.args[0]
            cflags = m_popen.call_args.kwargs.get("creationflags", 0)
            self.assertIn("winsound", " ".join(cmd))
            self.assertTrue(str(notify.NOTIFICATION_WAV) in cmd)
            self.assertEqual(cflags, 0x08000000)

        # Wait=True calls winsound.PlaySound directly in-process when available
        fake_winsound = mock.MagicMock()
        fake_winsound.SND_FILENAME = 0x20000
        fake_winsound.SND_NODEFAULT = 0x2
        with mock.patch.object(sys, "platform", "win32"), \
             mock.patch.dict(sys.modules, {"winsound": fake_winsound}):
            ok_wait = notify.play_notification_sound(wait=True)
            self.assertTrue(ok_wait)
            fake_winsound.PlaySound.assert_called_once()

        # Folderless update notifications dispatch to notify_popup (not hidden Win11 NotifyIcon balloons)
        with mock.patch.object(notify_popup, "show_themed_popup", return_value=True) as m_pop:
            ok_upd = notify.show_desktop_popup(
                "Zeno v2.0.6 is ready",
                "Updated",
                "Cleaner subtitles and chime fix.",
                folder=None,
                notes="Cleaner subtitles and chime fix.",
                badge="Updated",
                play_sound=True,
            )
            self.assertTrue(ok_upd)
            m_pop.assert_called_once()
            kw = m_pop.call_args.kwargs
            self.assertEqual(kw["badge"], "Updated")
            self.assertIn("Cleaner subtitles", kw["notes"])
            self.assertTrue(kw["play_sound"])

        # Direct call to show_themed_popup plays chime cleanly; play_sound=False suppresses it
        with mock.patch.object(notify_popup, "_play_chime", return_value=True) as m_chime, \
             mock.patch.object(sys, "platform", "win32"), \
             mock.patch.object(notify_popup.shutil, "which", return_value="powershell.exe"), \
             mock.patch.object(notify_popup.subprocess, "Popen") as m_wpf_popen:
            shown = notify_popup.show_themed_popup(
                title="Zeno updated to v2.0.6",
                badge="Updated",
                notes="Release summary",
                play_sound=True,
            )
            self.assertTrue(shown)
            m_chime.assert_called_once()
            m_wpf_popen.assert_called_once()

            notify_popup.show_themed_popup(
                title="Silent card",
                badge="Updated",
                notes="No double sound",
                play_sound=False,
            )
            self.assertEqual(m_chime.call_count, 1)

    def test_09_auto_updater_notifies_with_chime_only_after_real_update(self) -> None:
        """`auto_updater._notify_update_installed` pops the themed `Updated` card with chime once per version,
        respecting suppression in tests and stamping `work/_update_state.json` to prevent duplicate desktop toasts.
        """
        import auto_updater

        with tempfile.TemporaryDirectory(prefix="zeno_upd_notify_") as td:
            work_dir = Path(td) / "work"
            work_dir.mkdir(parents=True)
            with mock.patch.object(auto_updater, "WORK", work_dir), \
                 mock.patch.object(notify, "_is_suppressed", return_value=False), \
                 mock.patch.object(notify_popup, "show_themed_popup", return_value=True) as m_pop:
                auto_updater._notify_update_installed("2.0.4", "2.0.6", "Fixed Windows chime playback.")
                m_pop.assert_called_once()
                self.assertEqual(m_pop.call_args.kwargs["badge"], "Updated")
                self.assertTrue(m_pop.call_args.kwargs["play_sound"])
                st = json.loads((work_dir / "_update_state.json").read_text(encoding="utf-8"))
                self.assertEqual(st["toast_version"], "2.0.6")

                # Re-calling for same version does not double-notify
                auto_updater._notify_update_installed("2.0.4", "2.0.6", "Fixed Windows chime playback.")
                self.assertEqual(m_pop.call_count, 1)


if __name__ == "__main__":
    unittest.main()
