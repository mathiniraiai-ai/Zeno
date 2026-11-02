"""Procedural Broadcast UI & Overlay PNG Generator (`ui_generator.py`).

Uses Pillow (`PIL.Image`, `ImageDraw`, `ImageFont`, `ImageFilter`) with 2x-4x supersampling
and SHA-1 caching in `work/_ui_cache/` to procedurally generate:
  1. Top Texting / Chat Bar (`render_chat_overlay_png`) — `"floating_overlay"` & `"docked"` modes
     with multi-script font discovery (Kannada, Tamil, Telugu, Hindi, Malayalam, Latin).
  2. Mobile Video Call Chrome (`render_video_call_chrome_png` & `render_pip_mask_png`) — status bar,
     caller header, live call timer, rounded/circular PiP border (`#FFFFFF` / `#10B981`), and
     bottom call control bar.
  3. "Stop Saying" Hook Banners (`render_stop_saying_banner_png`) — crossed-out red pill banners.
  4. Custom Badge / Mark Renderer (`ensure_mark_png`) — `default`, `glossy`, `flash`, `vignette`,
     or any custom PNG in `assets/marks/`.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple, Union

from PIL import Image, ImageDraw, ImageFilter, ImageFont

from common import (
    FONTS_DIR,
    MARKS_DIR,
    OVERLAYS_DIR,
    ROOT,
    SCRIPTCUT_ROOT,
    WORK,
    _normalize_mark_type,
    resolve,
    resolve_mark_asset,
)

UI_CACHE_DIR: Path = WORK / "_ui_cache"


def _hex_to_rgba(hex_str: str, alpha: int = 255) -> Tuple[int, int, int, int]:
    h = str(hex_str or "#FFFFFF").strip().lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    if len(h) == 8:
        return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16), int(h[6:8], 16))
    return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16), alpha)


def detect_text_script(text: str) -> str:
    """Detect primary Indic or Latin script from Unicode codepoints in `text`."""
    for ch in text or "":
        cp = ord(ch)
        if 0x0C80 <= cp <= 0x0CFF:
            return "kannada"
        if 0x0B80 <= cp <= 0x0BFF:
            return "tamil"
        if 0x0C00 <= cp <= 0x0C7F:
            return "telugu"
        if 0x0900 <= cp <= 0x097F:
            return "devanagari"
        if 0x0D00 <= cp <= 0x0D7F:
            return "malayalam"
    return "latin"


_SCRIPT_FONT_CANDIDATES: Dict[str, List[str]] = {
    "kannada": [
        "/System/Library/Fonts/NotoSansKannada.ttc",
        "/System/Library/Fonts/Supplemental/Kannada MN.ttc",
        "/System/Library/Fonts/Supplemental/Kannada Sangam MN.ttc",
        "C:/Windows/Fonts/NirmalaB.ttf",
        "C:/Windows/Fonts/Nirmala.ttf",
        "C:/Windows/Fonts/tunga.ttf",
        "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
    ],
    "tamil": [
        "/System/Library/Fonts/Supplemental/Tamil MN.ttc",
        "/System/Library/Fonts/Supplemental/Tamil Sangam MN.ttc",
        "/System/Library/Fonts/Kohinoor.ttc",
        "C:/Windows/Fonts/NirmalaB.ttf",
        "C:/Windows/Fonts/Nirmala.ttf",
        "C:/Windows/Fonts/latha.ttf",
        "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
    ],
    "telugu": [
        "/System/Library/Fonts/KohinoorTelugu.ttc",
        "/System/Library/Fonts/Supplemental/Telugu MN.ttc",
        "/System/Library/Fonts/Supplemental/Telugu Sangam MN.ttc",
        "C:/Windows/Fonts/NirmalaB.ttf",
        "C:/Windows/Fonts/Nirmala.ttf",
        "C:/Windows/Fonts/gautami.ttf",
        "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
    ],
    "devanagari": [
        "/System/Library/Fonts/Kohinoor.ttc",
        "/System/Library/Fonts/Supplemental/Devanagari Sangam MN.ttc",
        "/System/Library/Fonts/Supplemental/ITFDevanagari.ttc",
        "C:/Windows/Fonts/NirmalaB.ttf",
        "C:/Windows/Fonts/Nirmala.ttf",
        "C:/Windows/Fonts/mangal.ttf",
        "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
    ],
    "malayalam": [
        "/System/Library/Fonts/Supplemental/Malayalam MN.ttc",
        "/System/Library/Fonts/Supplemental/Malayalam Sangam MN.ttc",
        "C:/Windows/Fonts/NirmalaB.ttf",
        "C:/Windows/Fonts/Nirmala.ttf",
        "C:/Windows/Fonts/kartika.ttf",
        "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
    ],
    "latin_bold": [
        str(FONTS_DIR / "Montserrat-ExtraBold.ttf"),
        str(SCRIPTCUT_ROOT / "assets" / "fonts" / "Montserrat-ExtraBold.ttf"),
        "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
        "/Library/Fonts/Arial Bold.ttf",
        "/System/Library/Fonts/HelveticaNeue.ttc",
        "/System/Library/Fonts/Helvetica.ttc",
        "C:/Windows/Fonts/arialbd.ttf",
        "C:/Windows/Fonts/segoeuib.ttf",
        "C:/Windows/Fonts/NirmalaB.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    ],
    "latin_regular": [
        "/System/Library/Fonts/HelveticaNeue.ttc",
        "/System/Library/Fonts/Helvetica.ttc",
        "/System/Library/Fonts/Supplemental/Arial.ttf",
        "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
        str(FONTS_DIR / "Montserrat-ExtraBold.ttf"),
        str(SCRIPTCUT_ROOT / "assets" / "fonts" / "Montserrat-ExtraBold.ttf"),
        "C:/Windows/Fonts/segoeui.ttf",
        "C:/Windows/Fonts/arial.ttf",
        "C:/Windows/Fonts/Nirmala.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ],
}


def load_ui_font(
    size: int,
    text: str = "",
    bold: bool = True,
    preferred_path: Optional[str] = None,
) -> ImageFont.FreeTypeFont:
    """Multi-script font discovery supporting Kannada, Tamil, Telugu, Hindi, Malayalam & Latin."""
    script = detect_text_script(text)
    candidates: List[str] = []
    if preferred_path:
        candidates.append(str(preferred_path))
        candidates.append(str(ROOT / str(preferred_path)))
        candidates.append(str(SCRIPTCUT_ROOT / str(preferred_path)))

    if script != "latin":
        candidates.extend(_SCRIPT_FONT_CANDIDATES.get(script, []))

    candidates.extend(_SCRIPT_FONT_CANDIDATES["latin_bold" if bold else "latin_regular"])
    candidates.extend(_SCRIPT_FONT_CANDIDATES["latin_bold"])

    for cand in candidates:
        if not cand:
            continue
        p = Path(cand)
        if p.is_file():
            try:
                return ImageFont.truetype(str(p), size=max(8, int(size)))
            except Exception:
                continue

    try:
        return ImageFont.load_default(size=max(8, int(size)))
    except TypeError:
        return ImageFont.load_default()


def _text_bbox_wh(font: ImageFont.FreeTypeFont, text: str) -> Tuple[int, int, int, int]:
    try:
        b = font.getbbox(text)
        return int(b[0]), int(b[1]), int(b[2] - b[0]), int(b[3] - b[1])
    except Exception:
        return 0, 0, len(text) * 12, 20


def _wrap_text(text: str, font: ImageFont.FreeTypeFont, max_w: int) -> List[str]:
    """Word-wrap `text` so every line fits within `max_w` pixels."""
    words = (text or "").strip().split()
    if not words:
        return [""]
    lines: List[str] = []
    cur = words[0]
    for w in words[1:]:
        test = f"{cur} {w}"
        _, _, tw, _ = _text_bbox_wh(font, test)
        if tw <= max_w:
            cur = test
        else:
            lines.append(cur)
            cur = w
    lines.append(cur)
    return lines


def _draw_rounded_line(
    draw: ImageDraw.ImageDraw,
    p1: Tuple[float, float],
    p2: Tuple[float, float],
    width: int,
    color: Tuple[int, int, int, int],
) -> None:
    draw.line([p1, p2], fill=color, width=width)
    r = width / 2.0
    for px, py in (p1, p2):
        draw.ellipse((px - r, py - r, px + r, py + r), fill=color)


def _draw_mini_status_badge(
    canvas: Image.Image,
    cx: int,
    cy: int,
    radius: int,
    status: str,
) -> None:
    """Draw a crisp mini ✓ (green) or ✗ (red) status badge at `(cx, cy)`."""
    norm = _normalize_mark_type(status)
    d = ImageDraw.Draw(canvas)
    ring_r = radius + max(2, radius // 5)
    d.ellipse((cx - ring_r, cy - ring_r, cx + ring_r, cy + ring_r), fill=(255, 255, 255, 255))
    fill_col = (16, 185, 129, 255) if norm == "right" else (239, 68, 68, 255)
    d.ellipse((cx - radius, cy - radius, cx + radius, cy + radius), fill=fill_col)

    sw = max(2, int(round(radius * 0.28)))
    if norm == "right":
        p1 = (cx - radius * 0.44, cy + radius * 0.04)
        p2 = (cx - radius * 0.10, cy + radius * 0.38)
        p3 = (cx + radius * 0.48, cy - radius * 0.32)
        _draw_rounded_line(d, p1, p2, sw, (255, 255, 255, 255))
        _draw_rounded_line(d, p2, p3, sw, (255, 255, 255, 255))
    else:
        sp = radius * 0.38
        _draw_rounded_line(d, (cx - sp, cy - sp), (cx + sp, cy + sp), sw, (255, 255, 255, 255))
        _draw_rounded_line(d, (cx - sp, cy + sp), (cx + sp, cy - sp), sw, (255, 255, 255, 255))


# ---------------------------------------------------------------------------
# 1. Top Texting / Chat Bar (`render_chat_overlay_png`)
# ---------------------------------------------------------------------------
def _normalize_chat_messages(messages_or_spec: Any) -> Tuple[List[Dict[str, Any]], str, str]:
    """Normalize flexible `messages` input into `(messages_list, mode, header_title)`."""
    mode = "floating_overlay"
    title = "Nova AI Coach"

    if isinstance(messages_or_spec, dict):
        mode = str(messages_or_spec.get("mode") or mode)
        title = str(messages_or_spec.get("title") or messages_or_spec.get("header") or title)
        raw_msgs = messages_or_spec.get("messages") or messages_or_spec.get("bubbles")
        if raw_msgs is None and ("text" in messages_or_spec or "message" in messages_or_spec):
            raw_msgs = [messages_or_spec]
        elif raw_msgs is None:
            raw_msgs = []
    elif isinstance(messages_or_spec, (list, tuple)):
        raw_msgs = list(messages_or_spec)
    elif isinstance(messages_or_spec, str):
        raw_msgs = [{"speaker": "user", "text": messages_or_spec}]
    else:
        raw_msgs = []

    out: List[Dict[str, Any]] = []
    for m in raw_msgs:
        if isinstance(m, str):
            out.append({"speaker": "Learner", "role": "outgoing", "text": m, "status": None, "at": 0.0})
            continue
        if not isinstance(m, dict):
            continue
        sp = str(m.get("speaker") or m.get("sender") or m.get("from") or "").strip()
        txt = str(m.get("text") or m.get("message") or "").strip()
        if not txt:
            continue
        status = m.get("status") or m.get("mark") or m.get("type")
        if not status:
            if re.search(r"\[WRONG\]", txt, re.I):
                status = "wrong"
            elif re.search(r"\[RIGHT\]|\[CORRECT\]", txt, re.I):
                status = "right"
        txt = re.sub(r"\s*\[(WRONG|RIGHT|CORRECT)\]\s*", "", txt, flags=re.I).strip()

        role = str(m.get("role") or m.get("side") or "").lower()
        if not role:
            sp_low = sp.lower()
            if any(k in sp_low for k in ("nova", "coach", "teacher", "ai", "tutor", "left", "incoming")):
                role = "incoming"
            else:
                role = "outgoing"
        out.append({
            "speaker": sp or ("Miss Nova" if role == "incoming" else "Ravi"),
            "role": role,
            "text": txt,
            "status": _normalize_mark_type(status) if status else None,
            "at": float(m.get("at") or 0.0),
        })
    return out, mode, title


def render_chat_overlay_png(
    messages: Any,
    width: int = 720,
    height: int = 1280,
    mode: Optional[str] = None,
    title: str = "Nova AI Coach",
    subtitle: str = "online",
    docked_height_frac: float = 0.22,
) -> Path:
    """Render a 2x-supersampled Top Texting / Chat Bar overlay PNG (`width x height` RGBA).

    Supports:
      - `mode="floating_overlay"`: Smooth top dark gradient scrim + frosted header pill
        (`"Nova AI Coach ● online"`) + incoming/outgoing chat bubbles with ❌ red wrong
        accent or ✅ green correction accent.
      - `mode="docked"`: Solid top strip header + chat panel (`int(height * docked_height_frac)`)
        with a crisp bottom divider, leaving the lower speaker region transparent.
    """
    norm_msgs, spec_mode, spec_title = _normalize_chat_messages(messages)
    eff_mode = (mode or spec_mode or "floating_overlay").strip().lower()
    if eff_mode in ("dock", "top_strip", "strip"):
        eff_mode = "docked"
    else:
        eff_mode = "floating_overlay"
    eff_title = title if title != "Nova AI Coach" else spec_title

    if not norm_msgs:
        norm_msgs = [{"speaker": "Miss Nova", "role": "incoming", "text": "Let's practice spoken English!", "status": None, "at": 0.0}]

    payload_key = json.dumps(
        {"msgs": norm_msgs, "w": width, "h": height, "mode": eff_mode, "title": eff_title, "sub": subtitle, "dh": docked_height_frac},
        sort_keys=True,
        ensure_ascii=False,
    )
    digest = hashlib.sha1(payload_key.encode("utf-8")).hexdigest()[:16]
    UI_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    out_path = UI_CACHE_DIR / f"chat_{eff_mode}_{digest}.png"
    if out_path.is_file() and out_path.stat().st_size > 100:
        return out_path

    S = 2  # 2x supersampling
    W, H = int(width) * S, int(height) * S
    scale_k = W / 1440.0  # normalized to 720p * 2 = 1440

    canvas = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    draw = ImageDraw.Draw(canvas)

    dock_h = int(round(H * docked_height_frac))
    if eff_mode == "docked":
        # Solid dark slate top bar with subtle vertical gradient
        for y in range(dock_h):
            t = y / float(max(1, dock_h - 1))
            r = int(15 * (1 - t) + 23 * t)
            g = int(23 * (1 - t) + 33 * t)
            b = int(42 * (1 - t) + 54 * t)
            draw.line([(0, y), (W, y)], fill=(r, g, b, 248))
        # Crisp bottom accent border
        sep_h = max(2, int(4 * scale_k))
        draw.rectangle((0, dock_h - sep_h, W, dock_h), fill=(71, 85, 105, 255))
    else:
        # Smooth top vertical gradient scrim so bubbles pop cleanly over any video
        scrim_h = int(round(H * min(0.42, 0.18 + 0.09 * len(norm_msgs))))
        for y in range(scrim_h):
            t = y / float(max(1, scrim_h - 1))
            alpha = int(round(215 * ((1.0 - t) ** 1.65)))
            if alpha > 0:
                draw.line([(0, y), (W, y)], fill=(8, 12, 24, alpha))

    # Draw Header Bar ("Nova AI Coach ● online")
    pad_x = int(round(36 * scale_k))
    hdr_y = int(round(24 * scale_k)) if eff_mode == "docked" else int(round(34 * scale_k))
    hdr_h = int(round(68 * scale_k))
    hdr_w = W - 2 * pad_x

    hdr_layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    hd = ImageDraw.Draw(hdr_layer)
    if eff_mode == "floating_overlay":
        hd.rounded_rectangle(
            (pad_x, hdr_y, pad_x + hdr_w, hdr_y + hdr_h),
            radius=hdr_h // 2,
            fill=(15, 23, 42, 210),
            outline=(148, 163, 184, 75),
            width=max(2, int(2 * scale_k)),
        )

    # Avatar circle with "N"
    av_r = int(round(hdr_h * 0.36))
    av_cx = pad_x + int(round(hdr_h * 0.56))
    av_cy = hdr_y + hdr_h // 2
    hd.ellipse((av_cx - av_r, av_cy - av_r, av_cx + av_r, av_cy + av_r), fill=(99, 102, 241, 255))
    av_font = load_ui_font(int(round(av_r * 1.15)), "N", bold=True)
    bx, by, bw, bh = _text_bbox_wh(av_font, "N")
    hd.text((av_cx - bw // 2 - bx, av_cy - bh // 2 - by), "N", font=av_font, fill=(255, 255, 255, 255))

    # Title + green online indicator
    title_font = load_ui_font(int(round(28 * scale_k)), eff_title, bold=True)
    sub_font = load_ui_font(int(round(22 * scale_k)), subtitle, bold=False)
    tx0 = av_cx + av_r + int(round(18 * scale_k))
    _, ty_off, tw_title, th_title = _text_bbox_wh(title_font, eff_title)
    hd.text((tx0, av_cy - th_title // 2 - ty_off), eff_title, font=title_font, fill=(248, 250, 252, 255))

    dot_cx = tx0 + tw_title + int(round(24 * scale_k))
    dot_r = max(4, int(round(7 * scale_k)))
    hd.ellipse((dot_cx - dot_r, av_cy - dot_r, dot_cx + dot_r, av_cy + dot_r), fill=(16, 185, 129, 255))
    _, sy_off, _, sh_sub = _text_bbox_wh(sub_font, subtitle)
    hd.text(
        (dot_cx + dot_r + int(round(10 * scale_k)), av_cy - sh_sub // 2 - sy_off),
        subtitle,
        font=sub_font,
        fill=(52, 211, 153, 255),
    )

    canvas = Image.alpha_composite(canvas, hdr_layer)

    # Render Chat Bubbles
    cursor_y = hdr_y + hdr_h + int(round(20 * scale_k))
    max_bubble_w = int(round(W * 0.76))
    bubble_pad_x = int(round(28 * scale_k))
    bubble_pad_y = int(round(18 * scale_k))
    line_gap = int(round(8 * scale_k))

    for msg in norm_msgs[-3:]:  # show up to 3 most recent turns so top area stays clean
        txt = msg["text"]
        role = msg["role"]
        status = msg["status"]
        speaker = msg["speaker"]

        msg_font = load_ui_font(int(round(32 * scale_k)), txt, bold=True)
        lbl_font = load_ui_font(int(round(20 * scale_k)), speaker, bold=True)

        wrapped = _wrap_text(txt, msg_font, max_bubble_w - 2 * bubble_pad_x)
        line_dims = [_text_bbox_wh(msg_font, ln) for ln in wrapped]
        text_w = max((d[2] for d in line_dims), default=80)
        text_h = sum(d[3] for d in line_dims) + max(0, len(wrapped) - 1) * line_gap

        _, _, lbl_w, lbl_h = _text_bbox_wh(lbl_font, speaker)
        bw = max(text_w, lbl_w) + 2 * bubble_pad_x + (int(round(34 * scale_k)) if status else 0)
        bh = lbl_h + int(round(8 * scale_k)) + text_h + 2 * bubble_pad_y

        if role == "incoming":
            bx0 = pad_x
            bg_fill = (30, 41, 59, 232)
            border_col = (100, 116, 139, 130)
            lbl_col = (165, 180, 252, 255)
        else:
            bx0 = W - pad_x - bw
            if status == "wrong":
                bg_fill = (69, 10, 18, 236)
                border_col = (239, 68, 68, 245)
                lbl_col = (252, 165, 165, 255)
            elif status == "right":
                bg_fill = (6, 58, 43, 236)
                border_col = (16, 185, 129, 245)
                lbl_col = (110, 231, 183, 255)
            else:
                bg_fill = (49, 46, 129, 234)
                border_col = (129, 140, 248, 180)
                lbl_col = (199, 210, 254, 255)

        by0 = cursor_y
        bx1, by1 = bx0 + bw, by0 + bh
        rad = int(round(26 * scale_k))

        # Drop shadow
        sh = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        sd = ImageDraw.Draw(sh)
        sd.rounded_rectangle(
            (bx0, by0 + int(6 * scale_k), bx1, by1 + int(6 * scale_k)),
            radius=rad,
            fill=(0, 0, 0, 95),
        )
        sh = sh.filter(ImageFilter.GaussianBlur(radius=max(2, int(8 * scale_k))))
        canvas = Image.alpha_composite(canvas, sh)

        # Bubble body
        b_layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        bd = ImageDraw.Draw(b_layer)
        bd.rounded_rectangle(
            (bx0, by0, bx1, by1),
            radius=rad,
            fill=bg_fill,
            outline=border_col,
            width=max(2, int(round(3.5 * scale_k))),
        )

        # Speaker micro-label
        lx_off, ly_off, _, _ = _text_bbox_wh(lbl_font, speaker)
        bd.text(
            (bx0 + bubble_pad_x - lx_off, by0 + bubble_pad_y - ly_off),
            speaker,
            font=lbl_font,
            fill=lbl_col,
        )

        # Message text lines
        ty = by0 + bubble_pad_y + lbl_h + int(round(8 * scale_k))
        for ln, (ox, oy, lw, lh) in zip(wrapped, line_dims):
            bd.text(
                (bx0 + bubble_pad_x - ox, ty - oy),
                ln,
                font=msg_font,
                fill=(255, 255, 255, 255),
            )
            ty += lh + line_gap

        canvas = Image.alpha_composite(canvas, b_layer)

        # Status badge (❌ wrong or ✅ right) on corner of bubble
        if status in ("wrong", "right"):
            badge_r = int(round(22 * scale_k))
            badge_cx = bx0 + int(round(6 * scale_k)) if role == "outgoing" else bx1 - int(round(6 * scale_k))
            badge_cy = by0 + bh // 2
            _draw_mini_status_badge(canvas, badge_cx, badge_cy, badge_r, status)

        cursor_y = by1 + int(round(16 * scale_k))

    final_img = canvas.resize((int(width), int(height)), resample=Image.Resampling.LANCZOS)
    final_img.save(out_path, format="PNG")
    return out_path


# ---------------------------------------------------------------------------
# 2. Mobile Video Call Chrome (`render_video_call_chrome_png` & `render_pip_mask_png`)
# ---------------------------------------------------------------------------
def get_video_call_geometry(
    width: int = 720,
    height: int = 1280,
    variant: str = "pip_call",
    pip_shape: str = "rounded",
    pip_position: str = "top-right",
) -> Dict[str, Any]:
    """Return exact pixel geometry for `video_call` / `pip_call` / `gmeet_grid` layouts."""
    W, H = int(width), int(height)
    scale_x = W / 720.0
    scale_y = H / 1280.0

    # Learned from Kannada 20228 / Supernova 04b/05b/09 templates
    if str(pip_shape).lower() == "circle":
        pip_d = int(round(220 * min(scale_x, scale_y)))
        pip_d += pip_d % 2
        pip_w, pip_h = pip_d, pip_d
        pip_radius = pip_d // 2
    else:
        pip_w = int(round(W * 0.29))
        pip_h = int(round(H * 0.265))
        pip_w += pip_w % 2
        pip_h += pip_h % 2
        pip_radius = int(round(24 * min(scale_x, scale_y)))

    margin_x = int(round(24 * scale_x))
    margin_top = int(round(108 * scale_y))  # sits cleanly below status bar & caller header
    if "left" in str(pip_position).lower():
        pip_x = margin_x
    else:
        pip_x = W - margin_x - pip_w
    pip_y = margin_top

    bar_w = int(round(W * 0.86))
    bar_h = int(round(88 * scale_y))
    bar_x = (W - bar_w) // 2
    bar_y = int(round(H * 0.82)) - bar_h // 2  # ~15% free space below bar

    # Google Meet 2-tile vertical grid (layout 09)
    tile_w = int(round(W * 0.92))
    tile_w += tile_w % 2
    tile_h = int(round(H * 0.34))
    tile_h += tile_h % 2
    tile_x = (W - tile_w) // 2
    tile_y0 = int(round(H * 0.09))
    tile_y1 = tile_y0 + tile_h + int(round(18 * scale_y))

    return {
        "variant": variant,
        "pip": {
            "x": pip_x,
            "y": pip_y,
            "w": pip_w,
            "h": pip_h,
            "radius": pip_radius,
            "shape": "circle" if str(pip_shape).lower() == "circle" else "rounded",
            "border_px": max(3, int(round(4 * min(scale_x, scale_y)))),
        },
        "bar": {
            "x": bar_x,
            "y": bar_y,
            "w": bar_w,
            "h": bar_h,
        },
        "tiles": [
            {"x": tile_x, "y": tile_y0, "w": tile_w, "h": tile_h, "radius": int(round(24 * min(scale_x, scale_y)))},
            {"x": tile_x, "y": tile_y1, "w": tile_w, "h": tile_h, "radius": int(round(24 * min(scale_x, scale_y)))},
        ],
    }


def render_pip_mask_png(
    width: int,
    height: int,
    radius: int = 24,
    shape: str = "rounded",
) -> Path:
    """Generate a 4x-supersampled greyscale (`L`) rounded-rectangle or circular alpha mask
    for FFmpeg `alphamerge`. Cached in `work/_ui_cache/`.
    """
    w, h = max(16, int(width)), max(16, int(height))
    shape_norm = "circle" if str(shape).lower() == "circle" else "rounded"
    rad = min(w, h) // 2 if shape_norm == "circle" else max(0, int(radius))

    key = f"pip_mask|{w}|{h}|{rad}|{shape_norm}"
    digest = hashlib.sha1(key.encode("utf-8")).hexdigest()[:14]
    UI_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    out_path = UI_CACHE_DIR / f"pip_mask_{w}x{h}_{digest}.png"
    if out_path.is_file() and out_path.stat().st_size > 50:
        return out_path

    S = 4
    big_w, big_h, big_r = w * S, h * S, rad * S
    mask = Image.new("L", (big_w, big_h), 0)
    d = ImageDraw.Draw(mask)
    if shape_norm == "circle":
        d.ellipse((2, 2, big_w - 3, big_h - 3), fill=255)
    else:
        d.rounded_rectangle((2, 2, big_w - 3, big_h - 3), radius=big_r, fill=255)

    final_mask = mask.resize((w, h), resample=Image.Resampling.LANCZOS)
    final_mask.save(out_path, format="PNG")
    return out_path


def _draw_procedural_call_bar(
    canvas: Image.Image,
    bx: int,
    by: int,
    bw: int,
    bh: int,
    scale_k: float,
) -> None:
    """Draw a sleek dark frosted mobile video-call control bar with Mic, Cam, Speaker, More, and Red End-Call buttons."""
    layer = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)

    # Soft shadow under call bar
    sh = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    sd = ImageDraw.Draw(sh)
    sd.rounded_rectangle(
        (bx, by + int(8 * scale_k), bx + bw, by + bh + int(8 * scale_k)),
        radius=bh // 2,
        fill=(0, 0, 0, 120),
    )
    sh = sh.filter(ImageFilter.GaussianBlur(radius=max(2, int(10 * scale_k))))
    canvas.alpha_composite(sh)

    # Pill background
    d.rounded_rectangle(
        (bx, by, bx + bw, by + bh),
        radius=bh // 2,
        fill=(28, 30, 36, 238),
        outline=(255, 255, 255, 42),
        width=max(2, int(2 * scale_k)),
    )

    # 5 buttons: Camera, Mic, Speaker, More, Red Hang-Up
    n_btns = 5
    btn_r = int(round(bh * 0.34))
    cy = by + bh // 2
    step = bw / float(n_btns + 0.6)
    start_x = bx + step * 0.8

    stroke = max(2, int(round(3.2 * scale_k)))
    white = (248, 250, 252, 255)

    for idx in range(n_btns):
        cx = int(round(start_x + idx * step))
        is_end = idx == n_btns - 1
        btn_col = (234, 67, 53, 255) if is_end else (60, 64, 67, 255)
        r = int(round(btn_r * 1.08)) if is_end else btn_r
        d.ellipse((cx - r, cy - r, cx + r, cy + r), fill=btn_col)

        if idx == 0:
            # Video camera icon
            vw, vh = int(r * 0.68), int(r * 0.48)
            vx0, vy0 = cx - int(vw * 0.62), cy - vh // 2
            d.rounded_rectangle((vx0, vy0, vx0 + vw, vy0 + vh), radius=max(2, int(4 * scale_k)), fill=white)
            tri = [
                (vx0 + vw + int(2 * scale_k), cy),
                (vx0 + vw + int(r * 0.38), cy - int(vh * 0.42)),
                (vx0 + vw + int(r * 0.38), cy + int(vh * 0.42)),
            ]
            d.polygon(tri, fill=white)
        elif idx == 1:
            # Microphone icon
            mw, mh = int(r * 0.38), int(r * 0.62)
            d.rounded_rectangle((cx - mw // 2, cy - int(r * 0.48), cx + mw // 2, cy + int(r * 0.12)), radius=mw // 2, fill=white)
            arc_r = int(r * 0.36)
            d.arc((cx - arc_r, cy - int(r * 0.25), cx + arc_r, cy + int(r * 0.35)), start=0, end=180, fill=white, width=stroke)
            d.line([(cx, cy + int(r * 0.35)), (cx, cy + int(r * 0.54))], fill=white, width=stroke)
        elif idx == 2:
            # Speaker / volume icon
            pts = [
                (cx - int(r * 0.42), cy - int(r * 0.18)),
                (cx - int(r * 0.18), cy - int(r * 0.18)),
                (cx + int(r * 0.08), cy - int(r * 0.42)),
                (cx + int(r * 0.08), cy + int(r * 0.42)),
                (cx - int(r * 0.18), cy + int(r * 0.18)),
                (cx - int(r * 0.42), cy + int(r * 0.18)),
            ]
            d.polygon(pts, fill=white)
            wave_r = int(r * 0.38)
            d.arc((cx - wave_r // 2, cy - wave_r, cx + wave_r, cy + wave_r), start=-45, end=45, fill=white, width=stroke)
        elif idx == 3:
            # 3 dots ("more") icon
            dot_r = max(2, int(round(r * 0.11)))
            for dx in (-int(r * 0.36), 0, int(r * 0.36)):
                d.ellipse((cx + dx - dot_r, cy - dot_r, cx + dx + dot_r, cy + dot_r), fill=white)
        else:
            # End-call phone handset arc + downward earpieces
            pw = int(r * 0.92)
            ph = int(r * 0.42)
            d.arc((cx - pw // 2, cy - ph // 2, cx + pw // 2, cy + ph), start=200, end=340, fill=white, width=max(3, int(stroke * 1.4)))

    canvas.alpha_composite(layer)


def render_video_call_chrome_png(
    width: int = 720,
    height: int = 1280,
    caller: str = "Miss Nova",
    timer_text: str = "00:42",
    pip_rect: Optional[Dict[str, Any]] = None,
    pip_shape: str = "rounded",
    active_speaker: str = "main",
    show_bar: bool = True,
    layout_variant: str = "pip_call",
    time_of_day: str = "09:41",
) -> Path:
    """Render 2x-supersampled Mobile Video Call Chrome PNG (`width x height` RGBA).

    Includes:
      - Top mobile status bar (`09:41`, cellular signal bars, Wi-Fi arcs, battery icon)
      - Caller name + live call timer pill (`00:42`)
      - Anti-aliased rounded-rectangle or circular PiP border (`#FFFFFF` or `#10B981` active)
        (or dual tile borders when `layout_variant == "gmeet_grid"`)
      - Bottom call control bar (uses `assets/overlays/video_call_bar.png` if present or
        draws a crisp procedural dark pill with Mic/Cam/Speaker/More/Red End-Call buttons).
    """
    W0, H0 = int(width), int(height)
    geom = get_video_call_geometry(W0, H0, variant=layout_variant, pip_shape=pip_shape)
    pip_info = dict(geom["pip"])
    if pip_rect:
        pip_info.update(pip_rect)

    payload_key = json.dumps(
        {
            "w": W0, "h": H0, "caller": caller, "timer": timer_text,
            "pip": pip_info, "active": active_speaker, "bar": show_bar,
            "variant": layout_variant, "tod": time_of_day,
        },
        sort_keys=True,
        ensure_ascii=False,
    )
    digest = hashlib.sha1(payload_key.encode("utf-8")).hexdigest()[:16]
    UI_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    out_path = UI_CACHE_DIR / f"vcall_chrome_{digest}.png"
    if out_path.is_file() and out_path.stat().st_size > 100:
        return out_path

    S = 2
    W, H = W0 * S, H0 * S
    scale_k = W / 1440.0

    canvas = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    draw = ImageDraw.Draw(canvas)

    # 1. Subtle top status gradient scrim & bottom bar gradient scrim
    top_scrim_h = int(round(H * 0.12))
    for y in range(top_scrim_h):
        t = y / float(max(1, top_scrim_h - 1))
        a = int(round(145 * ((1.0 - t) ** 1.6)))
        if a > 0:
            draw.line([(0, y), (W, y)], fill=(0, 0, 0, a))

    # 2. Top mobile status bar ("09:41", signal bars, Wi-Fi, battery)
    pad_x = int(round(42 * scale_k))
    status_y = int(round(22 * scale_k))
    tod_font = load_ui_font(int(round(26 * scale_k)), time_of_day, bold=True)
    draw.text((pad_x, status_y), time_of_day, font=tod_font, fill=(255, 255, 255, 245))

    # Battery icon on top-right
    bat_w, bat_h = int(round(44 * scale_k)), int(round(22 * scale_k))
    bat_x = W - pad_x - bat_w
    bat_y = status_y + int(round(4 * scale_k))
    draw.rounded_rectangle(
        (bat_x, bat_y, bat_x + bat_w, bat_y + bat_h),
        radius=max(2, int(5 * scale_k)),
        outline=(255, 255, 255, 230),
        width=max(2, int(2.5 * scale_k)),
    )
    draw.rectangle(
        (bat_x + int(4 * scale_k), bat_y + int(4 * scale_k), bat_x + int(bat_w * 0.82), bat_y + bat_h - int(4 * scale_k)),
        fill=(255, 255, 255, 245),
    )
    draw.rectangle(
        (bat_x + bat_w + 1, bat_y + int(bat_h * 0.28), bat_x + bat_w + int(4 * scale_k), bat_y + int(bat_h * 0.72)),
        fill=(255, 255, 255, 230),
    )

    # Signal bars to the left of battery
    sig_x = bat_x - int(round(68 * scale_k))
    for b_idx in range(4):
        bh_bar = int(round((8 + b_idx * 4) * scale_k))
        bx_bar = sig_x + b_idx * int(round(11 * scale_k))
        by_bar = bat_y + bat_h - bh_bar
        draw.rounded_rectangle(
            (bx_bar, by_bar, bx_bar + int(round(6 * scale_k)), bat_y + bat_h),
            radius=max(1, int(2 * scale_k)),
            fill=(255, 255, 255, 245),
        )

    # 3. Caller header + live timer pill on top-left (below status bar)
    hdr_y = status_y + int(round(44 * scale_k))
    caller_font = load_ui_font(int(round(28 * scale_k)), caller, bold=True)
    timer_font = load_ui_font(int(round(22 * scale_k)), timer_text, bold=False)
    draw.text((pad_x, hdr_y), caller, font=caller_font, fill=(255, 255, 255, 250))

    _, _, cw, ch = _text_bbox_wh(caller_font, caller)
    timer_y = hdr_y + ch + int(round(8 * scale_k))
    dot_r = max(4, int(round(6 * scale_k)))
    draw.ellipse((pad_x, timer_y + int(4 * scale_k), pad_x + 2 * dot_r, timer_y + int(4 * scale_k) + 2 * dot_r), fill=(16, 185, 129, 255))
    draw.text((pad_x + 2 * dot_r + int(round(10 * scale_k)), timer_y - int(2 * scale_k)), timer_text, font=timer_font, fill=(226, 232, 240, 240))

    # 4. PiP Border (or Dual Grid Tile Borders)
    border_layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    bd = ImageDraw.Draw(border_layer)

    if str(layout_variant).lower() == "gmeet_grid":
        for t_idx, tile in enumerate(geom["tiles"]):
            tx, ty, tw, th = tile["x"] * S, tile["y"] * S, tile["w"] * S, tile["h"] * S
            trad = tile["radius"] * S
            is_act = (t_idx == 0 and active_speaker in ("top", "slot_a", "main")) or (
                t_idx == 1 and active_speaker in ("bottom", "slot_b", "pip", "peer")
            )
            b_col = (16, 185, 129, 255) if is_act else (255, 255, 255, 190)
            b_w = max(3, int(round((6 if is_act else 3.5) * scale_k)))
            bd.rounded_rectangle((tx, ty, tx + tw, ty + th), radius=trad, outline=b_col, width=b_w)
    else:
        px = int(pip_info["x"]) * S
        py = int(pip_info["y"]) * S
        pw = int(pip_info["w"]) * S
        ph = int(pip_info["h"]) * S
        prad = int(pip_info["radius"]) * S
        bpx = max(3, int(pip_info.get("border_px", 4)) * S)
        pip_is_active = str(active_speaker).lower() in ("pip", "peer", "slot_b")
        border_col = (16, 185, 129, 255) if pip_is_active else (255, 255, 255, 255)

        # Drop shadow around PiP frame
        pip_sh = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        psd = ImageDraw.Draw(pip_sh)
        if pip_info.get("shape") == "circle":
            psd.ellipse((px - bpx, py - bpx + int(6 * scale_k), px + pw + bpx, py + ph + bpx + int(6 * scale_k)), fill=(0, 0, 0, 115))
        else:
            psd.rounded_rectangle(
                (px - bpx, py - bpx + int(6 * scale_k), px + pw + bpx, py + ph + bpx + int(6 * scale_k)),
                radius=prad + bpx // 2,
                fill=(0, 0, 0, 115),
            )
        pip_sh = pip_sh.filter(ImageFilter.GaussianBlur(radius=max(2, int(10 * scale_k))))
        canvas = Image.alpha_composite(canvas, pip_sh)

        if pip_info.get("shape") == "circle":
            bd.ellipse((px - bpx // 2, py - bpx // 2, px + pw + bpx // 2, py + ph + bpx // 2), outline=border_col, width=bpx)
        else:
            bd.rounded_rectangle(
                (px - bpx // 2, py - bpx // 2, px + pw + bpx // 2, py + ph + bpx // 2),
                radius=prad,
                outline=border_col,
                width=bpx,
            )

    canvas = Image.alpha_composite(canvas, border_layer)

    # 5. Bottom Call Control Bar
    if show_bar:
        bar_info = geom["bar"]
        bx, by, bw, bh = bar_info["x"] * S, bar_info["y"] * S, bar_info["w"] * S, bar_info["h"] * S
        custom_bar_cands = [
            OVERLAYS_DIR / "video_call_bar.png",
            OVERLAYS_DIR / "kn20228_video_call_bar.png",
        ]
        bar_img_path = next((p for p in custom_bar_cands if p.is_file()), None)
        if bar_img_path is not None:
            try:
                with Image.open(bar_img_path) as raw_bar:
                    bar_rgba = raw_bar.convert("RGBA")
                    aspect = bar_rgba.height / float(max(1, bar_rgba.width))
                    scaled_bh = max(24, int(round(bw * aspect)))
                    bar_resized = bar_rgba.resize((bw, scaled_bh), resample=Image.Resampling.LANCZOS)
                    canvas.alpha_composite(bar_resized, dest=(bx, by + (bh - scaled_bh) // 2))
            except Exception:
                _draw_procedural_call_bar(canvas, bx, by, bw, bh, scale_k)
        else:
            _draw_procedural_call_bar(canvas, bx, by, bw, bh, scale_k)

    final_img = canvas.resize((W0, H0), resample=Image.Resampling.LANCZOS)
    final_img.save(out_path, format="PNG")
    return out_path


# ---------------------------------------------------------------------------
# 3. "Stop Saying" Hook Banners (`render_stop_saying_banner_png`)
# ---------------------------------------------------------------------------
def _red_pill_gradient(w: int, h: int, stops: Sequence[Sequence[int]]) -> Image.Image:
    img = Image.new("RGBA", (w, h))
    px = img.load()
    n = len(stops) - 1
    for x in range(w):
        f = x / float(max(1, w - 1)) * n
        i = min(int(f), n - 1)
        t = f - i
        c = tuple(int(round(stops[i][k] * (1 - t) + stops[i + 1][k] * t)) for k in range(3))
        for y in range(h):
            sheen = int(8 * (y / float(max(1, h - 1))))
            px[x, y] = (min(255, c[0] + sheen), c[1], c[2], 255)
    return img


def render_stop_saying_banner_png(
    text: str,
    font_px: int = 54,
    box_h: int = 76,
    pad_x: int = 38,
    radius: int = 8,
    strike_overshoot: int = 36,
    strike_px: int = 6,
    uppercase: bool = True,
    with_strike: bool = True,
    dashed_strike: bool = False,
) -> Path:
    """Render a crossed-out red pill "Stop Saying" hook banner PNG (21132 / 20228 style),
    supporting both un-struck (`with_strike=False` for initial pop-in) and struck-through
    (`with_strike=True`, solid or dashed white line across the pill) states, cached in `work/_ui_cache/`.
    """
    label = re.sub(r"\s+", " ", (text or "").strip().strip("\"“”'.,!?"))
    if not label:
        label = "STOP SAYING THIS"
    if uppercase:
        label = label.upper()

    style_key = f"{label}|{font_px}|{box_h}|{pad_x}|{radius}|{strike_overshoot}|{strike_px}|{with_strike}|{dashed_strike}"
    digest = hashlib.sha1(style_key.encode("utf-8")).hexdigest()[:14]
    UI_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    out_path = UI_CACHE_DIR / f"stop_say_{digest}.png"
    if out_path.is_file() and out_path.stat().st_size > 80:
        return out_path

    font = load_ui_font(int(font_px), label, bold=True)
    left, top, bw, bh = _text_bbox_wh(font, label)
    box_w = max(80, bw + 2 * int(pad_x))
    over = int(strike_overshoot)
    canvas_w = box_w + 2 * over
    h = int(box_h)

    canvas = Image.new("RGBA", (canvas_w, h), (0, 0, 0, 0))
    box = _red_pill_gradient(box_w, h, [[150, 0, 0], [185, 0, 0], [62, 0, 0]])
    mask = Image.new("L", (box_w, h), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, box_w - 1, h - 1), radius=int(radius), fill=255)
    canvas.paste(box, (over, 0), mask)

    d = ImageDraw.Draw(canvas)
    tx = over + int(pad_x) - left
    ty = (h - bh) // 2 - top
    d.text((tx, ty), label, font=font, fill=(255, 255, 255, 255))

    if with_strike:
        cy = h // 2
        sp = max(2, int(strike_px))
        if dashed_strike:
            dash_w, gap_w = 28, 12
            x_cur = 0
            while x_cur < canvas_w:
                x_end = min(canvas_w - 1, x_cur + dash_w)
                d.rectangle((x_cur, cy - sp // 2, x_end, cy + sp // 2), fill=(255, 255, 255, 255))
                x_cur += dash_w + gap_w
        else:
            d.rectangle((0, cy - sp // 2, canvas_w - 1, cy + sp // 2), fill=(255, 255, 255, 255))

    canvas.save(out_path, format="PNG")
    return out_path


# ---------------------------------------------------------------------------
# 4. Custom Badge / Mark Renderer (`ensure_mark_png`)
# ---------------------------------------------------------------------------
def _generate_default_badge_png(out_path: Path, mark_type: str, size: int = 360) -> Path:
    """Generate the classic 4x-supersampled flat circle ✓ / ✗ badge (`make_marks.py` style)."""
    S = 4
    big = int(size) * S
    img = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    sh = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    m = int(big * 0.08)
    ImageDraw.Draw(sh).ellipse((m, m + big * 0.03, big - m, big - m + big * 0.03), fill=(0, 0, 0, 110))
    img = Image.alpha_composite(img, sh.filter(ImageFilter.GaussianBlur(big * 0.03)))
    d = ImageDraw.Draw(img)
    d.ellipse((m, m, big - m, big - m), fill=(255, 255, 255, 255))
    r = int(big * 0.035)
    col = (34, 177, 76, 255) if mark_type == "right" else (226, 47, 47, 255)
    d.ellipse((m + r, m + r, big - m - r, big - m - r), fill=col)

    w = int(big * 0.085)
    if mark_type == "right":
        pts = [(big * 0.31, big * 0.52), (big * 0.45, big * 0.66), (big * 0.70, big * 0.37)]
        d.line(pts, fill="white", width=w, joint="curve")
        for p in (pts[0], pts[2]):
            d.ellipse((p[0] - w / 2, p[1] - w / 2, p[0] + w / 2, p[1] + w / 2), fill="white")
    else:
        a, b = big * 0.35, big * 0.65
        for p, q in (((a, a), (b, b)), ((a, b), (b, a))):
            _draw_rounded_line(d, p, q, w, (255, 255, 255, 255))

    out_path.parent.mkdir(parents=True, exist_ok=True)
    img.resize((int(size), int(size)), resample=Image.Resampling.LANCZOS).save(out_path, format="PNG")
    return out_path


def _generate_glossy_badge_png(out_path: Path, mark_type: str, size: int = 360) -> Path:
    """Generate a 4x-supersampled studio glossy gradient badge (`overlay_generator.py` style)."""
    S = 4
    big = int(size) * S
    cx, cy = big * 0.5, big * 0.485
    outer_r = int(big * 0.365)
    inner_r = int(big * 0.325)

    canvas = Image.new("RGBA", (big, big), (0, 0, 0, 0))

    # Drop shadow
    sh = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    s_off = int(big * 0.024)
    ImageDraw.Draw(sh).ellipse(
        (cx - outer_r, cy - outer_r + s_off, cx + outer_r, cy + outer_r + s_off),
        fill=(0, 0, 0, 125),
    )
    canvas = Image.alpha_composite(canvas, sh.filter(ImageFilter.GaussianBlur(radius=big * 0.032)))

    # Crisp white outer ring
    ring = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    ImageDraw.Draw(ring).ellipse((cx - outer_r, cy - outer_r, cx + outer_r, cy + outer_r), fill=(255, 255, 255, 255))
    canvas = Image.alpha_composite(canvas, ring)

    # Vertical gradient inner circle
    top_rgb = (16, 185, 129) if mark_type == "right" else (255, 42, 75)
    bot_rgb = (5, 150, 105) if mark_type == "right" else (200, 16, 46)
    grad = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    gd = ImageDraw.Draw(grad)
    y0, y1 = int(cy - inner_r), int(cy + inner_r)
    for y in range(y0, y1 + 1):
        t = (y - y0) / float(max(1, y1 - y0))
        r = int(top_rgb[0] * (1 - t) + bot_rgb[0] * t)
        g = int(top_rgb[1] * (1 - t) + bot_rgb[1] * t)
        b = int(top_rgb[2] * (1 - t) + bot_rgb[2] * t)
        dy = y - cy
        dx = math.sqrt(max(0.0, inner_r * inner_r - dy * dy))
        gd.line([(cx - dx, y), (cx + dx, y)], fill=(r, g, b, 255))
    canvas = Image.alpha_composite(canvas, grad)

    # Top gloss highlight arc
    gloss = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    gld = ImageDraw.Draw(gloss)
    rx, ry = int(inner_r * 0.78), int(inner_r * 0.46)
    ey = int(cy - inner_r * 0.34)
    gld.ellipse((cx - rx, ey - ry, cx + rx, ey + ry), fill=(255, 255, 255, 68))
    canvas = Image.alpha_composite(canvas, gloss.filter(ImageFilter.GaussianBlur(radius=big * 0.01)))

    # Inner glyph (✓ or ✗)
    glyph = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    g_draw = ImageDraw.Draw(glyph)
    stroke_w = int(big * 0.076)
    if mark_type == "right":
        p1 = (cx - inner_r * 0.44, cy + inner_r * 0.03)
        p2 = (cx - inner_r * 0.11, cy + inner_r * 0.36)
        p3 = (cx + inner_r * 0.47, cy - inner_r * 0.31)
        _draw_rounded_line(g_draw, p1, p2, stroke_w, (255, 255, 255, 255))
        _draw_rounded_line(g_draw, p2, p3, stroke_w, (255, 255, 255, 255))
    else:
        sp = inner_r * 0.42
        _draw_rounded_line(g_draw, (cx - sp, cy - sp), (cx + sp, cy + span if (span := sp) else sp), stroke_w, (255, 255, 255, 255))
        _draw_rounded_line(g_draw, (cx - sp, cy + sp), (cx + sp, cy - sp), stroke_w, (255, 255, 255, 255))
    canvas = Image.alpha_composite(canvas, glyph)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    canvas.resize((int(size), int(size)), resample=Image.Resampling.LANCZOS).save(out_path, format="PNG")
    return out_path


def _generate_flash_vignette_png(
    out_path: Path,
    mark_type: str,
    width: int = 720,
    height: int = 1280,
    intensity: float = 0.78,
) -> Path:
    """Generate a radial edge-glow flash/vignette PNG (`width x height` RGBA)."""
    w, h = int(width), int(height)
    rgb = (16, 185, 129) if mark_type == "right" else (239, 68, 68)
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    px = img.load()
    cx, cy = w * 0.5, h * 0.5
    inv_rx, inv_ry = 1.0 / max(1.0, cx), 1.0 / max(1.0, cy)

    for y in range(h):
        dy = (y - cy) * inv_ry
        dy2 = dy * dy
        for x in range(w):
            dx = (x - cx) * inv_rx
            dist = math.sqrt(dx * dx + dy2)
            # Transparent center (dist < 0.58), smooth glow ramping toward frame edges
            edge_t = max(0.0, min(1.0, (dist - 0.55) / 0.65))
            alpha = int(round(255 * intensity * (edge_t ** 1.8)))
            if alpha > 0:
                px[x, y] = (rgb[0], rgb[1], rgb[2], alpha)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    img.save(out_path, format="PNG")
    return out_path


def ensure_mark_png(
    mark_type: str = "wrong",
    style: str = "default",
    size: int = 360,
    custom_path: Optional[Union[str, Path]] = None,
    canvas_w: int = 720,
    canvas_h: int = 1280,
) -> Path:
    """Resolve or procedurally generate any mark badge / flash PNG (`default`, `glossy`,
    `flash`, `vignette`, or custom PNG path in `assets/marks/`).
    """
    norm_type = _normalize_mark_type(mark_type)
    if custom_path:
        cp = resolve(custom_path)
        if cp.is_file():
            return cp

    style_norm = str(style or "default").strip().lower()
    target = resolve_mark_asset(norm_type, style=style)
    if target.is_file() and target.stat().st_size > 100:
        return target

    # Check if sibling ScriptCut-AI has a matching pre-rendered overlay
    sibling_map = {
        ("flash", "wrong"): SCRIPTCUT_ROOT / "assets" / "overlays" / "kn20228_red_flash.png",
        ("flash", "right"): SCRIPTCUT_ROOT / "assets" / "overlays" / "kn20228_green_flash.png",
        ("kn20228", "wrong"): SCRIPTCUT_ROOT / "assets" / "overlays" / "kn20228_red_flash.png",
        ("kn20228", "right"): SCRIPTCUT_ROOT / "assets" / "overlays" / "kn20228_green_flash.png",
        ("glossy", "wrong"): SCRIPTCUT_ROOT / "assets" / "overlays" / "wrong_x.png",
        ("glossy", "right"): SCRIPTCUT_ROOT / "assets" / "overlays" / "right_check.png",
    }
    sib = sibling_map.get((style_norm, norm_type))
    if sib and sib.is_file():
        return sib

    UI_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    if style_norm in ("flash", "vignette", "kn20228", "w42"):
        intensity = 0.82 if style_norm in ("flash", "kn20228") else 0.65
        cached = UI_CACHE_DIR / f"mark_{style_norm}_{norm_type}_{canvas_w}x{canvas_h}.png"
        if not cached.is_file():
            _generate_flash_vignette_png(cached, norm_type, width=canvas_w, height=canvas_h, intensity=intensity)
        return cached

    if style_norm in ("glossy", "supernova", "studio"):
        cached = UI_CACHE_DIR / f"mark_glossy_{norm_type}_{size}.png"
        if not cached.is_file():
            _generate_glossy_badge_png(cached, norm_type, size=size)
        return cached

    # Default badge
    default_out = MARKS_DIR / f"{norm_type}.png"
    if default_out.is_file() and default_out.stat().st_size > 100:
        return default_out
    cached = UI_CACHE_DIR / f"mark_default_{norm_type}_{size}.png"
    if not cached.is_file():
        _generate_default_badge_png(cached, norm_type, size=size)
    return cached
