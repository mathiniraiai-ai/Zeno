"""Bounded, tool-free GPT-6.1 Sol planner using the Responses API.

Explicit opt-in only: requires OPENAI_API_KEY in the process environment (never
reads Codex credentials or .env). No render, no automatic network retry, no silent
model switch. Usage is journalled before validation; an unknown bill stops retries.
"""
from __future__ import annotations

import base64
import json
import os
from pathlib import Path
import time
import urllib.error
import urllib.request
import uuid

import cost_control
import request_budget
import token_cost

MODEL = "gpt-6.1-sol"
ENDPOINT = "https://api.openai.com/v1/responses"


def payload(system: str, text: str, images: list[Path], policy: dict) -> dict:
    content = [{"type": "input_text", "text": text}]
    from PIL import Image
    for path in images:
        with Image.open(path) as picture:
            if max(picture.size) > 4096:
                raise ValueError("Clip sheet exceeds the reserved image size; create smaller review sheets first.")
        content.append({"type": "input_image", "detail": "high",
                        "image_url": "data:image/jpeg;base64," + base64.b64encode(path.read_bytes()).decode()})
    return {"model": MODEL, "instructions": system, "input": [{"role": "user", "content": content}],
            "max_output_tokens": policy["direct_max_output_tokens"],
            "reasoning": {"effort": policy["direct_reasoning_effort"]},
            "store": False, "tools": [], "service_tier": "default"}


def reserve_cost(system: str, text: str, images: list[Path], policy: dict) -> dict:
    # UTF-8 bytes are deliberately conservative for multilingual text. An image
    # allowance is a reserve, not an exact tokenizer count or provider spending cap.
    size = len((system + text).encode())
    if size > policy["direct_max_input_bytes"]:
        raise ValueError(f"Planning packet is {size:,} bytes; exceeds {policy['direct_max_input_bytes']:,}. "
                         "Split the evidence packet; nothing was sent.")
    inp = size + 2048 + len(images) * policy["direct_image_token_reserve"]
    request = {"model": MODEL, "context": inp, "input_tokens": inp, "cached_input_tokens": 0,
               "cache_write_input_tokens": inp, "output_tokens": policy["direct_max_output_tokens"]}
    return token_cost.estimate({"token_precision": "exact", "requests": [request]})


def send(body: dict) -> dict:
    key = os.environ.get("OPENAI_API_KEY")
    if not key:
        raise ValueError("OPENAI_API_KEY is not available. Direct API planning uses separate API billing; "
                         "use the fresh-chat workflow if no API account is configured.")
    request = urllib.request.Request(ENDPOINT, data=json.dumps(body).encode(),
                                     headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"})
    # No SDK retry policy: an ambiguous timeout must not start another paid call.
    with urllib.request.urlopen(request, timeout=1200) as response:
        return json.loads(response.read())


def response_text(response: dict) -> str:
    return "\n".join(part.get("text", "") for item in response.get("output", [])
                     if item.get("type") == "message" for part in item.get("content", [])
                     if part.get("type") == "output_text")


def measured_usage(response: dict, call_id: str, stage: str, stamp: float) -> dict:
    usage = response.get("usage") or {}
    if not isinstance(usage, dict):
        usage = {}
    inp, out = usage.get("input_tokens"), usage.get("output_tokens")
    detail = usage.get("input_tokens_details") or {}
    output_detail = usage.get("output_tokens_details") or {}
    details_valid = isinstance(detail, dict) and isinstance(output_detail, dict)
    detail = detail if isinstance(detail, dict) else {}
    output_detail = output_detail if isinstance(output_detail, dict) else {}
    cached = detail.get("cached_tokens", 0)
    writes = detail.get("cache_creation_tokens", detail.get("cache_write_tokens", 0))
    reasoning = output_detail.get("reasoning_tokens", 0)
    valid = details_valid and all(isinstance(value, int) and not isinstance(value, bool) and value >= 0
                                  for value in (inp, out, cached, writes, reasoning))
    valid = valid and cached + writes <= inp and reasoning <= out
    return {"call_id": call_id, "stage": stage, "timestamp": stamp, "model": response.get("model", MODEL),
            "usage_known": valid, "input_tokens": inp if valid else 0, "output_tokens": out if valid else 0,
            "cached_input_tokens": cached if valid else 0, "cache_write_input_tokens": writes if valid else 0,
            "reasoning_output_tokens": reasoning if valid else 0,
            "context": inp if valid else 0}


def _save(path: Path, data: dict) -> None:
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(path)


def run(project: Path, *, dry_run: bool = False) -> dict:
    import direct_mode
    project = project.resolve()
    if not project.is_relative_to(cost_control.ROOT / "work"):
        raise ValueError("Direct planning requires a project inside Zeno/work.")
    policy = cost_control.settings()
    max_calls = min(policy["direct_max_calls"], direct_mode.planner_max_calls())
    system, job, images = direct_mode.build_brief(project)
    # Extra policy is explicit even when an old saved brief contains outdated advice.
    system += ("\nAll split and baked-split zooms are z=1. Captions are fixed, single-row. "
               "Preserve every required spoken line. Do not waive wrong voices, missing dialogue, repeats or clipped words. "
               "List uncertainties in NOTES. Decisions requiring a human go in decisions and stop for review.")
    reserve = reserve_cost(system, job, images, policy)
    if not reserve.get("available"):
        raise ValueError("No verified cost reserve is available: " + reserve.get("reason", ""))
    if dry_run:
        return {"dry_run": True, "model": MODEL, "input_bytes": len((system + job).encode()),
                "images": len(images), "first_call_reserve_inr": reserve["inr"],
                "budget_inr": policy["model_budget_inr"], "max_calls": max_calls,
                "api_configured": bool(os.environ.get("OPENAI_API_KEY")), "paid_calls": 0}
    if not os.environ.get("OPENAI_API_KEY"):
        raise ValueError("OPENAI_API_KEY is unavailable. Use the fresh-chat path; no API request was made.")
    # A lock covers validation and writes as well as the paid call.
    lock = project / "direct.lock"
    try:
        fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        raise ValueError("A direct planner is running or was interrupted. Review direct_call_pending.json before retrying.")
    os.close(fd)
    originals = {name: (project / name).read_text() if (project / name).exists() else None
                 for name in ("plan.edl", "script_analysis.md", "decisions.txt")}
    report = {"model": MODEL, "started": time.time(), "passed": False, "calls": [], "checks": 0}
    run_id = uuid.uuid4().hex
    pending = project / "direct_call_pending.json"
    try:
        if pending.exists():
            raise ValueError("The previous API call has unresolved usage. Reconcile it before another paid request.")
        meta = json.loads((project / "token_telemetry.json").read_text())
        if meta.get("job_end_epoch"):
            raise ValueError("This edit is already finished. Run a new --inspect before a paid benchmark; saved reports stay frozen.")
        since = meta["job_start_epoch"]
        prior = [r for r in cost_control.rows(project / "direct_usage.jsonl") if r.get("timestamp", 0) >= since]
        text = job
        call_system = system
        context_kind = "full"
        correction_scope = None
        previous_failure = None
        for number in range(len(prior), max_calls):
            checkpoint = cost_control.snapshot(project)
            if checkpoint.get("blocked"):
                report["stopped"] = checkpoint["reason"]
                break
            if number > 0 and not checkpoint.get("available"):
                report["stopped"] = "Prior spending has no verified price; resolve accounting before another API call."
                break
            sheets_for_call = images if context_kind == "full" else []
            reserve = reserve_cost(call_system, text, sheets_for_call, policy)
            spent = checkpoint.get("cost", {}).get("inr", 0)
            if not reserve.get("available") or spent + reserve["inr"] > policy["model_budget_inr"]:
                report["stopped"] = "Next request's conservative reserve exceeds the remaining model budget."
                break
            body = payload(call_system, text, sheets_for_call, policy)
            stage = "direct planning" if correction_scope is None else "direct correction"
            call_id = uuid.uuid4().hex
            try:
                reservation_id = request_budget.reserve_call(
                    project, provider="openai", context_kind=context_kind,
                    input_bytes=len((call_system + text).encode()), image_count=len(sheets_for_call), scope="planner")
            except ValueError as exc:
                report["stopped"] = str(exc)
                report["budget_blocked"] = True
                break
            _save(pending, {"call_id": call_id, "started": time.time(), "reserve_inr": reserve["inr"], "stage": stage})
            print(f"[DIRECT] {stage}; request {number + 1}/{max_calls}; "
                  f"reserve INR {reserve['inr']:.2f}", flush=True)
            try:
                response = send(body)
            except Exception as exc:
                # Never serialize exception text: a transport/library error could contain credentials.
                report["stopped"] = f"API request failed ({type(exc).__name__}); usage is unresolved, no automatic retry."
                break
            usage = measured_usage(response, call_id, stage, time.time())
            with (project / "direct_usage.jsonl").open("a", encoding="utf-8") as journal:
                journal.write(json.dumps(usage) + "\n")
                journal.flush()
                os.fsync(journal.fileno())
            report["calls"].append(usage)
            request_budget.finish_call(project, reservation_id, usage_known=usage["usage_known"],
                                       usage=usage, usage_call_id=call_id)
            if not usage["usage_known"]:
                report["stopped"] = "API response did not include usage; stop before further spending."
                break
            pending.unlink()
            if response.get("status") != "completed":
                report["stopped"] = "API response was incomplete; saved usage, kept the original plan."
                break
            answer = response_text(response)
            (project / "direct_candidate.txt").write_text(answer, encoding="utf-8")
            blocks = direct_mode.parse_answer(answer)
            if correction_scope is not None:
                try:
                    blocks = direct_mode.merge_correction(project, blocks, correction_scope)
                except ValueError as exc:
                    report["stopped"] = "Correction needs review: " + str(exc)
                    break
            if not blocks.get("plan") or not blocks.get("analysis"):
                report["stopped"] = "Incomplete plan format; candidate saved for review."
                break
            existing = set((originals["decisions.txt"] or "").splitlines())
            proposed = [line for line in blocks.get("decisions", "").splitlines() if line.strip() and line not in existing]
            if proposed:
                report["stopped"] = "New exceptions require human review; see direct_candidate.txt. No checks were waived."
                break
            # Never discard an approved exception or silently accept a new one.
            blocks["decisions"] = originals["decisions.txt"] or ""
            if correction_scope is None:
                direct_mode.write_answer(project, blocks, originals["plan.edl"] or "")
            else:
                for name, key in (("plan.edl", "plan"), ("script_analysis.md", "analysis"),
                                  ("decisions.txt", "decisions")):
                    (project / name).write_text(blocks[key], encoding="utf-8")
            ok, diagnostics = direct_mode.run_check(project)
            report["checks"] += 1
            (project / f"direct_check_{number + 1}.log").write_text(diagnostics, encoding="utf-8")
            report["notes"] = blocks.get("notes", "")
            if ok:
                report["passed"] = True
                report["review_required"] = "Plan verified; proceed directly to render without asking Yes/No unless plan-only review was requested."
                break  # no extra model round for nonblocking advice
            if any(term in diagnostics for term in ("VOICE REVIEW REQUIRED", "WRONG VOICE", "voices not checked")):
                report["stopped"] = "Voice evidence needs review; no repeated classifier or model retries."
                break
            if diagnostics == previous_failure:
                report["stopped"] = "The same check failure repeated; stopped automatic corrections."
                break
            previous_failure = diagnostics
            # Fresh request with only the failed lines and their measured evidence.
            # No conversation handle, full packet, prior answer or images are replayed.
            try:
                call_system, text, correction_scope = direct_mode.build_correction(project, diagnostics)
            except ValueError as exc:
                report["stopped"] = "Correction needs review: " + str(exc)
                break
            context_kind = "targeted"
        if not report["passed"] and "stopped" not in report:
            report["stopped"] = "Maximum model calls reached."
    finally:
        if not report["passed"]:
            for name, content in originals.items():
                path = project / name
                if content is None:
                    path.unlink(missing_ok=True)
                else:
                    path.write_text(content, encoding="utf-8")
        report["ended"] = time.time()
        _save(project / "direct_report.json", report)
        history = project / "_history"
        history.mkdir(exist_ok=True)
        _save(history / f"direct-{run_id}.json", report)
        lock.unlink(missing_ok=True)
    return report
