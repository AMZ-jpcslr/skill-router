"""Keeps the installed-skill index current without a manual `index refresh`.

On each routed prompt the hook lists the skill folders' top level (one directory read per
root) and compares folder times with the last check. Only folders that changed are walked
again, so adding, removing or replacing a skill costs about as much as the skill itself.
A whole walk (unchanged files are still not reread) runs when there is no index yet, when
the index format changed with a plugin update, when a root was added, and once a day for
edits the top level does not show. Keywords need the host model and take minutes, so they
are written by a detached background process, and only for users who once asked for them
(the keyword file exists). Nothing here reads a request or sends anything.
"""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time

HERE = Path(__file__).resolve().parent
SWEEP = 24 * 3600        # whole walk at least this often
KEYWORD_RETRY = 3600     # a keyword run that did not finish is retried after this
KEYWORD_UNAVAILABLE = 24 * 3600
MAX_CHANGED = 64         # more changed folders than this: walk the roots whole
MAX_ENTRIES = 20_000


def _paths(L):
    state = L["STATE"]
    return state / "skill-auto.json", state / "skill-auto-keywords.json"


def _load(L, path):
    try:
        x = L["read_json"](path, {})
    except (OSError, ValueError):
        return {}
    return x if isinstance(x, dict) else {}


def _keyword_code():
    return hashlib.sha256((HERE / "keywords.py").read_bytes()).hexdigest()[:16]


def roots(L):
    out = []
    for p in (*L["DEFAULT"], *L["roots"]()):
        r = L["safe_root"](p)
        if r is not None and r not in out:
            out.append(r)
    return out


def _folder_time(e):
    # On Windows a listing carries the folder times its parent directory cached, and NTFS
    # updates that copy later than the folder itself (a folder made and filled just now can
    # still show its creation time). Asking the folder itself keeps an unchanged folder
    # unchanged between prompts. Elsewhere DirEntry.stat is the same lstat call.
    if os.name == "nt":
        return os.lstat(e.path).st_mtime_ns
    return e.stat(follow_symlinks=False).st_mtime_ns


def fingerprint(paths):
    """{root: [root mtime, {folder: mtime}]} from one directory read per root and one stat
    per folder (a few microseconds each)."""
    out = {}
    for root in paths:
        try:
            top = os.stat(root).st_mtime_ns
            kids = {}
            with os.scandir(root) as it:
                for e in it:
                    if len(kids) >= MAX_ENTRIES:
                        break
                    if e.is_dir(follow_symlinks=False):
                        kids[e.name] = _folder_time(e)
        except OSError:
            continue
        out[str(root)] = [top, kids]
    return out


def changes(old, new):
    """{root: folders to walk again}; None when a root must be walked whole."""
    if not isinstance(old, dict) or set(old) - set(new):
        return None
    out = {}
    for root, (top, kids) in new.items():
        prev = old.get(root)
        if not (isinstance(prev, list) and len(prev) == 2 and isinstance(prev[1], dict)):
            return None
        names = {n for n in set(kids) | set(prev[1]) if kids.get(n) != prev[1].get(n)}
        if names or top != prev[0]:
            out[root] = names
    return out


def settle(before, after, walked):
    """The fingerprint to remember after a walk. Reading a folder can move the times the
    system reports for it, so walked folders (all of them for a whole walk) take their time
    from after the walk. Folders not walked keep the time from before it, so a change made
    while walking is still seen next time instead of being taken as already indexed."""
    out = {}
    for root, (top, kids) in before.items():
        new = after.get(root)
        if new is None:
            out[root] = [top, dict(kids)]
            continue
        names = walked.get(root, set())
        whole = names is None
        # Only folders listed before the walk are taken as indexed: one that appeared or went
        # away during the walk keeps showing as changed.
        merged = {n: new[1][n] if (whole or n in names) and n in new[1] else t
                  for n, t in kids.items()}
        # The root time only matters for "something here changed"; it is safe to take the new
        # one, because any folder added or removed meanwhile still differs by name next time.
        out[root] = [new[0] if (whole or root in walked) else top, merged]
    return out


def _stale_folders(paths, rts):
    out = {}
    for p in paths:
        # Roots are resolved, so the path is too: on Windows the same folder can be spelled
        # with a short (8.3) name, other letter case or a link, and would not sit "under" its root.
        try:
            p = Path(p).resolve()
        except (OSError, RuntimeError, ValueError):
            p = Path(p)
        for r in rts:
            try:
                rel = p.relative_to(r)
            except ValueError:
                continue
            if len(rel.parts) > 1:
                out.setdefault(str(r), set()).add(rel.parts[0])
            break
    return out


def check(L, stale=(), now=None):
    """Bring the index up to date for this prompt. Returns what was done (for tests/status)."""
    now = time.time() if now is None else now
    auto_path, kw_path = _paths(L)
    st = _load(L, auto_path)
    rts = roots(L)
    fp = fingerprint(rts)
    # The index format is remembered here, so the index itself is not read on every prompt.
    whole = (st.get("format") != L["FORMAT"] or not L["INDEX"].is_file()
             or now - float(st.get("swept") or 0) > SWEEP)
    diff = None if whole else changes(st.get("fp"), fp)
    if diff is not None:
        for r, names in _stale_folders(stale, rts).items():
            diff.setdefault(r, set()).update(names)
        if sum(len(v) for v in diff.values()) > MAX_CHANGED:
            diff = None
    done = {"walked": None}
    if diff is None:
        stats = L["scan"](rts, L["INDEX"])
        st["swept"] = now
        st["format"] = L["FORMAT"]
        done["walked"] = "whole"
    elif diff:
        stats = L["scan"](rts, L["INDEX"], changed={r: diff.get(r, set()) for r in map(str, rts)})
        done["walked"] = sorted(n for v in diff.values() for n in v)
    else:
        stats = None
    if stats is not None:
        walked = {str(r): None for r in rts} if diff is None else {r: set(v) for r, v in diff.items()}
        fp = settle(fp, fingerprint(rts), walked)
    if stats is not None and (stats["read"] or stats["removed"] or done["walked"] == "whole"):
        st["changed"] = now
    code = _keyword_code()
    dirty = stats is not None or st.get("fp") != fp or st.get("kw_code") != code
    st.update(fp=fp, kw_code=code, version=1)
    # Keywords: only for users who made them once; new or changed skills, or new keyword code.
    if L["KEYWORDS"].is_file() and not os.environ.get("SKILL_ROUTER_NO_AUTO_KEYWORDS"):
        kw = _load(L, kw_path)
        want = [float(st.get("changed") or 0), code]
        behind = kw.get("done_for") != want
        wait = KEYWORD_UNAVAILABLE if kw.get("unavailable") else KEYWORD_RETRY
        if behind and now - float(st.get("kw_try") or 0) > wait:
            st["kw_try"] = now
            dirty = True
            done["keywords"] = spawn_keywords(want)
    if dirty:
        L["write_json"](auto_path, st)
    return done


def spawn_keywords(want):
    """Start `auto_index.py keywords` detached, with no console and no handle to the hook's
    pipes, so the hook returns at once and the host does not wait for it."""
    cmd = [sys.executable, "-I", "-B", "-X", "utf8", str(HERE / "auto_index.py"), "keywords", json.dumps(want)]
    kw = dict(stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, close_fds=True)
    try:
        if os.name == "nt":
            flags = 0x00000008 | 0x00000200 | 0x08000000  # DETACHED_PROCESS, NEW_PROCESS_GROUP, NO_WINDOW
            try:
                subprocess.Popen(cmd, creationflags=flags | 0x01000000, **kw)  # BREAKAWAY_FROM_JOB
            except OSError:
                subprocess.Popen(cmd, creationflags=flags, **kw)
        else:
            subprocess.Popen(cmd, start_new_session=True, **kw)
        return "started"
    except OSError:
        return "failed"


def run_keywords(L, want, runner=None):
    """The background job: keywords for rows without current ones, then the feature cache."""
    _, kw_path = _paths(L)
    records = _load(L, L["INDEX"]).get("records", [])
    out = {"done_for": None, "finished": time.time()}
    try:
        stats = L["write_keywords"](records, runner=runner)
        out.update(stats)
        if not stats.get("keywords_failed"):
            out["done_for"] = want
    except RuntimeError as err:
        out["unavailable"] = type(err).__name__ == "Unavailable"
        out["error"] = str(err)[:200]
    L["write_json"](kw_path, out)
    if out["done_for"]:
        L["prepared_features"](L["with_keywords"](L["one_per_name"](records)))
    return out


def status(L):
    auto_path, kw_path = _paths(L)
    st, kw = _load(L, auto_path), _load(L, kw_path)
    return {"last_whole_walk": st.get("swept"), "last_change": st.get("changed"),
            "keywords_auto": L["KEYWORDS"].is_file() and not os.environ.get("SKILL_ROUTER_NO_AUTO_KEYWORDS"),
            "keywords_last_run": {k: kw[k] for k in ("finished", "keywords_written", "keywords_failed", "error") if k in kw}}


if __name__ == "__main__":
    import runpy
    if len(sys.argv) == 3 and sys.argv[1] == "keywords":
        os.environ["SKILL_ROUTER_CHILD"] = "1"
        L = runpy.run_path(str(HERE / "local_index.py"))
        try:
            run_keywords(L, json.loads(sys.argv[2]))
        except (OSError, ValueError, TypeError, KeyError):
            raise SystemExit(1)
