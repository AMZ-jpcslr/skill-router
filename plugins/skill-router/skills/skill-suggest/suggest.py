"""Skill suggestions from how you already use your agent. Local and read-only.

Reads, only when run, the prompt history that Claude Code and Codex already keep on
this computer, plus their session logs for which skills were actually opened. It runs
each past request through the router's own matching to see what your installed skills
cover, then reports settings worth changing, recurring work no installed skill covers,
and public skills (bundled catalogue: names, descriptions, links) that fit that work.
It also reads the router's own record of requests no installed skill was used for
(no-fit.jsonl, written by the Stop hook on this computer: dates and single words).
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
INVOKED = re.compile(r"(?:^|(?<=\s))[/$](?:[a-z0-9-]+:)?([a-z0-9][a-z0-9-]{0,79})(?![a-z0-9_:/\\-]|\.\S)", re.I)
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
            # Python 3.10 accepts only three or six fractional digits.
            stamp = re.sub(r"(?<=:\d\d)[.,](\d+)", lambda m: "." + m[1][:6].ljust(6, "0"), value)
            parsed = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
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
            if not isinstance(o, dict):
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


def borrow_keywords(records, L):
    """An installed skill that is also in the catalogue, under the same name and without
    search terms of its own, takes the catalogue's (written from the same description)."""
    try:
        rows, _ = catalog(L)
    except (OSError, ValueError, KeyError):
        return records
    terms = {r["name"]: r["keywords"] for r in rows if r.get("keywords")}
    return [r if r.get("keywords") or r["name"] not in terms else dict(r, keywords=terms[r["name"]], borrowed_keywords=True)
            for r in records]


def catalog(L):
    raw = json.loads((HERE / "catalog.json").read_text(encoding="utf-8"))
    sources = {s["id"]: s for s in raw["sources"]}
    rows = [dict(r, path=r["url"], stamp=[zlib.crc32(r["description"].encode())]) for r in raw["skills"] if NAME.fullmatch(r.get("name", ""))]
    return rows, sources


def soft_matcher(L, rows, trust=None):
    """A shortlist (k > 1) merges two rankings by position: shared rare words, which
    find a skill named in the request (typescript, swift), and coverage of the whole
    request, which keeps one frequent word from pushing unrelated entries up. An entry
    both rank high leads; one only a single ranking finds still has a place. A single
    best entry (k = 1) is decided by shared words alone."""
    words, cover = word_matcher(L, rows, trust), coverage_matcher(L, rows, trust)

    def match(text, k=1, keep=0.75):
        if k == 1:
            return words(text)
        score = {}
        for ranked in (cover(text, k, keep), words(text, k, keep)):
            for i, r in enumerate(ranked):
                key = r["path"]
                score[key] = (score.get(key, (0, r))[0] + 1 / (10 + i), r)
        return [r for _, r in sorted(score.values(), key=lambda x: (-x[0], x[1]["name"]))[:k]]
    return match


def word_matcher(L, rows, trust=None):
    """Looser than routing: which catalogue entry a request is most about.
    Shared words (product, tool and file-format names) and the Japanese stretches of the
    request that an entry also says, each weighted by how rare it is across the entries;
    one clear best entry or none."""
    import math
    O = L["OPEN"]
    feats, texts, kws = [], [], []
    df = Counter()
    strip = lambda w: re.sub(r"[\u3041-\u309f\s]+", "", w)
    for r in rows:
        kws.append(([strip(O["normalize"](k)) for k in r.get("keywords") or []],
                    [O["features"](k)[2] for k in r.get("keywords") or []]))
        text = O["normalize"](r["name"].replace("-", " ") + " " + r["description"] + " " + " ".join(r.get("keywords") or []))
        name = O["features"](r["name"].replace("-", " "))
        meta = O["features"](text)
        feats.append((name[0], meta[0], meta[1], meta[2]))
        texts.append(text)
        df.update(meta[0])
    size = max(1, len(rows))
    idf = lambda n: math.log(1 + size / (1 + n))
    seen = {}

    def span_df(span):
        if span not in seen:
            seen[span] = sum(span in t for t in texts)
        return seen[span]

    def spans(query, grams):
        """Maximal stretches of the request covered by shared n-grams, without the
        hiragana (particles, endings) at their edges: ファイルを -> ファイル."""
        out = set()
        for run in O["JP"].findall(query):
            cover = [False] * len(run)
            for n in (2, 3):
                for i in range(len(run) - n + 1):
                    if run[i:i + n] in grams:
                        cover[i:i + n] = [True] * n
            i = 0
            while i < len(run):
                if not cover[i]:
                    i += 1
                    continue
                j = i
                while j < len(run) and cover[j]:
                    j += 1
                piece = re.sub(r"^[\u3041-\u309f]+|[\u3041-\u309f]+$", "", run[i:j])
                if len(piece) >= 2:
                    out.add(piece)
                i = j
        return out

    def match(text, k=1, keep=0.75):
        parts = O["work_clauses"](text) or [text]
        query = O["normalize"](" ".join(parts))
        ql, qj, qc = O["features"](query)
        ql = {t for t in ql if not (qc & O["OPS"] and O["features"](t)[2] & O["OPS"])}
        if not ql and not qj:
            return [] if k > 1 else None
        scored = []
        for r, (nl, dl, dj, dc), body, (kw, kconcepts) in zip(rows, feats, texts, kws):
            lat = ql & dl
            grams = qj & dj
            if not lat and not grams:
                continue
            # A stretch counts when it is most of one of the entry's search terms
            # (スライド of スライド作成), or a longer phrase its description says itself.
            jp = {p for p in spans(query, grams) if p in body and (
                  any(p in k and 2 * len(p) >= len(k) for k in kw) or (len(p) >= 3 and p in body[:len(body) - len(" ".join(r.get("keywords") or []))]))}
            # Operation words alone (作成, 修正) say what to do, not what about.
            jp = {p for p in jp if not (O["features"](p)[2] and O["features"](p)[2] <= O["OPS"])}
            # Without a shared word, one short stretch (入力, 計算) is too little to go on,
            # unless it names the same subject the router recognises in both (動画: video).
            if not lat and not (len(jp) >= 2 or any(len(p) >= 3 for p in jp) or (jp and (qc & dc) - O["OPS"])):
                continue
            # The same subject and operation in both, whatever the words (動画制作 and
            # "video creation" are both video + create), as the router's concepts tell.
            score = (sum(idf(df[t]) * (1.6 if t in nl else 1.0) for t in lat)
                     + sum(idf(span_df(p)) for p in jp)
                     + (1.5 * len(qc & dc) if (qc & dc) - O["OPS"] else 0)
                     # One of its search terms means the whole request: 動画作成 for 動画制作.
                     + (3.0 if qc - O["OPS"] and any(qc <= c for c in kconcepts) else 0))
            scored.append((score, -len(r["description"]), r))
        if not scored:
            return [] if k > 1 else None
        scored.sort(key=lambda x: (-x[0], x[1], x[2]["name"]))
        top = scored[0][0]
        if k > 1:
            return [r for sc, _, r in scored[:k] if sc >= 4.0 and sc >= top * keep]
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


def coverage_matcher(L, rows, trust=None):
    """Rank entries by how much of the request they cover, not by how many words they share.

    The request is split into its content terms: Latin words and the kanji/katakana
    stretches between particles (Slackに通知を送りたい -> slack, 通知). Each term is
    weighted by how rare it is across the entries. An entry earns a term when its name,
    search terms or description say it (fields weighted in that order; a Japanese term
    partly said, 動画制作 by 動画作成, earns that part). The score is the weighted share
    of the request covered, times how strongly it is covered, so one frequent word
    cannot outrank an entry that answers the whole request."""
    import math
    O = L["OPEN"]
    norm_ = O["normalize"]
    CHUNK = re.compile(r"[a-z][a-z0-9+#]{1,}|[\u30a1-\u30ff\u30fc]{2,}|[\u3400-\u9fff]{2,}|[\u3400-\u9fff\u30a1-\u30ff\u30fc]{2,}")
    docs = []
    for r in rows:
        name = norm_(r["name"].replace("-", " "))
        kw = norm_(" | ".join(r.get("keywords") or []))
        desc = norm_(r.get("description", ""))
        docs.append((name, kw, desc, O["features"](name + " " + kw + " " + desc)[2], [O["features"](k)[2] for k in r.get("keywords") or []]))
    size = max(1, len(rows))
    cache = {}

    def df(term):
        if term not in cache:
            cache[term] = sum(term in d[0] or term in d[1] or term in d[2] for d in docs)
        return cache[term]

    def latin_in(term, text):
        return re.search(r"(?<![a-z0-9])" + re.escape(term), text) is not None

    def query_terms(text):
        """Content terms, and the sides of the request they belong to: a Japanese request
        and the English rewording beside it say the same work twice, so each side is
        covered on its own rather than counting 通知 and notification as two needs."""
        stop = set(O["GENERIC"]) | {"want", "need", "help", "how", "make", "create", "write", "use", "new"}
        sides = {"jp": [], "en": []}
        for token in norm_(text).split():
            side = "jp" if re.search(r"[\u3040-\u30ff\u3400-\u9fff]", token) else "en"
            for t in re.findall(r"[\u3400-\u9fff\u30a1-\u30ff\u30fc]+|[a-z][a-z0-9+#.]*", token):
                t = t.strip(".")
                if re.match(r"[a-z]", t):
                    if len(t) >= 2 and t not in stop:
                        sides[side].append((t, "latin"))
                elif len(t) >= 2:
                    sides[side].append((t, "jp"))
        groups = [list(dict.fromkeys(g)) for g in sides.values() if g]
        terms = list(dict.fromkeys(t for g in groups for t in g))
        return terms, groups, O["features"](norm_(text))[2]

    def best_part(term, text):
        """Longest piece of a Japanese term (2+ chars) that the text says."""
        for n in range(len(term), 1, -1):
            for i in range(len(term) - n + 1):
                if term[i:i + n] in text:
                    return n
        return 0

    def match(text, k=1, keep=0.75):
        terms, groups, qc = query_terms(text)
        if not terms:
            return [] if k > 1 else None
        weights = {}
        for t, kind in terms:
            n = df(t) if kind == "latin" else max(1, min(df(t[i:i + 2]) for i in range(len(t) - 1)))
            weights[t] = math.log(1 + size / (1 + n))
        targets = qc - O["OPS"]
        scored = []
        for r, (name, kw, desc, dc, kcon) in zip(rows, docs):
            credit = {}
            for t, kind in terms:
                best = 0.0
                for field, mult in ((name, 1.6), (kw, 1.3), (desc, 1.0)):
                    if kind == "latin":
                        part = 1.0 if latin_in(t, field) else 0.0
                    else:
                        n = best_part(t, field)
                        part = n / len(t) if n >= 2 else 0.0
                    best = max(best, part * mult)
                if best:
                    credit[t] = best
            if not credit:
                continue
            cover, got, strength = 0.0, 0.0, 0.0
            for g in groups:
                g_got = sum(weights[t[0]] * min(credit.get(t[0], 0), 1.0) for t in g)
                g_cover = g_got / (sum(weights[t[0]] for t in g) or 1.0)
                if g_cover > cover:
                    cover, got = g_cover, g_got
                    strength = sum(weights[t[0]] * credit.get(t[0], 0) for t in g)
            if not got:
                continue
            concept = 0.0
            if targets and targets & dc:
                concept += 0.15
                if any(qc <= c for c in kcon):
                    concept += 0.15
            score = (cover + concept) * (strength / max(got, 1e-9)) * 10
            scored.append((score, cover, -len(r.get("description", "")), r))
        if not scored:
            return [] if k > 1 else None
        scored.sort(key=lambda x: (-x[0], x[2], x[3]["name"]))
        top = scored[0][0]
        if k > 1:
            return [r for sc, cv, _, r in scored[:k] if cv >= 0.5 and sc >= top * keep]
        if scored[0][1] < 0.6:
            return None
        near = [r for sc, _, _, r in scored if sc > top * 0.9]
        if len(near) == 1:
            return near[0]
        tiers = sorted(near, key=lambda r: TRUST.get((trust or {}).get(r.get("source")), 9))
        a, b = (TRUST.get((trust or {}).get(r.get("source")), 9) for r in tiers[:2])
        return tiers[0] if a < b and all(r.get("source") != "local" for r in near) else None
    return match


def without_keywords(pool):
    return [r if r["source"] == "local" else {k: v for k, v in r.items() if k != "keywords"} for r in pool]


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
    prepared = L["prepared_features"](records, save=False)
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
        bare = INVOKED.sub(lambda m: " " if m[1].lower() in called else m[0], text) if called else text
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
    native_root = (CLAUDE_HOME / "skills").resolve()
    native = [r for r in all_records if L["claude_location"](r["path"], native_root)]
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
        local_matches = borrow_keywords(records, L) if include_public else records
        pool = [dict(r, source="local") for r in local_matches] + [r for r in pub_rows if r["name"] not in names]
        trust = {k: v.get("trust") for k, v in sources.items()}
        match = soft_matcher(L, pool, trust)
        # Installed skills are compared on name and description alone, as are the public
        # ones: the catalogue's search terms must not let a public skill outrank one the
        # user already has. They only help find a public skill where nothing is installed.
        fair = soft_matcher(L, without_keywords(pool), trust)
        for day, text in uncovered:
            near = fair(text, 3)
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
    # Too little history to infer anything: offer the official and curated skills not yet
    # installed, ordered by how trusted their source is, and the ways to look further.
    if work < 10 and include_public:
        rows, sources = catalog(L)
        fresh = [r for r in rows if r["name"] not in names
                 and TRUST.get(sources.get(r["source"], {}).get("trust"), 9) <= 1]
        fresh.sort(key=lambda r: (TRUST.get(sources[r["source"]].get("trust"), 9), r["kind"] != "skill", r["name"]))
        report["starter"] = {"reason": "little_history", "work_requests": work,
                             "public": [public_row(r, sources) for r in fresh[:8]], "more": len(fresh) - min(8, len(fresh)),
                             "next": ["find", "topics", "browse"]}
    no_fit = recorded_no_fit(L, records, names, since.date(), include_public)
    if no_fit:
        report["no_fit"] = no_fit
    return report


def recorded_no_fit(L, records, names, since, include_public=True):
    """Work the Stop hook recorded as done without any skill, grouped by kind of work, each
    kind with the public skills whose descriptions are closest to its words. The agent had
    the installed candidates in front of it and used none, so the list is a shortlist for
    the agent to check against each description, as with find."""
    G = runpy.run_path(str(CORE / "gap_log.py"))
    entries = [e for e in G["read_entries"]() if e["d"] >= since.isoformat()]
    if not entries:
        return None
    themes = G["themes"](entries)
    rows, sources = catalog(L) if include_public else ([], {})
    trust = {k: v.get("trust") for k, v in sources.items()}
    public = [r for r in rows if r["name"] not in names]
    local_pool = [dict(r, source="local") for r in records]
    near_local = soft_matcher(L, without_keywords(local_pool + public), trust) if records else None
    near_public = soft_matcher(L, public, trust) if public else None
    for t in themes:
        query = " ".join(t["terms"])
        t["installed_near"] = [r["name"] for r in (near_local(query, 8, 0.5) if near_local else [])
                               if r["source"] == "local"][:3]
        t["public"] = [public_row(r, sources) for r in (near_public(query, 5, 0.5) if near_public else [])]
    return {"recorded": len(entries), "days": len({e["d"] for e in entries}), "themes": themes,
            "elsewhere": elsewhere(names)}


def find(description, include_public=True):
    """One-shot: which installed or public skill fits work described in words."""
    L, R = engine()
    # Every candidate is a skill, so the word itself ("使えるスキルは？") says nothing.
    description = re.sub(r"(?i)\bskills?\b|スキル", " ", description)
    index = L["read_json"](L["INDEX"], {})
    records = borrow_keywords(L["with_keywords"](L["one_per_name"](index.get("records", []) if isinstance(index, dict) else [])), L)
    guides = R["narrow_pick"]("。".join(L["OPEN"]["work_clauses"](description)))
    local = [{"skill": r["name"], "path": r["path"], "basis": "router"}
             for _, r in L["rank"](description, records, guides, None, L["prepared_features"](records, save=False))]
    rows, sources = catalog(L) if include_public else ([], {})
    have = {r["name"] for r in records}
    pool = [dict(r, source="local") for r in records] + [r for r in rows if r["name"] not in have]
    trust = {k: v.get("trust") for k, v in sources.items()}
    # A wider shortlist than routing: the agent reads each description and keeps only
    # those whose purpose fits (see SKILL.md), as it does for the router's candidates.
    near = soft_matcher(L, without_keywords(pool), trust)(description, 8, 0.5)
    known = {x["skill"] for x in local}
    summary = {r["name"]: L["summary"](r.get("description", ""), 200) for r in records}
    for x in local:
        x["description"] = summary.get(x["skill"], "")
    local += [{"skill": r["name"], "path": r["path"], "basis": "description_overlap", "description": summary[r["name"]]}
              for r in near if r["source"] == "local" and r["name"] not in known]
    out = {"local": local[:8], "builtin_guides": guides, "public": []}
    # Unless the router itself found one, public skills are listed for the agent to weigh too.
    if include_public and not any(x["basis"] == "router" for x in local):
        public = soft_matcher(L, pool[len(records):], trust)(description, 5, 0.5)
        out["public"] = [public_row(r, sources) for r in public]
    if not (local or guides or out["public"]):
        out["elsewhere"] = elsewhere(have)
    return out


def elsewhere(installed):
    """Nothing fits here: point to a wider registry search, run only if the user wants it.
    Only the search words the user agrees to leave this computer; never history."""
    return {"find_skills_installed": "find-skills" in installed,
            "command": "npx skills find <English keywords>", "registry": "https://skills.sh/"}


def browse_pool(L):
    index = L["read_json"](L["INDEX"], {})
    records = L["one_per_name"](index.get("records", []) if isinstance(index, dict) else [])
    records = borrow_keywords(L["with_keywords"](records), L)
    rows, sources = catalog(L)
    have = {r["name"] for r in records}
    return records, [r for r in rows if r["name"] not in have], sources


def topics(min_size=3):
    """Topics as the catalogue's sources label them; nothing is assigned by this tool."""
    L, _ = engine()
    records, rows, sources = browse_pool(L)
    counts = Counter(r["category"] for r in rows if r.get("category"))
    return {"topics": [{"topic": t, "public": c} for t, c in counts.most_common() if c >= min_size],
            "uncategorised": sum(not r.get("category") for r in rows), "installed": len(records),
            "note": "browse also matches names, descriptions and search terms"}


def browse(term, limit=20):
    """Installed and public skills whose topic, name, description or search terms say TERM."""
    L, _ = engine()
    records, rows, sources = browse_pool(L)
    q = norm(term).strip()
    if not q:
        raise ValueError("empty term")
    def says(r):
        topic = norm(r.get("category") or "")
        words = [norm(k) for k in r.get("keywords") or []]
        if q == topic or q in words:
            return 0
        if q in topic or any(q in w or (len(w) >= 2 and w in q) for w in words):
            return 1
        return 2 if q in norm(r["name"].replace("-", " ") + " " + r["description"]) else None
    local = sorted(((says(r), r["name"], r) for r in records if says(r) is not None), key=lambda x: x[:2])
    public = sorted(((says(r), TRUST.get(sources.get(r["source"], {}).get("trust"), 9), r["name"], r)
                     for r in rows if says(r) is not None), key=lambda x: x[:3])
    out = {"term": term, "local": [{"skill": r["name"], "path": r["path"]} for *_, r in local[:limit]],
           "public": [public_row(r, sources) for *_, r in public[:limit]],
           "public_total": len(public)}
    if not local and not public:
        out["elsewhere"] = elsewhere({r["name"] for r in records})
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
    sub.add_parser("topics", help="topics to browse, as the public sources label them")
    b = sub.add_parser("browse", help="installed and public skills on a topic or word")
    b.add_argument("term")
    b.add_argument("--limit", type=int, choices=range(1, 51), default=20, metavar="1-50")
    a = p.parse_args(argv)
    try:
        if a.cmd == "report":
            out = analyse(a.days, include_public=not a.no_public)
        elif a.cmd == "find":
            out = find(a.description, not a.no_public)
        elif a.cmd == "topics":
            out = topics()
        else:
            out = browse(a.term, a.limit)
    except (OSError, ValueError) as e:
        p.exit(2, f"{type(e).__name__}: {e}\n")
    sys.stdout.write(json.dumps(out, ensure_ascii=False, indent=1) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
