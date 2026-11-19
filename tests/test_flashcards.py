#!/usr/bin/env python3
"""Comprehensive regression & E2E test suite for Bilingual Flashcards (Phase 2):
1. `prepare.parse_script_file` on `script_nothing.txt` (20 spoken lines, 0 phantom lines,
   0 false `wrong` marks including `"Almost!"`, all 8 flashcards normalized `en` top /
   `native` bottom, Scene 2 `Robot` + `single` takeover).
2. Format detection (`translate_drill`) with & without Notion title, `_DEFAULT_REGISTRY` sync,
   and `compact_brief` token delta (`<= +300` tokens).
3. EDL parse & lossless `format_edl()` roundtrip for `card:"Nothing | ಏನಿಲ್ಲ"@pos=auto`,
   `card:none`, `#cards off`, quote-safe `|` in `script_order.unread_plan_lines` and
   `edl_parser.parse_edl`/`format_edl`.
4. `script_order.calibrate_flashcards` auto-attaching cards across Robot + Woman beats,
   normalizing flipped bilingual order, honoring `card:none`/`#cards off`, and rescuing
   accidental `banner:"Nothing | ಏನಿಲ್ಲ"` BEFORE `calibrate_post_word_beats`.
5. Real Node `ui_generator.render_flashcards_batch` on Kannada (`ಇದೂವರಿಗೂ`, `ನನ್ನನ್ನು ನಂಬು`),
   Telugu, and Hindi (RGBA PNG, exact dimensions, non-blank interior text pixels, SHA-1 cache hit).
6. `flashcard_placer.choose_face_safe_rect` 4-tier placement (`top_empty`, compact `top_empty`/`alt_empty`,
   `body_center`/`bottom_safe`, strict 0 px overlap) + `verify.check_flashcards` strict hard-fail
   on face-shield overlap (including dual-source anti-occlusion via clean `mix.mkv`) & subtitle overlap.
7. Full synthetic FFmpeg E2E `render.py --verify --draft --square` with `split_h` + bilingual flashcard
   (`Nothing | ಏನಿಲ್ಲ`) spanning 2 cuts + `whip_left` into `single` takeover + subtitles -> exits `0`,
   `[CARDS PASS]`, `[VERIFY PASS]`, `[SUBS PASS]`, `[SQUARE] PASS`, `[PICTURE PASS]`, and Premiere
   `V7` flashcard layer verified!
"""
from __future__ import annotations

import gzip
import json
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path
from unittest import mock

from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))

import common  # noqa: E402
import edit_brief  # noqa: E402
import edit_packet  # noqa: E402
import edl_parser  # noqa: E402
import flashcard_placer  # noqa: E402
import format_library  # noqa: E402
import prepare  # noqa: E402
import script_order  # noqa: E402
import ui_generator  # noqa: E402
import verify  # noqa: E402


_INLINE_SCRIPT_NOTHING = """\
OG video: https://drive.google.com/drive/folders/11qeHV4zlZWLFRuOsVRLgFpn8s_Q43rH9


First Frames (6 images)
Format change: https://www.youtube.com/shorts/xS0F81RC_bs


Split screen with flash cards


Characters:
* Robot: Female robot
* Woman: girl in her 30s. 


Flashcard Text: Nothing | ಏನಿಲ್ಲ
Background: The woman cooking in the kitchen, occasionally looking at her baby sitting in the baby seat on the counter.
Robot: ಏನೂ ಇಲ್ಲ ಅನ್ನೋದಕ್ಕೆ English ನಲ್ಲಿ ಏನಂತಾರೆ? "Nothing." ನನ್ನ ಹಿಂದೇನೆ ಹೇಳಿ.
Woman: Nothing. Nothing!
Flashcard Text: Till now | ಇದೂವರಿಗೂ
Background: The woman cooking in the kitchen, occasionally looking at her baby sitting in the baby seat on the counter.
Robot: ಇಲ್ಲಿಯವರೆಗೆ ಅನ್ನೋದಕ್ಕೆ English ನಲ್ಲಿ ಏನಂತಾರೆ? "Till now." ನನ್ನ ಹಿಂದೇನೆ ಹೇಳಿ.
Woman: Till now! Till now!
Flashcard Text: Nobody | ಯಾರೂ ಇಲ್ಲ
Background: Bedroom or living room sofa. The woman is folding fresh laundry into a neat stack while her baby plays nearby with toys.
Robot: ಯಾರೂ ಇಲ್ಲ ಅನ್ನೋದಕ್ಕೆ English ನಲ್ಲಿ ಏನಂತಾರೆ? "Nobody." ನನ್ನ ಹಿಂದೇನೆ ಹೇಳಿ.
Woman: Nobody. Nobody!
Flashcard Text: Almost | ಬಹುತೇಕ
Background: Same bedroom or living room. The woman continues folding laundry while keeping an eye on her baby.
Robot: ಬಹುತೇಕ ಅನ್ನೋದಕ್ಕೆ English ನಲ್ಲಿ ಏನಂತಾರೆ? "Almost." ನನ್ನ ಹಿಂದೇನೆ ಹೇಳಿ.
Woman: Almost!
Flashcard Text: Not you | ನೀನಲ್ಲ
Background: Kitchen sink. The woman is rinsing fresh apples and green vegetables under a running tap and peeling them.
Robot: ನೀವಲ್ಲ ಅನ್ನೋದಕ್ಕೆ English ನಲ್ಲಿ ಏನಂತಾರೆ? "Not you." ನನ್ನ ಹಿಂದೇನೆ ಹೇಳಿ.
Woman: Not you. Not you!
Flashcard Text: ಮತ್ತೆ ಬೇಡ | Not again
Background: Kitchen sink. The woman is rinsing fresh apples and green vegetables under a running tap and peeling them.
Robot: ಮತ್ತೆ ಬೇಡ ಅನ್ನೋದಕ್ಕೆ English ನಲ್ಲಿ ಏನಂತಾರೆ? "Not again." ನನ್ನ ಹಿಂದೆ ಹೇಳಿ.
Woman: "Not again. Not again!"


Flashcard Text: ಆರಾಮಾಗಿ | Casually
Background Location: The woman is relaxing on a balcony chair, holding a traditional steel tumbler of hot filter coffee and taking a calm sip.
Robot: ಆರಾಮಾಗಿ ಅನ್ನೋದಕ್ಕೆ English ನಲ್ಲಿ ಏನಂತಾರೆ? "Casually." ನನ್ನ ಹಿಂದೆ ಹೇಳಿ.
Woman: "Casually. Casually!"
Flashcard Text: ನನ್ನನ್ನು ನಂಬು | Trust me
Background Location: The woman is relaxing on a balcony chair, holding a traditional steel tumbler of hot filter coffee and taking a calm sip.
Robot: ನನ್ನನ್ನು ನಂಬಿ ಅನ್ನೋದಕ್ಕೆ English ನಲ್ಲಿ ಏನಂತಾರೆ? "Trust me." ನನ್ನ ಹಿಂದೆ ಹೇಳಿ.
Woman: "Trust me. Trust me!"


Robot Takes over the screen
(Visual: Full screen of Robot in selfie style sitting on the sofa with the baby on her lap)


ನಿಮ್ಮ spoken English ಅನ್ನ ಪೂರ್ತಿಯಾಗಿ ಬದಲಾಯಿಸೋಕೆ ನಾನು ಬಂದಿದ್ದೀನಿ. ದಿನಾ Reels, Shorts ನೋಡಿ time ಹಾಳು ಮಾಡೋ ಬದಲು, ದಿನಕ್ಕೆ ಬರೀ ten to fifteen minutes ನನ್ನ ಜೊತೆ English ಮಾತಾಡೋದನ್ನ practice ಮಾಡಿ. ಈಗ ನಾನು ಹೇಳಿಕೊಟ್ಟ ಹಾಗೇ ನಿಮಗೆ ಕನ್ನಡದಲ್ಲೇ ಸಹಾಯ ಮಾಡ್ತೀನಿ.
ಶುರು ಮಾಡೋಕೆ, ನಿಮ್ಮ English level ಎಷ್ಟಿದೆ ಅಂತ ತಿಳಿಯೋಕೆ ನಾನು ಒಂದು initial level test ತಗೋತೀನಿ. ನಿಮ್ಮ ಅಗತ್ಯಕ್ಕೆ ತಕ್ಕ ಹಾಗೆ ಪೂರ್ತಿಯಾಗಿ ಒಂದು personalized learning plan ready ಮಾಡ್ತೀನಿ. ನಿಮಗೆ grammar ನಲ್ಲಿ ಸಹಾಯ ಬೇಕಿದ್ರೂ ಅಥವಾ ಸರಿಯಾದ tenses ಕಲಿಯಬೇಕಿದ್ರೂ, ನೀವು accurate ಆಗಿ confident ಆಗಿ ಮಾತಾಡೋ ತರ targeted activities ಕೊಡ್ತೀನಿ.
ನಾನು ನಿಮಗೆ twenty four into seven ಸಿಗ್ತೀನಿ, ಅದೂ ಯಾವುದೇ judgment ಇಲ್ಲದೆ. ನಿಮ್ಮ ತಪ್ಪುಗಳನ್ನ ನೋಡಿ ನಾನು ಯಾವತ್ತೂ ನಗಲ್ಲ, ತಕ್ಷಣ ಅದನ್ನ ಸರಿಪಡಿಸಿ ನಿಮಗೆ ಸರಿಯಾಗಿ ಅರ್ಥವಾಗೋ ಹಾಗೆ ಕನ್ನಡದಲ್ಲೇ clear ಆಗಿ explain ಮಾಡ್ತೀನಿ. ಪ್ರತಿ activity ಆದ್ಮೇಲೆ ಒಂದು score ಜೊತೆಗೆ detailed progress report ಸಿಗುತ್ತೆ, ಇದ್ರಿಂದ ನೀವು ದಿನದಿಂದ ದಿನಕ್ಕೆ ಎಷ್ಟು ಸುಧಾರಿಸ್ಕೊಳ್ತಿದ್ದೀರಿ ಅಂತ ಸುಲಭವಾಗಿ ನೋಡಬಹುದು.
ಬರೀ thirty days ನಲ್ಲಿ ನಿಮ್ಮ fluency, confidence ನಲ್ಲಿ ನೀವು ದೊಡ್ಡ ಬದಲಾವಣೆಯನ್ನ ನೋಡ್ತೀರಿ. ನಿಮ್ಮ spoken English ಇನ್ನೂ ಚೆನ್ನಾಗಿ ಮಾಡೋದು ನನ್ನ ಜವಾಬ್ದಾರಿ. ಅದಕ್ಕೆ ಈಗಲೇ ಕೆಳಗಿರೋ "Install Now" button click ಮಾಡಿ, Supernova AI download ಮಾಡ್ಕೊಂಡು ನನ್ನ ಜೊತೆ practice ಶುರು ಮಾಡಿ!
"""


def _resolve_script_nothing_path(tmp_dir: Path) -> Path:
    """Use real `scratch/flashcard_study/script_nothing.txt` if present, else write inline equivalent."""
    cands = [
        ROOT / "scratch" / "flashcard_study" / "script_nothing.txt",
        Path.home()
        / ".gemini"
        / "antigravity"
        / "brain"
        / "db94726a-3490-4717-b9ca-226118b637e9"
        / "scratch"
        / "flashcard_study"
        / "script_nothing.txt",
    ]
    for cand in cands:
        if cand.is_file():
            return cand
    fallback = tmp_dir / "script_nothing.txt"
    fallback.write_text(_INLINE_SCRIPT_NOTHING, encoding="utf-8")
    return fallback


def _make_synthetic_clip(
    out_path: Path,
    *,
    width: int = 360,
    height: int = 640,
    fps: int = 25,
    duration: float = 4.0,
    v_filter: str = "",
    freq: int = 440,
    with_audio: bool = True,
) -> Path:
    """Create a tiny fast deterministic moving synthetic clip via bundled FFmpeg."""
    base_src = f"testsrc2=size={width}x{height}:rate={fps}:duration={duration:.3f}"
    src_expr = f"{base_src},{v_filter}" if v_filter else base_src
    cmd = [
        str(common.FFMPEG),
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-f",
        "lavfi",
        "-i",
        src_expr,
    ]
    if with_audio:
        cmd.extend([
            "-f",
            "lavfi",
            "-i",
            f"sine=frequency={freq}:sample_rate=48000:duration={duration:.3f},volume=0.18",
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-ac",
            "2",
            "-b:a",
            "128k",
            "-shortest",
            str(out_path),
        ])
    else:
        cmd.extend([
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-pix_fmt",
            "yuv420p",
            str(out_path),
        ])
    subprocess.run(cmd, check=True, capture_output=True)
    return out_path


def _rect_intersect_area(a: tuple[int, int, int, int], b: tuple[int, int, int, int]) -> int:
    ix = max(0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0, min(a[3], b[3]) - max(a[1], b[1]))
    return ix * iy


class FlashcardsRegressionTests(unittest.TestCase):
    # ------------------------------------------------------------------
    # 1. Script Parsing (`script_nothing.txt`)
    # ------------------------------------------------------------------
    def test_parse_script_nothing_clean_lines_cards_and_takeover(self) -> None:
        """`prepare.parse_script_file` produces exactly 20 spoken lines, 0 phantom lines,
        0 false `wrong` marks (including `"Almost!"`), all 8 bilingual flashcards normalized
        `en` top / `native` bottom, and Scene 2 `Robot` + `single` takeover.
        """
        with tempfile.TemporaryDirectory() as tmp:
            sp = _resolve_script_nothing_path(Path(tmp))
            meta, lines = prepare.parse_script_file(sp)

        self.assertEqual(len(lines), 20, f"Expected 20 spoken lines, got {len(lines)}: {[l.get('id') for l in lines]}")

        # Zero phantom lines (Flashcard Text / Background / Robot Takes over / Visual)
        forbidden_speakers = {"flashcard text", "background", "background location", "visual"}
        for ln in lines:
            spk_low = str(ln.get("speaker") or "").strip().lower()
            self.assertNotIn(spk_low, forbidden_speakers, f"Phantom speaker leaked: {ln}")
            txt_low = str(ln.get("text") or "").strip().lower()
            self.assertFalse(txt_low.startswith("flashcard text"), f"Phantom flashcard cue: {ln}")
            self.assertFalse(txt_low.startswith("background"), f"Phantom background cue: {ln}")
            self.assertNotIn("takes over the screen", txt_low, f"Phantom takeover cue: {ln}")
            self.assertFalse(txt_low.startswith("visual:"), f"Phantom visual cue: {ln}")

        # Zero false `wrong` marks — specifically verifying "Almost!" and Robot's "Almost." prompt!
        wrong_lines = [ln for ln in lines if str(ln.get("mark") or "").strip().lower() == "wrong"]
        self.assertEqual(wrong_lines, [], f"False `wrong` marks detected: {wrong_lines}")
        almost_lines = [ln for ln in lines if "almost" in str(ln.get("text") or "").lower()]
        self.assertEqual(len(almost_lines), 2, f"Expected Robot + Woman 'Almost' lines, got {almost_lines}")
        for al in almost_lines:
            self.assertIsNone(al.get("mark"), f"'Almost' line must NOT be marked wrong: {al}")

        # All 8 flashcards normalized `en` top / `native` bottom (including 3 flipped cards!)
        expected_cards = [
            ("Nothing", "ಏನಿಲ್ಲ"),
            ("Till now", "ಇದೂವರಿಗೂ"),
            ("Nobody", "ಯಾರೂ ಇಲ್ಲ"),
            ("Almost", "ಬಹುತೇಕ"),
            ("Not you", "ನೀನಲ್ಲ"),
            ("Not again", "ಮತ್ತೆ ಬೇಡ"),
            ("Casually", "ಆರಾಮಾಗಿ"),
            ("Trust me", "ನನ್ನನ್ನು ನಂಬು"),
        ]
        meta_cards = meta.get("flashcards") or []
        self.assertEqual(len(meta_cards), 8, f"Expected 8 meta['flashcards'], got {len(meta_cards)}: {meta_cards}")
        for idx, ((exp_en, exp_nat), got_fc) in enumerate(zip(expected_cards, meta_cards), 1):
            self.assertEqual(got_fc.get("en"), exp_en, f"Card #{idx} en mismatch: {got_fc}")
            self.assertEqual(got_fc.get("native"), exp_nat, f"Card #{idx} native mismatch: {got_fc}")

        # L1..L16: 8 Robot + Woman pairs on split_h with matching normalized flashcard
        for pair_idx, (exp_en, exp_nat) in enumerate(expected_cards):
            l_robot = lines[pair_idx * 2]
            l_woman = lines[pair_idx * 2 + 1]
            self.assertEqual(l_robot.get("speaker"), "Robot")
            self.assertEqual(l_woman.get("speaker"), "Woman")
            self.assertEqual(l_robot.get("layout_hint"), "split_h")
            self.assertEqual(l_woman.get("layout_hint"), "split_h")
            for ln in (l_robot, l_woman):
                fc = ln.get("flashcard")
                self.assertIsInstance(fc, dict, f"{ln.get('id')} missing flashcard")
                self.assertEqual(fc["en"], exp_en, f"{ln.get('id')} flashcard en mismatch: {fc}")
                self.assertEqual(fc["native"], exp_nat, f"{ln.get('id')} flashcard native mismatch: {fc}")

        # L17..L20: Scene 2 full-screen Robot takeover (`single`, no flashcard)
        for ln in lines[16:]:
            self.assertEqual(ln.get("speaker"), "Robot", f"Takeover line missing Robot speaker: {ln}")
            self.assertEqual(ln.get("layout_hint"), "single", f"Takeover line not single layout: {ln}")
            self.assertIsNone(ln.get("flashcard"), f"Takeover line should clear flashcard: {ln}")
            self.assertIn("2", str(ln.get("scene") or ""), f"Takeover line should be Scene 2: {ln}")

    # ------------------------------------------------------------------
    # 2. Format Detection (`translate_drill`) & Compact Brief Token Budget
    # ------------------------------------------------------------------
    def test_format_detection_translate_drill_and_token_budget(self) -> None:
        """Both Notion-titled and title-less flashcard scripts route deterministically to
        `translate_drill`, `_DEFAULT_REGISTRY` matches `formats/registry.json`, and
        `compact_brief('translate_drill')` grows by `<= +300` tokens (`<= +1200 B`).
        """
        with tempfile.TemporaryDirectory() as tmp:
            sp = _resolve_script_nothing_path(Path(tmp))
            raw_text = sp.read_text(encoding="utf-8")
            meta, _lines = prepare.parse_script_file(sp)

        notion_title = (
            "Reel - W46 - Kannada - AI Only - Split screen with flash card - Nothing - "
            "Replication with New Script - Disha - 111s - Vidhat"
        )
        meta_with_title = {**meta, "title": notion_title}
        meta_no_title = {**meta, "title": "gdoc_1nothing_tab0"}

        # With and without Notion title, with raw_text AND meta-only
        self.assertEqual(format_library.detect_catalog_format(meta_with_title, {}, script_text=raw_text), "translate_drill")
        self.assertEqual(format_library.detect_catalog_format(meta_no_title, {}, script_text=raw_text), "translate_drill")
        self.assertEqual(format_library.detect_catalog_format(meta_with_title, {}, script_text=""), "translate_drill")
        self.assertEqual(format_library.detect_catalog_format(meta_no_title, {}, script_text=""), "translate_drill")
        self.assertEqual(edit_brief.detect_format(meta_with_title, {}, script_text=raw_text), "translate_drill")
        self.assertEqual(edit_brief.detect_format(meta_no_title, {}, script_text=raw_text), "translate_drill")

        # Verify `_DEFAULT_REGISTRY["translate_drill"]` sync with `formats/registry.json`
        def_td = next(f for f in script_order._DEFAULT_REGISTRY["formats"] if f["id"] == "translate_drill")
        for lay in ("split_h", "split_v", "single"):
            self.assertIn(lay, def_td["layouts"])
        for kind in ("split_reveal", "whip_left", "whip_right", "zoom_in", "zoom_out"):
            self.assertIn(kind, def_td["kinds"])

        # Token budget check: Phase 1 measured baseline for `compact_brief("translate_drill")` = 6,392 B
        brief_text = edit_brief.build({"format": "translate_drill", "title": notion_title}, {"segments": []})
        compact_text = edit_packet.compact_brief(brief_text)
        delta_bytes = len(compact_text.encode("utf-8")) - 6392
        self.assertLessEqual(
            delta_bytes,
            1200,
            f"compact_brief('translate_drill') grew by {delta_bytes} B ({delta_bytes / 4.0:.1f} tok > +300 tok budget)",
        )

    # ------------------------------------------------------------------
    # 3. EDL Parse, Lossless Roundtrip & Quote-Safe `|` Handling
    # ------------------------------------------------------------------
    def test_edl_parse_roundtrip_cards_and_quote_safe_pipes(self) -> None:
        """`card:"Nothing | ಏನಿಲ್ಲ"@pos=auto`, `card:none`, and `#cards off` parse & roundtrip
        losslessly via `format_edl()`, while quoted `|` never breaks `unread_plan_lines` or
        `edl_parser.parse_edl`/`format_edl` with unquoted spaced clip/peer tokens.
        """
        edl_text = (
            '#src_dir "/tmp/raw"\n'
            "#canvas 1080x1920@30\n"
            'L1 "Robot Clip.mp4" 0.04-4.10 layout=split_h z=1 sp="Robot" slot=left '
            'peer="Woman:Woman Idle.mp4"@0.00-4.06@right card:"Nothing | ಏನಿಲ್ಲ"@pos=auto\n'
            'L2 "Woman Clip.mp4" 0.12-1.85 layout=split_h z=1 sp="Woman" slot=right '
            'peer="Robot:Robot Idle.mp4"@0.00-1.73@left card:"ಮತ್ತೆ ಬೇಡ | Not again"@0.10/1.50@pos=top@style=clean\n'
            'L3 "Robot Clip.mp4" 4.20-6.00 layout=single z=1 sp="Robot" card:none\n'
        )
        parsed = edl_parser.parse_edl(edl_text)
        segs = parsed.get("segments") or []
        self.assertEqual(len(segs), 3)

        # L1 card
        fc1 = segs[0].get("flashcards") or []
        self.assertEqual(len(fc1), 1)
        self.assertEqual(fc1[0].get("en"), "Nothing")
        self.assertEqual(fc1[0].get("native"), "ಏನಿಲ್ಲ")

        # L2 flipped card normalized en top / native bottom
        fc2 = segs[1].get("flashcards") or []
        self.assertEqual(len(fc2), 1)
        self.assertEqual(fc2[0].get("en"), "Not again")
        self.assertEqual(fc2[0].get("native"), "ಮತ್ತೆ ಬೇಡ")
        self.assertAlmostEqual(float(fc2[0].get("at", 0.0)), 0.10, places=2)
        self.assertAlmostEqual(float(fc2[0].get("duration", 0.0)), 1.50, places=2)
        self.assertEqual(fc2[0].get("pos"), "top")

        # L3 card:none opt-out
        self.assertTrue(bool(segs[2].get("cards_off")))
        self.assertFalse(bool(segs[2].get("flashcards")))

        # Lossless roundtrip via format_edl -> parse_edl + quote-safe unread_plan_lines
        formatted = edl_parser.format_edl(parsed)
        self.assertIn("card:", formatted)
        self.assertIn("card:none", formatted)
        reparsed = edl_parser.parse_edl(formatted)
        self.assertEqual(len(reparsed.get("segments") or []), 3)
        self.assertEqual(reparsed["segments"][0]["flashcards"][0]["en"], "Nothing")
        self.assertEqual(reparsed["segments"][0]["flashcards"][0]["native"], "ಏನಿಲ್ಲ")
        self.assertEqual(reparsed["segments"][1]["flashcards"][0]["en"], "Not again")
        self.assertEqual(reparsed["segments"][1]["flashcards"][0]["native"], "ಮತ್ತೆ ಬೇಡ")
        self.assertTrue(bool(reparsed["segments"][2].get("cards_off")))

        with tempfile.TemporaryDirectory() as tmp:
            edl_file = Path(tmp) / "plan.edl"
            edl_file.write_text(formatted, encoding="utf-8")
            self.assertEqual(script_order.unread_plan_lines(reparsed, edl_file), [])

        # `#cards off` job-level header roundtrip
        off_edl = '#src_dir "/tmp/raw"\n#cards off\nL1 clip.mp4 0.00-1.50 z=1 sp=Robot\n'
        off_parsed = edl_parser.parse_edl(off_edl)
        self.assertTrue(bool(off_parsed.get("cards_off")))
        off_fmt = edl_parser.format_edl(off_parsed)
        self.assertIn("#cards off", off_fmt.lower())
        self.assertTrue(bool(edl_parser.parse_edl(off_fmt).get("cards_off")))

        # Quote-safe `|` and unquoted spaced clip/peer normalization in `edl_parser`
        raw_spaced = (
            '#src_dir "/tmp/raw"\n'
            'L1 Robot Clip.mp4 0.04-4.10 layout=split_h z=1 sp=Robot '
            'peer=Woman:Woman Idle.mp4@0.00-4.06@right card:"Nothing | ಏನಿಲ್ಲ"@pos=auto\n'
        )
        spaced_parsed = edl_parser.parse_edl(raw_spaced)
        self.assertEqual(len(spaced_parsed.get("segments") or []), 1)
        self.assertEqual(spaced_parsed["segments"][0]["src"], "/tmp/raw/Robot Clip.mp4")
        self.assertEqual(spaced_parsed["segments"][0]["peer"]["src"], "/tmp/raw/Woman Idle.mp4")
        self.assertEqual(spaced_parsed["segments"][0]["flashcards"][0]["en"], "Nothing")
        self.assertEqual(spaced_parsed["segments"][0]["flashcards"][0]["native"], "ಏನಿಲ್ಲ")
        norm_edl = edl_parser.format_edl(spaced_parsed)
        self.assertIn('"Robot Clip.mp4"', norm_edl, f"Failed to quote src clip with card pipe: {norm_edl}")
        self.assertIn('"Woman:Woman Idle.mp4"@0-4.06@right', norm_edl, f"Failed to quote peer with card pipe: {norm_edl}")
        self.assertIn('card:"Nothing | ಏನಿಲ್ಲ"@pos=auto', norm_edl)
        with tempfile.TemporaryDirectory() as tmp:
            spaced_file = Path(tmp) / "spaced.edl"
            spaced_file.write_text(norm_edl, encoding="utf-8")
            self.assertEqual(script_order.unread_plan_lines(edl_parser.parse_edl(spaced_file), spaced_file), [])

    # ------------------------------------------------------------------
    # 4. `calibrate_flashcards` Auto-Attachment & Banner Rescue
    # ------------------------------------------------------------------
    def test_calibrate_flashcards_attaches_and_rescues_banner_before_post_word_beats(self) -> None:
        """`calibrate_flashcards` auto-attaches scripted cards across Robot + Woman beats,
        normalizes flipped cards, honors `card:none`/`#cards off`, and rescues accidental
        `banner:"Nothing | ಏನಿಲ್ಲ"` BEFORE `calibrate_post_word_beats` can turn it into a red strike!
        """
        with tempfile.TemporaryDirectory() as tmp:
            tmp_dir = Path(tmp)
            script_path = tmp_dir / "script.txt"
            script_path.write_text(
                "Split screen with flash cards\n\n"
                "Characters:\n* Robot: AI teacher\n* Woman: Learner\n\n"
                "Flashcard Text: Nothing | ಏನಿಲ್ಲ\n"
                "Background: Kitchen\n"
                "Robot: Say Nothing.\n"
                "Woman: Nothing!\n"
                "Flashcard Text: ಮತ್ತೆ ಬೇಡ | Not again\n"
                "Background: Kitchen\n"
                "Robot: Say Not again.\n"
                "Woman: Not again!\n"
                "Robot Takes over the screen\n"
                "(Visual: Full screen of Robot)\n"
                "Download Supernova today.\n",
                encoding="utf-8",
            )

            plan = {
                "segments": [
                    # L1: missing card -> auto-attach "Nothing | ಏನಿಲ್ಲ"
                    {"id": "L1", "src": "r.mp4", "start": 0.0, "end": 2.0, "layout": "split_h"},
                    # L2: accidental banner:"Nothing | ಏನಿಲ್ಲ" -> rescue to flashcards!
                    {
                        "id": "L2",
                        "src": "w.mp4",
                        "start": 0.0,
                        "end": 1.2,
                        "layout": "split_h",
                        "banners": [{"text": "Nothing | ಏನಿಲ್ಲ", "at": 0.0}],
                    },
                    # L3: explicit opt-out via cards_off -> stay empty
                    {"id": "L3", "src": "r.mp4", "start": 2.1, "end": 3.8, "layout": "split_h", "cards_off": True},
                    # L4: flipped pre-existing flashcard -> normalize en top / native bottom
                    {
                        "id": "L4",
                        "src": "w.mp4",
                        "start": 1.3,
                        "end": 2.5,
                        "layout": "split_h",
                        "flashcards": [{"text": "ಮತ್ತೆ ಬೇಡ | Not again", "en": "ಮತ್ತೆ ಬೇಡ", "native": "Not again"}],
                    },
                    # L5: Scene 2 takeover -> no flashcard attached
                    {"id": "L5", "src": "r.mp4", "start": 4.0, "end": 6.0, "layout": "single"},
                ],
                "marks": [],
            }

            changed_fc = script_order.calibrate_flashcards(plan, script_path, tmp_dir)
            self.assertGreaterEqual(changed_fc, 3)

            # Now run `calibrate_post_word_beats` — rescued banner must NOT produce wrong mark or hold_tail!
            changed_beats = script_order.calibrate_post_word_beats(plan, script_path, tmp_dir)
            self.assertEqual(changed_beats, 0, f"Post-word beats wrongly triggered on flashcard script: {plan}")
            self.assertEqual(plan["marks"], [], f"Rescued flashcard banner leaked red wrong mark: {plan['marks']}")

            s1, s2, s3, s4, s5 = plan["segments"]
            self.assertEqual(s1["flashcards"][0]["en"], "Nothing")
            self.assertEqual(s1["flashcards"][0]["native"], "ಏನಿಲ್ಲ")
            self.assertEqual(s1["flashcards"][0]["pos"], "auto")
            self.assertEqual(s1["flashcards"][0]["style"], "clean")

            # L2 banner rescued cleanly
            self.assertFalse(s2.get("banners"), f"Banner was not removed on rescue: {s2}")
            self.assertEqual(s2["flashcards"][0]["en"], "Nothing")
            self.assertEqual(s2["flashcards"][0]["native"], "ಏನಿಲ್ಲ")
            self.assertEqual(float(s2.get("hold_tail", 0.0)), 0.0)

            # L3 respected cards_off
            self.assertFalse(s3.get("flashcards"))

            # L4 normalized flipped text
            self.assertEqual(s4["flashcards"][0]["en"], "Not again")
            self.assertEqual(s4["flashcards"][0]["native"], "ಮತ್ತೆ ಬೇಡ")
            self.assertEqual(s4["flashcards"][0]["text"], "Not again | ಮತ್ತೆ ಬೇಡ")

            # L5 takeover has no flashcard
            self.assertFalse(s5.get("flashcards"))

            # Job-wide `#cards off` returns 0 and attaches nothing
            off_plan = {
                "cards_off": True,
                "segments": [{"id": "L1", "src": "r.mp4", "start": 0.0, "end": 2.0}],
            }
            self.assertEqual(script_order.calibrate_flashcards(off_plan, script_path, tmp_dir), 0)
            self.assertFalse(off_plan["segments"][0].get("flashcards"))

    # ------------------------------------------------------------------
    # 5. Real Node `ui_generator.render_flashcards_batch` (Kannada/Telugu/Hindi)
    # ------------------------------------------------------------------
    def test_node_render_flashcards_batch_indic_scripts_and_cache_hit(self) -> None:
        """Real Node HarfBuzz/Pango renderer produces valid RGBA PNGs with exact dimensions,
        non-blank interior text pixels for Kannada, Telugu, and Hindi, and hits SHA-1 cache.
        """
        import numpy as np

        with tempfile.TemporaryDirectory() as tmp:
            tmp_dir = Path(tmp)
            specs = [
                {"en": "Till now", "native": "ಇದೂವರಿಗೂ", "width": 660, "height": 290, "style": "clean", "out_path": tmp_dir / "kn1.png"},
                {"en": "Trust me", "native": "ನನ್ನನ್ನು ನಂಬು", "width": 660, "height": 290, "style": "clean", "out_path": tmp_dir / "kn2.png"},
                {"en": "Nothing", "native": "ఏమీ లేదు", "width": 660, "height": 290, "style": "clean", "out_path": tmp_dir / "te1.png"},
                {"en": "Almost", "native": "लगभग", "width": 660, "height": 290, "style": "clean", "out_path": tmp_dir / "hi1.png"},
            ]
            paths = ui_generator.render_flashcards_batch(specs)
            self.assertEqual(len(paths), 4)

            for spec, p in zip(specs, paths):
                self.assertTrue(p.is_file() and p.stat().st_size > 1000, f"Invalid flashcard PNG: {p}")
                with Image.open(p) as im:
                    self.assertEqual(im.mode, "RGBA", f"{p.name} must be RGBA")
                    self.assertEqual(im.size, (spec["width"], spec["height"]), f"{p.name} dimension mismatch")
                    arr = np.asarray(im)

                # Non-blank opaque card region
                opaque_share = float((arr[:, :, 3] > 200).mean())
                self.assertGreater(opaque_share, 0.35, f"{p.name} unexpectedly blank (opaque={opaque_share:.2f})")

                # Non-blank interior text contrast (both top English half & bottom Indic half have distinct ink!)
                h, w = arr.shape[:2]
                top_crop = arr[int(h * 0.16) : int(h * 0.48), int(w * 0.12) : int(w * 0.88), :3].astype(np.float32)
                bot_crop = arr[int(h * 0.52) : int(h * 0.86), int(w * 0.12) : int(w * 0.88), :3].astype(np.float32)
                self.assertGreater(float(top_crop.std()), 12.0, f"{p.name} missing English top text ink")
                self.assertGreater(float(bot_crop.std()), 10.0, f"{p.name} missing Indic bottom text ink")

            # SHA-1 cache hit: re-rendering identical specs must NOT invoke Node subprocess again!
            specs_cached = [{**s, "out_path": tmp_dir / f"cached_{i}.png"} for i, s in enumerate(specs)]
            with mock.patch.object(
                ui_generator,
                "_invoke_flashcard_renderer_batch",
                side_effect=AssertionError("Expected SHA-1 cache hit without invoking Node"),
            ):
                cached_paths = ui_generator.render_flashcards_batch(specs_cached)
            self.assertEqual(len(cached_paths), 4)
            for p_orig, p_cached in zip(paths, cached_paths):
                self.assertEqual(p_orig.read_bytes(), p_cached.read_bytes())

    # ------------------------------------------------------------------
    # 6. `flashcard_placer.choose_face_safe_rect` & `verify.check_flashcards` Hard-Fail
    # ------------------------------------------------------------------
    def test_face_safe_placement_tiers_and_verify_check_flashcards_hard_fail(self) -> None:
        """`choose_face_safe_rect` picks `top_empty`, compact `top_empty`/`alt_empty`, and
        `body_center`/`bottom_safe` with 0 px shield overlap; `verify.check_flashcards` hard-fails
        on intentional face overlap (including occlusion blindness via clean `mix.mkv`) & sub overlap.
        """
        W, H = 1080, 1920

        # Case A: Open ceiling -> top_empty, 0 overlap
        shields_open = [(120, 480, 440, 860), (640, 480, 960, 860)]
        r_open = flashcard_placer.choose_face_safe_rect(W, H, shields_open, layout="split_h")
        self.assertEqual(r_open["tier"], "top_empty", f"Expected top_empty on open ceiling: {r_open}")
        box_open = (r_open["x0"], r_open["y0"], r_open["x1"], r_open["y1"])
        for sh in shields_open:
            self.assertEqual(_rect_intersect_area(box_open, sh), 0, f"Overlap on open ceiling: {r_open} vs {sh}")

        # Case B: High head -> compact top_empty or alt_empty, 0 overlap
        shields_high = [(120, 290, 440, 680), (640, 290, 960, 680)]
        r_high = flashcard_placer.choose_face_safe_rect(W, H, shields_high, layout="split_h")
        self.assertIn(r_high["tier"], ("top_empty", "alt_empty"), f"Expected top_empty/alt_empty on high head: {r_high}")
        box_high = (r_high["x0"], r_high["y0"], r_high["x1"], r_high["y1"])
        for sh in shields_high:
            self.assertEqual(_rect_intersect_area(box_high, sh), 0, f"Overlap on high head: {r_high} vs {sh}")

        # Case C: Top completely blocked by faces -> body_center or bottom_safe, 0 overlap
        shields_top_blocked = [(40, 30, 520, 620), (560, 30, 1040, 620)]
        r_blocked = flashcard_placer.choose_face_safe_rect(W, H, shields_top_blocked, layout="split_h")
        self.assertIn(
            r_blocked["tier"],
            ("body_center", "bottom_safe"),
            f"Expected body_center/bottom_safe when top blocked: {r_blocked}",
        )
        box_blocked = (r_blocked["x0"], r_blocked["y0"], r_blocked["x1"], r_blocked["y1"])
        for sh in shields_top_blocked:
            self.assertEqual(_rect_intersect_area(box_blocked, sh), 0, f"Overlap on top-blocked frame: {r_blocked} vs {sh}")

        # Case D: `verify.check_flashcards` HARD-FAIL on face overlap, dual-source occlusion, & subtitle collision
        with tempfile.TemporaryDirectory() as tmp:
            tmp_dir = Path(tmp)
            out_mp4 = _make_synthetic_clip(tmp_dir / "reel_fc_qc.mp4", width=360, height=640, fps=25, duration=1.6, with_audio=False)
            overlays_dir = tmp_dir / "overlays"
            overlays_dir.mkdir(parents=True, exist_ok=True)
            dummy_png = overlays_dir / "fc_01.png"
            Image.new("RGBA", (220, 96), (255, 255, 255, 255)).save(dummy_png)

            card_meta = {
                "canvas_w": 360,
                "canvas_h": 640,
                "cards": [
                    {
                        "index": 1,
                        "en": "Nothing",
                        "native": "ಏನಿಲ್ಲ",
                        "text": "Nothing | ಏನಿಲ್ಲ",
                        "start": 0.0,
                        "end": 1.6,
                        "x0": 70,
                        "y0": 40,
                        "x1": 290,
                        "y1": 136,
                        "w": 220,
                        "h": 96,
                        "tier": "top_empty",
                        "png": str(dummy_png),
                        "style": "clean",
                        "compact": False,
                        "seg_ids": ["L1"],
                    }
                ],
            }
            (overlays_dir / "flashcards_meta.json").write_text(json.dumps(card_meta), encoding="utf-8")
            qc_plan = {
                "canvas": {"width": 360, "height": 640, "fps": 25},
                "subs": {"mode": "off"},
                "segments": [{"id": "L1", "src": str(out_mp4), "start": 0.0, "end": 1.6, "layout": "split_h"}],
            }

            # D1: Clean shield (below card) -> PASS
            with mock.patch.object(flashcard_placer, "detect_expanded_face_shields", return_value=[(60, 200, 160, 360)]):
                ok_clean, msg_clean = verify.check_flashcards(qc_plan, out_mp4, is_square=False, plan_dir=tmp_dir)
            self.assertTrue(ok_clean, f"Expected clean flashcard to PASS: {msg_clean}")
            self.assertIn("top_empty", msg_clean)

            # D2: Overlapping face shield on rendered video -> HARD FAIL (0 tolerance)
            with mock.patch.object(flashcard_placer, "detect_expanded_face_shields", return_value=[(100, 90, 220, 240)]):
                ok_hit, msg_hit = verify.check_flashcards(qc_plan, out_mp4, is_square=False, plan_dir=tmp_dir)
            self.assertFalse(ok_hit, f"Expected overlapping face shield to HARD FAIL: {msg_hit}")
            self.assertIn("overlaps", msg_hit.lower())

            # D3: Dual-source anti-occlusion: rendered video returns [] (occluded!), clean mix.mkv has face -> HARD FAIL!
            mix_dir = tmp_dir / "_segments"
            mix_dir.mkdir(parents=True, exist_ok=True)
            mix_mkv = _make_synthetic_clip(
                mix_dir / "mix.mkv", width=360, height=640, fps=25, duration=1.6, v_filter="hue=h=120", with_audio=False
            )
            self.assertTrue(mix_mkv.is_file())
            call_idx = {"n": 0}

            def _occluded_detect(frame_bgr, layout="single"):
                # Clean mix.mkv is checked first (even calls), rendered out_mp4 second (odd calls -> [])
                idx_now = call_idx["n"]
                call_idx["n"] += 1
                return [(110, 60, 230, 180)] if (idx_now % 2 == 0) else []

            with mock.patch.object(flashcard_placer, "detect_expanded_face_shields", side_effect=_occluded_detect):
                ok_occ, msg_occ = verify.check_flashcards(qc_plan, out_mp4, is_square=False, plan_dir=tmp_dir)
            self.assertFalse(ok_occ, f"Expected dual-source clean mix.mkv to defeat occlusion blindness: {msg_occ}")
            self.assertIn("clean_mix", msg_occ)

            # D4: Subtitle cue vertical overlap -> HARD FAIL
            mix_mkv.unlink(missing_ok=True)
            subs_dir = tmp_dir / "_subs"
            subs_dir.mkdir(parents=True, exist_ok=True)
            (subs_dir / "subs_meta.json").write_text(
                json.dumps({"cues": [{"start": 0.2, "end": 1.2, "y0_px": 100, "y1_px": 160}]}),
                encoding="utf-8",
            )
            qc_plan_subs = {**qc_plan, "subs": {"style": "classic"}}
            with mock.patch.object(flashcard_placer, "detect_expanded_face_shields", return_value=[]):
                ok_sub, msg_sub = verify.check_flashcards(qc_plan_subs, out_mp4, is_square=False, plan_dir=tmp_dir)
            self.assertFalse(ok_sub, f"Expected overlapping subtitle cue to HARD FAIL: {msg_sub}")
            self.assertIn("subtitle", msg_sub.lower())

    # ------------------------------------------------------------------
    # 7. Full Synthetic FFmpeg E2E `render.py --verify --draft --square`
    # ------------------------------------------------------------------
    def test_e2e_render_verify_draft_square_flashcards_and_premiere_v7(self) -> None:
        """Full synthetic E2E `render.py --verify --draft --square` with `split_h` + bilingual
        flashcard (`Nothing | ಏನಿಲ್ಲ`) spanning 2 cuts + `whip_left` into `single` takeover
        + subtitles -> exits 0, `[CARDS PASS]`, `[VERIFY PASS]`, `[SUBS PASS]`, `[SQUARE] PASS`,
        `[PICTURE PASS]`, and Premiere `V7` flashcard layer verified!
        """
        created_caches: list[Path] = []
        with tempfile.TemporaryDirectory() as tmp:
            tmp_dir = Path(tmp)
            raw_dir = tmp_dir / "raw"
            raw_dir.mkdir()
            proj_dir = tmp_dir / "proj_fc_e2e"
            proj_dir.mkdir()

            try:
                robot_clip = _make_synthetic_clip(raw_dir / "robot.mp4", width=360, height=640, fps=25, duration=4.2, freq=440)
                woman_clip = _make_synthetic_clip(
                    raw_dir / "woman.mp4", width=360, height=640, fps=25, duration=3.0, v_filter="hue=h=90", freq=520
                )

                # Seed aligned word caches so subtitles, cuts QC, and script coverage all run cleanly
                robot_cache = common.clip_work_dir(robot_clip)
                woman_cache = common.clip_work_dir(woman_clip)
                created_caches.extend([robot_cache, woman_cache])
                robot_cache.mkdir(parents=True, exist_ok=True)
                woman_cache.mkdir(parents=True, exist_ok=True)

                (robot_cache / "hq_words.json").write_text(
                    json.dumps([
                        {"w": "Nothing", "start": 0.12, "end": 0.52},
                        {"w": "say", "start": 0.58, "end": 0.90},
                        {"w": "with", "start": 0.96, "end": 1.24},
                        {"w": "me", "start": 1.30, "end": 1.56},
                        {"w": "Download", "start": 1.92, "end": 2.38},
                        {"w": "Supernova", "start": 2.44, "end": 2.94},
                        {"w": "today", "start": 3.00, "end": 3.40},
                    ]),
                    encoding="utf-8",
                )
                (woman_cache / "hq_words.json").write_text(
                    json.dumps([
                        {"w": "Nothing", "start": 0.14, "end": 0.62},
                        {"w": "Nothing", "start": 0.72, "end": 1.22},
                    ]),
                    encoding="utf-8",
                )

                script_file = proj_dir / "script.txt"
                script_file.write_text(
                    "Split screen with flash cards\n\n"
                    "Characters:\n* Robot: Female robot\n* Woman: Learner\n\n"
                    "Flashcard Text: Nothing | ಏನಿಲ್ಲ\n"
                    "Background: Kitchen\n"
                    "Robot: Nothing say with me\n"
                    "Woman: Nothing Nothing\n\n"
                    "Robot Takes over the screen\n"
                    "(Visual: Full screen of Robot)\n\n"
                    "Download Supernova today\n",
                    encoding="utf-8",
                )

                out_mp4 = proj_dir / "reel_fc_e2e.mp4"
                plan_edl = proj_dir / "plan.edl"
                plan_edl.write_text(
                    f'#output "{out_mp4}"\n'
                    f'#script "{script_file}"\n'
                    f'#src_dir "{raw_dir}"\n'
                    "#canvas 360x640@25\n"
                    "#subs auto y=65\n"
                    'L1 "robot.mp4" 0.00-1.72 layout=split_h z=1 sp="Robot" slot=left '
                    'peer="Woman:woman.mp4"@0.00-1.72@right card:"Nothing | ಏನಿಲ್ಲ"@pos=auto\n'
                    'L2 "woman.mp4" 0.00-1.40 layout=split_h z=1 sp="Woman" slot=right '
                    'peer="Robot:robot.mp4"@0.00-1.40@left card:"Nothing | ಏನಿಲ್ಲ"@pos=auto\n'
                    'L3 "robot.mp4" 1.80-3.52 layout=single z=1.0->1.06 sp="Robot" tr=whip_left@0.32\n',
                    encoding="utf-8",
                )

                cmd = [
                    sys.executable,
                    str(ROOT / "scripts" / "render.py"),
                    str(plan_edl),
                    "--verify",
                    "--draft",
                    "--square",
                ]
                proc = subprocess.run(cmd, capture_output=True, text=True, timeout=240)
                combined_out = (proc.stdout or "") + "\n" + (proc.stderr or "")
                self.assertEqual(proc.returncode, 0, f"render.py --verify failed (rc={proc.returncode}):\n{combined_out}")

                # Verify all Deep QC PASS gates printed
                for pat, label in (
                    (r"\[CARDS\s+PASS\]", "[CARDS PASS]"),
                    (r"\[VERIFY\s+PASS\]", "[VERIFY PASS]"),
                    (r"\[SUBS\s+PASS\]", "[SUBS PASS]"),
                    (r"\[SQUARE\]\s+PASS", "[SQUARE] PASS"),
                    (r"\[PICTURE\s+PASS\]", "[PICTURE PASS]"),
                ):
                    self.assertRegex(combined_out, pat, f"Missing {label} in output:\n{combined_out}")

                # Verify 9:16 flashcard metadata + merged 2-cut span (`L1`, `L2`), and confirm
                # 1:1 Square wraps the completed 9:16 reel directly (no separate 1:1 card/sub regeneration).
                fc_meta_p = proj_dir / "overlays" / "flashcards_meta.json"
                fc_sq_meta_p = proj_dir / "overlays" / "flashcards_sq_meta.json"
                self.assertTrue(fc_meta_p.is_file(), "Missing overlays/flashcards_meta.json")
                self.assertFalse(fc_sq_meta_p.exists(), "1:1 Square must wrap completed 9:16 reel directly without flashcards_sq_meta.json")
                self.assertFalse((proj_dir / "_subs_sq").exists(), "1:1 Square must not regenerate separate _subs_sq subtitles")
                sq_mp4 = common.square_job_output(out_mp4)
                self.assertTrue(sq_mp4.is_file(), f"Missing 1:1 Square output: {sq_mp4}")
                sq_info = common.probe(sq_mp4)
                self.assertEqual((sq_info.get("width"), sq_info.get("height")), (1080, 1080))
                fc_meta = json.loads(fc_meta_p.read_text(encoding="utf-8"))
                self.assertEqual(len(fc_meta.get("cards") or []), 1, f"Expected 1 merged card across L1+L2: {fc_meta}")
                c0 = fc_meta["cards"][0]
                self.assertEqual(c0.get("en"), "Nothing")
                self.assertEqual(c0.get("native"), "ಏನಿಲ್ಲ")
                self.assertGreaterEqual(float(c0["end"]) - float(c0["start"]), 2.8, f"Card did not span both L1+L2 cuts: {c0}")
                self.assertTrue(Path(c0["png"]).is_file(), f"Missing rendered card PNG: {c0}")

                # Verify Premiere Pro XML V7 flashcard layer & well-formed .prproj
                xml_files = sorted(proj_dir.glob("*.premiere.xml"))
                prproj_files = sorted(proj_dir.glob("*.prproj"))
                self.assertTrue(xml_files, f"Missing Premiere .premiere.xml in {proj_dir}")
                self.assertTrue(prproj_files, f"Missing Premiere .prproj in {proj_dir}")

                with gzip.open(prproj_files[0], "rb") as gz:
                    ET.fromstring(gz.read())

                tree = ET.parse(xml_files[0])
                root = tree.getroot()
                v_tracks = root.findall(".//sequence/media/video/track")
                self.assertGreaterEqual(len(v_tracks), 7, f"Expected >= 7 video tracks (V7 flashcards), got {len(v_tracks)}")
                v7_clips = v_tracks[6].findall("clipitem")
                self.assertGreaterEqual(len(v7_clips), 1, f"Expected flashcard clipitem on V7 (track index 6), got {v7_clips}")
                v7_names = [(ci.findtext("name") or "") for ci in v7_clips]
                self.assertTrue(
                    any("card" in nm.lower() for nm in v7_names),
                    f"Expected flashcard clip on V7, got names: {v7_names}",
                )
            finally:
                for cdir in created_caches:
                    shutil.rmtree(cdir, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
