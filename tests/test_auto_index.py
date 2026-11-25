"""The index follows skill folders on its own: only changed folders are walked, no model call in the hook."""
import json
import os
from pathlib import Path
import runpy
import subprocess
import sys
import tempfile
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "plugins/skill-router"
CORE = PLUGIN / "skills/skill-router-settings"


def write_skill(base, name, description):
    p = base / name / "SKILL.md"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(f"---\nname: {name}\ndescription: {description}\n---\nbody\n", encoding="utf-8")
    return p


class AutoIndex(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name) / "home"
        self.skills = self.home / ".agents/skills"
        self.skills.mkdir(parents=True)
        self.state = self.home / ".skill-router-beta"
        self.saved = {k: os.environ.get(k) for k in ("HOME", "USERPROFILE", "SKILL_ROUTER_STATE_DIR")}
        os.environ.update(HOME=str(self.home), USERPROFILE=str(self.home), SKILL_ROUTER_STATE_DIR=str(self.state))
        for i in range(30):
            write_skill(self.skills, f"filler-{i:02d}", f"Work with tool {i}: setup and reporting.")
        self.L = runpy.run_path(str(CORE / "local_index.py"))
        self.A = runpy.run_path(str(CORE / "auto_index.py"))

    def tearDown(self):
        for k, v in self.saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        self.tmp.cleanup()

    def names(self):
        return sorted(r["name"] for r in self.L["read_json"](self.L["INDEX"], {})["records"])

    def test_first_use_builds_the_index(self):
        self.assertEqual(self.A["check"](self.L)["walked"], "whole")
        self.assertEqual(len(self.names()), 30)
        self.assertIsNone(self.A["check"](self.L)["walked"])

    def test_unchanged_folders_write_nothing(self):
        self.A["check"](self.L)
        auto = self.state / "skill-auto.json"
        before = (auto.stat().st_mtime_ns, self.L["INDEX"].stat().st_mtime_ns)
        time.sleep(0.02)
        self.A["check"](self.L)
        self.assertEqual(before, (auto.stat().st_mtime_ns, self.L["INDEX"].stat().st_mtime_ns))

    def test_added_and_removed_skills_walk_only_their_folders(self):
        self.A["check"](self.L)
        write_skill(self.skills, "apology-letters", "Write apology emails to clients.")
        self.assertEqual(self.A["check"](self.L)["walked"], ["apology-letters"])
        self.assertIn("apology-letters", self.names())
        for f in (self.skills / "filler-03").iterdir():
            f.unlink()
        (self.skills / "filler-03").rmdir()
        self.assertEqual(self.A["check"](self.L)["walked"], ["filler-03"])
        self.assertNotIn("filler-03", self.names())
        self.assertEqual(len(self.names()), 30)

    def test_skill_in_a_new_root_is_found(self):
        self.A["check"](self.L)
        write_skill(self.home / ".claude/skills", "native-one", "Native skill.")
        self.assertEqual(self.A["check"](self.L)["walked"], "whole")
        self.assertIn("native-one", self.names())

    def test_edit_seen_while_routing_is_reread(self):
        self.A["check"](self.L)
        p = self.skills / "filler-05/SKILL.md"
        folder_time = (self.skills / "filler-05").stat().st_mtime_ns
        with p.open("a", encoding="utf-8") as f:  # an edit in place leaves the folder time alone
            f.write("more\n")
        os.utime(p.parent, ns=(folder_time, folder_time))
        self.assertIsNone(self.A["check"](self.L)["walked"])
        self.assertEqual(self.A["check"](self.L, [str(p)])["walked"], ["filler-05"])
        row = next(r for r in self.L["read_json"](self.L["INDEX"], {})["records"] if r["name"] == "filler-05")
        self.assertEqual(row["stamp"][1], p.stat().st_size)

    def test_daily_walk_and_format_change_walk_whole(self):
        self.A["check"](self.L)
        self.assertEqual(self.A["check"](self.L, now=time.time() + 2 * 86400)["walked"], "whole")
        # A plugin update that changes the index format rereads every skill once.
        auto = self.state / "skill-auto.json"
        self.L["write_json"](auto, dict(json.loads(auto.read_text(encoding="utf-8")), format=self.L["FORMAT"] - 1))
        self.assertEqual(self.A["check"](self.L)["walked"], "whole")
        self.assertIsNone(self.A["check"](self.L)["walked"])

    def test_keywords_are_not_started_without_an_opt_in(self):
        self.assertNotIn("keywords", self.A["check"](self.L))

    def test_keywords_run_in_the_background_for_new_skills(self):
        # Once the user has made keywords, a new skill gets them without being asked again.
        fake = Path(self.tmp.name) / "fake_keywords.py"
        fake.write_text("import json,sys,re\n"
                        "ids=[int(x) for x in re.findall(r'\"id\": (\\d+)',sys.stdin.read())]\n"
                        "print(json.dumps({'skills':[{'id':i,'keywords':['お詫び'],'requests':['お詫びメールを書いて']} for i in ids]}))\n",
                        encoding="utf-8")
        os.environ["SKILL_ROUTER_KEYWORD_CMD"] = f'"{sys.executable}" -I "{fake}"'
        try:
            self.L["write_json"](self.L["KEYWORDS"], {"version": 1, "records": {}})
            self.assertEqual(self.A["check"](self.L).get("keywords"), "started")
            kw_state = self.state / "skill-auto-keywords.json"
            for _ in range(300):
                if kw_state.exists():
                    break
                time.sleep(0.1)
            done = json.loads(kw_state.read_text(encoding="utf-8"))
            self.assertEqual(done.get("keywords_written"), 30, done)
            self.assertEqual(len(json.loads(self.L["KEYWORDS"].read_text(encoding="utf-8"))["records"]), 30)
            # Caught up: nothing is started again until a skill changes.
            self.assertNotIn("keywords", self.A["check"](self.L, now=time.time() + 2 * 3600))
        finally:
            os.environ.pop("SKILL_ROUTER_KEYWORD_CMD", None)


class HookBuildsIndex(unittest.TestCase):
    def test_hook_routes_a_skill_added_without_refresh(self):
        with tempfile.TemporaryDirectory() as tmp:
            home, project = Path(tmp) / "home", Path(tmp) / "project"
            (project / ".git").mkdir(parents=True)
            state = home / ".skill-router-beta"
            state.mkdir(parents=True)
            (state / "settings.json").write_text(json.dumps(
                {"all_projects": False, "folderless": False, "projects": [str(project.resolve())]}), encoding="utf-8")
            env = {k: v for k, v in os.environ.items() if k not in ("PLUGIN_ROOT", "SKILL_ROUTER_HOST", "SKILL_ROUTER_STATE_DIR")}
            env.update(HOME=str(home), USERPROFILE=str(home), CLAUDE_PLUGIN_ROOT=str(PLUGIN))

            def hook():
                event = json.dumps({"hook_event_name": "UserPromptSubmit", "cwd": str(project),
                                    "prompt": "README.md の日本語を自然に整えて。"})
                r = subprocess.run([sys.executable, "-I", "-B", "-X", "utf8", str(PLUGIN / "hooks/run_hook.py")],
                                   input=event.encode(), capture_output=True, env=env, cwd=project, timeout=30)
                self.assertEqual(r.returncode, 0, r.stderr.decode(errors="replace"))
                return json.loads(r.stdout)["hookSpecificOutput"]["additionalContext"]

            self.assertNotIn("nihongo", hook())
            write_skill(home / ".agents/skills", "nihongo", "Japanese writing and Japanese proofreading; polish Japanese prose.")
            self.assertIn('"name": "nihongo"', hook())


if __name__ == "__main__":
    unittest.main()
