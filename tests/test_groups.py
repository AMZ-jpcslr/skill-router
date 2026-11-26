"""Skills doing the same work are grouped at index time and shown together with their traits."""
import json
import os
from pathlib import Path
import runpy
import tempfile
import unittest

HERE = Path(__file__).resolve().parents[1] / "plugins/skill-router/skills/skill-router-settings"


class Groups(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        home = Path(self.folder.name)
        self.old = {k: os.environ.get(k) for k in ("HOME", "USERPROFILE", "SKILL_ROUTER_STATE_DIR")}
        os.environ.update(HOME=str(home), USERPROFILE=str(home), SKILL_ROUTER_STATE_DIR=str(home / "state"))
        skills = {
            "commit-writer": "Write clear git commit messages from a diff.",
            "commit-humanizer": "Rewrite git commit messages so they read like a human engineer wrote them.",
            "slide-maker": "Create presentation slide decks from an outline.",
            "commit-polisher": "Polish the wording of commit text before pushing.",
        }
        for name, description in skills.items():
            path = home / ".agents/skills" / name / "SKILL.md"
            path.parent.mkdir(parents=True)
            path.write_text(f"---\nname: {name}\ndescription: {description}\n---\n", encoding="utf-8")
        self.L = runpy.run_path(str(HERE / "local_index.py"))
        self.L["scan"](self.L["DEFAULT"], self.L["INDEX"])
        self.G = runpy.run_path(str(HERE / "groups.py"))
        self.asked = []

    def tearDown(self):
        for k, v in self.old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        self.folder.cleanup()

    def tasks(self, prompt):
        self.asked.append("tasks")
        rows = [json.loads(line) for line in prompt.splitlines() if line.startswith("{")]
        # Different wording of the same work folds to one key ("writing"/"write", order).
        words = {"commit-writer": "write commit messages", "commit-humanizer": "commit messages writing",
                 "slide-maker": "create slide decks", "commit-polisher": "polish commit text"}
        return {"skills": [{"id": r["id"], "task": words[r["name"]]} for r in rows]}

    def families(self, prompt):
        self.asked.append("families")
        names = dict(line.split("\t")[::-1] for line in prompt.splitlines() if "\t" in line)
        # Other words for the same work are joined; one name of it is enough to join the folded rest.
        return {"families": [[int(names["polish commit text"]), int(names["write commit messages"])]]}

    def failing(self, prompt):
        raise RuntimeError("no model")

    def traits(self, prompt):
        self.asked.append("traits")
        rows = [json.loads(line) for line in prompt.splitlines() if line.startswith("{")]
        return {"skills": [{"id": r["id"], "best_for": f"{r['name']} の得意", "not_for": "ほかが合う依頼"} for r in rows]}

    def test_same_work_is_one_group_with_traits(self):
        stats = self.G["build"](self.L, task_runner=self.tasks, traits_runner=self.traits,
                           family_runner=self.families)
        self.assertEqual((stats["groups"], stats["grouped_skills"], stats["groups_with_traits"]), (1, 3, 1))
        family = self.L["similar_skills"]()
        group = next(iter(family.values()))
        self.assertEqual(sorted(m["name"] for m in group["members"]),
                         ["commit-humanizer", "commit-polisher", "commit-writer"])
        self.assertTrue(all(m["best_for"].endswith("の得意") for m in group["members"]))
        # Unchanged skills are not asked again.
        self.asked.clear()
        self.G["build"](self.L, task_runner=self.tasks, traits_runner=self.traits,
                           family_runner=self.families)
        self.assertEqual(self.asked, [])

    def test_hook_shows_the_group_together(self):
        self.G["build"](self.L, task_runner=self.tasks, traits_runner=self.traits,
                           family_runner=self.families)
        R = runpy.run_path(str(HERE / "router.py"))
        idx = R["read_index_module"]()
        out = R["shortlist"](idx, "Rewrite this commit message so it sounds natural.", set(), False)
        entries = [x for g in out for x in g["skills"]]
        same = [x for x in entries if "choose_one" in x]
        self.assertEqual(len(same), 1)
        self.assertLessEqual({"commit-humanizer", "commit-writer"}, {r["name"] for r in same[0]["choose_one"]})
        self.assertIn("same_work", R["shortlist_text"](out, False, False))
        for path in R["listed_paths"](out):
            self.assertTrue(Path(path).is_file())

    def test_without_families_only_the_same_names_join(self):
        stats = self.G["build"](self.L, task_runner=self.tasks, traits_runner=self.traits,
                                family_runner=self.failing)
        self.assertEqual((stats["groups"], stats["grouped_skills"]), (1, 2))
        # The failed pass is asked again next time.
        self.asked.clear()
        stats = self.G["build"](self.L, task_runner=self.tasks, traits_runner=self.traits,
                                family_runner=self.families)
        self.assertEqual((stats["groups"], stats["grouped_skills"]), (1, 3))
        self.assertIn("families", self.asked)


if __name__ == "__main__":
    unittest.main()
