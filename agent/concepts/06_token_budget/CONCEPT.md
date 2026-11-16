# Tokens and cost reporting

Use a fresh chat per edit with only needed tools. Read the generated brief and measurements once. Keep the model's editorial decisions and visual review; run transcription, timing, rendering and verification locally through Zeno.

The entry packet is `editing_packet.md`; full source evidence remains available with `scripts/edit_packet.py <project> --line L12`. Model checkpoints are configured in `config/cost_controls.json` (default INR 40 API-equivalent estimate, 24 chat requests, 100k context at intake). `scripts/cost_control.py <project>` returns exit 2 when the checkpoint is reached; save the work and stop further planning calls. Host checkpoints are cooperative, not a provider billing cap, and cannot prevent an already-sent host request. Approved local renders can finish without another planning call.

Optional `scripts/direct_mode.py <project> --provider openai --dry-run` previews the bounded Sol API path. Real API planning is explicitly selected and separately billed; no credentials are borrowed from Codex. It reserves conservative next-call cost, caps output and calls, journals usage, and stops on ambiguous timeouts or evidence requiring human review. No automatic polish call. Full workflow and limitations: `LOW_COST_WORKFLOW.md`.

The usual 50k–70k range is historical, not a target. Count uncached input, cached input and output separately. Cached input is included in the combined total. A smaller number of model requests with a shorter relevant context saves usage.

`token_tracker.py` reads the current Codex session's cumulative counters or Claude usage within the job window. Unknown or ambiguous measurements are labeled unavailable. Reasoning is included in output. Historical Gemini transcription records are labeled reused; new transcription is measured separately.

The report includes a dated API-equivalent USD/INR estimate for supported models. It is not an invoice. Defaults are Standard speed and the documented reference exchange rate. Override with `ZENO_COST_SPEED` and `ZENO_USD_INR`. Unknown models have no guessed price. Tool fees, tax and post-render chat are excluded.

After rendering, read `work/<project>/token_report.md`. README and TOKEN_LEDGER rows are updated automatically. `<PY> scripts/token_tracker.py --project <project>` reads the saved report without creating another run. Use `--refresh` only for an intentional recalculation.

Reports include per-stage request counts and cost attributed by request timestamp, not a charge for Python rendering. Direct API usage is added separately and counted once. Preserve a baseline before the next inspect overwrites a project snapshot. `scripts/cost_control.py <trial-project> --compare <baseline.json>` compares saved counters without model calls; identical footage, checks and scope are required for a defensible savings percentage.
