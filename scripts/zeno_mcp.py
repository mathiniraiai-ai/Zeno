#!/usr/bin/env python3
"""Zeno connector for Claude (a local MCP server: Claude starts it on this computer and talks to it over standard input and output).

It needs no extra packages. It lets Claude (Claude Code, or Claude Desktop) look at Zeno's jobs and start the measuring step. It is small on purpose:
a few short tool descriptions cost about 100 tokens when Claude starts. Everything runs on this computer; the footage never leaves it.

    python3 scripts/zeno_mcp.py            # normally started by Claude, not by hand
    python3 scripts/zeno_mcp.py --selftest # prints the tool list and exits
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))
os.environ.setdefault("ZENO_EVENTS", "on")

PROTOCOL = "2024-11-05"
NAME = "zeno"

TOOLS: List[Dict[str, Any]] = [
    {"name": "zeno_jobs", "description": "List Zeno edits with their status (working, plan ready, rendered) and progress.",
     "inputSchema": {"type": "object", "properties": {"limit": {"type": "integer", "description": "how many, newest first (default 8)"}}}},
    {"name": "zeno_job", "description": "One edit in detail: stage, progress, plan summary, output files and the last steps.",
     "inputSchema": {"type": "object", "properties": {"name": {"type": "string", "description": "work folder name from zeno_jobs"}}, "required": ["name"]}},
    {"name": "zeno_start_edit", "description": "Start the measuring step for a new edit (script link or file, footage folder, Notion name). Returns at once; follow it with zeno_jobs. Then write the plan as the /edit rules say.",
     "inputSchema": {"type": "object", "properties": {"script": {"type": "string", "description": "Google Doc tab link (must end in ?tab=...) or a script file"},
                                                       "footage": {"type": "string", "description": "local folder with the raw clips"},
                                                       "name": {"type": "string", "description": "the Notion name of the video"},
                                                       "style": {"type": "string"}, "lang": {"type": "string"}},
                     "required": ["script", "footage", "name"]}},
    {"name": "zeno_check_plan", "description": "Run the plan check on a job's plan and return its verdict and advice lines.",
     "inputSchema": {"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]}},
    {"name": "zeno_render", "description": "Render the approved plan (9:16 reel and 1:1 square). Call it only after the editor has said Yes to the plan.",
     "inputSchema": {"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]}},
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


def call(name: str, a: Dict[str, Any]) -> Dict[str, Any]:
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
        cmd = [_py(), str(ROOT / "scripts" / "zeno.py"), "--inspect", str(Path(footage).expanduser()), "--script", script, "--name", title]
        for flag, key in (("--style", "style"), ("--lang", "lang")):
            if a.get(key):
                cmd += [flag, str(a[key])]
        log = ROOT / "work" / "_mcp_start.log"
        log.parent.mkdir(exist_ok=True)
        with open(log, "ab") as fh:
            subprocess.Popen(cmd, cwd=str(ROOT), stdout=fh, stderr=fh, start_new_session=True)
        return _text("Measuring started in the background. Follow it with zeno_jobs; when it reaches 'waiting for the editor's plan', read line_map.md, facts.md and the clip sheet in its work folder and write plan.edl.")
    if name == "zeno_check_plan":
        d = _job_dir(str(a.get("name", "")))
        plan = d / "plan.edl" if d else None
        if not plan or not plan.is_file():
            return _text("No plan.edl for that edit yet.", True)
        r = subprocess.run([_py(), str(ROOT / "scripts" / "zeno.py"), "--check-plan", str(plan)], cwd=str(ROOT), capture_output=True, text=True, timeout=300)
        out = [ln for ln in (r.stdout + r.stderr).splitlines() if ln.startswith("[") and "WARN:" not in ln]
        return _text("\n".join(out[:40]) or "No output.", r.returncode != 0)
    if name == "zeno_render":
        d = _job_dir(str(a.get("name", "")))
        plan = d / "plan.edl" if d else None
        if not plan or not plan.is_file():
            return _text("No plan.edl for that edit yet.", True)
        log = open(d / "_render.log", "ab")
        subprocess.Popen([_py(), str(ROOT / "scripts" / "zeno.py"), "--execute", str(plan), "--square"], cwd=str(ROOT), stdout=log, stderr=log, start_new_session=True)
        return _text("Rendering started in the background. Follow it with zeno_job.")
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
