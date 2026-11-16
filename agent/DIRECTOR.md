# Zeno Director

The agent chooses the edit. The engine measures, renders and verifies. Follow the active user's instructions and the owner's visual rules in `AGENTS.md` / `CLAUDE.md`.

1. For a new video, prefer a fresh chat with only needed connectors. Inputs are a local footage folder, one script tab and the exact Notion name. Use supplied inputs immediately.
2. Run `.venv/bin/python scripts/zeno.py --inspect <footage> --script <script> --name "<name>" --compact` (Windows `.venv\Scripts\python.exe`). Inspect runs live preflight. If inputs are missing, run `--preflight --compact` and ask for them.
3. Read `editing_packet.md` and ALL clip sheets together, once. The packet includes the brief, line map, word timings, framing and warnings. Do not reread those source files. Retrieve only the affected source evidence with `<PY> scripts/edit_packet.py <project> --line L12`. Read a requested style's `styles/<name>/SKILL.md` once; pass `--style <name>` when known.
4. Check `<PY> scripts/cost_control.py <project>` before planning or corrections. If blocked, save work and stop further model rounds. Write the per-line emotion/beat analysis and complete plan; run `--check-plan` in the same command. Review warnings once; correct actual blockers. A repeated unchanged blocker or short voice uncertainty needs targeted evidence, not another identical check. Keep full logs on disk.
5. Show the plan and obtain Yes/No once, honoring explicit prior authorization. Execute `--execute <plan> --square --compact`. Let the running command monitor itself. Use completion notifications or bounded waits supported by the host instead of repeatedly asking the model to read progress files.
6. Inspect verification and review sheets, fix issues, and deliver exports with the saved token/cost report, including requests and cost by stage. Record human feedback using `--feedback` before revisions. Development belongs in a separate chat; never debug Zeno itself in a benchmark edit.

For the optional bounded Sol API planner, use `LOW_COST_WORKFLOW.md`; it requires separate API billing and must be chosen explicitly. The fresh-chat workflow needs no API key. Python already owns the complete inspect/render/verify process and desktop progress; do not issue model calls just to poll progress files.

Open a concept only for its relevant problem: script access (01), intake (02), styles (03), reference learning (04), text (05), telemetry (06), alignment (07), preproduction (08), team learning (09). No code-development manuals during ordinary editing.
