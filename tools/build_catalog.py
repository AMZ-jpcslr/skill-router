"""Build the bundled public-skill catalogue for skill-suggest (maintainers only).

Reads public manifests over HTTPS and keeps only name, a short description, the
source URL and how to install. Skill bodies are never copied.
Usage: python3 tools/build_catalog.py [--keywords] [OUT]
--keywords has the local Claude Code write Japanese search terms for each entry, with
the same index-time step the router uses for installed skills (keywords.py); entries
whose description has not changed keep the terms they already have.
"""
import json
import re
import runpy
import sys
import zlib
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from pathlib import Path

RAW = "https://raw.githubusercontent.com/{repo}/{ref}/{path}"
WEB = "https://github.com/{repo}/tree/{ref}/{path}"
OUT = Path(__file__).resolve().parents[1] / "plugins/skill-router/skills/skill-suggest/catalog.json"
NAME = re.compile(r"[a-z0-9][a-z0-9-]{0,79}\Z")

# Each source is read by a generic adapter for its manifest format.
SOURCES = [
    {"id": "anthropics/skills", "repo": "anthropics/skills", "ref": "main", "trust": "official",
     "adapter": "claude-marketplace", "manifest": ".claude-plugin/marketplace.json",
     "install": {"claude-code": "/plugin marketplace add anthropics/skills", "codex": "$skill-installer {url}"}},
    {"id": "openai/plugins", "repo": "openai/plugins", "ref": "main", "trust": "official",
     "adapter": "codex-marketplace", "manifest": ".agents/plugins/marketplace.json",
     "install": {"codex": "/plugins"}},
    {"id": "obra/superpowers", "repo": "obra/superpowers", "ref": "main", "trust": "curated",
     "adapter": "readme-list", "manifest": "README.md", "skills_dir": "skills",
     "install": {"claude-code": "/plugin install superpowers@claude-plugins-official", "codex": "$skill-installer {url}"}},
    {"id": "sickn33/antigravity-awesome-skills", "repo": "sickn33/antigravity-awesome-skills", "ref": "main",
     "trust": "community", "adapter": "skills-index", "manifest": "skills_index.json", "risk": ["safe"],
     "install": {"claude-code": "npx antigravity-awesome-skills --claude", "codex": "$skill-installer {url}"}},
]


def get(url, limit=8_000_000):
    with urllib.request.urlopen(url, timeout=30) as r:
        return r.read(limit).decode("utf-8")


def front_matter(text):
    m = re.match(r"﻿?---\r?\n(.*?)\r?\n---", text, re.S)
    if not m:
        return None, None
    fields, key = {}, None
    for line in m.group(1).splitlines():
        if key and (line.startswith((" ", "\t")) or not line.strip()):
            fields[key] += " " + line.strip()
            continue
        key = None
        k, sep, v = line.partition(":")
        if sep and k in ("name", "description"):
            v = v.strip()
            if re.fullmatch(r"[|>][+-]?", v):
                fields[k], key = "", k
            else:
                fields[k] = v.strip("'\"")
    return fields.get("name"), fields.get("description")


def short(text, limit=300):
    text = " ".join(str(text or "").split())
    return text if len(text) <= limit else text[:limit - 1] + "…"


def topic(value):
    """A source's own category label, normalised; the catalogue adds none of its own."""
    value = " ".join(str(value or "").replace("_", "-").split()).casefold()
    return value if 2 <= len(value) <= 40 else None


def skill_entry(src, path):
    try:
        name, desc = front_matter(get(RAW.format(path=f"{path}/SKILL.md", **src)))
    except OSError:
        return None
    if not name or not desc:
        return None
    return {"name": name, "description": short(desc), "url": WEB.format(path=path, **src), "kind": "skill"}


def read_source(src):
    manifest = get(RAW.format(path=src["manifest"], **src))
    if src["adapter"] == "claude-marketplace":
        paths = [p.removeprefix("./") for plugin in json.loads(manifest)["plugins"] for p in plugin.get("skills", [])]
        with ThreadPoolExecutor(8) as pool:
            return [e for e in pool.map(lambda p: skill_entry(src, p), dict.fromkeys(paths)) if e]
    if src["adapter"] == "readme-list":
        names = dict.fromkeys(re.findall(r"(?m)^\s*[-*]\s+\*\*([a-z0-9][a-z0-9-]+)\*\*", manifest))
        with ThreadPoolExecutor(8) as pool:
            return [e for e in pool.map(lambda n: skill_entry(src, f"{src['skills_dir']}/{n}"), names) if e]
    if src["adapter"] == "codex-marketplace":
        def plugin(p):
            path = (p.get("source") or {}).get("path") if isinstance(p.get("source"), dict) else None
            if not path:  # plugins hosted elsewhere are skipped
                return None
            path = path.removeprefix("./")
            try:
                m = json.loads(get(RAW.format(path=f"{path}/.codex-plugin/plugin.json", **src)))
            except (OSError, ValueError):
                return None
            ui = m.get("interface") or {}
            desc = ui.get("longDescription") or m.get("description")
            if not desc:
                return None
            kw = ", ".join(m.get("keywords") or [])
            return {"name": m.get("name") or p["name"], "description": short(desc + (f" ({kw})" if kw else "")),
                    "url": WEB.format(path=path, **src), "kind": "plugin",
                    **({"category": topic(p.get("category") or ui.get("category"))} if topic(p.get("category") or ui.get("category")) else {})}
        with ThreadPoolExecutor(8) as pool:
            return [e for e in pool.map(plugin, json.loads(manifest)["plugins"]) if e]
    if src["adapter"] == "skills-index":
        out = []
        for row in json.loads(manifest):
            if src.get("risk") and row.get("risk") not in src["risk"]:
                continue
            if row.get("name") and row.get("description") and row.get("path"):
                out.append({"name": row["name"], "description": short(row["description"]),
                            "url": WEB.format(path=row["path"], **src), "kind": "skill",
                            **({"category": topic(row.get("category"))} if topic(row.get("category")) else {})})
        return out
    raise ValueError(src["adapter"])


def add_keywords(entries, previous):
    K = runpy.run_path(str(OUT.parents[1] / "skill-router-settings" / "keywords.py"))
    cached = {(e["url"], e["name"], e["description"]): e["keywords"] for e in previous if e.get("keywords")}
    rows = []
    for e in entries:
        terms = cached.get((e["url"], e["name"], e["description"]))
        if terms:
            e["keywords"] = terms
        else:
            rows.append(dict(e, path=e["url"], stamp=zlib.crc32(e["description"].encode())))
    if rows:
        saved, stats = K["update"](rows, {})
        print(stats, file=sys.stderr)
        for e in entries:
            if e["url"] in saved:
                e["keywords"] = saved[e["url"]]["keywords"]


def main():
    args = [a for a in sys.argv[1:] if a != "--keywords"]
    out = Path(args[0]) if args else OUT
    entries, seen = [], set()
    for src in SOURCES:  # earlier, more trusted sources win a name clash
        rows = read_source(src)
        print(f"{src['id']}: {len(rows)}", file=sys.stderr)
        for row in rows:
            if not NAME.fullmatch(row["name"]) or row["name"] in seen:
                continue
            seen.add(row["name"])
            entries.append({**row, "source": src["id"]})
    if "--keywords" in sys.argv:
        try:
            previous = json.loads(out.read_text(encoding="utf-8")).get("skills", [])
        except (OSError, ValueError):
            previous = []
        add_keywords(entries, previous)
    data = {"version": 2, "built": date.today().isoformat(),
            "sources": [{k: s[k] for k in ("id", "trust", "install")} | {"url": f"https://github.com/{s['repo']}"}
                        for s in SOURCES],
            "skills": entries}
    out.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8")
    print(f"{len(entries)} entries -> {out}", file=sys.stderr)


if __name__ == "__main__":
    main()
