"""When the closest skills score nearly alike, the hook asks for one search in the agent's own words."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

PLUGIN = Path(__file__).resolve().parents[1] / "plugins/skill-router"
ROUTER = PLUGIN / "skills/skill-router-settings/router.py"
MARK = "点差が小さく"


class Contested(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.home = Path(self.folder.name)
        self.project = self.home / "trial"
        self.project.mkdir()
        self.env = dict(os.environ, HOME=self.folder.name, USERPROFILE=self.folder.name,
                        SKILL_ROUTER_HOST="claude-code")
        self.env.pop("SKILL_ROUTER_STATE_DIR", None)
        skills = {
            # Two skills that say nearly the same thing in a request's terms.
            "mesh-volume": "Analyze 3D mesh files (STL) to calculate volume and connected components.",
            "mesh-volume-lite": "Analyze 3D mesh files (STL) to calculate volume and connected components quickly.",
            # One skill no other resembles.
            "bgp-oscillation": "Detect BGP route oscillation and route leaks in Azure Virtual WAN hub topologies.",
            "slide-maker": "Create presentation slide decks from an outline.",
        }
        for name, description in skills.items():
            path = self.home / ".agents/skills" / name / "SKILL.md"
            path.parent.mkdir(parents=True)
            path.write_text(f"---\nname: {name}\ndescription: {description}\n---\n", encoding="utf-8")
        self.cli = [sys.executable, "-I", "-B", "-X", "utf8", str(ROUTER)]
        self.assertEqual(self.run_cli(["index", "refresh"])["indexed"], 4)
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
        return self.run_cli(["hook"], payload).get("hookSpecificOutput", {}).get("additionalContext", "")

    def test_near_tie_asks_for_one_search(self):
        text = self.context("Calculate the volume of the largest connected component in this STL mesh file.")
        self.assertIn(MARK, text)
        self.assertEqual(text.count(MARK), 1)
        self.assertIn("index find", text)

    def test_clear_lead_adds_no_step(self):
        text = self.context("Detect BGP route oscillation and route leaks in our Azure Virtual WAN.")
        self.assertIn("bgp-oscillation", text)
        self.assertNotIn(MARK, text)

    def test_not_a_task_is_untouched(self):
        self.assertEqual(self.context("ありがとう"), "")

    def test_contested_ignores_copies_of_one_name(self):
        env = dict(os.environ, HOME=self.folder.name, USERPROFILE=self.folder.name)
        # The same skill installed in two roots is one skill, not a tie.
        copy = self.home / ".codex/skills/bgp-oscillation/SKILL.md"
        copy.parent.mkdir(parents=True)
        copy.write_text((self.home / ".agents/skills/bgp-oscillation/SKILL.md").read_text(encoding="utf-8"),
                        encoding="utf-8")
        self.run_cli(["index", "refresh"])
        code = ("import json,runpy,sys;L=runpy.run_path(sys.argv[1]);"
                "print(json.dumps([L['contested'](t) for t in sys.argv[2:]]))")
        out = subprocess.run([sys.executable, "-I", "-c", code, str(ROUTER.with_name("local_index.py")),
                              "detect BGP route oscillation in Azure Virtual WAN",
                              "calculate STL mesh volume and connected components"],
                             env=env, capture_output=True, text=True, check=True, timeout=20).stdout
        self.assertEqual(json.loads(out), [False, True])


if __name__ == "__main__":
    unittest.main()
