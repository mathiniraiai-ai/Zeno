# Local debugging and token-cost audit

Date: 2026-11-16. Changes are local; no commit, push or deployment was performed.

## Implemented

- Short, synchronized `AGENTS.md` / `CLAUDE.md`, a compact Director guide, and job-specific briefs. The scrolling-page brief omits irrelevant motion/caption rules. Detailed reference syntax remains available when needed. Tool-free direct planning retains explicitly requested style instructions.
- Fresh Codex launchers (`edit-codex.command` / `.bat`) with session-only connector overrides. Validated against the installed CLI: all 10 configured MCP servers disabled. Existing chats and global settings are untouched. Managed tools may remain.
- `--compact` for bounded console diagnostics, complete local logs, preserved exit codes and failure tails. Desktop progress remains active. Pacing/style warnings are summarized with a link to their full log.
- Named Codex-session accounting with cumulative-counter deltas, duplicate-event suppression, workspace filtering and an exact recorded time window. Unavailable counters and unknown model prices are identified explicitly.
- Separate uncached input, cache reads, cache writes, output and reasoning counts. Versioned GPT-6.1 Sol API-equivalent estimates include dated INR conversion and speed assumptions, with a component breakdown. Subscription invoices, tool charges, tax and transcription pricing are not inferred.
- Reused transcription is separate from new transcription. Final clip durations are recalculated from the actual cuts instead of the empty initial plan. Existing caches are preserved.
- Saved report reads are read-only. Recalculation preserves the saved end time; a real render supplies a new end time. Repeated reporting updates the same output in SQLite, README and TOKEN_LEDGER. Job markers are isolated by available chat ID and edit identity.
- Service HTTP tests use temporary storage and no live Gemini key. CI discovers all regression tests and checks that the two instruction files remain identical.

## Bugs exposed by validation

The render regression intermittently exceeded the audio true-peak tolerance. The final mix now oversamples before limiting and leaves additional headroom for AAC reconstruction. Room-tone generation uses a fixed seed so equivalent renders are reproducible. The render regression now asserts the owner's -1 dBTP ceiling.

Loudness verification previously returned a passing default if FFmpeg could not measure the file. It now fails that measurement. The measurement cache includes the absolute file path and nanosecond modification time, preventing same-name files in different directories from sharing a result.

The vendored uroman TSV shell wrapper had CRLF line endings that failed Bash parsing. Only its line endings were normalized; attribution and code were preserved.

## Measured instruction reduction

Compared with repository HEAD at the start of this implementation review:

| File | Before | After | Reduction |
|---|---:|---:|---:|
| AGENTS.md | 21,593 bytes | 4,368 bytes | 79.8% |
| CLAUDE.md | 6,298 bytes | 4,368 bytes | 30.6% |
| agent/DIRECTOR.md | 8,977 bytes | 1,844 bytes | 79.5% |
| agent/EDIT_RULES.md | 28,350 bytes | 7,571 bytes | 73.3% |
| .claude/commands/zeno.md | 5,209 bytes | 1,612 bytes | 69.1% |

The new scrolling-page rule body is 6,151 bytes before script metadata. The previous saved W46 brief is 19,502 bytes including metadata, so those two sizes are not an exact like-for-like cost benchmark. Byte reductions are not measured token or rupee savings. The next comparable fresh-session edit should establish that baseline.

## Validation

| Check | Result |
|---|---|
| All Python regression suites | 197 tests passed, including synthetic vertical + square rendering, subtitles, transitions, SFX, accounting and thermal/GPU behavior |
| Local service authentication/data suite | 138/138 passed |
| Local HTTP suite, no live transcription | 7/7 passed |
| Client/server transcription prompt synchronization | Passed |
| Python syntax | 105 files passed |
| JavaScript syntax | 7 files passed |
| JSON parsing | 31 files passed |
| Shell syntax | 5 files passed |
| Agent instruction equality | Passed |
| Live preflight | 8/8 passed: FFmpeg, GPU, OpenCV, aligner, server authentication, audio upload, key store, Gemini reachability |
| Saved W46 plan through compact CLI | Blocking checks passed; 12 nonblocking pacing notes retained for editorial review |
| Codex launcher configuration | Accepted by installed CLI; all 10 configured MCP servers disabled |
| Git whitespace/error check | Passed |

Local logs: `work/debug_engine.log`, `work/debug_service_keys.log`, `work/debug_service_http.log`, `work/debug_render.log`, `work/debug_accounting.log`, `work/debug_saved_plan.log`; compact command logs are in `work/_logs/`.

## Limits and next run

This is a broad regression and integration pass, not proof that every possible input is bug-free. Windows execution, installer/package builds, interactive desktop behavior and a fresh paid direct-model planning run were not validated here. Live service checks passed, but no server code was deployed. The synthetic render covers the changed audio path; the full W46 production video was not rerendered during this development pass.

For the next cost comparison, start a fresh edit via `edit-codex.command` (Mac) or `edit-codex.bat` (Windows), supply the same script tab, footage and Notion name, and retain the caches. Compare saved reports only for equivalent work and model/speed settings. Keep development/debugging outside that edit's accounting window.
