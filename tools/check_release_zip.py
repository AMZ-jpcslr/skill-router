"""Checks a release ZIP the way a user receives it.

    python tools/check_release_zip.py              # builds the ZIP with `git archive` from the checkout
    python tools/check_release_zip.py some.zip     # checks a ZIP that was already built

It fails when a Markdown file in the ZIP links to a file the ZIP does not contain (an image
or video left out by .gitattributes, a renamed page), when a JSON file does not parse, when a
file a hook or manifest names is missing, when the two plugin manifests disagree on the
version, or when development-only files (tests, tools, personal state) were packed.
"""
import json
from pathlib import Path, PurePosixPath
import re
import subprocess
import sys
import tempfile
import zipfile
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parent.parent
LINK = re.compile(r"!?\[[^\]]*\]\(\s*<?([^)\s>]+)>?(?:\s+\"[^\"]*\")?\s*\)")
HTML_SRC = re.compile(r"""<(?:img|video|source|a)\b[^>]*?\b(?:src|href)\s*=\s*["']([^"']+)["']""", re.I)
FENCE = re.compile(r"(?ms)^[ \t]*(`{3,}|~{3,}).*?^[ \t]*\1[^\n]*")
REQUIRED = (
    "README.md",
    "LICENSE",
    "plugins/skill-router/.claude-plugin/plugin.json",
    "plugins/skill-router/.codex-plugin/plugin.json",
    "plugins/skill-router/hooks/hooks.json",
)
NOT_SHIPPED = re.compile(r"(?:^|/)(?:tests|tools|\.github|__pycache__)/|\.pyc$|(?:^|/)skill-(?:index|auto|router-settings)[^/]*\.json$|(?:^|/)settings\.json$")
HOOK_FILE = re.compile(r"[\w./${}-]*?([\w-]+\.py)\b")


def build(out):
    """ZIP of the checkout as `git archive` packs it. Uncommitted edits to tracked files are
    included (through a stash commit that is not stored), so a check before committing sees them."""
    git = ["git", "-C", str(ROOT)]
    tree = subprocess.run([*git, "stash", "create"], capture_output=True, text=True, check=True).stdout.strip() or "HEAD"
    subprocess.run([*git, "archive", "--format=zip", "-o", out, tree], check=True)
    return out


def links(text):
    text = FENCE.sub("", text)
    for rx in (LINK, HTML_SRC):
        for m in rx.finditer(text):
            yield m.group(1)


def problems(zip_path):
    out = []
    with zipfile.ZipFile(zip_path) as z:
        names = {n.rstrip("/") for n in z.namelist()}
        files = {n for n in z.namelist() if not n.endswith("/")}
        dirs = {str(p) for n in files for p in PurePosixPath(n).parents if str(p) != "."}
        for need in REQUIRED:
            if need not in files:
                out.append(f"missing: {need}")
        for n in sorted(files):
            if NOT_SHIPPED.search(n):
                out.append(f"development file packed: {n}")
            if n.endswith(".json"):
                try:
                    json.loads(z.read(n).decode("utf-8"))
                except ValueError as err:
                    out.append(f"{n}: not valid JSON ({err})")
            if n.endswith(".md"):
                base = PurePosixPath(n).parent
                for target in links(z.read(n).decode("utf-8")):
                    u = urlsplit(target)
                    if u.scheme or u.netloc or target.startswith(("#", "mailto:")):
                        continue
                    rel = unquote(u.path)
                    if not rel:
                        continue
                    parts = []
                    for p in (base / rel).parts:
                        if p == "..":
                            if not parts:
                                parts = None
                                break
                            parts.pop()
                        elif p != ".":
                            parts.append(p)
                    path = "/".join(parts) if parts else None
                    if path is None or not (path in files or path in dirs or path in names):
                        out.append(f"{n}: link to a file not in the ZIP: {target}")
        hooks = "plugins/skill-router/hooks/hooks.json"
        if hooks in files:
            for script in set(HOOK_FILE.findall(z.read(hooks).decode("utf-8"))):
                if not any(f.endswith("/" + script) for f in files):
                    out.append(f"{hooks}: names {script}, not in the ZIP")
        versions = {}
        for m in ("plugins/skill-router/.claude-plugin/plugin.json", "plugins/skill-router/.codex-plugin/plugin.json"):
            if m in files:
                try:
                    versions[m] = json.loads(z.read(m).decode("utf-8")).get("version")
                except ValueError:
                    pass
        if len(set(versions.values())) > 1:
            out.append(f"plugin versions differ: {versions}")
    return out


def main(argv):
    if argv:
        found = problems(argv[0])
    else:
        with tempfile.TemporaryDirectory() as d:
            found = problems(build(str(Path(d) / "skill-router.zip")))
    for p in found:
        print(p)
    print("release ZIP: " + ("OK" if not found else f"{len(found)} problem(s)"))
    return 1 if found else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
