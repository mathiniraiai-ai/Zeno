"""Open a fresh editing chat with session-only connector overrides.

Notion, Meta, and Zeno MCP connectors stay enabled by default so editors can inspect
Notion cards/queues and queue videos back-to-back (up to 1M context tokens), while
non-editing MCP servers, plugins, synced global skills, and browser bloat are stripped.
No global settings or credentials are changed. --dry-run shows the command without
starting a model session.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import tomllib

ROOT = Path(__file__).resolve().parent.parent
ESSENTIAL_MCP_KEYWORDS = ("notion", "meta", "zeno")
OPTION_B_NOTES = (
    "Option B manual/Antigravity fallback (only if --auto-plan cannot run): "
    "(1) 1 parallel batch read of editing_packet_p*.md (or editing_packet.md) + all clip_sheet_*.jpg listed by --inspect; "
    "(2) 1 parallel batch write of script_analysis.md + plan.edl; "
    "(3) 1 combined finish command `<PY> scripts/zeno.py --execute <plan> --square --compact`. "
    "Never poll background tasks/logs (end turn & wait for async wakeup), never pause to ask Yes/No before rendering, "
    "and never open agent/REFERENCE.md, CONCEPT.md, EDIT_RULES.md, FORMATS.md, scripts/*.py, facts.md, line_map.md, "
    "clip_caches.json, ffprobe, or dir."
)
PROMPT = (
    "Start a Zeno video edit session using AGENTS.md and agent/DIRECTOR.md "
    "(Notion, Meta, and Zeno connectors stay enabled; supports queued multi-video chats up to 1M context tokens "
    "with isolated per-video cost windows). With inputs, run `<PY> scripts/zeno.py --inspect <footage> --script <script> "
    "--name \"<name>\" --auto-plan --compact` (`.venv/bin/python` on Mac/Linux or `.venv\\Scripts\\python.exe` on Windows) "
    "for 1-call isolated planning + automatic 9:16 & 1:1 Square render without asking Yes/No. "
    f"{OPTION_B_NOTES} "
    "Preserve caches, keep render/progress/checks in Python, honor config/cost_controls.json checkpoints, "
    "and report measured cached input and cost separately. "
    "Ask only for missing script tab, local footage, and Notion name. No development work in this edit."
)


def _is_essential_mcp(name: str) -> bool:
    lowered = name.lower()
    return any(keyword in lowered for keyword in ESSENTIAL_MCP_KEYWORDS)


def codex_overrides(configs: list[Path], keep_essential: bool = True) -> list[str]:
    names = {"mcp_servers": set(), "plugins": set(), "apps": {"_default"}}
    for path in configs:
        if not path.is_file():
            continue
        with path.open("rb") as stream:
            config = tomllib.load(stream)
        for section in names:
            names[section].update(config.get(section, {}).keys())
    # Names only: server commands, URLs, environment and credentials are never printed.
    args = []
    for section, entries in names.items():
        for name in sorted(entries):
            if section == "mcp_servers" and keep_essential and _is_essential_mcp(name):
                continue
            # Codex splits override paths on dots; quotes become literal key characters.
            if "." in name:
                raise ValueError(f"Cannot safely override the dotted connector name {name!r} for this launch.")
            args.extend(["-c", f"{section}.{name}.enabled=false"])
    return args


def resolve_agent_binary(agent: str) -> str | None:
    found = shutil.which(agent)
    if found:
        return found
    home = Path.home()
    local_app = os.getenv("LOCALAPPDATA")
    app_data = os.getenv("APPDATA")
    candidates: list[Path] = []
    if agent == "codex":
        candidates.extend([
            Path("/Applications/Codex.app/Contents/Resources/codex"),
            Path("/Applications/Codex.app/Contents/MacOS/codex"),
            Path("/Applications/ChatGPT.app/Contents/Resources/codex-cli/CodexCLI.app/Contents/MacOS/codex"),
            home / ".local" / "bin" / "codex",
            Path("/opt/homebrew/bin/codex"),
            Path("/usr/local/bin/codex"),
        ])
        if local_app:
            candidates.append(Path(local_app) / "Programs" / "codex" / "codex.exe")
        if app_data:
            candidates.append(Path(app_data) / "npm" / "codex.cmd")
            candidates.append(Path(app_data) / "npm" / "codex.exe")
    elif agent == "claude":
        candidates.extend([
            home / ".local" / "bin" / "claude",
            Path("/opt/homebrew/bin/claude"),
            Path("/usr/local/bin/claude"),
        ])
        if local_app:
            candidates.append(Path(local_app) / "Programs" / "claude" / "claude.exe")
        if app_data:
            candidates.append(Path(app_data) / "npm" / "claude.cmd")
            candidates.append(Path(app_data) / "npm" / "claude.exe")
    for candidate in candidates:
        if candidate.is_file() and (os.name == "nt" or os.access(candidate, os.X_OK)):
            return str(candidate)
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--agent", choices=("codex", "claude"), default="codex")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--no-cloud-connectors", action="store_true",
                        help="Disable cloud MCP connectors (including Notion/Meta) for a strictly offline/isolated chat")
    parser.add_argument("--prompt-file", type=Path, help="A small job-specific handoff, appended to the fresh prompt")
    args = parser.parse_args()
    binary = resolve_agent_binary(args.agent)
    if not binary:
        parser.error(f"{args.agent} is not installed or is not on PATH")
    supplied = ""
    if args.prompt_file:
        supplied = args.prompt_file.read_text(encoding="utf-8")
        if len(supplied.encode("utf-8")) > 12000:
            parser.error("Handoff exceeds 12 KB; use only this video's inputs and decisions.")
    env = os.environ.copy()
    if args.agent == "codex":
        config_root = Path(os.getenv("CODEX_HOME", str(Path.home() / ".codex")))
        try:
            overrides = codex_overrides(
                [config_root / "config.toml", ROOT / ".codex" / "config.toml"],
                keep_essential=not args.no_cloud_connectors,
            )
        except (ValueError, OSError) as exc:
            parser.error(str(exc))
        prompt = PROMPT + ("\n\n" + supplied if supplied else "")
        command = [binary, "--model", "gpt-6.1-sol", "-c", "features.web_search_request=false", *overrides, prompt]
    else:
        claude_parts = ["/zeno"]
        if supplied:
            claude_parts.append(supplied)
        claude_parts.append(OPTION_B_NOTES)
        claude_prompt = "\n\n".join(claude_parts)
        command = [binary, "--setting-sources", "project,local", "--no-chrome"]
        if args.no_cloud_connectors:
            env["ENABLE_CLAUDEAI_MCP_SERVERS"] = "false"
            command.append("--strict-mcp-config")
        command.append(claude_prompt)
    if args.no_cloud_connectors:
        print("Fresh editing session. Cloud and non-essential connectors disabled for this launch.", flush=True)
    else:
        print("Fresh editing session. Notion, Meta, and Zeno connectors preserved; non-editing bloat disabled for this launch.", flush=True)
    if args.dry_run:
        print(json.dumps(command, ensure_ascii=False))
        return 0
    return subprocess.call(command, cwd=ROOT, env=env)


if __name__ == "__main__":
    raise SystemExit(main())
