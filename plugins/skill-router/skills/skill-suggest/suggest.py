"""Skill suggestions from how you already use your agent. Local and read-only.

Reads, only when run, the prompt history that Claude Code and Codex already keep on
this computer, plus their session logs for which skills were actually opened. It runs
each past request through the router's own matching to see what your installed skills
cover, then reports settings worth changing, recurring work no installed skill covers,
and public skills (bundled catalogue: names, descriptions, links) that fit that work.
Nothing is written except the router's own feature cache; no network; no prompt text
is printed, only counts and single words.
"""
import argparse
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import re
import runpy
import stat
import sys
import unicodedata
import zlib

HERE = Path(__file__).resolve().parent
CORE = HERE.parent / "skill-router-settings"
HOME = Path.home()
CLAUDE_HOME = Path(os.environ.get("CLAUDE_CONFIG_DIR") or HOME / ".claude")
CODEX_HOME = Path(os.environ.get("CODEX_HOME") or HOME / ".codex")
NAME = re.compile(r"[a-z0-9][a-z0-9-]{0,79}\Z")
SKILL_PATH = re.compile(r"[/\\]([a-z0-9][a-z0-9-]{0,79})[/\\]+SKILL\.md\b", re.I)
INVOKED = re.compile(r"(?:^|(?<=\s))[/$](?:[a-z0-9-]+:)?([a-z0-9][a-z0-9-]{1,79})(?![a-z0-9-])", re.I)
TERM = re.compile(r"[a-z][a-z0-9+#.-]{2,}|[ァ-ヿー]{3,}|[㐀-鿿]{2,6}")
OWN = {"skill-suggest", "skill-router-settings"}
TRUST = {"official": 0, "curated": 1, "community": 2}
MAX_PROMPTS = 5000
MAX_PROMPT_CHARS = 4000
MAX_LOG_BYTES = 400_000_000
# Claude Code lists skills with their descriptions within a character budget; this is
# its documented fallback, overridable as it is in Claude Code itself.
LIST_BUDGET = int(os.environ.get("SLASH_COMMAND_TOOL_CHAR_BUDGET") or 8000)


def plain_file(p):
    try:
        st = p.lstat()
    except OSError:
        return False
    return stat.S_ISREG(st.st_mode) and not getattr(st, "st_file_attributes", 0) & 0x400


def norm(s):
    return unicodedata.normalize("NFKC", s).casefold()


def jsonl(path, byte_budget):
    """Yield (line, obj) from a JSON-lines file; stop at the shared byte budget."""
    if not plain_file(path):
        return
    try:
        with path.open("rb") as f:
            for raw in f:
                byte_budget[0] -= len(raw)
                if byte_budget[0] < 0:
                    return
                yield raw
    except OSError:
        return


def when(value):
    """Epoch seconds/milliseconds or ISO-8601 -> aware datetime, else None."""
    try:
        if isinstance(value, (int, float)):
            return datetime.fromtimestamp(value / 1000 if value > 1e11 else value, timezone.utc)
        if isinstance(value, str):
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, OSError, OverflowError):
        return None
    return None


def text_of(content):
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(c.get("text", "") for c in content if isinstance(c, dict) and c.get("type") in ("text", "input_text"))
    return ""


def recent_files(root, pattern, since):
    try:
        files = [p for p in root.glob(pattern) if plain_file(p) and p.stat().st_mtime >= since.timestamp()]
    except OSError:
        return []
    return sorted(files, key=lambda p: p.stat().st_mtime, reverse=True)


def read_prompts(since, budget):
    """[(host, datetime, project or None, text)] from the hosts' own prompt history."""
    out = []
    for raw in jsonl(CLAUDE_HOME / "history.jsonl", budget):
        try:
            o = json.loads(raw)
            t = when(o.get("timestamp"))
            if t and t >= since and isinstance(o.get("display"), str):
                out.append(("claude-code", t, o.get("project"), o["display"]))
        except (ValueError, AttributeError):
            continue
    if not out:
        # Older or trimmed installs: the session transcripts hold the user's turns too.
        for path in recent_files(CLAUDE_HOME / "projects", "*/*.jsonl", since):
            for raw in jsonl(path, budget):
                if b'"type":"user"' not in raw.replace(b" ", b""):
                    continue
                try:
                    o = json.loads(raw)
                    t = when(o.get("timestamp"))
                    text = text_of((o.get("message") or {}).get("content"))
                    if (o.get("type") == "user" and not o.get("isMeta") and t and t >= since
                            and text.strip() and not text.lstrip().startswith("<")):
                        out.append(("claude-code", t, o.get("cwd"), text))
                except (ValueError, AttributeError):
                    continue
    codex = []
    for raw in jsonl(CODEX_HOME / "history.jsonl", budget):
        try:
            o = json.loads(raw)
            t = when(o.get("ts"))
            if t and t >= since and isinstance(o.get("text"), str):
                codex.append(("codex", t, None, o["text"]))
        except (ValueError, AttributeError):
            continue
    if not codex:
        # history.persistence may be off; the session logs still hold the user's turns.
        for path in recent_files(CODEX_HOME / "sessions", "**/*.jsonl", since):
            for raw in jsonl(path, budget):
                if b'"user_message"' not in raw:
                    continue
                try:
                    o = json.loads(raw)
                    p = o.get("payload") or {}
                    t = when(o.get("timestamp"))
                    if p.get("type") == "user_message" and t and t >= since and isinstance(p.get("message"), str):
                        codex.append(("codex", t, None, p["message"]))
                except (ValueError, AttributeError):
                    continue
    out += codex
    seen, unique = set(), []
    for host, t, project, text in sorted(out, key=lambda x: x[1], reverse=True):
        key = (t.date(), text.strip())
        if text.strip() and key not in seen:
            seen.add(key)
            unique.append((host, t, project, text[:MAX_PROMPT_CHARS]))
    return unique[:MAX_PROMPTS]


def tool_calls(node):
    """Tool-call nodes in one log record, for both hosts' log formats."""
    stack = [node]
    while stack:
        x = stack.pop()
        if isinstance(x, dict):
            if x.get("type") in ("tool_use", "function_call", "custom_tool_call", "local_shell_call"):
                yield x
                continue
            stack.extend(x.values())
        elif isinstance(x, list):
            stack.extend(x)


def read_skill_use(since, budget):
    """{skill name: set(days)} for skills an agent opened or invoked (not merely listed)."""
    used = defaultdict(set)
    logs = recent_files(CLAUDE_HOME / "projects", "*/*.jsonl", since) + recent_files(CODEX_HOME / "sessions", "**/*.jsonl", since)
    for path in logs:
        for raw in jsonl(path, budget):
            if b"SKILL.md" not in raw and b'"Skill"' not in raw:
                continue
            try:
                o = json.loads(raw)
            except ValueError:
                continue
            t = when(o.get("timestamp"))
            if not t or t < since:
                continue
            for call in tool_calls(o):
                args = call.get("input") or call.get("arguments") or call.get("action") or ""
                if call.get("name") == "Skill" and isinstance(args, dict) and isinstance(args.get("skill"), str):
                    used[args["skill"].split(":")[-1]].add(t.date())
                for m in SKILL_PATH.finditer(args if isinstance(args, str) else json.dumps(args)):
                    used[m.group(1).lower()].add(t.date())
    return used


def engine():
    os.environ.setdefault("SKILL_ROUTER_STATE_DIR", str(HOME / ".skill-router-beta"))
    L = runpy.run_path(str(CORE / "local_index.py"))
    R = runpy.run_path(str(CORE / "router.py"))
    return L, R


def catalog(L):
    raw = json.loads((HERE / "catalog.json").read_text(encoding="utf-8"))
    sources = {s["id"]: s for s in raw["sources"]}
    rows = [dict(r, path=r["url"], stamp=[zlib.crc32(r["description"].encode())]) for r in raw["skills"] if NAME.fullmatch(r.get("name", ""))]
    return rows, sources


def soft_matcher(L, rows, trust=None):
    """Looser than routing: which one catalogue entry a request is most about.
    Shared rare words (product, tool, file-format names) and Japanese n-grams, weighted
    by rarity across the catalogue; one clear best entry or none."""
    import math
    O = L["OPEN"]
    feats = []
    df = Counter()
    for r in rows:
        name = O["features"](r["name"].replace("-", " "))
        meta = O["features"](r["name"].replace("-", " ") + " " + r["description"] + " " + " ".join(r.get("keywords") or []))
        f = (name[0], meta[0], meta[1])
        feats.append(f)
        df.update(f[1] | f[2])
    size = max(1, len(rows))
    idf = lambda t: math.log(1 + size / (1 + df[t]))

    def match(text, k=1):
        parts = O["work_clauses"](text) or [text]
        ql, qj, qc = O["features"](" ".join(parts))
        ql = {t for t in ql if not (qc & O["OPS"] and O["features"](t)[2] & O["OPS"])}
        if not ql and not qj:
            return [] if k > 1 else None
        scored = []
        for r, (nl, dl, dj) in zip(rows, feats):
            lat = ql & dl
            jp = qj & dj
            if not lat and len(jp) < 4:
                continue
            score = sum(idf(t) * (1.6 if t in nl else 1.0) for t in lat) + 0.25 * sum(idf(t) for t in jp)
            scored.append((score, -len(r["description"]), r))
        if not scored:
            return [] if k > 1 else None
        scored.sort(key=lambda x: (-x[0], x[1], x[2]["name"]))
        top = scored[0][0]
        if k > 1:
            return [r for sc, _, r in scored[:k] if sc >= 4.0 and sc >= top * 0.75]
        if top < 4.0:
            return None
        near = [r for sc, _, r in scored if sc > top * 0.9]
        if len(near) == 1:
            return near[0]
        # Near-equal public entries: an official or curated source settles it; a tie
        # within the same tier, or with an installed skill, stays unresolved.
        tiers = sorted(near, key=lambda r: TRUST.get((trust or {}).get(r.get("source")), 9))
        a, b = (TRUST.get((trust or {}).get(r.get("source")), 9) for r in tiers[:2])
        return tiers[0] if a < b and all(r.get("source") != "local" for r in near) else None
    return match


def public_row(r, sources, **extra):
    src = sources.get(r["source"], {})
    return {"skill": r["name"], "kind": r["kind"], **extra, "url": r["url"], "source": r["source"],
            "trust": src.get("trust"), "description": r["description"],
            "install": {k: v.format(url=r["url"]) for k, v in (src.get("install") or {}).items()}}


def terms(text, drop=()):
    """Single words of a request (Latin words, katakana runs, short kanji runs)."""
    found = set()
    for t in TERM.findall(norm(text)):
        t = t.strip(".-")
        if len(t) >= 2 and t not in drop:
            found.add(t)
    return found


def stop_terms(L):
    O = L["OPEN"]
    return set(O["GENERIC"]) | {"the", "and", "this", "that", "with", "for", "please", "してください", "ください", "お願い"}


def analyse(days=60, today=None, include_public=True):
    L, R = engine()
    today = today or datetime.now(timezone.utc)
    since = today - timedelta(days=days)
    budget = [MAX_LOG_BYTES]
    prompts = read_prompts(since, budget)
    used = read_skill_use(since, budget)
    index = L["read_json"](L["INDEX"], {})
    all_records = index.get("records", []) if isinstance(index, dict) else []
    records = L["with_keywords"](L["one_per_name"](all_records))
    prepared = L["prepared_features"](records)
    names = {r["name"] for r in all_records}
    work_clauses = L["OPEN"]["work_clauses"]
    stop = stop_terms(L)

    matched = defaultdict(set)          # skill -> days the router would offer it
    named = defaultdict(list)           # skill -> [(day, routed?, text)] named by hand
    uncovered = []                      # (day, text) work with no local skill or guide
    projects = Counter()
    work = 0
    hosts = Counter()
    for host, t, project, text in prompts:
        hosts[host] += 1
        day = t.date()
        called = {m.group(1).lower() for m in INVOKED.finditer(text)} & names
        bare = INVOKED.sub(" ", text) if called else text
        parts = work_clauses(bare)
        if not parts and not called:
            continue
        work += 1
        if project:
            projects[project] += 1
        guides = R["narrow_pick"]("。".join(parts)) if parts else []
        offered = set()
        if parts:
            try:
                offered = {row["name"] for _, row in L["rank"](bare, records, guides, None, prepared)}
            except (ValueError, KeyError, TypeError, RuntimeError):
                offered = set()
        for n in offered:
            matched[n].add(day)
        for n in called - OWN:
            named[n].append((day, n in offered, bare))
        if parts and not offered and not guides and not called:
            uncovered.append((day, bare))

    report = {"period_days": days, "prompts_read": len(prompts), "work_requests": work,
              "hosts": dict(hosts), "installed_skills": len(names),
              "settings": [], "gaps": [], "public": []}
    if not prompts:
        report["note"] = "no_history"
    settings = report["settings"]

    if not all_records:
        settings.append({"kind": "index_missing", "action": "router.py index refresh"})

    # 1. Skills named by hand that the router would not have offered: the words the
    #    user actually says are the ones their description lacks.
    for n, rows in sorted(named.items(), key=lambda kv: -len({d for d, *_ in kv[1]})):
        missed = [r for r in rows if not r[1]]
        if len({d for d, *_ in rows}) < 2 or not missed:
            continue
        row = next((r for r in records if r["name"] == n), None)
        desc = norm((row or {}).get("description", "") + " " + " ".join((row or {}).get("keywords", [])) + " " + n)
        counts = Counter()
        for _, _, text in missed:
            counts.update(w for w in terms(text, stop) if w not in desc)
        words = [w for w, c in counts.most_common(8) if c >= 2][:5]
        settings.append({"kind": "named_by_hand", "skill": n, "days": len({d for d, *_ in rows}),
                         "router_offered_days": len({d for d, ok, _ in rows if ok}),
                         "add_to_description": words, "path": (row or {}).get("path")})

    # 2. Claude Code lists ~/.claude/skills itself, within a character budget. Skills
    #    there that went unused cost that budget every turn; the router still finds them
    #    from ~/.agents/skills.
    native_root = (CLAUDE_HOME / "skills").as_posix().casefold().rstrip("/") + "/"
    native = [r for r in all_records if Path(r["path"]).as_posix().casefold().startswith(native_root)]
    cost = sum(len(r["name"]) + len(r["description"]) + 4 for r in native)
    if native and (cost > LIST_BUDGET * 0.5 or len(native) > 40):
        idle = [r for r in native if r["name"] not in used and r["name"] not in matched and r["name"] not in named]
        idle.sort(key=lambda r: -len(r["description"]))
        settings.append({"kind": "claude_list_budget", "native_skills": len(native), "listed_chars": cost,
                         "budget_chars": LIST_BUDGET, "unused": [{"skill": r["name"], "chars": len(r["description"])} for r in idle[:10]],
                         "unused_total": len(idle), "move_to": (HOME / ".agents" / "skills").as_posix()})

    # 3. The same name in several places: only one copy is loaded.
    by_name = defaultdict(list)
    for r in all_records:
        by_name[r["name"]].append(r)
    dups = [{"skill": n, "paths": [r["path"] for r in rs], "same_size": len({r["stamp"][1] for r in rs}) == 1}
            for n, rs in sorted(by_name.items()) if len(rs) > 1 and n not in OWN]
    if dups:
        settings.append({"kind": "duplicates", "count": len(dups), "items": dups[:10]})

    # 4. Skills nothing used, matched or named during the period.
    idle = sorted(n for n in names - OWN if n not in used and n not in matched and n not in named)
    if names and work >= 20:
        settings.append({"kind": "unused", "count": len(idle), "of": len(names - OWN), "examples": idle[:10]})

    # 5. Japanese requests against English-only descriptions: index-time keywords help.
    jp_share = sum(bool(re.search(r"[぀-ヿ]", t)) for *_, t in prompts) / max(1, len(prompts))
    with_kw = sum(bool(r.get("keywords")) for r in records)
    if jp_share >= 0.3 and records and with_kw < len(records) * 0.5:
        settings.append({"kind": "keywords_missing", "japanese_share": round(jp_share, 2),
                         "skills_with_keywords": with_kw, "action": "router.py index refresh --keywords"})

    # 6. Work done in folders the router is switched off for (Claude Code records the folder).
    try:
        conf = R["read_config"](R["STATE"])
        off = Counter()
        for project, c in projects.items():
            kind, root = R["scope"](project)
            if kind == "project" and not R["active"](kind, root, conf):
                off[Path(project).name or project] += c
        if sum(off.values()) >= 5 and sum(off.values()) >= 0.3 * sum(projects.values()):
            settings.append({"kind": "router_off_for_folders", "requests": sum(off.values()),
                             "folders": [n for n, _ in off.most_common(5)], "action": "router.py all-projects on"})
    except (OSError, ValueError, KeyError, TypeError):
        pass

    # Recurring work with nothing installed for it; matched against public skills
    # with the very same retrieval the router uses for local ones.
    # Requests the router passed over are matched again, more loosely, against installed
    # and public skills together: a public skill is suggested only where no installed one
    # comes close, and an installed one that does is a description worth improving.
    public_days = defaultdict(set)
    missed_local = defaultdict(set)
    leftover = []
    if uncovered:
        pub_rows, sources = catalog(L) if include_public else ([], {})
        pool = [dict(r, source="local") for r in records] + [r for r in pub_rows if r["name"] not in names]
        match = soft_matcher(L, pool, {k: v.get("trust") for k, v in sources.items()})
        for day, text in uncovered:
            near = match(text, 3)
            local = [r for r in near if r["source"] == "local"]
            if local:
                for r in local[:1]:
                    missed_local[r["name"]].add(day)
                continue
            best = match(text)
            if best and best["source"] != "local":
                public_days[best["path"]].add(day)
            else:
                leftover.append((day, text))
        by_path = {r["path"]: r for r in pool}
        ranked = sorted(public_days.items(), key=lambda kv: (-len(kv[1]), by_path[kv[0]]["name"]))
        for path, ds in ranked:
            if len(ds) < 2:
                continue
            report["public"].append(public_row(by_path[path], sources, days=len(ds)))
            if len(report["public"]) >= 8:
                break
    near_missed = [{"skill": n, "days": len(d)} for n, d in sorted(missed_local.items(), key=lambda kv: (-len(kv[1]), kv[0])) if len(d) >= 2]
    if near_missed:
        settings.insert(0, {"kind": "installed_but_not_selected", "items": near_missed[:10],
                            "action": "router.py index refresh --keywords" if not any(r.get("keywords") for r in records) else None})
    term_days = defaultdict(set)
    for day, text in leftover:
        for w in terms(text, stop):
            if not (L["ACTION"].search(w) or L["OPEN"]["REQUEST"].search(w) or L["OPEN"]["features"](w)[2] & L["OPEN"]["OPS"]):
                term_days[w].add(day)
    report["gaps"] = [{"term": w, "days": len(d)} for w, d in sorted(term_days.items(), key=lambda kv: (-len(kv[1]), kv[0])) if len(d) >= 3][:10]
    report["uncovered_requests"] = len(uncovered)
    return report


def find(description, include_public=True):
    """One-shot: which installed or public skill fits work described in words."""
    L, R = engine()
    index = L["read_json"](L["INDEX"], {})
    records = L["with_keywords"](L["one_per_name"](index.get("records", []) if isinstance(index, dict) else []))
    guides = R["narrow_pick"]("。".join(L["OPEN"]["work_clauses"](description)))
    local = [{"skill": r["name"], "path": r["path"], "basis": "router"}
             for _, r in L["rank"](description, records, guides, None, L["prepared_features"](records))]
    rows, sources = catalog(L) if include_public else ([], {})
    have = {r["name"] for r in records}
    pool = [dict(r, source="local") for r in records] + [r for r in rows if r["name"] not in have]
    near = soft_matcher(L, pool, {k: v.get("trust") for k, v in sources.items()})(description, 5)
    known = {x["skill"] for x in local}
    local += [{"skill": r["name"], "path": r["path"], "basis": "description_overlap"}
              for r in near if r["source"] == "local" and r["name"] not in known]
    out = {"local": local[:5], "builtin_guides": guides, "public": []}
    if not local:
        out["public"] = [public_row(r, sources) for r in near if r["source"] != "local"][:3]
    return out


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)
    rep = sub.add_parser("report", help="settings, gaps and public skills from your own history")
    rep.add_argument("--days", type=int, choices=range(7, 181), default=60, metavar="7-180")
    rep.add_argument("--no-public", action="store_true")
    f = sub.add_parser("find", help="skills for work you describe")
    f.add_argument("description")
    f.add_argument("--no-public", action="store_true")
    a = p.parse_args(argv)
    try:
        out = analyse(a.days, include_public=not a.no_public) if a.cmd == "report" else find(a.description, not a.no_public)
    except (OSError, ValueError) as e:
        p.exit(2, f"{type(e).__name__}: {e}\n")
    sys.stdout.write(json.dumps(out, ensure_ascii=False, indent=1) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
