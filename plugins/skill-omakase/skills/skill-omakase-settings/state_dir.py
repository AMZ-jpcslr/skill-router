"""Where Skill Omakase keeps its settings, index and records, and how earlier installs carry over.

The plugin was called Skill Router up to beta.9. Its folder was ~/.skill-router-beta and its
environment variables started with SKILL_ROUTER_. Both still work:

- each SKILL_ROUTER_* variable is read as the SKILL_OMAKASE_* variable of the same name when
  that one is not set;
- the first run after the rename moves ~/.skill-router-beta to ~/.skill-omakase, so settings,
  the skill index and records stay as they were. If the move fails (a file is open on Windows,
  say), the old folder is used in place until it can be moved.
"""
import os
from pathlib import Path

PREFIX = "SKILL_OMAKASE_"
LEGACY_PREFIX = "SKILL_ROUTER_"
NAME = ".skill-omakase"
LEGACY_NAME = ".skill-router-beta"


def adopt_legacy_env(environ=os.environ):
    for key, value in list(environ.items()):
        if key.startswith(LEGACY_PREFIX):
            environ.setdefault(PREFIX + key[len(LEGACY_PREFIX):], value)


def state_dir(home=None):
    adopt_legacy_env()
    explicit = os.environ.get(PREFIX + "STATE_DIR")
    if explicit:
        return Path(explicit)
    home = Path(home) if home else Path.home()
    new, old = home / NAME, home / LEGACY_NAME
    if not new.exists() and old.is_dir():
        try:
            old.rename(new)
        except OSError:
            return old if not new.exists() else new
    return new


adopt_legacy_env()
STATE_DIR = state_dir()
