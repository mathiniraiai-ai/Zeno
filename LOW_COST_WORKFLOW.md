# Zeno bounded planning workflow

Automatic edits can share a chat: each `--auto-plan` dispatch uses isolated planning context. Supply footage, the exact script tab or local script, and the exact Notion name. Run `--inspect ... --auto-plan --compact` once. The planner receives selected rules, measured evidence and clip sheets; Python checks and renders the plan. Do not reread the full packet in the outer chat. Large-chat intake blocks still apply to manual planning; outer-chat usage and per-project request/spending limits still count. Shared chat is a convenience, not a promise of zero history overhead.

Editorial quality takes priority over a call-count target. Keep the complete requested style skill in the initial planning request; a shortened style section is not a substitute. Corrections retain the saved emotional analysis and approved exceptions. Reduce redundant context, approval exchanges and status polling, not required editorial evidence or review. A technical plan pass is not proof of pacing, emotional impact or effect quality; inspect the rendered result. If a budget prevents necessary work, report the unfinished review instead of calling the edit complete. Render automatically without a Yes/No exchange unless the user requested plan-only review.

## Enforced limits

`config/cost_controls.json` sets these defaults:

| Control | Default |
|---|---:|
| Automatic planner requests per invocation | 2: plan, then one correction |
| Managed requests over the saved project's lifetime | 8 maximum |
| Requests carrying the full planning context | 2 maximum |
| Combined full-context conservative input reserve | 100,000 |
| Targeted correction text | 24,000 UTF-8 bytes maximum |
| New-edit outer chat context checkpoint | 100,000 tokens |
| Outer chat request checkpoint | 8 |
| Model cost estimate checkpoint | INR 40 |
| Optional API output limit, including reasoning | 8,000 tokens |

These are ceilings, not targets. A passing first plan needs no correction call. Fresh correction requests contain only failed lines, relevant measured evidence, neighboring cuts and essential rules. They carry no conversation handle or repeated images. Their responses are merged into the saved plan; unaffected cuts and headers remain unchanged. Ambiguous failures, unsafe patches and proposed new exceptions stop for review.

`request_budget.sqlite3` records reservations before dispatch, serializes concurrent planners, and survives restarts and repeated inspect runs in the same project. Pending or unknown usage blocks further dispatch. Do not delete the ledger or pending-call records to retry. A new chat does not replenish the project budget. `[PLANNER STOPPED]` must not trigger manual planning or a packet reread in the outer chat.

The full-context reserve uses UTF-8 bytes plus framing and image allowances. It is deliberately conservative, not an exact tokenizer count or a guarantee that billed cache tokens remain below 100,000. Small corrections still have input, output and potentially cached tokens. A cache hit records reused input again; it does not give later requests free memory of the rules. Savings come from omitting the large context on subsequent calls.

The hard reservation limit covers Zeno-managed dispatches. The surrounding chat app controls its own requests, and a subscription CLI may make internal requests. Zeno cannot enforce a whole-account provider cap. Host checkpoints are additional safeguards, not a subscription billing cap. Unknown usage remains unknown. Four to six total calls is a workflow aim, not a proven end-to-end guarantee.

## Local supporting-reference retrieval

The planner automatically uses `scripts/editorial_retrieval.py` before its initial request and scoped corrections. It runs locally using Python's standard library: no external plugin, embedding model, network request or separate installation is required. Check readiness with `.venv/bin/python scripts/editorial_retrieval.py --check`.

Retrieval searches only `agent/REFERENCE.md` and local approved format recipes in `styles/formats/`. It selects at most three complete excerpts within 6,000 UTF-8 bytes and the remaining prompt allowance. Mandatory rules, the complete selected style, saved emotional analysis, approved exceptions and required measurements are supplied independently, never filtered by search. Existing brief and reference-library selection remain in place. Retrieved examples cannot override current rules; historical measured geometry is reference material, not a new requirement.

Each project gets `retrieval_planning.json` and, when used, `retrieval_correction.json`, recording source paths, content hashes, selected bytes, skipped oversized matches, warnings and retrieval time. Source updates take effect on the next request; no index cleanup is needed. Exact duplicate excerpts are omitted. This is lexical retrieval, not semantic/vector search. A no-match result means no optional excerpt was added; it is not proof that the editorial evidence is complete. Savings and editorial quality still require a real-video benchmark, and retrieval can add useful context compared with a run that previously fetched no reference at all.

## Optional direct API

Direct OpenAI API planning is explicit opt-in with separate API credentials and billing. First run:

```sh
.venv/bin/python scripts/direct_mode.py work/<project> --provider openai --dry-run
```

The dry run makes no paid call. Actual runs reserve input/image and output cost before dispatch, use no automatic network retry, and journal provider usage. A timeout or missing usage stops further calls. Failed attempts restore the original plan and retain candidates/check logs for review. No silent provider switching or Codex credential reuse is allowed.

## Verification and reporting

Keep all editorial, voice, timing, render and visual checks. Approved local checks/rendering can finish after a model budget stop. Report uncached input, cache reads, cache writes, output, combined total, measured requests, elapsed time, new/reused transcription and unknowns. INR costs are dated API-equivalent estimates, not subscription invoices.

Offline regression tests validate bounds, persistence, targeted patches and accounting. They do not establish a live savings percentage or final video quality. Run a separate explicitly authorized fresh-chat benchmark to measure those.
