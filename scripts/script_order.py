#!/usr/bin/env python3
"""Measurements that let the Director (Claude) decide the edit. This file decides nothing about the edit itself.

The script is the master timeline. This module measures, from the words actually inside each cut:
- which script line each segment says, and whether the plan follows script order,
- which lines are missing, repeated or not proven,
- every clip that could supply each line (`line_map.md`, the Director's main input).

`gate()` is what `zeno.py --execute` runs: a plan with an open problem does not render. The Director resolves each problem in
`plan.edl`, or records a deliberate exception in `work/<project>/decisions.txt`:

    accept L7 script says this line is dropped for time

    python3 scripts/script_order.py work/<project>/plan.edl     # print the check; exit 2 if the plan would be blocked
"""
from __future__ import annotations

import re
import sys
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent))

WORD_MATCH = 0.64     # same threshold the planner uses for "this is the same word"
FIT_MIN = 0.40        # below this a segment is not proven to say a line
TIE = 0.08            # lines this close to the best fit are treated as equally likely (repeated wording in a script)
HEARD_MIN = 0.50      # share of a line's words that must be heard for the line to count as present
STRONG = 0.55         # evidence needed to call a segment out of order from its words alone
REPEAT_RECALL = 0.8   # two separate segments that each hold this much of a line say it twice


@lru_cache(maxsize=200000)
def _sim(a: str, b: str) -> float:
    from prepare import _word_similarity

    return _word_similarity(a, b)


def clean(words: List[str]) -> List[str]:
    from prepare import _clean_token

    return [t for t in (_clean_token(w) for w in words) if t]


def _hits(need: List[str], have: List[str]) -> float:
    """Share of `need` found in `have`."""
    if not need or not have:
        return 0.0
    pool = set(have)
    found = 0
    for t in need:
        if t in pool or max(_sim(t, h) for h in pool) >= WORD_MATCH:
            found += 1
    return found / len(need)


def fit(tokens: List[str], line_tokens: List[str]) -> float:
    """F1 of shared words: 1.0 means the segment says exactly this line."""
    rec, prec = _hits(line_tokens, tokens), _hits(tokens, line_tokens)
    return 0.0 if rec + prec == 0 else 2 * rec * prec / (rec + prec)


def recall(tokens: List[str], line_tokens: List[str]) -> float:
    return _hits(line_tokens, tokens)


def part_fit(tokens: List[str], line_tokens: List[str]) -> float:
    """Like fit(), but a piece of a long line counts: half a line split into L7a/L7b is a good match, not a weak one."""
    f = fit(tokens, line_tokens)
    if len(tokens) >= 3 and len(tokens) < 0.8 * len(line_tokens):
        return max(f, 0.95 * _hits(tokens, line_tokens))
    return f


def place_in_script(tokens: List[str], lines: List[Dict[str, Any]]) -> Tuple[Optional[int], int, float]:
    """(script line number, word offset inside it, fit) of where this text starts in the script. None if it fits nowhere.
    Uses the earliest line the text really contains, so a clip holding L2+L3 sorts at L2."""
    if not tokens:
        return None, 0, 0.0
    scored = [(fit(tokens, l.get("tokens") or []), l) for l in lines if l.get("tokens")]
    if not scored:
        return None, 0, 0.0
    best_f, best_l = max(scored, key=lambda x: x[0])
    if best_f < 0.3:
        return None, 0, best_f
    # A clip holding L2+L3 sorts at L2, but only if it really opens with L2's words. Merely sharing common words with an
    # earlier line proves nothing: a long speech contains most of the words of any short line.
    by_no = {int(l["index"]): l for l in lines if l.get("tokens")}
    start = best_l
    for back in range(1, 4):
        prev = by_no.get(int(best_l["index"]) - back)
        if prev is None:
            continue
        lt = prev["tokens"]
        if len(lt) >= 3 and recall(tokens[: len(lt) + 2], lt) >= 0.7 and fit(tokens[: len(lt) + 2], lt) >= 0.6:
            start = prev
    offset = 0
    head = tokens[: min(3, len(tokens))]
    for i, t in enumerate(start["tokens"]):
        if any(_sim(t, h) >= WORD_MATCH for h in head):
            offset = i
            break
    return int(start["index"]), offset, best_f


def _line_ids_of(seg_id: str) -> List[str]:
    """L6_L7_L8a -> [L6, L7, L8]; L14b -> [L14]; S1a or a free name -> []"""
    return re.findall(r"(?<![A-Za-z0-9])L\d+", str(seg_id or "").replace("_", " "))


def _line_id_of(seg_id: str) -> str:
    ids = _line_ids_of(seg_id)
    return ids[0] if ids else ""


def check_order(seg_rows: List[Tuple[str, List[str]]], lines: List[Dict[str, Any]]) -> Dict[str, Any]:
    """seg_rows: [(segment id, words inside the cut)] in plan order. lines: every script line, in script order."""
    pos_of = {l["id"]: i for i, l in enumerate(lines)}
    out_of_order: List[str] = []
    repeated: List[str] = []
    unknown: List[str] = []
    said_by: Dict[int, List[int]] = {}
    assigned: List[Dict[str, Any]] = []
    cur, cur_id = -1, -1
    for n, (sid, toks) in enumerate(seg_rows):
        claims = [pos_of[i] for i in _line_ids_of(sid) if i in pos_of]
        if claims:
            if min(claims) < cur_id:
                out_of_order.append(f"{sid} comes after {lines[cur_id]['id']}, but the script has it earlier")
            cur_id = max(cur_id, max(claims))
        if not toks or (claims and len(toks) <= 2):      # silent beat or a short reaction: too few words to prove anything
            assigned.append({"id": sid, "line": lines[claims[0]]["id"] if claims else None, "fit": None})
            continue
        fits = [(part_fit(toks, l.get("tokens") or []) if l.get("tokens") else 0.0) for l in lines]
        # The plan's own label wins when the words agree with it. Two script lines can share most of their words.
        agreed = [i for i in claims if fits[i] >= FIT_MIN]
        if agreed:
            for i in agreed:
                said_by.setdefault(i, []).append(n)
            cur = max(cur, max(agreed))
            assigned.append({"id": sid, "line": lines[agreed[0]]["id"], "fit": round(fits[agreed[0]], 2)})
            continue
        best = max(fits) if fits else 0.0
        cands = [i for i, f in enumerate(fits) if f >= FIT_MIN and f >= best - TIE]
        if not cands:
            unknown.append(sid)
            assigned.append({"id": sid, "line": None, "fit": round(best, 2)})
            continue
        ahead = [i for i in cands if i >= cur]
        if ahead:
            chosen = ahead[0]
            cur = chosen
        else:
            chosen = max(cands, key=lambda i: fits[i])
            if best >= STRONG and len(toks) >= 3 and not any(m.startswith(sid + " ") for m in out_of_order):
                out_of_order.append(f"{sid} says {lines[chosen]['id']}, but it comes after {lines[cur]['id']}")
        said_by.setdefault(chosen, []).append(n)
        assigned.append({"id": sid, "line": lines[chosen]["id"], "fit": round(fits[chosen], 2)})
    for i, hits in said_by.items():      # said twice = two cuts that each hold nearly the whole line, not two halves of it
        whole = [n for n in hits if recall(seg_rows[n][1], lines[i].get("tokens") or []) >= REPEAT_RECALL]
        # In a long line with common words two different halves can each touch most of it; a true repeat says the same words.
        if len(whole) >= 2 and _hits(seg_rows[whole[0]][1], seg_rows[whole[1]][1]) >= 0.7:
            repeated.append(f"{lines[i]['id']} is said {len(whole)} times ({', '.join(seg_rows[n][0] for n in whole)})")
    missing: List[str] = []
    unproven: List[str] = []
    ids_present = {i for s, _ in seg_rows for i in _line_ids_of(s)}
    for l in lines:
        lt = l.get("tokens") or []
        if not lt:
            if l["id"] not in ids_present:
                missing.append(l["id"])
            continue
        heard = 0.0
        for n in range(len(seg_rows)):
            near = seg_rows[n][1] + (seg_rows[n + 1][1] if n + 1 < len(seg_rows) else [])
            heard = max(heard, recall(near, lt))
        if heard >= HEARD_MIN:
            continue
        if l["id"] in ids_present:
            unproven.append(l["id"])
        else:
            missing.append(l["id"])
    return {"assigned": assigned, "out_of_order": out_of_order, "repeated": repeated, "missing": missing, "unproven": unproven, "unknown": unknown}


def seg_tokens(seg: Dict[str, Any]) -> List[str]:
    from common import clip_work_dir, read_json

    wdir = clip_work_dir(seg.get("src", ""))
    st, en = float(seg["start"]), float(seg["end"])
    for name in ("hq_words.json", "words.json"):
        f = wdir / name
        if f.is_file():
            try:
                ws = read_json(f)
            except Exception:
                continue
            return clean([w.get("w", "") for w in ws if float(w.get("end", 0)) > st + 0.08 and float(w.get("start", 0)) < en - 0.08])
    return []


def read_accepted(work_dir: Path) -> Dict[str, str]:
    f = Path(work_dir) / "decisions.txt"
    out: Dict[str, str] = {}
    if f.is_file():
        for ln in f.read_text(encoding="utf-8", errors="ignore").splitlines():
            m = re.match(r"\s*accept\s+(\S+)\s+(\S.*)$", ln, re.I)
            if m:
                out[m.group(1)] = m.group(2).strip()
    return out


def gate(plan: Dict[str, Any], script_path: Any, work_dir: Path) -> Dict[str, Any]:
    """Every open problem in a plan. `blocked` is what stops a render. An item is cleared by fixing the plan, or by `accept <id> <reason>`."""
    from prepare import parse_script_file

    _, lines = parse_script_file(Path(script_path))
    rows = [(str(s.get("id", "")), seg_tokens(s)) for s in plan.get("segments", [])]
    rep = check_order(rows, lines)
    ok = read_accepted(work_dir)

    def open_items(items: List[str]) -> List[str]:
        return [i for i in items if not any(re.search(rf"\b{re.escape(k)}[a-z]?\b", i) for k in ok)]

    problems = {
        "OUT OF ORDER": open_items(rep["out_of_order"]),
        "MISSING": open_items(rep["missing"]),
        "SAID TWICE": open_items(rep["repeated"]),
        "NOT PROVEN (the words in the cut do not match this line)": open_items(rep["unproven"]),
        "UNKNOWN SEGMENT (says no script line)": open_items(rep["unknown"]),
    }
    problems = {k: v for k, v in problems.items() if v}
    return {"blocked": bool(problems), "problems": problems, "accepted": ok, "report": rep}


def format_gate(g: Dict[str, Any]) -> str:
    if not g["problems"]:
        return "[ORDER PASS] The plan follows the script: every line present, once, in order" + (f" ({len(g['accepted'])} accepted exception(s))" if g["accepted"] else "")
    out = ["[ORDER BLOCKED] Decide these in plan.edl, or record a deliberate exception in decisions.txt (`accept <id> <reason>`):"]
    for k, v in g["problems"].items():
        out.append(f"  {k}: " + "; ".join(v))
    return "\n".join(out)


def _best_window(words: List[Dict[str, Any]], toks: List[str], lt: List[str]) -> Tuple[float, int, int]:
    """(recall, i, j): the stretch of a clip that says this line best."""
    n = len(toks)
    size = min(n, max(1, int(len(lt) * 1.4) + 1))
    best = (0.0, 0, max(0, size - 1))
    for i in range(0, max(1, n - size + 1)):
        r = recall(toks[i:i + size], lt)
        if r > best[0] + 1e-9:
            best = (r, i, i + size - 1)
    return best


def build_line_map(plan: Dict[str, Any], lines: List[Dict[str, Any]], clips: List[Dict[str, Any]], limit_alts: int = 2) -> str:
    """The Director's decision sheet: per script line, the cut in the draft plan and every other clip that can supply it."""
    prep = []
    for c in clips:
        ws = [w for w in (c.get("hq_words") or []) if clean([w.get("w", "")])]
        prep.append((c, ws, clean([w.get("w", "") for w in ws])))
    segs_by_line: Dict[str, List[Dict[str, Any]]] = {}
    for s in plan.get("segments", []):
        for lid in _line_ids_of(s.get("id", "")):
            segs_by_line.setdefault(lid, []).append(s)
    used = {str(s.get("src")) for s in plan.get("segments", [])}
    out = ["# LINE MAP: the script is the timeline. You decide; Zeno measured.",
           "# fit = share of the line's words heard in that stretch. DECIDE = you must choose. ALT = another clip that says this line.", ""]
    decide = 0
    for l in lines:
        lt = l.get("tokens") or []
        who = f" [{l['speaker']}]" if l.get("speaker") else ""
        head = f"{l['id']}{who} {str(l.get('text', ''))[:64]}"
        cands = []
        for c, ws, toks in prep:
            if lt and toks:
                r, i, j = _best_window(ws, toks, lt)
                if r >= 0.5:
                    bad = c.get("wrong_word_idx") or set()
                    cands.append((r, c, ws[i]["start"], ws[min(j, len(ws) - 1)]["end"], any(k in bad for k in range(i, j + 1))))
        cands.sort(key=lambda x: -x[0])
        segs = segs_by_line.get(l["id"], [])
        use_src = {str(s.get("src")) for s in segs}
        if not lt:
            out.append(f"{head}")
            if segs:
                out.append(f"  CHECK silent beat -> {Path(str(segs[0]['src'])).stem}, paired by file order only; confirm on clip_sheet")
            else:
                out.append("  DECIDE no silent clip for this beat")
                decide += 1
            continue
        out.append(head)
        if segs:
            for s in segs:
                r = next((x for x in cands if str(x[1]["src"]) == str(s.get("src"))), None)
                note = f" fit={r[0]:.2f}" if r else " fit=low"
                if r is None or r[0] < STRONG:
                    decide += 1
                    note += " DECIDE"
                if r is not None and r[4]:
                    note += " wrong-voice"
                out.append(f"  USE {Path(str(s['src'])).stem} {float(s['start']):.2f}-{float(s['end']):.2f}{note}")
        else:
            decide += 1
            out.append("  DECIDE no clip placed" if cands else "  DECIDE no clip says this line: tell the user to reshoot, or accept the gap")
        for r, c, a, b, wrong in [x for x in cands if str(x[1]["src"]) not in use_src][:limit_alts]:
            out.append(f"  ALT {c['stem']} {a:.2f}-{b:.2f} fit={r:.2f}" + (" wrong-voice" if wrong else ""))
    spare = [c for c, ws, _ in prep if str(c.get("src")) not in used and not c.get("matched_lines") and not c.get("is_listening")]
    if spare:
        out += ["", "## CLIPS MATCHED TO NO LINE (Zeno did not guess; look at them and decide)"]
        for c in spare:
            ws = c.get("hq_words") or []
            out.append(f"{c['stem']} ({float((c.get('info') or {}).get('duration') or 0):.1f}s) heard: {' '.join(str(w.get('w', '')) for w in ws[:12])}")
    out.insert(2, f"# Open decisions: {decide}")
    return "\n".join(out) + "\n"


def main() -> None:
    import edl_parser
    from common import read_json, resolve

    p = resolve(sys.argv[1])
    plan = edl_parser.load_edl(p)
    side = p.parent / "plan.json"
    sp = plan.get("script") or (read_json(side).get("script") if side.is_file() else None)
    if not sp:
        print("[ORDER] This plan names no script, so order cannot be measured.")
        sys.exit(2)
    g = gate(plan, resolve(sp), p.parent)
    for a in g["report"]["assigned"]:
        print(f"  {a['id']:<8} says {a['line'] or '?':<6} fit={a['fit']}")
    print(format_gate(g))
    sys.exit(2 if g["blocked"] else 0)


if __name__ == "__main__":
    main()
