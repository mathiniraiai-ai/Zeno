"""Persistent 15-Year Editor Brain Memory Manager (`brain_memory.py`).

Stores and retrieves permanent editorial rules learned from user feedback in
`styles/<style>/brain_memory.json`, and formats a compact `# BRAIN MEMORY` block
for the top of `work/<project>/line_map.md` so every fresh Claude Code
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


def rule_text_hash(text: Any) -> str:
    """The identity of a rule: its text, lower-cased with spaces collapsed. The Zeno server computes the same value."""
    import hashlib
    norm = " ".join(str(text or "").lower().split())
    return hashlib.sha256(norm.encode("utf-8")).hexdigest()[:16]


def add_brain_memory_rule(
    rule_text: str,
    category: str = "editorial",
    rule_id: str = "",
    style_name: str = "micronarrative",
    source: str = "remember",
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

    # source: "remember" = a person recorded it on this PC (sent to the team); "team" = pulled from the team's server.
    # The rules that ship with Zeno carry no source and are never sent to the server: the release is where they live.
    new_entry = {
        "id": rule_id,
        "category": category,
        "rule": clean_text,
        "source": source,
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


def format_brain_memory_for_brief(style_name: str = "micronarrative", max_chars: int = 1150) -> List[str]:
    """Format persistent brain memory rules as compact lines for `line_map.md` within `max_chars` budget."""
    rules = load_brain_memory(style_name)
    if not rules:
        return []
    out = ["# BRAIN MEMORY (MANDATORY 15-YEAR EDITOR RULES):"]
    used = len(out[0])
    for idx, r in enumerate(rules):
        rid = str(r.get("id", "RULE"))[:28]
        txt = re.sub(r"\s+", " ", str(r.get("rule", "")).strip())
        if len(txt) > 95:
            txt = txt[:92].rstrip() + "..."
        line = f"  - [{rid}] {txt}"
        if used + len(line) + 1 > max_chars:
            rem = len(rules) - idx
            out.append(f"  - [+{rem} MORE RULES in styles/{style_name}/brain_memory.json]")
            break
        out.append(line)
        used += len(line) + 1
    return out


def _save_rules(style_name: str, rules: List[Dict[str, Any]]) -> None:
    path = _memory_path(style_name)
    payload: Dict[str, Any] = {"style": style_name, "updated_at": dt.date.today().isoformat(), "rules": rules}
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(loaded, dict):
            payload = {**loaded, "updated_at": dt.date.today().isoformat(), "rules": rules}
    except Exception:
        pass
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def sync_brain_memory_with_cloud(style_name: str = "micronarrative") -> Dict[str, Any]:
    """Exchange team rules with Zeno Cloud.

    The rules that ship with Zeno live in the release and are never uploaded (uploading them made every old version of a rule
    come back to every PC after it was corrected). Only rules a person recorded here with `--remember` are sent. Rules the owner
    retired on the server are removed here, and new team rules are pulled in."""
    import auth_session
    sess = auth_session.load_session()
    token = str(sess.get("token") or "").strip()
    cloud_url = str(sess.get("cloud_url") or auth_session.get_cloud_url()).strip().rstrip("/")
    if not token or not cloud_url:
        return {"ok": False, "pushed": 0, "pulled": 0, "message": "No active Zeno Cloud session token"}

    pushed = pulled = removed = 0
    try:
        import httpx
        headers = {"Authorization": f"Bearer {token}"}
        with httpx.Client(timeout=10.0) as client:
            get_resp = client.get(f"{cloud_url}/v1/learnings", headers=headers, params={"limit": 100})
            if get_resp.status_code != 200:
                return {"ok": False, "pushed": 0, "pulled": 0, "message": f"Cloud sync warning: HTTP {get_resp.status_code}"}
            data = get_resp.json() if get_resp.content else {}
            cloud_rules = (data.get("learned_rules") if isinstance(data, dict) else None) or []
            retired = {str(h) for h in ((data.get("retired_hashes") if isinstance(data, dict) else None) or [])}

            local_rules = load_brain_memory(style_name)
            # 1. a rule the owner retired leaves this PC (only rules that came from people; the shipped ones follow the release)
            kept = [r for r in local_rules
                    if not (r.get("source") in ("team", "remember") and rule_text_hash(r.get("rule")) in retired)]
            removed = len(local_rules) - len(kept)
            if removed:
                _save_rules(style_name, kept)
            local_hashes = {rule_text_hash(r.get("rule")) for r in kept}

            # 2. new team rules come in
            cloud_hashes = set()
            for cr in cloud_rules:
                rtxt = str(cr.get("rule_text") or cr.get("rule") or "").strip()
                h = rule_text_hash(rtxt)
                cloud_hashes.add(h)
                if rtxt and h not in local_hashes and h not in retired:
                    add_brain_memory_rule(rule_text=rtxt, category=str(cr.get("category") or "editorial"),
                                          rule_id=str(cr.get("rule_id") or ""), style_name=style_name, source="team")
                    local_hashes.add(h)
                    pulled += 1

            # 3. what a person recorded on this PC goes to the team (once)
            for r in kept:
                if r.get("source") != "remember" or rule_text_hash(r.get("rule")) in cloud_hashes:
                    continue
                resp = client.post(f"{cloud_url}/v1/learnings/rule", headers=headers, json={
                    "rule": r.get("rule", ""), "category": r.get("category", "editorial"), "language": r.get("lang", "all"),
                    "style": style_name, "source": "cli_remember"})
                if resp.status_code == 200:
                    pushed += 1
        msg = f"Sent {pushed} rule(s) recorded here, pulled {pulled} new team rule(s)" + (f", removed {removed} retired rule(s)" if removed else "") + "."
        return {"ok": True, "pushed": pushed, "pulled": pulled, "removed": removed, "message": msg}
    except Exception as exc:
        return {"ok": False, "pushed": pushed, "pulled": pulled, "message": f"Cloud sync warning: {exc}"}
