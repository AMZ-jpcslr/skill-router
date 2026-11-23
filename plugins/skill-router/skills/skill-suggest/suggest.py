"""Offline, opt-in skill suggestions. No prompt history or skill execution."""
import argparse
import json
import re
import sqlite3
import stat
from contextlib import closing
from datetime import date, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUTCOMES = ("unknown", "missed", "manual", "used")
NAME = re.compile(rb"(?m)^name:[ \t]*['\"]?([a-z0-9][a-z0-9-]{0,79})['\"]?[ \t]*\r?$")
DEFAULT_ROOTS = (Path.home()/".agents"/"skills", Path.home()/".codex"/"skills")
NOFOLLOW = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)


def catalog():
    raw = json.loads((HERE/"catalog.json").read_text(encoding="utf-8"))
    for entry in raw["skills"]:
        names = [entry["id"], *entry["aliases"]]
        if not all(NAME.fullmatch(("name: "+n).encode("ascii")) for n in names):
            raise ValueError("invalid skill identifier")
    return raw


def safe_mode(p):
    try:
        st = p.lstat()
        if stat.S_ISLNK(st.st_mode) or getattr(st, "st_file_attributes", 0) & NOFOLLOW:
            return 0
        return st.st_mode
    except OSError:
        return 0


def has_plain_parents(p):
    return all(stat.S_ISDIR(safe_mode(d)) for d in p.parents)


def inventory(roots, entries):
    """Read bounded local front matter only; never search arbitrary directories."""
    installed = set()
    scanned, skipped = 0, 0
    for root in dict.fromkeys(p.expanduser().absolute() for p in roots):
        if not stat.S_ISDIR(safe_mode(root)) or not has_plain_parents(root):
            skipped += 1
            continue
        scanned += 1
        for entry in entries:
            for ident in (entry["id"], *entry["aliases"]):
                path = root/ident/"SKILL.md"
                if not stat.S_ISDIR(safe_mode(path.parent)) or not stat.S_ISREG(safe_mode(path)):
                    continue
                try:
                    with path.open("rb") as f:
                        opening = f.readline(32).rstrip(b"\r\n").removeprefix(b"\xef\xbb\xbf")
                        if opening != b"---":
                            continue
                        # Read only through the closing delimiter, with a hard byte budget.
                        lines, limit = [], 8192
                        while limit > 0:
                            line = f.readline(limit)
                            if not line:
                                break
                            if line.rstrip(b"\r\n") == b"---":
                                if NAME.findall(b"".join(lines)) == [ident.encode("ascii")]:
                                    installed.add(ident)
                                break
                            limit -= len(line)
                            lines.append(line)
                except OSError:
                    continue
    return installed, {"scanned_roots": scanned, "unavailable_roots": skipped}


def parse_day(value):
    day = date.fromisoformat(value)
    if day.isoformat() != value:
        raise ValueError("expected date YYYY-MM-DD")
    return day


def record(path, tasks, outcome, today):
    path = path.expanduser().absolute()
    if path.is_symlink() or not has_plain_parents(path):
        raise ValueError("history must be under an existing ordinary directory")
    if path.exists() and not stat.S_ISREG(safe_mode(path)):
        raise ValueError("history must be an ordinary file")
    with closing(sqlite3.connect(path)) as conn, conn:
        conn.execute("""CREATE TABLE IF NOT EXISTS observations (
            task TEXT NOT NULL, day TEXT NOT NULL, outcome TEXT NOT NULL,
            PRIMARY KEY(task, day, outcome))""")
        conn.execute("DELETE FROM observations WHERE day < ?", ((today-timedelta(days=59)).isoformat(),))
        conn.executemany("INSERT OR IGNORE INTO observations VALUES(?,?,?)",
                         [(task, today.isoformat(), outcome) for task in set(tasks)])


def read_history(path, today, tasks):
    path = path.expanduser().absolute()
    if not path.exists() and not path.is_symlink():
        return []
    if not stat.S_ISREG(safe_mode(path)) or not has_plain_parents(path):
        raise ValueError("history must be an ordinary file")
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as conn:
        records = conn.execute("SELECT task,day,outcome FROM observations WHERE day BETWEEN ? AND ?",
                               ((today-timedelta(days=59)).isoformat(),today.isoformat()))
        return [(task, parse_day(day), outcome) for task,day,outcome in records
                if task in tasks and outcome in OUTCOMES]


def suggest(data, installed, observations, today, current=None, limit=3, external=False):
    grouped = {task: {o:set() for o in OUTCOMES} for task in data["tasks"]}
    for task, day, outcome in observations:
        if task in grouped and outcome in OUTCOMES and 0 <= (today-day).days < 60:
            grouped[task][outcome].add(day)
    ranked = []
    for task, stats in grouped.items():
        days = set().union(*stats.values())
        gaps = stats["missed"] | stats["manual"]
        if current is not None and task != current:
            continue
        if current is None:
            if len(days) < 2:
                continue
            if len(stats["used"]) >= 2 and not gaps:
                continue
        weighted_days = sum(2 if (today-day).days < 14 else 1 for day in days)
        ranked.append((-(weighted_days + len(gaps)*2),task,len(days),len(gaps)))
    output, seen = [], set()
    for _,task,days,gaps in sorted(ranked):
        choices = []
        for entry in data["skills"]:
            if task not in entry["fit"]:
                continue
            local = next((name for name in (entry["id"],*entry["aliases"]) if name in installed),None)
            if not local and not (external and entry.get("source")):
                continue
            choices.append((not bool(local),-entry["fit"][task],entry["id"],entry,local))
        for _,_,ident,entry,local in sorted(choices):
            if ident in seen:
                continue
            if len(output) >= limit:
                return output
            output.append({
                "skill":local or ident,
                "location": "local" if local else "public_candidate",
                "task":task, "task_label":data["tasks"][task],
                "observed_days":days, "manual_or_missed_days":gaps,
                "fit":entry["reason"], "source":entry.get("source") if not local else None,
            })
            seen.add(ident)
            if current is None:
                break
    return output


def main(argv=None):
    data = catalog()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--history", type=Path, default=Path.home()/".skill-suggest-history.sqlite3")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("tasks")
    log = sub.add_parser("record", help="explicit opt-in for the given task labels only")
    log.add_argument("--task", action="append", required=True, choices=tuple(data["tasks"]))
    log.add_argument("--outcome", choices=OUTCOMES, default="unknown")
    find = sub.add_parser("recommend")
    find.add_argument("--task", choices=tuple(data["tasks"]), help="one-shot; does not open history")
    find.add_argument("--root", type=Path, action="append", help="each root contains skill directories; replaces default roots")
    find.add_argument("--include-public",action="store_true",help="also list curated public candidates; never connects to the network")
    find.add_argument("--limit",type=int, choices=range(1,11),default=3)
    find.add_argument("--demo", action="store_true", help="synthetic history only; no personal history read or write")
    find.add_argument("--json",action="store_true")
    args=parser.parse_args(argv)
    today=date.today()
    try:
        if args.cmd=="tasks":
            for key, label in data["tasks"].items():
                print(f"{key}: {label}")
            return 0
        if args.cmd=="record":
            record(args.history,args.task,args.outcome,today)
            print("作業分類・日付・指定された利用状況だけ保存しました。")
            return 0
        if args.demo and args.task:
            parser.error("--demo and --task cannot be combined")
        if args.demo:
            observations=[(task,today-timedelta(days=i),outcome)
                          for task,outcome,count in (("regression-test","manual",3),("spreadsheet","missed",2),("pdf","unknown",1))
                          for i in range(count)]
        elif args.task:
            observations=[]
        else:
            observations=read_history(args.history,today,data["tasks"])
        installed, roots = inventory(args.root or DEFAULT_ROOTS,data["skills"])
        rows=suggest(data,installed,observations,today,args.task,args.limit,args.include_public)
        if args.json:
            print(json.dumps({"demo":args.demo,"roots":roots,"recommendations":rows},ensure_ascii=False,indent=2))
        else:
            if args.demo:
                print("合成デモ（実際の利用履歴ではありません）")
            if not rows:
                print("おすすめなし。作業を直接指定するか、別日に繰り返した作業を記録してください。")
            for row in rows:
                label="手元で確認" if row["location"]=="local" else "公開候補・導入状況未確認"
                why=f"／直近60日で{row['observed_days']}日" if row["observed_days"] else ""
                if row["manual_or_missed_days"]:
                    why+=f"（手動指定・未適用を記録: {row['manual_or_missed_days']}日）"
                print(f"- {row['skill']} [{label}] {row['task_label']}{why}: {row['fit']}")
                if row["source"]:
                    print("  "+row["source"])
        return 0
    except (OSError, ValueError, sqlite3.Error) as e:
        parser.exit(2,f"入力またはローカルの記録を確認してください ({type(e).__name__})。\n")


if __name__=="__main__":
    raise SystemExit(main())
