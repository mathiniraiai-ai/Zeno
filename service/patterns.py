"""Turn stored job records into patterns: what went right, what went wrong, for whom, on which version, format and model.

`digest(payload)` makes a small summary of one job record (kept in its own column, so the analysis never has to load the
multi-megabyte records again). `analyze(digests)` groups them by editor, version, format, agent model and language, and
lists the most common plan-check problems, verification failures, reasons a job was blocked, and what the editors said.

The outcome of a job:
  good   rendered, verification passed, and no negative feedback
  bad    the last render failed, the job was blocked before it started, or the feedback was negative
  mixed  rendered, but verification flagged something or the feedback was both good and bad
  open   measured or being checked, not rendered yet (still running, or abandoned)
Standard library only, so it runs inside the service and in tests.
"""
from __future__ import annotations

import re
import time
from collections import Counter, defaultdict
from typing import Any, Dict, Iterable, List, Optional

DIGEST_VERSION = 1

_POS = re.compile(r"(?i)\b(perfect|good|great|nice|fine|correct|works?|working|love|liked?|approved?|awesome|clean|smooth|better|resolved)\b")
_NEG = re.compile(r"(?i)\b(wrong|issues?|problems?|not|no|cut|cuts|missing|bad|too|overlap\w*|broken|fail\w*|errors?|mismatch\w*|should|"
                  r"didn'?t|doesn'?t|isn'?t|can'?t|stuck|slow|jump\w*|covered|repeat\w*|glitch\w*|blur\w*|out of sync|off)\b")
_VERIFY_TAG = re.compile(r"\[([A-Z]+)\s+(FAIL|WARN)\]")


def sentiment(text: str) -> str:
    pos, neg = len(_POS.findall(text or "")), len(_NEG.findall(text or ""))
    if pos and not neg:
        return "positive"
    if neg and not pos:
        return "negative"
    if pos and neg:
        return "mixed"
    return "neutral"


def _num(x: Any) -> float:
    try:
        return float(x or 0)
    except Exception:
        return 0.0


_FAMILIES = {"1": "micro narrative", "2": "live conversation", "3": "split screen", "4": "scrolling text",
             "5": "how would you translate", "6": "story based", "7": "realistic to 3d", "8": "3d to 2d", "9": "story pitch"}


def _format_of(analysis: str) -> str:
    """The format the Director named on the first line of script_analysis.md ('Format: <id> (...)')."""
    first = (analysis or "").strip().splitlines()[0] if (analysis or "").strip() else ""
    m = re.match(r"(?i)\s*[#*\s]*format\s*[*]*\s*[:\-]\s*(.+)", first)
    if not m:
        return ""
    raw = m.group(1).replace("*", "").replace("`", "").strip()
    text = re.split(r"[(,;]", raw)[0].strip()
    num = re.match(r"^(\d+)\.?\s*(.*)$", text)
    if num:
        text = num.group(2).strip() or _FAMILIES.get(num.group(1), num.group(1))
    return text.lower()[:60]


def digest(p: Dict[str, Any], editor: str = "", created_at: float = 0.0, updated_at: float = 0.0) -> Dict[str, Any]:
    """A small summary of one job record."""
    p = p or {}
    jr = p.get("job_report") if isinstance(p.get("job_report"), dict) else {}
    tok = p.get("tokens") if isinstance(p.get("tokens"), dict) else {}
    ver = p.get("verify") if isinstance(p.get("verify"), dict) else {}
    conv = p.get("conversation") if isinstance(p.get("conversation"), dict) else {}
    summ = p.get("summary") if isinstance(p.get("summary"), dict) else {}
    events = p.get("events") if isinstance(p.get("events"), list) else []
    kind = str(p.get("kind") or "")

    checks = [c for c in (jr.get("plan_checks") or []) if isinstance(c, dict)]
    problems: Counter = Counter()
    for c in checks:
        for cat, items in (c.get("problems") or {}).items():
            problems[str(cat)] += len(items) if isinstance(items, list) else 1
    until_pass = next((i + 1 for i, c in enumerate(checks) if c.get("result") == "passed"), None)

    verify_fails = sorted({f"{m.group(1)} {m.group(2)}" for m in _VERIFY_TAG.finditer(str(ver.get("text") or ""))})
    renders_failed = sum(1 for e in events if isinstance(e, dict) and e.get("kind") in ("render_failed", "failed")) or (1 if kind in ("render_failed", "failed") else 0)
    renders = int(summ.get("renders") or 0)

    fb = [f for f in (p.get("feedback") or []) if isinstance(f, dict) and f.get("text")]
    fb_s = [sentiment(f["text"]) for f in fb]

    first_render = next((e.get("at") for e in events if isinstance(e, dict) and e.get("kind") == "render_start"), None)
    if first_render is None:
        hist = [e for e in (p.get("plan_history") or []) if isinstance(e, dict) and e.get("stage") == "render"]
        first_render = hist[0].get("at") if hist else None
    prompts = [q for q in (conv.get("prompts") or []) if isinstance(q, dict)]
    corrections = [q["text"][:400] for q in prompts if first_render and _num(q.get("at")) > _num(first_render)][:12]

    start = _num(tok.get("job_start_epoch")) or (min((_num(e.get("at")) for e in events if isinstance(e, dict)), default=0.0))
    end = _num(tok.get("job_end_epoch")) or (max((_num(e.get("at")) for e in events if isinstance(e, dict)), default=0.0))
    counts = conv.get("counts") if isinstance(conv.get("counts"), dict) else {}
    models = conv.get("models") if isinstance(conv.get("models"), dict) else {}
    model = str(tok.get("agent_model") or (max(models, key=models.get) if models else "") or "")

    if kind == "blocked":
        outcome = "bad"
    elif kind in ("inspect", "inspected", "checking") and not renders:
        outcome = "open"
    elif kind in ("render_failed", "failed") or (renders == 0 and renders_failed):
        outcome = "bad"
    elif "negative" in fb_s and "positive" not in fb_s:
        outcome = "bad"
    elif ver.get("ok") is True and not verify_fails and "negative" not in fb_s:
        outcome = "good"
    elif renders or ver:
        outcome = "mixed"
    else:
        outcome = "open"

    blocked = p.get("blocked") if isinstance(p.get("blocked"), dict) else {}
    return {
        "v": DIGEST_VERSION,
        "job_uid": p.get("job_uid"),
        "editor": editor,
        "kind": kind,
        "outcome": outcome,
        "version": str(p.get("zeno_version") or ""),
        "platform": str(p.get("platform") or ""),
        "agent": str(conv.get("agent") or tok.get("agent_name") or ""),
        "model": model,
        "format": _format_of(str(p.get("script_analysis") or "")),
        "language": str(tok.get("language") or ""),
        "video_name": str(p.get("video_name") or "")[:200],
        "video_seconds": round(_num(summ.get("video_seconds") or tok.get("video_seconds")), 1),
        "segments": int(summ.get("segments") or 0),
        "layouts": summ.get("layouts") or {},
        "subs": bool(summ.get("subs")),
        "marks": int(summ.get("marks") or 0),
        "sfx": int(summ.get("sfx") or 0),
        "plan_versions": int(summ.get("plan_versions") or 0),
        "checks": len(checks),
        "checks_blocked": sum(1 for c in checks if c.get("result") == "blocked"),
        "checks_until_pass": until_pass,
        "problems": dict(problems),
        "renders": renders,
        "renders_failed": renders_failed,
        "verify_ok": ver.get("ok") if ver else None,
        "verify_fails": verify_fails,
        "failures": [str(f)[:300] for f in (jr.get("failures") or [])][:10] if isinstance(jr.get("failures"), list) else [],
        "blocked_stage": str(blocked.get("stage") or ""),
        "blocked_reason": str(blocked.get("reason") or "")[:600],
        "feedback": [{"text": f["text"][:600], "sentiment": s, "source": f.get("source", "")} for f, s in zip(fb[-10:], fb_s[-10:])],
        "prompts": int(counts.get("prompts") or len(prompts)),
        "first_prompt": prompts[0]["text"][:400] if prompts else "",
        "corrections": corrections,
        "tool_calls": int(counts.get("tool_calls") or 0),
        "tool_errors": int(counts.get("errors") or 0),
        "touched_code": bool(counts.get("touched_zeno_code")),
        "agent_tokens": int(_num(tok.get("agent_total_tokens"))),
        "total_tokens": int(_num(tok.get("grand_total_tokens"))),
        "minutes": round((end - start) / 60.0, 1) if start and end and end > start else None,
        "created_at": created_at,
        "updated_at": updated_at,
    }


def _avg(xs: Iterable[Optional[float]]) -> Optional[float]:
    vals = [float(x) for x in xs if x is not None]
    return round(sum(vals) / len(vals), 1) if vals else None


def _group(ds: List[Dict[str, Any]]) -> Dict[str, Any]:
    oc = Counter(d["outcome"] for d in ds)
    rendered = [d for d in ds if d["renders"]]
    probs: Counter = Counter()
    for d in ds:
        probs.update(d.get("problems") or {})
    return {
        "jobs": len(ds),
        "good": oc["good"], "mixed": oc["mixed"], "bad": oc["bad"], "open": oc["open"],
        "good_rate": round(oc["good"] / max(1, oc["good"] + oc["mixed"] + oc["bad"]), 2),
        "avg_checks_until_pass": _avg(d["checks_until_pass"] for d in ds),
        "avg_renders": _avg(d["renders"] for d in rendered),
        "avg_tool_errors": _avg(d["tool_errors"] for d in ds if d["tool_calls"]),
        "avg_agent_tokens": _avg(d["agent_tokens"] or None for d in ds),
        "avg_minutes": _avg(d["minutes"] for d in ds),
        "top_problems": probs.most_common(5),
    }


def analyze(digests: List[Dict[str, Any]], days: float = 0.0) -> Dict[str, Any]:
    """Patterns across jobs. `days` limits to recent jobs (0 = all)."""
    now = time.time()
    ds = [d for d in digests if isinstance(d, dict) and (not days or _num(d.get("updated_at")) >= now - days * 86400)]
    by: Dict[str, Dict[str, List[Dict[str, Any]]]] = {k: defaultdict(list) for k in ("editor", "version", "format", "model", "language", "agent")}
    for d in ds:
        for k in by:
            by[k][str(d.get(k) or "unknown")].append(d)
    problems: Counter = Counter()
    verify: Counter = Counter()
    blocked: Counter = Counter()
    for d in ds:
        problems.update(d.get("problems") or {})
        verify.update(d.get("verify_fails") or [])
        if d.get("blocked_stage"):
            blocked[d["blocked_stage"]] += 1
    feedback = [{"editor": d["editor"], "video": d["video_name"], "version": d["version"], **f}
                for d in ds for f in d.get("feedback") or []]
    corrections = [{"editor": d["editor"], "video": d["video_name"], "text": t} for d in ds for t in d.get("corrections") or []]
    return {
        "generated_at": now,
        "days": days,
        "overall": _group(ds),
        "by": {k: {name: _group(rows) for name, rows in sorted(groups.items(), key=lambda kv: -len(kv[1]))} for k, groups in by.items()},
        "top_check_problems": problems.most_common(15),
        "top_verify_failures": verify.most_common(15),
        "blocked_by_stage": blocked.most_common(),
        "blocked_examples": [{"editor": d["editor"], "stage": d["blocked_stage"], "reason": d["blocked_reason"][:300]}
                             for d in ds if d.get("blocked_stage")][:20],
        "feedback_negative": [f for f in feedback if f["sentiment"] in ("negative", "mixed")][:40],
        "feedback_positive": [f for f in feedback if f["sentiment"] == "positive"][:40],
        "editor_corrections_after_render": corrections[:60],
        "jobs_that_edited_zeno_code": [{"editor": d["editor"], "video": d["video_name"]} for d in ds if d.get("touched_code")][:30],
    }
