# 09 · Team learning (what every user teaches the server)

**Split of work.** Everything heavy runs on each computer: wav2vec2 alignment, OpenCV, FFmpeg, rendering. The server only holds
what was learned and answers questions about it (its database, plus Gemini for transcription and embeddings). Data moves by API,
the same way audio goes to the server for transcription: no commit and no redeploy is needed for new knowledge. The server code
is deployed only when the server's own code changes.

## What we learn, where it is stored, how it is used
| What | Source | Stored in | Used for | Status |
|---|---|---|---|---|
| Per-format numbers of every verified job (shot length, zoom-ins, transitions per minute, marks, strike-through banners) | `format_learning.py` after `render.py` | `zeno_format_observations` | candidate recipes; an admin approves after 5 different scripts | built |
| Format recipes and reference learnings (split geometry, transitions, structure) | `knowledge_sync.py push` (admin) | `zeno_knowledge` (kind `format`, `reference`) with an embedding | every computer pulls them at the start of a job; `POST /v1/match` answers "which format is this wording?" | built |
| Transcripts by audio hash | `/v1/transcribe` | `zeno_transcript_cache` | a retry or second editor costs nothing | built |
| Editor corrections (`plan.auto.edl` -> `plan.edl` differences) | `token_tracker` telemetry | `zeno_runs.edl_diff_json` | candidate editor rules (same approval step) | stored, not yet aggregated |
| Takes an editor swapped for the engine's choice | same diffs | `zeno_runs` | tune take scoring | planned |
| Script shapes the reader did not understand (counts of unknown line patterns, never the text) | `prepare.py` | planned table | find parser gaps early (the W43 and W44 gaps were found by hand) | planned |
| Alignment trouble spots per language (low-confidence word counts) | aligner | planned table | decide where a gold set is worth hand-marking | planned |
| Cost per minute of finished video, per style and agent | telemetry | `zeno_runs` | budget checks | built |

## Rules
- Send numbers and short wording only. Never the script text (a hash identifies it), footage, audio (except to transcribe) or video.
- A recipe changes for everyone only after an admin approves it (`/admin/api/formats/<style>/<format>/approve`), or an admin publishes it
  (`knowledge_sync.py push`). One bad job must never change the team's recipes.
- Matching: the server uses embedding similarity when it can embed the wording (Gemini, `ZENO_EMBED_MODEL`) and alias/word matching
  otherwise. It re-embeds items that were published while embedding was down. `format_library.py` keeps a local copy of the same
  matching, so a computer still works offline.
- At the start of every job `zeno.py --inspect` pulls what was published since its last pull (`work/_knowledge/`, git-ignored). A pulled
  recipe replaces a committed one with the same id on that computer.

## Commands
```bash
python3 scripts/knowledge_sync.py pull                    # runs by itself in the pre-job gate
python3 scripts/knowledge_sync.py push                    # admin: publish styles/formats/*.json and each style's variants
python3 scripts/knowledge_sync.py match "floating window, horizontal split screen"
```
