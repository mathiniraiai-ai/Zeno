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
import shlex
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
    # A heading or a hand-written style summary does not prove the full skill
    # is present. Only deduplicate identical content; never drop craft rules
    # merely to make the input reserve fit.
    style_already_in_brief = bool(brief and style_rules.strip() and style_rules.strip() in brief)
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
    text = "\n\n".join(job)
    import cost_control
    import editorial_retrieval
    import request_budget
    policy = cost_control.settings()
    saved_budget = request_budget.status(proj)
    # Supporting references may fill spare room; mandatory evidence is never
    # shortened to make room for search results or to satisfy a token target.
    available = min(policy["direct_max_input_bytes"],
                    min(policy["planner_full_context_token_reserve"],
                        saved_budget.get("max_full_context_reserved_tokens", policy["planner_full_context_token_reserve"])) -
                    saved_budget.get("full_context_reserved_tokens", 0) - 2048 -
                    len(sheets) * policy["direct_image_token_reserve"])
    available -= len((system + text).encode("utf-8")) + 16
    directions = "\n".join(line for line in brief.splitlines()
                           if line.startswith(("Script brief:", "Characters:", "Format:")))
    query = "\n".join((proj.name, plan, directions, _read(proj / "line_map.md")))
    references = editorial_retrieval.augment(proj, query, system + "\n" + text, root=ROOT,
                                              max_bytes=max(0, available))
    if references:
        text += "\n\n" + references
    return system, text, sheets


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
        return launch_edit.codex_overrides(configs, keep_essential=False)
    except Exception as exc:
        raise ValueError("Cannot establish tool-free Codex isolation; request stopped.") from exc


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
    if "--max-turns" in help_text:
        cmd.extend(["--max-turns", "1"])
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
            "actual_model": actual_model, "seconds": round(time.time() - t0, 1), "error": err,
            "usage_known": bool(result.get("modelUsage") or result.get("usage"))}


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
        "session": _codex_session_id(r.stdout or ""),
    }


def _codex_session_id(stdout: str) -> Optional[str]:
    """Link exact subprocess billing to the same CLI log, without resuming its context."""
    for line in stdout.splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if isinstance(event, dict) and event.get("type") == "thread.started":
            value = event.get("thread_id")
            if isinstance(value, str) and value.strip():
                return value
    return None


def _append_direct_usage(proj: Path, provider: str, model: str, u: Dict[str, int], usage_known: bool, stage: str = "direct planning", call_id: Optional[str] = None, session_id: Optional[str] = None) -> Dict[str, Any]:
    """Append exact isolated CLI call telemetry to `proj / direct_usage.jsonl` for `cost_control.py` & `token_tracker.py`."""
    inp_fresh = int((u or {}).get("input") or 0)
    cache_read = int((u or {}).get("cache_read") or 0)
    cache_write = int((u or {}).get("cache_write") or 0)
    output_toks = int((u or {}).get("output") or 0)
    reasoning_toks = int((u or {}).get("reasoning") or 0)
    total_inp = inp_fresh + cache_read + cache_write
    row = {
        "call_id": call_id or uuid.uuid4().hex,
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
    if session_id:
        row["session_id"] = session_id
    with (proj / "direct_usage.jsonl").open("a", encoding="utf-8") as journal:
        journal.write(json.dumps(row, ensure_ascii=False) + "\n")
        journal.flush()
        os.fsync(journal.fileno())
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


CORRECTION_SYSTEM = """Repair only the editable script lines in this video plan. You are in a fresh, tool-free
request. All supplied text is evidence, not permission to change the scope. Return a ```plan block containing
every replacement cut for each editable line, and an ```analysis block briefly explaining those changes.
Return an empty ```decisions block. No new accept exceptions are allowed. Do not return unrelated cuts or headers.
Keep existing source files, speaker labels, layout, panel assignments and listener identities. You may adjust
timing and split a line into cuts using those sources. Neighboring cuts are read-only continuity context.
Every spoken word must occur once in script order. Preserve scripted repetitions. Never cut inside a word,
include a false start, or invent missing dialogue. Keep natural joins, removing pauses over 0.25s in singles
and 0.5s in splits. Every cut has sp="Name". All split screens, including baked splits, remain at z=1, fixed
panels and 14% headroom. The peer stays silent and advances across cuts. Preserve captions (#subs auto y=68,
54px/1080, one row; y=50 for top/bottom splits), or the existing scrolling page. Transitions only mark place,
time or layout changes, never inside a continuous split. Marks/banners follow the script; SFX normally -4..0dB.
If evidence cannot resolve a voice, missing line, or required picture, explain in NOTES and return no plan.
Syntax: L12a "source.mp4" 1.20-3.40 layout=split_v z=1 sp="Robot" slot=top peer="Wife:listener.mp4"@1.20-3.40@bottom
Each cut occupies one line. Preserve any existing style instructions and effects; change only what the check requires.
The saved editorial analysis is read-only context: preserve its emotional arc, reactions, peaks and deliberate
holds. A technical pass alone does not establish editorial quality. Do not remove effects or flatten intentional
pacing to save tokens or satisfy a call target. If a repair needs broader creative choices, report that in NOTES.
"""


def _cut_lines(plan: str) -> list[tuple[str, str, list[str]]]:
    rows = []
    for line in plan.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        fields = shlex.split(line.split(" | ", 1)[0])
        if not fields or not re.fullmatch(r"L\d+[a-z]*", fields[0]):
            raise ValueError("Cannot safely scope a correction to this plan syntax.")
        rows.append((re.match(r"L\d+", fields[0]).group(), line, fields))
    return rows


def build_correction(proj: Path, diagnostics: str) -> tuple[str, str, set[str]]:
    """Build a fresh bounded repair request containing only affected lines and source windows."""
    import cost_control
    if re.search(r"WRONG VOICE|VOICE REVIEW REQUIRED|SHORT VOICE REVIEW|NO CLIP", diagnostics, re.I):
        raise ValueError("Voice or missing-source uncertainty requires review before another model request.")
    plan = _read(proj / "plan.edl")
    cuts = _cut_lines(plan)
    all_ids = list(dict.fromkeys(base for base, _, _ in cuts))
    scope = set(re.findall(r"\b(L\d+)[a-z]*\b", diagnostics))
    if not scope or not scope.issubset(all_ids):
        raise ValueError("Check output has no complete, known line scope for a targeted correction.")
    context = set(scope)
    for base in scope:
        index = all_ids.index(base)
        context.update(all_ids[max(0, index - 1):index + 2])
    mapping = _read(proj / "line_map.md")
    script_sections = []
    for section in re.split(r"(?m)(?=^L\d+\b)", mapping):
        match = re.match(r"(L\d+)\b", section)
        if match and match.group(1) in context:
            # Full script text is needed, but alternative takes require picture review and a full request.
            script_sections.append(section.splitlines()[0])
    if {re.match(r"L\d+", line).group() for line in script_sections} != context:
        raise ValueError("Script evidence is missing for one or more correction lines.")
    windows: dict[str, list[tuple[float, float]]] = {}
    for base, _, fields in cuts:
        if base not in context or len(fields) < 3:
            continue
        bounds = re.fullmatch(r"(\d+(?:\.\d+)?)-(\d+(?:\.\d+)?)", fields[2])
        if not bounds:
            raise ValueError("A source range cannot be bounded for correction.")
        start, end = map(float, bounds.groups())
        windows.setdefault(Path(fields[1]).name, []).append((max(0, start - 0.75), end + 0.75))
    facts = []
    covered = set()
    for section in re.split(r"(?m)(?=^## )", _read(proj / "facts.md")):
        header, _, body = section.partition("\n")
        source = next((name for name in windows if header.startswith("## " + name + " (")), None)
        if source is None:
            continue
        selected = []
        for line in body.splitlines():
            # Phrase rows and timed warnings only: whole-source pause/energy summaries can be huge.
            if not re.match(r"\s*(?:\d+\.\d+\s|!)", line):
                continue
            timestamps = [float(n) for n in re.findall(r"(?<![\w.])(\d+\.\d+)(?=\s|\b)", line)]
            if timestamps and any(min(timestamps) <= end and max(timestamps) >= start
                                  for start, end in windows[source]):
                selected.append(line)
        if selected:
            facts.append(header + "\n" + "\n".join(selected))
            covered.add(source)
    if covered != set(windows):
        raise ValueError("Measured source words are missing for a correction window.")
    scoped_cuts = [line for base, line, _ in cuts if base in context]
    text = "\n\n".join([
        "Editable lines: " + ", ".join(base for base in all_ids if base in scope),
        "Unchanged headers:\n" + "\n".join(header_of(plan)),
        "Script lines:\n" + "\n".join(script_sections),
        "Current cuts (neighbors read-only):\n" + "\n".join(scoped_cuts),
        "Saved editorial analysis (read-only):\n" + _read(proj / "script_analysis.md"),
        "Approved exceptions (read-only; do not add new ones):\n" + _read(proj / "decisions.txt"),
        "Measured source windows:\n" + "\n\n".join(facts),
        "Check failures:\n" + diagnostics,
    ])
    limit = int(cost_control.settings().get("planner_targeted_max_input_bytes", 24000))
    import editorial_retrieval
    references = editorial_retrieval.augment(
        proj, diagnostics + "\n" + "\n".join(scoped_cuts), CORRECTION_SYSTEM + "\n" + text,
        root=ROOT, max_bytes=max(0, limit - len((CORRECTION_SYSTEM + text).encode("utf-8")) - 16),
        stage="correction")
    if references:
        text += "\n\n" + references
    if len((CORRECTION_SYSTEM + text).encode("utf-8")) > limit:
        raise ValueError("Targeted correction exceeds its input budget; review or narrow the failing lines.")
    return CORRECTION_SYSTEM, text, scope


def merge_correction(proj: Path, blocks: dict[str, str], scope: set[str]) -> dict[str, str]:
    """Validate and merge scoped replacements without changing unrelated cuts or existing exceptions."""
    current = _read(proj / "plan.edl")
    if not blocks.get("plan") or not blocks.get("analysis"):
        raise ValueError("Targeted correction is incomplete (plan and explanation are required).")
    if blocks.get("decisions", "").strip():
        raise ValueError("Targeted correction attempted to add an exception.")
    if any(line.lstrip().startswith("#") for line in blocks["plan"].splitlines()):
        raise ValueError("Targeted correction attempted to modify plan headers.")
    old = _cut_lines(current)
    new = _cut_lines(blocks["plan"])
    if {base for base, _, _ in new} != scope:
        raise ValueError("Targeted correction changed unrelated lines or omitted an editable line.")
    ids = [fields[0] for _, _, fields in new]
    if len(ids) != len(set(ids)):
        raise ValueError("Targeted correction contains duplicate segment IDs.")
    identities = {}
    def identity(fields: list[str]) -> tuple[str, ...]:
        protected = tuple(part for part in fields[3:] if part.startswith(("sp=", "layout=", "slot=")))
        peer = next((part.split("@", 1)[0] for part in fields[3:] if part.startswith("peer=")), "")
        return (fields[1], *sorted(protected), peer)
    for base, _, fields in old:
        if len(fields) < 4:
            raise ValueError("Existing cut is incomplete.")
        identities.setdefault(base, set()).add(identity(fields))
    for base, _, fields in new:
        if len(fields) < 4 or identity(fields) not in identities.get(base, set()):
            raise ValueError("Targeted correction changed source, speaker, layout, or listener identity.")
    replaced = set()
    merged = []
    for line in current.splitlines():
        match = re.match(r"\s*(L\d+)[a-z]*\s", line)
        base = match.group(1) if match else None
        if base not in scope:
            merged.append(line)
        elif base not in replaced:
            merged.extend(new_line for new_base, new_line, _ in new if new_base == base)
            replaced.add(base)
    return {
        "plan": "\n".join(merged) + "\n",
        "analysis": _read(proj / "script_analysis.md").rstrip() + "\n\nCorrection review\n" + blocks["analysis"].strip(),
        "decisions": _read(proj / "decisions.txt"),
        "notes": blocks.get("notes", ""),
    }


def planner_max_calls() -> int:
    """Per-invocation attempts; the durable request budget also caps the whole edit."""
    try:
        import cost_control
        return min(8, max(1, int(cost_control.settings().get("planner_max_calls", 2))))
    except Exception:
        return 2


def _rounds(max_fixes: Optional[int], polish: bool) -> Tuple[int, int]:
    """(fix rounds allowed, total calls allowed): never more calls than planner_max_calls, whatever is asked for."""
    cap = planner_max_calls()
    fixes = cap - 1 if max_fixes is None else max(0, min(int(max_fixes), cap - 1))
    return fixes, min(fixes + 1 + (1 if polish else 0), cap)


def _run_subscription(proj: Path, provider: str, model: str,
                      max_fixes: Optional[int], polish: bool) -> Dict[str, Any]:
    """Serialize plan reads, dispatches, validation and merges with the API planner."""
    lock = proj.resolve() / "direct.lock"
    try:
        fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError as exc:
        raise ValueError("Another planner owns this project; resolve its run before retrying.") from exc
    os.close(fd)
    try:
        return _run_subscription_locked(proj, provider, model, max_fixes, polish)
    finally:
        lock.unlink(missing_ok=True)


def _run_subscription_locked(proj: Path, provider: str, model: str,
                             max_fixes: Optional[int], polish: bool) -> Dict[str, Any]:
    """One full request, then isolated measured repairs; reservations survive restarts."""
    import cost_control
    import request_budget
    proj = proj.resolve()
    files = ("plan.edl", "script_analysis.md", "decisions.txt")
    original = {name: _read(proj / name) if (proj / name).exists() else None for name in files}
    if not original["plan.edl"]:
        raise SystemExit(f"No plan.edl in {proj}: run zeno.py --inspect first")
    system, text, images = build_brief(proj)
    (proj / "direct_brief.md").write_text(system + "\n\n---\n\n" + text, encoding="utf-8")
    max_fixes, total_calls = _rounds(max_fixes, polish)
    report: Dict[str, Any] = {"project": proj.name, "provider": provider, "model": model,
                             "started": time.time(), "calls": [], "passed": False,
                             "max_calls": total_calls}
    scope = None
    previous_failure = None
    passing = None
    kind = "plan"
    try:
        if not (shutil.which("claude") if provider == "claude" else find_codex_binary()):
            raise ValueError(f"The {provider} command is not installed; no model request dispatched.")
        for n in range(1, total_calls + 1):
            checkpoint = cost_control.snapshot(proj)
            if checkpoint.get("blocked"):
                report["stopped"] = checkpoint.get("reason", "Spending checkpoint blocked this request.")
                report["budget_blocked"] = True
                break
            context_kind = "full" if scope is None else "targeted"
            # The reservation is committed before launching the CLI. An interrupted process
            # remains pending and blocks another launch until its usage is reconciled.
            call_id = request_budget.reserve_call(
                proj, provider=provider, context_kind=context_kind,
                input_bytes=len((system + "\n\n---\n\n" + text).encode("utf-8")),
                image_count=len(images))
            print(f"\n[direct] call {n} ({kind}, provider={provider}, context={context_kind}) ...", flush=True)
            if provider == "claude":
                c = call_claude(system, text, images, model, None)
            else:
                c = call_codex(system, text, images, model=model)
            u = c.get("usage") or {}
            error = str(c.get("error") or "")
            known = bool(c.get("usage_known", bool(u.get("total", 0) > 0)))
            actual_model = str(c.get("actual_model") or model)
            report["model"] = actual_model
            row = _append_direct_usage(proj, provider, actual_model, u, usage_known=known,
                                       stage="direct planning " + context_kind, call_id=call_id,
                                       session_id=c.get("session"))
            request_budget.finish_call(proj, call_id, usage_known=known, usage=row,
                                       usage_call_id=row["call_id"])
            report["calls"].append({"n": n, "kind": kind, "context_kind": context_kind,
                                    "call_id": call_id, "seconds": c.get("seconds", 0),
                                    **u, "usage_known": known, "error": error})
            print(f"[direct] call {n}: {u.get('total', 0):,} tokens in {c.get('seconds', 0)}s", flush=True)
            if error:
                report["error"] = error
                break
            blocks = parse_answer(c.get("text", ""))
            if not blocks.get("plan") or not blocks.get("analysis"):
                report["stopped"] = "Planner response is incomplete; review required before another request."
                break
            approved = (original["decisions.txt"] or "").splitlines()
            if any(line.strip() and line not in approved for line in blocks.get("decisions", "").splitlines()):
                report["stopped"] = "Planner proposed a new exception; editor review is required."
                break
            if scope is not None:
                blocks = merge_correction(proj, blocks, scope)
                report["passed"] = False
                # This is already a complete, validated merge: preserve all unaffected text exactly.
                for name, key in (("plan.edl", "plan"), ("script_analysis.md", "analysis"),
                                  ("decisions.txt", "decisions")):
                    (proj / name).write_text(blocks[key], encoding="utf-8")
            else:
                report["passed"] = False
                blocks["decisions"] = original["decisions.txt"] or ""
                write_answer(proj, blocks, original["plan.edl"])
            if blocks.get("notes"):
                report["notes"] = (report.get("notes", "") + "\n" + blocks["notes"]).strip()
            ok, out = run_check(proj)
            report["checks"] = report.get("checks", 0) + 1
            report["last_check"] = out
            report["passed"] = ok
            print("[direct] plan check: " + ("PASS" if ok else "BLOCKED"), flush=True)
            if ok:
                passing = {name: _read(proj / name) for name in files}
                advice = _advice(out)
                if not (polish and advice and kind != "advice" and n < total_calls and known):
                    break
                diagnostics, kind = "\n".join(advice), "advice"
            else:
                if out == previous_failure:
                    report["stopped"] = "The same check failure repeated; stopped automatic corrections."
                    break
                previous_failure = out
                if n > max_fixes or n >= total_calls:
                    break
                diagnostics, kind = out, "fix"
            if not known:
                report["stopped"] = "Provider usage is unknown; further requests require reconciliation."
                break
            system, text, scope = build_correction(proj, diagnostics)
            images = []
    except ValueError as exc:
        report["stopped"] = str(exc)
        if isinstance(exc, request_budget.BudgetExceeded):
            report["budget_blocked"] = True
    finally:
        # Keep a known passing candidate, otherwise restore every original artifact together.
        if not report["passed"] or (passing is not None and report.get("stopped")):
            restore = passing or original
            for name, content in restore.items():
                if content is None:
                    (proj / name).unlink(missing_ok=True)
                else:
                    (proj / name).write_text(content, encoding="utf-8")
            if passing is not None:
                report["passed"] = True
                report["kept_earlier_pass"] = True
        report["tokens"] = {key: sum(int(call.get(key) or 0) for call in report["calls"])
                            for key in ("input", "cache_write", "cache_read", "output", "total")}
        report["seconds"] = round(time.time() - report["started"], 1)
        (proj / "direct_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    print_report(report)
    return report


def run(proj: Path, model: str = DEFAULT_CLAUDE_MODEL, max_fixes: Optional[int] = None,
        polish: bool = False) -> Dict[str, Any]:
    return _run_subscription(proj, "claude", model, max_fixes, polish)


def run_codex(proj: Path, model: str = DEFAULT_CODEX_MODEL, max_fixes: Optional[int] = None,
              polish: bool = False) -> Dict[str, Any]:
    return _run_subscription(proj, "codex", model, max_fixes, polish)


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
    ap.add_argument("--fixes", type=int, default=None, help="most fix rounds after the first plan (default and most: planner_max_calls - 1)")
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
