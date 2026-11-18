"""Tests for Step 1 OTA cleaner & `_zeno_manifest.json` mirror sync (`v1.0.23` bridge -> `v2.0.0`)."""
from __future__ import annotations

import hashlib
import io
import json
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))


def _build_synthetic_bundle(
    version: str,
    files: dict[str, bytes | str],
    managed_dirs: list[str],
    managed_top: list[str],
    obsolete: list[str] | None = None,
    clear_work: bool = False,
) -> tuple[bytes, str]:
    manifest = {
        "schema": 1,
        "version": version,
        "managed_dirs": managed_dirs,
        "managed_top_files": managed_top,
        "obsolete_paths": obsolete or [],
        "clear_work_projects": clear_work,
        "files": sorted(files.keys()),
    }
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for rel, body in files.items():
            zf.writestr(rel, body)
        zf.writestr("_zeno_manifest.json", json.dumps(manifest, indent=2))
    blob = buf.getvalue()
    return blob, hashlib.sha256(blob).hexdigest()


class OtaManifestCleanerTests(unittest.TestCase):
    def test_release_bundle_embeds_manifest_with_formats_and_managed_dirs(self):
        import release

        blob, _count, _sha = release.build_bundle_bytes(version="1.0.23", clear_work_projects=True)
        with zipfile.ZipFile(io.BytesIO(blob), "r") as zf:
            names = set(zf.namelist())
            self.assertIn("_zeno_manifest.json", names)
            man = json.loads(zf.read("_zeno_manifest.json").decode("utf-8"))
        self.assertEqual(man["version"], "1.0.23")
        self.assertTrue(man["clear_work_projects"])
        self.assertIn("formats", man["managed_dirs"])
        self.assertIn("scripts", man["managed_dirs"])
        self.assertIn("assets/vision/face_detection_yunet_2023mar.onnx", man["files"])
        self.assertNotIn("_zeno_manifest.json", man["files"])

    def test_minor_ota_prunes_stale_managed_files_and_keeps_runtime_deps_and_user_data(self):
        import auto_updater

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "Zeno"
            (root / "scripts").mkdir(parents=True)
            (root / "subtitles" / "node_modules" / "canvas").mkdir(parents=True)
            (root / "work" / "_clips" / "C1").mkdir(parents=True)
            (root / "work" / "old_proj").mkdir(parents=True)
            (root / "input" / "footage").mkdir(parents=True)
            (root / "models").mkdir(parents=True)
            (root / ".venv" / "bin").mkdir(parents=True)

            (root / "VERSION").write_text("1.0.22\n", encoding="utf-8")
            (root / "LOW_COST_WORKFLOW.md").write_text("old doc\n", encoding="utf-8")
            (root / "scripts" / "zeno.py").write_text("# old\n", encoding="utf-8")
            (root / "scripts" / "stale_helper.py").write_text("# remove me\n", encoding="utf-8")
            (root / "scripts" / "legacy_sub").mkdir()
            (root / "scripts" / "legacy_sub" / "dead.py").write_text("# nested stale\n", encoding="utf-8")
            (root / "subtitles" / "node_modules" / "canvas" / "index.js").write_text("module.exports={};\n", encoding="utf-8")
            (root / ".env").write_text("TOKEN=keep\n", encoding="utf-8")
            (root / ".zeno_session.json").write_text('{"token":"keep"}\n', encoding="utf-8")
            (root / ".zeno_owner_session.json").write_text('{"token":"owner"}\n', encoding="utf-8")
            (root / "work" / "_clips" / "C1" / "hq_words.json").write_text("[]", encoding="utf-8")
            (root / "work" / "old_proj" / "plan.edl").write_text("# keep on minor\n", encoding="utf-8")
            (root / "input" / "footage" / "clip.mp4").write_bytes(b"video")
            (root / "models" / "yunet.onnx").write_bytes(b"onnx")
            (root / ".venv" / "bin" / "python").write_text("", encoding="utf-8")

            blob, sha = _build_synthetic_bundle(
                version="1.0.23",
                files={
                    "VERSION": "1.0.23\n",
                    "scripts/zeno.py": "# v1.0.23\n",
                    "subtitles/styles.json": "{}\n",
                },
                managed_dirs=["scripts", "subtitles", "agent", "styles", "formats"],
                managed_top=["VERSION", "LOW_COST_WORKFLOW.md"],
            )
            with patch.object(auto_updater, "ROOT", root), \
                 patch.object(auto_updater, "WORK", root / "work"), \
                 patch.object(auto_updater, "RELEASE_STATE_FILE", root / "work" / ".cloud_release_state.json"):
                res = auto_updater.apply_bundle_bytes(blob, expected_sha256=sha, version="1.0.23", sha="abc")

            self.assertTrue(res["ok"], res)
            self.assertFalse((root / "_zeno_manifest.json").exists())
            self.assertFalse((root / "scripts" / "stale_helper.py").exists())
            self.assertFalse((root / "scripts" / "legacy_sub").exists())
            self.assertFalse((root / "LOW_COST_WORKFLOW.md").exists())
            # Preserved runtime deps, secrets, venv, models, footage, and minor-version project work
            self.assertTrue((root / "subtitles" / "node_modules" / "canvas" / "index.js").is_file())
            self.assertEqual((root / ".env").read_text(encoding="utf-8"), "TOKEN=keep\n")
            self.assertTrue((root / ".zeno_session.json").is_file())
            self.assertTrue((root / ".zeno_owner_session.json").is_file())
            self.assertTrue((root / ".venv" / "bin" / "python").is_file())
            self.assertTrue((root / "models" / "yunet.onnx").is_file())
            self.assertTrue((root / "input" / "footage" / "clip.mp4").is_file())
            self.assertTrue((root / "work" / "_clips" / "C1" / "hq_words.json").is_file())
            self.assertTrue((root / "work" / "old_proj" / "plan.edl").is_file())

    def test_major_2_0_cleanup_via_clean_after_update_wipes_1x_rules_and_keeps_clips(self):
        import clean

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "Zeno"
            for rel in (
                "agent/EDIT_RULES.md",
                "agent/FORMATS.md",
                "styles/micronarrative/SKILL.md",
                "scripts/zeno.py",
                "formats/core_rules.md",
                "work/_clips/C1/hq_words.json",
                "work/gdoc_1old/editing_packet.md",
                "work/_subs/old.png",
                "work/_job_outbox/pending.json",
            ):
                p = root / rel
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_text("data", encoding="utf-8")
            (root / "work" / ".last_cleanup_state.json").write_text(json.dumps({"version": "1.0.23"}), encoding="utf-8")
            (root / ".env").write_text("KEY=1\n", encoding="utf-8")

            # Simulate packaged app.asar extracting 2.0.0 + _zeno_manifest.json, then spawning clean.py --after-update
            manifest = {
                "schema": 1,
                "version": "2.0.0",
                "managed_dirs": ["agent", "styles", "scripts", "formats"],
                "managed_top_files": ["VERSION"],
                "obsolete_paths": ["styles"],
                "clear_work_projects": False,
                "files": ["VERSION", "scripts/zeno.py", "formats/core_rules.md"],
            }
            (root / "VERSION").write_text("2.0.0\n", encoding="utf-8")
            (root / "_zeno_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

            res = clean.cleanup_after_update(root=root, version="2.0.0")
            self.assertTrue(res["ran"], res)
            self.assertTrue(res["clear_work_projects"])
            self.assertFalse((root / "agent").exists())
            self.assertFalse((root / "styles").exists())
            self.assertFalse((root / "work" / "gdoc_1old").exists())
            self.assertFalse((root / "work" / "_subs").exists())
            self.assertFalse((root / "_zeno_manifest.json").exists())
            # Preserved caches and new 2.0.0 core
            self.assertTrue((root / "formats" / "core_rules.md").is_file())
            self.assertTrue((root / "work" / "_clips" / "C1" / "hq_words.json").is_file())
            self.assertTrue((root / "work" / "_job_outbox" / "pending.json").is_file())
            self.assertEqual((root / ".env").read_text(encoding="utf-8"), "KEY=1\n")


if __name__ == "__main__":
    unittest.main()
