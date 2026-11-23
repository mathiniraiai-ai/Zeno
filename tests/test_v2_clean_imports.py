#!/usr/bin/env python3
"""Regression tests for Zeno v2.0.0 clean imports, zero-MCP config, delivery root, and CLI flags."""
from __future__ import annotations

import ast
import importlib
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import types
import unittest
import wave
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = ROOT / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

DELETED_1X_MODULES = (
    "zeno_mcp",
    "ai_director",
    "direct_mode",
    "sol_direct",
    "request_budget",
    "editorial_retrieval",
    "cost_control",
    "chat_guard",
    "call_cap",
    "hooks_setup",
    "launch_edit",
    "preproduce",
    "session_check",
    "eval_alignment",
    "brain_memory",
    "format_learning",
    "style_readiness",
    "team_patterns",
    "knowledge_sync",
    "editor_note",
    "learn_reference",
)


class V2CleanImportsTests(unittest.TestCase):
    def test_deleted_1x_modules_absent_and_unimported(self) -> None:
        """Deleted 1.x modules must not exist in `scripts/` or be imported by any `scripts/*.py` module."""
        deleted_set = set(DELETED_1X_MODULES)
        for mod_name in DELETED_1X_MODULES:
            self.assertFalse(
                (SCRIPTS_DIR / f"{mod_name}.py").exists(),
                f"Legacy 1.x module still present: scripts/{mod_name}.py",
            )

        py_files = sorted(SCRIPTS_DIR.glob("*.py"))
        self.assertGreaterEqual(len(py_files), 30)
        for py_path in py_files:
            src = py_path.read_text(encoding="utf-8")
            tree = ast.parse(src, filename=str(py_path))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        top = alias.name.split(".", 1)[0]
                        self.assertNotIn(
                            top,
                            deleted_set,
                            f"{py_path.name} still imports deleted 1.x module '{alias.name}'",
                        )
                elif isinstance(node, ast.ImportFrom) and node.module:
                    top = node.module.split(".", 1)[0]
                    self.assertNotIn(
                        top,
                        deleted_set,
                        f"{py_path.name} still imports from deleted 1.x module '{node.module}'",
                    )

    def test_all_scripts_syntax_and_clean_import(self) -> None:
        """Every top-level `scripts/*.py` file compiles and imports cleanly without `ModuleNotFoundError`."""
        py_files = sorted(SCRIPTS_DIR.glob("*.py"))
        with mock.patch.dict(os.environ, {"_ZENO_REEXEC": "1"}):
            for py_path in py_files:
                src = py_path.read_text(encoding="utf-8")
                compile(src, str(py_path), "exec")
                mod = importlib.import_module(py_path.stem)
                self.assertIsNotNone(mod, f"Failed to import scripts/{py_path.name}")

        for dead_mod in DELETED_1X_MODULES:
            self.assertNotIn(dead_mod, sys.modules, f"Deleted 1.x module leaked into sys.modules: {dead_mod}")

    def test_common_delivery_base_dir_defaults_and_override(self) -> None:
        """`common._delivery_base_dir()` defaults to `~/Downloads/Zeno Outs` and honors `ZENO_OUTPUT_DIR`."""
        import common

        with mock.patch.dict(os.environ, {"ZENO_OUTPUT_DIR": ""}):
            expected_default = Path.home() / "Downloads" / "Zeno Outs"
            self.assertEqual(common._delivery_base_dir(), expected_default)

        with tempfile.TemporaryDirectory(prefix="zeno_out_override_") as td:
            custom = Path(td) / "Custom Zeno Outs"
            with mock.patch.dict(os.environ, {"ZENO_OUTPUT_DIR": str(custom)}):
                self.assertEqual(common._delivery_base_dir(), custom)

    def test_v2_zero_mcp_and_agents_claude_parity(self) -> None:
        """`AGENTS.md == CLAUDE.md`, `.mcp.json == {}`, and `.claude/settings.json` denies `mcp__*`."""
        agents_bytes = (ROOT / "AGENTS.md").read_bytes()
        claude_bytes = (ROOT / "CLAUDE.md").read_bytes()
        self.assertGreater(len(agents_bytes), 500)
        self.assertEqual(agents_bytes, claude_bytes, "AGENTS.md and CLAUDE.md must be byte-identical")

        mcp_cfg = json.loads((ROOT / ".mcp.json").read_text(encoding="utf-8"))
        self.assertEqual(mcp_cfg, {}, f"Expected empty .mcp.json in v2.0.0 Host-AI Direct mode, got: {mcp_cfg}")

        settings_cfg = json.loads((ROOT / ".claude" / "settings.json").read_text(encoding="utf-8"))
        self.assertFalse(settings_cfg.get("enableAllProjectMcpServers", True))
        self.assertEqual(
            str((settings_cfg.get("env") or {}).get("ENABLE_CLAUDEAI_MCP_SERVERS", "")).lower(),
            "false",
        )
        deny_list = (settings_cfg.get("permissions") or {}).get("deny") or []
        self.assertIn("mcp__*", deny_list, f"Missing mcp__* deny rule in .claude/settings.json: {deny_list}")

    def test_zeno_cli_help_v2_flags_and_no_auto_plan(self) -> None:
        """`zeno.py --help` removes `--auto-plan` and exposes `--queue`, `--inspect-only`, `--execute`, `--compact`, `--square`."""
        proc = subprocess.run(
            [sys.executable, str(SCRIPTS_DIR / "zeno.py"), "--help"],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            check=True,
            timeout=30,
        )
        help_out = (proc.stdout or "") + "\n" + (proc.stderr or "")
        self.assertNotIn("--auto-plan", help_out)
        for flag in ("--queue", "--inspect-only", "--execute", "--compact", "--square"):
            self.assertIn(flag, help_out, f"Missing expected v2.0.0 CLI flag {flag} in zeno.py --help")

    def test_gemini_transcribe_model_order_prompt_sync_and_speaker_script_lines(self) -> None:
        """Full Flash models lead transcription order, prompt stays synced with anti-echo guard,
        `_prompt_version` hashes `MODELS`, and `_parse_script_path_to_lines` emits clean speaker-prefixed dialogue.
        """
        import tempfile
        from unittest import mock

        if str(SCRIPTS_DIR) not in sys.path:
            sys.path.insert(0, str(SCRIPTS_DIR))
        service_dir = str(ROOT / "service")
        if service_dir not in sys.path:
            sys.path.insert(0, service_dir)

        from align_engine import _parse_script_path_to_lines
        from align_engine.gemini_transcriber import GeminiTranscriber, build_transcribe_prompt
        import prompt as service_prompt_mod
        import app as service_app_mod

        expected_top3 = ["gemini-3.1-flash-lite", "gemini-3.5-flash-lite", "gemini-2.5-flash"]
        with mock.patch.dict(os.environ, {"GEMINI_FORCE_MODEL": ""}, clear=False):
            gt = GeminiTranscriber(api_keys=[])
        self.assertEqual(gt.models[:3], expected_top3)
        self.assertEqual(service_app_mod.MODELS[:3], expected_top3)

        sample_lines = [
            'Female Robot: No! Say: "I\'m Rahul."',
            "Rahul: I'm Rahul.",
            "Rahul: I'm Rahul.",
        ]
        p_repo = build_transcribe_prompt(sample_lines, language_hint="te")
        p_svc = service_prompt_mod.build_transcribe_prompt(sample_lines, language_hint="te")
        self.assertEqual(p_repo, p_svc)
        self.assertIn("STRICT ANTI-ECHO & SINGLE-CLIP GUARD", p_repo)
        self.assertIn("FULL MULTI-SPEAKER SCRIPT", p_repo)
        self.assertEqual(p_repo.count("Rahul: I'm Rahul."), 1)
        self.assertNotIn('"words": ["word1"', p_repo)

        v1 = service_app_mod._prompt_version()
        orig_models = list(service_app_mod.MODELS)
        try:
            service_app_mod.MODELS = ["gemini-3.6-flash", "gemini-3.5-flash"]
            v2 = service_app_mod._prompt_version()
        finally:
            service_app_mod.MODELS = orig_models
        self.assertNotEqual(v1, v2)

        raw_script = "\n".join([
            "Reference: https://drive.google.com/drive/folders/abc",
            "First Frames: 4 verified images",
            "https://drive.google.com/drive/folders/xyz",
            "Characters",
            "* Rahul -- Man, late 20s",
            "* Female Robot",
            "Scenes Breakdown",
            "Scene 1",
            "Flashback. Rahul sits in an interview.",
            'Female Robot: No! Say: "I\'m Rahul."',
            "Rahul: I'm Rahul.",
        ])
        with tempfile.TemporaryDirectory() as td:
            sp = Path(td) / "script.txt"
            sp.write_text(raw_script, encoding="utf-8")
            parsed_lines = _parse_script_path_to_lines(sp)
        self.assertTrue(all("drive.google.com" not in ln for ln in parsed_lines), parsed_lines)
        self.assertTrue(all("Scenes Breakdown" not in ln for ln in parsed_lines), parsed_lines)
        self.assertTrue(any(ln.startswith("Female Robot:") for ln in parsed_lines), parsed_lines)
        self.assertTrue(any(ln.startswith("Rahul:") for ln in parsed_lines), parsed_lines)

    def test_frontend_supernova_ai_branding_and_release_notes_footer(self) -> None:
        """Desktop renderer, Electron main, OTA packager, and web login page carry Supernova AI branding and release-notes footer."""
        import release

        index_html = (ROOT / "app" / "renderer" / "index.html").read_text(encoding="utf-8")
        self.assertIn("A product by Supernova AI", index_html)
        self.assertNotIn(">A product by Supernova<", index_html)
        for el_id in ('id="releaseFooter"', 'id="releaseKicker"', 'id="releaseNotes"'):
            self.assertIn(el_id, index_html, f"Missing {el_id} in app/renderer/index.html")

        styles_css = (ROOT / "app" / "renderer" / "styles.css").read_text(encoding="utf-8")
        footer_block = re.search(r"\.release-footer\s*\{([^}]*)\}", styles_css)
        self.assertIsNotNone(footer_block, "Missing .release-footer rule in app/renderer/styles.css")
        self.assertIn("bottom:", footer_block.group(1))
        self.assertIn("-webkit-line-clamp: 2", styles_css)

        app_js = (ROOT / "app" / "renderer" / "app.js").read_text(encoding="utf-8")
        self.assertIn("function renderReleaseNotes()", app_js)
        self.assertIn("renderReleaseNotes();", app_js)

        main_js = (ROOT / "app" / "main.js").read_text(encoding="utf-8")
        self.assertIn("release_notes:", main_js)
        self.assertIn("RELEASE_NOTES.txt", main_js)

        preload_js = (ROOT / "app" / "preload.js").read_text(encoding="utf-8")
        self.assertIn("enrichInfo", preload_js)
        self.assertIn("triggerPendingCleanup", preload_js)
        self.assertIn("--after-update", preload_js)
        self.assertIn("release_notes:", preload_js)
        self.assertIn("RELEASE_NOTES.txt", preload_js)

        notes_path = ROOT / "RELEASE_NOTES.txt"
        self.assertTrue(notes_path.is_file(), "RELEASE_NOTES.txt must exist at repo root")
        self.assertTrue(notes_path.read_text(encoding="utf-8").strip(), "RELEASE_NOTES.txt must be non-empty")
        self.assertIn("RELEASE_NOTES.txt", release.TOP_FILES)

        ui_py = (ROOT / "service" / "ui.py").read_text(encoding="utf-8")
        self.assertIn("A product by Supernova AI", ui_py)
        self.assertIn("<!-- Generate Zeno Session -->", ui_py)

    def test_server_gemini_free_tier_queue_buffer_and_cooldown_guards(self) -> None:
        """Server defaults to Gemini Free Tier limits, distinguishes RPM vs RPD 429s, rotates ready Flash ahead of Lite, and fails fast when all models are cooled >20s."""
        service_dir = str(ROOT / "service")
        if service_dir not in sys.path:
            sys.path.insert(0, service_dir)
        import app as service_app_mod
        from google import genai

        clean_env = {
            "GEMINI_TIER": "",
            "GEMINI_CONCURRENCY": "",
            "GEMINI_MIN_GAP_SEC": "",
            "GEMINI_FLASH_RPM": "",
            "GEMINI_LITE_RPM": "",
            "GEMINI_TPM": "",
            "GEMINI_RPM": "",
            "GEMINI_API_KEY": "test-free-tier-key",
            "GEMINI_API_KEY_1": "",
            "GEMINI_API_KEY_2": "",
            "GEMINI_API_KEY_3": "",
            "GEMINI_API_KEY_4": "",
        }
        with mock.patch.dict(os.environ, clean_env, clear=False):
            self.assertEqual(service_app_mod._gemini_tier(), "free")
            self.assertEqual(service_app_mod._min_global_gap_sec(), 3.0)

            flash_rpm, flash_tpm, flash_spacing = service_app_mod._model_limits("gemini-3.5-flash")
            self.assertEqual((flash_rpm, flash_tpm), (5, 240000))
            self.assertGreaterEqual(flash_spacing, 12.5)

            lite_rpm, lite_tpm, lite_spacing = service_app_mod._model_limits("gemini-3.1-flash-lite")
            self.assertEqual((lite_rpm, lite_tpm), (15, 240000))
            self.assertGreaterEqual(lite_spacing, 4.2)

            gem = service_app_mod._Gemini()
            self.assertEqual(gem._concurrency_limit(), 1)

            rpm_429 = RuntimeError(
                "429 RESOURCE_EXHAUSTED GenerateRequestsPerMinutePerProjectPerModel-FreeTier You exceeded your current quota"
            )
            rpd_429 = RuntimeError(
                "429 RESOURCE_EXHAUSTED RequestsPerDayPerProjectPerModel-FreeTier per day"
            )
            self.assertFalse(gem._is_daily_quota(rpm_429))
            self.assertTrue(gem._is_daily_quota(rpd_429))

            # Just-used gemini-3.5-flash rotates cleanly to immediately-ready gemini-3.6-flash while Lite stays behind ready Full Flash models.
            with gem.lock:
                gem._record_call_locked(time.time() - 3.5, 0, "gemini-3.5-flash")
            self.assertEqual(gem.model_wait_seconds(0, "gemini-3.6-flash"), 0.0)
            self.assertGreater(gem.model_wait_seconds(0, "gemini-3.5-flash"), 5.0)
            self.assertEqual(
                gem.candidate_models(["gemini-3.5-flash", "gemini-3.6-flash", "gemini-3.1-flash-lite"]),
                ["gemini-3.6-flash", "gemini-3.5-flash", "gemini-3.1-flash-lite"],
            )

            gem.record_call(0, "gemini-3.1-flash-lite")
            ordered_full = gem.candidate_models()
            self.assertEqual(ordered_full[0], "gemini-2.5-flash")
            self.assertLess(ordered_full.index("gemini-2.5-flash"), ordered_full.index("gemini-3.5-flash-lite"))
            self.assertLess(ordered_full.index("gemini-3.5-flash-lite"), ordered_full.index("gemini-3.1-flash-lite"))

            # All-cooled soonest > 20s raises HTTPException(429) immediately without calling upstream Gemini.
            now = time.time()
            with gem.lock:
                for model_name in service_app_mod.MODELS:
                    gem.cooldowns[(0, model_name)] = now + 45.0
            with mock.patch.object(
                genai, "Client", side_effect=AssertionError("Upstream Gemini must not be called")
            ) as m_client:
                t0 = time.monotonic()
                with self.assertRaises(service_app_mod.HTTPException) as exc_ctx:
                    gem.transcribe(b"RIFFfakewav", "prompt", 2.0, owner="tester")
                elapsed = time.monotonic() - t0
            self.assertEqual(exc_ctx.exception.status_code, 429)
            self.assertGreaterEqual(int((exc_ctx.exception.headers or {}).get("Retry-After", "0")), 20)
            self.assertLess(elapsed, 0.5)
            m_client.assert_not_called()
            self.assertEqual(gem._active_slots, 0)

    def test_client_gemini_free_tier_limiter_and_prepare_rule3_no_retry(self) -> None:
        """Client GeminiRateLimiter enforces Free Tier defaults, parses RPM/RPD/retry-after, and prepare.ensure_clip_transcribed honors Rule 3 no-retry and quota circuit-breaker."""
        from align_engine.gemini_transcriber import GeminiRateLimiter, GeminiTranscriber
        import prepare

        clean_env = {
            "GEMINI_TIER": "",
            "GEMINI_FLASH_RPM": "",
            "GEMINI_LITE_RPM": "",
            "GEMINI_MIN_GAP_SEC": "",
            "ZENO_CLOUD_CLIP_GAP_SEC": "",
            "GEMINI_TPM": "",
            "ZENO_EVENTS": "off",
        }
        with mock.patch.dict(os.environ, clean_env, clear=False):
            self.assertEqual(GeminiRateLimiter._get_tier(), "free")
            self.assertEqual(GeminiRateLimiter._flash_rpm(), 5)
            self.assertEqual(GeminiRateLimiter._lite_rpm(), 15)
            self.assertGreaterEqual(GeminiRateLimiter._min_gap_sec(), 3.0)
            self.assertGreaterEqual(GeminiRateLimiter._cloud_clip_gap_sec(), 3.0)

            lim = GeminiRateLimiter()
            self.assertEqual(lim.rpm_for_model("gemini-3.5-flash"), 5)
            self.assertEqual(lim.rpm_for_model("gemini-3.1-flash-lite"), 15)
            self.assertGreaterEqual(lim.spacing_for_model("gemini-3.5-flash"), 12.5)
            self.assertGreaterEqual(lim.spacing_for_model("gemini-3.1-flash-lite"), 4.2)

            rpm_429 = RuntimeError(
                "429 RESOURCE_EXHAUSTED GenerateRequestsPerMinutePerProjectPerModel-FreeTier You exceeded your current quota"
            )
            rpd_429 = RuntimeError(
                "429 RESOURCE_EXHAUSTED RequestsPerDayPerProjectPerModel-FreeTier per day"
            )
            self.assertFalse(GeminiTranscriber._is_daily_quota(rpm_429))
            self.assertTrue(GeminiTranscriber._is_daily_quota(rpd_429))
            self.assertEqual(GeminiTranscriber._parse_retry_after("Please retry in 24.5s."), 24.5)
            self.assertEqual(GeminiTranscriber._parse_retry_after("Please retry in 1m15s"), 75.0)
            self.assertEqual(GeminiTranscriber._parse_retry_after("{'retryDelay': '42s'}"), 42.0)
            self.assertEqual(GeminiTranscriber._parse_retry_after("Retry-After: 18"), 18.0)
            self.assertIsNone(GeminiTranscriber._parse_retry_after("unrelated failure"))

            with tempfile.TemporaryDirectory(prefix="zeno_prep_rule3_") as td:
                td_path = Path(td)

                def _make_clip_work(name: str) -> Path:
                    wdir = td_path / name
                    wdir.mkdir(parents=True, exist_ok=True)
                    (wdir / "info.json").write_text(
                        json.dumps({
                            "path": f"{name}.mp4",
                            "duration": 2.0,
                            "width": 720,
                            "height": 1280,
                            "fps": 24.0,
                            "has_audio": True,
                        }),
                        encoding="utf-8",
                    )
                    # Non-silent 0.5s 16 kHz mono PCM WAV so clip_has_no_sound() returns False
                    with wave.open(str(wdir / "audio16k.wav"), "wb") as wf:
                        wf.setnchannels(1)
                        wf.setsampwidth(2)
                        wf.setframerate(16000)
                        wf.writeframes(b"\xa0\x0f" * 8000)
                    return wdir

                # Rule 3 genuine non-speech (<0.8s VAD): {"status": "success", "words": []} -> 1 call only, no retry, no transcribe.py spawn
                wdir_rule3 = _make_clip_work("music_clip")
                mock_transcribe = mock.Mock(return_value={"status": "success", "words": []})
                fake_align = types.SimpleNamespace(transcribe_and_align_clip=mock_transcribe)
                with (
                    mock.patch.object(prepare, "align_engine", fake_align),
                    mock.patch.object(prepare, "_speech_spans_of", return_value=[(0.1, 0.5)]),
                    mock.patch.object(
                        prepare.subprocess,
                        "run",
                        side_effect=AssertionError("transcribe.py must not be spawned"),
                    ) as m_run,
                ):
                    info_r3, hq_r3, base_r3 = prepare.ensure_clip_transcribed(
                        "music_clip",
                        str(td_path / "missing_src.mp4"),
                        wdir_rule3,
                        "en",
                        None,
                    )
                self.assertEqual(mock_transcribe.call_count, 1)
                m_run.assert_not_called()
                self.assertEqual(info_r3.get("_transcript_status"), "success")
                self.assertEqual(hq_r3, [])
                self.assertEqual(base_r3, [])
                ev_r3 = json.loads((wdir_rule3 / "engine_version.json").read_text(encoding="utf-8"))
                self.assertNotEqual(ev_r3.get("transcript"), "incomplete")

                # Active human speech (>=0.8s VAD) with empty words MUST NOT cache confirmed_no_speech:
                wdir_speech_empty = _make_clip_work("spoken_empty_clip")
                mock_transcribe_speech = mock.Mock(return_value={"status": "success", "words": []})
                fake_align_speech = types.SimpleNamespace(transcribe_and_align_clip=mock_transcribe_speech)
                with (
                    mock.patch.object(prepare, "align_engine", fake_align_speech),
                    mock.patch.object(prepare, "_speech_spans_of", return_value=[(0.1, 1.6)]),
                    mock.patch.object(
                        prepare.subprocess,
                        "run",
                        side_effect=AssertionError("transcribe.py must not be spawned"),
                    ),
                ):
                    _info_se, hq_se, _base_se = prepare.ensure_clip_transcribed(
                        "spoken_empty_clip",
                        str(td_path / "missing_src_se.mp4"),
                        wdir_speech_empty,
                        "kn",
                        None,
                    )
                self.assertEqual(mock_transcribe_speech.call_count, 2)
                self.assertEqual(hq_se, [])
                meta_se = json.loads((wdir_speech_empty / "transcript_meta.json").read_text(encoding="utf-8"))
                self.assertEqual(meta_se.get("status"), "incomplete")

                # Circuit-breaker: skip_remote_transcription=True -> quota_skipped without calling align_engine
                wdir_skip = _make_clip_work("queued_clip")
                mock_transcribe_skip = mock.Mock(
                    side_effect=AssertionError("align_engine must not be called when circuit-breaker active")
                )
                fake_align_skip = types.SimpleNamespace(transcribe_and_align_clip=mock_transcribe_skip)
                with (
                    mock.patch.object(prepare, "align_engine", fake_align_skip),
                    mock.patch.object(
                        prepare.subprocess,
                        "run",
                        side_effect=AssertionError("transcribe.py must not be spawned"),
                    ) as m_run_skip,
                ):
                    info_sk, hq_sk, base_sk = prepare.ensure_clip_transcribed(
                        "queued_clip",
                        str(td_path / "missing_src2.mp4"),
                        wdir_skip,
                        "en",
                        None,
                        skip_remote_transcription=True,
                        skip_reason="daily quota exhausted",
                    )
                mock_transcribe_skip.assert_not_called()
                m_run_skip.assert_not_called()
                self.assertEqual(info_sk.get("_transcript_status"), "quota_skipped")
                self.assertIn("daily quota exhausted", info_sk.get("_transcript_error", ""))
                self.assertEqual(hq_sk, [])
                self.assertEqual(base_sk, [])

    def test_codex_network_sandbox_config_trust_and_preflight_hint(self) -> None:
        """`.codex/config.toml` and `edit-codex.*` allow Zeno Cloud + Google Docs network access,
        `clean._ensure_codex_project_trust` safely trusts Zeno in `~/.codex/config.toml` (including Windows paths),
        and `preflight.check_transcription_auth` surfaces `CODEX_SANDBOX_NETWORK_DISABLED=1` escalation guidance.
        """
        import tempfile
        import tomllib
        from unittest import mock

        if str(SCRIPTS_DIR) not in sys.path:
            sys.path.insert(0, str(SCRIPTS_DIR))
        import auth_session
        import clean
        import preflight

        cfg = tomllib.loads((ROOT / ".codex" / "config.toml").read_text(encoding="utf-8"))
        self.assertEqual(cfg.get("sandbox_mode"), "danger-full-access")
        self.assertTrue((cfg.get("sandbox_workspace_write") or {}).get("network_access"))
        self.assertTrue((cfg.get("features") or {}).get("multi_agent"))

        for launcher in ("edit-codex.bat", "edit-codex.command"):
            body = (ROOT / launcher).read_text(encoding="utf-8")
            self.assertIn("danger-full-access", body)
            self.assertIn("sandbox_workspace_write.network_access=true", body)

        with tempfile.TemporaryDirectory(prefix="zeno_codex_trust_") as td:
            fake_home = Path(td) / "home"
            (fake_home / ".codex").mkdir(parents=True)
            win_root = Path(r"C:\Users\Admin\Documents\Zeno")
            added = clean._ensure_codex_project_trust(win_root, home_dir=fake_home)
            self.assertTrue(added)
            parsed = tomllib.loads((fake_home / ".codex" / "config.toml").read_text(encoding="utf-8"))
            self.assertEqual(parsed["projects"][str(win_root)]["trust_level"], "trusted")
            # Idempotent re-run does not duplicate or rewrite
            self.assertFalse(clean._ensure_codex_project_trust(win_root, home_dir=fake_home))

        with (
            mock.patch.dict(os.environ, {"CODEX_SANDBOX_NETWORK_DISABLED": "1", "ZENO_NO_LIVE": "0"}, clear=False),
            mock.patch.object(auth_session, "get_session_info", return_value={"authenticated": True, "mode": "zeno_cloud", "source": ".zeno_session.json"}),
            mock.patch.object(auth_session, "get_active_token", return_value="tok"),
            mock.patch.object(auth_session, "get_cloud_url", return_value="https://zeno-production-bef7.up.railway.app"),
            mock.patch.object(preflight, "_collect_local_gemini_keys", return_value=[]),
            mock.patch("httpx.post", side_effect=OSError("DNS lookup failed")),
        ):
            checks = preflight.check_transcription_auth()
            self.assertEqual(len(checks), 1)
            self.assertFalse(checks[0][0])
            self.assertIn("require_escalated", checks[0][2])
            self.assertIn("Full access", checks[0][2])


if __name__ == "__main__":
    unittest.main()
