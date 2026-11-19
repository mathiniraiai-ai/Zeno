"""Compact Edit Decision List (`plan.edl`) Parser and Serializer (`edl_parser.py`).

Converts losslessly between the compact one-line-per-segment `plan.edl` DSL and the
structured `plan.json` dictionary consumed by the rendering, verification, subtitle,
and Premiere XML export pipelines. Maintains full backward compatibility with `plan.json`.

Supported Header Directives:
    #output output/reel_edit.mp4
    #source input/footage            (alias: #src_dir)
    #script input/script/lesson.txt
    #style micronarrative
    #preset hybrid
    #mark_pack flash
    #sfx_pack default
    #card_style clean
    #cards off
    #loudness -11.5
    #canvas 1080x1920@30 loudness=-11.5 preset=hybrid mark_pack=flash
    #subs auto y=65 adapt=off size=54 split_y=50
    #page scroll size=44 bar=off dash=off fade=off bg=#FFFFFF
    #page_wrong "I myself"
    #page_right "I"
    #speakers nova="Miss Nova" ravi="Ravi"

Supported Segment Line Syntax:
    <id> <src> <start>-<end> [layout=single|split_h|split_v|top_pip|video_call|chat_bar]
    [z=1 | z=1.08 | z=1.0->1.06 | z=sudden_in:1.08@0.25 | z=flash_zoom:1.12@0.40]
    [focus=0.5,0.38 | focus=0.45,0.38->0.55,0.38] [sp="Speaker Name"]
    [slot=top|bottom|left|right] [active=top|bottom|left|right]
    [peer="Speaker:clip.mp4"@0.00-3.20@bottom[@offset_y=+0.08]] [offset_y=+0.08]
    [hold=0.48] [hold_src="clip.mp4"@0.50-1.00] [audio_end=1.40]
    [subs=off|on] [tr=split_reveal@0.40] [vol=+1.5dB]
    [mark:wrong@3.35/0.48] [banner:"STOP SAYING"@0.15] [card:"Nothing | ಏನಿಲ್ಲ"@pos=top]
    [sfx:pop.wav@0.00/-3] [chat:ravi@"Hello"@0.10@wrong] [# editorial note]
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

_SCRIPTS_DIR = Path(__file__).resolve().parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from common import read_json, resolve, write_json

DEFAULT_MARK_DURATION: float = 0.48

_KNOWN_LAYOUT_TOKENS = {
    "single",
    "full",
    "fullscreen",
    "split_h",
    "left_right",
    "hstack",
    "horizontal_split",
    "split_v",
    "top_bottom",
    "vstack",
    "vertical_split",
    "split",
    "top_pip",
    "pip_top",
    "floating_top",
    "floating_pip",
    "video_call",
    "pip_call",
    "pip",
    "gmeet_grid",
    "gmeet",
    "grid_call",
    "chat_bar",
    "texting",
    "chat",
}

_VIDEO_FILE_SUFFIXES = (".mp4", ".mov", ".mkv", ".webm", ".avi", ".m4v")
_TIME_RANGE_RE = re.compile(r"^([\d.]+)\s*(?:-|\.\.)\s*([\d.]+)$")
_KNOWN_BARE_FLAG_TOKENS = {"slomo", "mute", "muted", "beat"}
_DB_SUFFIX_RE = re.compile(r"\s*db\s*$", re.I)
_LUFS_SUFFIX_RE = re.compile(r"\s*lufs\s*$", re.I)
_SPEED_MIN: float = 0.25
_SPEED_MAX: float = 4.0
_OFFSET_Y_MIN: float = -0.25
_OFFSET_Y_MAX: float = 0.25
_CARD_LATIN_RE = re.compile(r"[A-Za-z]")
_CARD_NON_LATIN_RE = re.compile(r"[\u0900-\u0DFF\u0600-\u06FF\u0400-\u04FF\u4E00-\u9FFF]")
_CARD_FALLBACK_SEPS = (
    re.compile(r"\s*[—–]\s*"),
    re.compile(r"\s+/\s+"),
    re.compile(r"\s+-\s+"),
    re.compile(r"\s*/\s*"),
    re.compile(r"\s*-\s*"),
)


def _strip_card_outer_quotes(text: str) -> str:
    s = (text or "").strip()
    if len(s) >= 2 and (
        (s[0] == '"' and s[-1] == '"')
        or (s[0] == "'" and s[-1] == "'")
        or (s[0] == "“" and s[-1] == "”")
    ):
        return s[1:-1].strip()
    return s


def _card_script_counts(text: str) -> Tuple[int, int]:
    return len(_CARD_LATIN_RE.findall(text)), len(_CARD_NON_LATIN_RE.findall(text))


def _card_is_pure_bilingual(left: str, right: str) -> bool:
    l_lat, l_non = _card_script_counts(left)
    r_lat, r_non = _card_script_counts(right)
    return (l_non == 0 and l_lat > 0 and r_lat == 0 and r_non > 0) or (
        r_non == 0 and r_lat > 0 and l_lat == 0 and l_non > 0
    )


def _card_is_clearly_bilingual(left: str, right: str) -> bool:
    l_lat, l_non = _card_script_counts(left)
    r_lat, r_non = _card_script_counts(right)
    if (l_non > 0 and r_non == 0 and r_lat > 0) or (r_non > 0 and l_non == 0 and l_lat > 0):
        return True
    return (l_non > l_lat and r_lat > r_non) or (r_non > r_lat and l_lat > l_non)


def _parse_flashcard_text(raw: str) -> Dict[str, str]:
    """Normalize bilingual flashcard text so `en` is always Line 1 TOP and `native` is Line 2 BOTTOM."""
    cleaned = _strip_card_outer_quotes(re.sub(r"\s+", " ", str(raw or "")).strip())
    if not cleaned:
        return {"text": "", "en": "", "native": ""}

    left = ""
    right = ""
    if "|" in cleaned:
        p0, p1 = cleaned.split("|", 1)
        left, right = _strip_card_outer_quotes(p0), _strip_card_outer_quotes(p1)
    else:
        for sep_re in _CARD_FALLBACK_SEPS:
            matches = list(sep_re.finditer(cleaned))
            if not matches:
                continue
            chosen: Optional[Tuple[str, str]] = None
            for m in matches:
                cl = _strip_card_outer_quotes(cleaned[: m.start()])
                cr = _strip_card_outer_quotes(cleaned[m.end() :])
                if cl and cr and _card_is_pure_bilingual(cl, cr):
                    chosen = (cl, cr)
                    break
            if chosen is None:
                for m in matches:
                    cl = _strip_card_outer_quotes(cleaned[: m.start()])
                    cr = _strip_card_outer_quotes(cleaned[m.end() :])
                    if cl and cr and _card_is_clearly_bilingual(cl, cr):
                        chosen = (cl, cr)
                        break
            if chosen is not None:
                left, right = chosen
                break

    if left and right:
        l_lat, l_non = _card_script_counts(left)
        r_lat, r_non = _card_script_counts(right)
        if (
            (l_non > 0 and r_non == 0 and r_lat > 0)
            or (l_non > l_lat and r_lat >= r_non)
            or (l_non > r_non and r_lat > l_lat)
        ):
            en, native = right, left
        elif (
            (r_non > 0 and l_non == 0 and l_lat > 0)
            or (r_non > r_lat and l_lat >= l_non)
            or (r_non > l_non and l_lat > r_lat)
        ):
            en, native = left, right
        else:
            en, native = left, right
    else:
        single = left or right or cleaned
        s_lat, s_non = _card_script_counts(single)
        if s_non > s_lat:
            en, native = "", single
        else:
            en, native = single, ""

    text = f"{en} | {native}" if (en and native) else (en or native)
    return {"text": text, "en": en, "native": native}


def _parse_bool_token(val: str) -> bool:
    """Parse common boolean token values (`1`/`true`/`on`/`yes` vs `0`/`false`/`off`/`no`)."""
    return str(val or "").strip().lower() not in ("", "0", "false", "no", "off")


def _parse_db_value(val: str) -> float:
    """Parse a decibel string such as `+1.5dB` or `-20db` into a float."""
    cleaned = _DB_SUFFIX_RE.sub("", str(val or "").strip()).strip()
    return round(float(cleaned), 4)


def _clamp_speed(val: float) -> float:
    """Clamp playback speed factor to the supported `[0.25, 4.0]` range."""
    return round(max(_SPEED_MIN, min(_SPEED_MAX, float(val))), 4)


def _clamp_offset_y(val: float) -> float:
    """Clamp per-panel vertical shift `offset_y` to the supported `[-0.25, 0.25]` range."""
    return round(max(_OFFSET_Y_MIN, min(_OFFSET_Y_MAX, float(val))), 4)


def _set_zoom_end(seg: Dict[str, Any], val: float) -> None:
    """Populate both canonical `zoom_to` and `zoom_end` keys for cross-module compatibility."""
    v = round(float(val), 4)
    seg["zoom_to"] = v
    seg["zoom_end"] = v


def _merge_unquoted_src_tokens(toks: List[str], src_dir: str = "") -> List[str]:
    """Merge unquoted clip filenames containing spaces when followed by `<start>-<end>`."""
    if len(toks) < 4 or _TIME_RANGE_RE.match(toks[2]):
        return toks
    for j in range(3, len(toks)):
        if _TIME_RANGE_RE.match(toks[j]):
            cand = " ".join(_unquote(t) for t in toks[1:j]).strip()
            if cand and (
                cand.lower().endswith(_VIDEO_FILE_SUFFIXES)
                or (src_dir and resolve(_join_src_dir(src_dir, cand)).is_file())
            ):
                return [toks[0], cand, *toks[j:]]
            break
    return toks


def _merge_unquoted_peer_tokens(toks: List[str]) -> List[str]:
    """Merge unquoted `peer=`/`pip=`/`hold_src=` tokens whose speaker or clip name contains spaces."""
    if len(toks) < 2:
        return toks
    out: List[str] = []
    i = 0
    while i < len(toks):
        tok = toks[i]
        low = tok.lower()
        if (low.startswith("peer=") or low.startswith("pip=") or low.startswith("hold_src=")) and "@" not in tok:
            merged_until: Optional[int] = None
            for k in range(i + 1, min(len(toks), i + 6)):
                nxt = toks[k]
                nxt_low = nxt.lower()
                if (
                    "=" in nxt.split("@", 1)[0]
                    or nxt_low in _KNOWN_LAYOUT_TOKENS
                    or nxt_low in _KNOWN_BARE_FLAG_TOKENS
                    or nxt_low.startswith(("mark:", "chat:", "sfx:", "banner:", "stop:", "card:", "flashcard:"))
                ):
                    break
                if "@" in nxt:
                    merged_until = k
                    break
            if merged_until is not None:
                out.append(" ".join(toks[i : merged_until + 1]))
                i = merged_until + 1
                continue
        out.append(tok)
        i += 1
    return out


def _quote_if_needed(val: str) -> str:
    """Wrap `val` in double quotes if empty or if it contains delimiter characters."""
    text = str(val or "")
    if not text or any(ch in text for ch in (" ", "\t", '"', "#", "@", "=", "|")):
        escaped = text.replace("\\", "\\\\").replace('"', '\\"')
        return f'"{escaped}"'
    return text


def _unquote(val: str) -> str:
    """Strip matching outer single or double quotes and unescape inner characters."""
    text = str(val or "").strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in ('"', "'"):
        inner = text[1:-1]
        return inner.replace('\\"', '"').replace("\\'", "'").replace("\\\\", "\\")
    return text


def _split_line_and_note(line: str) -> Tuple[str, Optional[str]]:
    """Split a segment line into `(tokens_body, inline_note)` while respecting quotes."""
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
    """Split `body` on whitespace while keeping quoted substrings intact within tokens."""
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


def _split_top_level(text: str, sep: str = "@") -> List[str]:
    """Split `text` by `sep` only outside quoted substrings."""
    parts: List[str] = []
    cur: List[str] = []
    in_quote: Optional[str] = None
    escape = False
    for ch in text:
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


def _has_top_level_pipe(text: str) -> bool:
    """Return True if `text` contains a `|` delimiter outside quoted substrings."""
    return len(_split_top_level(text, "|")) > 1


def _join_src_dir(src_dir: str, clip_ref: str) -> str:
    """Resolve `clip_ref` relative to `src_dir` when `clip_ref` is a bare filename."""
    clip = _unquote(clip_ref)
    if not src_dir:
        return clip
    if "/" in clip or "\\" in clip or re.match(r"^[A-Za-z]:", clip):
        return clip
    norm_src = src_dir.rstrip("/\\")
    if norm_src.lower().endswith(_VIDEO_FILE_SUFFIXES):
        base_name = norm_src.replace("\\", "/").rsplit("/", 1)[-1]
        if clip == base_name:
            return norm_src
        if "/" in norm_src.replace("\\", "/"):
            norm_src = norm_src.replace("\\", "/").rsplit("/", 1)[0]
        else:
            return clip
    return f"{norm_src}/{clip}"


def _fmt_num(val: Optional[float]) -> str:
    """Format a floating-point parameter compactly without trailing zeros."""
    if val is None:
        return "0"
    rounded = round(float(val), 4)
    if abs(rounded - round(rounded, 2)) < 1e-6:
        return f"{rounded:.2f}".rstrip("0").rstrip(".")
    return f"{rounded:.4f}".rstrip("0").rstrip(".")


def _fmt_time(val: float) -> str:
    """Format a segment timestamp to 2 decimal places (or up to 3 when millisecond-precise)."""
    rounded = round(float(val), 4)
    if abs(rounded - round(rounded, 2)) < 1e-5:
        return f"{rounded:.2f}"
    return f"{rounded:.3f}".rstrip("0").rstrip(".")


def _parse_peer_spec(raw_peer: str, src_dir: str) -> Dict[str, Any]:
    """Parse a `peer=...` specification into a normalized peer dictionary."""
    peer_dict: Dict[str, Any] = {}
    p_parts = _split_top_level(raw_peer, "@")
    first_part = _unquote(p_parts[0])
    if ":" in first_part and not re.match(r"^[A-Za-z]:[/\\]", first_part):
        peer_sp, peer_src = first_part.split(":", 1)
        peer_dict["speaker"] = peer_sp.strip()
        peer_dict["src"] = _join_src_dir(src_dir, peer_src.strip())
    else:
        peer_dict["src"] = _join_src_dir(src_dir, first_part)

    for extra in p_parts[1:]:
        ex = _unquote(extra).strip()
        if not ex:
            continue
        if re.match(r"^[\d.]+-[\d.]+$", ex):
            s0, s1 = ex.split("-", 1)
            peer_dict["idle_span"] = [round(float(s0), 4), round(float(s1), 4)]
            peer_dict.setdefault("mode", "loop")
        elif re.match(r"^[\d.]+,[\d.]+$", ex):
            fx_s, fy_s = ex.split(",", 1)
            peer_dict["focus"] = [round(float(fx_s), 4), round(float(fy_s), 4)]
        elif ex.lower() in ("top", "bottom", "left", "right"):
            peer_dict["slot"] = ex.lower()
        elif ex.lower() in ("still", "live", "loop"):
            peer_dict["mode"] = ex.lower()
        elif "=" in ex:
            pk, pv = ex.split("=", 1)
            pk = pk.strip().lower()
            pv = _unquote(pv.strip())
            if pk in ("offset_y", "dy"):
                try:
                    peer_dict["offset_y"] = _clamp_offset_y(float(pv))
                except ValueError:
                    pass
        else:
            try:
                peer_dict["still_at"] = round(float(ex), 4)
            except ValueError:
                pass
    return peer_dict


def _parse_zoom_token(raw_zoom: str, seg: Dict[str, Any]) -> None:
    """Parse a `z=...` or `zoom=...` token and populate zoom fields on `seg`."""
    raw_z = raw_zoom.strip()
    at_part: Optional[str] = None
    if "@" in raw_z:
        raw_z, at_part = raw_z.split("@", 1)

    raw_style_name: Optional[str] = None
    style_part: Optional[str] = None
    low_z = raw_z.lower()
    for st_name in (
        "sudden_in",
        "punch_in",
        "sudden_out",
        "punch_out",
        "flash_zoom",
        "zoom_in",
        "zoom_out",
        "mild_in",
        "mild_out",
        "smooth",
        "bell",
    ):
        if low_z == st_name or low_z.startswith(st_name + ":") or low_z.endswith(":" + st_name):
            raw_style_name = st_name
            if st_name in ("punch_in", "sudden_in"):
                style_part = "sudden_in"
            elif st_name in ("punch_out", "sudden_out"):
                style_part = "sudden_out"
            elif st_name in ("mild_in", "mild_out", "zoom_in", "zoom_out", "smooth"):
                style_part = "smooth"
            else:
                style_part = st_name
            raw_z = re.sub(rf"(^|:){re.escape(st_name)}(:|$)", "", raw_z, flags=re.I).strip(":")
            break

    if style_part:
        seg["zoom_style"] = style_part
        if not raw_z:
            if style_part == "bell":
                seg["zoom"] = 1.2
                seg["zoom_peak"] = 1.55
                _set_zoom_end(seg, 1.0)
            elif raw_style_name in ("mild_out", "zoom_out"):
                seg["zoom"] = 1.08
                _set_zoom_end(seg, 1.0)
            elif raw_style_name == "mild_in":
                seg["zoom"] = 1.0
                _set_zoom_end(seg, 1.06)
            elif raw_style_name == "zoom_in":
                seg["zoom"] = 1.0
                _set_zoom_end(seg, 1.08)
            elif raw_style_name == "smooth":
                seg["zoom"] = 1.0
                _set_zoom_end(seg, 1.06)
            elif style_part == "sudden_out":
                seg["zoom"] = 1.1
                _set_zoom_end(seg, 1.0)
                seg.setdefault("zoom_dur", 0.20)
            elif style_part == "flash_zoom":
                seg["zoom"] = 1.0
                _set_zoom_end(seg, 1.12)
                seg.setdefault("zoom_dur", 0.16)
            else:
                seg["zoom"] = 1.0
                _set_zoom_end(seg, 1.1)
                seg.setdefault("zoom_dur", 0.18)

    if raw_z:
        if style_part == "bell" or raw_z.count("->") >= 2:
            seg["zoom_style"] = "bell"
            parts = [p.strip() for p in raw_z.split("->")]
            if len(parts) >= 3:
                seg["zoom"] = round(float(parts[0]), 4) if parts[0] else 1.2
                seg["zoom_peak"] = round(float(parts[1]), 4) if parts[1] else 1.55
                _set_zoom_end(seg, float(parts[2]) if parts[2] else 1.0)
            elif len(parts) == 2:
                seg["zoom"] = round(float(parts[0]), 4) if parts[0] else 1.2
                seg["zoom_peak"] = round(float(parts[1]), 4) if parts[1] else 1.55
                _set_zoom_end(seg, 1.0)
            else:
                seg["zoom"] = 1.2
                seg["zoom_peak"] = round(float(parts[0]), 4)
                _set_zoom_end(seg, 1.0)
        elif "->" in raw_z:
            z0_s, z1_s = raw_z.split("->", 1)
            if z0_s.strip():
                seg["zoom"] = round(float(z0_s.strip()), 4)
            if z1_s.strip():
                _set_zoom_end(seg, float(z1_s.strip()))
            if "zoom_style" not in seg:
                seg["zoom_style"] = "smooth"
        else:
            val = round(float(raw_z), 4)
            if style_part in ("sudden_in", "flash_zoom") or raw_style_name in ("mild_in", "zoom_in", "smooth"):
                seg["zoom"] = 1.0
                _set_zoom_end(seg, val)
            elif style_part == "sudden_out" or raw_style_name in ("mild_out", "zoom_out"):
                seg["zoom"] = val
                _set_zoom_end(seg, 1.0)
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
        if seg.get("zoom_style") in (None, "", "smooth"):
            z0_cur = float(seg.get("zoom", 1.0) or 1.0)
            z1_raw = seg.get("zoom_to") if seg.get("zoom_to") is not None else seg.get("zoom_end")
            z1_cur = float(z1_raw if z1_raw is not None else z0_cur)
            seg["zoom_style"] = "sudden_out" if z1_cur < z0_cur else "sudden_in"
            seg.setdefault("zoom_dur", 0.18)


def _convert_pipe_segment_to_tokens(pcols: List[str]) -> Tuple[List[str], Optional[str]]:
    """Convert a pipe-delimited segment row (`id | src | start-end | layout | zoom | tr | extras | note`)
    into canonical space-delimited segment tokens and an optional inline note.
    """
    toks: List[str] = [_quote_if_needed(_unquote(pcols[0])), _quote_if_needed(_unquote(pcols[1])), pcols[2].strip()]
    if len(pcols) >= 4 and pcols[3] and pcols[3] != "-":
        lay_col = pcols[3].strip()
        toks.append(lay_col if lay_col.lower().startswith("layout=") else f"layout={lay_col}")
    if len(pcols) >= 5 and pcols[4] and pcols[4] != "-":
        z_col = pcols[4].strip()
        toks.append(z_col if re.match(r"^(?:z|zoom)=", z_col, re.I) else f"z={z_col}")
    if len(pcols) >= 6 and pcols[5] and pcols[5].lower() not in ("-", "cut", "none"):
        tr_col = pcols[5].strip()
        toks.append(tr_col if re.match(r"^(?:tr|trans|transition)=", tr_col, re.I) else f"tr={tr_col}")
    if len(pcols) >= 7 and pcols[6] and pcols[6].lower() not in ("-", "none"):
        toks.extend(_tokenize_line(pcols[6]))
    pipe_note = pcols[7].strip() if len(pcols) >= 8 and pcols[7].strip() else None
    return toks, pipe_note


def parse_edl(path_or_text: Union[str, Path, Dict[str, Any]]) -> Dict[str, Any]:
    """Parse a `.edl` file, `.json` file, raw EDL string, or JSON string into a `plan.json` dictionary."""
    if isinstance(path_or_text, dict):
        return dict(path_or_text)

    raw_text: Optional[str] = None
    file_path: Optional[Path] = None

    if isinstance(path_or_text, Path):
        file_path = path_or_text if path_or_text.is_file() else resolve(path_or_text)
        raw_text = file_path.read_text(encoding="utf-8")
    elif isinstance(path_or_text, str):
        if "\n" not in path_or_text and len(path_or_text) < 260:
            cand = Path(path_or_text)
            if cand.is_file():
                file_path = cand
                raw_text = cand.read_text(encoding="utf-8")
            else:
                resolved_cand = resolve(path_or_text)
                if resolved_cand.is_file():
                    file_path = resolved_cand
                    raw_text = resolved_cand.read_text(encoding="utf-8")
        if raw_text is None:
            raw_text = path_or_text
    else:
        raise TypeError(f"Expected str, Path, or dict, got {type(path_or_text)}")

    # Backward compatibility with plan.json files and serialized JSON plan payloads
    stripped_payload = raw_text.lstrip("\ufeff \t\r\n")
    if (file_path is not None and file_path.suffix.lower() == ".json") or stripped_payload.startswith("{"):
        parsed_json = json.loads(stripped_payload)
        if isinstance(parsed_json, dict):
            return parsed_json

    plan: Dict[str, Any] = {}
    canvas: Dict[str, Any] = {}
    audio: Dict[str, Any] = {}
    speakers: Dict[str, Any] = {}
    segments: List[Dict[str, Any]] = []
    marks: List[Dict[str, Any]] = []
    sfx_list: List[Dict[str, Any]] = []
    src_dir: str = ""

    header_re = re.compile(
        r"^#\s*(output|script|style|subs|page_wrong|page_right|page|"
        r"src_dir|source_dir|source|canvas|speakers|audio|loudness|"
        r"preset|mark_pack|sfx_pack|card_style|card_pack|cards|"
        r"square|max_hook_transitions|concept_brief|characters|scene_\d+)\b\s*:?\s*(.*)$",
        re.I,
    )

    for raw_line in raw_text.splitlines():
        line = raw_line.strip()
        if not line:
            continue

        # 1. Header Directives
        if line.startswith("#"):
            m_dir = header_re.match(line)
            if not m_dir:
                continue
            directive = m_dir.group(1).lower()
            rest = m_dir.group(2).strip()

            if directive == "output":
                plan["output"] = _unquote(_split_top_level(rest, "|")[0].strip())
            elif directive == "script":
                plan["script"] = _unquote(_split_top_level(rest, "|")[0].strip())
            elif directive == "style":
                plan["style"] = re.sub(r"[^a-z0-9_-]", "", _unquote(_split_top_level(rest, "|")[0].strip()).lower())
            elif directive in ("src_dir", "source_dir", "source"):
                src_dir = _unquote(_split_top_level(rest, "|")[0].strip())
            elif directive == "loudness":
                raw_lv = _unquote(_split_top_level(rest, "|")[0].strip()).lower().rstrip("lufs").strip()
                if raw_lv:
                    fv = float(raw_lv)
                    audio["loudness"] = int(fv) if fv.is_integer() else fv
            elif directive == "square":
                sq_tok = _unquote(_split_top_level(rest, "|")[0].strip()).lower()
                plan["square"] = sq_tok not in ("off", "false", "no", "0")
            elif directive == "max_hook_transitions":
                mht_tok = _unquote(_split_top_level(rest, "|")[0].strip())
                if mht_tok:
                    plan["max_hook_transitions"] = int(float(mht_tok))
            elif directive == "cards":
                c_tok = _unquote(_split_top_level(rest, "|")[0].strip()).lower()
                if c_tok in ("off", "none", "false", "no", "0", "disable", "disabled"):
                    plan["cards_off"] = True
                elif c_tok in ("on", "true", "yes", "1", "auto"):
                    plan["cards_off"] = False
            elif directive in ("card_style", "card_pack"):
                cs_tok = _unquote(_split_top_level(rest, "|")[0].strip())
                if cs_tok:
                    plan["card_style"] = cs_tok
            elif directive in ("page", "page_wrong", "page_right"):
                body, _ = _split_line_and_note(rest)
                toks = _tokenize_line(_split_top_level(body, "|")[0].strip())
                page = plan.get("page") if isinstance(plan.get("page"), dict) else None
                if directive == "page":
                    if toks and _unquote(toks[0]).lower() in ("off", "none", "false", "no", "0"):
                        plan["page"] = None
                    else:
                        page = page or {"mode": "scroll"}
                        for tok in toks:
                            m_kv = re.match(r"^(size|bar|dash|fade|bg|color|wrong|right|highlight|accent|font|font_family)=(.+)$", tok, re.I)
                            if not m_kv:
                                continue
                            pk, pv = m_kv.group(1).lower(), _unquote(m_kv.group(2))
                            if pk == "size":
                                try:
                                    page[pk] = float(pv)
                                except ValueError:
                                    pass
                            elif pk in ("bar", "dash", "fade"):
                                page[pk] = pv.lower() not in ("off", "false", "no", "0")
                            elif pk in ("font", "font_family"):
                                if pv.strip():
                                    page["font"] = pv.strip()
                            else:
                                page[pk] = pv
                        plan["page"] = page
                else:
                    page = page or {"mode": "scroll"}
                    phrase_key = "wrong_phrases" if directive == "page_wrong" else "right_phrases"
                    page.setdefault(phrase_key, []).extend(
                        phrase for phrase in (_unquote(tok).strip() for tok in toks) if phrase
                    )
                    plan["page"] = page
            elif directive == "subs":
                subs_body, _ = _split_line_and_note(rest)
                subs_body = _split_top_level(subs_body, "|")[0].strip()
                toks = _tokenize_line(subs_body)
                if not toks:
                    plan["subs"] = {"style": "auto", "y": None}
                else:
                    style_parts: List[str] = []
                    y_val: Optional[float] = None
                    adapt_val: Optional[bool] = None
                    extra_subs: Dict[str, Any] = {}
                    for tok in toks:
                        m_y = re.match(r"^y=([0-9.]+)%?$", tok, re.I)
                        m_a = re.match(r"^adapt=(on|off|true|false|1|0)$", tok, re.I)
                        m_sz = re.match(r"^size=([0-9.]+)$", tok, re.I)
                        m_sy = re.match(r"^split_y=([0-9.]+)%?$", tok, re.I)
                        m_fn = re.match(r"^(?:font|font_family)=(.+)$", tok, re.I)
                        m_wc = re.match(r"^with_cards=(on|off|true|false|1|0)$", tok, re.I)
                        if m_sz:
                            extra_subs["size"] = float(m_sz.group(1))
                            continue
                        if m_sy:
                            extra_subs["split_y"] = float(m_sy.group(1))
                            continue
                        if m_fn:
                            fn_unq = _unquote(m_fn.group(1).strip())
                            if fn_unq:
                                extra_subs["font"] = fn_unq
                            continue
                        if m_wc:
                            extra_subs["with_cards"] = m_wc.group(1).lower() in ("on", "true", "1")
                            continue
                        m_st = re.match(r"^style=(.+)$", tok, re.I)
                        if m_y:
                            y_val = float(m_y.group(1))
                        elif m_a:
                            adapt_val = m_a.group(1).lower() in ("on", "true", "1")
                        elif m_st:
                            st_unq = _unquote(m_st.group(1).strip())
                            if st_unq:
                                style_parts.append(st_unq)
                        else:
                            st_unq = _unquote(tok)
                            if st_unq:
                                style_parts.append(st_unq)
                    style_str = " ".join(style_parts).strip()
                    low_st = style_str.lower()
                    if low_st in ("off", "none", "false", "no", "0", "disable", "disabled"):
                        plan["subs"] = None
                    else:
                        if not style_str or low_st in ("on", "true", "yes", "1"):
                            style_str = "auto"
                        subs_obj: Dict[str, Any] = {"style": style_str, "y": y_val}
                        if adapt_val is not None:
                            subs_obj["adapt"] = adapt_val
                        subs_obj.update(extra_subs)
                        plan["subs"] = subs_obj
            elif directive in ("concept_brief", "characters") or directive.startswith("scene_"):
                plan[directive] = rest
            elif directive in ("preset", "mark_pack", "sfx_pack"):
                pipe_parts = _split_top_level(rest, "|")
                first_chunk = pipe_parts[0].strip()
                for part in pipe_parts:
                    p_clean = part.strip()
                    m_sub = re.match(r"^(preset|mark_pack|sfx_pack|card_style|card_pack|loudness)\b\s*:?\s*(.*)$", p_clean, re.I)
                    if m_sub:
                        sub_k = m_sub.group(1).lower()
                        sub_v = _unquote(m_sub.group(2).strip())
                        if sub_k == "loudness":
                            fv = float(sub_v.lower().rstrip("lufs").strip())
                            audio["loudness"] = int(fv) if fv.is_integer() else fv
                        elif sub_k == "card_pack":
                            plan["card_style"] = sub_v
                        else:
                            plan[sub_k] = sub_v
                    elif p_clean == first_chunk:
                        plan[directive] = _unquote(p_clean)
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
                        ck, cv = tok.split("=", 1)
                        ck = ck.strip().lower()
                        cv = _unquote(cv.strip())
                        if ck == "loudness":
                            fv = float(_LUFS_SUFFIX_RE.sub("", cv.strip()).strip())
                            audio["loudness"] = int(fv) if fv.is_integer() else fv
                        elif ck == "fps":
                            fv = float(cv)
                            canvas["fps"] = int(fv) if fv.is_integer() else fv
                        elif ck in ("width", "w"):
                            canvas["width"] = int(cv)
                        elif ck in ("height", "h"):
                            canvas["height"] = int(cv)
                        elif ck == "square":
                            canvas["square"] = cv.lower() not in ("off", "false", "no", "0")
                        elif ck == "max_hook_transitions":
                            plan["max_hook_transitions"] = int(float(cv))
                        elif ck in ("gain_db", "music_gain_db", "music_hook_db"):
                            audio[ck] = _parse_db_value(cv)
                        elif ck == "music":
                            audio["music"] = cv
                        elif ck == "music_duck":
                            audio["music_duck"] = _parse_bool_token(cv)
                        elif ck in ("preset", "mark_pack", "sfx_pack", "card_style"):
                            plan[ck] = cv
                        elif ck == "card_pack":
                            plan["card_style"] = cv
                        elif ck == "cards":
                            if cv.lower() in ("off", "none", "false", "no", "0", "disable", "disabled"):
                                plan["cards_off"] = True
                            elif cv.lower() in ("on", "true", "yes", "1", "auto"):
                                plan["cards_off"] = False
                        else:
                            canvas[ck] = cv
            elif directive == "audio":
                for tok in _tokenize_line(rest):
                    if "=" in tok:
                        ak, av = tok.split("=", 1)
                        ak, av = ak.strip().lower(), _unquote(av.strip())
                        if ak == "loudness":
                            fv = float(_LUFS_SUFFIX_RE.sub("", av.strip()).strip())
                            audio["loudness"] = int(fv) if fv.is_integer() else fv
                        elif ak in ("gain_db", "music_gain_db", "music_hook_db"):
                            audio[ak] = _parse_db_value(av)
                        elif ak == "music_duck":
                            audio[ak] = _parse_bool_token(av)
                        else:
                            audio[ak] = av
            elif directive == "speakers":
                if rest.startswith("{"):
                    try:
                        speakers.update(json.loads(rest))
                    except Exception:
                        pass
                else:
                    for tok in _tokenize_line(rest):
                        if "=" in tok:
                            sk, sv = tok.split("=", 1)
                            speakers[sk.strip()] = _unquote(sv.strip())
            continue

        # 2. Standalone space-delimited `sfx ...` and `mark ...` rows
        if line.lower().startswith("sfx ") and not _has_top_level_pipe(line):
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
                            gv = float(sv.lower().rstrip("db").strip())
                            s_entry["gain_db"] = int(gv) if gv.is_integer() else gv
                if s_entry["segment"]:
                    sfx_list.append(s_entry)
            continue

        if line.lower().startswith("mark ") and not _has_top_level_pipe(line):
            mtoks = _tokenize_line(line)
            if len(mtoks) >= 2:
                m_entry_s: Dict[str, Any] = {
                    "type": _unquote(mtoks[1]) or "wrong",
                    "segment": "",
                    "at": 0.0,
                    "duration": DEFAULT_MARK_DURATION,
                }
                for mt in mtoks[2:]:
                    if "=" in mt:
                        mk, mv = mt.split("=", 1)
                        mk, mv = mk.strip().lower(), _unquote(mv.strip())
                        if mk in ("seg", "segment"):
                            m_entry_s["segment"] = mv
                        elif mk == "at":
                            m_entry_s["at"] = round(float(mv), 4)
                        elif mk in ("dur", "duration"):
                            m_entry_s["duration"] = round(float(mv), 4)
                        elif mk in ("pos", "position"):
                            m_entry_s["position"] = mv
                        elif mk == "size":
                            m_entry_s["size"] = round(float(mv), 4)
                        elif mk == "sfx":
                            m_entry_s["sfx"] = None if mv.lower() == "none" else mv
                        elif mk in ("gain", "gain_db", "sfx_gain_db"):
                            gv = float(mv.lower().rstrip("db").strip())
                            m_entry_s["sfx_gain_db"] = int(gv) if gv.is_integer() else gv
                        elif mk == "style":
                            m_entry_s["style"] = mv
                if m_entry_s["segment"]:
                    marks.append(m_entry_s)
            continue

        # 3. Segment Line or Pipe-Delimited Table Row (`# MARKS:`, `# SFX:`, `# SEGMENTS:`)
        body, note = _split_line_and_note(line)
        if not body:
            continue

        if _has_top_level_pipe(body):
            pcols = [col.strip() for col in _split_top_level(body, "|")]
            head_low = _unquote(pcols[0]).lower() if pcols else ""
            if (
                len(pcols) >= 3
                and head_low in ("wrong", "right", "x", "check", "tick", "cross")
                and re.match(r"^-?[\d.]+$", pcols[2])
            ):
                m_type_p = "wrong" if head_low in ("wrong", "x", "cross") else "right"
                dur_p = (
                    round(float(pcols[3]), 4)
                    if (len(pcols) >= 4 and pcols[3] and re.match(r"^[\d.]+$", pcols[3]))
                    else DEFAULT_MARK_DURATION
                )
                m_row: Dict[str, Any] = {
                    "type": m_type_p,
                    "segment": _unquote(pcols[1]),
                    "at": round(float(pcols[2]), 4),
                    "duration": dur_p,
                }
                if len(pcols) >= 5 and pcols[4]:
                    m_row["position"] = _unquote(pcols[4])
                if len(pcols) >= 6 and pcols[5]:
                    m_row["size"] = round(float(pcols[5]), 4)
                if len(pcols) >= 7 and pcols[6]:
                    sv_sfx = _unquote(pcols[6])
                    if sv_sfx.lower() != "default":
                        m_row["sfx"] = None if sv_sfx.lower() == "none" else sv_sfx
                if len(pcols) >= 8 and pcols[7]:
                    gv_m = float(_unquote(pcols[7]).lower().rstrip("db").strip())
                    m_row["sfx_gain_db"] = int(gv_m) if gv_m.is_integer() else gv_m
                marks.append(m_row)
                continue

            if (
                len(pcols) >= 3
                and head_low.endswith((".wav", ".mp3", ".ogg", ".m4a", ".aac"))
                and re.match(r"^-?[\d.]+$", pcols[2])
            ):
                s_row: Dict[str, Any] = {
                    "file": _unquote(pcols[0]),
                    "segment": _unquote(pcols[1]),
                    "at": round(float(pcols[2]), 4),
                }
                if len(pcols) >= 4 and pcols[3]:
                    gv_s = float(_unquote(pcols[3]).lower().rstrip("db").strip())
                    s_row["gain_db"] = int(gv_s) if gv_s.is_integer() else gv_s
                sfx_list.append(s_row)
                continue

            if len(pcols) >= 3 and re.match(r"^([\d.]+)\s*(?:-|\.\.)\s*([\d.]+)$", pcols[2]):
                toks, pipe_note = _convert_pipe_segment_to_tokens(pcols)
                if pipe_note and not note:
                    note = pipe_note
            else:
                toks = _tokenize_line(body)
        else:
            toks = _tokenize_line(body)

        toks = _merge_unquoted_src_tokens(toks, src_dir)
        toks = _merge_unquoted_peer_tokens(toks)

        if len(toks) < 3:
            continue

        seg_id = _unquote(toks[0])
        src_val = _join_src_dir(src_dir, toks[1])
        m_time = _TIME_RANGE_RE.match(toks[2])
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
        flashcards: List[Dict[str, Any]] = []

        for tok in toks[3:]:
            tok_low = tok.lower()

            # Bare flag tokens (`slomo`, `mute`, `beat`) and layout tokens (`split_v`, `top_pip`, `chat_bar:docked`)
            bare_unq = _unquote(tok)
            bare_low = bare_unq.strip().lower()
            bare_head = bare_unq.split(":", 1)[0].strip().lower()
            if "=" not in tok and bare_low in _KNOWN_BARE_FLAG_TOKENS:
                if bare_low == "slomo":
                    seg["speed"] = 0.5
                elif bare_low in ("mute", "muted"):
                    seg["mute"] = True
                elif bare_low == "beat":
                    seg["beat"] = True
                continue
            if "=" not in tok and bare_head in _KNOWN_LAYOUT_TOKENS:
                seg["layout"] = bare_head
                if ":" in bare_unq:
                    sub_mode = bare_unq.split(":", 1)[1].strip().lower()
                    if sub_mode in ("docked", "floating_overlay", "floating"):
                        chat_mode = "docked" if "dock" in sub_mode else "floating_overlay"
                continue

            # Key=Value tokens
            if "=" in tok and not tok_low.startswith(("mark:", "chat:", "sfx:", "banner:", "stop:", "card:", "flashcard:")):
                k, v = tok.split("=", 1)
                k = k.strip().lower()
                v_unq = _unquote(v.strip())

                if k == "layout":
                    if ":" in v_unq:
                        lay_part, sub_spec = v_unq.split(":", 1)
                        seg["layout"] = lay_part.strip()
                        sub_low = sub_spec.strip().lower()
                        if sub_low in ("docked", "floating_overlay", "floating"):
                            chat_mode = "docked" if "dock" in sub_low else "floating_overlay"
                        elif "=" in sub_spec:
                            p_slot_k, p_rhs = sub_spec.split("=", 1)
                            if p_slot_k.strip().lower() in ("top", "bottom", "left", "right") and "peer" not in seg:
                                p_parsed = _parse_peer_spec(p_rhs.strip(), src_dir)
                                p_parsed.setdefault("slot", p_slot_k.strip().lower())
                                seg["peer"] = p_parsed
                    else:
                        seg["layout"] = v_unq
                elif k in ("z", "zoom"):
                    _parse_zoom_token(v_unq, seg)
                elif k in ("zoom_to", "zoom_end"):
                    _set_zoom_end(seg, float(v_unq))
                elif k == "zoom_peak":
                    seg["zoom_peak"] = round(float(v_unq), 4)
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
                elif k in ("active", "active_slot", "panel"):
                    seg["active"] = v_unq
                    seg.setdefault("slot", v_unq)
                elif k == "framing":
                    seg["framing"] = v_unq
                elif k in ("offset_y", "dy"):
                    try:
                        seg["offset_y"] = _clamp_offset_y(float(v_unq))
                    except ValueError:
                        pass
                elif k == "subs":
                    sv_low = v_unq.strip().lower()
                    if sv_low in ("off", "0", "false", "none", "no", "disable", "disabled"):
                        seg["subs_off"] = True
                        seg.pop("subs_on", None)
                    elif sv_low in ("on", "1", "true", "yes", "force", "enable", "enabled"):
                        seg["subs_on"] = True
                        seg.pop("subs_off", None)
                elif k == "label":
                    seg["label"] = v_unq
                elif k in ("tr", "trans", "transition"):
                    sep_ch = "@" if "@" in v_unq else (":" if ":" in v_unq else "@")
                    tr_parts = _split_top_level(v_unq, sep_ch)
                    seg["transition"] = tr_parts[0]
                    if len(tr_parts) > 1 and tr_parts[1]:
                        seg["transition_duration"] = round(float(tr_parts[1]), 4)
                elif k in ("hold", "hold_tail"):
                    hv_s = v_unq.lower().rstrip("s").strip()
                    if hv_s:
                        hv = float(hv_s)
                        if hv > 0:
                            seg["hold_tail"] = round(hv, 4)
                elif k == "hold_src":
                    h_parts = _split_top_level(v_unq, "@")
                    h_clip = _unquote(h_parts[0]).strip() if h_parts else ""
                    if h_clip:
                        seg["hold_src"] = _join_src_dir(src_dir, h_clip)
                    for extra in h_parts[1:]:
                        ex = _unquote(extra).strip()
                        if not ex:
                            continue
                        m_hr = _TIME_RANGE_RE.match(ex)
                        if m_hr:
                            seg["hold_span"] = [
                                round(float(m_hr.group(1)), 4),
                                round(float(m_hr.group(2)), 4),
                            ]
                        else:
                            try:
                                seg["hold_start"] = round(float(ex.rstrip("sS")), 4)
                            except ValueError:
                                pass
                elif k in ("audio_end", "aud_end"):
                    ae_s = v_unq.lower().rstrip("s").strip()
                    if ae_s:
                        try:
                            ae_v = float(ae_s)
                            if ae_v > 0:
                                seg["audio_end"] = round(ae_v, 4)
                        except ValueError:
                            pass
                elif k in ("speed", "slomo"):
                    sv_low = v_unq.strip().lower()
                    if k == "slomo" and sv_low in ("", "on", "true", "yes"):
                        seg["speed"] = 0.5
                    else:
                        sv_clean = sv_low.rstrip("x").strip()
                        if sv_clean:
                            seg["speed"] = _clamp_speed(float(sv_clean))
                elif k in ("mute", "muted"):
                    seg["mute"] = _parse_bool_token(v_unq)
                elif k == "beat":
                    seg["beat"] = _parse_bool_token(v_unq)
                elif k in ("audio", "audio_mode"):
                    av_low = v_unq.strip().lower()
                    if av_low in ("mute", "muted", "off", "none"):
                        seg["mute"] = True
                    elif av_low:
                        seg["audio_mode"] = av_low
                elif k in ("peer", "pip"):
                    seg["peer"] = _parse_peer_spec(v_unq, src_dir)
                elif k in ("vol", "volume", "gain", "gain_db"):
                    gv = _parse_db_value(v_unq)
                    seg["gain_db"] = int(gv) if gv.is_integer() else round(gv, 2)
                elif k == "loudness":
                    lv = float(_LUFS_SUFFIX_RE.sub("", v_unq.strip()).strip())
                    seg["loudness"] = int(lv) if lv.is_integer() else round(lv, 2)
                elif k == "note":
                    seg["note"] = v_unq
                continue

            # Colon-prefixed event tokens: `mark:...`, `chat:...`, `sfx:...`, `banner:...`
            if tok_low.startswith("mark:"):
                rest_m = tok[5:]
                m_parts = _split_top_level(rest_m, "@")
                m_type = _unquote(m_parts[0]) or "wrong"
                m_entry: Dict[str, Any] = {
                    "type": m_type,
                    "segment": seg_id,
                    "at": 0.0,
                    "duration": DEFAULT_MARK_DURATION,
                }
                if len(m_parts) >= 2 and m_parts[1]:
                    at_dur = m_parts[1]
                    if "/" in at_dur:
                        at_s, dur_s = at_dur.split("/", 1)
                        m_entry["at"] = round(float(at_s), 4)
                        if dur_s.strip():
                            m_entry["duration"] = round(float(dur_s), 4)
                    elif "*" in at_dur:
                        at_s, dur_s = at_dur.split("*", 1)
                        m_entry["at"] = round(float(at_s), 4)
                        if dur_s.strip():
                            m_entry["duration"] = round(float(dur_s), 4)
                    else:
                        m_entry["at"] = round(float(at_dur), 4)
                for mp in m_parts[2:]:
                    mp_u = _unquote(mp)
                    if "=" in mp_u:
                        mk, mv = mp_u.split("=", 1)
                        mk, mv = mk.strip().lower(), mv.strip()
                        if mk in ("dur", "duration"):
                            m_entry["duration"] = round(float(mv), 4)
                        elif mk in ("pos", "position"):
                            m_entry["position"] = mv
                        elif mk == "size":
                            m_entry["size"] = round(float(mv), 4)
                        elif mk == "sfx":
                            m_entry["sfx"] = None if mv.lower() == "none" else mv
                        elif mk in ("gain", "sfx_gain_db"):
                            fv = float(mv.lower().rstrip("db").strip())
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
                s_item: Dict[str, Any] = {"file": s_file, "segment": seg_id, "at": 0.0}
                if len(s_parts) >= 2 and s_parts[1]:
                    at_gain = s_parts[1]
                    if "/" in at_gain:
                        at_s, g_s = at_gain.split("/", 1)
                        s_item["at"] = round(float(at_s), 4)
                        gv = float(g_s.lower().rstrip("db").strip())
                        s_item["gain_db"] = int(gv) if gv.is_integer() else gv
                    else:
                        s_item["at"] = round(float(at_gain), 4)
                if len(s_parts) >= 3 and s_parts[2]:
                    gv = float(_unquote(s_parts[2]).lower().rstrip("db").strip())
                    s_item["gain_db"] = int(gv) if gv.is_integer() else gv
                sfx_list.append(s_item)

            elif tok_low.startswith(("banner:", "stop:")):
                rest_b = tok.split(":", 1)[1]
                b_parts = _split_top_level(rest_b, "@")
                b_txt = _unquote(b_parts[0]) if b_parts else ""
                b_at: Optional[float] = None
                b_dur: Optional[float] = None
                if len(b_parts) >= 2 and b_parts[1].strip():
                    at_spec = b_parts[1].strip()
                    if "/" in at_spec:
                        at_s, dur_s = at_spec.split("/", 1)
                        if at_s.strip():
                            b_at = round(float(at_s), 4)
                        if dur_s.strip():
                            b_dur = round(float(dur_s), 4)
                    else:
                        b_at = round(float(at_spec), 4)
                b_obj: Dict[str, Any] = {"text": b_txt}
                if b_at is not None:
                    b_obj["at"] = b_at
                if b_dur is not None:
                    b_obj["duration"] = b_dur
                banners.append(b_obj)

            elif tok_low.startswith(("card:", "flashcard:")):
                rest_fc = tok.split(":", 1)[1]
                fc_parts = _split_top_level(rest_fc, "@")
                raw_first = fc_parts[0].strip() if fc_parts else ""
                is_quoted_first = (
                    len(raw_first) >= 2 and raw_first[0] == raw_first[-1] and raw_first[0] in ('"', "'")
                )
                if not is_quoted_first and raw_first.lower() in ("none", "off", "false", "0"):
                    seg["cards_off"] = True
                    continue
                fc_norm = _parse_flashcard_text(_unquote(raw_first))
                fc_at: Optional[float] = None
                fc_dur: Optional[float] = None
                fc_pos: Optional[str] = None
                fc_style: Optional[str] = None
                for fp in fc_parts[1:]:
                    fp_u = _unquote(fp).strip()
                    if not fp_u:
                        continue
                    fp_low = fp_u.lower()
                    if "=" in fp_u:
                        fk, fv = fp_u.split("=", 1)
                        fk, fv = fk.strip().lower(), _unquote(fv.strip())
                        if fk in ("pos", "position"):
                            if fv:
                                fc_pos = fv.lower()
                        elif fk in ("style", "pack"):
                            if fv:
                                fc_style = fv.lower()
                        elif fk == "at":
                            fc_at = round(float(fv), 4)
                        elif fk in ("dur", "duration"):
                            fc_dur = round(float(fv), 4)
                    elif fp_low in ("auto", "top", "empty", "body", "bottom"):
                        fc_pos = fp_low
                    elif fp_low in ("clean", "classic"):
                        fc_style = fp_low
                    elif "/" in fp_u:
                        at_s, dur_s = fp_u.split("/", 1)
                        if at_s.strip():
                            fc_at = round(float(at_s.strip()), 4)
                        if dur_s.strip():
                            fc_dur = round(float(dur_s.strip()), 4)
                    else:
                        try:
                            fc_at = round(float(fp_u), 4)
                        except ValueError:
                            pass
                fc_obj: Dict[str, Any] = {
                    "text": fc_norm["text"],
                    "en": fc_norm["en"],
                    "native": fc_norm["native"],
                }
                if fc_at is not None:
                    fc_obj["at"] = fc_at
                if fc_dur is not None:
                    fc_obj["duration"] = fc_dur
                if fc_pos is not None:
                    fc_obj["pos"] = fc_pos
                if fc_style is not None:
                    fc_obj["style"] = fc_style
                flashcards.append(fc_obj)

        if seg.get("label") and not chat_msgs and str(seg.get("layout") or "").lower() in ("chat_bar", "texting", "chat"):
            chat_msgs.append({"speaker": str(seg.get("speaker") or "nova"), "text": str(seg["label"]), "at": 0.0})

        if chat_msgs or chat_mode:
            chat_obj: Dict[str, Any] = seg.get("chat") if isinstance(seg.get("chat"), dict) else {}
            if chat_mode:
                chat_obj["mode"] = chat_mode
            if chat_msgs:
                chat_obj["messages"] = chat_msgs
            seg["chat"] = chat_obj

        if banners:
            seg["banners"] = banners

        if flashcards:
            seg["flashcards"] = flashcards

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
    """Detect a shared parent directory across all segment `src` paths, or return empty string."""
    dirs: List[str] = []
    for seg in segments:
        src = str(seg.get("src") or "").replace("\\", "/")
        if "/" in src:
            parent_dir, _ = src.rsplit("/", 1)
            dirs.append(parent_dir)
        else:
            return ""
    if dirs and len(set(dirs)) == 1:
        return dirs[0]
    return ""


def format_edl(plan_dict: Dict[str, Any]) -> str:
    """Serialize a `plan.json` dictionary into the compact one-line-per-segment `plan.edl` DSL."""
    lines: List[str] = []

    if plan_dict.get("output"):
        lines.append(f"#output {_quote_if_needed(str(plan_dict['output']))}")
    if plan_dict.get("script"):
        lines.append(f"#script {_quote_if_needed(str(plan_dict['script']))}")
    if plan_dict.get("style"):
        lines.append(f"#style {plan_dict['style']}")

    page_val = plan_dict.get("page")
    if isinstance(page_val, dict):
        p_line = "#page scroll"
        if page_val.get("size") is not None:
            p_line += f" size={_fmt_num(float(page_val['size']))}"
        for k in ("bar", "dash", "fade"):
            if page_val.get(k) is True:
                p_line += f" {k}=on"
        for k in ("bg", "color", "wrong", "right", "highlight", "accent"):
            if page_val.get(k):
                p_line += f" {k}={page_val[k]}"
        if page_val.get("font"):
            p_line += f" font={_quote_if_needed(str(page_val['font']))}"
        lines.append(p_line)
        for key, directive_name in (("wrong_phrases", "page_wrong"), ("right_phrases", "page_right")):
            for phrase in page_val.get(key) or []:
                lines.append(f'#{directive_name} "{phrase}"')

    if "subs" in plan_dict:
        subs_val = plan_dict.get("subs")
        if (
            subs_val is None
            or subs_val is False
            or (
                isinstance(subs_val, dict)
                and (
                    subs_val.get("enabled") is False
                    or str(subs_val.get("mode", "")).strip().lower() in ("off", "none", "false", "0", "no")
                    or str(subs_val.get("style", "")).strip().lower() in ("off", "none", "false", "0", "no")
                )
            )
        ):
            lines.append("#subs off")
        elif isinstance(subs_val, dict) and subs_val.get("style"):
            s_line = f"#subs {_quote_if_needed(str(subs_val['style']))}"
            if subs_val.get("y") is not None:
                s_line += f" y={_fmt_num(float(subs_val['y']))}"
            if subs_val.get("adapt") is False:
                s_line += " adapt=off"
            elif subs_val.get("adapt") is True:
                s_line += " adapt=on"
            if subs_val.get("size") is not None:
                s_line += f" size={_fmt_num(float(subs_val['size']))}"
            if subs_val.get("split_y") is not None:
                s_line += f" split_y={_fmt_num(float(subs_val['split_y']))}"
            if subs_val.get("font"):
                s_line += f" font={_quote_if_needed(str(subs_val['font']))}"
            if subs_val.get("with_cards") is True:
                s_line += " with_cards=on"
            elif subs_val.get("with_cards") is False:
                s_line += " with_cards=off"
            lines.append(s_line)

    if plan_dict.get("cards_off") is True:
        lines.append("#cards off")
    card_style_val = plan_dict.get("card_style") or (plan_dict.get("canvas") or {}).get("card_style")
    if card_style_val:
        lines.append(f"#card_style {_quote_if_needed(str(card_style_val))}")

    segs = plan_dict.get("segments") or []
    src_dir = str(plan_dict.get("src_dir") or plan_dict.get("source") or _detect_common_src_dir(segs))
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

    mht_val = canvas.get("max_hook_transitions")
    if mht_val is None:
        mht_val = plan_dict.get("max_hook_transitions")
    if mht_val is not None:
        canvas_tokens.append(f"max_hook_transitions={int(float(mht_val))}")

    for ck, cv in canvas.items():
        if ck not in (
            "width",
            "height",
            "fps",
            "preset",
            "mark_pack",
            "sfx_pack",
            "card_style",
            "card_pack",
            "cards",
            "max_hook_transitions",
            "gain_db",
            "music",
            "music_gain_db",
            "music_hook_db",
            "music_duck",
        ):
            canvas_tokens.append(f"{ck}={_quote_if_needed(str(cv))}")

    if canvas_tokens:
        lines.append(f"#canvas {' '.join(canvas_tokens)}")

    audio_tokens: List[str] = []
    if audio.get("gain_db") is not None:
        gv = _parse_db_value(str(audio["gain_db"]))
        sign = "+" if gv > 0 else ""
        audio_tokens.append(f"gain_db={sign}{_fmt_num(gv)}dB")
    if audio.get("music"):
        audio_tokens.append(f"music={_quote_if_needed(str(audio['music']))}")
    if audio.get("music_gain_db") is not None:
        mgv = _parse_db_value(str(audio["music_gain_db"]))
        sign = "+" if mgv > 0 else ""
        audio_tokens.append(f"music_gain_db={sign}{_fmt_num(mgv)}dB")
    if audio.get("music_hook_db") is not None:
        mhv = _parse_db_value(str(audio["music_hook_db"]))
        sign = "+" if mhv > 0 else ""
        audio_tokens.append(f"music_hook_db={sign}{_fmt_num(mhv)}dB")
    if audio.get("music_duck") is not None:
        audio_tokens.append(f"music_duck={'on' if audio['music_duck'] else 'off'}")
    for ak, av in audio.items():
        if ak not in ("loudness", "gain_db", "music", "music_gain_db", "music_hook_db", "music_duck") and av is not None:
            audio_tokens.append(f"{ak}={_quote_if_needed(str(av))}")
    if audio_tokens:
        lines.append(f"#audio {' '.join(audio_tokens)}")

    speakers = plan_dict.get("speakers") or {}
    if isinstance(speakers, dict) and speakers:
        if any(isinstance(v, dict) for v in speakers.values()):
            lines.append(f"#speakers {json.dumps(speakers, ensure_ascii=False)}")
        else:
            sp_toks = [f"{k}={_quote_if_needed(str(v))}" for k, v in speakers.items()]
            lines.append(f"#speakers {' '.join(sp_toks)}")

    if lines:
        lines.append("")

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
        if src_dir:
            norm_base = src_dir.replace("\\", "/").rstrip("/")
            if norm.startswith(norm_base + "/"):
                return norm[len(norm_base) + 1 :]
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
        zt_raw = s.get("zoom_to") if s.get("zoom_to") is not None else s.get("zoom_end")
        has_zt = zt_raw is not None
        z_style = str(s.get("zoom_style") or "").strip().lower()
        z0f = float(s.get("zoom") if has_z else 1.0)
        z1f = float(zt_raw if has_zt else z0f)
        z_tail = ""
        if s.get("zoom_at") is not None:
            z_tail = f"@{_fmt_num(s['zoom_at'])}" + (
                f"/{_fmt_num(s['zoom_dur'])}" if s.get("zoom_dur") is not None else ""
            )
        elif s.get("zoom_dur") is not None and z_style in ("sudden_in", "sudden_out", "flash_zoom", "bell"):
            _def_dur = {"sudden_in": 0.18, "sudden_out": 0.20, "flash_zoom": 0.16}.get(z_style)
            if _def_dur is None or abs(float(s["zoom_dur"]) - _def_dur) > 1e-6:
                z_tail = f"@/{_fmt_num(s['zoom_dur'])}"
        if z_style == "bell" or s.get("zoom_peak") is not None:
            z0_b = float(s.get("zoom") if has_z else 1.2)
            z1_b = float(zt_raw if has_zt else 1.0)
            zp_b = float(s.get("zoom_peak") if s.get("zoom_peak") is not None else max(1.55, z0_b, z1_b))
            toks.append(f"z=bell:{_fmt_num(z0_b)}->{_fmt_num(zp_b)}->{_fmt_num(z1_b)}{z_tail}")
        elif z_style in ("sudden_in", "flash_zoom"):
            if has_zt and abs(z0f - 1.0) < 1e-6:
                toks.append(f"z={z_style}:{_fmt_num(z1f)}{z_tail}")
            elif has_z and has_zt:
                toks.append(f"z={z_style}:{_fmt_num(z0f)}->{_fmt_num(z1f)}{z_tail}")
            elif has_zt:
                toks.append(f"z={z_style}:{_fmt_num(z1f)}{z_tail}")
            elif has_z:
                toks.append(f"z={z_style}:{_fmt_num(z0f)}{z_tail}")
            else:
                toks.append(f"z={z_style}{z_tail}")
        elif z_style == "sudden_out":
            if has_z and abs(z1f - 1.0) < 1e-6:
                toks.append(f"z=sudden_out:{_fmt_num(z0f)}{z_tail}")
            elif has_z and has_zt:
                toks.append(f"z=sudden_out:{_fmt_num(z0f)}->{_fmt_num(z1f)}{z_tail}")
            elif has_z:
                toks.append(f"z=sudden_out:{_fmt_num(z0f)}{z_tail}")
            else:
                toks.append(f"z=sudden_out{z_tail}")
        elif has_z and has_zt:
            toks.append(f"z={_fmt_num(z0f)}->{_fmt_num(z1f)}{z_tail}")
        elif has_zt:
            toks.append(f"z=->{_fmt_num(z1f)}{z_tail}")
        elif has_z:
            toks.append(f"z={_fmt_num(z0f)}")

        if s.get("focus") and isinstance(s["focus"], (list, tuple)) and len(s["focus"]) >= 2:
            f0_s = f"{_fmt_num(s['focus'][0])},{_fmt_num(s['focus'][1])}"
            fe = s.get("focus_end")
            if isinstance(fe, (list, tuple)) and len(fe) >= 2 and (
                abs(float(fe[0]) - float(s["focus"][0])) > 0.005
                or abs(float(fe[1]) - float(s["focus"][1])) > 0.005
            ):
                toks.append(f"focus={f0_s}->{_fmt_num(fe[0])},{_fmt_num(fe[1])}")
            else:
                toks.append(f"focus={f0_s}")

        if s.get("speaker"):
            toks.append(f"sp={_quote_if_needed(str(s['speaker']))}")

        if s.get("slot"):
            toks.append(f"slot={_quote_if_needed(str(s['slot']))}")
        elif s.get("active"):
            toks.append(f"active={_quote_if_needed(str(s['active']))}")

        if s.get("framing"):
            toks.append(f"framing={_quote_if_needed(str(s['framing']))}")

        oy_raw = s.get("offset_y")
        if oy_raw is not None and not isinstance(oy_raw, bool):
            try:
                oy_f = _clamp_offset_y(float(oy_raw))
            except (TypeError, ValueError):
                oy_f = 0.0
            if abs(oy_f) > 1e-4:
                sign = "+" if oy_f > 0 else ""
                toks.append(f"offset_y={sign}{_fmt_num(oy_f)}")

        if s.get("subs_off") is True or (isinstance(s.get("subs_off"), str) and _parse_bool_token(s["subs_off"])):
            toks.append("subs=off")
        elif s.get("subs_on") is True or (isinstance(s.get("subs_on"), str) and _parse_bool_token(s["subs_on"])):
            toks.append("subs=on")

        if s.get("transition") and s.get("transition") != "cut":
            tr_str = str(s["transition"])
            if s.get("transition_duration") is not None:
                tr_str += f"@{_fmt_num(s['transition_duration'])}"
            toks.append(f"tr={tr_str}")

        hold_raw = s.get("hold_tail") if s.get("hold_tail") is not None else s.get("hold")
        if hold_raw is not None:
            try:
                hold_f = float(hold_raw)
            except (TypeError, ValueError):
                hold_f = 0.0
            if hold_f > 0.001:
                toks.append(f"hold={_fmt_num(hold_f)}")

        if s.get("hold_src"):
            h_src = _strip_src_dir(str(s["hold_src"]))
            h_tok = f"hold_src={_quote_if_needed(h_src)}"
            h_span = s.get("hold_span")
            if isinstance(h_span, (list, tuple)) and len(h_span) >= 2:
                h_tok += f"@{_fmt_num(float(h_span[0]))}-{_fmt_num(float(h_span[1]))}"
            elif s.get("hold_start") is not None and s.get("hold_end") is not None:
                h_tok += f"@{_fmt_num(float(s['hold_start']))}-{_fmt_num(float(s['hold_end']))}"
            elif s.get("hold_start") is not None:
                h_tok += f"@{_fmt_num(float(s['hold_start']))}"
            toks.append(h_tok)

        ae_raw = s.get("audio_end")
        if ae_raw is not None and not isinstance(ae_raw, bool):
            try:
                ae_f = float(ae_raw)
            except (TypeError, ValueError):
                ae_f = 0.0
            if ae_f > 0.001:
                toks.append(f"audio_end={_fmt_num(ae_f)}")

        spd_raw = s.get("speed")
        if spd_raw is not None and not isinstance(spd_raw, bool):
            try:
                spd_f = _clamp_speed(float(str(spd_raw).strip().rstrip("xX")))
            except (TypeError, ValueError):
                spd_f = 1.0
            if abs(spd_f - 1.0) > 1e-3:
                toks.append(f"speed={_fmt_num(spd_f)}")

        if (
            s.get("mute") is True
            or (isinstance(s.get("mute"), str) and _parse_bool_token(s["mute"]))
            or s.get("muted") is True
            or str(s.get("audio_mode") or "").strip().lower() in ("mute", "muted", "off", "none")
        ):
            toks.append("mute=1")
        elif s.get("audio_mode"):
            toks.append(f"audio={_quote_if_needed(str(s['audio_mode']))}")

        if s.get("beat") is True or (isinstance(s.get("beat"), str) and _parse_bool_token(s["beat"])):
            toks.append("beat=1")

        if s.get("gain_db") is not None and float(s["gain_db"]) != 0.0:
            gv = float(s["gain_db"])
            sign = "+" if gv > 0 else ""
            toks.append(f"vol={sign}{_fmt_num(gv)}dB")

        if s.get("loudness") is not None:
            toks.append(f"loudness={_fmt_num(s['loudness'])}")

        peer = s.get("peer")
        if isinstance(peer, dict) and peer.get("src"):
            p_src = _strip_src_dir(str(peer["src"]))
            p_head = f"{peer['speaker']}:{p_src}" if peer.get("speaker") else p_src
            p_parts = [_quote_if_needed(p_head)]
            if peer.get("still_at") is not None:
                p_parts.append(_fmt_num(peer["still_at"]))
            if isinstance(peer.get("idle_span"), (list, tuple)) and len(peer["idle_span"]) >= 2:
                p_parts.append(f"{_fmt_num(peer['idle_span'][0])}-{_fmt_num(peer['idle_span'][1])}")
            if isinstance(peer.get("focus"), (list, tuple)) and len(peer["focus"]) >= 2:
                p_parts.append(f"{_fmt_num(peer['focus'][0])},{_fmt_num(peer['focus'][1])}")
            if peer.get("slot"):
                p_parts.append(str(peer["slot"]))
            if peer.get("mode") in ("still", "live"):
                p_parts.append(str(peer["mode"]))
            p_oy_raw = peer.get("offset_y")
            if p_oy_raw is not None and not isinstance(p_oy_raw, bool):
                try:
                    p_oy_f = _clamp_offset_y(float(p_oy_raw))
                except (TypeError, ValueError):
                    p_oy_f = 0.0
                if abs(p_oy_f) > 1e-4:
                    sign = "+" if p_oy_f > 0 else ""
                    p_parts.append(f"offset_y={sign}{_fmt_num(p_oy_f)}")
            toks.append(f"peer={'@'.join(p_parts)}")

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

        for bn in s.get("banners") or s.get("stop_saying") or []:
            if isinstance(bn, dict):
                btxt = _quote_if_needed(str(bn.get("text") or ""))
                has_at = bn.get("at") is not None
                has_dur = bn.get("duration") is not None
                if has_at or has_dur:
                    bat = _fmt_num(float(bn["at"])) if has_at else ""
                    if has_dur:
                        bat += f"/{_fmt_num(float(bn['duration']))}"
                    toks.append(f"banner:{btxt}@{bat}")
                else:
                    toks.append(f"banner:{btxt}")
            elif isinstance(bn, str):
                toks.append(f"banner:{_quote_if_needed(bn)}")

        if s.get("cards_off") is True:
            toks.append("card:none")
        else:
            for fc in s.get("flashcards") or []:
                if isinstance(fc, dict):
                    raw_fc_txt = str(fc.get("text") or "").strip()
                    if not raw_fc_txt:
                        en_s = str(fc.get("en") or "").strip()
                        nat_s = str(fc.get("native") or "").strip()
                        raw_fc_txt = f"{en_s} | {nat_s}" if (en_s and nat_s) else (en_s or nat_s)
                    if not raw_fc_txt:
                        continue
                    escaped_fc = raw_fc_txt.replace("\\", "\\\\").replace('"', '\\"')
                    fc_tok = f'card:"{escaped_fc}"'
                    has_at = fc.get("at") is not None
                    has_dur = fc.get("duration") is not None
                    if has_at or has_dur:
                        fat = _fmt_num(float(fc["at"])) if has_at else ""
                        if has_dur:
                            fat += f"/{_fmt_num(float(fc['duration']))}"
                        fc_tok += f"@{fat}"
                    pos_v = str(fc.get("pos") or "").strip()
                    if pos_v:
                        fc_tok += f"@pos={pos_v}"
                    style_v = str(fc.get("style") or "").strip()
                    if style_v:
                        fc_tok += f"@style={style_v}"
                    toks.append(fc_tok)
                elif isinstance(fc, str) and fc.strip():
                    escaped_fc = fc.strip().replace("\\", "\\\\").replace('"', '\\"')
                    toks.append(f'card:"{escaped_fc}"')

        for m in marks_by_seg.get(sid, []):
            m_type = m.get("type", "wrong")
            m_at = _fmt_num(float(m.get("at", 0.0)))
            if m.get("duration") is not None and abs(float(m["duration"]) - DEFAULT_MARK_DURATION) > 1e-6:
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


# Public aliases for backward compatibility across modules
to_edl = format_edl
dump_edl = format_edl
plan_to_edl = format_edl
from_edl = parse_edl
load_edl = parse_edl
edl_to_plan = parse_edl


def main() -> None:
    ap = argparse.ArgumentParser(description="Convert losslessly between plan.edl and plan.json.")
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
