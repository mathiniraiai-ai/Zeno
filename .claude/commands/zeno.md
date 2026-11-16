---
description: Edit one script-led video with Zeno's measured facts and verified plan.
argument-hint: "<footage> <script-tab> --name <Notion name>"
---
# /zeno

User inputs: `$ARGUMENTS`

Follow `AGENTS.md` and `agent/DIRECTOR.md`. Use the supplied inputs without asking again.

## Procedure
1. Run `<PY> scripts/zeno.py --inspect <footage> --script <script> --name "<name>" --compact`. If inputs are missing, run `--preflight --compact`, show the results, and ask for them. Never skip preflight.
2. Read `editing_packet.md` and all clip sheets once. It includes the brief and measurements. Check `scripts/cost_control.py <project>` before planning or corrections; honor a spending stop. For one line's evidence use `scripts/edit_packet.py <project> --line L12`.
3. Write `script_analysis.md`, every plan segment and necessary `decisions.txt` entries. In the same call run `--check-plan`. Fix actual blockers and review every warning.
4. Show the concrete plan and obtain Yes/No once unless already explicitly authorized.
5. Run `<PY> scripts/zeno.py --execute <plan> --square --compact`. Keep the process running through completion. Use supported completion notifications or bounded waits. Full output is saved at the printed log path; read it when warnings/failures need investigation. Never pipe to tail in a way that hides the exit status.
6. Read verification and review sheets, fix failures, and deliver the videos and Premiere files with the saved token report. README and ledger entries are automatic.

For revisions, record feedback first, then read the saved plan and only affected evidence. Reuse measured footage. Preserve all hard editorial and visual rules while reducing unnecessary reading and model calls.
