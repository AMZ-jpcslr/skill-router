"""skill-suggest: suggestions from the hosts' own local history, read-only, no prompt text out."""
import json
import os
from pathlib import Path
import runpy
import tempfile
import time
import unittest

PLUGIN = Path(__file__).resolve().parents[1] / "plugins/skill-router"
SUGGEST = PLUGIN / "skills/skill-suggest/suggest.py"


def skill(home, root, name, description):
    path = home / root / name / "SKILL.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"---\nname: {name}\ndescription: {description}\n---\n# {name}\n", encoding="utf-8")
    return path


class SkillSuggest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        self.env = {"SKILL_ROUTER_STATE_DIR": str(self.home / "state"), "CLAUDE_CONFIG_DIR": str(self.home / ".claude"),
                    "CODEX_HOME": str(self.home / ".codex")}
        self.saved = {k: os.environ.get(k) for k in self.env}
        os.environ.update(self.env)
        (self.home / ".claude").mkdir()
        (self.home / ".codex").mkdir()
        self.now = time.time()

    def tearDown(self):
        for k, v in self.saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        self.tmp.cleanup()

    def index(self, *paths):
        L = runpy.run_path(str(PLUGIN / "skills/skill-router-settings/local_index.py"))
        roots = sorted({p.parent.parent for p in paths})
        L["scan"](roots, L["INDEX"])

    def claude_history(self, prompts):
        with (self.home / ".claude/history.jsonl").open("w", encoding="utf-8") as f:
            for day, text in prompts:
                f.write(json.dumps({"display": text, "pastedContents": {}, "timestamp": int((self.now - day * 86400) * 1000),
                                    "project": None}, ensure_ascii=False) + "\n")

    def report(self, **kw):
        M = runpy.run_path(str(SUGGEST))
        return M["analyse"](**kw)

    def test_named_skill_gets_the_words_the_user_says(self):
        p = skill(self.home, ".agents/skills", "team-status", "Write the team's status report in the house format.")
        self.index(p)
        self.claude_history([(d, "$team-status 今週の週報を書いて") for d in range(4)])
        out = self.report(include_public=False)
        named = next(s for s in out["settings"] if s["kind"] == "named_by_hand")
        self.assertEqual(named["skill"], "team-status")
        self.assertIn("週報", named["add_to_description"])
        # Single words only: no request is reproduced.
        self.assertNotIn("今週の週報を書いて", json.dumps(out, ensure_ascii=False))

    def test_recurring_uncovered_work_finds_a_public_skill(self):
        p = skill(self.home, ".agents/skills", "apology-letters", "Formal apology letter templates for customers.")
        self.index(p)
        self.claude_history([(d, "Figmaのデザインを画面に実装して") for d in range(3)])
        out = self.report()
        self.assertEqual(out["uncovered_requests"], 3)
        self.assertTrue(out["public"], out)
        top = out["public"][0]
        self.assertIn("figma", top["skill"])
        self.assertTrue(top["url"].startswith("https://github.com/"))
        self.assertEqual(top["days"], 3)

    def test_installed_skill_beats_public_and_is_flagged(self):
        p = skill(self.home, ".agents/skills", "figma-implementation", "Implement Figma designs as code: Figma frames to components.")
        self.index(p)
        self.claude_history([(d, "Figmaのデザインを画面に実装して") for d in range(3)])
        out = self.report()
        self.assertFalse(any("figma" in x["skill"] for x in out["public"]))
        flagged = [s for s in out["settings"] if s["kind"] == "installed_but_not_selected"]
        routed = out["uncovered_requests"] == 0
        self.assertTrue(routed or flagged, out)

    def test_codex_sessions_and_skill_use_are_read_without_writing(self):
        used = skill(self.home, ".agents/skills", "docker-expert", "Docker containerization expert: create Dockerfiles and compose files.")
        idle = skill(self.home, ".agents/skills", "pdf-tools", "Extract tables from PDF files.")
        self.index(used, idle)
        day = self.home / ".codex/sessions/2026/11/20"
        day.mkdir(parents=True)
        stamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(self.now - 3600))
        log = day / "rollout-1.jsonl"
        rows = [{"timestamp": stamp, "type": "event_msg", "payload": {"type": "user_message", "message": f"Dockerfileを作成して {i}"}}
                for i in range(25)]
        rows.append({"timestamp": stamp, "type": "response_item", "payload": {
            "type": "function_call", "name": "shell", "arguments": json.dumps({"command": ["cat", str(used)]})}})
        # Skills merely listed to the model are not use.
        rows.append({"timestamp": stamp, "type": "response_item", "payload": {
            "type": "message", "role": "developer", "content": [{"type": "input_text", "text": str(idle)}]}})
        log.write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")
        before = log.stat().st_mtime_ns
        out = self.report(include_public=False)
        self.assertEqual(out["hosts"], {"codex": 25})
        unused = next(s for s in out["settings"] if s["kind"] == "unused")
        self.assertIn("pdf-tools", unused["examples"])
        self.assertNotIn("docker-expert", unused["examples"])
        self.assertEqual(log.stat().st_mtime_ns, before)

    def test_no_history(self):
        out = self.report()
        self.assertEqual(out["note"], "no_history")
        self.assertEqual(out["public"], [])

    def test_find_names_installed_then_public(self):
        p = skill(self.home, ".agents/skills", "terraform-modules", "Write and refactor Terraform modules for AWS infrastructure.")
        self.index(p)
        M = runpy.run_path(str(SUGGEST))
        out = M["find"]("Terraformのモジュールを整理して")
        self.assertEqual([x["skill"] for x in out["local"]], ["terraform-modules"])
        self.assertEqual(out["public"], [])

    def test_not_in_skill_lists(self):
        head = (PLUGIN / "skills/skill-suggest/SKILL.md").read_text(encoding="utf-8").split("---")[1]
        self.assertIn("disable-model-invocation: true", head)
        codex = (PLUGIN / "skills/skill-suggest/agents/openai.yaml").read_text(encoding="utf-8")
        self.assertIn("allow_implicit_invocation: false", codex)

    def test_catalog_is_links_and_metadata_only(self):
        data = json.loads((PLUGIN / "skills/skill-suggest/catalog.json").read_text(encoding="utf-8"))
        self.assertEqual(data["version"], 2)
        self.assertGreater(len(data["skills"]), 100)
        allowed = {"name", "description", "url", "kind", "source", "keywords"}
        ids = {s["id"] for s in data["sources"]}
        for row in data["skills"]:
            self.assertLessEqual(set(row), allowed)
            self.assertIn(row["source"], ids)
            self.assertLessEqual(len(row["description"]), 300)


if __name__ == "__main__":
    unittest.main()
