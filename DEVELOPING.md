# DEVELOPING.md

For changing Zeno's own code. Not needed for a video edit.

## 7. Developing the Zeno code itself (not editing a video)

Use `.venv/bin/python` (Python 3.12; `uv venv --python 3.12 .venv && uv pip install --python .venv/bin/python -r requirements.txt`, or run `install.command` / `install.bat`). `scripts/zeno.py` re-execs itself into `.venv` automatically.

```bash
.venv/bin/python -m unittest tests.test_engine                                  # engine regression suite (~0.1 s, no API calls)
.venv/bin/python -m unittest tests.test_engine.TestZenoEngine.<test_name>       # one test
.venv/bin/python scripts/setup.py --smoke                                       # env check, never prints keys (--prewarm downloads the 360 MB wav2vec2 weights)
.venv/bin/python scripts/eval_alignment.py --help                               # score word timing against hand-marked labels
.venv/bin/python scripts/knowledge_sync.py pull|push|match "<wording>"                # knowledge kept in the server database (no commit or redeploy for new recipes)
.venv/bin/python scripts/format_library.py search "<script wording>"                  # look up how a format should look; `extract <video> --id ...` measures a reference locally, no tokens, video not kept
.venv/bin/python scripts/style_readiness.py [style] [--json]                    # measured readiness level (0-3) of a style from files and tests
.venv/bin/python scripts/clean.py [--apply]                                     # free disk (dry run by default; only touches work/ and input/standins|reference)
python service/test_keys.py                                                     # service: signup/admin/revoke/usage on local SQLite (add --database-url postgresql://... for Postgres)
python service/test_service.py [--no-live | --url https://<host> --token zeno_...]
python service/check_prompt_sync.py                                             # must pass after editing either copy of the Gemini prompt
cd service && uvicorn app:app --port 8000
railway up service --path-as-root --detach                                     # deploy the server (from the repo root; plain `railway up` uploads the whole repo and fails)                                       # run the key/transcription service locally
```
There is no linter or build step. `tests/test_engine.py` also scores a gold-set plan under `work/` (git-ignored), so that check only runs where the work folder exists.

## 8. Architecture (big picture)

**Director / Engine split.** The LLM Director (`agent/DIRECTOR.md`, `agent/EDIT_RULES.md`) makes every editing decision and writes `plan.edl`. Python in `scripts/` only transcribes, measures, checks and renders. Do not add editing decisions back into Python: a measurement or a safety check belongs in the Engine, a choice belongs in `EDIT_RULES.md`. `CLAUDE.md` and `AGENTS.md` (Antigravity/OpenAI) are parallel manuals and must be kept in sync.

**Pipeline** (`scripts/zeno.py` orchestrates): ingest/extract audio → transcribe → align → measure → write `line_map.md` + `facts.md` + clip sheets → the Director writes `plan.edl` → `--check-plan` → Yes/No gate → `render.py` (+ `premiere_export.py`, `verify.py`, token telemetry).
- `prepare.py` measures: transcripts, 10 ms energy, pauses, silent clips, faces. `script_order.py` holds the candidates per line and the plan checks (order, coverage, cuts, voices via `speaker_check.py`) and moves cuts to the quietest point of their gap. Per-clip caches live in `work/_clips/<stem>-<fingerprint>`.
- `edl_parser.py` is the lossless `plan.edl` ⇄ `plan.json` bridge; `render.py` + `layouts.py` turn segments into FFmpeg filter graphs (layouts, zooms, overlay transitions from `assets/transitions/*.json`, marks, SFX, loudness). `vision_tracker.py` (OpenCV/YuNet) drives framing.
- `scripts/align_engine/` is the alignment stack: Gemini verbatim transcript (`gemini_transcriber.py`) → `guardian.py`/lexicon loanword healing → Indic G2P (`phonetics/`, `uroman/` fallback) → wav2vec2 base CTC (`acoustic.py`, `viterbi.py`) → energy snaps in `onset_verifier.py`. wav2vec2 base (MIT) is the only aligner; MMS (CC-BY-NC) must not come back.
- Tuning thresholds live in `styles/<name>/spec.json` (`tuning`/`engine` blocks) read through `style_config.py`; learned editor rules persist in `styles/<name>/brain_memory.json` (`brain_memory.py`). New styles are learned from a reference video with `learn_reference.py` (scan → frames → align → standins).

**Transcription & Editorial Learning Service** (`service/`, FastAPI on Railway, deployed from this folder as its own root): holds the only Gemini key and collects team editorial learnings. Team PCs authenticate once via zero-touch Google OAuth (`python3 scripts/setup.py --login` or `install.command`, which opens the browser for `@gosupernova.live` sign-in (more domains only via `ZENO_ALLOWED_EMAIL_DOMAINS`) and automatically saves `.zeno_session.json` + `ZENO_CLOUD_URL` / `ZENO_CLOUD_TOKEN` in `.env`, with legacy `SCRIPTCUT_CLOUD_*` fallback). Keys are stored as SHA-256 hashes in Postgres (SQLite locally) and revocable at `/admin`. `scripts/token_tracker.py` posts per-run telemetry and `plan.auto.edl -> plan.edl` diffs to `/v1/telemetry`, and `python3 scripts/zeno.py --sync-learnings` syncs team rules via `/v1/learnings`. **Team rules:** the rules that ship in `styles/<name>/brain_memory.json` live in the release and are never uploaded (uploading them made every old version of a corrected rule come back to every PC). The server holds only rules a person recorded with `--remember` (`source: remember`; pulled copies are `source: team`). A rule's identity is its text (`rule_text_hash`, same function on both sides), so it is stored once. To withdraw a team rule, retire it: `POST /admin/api/learnings/retire {ids, retired}` (list with `GET /admin/api/learnings`); nothing is deleted, every PC drops it at its next sync, and a PC that still holds it cannot push it back. **Formats:** `agent/FORMATS.md` (for the Director) and `agent/formats.json` (for `script_order.transition_report`) describe the team's formats and each one's transition pattern; keep the two in step. `service/prompt.py` is a deliberate copy of the prompt in `gemini_transcriber.py` (Railway only sees `service/`); keep them identical.

**Job archive** (`scripts/job_archive.py`, server table `zeno_job_archive`, `POST /v1/jobs`, admin `GET /admin/api/jobs[/<id>]`): one text-only record per edit (script, script analysis, every plan version with the check result, decisions, line map, facts, verification text, split-panel notes, timeline, tokens, editor feedback). Plan versions are kept in `work/<project>/_history/` at every `--check-plan` and `--execute`; `zeno.py --feedback "..."` adds the human's words; a record that cannot be sent waits in `work/_job_outbox/`. A failed render is archived too (`kind=render_failed`). `ZENO_ARCHIVE=0` turns it off. Needs the service redeployed.

## Verified status & open improvements (audit of 2026-11-04)
Checked and passing: all Python files compile; `tests.test_engine` (28), `service/test_keys.py` (60 checks), `service/test_service.py --no-live` (6), prompt sync, JSON validity, shell syntax, `zeno.py --preflight`. Do not treat an item as fixed until it is under "Fixed".

**Fixed**
- `zeno.py` imports (`--swap-layout`, `--swap-take`, `--error-sfx` no longer crash); a Google Doc link loads only its `?tab=` (a link with no tab is refused).
- Lexicons have no key with two different values (Tamil `டேக்ஸ்` is "takes"; "tax" is `டாக்ஸ்`).
- Script reading (`parse_script_file`, also used by `learn_reference.dialogue`): reference, visual, character-list, link and scene-change lines are directions, not dialogue; `Scene changes` / `Back to` / `Cut to` / `Flashback` start a scene and get a transition; `(X Sign and Text Strikethrough)` and `[TEXT: "..." | strike]` become struck-through banners; layout notes such as `floating window ... horizontal split screen` or `Horizontal Split screen Top: Robot | Bottom: Lady 1` set the layout and who sits where; emoji marks (`❌` wrong, `✅` right) become marks; `Speaker: : text`, hyphenated names (`Co-worker 3`) and one name per character (`Robot` = `Female Humanoid Robot`) are handled.
- `group_takes` uses the script: two clips that each fit a different script line are never takes of one line (a learner repeating the teacher's corrected sentence used to be pruned, which dropped the ✓).
- Service: name-only signup needs `ZENO_SIGNUP_CODE`; company keys are not admin unless listed in `ZENO_ADMIN_EMAILS` (or `ZENO_ADMIN_ALLOW_DOMAIN=1`); only `gosupernova.live` unless `ZENO_ALLOWED_EMAIL_DOMAINS` says otherwise; Google ID token for our client id only; a revoked key stays revoked after a restart. These need a Railway redeploy of `service/`.
- Listener rule in every split: the other panel shows the other character from their silent clip (matched by file name, else by looks), walked through turn after turn; panels are read from `Top: A | Bottom: B`, `Name - Left screen` and the character list; no silent clip means a quiet moment (or a held frame) with a `[LISTENER_FALLBACK]` alert; names that appear only in the panel note are merged with the speaker (`Robot` = `Female Humanoid Robot`).
- Windows with no Git / a ZIP-installed folder: the installers install Git if missing (winget) and run `auto_updater.py --connect-git`, which turns the folder into a normal clone of the private repository without touching any file (the repository is public, so no GitHub account or sign-in is needed); `update.bat`/`update.command` connect instead of stopping; the updater says why it cannot update (`git_missing`, `no_git_repo`) instead of failing silently, and its pull fails fast instead of hanging on a sign-in prompt. Untested on a real Windows PC until the tester reports.
- `--style <name>` writes `# style: <name>` into the plan (and survives quick tweaks), so jobs count toward a style's readiness level and the team's learning; the gate prints which style the job follows.
- Pre-job gate (`zeno.py --preflight`, enforced by `--inspect`), `style_readiness.py`, the format library (`styles/formats/`), per-clip engine versions in caches, `--fresh`, automatic low-disk cleanup, the server transcript cache and team format learning (observations, admin approval, `--sync-learnings`).
- micronarrative now has two references (`variants` in its spec.json): W42 (left/right split, warm flash) and W43 Hindi (top/bottom split with the seam at 0.472 of the height, fast blur whips and a glitch frame, one 91 s split-screen lesson).

**Open**
- micronarrative is level 1 (RENDER-ONLY) in `style_readiness.py`: nothing is proven on real raw footage, there is no hand-marked gold set, 6 unknowns are open (including whether the transition style is chosen per reel, per scene type or by the script).
- "Horizontal split screen" is read as a horizontal divider (top and bottom, `split_v`); change `SCRIPT_SPLIT_LAYOUT` in `prepare.py` if the team means the opposite (the W43 reference confirms top and bottom).
- Only the strike-through on-screen text cue is rendered; the general text format is undefined.
- The Railway service must be redeployed for `/v1/selftest`, the transcript cache, `/v1/observations` and the security fixes to take effect; no admin page yet for approving formats.
- The renderer's blur-whip transitions, caption colours and the W43 split seam position (0.472, not 0.5) are measured or seen but not yet reproduced by `layouts.py`.

**Improvements (planned)**
- Editor scorecard after every render: clipped words, longest pause, A/V offset, shot-length spread, transition count, loudness, text readability, script coverage.
- J-cuts and L-cuts (audio leads or trails the picture by 4-12 frames) and reaction holds read from the script dialogue.
- Cut-aware contact sheets with a short text caption per reference, stored in Postgres, for people to review.
- Daily per-computer preflight with a desktop notification on failure.
- Measure alignment on a hand-marked gold set with `eval_alignment.py` before changing any threshold; measure editing numbers on the user's best finished videos and confirm with A/B tests.
- Keep `CLAUDE.md` and `AGENTS.md` in sync with a check script; tests for `render.py` and `layouts.py`; CI on every push; test the Windows installer.

