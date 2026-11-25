"""Pricing-table regression tests (USD per million tokens: fresh, cache read, cache write 5m, output)."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import token_cost  # noqa: E402


def _req(model, inp=0, cached=0, write=0, out=0, ctx=None):
    return {"model": model, "input_tokens": inp + cached + write, "cached_input_tokens": cached,
            "cache_write_input_tokens": write, "output_tokens": out, "context": ctx or (inp + cached + write)}


class TestTokenPricing(unittest.TestCase):
    def test_rates(self) -> None:
        r = token_cost._resolve_rates
        self.assertEqual(r("claude-sonnet-5-5"), (2.0, 0.10, 2.50, 10.0))
        self.assertEqual(r("claude-opus-5-5"), (4.0, 0.20, 5.00, 20.0))
        self.assertEqual(r("claude-haiku-5"), (0.10, 0.01, 0.125, 0.50))
        self.assertEqual(r("claude-haiku-4-5"), (1.0, 0.10, 1.25, 5.0))
        self.assertEqual(r("claude-opus-4-6"), (5.0, 0.50, 6.25, 25.0))
        self.assertEqual(r("claude-opus-4.5"), (5.0, 0.50, 6.25, 25.0))
        self.assertEqual(r("claude-opus-4-1"), (15.0, 1.50, 18.75, 75.0))
        self.assertEqual(r("claude-sonnet-4-5"), (3.0, 0.30, 3.75, 15.0))

    def test_cost_math_buckets(self) -> None:
        agent = {"token_precision": "exact",
                 "requests": [_req("claude-sonnet-5-5", inp=1_000_000, cached=2_000_000, write=1_000_000, out=500_000)]}
        c = token_cost.estimate(agent)
        self.assertTrue(c["available"])
        b = c["usd_by_type"]
        self.assertAlmostEqual(b["input"], 2.0)
        self.assertAlmostEqual(b["cached"], 0.2)
        self.assertAlmostEqual(b["cache_write"], 2.5)
        self.assertAlmostEqual(b["output"], 5.0)

    def test_haiku5_long_prompt_tier(self) -> None:
        short = token_cost.estimate({"token_precision": "exact", "requests": [_req("claude-haiku-5", inp=50_000, out=1000)]})
        long_ = token_cost.estimate({"token_precision": "exact", "requests": [_req("claude-haiku-5", inp=200_000, out=1000)]})
        self.assertAlmostEqual(short["usd"], 50_000 * 0.10 / 1e6 + 1000 * 0.5 / 1e6)
        self.assertAlmostEqual(long_["usd"], 200_000 * 0.50 / 1e6 + 1000 * 2.5 / 1e6)


if __name__ == "__main__":
    unittest.main()
