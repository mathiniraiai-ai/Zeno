#!/usr/bin/env python3
"""Verify service/prompt.py parity and v2.0.0 prompt/doc sync across the repo.

Run from the repo root (`python service/check_prompt_sync.py`) or from `service/`
(`../.venv/bin/python check_prompt_sync.py`).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import List

ROOT = Path(__file__).resolve().parent.parent
SERVICE = Path(__file__).resolve().parent


def check_transcription_prompt(errors: List[str]) -> None:
    """Fail if service/prompt.py differs from scripts/align_engine/gemini_transcriber.py."""
    sys.path.insert(0, str(ROOT / "scripts"))
    sys.path.insert(0, str(SERVICE))
    from align_engine.gemini_transcriber import build_transcribe_prompt as repo_prompt  # noqa: E402
    from prompt import build_transcribe_prompt as service_prompt  # noqa: E402

    cases = [
        (None, "auto"),
        (["Woman: hello there", "നിനക്ക് ഇഷ്ടപ്പെട്ടോ?"], "ml"),
        (["a"] * 50, "ta"),
    ]
    bad = [c for c in cases if repo_prompt(c[0], language_hint=c[1]) != service_prompt(c[0], language_hint=c[1])]
    if bad:
        errors.append(f"transcription prompt mismatch in {len(bad)} case(s): copy build_transcribe_prompt again")
    else:
        print("OK: service/prompt.py matches scripts/align_engine/gemini_transcriber.py")


def _read_required(rel: str, errors: List[str]) -> str:
    path = ROOT / rel
    if not path.is_file():
        errors.append(f"missing required v2.0.0 doc/config: {rel}")
        return ""
    text = path.read_text(encoding="utf-8")
    if not text.strip():
        errors.append(f"empty required v2.0.0 doc/config: {rel}")
    return text


def check_v2_doc_sync(errors: List[str]) -> None:
    """Verify v2.0.0 Host-AI Direct docs, rules, commands, delivery path, and zero-MCP invariants."""
    claude_md = _read_required("CLAUDE.md", errors)
    agents_md = _read_required("AGENTS.md", errors)
    if claude_md and agents_md and claude_md != agents_md:
        errors.append("CLAUDE.md and AGENTS.md differ (must be identical)")

    rule_rel = ".agent/rules/zeno.md"
    rule_md = _read_required(rule_rel, errors)

    skill_rel = ".agent/skills/zeno/SKILL.md"
    skill_md = _read_required(skill_rel, errors)

    cmd_dir = ROOT / ".claude" / "commands"
    cmd_files = sorted(cmd_dir.glob("*.md")) if cmd_dir.is_dir() else []
    for expected in ("zeno.md", "edit.md"):
        if not (cmd_dir / expected).is_file():
            errors.append(f"missing required command doc: .claude/commands/{expected}")

    doc_texts = {
        "CLAUDE.md": claude_md,
        "AGENTS.md": agents_md,
        rule_rel: rule_md,
        skill_rel: skill_md,
        **{f".claude/commands/{p.name}": p.read_text(encoding="utf-8") for p in cmd_files},
    }

    core_markers = [
        "Zeno v2.0.0",
        "Host-AI Direct",
        "~/Downloads/Zeno Outs/",
        "editing_packet.md",
        "scripts/zeno.py",
        "--inspect",
        "--execute",
        "--queue",
        "scripts/edit_packet.py",
    ]
    for label, text in doc_texts.items():
        if not text:
            continue
        for marker in core_markers:
            if marker not in text:
                errors.append(f"{label} missing v2.0.0 marker: {marker!r}")

    for label in ("CLAUDE.md", "AGENTS.md"):
        text = doc_texts.get(label, "")
        if text:
            for extra in ("script_analysis.md", "plan.edl", "review_sheet.jpg"):
                if extra not in text:
                    errors.append(f"{label} missing v2.0.0 marker: {extra!r}")
            if "zero mcp" not in text.lower():
                errors.append(f"{label} missing 'zero MCP' statement")

    if rule_md:
        if "AGENTS.md" not in rule_md:
            errors.append(f"{rule_rel} must reference AGENTS.md")
        if "zero mcp" not in rule_md.lower():
            errors.append(f"{rule_rel} missing 'zero MCP' statement")

    for p in cmd_files:
        rel = f".claude/commands/{p.name}"
        text = doc_texts.get(rel, "")
        if text and "zero mcp" not in text.lower():
            errors.append(f"{rel} missing 'Zero MCP' statement")

    forbidden_in_docs = [
        "--auto-plan",
        "zeno_mcp",
        "ai_director",
        "agent/EDIT_RULES.md",
        "agent/DIRECTOR.md",
        "editor-workflow.md",
        "zeno-pro",
        "Zeno Outputs",
        "Zeno Pro",
        "director_packet.md",
        "TOKEN_LEDGER.md",
        "LOW_COST_WORKFLOW.md",
        "brain_memory",
        "styles/",
        "mms-300m",
    ]
    for label, text in doc_texts.items():
        low = text.lower()
        for bad in forbidden_in_docs:
            if bad.lower() in low:
                errors.append(f"{label} contains forbidden 1.x/legacy marker: {bad!r}")

    # Zero-MCP config checks
    mcp_raw = _read_required(".mcp.json", errors)
    if mcp_raw:
        try:
            mcp_data = json.loads(mcp_raw)
            if not (isinstance(mcp_data, dict) and set(mcp_data) <= {"mcpServers"} and not mcp_data.get("mcpServers")):
                errors.append(f".mcp.json must define zero MCP servers ({{}} or {{'mcpServers': {{}}}}), got {mcp_data!r}")
        except json.JSONDecodeError as exc:
            errors.append(f".mcp.json invalid JSON: {exc}")

    settings_raw = _read_required(".claude/settings.json", errors)
    if settings_raw:
        try:
            settings = json.loads(settings_raw)
            if settings.get("enableAllProjectMcpServers") is not False:
                errors.append(".claude/settings.json must set enableAllProjectMcpServers=false")
            if (settings.get("env") or {}).get("ENABLE_CLAUDEAI_MCP_SERVERS") != "false":
                errors.append(".claude/settings.json must set env.ENABLE_CLAUDEAI_MCP_SERVERS='false'")
            deny = (settings.get("permissions") or {}).get("deny") or []
            if "mcp__*" not in deny:
                errors.append(".claude/settings.json must deny 'mcp__*'")
        except json.JSONDecodeError as exc:
            errors.append(f".claude/settings.json invalid JSON: {exc}")

    # Verify deleted 1.x files stay deleted
    deleted_paths = [
        ".agent/rules/editor-workflow.md",
        ".agent/rules/zeno-pro.md",
        ".agent/skills/zeno-pro",
        "agent/EDIT_RULES.md",
        "agent/DIRECTOR.md",
        "TOKEN_LEDGER.md",
        "LOW_COST_WORKFLOW.md",
        "scripts/zeno_mcp.py",
        "scripts/brain_memory.py",
        "scripts/format_learning.py",
        "scripts/knowledge_sync.py",
        "scripts/learn_reference.py",
    ]
    for rel in deleted_paths:
        if (ROOT / rel).exists():
            errors.append(f"deleted 1.x path still exists: {rel}")

    # Verify scripts/zeno.py has zero --auto-plan / MCP / Zeno Outputs references
    zeno_py = _read_required("scripts/zeno.py", errors)
    if zeno_py:
        for bad in ("--auto-plan", "ai_director", "zeno_mcp", "Zeno Outputs"):
            if bad.lower() in zeno_py.lower():
                errors.append(f"scripts/zeno.py contains forbidden 1.x marker: {bad!r}")

    if not errors:
        print("OK: v2.0.0 docs, rules, commands, ~/Downloads/Zeno Outs/, and zero-MCP checks synced")


def main() -> int:
    errors: List[str] = []
    check_transcription_prompt(errors)
    doc_errors: List[str] = []
    check_v2_doc_sync(doc_errors)
    errors.extend(doc_errors)
    for err in errors:
        print(f"FAIL: {err}")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
