# Zeno low-cost editing workflow

Implemented locally on 2026-11-16. Real savings are pending the fresh-chat trial. The INR figures below are API-equivalent model estimates before tax, not subscription invoices; transcription and host/tool charges are separate.

## Default: fresh chat, no API key

Open `edit-codex.command` (Mac) or `edit-codex.bat` (Windows). The launcher explicitly chooses `gpt-6.1-sol`, disables configured unused connectors for this session only, and supplies the short editing workflow. Existing global settings are untouched; managed tools can still remain. Alternatively open a new desktop chat in this project and select 6.1 Sol. Do not paste previous chats or the old long AGENTS.md into it.

For the prepared W45 comparison, the handoff is `agent/NEXT_COST_TEST.txt`. The launcher accepts `--prompt-file agent/NEXT_COST_TEST.txt`. Do not start this trial in the development chat.

1. Supply the local footage folder, exact script tab and Notion name. Run `--inspect ... --compact` with the local venv interpreter. This performs live preflight, measures/reuses footage, writes an editing packet and reports the spending checkpoint.
2. Read `editing_packet.md`, every clip sheet and the explicitly requested style once. The packet preserves every script line, candidate, measured word, framing note and warning. Only redundant speech interval lists are omitted; all detailed evidence remains in the original files. Do not additionally load the full brief, facts and line map. For one problem use `scripts/edit_packet.py <project> --line L12`.
3. Before planning or each correction, run `scripts/cost_control.py <project>`. Exit 2 means stop further model work, save progress and explain the checkpoint. Write the analysis and complete plan together and check it in the same call. Fix actual blockers, review nonblocking advice once, and avoid unchanged repeated checks.
4. Obtain the existing Yes/No approval on the concrete plan. Run `--execute <plan> --square --compact`. Python owns rendering, progress, export and verification. Use the host's process completion/wait mechanism; never call the model solely to read progress files. Review the final measurements and pictures.
5. Deliver the files and saved usage report, including request counts, cached input and stage costs. Keep coding/debugging in a separate chat. Do not change FPS or omit quality checks to save model tokens.

## Spending policy

`config/cost_controls.json` defaults:

| Control | Default |
|---|---:|
| Model estimate checkpoint | INR 40 |
| Chat request checkpoint | 24 |
| Context limit at new-edit intake | 100,000 tokens |
| Direct Sol calls, including corrections | 3 per edit |
| Direct output limit per call, including reasoning | 8,000 tokens |
| Direct text payload limit | 100,000 UTF-8 bytes |
| Direct reasoning | medium |

These are safeguards, not spending targets. Config changes are deliberate owner choices. `ZENO_MODEL_BUDGET_INR` overrides the rupee checkpoint; `ZENO_SESSION_LIMIT` overrides the intake context limit. Prices and FX come from `token_cost.py`, with dated sources; `ZENO_USD_INR` and `ZENO_COST_SPEED` are existing reporting overrides.

A desktop/CLI agent's next request is controlled by its host. Zeno can check between workflow stages but cannot intercept every host call or impose a subscription billing cap. Unknown or delayed host counters are disclosed. Completed report windows remain frozen. Local approved renders/checks can finish after the model checkpoint is reached.

## Optional: bounded direct Sol API planning

Choose this explicitly. It requires an OpenAI API account and `OPENAI_API_KEY` supplied securely in the process environment. Never paste a key into chat or borrow Codex credentials. No API credentials are bundled or created, and no paid live test was made during implementation.

After inspect:

```sh
.venv/bin/python scripts/direct_mode.py work/<project> --provider openai --dry-run
.venv/bin/python scripts/direct_mode.py work/<project> --provider openai
```

The dry run uses no model call and reports whether the API key is available without showing it. The planner sends selected rules, measured evidence and all clip sheets. Each correction is a self-contained request with the current candidate and bounded diagnostics; it has no accumulated chat history, tools or unrelated connectors. Actual provider usage is saved in `direct_usage.jsonl` and included in the final report.

Before each call it reserves conservative input/image cost plus the full output allowance against the configured budget. UTF-8 bytes and image allowances are reserves, not exact token counts or a provider-enforced invoice limit. Unknown host usage means the combined job total is incomplete. There is no automatic network retry, no extra polish round after passing, and no silent model fallback. The legacy Claude path remains explicit via `--provider claude`; the Sol controls do not govern that legacy runner.

A timeout leaves `direct_call_pending.json` and stops subsequent paid requests until usage is reconciled. Do not delete it to retry blindly. A crashed process can leave `direct.lock`; first confirm the process has ended and reconcile the pending call. Incomplete responses, repeated unchanged failures, short voice uncertainty or proposed exceptions stop for review. The original plan is restored on failure; `direct_candidate.txt`, check logs and history preserve the proposal. A successful plan still needs human approval before rendering.

Sources for the integration: https://developers.openai.com/api/docs/guides/text and https://developers.openai.com/api/docs/models/gpt-6.1-sol.

## Regression fixes and validation scope

- Scene headings accept hyphen, en dash and em dash, so headings are not spoken lines.
- Repeat checking treats scripted “gotta” and “got to” consistently while retaining occurrence counts and word timestamps. Extra repetitions and stutters still block.
- Candidate lists expose each matching source before extra takes of one source; text similarity does not establish the speaker.
- Short voice disagreements remain blocking review items where previously blocking. They are labelled uncertain and request targeted review rather than repeated classification. Existing isolated-short-doubt handling remains; unchecked voices must still be disclosed.

The parser fix can change line IDs when a fresh inspect excludes previously misclassified scene headings. Build a new plan for the fresh trial; do not copy the old W45 plan's line numbers or exceptions blindly. The finished exports and cached transcriptions remain untouched. No change here invalidates the paid transcript cache.

Measure savings only after a fresh comparable edit. Read `COST_OPTIMIZATION_REPORT.md` for local validation and remaining live-test limits.
