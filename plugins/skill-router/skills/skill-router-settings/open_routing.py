"""Experimental task framing and open retrieval. No network or model calls."""
from collections import Counter
from functools import lru_cache
import math
import re
import unicodedata

def normalize(text):
    return unicodedata.normalize("NFKC",text).casefold()

FENCE=re.compile(r"(?ms)^[ \t]*[~\x60]{3,}.*?^[ \t]*[~\x60]{3,}[^\n]*")
QUOTE=re.compile(r"「[^」]*」|『[^』]*』|“[^”]*”")
# The markers describe grammatical roles, not specific deliverable domains.
EXAMPLE=re.compile(r"^(?:参考(?:資料|例)?|素材|背景|作例|例文|(?:デモに出す|画面に出す)例|(?:動画|画像|デモ|映像)の中(?:では|で|に)|デモ(?:用|内)(?:では|で|に)?|例として)(?:[:：\s、]|(?<=では)|(?<=で)|(?<=として)|$)")
SOURCE=re.compile(r"(?:を|の内容を)(?:例|作例|参考|素材|題材|サンプル)(?:と|に)して|を(?:参考|素材|題材|例)に|(?:例|資料|素材|参考)を(?:もと|元|基)に|(?:から|で)(?:引用|参照)して|(?:流れ|場面|様子|例)(?:を|として)(?:見せ|描|再現|使)|例として(?:見せ|描|使)|(?:を|も)(?:参照|参考に)して")
NOT_REQUEST=re.compile(r"\b(?:do not|don't|never|without)\b|しないで|せずに|は不要|は変更しない|は修正しない|するべきか|したほうがいい|できるか教え|(?:方法|意味)(?:を教え|が載|は何)|例です|載っています|とは[？?]|って何|とは何|だけを参考")
REQUEST=re.compile(r"して|考えて|(?:相手|役)になって|(?:作り|書き)直し(?:て|ください)|(?:更新|修正|編集|追加|作成)し\s*$|作って|書いて|まとめて|仕上げて|書き出して|レンダリングして|整えて|直して|調べて|提案して|付けて|入れて|追加して|切って|分けて|保存して|用意して|生成して|確認して|(?:を|で)(?:制作|作成|編集|抽出|変換|整理|実装|検証|追加|推敲|校正)\s*$|\b(?:create|generate|render|transcribe|extract|convert|rewrite|revise|polish|validate|fix|edit|summarize|compile|proofread|write|build|test|make|produce|explain|describe|export|remove|strip|humanize|clean up|tighten)\b")
HEADING=re.compile(r"^(?:成果物|納品物|作業|依頼|お願い|やること|出力|deliverables?|task)\s*[:：]\s*")
SECTION=re.compile(r"^\s*(?:【([^】]+)】|#{1,6}\s+([^:：\n]+))\s*[\\:]?\s*$")
CONTEXT_ONLY=re.compile(r"^(?:素材|背景|参考(?:資料|例)?|伝えたいこと|作例|例文|コンセプト|source materials?|examples?|background)$")
OUTPUT_LABEL=re.compile(r"^(?:.+?(?:動画|画像|音声|スライド|ドキュメント|成果物)|動画|画像|音声|スライド|成果物|納品物|outputs?|deliverables?)$")
OUTPUT_VERB=re.compile(r"(?:を|に)(?:作る|制作する|作成する|生成する|書き出す|用意する|出す)\s*$")
# English sentences end with ". " (or ?/!) followed by a new sentence; file names such as
# "README.md" have no space after the dot and stay intact.
SPLIT=re.compile(r"[。\n；;]|(?<=[a-z0-9)\]\"'])[.?!]\s+(?=[a-z])|[、,]\s*(?=(?:さらに|次に|続けて|その後|別途|一方|あわせて|併せて|そして|ほかに))")

KEEP_AS_IS=re.compile(r"(?:変え|変更し|触ら|残さ|含め|し)ない|維持|保持|そのまま|禁止|不要")

def work_clauses(text):
    clean=FENCE.sub(" ",normalize(text))
    if re.search(r"(?:^|[。\n])\s*(?:今回は[、,\s]*)?(?:(?:自動選択|ルーター|スキル)(?:は|を)?(?:不要|使わないで(?:ください)?))(?=\s*(?:[。.!！\n、,]|$))",QUOTE.sub(" ",clean)):
        return []
    parts=[]
    section=""
    for line in clean.splitlines():
        heading=SECTION.fullmatch(line)
        if heading:
            section=next(x for x in heading.groups() if x).strip()
            continue
        if CONTEXT_ONLY.fullmatch(section):continue
        line=QUOTE.sub(" ",line)
        for part in SPLIT.split(line):
            part=part.strip(" \t、・-*")
            if EXAMPLE.match(part):
                # A comma can end an introductory source followed by the actual request.
                intro,*rest=re.split(r"[、,]",part,maxsplit=1)
                part=rest[0].strip() if rest and not SOURCE.search(rest[0]) else ""
            if not part:continue
            match=SOURCE.search(part)
            if match:
                tail=part[match.end():].strip(" 、,")
                # A trailing 'show it as an example' has no independent task.
                part=tail if REQUEST.search(tail) else ""
            if not part:continue
            # Preserve an earlier request followed by a prohibition about a separate item.
            # Drop the tail only when it is that prohibition ("…して、コミットはしないで"), not
            # when it is the request itself ("変更を見て、コミットメッセージを書いて").
            head,*tail=re.split(r"[、,]\s*(?=(?:公開|共有|コミット|プッシュ|push|既存ファイル|(?:日本語の)?(?:文体|語調|言い回し)))",part,maxsplit=1)
            if tail and (KEEP_AS_IS.search(tail[0]) or NOT_REQUEST.search(tail[0])):
                part=head
            part=HEADING.sub("",part)
            if (REQUEST.search(part) or video_operation(part) in {"video-explain","video-extract","video-extract-audio","video-extract-frames","video-frames-ocr"} or OUTPUT_LABEL.fullmatch(section) and OUTPUT_VERB.search(part)) and (not NOT_REQUEST.search(part) or video_operation(part)=="video-explain"):
                parts.append((section+" "+part if OUTPUT_LABEL.fullmatch(section) else part))
    return parts


VIDEO = re.compile(r"動画|映像|録画|mp4|\bvideos?\b")
VIDEO_EXPLAIN = re.compile(
    r"(?:手順|やり方|方法|流れ|仕組み).{0,22}(?:説明|教え|知りたい|解説|だけ|のみ)"
    r"|(?:説明|解説)(?:だけ|のみ)|(?:どう|どのように).{0,40}(?:教え|説明)"
    r"|\b(?:explain|describe|walk me through|how (?:to|do i|can i)|instructions? (?:only|for))\b"
)
VIDEO_SOURCE = re.compile(
    r"(?:動画|映像|録画|mp4|クリップ).{0,22}(?:から|の中から|内の|に映っ)"
    r"|(?:既存|添付|収録済み|撮影済み|手元|元|この|その).{0,12}(?:動画|映像|録画|mp4)"
    r"|\b(?:from (?:the |this |an? )?(?:existing |attached )?(?:video|footage|mp4)|(?:attached|existing|recorded) (?:video|footage|mp4))\b"
)
VIDEO_EXTRACT = re.compile(
    r"抽出|取り出|抜き出|書き起こ|文字起こ|切り出|ocr|文字認識|フレーム.{0,12}拾"
    r"|\b(?:extract\w*|pull out|transcri\w*|capture frames)\b"
)
VIDEO_CREATE = re.compile(
    r"作って|作成|制作|生成|用意して|書き出|作り直|レンダリング|に(?:して|仕上げ)|作りたい"
    r"|\b(?:make|build|create|produce|render|generate|export)\b"
)
VIDEO_EDIT = re.compile(
    r"編集|トリミング|切り詰め|切って|(?:テロップ|字幕).{0,10}(?:付け|つけ|追加|入れ)"
    r"|\b(?:edit|trim|cut|add captions|add subtitles)\b"
)
VIDEO_OCR = re.compile(r"ocr|文字認識|画面.{0,12}文字|文字.{0,12}(?:読|抜|抽)|字幕.{0,12}(?:読|抜|抽)|テロップ.{0,12}(?:読|抜|抽)|(?:画像|フレーム|コマ|静止画).{0,12}(?:読|文字)|\b(?:on.screen text|visible text)\b")

def video_operation(part):
    """Classify what is requested, not merely the medium mentioned."""
    if not VIDEO.search(part):
        return None
    if VIDEO_EXPLAIN.search(part):
        return "video-explain"
    source=VIDEO_SOURCE.search(part)
    if source and VIDEO_EXTRACT.search(part):
        if VIDEO_OCR.search(part):
            return "video-frames-ocr"
        if re.search(r"音声|音だけ|audio|sound|書き起こ|文字起こ|\btranscri",part):
            return "video-extract-audio"
        if re.search(r"静止画|フレーム|画像|コマ|\bframes?\b|\bstills?\b",part):
            return "video-extract-frames"
        return "video-extract"
    if source and VIDEO_EDIT.search(part):
        return "video-edit"
    if VIDEO_CREATE.search(part):
        return "video-create"
    if VIDEO_EDIT.search(part):
        return "video-edit"
    return "video-unspecified"

# Shared query vocabulary. New skills do not need a category, provider, or name added.
# These equivalences supplement generic token and Japanese character n-gram matching.
ALIASES={
 "video": r"動画|映像|mp4|\bvideos?\b",
 "raster": r"png|jpeg|ビットマップ|\braster\b|\bbitmaps?\b",
 "image": r"画像|イラスト|ビットマップ|png|jpeg|\b(?:images?|imagegen|illustrations?|bitmaps?|raster)\b",
 "animation": r"アニメーション|(?<!ロ)モーション|\banimat(?:ion|e|ed)\b",
 "create": r"制作|生成|作成|作って|作り直|作る|書き出|(?:図|表|一覧|箇条書き)に(?:して|する)|(?<!に)書いて|\b(?:generat\w+|creat\w+|render\w+|produc\w+|writ(?:e|es|ing))\b",
 "extract": r"抽出|切り出|\bextract\w*\b",
 "frames": r"フレーム|コマ(?:を|の)|\b(?:key ?frames?|frames?)\b",
 "ocr": r"文字認識|\bocr\b|\boptical character recognition\b",
 "edit": r"編集|修正|直して|直す|改善|リファクタ|\b(?:edit\w*|rewrit\w*|fix(?:es|ed|ing)?|refactor\w*|improv\w+)\b",
 "convert": r"変換|\bconvert\w*\b",
 "audio": r"音声|録音|\b(?:audio|recordings?)\b",
 "transcribe": r"文字起こし|書き起こ|\b(?:transcri\w+|speech.to.text)\b",
 "citation": r"引用情報|出典|参考文献|\b(?:citation\w*|bibliograph\w*|references?)\b",
 "academic": r"論文|\b(?:research|papers?|academic)\b",
 "organize": r"整理|管理|\b(?:manag\w+|organiz\w+)\b",
 "schedule": r"カレンダー|予定|\b(?:calendars?|schedule\w*|events?)\b",
 "check": r"確認|検証|監査|\b(?:check\w*|verif\w+|test\w*|audit\w*)\b",
 "translate": r"翻訳|訳して|\btranslat\w+\b",
 "location": r"地図|位置情報|\b(?:map|maps|geospatial)\b",
 # Developer writing tasks: Japanese loanwords must meet the English skill descriptions.
 # Latin words are bounded by lookarounds because Japanese particles count as \w.
 "proofread": r"校正|文法|誤字|スペル(?:ミス)?|(?<![a-z0-9])(?:proofread\w*|grammar|spelling|typos?)(?![a-z0-9])",
 "commit": r"コミット|(?<![a-z0-9])commit(?:s|ted|ting)?(?![a-z0-9])",
 "changelog": r"チェンジログ|リリースノート|(?<![a-z0-9])(?:changelogs?|release notes)(?![a-z0-9])",
 "pull-request": r"プルリク(?:エスト)?|(?<![a-z0-9])pull requests?(?![a-z0-9])|(?<![a-z0-9])(?<!自己)prs?(?![a-z0-9])",
 "code-review": r"コードレビュー|(?<![a-z0-9])(?:code[- ]?review\w*|review(?:ing)? (?:the |this |your )?code|review comments?)(?![a-z0-9])",
 # Internal status reports and AI-sounding prose: named the same way in requests and skills.
 "status-update": r"(?<![a-z0-9])3p ?(?:アップデート|updates?)|週次(?:報告|レポート|アップデート)|進捗報告|ステータス(?:報告|レポート)|(?<![a-z0-9])(?:status (?:reports?|updates?)|leadership updates?|weekly updates?)(?![a-z0-9])",
 "ai-writing": r"(?<![a-z0-9])ai(?:っぽ|臭|らし)|人間が書いた(?:ような|みたい|よう)|(?<![a-z0-9])(?:ai[- ](?:writing(?: patterns?)?|isms?|slop|tells?)|sounds? (?:like )?(?:an? )?ai(?:[- ](?:generated|written))?|(?:reads?|sounds?) like (?:a )?human|humans? wrote|humaniz\w*)(?![a-z0-9])"
}
SPECIALIZATIONS={
    "search marketing": r"\bseo(?:\b|[- ])|検索順位",
    "editorial article": r"\barticles?\b|記事",
    "third-party provider": r"\bgemini(?:\b|[- ])|nano banana",
    "chinese text": r"\bchinese\b|中文|中国語",
    "social post": r"\blinkedin\b|リンクトイン",
}
SCOPE={key:re.compile(rx,re.I) for key,rx in SPECIALIZATIONS.items()}
# Named developer/writing tasks. Each is specific enough to qualify a candidate alone.
TASKS={"proofread","commit","changelog","pull-request","code-review","status-update","ai-writing"}
OPS={"create","extract","ocr","edit","convert","transcribe","organize","check","translate"}
GENERIC={"using","with","from","into","task","works","use","skill","please","when","for","have","make","all","best","practices","tool","tools","and","the","output"}
PATTERNS={k:re.compile(v) for k,v in ALIASES.items()}
LATIN=re.compile(r"[a-z][a-z0-9+#]{2,}")
JP=re.compile(r"[\u3041-\u30ff\u3400-\u9fff]{2,}")

def canonical(token):
    """Fold plain inflections: refactoring/refactor, dockerfile/docker, reviews/review.
    Derived nouns stay distinct (generated is not generator)."""
    if token.endswith("ies") and len(token)>=7:return token[:-3]+"y"
    for suffix in ("file","ings","ing","ed","es","s"):
        if token.endswith(suffix) and len(token)-len(suffix)>=4 and not token.endswith("ss"):
            stem=token[:-len(suffix)]
            if suffix=="es" and not stem.endswith(("ch","sh","x","z")):stem=token[:-1]
            # dockerfiles -> dockerfile -> docker
            return canonical(stem) if stem.endswith("file") else stem
    return token

@lru_cache(maxsize=25000)
def features(value):
    text=normalize(value)
    latin={canonical(t) for t in set(LATIN.findall(text))-GENERIC}
    jp=set()
    for run in JP.findall(text):
        for n in (2,3):
            jp.update(run[i:i+n] for i in range(len(run)-n+1))
    return (frozenset(latin),frozenset(jp),frozenset(k for k,p in PATTERNS.items() if p.search(text)))


# Cross-language verbs and artefacts. Match a structured operation, not an individual
# skill or example prompt. A specialised candidate must demonstrate that operation.
ROLES = {
    "video-create": (
        r"(?:動画|映像|アニメーション).*?(?:作って|作成|制作|生成|書き出|作り直)|(?:作成|制作|生成).*?(?:動画|映像)",
        r"\bvideo (?:creation|production|generation)\b|\b(?:create|produce|render|generate)\b[^.;]{0,35}\b(?:walkthrough )?videos?\b|\bgenerate media\b",
        r"\b(?:stitch projects|ai video|video and audio)\b"),
    "video-edit": (
        r"(?:動画|映像|録画).*?(?:編集|字幕|テロップ|切って|切り詰め|トリミング)",
        r"\b(?:video and audio|video|media)\b[^.;]{0,60}\b(?:edit(?:ing)?|overlays/subtitles)\b|\bedit\b[^.;]{0,30}\bvideo\b",
        r"\b(?:video perception|edit)\b"),
    "video-frames-ocr": (
        r"(?:動画|映像|録画|mp4).*?(?:数秒おき|フレーム|コマ|静止画|画像).*?(?:文字認識|読[むみ]|ocr)",
        r"(?=.*\b(?:video|videos|mp4)\b)(?=.*\b(?:key ?frames?|frames?)\b)(?=.*\bocr\b)",
        r""),
    "audio-transcription": (
        r"(?:録音|音声).*?(?:書き起こ|文字起こ|書き起し|文字に|文章に)",
        r"(?=.*\b(?:audio|speech|recordings?)\b)(?=.*\b(?:transcri\w+|speech.to.text)\b)",
        r"\b(?:azure|sdk|api)\b"),
    "document-form-fill": (
        r"(?<![a-z0-9])pdf(?![a-z0-9]).*?(?:フォーム|申込書|申請書|記入欄|氏名欄|入力欄).*?(?:入力|記入|埋め)",
        r"(?=.*\bpdf\b)(?=.*\b(?:forms?|fill(?:ed|ing)?)\b)",
        r""),
    "document-review": (
        r"(?:(?<![a-z0-9])(?:docx|word)(?![a-z0-9])|ワード).*?(?:変更履歴|修正履歴|校閲|共同編集)",
        r"(?=.*\b(?:docx|word)\b)(?=.*\b(?:edit|tracked changes|track changes|review|comments)\b)",
        r"\b(?:convert|conversion|sdk)\b"),
    "query-performance": (
        r"(?:索引|実行計画|クエリ).*?(?:索引|実行計画|クエリ|パフォーマンス|遅い|高速|最適|インデックス)",
        r"(?=.*\b(?:queries|query)\b)(?=.*\b(?:indexing|indexes|indices|index|query plan)\b)(?=.*\b(?:optimization|tuning|slow|performance)\b)",
        r"\b(?:postgresql|postgres|neon|mysql|sqlite|mongodb)\b"),
    "product-language": (
        r"(?:翻訳ファイル|言語切り替え|言語を切り替え|多言語化|ローカライズ|右から左).*?(?:言語|翻訳|修正|確認|移して|文言|対応)",
        r"(?=.*\b(?:internationalization|locali[sz]ation|i18n)\b)(?=.*\b(?:translations?|locale|rtl|hardcoded strings)\b)",
        r""),
}
ROUTE_PATTERNS = [(key,re.compile(query),re.compile(r"\A"+evidence if evidence.startswith("(?=") else evidence),re.compile(limit) if limit else None)
                  for key,(query,evidence,limit) in ROLES.items()]

VIDEO_EXTRA_EVIDENCE = {
    "video-extract-frames": r"\A(?=.*\b(?:video|videos|mp4|footage)\b)(?=.*\b(?:extract\w*|capture\w*)\b)(?=.*\b(?:frames?|stills?)\b)",
    "video-extract-audio": r"\A(?=.*\b(?:video|videos|mp4|footage|ffmpeg)\b)(?=.*\b(?:extract\w*|transcri\w*|convert\w*)\b)(?=.*\b(?:audio|speech)\b)",
    "video-extract": r"\A(?=.*\b(?:video|videos|mp4|footage)\b)(?=.*\b(?:extract\w*|transcri\w*)\b)",
}

def role_picks(parts, records, explanations=None):
    """Choose only candidates with positive evidence for all requested role constraints."""
    # Stitch together adjacent parts for selection when a second sentence names an
    # operation on the same artefact. Prefer a specialised compound to a generic edit.
    whole="。".join(parts)
    video_by_part={i: op for i,part in enumerate(parts) if (op:=video_operation(part))}
    active=[]
    for key,op,evidence,limit in ROUTE_PATTERNS:
        if key.startswith("video-"):
            matches=[part for i,part in enumerate(parts) if video_by_part.get(i)==key]
            if matches:
                active.append((key,"。".join(matches),evidence,limit))
        else:
            # Non-video roles retain their existing cross-sentence matching.
            matched=op.search(whole)
            if matched:
                active.append((key,matched.group(),evidence,limit))
    for key,rx in VIDEO_EXTRA_EVIDENCE.items():
        matches=[part for i,part in enumerate(parts) if video_by_part.get(i)==key]
        if matches:
            active.append((key,"。".join(matches),re.compile(rx),None))
    picks=[]
    # Explicit video roles must not fall through to unrelated metadata matches.
    # A general request mentioning video is also insufficient to infer extraction.
    covered=set(video_by_part)
    for key,role_text,evidence,limit in active:
        if not key.startswith("video-"):
            first_start=whole.index(role_text)
            first=whole.count("。",0,first_start)
            last=whole.count("。",0,first_start+len(role_text))
            covered.update(range(first,last+1))
        ql=features(role_text)[0]
        choices=[]
        for row in records:
            desc=normalize(row["description"])
            name=normalize(row["name"])
            positive=re.split(r"(?i)\b(?:do not use|not intended for|avoid using)\b",desc,maxsplit=1)[0]
            evidence_text=name+" "+positive
            if not evidence.search(evidence_text): continue
            restricted=limit.search(evidence_text) if limit else None
            score=8
            score+=2*len(ql & features(name)[0])
            if restricted and not (features(restricted.group())[0]&ql): score-=3
            # Stronger evidence for functions explicitly requested, rather than
            # incidental media or provider references.
            if key=="video-edit" and re.search(r"字幕|テロップ",whole):
                score+=2 if re.search(r"\b(?:subtitles?|captions?|text overlays)\b",positive) else 0
            if key=="video-edit":
                score+=3 if re.search(r"\b(?:edit timelines?|trimm?ing|cuts?)\b",positive) else 0
                score-=4 if re.search(r"\b(?:generative video editing|text-to-video|lora)\b",positive) else 0
            if key=="document-review":
                score+=3 if re.search(r"\b(?:track(?:ed)? changes|review|comments)\b",positive) else 0
            if key=="audio-transcription":
                score+=2 if re.search(r"\b(?:recordings?|documentation|markdown)\b",positive) else 0
            if key=="query-performance":
                score+=2 if re.search(r"\bquery plans?\b",positive) else 0
            if key=="product-language":
                score+=2 if re.search(r"\b(?:locale|hardcoded strings|rtl)\b",positive) else 0
            if key=="video-create":
                if re.search(r"\b(?:extract\w*|transcri\w*)\b",name) and not re.search(r"\bvideo (?:creation|production|generation)\b",positive): continue
                if re.search(r"\bvideo creation\b",positive): score+=2
                score+=len((ql & features(positive)[0]) - features(name)[0])
            choices.append((score,row))
        choices.sort(key=lambda item:(-item[0],item[1]["name"],item[1]["path"]))
        if choices and (len(choices)==1 or choices[0][0]>choices[1][0]):
            picks.append(("open-search",choices[0][1]))
            if explanations is not None:
                explanations.setdefault(choices[0][1]["path"], {
                    "operation":key, "basis":"operation_match",
                    "score":choices[0][0], "runner_up":choices[1][0] if len(choices)>1 else None})
    return picks,covered

def keyword_terms(row):
    """Index-time keywords and aliases (written by the host model at refresh), normalized."""
    out=[]
    for k in row.get("keywords") or ():
        k=normalize(str(k)).strip()
        if 2<=len(k)<=40 and k not in out:out.append(k)
    return out[:16]

# A keyword such as "スライド作成" also matches "スライドを作成して": the subject is said,
# and so is the same action word.
ACTION_TAIL=re.compile(r"(作成|生成|作り|作る|開発|設計|変換|編集|修正|分析|確認|管理|追加|整理|翻訳|添削|校正|要約|化)$")

def said_keyword(k,part):
    if k in part:return True
    m=ACTION_TAIL.search(k)
    if not m:return False
    stem=k[:m.start()]
    return len(stem)>=2 and stem in part and m.group(1) in part

def prepare(row):
    """Everything rank_open needs from one skill row; cacheable per row (see local_index)."""
    n,d=row["name"],row["description"]
    positive=re.split(r"(?i)\b(?:do not use|not intended for|avoid using)\b",d,maxsplit=1)[0]
    keywords=keyword_terms(row)
    # Keywords count only when the request says one (below); folding them into the
    # description's vocabulary added operations and tasks the skill never claimed.
    meta=features(n+" "+positive)
    return (features(n)[0],features(n)[2],meta,
            frozenset(k for k,v in SCOPE.items() if v.search(n+" "+positive[:160])),
            tuple(k for k in keywords if JP.search(k)),
            tuple((features(k)[1],features(k)[0]) for k in keywords if JP.search(k)))

def pack(entry):
    nwords,nconcepts,meta,scope,kw,kj=entry
    return [sorted(nwords),sorted(nconcepts),[sorted(x) for x in meta],sorted(scope),list(kw),[[sorted(g),sorted(w)] for g,w in kj]]

def unpack(raw):
    nwords,nconcepts,meta,scope,kw,kj=raw
    return (frozenset(nwords),frozenset(nconcepts),tuple(frozenset(x) for x in meta),frozenset(scope),tuple(kw),tuple((frozenset(g),frozenset(w)) for g,w in kj))

def catalog_counts(entries):
    counts=Counter()
    kcounts=Counter()
    for entry in entries:
        for group in entry[2]:counts.update(group)
        kcounts.update(entry[4])
    return counts,kcounts

def rank_open(parts, records, known_for, top=8, explanations=None, prepared=None):
    """Retrieve by content and reject weak, operation-mismatched, or tied candidates.
    prepared: optional {path: prepare(row)} computed ahead of time."""
    primary,covered=role_picks(parts,records,explanations)
    parts=[p for idx,p in enumerate(parts) if idx not in covered and not known_for(p)]
    if not parts:return primary
    prepared=prepared or {}
    catalog=[(row,*(prepared.get(row["path"]) or prepare(row))) for row in records]
    # Term frequencies over the whole catalogue; a cache may supply them (see local_index).
    counts,kcounts=getattr(prepared,"counts",None) or catalog_counts(c[1:] for c in catalog)
    size=len(catalog)
    if not size:return []
    def weight(k):return min(3.5,math.log(1+(size+.5)/(counts[k]+.5)))
    out=list(primary)
    for part in parts:
        ql,qj,qc=features(part)
        qscope={k for k,v in SCOPE.items() if v.search(part)}
        targets=qc-OPS
        operations=qc&OPS
        for target in (tuple(sorted(targets & {"video","image","audio"})) if len(targets & {"video","image","audio"})>1 else (None,)):
            pool=[]
            for row,nwords,nconcepts,(dl,dj,dc),scope,kw,kj in catalog:
                exact=(ql&dl)-set(ALIASES)
                sem=qc&dc
                hit=targets&dc
                if target is not None and target not in hit:continue
                name=exact&nwords
                format_only={x for x in exact if x in {"png","jpeg","gif","mp4","svg","csv","json","md"}}
                # A tool or skill whose existing inputs are missing is weaker than a general fit.
                required=re.search(r"(?i)\bfrom\s+([a-z][a-z0-9_-]+)\s+(?:projects?|workspaces?)\b",row["description"])
                jp=qj&dj
                # A task term alone is insufficient. Match the operation as well,
                # or have concrete repository/tool vocabulary / multiple rare words.
                # A Japanese keyword written for this skill at index time ("お詫びメール"),
                # said in the request, names the task itself, so it stands for the operation.
                # How much of each keyword the request says: all of it ("お詫びメール"), its
                # subject and action apart ("スライドを作成"), or most of its character n-grams
                # ("プロンプトを改善" for "プロンプト改善"). Only the best keyword counts.
                # A keyword's English words ("todo 書き直し") must be in the request as well.
                kfit=[(1. if said_keyword(k,part) else (len(g&qj)/len(g) if g and w<=ql else 0.),k) for k,(g,w) in zip(kw,kj)]
                said_kw=[k for c,k in kfit if c>=.6]
                kbest=max(((c*min(3.5,math.log(1+(size+.5)/(kcounts[k]+.5)))*min(1.,len(k)/4),k) for c,k in kfit if c>=.6),default=(0,None))[0]
                if operations and not operations&dc and not said_kw:
                    if len(name)<2:continue
                # One rare word shared by the request and the skill's name (Docker, Mermaid,
                # Kubernetes) is concrete enough when the request asks for work.
                rare_name=bool(operations) and any(weight(t)>=2.6 for t in name)
                if not ((hit and (sem&OPS or name or hit&TASKS)) or len(name)>=2 or len(exact)>=3 or len(jp)>=5 or rare_name
                        or said_kw):
                    continue
                score=(sum(weight(t) for t in exact)*1.1
                       +sum(weight(t) for t in name)*1.1
                       +sum(weight(t) for t in hit)*1.8
                       + 3.4*len(nconcepts&hit)
                       - .65*sum(weight(t) for t in format_only)
                       - (3 if required and required.group(1).lower() not in ql else 0)
                       - 2.7*len(scope-qscope)
                       +sum(weight(t) for t in sem&OPS)*1.15
                       +sum(weight(t) for t in jp)*.28
                       # The strongest keyword only: "リファクタ" and "リファクタリング" are one term.
                       +kbest*1.8
                       - .35*len((dc&OPS)-operations))
                # Specific tool requested: prefer its implementation over vague "editor" overlap.
                if operations and (operations&dc)!=operations and not said_kw:
                    score-=1.4*len(operations-dc)
                # Within a task family named by the skills themselves, prefer the general
                # one ("code-reviewer") to a qualified one ("receiving-code-review").
                # A named task the candidate never mentions is a weak fit, however much
                # incidental vocabulary it shares.
                if targets&TASKS and not hit&TASKS:
                    score-=5
                # A skill for another named task ("unslop-review" for review comments) is a
                # specialisation the request did not ask for.
                score-=2.5*len((nconcepts&TASKS)-qc)
                # The request and the skill naming the task with the same phrase
                # ("3P update") is stronger than a merely related one ("weekly updates").
                for key in hit&TASKS:
                    said=lambda text:{m.group().rstrip("s") for m in PATTERNS[key].finditer(text)}
                    if said(normalize(part))&said(normalize(row["name"]+" "+row["description"])):
                        score+=3
                family=nconcepts&hit&TASKS
                rest=normalize(row["name"])
                for key in family:rest=PATTERNS[key].sub(" ",rest)
                unasked=len({canonical(w) for w in LATIN.findall(rest)}-ql-GENERIC)
                if family:score-=.9*unasked
                pool.append((score,row,unasked))
            pool.sort(key=lambda item:(-item[0],item[1]["name"],item[1]["path"]))
            # Near-tied candidates whose names carry words the request never mentions
            # (odoo-docker-deployment, react-flow-node-ts) yield to the plain one (docker-expert).
            near=[item for item in pool if pool and pool[0][0]-item[0]<.8]
            if len(near)>1:
                least=min(item[2] for item in near)
                plain=[item for item in near if item[2]==least]
                if len(plain)==1 and all(item[2]>=least+1 for item in near if item is not plain[0]):
                    pool=[plain[0]]+[item for item in pool if item[0]<pool[0][0]-.8]
            pool=[(score,row) for score,row,*_ in pool]
            shortlist=pool[:top]
            tied=[]
            if shortlist and shortlist[0][0]>=5.25 and len(shortlist)>1 and shortlist[0][0]-shortlist[1][0]<.8:
                # Up to three equally strong members of the same named task family are all
                # offered; the agent chooses between them. Any other tie stays unresolved.
                fam=lambda r:features(r["name"])[2]&targets&TASKS
                top_fam=fam(shortlist[0][1])
                group=[r for sc,r in shortlist if shortlist[0][0]-sc<.8]
                if top_fam and len(group)<=3 and all(fam(r)==top_fam for r in group):
                    tied=group[1:]
            if shortlist and shortlist[0][0]>=5.25 and (len(shortlist)==1 or shortlist[0][0]-shortlist[1][0]>=.8 or tied):
                out.append(("open-search",shortlist[0][1]))
                out.extend(("open-search",r) for r in tied)
                if explanations is not None:
                    explanations.setdefault(shortlist[0][1]["path"], {
                        "operation":target or "general", "basis":"metadata_match",
                        "score":round(shortlist[0][0],3),
                        "runner_up":round(shortlist[1][0],3) if len(shortlist)>1 else None})
    return out
