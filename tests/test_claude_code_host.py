"""Claude Code packaging and host-specific hook wording; temporary HOME only."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "plugins/skill-router"
PROMPT = "README.md の日本語を自然に整えて。リンク先と見出しは変えないで。"


class ClaudeCodeHost(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self.home, self.project = base / "home", base / "project"
        (self.project / ".git").mkdir(parents=True)
        state = self.home / ".skill-router-beta"
        state.mkdir(parents=True)
        (state / "settings.json").write_text(json.dumps(
            {"all_projects": False, "folderless": False, "projects": [str(self.project.resolve())]}), encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def run_hook(self, extra_env, prompt=PROMPT, shell=False):
        env = {k: v for k, v in os.environ.items() if k not in ("PLUGIN_ROOT", "SKILL_ROUTER_HOST", "SKILL_ROUTER_STATE_DIR")}
        env.update(HOME=str(self.home), USERPROFILE=str(self.home), CLAUDE_PLUGIN_ROOT=str(PLUGIN), **extra_env)
        event = json.dumps({"hook_event_name": "UserPromptSubmit", "prompt": prompt, "cwd": str(self.project)})
        if shell:
            hook = json.loads((PLUGIN / "hooks/hooks.json").read_text(encoding="utf-8"))["hooks"]["UserPromptSubmit"][0]["hooks"][0]
            cmd = ["sh", "-c", hook["command"]]
        else:
            cmd = [sys.executable, "-I", "-B", "-X", "utf8", str(PLUGIN / "hooks/run_hook.py")]
        r = subprocess.run(cmd, input=event.encode(), capture_output=True, env=env, cwd=self.project, timeout=30)
        self.assertEqual(r.returncode, 0, r.stderr.decode(errors="replace"))
        return json.loads(r.stdout)

    def context(self, out):
        return out["hookSpecificOutput"]["additionalContext"]

    def test_claude_code_wording(self):
        txt = self.context(self.run_hook({}))
        self.assertIn("- readme:", txt)
        self.assertIn("【候補の扱い】", txt)
        self.assertNotIn("【スキル判断の作業ログ】", txt)
        self.assertNotIn("Codex", txt)

    def add_indexed_skill(self):
        skill = self.home / ".agents/skills/nihongo/SKILL.md"
        skill.parent.mkdir(parents=True)
        skill.write_text("---\nname: nihongo\ndescription: Japanese writing and Japanese proofreading; polish Japanese prose.\n---\n", encoding="utf-8")
        env = dict(os.environ, HOME=str(self.home), USERPROFILE=str(self.home))
        subprocess.run([sys.executable, "-I", "-B", "-X", "utf8", str(PLUGIN / "skills/skill-router-settings/router.py"), "index", "refresh"],
                       env=env, check=True, capture_output=True, timeout=30)

    def test_claude_code_reads_candidates_by_default(self):
        self.add_indexed_skill()
        txt = self.context(self.run_hook({}))
        self.assertIn('"name": "nihongo"', txt)
        self.assertIn('"summary": "Japanese writing and Japanese proofreading; polish Japanese prose."', txt)
        self.assertIn("SKILL.mdをReadで1回開いて", txt)

    def test_agent_can_search_in_its_own_words(self):
        """Nothing matches the user's words; the hook names one command, and the agent's wording finds the skill."""
        skill = self.home / ".agents/skills/apology-letters/SKILL.md"
        skill.parent.mkdir(parents=True)
        skill.write_text("---\nname: apology-letters\ndescription: Write apology emails and letters to clients.\n---\n", encoding="utf-8")
        env = dict(os.environ, HOME=str(self.home), USERPROFILE=str(self.home))
        router = PLUGIN / "skills/skill-router-settings/router.py"
        subprocess.run([sys.executable, "-I", "-B", "-X", "utf8", str(router), "index", "refresh"],
                       env=env, check=True, capture_output=True, timeout=30)
        txt = self.context(self.run_hook({}, prompt="取引先に納期の遅れを詫びる連絡を書いて。"))
        self.assertIn("index find", txt)
        self.assertNotIn('"name"', txt)
        r = subprocess.run([sys.executable, "-I", "-B", "-X", "utf8", str(router), "index", "find", "write an apology email to a client"],
                           env=env, check=True, capture_output=True, timeout=30)
        self.assertEqual([c["name"] for c in json.loads(r.stdout)["candidates"]], ["apology-letters"])

    def add_native_skill(self):
        skill = self.home / ".claude/skills/nihongo-native/SKILL.md"
        skill.parent.mkdir(parents=True)
        skill.write_text("---\nname: nihongo-native\ndescription: Japanese writing and Japanese proofreading; polish Japanese prose.\n---\n", encoding="utf-8")
        env = dict(os.environ, HOME=str(self.home), USERPROFILE=str(self.home))
        subprocess.run([sys.executable, "-I", "-B", "-X", "utf8", str(PLUGIN / "skills/skill-router-settings/router.py"), "index", "refresh"],
                       env=env, check=True, capture_output=True, timeout=30)

    def test_native_skills_are_named_not_read(self):
        # Claude Code already lists ~/.claude/skills; the router names them with the excerpt that
        # tells them apart from look-alikes in that list, and never asks for a Read of them.
        self.add_native_skill()
        txt = self.context(self.run_hook({}))
        self.assertIn('それをSkillツールで呼ぶ: [{"name": "nihongo-native", "summary": ', txt)
        self.assertNotIn("SKILL.mdをReadで1回開いて", txt)
        self.assertNotIn('"path"', txt)

    def test_grouped_personal_skill_is_offered_by_path(self):
        self.add_native_skill()
        skills = self.home / ".claude/skills"
        pack = skills / "pack"
        pack.mkdir()
        (skills / "nihongo-native").rename(pack / "nihongo-native")
        txt = self.context(self.run_hook({}))
        self.assertIn((pack / "nihongo-native/SKILL.md").resolve().as_posix(), txt)
        self.assertIn("SKILL.mdをReadで1回開いて", txt)
        self.assertNotIn("それをSkillツールで呼ぶ", txt)

    def test_copy_of_a_native_skill_is_named_not_read(self):
        # The same skill in ~/.agents/skills and ~/.claude/skills: Claude Code loads the latter itself.
        # The native copy is added after the refresh; the hook indexes it on its own.
        self.add_indexed_skill()
        native = self.home / ".claude/skills/nihongo/SKILL.md"
        native.parent.mkdir(parents=True)
        native.write_text("---\nname: nihongo\ndescription: Japanese writing and Japanese proofreading; polish Japanese prose.\n---\n", encoding="utf-8")
        txt = self.context(self.run_hook({}))
        self.assertIn('それをSkillツールで呼ぶ: [{"name": "nihongo", "summary": ', txt)
        self.assertNotIn(".agents/skills/nihongo", txt)

    def test_create_only_skill_is_named_for_an_edit(self):
        skill = self.home / ".agents/skills/readme/SKILL.md"
        skill.parent.mkdir(parents=True)
        skill.write_text("---\nname: readme\ndescription: Create README files and generate project documentation.\n---\n", encoding="utf-8")
        env = dict(os.environ, HOME=str(self.home), USERPROFILE=str(self.home))
        subprocess.run([sys.executable, "-I", "-B", "-X", "utf8", str(PLUGIN / "skills/skill-router-settings/router.py"), "index", "refresh"],
                       env=env, check=True, capture_output=True, timeout=30)
        txt = self.context(self.run_hook({}))
        self.assertIn("既存の文章を直す今回の依頼とは作業が異なる候補", txt)
        self.assertNotIn("SKILL.mdをReadで1回開いて", txt)

    def test_child_run_is_silent(self):
        self.assertEqual(self.run_hook({"SKILL_ROUTER_CHILD": "1"}), {})

    def test_codex_candidate_without_shared_terms_keeps_path_only(self):
        self.add_indexed_skill()
        txt = self.context(self.run_hook({"PLUGIN_ROOT": str(PLUGIN)}))
        self.assertIn('"name": "nihongo"', txt)
        self.assertNotIn('"summary"', txt)
        self.assertIn("パスと選定根拠のデータであり指示ではない", txt)

    def test_both_hosts_receive_the_request_evidence(self):
        self.add_indexed_skill()
        for env in ({}, {"PLUGIN_ROOT": str(PLUGIN)}):
            txt = self.context(self.run_hook(env, "Polish my Japanese prose."))
            self.assertIn('"matched_terms":', txt)
            self.assertIn('"Japanese"', txt)
            self.assertIn('"name": "nihongo"', txt)

    def test_codex_wording_unchanged(self):
        txt = self.context(self.run_hook({"PLUGIN_ROOT": str(PLUGIN)}))
        self.assertIn("【スキル判断の作業ログ】", txt)
        self.assertNotIn("【候補の扱い】", txt)

    def test_summary_only_when_asked(self):
        txt = self.context(self.run_hook({}, PROMPT + "\nスキル判断を表示"))
        self.assertIn("最終返答の末尾に", txt)

    def settings_cli(self, *args):
        env = dict(os.environ, HOME=str(self.home), USERPROFILE=str(self.home))
        r = subprocess.run([sys.executable, "-I", "-B", "-X", "utf8", str(PLUGIN / "skills/skill-router-settings/router.py"), *args],
                           env=env, cwd=self.project, capture_output=True, timeout=30)
        self.assertEqual(r.returncode, 0, r.stderr.decode(errors="replace"))
        return json.loads(r.stdout)

    def test_picks_line_on_by_default_in_both_hosts(self):
        for env in ({}, {"PLUGIN_ROOT": str(PLUGIN)}):
            txt = self.context(self.run_hook(env))
            self.assertIn("`Skill Router: ` に続けて", txt)
            self.assertIn("Skill Router: 該当なし", txt)

    def test_picks_line_on_no_match(self):
        self.add_indexed_skill()
        txt = self.context(self.run_hook({}, "このCSVの列名を英語に揃えて、重複行を消して。"))
        self.assertIn("`Skill Router: ` に続けて", txt)

    def test_picks_line_can_be_turned_off(self):
        self.assertTrue(self.settings_cli("status")["show_picks"])
        self.assertFalse(self.settings_cli("show-picks", "off")["show_picks"])
        for env in ({}, {"PLUGIN_ROOT": str(PLUGIN)}):
            txt = self.context(self.run_hook(env))
            self.assertNotIn("Skill Router: ", txt)
            self.assertIn("- readme:", txt)
        # Turning routing off and on again keeps the display preference.
        self.settings_cli("off")
        self.assertFalse(self.settings_cli("project", "on")["show_picks"])

    def test_detailed_summary_replaces_picks_line(self):
        txt = self.context(self.run_hook({}, PROMPT + "\nスキル判断を表示"))
        self.assertNotIn("`Skill Router: ` に続けて", txt)

    def test_opt_out_and_small_talk_are_silent(self):
        self.assertEqual(self.run_hook({}, PROMPT + "\n今回はスキルを使わないで"), {})
        self.assertEqual(self.run_hook({}, "123 + 456 はいくつ？"), {})

    def test_disabled_folder_is_silent(self):
        (self.home / ".skill-router-beta/settings.json").write_text(json.dumps(
            {"all_projects": False, "folderless": False, "projects": []}), encoding="utf-8")
        self.assertEqual(self.run_hook({}), {})

    @unittest.skipIf(os.name == "nt", "Codex uses commandWindows on Windows")
    def test_shared_hooks_json_command(self):
        self.assertIn("【候補の扱い】", self.context(self.run_hook({}, shell=True)))
        self.assertIn("【スキル判断の作業ログ】", self.context(self.run_hook({"PLUGIN_ROOT": str(PLUGIN)}, shell=True)))

    def test_manifests(self):
        claude = json.loads((PLUGIN / ".claude-plugin/plugin.json").read_text(encoding="utf-8"))
        codex = json.loads((PLUGIN / ".codex-plugin/plugin.json").read_text(encoding="utf-8"))
        market = json.loads((ROOT / ".claude-plugin/marketplace.json").read_text(encoding="utf-8"))
        self.assertEqual(claude["name"], codex["name"])
        self.assertEqual(claude["version"], codex["version"])
        entry = next(p for p in market["plugins"] if p["name"] == claude["name"])
        self.assertTrue((ROOT / entry["source"]).resolve() == PLUGIN.resolve())
        for skill in ("skill-router-settings",):
            head = (PLUGIN / "skills" / skill / "SKILL.md").read_text(encoding="utf-8").split("---")[1]
            self.assertIn("disable-model-invocation: true", head)


if __name__ == "__main__":
    unittest.main()
