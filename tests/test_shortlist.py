"""The hook lists the next closest skills, beside its own picks, for the agent to weigh."""
import json
import os
import runpy
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

    def context(self, prompt, session_id=None):
        payload = json.dumps({"hook_event_name": "UserPromptSubmit", "prompt": prompt,
                              "cwd": str(self.project), **({"session_id": session_id} if session_id else {})}
                             ).encode("utf-8")
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

    def test_manual_group_under_claude_skills_stays_on_shortlist(self):
        root = self.home / ".claude/skills"
        root.mkdir(parents=True)
        pack = root / "pack"
        (self.home / ".agents/skills").rename(pack)
        rows = self.listed(self.context("Write the weekly status report for my team."))
        self.assertTrue(rows)
        for folder, name in rows:
            self.assertEqual(Path(folder), pack.resolve())
            self.assertTrue((pack / name / "SKILL.md").is_file())

    def test_listed_skills_of_the_latest_request_are_recorded(self):
        context = self.context("Write the weekly status report for my team.", "s1")
        files = list((self.home / ".skill-router-beta/sessions").glob("*.json")) or \
            [f for f in self.home.rglob("sessions/*.json")]
        state = json.loads(files[0].read_text(encoding="utf-8"))
        self.assertEqual(sorted(Path(p).parent.name for p in state["last_listed"]),
                         sorted({name for _, name in self.listed(context)}
                                | {n for n in (f"report-writer-{i}" for i in range(40))
                                   if f'"name": "{n}"' in context.split(HEAD, 1)[0]}))
        # A later request with nothing close leaves an empty list, not the earlier one.
        self.context("Translate this poem into Latin verse.", "s1")
        state = json.loads(files[0].read_text(encoding="utf-8"))
        self.assertEqual(state["last_listed"], [])


class Snippet(unittest.TestCase):
    def test_match_uses_an_indexed_passage_and_only_its_own_terms(self):
        L = runpy.run_path(str(PLUGIN / "skills/skill-router-settings/local_index.py"))
        describe = L["candidate_description"]
        text = ("General tools for geospatial analysis and assorted workflows. "
                "Measure distances between plate boundaries. Do not use for volcano modeling.")
        detail = describe(text, "Measure plate distances and volcano eruptions.")
        self.assertEqual(set(detail["matched_terms"]), {"Measure", "plate", "distances"})
        self.assertIn("plate boundaries", detail["summary"])
        self.assertNotIn("matched_terms", describe(text, "Model volcano eruptions."))
        self.assertNotIn("matched_terms", describe(text, "Write the output to /tmp/distances.txt"))
        self.assertEqual(describe("画像を回転する。", "画像を回転して。")["matched_terms"], ["画像", "回転"])

    def test_the_part_naming_the_request_is_kept(self):
        L = runpy.run_path(str(PLUGIN / "skills/skill-router-settings/local_index.py"))
        text = ("Use this skill any time a spreadsheet file is the primary input or output. This means any task "
                "where the user wants to open, read, edit, or fix an existing .xlsx, .xlsm, .csv, or .tsv file.")
        self.assertIn(".csv", L["snippet"](text, "このCSVの異常値を調べて", 120))
        self.assertNotIn(".csv", L["snippet"](text, "表を作って", 120))
        self.assertEqual(L["snippet"]("Short text.", "csv", 120), "Short text.")
        self.assertLessEqual(len(L["snippet"](text, "csv", 120)), 120)


if __name__ == "__main__":
    unittest.main()
