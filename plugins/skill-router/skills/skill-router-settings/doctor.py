"""Skill health check: installed skills the router can hardly pick, and skills it cannot tell
apart. Reads the local index only; never reads skill bodies, runs skills, or edits a SKILL.md.

Every check reuses the router's own scoring, so a finding means the same thing it means when a
request comes in: a skill whose own description (and index-time example requests) is matched as
well or better by another skill will lose, or tie with, that skill on real requests.
"""
import argparse
import json
import math
from pathlib import Path
import re
import runpy

HERE = Path(__file__).resolve().parent
OWN = {"skill-router-settings", "skill-suggest"}
# A skill whose description another one matches this nearly is not told apart from it.
CLOSE = .85
# Candidates the router's full scorer looks at for each probe, after a cheap prefilter.
POOL = 24
# Example requests per skill used as probes (written at index time with --keywords).
PROBES = 3
# Fewer catalogue-rare words than this and the description says little a request can match.
MIN_DISTINCT = 3
MIN_CHARS = 40
# "Use when ...", "...するときに使う": a description that says when it applies.
WHEN = re.compile(r"(?i)\buse (?:this |it )?(?:skill )?(?:when|for|to|if|whenever)\b|\bwhen (?:the user|you|asked|working)\b"
                  r"|\btriggers? (?:on|when)\b|(?:とき|時|場合|際)(?:に|は|の)|ために使|に使う|で使う|に使用|で使用")


def engine():
    return runpy.run_path(str(HERE / "local_index.py"))


def lead(description):
    positive = re.split(r"(?i)\b(?:do not use|not intended for|avoid using)\b", description, maxsplit=1)[0]
    return re.split(r"(?<=。)|(?<=[.!?])\s+(?=[A-Z])", positive.strip(), maxsplit=1)[0][:200]


class Catalog:
    """The routing features of every installed skill, with the router's document frequencies."""

    def __init__(self, L, records, prepared=None):
        self.L, self.O = L, L["OPEN"]
        self.rows = records
        self.size = len(records)
        prepared = prepared if prepared is not None else L["prepared_features"](records, save=False)
        self.entries = [prepared[r["path"]] for r in records]
        order = [r["path"] for r in records]
        if getattr(prepared, "order", None) == order and prepared.counts and prepared.post is not None:
            self.counts, self.post = prepared.counts, prepared.post
        else:
            self.counts = self.O["catalog_counts"](self.entries)
            self.post = self.O["postings"](self.entries, self.counts)
        self.at = {r["name"]: i for i, r in enumerate(records)}

    def idf(self, t):
        df = self.counts[0].get(t) or self.counts[1].get(t) or 0
        return math.log(1 + (self.size + .5) / (df + .5))

    def terms(self, i):
        """The words a request can match this skill by: name, description and keywords."""
        nw, nc, meta, scope, dw, kw, jk, dg = self.entries[i]
        return (nw | dw | kw | dg) - self.O["ROLE_WORDS"] - self.O["EN_STOP"]

    def probe(self, text, i, extra=()):
        """Score `text` against skill i and the skills sharing its rarest words, as the router
        would. Returns (own score, [(score, j), ...] best first, without i)."""
        O = self.O
        q = O["Query"](O["normalize"](text)[:400], "", self.counts)
        said = set(q.words) | set(q.jp) | set(q.translated)
        common = max(30, self.size // 10)
        weight = {}
        for t in said:
            hits = self.post.get(t, ())
            if len(hits) > common:
                continue
            w = self.idf(t)
            for j in hits:
                weight[j] = weight.get(j, 0.) + w
        weight.pop(i, None)
        pool = sorted(weight, key=lambda j: -weight[j])[:POOL]
        pool += [j for j in extra if j != i and j not in pool]

        def score(j):
            return O["score_skill"](q, self.entries[j], self.counts[0], self.counts[1], self.size, self.rows[j])[0]

        others = sorted(((score(j), j) for j in pool), key=lambda x: -x[0])
        return score(i), others


def issues_of(cat, i):
    """What makes skill i hard to pick from its text alone (no comparison with others)."""
    row = cat.rows[i]
    d = row.get("description", "")
    terms = cat.terms(i)
    rare = max(3, cat.size // 100)
    distinct = term_list(cat, [t for t in terms if (cat.counts[0].get(t) or cat.counts[1].get(t) or 0) <= rare],
                         k=99, rows=(i,))
    nw = cat.entries[i][0] - cat.O["ROLE_WORDS"]
    out = []
    if len(d.strip()) < MIN_CHARS or len(terms) < 6:
        out.append("too_short")
    if cat.size >= 30 and len(distinct) < MIN_DISTINCT:
        out.append("few_specific_words")
    if cat.size >= 30 and all(cat.idf(t) < 3.0 for t in nw):
        out.append("generic_name")
    if not WHEN.search(d):
        out.append("no_when_to_use")
    has_jp = bool(cat.entries[i][7]) or bool(cat.entries[i][6])
    return out, distinct[:8], has_jp


def diagnose(cat, i, partners=()):
    """Probe skill i with its own lead sentence and example requests."""
    row = cat.rows[i]
    # The skill's own first sentence, without its name: requests rarely say a skill's name.
    probes = [lead(row.get("description", ""))]
    asks = [k for k in row.get("keywords") or () if cat.O["JP"].search(k) and len(k) >= 8][-PROBES:]
    probes += asks
    rivals = {}
    wins = 0
    for n, text in enumerate(probes):
        own, others = cat.probe(text, i, partners)
        if n > 0 and own > 0 and (not others or others[0][0] < own):
            wins += 1
        for sc, j in others:
            if own <= 0 or sc < CLOSE * own:
                break
            r = rivals.setdefault(j, {"ratio": 0., "beats": 0, "probes": 0})
            r["ratio"] = max(r["ratio"], round(sc / own, 2))
            r["probes"] += 1
            r["beats"] += sc > own
    return rivals, wins, len(asks)


def similar(cat, targets, least=.5):
    """Pairs whose words, weighted by how rare they are, are mostly the same: the router has
    little but common words to tell them apart by, whatever a request says."""
    common = max(30, cat.size // 10)
    total = [sum(cat.idf(t) for t in cat.terms(i)) for i in range(cat.size)]
    for i in targets:
        shared = {}
        for t in cat.terms(i):
            hits = cat.post.get(t, ())
            if len(hits) > common:
                continue
            w = cat.idf(t)
            for j in hits:
                if j != i:
                    shared[j] = shared.get(j, 0.) + w
        for j, w in shared.items():
            if w / ((total[i] + total[j] - w) or 1) >= least:
                yield i, j, w


def term_list(cat, terms, k=6, rows=()):
    """The rarest words first, as the skills write them ("debugging", not the folded "debugg")."""
    O = cat.O
    seen = {}
    for i in rows:
        r = cat.rows[i]
        text = O["normalize"](" ".join([r["name"].replace("-", " "), r.get("description", ""), *(r.get("keywords") or ())]))
        for w in O["WORD"].findall(text):
            seen.setdefault(O["canonical"](w), w)
    keep = [t for t in terms if len(t) >= 3 or not t.isascii()]
    return [seen.get(t, t) for t in sorted(keep, key=lambda t: (-cat.idf(t), t))[:k]]


def pair(cat, a, b, rival):
    ta, tb = cat.terms(a), cat.terms(b)
    union = ta | tb
    sim = sum(cat.idf(t) for t in ta & tb) / (sum(cat.idf(t) for t in union) or 1)
    ra, rb = cat.rows[a], cat.rows[b]
    return {"skills": [ra["name"], rb["name"]],
            "similarity": round(sim, 2),
            "closeness": rival["ratio"],
            "lost_probes": rival["beats"],
            "shared_words": term_list(cat, ta & tb, rows=(a, b)),
            "only_" + ra["name"]: term_list(cat, ta - tb, rows=(a,)),
            "only_" + rb["name"]: term_list(cat, tb - ta, rows=(b,)),
            "descriptions": {ra["name"]: summary(ra), rb["name"]: summary(rb)},
            "paths": {ra["name"]: Path(ra["path"]).as_posix(), rb["name"]: Path(rb["path"]).as_posix()}}


def summary(row, limit=400):
    clean = " ".join("".join(c if c.isprintable() else " " for c in str(row.get("description", ""))).split())
    return clean if len(clean) <= limit else clean[:limit - 1] + "…"


def run(cat, limit=10, only=None):
    """The report: skills hard to pick, pairs not told apart, and counts."""
    hard, pairs, seen = [], {}, set()
    no_jp = 0
    targets = [cat.at[only]] if only is not None else range(cat.size)
    for i in targets:
        row = cat.rows[i]
        if row["name"] in OWN:
            continue
        issues, distinct, has_jp = issues_of(cat, i)
        no_jp += not has_jp
        rivals, wins, asked = diagnose(cat, i)
        if asked and wins < asked:
            issues.append("loses_own_examples")
        for j, r in rivals.items():
            if cat.rows[j]["name"] in OWN:
                continue
            key = tuple(sorted((i, j)))
            prev = pairs.get(key)
            if prev is None or (r["ratio"], r["beats"]) > (prev[2]["ratio"], prev[2]["beats"]):
                pairs[key] = (i, j, r)
        if only is not None or [x for x in issues if x != "no_when_to_use"]:
            hard.append({"name": row["name"], "path": Path(row["path"]).as_posix(), "issues": issues,
                         "specific_words": distinct, "own_examples_won": f"{wins}/{asked}" if asked else None,
                         "close_rivals": [cat.rows[j]["name"] for j, _ in
                                          sorted(rivals.items(), key=lambda x: -x[1]["ratio"])[:3]],
                         "description": summary(row)})
    for i, j, sim in similar(cat, targets):
        key = tuple(sorted((i, j)))
        if key not in pairs and not {cat.rows[i]["name"], cat.rows[j]["name"]} & OWN:
            pairs[key] = (i, j, {"ratio": 0., "beats": 0, "probes": 0})
    order = {"too_short": 3, "few_specific_words": 3, "loses_own_examples": 2, "generic_name": 1, "no_when_to_use": .5}
    hard.sort(key=lambda h: (-sum(order.get(x, 0) for x in h["issues"]), h["name"]))
    found = [pair(cat, a, b, r) for a, b, r in pairs.values()]
    found.sort(key=lambda p: (-(p["lost_probes"] + p["closeness"] + p["similarity"]), p["skills"]))
    report = {"skills": cat.size - sum(r["name"] in OWN for r in cat.rows),
              "hard_to_pick_count": len(hard), "overlap_count": len(found),
              "hard_to_pick": hard[:limit], "overlaps": found[:limit],
              "without_japanese_words": no_jp}
    if no_jp and not any(r.get("keywords") for r in cat.rows):
        report["action"] = "router.py index refresh --keywords"
    return report


def records_of(L, index=None):
    obj = L["read_json"](index or L["INDEX"], {})
    if not isinstance(obj, dict) or obj.get("version") not in L["READABLE_FORMATS"]:
        return None
    return L["with_keywords"](L["one_per_name"](obj["records"]))


def compare(L, records, name, description):
    """The report for one skill before and after a proposed description; nothing is written."""
    by_name = {r["name"]: n for n, r in enumerate(records)}
    if name not in by_name:
        return {"error": "unknown_skill", "name": name}
    prepared = L["prepared_features"](records)
    before = run(Catalog(L, records, prepared), only=name)
    changed = list(records)
    row = changed[by_name[name]] = dict(records[by_name[name]], description=" ".join(description.split())[:L["DESCRIPTION_LIMIT"]])
    trial = {r["path"]: prepared[r["path"]] for r in records}
    trial[row["path"]] = L["OPEN"]["prepare"](row)
    after = run(Catalog(L, changed, trial), only=name)

    def brief(rep):
        h = rep["hard_to_pick"][0] if rep["hard_to_pick"] else {"issues": [], "specific_words": []}
        return {"issues": h["issues"], "specific_words": h["specific_words"],
                "overlaps": [{"with": [s for s in p["skills"] if s != name][0], "closeness": p["closeness"],
                              "lost_probes": p["lost_probes"]} for p in rep["overlaps"]]}

    return {"name": name, "before": brief(before), "after": brief(after), "written": False}


def command(argv):
    p = argparse.ArgumentParser(prog="router.py doctor", description=__doc__)
    p.add_argument("--limit", type=int, default=10, help="findings shown per section (default 10)")
    p.add_argument("--skill", help="check one installed skill in detail")
    p.add_argument("--try-description", dest="trial", help="with --skill: check a proposed description without writing it")
    a = p.parse_args(argv)
    if a.trial and not a.skill:
        p.error("--try-description needs --skill")
    L = engine()
    records = records_of(L)
    if records is None:
        result = {"error": "index_missing", "action": "router.py index refresh"}
    elif a.trial:
        result = compare(L, records, a.skill, a.trial)
    else:
        prepared = L["prepared_features"](records)
        cat = Catalog(L, records, prepared)
        if a.skill and a.skill not in cat.at:
            result = {"error": "unknown_skill", "name": a.skill}
        else:
            result = run(cat, limit=max(1, min(a.limit, 100)), only=a.skill)
    print(json.dumps(result, ensure_ascii=True))
    return 0 if "error" not in result else 1
