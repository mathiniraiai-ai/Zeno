---
name: zeno
description: Sequential script-led AI video editing workflow (Zeno v2.0.0 Host-AI Direct) delivering 9:16 + 1:1 Square + Premiere outputs to ~/Downloads/Zeno Outs/<Name>/ with desktop sound & popup notification.
---

Follow `AGENTS.md` (`<PY>` = `.venv/bin/python` on macOS/Linux, `.venv\Scripts\python.exe` on Windows):
1. **Single Video**: Run `<PY> scripts/zeno.py "<footage>" --script "<script>" --name "<Name>" --inspect --compact`. Wait without polling.
2. **Multi-Video Queue (`2+` videos)**: Run `<PY> scripts/zeno.py --queue <queue.json> --inspect-only --compact`, then spawn **1 isolated subagent per queued video** (returning only `~/Downloads/Zeno Outs/<Name>/` + status) so packets and contact sheets stay out of the main chat.
3. **Plan, Repair & Deliver**: Inside the single-video context/subagent, read **only** `work/<project>/editing_packet.md` (or `editing_packet_p*.md` if chunked) + `work/<project>/clip_sheet_*.jpg` in one batch (never open `formats/*`, `scripts/*`, `facts.md`, `line_map.md`, or `clip_caches.json`). Write `work/<project>/script_analysis.md` (`Format: <id> (<title>)` on line 1) + `work/<project>/plan.edl` (+ `decisions.txt` only if needed), repair any `[BLOCKED]` line via `<PY> scripts/edit_packet.py work/<project> --line <ID>`, and run `<PY> scripts/zeno.py --execute work/<project>/plan.edl --square --compact` (delivers to `~/Downloads/Zeno Outs/<Name>/` with automatic desktop sound & popup notification; inspect `review_sheet.jpg` once).
