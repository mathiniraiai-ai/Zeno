# Zeno low-cost editing workflow

The INR figures below are API-equivalent model estimates before tax, not subscription invoices; transcription and host/tool charges are separate.

## Default: `< 1,00,000`-token 1-call subscription workflow, one video per chat (no API key)

Editors start each video in a new desktop or CLI chat, with claude.ai connectors off in this folder (`.claude/settings.json`), so every step re-reads a small chat. The planner makes at most 2 model calls per video (the plan, then one fix); if the plan still fails it prints `[PLANNER STOPPED]` and the editor decides, the chat does not plan by hand. Editors may also work in the chat directly, or launch a clean CLI session via `edit-codex.command` (Mac) / `edit-codex.bat` (Windows). Each video's `--inspect` starts its own isolated per-video telemetry and cost window (`INR 40` / `24` chat requests per video).

1. Supply the local footage folder, exact script `?tab=...` (or local path), and exact Notion name (given by the editor). Run:
   ```sh
   .venv/bin/python scripts/zeno.py --inspect <footage> --script <script> --name "<name>" --auto-plan --compact
   ```
   (Windows: `.venv\Scripts\python.exe`). This runs live preflight, inspects/reuses cached clips, invokes Zeno's isolated 1-call subscription planner (`claude -p` or `codex exec` using the editor's existing subscription with zero connector/history re-read overhead — typically **`20k–55k` total tokens**, well under `< 1,00,000` tokens including cache), validates `--check-plan`, and prints the concrete plan Pitch Card.
2. When `--auto-plan` passes `[PLAN PASS]`, **do NOT re-open** `editing_packet.md`, `line_map.md`, `facts.md`, `EDIT_RULES.md`, or `clip_sheet_*.jpg` in the outer chat. Read `editing_packet.md` and `clip_sheet_*.jpg` once (plus `styles/<name>/SKILL.md` if requested) **only** if manual planning is required, or run `<PY> scripts/edit_packet.py <project> --line L12` for a targeted line revision.
3. Before manual planning or each correction round, run `<PY> scripts/cost_control.py <project>`. Exit 2 means stop further model work for that video, preserve saved progress, and explain the checkpoint. When editing manually, write `script_analysis.md` and `plan.edl` together and run `--check-plan` in the same call.
4. Show the concrete plan Pitch Card and obtain Yes/No once (unless the user explicitly pre-authorized queued rendering across a batch of videos). Run:
   ```sh
   .venv/bin/python scripts/zeno.py --execute <plan> --square --compact
   ```
   as **one single blocking/completion-notified run** — never poll `write_stdin` or progress files in a loop. `verify.py` checks both 9:16 and 1:1 Square framing and builds a single compact `review_sheet.jpg` automatically; never write custom FFmpeg/Python frame-extraction scripts. Inspect `review_sheet.jpg` once (or on failure) and fix any failed checks.
5. Deliver the rendered files and saved per-video usage report, including request counts, uncached/cached input, output, new vs reused transcription, and stage cost estimates. Keep coding/debugging in a separate chat. Do not change FPS or omit quality checks to save model tokens.

## Spending policy

`config/cost_controls.json` defaults:

| Control | Default |
|---|---:|
| Model estimate checkpoint (isolated per video) | INR 40 |
| Chat request checkpoint (isolated per video) | 24 |
| Context limit at new-edit intake (multi-video queue) | 1,000,000 tokens |
| Planner calls (Claude, Codex or Sol), including corrections | 2 per edit |
| Direct output limit per call, including reasoning | 8,000 tokens |
| Direct text payload limit | 100,000 UTF-8 bytes |
| Direct reasoning | medium |

These are safeguards, not spending targets (`< 1,00,000` total tokens per video including cache is the expected envelope; `20k–55k` is typical with `--auto-plan`). Config changes are deliberate owner choices. `ZENO_MODEL_BUDGET_INR` overrides the rupee checkpoint; `ZENO_SESSION_LIMIT` overrides the intake context limit (`1,000,000`). Prices and FX come from `token_cost.py`, with dated sources; `ZENO_USD_INR` and `ZENO_COST_SPEED` are existing reporting overrides.

A desktop/CLI agent's next request is controlled by its host. Zeno checks between workflow stages and isolates each video's telemetry window on `--inspect`, while keeping completed report windows frozen. Local approved renders/checks can finish after the model checkpoint is reached.

## Optional: bounded direct Sol API planning

Choose this explicitly. It requires an OpenAI API account and `OPENAI_API_KEY` supplied securely in the process environment. Never paste a key into chat or borrow Codex credentials. No API credentials are bundled or created.

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

The finished exports and cached transcriptions remain untouched; no workflow doc or planner update invalidates the paid transcript cache.
