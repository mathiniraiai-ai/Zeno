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
            "new_chat_context_limit": 100000, "direct_max_calls": 3,
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
    inp = sum(r["input_tokens"] for r in requests)
    cached = sum(r.get("cached_input_tokens", 0) for r in requests)
    out = sum(r["output_tokens"] for r in requests)
    return {"token_precision": "exact" if requests else "unavailable", "requests": requests,
            "input_tokens": inp - cached, "cached_tokens": cached, "output_tokens": out,
            "cache_write_tokens": sum(r.get("cache_write_input_tokens", 0) for r in requests),
            "reasoning_tokens": sum(r.get("reasoning_output_tokens", 0) for r in requests),
            "total_tokens": inp - cached + out, "total_tokens_including_cache": inp + out,
            "steps_count": len(requests)}


def direct_requests(project: Path, since: float, until: float) -> list[dict]:
    unique = {}
    for r in rows(project / "direct_usage.jsonl"):
        if since <= r.get("timestamp", 0) <= until and r.get("usage_known"):
            unique[r["call_id"]] = r
    return list(unique.values())


def combine(agent: dict, project: Path, since: float, until: float) -> dict:
    direct = direct_requests(project, since, until)
    if not direct:
        return agent
    if agent.get("token_precision") != "exact" and agent.get("total_tokens_including_cache", 0):
        # Never overwrite unmeasured host usage with a deceptively exact direct-only total.
        return {**agent, "direct_usage_unmerged": True}
    requests = list(agent.get("requests") or []) + direct
    combined = {**agent, **usage_from_requests(requests)}
    combined["agent_name"] = (agent.get("agent_name", "Host") + " + OpenAI direct")
    combined["agent_model"] = ", ".join(sorted({r["model"] for r in requests}))
    combined["host_usage_unknown"] = agent.get("token_precision") != "exact"
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
    agent = detect_agent_usage(since_epoch=since, until_epoch=end,
                               session_id=meta.get("agent_session_id"))
    agent = combine(agent, project, since, end)
    cost = token_cost.estimate(agent)
    policy = settings()
    count = agent.get("steps_count", 0)
    unknown_calls = [r for r in rows(project / "direct_usage.jsonl")
                     if since <= r.get("timestamp", 0) <= end and not r.get("usage_known")]
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
