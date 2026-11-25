"""Versioned API-equivalent estimates. Never presented as a subscription invoice."""
from __future__ import annotations

import os
from typing import Any

# USD per million tokens, Standard speed: (fresh_input, cached_input, cache_write_5m, output). Verified 2026-11-16.
# https://developers.openai.com/api/docs/models/gpt-6.1-sol ; https://docs.anthropic.com/en/docs/about-claude/pricing
RATES: dict[str, tuple[float, float, float, float]] = {
    "gpt-6.2-sol": (2.0, 0.1, 2.5, 10.0),
    "gpt-6.1-sol": (2.0, 0.1, 2.5, 10.0),
    "gpt-5-codex": (1.25, 0.125, 1.25, 10.0),
    "gpt-5.6": (1.25, 0.125, 1.25, 10.0),
    "gpt-5": (1.25, 0.125, 1.25, 10.0),
    "o3": (2.0, 0.50, 2.0, 8.0),
    "claude-sonnet-5-5": (2.0, 0.10, 2.50, 10.0),
    "claude-sonnet-5": (2.0, 0.20, 2.50, 10.0),
    "claude-sonnet-4": (3.0, 0.30, 3.75, 15.0),
    "sonnet": (3.0, 0.30, 3.75, 15.0),
    "claude-opus-5-5": (4.0, 0.20, 5.00, 20.0),
    "claude-opus-5": (5.0, 0.50, 6.25, 25.0),
    "opus": (5.0, 0.50, 6.25, 25.0),
    "claude-opus-4": (15.0, 1.50, 18.75, 75.0),
    "claude-haiku-5": (0.10, 0.01, 0.125, 0.50),
    "claude-haiku-4-5": (1.0, 0.10, 1.25, 5.0),
    "haiku": (1.0, 0.10, 1.25, 5.0),
    "claude-fable": (10.0, 0.25, 12.50, 50.0),
    "gemini-4-neon": (3.0, 0.15, 3.75, 15.0),
    "gemini-4-hydrogen": (1.5, 0.075, 1.875, 7.5),
}
RATE_DATE = "2026-11-16"
RATE_SOURCE = "https://developers.openai.com/api/docs/models/gpt-6.1-sol ; https://docs.anthropic.com/en/docs/about-claude/pricing"
FX_REFERENCE = 94.1159
FX_DATE = "2026-11-16"
FX_SOURCE = "https://rbi.org.in/"

_PREFIX_RATES: tuple[tuple[str, tuple[float, float, float, float]], ...] = (
    ("gpt-6.2-sol", (2.0, 0.1, 2.5, 10.0)),
    ("gpt-6.1-sol", (2.0, 0.1, 2.5, 10.0)),
    ("gpt-5-codex", (1.25, 0.125, 1.25, 10.0)),
    ("gpt-5.6", (1.25, 0.125, 1.25, 10.0)),
    ("gpt-5", (1.25, 0.125, 1.25, 10.0)),
    ("o3", (2.0, 0.50, 2.0, 8.0)),
    ("claude-sonnet-5-5", (2.0, 0.10, 2.50, 10.0)),
    ("claude-sonnet-5.5", (2.0, 0.10, 2.50, 10.0)),
    ("claude-sonnet-5", (2.0, 0.20, 2.50, 10.0)),
    ("claude-sonnet-4", (3.0, 0.30, 3.75, 15.0)),
    ("claude-opus-5-5", (4.0, 0.20, 5.00, 20.0)),
    ("claude-opus-5.5", (4.0, 0.20, 5.00, 20.0)),
    ("claude-opus-5", (5.0, 0.50, 6.25, 25.0)),
    # Opus 4.5 and newer 4.x cost $5/$25 (not the $15/$75 of Opus 4 / 4.1); must precede the generic claude-opus-4 prefix.
    *[(f"claude-opus-4{sep}{minor}", (5.0, 0.50, 6.25, 25.0)) for minor in range(5, 10) for sep in ("-", ".")],
    ("claude-opus-4", (15.0, 1.50, 18.75, 75.0)),
    ("claude-haiku-5", (0.10, 0.01, 0.125, 0.50)),
    ("claude-haiku-4-5", (1.0, 0.10, 1.25, 5.0)),
    ("claude-haiku-4.5", (1.0, 0.10, 1.25, 5.0)),
    ("claude-fable", (10.0, 0.25, 12.50, 50.0)),
    # Antigravity logs effort-suffixed names, e.g. gemini-4-neon-xhigh / gemini-4-hydrogen-high.
    ("gemini-4-neon", (3.0, 0.15, 3.75, 15.0)),
    ("gemini-4-hydrogen", (1.5, 0.075, 1.875, 7.5)),
)


def _resolve_rates(model_name: Any) -> tuple[float, float, float, float] | None:
    name = str(model_name or "").strip().lower()
    if not name:
        return None
    if "/" in name:
        name = name.rsplit("/", 1)[-1]
    for pfx in ("us.anthropic.", "eu.anthropic.", "apac.anthropic.", "global.anthropic.", "anthropic."):
        if name.startswith(pfx):
            name = name[len(pfx):]
            break
    if name.endswith("[1m]"):
        name = name[:-4].strip()
    if name in RATES:
        return RATES[name]
    for prefix, rates in _PREFIX_RATES:
        if name.startswith(prefix):
            return rates
    return None


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
        model_raw = request.get("model")
        rates = _resolve_rates(model_raw)
        if rates is None:
            return {"available": False, "reason": f"No verified price for {model_raw}."}
        inp = int(request.get("input_tokens") or 0)
        cached = int(request.get("cached_input_tokens") or 0)
        write_raw = int(request.get("cache_write_input_tokens") or 0)
        eph_1h = max(0, int(request.get("ephemeral_1h_input_tokens") or 0))
        total_write = max(write_raw, eph_1h)
        write_1h = min(total_write, eph_1h)
        write_5m = total_write - write_1h
        fresh = max(0, inp - cached - total_write)
        out = int(request.get("output_tokens") or 0)
        ctx = int(request.get("context", inp) or 0)
        norm_model = str(model_raw or "").strip().lower().rsplit("/", 1)[-1]
        is_sol = norm_model.startswith(("gpt-6.1-sol", "gpt-6.2-sol"))
        long_input = 2 if (is_sol and ctx > 272000) else 1
        long_output = 1.5 if (is_sol and ctx > 272000) else 1
        if norm_model.startswith("claude-haiku-5") and ctx > 100000:
            # Haiku 5 long-prompt tier (> 100K tokens): every price component is 5x the base tier.
            long_input = long_output = 5
        speed_mult = multipliers[speed]
        buckets["input"] += fresh * rates[0] * long_input * speed_mult / 1_000_000
        buckets["cached"] += cached * rates[1] * long_input * speed_mult / 1_000_000
        write_usd = (write_5m * rates[2] + write_1h * (rates[0] * 2.0)) * long_input * speed_mult / 1_000_000
        buckets["cache_write"] += write_usd
        buckets["output"] += out * rates[3] * long_output * speed_mult / 1_000_000
    usd = sum(buckets.values())
    return {"available": True, "basis": "API-equivalent estimate before tax; subscription charges depend on the plan",
            "speed_assumption": speed, "rates_as_of": RATE_DATE, "rate_source": RATE_SOURCE,
            "usd_inr": fx, "fx_as_of": "user supplied" if "ZENO_USD_INR" in os.environ else FX_DATE,
            "fx_source": FX_SOURCE, "usd_by_type": buckets, "usd": usd, "inr": usd * fx}
