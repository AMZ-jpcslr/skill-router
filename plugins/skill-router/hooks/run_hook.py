"""Host-neutral launcher for the Skill Router UserPromptSubmit hook.

Codex sets PLUGIN_ROOT (and CLAUDE_PLUGIN_ROOT for compatibility); Claude Code
sets only CLAUDE_PLUGIN_ROOT. The host decides how the decision protocol is
worded; settings and the skill index are shared between hosts.
"""
import os
from pathlib import Path
import runpy
import sys

if not os.environ.get("PLUGIN_ROOT"):
    os.environ.setdefault("SKILL_ROUTER_HOST", "claude-code")
root = Path(__file__).resolve().parent.parent
sys.argv[1:] = ["hook"]
runpy.run_path(str(root / "skills" / "skill-router-settings" / "router.py"), run_name="__main__")
