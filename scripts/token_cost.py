"""Versioned API-equivalent estimates. Never presented as a subscription invoice."""
from __future__ import annotations

import os
from typing import Any

# USD per million tokens, Standard speed. Verified 2026-11-16.
# https://developers.openai.com/api/docs/models/gpt-6.1-sol
RATES = {"gpt-6.1-sol": (2.0, 0.1, 2.5, 10.0)}
RATE_DATE = "2026-11-16"
RATE_SOURCE = "https://developers.openai.com/api/docs/models/gpt-6.1-sol"
FX_REFERENCE = 94.1159
FX_DATE = "2026-11-16"
FX_SOURCE = "https://rbi.org.in/"


def estimate(agent: dict[str, Any]) -> dict[str, Any]:
    if agent.get("token_precision") != "exact" or not agent.get("requests"):
        return {"available": False, "reason": "Exact per-request model usage is unavailable."}
    fx_text = os.getenv("ZENO_USD_INR", str(FX_REFERENCE))
    try:
        fx = float(fx_text)
        if not 0 < fx < 10000:
            raise ValueError
    except ValueError:
        return {"available": False, "reason": "ZENO_USD_INR must be a positive exchange rate."}
    speed = os.getenv("ZENO_COST_SPEED", "standard").lower()
    multipliers = {"standard": 1, "fast": 2, "ultrafast": 6}
    if speed not in multipliers:
        return {"available": False, "reason": "Unknown estimate speed. Use standard, fast or ultrafast."}
    buckets = dict.fromkeys(("input", "cached", "cache_write", "output"), 0.0)
    for request in agent["requests"]:
        rates = RATES.get(request["model"])
        if rates is None:
            return {"available": False, "reason": f"No verified price for {request['model']}."}
        inp = request["input_tokens"]
        cached = request["cached_input_tokens"]
        write = request["cache_write_input_tokens"]
        fresh = max(0, inp - cached - write)
        long_input = 2 if request["context"] > 272000 else 1
        long_output = 1.5 if request["context"] > 272000 else 1
        counts = (fresh, cached, write, request["output_tokens"])
        for key, count, rate in zip(buckets, counts, rates):
            multiplier = long_output if key == "output" else long_input
            buckets[key] += count * rate * multiplier * multipliers[speed] / 1_000_000
    usd = sum(buckets.values())
    return {"available": True, "basis": "API-equivalent estimate before tax; subscription charges depend on the plan",
            "speed_assumption": speed, "rates_as_of": RATE_DATE, "rate_source": RATE_SOURCE,
            "usd_inr": fx, "fx_as_of": "user supplied" if "ZENO_USD_INR" in os.environ else FX_DATE,
            "fx_source": FX_SOURCE, "usd_by_type": buckets, "usd": usd, "inr": usd * fx}
