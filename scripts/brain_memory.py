"""Persistent 15-Year Editor Brain Memory Manager (`brain_memory.py`).

Stores and retrieves permanent editorial rules learned from user feedback in
`styles/<style>/brain_memory.json`, and formats a compact `# BRAIN MEMORY` block
for injection at the top of `work/<project>/brief.txt` so every fresh Claude Code
or Antigravity session automatically applies all learned editorial rules.
"""
from __future__ import annotations

import datetime as dt
import json
import re
from pathlib import Path
from typing import Any, Dict, List

ROOT = Path(__file__).resolve().parent.parent
STYLES_DIR = ROOT / "styles"


def _memory_path(style_name: str = "micronarrative") -> Path:
    return STYLES_DIR / (style_name or "micronarrative") / "brain_memory.json"


def load_brain_memory(style_name: str = "micronarrative") -> List[Dict[str, Any]]:
    """Return the list of persistent editorial rules for `style_name`."""
    path = _memory_path(style_name)
    if not path.is_file():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        rules = data.get("rules", [])
        return [r for r in rules if isinstance(r, dict) and r.get("rule")]
    except Exception:
        return []


def add_brain_memory_rule(
    rule_text: str,
    category: str = "editorial",
    rule_id: str = "",
    style_name: str = "micronarrative",
) -> Dict[str, Any]:
    """Append or update a persistent editorial rule in `styles/<style>/brain_memory.json`."""
    clean_text = re.sub(r"\s+", " ", str(rule_text or "").strip())
    if not clean_text:
        raise ValueError("Rule text cannot be empty.")

    path = _memory_path(style_name)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload: Dict[str, Any] = {"style": style_name, "updated_at": dt.date.today().isoformat(), "rules": []}
    if path.is_file():
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                payload.update(loaded)
        except Exception:
            pass

    rules: List[Dict[str, Any]] = list(payload.get("rules") or [])
    if not rule_id:
        words = re.findall(r"[A-Za-z0-9]+", clean_text.upper())[:4]
        rule_id = "_".join(words) if words else f"RULE_{len(rules) + 1}"

    new_entry = {
        "id": rule_id,
        "category": category,
        "rule": clean_text,
    }
    replaced = False
    for idx, existing in enumerate(rules):
        if existing.get("id") == rule_id or existing.get("rule", "").lower() == clean_text.lower():
            rules[idx] = new_entry
            replaced = True
            break
    if not replaced:
        rules.append(new_entry)

    payload["updated_at"] = dt.date.today().isoformat()
    payload["rules"] = rules
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return new_entry


def format_brain_memory_for_brief(style_name: str = "micronarrative") -> List[str]:
    """Format persistent brain memory rules as compact lines for `brief.txt`."""
    rules = load_brain_memory(style_name)
    if not rules:
        return []
    out = ["# BRAIN MEMORY (MANDATORY 15-YEAR EDITOR RULES):"]
    for r in rules:
        rid = r.get("id", "RULE")
        txt = r.get("rule", "")
        out.append(f"  - [{rid}] {txt}")
    return out


def sync_brain_memory_with_cloud(style_name: str = "micronarrative") -> Dict[str, Any]:
    """Push local `brain_memory.json` rules to Zeno Cloud (`/v1/learnings/rule`) and pull any new shared team rules."""
    import auth_session
    sess = auth_session.load_session()
    token = str(sess.get("token") or "").strip()
    cloud_url = str(sess.get("cloud_url") or auth_session.get_cloud_url()).strip().rstrip("/")
    if not token or not cloud_url:
        return {"ok": False, "pushed": 0, "pulled": 0, "message": "No active Zeno Cloud session token"}

    local_rules = load_brain_memory(style_name)
    pushed = 0
    pulled = 0
    try:
        import httpx
        headers = {"Authorization": f"Bearer {token}"}
        with httpx.Client(timeout=10.0) as client:
            for r in local_rules:
                resp = client.post(
                    f"{cloud_url}/v1/learnings/rule",
                    headers=headers,
                    json={
                        "rule": r.get("rule", ""),
                        "category": r.get("category", "editorial"),
                        "language": r.get("lang", "all"),
                        "style": style_name,
                        "source": "sync_learnings",
                    },
                )
                if resp.status_code == 200:
                    pushed += 1

            get_resp = client.get(f"{cloud_url}/v1/learnings", headers=headers, params={"limit": 100})
            if get_resp.status_code == 200:
                data = get_resp.json()
                cloud_rules = data.get("learned_rules") if isinstance(data, dict) else []
                existing_texts = {str(x.get("rule", "")).strip().lower() for x in local_rules}
                for cr in cloud_rules or []:
                    rtxt = str(cr.get("rule_text") or cr.get("rule") or "").strip()
                    if rtxt and rtxt.lower() not in existing_texts:
                        add_brain_memory_rule(
                            rule_text=rtxt,
                            category=str(cr.get("category") or "editorial"),
                            rule_id=str(cr.get("rule_id") or ""),
                            style_name=style_name,
                        )
                        existing_texts.add(rtxt.lower())
                        pulled += 1
        return {
            "ok": True,
            "pushed": pushed,
            "pulled": pulled,
            "message": f"Pushed {pushed} rule(s) to Zeno Cloud and pulled {pulled} new team rule(s).",
        }
    except Exception as exc:
        return {"ok": False, "pushed": pushed, "pulled": pulled, "message": f"Cloud sync warning: {exc}"}

