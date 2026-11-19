# Zeno v2.0.0 — AI Video Editor

Use `.venv/bin/python` (macOS/Linux) or `.venv\Scripts\python.exe` (Windows) as `<PY>`. Zeno v2.0.0 uses the **Host-AI Direct workflow** (zero MCP servers, direct Host-AI planning). Process 1, 2, or 3+ videos **sequentially one by one** with **zero Yes/No prompts** until every video is delivered to `~/Downloads/Zeno Outs/<Name>/`. Never poll background jobs (`sleep`, `write_stdin`, `manage_task`); if a command backgrounds, end your turn and wait for async wakeup, otherwise wait for each command to finish. For missing inputs, run `<PY> scripts/zeno.py --preflight --compact` and ask only for what is missing.

## Host-AI Direct Workflow

### Single Video
1. **Inspect**: `<PY> scripts/zeno.py "<footage>" --script "<gdoc_tab_url_or_path>" --name "<Name>" --inspect --compact`
2. **Read Once (1 batch)**: Read **only** `work/<project>/editing_packet.md` (or `editing_packet_p*.md` if chunked) and `work/<project>/clip_sheet_*.jpg` in one batch. Never open `formats/*`, `scripts/*`, `facts.md`, `line_map.md`, or `clip_caches.json`.
3. **Plan**: Write `work/<project>/script_analysis.md` (`Format: <id> (<title>)` on line 1) and `work/<project>/plan.edl` (plus `work/<project>/decisions.txt` only if needed for genuine unscripted gaps, e.g. `accept <id> <reason>`).
4. **Execute, Repair & Deliver**: `<PY> scripts/zeno.py --execute work/<project>/plan.edl --square --compact` — validates script order/cuts/voices, snaps pauses, renders `9:16` + `1:1` + Premiere `.prproj`/`.xml`, runs Deep QC (`review_sheet.jpg`), delivers to `~/Downloads/Zeno Outs/<Name>/`, and triggers automatic desktop sound & popup notification. For `[BLOCKED]` fixes, run `<PY> scripts/edit_packet.py work/<project> --line <ID>` instead of re-reading the full packet, update `plan.edl`, and re-run `--execute`. Inspect `~/Downloads/Zeno Outs/<Name>/review_sheet.jpg` once.

### Multi-Video Queue (`2+` videos)
1. **Inspect All**: `<PY> scripts/zeno.py --queue <queue.json> --inspect-only --compact` (e.g. `work/queue.json`) to prepare every queued video's compact packet outside the main chat.
2. **Isolate 1 Subagent Per Queued Video**: Spawn **1 isolated subagent per queued video** (returning only `~/Downloads/Zeno Outs/<Name>/` + status so packets and contact sheets stay out of the main chat). Each subagent runs Read Once -> Plan -> `--execute work/<project>/plan.edl --square --compact` + `scripts/edit_packet.py work/<project> --line <ID>` repairs until delivered to `~/Downloads/Zeno Outs/<Name>/` with automatic desktop sound & popup notification. Fix any `[BLOCKED]` line before starting the next video.

## Editorial & Technical Invariants
- **Script Order & Word Bounds**: Play every spoken line once in script order with `sp="ExactSpeaker"`; guard speech `0.02-0.05s`, join turns `0.06-0.15s`, trim `! REPEAT / STUMBLE`, snap cuts to pauses, and keep scripted laughs/cries `<=1.2s`.
- **Splits & Motion**: `split_h`, `split_v`, `top_pip`, and baked splits are ALWAYS `z=1` with `14%` headroom and advancing silent listener (`peer="Name:clip.mp4"@in-out@side`); `single` pause `>0.25s` -> split + `1.08` punch-in; turns `>=2.5s` -> smooth `1.0->1.06` push; pass `focus=x,y` from the packet.
- **Transitions, Marks & Banners**: Add `+1-2` purposeful story-driven transitions/reel on real scene/location/act shifts (dialogue cuts stay crisp hard cuts; zero intra-split, same-shot, or mid-line transitions). Use script-accurate `✓`/`✗` marks with live non-frozen video visuals during post-word marks/SFX without dialogue overlap, `card:"English | Native"` for `Flashcard Text:`, and `banner:` only for Stop Saying strikethrough; zero fake `"Install Now"` CTA buttons; keep bottom `20-25%` CTA-safe; zero continuous BGM by default (`-10.5 LUFS`).
- **Subtitles & 1:1 Square**: Default `#subs auto y=65` (strict single-line subtitles only, `54px` at `1080w`; `y=50` on `split_v`), or `#page scroll` (replaces `#subs`, banners, marks, SFX). `--square` 1:1 is always a 2-layer 5–10% Gaussian blur wrap of the completed `9:16` reel (no separate 1:1 images, flashcards, timestamps, or subtitles).
- **Deliverables (`~/Downloads/Zeno Outs/<Name>/`)**: `reel_<Name>.mp4`, `SQ_<Name>.mp4`, `<Name>.prproj`, `<Name>.xml`, `review_sheet.jpg`, `<Name>.plan.edl`, `<Name>.script.txt`.
