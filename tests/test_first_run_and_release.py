"""What a new install goes through before any setting exists, and what the release ZIP contains.
These run on Windows CI as well, which is where most users install."""
import json
import os
from pathlib import Path
import runpy
import shutil
import subprocess
import sys
import tempfile
import unittest
import zipfile

ROOT = Path(__file__).resolve().parents[1]
CORE = ROOT / "plugins/skill-router/skills/skill-router-settings"
ROUTER = CORE / "router.py"


class FirstRun(unittest.TestCase):
    """status → off → this folder on → off, starting from no settings file at all."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self.home, self.project = base / "home", base / "project"
        (self.project / ".git").mkdir(parents=True)
        self.home.mkdir()
        self.env = {k: v for k, v in os.environ.items() if k not in ("SKILL_ROUTER_STATE_DIR", "SKILL_ROUTER_HOST")}
        self.env.update(HOME=str(self.home), USERPROFILE=str(self.home))

    def tearDown(self):
        self.tmp.cleanup()

    def run_router(self, *args):
        p = subprocess.run([sys.executable, "-I", "-B", "-X", "utf8", str(ROUTER), *args], cwd=self.project,
                           env=self.env, capture_output=True, text=True, encoding="utf-8", timeout=60)
        self.assertEqual(p.returncode, 0, p.stderr)
        return json.loads(p.stdout)

    def test_settings_from_nothing(self):
        settings = self.home / ".skill-router-beta/settings.json"
        self.assertFalse(settings.exists())
        first = self.run_router("status")
        self.assertFalse(first["current_active"])
        self.assertTrue(first["show_picks"])
        self.assertTrue(first["record_gaps"])
        self.assertFalse(settings.exists(), "status alone must not write settings")
        off = self.run_router("off")
        self.assertFalse(off["current_active"])
        self.assertTrue(off["show_picks"])
        on = self.run_router("project", "on")
        self.assertTrue(on["current_active"])
        self.assertTrue(on["current_project_enabled"])
        self.assertTrue(self.run_router("status")["current_active"])
        again = self.run_router("off")
        self.assertFalse(again["current_active"])
        self.assertEqual(again["projects"], [])
        self.assertFalse(self.run_router("status")["current_active"])

    def test_display_choice_survives_off(self):
        self.run_router("show-picks", "off")
        self.assertFalse(self.run_router("off")["show_picks"])


class MetaRequestsAboutTheIndex(unittest.TestCase):
    """Words the router itself uses ("索引") must not, said twice, make a database request."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.saved = os.environ.get("SKILL_ROUTER_STATE_DIR")
        os.environ["SKILL_ROUTER_STATE_DIR"] = self.tmp.name
        self.L = runpy.run_path(str(CORE / "local_index.py"))
        self.rows = [{"name": "sql-tuning", "path": "/s/sql-tuning/SKILL.md",
                      "description": "Optimize slow SQL queries with indexing and query plan tuning.",
                      "stamp": [1, 1, 1]}]

    def tearDown(self):
        if self.saved is None:
            os.environ.pop("SKILL_ROUTER_STATE_DIR", None)
        else:
            os.environ["SKILL_ROUTER_STATE_DIR"] = self.saved
        self.tmp.cleanup()

    def picked(self, prompt):
        return [r["name"] for k, r in self.L["rank"](prompt, self.rows, []) if k == "open-search"]

    def test_one_word_twice_is_not_a_query_request(self):
        self.assertEqual(self.picked("スキルの索引を更新して、索引の状態を確認して。"), [])

    def test_query_work_is_still_found(self):
        for prompt in ("このクエリが遅いので索引を見直して", "SQLに索引を追加して実行計画を確認して",
                       "インデックスを貼って検索を高速化して"):
            with self.subTest(prompt=prompt):
                self.assertEqual(self.picked(prompt), ["sql-tuning"])


@unittest.skipUnless(shutil.which("git") and (ROOT / ".git").exists(), "needs a git checkout")
class ReleaseZip(unittest.TestCase):
    def test_zip_has_no_broken_links_or_development_files(self):
        C = runpy.run_path(str(ROOT / "tools/check_release_zip.py"))
        with tempfile.TemporaryDirectory() as d:
            z = C["build"](str(Path(d) / "skill-router.zip"))
            self.assertEqual(C["problems"](z), [])
            with zipfile.ZipFile(z) as f:
                self.assertIn("plugins/skill-router/skills/skill-router-settings/router.py", f.namelist())

    def test_checker_sees_a_link_left_out_of_the_zip(self):
        C = runpy.run_path(str(ROOT / "tools/check_release_zip.py"))
        with tempfile.TemporaryDirectory() as d:
            z = Path(d) / "bad.zip"
            with zipfile.ZipFile(z, "w") as f:
                f.writestr("README.md", "![hero](assets/hero.png)\n[docs](docs/guide.md) [web](https://example.com/x.png)\n")
                f.writestr("tests/test_x.py", "")
            found = C["problems"](str(z))
            self.assertTrue(any("assets/hero.png" in p for p in found))
            self.assertTrue(any("docs/guide.md" in p for p in found))
            self.assertFalse(any("example.com" in p for p in found))
            self.assertTrue(any("tests/test_x.py" in p for p in found))


if __name__ == "__main__":
    unittest.main()
