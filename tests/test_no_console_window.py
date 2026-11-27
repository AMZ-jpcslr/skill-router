"""Programs the plugin starts in the background must not open console windows on Windows."""
import ast
from pathlib import Path
import runpy
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "plugins/skill-omakase"
CORE = PLUGIN / "skills/skill-omakase-settings"
LAUNCHERS = {"run", "Popen", "call", "check_call", "check_output"}


class NoConsoleWindow(unittest.TestCase):
    def test_every_launch_sets_creation_flags(self):
        missing = []
        for path in PLUGIN.rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                        and node.func.attr in LAUNCHERS and isinstance(node.func.value, ast.Name)
                        and node.func.value.id == "subprocess"):
                    continue
                named = {k.arg for k in node.keywords}
                spread = any(k.arg is None for k in node.keywords)
                if "creationflags" not in named and "start_new_session" not in named and not spread:
                    missing.append(f"{path.relative_to(ROOT)}:{node.lineno}")
        self.assertEqual(missing, [])

    def test_claude_runner_hides_console_on_windows(self):
        K = runpy.run_path(str(CORE / "keywords.py"))
        done = mock.Mock(returncode=0, stdout='{"structured_output": {"skills": []}}')
        with mock.patch("subprocess.run", return_value=done) as run:
            K["claude_runner"].__globals__["find_claude"] = lambda: "claude"
            K["claude_runner"]("prompt")
        kwargs = run.call_args.kwargs
        if K["os"].name == "nt":
            self.assertEqual(kwargs.get("creationflags", 0) & 0x08000000, 0x08000000)
        else:
            self.assertNotIn("creationflags", kwargs)


if __name__ == "__main__":
    unittest.main()
