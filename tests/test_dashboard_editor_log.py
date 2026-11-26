"""The admin dashboard must count every delivered video and expose start/end/time/blockers per editor."""
from __future__ import annotations

import json
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "service"))
import usage_page  # noqa: E402

HARNESS = r"""
const fs = require('fs');
const src = fs.readFileSync(process.argv[2], 'utf8');
const grab = re => { const m = src.match(re); if (!m) throw new Error('missing ' + re); return m[0]; };
const code = [grab(/function n\(v\) \{[^\n]*\}/), grab(/const canon = [^\n]*;/), grab(/function dayKey\(t\) \{[^\n]*\}/),
  grab(/function logName[\s\S]*?\n\}\n/), grab(/function buildEditorLog[\s\S]*?\n\}\n/)].join('\n');
eval(code + ';global.build = buildEditorLog;');
const fx = JSON.parse(fs.readFileSync(process.argv[3], 'utf8'));
const L = build(fx.track, fx.runs, fx.jobs, {from: 0, to: 0});
console.log(JSON.stringify({rows: L.rows.length, delivered: L.rows.filter(r => r.end).length, editors: L.editors.map(e => [e.name, e.delivered, e.blocks]), blockers: L.blockers.length}));
"""


class EditorLogTests(unittest.TestCase):
    def test_page_has_editor_log_and_counts_dev_runs_by_default(self) -> None:
        page = usage_page.USAGE_PAGE
        self.assertIn('["log", "Editor log"]', page)
        self.assertIn("useState(true);  // every video counts", page)

    def test_every_video_counted_old_and_new_recorders_merged(self) -> None:
        page = usage_page.USAGE_PAGE
        js = [s for s in re.findall(r"<script(?:\s[^>]*)?>(.*?)</script>", page, re.S) if s.strip()][0]
        v2 = [{"job_key": f"vj_{i}", "editor_name": "Balesh P", "editor_email": "b@x", "notion_name": f"Video {i}",
               "project_name": f"p{i}", "opened_at": 1000 + i * 100, "first_completed_at": 1050 + i * 100,
               "last_event_at": 1050 + i * 100, "status": "done", "render_count": 1, "revision_count": 0,
               "error_count": 0, "block_count": 1 if i == 0 else 0} for i in range(3)]
        runs = [{"id": 1, "editor_name": "Balesh P", "project": "p0", "created_at": 1100, "job_report": {"agent": {"window_minutes": 5, "mixed_with_development": True}}},
                {"id": 9, "editor_name": "Nishanth D", "project": "old1", "created_at": 5000, "job_report": {"agent": {"window_minutes": 12}}}]
        jobs = [{"editor_name": "Nishanth D", "project": "old1", "video_name": "reel_Old Video", "kind": "render", "updated_at": 5000, "created_at": 4000, "zeno_version": "3.0.2"},
                {"editor_name": "Balesh P", "project": "px", "video_name": "Blocked one", "kind": "blocked", "updated_at": 2000, "created_at": 2000}]
        with tempfile.TemporaryDirectory() as tmp:
            t = Path(tmp)
            (t / "page.js").write_text(js, encoding="utf-8")
            (t / "h.js").write_text(HARNESS, encoding="utf-8")
            (t / "fx.json").write_text(json.dumps({"track": {"jobs": v2}, "runs": runs, "jobs": jobs}), encoding="utf-8")
            out = subprocess.run(["node", str(t / "h.js"), str(t / "page.js"), str(t / "fx.json")], capture_output=True, text=True)
        self.assertEqual(out.returncode, 0, out.stderr)
        res = json.loads(out.stdout.strip().splitlines()[-1])
        self.assertEqual(res["rows"], 4)  # 3 tracked Balesh videos + 1 only the old recorder saw (mixed-dev run is not dropped)
        self.assertEqual(res["delivered"], 4)
        self.assertEqual(res["blockers"], 1)
        by = {e[0]: e for e in res["editors"]}
        self.assertEqual(by["Balesh P"][1], 3)
        self.assertEqual(by["Balesh P"][2], 2)  # 1 tracked blocked attempt + 1 from the job log
        self.assertEqual(by["Nishanth D"][1], 1)


if __name__ == "__main__":
    unittest.main()


class RenderLogCounterTests(unittest.TestCase):
    def test_render_log_has_counter(self) -> None:
        page = usage_page.USAGE_PAGE
        self.assertIn("'Videos rendered'", page)
        self.assertIn("counter, perEditor", page)
