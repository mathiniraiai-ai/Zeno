#!/usr/bin/env python3
"""Tool-free isolated 1-call planning entry point (`claude`, `codex`, `auto`, or `openai`).

Subscription-native CLI planners (`--provider claude`, `--provider codex`, `--provider auto`) run
inside an empty temporary directory with tools, MCP servers, skills, rules and repo auto-discovery
disabled while preserving the editor's keychain / `$CODEX_HOME/auth.json` OAuth subscription.

OpenAI API direct mode (`--provider openai`) remains intact behind `sol_direct.py` and requires a
separately configured `OPENAI_API_KEY` and separate API billing (see `LOW_COST_WORKFLOW.md`).

    .venv/bin/python scripts/direct_mode.py work/<project>                          # auto-detect subscription CLI, 1 call + check
    .venv/bin/python scripts/direct_mode.py work/<project> --provider claude        # Claude subscription CLI (-p in empty tmp dir)
    .venv/bin/python scripts/direct_mode.py work/<project> --provider codex         # Codex subscription CLI (codex exec in empty tmp dir)
    .venv/bin/python scripts/direct_mode.py work/<project> --provider openai        # Bounded GPT-6.1 Sol Responses API planner
    .venv/bin/python scripts/direct_mode.py work/<project> --dry-run                # Validate binary/reserve and brief size; no model call
    .venv/bin/python scripts/direct_mode.py work/<project> --brief-only             # Build direct_brief.md and show its size
"""
from __future__ import annotations

import argparse
import base64
import contextlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

PY = sys.executable
CALL_TIMEOUT = 1200
DEFAULT_CLAUDE_MODEL = "sonnet"
DEFAULT_CODEX_MODEL = "gpt-6.1-sol"
HEADER_KEYS = ("#output", "#script", "#src_dir", "#canvas", "#style")   # owned by Zeno, never taken from the model's answer

_CLI_HELP_CACHE: Dict[Tuple[str, ...], str] = {}
_RUN_TMP_CWD: Optional[str] = None

SYSTEM = """You are the editor of a short vertical video for Zeno. Zeno has already transcribed and measured the footage; \
you make every editing decision and write the plan, and Zeno renders it. Nobody is at the keyboard while you work: never \
ask a question, decide, and list what you were unsure of in NOTES. You have no tools: everything you need is in this \
message, so do not ask to open files.

Answer with exactly these blocks, in this order, and nothing between them:

```analysis
<the content of script_analysis.md: first line `Format: <id> (<why>)` from the formats table, then the emotion plan the \
rules ask for, one row per spoken line, at most three PEAK moments>
```

```plan
<every segment line of plan.edl in script order, plus any extra header lines the rules ask for (for example #subs or \
#page). Do not repeat #output, #script, #src_dir, #canvas or #style: Zeno keeps those.>
```

```decisions
<only deliberate scripted exceptions, one per line: `accept <id> <reason>`; leave the block empty if there are none>
```

NOTES:
<for the editor, in plain words: which clip you used for each part, the layouts, where the peaks are, every place you \
departed from what the script literally says, and anything you are unsure of (a voice the check may doubt, a line no \
clip says well)>

When Zeno sends you the plan check's output, answer again with all the blocks, complete (not a diff), fixing every \
blocking problem. Lines marked [TIGHT] or [STYLE] are advice: fix them, or keep them on purpose and say why in that \
segment's note.
First-pass precision: put `sp="Name"` on every cut, keep all splits/baked-splits at `z=1`, never cut inside a word \
(stop at the previous word's `|end` before any `! NO SPEECH` ghost word), use `z=1->1.06` for slow pushes while keeping \
at least 25% of single cuts still at `z=1`, and trim pauses cleanly on Call 1 (`<= 0.25s` on single cuts, `<= 0.50s` on \
split turns) so the plan passes on the first call.

The rules you follow come below.
"""


def _read(p: Path) -> str:
    try:
        return p.read_text(encoding="utf-8")
    except Exception:
        return ""


def _section(text: str, start: str, end: str) -> str:
    i = text.find(start)
    if i < 0:
        return ""
    j = text.find(end, i + len(start))
    return text[i:j if j > 0 else len(text)].strip()


def header_of(plan_text: str) -> List[str]:
    return [l for l in plan_text.splitlines() if l.startswith("#") and l.strip()]


def style_of(plan_text: str) -> str:
    for l in plan_text.splitlines():
        if l.startswith("#style"):
            return l.split(None, 1)[1].strip() if len(l.split(None, 1)) > 1 else ""
    return ""


def rules_text(style: str) -> str:
    """The rule files a Claude Code agent reads during an edit, in the same words (nothing is summarised away)."""
    parts = []
    zeno = _read(ROOT / ".claude/commands/zeno.md")
    good = _section(zeno, "## What a good edit is", "## Inputs")
    if good:
        parts.append(good)
    for title, path in (("agent/EDIT_RULES.md", ROOT / "agent/EDIT_RULES.md"),
                        (f"styles/{style}/SKILL.md", ROOT / "styles" / style / "SKILL.md"),
                        ("agent/FORMATS.md", ROOT / "agent/FORMATS.md")):
        t = _read(path)
        if t:
            parts.append(f"=== {title} ===\n{t.strip()}")
    ref = _read(ROOT / "agent/REFERENCE.md")
    if ref:
        parts.append(f"=== agent/REFERENCE.md (layouts, marks, syntax) ===\n{ref.strip()}")
    return "\n\n".join(parts)


def build_brief(proj: Path) -> Tuple[str, str, List[Path]]:
    """(system prompt, job message, clip sheet pictures)."""
    plan = _read(proj / "plan.edl")
    brief = _read(proj / "brief.md")
    style = style_of(plan)
    style_rules = _read(ROOT / "styles" / style / "SKILL.md") if style else ""
    # A tool-free director cannot open the requested style later. Keep its rules when not
    # already embedded in brief.md, while avoiding another copy of all generic editing references.
    style_already_in_brief = bool(
        brief
        and style
        and (
            (style_rules.strip() and style_rules.strip() in brief)
            or f"# style: {style.lower()}" in brief.lower()
        )
    )
    extra_style = "" if (not style_rules or style_already_in_brief) else ("\n" + style_rules)
    system = SYSTEM + "\n" + ((brief + extra_style) if brief else rules_text(style))
    job = [f"Project folder: {proj.relative_to(ROOT) if str(proj).startswith(str(ROOT)) else proj}",
           "=== plan.edl header (Zeno keeps it) ===\n" + "\n".join(header_of(plan))]
    for name in ("line_map.md", "facts.md"):
        t = _read(proj / name)
        if name == "facts.md":
            from edit_packet import compact_facts
            t = compact_facts(t)
        if t:
            job.append(f"=== {name} ===\n{t.strip()}")
    dec = _read(proj / "decisions.txt").strip()
    if dec:
        job.append("=== decisions.txt already recorded (keep what still applies) ===\n" + dec)
    sheets = sorted(proj.glob("clip_sheet_*.jpg"))
    if sheets:
        job.append(f"The {len(sheets)} picture(s) attached are {', '.join(p.name for p in sheets)}: frames of each clip, as named in line_map.md.")
    job.append("Write the edit now.")
    return system, "\n\n".join(job), sheets


def _cli_help(cmd_tuple: Tuple[str, ...]) -> str:
    """Inspect a CLI's `--help` once per process to detect supported isolation flags."""
    if cmd_tuple in _CLI_HELP_CACHE:
        return _CLI_HELP_CACHE[cmd_tuple]
    if getattr(subprocess.run, "__module__", "") != "subprocess" or not os.path.isfile(cmd_tuple[0]):
        return ""
    try:
        r = subprocess.run(list(cmd_tuple), capture_output=True, text=True, timeout=10, encoding="utf-8", errors="replace")
        out = (r.stdout or "") + "\n" + (r.stderr or "")
    except Exception:
        out = ""
    _CLI_HELP_CACHE[cmd_tuple] = out
    return out


def find_codex_binary() -> Optional[str]:
    """Locate the `codex` CLI on PATH or inside macOS/Windows Desktop app bundles."""
    hit = shutil.which("codex")
    if hit:
        return hit
    candidates = [
        Path("/Applications/Codex.app/Contents/Resources/codex"),
        Path("/Applications/ChatGPT.app/Contents/Resources/codex-cli/CodexCLI.app/Contents/MacOS/codex"),
        Path.home() / "Applications" / "Codex.app" / "Contents" / "Resources" / "codex",
        Path.home() / "Applications" / "ChatGPT.app" / "Contents" / "Resources" / "codex-cli" / "CodexCLI.app" / "Contents" / "MacOS" / "codex",
    ]
    local_app = os.environ.get("LOCALAPPDATA")
    app_data = os.environ.get("APPDATA")
    if local_app:
        candidates.append(Path(local_app) / "Programs" / "codex" / "codex.exe")
    if app_data:
        candidates.append(Path(app_data) / "npm" / "codex.cmd")
        candidates.append(Path(app_data) / "npm" / "codex.exe")
    for cand in candidates:
        try:
            if cand.is_file() and os.access(cand, os.X_OK):
                return str(cand)
        except OSError:
            continue
    return None


def resolve_provider(provider: str) -> str:
    """Resolve `auto` to the active subscription CLI (`codex` or `claude`)."""
    if provider != "auto":
        return provider
    codex_bin = find_codex_binary()
    claude_bin = shutil.which("claude")
    if os.getenv("CODEX_THREAD_ID") and codex_bin:
        return "codex"
    if claude_bin:
        return "claude"
    if codex_bin:
        return "codex"
    raise RuntimeError("No subscription CLI found: install Claude Code (`claude`) or Codex (`codex`), or pass `--provider openai`.")


def _safe_codex_overrides() -> List[str]:
    """Build `-c <section>.<name>.enabled=false` flags when `--ignore-user-config` is unavailable."""
    config_root = Path(os.getenv("CODEX_HOME", str(Path.home() / ".codex")))
    configs = [config_root / "config.toml", ROOT / ".codex" / "config.toml"]
    try:
        import launch_edit
        return launch_edit.codex_overrides(configs)
    except Exception:
        pass
    try:
        import tomllib
        names: Dict[str, set] = {"mcp_servers": set(), "plugins": set(), "apps": {"_default"}}
        for p in configs:
            if p.is_file():
                with p.open("rb") as stream:
                    cfg = tomllib.load(stream)
                for sec in names:
                    names[sec].update(k for k in cfg.get(sec, {}).keys() if "." not in k)
        out: List[str] = []
        for sec, entries in names.items():
            for name in sorted(entries):
                out.extend(["-c", f"{sec}.{name}.enabled=false"])
        return out
    except Exception:
        return []


def _message(text: str, images: List[Path]) -> Dict[str, Any]:
    content: List[Dict[str, Any]] = []
    for p in images:
        content.append({"type": "image", "source": {"type": "base64", "media_type": "image/jpeg",
                                                    "data": base64.b64encode(p.read_bytes()).decode()}})
    content.append({"type": "text", "text": text})
    return {"type": "user", "message": {"role": "user", "content": content}}


def _claude_flags(exe: str, model: str, system: str, session: Optional[str]) -> List[str]:
    """Build isolated Claude CLI args. Never pass `--bare` so keychain/OAuth subscriptions work."""
    help_text = _cli_help((exe, "--help"))
    effort = (os.environ.get("ZENO_CLAUDE_EFFORT") or "medium").strip()
    cmd = [
        exe, "-p", "--model", model,
        "--input-format", "stream-json",
        "--output-format", "stream-json",
        "--verbose",
        "--system-prompt", system,
        "--tools", "",
        "--strict-mcp-config",
        "--disable-slash-commands",
        "--setting-sources", "",
    ]
    if "--effort" in help_text and effort:
        cmd.extend(["--effort", effort])
    if "--no-chrome" in help_text:
        cmd.append("--no-chrome")
    if session:
        cmd += ["--resume", session]
    return cmd


def call_claude(system: str, text: str, images: List[Path], model: str, session: Optional[str]) -> Dict[str, Any]:
    """One request to Claude through the `claude` command in an empty temp dir (never `cwd=ROOT`, never `--bare`).
    Returns {"text", "session", "usage", "actual_model", "seconds", "error"}."""
    exe = shutil.which("claude")
    if not exe:
        return {"error": "the claude command is not installed", "text": "", "usage": {}, "session": session, "seconds": 0}
    cmd = _claude_flags(exe, model, system, session)
    env = dict(os.environ, ENABLE_CLAUDEAI_MCP_SERVERS="false")
    env.setdefault("MAX_THINKING_TOKENS", os.environ.get("ZENO_MAX_THINKING_TOKENS", "4096"))
    env.pop("CLAUDECODE", None)
    env.pop("CLAUDE_CODE_ENTRYPOINT", None)
    t0 = time.time()
    try:
        with contextlib.ExitStack() as stack:
            tmp_cwd = _RUN_TMP_CWD or stack.enter_context(tempfile.TemporaryDirectory(prefix="zeno_claude_"))
            r = subprocess.run(cmd, input=json.dumps(_message(text, images)) + "\n", capture_output=True, text=True,
                               timeout=CALL_TIMEOUT, cwd=tmp_cwd, env=env, encoding="utf-8", errors="replace")
    except subprocess.TimeoutExpired:
        return {"error": f"no answer within {CALL_TIMEOUT // 60} minutes", "text": "", "usage": {}, "session": session, "seconds": CALL_TIMEOUT}
    result: Dict[str, Any] = {}
    for line in (r.stdout or "").splitlines():
        try:
            d = json.loads(line)
        except Exception:
            continue
        if isinstance(d, dict) and d.get("type") == "result":
            result = d
    usage = _usage(result)
    actual_model = _primary_claude_model(result, model)
    err = "" if result and not result.get("is_error") else (result.get("result") or (r.stderr or "")[-600:] or f"claude exited with {r.returncode}")
    return {"text": str(result.get("result") or ""), "session": result.get("session_id") or session, "usage": usage,
            "actual_model": actual_model, "seconds": round(time.time() - t0, 1), "error": err}


def _primary_claude_model(result: Dict[str, Any], fallback: str) -> str:
    mu = result.get("modelUsage") if isinstance(result.get("modelUsage"), dict) else {}
    if not mu:
        return fallback
    return max(
        mu.keys(),
        key=lambda k: sum(int((mu[k] or {}).get(f) or 0) for f in ("inputTokens", "cacheCreationInputTokens", "cacheReadInputTokens", "outputTokens")),
    )


def _usage(result: Dict[str, Any]) -> Dict[str, int]:
    """Tokens of one call, every model it used (Claude Code may add a tiny side call), all four kinds."""
    out = {"input": 0, "cache_write": 0, "cache_read": 0, "output": 0}
    mu = result.get("modelUsage") if isinstance(result.get("modelUsage"), dict) else {}
    if mu:
        for v in mu.values():
            out["input"] += int(v.get("inputTokens") or 0)
            out["cache_write"] += int(v.get("cacheCreationInputTokens") or 0)
            out["cache_read"] += int(v.get("cacheReadInputTokens") or 0)
            out["output"] += int(v.get("outputTokens") or 0)
    else:
        u = result.get("usage") or {}
        out = {"input": int(u.get("input_tokens") or 0), "cache_write": int(u.get("cache_creation_input_tokens") or 0),
               "cache_read": int(u.get("cache_read_input_tokens") or 0), "output": int(u.get("output_tokens") or 0)}
    out["total"] = sum(out.values())
    return out


def _codex_exec_flags(exe: str, tmp_cwd: str, out_file: Path, images: List[Path], model: str) -> List[str]:
    """Build isolated `codex exec` args while preserving `$CODEX_HOME/auth.json` OAuth."""
    help_text = _cli_help((exe, "exec", "--help"))
    cmd = [exe, "exec", "-C", tmp_cwd, "--skip-git-repo-check"]
    if not help_text or "--ephemeral" in help_text:
        if "--ephemeral" in help_text:
            cmd.append("--ephemeral")
    if "--ignore-user-config" in help_text:
        cmd.append("--ignore-user-config")
    else:
        cmd.extend(_safe_codex_overrides())
    if "--ignore-rules" in help_text:
        cmd.append("--ignore-rules")
    if "--disable" in help_text:
        for feat in ("apps", "plugins", "shell_tool", "unified_exec", "multi_agent"):
            cmd.extend(["--disable", feat])
    codex_effort = (os.environ.get("ZENO_CODEX_EFFORT") or "medium").strip()
    if codex_effort:
        cmd.extend(["-c", f'model_reasoning_effort="{codex_effort}"'])
    for img in images:
        cmd.extend(["-i", str(img)])
    cmd.extend(["-s", "read-only", "-m", model, "--json", "-o", str(out_file), "-"])
    return cmd


def _parse_codex_jsonl(stdout: str, out_file: Path, fallback_model: str) -> Tuple[str, Dict[str, int], str, str, bool]:
    """Extract `(text, usage, actual_model, error, usage_known)` from `codex exec --json` output."""
    last_total: Optional[Dict[str, Any]] = None
    turn_usages: List[Dict[str, Any]] = []
    agent_messages: List[str] = []
    detected_model = fallback_model
    errors: List[str] = []

    for line in (stdout or "").splitlines():
        if not line.strip():
            continue
        try:
            d = json.loads(line)
        except Exception:
            continue
        if not isinstance(d, dict):
            continue
        dtype = d.get("type")
        payload = d.get("payload") if isinstance(d.get("payload"), dict) else {}
        if dtype == "turn_context" and payload.get("model"):
            detected_model = str(payload["model"])
        elif d.get("model") and isinstance(d.get("model"), str):
            detected_model = str(d["model"])

        if dtype == "token_count" or (dtype == "event_msg" and payload.get("type") == "token_count"):
            info = (payload.get("info") if dtype == "event_msg" else d.get("info")) or {}
            if isinstance(info, dict) and isinstance(info.get("total_token_usage"), dict):
                last_total = info["total_token_usage"]
        elif dtype == "turn.completed" and isinstance(d.get("usage"), dict):
            turn_usages.append(d["usage"])
        elif dtype == "item.completed" and isinstance(d.get("item"), dict):
            item = d["item"]
            if item.get("type") == "agent_message" and item.get("text"):
                agent_messages.append(str(item["text"]))
        elif dtype == "event_msg" and payload.get("type") == "agent_message" and payload.get("message"):
            agent_messages.append(str(payload["message"]))
        elif dtype in ("error", "turn.failed"):
            msg = d.get("message") or (d.get("error") or {}).get("message") if isinstance(d.get("error"), dict) else d.get("error")
            if msg:
                errors.append(str(msg))

    usage_known = False
    if last_total is not None:
        inp_total = int(last_total.get("input_tokens") or 0)
        cached = int(last_total.get("cached_input_tokens") or 0)
        cache_write = int(last_total.get("cache_write_input_tokens") or 0)
        fresh = max(0, inp_total - cached - cache_write)
        output = int(last_total.get("output_tokens") or 0)
        reasoning = int(last_total.get("reasoning_output_tokens") or 0)
        usage_known = True
    elif turn_usages:
        inp_total = sum(int(u.get("input_tokens") or 0) for u in turn_usages)
        cached = sum(int(u.get("cached_input_tokens") or 0) for u in turn_usages)
        cache_write = sum(int(u.get("cache_write_input_tokens") or 0) for u in turn_usages)
        fresh = max(0, inp_total - cached - cache_write)
        output = sum(int(u.get("output_tokens") or 0) for u in turn_usages)
        reasoning = sum(
            int(u.get("reasoning_output_tokens") or (u.get("output_tokens_details") or {}).get("reasoning_tokens") or 0)
            for u in turn_usages
        )
        usage_known = True
    else:
        fresh = cached = cache_write = output = reasoning = 0

    usage = {
        "input": fresh,
        "cache_write": cache_write,
        "cache_read": cached,
        "output": output,
        "reasoning": reasoning,
        "total": fresh + cache_write + cached + output,
    }
    text = _read(out_file).strip() if out_file.is_file() else ""
    if not text and agent_messages:
        text = agent_messages[-1].strip()
    return text, usage, detected_model, "; ".join(errors), usage_known


def call_codex(system: str, text: str, images: List[Path], model: str = DEFAULT_CODEX_MODEL) -> Dict[str, Any]:
    """One isolated request to Codex via `codex exec` in an empty temp dir using subscription OAuth."""
    exe = find_codex_binary()
    if not exe:
        return {"error": "the codex command is not installed", "text": "", "usage": {}, "actual_model": model, "seconds": 0, "usage_known": False}
    env = dict(os.environ)
    env.pop("CODEX_THREAD_ID", None)
    prompt = system + "\n\n---\n\n" + text
    t0 = time.time()
    try:
        with tempfile.TemporaryDirectory(prefix="zeno_codex_cwd_") as tmp_cwd, \
             tempfile.TemporaryDirectory(prefix="zeno_codex_out_") as tmp_out:
            out_file = Path(tmp_out) / "last_message.txt"
            cmd = _codex_exec_flags(exe, tmp_cwd, out_file, images, model)
            r = subprocess.run(cmd, input=prompt, capture_output=True, text=True,
                               timeout=CALL_TIMEOUT, cwd=tmp_cwd, env=env, encoding="utf-8", errors="replace")
            ans, usage, actual_model, json_err, usage_known = _parse_codex_jsonl(r.stdout or "", out_file, model)
    except subprocess.TimeoutExpired:
        return {"error": f"no answer within {CALL_TIMEOUT // 60} minutes", "text": "", "usage": {},
                "actual_model": model, "seconds": CALL_TIMEOUT, "usage_known": False}
    err = ""
    if r.returncode != 0 and not ans:
        err = json_err or (r.stderr or "")[-600:].strip() or f"codex exited with {r.returncode}"
    elif not ans and json_err:
        err = json_err
    return {
        "text": ans,
        "usage": usage,
        "actual_model": actual_model,
        "seconds": round(time.time() - t0, 1),
        "error": err,
        "usage_known": usage_known,
    }


def _append_direct_usage(proj: Path, provider: str, model: str, u: Dict[str, int], usage_known: bool, stage: str = "direct planning") -> Dict[str, Any]:
    """Append exact isolated CLI call telemetry to `proj / direct_usage.jsonl` for `cost_control.py` & `token_tracker.py`."""
    inp_fresh = int((u or {}).get("input") or 0)
    cache_read = int((u or {}).get("cache_read") or 0)
    cache_write = int((u or {}).get("cache_write") or 0)
    output_toks = int((u or {}).get("output") or 0)
    reasoning_toks = int((u or {}).get("reasoning") or 0)
    total_inp = inp_fresh + cache_read + cache_write
    row = {
        "call_id": uuid.uuid4().hex,
        "timestamp": time.time(),
        "provider": provider,
        "model": model,
        "stage": stage,
        "usage_known": bool(usage_known),
        "input_tokens": total_inp,
        "cached_input_tokens": cache_read,
        "cache_write_input_tokens": cache_write,
        "output_tokens": output_toks,
        "reasoning_output_tokens": reasoning_toks,
        "context": total_inp,
    }
    try:
        with (proj / "direct_usage.jsonl").open("a", encoding="utf-8") as journal:
            journal.write(json.dumps(row, ensure_ascii=False) + "\n")
            journal.flush()
    except OSError:
        pass
    return row


_BLOCK = re.compile(r"```(analysis|plan|decisions)[ \t]*\n(.*?)```", re.S)


def parse_answer(text: str) -> Dict[str, str]:
    blocks = {k: v.strip() for k, v in _BLOCK.findall(text or "")}
    notes = text.split("NOTES:", 1)[1].strip() if "NOTES:" in (text or "") else ""
    blocks["notes"] = notes
    return blocks


def merge_plan(original: str, answer_plan: str) -> str:
    """Zeno's header lines stay exactly as they were; Claude's extra header lines and every segment line are added."""
    head = header_of(original)
    keys = {l.split()[0] for l in head}
    extra = []
    body = []
    for l in answer_plan.splitlines():
        if not l.strip():
            continue
        if l.startswith("#"):
            k = l.split()[0]
            if k in HEADER_KEYS or k in keys:
                continue
            extra.append(l)
        else:
            body.append(l)
    return "\n".join(head + extra) + "\n\n" + "\n".join(body) + "\n"


def write_answer(proj: Path, blocks: Dict[str, str], original_plan: str) -> None:
    (proj / "script_analysis.md").write_text(blocks.get("analysis", "").strip() + "\n", encoding="utf-8")
    (proj / "plan.edl").write_text(merge_plan(original_plan, blocks.get("plan", "")), encoding="utf-8")
    dec = blocks.get("decisions", "").strip()
    lines = [l.strip() for l in dec.splitlines() if l.strip().lower().startswith("accept ")]
    (proj / "decisions.txt").write_text(("\n".join(lines) + "\n") if lines else "", encoding="utf-8")


def run_check(proj: Path) -> Tuple[bool, str]:
    r = subprocess.run([PY, str(ROOT / "scripts" / "zeno.py"), "--check-plan", str(proj / "plan.edl")],
                       capture_output=True, text=True, cwd=str(ROOT), encoding="utf-8", errors="replace")
    out = (r.stdout or "") + (r.stderr or "")
    return r.returncode == 0 and "[PLAN PASS]" in out, out.strip()


def _advice(check_out: str) -> List[str]:
    return [l for l in check_out.splitlines() if l.startswith(("[TIGHT]", "[STYLE]"))]


def run(proj: Path, model: str = DEFAULT_CLAUDE_MODEL, max_fixes: int = 2, polish: bool = False) -> Dict[str, Any]:
    """Isolated 1-call Claude subscription planner (`claude -p` in an empty temporary directory)."""
    global _RUN_TMP_CWD
    proj = proj.resolve()
    original = _read(proj / "plan.edl")
    if not original:
        raise SystemExit(f"No plan.edl in {proj}: run zeno.py --inspect first")
    original_header = "\n".join(header_of(original)) + "\n"
    system, job, sheets = build_brief(proj)
    (proj / "direct_brief.md").write_text(system + "\n\n---\n\n" + job, encoding="utf-8")
    report: Dict[str, Any] = {
        "project": proj.name, "provider": "claude", "model": model,
        "started": time.time(), "calls": [], "passed": False,
    }
    session: Optional[str] = None
    passing: Dict[str, str] = {}
    previous_failure: Optional[str] = None
    text, images, kind = job, sheets, "plan"

    with tempfile.TemporaryDirectory(prefix="zeno_direct_") as tmp_cwd:
        _RUN_TMP_CWD = tmp_cwd
        try:
            for n in range(1, max_fixes + 2 + (1 if polish else 0)):
                print(f"\n[direct] call {n} ({kind}, provider=claude) ...", flush=True)
                c = call_claude(system, text, images, model, session)
                session = c.get("session")
                u = c.get("usage") or {}
                actual_model = str(c.get("actual_model") or model)
                report["model"] = actual_model
                report["calls"].append({"n": n, "kind": kind, "seconds": c["seconds"], **u, "error": c["error"]})
                if not (c["error"] and "not installed" in str(c["error"])):
                    known = bool(u and (u.get("total", 0) > 0 or not c["error"]))
                    _append_direct_usage(proj, "claude", actual_model, u, usage_known=known, stage="direct planning")
                print(f"[direct] call {n}: {u.get('total', 0):,} tokens (input {u.get('input', 0):,}, cache write {u.get('cache_write', 0):,}, "
                      f"cache read {u.get('cache_read', 0):,}, output {u.get('output', 0):,}) in {c['seconds']}s", flush=True)
                if c["error"]:
                    report["error"] = c["error"]
                    print(f"[direct] Claude did not answer: {c['error'][:400]}")
                    break
                blocks = parse_answer(c["text"])
                if not blocks.get("plan"):
                    if n > max_fixes:
                        break
                    text, images, kind = "Your answer had no ```plan block. Answer again with every block, complete.", [], "format fix"
                    continue
                write_answer(proj, blocks, original_header)
                report["notes"] = blocks.get("notes", "")
                ok, out = run_check(proj)
                report["checks"] = report.get("checks", 0) + 1
                print("[direct] plan check: " + ("PASS" if ok else "BLOCKED"), flush=True)
                if ok:
                    report["passed"] = True
                    passing = {f: _read(proj / f) for f in ("plan.edl", "script_analysis.md", "decisions.txt")}
                    adv = _advice(out)
                    if polish and adv and kind != "advice":
                        text, images, kind = ("The plan check passed. It printed this advice; fix it, or keep it on purpose and say why "
                                              "in that segment's note. Answer again with every block, complete.\n\n" + "\n".join(adv)[:6000]), [], "advice"
                        continue
                    break
                if previous_failure is not None and out == previous_failure:
                    report["stopped"] = "The same check failure repeated; stopped automatic corrections."
                    print("[direct] same blocker repeated; stopping early.", flush=True)
                    break
                previous_failure = out
                if n > max_fixes:
                    break
                text, images, kind = ("The plan check blocked this plan. Fix every blocking problem and answer again with every block, "
                                      "complete.\n\n" + out[-9000:]), [], "fix"
        finally:
            _RUN_TMP_CWD = None

    if passing and not run_check(proj)[0]:   # a later round broke a plan that had passed: keep the one that passed
        for f, t in passing.items():
            (proj / f).write_text(t, encoding="utf-8")
        report["kept_earlier_pass"] = True
    report["passed"] = run_check(proj)[0]
    if not report["passed"] and not passing:
        (proj / "plan.edl").write_text(original, encoding="utf-8")
    tot = {k: sum(int(c.get(k) or 0) for c in report["calls"]) for k in ("input", "cache_write", "cache_read", "output", "total")}
    report["tokens"] = tot
    report["seconds"] = round(time.time() - report["started"], 1)
    (proj / "direct_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    print_report(report)
    return report


def run_codex(proj: Path, model: str = DEFAULT_CODEX_MODEL, max_fixes: int = 2, polish: bool = False) -> Dict[str, Any]:
    """Isolated 1-call Codex subscription planner (`codex exec` in an empty temporary directory)."""
    proj = proj.resolve()
    original = _read(proj / "plan.edl")
    if not original:
        raise SystemExit(f"No plan.edl in {proj}: run zeno.py --inspect first")
    original_header = "\n".join(header_of(original)) + "\n"
    system, job, sheets = build_brief(proj)
    (proj / "direct_brief.md").write_text(system + "\n\n---\n\n" + job, encoding="utf-8")
    report: Dict[str, Any] = {
        "project": proj.name, "provider": "codex", "model": model,
        "started": time.time(), "calls": [], "passed": False,
    }
    passing: Dict[str, str] = {}
    previous_failure: Optional[str] = None
    last_answer = ""
    text, images, kind = job, sheets, "plan"

    for n in range(1, max_fixes + 2 + (1 if polish else 0)):
        print(f"\n[direct] call {n} ({kind}, provider=codex) ...", flush=True)
        c = call_codex(system, text, images, model=model)
        u = c.get("usage") or {}
        actual_model = str(c.get("actual_model") or model)
        report["model"] = actual_model
        report["calls"].append({"n": n, "kind": kind, "seconds": c["seconds"], **u, "error": c["error"]})
        if not (c["error"] and "not installed" in str(c["error"])):
            known = bool(c.get("usage_known", bool(u and u.get("total", 0) > 0)))
            _append_direct_usage(proj, "codex", actual_model, u, usage_known=known, stage="direct planning")
        print(f"[direct] call {n}: {u.get('total', 0):,} tokens (input {u.get('input', 0):,}, cache write {u.get('cache_write', 0):,}, "
              f"cache read {u.get('cache_read', 0):,}, output {u.get('output', 0):,}) in {c['seconds']}s", flush=True)
        if c["error"]:
            report["error"] = c["error"]
            print(f"[direct] Codex did not answer: {c['error'][:400]}")
            break
        last_answer = c["text"]
        blocks = parse_answer(last_answer)
        if not blocks.get("plan"):
            if n > max_fixes:
                break
            text = job + "\n\nYour previous answer had no ```plan block. Answer again with every block, complete."
            images, kind = [], "format fix"
            continue
        write_answer(proj, blocks, original_header)
        report["notes"] = blocks.get("notes", "")
        ok, out = run_check(proj)
        report["checks"] = report.get("checks", 0) + 1
        print("[direct] plan check: " + ("PASS" if ok else "BLOCKED"), flush=True)
        if ok:
            report["passed"] = True
            passing = {f: _read(proj / f) for f in ("plan.edl", "script_analysis.md", "decisions.txt")}
            adv = _advice(out)
            if polish and adv and kind != "advice":
                text = (job + "\n\nCURRENT CANDIDATE:\n" + last_answer + "\n\nPLAN CHECK ADVICE:\n"
                        "The plan check passed. It printed this advice; fix it, or keep it on purpose and say why "
                        "in that segment's note. Answer again with every block, complete.\n\n" + "\n".join(adv)[:6000])
                images, kind = [], "advice"
                continue
            break
        if previous_failure is not None and out == previous_failure:
            report["stopped"] = "The same check failure repeated; stopped automatic corrections."
            print("[direct] same blocker repeated; stopping early.", flush=True)
            break
        previous_failure = out
        if n > max_fixes:
            break
        # Self-contained retry prompt without re-sending clip sheet images.
        text = (job + "\n\nCURRENT CANDIDATE:\n" + last_answer + "\n\nCHECK FAILURES:\n"
                "The plan check blocked this plan. Fix every blocking problem and answer again with every block, "
                "complete.\n\n" + out[-9000:])
        images, kind = [], "fix"

    if passing and not run_check(proj)[0]:
        for f, t in passing.items():
            (proj / f).write_text(t, encoding="utf-8")
        report["kept_earlier_pass"] = True
    report["passed"] = run_check(proj)[0]
    if not report["passed"] and not passing:
        (proj / "plan.edl").write_text(original, encoding="utf-8")
    tot = {k: sum(int(c.get(k) or 0) for c in report["calls"]) for k in ("input", "cache_write", "cache_read", "output", "total")}
    report["tokens"] = tot
    report["seconds"] = round(time.time() - report["started"], 1)
    (proj / "direct_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    print_report(report)
    return report


def print_report(r: Dict[str, Any]) -> None:
    label = str(r.get("provider") or "claude").capitalize()
    print("\n" + "=" * 92)
    print(f"DIRECT MODE: {r['project']} | provider {r.get('provider', 'claude')} | model {r['model']} | "
          f"plan {'PASSED' if r['passed'] else 'NOT PASSED'} | "
          f"{len(r['calls'])} {label} call(s), {r.get('checks', 0)} plan check(s), {r.get('seconds', 0) / 60:.1f} min")
    print("-" * 92)
    print(f"{'call':<5} {'kind':<11} {'input':>9} {'cache write':>12} {'cache read':>12} {'output':>9} {'total':>10} {'sec':>6}")
    for c in r["calls"]:
        print(f"{c['n']:<5} {c['kind']:<11} {c.get('input', 0):>9,} {c.get('cache_write', 0):>12,} {c.get('cache_read', 0):>12,} "
              f"{c.get('output', 0):>9,} {c.get('total', 0):>10,} {c.get('seconds', 0):>6}")
    t = r["tokens"]
    print("-" * 92)
    print(f"{'ALL':<17} {t['input']:>9,} {t['cache_write']:>12,} {t['cache_read']:>12,} {t['output']:>9,} {t['total']:>10,}")
    print("=" * 92)
    if r.get("notes"):
        print(f"\nNOTES FROM THE EDITOR ({label}):\n" + r["notes"][:4000])


def brief_only(proj: Path) -> None:
    system, job, sheets = build_brief(proj.resolve())
    (proj / "direct_brief.md").write_text(system + "\n\n---\n\n" + job, encoding="utf-8")
    est = lambda s: len(s.encode("utf-8")) // 3   # noqa: E731  rough: English ~4 bytes/token, Indic text ~2-3
    print(f"System prompt (rules): {len(system):,} chars (~{est(system):,} tokens)")
    print(f"Job message: {len(job):,} chars (~{est(job):,} tokens)")
    print(f"Pictures: {len(sheets)} (~{len(sheets) * 500:,} tokens)")
    print(f"First call, roughly: ~{est(system) + est(job) + len(sheets) * 500:,} tokens in. Written to {proj / 'direct_brief.md'}")


def dry_run_cli(proj: Path, provider: str, model: Optional[str]) -> int:
    """Validate subscription CLI binary, isolation flags, and brief size without any live model call."""
    resolved = resolve_provider(provider)
    if resolved == "claude":
        exe = shutil.which("claude")
        chosen_model = model or os.environ.get("ZENO_CLAUDE_MODEL") or DEFAULT_CLAUDE_MODEL
        if not exe:
            print("[DRY-RUN] provider=claude: binary NOT FOUND (`claude` is not on PATH)")
            return 2
        flags = _claude_flags(exe, chosen_model, "<system>", None)
        print(f"[DRY-RUN] provider=claude | binary={exe} | model={chosen_model} | isolated_cwd=<empty_tempdir> | bare=False | no_chrome={'--no-chrome' in flags}")
    else:
        exe = find_codex_binary()
        chosen_model = model or os.environ.get("ZENO_CODEX_MODEL") or DEFAULT_CODEX_MODEL
        if not exe:
            print("[DRY-RUN] provider=codex: binary NOT FOUND (`codex` not found on PATH or Desktop app bundle)")
            return 2
        sample = _codex_exec_flags(exe, "<empty_tempdir>", Path("<out_file>"), [], chosen_model)
        iso = [f for f in ("--skip-git-repo-check", "--ephemeral", "--ignore-user-config", "--ignore-rules", "--disable") if f in sample]
        print(f"[DRY-RUN] provider=codex | binary={exe} | model={chosen_model} | isolated_cwd=<empty_tempdir> | flags={','.join(iso)}")
    brief_only(proj)
    return 0


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("project", help="work/<project> (after zeno.py --inspect)")
    ap.add_argument("--provider", choices=("auto", "claude", "codex", "openai"), default="auto",
                    help="Subscription CLI (`auto`, `claude`, `codex`) or bounded OpenAI API (`openai`)")
    ap.add_argument("--model", default=None)
    ap.add_argument("--dry-run", action="store_true", help="Validate binary/reserve and brief size without a model call")
    ap.add_argument("--fixes", type=int, default=2, help="most fix rounds after the first plan (default: 2)")
    ap.add_argument("--polish", action="store_true", help="opt-in extra round to clear non-blocking [TIGHT]/[STYLE] advice")
    ap.add_argument("--no-polish", action="store_true", help="skip non-blocking [TIGHT]/[STYLE] advice round (default)")
    ap.add_argument("--brief-only", action="store_true")
    a = ap.parse_args()
    proj = Path(a.project)
    if not proj.is_absolute():
        proj = ROOT / proj
    if a.brief_only:
        brief_only(proj)
        return
    if a.provider == "openai":
        import sol_direct
        if a.model and a.model != sol_direct.MODEL:
            ap.error("The bounded planner currently supports gpt-6.1-sol only; no silent model switch.")
        try:
            r = sol_direct.run(proj, dry_run=a.dry_run)
        except (ValueError, OSError, KeyError) as exc:
            ap.error(str(exc))
        summary = r if a.dry_run else {"model": r["model"], "passed": r["passed"], "calls": len(r["calls"]),
                                      "stopped": r.get("stopped"), "review_required": r.get("review_required"),
                                      "report": str(proj / "direct_report.json")}
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        sys.exit(0 if r.get("passed") or r.get("dry_run") else 2)
    try:
        provider = resolve_provider(a.provider)
    except RuntimeError as exc:
        ap.error(str(exc))
    if a.dry_run:
        sys.exit(dry_run_cli(proj, provider, a.model))
    do_polish = bool(a.polish and not a.no_polish)
    if provider == "codex":
        chosen = a.model or os.environ.get("ZENO_CODEX_MODEL") or DEFAULT_CODEX_MODEL
        r = run_codex(proj, model=chosen, max_fixes=a.fixes, polish=do_polish)
    else:
        chosen = a.model or os.environ.get("ZENO_CLAUDE_MODEL") or DEFAULT_CLAUDE_MODEL
        r = run(proj, model=chosen, max_fixes=a.fixes, polish=do_polish)
    sys.exit(0 if r["passed"] else 1)


if __name__ == "__main__":
    main()
