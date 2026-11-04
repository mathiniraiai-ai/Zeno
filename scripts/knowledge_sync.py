#!/usr/bin/env python3
"""Knowledge that lives in the Zeno server's database instead of in git.

Why: a new format recipe or reference learning used to need a commit, a push and a redeploy. Now an admin publishes it once
(`push`) and every computer picks it up at the start of its next job (`pull`, run by the pre-job gate). The heavy work
(wav2vec2, OpenCV, FFmpeg) stays on each computer; the server only stores what was learned and answers `match`.

    python3 scripts/knowledge_sync.py pull                  # what the gate runs; quiet and safe
    python3 scripts/knowledge_sync.py push                  # admin: publish styles/formats/*.json and each style's `variants`
    python3 scripts/knowledge_sync.py match "floating window, horizontal split screen"

Pulled items land in work/_knowledge/<kind>/<name>.json (git-ignored). `format_library` reads them after the git recipes, so
a published recipe with the same id replaces the committed one on every computer.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, List

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common  # noqa: E402

ROOT = common.ROOT
KNOW_DIR = common.WORK / "_knowledge"
STATE = KNOW_DIR / "state.json"


def _auth() -> Dict[str, str]:
    import auth_session

    token, base = auth_session.get_active_token(), auth_session.get_cloud_url().rstrip("/")
    return {"token": token, "base": base}


def pull(timeout: float = 8.0, quiet: bool = True) -> Dict[str, Any]:
    """Fetch what was published since the last pull. Never raises: a computer works fine without the server."""
    try:
        import httpx

        a = _auth()
        if not a["token"]:
            return {"ok": False, "pulled": 0, "message": "not signed in"}
        try:
            since = float(json.loads(STATE.read_text(encoding="utf-8")).get("since", 0.0))
        except (OSError, ValueError):
            since = 0.0
        r = httpx.get(a["base"] + "/v1/knowledge", params={"since": since}, headers={"Authorization": f"Bearer {a['token']}"}, timeout=timeout)
        if r.status_code == 404:
            return {"ok": True, "pulled": 0, "message": "the server has no knowledge store yet"}
        r.raise_for_status()
        data = r.json()
        for it in data.get("items", []):
            name = it["name"].replace("/", "__")
            common.write_json(KNOW_DIR / it["kind"] / f"{name}.json", it["payload"])
        common.write_json(STATE, {"since": data.get("now", since)})
        n = len(data.get("items", []))
        return {"ok": True, "pulled": n, "message": f"{n} knowledge item(s) updated" if n else "knowledge is up to date"}
    except Exception as exc:   # noqa: BLE001
        return {"ok": False, "pulled": 0, "message": f"could not reach the server ({type(exc).__name__})"}


def collect_local() -> List[Dict[str, Any]]:
    """What an admin publishes: every committed format recipe, and every reference learned for a style (its `variants`)."""
    items = []
    for f in sorted((ROOT / "styles" / "formats").glob("*.json")):
        r = json.loads(f.read_text(encoding="utf-8"))
        items.append({"kind": "format", "name": r["id"], "payload": r,
                      "text": " ".join([r.get("description", ""), r.get("when", ""), " ".join(r.get("names", []))])})
    for spec_path in sorted((ROOT / "styles").glob("*/spec.json")):
        spec = json.loads(spec_path.read_text(encoding="utf-8"))
        for v in spec.get("variants") or []:
            items.append({"kind": "reference", "name": f"{spec_path.parent.name}/{v['id']}", "payload": v,
                          "text": " ".join(str(x) for x in (v.get("reference", ""), v.get("language", ""), [d for d in v.get("differs_from_w42", [])]))})
    return items


def push(admin_password: str = "") -> Dict[str, Any]:
    """Publish local knowledge to the server database. Needs an admin: an email in ZENO_ADMIN_EMAILS signed in here, or ZENO_ADMIN_PASSWORD."""
    import httpx

    a = _auth()
    headers = {"X-Zeno-Admin": "1"}
    pw = admin_password or os.environ.get("ZENO_ADMIN_PASSWORD", "")
    if pw:
        headers["X-Admin-Password"] = pw
    elif a["token"]:
        headers["Authorization"] = f"Bearer {a['token']}"
    else:
        return {"ok": False, "message": "sign in (python3 scripts/setup.py --login) or set ZENO_ADMIN_PASSWORD"}
    done, failed = 0, []
    for it in collect_local():
        r = httpx.put(f"{a['base']}/admin/api/knowledge/{it['kind']}/{it['name']}", headers=headers, json={"payload": it["payload"], "text": it["text"]}, timeout=30.0)
        if r.status_code == 200:
            done += 1
        else:
            failed.append(f"{it['kind']}/{it['name']}: {r.status_code} {r.text[:80]}")
    return {"ok": not failed, "published": done, "failed": failed}


def match(text: str, k: int = 3) -> Dict[str, Any]:
    """Ask the server which format this wording describes; falls back to the local library when the server cannot answer."""
    try:
        import httpx

        a = _auth()
        r = httpx.post(a["base"] + "/v1/match", headers={"Authorization": f"Bearer {a['token']}"}, json={"text": text, "kind": "format", "k": k}, timeout=12.0)
        if r.status_code == 200:
            return {"source": "server", "matches": r.json()["matches"]}
    except Exception:   # noqa: BLE001
        pass
    import format_library

    return {"source": "local", "matches": [{"name": h["id"], "score": h["score"], "method": "words", "layout": h["layout"]} for h in format_library.search(text, k)]}


def main() -> None:
    ap = argparse.ArgumentParser(description="Knowledge kept in the Zeno server database")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("pull")
    sub.add_parser("push")
    m = sub.add_parser("match"); m.add_argument("text")
    a = ap.parse_args()
    if a.cmd == "pull":
        print(pull()["message"])
    elif a.cmd == "push":
        res = push()
        print(json.dumps(res, indent=1, ensure_ascii=False))
        sys.exit(0 if res.get("ok") else 1)
    else:
        res = match(a.text)
        print(f"answered by: {res['source']}")
        for h in res["matches"]:
            print(f"  {h['score']:>7}  {h['name']:<24} -> {h.get('layout')}  ({h.get('method')})")


if __name__ == "__main__":
    main()
