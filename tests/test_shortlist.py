"""The hook lists the next closest skills, beside its own picks, for the agent to weigh."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

PLUGIN = Path(__file__).resolve().parents[1] / "plugins/skill-router"
ROUTER = PLUGIN / "skills/skill-router-settings/router.py"
HEAD = "説明の語が依頼と近かった導入済みスキルの一覧"


class Shortlist(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.home = Path(self.folder.name)
        self.project = self.home / "trial"
        self.project.mkdir()
        self.env = dict(os.environ, HOME=self.folder.name, USERPROFILE=self.folder.name,
                        SKILL_ROUTER_HOST="claude-code")
        self.env.pop("SKILL_ROUTER_STATE_DIR", None)
        skills = {f"report-writer-{i}": f"Write weekly status reports for team {i}: summary, risks and next steps."
                  for i in range(40)}
        skills["slide-maker"] = "Create presentation slide decks from an outline."
        for name, description in skills.items():
            path = self.home / ".agents/skills" / name / "SKILL.md"
            path.parent.mkdir(parents=True)
            path.write_text(f"---\nname: {name}\ndescription: {description}\n---\n", encoding="utf-8")
        self.cli = [sys.executable, "-I", "-B", "-X", "utf8", str(ROUTER)]
        self.assertEqual(self.run_cli(["index", "refresh"])["indexed"], 41)
        self.assertTrue(self.run_cli(["project", "on"])["current_active"])

    def tearDown(self):
        self.folder.cleanup()

    def run_cli(self, args, payload=None):
        out = subprocess.run(self.cli + args, cwd=self.project, env=self.env, input=payload,
                             capture_output=True, check=True, timeout=20).stdout
        return json.loads(out) if out.strip() else {}

    def context(self, prompt):
        payload = json.dumps({"hook_event_name": "UserPromptSubmit", "prompt": prompt,
                              "cwd": str(self.project)}).encode("utf-8")
        out = self.run_cli(["hook"], payload)
        return out.get("hookSpecificOutput", {}).get("additionalContext", "")

    def listed(self, context):
        body = context.split(HEAD, 1)[1]
        groups = json.loads(body.split("\n", 2)[1])
        return [(g["dir"], r["name"]) for g in groups for r in g["skills"]]

    def test_close_skills_are_listed_for_the_agent_to_choose(self):
        context = self.context("Write the weekly status report for my team.")
        self.assertIn(HEAD, context)
        rows = self.listed(context)
        self.assertTrue(0 < len(rows) <= 25)
        # Each row names a SKILL.md the agent can open: <dir>/<name>/SKILL.md.
        for folder, name in rows:
            self.assertTrue((Path(folder) / name / "SKILL.md").is_file())
        # A skill the hook already offered above is not listed again.
        above = context.split(HEAD, 1)[0]
        self.assertFalse({name for _, name in rows} & {n for n in (f"report-writer-{i}" for i in range(40))
                                                         if f'"name": "{n}"' in above})

    def test_no_list_without_work(self):
        self.assertNotIn(HEAD, self.context("ありがとう！"))


if __name__ == "__main__":
    unittest.main()
