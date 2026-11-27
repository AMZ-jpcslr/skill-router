"""Local record of work no installed skill was used for. Nothing leaves this computer.

Run by the Stop hook at the end of a turn. It reads the end of the host's own transcript
of this conversation, finds the user's last request and whether any skill was opened for
it after that (the Skill tool, or a SKILL.md read by any tool). A work request for which
no skill was opened, none was named by hand and no built-in guide applied is appended to
no-fit.jsonl beside the settings: the date, the host and the content words of the request
(single words, never the request itself). skill-suggest reads it to recommend skills for
that work. The hook prints nothing to the agent; when the same kind of work keeps coming
back, it shows the user one line at most once a week.
"""
from collections import Counter
from datetime import date, datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import time
import unicodedata

STATE_DIR = Path(os.environ.get("SKILL_ROUTER_STATE_DIR") or Path.home() / ".skill-router-beta")
LOG = STATE_DIR / "no-fit.jsonl"
MARK = STATE_DIR / "no-fit-state.json"
KEEP_DAYS = 180
MAX_ENTRIES = 3000
TAIL_BYTES = 3_000_000
MAX_TERMS = 12
# One line to the user when work of one kind went without a skill on this many days.
NOTICE_DAYS = 3
NOTICE_WINDOW = 60
NOTICE_EVERY = 7 * 86400
OWN = {"skill-suggest", "skill-router-settings"}
TERM = re.compile(r"[a-z][a-z0-9+#.-]{2,}|[ァ-ヿー]{3,}|[㐀-鿿]{2,6}")
SKILL_PATH = re.compile(r"[/\\]([a-z0-9][a-z0-9-]{0,79})[/\\]+SKILL\.md\b", re.I)
INVOKED = re.compile(r"(?:^|(?<=\s))[/$](?:[a-z0-9-]+:)?([a-z0-9][a-z0-9-]{0,79})(?![a-z0-9_:/\\-]|\.\S)", re.I)
STOP_WORDS = {"the", "and", "this", "that", "with", "for", "please", "してください", "ください", "お願い"}


def plain_file(p):
    try:
        st = p.lstat()
    except OSError:
        return False
    return stat.S_ISREG(st.st_mode) and not getattr(st, "st_file_attributes", 0) & 0x400


def content_terms(text, L):
    """The words that say what the work is about: Latin words, katakana runs and short
    kanji runs, without the words that only say what to do (作成, 修正, write) or ask."""
    O = L["OPEN"]
    out = []
    for t in TERM.findall(unicodedata.normalize("NFKC", text).casefold()):
        t = t.strip(".-")
        if (len(t) < 2 or t in STOP_WORDS or L["GENERIC"].fullmatch(t) or L["ACTION"].search(t)
                or O["REQUEST"].search(t) or O["features"](t)[2] & O["OPS"]):
            continue
        if t not in out:
            out.append(t)
    return out[:MAX_TERMS]


def _text(content):
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        if any(isinstance(c, dict) and c.get("type") == "tool_result" for c in content):
            return None
        return "\n".join(c.get("text", "") for c in content if isinstance(c, dict) and c.get("type") in ("text", "input_text"))
    return None


def _tool_calls(node):
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


def _opened(o):
    """Skill names one transcript record opened: the Skill tool, or a SKILL.md path in any tool call."""
    found = set()
    for call in _tool_calls(o):
        args = call.get("input") or call.get("arguments") or call.get("action") or ""
        if call.get("name") == "Skill" and isinstance(args, dict) and isinstance(args.get("skill"), str):
            found.add(args["skill"].split(":")[-1].lower())
        for m in SKILL_PATH.finditer(args if isinstance(args, str) else json.dumps(args)):
            found.add(m.group(1).lower())
    return found


def last_turn(path):
    """(request text or None, skills opened after it) from the end of a Claude Code or Codex
    transcript. None when the last turn did not start with the user's own words (a slash
    command, a notification) or is not in the part read."""
    p = Path(path)
    if not plain_file(p):
        return None, set()
    with p.open("rb") as f:
        f.seek(0, 2)
        size = f.tell()
        f.seek(max(0, size - TAIL_BYTES))
        if size > TAIL_BYTES:
            f.readline()
        lines = f.read().splitlines()
    prompt, opened = None, set()
    for raw in lines:
        if not raw.strip():
            continue
        try:
            o = json.loads(raw)
        except ValueError:
            continue
        if not isinstance(o, dict):
            continue
        payload = o.get("payload") if isinstance(o.get("payload"), dict) else {}
        start = None
        if o.get("type") == "user" and not o.get("isMeta"):
            # Claude Code: the user's turn. Tool results come back as user records too.
            text = _text((o.get("message") or {}).get("content"))
            if text is not None and text.strip():
                start = text
        elif o.get("type") == "event_msg" and payload.get("type") == "user_message" and isinstance(payload.get("message"), str):
            start = payload["message"]                            # Codex
        if start is not None:
            # A slash command or a host notice starts a turn that is not a request in words.
            prompt = None if start.lstrip().startswith("<") else start
            opened = set()
            continue
        opened |= _opened(o)
    return prompt, opened - OWN


def read_entries(today=None):
    today = today or date.today()
    since = (today - timedelta(days=KEEP_DAYS)).isoformat()
    out = []
    if not plain_file(LOG):
        return out
    try:
        with LOG.open("r", encoding="utf-8") as f:
            for line in f:
                try:
                    e = json.loads(line)
                except ValueError:
                    continue
                if (isinstance(e, dict) and isinstance(e.get("d"), str) and e["d"] >= since
                        and isinstance(e.get("w"), list) and all(isinstance(w, str) for w in e["w"])):
                    out.append(e)
    except OSError:
        return []
    return out[-MAX_ENTRIES:]


def _write_json(path, data):
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


def append(entry):
    STATE_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
    if LOG.is_symlink():
        return
    with LOG.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    # Old entries go once the file has grown; the last KEEP_DAYS days and MAX_ENTRIES stay.
    if LOG.stat().st_size > 400_000:
        kept = read_entries()
        tmp = LOG.with_suffix(".tmp")
        tmp.write_text("".join(json.dumps(e, ensure_ascii=False) + "\n" for e in kept), encoding="utf-8")
        os.replace(tmp, LOG)


def forget():
    for p in (LOG, MARK):
        try:
            p.unlink()
        except OSError:
            pass


def themes(entries, limit=8):
    """Recorded requests grouped by the kind of work: two requests are one kind when they
    share two content words, or one word that few other requests use. Each theme gives its
    most frequent words, the days it came up and how many requests."""
    n = len(entries)
    df = Counter(w for e in entries for w in set(e["w"]))
    rare = lambda w: df[w] <= max(2, n // 10)
    parent = list(range(n))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i
    by_word = {}
    for i, e in enumerate(entries):
        for w in set(e["w"]):
            by_word.setdefault(w, []).append(i)
    for i, e in enumerate(entries):
        mine = set(e["w"])
        near = Counter(j for w in mine for j in by_word[w] if j < i)
        for j, shared in near.items():
            if shared >= 2 or any(rare(w) for w in mine & set(entries[j]["w"])):
                parent[find(i)] = find(j)
    groups = {}
    for i in range(n):
        groups.setdefault(find(i), []).append(entries[i])
    out = []
    for g in groups.values():
        words = Counter(w for e in g for w in set(e["w"]))
        top = [w for w, _ in sorted(words.items(), key=lambda kv: (-kv[1], -len(kv[0]), kv[0]))[:5]]
        out.append({"terms": top, "days": len({e["d"] for e in g}), "requests": len(g),
                    "last": max(e["d"] for e in g), "hosts": sorted({e.get("h", "") for e in g} - {""})})
    out.sort(key=lambda t: (-t["days"], -t["requests"], t["terms"]))
    return out[:limit]


def _notice(entry, today):
    """One line for the user when the kind of work just recorded keeps coming back."""
    try:
        mark = json.loads(MARK.read_text(encoding="utf-8")) if plain_file(MARK) else {}
    except (OSError, ValueError):
        mark = {}
    if time.time() - float(mark.get("noticed", 0) or 0) < NOTICE_EVERY:
        return None
    since = (today - timedelta(days=NOTICE_WINDOW)).isoformat()
    recent = [e for e in read_entries(today) if e["d"] >= since]
    mine = set(entry["w"])
    for t in themes(recent, limit=50):
        if t["days"] >= NOTICE_DAYS and mine & set(t["terms"]):
            mark["noticed"] = time.time()
            _write_json(MARK, mark)
            return ("Skill Router: 「" + "・".join(t["terms"][:3]) + "」の作業で、合う導入済みスキルがない日が"
                    f"{t['days']}日ありました。「おすすめのスキルを教えて」と頼むと、合う公開スキルを探します。"
                    "（記録はこのPCの中だけ。止めるには「Skill Routerの記録をオフにして」）")
    return None


def record(event, idx, narrow_pick, skip=lambda text: False, host="codex", today=None):
    """Stop hook: append the last request when no skill was used for it. Returns the hook
    output: {} or a one-line notice for the user."""
    if event.get("stop_hook_active"):
        return {}
    path = event.get("transcript_path")
    if not isinstance(path, str) or not path:
        return {}
    prompt, opened = last_turn(path)
    if prompt is None or opened:
        return {}
    clean = unicodedata.normalize("NFKC", prompt).strip()
    if not clean or skip(clean) or INVOKED.search(clean):
        return {}
    clauses = idx["OPEN"]["work_clauses"](prompt)
    if not clauses or narrow_pick("。".join(clauses)):
        return {}
    words = content_terms(" ".join(clauses), idx)
    if not words:
        return {}
    # A turn can end more than once (background work finishing); it is recorded once.
    key = hashlib.sha256((str(event.get("session_id")) + "\0" + clean).encode()).hexdigest()[:20]
    try:
        mark = json.loads(MARK.read_text(encoding="utf-8")) if plain_file(MARK) else {}
    except (OSError, ValueError):
        mark = {}
    if key in mark.get("recent", []):
        return {}
    mark["recent"] = (mark.get("recent", []) + [key])[-50:]
    _write_json(MARK, mark)
    today = today or datetime.now(timezone.utc).astimezone().date()
    entry = {"d": today.isoformat(), "h": host, "w": words}
    append(entry)
    line = _notice(entry, today)
    return {"systemMessage": line} if line else {}
