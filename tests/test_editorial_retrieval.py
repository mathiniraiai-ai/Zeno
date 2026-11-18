"""Offline checks for local retrieval; no credentials, networks or model calls."""
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import direct_mode
import editorial_retrieval as retrieval


class TestEditorialRetrieval(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "agent").mkdir()
        (self.root / "styles" / "formats").mkdir(parents=True)
        self.reference = self.root / "agent" / "REFERENCE.md"
        self.reference.write_text("## Captions\nCaption placement at the divider.\n\n"
                                  "## Sound effects\nUse a swoosh for this example.\n")

    def test_only_relevant_whole_sections_with_provenance(self):
        packet, report = retrieval.retrieve("captions divider", root=self.root)
        self.assertIn("Caption placement", packet)
        self.assertNotIn("swoosh", packet)
        self.assertEqual(report["selected"][0]["source"], "agent/REFERENCE.md")
        self.assertEqual(len(report["selected"][0]["sha256"]), 64)
        self.assertEqual(report["model_calls"], 0)

    def test_no_match_does_not_fabricate_reference(self):
        packet, report = retrieval.retrieve("zebras volcano", root=self.root)
        self.assertEqual(packet, "")
        self.assertEqual(report["selected"], [])

    def test_byte_bound_never_truncates_source_or_overflows(self):
        self.reference.write_text("## Captions\n" + "ಕನ್ನಡ captions " * 100)
        packet, report = retrieval.retrieve("captions", root=self.root, max_bytes=500)
        self.assertEqual(packet, "")
        self.assertTrue(report["oversized_matches"])
        self.assertLessEqual(report["retrieved_bytes"], 500)

    def test_fenced_edl_headers_stay_in_their_section(self):
        text = "## SFX\n```edl\n# SFX: file | segment\nclick.wav | L1\n```\n## Captions\nFixed size."
        sections = list(retrieval._sections(text))
        self.assertEqual(len(sections), 2)
        self.assertIn("# SFX: file", sections[0][1])
        self.assertIn("click.wav", sections[0][1])

    def test_exact_duplicate_is_not_sent_again(self):
        original = "## Captions\nCaption placement at the divider."
        packet, report = retrieval.retrieve("captions divider", root=self.root, already_present=original)
        self.assertEqual(packet, "")
        self.assertEqual(report["selected"], [])

    def test_recipe_alias_and_automatic_source_refresh(self):
        recipe = self.root / "styles" / "formats" / "split.json"
        recipe.write_text(json.dumps({"id": "split_v", "layout": "split_v", "names": ["one above another"],
                                      "confidence": "measured", "description": "ORIGINAL_RECIPE"}))
        first, report = retrieval.retrieve("one above another", root=self.root)
        self.assertIn("ORIGINAL_RECIPE", first)
        old_hash = report["selected"][0]["sha256"]
        recipe.write_text(recipe.read_text().replace("ORIGINAL_RECIPE", "UPDATED_RECIPE"))
        second, report = retrieval.retrieve("one above another", root=self.root)
        self.assertIn("UPDATED_RECIPE", second)
        self.assertNotEqual(old_hash, report["selected"][0]["sha256"])

    def test_private_files_and_other_projects_are_not_indexed(self):
        (self.root / ".env").write_text("captions TEST_SENTINEL_NOT_A_REAL_SECRET")
        (self.root / "work").mkdir()
        (self.root / "work" / "other.md").write_text("captions ANOTHER_VIDEO")
        packet, _ = retrieval.retrieve("captions", root=self.root)
        self.assertNotIn("TEST_SENTINEL", packet)
        self.assertNotIn("ANOTHER_VIDEO", packet)

    def test_invalid_optional_reference_is_reported(self):
        (self.root / "styles" / "formats" / "broken.json").write_text("[not-json")
        _, report = retrieval.retrieve("captions", root=self.root)
        self.assertEqual(len(report["warnings"]), 1)

    def test_planner_keeps_complete_mandatory_style_and_records_retrieval(self):
        project = self.root / "work" / "captions"
        project.mkdir(parents=True)
        style = self.root / "styles" / "chosen"
        style.mkdir()
        (style / "SKILL.md").write_text("COMPLETE_STYLE_RULES\nKeep deliberate emotional holds.")
        (project / "brief.md").write_text("MANDATORY_RULES\n# STYLE: chosen\nShortened style description")
        (project / "plan.edl").write_text("#style chosen\n")
        (project / "line_map.md").write_text("L1 [Speaker] Keep captions at the divider")
        (project / "facts.md").write_text("Measured words")
        with patch.object(direct_mode, "ROOT", self.root):
            system, message, _ = direct_mode.build_brief(project)
        self.assertIn("MANDATORY_RULES", system)
        self.assertIn("COMPLETE_STYLE_RULES\nKeep deliberate emotional holds.", system)
        self.assertIn("Measured words", message)
        self.assertIn("Source: agent/REFERENCE.md", message)
        manifest = json.loads((project / "retrieval_planning.json").read_text())
        self.assertFalse(manifest["mandatory_context_filtered"])
        self.assertEqual(manifest["model_calls"], 0)

        # A prior full-context request reduces only the optional retrieval allowance.
        import request_budget
        with patch.object(direct_mode, "ROOT", self.root), \
                patch.object(request_budget, "status", return_value={
                    "full_context_reserved_tokens": 99900, "max_full_context_reserved_tokens": 100000}):
            system, message, _ = direct_mode.build_brief(project)
        self.assertIn("COMPLETE_STYLE_RULES", system)
        self.assertIn("Measured words", message)
        self.assertNotIn("Retrieved supporting references", message)
        manifest = json.loads((project / "retrieval_planning.json").read_text())
        self.assertEqual(manifest["retrieved_bytes"], 0)


if __name__ == "__main__":
    unittest.main()
