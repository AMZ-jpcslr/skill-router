"""Skill kinds written at index time, and routing of the agent's own to-do items; temporary HOME only."""
import json
import os
from pathlib import Path
import runpy
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "plugins/skill-router"
CORE = PLUGIN / "skills/skill-router-settings"
WRITING_RULE = "依頼や資料にない事実・数字・経験・理由を作らない"

SKILLS = {
    "config-tests": ("Write unit tests for configuration file parsers.", "task"),
    "drive-sync": ("Automate Google Drive: upload, download, move and organize files in Drive folders.", "tool"),
    "key-points": ("Summarize documents into a few key points for busy readers.", "writing"),
}


class KindsAndTodos(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self.home, self.project = base / "home", base / "project"
        (self.project / ".git").mkdir(parents=True)
        self.state = self.home / ".skill-router-beta"
        self.state.mkdir(parents=True)
        (self.state / "settings.json").write_text(json.dumps(
            {"all_projects": True, "folderless": False, "projects": []}), encoding="utf-8")
        self.env = {k: v for k, v in os.environ.items() if k not in ("PLUGIN_ROOT", "SKILL_ROUTER_HOST", "SKILL_ROUTER_STATE_DIR")}
        self.env.update(HOME=str(self.home), USERPROFILE=str(self.home), CLAUDE_PLUGIN_ROOT=str(PLUGIN),
                        SKILL_ROUTER_NO_AUTO_INDEX="1")

    def tearDown(self):
        self.tmp.cleanup()

    def index(self, kinds=True):
        for name, (desc, _) in SKILLS.items():
            f = self.home / ".agents/skills" / name / "SKILL.md"
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_text(f"---\nname: {name}\ndescription: {desc}\n---\n", encoding="utf-8")
        subprocess.run([sys.executable, "-I", "-B", "-X", "utf8", str(CORE / "router.py"), "index", "refresh"],
                       env=self.env, check=True, capture_output=True, timeout=30)
        if kinds:
            rows = json.loads((self.state / "skill-index.json").read_text(encoding="utf-8"))["records"]
            recs = {r["path"]: {"stamp": r["stamp"], "keywords": [], "requests": [], "asked": "8+kind",
                                "kind": SKILLS[r["name"]][1]} for r in rows}
            (self.state / "skill-keywords.json").write_text(json.dumps({"version": 1, "records": recs}), encoding="utf-8")

    def hook(self, event):
        event = dict(event, cwd=str(self.project), session_id="s-1")
        r = subprocess.run([sys.executable, "-I", "-B", "-X", "utf8", str(PLUGIN / "hooks/run_hook.py")],
                           input=json.dumps(event).encode(), capture_output=True, env=self.env, cwd=self.project, timeout=30)
        self.assertEqual(r.returncode, 0, r.stderr.decode(errors="replace"))
        out = json.loads(r.stdout)
        return out.get("hookSpecificOutput", {}).get("additionalContext", "")

    def prompt(self, text):
        return self.hook({"hook_event_name": "UserPromptSubmit", "prompt": text})

    def test_writing_rules_only_for_prose(self):
        self.index()
        txt = self.prompt("Write unit tests for the configuration file parser.")
        self.assertIn('"config-tests"', txt)
        self.assertIn("【候補の扱い】", txt)
        self.assertNotIn(WRITING_RULE, txt)
        txt = self.prompt("この資料を要点にまとめて、忙しい人向けに短く書いて。Summarize the document into key points.")
        self.assertIn('"key-points"', txt)
        self.assertIn(WRITING_RULE, txt)

    def test_without_kinds_the_rules_stay(self):
        self.index(kinds=False)
        txt = self.prompt("Write unit tests for the configuration file parser.")
        self.assertIn('"config-tests"', txt)
        self.assertIn(WRITING_RULE, txt)

    def test_tool_candidates_are_weighed_not_read(self):
        R = runpy.run_path(str(CORE / "router.py"))
        refs = [{"name": "drive-sync", "fit": "read", "kind": "tool"}, {"name": "key-points", "fit": "choose", "kind": "writing"}]
        out = R["demote_tools"](refs)
        self.assertEqual([r["name"] for r in out], ["key-points", "drive-sync"])
        self.assertEqual(out[1]["fit"], "choose")
        self.assertEqual(R["tool_note"]([{"kind": "task"}]), "")
        self.assertIn("kind が tool", R["tool_note"]([{"kind": "tool"}]))

    def test_todo_items_get_their_own_candidates_once(self):
        self.index()
        ev = {"hook_event_name": "PostToolUse", "tool_name": "TaskCreate",
              "tool_input": {"subject": "Write unit tests for the configuration file parser", "description": ""}}
        txt = self.hook(ev)
        self.assertIn("config-tests", txt)
        self.assertIn("作業計画の項目ごと", txt)
        self.assertEqual(self.hook(ev), "")
        done = {"hook_event_name": "PostToolUse", "tool_name": "TodoWrite",
                "tool_input": {"todos": [{"content": "Organize files in Google Drive folders", "status": "completed"}]}}
        self.assertEqual(self.hook(done), "")

    def test_todo_skips_candidates_already_offered(self):
        self.index()
        self.assertIn("config-tests", self.prompt("Write unit tests for the configuration file parser."))
        ev = {"hook_event_name": "PostToolUse", "tool_name": "TodoWrite",
              "tool_input": {"todos": [{"content": "Write unit tests for configuration file parsers", "status": "pending"}]}}
        self.assertNotIn("config-tests", self.hook(ev))

    def test_todo_is_silent_where_routing_is_off(self):
        self.index()
        (self.state / "settings.json").write_text(json.dumps(
            {"all_projects": False, "folderless": False, "projects": []}), encoding="utf-8")
        ev = {"hook_event_name": "PostToolUse", "tool_name": "TaskCreate",
              "tool_input": {"subject": "Write unit tests for the configuration file parser"}}
        self.assertEqual(self.hook(ev), "")

    def test_keywords_write_kinds_and_ask_old_entries_again(self):
        K = runpy.run_path(str(CORE / "keywords.py"))
        rows = [{"path": f"/s/{n}/SKILL.md", "name": n, "description": d, "stamp": [1, 2, 3]} for n, (d, _) in SKILLS.items()]
        asked = []

        def runner(prompt):
            ids = [json.loads(l)["id"] for l in prompt.splitlines() if l.startswith("{")]
            asked.extend(ids)
            return {"skills": [{"id": i, "keywords": ["語"], "requests": [], "kind": "tool" if i == 1 else "task"} for i in ids]}
        old = {rows[0]["path"]: {"stamp": [1, 2, 3], "keywords": ["x"], "requests": [], "asked": 8}}
        saved, stats = K["update"](rows, old, runner=runner)
        self.assertEqual(len(asked), 3)
        self.assertEqual(saved[rows[1]["path"]]["kind"], "tool")
        asked.clear()
        K["update"](rows, saved, runner=runner)
        self.assertEqual(asked, [])


if __name__ == "__main__":
    unittest.main()
