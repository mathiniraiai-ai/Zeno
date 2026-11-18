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
import re
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
WORKFLOW_HINT = (
    "Option B 4-turn protocol: batch-read packet_parts + clip_sheets, "
    "batch-write script_analysis.md + plan.edl, run --execute once without polling."
)


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
    {"name": "zeno_inspect", "description": "Run Zeno's --inspect --compact step and return Option B JSON (packet_parts, clip_sheets, execute_command, review_sheet, workflow_hint).",
     "inputSchema": {"type": "object", "properties": {"script": {"type": "string", "description": "Google Doc tab link (must end in ?tab=...) or a script file"},
                                                       "footage": {"type": "string", "description": "local folder with the raw clips"},
                                                       "name": {"type": "string", "description": "the Notion name of the video"},
                                                       "style": {"type": "string"}, "lang": {"type": "string"},
                                                       "wait_seconds": {"type": "integer", "description": "leave unset: the call waits until the video is rendered (up to 90 min)"}},
                     "required": ["script", "footage", "name"]}},
    {"name": "zeno_start_edit", "description": "Run Zeno's 1-call inspect + isolated auto-planner + automatic 9:16 & 1:1 Square render (--inspect --auto-plan --compact) end-to-end without asking Yes/No. Returns the Pitch Card, verification result, output MP4 paths, and token report directly; do NOT read line_map.md, facts.md, editing_packet.md, or clip_sheet_*.jpg unless manual fallback is returned.",
     "inputSchema": {"type": "object", "properties": {"script": {"type": "string", "description": "Google Doc tab link (must end in ?tab=...) or a script file"},
                                                       "footage": {"type": "string", "description": "local folder with the raw clips"},
                                                       "name": {"type": "string", "description": "the Notion name of the video"},
                                                       "style": {"type": "string"}, "lang": {"type": "string"},
                                                       "wait_seconds": {"type": "integer", "description": "leave unset: the call waits until the video is rendered (up to 90 min)"}},
                     "required": ["script", "footage", "name"]}},
    {"name": "zeno_edit", "description": "Run Zeno's 1-call auto-planner + automatic 9:16 & 1:1 Square render without asking Yes/No; on manual fallback returns Option B JSON (packet_parts, clip_sheets, execute_command, review_sheet, workflow_hint).",
     "inputSchema": {"type": "object", "properties": {"name": {"type": "string", "description": "Notion name or existing work folder name"},
                                                       "script": {"type": "string", "description": "Google Doc tab link (must end in ?tab=...) or a script file (required for new edit)"},
                                                       "footage": {"type": "string", "description": "local folder with the raw clips (required for new edit)"},
                                                       "style": {"type": "string"}, "lang": {"type": "string"},
                                                       "wait_seconds": {"type": "integer", "description": "leave unset: the call waits until the video is rendered (up to 90 min)"}},
                     "required": ["name"]}},
    {"name": "zeno_auto_plan", "description": "Run Zeno's isolated 1-call planner + automatic render (--auto-plan --compact) on an already-inspected edit without asking Yes/No.",
     "inputSchema": {"type": "object", "properties": {"name": {"type": "string", "description": "work folder name from zeno_jobs"}}, "required": ["name"]}},
    {"name": "zeno_check_plan", "description": "Run the plan check on a job's plan and return its verdict and advice lines.",
     "inputSchema": {"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]}},
    {"name": "zeno_render", "description": "Render a verified plan (9:16 reel and 1:1 square) immediately without asking Yes/No, with automatic QC + post-job cleanup.",
     "inputSchema": {"type": "object", "properties": {"name": {"type": "string"},
                                                       "wait_seconds": {"type": "integer", "description": "leave unset: the call waits until the render is done (up to 90 min)"}}, "required": ["name"]}},
]


def _py() -> str:
    venv = ROOT / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    return str(venv) if venv.exists() else sys.executable


def _display_py() -> str:
    if (ROOT / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")).exists():
        return ".venv\\Scripts\\python.exe" if os.name == "nt" else ".venv/bin/python"
    return _py()


def _rel_path(p: Path) -> str:
    try:
        return str(p.relative_to(ROOT))
    except ValueError:
        return str(p)


def _text(s: str, error: bool = False) -> Dict[str, Any]:
    return {"content": [{"type": "text", "text": s}], "isError": error}


def _job_dir(name: str) -> Optional[Path]:
    if not name or "/" in name or "\\" in name or name.startswith("."):
        return None
    d = ROOT / "work" / name
    return d if d.is_dir() else None


def _packet_part_sort_key(path: Path) -> Tuple[int, int, str]:
    m = re.search(r"editing_packet_p(\d+)\.md$", path.name)
    return (0, int(m.group(1)), path.name) if m else (1, 0, path.name)


def _extract_string_list(raw: Any) -> List[str]:
    if not isinstance(raw, list):
        return []
    out: List[str] = []
    for item in raw:
        if isinstance(item, str) and item.strip():
            out.append(item.strip())
        elif isinstance(item, dict):
            val = item.get("path") or item.get("file") or item.get("name")
            if isinstance(val, str) and val.strip():
                out.append(val.strip())
    return out


def _collect_packet_artifacts(d: Path) -> Tuple[List[str], List[str]]:
    manifest: Dict[str, Any] = {}
    manifest_path = d / "editing_packet.json"
    if manifest_path.is_file():
        try:
            loaded = json.loads(manifest_path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                manifest = loaded
        except (OSError, ValueError):
            manifest = {}

    parts = _extract_string_list(manifest.get("parts"))
    if not parts:
        part_files = sorted(d.glob("editing_packet_p*.md"), key=_packet_part_sort_key)
        parts = [p.name for p in part_files] if part_files else ["editing_packet.md"]

    sheets = _extract_string_list(manifest.get("clip_sheets"))
    if not sheets:
        sheets = sorted(p.name for p in d.glob("clip_sheet_*.jpg"))
    return parts, sheets


def _has_packet(d: Optional[Path]) -> bool:
    if d is None or not d.is_dir():
        return False
    return (
        (d / "editing_packet.json").is_file()
        or (d / "editing_packet.md").is_file()
        or any(d.glob("editing_packet_p*.md"))
    )


def _option_b_payload(
    d: Optional[Path],
    status: str,
    output: str = "",
    error: str = "",
    returncode: int = 0,
) -> Dict[str, Any]:
    packet_parts: List[str] = []
    clip_sheets: List[str] = []
    execute_command = ""
    review_sheet = ""
    work_dir = ""
    project = ""
    if d is not None and d.is_dir():
        project = d.name
        work_dir = _rel_path(d)
        packet_parts, clip_sheets = _collect_packet_artifacts(d)
        execute_command = f"{_display_py()} scripts/zeno.py --execute {_rel_path(d / 'plan.edl')} --square --compact"
        review_sheet = _rel_path(d / "review_sheet.jpg")
    payload: Dict[str, Any] = {
        "status": status,
        "project": project,
        "work_dir": work_dir,
        "packet_parts": packet_parts,
        "clip_sheets": clip_sheets,
        "execute_command": execute_command,
        "review_sheet": review_sheet,
        "workflow_hint": WORKFLOW_HINT,
    }
    if status == "manual_fallback":
        payload["manual_fallback"] = True
        payload["auto_plan_returncode"] = returncode
    if error:
        payload["error"] = error
    if output:
        payload["output"] = output
    return payload


def _find_job_dir(title: str, output_text: str = "", jobs_mod: Any = None) -> Optional[Path]:
    direct = _job_dir(title)
    if direct is not None:
        return direct
    work_root = ROOT / "work"
    if not work_root.is_dir():
        return None
    skip = {"_clips", "_segments", "_job_outbox"}
    for m in re.finditer(r"(?:^|[\s'\"`(\[])(?:[^\s'\"`(\[]*[/\\])?work[/\\]([A-Za-z0-9_.-]+)(?:[/\\]|\b)", output_text or ""):
        cand_name = m.group(1)
        if cand_name in skip or cand_name.startswith(("_", ".")):
            continue
        cand = work_root / cand_name
        if cand.is_dir():
            return cand

    norm = lambda s: re.sub(r"[^a-z0-9]+", "", (s or "").lower())
    target_norm = norm(title)

    if jobs_mod is not None and getattr(jobs_mod, "WORK", None) == work_root:
        try:
            rows = jobs_mod.list_jobs()
            for j in rows:
                j_title = str(j.get("title") or "")
                j_name = str(j.get("name") or "")
                if j_title == title or j_name == title:
                    cand = _job_dir(j_name)
                    if cand is not None:
                        return cand
            if target_norm:
                for j in rows:
                    j_title = str(j.get("title") or "")
                    j_name = str(j.get("name") or "")
                    if norm(j_title) == target_norm or norm(j_name) == target_norm:
                        cand = _job_dir(j_name)
                        if cand is not None:
                            return cand
        except Exception:
            pass

    dirs = sorted(
        (p for p in work_root.iterdir() if p.is_dir() and not p.name.startswith(("_", "."))),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    for p in dirs:
        plan_f = p / "plan.edl"
        if plan_f.is_file():
            try:
                for line in plan_f.read_text(encoding="utf-8", errors="replace").splitlines()[:15]:
                    if line.startswith("#output "):
                        stem = Path(line.split(None, 1)[1].strip().strip('"')).stem
                        cleaned = re.sub(r"^(?:reel|SQ)_", "", stem)
                        if cleaned == title or (target_norm and norm(cleaned) == target_norm):
                            return p
            except OSError:
                pass
    slug = re.sub(r"[^a-z0-9]+", "_", title.lower()).strip("_")
    if slug and (work_root / slug).is_dir():
        return work_root / slug
    for p in dirs:
        if target_norm and norm(p.name) == target_norm:
            return p
    return None


def _is_manual_fallback(rc: int, tail: str, d: Optional[Path]) -> bool:
    if not _has_packet(d):
        return False
    if rc != 0:
        return True
    lowered = (tail or "").lower()
    if "[plan pass]" in lowered or "[auto-render]" in lowered:
        return False
    return (
        "[manual" in lowered
        or "manual fallback" in lowered
        or "manual planning" in lowered
        or (d is not None and not (d / "pitch_card.md").is_file() and "[plan ready]" not in lowered)
    )


def _finish_auto_edit(
    tool_name: str,
    title: str,
    rc: Optional[int],
    tail: str,
    d: Optional[Path] = None,
    jobs_mod: Any = None,
) -> Dict[str, Any]:
    if rc is None:
        return _text(
            "[ZENO STILL RUNNING] The edit passed 90 minutes and continues in the background. Tell the editor it is still "
            "running and END THE TURN now. Do not call any Zeno tool again in this message (each check re-reads the whole chat); "
            "the editor will ask for the status later. NEVER ask Yes/No, "
            "and NEVER open agent/REFERENCE.md, CONCEPT.md, EDIT_RULES.md, FORMATS.md, scripts/*.py, facts.md, "
            "line_map.md, clip_caches.json, ffprobe, or dir."
        )
    proj_dir = d or _find_job_dir(title, tail, jobs_mod=jobs_mod)
    if "[PLANNER STOPPED]" in (tail or ""):
        return _text(
            (tail or "") + "\n\n[ZENO 2-CALL RULE] The planner reached its call limit. Do NOT plan by hand, do NOT open "
            "editing_packet*.md, line_map.md, facts.md or clip_sheet_*.jpg, and do NOT call this tool again for this "
            "video. Report the [BLOCKED] lines to the editor and ask how to go on.",
            True,
        )
    if _is_manual_fallback(rc, tail, proj_dir):
        payload = _option_b_payload(proj_dir, status="manual_fallback", output=tail, returncode=rc)
        return _text(json.dumps(payload, ensure_ascii=False, indent=2), False)
    if rc != 0:
        return _text(tail or f"{tool_name} exited with code {rc}", True)
    rule = (
        "\n\n[ZENO 1-CALL RULE] Auto-render completed above (no Yes/No needed). Deliver the Pitch Card summary, "
        "output MP4 paths, and saved token report directly — do NOT open editing_packet.md, editing_packet_p*.md, "
        "line_map.md, facts.md, EDIT_RULES.md, FORMATS.md, agent/REFERENCE.md, CONCEPT.md, scripts/*.py, or clip_sheet_*.jpg."
    )
    return _text((tail or f"{tool_name} exited with code {rc}") + rule, False)


def _short_job(j: Dict[str, Any]) -> str:
    st = j.get("status", {})
    pct = f" {j.get('pct', 0)}%" if st.get("phase") == "working" else ""
    return f"{j['name']} | {j.get('title', '')[:60]} | {st.get('label', '')}{pct}"


EDIT_WAIT_S = 5400   # one tool call waits for the whole edit (typically 10-40 min); 90 min is only the ceiling for a stuck job


def _wait_seconds(a: Dict[str, Any]) -> int:
    """Edits and renders wait until done: each early return makes the chat check back, and every check re-reads the whole chat.
    A short wait asked by the model is ignored on purpose (it would bring the polling back); 0 still means start and return."""
    try:
        return 0 if a.get("wait_seconds") is not None and int(a.get("wait_seconds")) <= 0 else EDIT_WAIT_S
    except (TypeError, ValueError):
        return EDIT_WAIT_S


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


def _validate_start_inputs(a: Dict[str, Any]) -> Tuple[Optional[Tuple[str, Path, str]], Optional[Dict[str, Any]]]:
    script = str(a.get("script", "")).strip()
    footage = str(a.get("footage", "")).strip()
    title = str(a.get("name", "")).strip()
    if not (script and footage and title):
        return None, _text("Need the script, the footage folder and the Notion name.", True)
    if script.startswith("http") and "tab=" not in script:
        return None, _text("The Google Doc link must end in ?tab=... (one tab, never the whole document).", True)
    footage_path = Path(footage).expanduser()
    if not footage_path.is_dir():
        return None, _text(f"Footage folder not found: {footage}", True)
    return (script, footage_path, title), None


def _build_inspect_cmd(script: str, footage_path: Path, title: str, a: Dict[str, Any], auto_plan: bool) -> List[str]:
    cmd = [
        _py(), str(ROOT / "scripts" / "zeno.py"),
        "--inspect", str(footage_path),
        "--script", script,
        "--name", title,
    ]
    if auto_plan:
        cmd.append("--auto-plan")
    cmd.append("--compact")
    for flag, key in (("--style", "style"), ("--lang", "lang")):
        if a.get(key):
            cmd += [flag, str(a[key])]
    return cmd


def _run_auto_plan_for_job(tool_name: str, d: Path, jobs_mod: Any) -> Dict[str, Any]:
    plan = d / "plan.edl"
    if not plan.is_file():
        return _text("No plan.edl for that edit yet.", True)
    rc, txt = _run_with_log_tail(
        [_py(), str(ROOT / "scripts" / "zeno.py"), "--auto-plan", "--plan", str(plan), "--compact"],
        d / "_auto_plan.log", wait_s=EDIT_WAIT_S,
    )
    return _finish_auto_edit(tool_name, d.name, rc, txt, d=d, jobs_mod=jobs_mod)


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
        elif not p.get("cuts") and _has_packet(d):
            parts, sheets = _collect_packet_artifacts(d)
            lines.append(f"packet_parts: {', '.join(parts)}")
            if sheets:
                lines.append(f"clip_sheets: {', '.join(sheets)}")
            lines.append(f"execute_command: {_display_py()} scripts/zeno.py --execute {_rel_path(d / 'plan.edl')} --square --compact")
        review_f = d / "review_sheet.jpg"
        if review_f.is_file():
            lines.append(f"review_sheet: {_rel_path(review_f)}")
        lines += ["last steps:"] + [f"  {e['kind']}: {str(e['text'])[:110]}" for e in x.get("events", [])[-6:]]
        return _text("\n".join(lines))
    if name == "zeno_inspect":
        parsed, err_resp = _validate_start_inputs(a)
        if err_resp is not None or parsed is None:
            return err_resp or _text("Invalid arguments.", True)
        script, footage_path, title = parsed
        cmd = _build_inspect_cmd(script, footage_path, title, a, auto_plan=False)
        wait_s = max(0, min(900, int(a.get("wait_seconds") if a.get("wait_seconds") is not None else 480)))
        rc, tail = _run_with_log_tail(cmd, ROOT / "work" / "_mcp_start.log", wait_s=wait_s)
        if rc is None:
            return _text(json.dumps({
                "status": "running",
                "message": "Inspecting footage in the background. End turn and wait, or check zeno_jobs ONCE after completion — NEVER poll in a loop.",
                "workflow_hint": WORKFLOW_HINT,
            }, ensure_ascii=False, indent=2))
        d = _find_job_dir(title, tail, jobs_mod=jobs)
        if rc != 0:
            if _has_packet(d):
                payload = _option_b_payload(d, status="error", output=tail, error=f"zeno_inspect exited with code {rc}", returncode=rc)
                return _text(json.dumps(payload, ensure_ascii=False, indent=2), True)
            return _text(tail or f"zeno_inspect exited with code {rc}", True)
        payload = _option_b_payload(d, status="inspected", output=tail, returncode=0)
        return _text(json.dumps(payload, ensure_ascii=False, indent=2), False)
    if name in ("zeno_start_edit", "zeno_edit"):
        if name == "zeno_edit" and not str(a.get("script", "")).strip() and not str(a.get("footage", "")).strip():
            d = _job_dir(str(a.get("name", "")).strip())
            if d is not None:
                return _run_auto_plan_for_job(name, d, jobs)
        parsed, err_resp = _validate_start_inputs(a)
        if err_resp is not None or parsed is None:
            return err_resp or _text("Invalid arguments.", True)
        script, footage_path, title = parsed
        cmd = _build_inspect_cmd(script, footage_path, title, a, auto_plan=True)
        rc, tail = _run_with_log_tail(cmd, ROOT / "work" / "_mcp_start.log", wait_s=_wait_seconds(a))
        return _finish_auto_edit(name, title, rc, tail, jobs_mod=jobs)
    if name == "zeno_auto_plan":
        d = _job_dir(str(a.get("name", "")))
        if d is None or not (d / "plan.edl").is_file():
            return _text("No plan.edl for that edit yet.", True)
        return _run_auto_plan_for_job(name, d, jobs)
    if name == "zeno_check_plan":
        d = _job_dir(str(a.get("name", "")))
        plan = d / "plan.edl" if d else None
        if not plan or not plan.is_file():
            return _text("No plan.edl for that edit yet.", True)
        r = subprocess.run([_py(), str(ROOT / "scripts" / "zeno.py"), "--check-plan", str(plan), "--compact"], cwd=str(ROOT), capture_output=True, text=True, timeout=300)
        combined = (r.stdout + "\n" + r.stderr).strip()
        out = [ln for ln in combined.splitlines() if ln.startswith("[") and "WARN:" not in ln]
        msg = "\n".join(out[:40]) if out else (combined[-2000:] if combined else "No output.")
        if r.returncode == 0:
            msg += f"\nexecute_command: {_display_py()} scripts/zeno.py --execute {_rel_path(plan)} --square --compact"
        return _text(msg, r.returncode != 0)
    if name == "zeno_render":
        d = _job_dir(str(a.get("name", "")))
        plan = d / "plan.edl" if d else None
        if not plan or not plan.is_file():
            return _text("No plan.edl for that edit yet.", True)
        wait_s = _wait_seconds(a)
        cmd = [_py(), str(ROOT / "scripts" / "zeno.py"), "--execute", str(plan), "--square", "--compact"]
        rc, tail = _run_with_log_tail(cmd, d / "_render.log", wait_s=wait_s)
        if rc is None:
            return _text(
                "[ZENO STILL RUNNING] Rendering + QC passed 90 minutes and continues in the background. Tell the editor and END THE TURN "
                "now; do not call any tool again in this message. NEVER poll or write custom FFmpeg/Python scripts."
            )
        msg = tail or f"Render finished with exit code {rc}."
        if rc == 0 and (d / "review_sheet.jpg").is_file() and "review_sheet" not in msg:
            msg += f"\nreview_sheet: {_rel_path(d / 'review_sheet.jpg')}"
        return _text(msg, rc != 0)
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
