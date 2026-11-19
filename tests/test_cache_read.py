#!/usr/bin/env python3
"""Measure exact `cache_read` for 1 single Host-AI Direct call inside Zeno v2.0.0 (`tests/test_cache_read.py`).

NOTE ON CACHE WRITE vs CACHE READ (AND WHY EFFORT IS ALWAYS LOW):
- Providers (Claude / Codex) decide `cache_write` vs `cache_read` automatically by prefix matching:
  * Call 1 (cold): writes the static workspace/system prefix into the 5-minute prompt cache (`cache_write`),
    so `cache_read` is partial or 0 on the very first request.
  * Call 2+ (warm, within 5 minutes): the prefix is already cached, so the provider performs pure `cache_read`
    (`cache_write = 0`). That warm `cache_read` number is the true per-call cache read cost that repeats across
    10 calls or 40 calls in a session.
- Reasoning/thinking effort is pinned to `low` (`--effort low` / `model_reasoning_effort="low"`) so every call
  stays fast and minimal by default.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))

import edit_brief  # noqa: E402
import edit_packet  # noqa: E402


def _find_cli_binary(name: str) -> str | None:
    """Locate a host AI CLI binary via PATH or standard user/Homebrew bin directories."""
    found = shutil.which(name)
    if found:
        return found
    for cand in (
        Path.home() / ".local" / "bin" / name,
        Path("/opt/homebrew/bin") / name,
        Path("/usr/local/bin") / name,
    ):
        if cand.is_file() and os.access(cand, os.X_OK):
            return str(cand)
    return None


def find_claude_binary() -> str | None:
    return _find_cli_binary("claude")


def find_codex_binary() -> str | None:
    return _find_cli_binary("codex")


def _workspace_system_prompt() -> str:
    for fname in ("CLAUDE.md", "AGENTS.md"):
        p = ROOT / fname
        if p.is_file():
            return p.read_text(encoding="utf-8")
    return ""


def measure_claude_workspace_call(packet_text: str = "") -> dict:
    """Warm cache on Call 1, then measure pure `cache_read` on 1 single Call 2 inside Zeno v2.0.0."""
    exe = find_claude_binary()
    if not exe:
        return {"provider": "claude_workspace", "available": False, "error": "claude CLI not found"}

    prompt = (
        f"{packet_text}\n\nReply with exactly: OK"
        if packet_text
        else "Reply with exactly: OK"
    )
    sys_prompt = _workspace_system_prompt()
    base_cmd = [
        exe,
        "-p",
        "--model",
        os.environ.get("ZENO_CLAUDE_MODEL", "sonnet"),
        "--output-format",
        "json",
        "--setting-sources",
        "project,local",
        "--no-chrome",
        "--effort",
        "low",
        "--max-turns",
        "1",
        "--system-prompt",
        sys_prompt,
        "--tools",
        "Bash",
        "--disable-slash-commands",
        "--strict-mcp-config",
    ]
    env = dict(os.environ, MAX_THINKING_TOKENS="1024", ENABLE_CLAUDEAI_MCP_SERVERS="false")
    env.pop("CLAUDECODE", None)
    env.pop("CLAUDE_CODE_ENTRYPOINT", None)

    def _one_call(extra_args: list[str], msg: str) -> dict:
        r = subprocess.run(
            [*base_cmd, *extra_args, msg],
            cwd=str(ROOT),
            env=env,
            capture_output=True,
            text=True,
            timeout=120,
        )
        data = json.loads((r.stdout or "{}").strip().splitlines()[-1])
        mu = data.get("modelUsage") or {}
        if mu:
            cache_read = sum(int((v or {}).get("cacheReadInputTokens") or 0) for v in mu.values())
            cache_write = sum(int((v or {}).get("cacheCreationInputTokens") or 0) for v in mu.values())
            uncached_in = sum(int((v or {}).get("inputTokens") or 0) for v in mu.values())
            out = sum(int((v or {}).get("outputTokens") or 0) for v in mu.values())
        else:
            u = data.get("usage") or {}
            cache_read = int(u.get("cache_read_input_tokens") or 0)
            cache_write = int(u.get("cache_creation_input_tokens") or 0)
            uncached_in = int(u.get("input_tokens") or 0)
            out = int(u.get("output_tokens") or 0)
        return {
            "session_id": data.get("session_id"),
            "cache_read": cache_read,
            "cache_write": cache_write,
            "input": uncached_in,
            "output": out,
        }

    # Call 1 warms the session cache; Call 2 resumes the session to measure pure 1-turn cache_read.
    cold = _one_call([], prompt)
    sid = cold.get("session_id")
    warm = _one_call(["--resume", sid] if sid else [], "OK")
    return {
        "provider": "claude_workspace",
        "available": True,
        "cache_read": warm["cache_read"],
        "warm_call": warm,
        "cold_call": cold,
    }


def measure_claude_isolated_packet_call(packet_text: str) -> dict:
    """Measure 1 isolated tool-free Host-AI packet call (warm `cache_read` only)."""
    exe = find_claude_binary()
    if not exe:
        return {"provider": "claude_isolated_packet", "available": False}
    prompt = f"{packet_text}\n\nReply with exactly: OK"
    sys_prompt = _workspace_system_prompt()
    env = dict(os.environ, ENABLE_CLAUDEAI_MCP_SERVERS="false", MAX_THINKING_TOKENS="1024")
    env.pop("CLAUDECODE", None)
    env.pop("CLAUDE_CODE_ENTRYPOINT", None)

    def _run(extra: list[str], msg: str) -> dict:
        cmd = [
            exe, "-p", "--model", "sonnet", "--output-format", "json",
            "--system-prompt", sys_prompt,
            "--tools", "", "--strict-mcp-config", "--disable-slash-commands",
            "--setting-sources", "", "--effort", "low", "--max-turns", "1", "--no-chrome",
            *extra, msg,
        ]
        with tempfile.TemporaryDirectory(prefix="zeno_iso_") as tmp:
            r = subprocess.run(cmd, cwd=tmp, env=env, capture_output=True, text=True, timeout=120)
        d = json.loads((r.stdout or "{}").strip().splitlines()[-1])
        mu = d.get("modelUsage") or {}
        cr = sum(int((v or {}).get("cacheReadInputTokens") or 0) for v in mu.values()) if mu else int((d.get("usage") or {}).get("cache_read_input_tokens") or 0)
        return {"session_id": d.get("session_id"), "cache_read": cr}

    cold = _run([], prompt)
    sid = cold.get("session_id")
    warm = _run(["--resume", sid] if sid else [], "OK")
    return {
        "provider": "claude_isolated_packet",
        "available": True,
        "cache_read": warm["cache_read"],
    }


def measure_codex_workspace_call(packet_text: str = "") -> dict:
    """Warm session on Call 1, then resume on Call 2 to measure pure 1-turn `cache_read` in Codex."""
    exe = find_codex_binary()
    if not exe:
        return {"provider": "codex_workspace", "available": False, "error": "codex CLI not found"}

    prompt = (
        f"{packet_text}\n\nReply with exactly: OK"
        if packet_text
        else "Reply with exactly: OK"
    )
    common_flags = [
        "-C", str(ROOT),
        "--skip-git-repo-check",
        "-c", "features.web_search_request=false",
        "-c", "features.multi_agent=false",
        "-c", 'model_reasoning_effort="low"',
        "-s", "read-only",
        "-m", "gpt-6.1-sol",
        "--json",
    ]

    def _parse(stdout: str) -> dict:
        tid, cache_read, uncached_in, out_tok = None, 0, 0, 0
        for line in (stdout or "").splitlines():
            try:
                d = json.loads(line)
            except Exception:
                continue
            if not isinstance(d, dict):
                continue
            if d.get("type") == "thread.started" and d.get("thread_id"):
                tid = str(d["thread_id"])
            if d.get("type") == "turn.completed" and isinstance(d.get("usage"), dict):
                u = d["usage"]
                inp = int(u.get("input_tokens") or 0)
                cached = int(u.get("cached_input_tokens") or 0)
                out_tok = int(u.get("output_tokens") or 0)
                cache_read = cached
                uncached_in = max(0, inp - cached)
        return {"thread_id": tid, "cache_read": cache_read, "input": uncached_in, "output": out_tok}

    r1 = subprocess.run([exe, "exec", *common_flags, "-"], input=prompt, cwd=str(ROOT), capture_output=True, text=True, timeout=120)
    cold = _parse(r1.stdout)
    tid = cold.get("thread_id")
    if tid:
        r2 = subprocess.run([exe, "exec", "resume", tid, "-c", 'model_reasoning_effort="low"', "-m", "gpt-6.1-sol", "--json", "OK"], cwd=str(ROOT), capture_output=True, text=True, timeout=120)
        warm = _parse(r2.stdout)
    else:
        warm = cold
    return {
        "provider": "codex_workspace",
        "available": True,
        "cache_read": warm["cache_read"],
        "warm_call": warm,
        "cold_call": cold,
    }


def _seed_sample_project(proj: Path) -> None:
    meta = {
        "concept_brief": "Scene 1 live problem in office, Scene 2 Miss Nova split screen English correction",
        "characters": {"Ravi": "learner", "Miss Nova": "AI teacher"},
        "scenes": [{"id": 1, "text": "Office"}, {"id": 2, "text": "Split screen with Miss Nova"}],
    }
    plan_stub = {"preset": "auto", "style": "micronarrative"}
    edit_brief.write(proj, meta, plan_stub, style="micronarrative")
    (proj / "plan.edl").write_text(
        '#output "/Users/user/Downloads/Zeno Outs/Demo/reel_Demo.mp4"\n#src_dir "raw"\n#script "script.txt"\n#canvas 1080x1920@30\n',
        encoding="utf-8",
    )
    # Copy real W47 line_map + facts size profile if present (read-only), else realistic sample
    w47 = ROOT / "work" / "gdoc_1k0D7g1J24rF_t_zhww87pbss2f"
    if (w47 / "line_map.md").is_file() and (w47 / "facts.md").is_file():
        (proj / "line_map.md").write_text((w47 / "line_map.md").read_text(encoding="utf-8"), encoding="utf-8")
        (proj / "facts.md").write_text((w47 / "facts.md").read_text(encoding="utf-8"), encoding="utf-8")
    else:
        (proj / "line_map.md").write_text("L1 Ravi: CAND S1 0.40-2.10 heard=100%\n", encoding="utf-8")
        (proj / "facts.md").write_text("## S1 (10.0s)\n0.40 hello 0.80 world|1.20\n", encoding="utf-8")


def build_sample_editing_packet() -> str:
    """Build a realistic Zeno v2.0.0 segregated `editing_packet.md` (1 format + line_map + facts)."""
    with tempfile.TemporaryDirectory(prefix="zeno_pkt_") as tmp:
        proj = Path(tmp)
        _seed_sample_project(proj)
        edit_packet.write(proj)
        return (proj / "editing_packet.md").read_text(encoding="utf-8")


build_sample_zeno_pro_packet = build_sample_editing_packet


class CacheReadTests(unittest.TestCase):
    def test_build_sample_editing_packet_uses_v2_layout_and_zeno_outs(self) -> None:
        pkt = build_sample_editing_packet()
        self.assertIn("# Zeno Director Packet", pkt)
        self.assertIn("/Users/user/Downloads/Zeno Outs/Demo/reel_Demo.mp4", pkt)
        self.assertIn("L1", pkt)
        self.assertNotIn("ai_director", pkt)
        self.assertNotIn("--auto-plan", pkt)

    def test_editing_packet_prefix_and_system_prompt_cache_stability(self) -> None:
        self.assertEqual(
            (ROOT / "AGENTS.md").read_bytes(),
            (ROOT / "CLAUDE.md").read_bytes(),
            "AGENTS.md and CLAUDE.md must stay byte-identical for stable prompt cache prefixes",
        )
        with tempfile.TemporaryDirectory(prefix="zeno_pkt_stab_") as tmp:
            proj = Path(tmp)
            _seed_sample_project(proj)
            edit_packet.write(proj)
            first_edit = (proj / "editing_packet.md").read_bytes()
            first_dir = (proj / "director_packet.md").read_bytes()
            self.assertEqual(first_edit, first_dir)

            edit_packet.write(proj)
            second_edit = (proj / "editing_packet.md").read_bytes()
            self.assertEqual(first_edit, second_edit, "Repeated edit_packet.write() must be deterministic for cache hits")

    def test_missing_clis_return_unavailable_cleanly(self) -> None:
        with mock.patch("test_cache_read.find_claude_binary", return_value=None), \
             mock.patch("test_cache_read.find_codex_binary", return_value=None):
            c_ws = measure_claude_workspace_call("sample")
            c_iso = measure_claude_isolated_packet_call("sample")
            x_ws = measure_codex_workspace_call("sample")
        self.assertFalse(c_ws["available"])
        self.assertFalse(c_iso["available"])
        self.assertFalse(x_ws["available"])


def main() -> int:
    ap = argparse.ArgumentParser(description="Measure 1-call cache_read in Zeno v2.0.0")
    ap.add_argument("--with-packet", action="store_true", help="Include a full Zeno v2.0.0 video editing_packet.md")
    ap.add_argument("--cache-read-only", action="store_true", default=True, help="Report warm cache_read metric")
    args = ap.parse_args()

    pkt = build_sample_editing_packet() if args.with_packet else ""
    results = {
        "packet_bytes": len(pkt.encode("utf-8")) if pkt else 0,
        "claude_workspace_1_call_cache_read": measure_claude_workspace_call(pkt).get("cache_read", 0),
        "claude_isolated_packet_1_call_cache_read": (
            measure_claude_isolated_packet_call(pkt)
            if pkt
            else measure_claude_isolated_packet_call("Ready.")
        ).get("cache_read", 0),
        "codex_workspace_1_call_cache_read": measure_codex_workspace_call(pkt).get("cache_read", 0),
    }
    print(json.dumps(results, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
