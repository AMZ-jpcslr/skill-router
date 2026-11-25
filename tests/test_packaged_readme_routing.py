"""Run the packaged hook entry point with an isolated, synthetic skills index."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

PLUGIN = Path(__file__).resolve().parents[1] / "plugins/skill-router"
ROUTER = PLUGIN / "skills/skill-router-settings/router.py"
HOOK = json.loads((PLUGIN / "hooks/hooks.json").read_text(encoding="utf-8"))
HOOK = HOOK["hooks"]["UserPromptSubmit"][0]["hooks"][0]


class PackagedReadmeRouting(unittest.TestCase):
    def test_same_hook_received_by_codex(self):
        with tempfile.TemporaryDirectory() as folder:
            home = Path(folder)
            project = home / "trial"
            project.mkdir()
            # Codex sets CLAUDE_PLUGIN_ROOT alongside PLUGIN_ROOT for compatibility.
            env = dict(os.environ, HOME=folder, USERPROFILE=folder, PLUGIN_ROOT=str(PLUGIN), CLAUDE_PLUGIN_ROOT=str(PLUGIN))
            env["PATH"] = str(Path(sys.executable).parent) + os.pathsep + env["PATH"]

            def run(command, payload=None):
                result = subprocess.run(command, cwd=project, env=env, input=payload,
                                        capture_output=True, check=True, timeout=10)
                return json.loads(result.stdout)

            for name, description in {
                "readme": "README documentation and technical writing.",
                "nihongo": "Japanese writing and Japanese proofreading; polish Japanese prose.",
                "synthetic-motion": "Best practices for video creation in React. Render title animations and motion graphics.",
            }.items():
                path = home / ".agents/skills" / name / "SKILL.md"
                path.parent.mkdir(parents=True)
                path.write_text(f"---\nname: {name}\ndescription: {description}\n---\n", encoding="utf-8")
            cli = [sys.executable, "-I", "-B", "-X", "utf8", str(ROUTER)]
            self.assertEqual(run(cli + ["index", "refresh"])["indexed"], 3)
            self.assertTrue(run(cli + ["project", "on"])["current_active"])
            hook_command = HOOK["commandWindows"] if os.name == "nt" else ["sh", "-c", HOOK["command"]]
            cases = [
                ("READMEを読みやすくしてください。", {"readme"}),
                ("Rewrite the README for new users.", {"readme"}),
                ("Please revise README.md for first-time contributors.", {"readme"}),
                ("Polish the README for first-time contributors.", {"readme"}),
                ("READMEを推敲してください。", {"readme"}),
                ("Make the README easier to read.", {"readme"}),
                ("READMEの説明文を初めて読む人にも自然な日本語に直して。", {"readme", "nihongo"}),
                ("READMEのリンク先だけ直してください。", {"readme"}),
                ("READMEに「日本語を読みやすくしてください。」をそのまま1行だけ追加してください。", set()),
                ("READMEを読みやすくする方法を教えてください。", set()),
                ("READMEを読みやすくしないでください。", set()),
                ("Explain how to rewrite the README for new users.", set()),
                ("Do not rewrite the README.", set()),
                ("今回は自動選択を使わないで。READMEを読みやすくしてください。", set()),
                ("Make a two-second title video and export it as MP4.", {"synthetic-motion"}),
                ("参考例：READMEを書き直す画面。2秒の紹介動画を作って。", {"synthetic-motion"}),
            ]
            for prompt, expected in cases:
                with self.subTest(prompt=prompt):
                    payload = json.dumps({"hook_event_name": "UserPromptSubmit",
                                          "prompt": prompt, "cwd": str(project)}).encode("utf-8")
                    output = run(hook_command, payload)
                    context = output.get("hookSpecificOutput", {}).get("additionalContext", "")
                    received = {name for name in ("readme", "nihongo", "synthetic-motion")
                                if f'"name": "{name}"' in context}
                    self.assertEqual(received, expected)
                    self.assertEqual("- readme:" in context, "readme" in expected)
                    # With nothing matched, only the line on searching in the agent's own words is added.
                    rest = context.split("導入済みスキルの候補が上になく")[0]
                    self.assertEqual(bool(rest.strip()), bool(expected))


if __name__ == "__main__":
    unittest.main()
