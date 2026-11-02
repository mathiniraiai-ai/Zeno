"""Compact 1-Line-Per-Segment Edit Decision List (`plan.edl`) Parser & Formatter (`edl_parser.py`).

Converts losslessly between the ultra-compact `plan.edl` DSL (~12x fewer tokens than JSON)
and the full `plan.json` dictionary used by `render.py`.

Supported Header Directives:
    #output output/my_video.mp4
    #src_dir input/footage
    #canvas 720x1280@24 loudness=-14 preset=hybrid mark_pack=default
    #speakers nova="Miss Nova" ravi="Ravi"

Supported Segment Line Tokens:
    <id> <src> <start>-<end> [layout=...] [z=1.08 | z=1.0->1.06] [focus=0.5,0.4]
    [sp=nova] [slot=top] [tr=fade@0.3] [peer=S2.mp4@1.20]
    [mark:wrong@4.35] [chat:ravi@"I hunger"@0.0] [sfx:whoosh.wav@0.0] [# note text]
"""
from __future__ import annotations

import argparse
import json
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

from common import read_json, resolve, write_json


def _quote_if_needed(val: str) -> str:
    s = str(val or "")
    if not s or any(ch in s for ch in (' ', '\t', '"', '#', '@', '=')):
        escaped = s.replace("\\", "\\\\").replace('"', '\\"')
        return f'"{escaped}"'
    return s


def _unquote(val: str) -> str:
    s = str(val or "").strip()
    if len(s) >= 2 and s[0] == s[-1] and s[0] in ('"', "'"):
        inner = s[1:-1]
        return inner.replace('\\"', '"').replace("\\'", "'").replace("\\\\", "\\")
    return s


def _split_line_and_note(line: str) -> Tuple[str, Optional[str]]:
    """Split a segment line into `(tokens_part, comment_note)` respecting quoted strings."""
    in_quote: Optional[str] = None
    escape = False
    for idx, ch in enumerate(line):
        if escape:
            escape = False
            continue
        if ch == "\\":
            escape = True
            continue
        if in_quote:
            if ch == in_quote:
                in_quote = None
        else:
            if ch in ('"', "'"):
                in_quote = ch
            elif ch == "#":
                body = line[:idx].strip()
                note = line[idx + 1 :].strip()
                return body, note
    return line.strip(), None


def _tokenize_line(body: str) -> List[str]:
    """Split `body` on whitespace while keeping quoted substrings intact inside tokens."""
    tokens: List[str] = []
    cur: List[str] = []
    in_quote: Optional[str] = None
    escape = False

    for ch in body:
        if escape:
            cur.append("\\")
            cur.append(ch)
            escape = False
            continue
        if ch == "\\":
            escape = True
            continue
        if in_quote:
            cur.append(ch)
            if ch == in_quote:
                in_quote = None
        else:
            if ch in ('"', "'"):
                in_quote = ch
                cur.append(ch)
            elif ch.isspace():
                if cur:
                    tokens.append("".join(cur))
                    cur = []
            else:
                cur.append(ch)
    if cur:
        tokens.append("".join(cur))
    return tokens


def _split_top_level(s: str, sep: str = "@") -> List[str]:
    """Split `s` by `sep` outside of quotes."""
    parts: List[str] = []
    cur: List[str] = []
    in_quote: Optional[str] = None
    escape = False
    for ch in s:
        if escape:
            cur.append(ch)
            escape = False
            continue
        if ch == "\\":
            escape = True
            cur.append(ch)
            continue
        if in_quote:
            cur.append(ch)
            if ch == in_quote:
                in_quote = None
        else:
            if ch in ('"', "'"):
                in_quote = ch
                cur.append(ch)
            elif ch == sep:
                parts.append("".join(cur))
                cur = []
            else:
                cur.append(ch)
    parts.append("".join(cur))
    return parts


def _join_src_dir(src_dir: str, clip_ref: str) -> str:
    c = _unquote(clip_ref)
    if not src_dir:
        return c
    if "/" in c or "\\" in c or re.match(r"^[A-Za-z]:", c):
        return c
    base = src_dir.rstrip("/\\")
    return f"{base}/{c}"


def _fmt_num(v: float) -> str:
    if v is None:
        return "0"
    r = round(float(v), 4)
    if abs(r - round(r, 2)) < 1e-6:
        s = f"{r:.2f}".rstrip("0").rstrip(".")
        return s if "." in s else f"{int(round(r))}.0" if False else s
    return f"{r:.4f}".rstrip("0").rstrip(".")


def _fmt_time(v: float) -> str:
    r = round(float(v), 4)
    if abs(r - round(r, 2)) < 1e-5:
        return f"{r:.2f}"
    return f"{r:.3f}".rstrip("0").rstrip(".")


def parse_edl(path_or_text: Union[str, Path]) -> Dict[str, Any]:
    """Parse a `.edl` file path or raw EDL string into a full `plan.json` dictionary."""
    raw_text: Optional[str] = None
    if isinstance(path_or_text, Path):
        raw_text = path_or_text.read_text(encoding="utf-8")
    elif isinstance(path_or_text, str):
        if "\n" not in path_or_text and len(path_or_text) < 260:
            p = Path(path_or_text)
            if p.is_file():
                raw_text = p.read_text(encoding="utf-8")
            else:
                rp = resolve(path_or_text)
                if rp.is_file():
                    raw_text = rp.read_text(encoding="utf-8")
        if raw_text is None:
            raw_text = path_or_text
    else:
        raise TypeError(f"Expected str or Path, got {type(path_or_text)}")

    plan: Dict[str, Any] = {}
    canvas: Dict[str, Any] = {}
    audio: Dict[str, Any] = {}
    speakers: Dict[str, Any] = {}
    segments: List[Dict[str, Any]] = []
    marks: List[Dict[str, Any]] = []
    sfx_list: List[Dict[str, Any]] = []
    src_dir: str = ""

    for raw_line in raw_text.splitlines():
        line = raw_line.strip()
        if not line:
            continue

        # 1. Check Header Directives
        if line.startswith("#"):
            m_dir = re.match(r"^#\s*(output|script|src_dir|canvas|speakers|audio|preset|mark_pack|sfx_pack|concept_brief|characters|scene_\d+)\b\s*:?\s*(.*)$", line, re.I)
            if not m_dir:
                continue
            directive = m_dir.group(1).lower()
            rest = m_dir.group(2).strip()

            if directive == "output":
                plan["output"] = _unquote(rest.split("|")[0].strip())
            elif directive == "script":
                plan["script"] = _unquote(rest.split("|")[0].strip())
            elif directive == "src_dir":
                src_dir = _unquote(rest.split("|")[0].strip())
            elif directive in ("concept_brief", "characters") or directive.startswith("scene_"):
                plan[directive] = rest
            elif directive in ("preset", "mark_pack", "sfx_pack"):
                for part in rest.split("|"):
                    m_sub = re.match(r"^(preset|mark_pack|sfx_pack)\b\s*:?\s*(.*)$", part.strip(), re.I)
                    if m_sub:
                        plan[m_sub.group(1).lower()] = _unquote(m_sub.group(2).strip())
                    elif part.strip() == rest.split("|")[0].strip():
                        plan[directive] = _unquote(part.strip())
            elif directive == "canvas":
                for tok in _tokenize_line(rest):
                    m_res = re.match(r"^(\d+)x(\d+)(?:@([\d.]+))?$", tok, re.I)
                    if m_res:
                        canvas["width"] = int(m_res.group(1))
                        canvas["height"] = int(m_res.group(2))
                        if m_res.group(3):
                            fv = float(m_res.group(3))
                            canvas["fps"] = int(fv) if fv.is_integer() else fv
                        continue
                    if "=" in tok:
                        k, v = tok.split("=", 1)
                        k = k.strip().lower()
                        v = _unquote(v.strip())
                        if k == "loudness":
                            fv = float(v)
                            audio["loudness"] = int(fv) if fv.is_integer() else fv
                        elif k == "fps":
                            fv = float(v)
                            canvas["fps"] = int(fv) if fv.is_integer() else fv
                        elif k in ("width", "w"):
                            canvas["width"] = int(v)
                        elif k in ("height", "h"):
                            canvas["height"] = int(v)
                        elif k in ("preset", "mark_pack", "sfx_pack"):
                            plan[k] = v
                        else:
                            canvas[k] = v
            elif directive == "audio":
                for tok in _tokenize_line(rest):
                    if "=" in tok:
                        k, v = tok.split("=", 1)
                        k, v = k.strip().lower(), _unquote(v.strip())
                        if k == "loudness":
                            fv = float(v)
                            audio["loudness"] = int(fv) if fv.is_integer() else fv
                        else:
                            audio[k] = v
            elif directive == "speakers":
                if rest.startswith("{"):
                    try:
                        speakers.update(json.loads(rest))
                    except Exception:
                        pass
                else:
                    for tok in _tokenize_line(rest):
                        if "=" in tok:
                            k, v = tok.split("=", 1)
                            speakers[k.strip()] = _unquote(v.strip())
            continue

        # 1b. Standalone SFX line: `sfx whoosh.wav seg=L5 at=0.0 gain=-8` or `whoosh.wav | L5 | 0.0 | -8`
        if line.lower().startswith("sfx ") and "|" not in line:
            stoks = _tokenize_line(line)
            if len(stoks) >= 2:
                s_entry: Dict[str, Any] = {"file": _unquote(stoks[1]), "segment": "", "at": 0.0}
                for st in stoks[2:]:
                    if "=" in st:
                        sk, sv = st.split("=", 1)
                        sk, sv = sk.strip().lower(), _unquote(sv.strip())
                        if sk in ("seg", "segment"):
                            s_entry["segment"] = sv
                        elif sk == "at":
                            s_entry["at"] = round(float(sv), 4)
                        elif sk in ("gain", "gain_db"):
                            gv = float(sv.rstrip("dB"))
                            s_entry["gain_db"] = int(gv) if gv.is_integer() else gv
                if s_entry["segment"]:
                    sfx_list.append(s_entry)
            continue

        # 2. Segment Line
        body, note = _split_line_and_note(line)
        if not body:
            continue
        toks = _tokenize_line(body)
        if len(toks) < 3:
            continue

        seg_id = toks[0]
        src_val = _join_src_dir(src_dir, toks[1])
        time_tok = toks[2]
        m_time = re.match(r"^([\d.]+)\s*(?:-|\.\.)\s*([\d.]+)$", time_tok)
        if not m_time:
            continue
        start_sec = float(m_time.group(1))
        end_sec = float(m_time.group(2))

        seg: Dict[str, Any] = {
            "id": seg_id,
            "src": src_val,
            "start": round(start_sec, 4),
            "end": round(end_sec, 4),
        }
        if note is not None and note != "":
            seg["note"] = note

        chat_msgs: List[Dict[str, Any]] = []
        chat_mode: Optional[str] = None
        banners: List[Dict[str, Any]] = []

        for tok in toks[3:]:
            # Key=Value tokens
            if "=" in tok and not tok.lower().startswith(("mark:", "chat:", "sfx:", "banner:", "stop:")):
                k, v = tok.split("=", 1)
                k = k.strip().lower()
                v_raw = v.strip()
                v_unq = _unquote(v_raw)

                if k == "layout":
                    if ":" in v_unq:
                        lay_part, sub_mode = v_unq.split(":", 1)
                        seg["layout"] = lay_part.strip()
                        if sub_mode.strip().lower() in ("docked", "floating_overlay", "floating"):
                            chat_mode = "docked" if "dock" in sub_mode.lower() else "floating_overlay"
                    else:
                        seg["layout"] = v_unq
                elif k in ("z", "zoom"):
                    raw_z = v_unq.strip()
                    at_part = None
                    if "@" in raw_z:
                        raw_z, at_part = raw_z.split("@", 1)
                    style_part = None
                    low_z = raw_z.lower()
                    for st_name in ("sudden_in", "punch_in", "sudden_out", "punch_out", "flash_zoom", "smooth", "mild_in", "mild_out"):
                        if low_z == st_name or low_z.startswith(st_name + ":") or low_z.endswith(":" + st_name):
                            if st_name in ("punch_in", "sudden_in"):
                                style_part = "sudden_in"
                            elif st_name in ("punch_out", "sudden_out"):
                                style_part = "sudden_out"
                            elif st_name in ("mild_in", "mild_out"):
                                style_part = "smooth"
                            else:
                                style_part = st_name
                            raw_z = re.sub(rf"(^|:){re.escape(st_name)}(:|$)", "", raw_z, flags=re.I).strip(":")
                            break
                    if style_part:
                        seg["zoom_style"] = style_part
                        if not raw_z:
                            if style_part == "sudden_out":
                                seg["zoom"], seg["zoom_to"] = 1.10, 1.0
                                seg.setdefault("zoom_dur", 0.20)
                            elif style_part == "flash_zoom":
                                seg["zoom"], seg["zoom_to"] = 1.0, 1.12
                                seg.setdefault("zoom_dur", 0.16)
                            else:
                                seg["zoom"], seg["zoom_to"] = 1.0, 1.10
                                seg.setdefault("zoom_dur", 0.18)
                    if raw_z:
                        if "->" in raw_z:
                            z0_s, z1_s = raw_z.split("->", 1)
                            if z0_s.strip():
                                z0_v = float(z0_s.strip())
                                if not (abs(z0_v - 1.0) < 1e-6 and seg.get("zoom") is None and z0_s.strip() == "1.0" and seg.get("_explicit_z0") is False):
                                    seg["zoom"] = round(z0_v, 4)
                            if z1_s.strip():
                                seg["zoom_to"] = round(float(z1_s.strip()), 4)
                        else:
                            val = round(float(raw_z), 4)
                            if style_part in ("sudden_in", "flash_zoom"):
                                seg["zoom"] = 1.0
                                seg["zoom_to"] = val
                            elif style_part == "sudden_out":
                                seg["zoom"] = val
                                seg["zoom_to"] = 1.0
                            else:
                                seg["zoom"] = val
                    if at_part:
                        if "/" in at_part:
                            at_s, dur_s = at_part.split("/", 1)
                            if at_s.strip():
                                seg["zoom_at"] = round(float(at_s.strip()), 4)
                            if dur_s.strip():
                                seg["zoom_dur"] = round(float(dur_s.strip()), 4)
                        else:
                            seg["zoom_at"] = round(float(at_part.strip()), 4)
                        if "zoom_style" not in seg:
                            z0_c = float(seg.get("zoom", 1.0) or 1.0)
                            z1_c = float(seg.get("zoom_to", z0_c) or z0_c)
                            seg["zoom_style"] = "sudden_out" if z1_c < z0_c else "sudden_in"
                            seg.setdefault("zoom_dur", 0.18)
                elif k == "zoom_to":
                    seg["zoom_to"] = round(float(v_unq), 4)
                elif k == "zoom_at":
                    seg["zoom_at"] = round(float(v_unq), 4)
                elif k in ("zoom_dur", "zoom_duration"):
                    seg["zoom_dur"] = round(float(v_unq), 4)
                elif k in ("zoom_style", "z_style"):
                    seg["zoom_style"] = v_unq.strip().lower()
                elif k == "focus":
                    if "->" in v_unq:
                        f_left, f_right = v_unq.split("->", 1)
                        p0 = [float(x) for x in f_left.strip("[]()").split(",") if x.strip()]
                        p1 = [float(x) for x in f_right.strip("[]()").split(",") if x.strip()]
                        if len(p0) >= 2:
                            seg["focus"] = [round(p0[0], 4), round(p0[1], 4)]
                        if len(p1) >= 2:
                            seg["focus_end"] = [round(p1[0], 4), round(p1[1], 4)]
                    else:
                        parts = [float(x) for x in v_unq.strip("[]()").split(",") if x.strip()]
                        if len(parts) >= 2:
                            seg["focus"] = [round(parts[0], 4), round(parts[1], 4)]
                elif k == "focus_end":
                    parts = [float(x) for x in v_unq.strip("[]()").split(",") if x.strip()]
                    if len(parts) >= 2:
                        seg["focus_end"] = [round(parts[0], 4), round(parts[1], 4)]
                elif k in ("sp", "speaker"):
                    seg["speaker"] = v_unq.replace("_", " ")
                elif k == "scene":
                    seg["scene"] = v_unq.replace("_", " ")
                elif k in ("shot", "angle"):
                    seg["shot"] = v_unq.replace("_", " ")
                elif k == "slot":
                    seg["slot"] = v_unq
                elif k in ("tr", "trans", "transition"):
                    sep_ch = "@" if "@" in v_unq else (":" if ":" in v_unq else "@")
                    tr_parts = _split_top_level(v_unq, sep_ch)
                    seg["transition"] = tr_parts[0]
                    if len(tr_parts) > 1 and tr_parts[1]:
                        seg["transition_duration"] = round(float(tr_parts[1]), 4)
                elif k == "peer":
                    peer_dict: Dict[str, Any] = {}
                    p_parts = _split_top_level(v_unq, "@")
                    first_p = _unquote(p_parts[0])
                    if ":" in first_p and not re.match(r"^[A-Za-z]:[/\\]", first_p):
                        psp, psrc = first_p.split(":", 1)
                        peer_dict["speaker"] = psp.strip()
                        peer_dict["src"] = _join_src_dir(src_dir, psrc.strip())
                    else:
                        peer_dict["src"] = _join_src_dir(src_dir, first_p)
                    for extra in p_parts[1:]:
                        ex = _unquote(extra)
                        if re.match(r"^[\d.]+-[\d.]+$", ex):
                            s0, s1 = ex.split("-", 1)
                            peer_dict["idle_span"] = [round(float(s0), 4), round(float(s1), 4)]
                            peer_dict.setdefault("mode", "loop")
                        elif re.match(r"^[\d.]+,[\d.]+$", ex):
                            fx_s, fy_s = ex.split(",", 1)
                            peer_dict["focus"] = [round(float(fx_s), 4), round(float(fy_s), 4)]
                        elif ex.lower() in ("top", "bottom", "left", "right"):
                            peer_dict["slot"] = ex.lower()
                        else:
                            try:
                                peer_dict["still_at"] = round(float(ex), 4)
                            except ValueError:
                                pass
                    seg["peer"] = peer_dict
                elif k in ("vol", "volume", "gain", "gain_db"):
                    gv = float(v_unq.lower().rstrip("db").strip())
                    seg["gain_db"] = int(gv) if gv.is_integer() else round(gv, 2)
                elif k == "loudness":
                    lv = float(v_unq.lower().rstrip("lufs").strip())
                    seg["loudness"] = int(lv) if lv.is_integer() else round(lv, 2)
                elif k == "note":
                    seg["note"] = v_unq
                continue

            # Colon-prefixed event tokens: mark:..., chat:..., sfx:..., banner:...
            tok_low = tok.lower()
            if tok_low.startswith("mark:"):
                rest_m = tok[5:]
                m_parts = _split_top_level(rest_m, "@")
                m_type = _unquote(m_parts[0]) or "wrong"
                m_entry: Dict[str, Any] = {"type": m_type, "segment": seg_id, "at": 0.0}
                if len(m_parts) >= 2 and m_parts[1]:
                    at_dur = m_parts[1]
                    if "/" in at_dur:
                        at_s, dur_s = at_dur.split("/", 1)
                        m_entry["at"] = round(float(at_s), 4)
                        m_entry["duration"] = round(float(dur_s), 4)
                    else:
                        m_entry["at"] = round(float(at_dur), 4)
                for mp in m_parts[2:]:
                    mp_u = _unquote(mp)
                    if "=" in mp_u:
                        mk, mv = mp_u.split("=", 1)
                        mk = mk.strip().lower()
                        mv = mv.strip()
                        if mk in ("dur", "duration"):
                            m_entry["duration"] = round(float(mv), 4)
                        elif mk in ("pos", "position"):
                            m_entry["position"] = mv
                        elif mk == "size":
                            m_entry["size"] = round(float(mv), 4)
                        elif mk == "sfx":
                            m_entry["sfx"] = None if mv.lower() == "none" else mv
                        elif mk in ("gain", "sfx_gain_db"):
                            fv = float(mv)
                            m_entry["sfx_gain_db"] = int(fv) if fv.is_integer() else fv
                        elif mk == "style":
                            m_entry["style"] = mv
                    elif mp_u in ("center", "top-right", "top-left", "top-center", "bottom-right", "bottom-left"):
                        m_entry["position"] = mp_u
                marks.append(m_entry)

            elif tok_low.startswith("chat:"):
                rest_c = tok[5:]
                c_parts = _split_top_level(rest_c, "@")
                c_sp = _unquote(c_parts[0]) if len(c_parts) >= 1 else "user"
                c_txt = _unquote(c_parts[1]) if len(c_parts) >= 2 else ""
                c_at = round(float(c_parts[2]), 4) if len(c_parts) >= 3 and c_parts[2] else 0.0
                c_status = _unquote(c_parts[3]) if len(c_parts) >= 4 and c_parts[3] else None
                msg_obj: Dict[str, Any] = {"speaker": c_sp, "text": c_txt, "at": c_at}
                if c_status:
                    msg_obj["status"] = c_status
                chat_msgs.append(msg_obj)

            elif tok_low.startswith("sfx:"):
                rest_s = tok[4:]
                s_parts = _split_top_level(rest_s, "@")
                s_file = _unquote(s_parts[0])
                s_entry: Dict[str, Any] = {"file": s_file, "segment": seg_id, "at": 0.0}
                if len(s_parts) >= 2 and s_parts[1]:
                    at_gain = s_parts[1]
                    if "/" in at_gain:
                        at_s, g_s = at_gain.split("/", 1)
                        s_entry["at"] = round(float(at_s), 4)
                        gv = float(g_s.rstrip("dB"))
                        s_entry["gain_db"] = int(gv) if gv.is_integer() else gv
                    else:
                        s_entry["at"] = round(float(at_gain), 4)
                if len(s_parts) >= 3 and s_parts[2]:
                    gv = float(_unquote(s_parts[2]).rstrip("dB"))
                    s_entry["gain_db"] = int(gv) if gv.is_integer() else gv
                sfx_list.append(s_entry)

            elif tok_low.startswith(("banner:", "stop:")):
                rest_b = tok.split(":", 1)[1]
                b_parts = _split_top_level(rest_b, "@")
                b_txt = _unquote(b_parts[0]) if b_parts else ""
                b_at = round(float(b_parts[1]), 4) if len(b_parts) >= 2 and b_parts[1] else 0.0
                banners.append({"text": b_txt, "at": b_at})

        if chat_msgs or chat_mode:
            chat_obj: Dict[str, Any] = seg.get("chat") if isinstance(seg.get("chat"), dict) else {}
            if chat_mode:
                chat_obj["mode"] = chat_mode
            if chat_msgs:
                chat_obj["messages"] = chat_msgs
            seg["chat"] = chat_obj

        if banners:
            seg["banners"] = banners

        segments.append(seg)

    if canvas:
        plan["canvas"] = canvas
    if speakers:
        plan["speakers"] = speakers
    plan["segments"] = segments
    if marks:
        plan["marks"] = marks
    if sfx_list:
        plan["sfx"] = sfx_list
    if audio:
        plan["audio"] = audio
    return plan


def _detect_common_src_dir(segments: List[Dict[str, Any]]) -> str:
    """Find common parent directory across segment `src` paths if all clips live in one folder."""
    dirs: List[str] = []
    for s in segments:
        src = str(s.get("src") or "").replace("\\", "/")
        if "/" in src:
            d, _ = src.rsplit("/", 1)
            dirs.append(d)
        else:
            return ""
    if dirs and len(set(dirs)) == 1:
        return dirs[0]
    return ""


def format_edl(plan_dict: Dict[str, Any]) -> str:
    """Convert a `plan.json` dictionary into the compact 1-line-per-segment `plan.edl` DSL."""
    lines: List[str] = []

    if plan_dict.get("output"):
        lines.append(f"#output {_quote_if_needed(str(plan_dict['output']))}")
    if plan_dict.get("script"):
        lines.append(f"#script {_quote_if_needed(str(plan_dict['script']))}")

    segs = plan_dict.get("segments") or []
    src_dir = str(plan_dict.get("src_dir") or _detect_common_src_dir(segs))
    if src_dir:
        lines.append(f"#src_dir {_quote_if_needed(src_dir)}")

    canvas = plan_dict.get("canvas") or {}
    audio = plan_dict.get("audio") or {}
    canvas_tokens: List[str] = []
    if canvas.get("width") and canvas.get("height"):
        fps_part = f"@{canvas['fps']}" if canvas.get("fps") else ""
        canvas_tokens.append(f"{canvas['width']}x{canvas['height']}{fps_part}")
    elif canvas.get("fps"):
        canvas_tokens.append(f"fps={canvas['fps']}")

    if audio.get("loudness") is not None:
        canvas_tokens.append(f"loudness={audio['loudness']}")

    for extra_k in ("preset", "mark_pack", "sfx_pack"):
        val = canvas.get(extra_k) or plan_dict.get(extra_k)
        if val is not None:
            canvas_tokens.append(f"{extra_k}={_quote_if_needed(str(val))}")

    for ck, cv in canvas.items():
        if ck not in ("width", "height", "fps", "preset", "mark_pack", "sfx_pack"):
            canvas_tokens.append(f"{ck}={_quote_if_needed(str(cv))}")

    if canvas_tokens:
        lines.append(f"#canvas {' '.join(canvas_tokens)}")

    speakers = plan_dict.get("speakers") or {}
    if isinstance(speakers, dict) and speakers:
        if any(isinstance(v, dict) for v in speakers.values()):
            lines.append(f"#speakers {json.dumps(speakers, ensure_ascii=False)}")
        else:
            sp_toks = [f"{k}={_quote_if_needed(str(v))}" for k, v in speakers.items()]
            lines.append(f"#speakers {' '.join(sp_toks)}")

    if lines:
        lines.append("")

    # Group marks and sfx by segment ID
    marks_by_seg: Dict[str, List[Dict[str, Any]]] = {}
    for m in plan_dict.get("marks") or []:
        sid = str(m.get("segment") or "")
        marks_by_seg.setdefault(sid, []).append(m)

    sfx_by_seg: Dict[str, List[Dict[str, Any]]] = {}
    for e in plan_dict.get("sfx") or []:
        sid = str(e.get("segment") or "")
        sfx_by_seg.setdefault(sid, []).append(e)

    def _strip_src_dir(p_str: str) -> str:
        norm = str(p_str or "").replace("\\", "/")
        if src_dir and norm.startswith(src_dir.rstrip("/") + "/"):
            return norm[len(src_dir.rstrip("/")) + 1 :]
        return str(p_str or "")

    for i, s in enumerate(segs):
        sid = str(s.get("id") or f"S{i + 1}")
        src_short = _quote_if_needed(_strip_src_dir(str(s.get("src") or "")))
        t_range = f"{_fmt_time(float(s['start']))}-{_fmt_time(float(s['end']))}"
        toks: List[str] = [sid, src_short, t_range]

        lay = s.get("layout")
        chat_spec = s.get("chat")
        chat_mode = chat_spec.get("mode") if isinstance(chat_spec, dict) else None
        if lay and (lay != "single" or chat_mode):
            if lay == "chat_bar" and chat_mode == "docked":
                toks.append("layout=chat_bar:docked")
            else:
                toks.append(f"layout={lay}")

        has_z = "zoom" in s and s["zoom"] is not None
        has_zt = "zoom_to" in s and s["zoom_to"] is not None
        if has_z and has_zt:
            toks.append(f"z={_fmt_num(s['zoom'])}->{_fmt_num(s['zoom_to'])}")
        elif has_zt:
            toks.append(f"z=->{_fmt_num(s['zoom_to'])}")
        elif has_z:
            toks.append(f"z={_fmt_num(s['zoom'])}")

        if s.get("focus") and isinstance(s["focus"], (list, tuple)) and len(s["focus"]) >= 2:
            f0_s = f"{_fmt_num(s['focus'][0])},{_fmt_num(s['focus'][1])}"
            fe = s.get("focus_end")
            if isinstance(fe, (list, tuple)) and len(fe) >= 2 and (
                abs(float(fe[0]) - float(s["focus"][0])) > 0.005 or abs(float(fe[1]) - float(s["focus"][1])) > 0.005
            ):
                toks.append(f"focus={f0_s}->{_fmt_num(fe[0])},{_fmt_num(fe[1])}")
            else:
                toks.append(f"focus={f0_s}")

        if s.get("speaker"):
            toks.append(f"sp={_quote_if_needed(str(s['speaker']))}")

        if s.get("slot"):
            toks.append(f"slot={_quote_if_needed(str(s['slot']))}")

        if s.get("transition") and s.get("transition") != "cut":
            tr_str = str(s["transition"])
            if s.get("transition_duration") is not None:
                tr_str += f"@{_fmt_num(s['transition_duration'])}"
            toks.append(f"tr={tr_str}")

        if s.get("gain_db") is not None and float(s["gain_db"]) != 0.0:
            gv = float(s["gain_db"])
            sign = "+" if gv > 0 else ""
            toks.append(f"vol={sign}{_fmt_num(gv)}dB")

        if s.get("loudness") is not None:
            toks.append(f"loudness={_fmt_num(s['loudness'])}")

        peer = s.get("peer")
        if isinstance(peer, dict) and peer.get("src"):
            p_src = _strip_src_dir(str(peer["src"]))
            if peer.get("speaker"):
                p_head = f"{peer['speaker']}:{p_src}"
            else:
                p_head = p_src
            p_parts = [_quote_if_needed(p_head)]
            if peer.get("still_at") is not None:
                p_parts.append(_fmt_num(peer["still_at"]))
            if isinstance(peer.get("idle_span"), (list, tuple)) and len(peer["idle_span"]) >= 2:
                p_parts.append(f"{_fmt_num(peer['idle_span'][0])}-{_fmt_num(peer['idle_span'][1])}")
            if isinstance(peer.get("focus"), (list, tuple)) and len(peer["focus"]) >= 2:
                p_parts.append(f"{_fmt_num(peer['focus'][0])},{_fmt_num(peer['focus'][1])}")
            if peer.get("slot"):
                p_parts.append(str(peer["slot"]))
            toks.append(f"peer={'@'.join(p_parts)}")

        # Chat messages on segment
        msgs = (
            chat_spec.get("messages", [])
            if isinstance(chat_spec, dict)
            else (chat_spec if isinstance(chat_spec, list) else [])
        )
        for cm in msgs:
            if not isinstance(cm, dict):
                continue
            csp = _quote_if_needed(str(cm.get("speaker") or "user"))
            ctxt = _quote_if_needed(str(cm.get("text") or ""))
            cat = _fmt_num(float(cm.get("at", 0.0)))
            c_tok = f"chat:{csp}@{ctxt}@{cat}"
            if cm.get("status"):
                c_tok += f"@{cm['status']}"
            toks.append(c_tok)

        # Banners on segment
        for bn in s.get("banners") or s.get("stop_saying") or []:
            if isinstance(bn, dict):
                btxt = _quote_if_needed(str(bn.get("text") or ""))
                bat = _fmt_num(float(bn.get("at", 0.0)))
                toks.append(f"banner:{btxt}@{bat}")
            elif isinstance(bn, str):
                toks.append(f"banner:{_quote_if_needed(bn)}@0.0")

        # Marks on segment
        for m in marks_by_seg.get(sid, []):
            m_type = m.get("type", "wrong")
            m_at = _fmt_num(float(m.get("at", 0.0)))
            if m.get("duration") is not None:
                m_at += f"/{_fmt_num(float(m['duration']))}"
            m_tok = f"mark:{m_type}@{m_at}"
            if m.get("position"):
                m_tok += f"@pos={m['position']}"
            if m.get("size") is not None:
                m_tok += f"@size={_fmt_num(float(m['size']))}"
            if "sfx" in m and m["sfx"] != "default":
                m_tok += f"@sfx={m['sfx'] if m['sfx'] is not None else 'none'}"
            if m.get("sfx_gain_db") is not None:
                m_tok += f"@gain={m['sfx_gain_db']}"
            if m.get("style"):
                m_tok += f"@style={m['style']}"
            toks.append(m_tok)

        # SFX on segment
        for e in sfx_by_seg.get(sid, []):
            ef = _quote_if_needed(str(e.get("file") or "whoosh.wav"))
            eat = _fmt_num(float(e.get("at", 0.0)))
            if e.get("gain_db") is not None:
                eat += f"/{e['gain_db']}"
            toks.append(f"sfx:{ef}@{eat}")

        line_str = " ".join(toks)
        if s.get("note"):
            line_str += f"  # {s['note']}"
        lines.append(line_str)

    return "\n".join(lines) + "\n"


# Convenient aliases
to_edl = format_edl
dump_edl = format_edl
plan_to_edl = format_edl
from_edl = parse_edl
load_edl = parse_edl
edl_to_plan = parse_edl


def main() -> None:
    ap = argparse.ArgumentParser(description="Convert between plan.edl and plan.json.")
    ap.add_argument("input", help="Input .edl or .json file path")
    ap.add_argument("-o", "--output", help="Optional output file path (.json or .edl)")
    args = ap.parse_args()

    in_path = resolve(args.input)
    if in_path.suffix.lower() == ".edl":
        plan = parse_edl(in_path)
        if args.output:
            write_json(resolve(args.output), plan)
        else:
            print(json.dumps(plan, indent=2, ensure_ascii=False))
    else:
        plan = read_json(in_path)
        edl_str = format_edl(plan)
        if args.output:
            out_p = resolve(args.output)
            out_p.parent.mkdir(parents=True, exist_ok=True)
            out_p.write_text(edl_str, encoding="utf-8")
        else:
            print(edl_str, end="")


if __name__ == "__main__":
    main()
