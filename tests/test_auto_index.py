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

    def _times_move_when_walked(self):
        """Like NTFS, where the folder times a listing shows catch up only once a folder has
        been read: every folder the walk reads reports a later time afterwards."""
        scan = self.L["scan"]

        def moving(rts, index, changed=None):
            out = scan(rts, index, changed=changed)
            for root in rts:
                names = None if changed is None else changed.get(str(root), set())
                for d in Path(root).iterdir():
                    if d.is_dir() and (names is None or d.name in names):
                        t = d.stat().st_mtime + 5
                        os.utime(d, (t, t))
            return out
        self.L["scan"] = moving

    def test_times_that_move_on_reading_do_not_cause_rewalks(self):
        self._times_move_when_walked()
        self.assertEqual(self.A["check"](self.L)["walked"], "whole")
        auto = self.state / "skill-auto.json"
        before = (auto.stat().st_mtime_ns, self.L["INDEX"].stat().st_mtime_ns)
        time.sleep(0.02)
        self.assertIsNone(self.A["check"](self.L)["walked"])
        self.assertEqual(before, (auto.stat().st_mtime_ns, self.L["INDEX"].stat().st_mtime_ns))
        write_skill(self.skills, "apology-letters", "Write apology emails to clients.")
        self.assertEqual(self.A["check"](self.L)["walked"], ["apology-letters"])
        self.assertIsNone(self.A["check"](self.L)["walked"])

    def test_folder_added_during_a_walk_is_walked_next_time(self):
        self.A["check"](self.L)
        scan = self.L["scan"]

        def adding(rts, index, changed=None):
            out = scan(rts, index, changed=changed)
            if not (self.skills / "late-one").exists():
                write_skill(self.skills, "late-one", "Arrives while the walk runs.")
            return out
        self.L["scan"] = adding
        write_skill(self.skills, "early-one", "Arrives before the walk.")
        self.assertEqual(self.A["check"](self.L)["walked"], ["early-one"])
        self.assertNotIn("late-one", self.names())
        self.assertEqual(self.A["check"](self.L)["walked"], ["late-one"])
        self.assertIn("late-one", self.names())

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

    def test_stale_path_spelled_differently_from_its_root(self):
        self.A["check"](self.L)
        real = self.skills / "filler-07"
        alias = Path(self.tmp.name) / "alias"
        try:
            os.symlink(self.home, alias, target_is_directory=True)
        except (OSError, NotImplementedError):
            self.skipTest("cannot make a link here")
        p = alias / ".agents/skills/filler-07/SKILL.md"
        with (real / "SKILL.md").open("a", encoding="utf-8") as f:
            f.write("more\n")
        self.assertEqual(self.A["check"](self.L, [str(p)])["walked"], ["filler-07"])

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

    def test_remove_extra_root_by_its_original_spelling_after_removal(self):
        from contextlib import redirect_stdout
        from io import StringIO
        extra = self.home / "extra"
        extra.mkdir()
        spelling = self.skills / ".." / ".." / "extra"
        with redirect_stdout(StringIO()):
            self.L["command"](["add-root", str(spelling)])
            self.assertEqual(self.L["roots"](), [extra.resolve()])
            extra.rmdir()
            self.L["command"](["remove-root", str(spelling)])
            self.assertEqual(self.L["roots"](), [])

    def test_nested_native_and_system_skills_with_noncanonical_home(self):
        spelled = self.skills / ".." / ".."
        native = self.home / ".claude/skills"
        nested = write_skill(native / "pack", "my-skill", "Use a nested native skill.")
        external = write_skill(self.skills, "my-skill", "Another copy of this skill.")
        write_skill(self.home / ".codex/skills/.system", "system-skill", "Use a system skill.")
        os.environ.update(HOME=str(spelled), USERPROFILE=str(spelled))
        L = runpy.run_path(str(CORE / "local_index.py"))
        L["scan"](L["DEFAULT"], L["INDEX"])
        rows = L["read_json"](L["INDEX"], {})["records"]
        self.assertIn("system-skill", {r["name"] for r in rows})
        copies = [{"name": "my-skill", "path": str(p.resolve())} for p in (external, nested)]
        self.assertEqual(L["one_per_name"](copies), [copies[1]])
        R = runpy.run_path(str(CORE / "router.py"))
        self.assertTrue(R["claude_native"](copies[1], (spelled / ".claude/skills").resolve()))

    def test_find_checks_indexed_names_and_canonical_paths(self):
        from unittest.mock import patch
        path = self.skills / "filler-00/SKILL.md"
        good = {"name": "filler-00", "path": str(path.resolve()), "description": "Reporting"}
        candidates = [dict(good, name="bad name"),
                      dict(good, path=str(path.parent / ".." / path.parent.name / path.name)), good]
        find = self.L["find"]
        with patch.dict(find.__globals__, scored=lambda *args: [(1, r) for r in candidates]):
            self.assertEqual([r["name"] for r in find("reporting")], ["filler-00"])

    def test_changed_target_can_start_immediately_after_success(self):
        from unittest.mock import patch
        self.L["write_json"](self.L["KEYWORDS"], {"version": 1, "records": {}})
        check = self.A["check"]
        with patch.dict(check.__globals__, spawn_keywords=lambda want: "started"):
            self.assertEqual(check(self.L, now=10000)["keywords"], "started")
            state, worker = self.A["_paths"](self.L)
            attempted = self.L["read_json"](state, {})
            target = [attempted["changed"], attempted["kw_code"]]
            self.L["write_json"](worker, {"done_for": target})
            write_skill(self.skills, "new-tool", "Prepare reports.")
            self.assertEqual(check(self.L, now=10600)["keywords"], "started")
            write_skill(self.skills, "another-tool", "Create receipts.")
            self.assertNotIn("keywords", check(self.L, now=10601))
            self.L["write_json"](worker, {"done_for": None, "unavailable": False})
            self.assertNotIn("keywords", check(self.L, now=14199))
            self.assertEqual(check(self.L, now=14200)["keywords"], "started")
            self.L["write_json"](worker, {"done_for": None, "unavailable": True})
            self.assertNotIn("keywords", check(self.L, now=14200+3600))
            self.assertEqual(check(self.L, now=14200+86400)["keywords"], "started")


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
