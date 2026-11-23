---
description: Edit one video or a multi-video sequential queue in Zeno v2.0.0 and deliver finished folders to ~/Downloads/Zeno Outs/<VideoName>/.
argument-hint: "\"<footage_dir>\" --script <gdoc_tab_url_or_path> --name \"<VideoName>\""
---
# /zeno

Inputs: `$ARGUMENTS` (use `.venv/bin/python` on macOS/Linux or `.venv\Scripts\python.exe` on Windows as `<PY>`).

Process 1, 2, or 3+ queued videos **one by one sequentially** in this chat, delivering every finished package to `~/Downloads/Zeno Outs/<VideoName>/` with automatic desktop sound & popup notification:
- **Zero Yes/No or Option Prompts**: NEVER ask Yes/No, numbered options (`1/2/3`), `"tell me to go ahead"`, or `"Should I proceed?"` — including on `[INCOMPLETE CLIPS]`, sparse transcripts, or `[ORDER BLOCKED]`.
- **Diagnostic & Cache Rules**: Never read `work/_clips/**` directly (denied by design); always use `<PY> scripts/edit_packet.py work/<project> --diagnose`, `--clip "<name>"`, or `--line <ID>`. Never use `--fresh` unless every single clip needs re-transcribing; prefer `--retry-incomplete` or `--clip "<name>"`.
- Deliverables: `reel_<VideoName>.mp4`, `SQ_<VideoName>.mp4`, Premiere `.prproj`/`.xml`, `review_sheet.jpg`, `<VideoName>.plan.edl`, `<VideoName>.script.txt`.

## Workflow (Host-AI Direct, Zero MCP)
1. **Single Video Inspect**: Run `<PY> scripts/zeno.py "<footage_dir>" --script "<gdoc_tab_url_or_path>" --name "<VideoName>" --inspect --compact`. Wait for completion without polling (`sleep`, `write_stdin`, or `manage_task`). If `[INCOMPLETE CLIPS]` appears or `editing_packet.md` shows `untranscribed_speech` / missing candidates with uncovered speech, automatically run `<PY> scripts/edit_packet.py work/<project> --diagnose` and `<PY> scripts/zeno.py "<footage_dir>" --script "<gdoc_tab_url_or_path>" --name "<VideoName>" --inspect --retry-incomplete --compact` without asking permission.
2. **Multi-Video Queue (`2+` videos)**: Run `<PY> scripts/zeno.py --queue <queue.json> --inspect-only --compact` (e.g. `work/queue.json`), then spawn **1 isolated subagent per queued video** returning only `~/Downloads/Zeno Outs/<VideoName>/` + status so `editing_packet.md` and `clip_sheet_*.jpg` stay out of the main chat.
3. **Plan, Repair & Deliver** (inside the single-video context/subagent):
   - Read **ONLY** `work/<project>/editing_packet.md` (or `editing_packet_p*.md` if chunked) and `work/<project>/clip_sheet_*.jpg` in one batch (never open `formats/*`, `scripts/*`, `facts.md`, `line_map.md`, `clip_caches.json`, or `work/_clips/**`).
   - Write `work/<project>/script_analysis.md` (first line `Format: <id> (<title>)`) and `work/<project>/plan.edl` (plus `work/<project>/decisions.txt` only if needed).
   - For `[BLOCKED]` fixes, run `<PY> scripts/edit_packet.py work/<project> --line <ID>` (or `--diagnose` / `--clip "<name>"`) instead of re-reading the whole packet.
   - Run `<PY> scripts/zeno.py --execute work/<project>/plan.edl --square --compact`, inspect `~/Downloads/Zeno Outs/<VideoName>/review_sheet.jpg` once, and immediately start the next queued video.
4. **Missing inputs**: Run `<PY> scripts/zeno.py --preflight --compact` and ask only for what is missing.
5. **Editorial & Visual Invariants**: Follow all rules in `CLAUDE.md` (every spoken line once in script order, `sp="ExactSpeaker"`, complete spoken sentences from `start - 0.05s` through `end + 0.06-0.10s` verified against `pauses:` in `editing_packet.md`, never cutting `<0.45s` mid-sentence breaths/commas, trimming only genuine dead-air pauses `>=0.50s` or `>=0.55s` in `split_h`/`split_v`, `z=1` splits with 14% headroom + advancing silent listener `peer=`, `z=sudden_in:1.08@t` or `>=0.45s`-pause `1.08` punch-ins without slicing continuous speech / `1.0->1.06` pushes, `#subs auto y=65` strict single-line subtitles only / `y=50` on `split_v`, `#page scroll`, purposeful story-driven transitions, script-accurate marks/flashcards/banners with live non-frozen video visuals during post-word marks/SFX without dialogue overlap, 2-layer 5–10% Gaussian blur 1:1 Square wrap, bottom 20–25% CTA-safe).
