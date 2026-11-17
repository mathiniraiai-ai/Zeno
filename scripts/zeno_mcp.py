#!/usr/bin/env python3
"""Zeno connector for Claude (a local MCP server: Claude starts it on this computer and talks to it over standard input and output).

It needs no extra packages. It lets Claude (Claude Code, or Claude Desktop) look at Zeno's jobs and start the measuring step. It is small on purpose:
a few short tool descriptions cost about 100 tokens when Claude starts. Everything runs on this computer; the footage never leaves it.

    python3 scripts/zeno_mcp.py            # normally started by Claude, not by hand
    python3 scripts/zeno_mcp.py --selftest # prints the tool list and exits
"""
from __future__ import annotations

import importlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))
os.environ.setdefault("ZENO_EVENTS", "on")

PROTOCOL = "2024-11-05"
NAME = "zeno"


def _read_version() -> str:
    try:
        return (ROOT / "VERSION").read_text(encoding="utf-8").strip()
    except OSError:
        return "1.0.0"


_LOADED_VERSION = _read_version()


def _reload_if_updated() -> None:
    """Hot-reload Zeno Python modules and clear stale caches when an OTA update lands on disk."""
    global _LOADED_VERSION
    cur = _read_version()
    if cur == _LOADED_VERSION:
        return
    prev = _LOADED_VERSION
    _LOADED_VERSION = cur
    try:
        importlib.invalidate_caches()
        for mod_name in ("common", "clean", "jobs"):
            if mod_name in sys.modules:
                importlib.reload(sys.modules[mod_name])
        import clean as _clean
        _clean.cleanup_after_update(root=ROOT, version=cur)
    except Exception:
        pass
    sys.stderr.write(f"[zeno_mcp] hot-reloaded Zeno engine {prev} -> {cur}\n")


TOOLS: List[Dict[str, Any]] = [
    {"name": "zeno_jobs", "description": "List Zeno edits with their status (working, plan ready, rendered) and progress.",
     "inputSchema": {"type": "object", "properties": {"limit": {"type": "integer", "description": "how many, newest first (default 8)"}}}},
    {"name": "zeno_job", "description": "One edit in detail: stage, progress, plan summary, output files and the last steps.",
     "inputSchema": {"type": "object", "properties": {"name": {"type": "string", "description": "work folder name from zeno_jobs"}}, "required": ["name"]}},
    {"name": "zeno_start_edit", "description": "Run Zeno's 1-call inspect + isolated auto-planner + automatic 9:16 & 1:1 Square render (--inspect --auto-plan --compact) end-to-end without asking Yes/No. Returns the Pitch Card, verification result, output MP4 paths, and token report directly; do NOT read line_map.md, facts.md, editing_packet.md, or clip_sheet_*.jpg.",
     "inputSchema": {"type": "object", "properties": {"script": {"type": "string", "description": "Google Doc tab link (must end in ?tab=...) or a script file"},
                                                       "footage": {"type": "string", "description": "local folder with the raw clips"},
                                                       "name": {"type": "string", "description": "the Notion name of the video"},
                                                       "style": {"type": "string"}, "lang": {"type": "string"},
                                                       "wait_seconds": {"type": "integer", "description": "seconds to wait before continuing in background (default 480)"}},
                     "required": ["script", "footage", "name"]}},
    {"name": "zeno_auto_plan", "description": "Run Zeno's isolated 1-call planner + automatic render (--auto-plan --compact) on an already-inspected edit without asking Yes/No.",
     "inputSchema": {"type": "object", "properties": {"name": {"type": "string", "description": "work folder name from zeno_jobs"}}, "required": ["name"]}},
    {"name": "zeno_check_plan", "description": "Run the plan check on a job's plan and return its verdict and advice lines.",
     "inputSchema": {"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]}},
    {"name": "zeno_render", "description": "Render a verified plan (9:16 reel and 1:1 square) immediately without asking Yes/No, with automatic QC + post-job cleanup.",
     "inputSchema": {"type": "object", "properties": {"name": {"type": "string"},
                                                       "wait_seconds": {"type": "integer", "description": "seconds to wait before continuing in background (default 360)"}}, "required": ["name"]}},
]


def _py() -> str:
    venv = ROOT / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    return str(venv) if venv.exists() else sys.executable


def _text(s: str, error: bool = False) -> Dict[str, Any]:
    return {"content": [{"type": "text", "text": s}], "isError": error}


def _job_dir(name: str) -> Optional[Path]:
    if not name or "/" in name or "\\" in name or name.startswith("."):
        return None
    d = ROOT / "work" / name
    return d if d.is_dir() else None


def _short_job(j: Dict[str, Any]) -> str:
    st = j.get("status", {})
    pct = f" {j.get('pct', 0)}%" if st.get("phase") == "working" else ""
    return f"{j['name']} | {j.get('title', '')[:60]} | {st.get('label', '')}{pct}"


def _run_with_log_tail(cmd: List[str], log_path: Path, wait_s: int, max_chars: int = 7000) -> Tuple[Optional[int], str]:
    """Spawn `cmd` appending output to `log_path`, wait up to `wait_s`, and return `(returncode_or_None, new_log_tail)`."""
    log_path.parent.mkdir(parents=True, exist_ok=True)
    offset = log_path.stat().st_size if log_path.is_file() else 0
    fh = open(log_path, "ab")
    proc = subprocess.Popen(cmd, cwd=str(ROOT), stdout=fh, stderr=fh, start_new_session=True)
    if wait_s <= 0:
        fh.close()
        return None, ""
    try:
        rc = proc.wait(timeout=float(wait_s))
        fh.close()
    except subprocess.TimeoutExpired:
        fh.close()
        return None, ""
    try:
        with open(log_path, "rb") as rf:
            rf.seek(offset)
            chunk = rf.read().decode("utf-8", errors="replace")
    except OSError:
        chunk = ""
    lines = [ln for ln in chunk.splitlines() if "WARN:" not in ln and "global net_impl_backend" not in ln]
    joined = "\n".join(lines).strip()
    if len(joined) > max_chars:
        joined = "...\n" + joined[-max_chars:]
    return rc, joined


def call(name: str, a: Dict[str, Any]) -> Dict[str, Any]:
    _reload_if_updated()
    import jobs

    if name == "zeno_jobs":
        rows = jobs.list_jobs()[: int(a.get("limit") or 8)]
        return _text("\n".join(_short_job(j) for j in rows) or "No edits yet.")
    if name == "zeno_job":
        d = _job_dir(str(a.get("name", "")))
        if d is None:
            return _text("No such edit. Use zeno_jobs for the names.", True)
        x = jobs.detail(d.name)
        p = x.get("plan") or {}
        lines = [f"{x['title']}", f"status: {x['status']['label']} {x['status'].get('detail', '')}", f"stage {x.get('stage')} of 5, {x.get('pct', 0)}%"]
        if p:
            lines.append(f"plan: {p['cuts']} cuts, {p['seconds']} s, subtitles: {p.get('subs') or 'none'}")
        lines += [f"file: {o['name']} ({o['mb']} MB)" for o in x.get("outputs", []) if o["kind"] in ("reel", "square", "srt")]
        pitch_f = d / "pitch_card.md"
        if pitch_f.is_file() and p.get("cuts"):
            lines.append("pitch_card: ready (in pitch_card.md — do NOT re-open facts.md or line_map.md)")
        lines += ["last steps:"] + [f"  {e['kind']}: {str(e['text'])[:110]}" for e in x.get("events", [])[-6:]]
        return _text("\n".join(lines))
    if name == "zeno_start_edit":
        script, footage, title = str(a.get("script", "")).strip(), str(a.get("footage", "")).strip(), str(a.get("name", "")).strip()
        if not (script and footage and title):
            return _text("Need the script, the footage folder and the Notion name.", True)
        if script.startswith("http") and "tab=" not in script:
            return _text("The Google Doc link must end in ?tab=... (one tab, never the whole document).", True)
        if not Path(footage).expanduser().is_dir():
            return _text(f"Footage folder not found: {footage}", True)
        cmd = [
            _py(), str(ROOT / "scripts" / "zeno.py"),
            "--inspect", str(Path(footage).expanduser()),
            "--script", script,
            "--name", title,
            "--auto-plan", "--compact",
        ]
        for flag, key in (("--style", "style"), ("--lang", "lang")):
            if a.get(key):
                cmd += [flag, str(a[key])]
        wait_s = max(0, min(900, int(a.get("wait_seconds") if a.get("wait_seconds") is not None else 480)))
        log = ROOT / "work" / "_mcp_start.log"
        rc, tail = _run_with_log_tail(cmd, log, wait_s=wait_s)
        if rc is None:
            return _text(
                "Measuring + 1-call auto-planning + auto-render is running in the background (heavy clip batch). "
                "Wait ~60s before calling zeno_jobs ONCE — NEVER poll in a loop, NEVER ask Yes/No, and NEVER open line_map.md or facts.md."
            )
        rule = (
            "\n\n[ZENO 1-CALL RULE] Auto-render completed above (no Yes/No needed). Deliver the Pitch Card summary, "
            "output MP4 paths, and saved token report directly — do NOT open editing_packet.md, line_map.md, facts.md, EDIT_RULES.md, or clip_sheet_*.jpg."
        )
        return _text((tail or f"zeno_start_edit exited with code {rc}") + (rule if rc == 0 else ""), rc != 0)
    if name == "zeno_auto_plan":
        d = _job_dir(str(a.get("name", "")))
        plan = d / "plan.edl" if d else None
        if not plan or not plan.is_file():
            return _text("No plan.edl for that edit yet.", True)
        r = subprocess.run(
            [_py(), str(ROOT / "scripts" / "zeno.py"), "--auto-plan", "--plan", str(plan), "--compact"],
            cwd=str(ROOT), capture_output=True, text=True, timeout=420,
        )
        txt = (r.stdout + "\n" + r.stderr).strip()
        if len(txt) > 7000:
            txt = "...\n" + txt[-7000:]
        return _text(txt or "No output.", r.returncode != 0)
    if name == "zeno_check_plan":
        d = _job_dir(str(a.get("name", "")))
        plan = d / "plan.edl" if d else None
        if not plan or not plan.is_file():
            return _text("No plan.edl for that edit yet.", True)
        r = subprocess.run([_py(), str(ROOT / "scripts" / "zeno.py"), "--check-plan", str(plan), "--compact"], cwd=str(ROOT), capture_output=True, text=True, timeout=300)
        out = [ln for ln in (r.stdout + r.stderr).splitlines() if ln.startswith("[") and "WARN:" not in ln]
        return _text("\n".join(out[:40]) or "No output.", r.returncode != 0)
    if name == "zeno_render":
        d = _job_dir(str(a.get("name", "")))
        plan = d / "plan.edl" if d else None
        if not plan or not plan.is_file():
            return _text("No plan.edl for that edit yet.", True)
        wait_s = max(0, min(600, int(a.get("wait_seconds") if a.get("wait_seconds") is not None else 240)))
        cmd = [_py(), str(ROOT / "scripts" / "zeno.py"), "--execute", str(plan), "--square", "--compact"]
        rc, tail = _run_with_log_tail(cmd, d / "_render.log", wait_s=wait_s)
        if rc is None:
            return _text(
                "Rendering + QC continues in the background (>240s video). "
                "Wait ~60s before checking zeno_job ONCE — never poll in a tight loop."
            )
        return _text(tail or f"Render finished with exit code {rc}.", rc != 0)
    return _text(f"Unknown tool {name}", True)


def handle(msg: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    method, mid = msg.get("method"), msg.get("id")
    if mid is None:                       # a notification: nothing to answer
        return None
    try:
        if method == "initialize":
            ver = (msg.get("params") or {}).get("protocolVersion") or PROTOCOL
            result: Dict[str, Any] = {"protocolVersion": ver, "capabilities": {"tools": {}}, "serverInfo": {"name": NAME, "version": "1.0"}}
        elif method == "tools/list":
            result = {"tools": TOOLS}
        elif method == "tools/call":
            p = msg.get("params") or {}
            result = call(str(p.get("name")), p.get("arguments") or {})
        elif method == "ping":
            result = {}
        else:
            return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": f"Method not found: {method}"}}
    except Exception as e:   # noqa: BLE001
        result = _text(f"Zeno error: {e}", True) if method == "tools/call" else {}
    return {"jsonrpc": "2.0", "id": mid, "result": result}


def main() -> int:
    if "--selftest" in sys.argv:
        print(json.dumps({"server": NAME, "tools": [t["name"] for t in TOOLS], "approx_tokens": len(json.dumps(TOOLS)) // 4}))
        return 0
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except ValueError:
            continue
        out = handle(msg)
        if out is not None:
            sys.stdout.write(json.dumps(out) + "\n")
            sys.stdout.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main())
