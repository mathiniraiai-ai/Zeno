# Cost-control implementation — 2026-11-16

Status: implemented locally. No push, deployment, paid planning call or new video render. Actual savings await the fresh-chat W45 benchmark.

## What changed

| Area | Implemented behavior |
|---|---|
| Fresh context | Codex launcher selects 6.1 Sol, disables configured unused connectors for that session, and accepts a small job handoff. Intake context default reduced from 250k to 100k. |
| Editing evidence | Inspect creates `editing_packet.md`: applicable rules, all candidates, words, framing, warnings and picture references. Redundant voice interval lists remain on disk. Specific-line evidence can be retrieved without loading the whole job. |
| Local execution | Existing Python inspect/render/verify orchestration retained. Instructions require one running process with native progress and completion waits, rather than repeated model-driven progress inspection. |
| Spending checkpoints | Configurable INR 40 model-estimate and 24-request checkpoints. Status at inspect/check and before another planning/correction round; completed accounting windows stay frozen. Unknown usage is explicit. |
| Optional direct planning | Responses API integration for Sol, 3-call maximum per edit, 8k output cap, conservative next-call reserve, no accumulating chat history, no tools, no automatic network retries or polish round. No automatic provider switch. |
| Failure handling | Durable usage journal and pending-call reservation; ambiguous billing stops retries. Failed/incomplete candidates restore the prior plan. New exceptions and voice uncertainty require review, not automatic waivers. |
| Reports | Host and direct usage combined without counting direct calls twice; per-stage request counts/input/cache/output/estimated cost. Read-only baseline comparison command. Archive includes checkpoints/direct report/packet manifest. |
| Warning fixes | En/em dash scene headings excluded from dialogue; “gotta”/“got to” repeat comparisons keep occurrence counts and real word boundaries; each matching source exposed before extra takes of one source; uncertain short voices remain review blockers. |
| Stale decisions | When script line IDs/text change, old exceptions and analysis move into history before the new packet is built. Candidate-only changes preserve approved exceptions. |

## Validation

- Final full local suite: 228 tests passed. The intake-policy regression also passed separately with an explicit context-limit override. Earlier focused cost/accounting checks passed all 47 tests; these overlap with the full suite, so do not add the counts.
- 13 changed/new Python modules parse, configuration JSON validates, AGENTS.md and CLAUDE.md match and remain below the entry-rule size guard.
- Fresh-launch dry run pins `gpt-6.1-sol` and includes the exact W45 test handoff. It does not start a model session.
- Actual W45 script parses as 27 spoken lines with both scene headings excluded. No stored production plan was rewritten for this check.
- Actual W45 direct-planner dry run: 59,469 text bytes, all 1 clip sheet included, conservative first-call reserve approximately INR 24.36 at the report's reference FX. This is a reserve, not predicted actual cost or measured tokens. Zero paid calls. API key not configured in this process.
- Paid transcript and alignment cache fingerprints do not include the changed parser/packet/control modules. Existing caches and completed exports were preserved.
- Git whitespace/error check passed. No service implementation changes required a deployment.

Logs: `work/debug_cost_controls_full.log`, `work/debug_cost_controls_targeted.log`, `work/debug_cost_launcher.log`. The original W45 token snapshot is preserved at `work/_benchmarks/W45_before_cost_controls.json` (baseline INR 133.57 API-equivalent estimate, 12,085,504 cached input tokens).

## Limits and next trial

Host checkpoints cannot intercept every desktop/CLI model request or guarantee a subscription invoice cap. Direct-mode input/image estimates are conservative reserves, not exact tokenizer counts. Unknown host usage means the whole-job cost remains incomplete. Transcription, tax and post-render discussion are outside the model estimate.

The optional direct API integration is covered by offline response/failure tests and a dry run, not a paid end-to-end provider test. The standard fresh-chat path needs no API key. Windows launcher interaction was not tested on Windows. No cost-reduction percentage is claimed yet.

Open a fresh chat using `agent/NEXT_COST_TEST.txt`, or launch `edit-codex.command --prompt-file agent/NEXT_COST_TEST.txt`. Follow `LOW_COST_WORKFLOW.md`. Use the same clips, model and quality checks; count reused transcription separately. The corrected script parser changes line IDs, so plan afresh and review opening-dialogue coverage/short-voice evidence instead of copying old exceptions. Compare measured usage and reviewed video quality with the preserved baseline.
