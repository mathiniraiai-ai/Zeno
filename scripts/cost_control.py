"""Per-edit spending checkpoints and stage attribution, without model calls.

Chat checkpoints cannot intercept the host's model requests. Direct mode reserves
estimated spend before each request; all estimates exclude tax and transcription.
"""
from __future__ import annotations

import argparse
import json
import math
import os
from pathlib import Path
import time

import token_cost

ROOT = Path(__file__).resolve().parents[1]
DEFAULTS = {"schema": 1, "model_budget_inr": 40.0, "chat_request_limit": 24,
            "new_chat_context_limit": 1000000, "direct_max_calls": 2, "planner_max_calls": 2,
            "direct_max_output_tokens": 8000, "direct_max_input_bytes": 100000,
            "direct_image_token_reserve": 10000, "direct_reasoning_effort": "medium"}


def settings() -> dict:
    result = dict(DEFAULTS)
    path = ROOT / "config" / "cost_controls.json"
    if path.is_file():
        result.update(json.loads(path.read_text()))
    if "ZENO_MODEL_BUDGET_INR" in os.environ:
        result["model_budget_inr"] = float(os.environ["ZENO_MODEL_BUDGET_INR"])
    for key, value in result.items():
        if key not in {"schema", "direct_reasoning_effort"}:
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
                raise ValueError(f"{key} must be a finite positive number")
            if key != "model_budget_inr" and not isinstance(value, int):
                raise ValueError(f"{key} must be an integer")
    if result["direct_reasoning_effort"] not in {"low", "medium", "high", "xhigh", "max"}:
        raise ValueError("Invalid direct reasoning effort")
    return result


def rows(path: Path) -> list[dict]:
    if not path.is_file():
        return []
    result = []
    for line in path.read_text().splitlines():
        try:
            value = json.loads(line)
            if isinstance(value, dict):
                result.append(value)
        except ValueError:
            continue
    return result


def usage_from_requests(requests: list[dict]) -> dict:
    inp = sum(int(r.get("input_tokens") or 0) for r in requests)
    cached = sum(int(r.get("cached_input_tokens") or 0) for r in requests)
    uncached = max(0, inp - cached)
    out = sum(int(r.get("output_tokens") or 0) for r in requests)
    return {"token_precision": "exact" if requests else "unavailable", "requests": requests,
            "input_tokens": uncached, "cached_tokens": cached, "output_tokens": out,
            "cache_write_tokens": sum(int(r.get("cache_write_input_tokens") or 0) for r in requests),
            "reasoning_tokens": sum(int(r.get("reasoning_output_tokens") or 0) for r in requests),
            "total_tokens": uncached + out, "total_tokens_including_cache": uncached + cached + out,
            "context_start": int(requests[0].get("context", 0) or 0) if requests else 0,
            "context_end": int(requests[-1].get("context", 0) or 0) if requests else 0,
            "steps_count": len(requests), "turns": len(requests)}


def _is_usage_known(r: dict) -> bool:
    if "usage_known" in r:
        return bool(r.get("usage_known"))
    return isinstance(r.get("input_tokens"), int) and r.get("input_tokens", -1) >= 0


def _normalize_direct_row(r: dict) -> dict:
    inp = int(r.get("input_tokens") or 0)
    cached = int(r.get("cached_input_tokens", r.get("cache_read_input_tokens", 0)) or 0)
    write = int(r.get("cache_write_input_tokens", r.get("cache_creation_input_tokens", 0)) or 0)
    return {
        **r,
        "model": str(r.get("model") or "unknown"),
        "input_tokens": inp,
        "cached_input_tokens": cached,
        "cache_write_input_tokens": write,
        "ephemeral_1h_input_tokens": int(r.get("ephemeral_1h_input_tokens") or 0),
        "output_tokens": int(r.get("output_tokens") or 0),
        "reasoning_output_tokens": int(r.get("reasoning_output_tokens", r.get("thinking_tokens", 0)) or 0),
        "context": int(r.get("context", inp) or 0),
    }


def direct_requests(project: Path, since: float, until: float) -> list[dict]:
    unique = {}
    for idx, r in enumerate(rows(project / "direct_usage.jsonl")):
        if since <= r.get("timestamp", 0) <= until and _is_usage_known(r):
            key = r.get("call_id") or f"row-{idx}"
            unique[key] = _normalize_direct_row(r)
    return list(unique.values())


def _direct_agent_label(direct: list[dict]) -> str:
    for r in direct:
        explicit = str(r.get("agent_name") or "").strip()
        if explicit:
            return explicit
    providers = {str(r.get("provider") or "").strip().lower() for r in direct if r.get("provider")}
    models = [str(r.get("model") or "").strip().lower() for r in direct]
    if "claude" in providers or any(
        m.startswith("claude") or m in {"sonnet", "opus", "haiku"} or any(k in m for k in ("sonnet", "opus", "haiku", "fable"))
        for m in models
    ):
        return "Claude Direct"
    if "codex" in providers or any("codex" in m for m in models):
        return "Codex Direct"
    if "openai" in providers or any(m.startswith("gpt-6.1-sol") and str(r.get("stage") or "").startswith("direct ") for m, r in zip(models, direct)):
        return "OpenAI Direct"
    return "Codex Direct"


def combine(agent: dict, project: Path, since: float, until: float) -> dict:
    direct = direct_requests(project, since, until)
    if not direct:
        return agent
    direct_sids = {str(r["session_id"]) for r in direct if r.get("session_id") and r.get("session_id") not in ("local", "unknown")}
    same_subprocess_session = bool(agent.get("session_id")) and str(agent.get("session_id")) in direct_sids
    if not same_subprocess_session and agent.get("token_precision") != "exact" and (
        agent.get("total_tokens_including_cache", 0) or agent.get("total_tokens", 0)
    ):
        # Never overwrite unmeasured host usage with a deceptively exact direct-only total.
        return {**agent, "direct_usage_unmerged": True}
    host_reqs = [] if same_subprocess_session else list(agent.get("requests") or [])
    host_name = str(agent.get("agent_name") or "").strip()
    no_host = same_subprocess_session or (not host_reqs and host_name.lower() in {"", "unknown", "local", "cli", "local / cli"})
    requests = sorted(host_reqs + direct, key=lambda r: r.get("timestamp", 0))
    combined = {**agent, **usage_from_requests(requests)}
    direct_label = _direct_agent_label(direct)
    if no_host:
        combined["agent_name"] = direct_label
        combined["host_usage_unknown"] = False
        if combined.get("session_id") in (None, "", "local", "unknown"):
            combined["session_id"] = next(
                (str(r.get("session_id") or r.get("call_id")) for r in direct if r.get("session_id") or r.get("call_id")),
                "direct",
            )
    else:
        base_host = host_name or "Host"
        combined["agent_name"] = direct_label if base_host == direct_label else f"{base_host} + {direct_label}"
        combined["host_usage_unknown"] = agent.get("token_precision") != "exact"
    combined["agent_model"] = ", ".join(sorted({r["model"] for r in requests if r.get("model")})) or "unknown"
    return combined


def stage_report(project: Path, agent: dict, since: float, until: float) -> list[dict]:
    transitions = [(since, "intake")]
    stages = {"inspect_start": "measurement", "inspect_done": "planning", "check": "corrections/review",
              "render_start": "render/review", "render_done": "final review"}
    for event in rows(project / "stage_events.jsonl"):
        at = event.get("at", 0)
        if since <= at <= until and event.get("kind") in stages:
            transitions.append((at, stages[event["kind"]]))
    transitions.sort()
    groups: dict[str, list] = {}
    for request in agent.get("requests") or []:
        stage = request.get("stage") or next((name for at, name in reversed(transitions)
                                               if at <= request.get("timestamp", since)), "intake")
        groups.setdefault(stage, []).append(request)
    return [{"stage": stage, **usage_from_requests(reqs), "cost": token_cost.estimate(usage_from_requests(reqs))}
            for stage, reqs in groups.items()]


def snapshot(project: Path, until: float | None = None) -> dict:
    from token_tracker import detect_agent_usage
    path = project / "token_telemetry.json"
    meta = json.loads(path.read_text()) if path.is_file() else {}
    since = meta.get("job_start_epoch")
    if not since:
        return {"available": False, "blocked": True, "reason": "No edit start recorded; run --inspect first."}
    end = until or meta.get("job_end_epoch") or time.time()
    codex_sid = meta.get("agent_session_id") if str(meta.get("agent_name", "")).startswith("Codex") else None
    agent = detect_agent_usage(since_epoch=since, until_epoch=end,
                               session_id=codex_sid)
    agent = combine(agent, project, since, end)
    cost = token_cost.estimate(agent)
    policy = settings()
    count = agent.get("steps_count", 0)
    unknown_calls = [r for r in rows(project / "direct_usage.jsonl")
                     if since <= r.get("timestamp", 0) <= end and not _is_usage_known(r)]
    reached = cost.get("available") and cost["inr"] >= policy["model_budget_inr"]
    pending = (project / "direct_call_pending.json").exists()
    blocked = bool(reached or count >= policy["chat_request_limit"] or unknown_calls or pending)
    return {"available": cost.get("available", False), "blocked": blocked,
            "reason": "Model spending checkpoint reached; save work and review before further model calls." if blocked else
                      "Usage unavailable; this is not zero cost." if not cost.get("available") else "Within checkpoint",
            "cost": cost, "requests": count, "limit_inr": policy["model_budget_inr"],
            "unknown_direct_calls": len(unknown_calls),
            "pending_direct_call": pending,
            "host_usage_unknown": agent.get("host_usage_unknown", False),
            "stages": stage_report(project, agent, since, end)}


def print_checkpoint(project: Path) -> dict:
    report = snapshot(project)
    path = project / "cost_checkpoint.json"
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(report, ensure_ascii=False, indent=2))
    tmp.replace(path)
    cost = report.get("cost", {})
    amount = f"INR {cost['inr']:.2f}" if cost.get("available") else "unknown cost"
    partial = "; host usage unknown" if report.get("host_usage_unknown") else ""
    print(f"[COST] {amount}{partial}; {report.get('requests', '?')} requests; {report['reason']}")
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("project", type=Path)
    parser.add_argument("--compare", type=Path, help="Compare with a saved token_telemetry.json (read-only)")
    args = parser.parse_args()
    if args.compare:
        first = json.loads(args.compare.read_text())
        second = json.loads((args.project / "token_telemetry.json").read_text())
        for label, record in (("baseline", first), ("trial", second)):
            print(json.dumps({"run": label, "model": record.get("agent_model"),
                              "requests": record.get("agent_steps"),
                              "tokens": record.get("agent_total_including_cache"),
                              "cost": record.get("cost_estimate"),
                              "new_transcription": record.get("transcribe_new_total_tokens")}, ensure_ascii=False))
        print("Compare equivalent footage, checks and scope. Cached reruns exclude initial transcription; bytes are not token savings.")
    else:
        report = print_checkpoint(args.project)
        raise SystemExit(2 if report["blocked"] else 0)


if __name__ == "__main__":
    main()
