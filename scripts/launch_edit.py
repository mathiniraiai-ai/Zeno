"""Open a fresh editing chat with session-only connector overrides.

No global settings or credentials are changed. --dry-run shows the command without
starting a model session. Managed workspace settings may still supply tools.
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
PROMPT = ("Start one Zeno video edit using the low-cost workflow in AGENTS.md and agent/DIRECTOR.md. "
          "Use the editing packet once, preserve caches, keep render/progress/checks in Python, and honor "
          "config/cost_controls.json checkpoints. Report measured cached input and cost separately. "
          "Ask only for missing script tab, local footage and Notion name. No development work in this edit.")


def codex_overrides(configs: list[Path]) -> list[str]:
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
            # Codex splits override paths on dots; quotes become literal key characters.
            if "." in name:
                raise ValueError(f"Cannot safely override the dotted connector name {name!r} for this launch.")
            args.extend(["-c", f"{section}.{name}.enabled=false"])
    return args


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--agent", choices=("codex", "claude"), default="codex")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--prompt-file", type=Path, help="A small job-specific handoff, appended to the fresh prompt")
    args = parser.parse_args()
    binary = shutil.which(args.agent)
    if not binary:
        parser.error(f"{args.agent} is not installed or is not on PATH")
    env = os.environ.copy()
    if args.agent == "codex":
        config_root = Path(os.getenv("CODEX_HOME", str(Path.home() / ".codex")))
        try:
            overrides = codex_overrides([config_root / "config.toml", ROOT / ".codex" / "config.toml"])
        except (ValueError, OSError) as exc:
            parser.error(str(exc))
        prompt = PROMPT
        if args.prompt_file:
            supplied = args.prompt_file.read_text(encoding="utf-8")
            if len(supplied.encode()) > 12000:
                parser.error("Handoff exceeds 12 KB; use only this video's inputs and decisions.")
            prompt += "\n\n" + supplied
        command = [binary, "--model", "gpt-6.1-sol", *overrides, prompt]
    else:
        env["ENABLE_CLAUDEAI_MCP_SERVERS"] = "false"
        command = [binary, "--strict-mcp-config", "/zeno"]
    print("Fresh editing session. Configured connections disabled for this launch; managed workspace tools may remain.", flush=True)
    if args.dry_run:
        print(json.dumps(command, ensure_ascii=False))
        return 0
    return subprocess.call(command, cwd=ROOT, env=env)


if __name__ == "__main__":
    raise SystemExit(main())
