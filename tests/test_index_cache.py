"""Feature cache and index-time keywords: same routing as uncached, no model call in tests."""
import json
import os
from pathlib import Path
import runpy
import tempfile
import unittest

CORE = Path(__file__).resolve().parents[1] / "plugins/skill-router/skills/skill-router-settings"


def row(name, description, i):
    return {"name": name, "path": f"/s/{name}/SKILL.md", "description": description, "stamp": [i, 1, i]}


ROWS = [row("docker-expert", "Docker containerization expert: create Dockerfiles, images and compose files.", 1),
        row("odoo-docker-deployment", "Create a Docker and docker-compose setup for Odoo with PostgreSQL.", 2),
        row("apology-letters", "Formal correspondence templates for customers.", 3)]
ROWS += [row(f"filler-{t}-{i}", f"Work with {t} projects: setup and reporting for {t} variant {i}.", 10 + 20 * n + i)
         for n, t in enumerate(("kubernetes", "terraform", "spreadsheet", "calendar", "pdf")) for i in range(20)]


class IndexCache(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["SKILL_ROUTER_STATE_DIR"] = self.tmp.name
        self.L = runpy.run_path(str(CORE / "local_index.py"))
        self.K = runpy.run_path(str(CORE / "keywords.py"))

    def tearDown(self):
        os.environ.pop("SKILL_ROUTER_STATE_DIR", None)
        self.tmp.cleanup()

    def names(self, prompt, rows, prepared=None):
        return [r["name"] for _, r in self.L["rank"](prompt, rows, [], None, prepared)]

    def test_cache_matches_uncached(self):
        path = Path(self.tmp.name) / "f.json"
        first = self.L["prepared_features"](ROWS, path)
        self.assertTrue(path.exists())
        second = self.L["prepared_features"](ROWS, path)
        self.assertEqual(dict(first), dict(second))
        self.assertEqual(first.counts, second.counts)
        for prompt in ("このNode.jsアプリのDockerfileを作成して。", "Write a Dockerfile for this app."):
            self.assertEqual(self.names(prompt, ROWS, second), self.names(prompt, ROWS))

    def test_changed_skill_is_recomputed(self):
        path = Path(self.tmp.name) / "f.json"
        self.L["prepared_features"](ROWS, path)
        changed = [dict(ROWS[0], description="Kubernetes manifests.", stamp=[99, 1, 1])] + ROWS[1:]
        self.assertEqual(self.L["prepared_features"](changed, path)[ROWS[0]["path"]],
                         self.L["OPEN"]["prepare"](changed[0]))

    def test_keywords_route_a_japanese_request(self):
        prompt = "取引先へのお詫びメールを書いて。"
        self.assertEqual(self.names(prompt, ROWS), [])
        def fake(text):
            # The batch prompt carries names and descriptions, never the user's request.
            self.assertNotIn(prompt, text)
            items = [json.loads(line) for line in text.splitlines() if line.startswith("{")]
            return {"skills": [{"id": x["id"], "keywords": ["お詫びメール", "謝罪文", "business letter"] if x["name"] == "apology-letters" else []}
                               for x in items]}
        saved, stats = self.K["update"](ROWS, {}, runner=fake, workers=1)
        self.assertEqual(stats["keywords_failed"], 0)
        store = Path(self.tmp.name) / "k.json"
        store.write_text(json.dumps({"version": 1, "records": saved}, ensure_ascii=False), encoding="utf-8")
        rows = self.L["with_keywords"](ROWS, store)
        self.assertEqual(self.names(prompt, rows), ["apology-letters"])
        # A changed SKILL.md drops its stale keywords until the next refresh.
        moved = [dict(r, stamp=[0, 0, 0]) for r in ROWS]
        self.assertNotIn("keywords", self.L["with_keywords"](moved, store)[2])

    def test_keyword_text_is_cleaned(self):
        def fake(text):
            return {"skills": [{"id": 0, "keywords": ["ok", "x", "a\u0000b c", "y" * 80, "ok"]}]}
        saved, _ = self.K["update"](ROWS[:1], {}, runner=fake, workers=1)
        self.assertEqual(saved[ROWS[0]["path"]]["keywords"], ["ok", "a b c"])

    def test_failed_batch_is_reported(self):
        def broken(text):
            raise RuntimeError("no model")
        # A first batch that fails stops the run loudly instead of failing every batch.
        with self.assertRaises(self.K["Unavailable"]):
            self.K["update"](ROWS[:3], {}, runner=broken, workers=1)
        calls = []
        def flaky(text):
            calls.append(text)
            if len(calls) > 1:
                raise RuntimeError("later batch")
            items = [json.loads(line) for line in text.splitlines() if line.startswith("{")]
            return {"skills": [{"id": x["id"], "keywords": ["ok"]} for x in items]}
        saved, stats = self.K["update"](ROWS[:3], {}, runner=flaky, workers=1, batch=1)
        self.assertEqual((len(saved), stats["keywords_failed"], stats["keywords_missing"]), (1, 2, 2))

    def test_keyword_needs_every_word(self):
        # "チーム 連絡" is not said by "Memoチーム" alone.
        O = self.L["OPEN"]
        parts = tuple((p, O["features"](p)[1]) for p in "チーム 連絡".split())
        self.assertEqual(O["keyword_cover"](parts, frozenset(), "memoチームの今週の報告を書いて", O["features"]("memoチームの今週の報告を書いて")[1], frozenset()), 0.)
        self.assertEqual(O["keyword_cover"](parts, frozenset(), "チームへの連絡を書いて", O["features"]("チームへの連絡を書いて")[1], frozenset()), 1.)


if __name__ == "__main__":
    unittest.main()
