#!/usr/bin/env python3
"""What the job archive teaches, in plain words (owner only: it needs an admin sign-in on this computer).

    .venv/bin/python scripts/team_patterns.py                 # every job
    .venv/bin/python scripts/team_patterns.py --days 7        # the last week
    .venv/bin/python scripts/team_patterns.py --editor Jerin  # one editor
    .venv/bin/python scripts/team_patterns.py --versions      # which version each editor runs, and when each moved
    .venv/bin/python scripts/team_patterns.py --json out.json # the raw analysis and every job summary, for a spreadsheet

Reads /admin/api/patterns and /admin/api/versions on the Zeno server. Never prints a key.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict

sys.path.insert(0, str(Path(__file__).resolve().parent))

IST = timezone(timedelta(hours=5, minutes=30))


def _when(t: Any) -> str:
    try:
        return datetime.fromtimestamp(float(t), IST).strftime("%d %b %H:%M")
    except Exception:
        return "-"


def _get(path: str) -> Dict[str, Any]:
    import auth_session
    import httpx
    sess = auth_session.load_session()
    url = str(sess.get("cloud_url") or auth_session.get_cloud_url()).rstrip("/")
    token = str(sess.get("token") or "").strip()
    if not token:
        sys.exit("No Zeno sign-in on this computer (run: .venv/bin/python scripts/setup.py --login)")
    r = httpx.get(url + path, headers={"Authorization": f"Bearer {token}"}, timeout=120)
    if r.status_code != 200:
        sys.exit(f"The server said {r.status_code} for {path.split('?')[0]} (an admin sign-in is needed)")
    return r.json()


def _row(name: str, g: Dict[str, Any]) -> str:
    probs = ", ".join(f"{p.split(' (')[0]} {n}" for p, n in (g.get("top_problems") or [])[:3]) or "-"
    return (f"  {name[:28]:28s} {g['jobs']:4d}  {g['good']:4d} {g['mixed']:5d} {g['bad']:4d} {g['open']:4d}  {int(g['good_rate'] * 100):3d}%"
            f"  {g['avg_checks_until_pass'] if g['avg_checks_until_pass'] is not None else '-':>6}"
            f"  {g['avg_renders'] if g['avg_renders'] is not None else '-':>6}"
            f"  {int(g['avg_agent_tokens'] / 1000) if g['avg_agent_tokens'] else '-':>5}k"
            f"  {g['avg_minutes'] if g['avg_minutes'] is not None else '-':>5}   {probs}")


def show_patterns(a: Dict[str, Any]) -> None:
    head = (f"  {'':28s} {'jobs':>4s}  {'good':>4s} {'mixed':>5s} {'bad':>4s} {'open':>4s}  {'ok%':>4s}"
            f"  {'checks':>6s}  {'renders':>6s}  {'tokens':>6s}  {'min':>5s}   most common check problems")
    print(f"\nZENO PATTERNS ({'last ' + str(a['days']) + ' days' if a.get('days') else 'all jobs'}), made {_when(a['generated_at'])} IST")
    print("good = rendered, verified, no complaint | bad = failed, blocked or complained about | open = not rendered yet")
    print("checks = plan checks until the first pass | renders = renders per finished job | tokens = agent tokens per job")
    print(head)
    print(_row("ALL JOBS", a["overall"]))
    for key, title in (("editor", "BY EDITOR"), ("version", "BY ZENO VERSION"), ("format", "BY FORMAT"),
                       ("model", "BY AI MODEL"), ("language", "BY LANGUAGE")):
        print(f"\n{title}")
        for name, g in list(a["by"][key].items())[:15]:
            print(_row(name, g))
    print("\nPLAN-CHECK PROBLEMS (how often the check blocked a plan, all jobs)")
    for p, n in a["top_check_problems"][:12]:
        print(f"  {n:4d}  {p}")
    print("\nVERIFICATION FAILURES AFTER RENDER")
    for p, n in a["top_verify_failures"][:12] or [("none", 0)]:
        print(f"  {n:4d}  {p}")
    if a["blocked_by_stage"]:
        print("\nJOBS BLOCKED BEFORE A PLAN")
        for stage, n in a["blocked_by_stage"]:
            print(f"  {n:4d}  {stage}")
        for b in a["blocked_examples"][:6]:
            print(f"        {b['editor']}: {b['reason'][:150]}")
    print("\nWHAT EDITORS ASKED THE AGENT TO CHANGE AFTER A RENDER (newest first)")
    for c in a["editor_corrections_after_render"][:15] or [{"editor": "-", "text": "(none recorded yet: needs v1.0.14 on the editors' computers)"}]:
        print(f"  - {c['editor']}: {c['text'][:200]}")
    print("\nNEGATIVE / MIXED FEEDBACK")
    for f in a["feedback_negative"][:12] or [{"editor": "-", "text": "(none)"}]:
        print(f"  - {f['editor']}: {f['text'][:200]}")
    print("\nPOSITIVE FEEDBACK")
    for f in a["feedback_positive"][:8] or [{"editor": "-", "text": "(none)"}]:
        print(f"  + {f['editor']}: {f['text'][:200]}")
    if a["jobs_that_edited_zeno_code"]:
        print("\nJOBS WHERE THE AGENT EDITED ZENO'S OWN CODE (should not happen during an edit)")
        for j in a["jobs_that_edited_zeno_code"][:10]:
            print(f"  ! {j['editor']}: {j['video'][:120]}")


def show_versions(v: Dict[str, Any]) -> None:
    latest = (v.get("latest") or {}).get("version", "?")
    print(f"\nZENO VERSIONS (latest published: {latest}), checked {_when(v['now'])} IST")
    rows = [k for k in v["keys"] if not k.get("revoked_at")]
    best: Dict[str, Dict[str, Any]] = {}
    for k in rows:                       # one line per editor: the most recently seen key
        name = k["name"] or k["email"]
        if name not in best or (k.get("version_seen_at") or 0) > (best[name].get("version_seen_at") or 0):
            best[name] = k
    for name, k in sorted(best.items(), key=lambda kv: -(kv[1].get("last_used_at") or 0)):
        ver = k.get("zeno_version") or "not reported yet"
        mark = "" if ver == latest else "  <- behind" if k.get("zeno_version") else ""
        print(f"  {name[:26]:26s} {ver:18s} seen {_when(k.get('version_seen_at'))}  last active {_when(k.get('last_used_at'))}"
              f"  {k.get('client_platform') or ''}{mark}")
    print("\nVERSION CHANGES (newest first)")
    for e in v["history"][:30] or [{"editor_name": "-", "version": "(none yet)", "source": "", "created_at": 0}]:
        print(f"  {_when(e['created_at'])}  {e['editor_name'][:26]:26s} -> {e['version']:10s} ({e['source']})")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--days", type=float, default=0.0)
    ap.add_argument("--editor", default="")
    ap.add_argument("--versions", action="store_true")
    ap.add_argument("--json", default="")
    a = ap.parse_args()
    if a.versions:
        show_versions(_get("/admin/api/versions"))
        return
    q = f"/admin/api/patterns?days={a.days}" + (f"&editor={a.editor}" if a.editor else "")
    data = _get(q)
    if a.json:
        Path(a.json).write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"Wrote {a.json} ({len(data.get('jobs') or [])} jobs)")
    show_patterns(data["patterns"])


if __name__ == "__main__":
    main()
