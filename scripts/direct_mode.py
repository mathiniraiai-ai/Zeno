#!/usr/bin/env python3
"""Tool-free planning entry point. Default: bounded GPT-6.1 Sol via Responses API.

Use --dry-run to inspect the reserve without a paid call. OpenAI requires a separately
configured OPENAI_API_KEY and separate API billing. See LOW_COST_WORKFLOW.md.
The older Claude subscription CLI implementation remains behind --provider claude.

Why: in a Claude Code chat every step re-sends Claude Code's own instructions and tools (about 56k tokens) plus the
whole chat, and an edit takes 15 to 65 steps, so one 90 s video re-read 1 to 9 million tokens (16 Nov). The decisions
themselves are small. Here Zeno sends one brief (the edit rules, the format, the measured line map and facts, the clip
sheet pictures), Sonnet answers with the script analysis, the plan and any accepted exceptions, Zeno runs the plan
check itself, and only the check's problems go back for a fix. Same model, same rules, same checks.

    .venv/bin/python scripts/direct_mode.py work/<project>                # plan it (1 call + fixes), check it, report tokens
    .venv/bin/python scripts/direct_mode.py work/<project> --brief-only   # build the brief and show its size; no Claude call

Claude is reached through the `claude` command (the editor's own Claude plan, no API key), with no tools, no MCP
servers, no skills, no settings and Zeno's own system prompt. Writes script_analysis.md, plan.edl, decisions.txt,
direct_brief.md and direct_report.json in the project folder. Rendering stays `zeno.py --execute`.
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

PY = sys.executable
CALL_TIMEOUT = 1200
HEADER_KEYS = ("#output", "#script", "#src_dir", "#canvas", "#style")   # owned by Zeno, never taken from Claude's answer

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
    # A tool-free director cannot open the requested style later. Keep its rules,
    # while avoiding another copy of all generic editing references.
    system = SYSTEM + "\n" + ((brief + "\n" + style_rules) if brief else rules_text(style))
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


def _message(text: str, images: List[Path]) -> Dict[str, Any]:
    content: List[Dict[str, Any]] = []
    for p in images:
        content.append({"type": "image", "source": {"type": "base64", "media_type": "image/jpeg",
                                                    "data": base64.b64encode(p.read_bytes()).decode()}})
    content.append({"type": "text", "text": text})
    return {"type": "user", "message": {"role": "user", "content": content}}


def call_claude(system: str, text: str, images: List[Path], model: str, session: Optional[str]) -> Dict[str, Any]:
    """One request to Claude through the `claude` command, with nothing but Zeno's prompt. Returns
    {"text", "session", "usage", "seconds", "error"}."""
    exe = shutil.which("claude")
    if not exe:
        return {"error": "the claude command is not installed", "text": "", "usage": {}, "session": session, "seconds": 0}
    cmd = [exe, "-p", "--model", model, "--input-format", "stream-json", "--output-format", "stream-json", "--verbose",
           "--system-prompt", system, "--tools", "", "--strict-mcp-config", "--disable-slash-commands", "--setting-sources", ""]
    if session:
        cmd += ["--resume", session]
    env = dict(os.environ, ENABLE_CLAUDEAI_MCP_SERVERS="false")
    t0 = time.time()
    try:
        r = subprocess.run(cmd, input=json.dumps(_message(text, images)) + "\n", capture_output=True, text=True,
                           timeout=CALL_TIMEOUT, cwd=str(ROOT), env=env, encoding="utf-8", errors="replace")
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
    err = "" if result and not result.get("is_error") else (result.get("result") or (r.stderr or "")[-600:] or f"claude exited with {r.returncode}")
    return {"text": str(result.get("result") or ""), "session": result.get("session_id") or session, "usage": usage,
            "seconds": round(time.time() - t0, 1), "error": err}


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


def run(proj: Path, model: str = "sonnet", max_fixes: int = 3, polish: bool = True) -> Dict[str, Any]:
    proj = proj.resolve()
    original = _read(proj / "plan.edl")
    if not original:
        raise SystemExit(f"No plan.edl in {proj}: run zeno.py --inspect first")
    original_header = "\n".join(header_of(original)) + "\n"
    system, job, sheets = build_brief(proj)
    (proj / "direct_brief.md").write_text(system + "\n\n---\n\n" + job, encoding="utf-8")
    report: Dict[str, Any] = {"project": proj.name, "model": model, "started": time.time(), "calls": [], "passed": False}
    session: Optional[str] = None
    passing: Dict[str, str] = {}
    text, images, kind = job, sheets, "plan"
    for n in range(1, max_fixes + 3):
        print(f"\n[direct] call {n} ({kind}) ...", flush=True)
        c = call_claude(system, text, images, model, session)
        session = c["session"]
        u = c["usage"]
        report["calls"].append({"n": n, "kind": kind, "seconds": c["seconds"], **u, "error": c["error"]})
        print(f"[direct] call {n}: {u.get('total', 0):,} tokens (input {u.get('input', 0):,}, cache write {u.get('cache_write', 0):,}, "
              f"cache read {u.get('cache_read', 0):,}, output {u.get('output', 0):,}) in {c['seconds']}s", flush=True)
        if c["error"]:
            report["error"] = c["error"]
            print(f"[direct] Claude did not answer: {c['error'][:400]}")
            break
        blocks = parse_answer(c["text"])
        if not blocks.get("plan"):
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
        if n > max_fixes:
            break
        text, images, kind = ("The plan check blocked this plan. Fix every blocking problem and answer again with every block, "
                              "complete.\n\n" + out[-9000:]), [], "fix"
    if passing and not run_check(proj)[0]:   # a later round broke a plan that had passed: keep the one that passed
        for f, t in passing.items():
            (proj / f).write_text(t, encoding="utf-8")
        report["kept_earlier_pass"] = True
    report["passed"] = run_check(proj)[0]
    tot = {k: sum(int(c.get(k) or 0) for c in report["calls"]) for k in ("input", "cache_write", "cache_read", "output", "total")}
    report["tokens"] = tot
    report["seconds"] = round(time.time() - report["started"], 1)
    (proj / "direct_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    print_report(report)
    return report


def print_report(r: Dict[str, Any]) -> None:
    print("\n" + "=" * 92)
    print(f"DIRECT MODE: {r['project']} | model {r['model']} | plan {'PASSED' if r['passed'] else 'NOT PASSED'} | "
          f"{len(r['calls'])} Claude call(s), {r.get('checks', 0)} plan check(s), {r.get('seconds', 0) / 60:.1f} min")
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
        print("\nNOTES FROM THE EDITOR (Claude):\n" + r["notes"][:4000])


def brief_only(proj: Path) -> None:
    system, job, sheets = build_brief(proj.resolve())
    (proj / "direct_brief.md").write_text(system + "\n\n---\n\n" + job, encoding="utf-8")
    est = lambda s: len(s.encode("utf-8")) // 3   # noqa: E731  rough: English ~4 bytes/token, Indic text ~2-3
    print(f"System prompt (rules): {len(system):,} chars (~{est(system):,} tokens)")
    print(f"Job message: {len(job):,} chars (~{est(job):,} tokens)")
    print(f"Pictures: {len(sheets)} (~{len(sheets) * 500:,} tokens)")
    print(f"First call, roughly: ~{est(system) + est(job) + len(sheets) * 500:,} tokens in. Written to {proj / 'direct_brief.md'}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("project", help="work/<project> (after zeno.py --inspect)")
    ap.add_argument("--provider", choices=("openai", "claude"), default="openai",
                    help="OpenAI is the bounded Sol API planner; Claude is the legacy CLI path")
    ap.add_argument("--model", default=None)
    ap.add_argument("--dry-run", action="store_true", help="Validate size and budget reserve without a model call")
    ap.add_argument("--fixes", type=int, default=3, help="most fix rounds after the first plan")
    ap.add_argument("--no-polish", action="store_true", help="skip the round that clears [TIGHT]/[STYLE] advice")
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
    if a.dry_run:
        brief_only(proj)
        return
    r = run(proj, model=a.model or "sonnet", max_fixes=a.fixes, polish=not a.no_polish)
    sys.exit(0 if r["passed"] else 1)


if __name__ == "__main__":
    main()
