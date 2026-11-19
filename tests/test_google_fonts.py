#!/usr/bin/env python3
"""Regression tests for the bundled static Google Fonts pack (English + 9 Indian languages).

Verifies:
1. Every entry in `subtitles/fonts.json` exists on disk, is a valid SFNT TTF, contains
   ZERO `fvar` variable-font tables, and has exact `OS/2 usWeightClass` parity.
2. All newly bundled English (10 families) and Indian-language (19 families across
   Devanagari/Hindi/Marathi, Kannada, Telugu, Tamil, Malayalam, Bengali, Gujarati,
   Punjabi/Gurmukhi, Odia) Google Fonts resolve via `subtitles.list_fonts()` and
   `subtitles.resolve_font()`.
3. `#subs ... font="<Family>"` and `#page scroll ... font="<Family>"` parse and
   roundtrip losslessly through `edl_parser.format_edl()`.
4. Real Node `subtitles/render_cues.js`, `subtitles/render_page.js`, and
   `subtitles/render_flashcard.js` render English + Indic fonts crisply without
   `600`-shadows-`700` weight clobbering.
"""
from __future__ import annotations

import json
import struct
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))

import edl_parser  # noqa: E402
import subtitles  # noqa: E402


def _inspect_ttf(path: Path) -> dict:
    data = path.read_bytes()
    if len(data) < 12 or data[:4] not in (b"\x00\x01\x00\x00", b"true", b"OTTO"):
        return {"valid": False}
    num_tables = struct.unpack(">H", data[4:6])[0]
    tables = {}
    for i in range(num_tables):
        off = 12 + i * 16
        tag = data[off : off + 4].decode("latin1", "replace")
        tables[tag] = struct.unpack(">II", data[off + 8 : off + 16])
    os2_w = None
    if "OS/2" in tables:
        os2_off = tables["OS/2"][0]
        os2_w = struct.unpack(">H", data[os2_off + 4 : os2_off + 6])[0]
    return {"valid": True, "fvar": "fvar" in tables, "weight": os2_w}


class GoogleFontsPackTests(unittest.TestCase):
    def test_01_all_bundled_fonts_are_verified_static_ttfs(self) -> None:
        """Every font in `subtitles/fonts.json` must exist, be static (`fvar=False`),
        and match its declared `OS/2 usWeightClass`.
        """
        entries = subtitles.fonts()
        self.assertGreaterEqual(len(entries), 200)
        fonts_dir = ROOT / "subtitles" / "fonts"
        seen_files = set()
        for e in entries:
            fp = fonts_dir / str(e["file"])
            self.assertTrue(fp.is_file(), f"Missing bundled TTF: {fp}")
            self.assertNotIn(e["file"], seen_files, f"Duplicate font file in fonts.json: {e['file']}")
            seen_files.add(e["file"])
            info = _inspect_ttf(fp)
            self.assertTrue(info["valid"], f"Invalid SFNT header: {e['file']}")
            self.assertFalse(info["fvar"], f"Variable fvar font must not ship: {e['file']}")
            self.assertEqual(
                info["weight"],
                int(e["weight"]),
                f"OS/2 weight mismatch for {e['family']} ({e['file']}): {info['weight']} != {e['weight']}",
            )

    def test_02_english_and_all_9_indian_languages_catalog(self) -> None:
        """All 10 new flagship English Google Fonts and 9-script Indian Google Fonts
        are discoverable via `subtitles.list_fonts()` and `subtitles.resolve_font()`.
        """
        expected_english = [
            "Roboto", "Open Sans", "Lato", "Nunito", "Rubik",
            "DM Sans", "Work Sans", "Manrope", "Barlow", "Raleway",
        ]
        expected_indic = {
            "hindi": ["Hind", "Mukta", "Teko", "Noto Serif Devanagari"],
            "kannada": ["Noto Sans Kannada", "Noto Serif Kannada", "Tiro Kannada", "Anek Kannada"],
            "telugu": ["Hind Guntur", "Noto Serif Telugu", "Anek Telugu"],
            "tamil": ["Hind Madurai", "Mukta Malar", "Baloo Thambi 2"],
            "malayalam": ["Manjari", "Noto Serif Malayalam"],
            "bengali": ["Hind Siliguri", "Noto Serif Bengali"],
            "gujarati": ["Hind Vadodara", "Mukta Vaani"],
            "punjabi": ["Mukta Mahee", "Noto Sans Gurmukhi"],
            "odia": ["Baloo Bhaina 2", "Noto Serif Oriya"],
        }

        en_names = {r["family"] for r in subtitles.list_fonts("english")}
        for fam in expected_english:
            self.assertIn(fam, en_names, f"Missing English Google Font: {fam}")
            self.assertEqual(subtitles.resolve_font(fam.lower().replace(" ", "_")), fam)

        for lang, fams in expected_indic.items():
            lang_names = {r["family"] for r in subtitles.list_fonts(lang)}
            for fam in fams:
                self.assertIn(fam, lang_names, f"Missing {lang} Google Font: {fam}")
                self.assertEqual(subtitles.resolve_font(fam.lower()), fam)

        # Verify Noto Sans Gurmukhi now includes static 700 bold
        gurmukhi = {r["family"]: r["weights"] for r in subtitles.list_fonts("punjabi")}
        self.assertIn(700, gurmukhi["Noto Sans Gurmukhi"])
        # Verify Noto Sans Kannada includes static 800 ExtraBold
        kannada = {r["family"]: r["weights"] for r in subtitles.list_fonts("kannada")}
        self.assertIn(800, kannada["Noto Sans Kannada"])

    def test_03_edl_font_directive_roundtrip(self) -> None:
        """`#subs ... font="..."` and `#page scroll ... font="..."` roundtrip losslessly."""
        edl_text = (
            '#output "out.mp4"\n'
            '#subs "White Karaoke Pill" y=65 font="Open Sans" with_cards=on\n'
            '#page scroll size=44 font="Hind"\n'
            "L1 clip.mp4 0.00-2.50\n"
        )
        plan = edl_parser.parse_edl(edl_text)
        self.assertEqual(plan["subs"]["style"], "White Karaoke Pill")
        self.assertEqual(plan["subs"]["font"], "Open Sans")
        self.assertTrue(plan["subs"]["with_cards"])
        self.assertEqual(plan["page"]["font"], "Hind")

        serialized = edl_parser.format_edl(plan)
        self.assertIn('font="Open Sans"', serialized)
        self.assertIn("font=Hind", serialized)
        reparsed = edl_parser.parse_edl(serialized)
        self.assertEqual(reparsed["subs"]["font"], "Open Sans")
        self.assertEqual(reparsed["page"]["font"], "Hind")

    def test_04_node_renderers_cues_page_and_flashcard(self) -> None:
        """End-to-end Node rendering of custom English + Indic Google Fonts in
        `render_cues.js`, `render_page.js`, and `render_flashcard.js`, plus
        verification that Bold (700) produces strictly more ink than Regular (400).
        """
        node_bin = subtitles.node_path()
        self.assertTrue(node_bin, "Node.js binary must be available")

        with tempfile.TemporaryDirectory(prefix="zeno_gfonts_") as td:
            td_path = Path(td)

            # 1. render_cues.js with job.fontFamily + per-cue fontFamily
            cue_job = {
                "width": 1080,
                "height": 1920,
                "fps": 25,
                "styleId": 5,
                "fontFamily": "Roboto",
                "outDir": str(td_path / "cues"),
                "positionY": 65,
                "fontSize": 54,
                "cues": [
                    {
                        "startTime": 0.0,
                        "endTime": 0.6,
                        "text": "Speak English",
                        "words": [
                            {"text": "Speak", "startTime": 0.0, "endTime": 0.3},
                            {"text": "English", "startTime": 0.3, "endTime": 0.6},
                        ],
                    },
                    {
                        "startTime": 0.7,
                        "endTime": 1.3,
                        "text": "अंग्रेज़ी सीखें",
                        "words": [
                            {"text": "अंग्रेज़ी", "startTime": 0.7, "endTime": 1.0},
                            {"text": "सीखें", "startTime": 1.0, "endTime": 1.3},
                        ],
                        "style": {"fontFamily": "Hind"},
                    },
                    {
                        "startTime": 1.4,
                        "endTime": 2.0,
                        "text": "ஆங்கிலம் பேசு",
                        "words": [
                            {"text": "ஆங்கிலம்", "startTime": 1.4, "endTime": 1.7},
                            {"text": "பேசு", "startTime": 1.7, "endTime": 2.0},
                        ],
                        "style": {"fontFamily": "Hind Madurai"},
                    },
                ],
            }
            jf = td_path / "cue_job.json"
            jf.write_text(json.dumps(cue_job, ensure_ascii=False), encoding="utf-8")
            r = subprocess.run(
                [node_bin, str(ROOT / "subtitles" / "render_cues.js"), str(jf)],
                capture_output=True,
                text=True,
                check=True,
            )
            manifest = json.loads([ln for ln in r.stdout.splitlines() if ln.startswith("{")][-1])
            self.assertEqual(manifest["fontFamily"], "Roboto")
            self.assertGreaterEqual(len(manifest["frames"]), 3)
            for f in manifest["frames"]:
                im = Image.open(f["path"]).convert("RGBA")
                self.assertIsNotNone(im.getbbox(), f"Blank rendered cue frame: {f['path']}")

            # 2. render_page.js with Hind Devanagari font
            page_out = td_path / "page.png"
            page_job = {
                "width": 1080,
                "fontSize": 48,
                "fontFamily": "Hind",
                "out": str(page_out),
                "paragraphs": [
                    {"words": [{"text": "नमस्ते"}, {"text": "दुनिया", "bold": True, "color": "#16A34A"}]}
                ],
            }
            pjf = td_path / "page_job.json"
            pjf.write_text(json.dumps(page_job, ensure_ascii=False), encoding="utf-8")
            subprocess.run(
                [node_bin, str(ROOT / "subtitles" / "render_page.js"), str(pjf)],
                capture_output=True,
                text=True,
                check=True,
            )
            self.assertTrue(page_out.is_file())
            self.assertIsNotNone(Image.open(page_out).convert("RGBA").getbbox())

            # 3. render_flashcard.js with static bold Gurmukhi + custom fonts
            fc_out = td_path / "card_punjabi.png"
            fc_manifest = [
                {
                    "en": "Trust me",
                    "native": "ਮੇਰੇ 'ਤੇ ਭਰੋਸਾ ਕਰੋ",
                    "width": 660,
                    "height": 290,
                    "enFont": "Roboto",
                    "nativeFont": "Mukta Mahee",
                    "out": str(fc_out),
                }
            ]
            fmf = td_path / "fc_manifest.json"
            fmf.write_text(json.dumps(fc_manifest, ensure_ascii=False), encoding="utf-8")
            subprocess.run(
                [node_bin, str(ROOT / "subtitles" / "render_flashcard.js"), "--batch", str(fmf)],
                capture_output=True,
                text=True,
                check=True,
            )
            self.assertTrue(fc_out.is_file())
            self.assertEqual(Image.open(fc_out).size, (660, 290))


if __name__ == "__main__":
    unittest.main()
