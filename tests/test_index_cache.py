"""Feature cache and index-time keywords: same routing as uncached, no model call in tests."""
import json
import os
from pathlib import Path
import runpy
import tempfile
import unittest
from unittest.mock import patch

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
        # Rows are unpacked from the cache on first use; every one matches the fresh build.
        self.assertEqual({r["path"]: first[r["path"]] for r in ROWS}, {r["path"]: second[r["path"]] for r in ROWS})
        self.assertEqual(first.post, second.post)
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

    def test_written_requests_route_a_reworded_request(self):
        """A request the model wrote for the skill at index time reaches one worded differently."""
        prompt = "納期が遅れたので、取引先に謝るメールを丁寧に書いて。"
        def fake(text):
            self.assertIn("requests", text)
            items = [json.loads(line) for line in text.splitlines() if line.startswith("{")]
            return {"skills": [{"id": x["id"], "keywords": [],
                                "requests": ["取引先への謝罪メールを書いて", "納期遅れのお詫び文を作って"]
                                if x["name"] == "apology-letters" else []} for x in items]}
        saved, _ = self.K["update"](ROWS, {}, runner=fake, workers=1)
        self.assertEqual(saved[ROWS[2]["path"]]["requests"], ["取引先への謝罪メールを書いて", "納期遅れのお詫び文を作って"])
        store = Path(self.tmp.name) / "k.json"
        store.write_text(json.dumps({"version": 1, "records": saved}, ensure_ascii=False), encoding="utf-8")
        self.assertEqual(self.names(prompt, self.L["with_keywords"](ROWS, store)), ["apology-letters"])
        # Keywords saved before requests were asked for are written again once.
        old = {p: {"stamp": v["stamp"], "keywords": v["keywords"]} for p, v in saved.items()}
        _, stats = self.K["update"](ROWS, old, runner=fake, workers=1)
        self.assertEqual(stats["keywords_written"], len(ROWS))
        # Unchanged entries are kept; ones written when fewer requests were asked for are written again once.
        _, stats = self.K["update"](ROWS, saved, runner=fake, workers=1)
        self.assertEqual(stats["keywords_written"], 0)
        fewer = {p: dict(v, asked=3) for p, v in saved.items()}
        _, stats = self.K["update"](ROWS, fewer, runner=fake, workers=1)
        self.assertEqual(stats["keywords_written"], len(ROWS))

    def test_keyword_text_is_cleaned(self):
        def fake(text):
            return {"skills": [{"id": 0, "keywords": ["ok", "x", "a\u0000b c", "y" * 80, "ok"]}]}
        saved, _ = self.K["update"](ROWS[:1], {}, runner=fake, workers=1)
        self.assertEqual(saved[ROWS[0]["path"]]["keywords"], ["ok", "a b c"])

    def test_failed_batch_is_reported(self):
        def broken(text):
            raise RuntimeError("no model")
        # A first batch that fails stops the run loudly instead of failing every batch.
        with self.assertRaises(RuntimeError) as failure:
            self.K["update"](ROWS[:3], {}, runner=broken, workers=1)
        self.assertNotIsInstance(failure.exception, self.K["Unavailable"])
        calls = []
        def flaky(text):
            calls.append(text)
            if len(calls) > 1:
                raise RuntimeError("later batch")
            items = [json.loads(line) for line in text.splitlines() if line.startswith("{")]
            return {"skills": [{"id": x["id"], "keywords": ["ok"]} for x in items]}
        saved, stats = self.K["update"](ROWS[:3], {}, runner=flaky, workers=1, batch=1)
        self.assertEqual((len(saved), stats["keywords_failed"], stats["keywords_missing"]), (1, 2, 2))

    def test_same_skill_in_two_places_is_one_candidate(self):
        a = row("dup-skill", "Docker containerization expert: create Dockerfiles.", 500)
        b = dict(a, path="/other/dup-skill/SKILL.md", stamp=[501, 1, 501])
        self.assertEqual(len(self.L["one_per_name"]([a, b] + ROWS)), len(ROWS) + 1)

    def test_malformed_model_responses_are_transient(self):
        for response in ([1], "invalid", {"skills": None}):
            with self.subTest(response=response), self.assertRaises(RuntimeError) as failure:
                self.K["update"](ROWS[:1], {}, runner=lambda _: response)
            self.assertNotIsInstance(failure.exception, self.K["Unavailable"])
        from subprocess import CompletedProcess
        runner = self.K["claude_runner"]
        with patch.dict(runner.__globals__, find_claude=lambda: "claude"), patch("subprocess.run", return_value=CompletedProcess([], 0, '{"result":null}')):
            with self.assertRaises(ValueError):
                runner("prompt")


    def test_japanese_words_without_a_dictionary(self):
        O = self.L["OPEN"]
        self.assertEqual(O["jp_words"]("新卒採用情報"), frozenset({"新卒", "採用", "情報"}))
        self.assertEqual(O["jp_words"]("テストを先に書く"), frozenset({"テスト"}))
        # A katakana word is said whole, or joined to another catalogue word.
        vocab = {"ログ", "セキュリティ", "チェック"}
        self.assertEqual(O["Query"].said_jp("ログイン画面", vocab), frozenset())
        self.assertEqual(O["Query"].said_jp("セキュリティチェックして", vocab), frozenset({"セキュリティ", "チェック"}))

    def with_keywords(self, keywords):
        return [dict(r, keywords=keywords.get(r["name"], [])) for r in ROWS]

    def test_keyword_needs_what_it_acts_on(self):
        # "es作成" names an ES; a Dockerfile request only shares the action.
        rows = self.with_keywords({"apology-letters": ["es作成", "書き直し"]})
        self.assertNotIn("apology-letters", self.names("このNode.jsアプリのDockerfileを作成して。", rows))
        # An action alone does not name the task either.
        self.assertNotIn("apology-letters", self.names("この説明文を書き直して。", rows))
        self.assertEqual(self.names("ESを作成して。", rows), ["apology-letters"])

    def test_urls_and_save_paths_do_not_pick_a_skill(self):
        rows = ROWS + [row("github-docs-sync", "Sync docs to a GitHub repository over https, for com domains.", 500)]
        self.assertEqual(self.names("紹介文の最後に https://github.com/example/tools を入れて。", rows), [])
        self.assertEqual(self.names("C:\\Users\\me\\github\\docs-sync-com に保存して。", rows), [])


if __name__ == "__main__":
    unittest.main()
