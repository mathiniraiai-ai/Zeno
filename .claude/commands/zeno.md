---
description: Edit one video or a multi-video sequential queue in Zeno v2.0.0 and deliver finished folders to ~/Downloads/Zeno Outs/<VideoName>/.
argument-hint: "\"<footage_dir>\" --script <gdoc_tab_url_or_path> --name \"<VideoName>\""
---
# /zeno

Inputs: `$ARGUMENTS` (use `.venv/bin/python` on macOS/Linux or `.venv\Scripts\python.exe` on Windows as `<PY>`).

Process 1, 2, or 3+ queued videos **one by one sequentially** in this chat with **zero Yes/No prompts**, delivering every finished package to `~/Downloads/Zeno Outs/<VideoName>/` with automatic desktop sound & popup notification:
- Deliverables: `reel_<VideoName>.mp4`, `SQ_<VideoName>.mp4`, Premiere `.prproj`/`.xml`, `review_sheet.jpg`, `<VideoName>.plan.edl`, `<VideoName>.script.txt`.

## Workflow (Host-AI Direct, Zero MCP)
1. **Single Video Inspect**: Run `<PY> scripts/zeno.py "<footage_dir>" --script "<gdoc_tab_url_or_path>" --name "<VideoName>" --inspect --compact`. Wait for completion without polling (`sleep`, `write_stdin`, or `manage_task`).
2. **Multi-Video Queue (`2+` videos)**: Run `<PY> scripts/zeno.py --queue <queue.json> --inspect-only --compact` (e.g. `work/queue.json`), then spawn **1 isolated subagent per queued video** returning only `~/Downloads/Zeno Outs/<VideoName>/` + status so `editing_packet.md` and `clip_sheet_*.jpg` stay out of the main chat.
3. **Plan, Repair & Deliver** (inside the single-video context/subagent):
   - Read **ONLY** `work/<project>/editing_packet.md` (or `editing_packet_p*.md` if chunked) and `work/<project>/clip_sheet_*.jpg` in one batch (never open `formats/*`, `scripts/*`, `facts.md`, `line_map.md`, or `clip_caches.json`).
   - Write `work/<project>/script_analysis.md` (first line `Format: <id> (<title>)`) and `work/<project>/plan.edl` (plus `work/<project>/decisions.txt` only if needed).
   - For `[BLOCKED]` fixes, run `<PY> scripts/edit_packet.py work/<project> --line <ID>` instead of re-reading the whole packet.
   - Run `<PY> scripts/zeno.py --execute work/<project>/plan.edl --square --compact`, inspect `~/Downloads/Zeno Outs/<VideoName>/review_sheet.jpg` once, and immediately start the next queued video.
4. **Missing inputs**: Run `<PY> scripts/zeno.py --preflight --compact` and ask only for what is missing.
5. **Editorial & Visual Invariants**: Follow all rules in `CLAUDE.md` (every spoken line once in script order, `sp="ExactSpeaker"`, pause-snapped word-bound cuts, `z=1` splits with 14% headroom + advancing silent listener `peer=`, `1.08` punch-ins / `1.0->1.06` pushes, `#subs auto y=68` / `y=50` on `split_v`, `#page scroll`, purposeful story-driven transitions, script-accurate marks/flashcards/banners, 2-layer 5–10% Gaussian blur 1:1 Square wrap, bottom 20–25% CTA-safe).
