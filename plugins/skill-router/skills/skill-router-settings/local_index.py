"""Local metadata index; no network, skill execution, or transcript storage."""
import argparse
from collections import Counter
import hashlib
import json
import os
from pathlib import Path
import re
import runpy
import stat
import tempfile
import unicodedata
import zlib

HOME=Path.home()
STATE=Path(os.environ.get("SKILL_ROUTER_STATE_DIR") or HOME/".skill-router-beta")
INDEX=STATE/"skill-index.json"
ROOTS=STATE/"skill-roots.json"
KEYWORDS=STATE/"skill-keywords.json"
FEATURES=STATE/"skill-features.json"
MAX_BYTES=12_000_000
MAX_SKILLS=12_000
MAX_DIRS=20_000
NAME=re.compile(r"[a-z0-9][a-z0-9-]{0,79}\Z")
DEFAULT=(HOME/".agents/skills", HOME/".codex/skills", HOME/".claude/skills")
# 2: descriptions folded onto indented lines are read whole.
FORMAT=2
OPEN=runpy.run_path(str(Path(__file__).with_name("open_routing.py")))
# Cached features are valid only for the exact routing code that produced them.
FEATURE_KEY=hashlib.sha256(Path(__file__).with_name("open_routing.py").read_bytes()).hexdigest()[:16]

# Targets and verbs must agree in the same clause or a conjunction without a sentence break.
CAPS=[
 ("spreadsheet", r"excel|xlsx|スプレッドシート|表計算|精算表|予算表", r"集計|検算|照合|数式|計算|作成|編集|calculate|formula", ("xlsx-official","xlsx","spreadsheets"), ("spreadsheet","formula","recalculation")),
 ("pdf", r"pdf", r"抽出|変換|表を取り|結合|分割|作成|編集|extract|convert|merge", ("pdf-official","pdf"), ("pdf","extract","document")),
 ("slides", r"スライド|pptx|powerpoint|プレゼン資料", r"作成|整え|編集|直し|デザイン|作って|更新|create|edit", ("pptx-official","pptx","slides"), ("presentation","slide","deck")),
 ("meeting", r"議事録|会議メモ|meeting notes", r"まとめ|作成|要約|整理|決定事項|担当と期限|summari|action item", ("bulletmind","meeting-notes","internal-comms"), ("meeting","minutes","action item")),
 ("office-writing", r"上司向け|経営層|社内向け|部長向け|社内報告", r"報告|まとめ|要約|書|作成|draft|report", ("internal-comms",), ("internal communication","leadership","updates")),
 ("frontend", r"画面|ui|frontend|サイト|ダークモード", r"デザイン|配色|レイアウト|見やす|読みにく|スタイル|css|試作|design|contrast", ("frontend-design","ui-ux-pro-max"), ("frontend","design","accessibility")),
 ("browser-testing", r"ブラウザ|playwright|web画面", r"検証|確認|テスト|再現|test|check", ("webapp-testing","playwright","browser-testing"), ("browser","testing","automation")),
 ("japanese-style",None,None,(), ("自然な日本語","推敲","校正","日本語文章","言い回し","japanese writing","japanese proofreading")),
 ("readme", None,None,("readme","documentation"), ("readme","documentation","technical writing")),
 ("debugging",None,None,("debugging-and-error-recovery","systematic-debugging","bug-hunter"), ("debugging","root cause","reproduce")),
 ("javascript-testing",None,None,("javascript-testing-patterns","test-driven-development"), ("javascript","testing","regression")),
 ("python-testing",None,None,("python-testing-patterns","tdd-workflow"), ("python","testing","pytest")),
 ("regression-testing",None,None,("tdd-workflow","test-driven-development"), ("regression","testing","test-driven")),
 ("career-writing",None,None,("draft-career-es",), ("career","application","cover letter")),
 ("word-revisions",None,None,("docx-official","documents","docx"), ("word","tracked changes","docx")),
]
CAP_BY_NAME={x[0]:x for x in CAPS}
NEG=re.compile(r"(?:作成|編集|変更|変換|実行|追加|公開|操作|抽出|集計|計算|照合|検証|修正)(?:は|を)?(?:しない|不要|禁止)|(?:は|が)(?:不要|禁止)|読み取りだけ|(?:方法|意味)(?:を教えて|は何)|(?:しなくて|しなくても)(?:いい|よい)")
FENCE=re.compile(r"(?ms)^[ \t]*[~\x60]{3,}.*?^[ \t]*[~\x60]{3,}[^\n]*")
QUOTE=re.compile(r"「[^」]*」|『[^』]*』|“[^”]*”")
GENERIC=re.compile(r"(?:を|して|する|ください|お願い|みて|見て|対応|修正|作成|更新|テスト|変換|skill|workflow|best|coding|agent)",re.I)
ACTION=re.compile(r"作成|更新|修正|推敲|校正|添削|書き直|整え|言い換|変換|整理|作って|追加|抽出|集計|照合|確認|検証|実装|直して|(?:build|create|edit|fix|extract|convert|test)\b",re.I)

def norm(s):
    return unicodedata.normalize("NFKC",s).casefold()

def plain(path, mode):
    try:
        s=path.lstat()
    except OSError:
        return False
    return not (getattr(s,"st_file_attributes",0)&0x400) and mode(s.st_mode)

def read_json(path, default):
    if path.is_symlink() or (path.exists() and not plain(path,stat.S_ISREG)):
        raise ValueError("not an ordinary file")
    try:
        with path.open("rb") as f:
            data=f.read(MAX_BYTES+1)
    except FileNotFoundError:
        return default
    if len(data)>MAX_BYTES: raise ValueError("index is too large")
    return json.loads(data)

def write_json(path,data):
    if path.parent.exists() and not plain(path.parent,stat.S_ISDIR):
        raise ValueError("index directory is not an ordinary directory")
    path.parent.mkdir(parents=True,exist_ok=True)
    raw=json.dumps(data,ensure_ascii=False,separators=(",",":")).encode("utf-8")
    if len(raw)>MAX_BYTES: raise ValueError("index is too large")
    handle,tmp=tempfile.mkstemp(dir=path.parent)
    try:
        with os.fdopen(handle,"wb") as f: f.write(raw)
        os.replace(tmp,path)
    finally:
        Path(tmp).unlink(missing_ok=True)

def roots():
    x=read_json(ROOTS,[])
    if not isinstance(x,list) or len(x)>16 or any(not isinstance(p,str) for p in x):
        raise ValueError("invalid skill roots")
    return [Path(p) for p in x]

def safe_root(path):
    path=path.expanduser()
    if not path.is_absolute() or path.drive.startswith(chr(92)*2):
        return None
    try:
        p=path.resolve(strict=True)
        if p in (HOME.resolve(), Path(p.anchor)) or not plain(p,stat.S_ISDIR):
            return None
        return p
    except (RuntimeError,OSError,ValueError):
        return None

def metadata(path):
    with path.open("rb") as f:
        if f.readline(32).rstrip(b"\r\n").removeprefix(b"\xef\xbb\xbf")!=b"---": return None
        budget=8192
        fields={}
        block=plain=None
        chunks=[]
        while budget>0:
            line=f.readline(budget)
            if not line: return None
            budget-=len(line)
            if line.rstrip(b"\r\n")==b"---": break
            if block is not None and (line.startswith((b" ",b"\t")) or not line.strip()):
                chunks.append(line.decode("utf8").strip())
                continue
            if plain is not None and line.startswith((b" ",b"\t")) and line.strip():
                # A plain or quoted YAML scalar folded onto indented lines continues there.
                fields[plain]+=" "+line.decode("utf-8").strip()
                continue
            block=plain=None
            key,sep,value=line.partition(b":")
            if sep and key in (b"name",b"description"):
                if key in fields:return None
                field=value.decode("utf-8").strip()
                if key==b"description" and re.fullmatch(r"[|>][+-]?[1-9]?",field):
                    block=key
                    fields[key]=""
                else:
                    fields[key]=field
                    plain=key
        else:
            return None
    n=fields.get(b"name","").strip("'\\\"")
    d=fields.get(b"description","").strip("'\\\"") or " ".join(x for x in chunks if x)
    if not NAME.fullmatch(n) or not d:
        return None
    return n,d[:640]

def scan(roots_to_scan,idx):
    old=read_json(idx,{})
    prev={x["path"]:x for x in old.get("records",[])} if isinstance(old,dict) and old.get("version")==FORMAT else {}
    records={}
    stats={"reused":0,"read":0,"skipped":0,"removed":0,"truncated":False}
    scanned=[]
    dirs=0
    for root in dict.fromkeys(p for p in map(safe_root,roots_to_scan) if p is not None):
        scanned.append(str(root))
        stack=[(root,0)]
        while stack:
            base,depth=stack.pop()
            dirs+=1
            if dirs>MAX_DIRS or len(records)>=MAX_SKILLS:
                stats["truncated"]=True
                break
            path=base/"SKILL.md"
            if plain(path,stat.S_ISREG):
                try:
                    stamp=path.stat()
                    stamp=[stamp.st_mtime_ns,stamp.st_size,stamp.st_ino]
                    key=str(path)
                    if key in records: continue
                    oldrow=prev.get(key,{})
                    if oldrow.get("stamp")==stamp:
                        row=oldrow
                        stats["reused"]+=1
                    else:
                        stats["read"]+=1
                        parsed=metadata(path)
                        row={"name":parsed[0],"description":parsed[1],"path":key,"stamp":stamp} if parsed else None
                    if row: records[key]=row
                    else: stats["skipped"]+=1
                except (OSError,UnicodeError):
                    stats["skipped"]+=1
            if depth>=5: continue
            try:
                stack.extend((x,depth+1) for x in sorted(base.iterdir(),reverse=True)
                             if (not x.name.startswith(".") or (depth==0 and x.name==".system" and root==HOME/".codex/skills"))
                             and x.name not in ("node_modules","__pycache__","references","assets","scripts")
                             and plain(x,stat.S_ISDIR))
            except OSError:
                stats["skipped"]+=1
        if stats["truncated"]: break
    stats["removed"]=len(set(prev)-set(records))
    current=sorted(records.values(),key=lambda x:(x["name"],x["path"]))
    write_json(idx,{"version":FORMAT,"roots":scanned,"records":current})
    return {**stats,"indexed":len(current),"roots":len(scanned)}

def clauses(prompt):
    clean=QUOTE.sub(" ",FENCE.sub(" ",norm(prompt)))
    return [p for p in re.split(r"[\n。；;]",clean) if not NEG.search(p)]

JP_LANGUAGE=re.compile(r"日本語|japanese\b",re.I)
JP_EDIT=re.compile(r"推敲|校正|添削|(?:書き|言い換え|整え|自然に)[^\n。]{0,12}(?:直し|直す|換え)|書き換|書き直|整え|言い換|直し|直す|polish|proofread|rewrite",re.I)
JP_QUALITY=re.compile(r"推敲|校正|添削|自然|不自然|ぎこち|読みやす|言い回し|文体|aiっぽ|ai臭|natural|fluency|polish|proofread",re.I)
JP_STYLE_NO=re.compile(r"(?:文体|語調|言い回し|推敲|校正|添削)(?:は|を|の変更は)?(?:変更しない|変えない|しない|不要|そのまま|維持|保持)|(?:推敲|校正|添削)せず|リンク(?:先)?だけ|urlだけ",re.I)

def japanese_style(prompt,parts):
    clean=QUOTE.sub(" ",FENCE.sub(" ",norm(prompt)))
    if JP_STYLE_NO.search(clean):
        return False
    return any(JP_LANGUAGE.search(p) and JP_EDIT.search(p) and JP_QUALITY.search(p) for p in parts)

def capabilities(prompt,guide_names):
    parts=clauses(prompt)
    extra=[]
    for tag,target,op,*_ in CAPS:
        if target and any(re.search(target,p) and re.search(op,p) for p in parts):
            extra.append(tag)
    if japanese_style(prompt,parts):
        extra.append("japanese-style")
    # Word editing containing pasted meeting notes is not necessarily a meeting task.
    if "word-revisions" in guide_names:
        extra=[k for k in extra if k!="meeting" or any(
            re.search(r"(議事録|会議メモ)(?:を|の内容を)(?:別途|別に)?(?:要約|整理|まとめ)",p) for p in parts)]
    return list(dict.fromkeys([*guide_names,*extra])),parts

def rank_fixed(prompt,records,guide_names):
    stages,parts=capabilities(prompt,guide_names)
    picks=[]
    for stage in stages:
        _,_,_,preferred,terms=CAP_BY_NAME[stage]
        candidates=[]
        for r in records:
            n=r["name"]; meta=norm(r["description"])
            if stage=="japanese-style" and not JP_LANGUAGE.search(meta):continue
            prior=1000-10*preferred.index(n) if n in preferred else 0
            matches=sum(w in meta for w in terms)
            score=prior+matches if prior or matches>=2 else 0
            if score:candidates.append((score,r))
        candidates.sort(key=lambda x:(-x[0],x[1]["name"],x[1]["path"]))
        if candidates and (len(candidates)==1 or candidates[0][0]>candidates[1][0]):
            picks.append((stage,candidates[0][1]))
    # Conservative fallback for locally authored skills with specific names
    # outside the bundled task categories. Require at least two meaningful
    # distinct whole tokens and an action, not a product/topic mention alone.
    if not stages and any(ACTION.search(p) for p in parts):
        clean=" ".join(parts)
        words=set(re.findall(r"[a-z0-9]+",clean))
        options=[]
        for row in records:
            tokens=[t for t in row["name"].split("-") if len(t)>=3 and not GENERIC.fullmatch(t)]
            hits={t for t in tokens if t in words}
            if len(hits)>=2 and len(hits)==len(set(tokens)):
                options.append((len(hits),row))
        options.sort(key=lambda x:(-x[0],x[1]["name"],x[1]["path"]))
        if options and (len(options)==1 or options[0][0]>options[1][0]):
            picks.append(("specific-local", options[0][1]))
    seen=set()
    return [(stage,r) for stage,r in picks if r["path"] not in seen and not seen.add(r["path"])]

def rank(prompt,records,guide_names,explanations=None,prepared=None):
    parts=OPEN["work_clauses"](prompt)
    if not parts:return []
    clean="。".join(parts)
    if JP_STYLE_NO.search(norm(prompt)):
        clean+="。文体は維持"
    fixed=rank_fixed(clean,records,guide_names)
    known=lambda part: capabilities(part,[])[0] or any(
        tag in guide_names and re.search(rx,part) for tag,rx in (
            ("readme",r"\breadme\b"),("debugging",r"不具合|バグ|原因|debug|bug"),
            ("career-writing",r"応募書類|エントリーシート|志望動機|\bes\b"),
            ("word-revisions",r"変更履歴|word|docx"),
            ("regression-testing",r"回帰テスト|regression test"),
            ("python-testing",r"回帰テスト|regression test"),
            ("javascript-testing",r"回帰テスト|regression test")))
    results=fixed+OPEN["rank_open"](parts,records,known,explanations=explanations,prepared=prepared,prompt=prompt)
    # Beside a confident pick, a list of loose matches from the request's other sentences
    # (its output format, what to keep) is noise.
    if any(k!="open-shortlist" for k,_ in results):
        results=[(k,r) for k,r in results if k!="open-shortlist"]
    seen=set()
    return [(k,r) for k,r in results if r["path"] not in seen and not seen.add(r["path"])]

EDIT_OPS=frozenset({"edit","proofread","translate","ai-writing"})

def one_per_name(records):
    """The same skill installed in two places (~/.codex/skills and ~/.claude/skills) is one
    candidate, not a tie that hides both. Prefer the copy Claude Code loads itself."""
    native=(HOME/".claude"/"skills").as_posix().casefold().rstrip("/")+"/"
    best={}
    for row in records:
        key=row["name"]
        here=row["path"].replace("\\","/").casefold().startswith(native)
        if key not in best or (here and not best[key][0]):
            best[key]=(here,row)
    keep={id(r) for _,r in best.values()}
    return [r for r in records if id(r) in keep]

def with_keywords(records,path=None):
    """Attach index-time keywords to rows whose SKILL.md has not changed since."""
    obj=read_json(path or KEYWORDS,{})
    saved=obj.get("records",{}) if isinstance(obj,dict) and obj.get("version")==1 else {}
    out=[]
    for row in records:
        k=saved.get(row["path"])
        if isinstance(k,dict) and k.get("stamp")==row.get("stamp") and isinstance(k.get("keywords"),list):
            row=dict(row,keywords=[x for x in k["keywords"] if isinstance(x,str)][:16])
        out.append(row)
    return out

class Prepared(dict):
    """{path: features}, filled on first use from the cache's packed rows, plus catalogue term
    counts and postings (term -> positions in `order`) when they match these records."""
    counts=None
    post=None
    order=None
    def __init__(self,raw=None):
        super().__init__()
        self.raw=raw or {}
    def __missing__(self,path):
        packed=self.raw.get(path)
        if packed is None:raise KeyError(path)
        entry=OPEN["unpack"](json.loads(packed))
        self[path]=entry
        return entry
    def get(self,path,default=None):
        try:return self[path]
        except KeyError:return default

def prepared_features(records,path=None,save=True):
    """Per-row routing features, cached on disk so a hook neither re-tokenizes every skill nor
    unpacks rows the request shares no word with."""
    path=path or FEATURES
    try:
        obj=read_json(path,{})
    except (ValueError,json.JSONDecodeError):
        obj={}
    if not isinstance(obj,dict) or obj.get("key")!=FEATURE_KEY:obj={}
    cache=obj.get("records",{})
    # A row is current while its SKILL.md and its keywords are unchanged.
    sigs={r["path"]:[r.get("stamp"),zlib.crc32("\n".join(r.get("keywords") or ()).encode("utf-8"))] for r in records}
    order=[r["path"] for r in records]
    try:
        if (obj.get("order")==order and all(cache[p][0]==sigs[p] for p in order)
                and isinstance(obj.get("post"),dict) and isinstance(obj.get("counts"),list)):
            out=Prepared({p:cache[p][1] for p in order})
            c=obj["counts"]
            out.counts=(Counter(c[0]),Counter(c[1]),{j:[tuple(x) for x in v] for j,v in c[2].items()})
            out.post=obj["post"]; out.order=order
            return out
    except (KeyError,TypeError,ValueError,IndexError,AttributeError):
        pass
    out=Prepared()
    fresh={}
    for row in records:
        hit=cache.get(row["path"])
        entry=None
        try:
            if hit and hit[0]==sigs[row["path"]]:entry=OPEN["unpack"](json.loads(hit[1]))
        except (TypeError,ValueError):
            entry=None
        if entry is None:entry=OPEN["prepare"](row)
        out[row["path"]]=entry
        fresh[row["path"]]=[sigs[row["path"]],json.dumps(OPEN["pack"](entry),ensure_ascii=False,separators=(",",":"))]
    out.counts=OPEN["catalog_counts"](out[p] for p in order)
    out.post=OPEN["postings"]([out[p] for p in order],out.counts)
    out.order=order
    if save:
        try:
            write_json(path,{"key":FEATURE_KEY,"order":order,"records":fresh,"post":out.post,
                             "counts":[dict(out.counts[0]),dict(out.counts[1]),out.counts[2]]})
        except (OSError,ValueError):
            pass
    return out

def summary(text, limit=180):
    """Single-line excerpt of a skill description, shown to the agent as data."""
    clean=" ".join("".join(c if c.isprintable() else " " for c in str(text)).split())
    return clean if len(clean)<=limit else clean[:limit-1]+"…"

def active_refs(prompt,guide_names,index=INDEX):
    obj=read_json(index,{})
    # An index from the previous format still routes until the next refresh rereads it.
    if not isinstance(obj,dict) or obj.get("version") not in (1,FORMAT):return []
    out=[]
    allowed=[p for p in map(safe_root,(*DEFAULT,*roots())) if p is not None]
    explanations={}
    # A request to edit existing text is a weak fit for a skill that only creates new
    # material; such candidates are named, not handed with a read instruction.
    request=" ".join(OPEN["work_clauses"](prompt))
    asked=OPEN["features"](request)[2]
    editing=bool(asked&EDIT_OPS or JP_EDIT.search(request)) and "create" not in asked
    records=with_keywords(one_per_name(obj["records"]))
    for stage,row in rank(prompt,records,guide_names,explanations,prepared_features(records)):
        p=Path(row["path"])
        if not NAME.fullmatch(row["name"]) or any(ord(c)<32 for c in str(p)) or p.name!="SKILL.md":continue
        if not p.is_absolute() or not any(p.is_relative_to(root) for root in allowed):continue
        if not plain(p,stat.S_ISREG):continue
        try:
            resolved=p.resolve(strict=True)
            now=p.stat()
        except (OSError,RuntimeError):continue
        if p!=resolved or not any(resolved.is_relative_to(root) for root in allowed):continue
        if row["stamp"]!=[now.st_mtime_ns,now.st_size,now.st_ino]:continue
        offered=OPEN["features"](row["name"]+" "+row.get("description",""))[2]
        fit="name_only" if editing and "create" in offered and not offered&EDIT_OPS else "choose" if stage=="open-shortlist" else "read"
        out.append({"stage":stage,"name":row["name"],"path":p.as_posix(),"fit":fit,
                    "summary":summary(row.get("description","")),
                    "selection_reason":(explanations.get(row["path"]) if stage.startswith("open-") else None)
                    or {"operation":stage, "basis":"local_name_match" if stage=="specific-local" else "fixed_stage_match"}})
    return out[:8]

def command(argv):
    p=argparse.ArgumentParser(description=__doc__)
    sub=p.add_subparsers(dest="sub",required=True)
    r=sub.add_parser("refresh")
    r.add_argument("--keywords",action="store_true",help="also have the host model write Japanese keywords for new or changed skills")
    k=sub.add_parser("keywords",help="write Japanese keywords for new or changed skills")
    for x in (r,k):x.add_argument("--limit",type=int,default=None)
    sub.add_parser("status")
    for verb in ("add-root","remove-root"):
        sub.add_parser(verb).add_argument("path")
    a=p.parse_args(argv)
    if a.sub in ("refresh","keywords"):
        result=scan((*DEFAULT,*roots()),INDEX) if a.sub=="refresh" else {}
        records=read_json(INDEX,{}).get("records",[])
        if a.sub=="keywords" or a.keywords:
            K=runpy.run_path(str(Path(__file__).with_name("keywords.py")))
            old=read_json(KEYWORDS,{})
            saved=old.get("records",{}) if isinstance(old,dict) and old.get("version")==1 else {}
            try:
                saved,stats=K["update"](records,saved,limit=a.limit)
            except K["Unavailable"] as err:
                # Say so plainly; the index itself is still refreshed.
                result["keywords_error"]=str(err)
                print(json.dumps(result,ensure_ascii=True))
                return 2
            write_json(KEYWORDS,{"version":1,"records":saved})
            result.update(stats)
        prepared_features(with_keywords(records))
    elif a.sub=="status":
        curr=read_json(INDEX,{})
        kw=read_json(KEYWORDS,{}).get("records",{})
        result={"indexed":len(curr.get("records",[])),"with_keywords":len(kw),"extra_roots":[str(p) for p in roots()]}
    else:
        saved=roots()
        arg=Path(a.path).expanduser()
        if a.sub=="add-root":
            root=safe_root(arg)
            if root is None or not root.is_dir():p.error("choose a specific local directory of installed skills")
            if root not in saved:
                if len(saved)==16:p.error("too many extra roots")
                saved.append(root)
        else:
            if not arg.is_absolute():p.error("absolute root required")
            saved=[x for x in saved if x!=arg]
        write_json(ROOTS,[str(x) for x in saved])
        result={"extra_roots":[str(x) for x in saved],"refresh_required":True}
    print(json.dumps(result,ensure_ascii=True))
    return 0
