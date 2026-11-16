# CLAUDE.md

Guidance for Claude Code in this repository. This file is re-sent on every step of every chat, so it holds only what every
chat needs; edit rules live in `agent/` and reach an edit through `work/<project>/brief.md`.

# Zeno

Script-led AI video editing harness (`1080x1920` MP4). Zeno only transcribes, measures and renders. **The agent makes every
editing decision and writes `plan.edl`.** To edit a video, run `/zeno` (`.claude/commands/zeno.md`): it is the whole procedure.
Finished videos go to `~/Downloads/Zeno Outputs/<Notion name>/`.

## 1. Hard rules
1. **Workspace isolation:** never modify files outside this Zeno folder (team PCs `~/Documents/Zeno`; owner `/Users/mathiniraiselvan/Documents/Office project/Zeno`). Never touch other projects in `Documents`.
2. **Notion name:** never invent folder names. Ask for the Notion name and pass `--name "<Notion name>"`. Each edit gets `~/Downloads/Zeno Outputs/<Notion name>/` with `reel_<Notion name>.mp4` (9:16), `SQ_<Notion name>.mp4` (1:1), the timeline, `<Notion name>.prproj` / `.premiere.xml`, the script and the plan; a second edit becomes `<Notion name> (2)`. Raw clips can stay in any local folder.
3. **Takes:** the agent chooses the take from `line_map.md` and `facts.md`. Never trust file names, file order or first/last take: the script is the timeline.
4. **Speakers:** set `sp="<name>"` on every segment. `--check-plan` blocks `WRONG VOICE`: pick another take or ask for a reshoot. `voices not checked` goes in the report.
5. **Never read** `work/*/words.json`, `silences.json`, `align.json` (unless debugging one word timing) or `scripts/*.py` during an edit.
6. **One chat per video**, with unused MCP connectors off. Every step re-sends the whole chat. A hook (`scripts/chat_guard.py`) refuses a new edit in a chat over 250k, and `--preflight` / `--inspect` stop with `[BLOCKED] NEW CHAT NEEDED`: tell the user to type `/clear`. Changes after a render go in a new chat when `[TOKENS]` says so ("continue the edit in work/<project>/plan.edl: <change>").
7. **Clean editorial style:** captions `#subs auto y=68` ON by default (`#subs off` only on request); split screens never zoom (`z=1`); transitions only where the story changes place, layout or section; zero untrimmed stumbles or repeats; no unrequested B-roll, banners or CTAs. Details: `agent/EDIT_RULES.md` (owner's rules there override older wording anywhere).
8. **Feedback:** whenever the user comments on a plan or a finished video, first run `<PY> scripts/zeno.py --feedback "<their words>" --plan work/<project>/plan.edl`, then act on it.
9. **Never debug Zeno's code inside an edit chat.** If a tool gives a wrong result, report what you saw and stop; fixing Zeno is a separate chat (read `DEVELOPING.md` there).

## 2. Tokens and the end-of-job report
- A video usually takes 50k–70k agent tokens. Quality comes first; it is not a hard stop. Remove only waste: the biggest waste is extra steps, because each step re-sends the whole chat (the "re-read" number).
- At the end of every job print the token table Zeno prints and append a row to `README.md`:

```markdown
| Date | Task | Context tokens (start → end) | Tokens used | Re-read (cache) | 5-hour limit | Weekly limit |
|---|---|---|---|---|---|---|
| YYYY-MM-DD | **<Language> – "<Title>"** (<N> clips, <M> script lines): <MM:SS> final, <K> ✓/✗ marks → `~/Downloads/Zeno Outputs/<name>.mp4` | <start> → <end> | ~<delta> | <re-read from the Zeno report> | <start>% → <end>% | <start>% → <end>% |
```

## 3. Python, setup and the commands
- **Never run bare `python`/`python3`.** `<PY>` is `.venv/bin/python` (Mac/Linux) or `.venv\Scripts\python.exe` (Windows). If `.venv` is missing, run the installer (`bash install.command` / `powershell -NoProfile -ExecutionPolicy Bypass -File scripts/install_windows.ps1`) and wait; never hunt for a system Python.
- **Pre-job gate:** if the user has not given the inputs yet, run `<PY> scripts/zeno.py --preflight`, show the PASS/FAIL table and ask: "Share the script link (the Google Doc tab), the footage folder, and the Notion name for this video." If the inputs are already given, go straight to `--inspect`: it runs the same live checks first and stops with `[BLOCKED]` (exit 2) on any failure. Footage must be local; a Google Doc link must end in `?tab=...`. Never use `--skip-preflight`.
- `<PY> scripts/zeno.py --inspect <footage> --script <script> --name "<Notion name>" [--lang kn|ta|hi|te|ml|en|auto] [--style <name>]` writes `work/<project>/brief.md`, `line_map.md`, `facts.md`, `clip_sheet_<n>.jpg` and a `plan.edl` header.
- `<PY> scripts/zeno.py --check-plan work/<project>/plan.edl` until it passes; deliberate exceptions go in `work/<project>/decisions.txt` (`accept L2 <reason>`).
- Ask the user Yes/No once before rendering (skip it only when the user said to render without asking), then `<PY> scripts/zeno.py --execute work/<project>/plan.edl --square` (renders, exports Premiere files, verifies order, loudness `-11.5 LUFS`, cuts, splits and frames).
- `<PY> scripts/zeno.py --check` lists installed dependencies without reading secrets.

## 4. Caches, GPU, secrets
- Never ask to clear caches and never delete them by hand: `work/_clips/<stem>-<fingerprint>` refreshes itself. Only if a result is wrong for a reason the code cannot see, run once with `--fresh`. Disk cleanup: `<PY> scripts/clean.py` (dry run), show the total, ask once, then `--apply`.
- Zeno picks the GPU by itself (CUDA / Apple MPS for the aligner; VideoToolbox / NVENC / QSV / AMF for H.264, falling back to `libx264`) and runs at low priority. `ZENO_DEVICE`, `ZENO_ENCODER`, `ZENO_MAX_THREADS`, `ZENO_COOL=0` override.
- Never open, print or paste `.env`, `.zeno_session.json` or any key. Check setup with `<PY> scripts/setup.py --smoke` or `--whoami`; read `.env.example` for variable names.

## 5. Where things are
- An edit: `/zeno` → `work/<project>/brief.md` (the job's rules, cut from `agent/EDIT_RULES.md`, `agent/FORMATS.md` and `agent/REFERENCE.md`).
- Layout modes, mark packs, `plan.edl` syntax, SFX library: `agent/REFERENCE.md`. Styles: `styles/<name>/`. Other agents: `AGENTS.md`, `agent/DIRECTOR.md`.
- Changing Zeno itself: read `DEVELOPING.md` first (tests, architecture, verified status). Never read it during an edit.
