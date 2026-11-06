#!/usr/bin/env python3
"""Measurements and safety checks for the Director's plan. This file decides nothing about the edit itself.

The script is the master timeline. This module measures, from the words actually inside each cut:
- which script line each segment says, and whether the plan follows script order,
- which lines are missing, repeated or not proven,
- every clip that could supply each line (`line_map.md`, the Director's main input).

`gate()` is what `zeno.py --execute` runs: a plan with an open problem does not render. The Director resolves each problem in
`plan.edl`, or records a deliberate exception in `work/<project>/decisions.txt`:

    accept L7 script says this line is dropped for time

    python3 scripts/zeno.py --check-plan work/<project>/plan.edl     # exit 2 if the plan would be blocked
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
TIGHT_JOIN_SEC = 0.25     # silence across a join (end of one cut + start of the next) above this is reported
TIGHT_PAUSE_SEC = 0.30    # a pause between two words inside one cut above this is reported
RAPID_ZOOM_STYLES = ('sudden_in', 'flash_zoom')
MAX_RAPID_PER_MIN = 16       # rapid zooms a minute of full-frame live conversation (Meta-ad tempo, owner decision 2026-11-06); splits never zoom
AD_MIN_MOVING_SHARE = 0.60   # of the live conversation's cuts, at least this share should move (a zoom, or a transition into the cut)
AD_SECONDS_PER_TRANSITION = 14.0  # aim for a crisp transition about this often in the live conversation
MIN_STILL_SHARE = 0.25       # at least this share of single-speaker cuts should not move, so a zoom means something
SNAP_REACH_SEC = 0.12   # a cut may move at most this far to reach a quiet point
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
        carried = [t for sid, toks in seg_rows if l["id"] in _line_ids_of(sid) for t in toks]   # a long line split over several cuts (L6a, L6b, ...)
        heard = max(heard, recall(carried, lt))
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


def unread_plan_lines(plan: Dict[str, Any], plan_path: Path) -> List[str]:
    """Lines of plan.edl that look like segments but were not read. The usual cause is a clip name with spaces that is not in quotes:
    the parser then drops the whole line without a word, and the order check would blame the wrong lines."""
    ids = {str(s.get("id")) for s in plan.get("segments", [])}
    out: List[str] = []
    try:
        text = Path(plan_path).read_text(encoding="utf-8")
    except OSError:
        return out
    for n, line in enumerate(text.splitlines(), 1):
        raw = line.strip()
        if not raw or raw.startswith("#"):
            continue
        first = raw.split()[0].strip('"')
        if first not in ids:
            out.append(f"line {n} ('{raw[:46]}...') was not read: put clip names that contain spaces in quotes, e.g. \"MISS NOVA FV.mp4\"")
    return out


def full_gate(plan: Dict[str, Any], script_path: Any, work_dir: Path, plan_path: Optional[Path] = None) -> Dict[str, Any]:
    """Order, coverage, cuts and voices. Used by `--check-plan` and before every render."""
    g = gate(plan, script_path, work_dir)
    ok = g["accepted"]

    def still_open(items: List[str]) -> List[str]:
        return [i for i in items if i.split()[0].rstrip("abcdefgh") not in ok and i.split()[0] not in ok]

    if not plan.get("segments"):
        g["problems"] = {"EMPTY PLAN": ["no segments yet"]}
    unread = unread_plan_lines(plan, plan_path) if plan_path else []
    if unread:
        g["problems"] = {"PLAN LINES NOT READ (fix these first; the other messages may be a result of them)": unread, **g["problems"]}
    cuts = still_open(cut_problems(plan))
    if cuts:
        g["problems"]["CUT INSIDE A WORD"] = cuts
    try:
        voices, note = voice_problems(plan)
    except Exception as exc:   # noqa: BLE001
        voices, note = [], f"voices not checked: {exc}"
    voices = still_open(voices)
    if voices:
        g["problems"]["WRONG VOICE"] = voices
    g["voice_note"] = note
    try:
        g["tighten"] = tight_report(plan)
    except Exception:       # advice only
        g["tighten"] = []
    try:
        g["style"] = style_report(plan, work_dir)
    except Exception:
        g["style"] = []
    g["blocked"] = bool(g["problems"])
    return g


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


def build_line_map(lines: List[Dict[str, Any]], clips: List[Dict[str, Any]], limit: int = 4, style: str = "") -> str:
    """The script in full, and under each line every clip stretch that says it. A measurement, not a choice."""
    prep = []
    for c in clips:
        ws = [w for w in (c.get("hq_words") or []) if clean([w.get("w", "")])]
        prep.append((c, ws, clean([w.get("w", "") for w in ws])))
    named: set = set()
    out = ["# LINE MAP: the script is the timeline. CAND = a clip stretch that says this line; `heard` = share of the line's words found there.",
           "# Zeno chose nothing. Pick from these, check the words in facts.md, and write plan.edl."]
    if style:
        import brain_memory

        rules = brain_memory.format_brain_memory_for_brief(style)
        if rules:
            out += [""] + list(rules)
    out.append("")
    for l in lines:
        tags = [f"{k}={l[k]}" for k in ("scene", "mark", "layout_hint", "strike_text", "note") if l.get(k)]
        if l.get("panels"):
            tags.append(f"panels={l['panels']}")
        who = f" [{l['speaker']}]" if l.get("speaker") else ""
        out.append(f"{l['id']}{who} {l.get('text', '')}" + (f"  ({'; '.join(tags)})" if tags else ""))
        lt = l.get("tokens") or []
        if not lt:
            out.append("  silent beat: choose a silent clip from the clip sheet")
            continue
        cands = []
        for c, ws, toks in prep:
            if toks:
                r, i, j = _best_window(ws, toks, lt)
                if r >= 0.5:
                    cands.append((r, Path(str(c["src"])).name, ws[i]["start"], ws[min(j, len(ws) - 1)]["end"], c))
        cands.sort(key=lambda x: -x[0])
        for r, name, a, b, c in cands:
            named.add(id(c))
        for r, name, a, b, _ in cands[:limit]:
            out.append(f"  CAND {name} {float(a):.2f}-{float(b):.2f} heard={r:.2f}")
        if len(cands) > limit:
            out.append(f"  +{len(cands) - limit} weaker")
        if not cands:
            out.append("  NO CLIP says this line (check facts.md, then tell the user to reshoot or accept the gap)")
    spare = [(c, ws) for c, ws, _ in prep if id(c) not in named and ws and not c.get("is_listening")]
    if spare:
        out += ["", "## SPEECH THAT FITS NO LINE (slate, ad-lib or a bad transcript: look at it)"]
        for c, ws in spare:
            out.append(f"{Path(str(c['src'])).name}: {' '.join(str(w.get('w', '')) for w in ws[:14])}")
    return "\n".join(out) + "\n"


def voice_problems(plan: Dict[str, Any]) -> Tuple[List[str], str]:
    """Claude cannot hear, so voices are measured here: does the voice in each cut match the speaker the plan names?
    Returns (problems, note). The note says when voices could not be told apart."""
    import speaker_check
    from common import clip_work_dir, read_json
    from prepare import compute_10ms_rms_envelope

    items, ids = [], {}
    for n, s in enumerate(plan.get("segments", [])):
        who = str(s.get("speaker") or "")
        wdir = clip_work_dir(s.get("src", ""))
        f = _word_file(s.get("src", ""))
        wav = wdir / "audio16k.wav"
        if not who or " / " in who or f is None or not wav.is_file():
            continue
        st, en = float(s["start"]), float(s["end"])
        ws = [w for w in read_json(f) if float(w.get("end", 0)) > st + 0.08 and float(w.get("start", 0)) < en - 0.08]
        if ws:
            ids[n] = str(s.get("id"))
            items.append({"id": n, "wav": wav, "words": ws, "expected": [who] * len(ws), "rms_env": compute_10ms_rms_envelope(wav)})
    if len({i["expected"][0] for i in items}) < 2:
        return [], ""
    res = speaker_check.analyze(items)
    if not res.get("ok"):
        return [], f"voices not checked: {res.get('reason')}"
    by_id = {i["id"]: i for i in items}
    src_of = {n: str(plan["segments"][n].get("src")) for n in ids}
    disagree = {n for n, info in res.get("clips", {}).items() if info.get("spans")}
    out, doubts = [], []
    for n, info in res.get("clips", {}).items():
        for sp in info.get("spans", []):
            ws = by_id[n]["words"][sp["first"]: sp["last"] + 1]
            span_sec = (float(ws[-1]["end"]) - float(ws[0]["start"])) if ws else 0.0
            same_file = [m for m in ids if m != n and src_of[m] == src_of[n]]
            msg = f"{ids.get(n, n)} is labelled {sp.get('expected')}, but the voice is {sp.get('heard', 'someone else')}"
            # A very short span is too little to tell voices apart. If every other turn from the same file agrees with its label,
            # it is far more likely a miss of the measurement than a wrong person: say so, but do not block.
            if span_sec < 2.5 and same_file and not any(m in disagree for m in same_file):
                doubts.append(f"{msg} (only {span_sec:.1f}s of speech; the file's other {len(same_file)} turn(s) match: check by ear)")
            else:
                out.append(msg)
    return out, ("voice doubt: " + "; ".join(doubts)) if doubts else ""


def _word_file(src: Any) -> Optional[Path]:
    """The cached aligned words of a clip. The cache stores them as words.json (hq_words.json in older caches)."""
    from common import clip_work_dir

    wdir = clip_work_dir(src)
    for name in ("hq_words.json", "words.json"):
        if (wdir / name).is_file():
            return wdir / name
    return None


def _words_of(seg: Dict[str, Any]) -> List[Dict[str, Any]]:
    from common import read_json

    f = _word_file(seg.get("src", ""))
    return read_json(f) if f else []


def _env_of(seg: Dict[str, Any]) -> List[Tuple[float, float]]:
    from common import clip_work_dir
    from prepare import compute_10ms_rms_envelope

    wav = clip_work_dir(seg.get("src", "")) / "audio16k.wav"
    return compute_10ms_rms_envelope(wav) if wav.is_file() else []


def _speech_spans_for(seg: Dict[str, Any]) -> List[Tuple[float, float]]:
    from common import clip_work_dir, read_json

    f = clip_work_dir(seg.get("src", "")) / "speech_spans.json"
    try:
        return [tuple(x) for x in read_json(f).get("spans", [])] if f.is_file() else []
    except Exception:
        return []


def cut_problems(plan: Dict[str, Any]) -> List[str]:
    """Cuts that land inside a spoken word. A word counts only where there is real sound: an aligner time that sits in silence is wrong."""
    out = []
    for s in plan.get("segments", []):
        env = None
        spoken = _speech_spans_for(s)
        for w in _words_of(s):
            a, b = float(w["start"]), float(w["end"])
            for name, t in (("start", float(s["start"])), ("end", float(s["end"]))):
                if a + 0.05 < t < b - 0.05:
                    if spoken and not any(x0 - 0.03 <= t <= x1 + 0.03 for x0, x1 in spoken):
                        continue      # the voice detector hears no speech at this moment: a word the transcript put here is a ghost
                    if env is None:
                        env = _env_of(s)
                    loud = max((db for tt, db in env if t - 0.03 <= tt <= t + 0.03), default=-140.0)
                    if env and loud < -50.0:
                        continue      # the cut lands in silence: no sound is cut
                    out.append(f"{s.get('id')} {name} {t:.2f} is inside the word '{w.get('w')}' ({a:.2f}-{b:.2f})")
    return out


def tight_report(plan: Dict[str, Any]) -> List[str]:
    """Where the edit drags: silence at joins and pauses inside cuts, from the aligned words. Advice only; it never blocks a render."""
    out: List[str] = []
    segs = plan.get("segments", [])
    mark_end: Dict[str, float] = {}
    for m in plan.get("marks", []):
        mark_end[str(m.get("segment"))] = max(mark_end.get(str(m.get("segment")), 0.0), float(m.get("at", 0)) + float(m.get("duration", 0.56)))
    rows = []
    for s in segs:
        st, en = float(s["start"]), float(s["end"])
        ws = [w for w in _words_of(s) if float(w["end"]) > st + 0.05 and float(w["start"]) < en - 0.05]
        rows.append((s, st, en, ws))
    for k, (s, st, en, ws) in enumerate(rows):
        if not ws:
            continue
        sid = str(s.get("id"))
        lead = max(0.0, float(ws[0]["start"]) - st)
        tail = max(0.0, en - float(ws[-1]["end"]))
        # When a segment has a wrong/right mark at the end of the sentence, allow up to ~1.0s post-word tail for the flashy red/green gradient + SFX before the next cut
        tail_free = max(0.0, en - max(float(ws[-1]["end"]), st + mark_end.get(sid, 0.0))) if sid in mark_end else tail
        nxt = rows[k + 1] if k + 1 < len(rows) else None
        if nxt and nxt[3]:
            nxt_lead = max(0.0, float(nxt[3][0]["start"]) - nxt[1])
            if tail_free + nxt_lead > TIGHT_JOIN_SEC:
                out.append(f"{sid} -> {nxt[0].get('id')}: {tail_free + nxt_lead:.2f}s of silence at the join (tail {tail_free:.2f}s + lead {nxt_lead:.2f}s): "
                           f"end {sid} at {en - max(0.0, tail_free - 0.08):.2f} or start {nxt[0].get('id')} later")
        for w0, w1 in zip(ws, ws[1:]):
            gap = float(w1["start"]) - float(w0["end"])
            if gap > TIGHT_PAUSE_SEC:
                out.append(f"{sid}: {gap:.2f}s pause between '{w0.get('w')}' and '{w1.get('w')}' at {float(w0['end']):.2f}-{float(w1['start']):.2f}: cut it (split the cut) or accept it as a breath")
        if k == 0 and lead > 0.25:
            out.append(f"{sid}: starts {lead:.2f}s before the first word")
    return out


def style_report(plan: Dict[str, Any], work_dir: Optional[Path] = None) -> List[str]:
    """Advice on the emotion plan: whether the script was analysed, whether zooms and effects follow the rules. Never blocks a render."""
    out: List[str] = []
    segs = plan.get("segments", [])
    if work_dir is not None and not (Path(work_dir) / "script_analysis.md").is_file():
        out.append("no script_analysis.md: analyse the script first (emotion, beat, zoom and effect per line) and save it in the work folder; it is how the zooms and effects are planned")
    from common import resolve_sfx_asset

    fps = float((plan.get("canvas") or {}).get("fps") or 30)
    total = sum(float(s["end"]) - float(s["start"]) for s in segs) or 1.0
    rapid, flash, singles, still = [], 0, 0, 0
    prev_rapid = False
    live_total, live_cuts, live_moving, live_trans = 0.0, 0, 0, 0
    for s in segs:
        lay = str(s.get("layout") or "single").lower()
        style = str(s.get("zoom_style") or "")
        z0, z1 = float(s.get("zoom") or 1.0), float(s.get("zoom_to") or s.get("zoom") or 1.0)
        moves = style in RAPID_ZOOM_STYLES or style == "sudden_out" or abs(z1 - z0) > 1e-6          # animated zoom; a fixed crop stands still
        dur = float(s["end"]) - float(s["start"])
        if lay in ("split_h", "split_v", "top_pip") and moves:
            out.append(f"{s.get('id')}: zoom on a {lay} cut is ignored (splits stay still)")
        if lay == "single":
            live_total += dur
            has_tr = bool(s.get("transition")) and str(s.get("transition")) != "cut"
            live_trans += 1 if has_tr else 0
            if dur >= 1.0:
                live_cuts += 1
                live_moving += 1 if (moves or has_tr) else 0
            if dur >= 1.5:
                singles += 1
                still += 0 if moves else 1
            is_rapid = style in RAPID_ZOOM_STYLES
            if is_rapid and prev_rapid:
                out.append(f"{s.get('id')}: a rapid zoom straight after another rapid zoom; make one of them slow or none")
            prev_rapid = is_rapid
            if is_rapid:
                rapid.append(s)
            if style == "flash_zoom":
                flash += 1
        else:
            prev_rapid = False
    if flash > 1:
        out.append(f"{flash} flash zooms: keep it to the single biggest beat of the video")
    per_min = len(rapid) / (max(live_total, 1.0) / 60.0)
    if per_min > MAX_RAPID_PER_MIN:
        out.append(f"{len(rapid)} rapid zooms in {live_total:.0f}s of live conversation ({per_min:.1f} a minute; starting limit {MAX_RAPID_PER_MIN}): thin them out so the peaks still stand out")
    if live_total >= 25.0 and live_cuts >= 5:
        if live_moving / live_cuts < AD_MIN_MOVING_SHARE:
            out.append(f"only {live_moving} of {live_cuts} live cuts move (a zoom or a transition): this is for a Meta ad, so aim for at least {int(AD_MIN_MOVING_SHARE * 100)}% (rapid zoom in on a stressed word, slow push or pull on longer cuts, a zoom out on release)")
        want = int(live_total // AD_SECONDS_PER_TRANSITION)
        if live_trans < want:
            out.append(f"{live_trans} transition(s) in {live_total:.0f}s of live conversation: for a Meta ad aim for about {want} (whip_left/whip_right across clip changes, slide_up, zoom_burst on a reveal)")
    if singles >= 4 and still / singles < MIN_STILL_SHARE:
        out.append(f"only {still} of {singles} single-speaker cuts stand still (aim for at least {int(MIN_STILL_SHARE * 100)}%): stillness is what gives a zoom its weight")
    ids = {str(s.get("id")): s for s in segs}
    for e in plan.get("sfx", []):
        name, seg = str(e.get("file")), ids.get(str(e.get("segment")))
        try:
            known = resolve_sfx_asset(name).is_file()
        except Exception:
            known = False
        if not known:
            out.append(f"sound '{name}' on {e.get('segment')} is not in assets/sfx: it will not play")
        if float(e.get("gain_db", -8)) > 0:
            out.append(f"sound '{name}' on {e.get('segment')} is boosted above 0 dB: keep effects under the voice")
        if seg is not None and float(e.get("at", 0)) > float(seg["end"]) - float(seg["start"]):
            out.append(f"sound '{name}' on {e.get('segment')} starts after the cut ends")
    return out


def snap_cuts(plan: Dict[str, Any]) -> int:
    """Mechanical: a cut in the gap between two words moves to the quietest 10 ms of that gap, so it cannot click. Keeps the Director's words."""
    from common import clip_work_dir
    from prepare import compute_10ms_rms_envelope, find_rms_minimum

    moved = 0
    for s in plan.get("segments", []):
        ws = _words_of(s)
        wav = clip_work_dir(s.get("src", "")) / "audio16k.wav"
        if len(ws) < 2 or not wav.is_file():
            continue
        env = compute_10ms_rms_envelope(wav)
        for key in ("start", "end"):
            t = float(s[key])
            for w0, w1 in zip(ws, ws[1:]):
                a, b = float(w0["end"]), float(w1["start"])
                if a <= t <= b and b - a >= 0.03:
                    # only a small nudge: in a long gap (or digital silence) the quietest point can be seconds away, which adds dead air
                    lo, hi = max(a, t - SNAP_REACH_SEC), min(b, t + SNAP_REACH_SEC)
                    new = round(float(find_rms_minimum(env, lo, hi)[0]), 2)
                    if new != round(t, 2):
                        s[key] = new
                        moved += 1
                    break
    return moved


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
    g = full_gate(plan, resolve(sp), p.parent, p)
    for a in g["report"]["assigned"]:
        print(f"  {a['id']:<8} says {a['line'] or '?':<6} fit={a['fit']}")
    print(format_gate(g))
    if g.get("voice_note"):
        print(f"[VOICE] {g['voice_note']}. Say so in the report.")
    sys.exit(2 if g["blocked"] else 0)


if __name__ == "__main__":
    main()
