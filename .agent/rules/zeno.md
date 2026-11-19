---
trigger: always_on
---

Follow `AGENTS.md` (Zeno v2.0.0 Host-AI Direct workflow, zero MCP, zero Yes/No prompts): process videos sequentially to `~/Downloads/Zeno Outs/<Name>/` with automatic desktop sound & popup notification. Single video: `<PY> scripts/zeno.py "<footage>" --script "<script>" --name "<Name>" --inspect --compact`, read **only** `work/<project>/editing_packet.md` + `work/<project>/clip_sheet_*.jpg` once (never `formats/*`, `scripts/*`, `facts.md`, `line_map.md`, or `clip_caches.json`), write `script_analysis.md` + `plan.edl`, use `scripts/edit_packet.py work/<project> --line <ID>` for `[BLOCKED]` fixes, and run `<PY> scripts/zeno.py --execute work/<project>/plan.edl --square --compact`. Multi-video (`2+`): run `--queue <queue.json> --inspect-only --compact` and spawn **1 isolated subagent per queued video** returning only `~/Downloads/Zeno Outs/<Name>/` + status.
