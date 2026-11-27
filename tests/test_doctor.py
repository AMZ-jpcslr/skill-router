"""Skill health check: vague skills and overlapping pairs found by the router's own scoring;
proposals are checked without writing anything."""
import contextlib
import io
import json
import os
from pathlib import Path
import runpy
import tempfile
import unittest

CORE = Path(__file__).resolve().parents[1] / "plugins/skill-router/skills/skill-router-settings"


def row(name, description, i):
    return {"name": name, "path": f"/s/{name}/SKILL.md", "description": description, "stamp": [i, 1, i]}


TOPICS = ("kubernetes", "terraform", "spreadsheet", "calendar", "invoice", "podcast", "sqlite", "gitlab")
FILLER = [row(f"{t}-helper-{i}", f"Set up {t} projects and write {t} reports for team {i}. Use when the user works on {t}.", 100 + 10 * n + i)
          for n, t in enumerate(TOPICS) for i in range(5)]
ROWS = FILLER + [
    row("natural-japanese", "日本語の文章を自然な言い回しに推敲する。AIっぽい硬い表現や直訳調を直し、読み手に合う語調に整えるときに使う。", 1),
    row("japanese-polish", "日本語の文章を推敲し、AIっぽい硬い表現や直訳調を自然な言い回しに直すときに使う。", 2),
    row("plain-japanese-for-kids", "小学生にも読めるやさしい日本語に書き換える。漢字にふりがなを付け、一文を短くするときに使う。", 3),
    row("helper", "Helps with things.", 4),
    row("mermaid-diagrams", "Draw Mermaid flowcharts and sequence diagrams from a description. Use when the user asks for a diagram.", 5),
]


class Doctor(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["SKILL_ROUTER_STATE_DIR"] = self.tmp.name
        Path(self.tmp.name, "skill-index.json").write_text(
            json.dumps({"version": 2, "roots": [], "records": ROWS}, ensure_ascii=False), encoding="utf-8")
        self.D = runpy.run_path(str(CORE / "doctor.py"))

    def tearDown(self):
        os.environ.pop("SKILL_ROUTER_STATE_DIR", None)
        self.tmp.cleanup()

    def call(self, *argv):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = self.D["command"](list(argv))
        return code, json.loads(out.getvalue())

    def test_vague_skill_is_flagged_and_clear_one_is_not(self):
        code, r = self.call("--limit", "100")
        self.assertEqual(code, 0)
        hard = {h["name"]: h for h in r["hard_to_pick"]}
        self.assertIn("helper", hard)
        self.assertIn("too_short", hard["helper"]["issues"])
        self.assertIn("generic_name", hard["helper"]["issues"])
        self.assertNotIn("mermaid-diagrams", hard)

    def test_overlapping_pair_is_found_and_distinct_skill_is_not(self):
        _, r = self.call("--limit", "100")
        pairs = [set(p["skills"]) for p in r["overlaps"]]
        self.assertIn({"natural-japanese", "japanese-polish"}, pairs)
        self.assertFalse(any("mermaid-diagrams" in p for p in pairs))
        found = next(p for p in r["overlaps"] if set(p["skills"]) == {"natural-japanese", "japanese-polish"})
        self.assertTrue(found["shared_words"])
        self.assertIn("only_natural-japanese", found)

    def test_single_skill_report(self):
        code, r = self.call("--skill", "mermaid-diagrams")
        self.assertEqual(code, 0)
        self.assertEqual([h["name"] for h in r["hard_to_pick"]], ["mermaid-diagrams"])
        self.assertIn("mermaid", r["hard_to_pick"][0]["specific_words"])

    def test_proposed_description_is_checked_not_written(self):
        before = Path(self.tmp.name, "skill-index.json").read_bytes()
        code, r = self.call("--skill", "helper", "--try-description",
                            "Rename and sort photo files by the date they were taken (EXIF). "
                            "Use when the user wants a folder of photos organized by date.")
        self.assertEqual(code, 0)
        self.assertFalse(r["written"])
        self.assertIn("too_short", r["before"]["issues"])
        self.assertNotIn("too_short", r["after"]["issues"])
        self.assertTrue(r["after"]["specific_words"])
        self.assertEqual(Path(self.tmp.name, "skill-index.json").read_bytes(), before)

    def test_unknown_skill_and_missing_index(self):
        code, r = self.call("--skill", "no-such-skill")
        self.assertEqual((code, r["error"]), (1, "unknown_skill"))
        Path(self.tmp.name, "skill-index.json").unlink()
        code, r = self.call()
        self.assertEqual((code, r["error"]), (1, "index_missing"))

    def test_description_trial_stops_at_index_limit(self):
        prefix = ("photo " * 107)[:640]
        _, first = self.call("--skill", "helper", "--try-description", prefix)
        _, extended = self.call("--skill", "helper", "--try-description", prefix + " extra exif geojson")
        self.assertEqual(first["after"], extended["after"])

    def test_no_evidence_does_not_win_an_example(self):
        from types import SimpleNamespace
        L = self.D["engine"]()
        cat = SimpleNamespace(rows=[{"description": "Help", "keywords": ["依頼文のキーワード"]}],
                              O=L["OPEN"], probe=lambda *args: (-0.35, []))
        _, wins, examples = self.D["diagnose"](cat, 0)
        self.assertEqual((wins, examples), (0, 1))

    def test_scale(self):
        import time
        many = [row(f"skill-{i}", f"Handle task {i} with tool{i % 97} and format{i % 31}: create, check and convert files "
                                  f"for area{i % 53}. Use when the user asks about area{i % 53}.", 1000 + i) for i in range(2000)]
        Path(self.tmp.name, "skill-index.json").write_text(json.dumps({"version": 2, "roots": [], "records": many}))
        t = time.perf_counter()
        code, r = self.call()
        self.assertEqual(code, 0)
        self.assertEqual(r["skills"], 2000)
        self.assertLess(time.perf_counter() - t, 30)


if __name__ == "__main__":
    unittest.main()
