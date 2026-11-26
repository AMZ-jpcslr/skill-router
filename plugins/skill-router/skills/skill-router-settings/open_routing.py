"""Experimental task framing and open retrieval. No network or model calls."""
from collections import Counter
from functools import lru_cache
import math
import re
import unicodedata

# The space Japanese typesetting puts between Latin letters and kana or kanji ("AI っぽい",
# "PR の説明文") is spacing, not a word break: for the named tasks "AI っぽい" says what
# "AIっぽい" says. (Words, files and URLs keep the space, which does end them.)
WAOU_SPACE=re.compile(r"(?<=[a-z0-9])[ \u3000]+(?=[\u3040-\u30ff\u4e00-\u9fff])|(?<=[\u3040-\u30ff\u4e00-\u9fff])[ \u3000]+(?=[a-z0-9])")

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

# A request by its form rather than its verb: a Japanese clause ending in a request form
# (〜て/〜で, 〜たい, 〜ほしい, お願い), or an English imperative sentence. Asking only to be
# told something (〜教えて, a question) is not work for a skill.
JP_REQUEST_END=re.compile(r"(?:(?<!教え)(?<!教えて)[てで](?:ください|下さい|ほしい|欲しい|もらえ(?:る|ます)か?|くれ(?:る|ない)?|みて)?|たい(?:です)?|(?:て|で)?ほしい(?:です)?|お願い(?:します|いたします)?|ように(?:して|したい))[?？!！。\s]*$")
INFO_ONLY=re.compile(r"(?:違い|仕方|やり方|理由|意味|方法|とは|何|なぜ|どう(?:して|やって|すれば))[^、,。]{0,12}(?:を|は|が)?(?:教えて|知りたい|説明して)")
EN_LEAD=re.compile(r"^(?:(?:please|pls|kindly)\s+|(?:can|could|would|will) you\s+(?:please\s+)?|i (?:want|need|would like) (?:you )?to\s+|help me(?: to)?\s+|let'?s\s+)")
EN_NOT_VERB=frozenset("""the a an this that these those it its i we you my our your their there here what why how when where who which
whose is are was were am do does did can could would should will has have had great thanks thank ok okay sure yes no nice
cool perfect hello hi hey good awesome sorry so and but also""".split())

# A question ("なんで失敗してるの？", "どっちがいい？") asks to be told, unless it asks for the
# work itself ("直してくれる？", "作ってもらえますか？").
JP_QUESTION=re.compile(r"(?:の|か|かな|って何|は|いい|どう|どっち|なに|何)[?？]?\s*$|[?？]\s*$")
JP_ASKS_WORK=re.compile(r"(?:て|で)(?:くれ(?:る|ない|ます)|もらえ(?:る|ない|ます)|いただけ(?:る|ます))か?[?？]?\s*$")

JP_PROHIBIT=re.compile(r"ないで(?:ください|下さい|ほしい|欲しい|おいて)?[?？!！。\s]*$|ないように(?:して(?:ください)?)?[?？!！。\s]*$")

def is_request(part):
    jp=bool(re.search(r"[\u3041-\u30ff\u3400-\u9fff]",part))
    if jp and JP_QUESTION.search(part) and not JP_ASKS_WORK.search(part):return False
    # "…は入れないで" limits the work; it is not work of its own.
    if jp and JP_PROHIBIT.search(part):return False
    if REQUEST.search(part):return True
    if JP_REQUEST_END.search(part) and not INFO_ONLY.search(part):return True
    if jp:return False
    t=part.strip()
    lead=EN_LEAD.match(t)
    body=t[lead.end():] if lead else t
    first=re.match(r"[a-z]+",body)
    if not first or len(body.split())<3:return False
    if t.endswith("?") and not lead:return False
    return bool(lead) or first.group() not in EN_NOT_VERB

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
            if (is_request(part) or video_operation(part) in {"video-explain","video-extract","video-extract-audio","video-extract-frames","video-frames-ocr"} or OUTPUT_LABEL.fullmatch(section) and OUTPUT_VERB.search(part)) and (not NOT_REQUEST.search(part) or video_operation(part)=="video-explain"):
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
# Latin words are bounded by lookarounds: Japanese characters count as \w, so "LinkedInに"
# has no \b after the name.
SPECIALIZATIONS={
    "search marketing": r"(?<![a-z0-9])seo(?![a-z0-9])|検索順位",
    "editorial article": r"(?<![a-z0-9])articles?(?![a-z0-9])|記事",
    "third-party provider": r"(?<![a-z0-9])gemini(?![a-z0-9])|nano banana",
    "chinese text": r"(?<![a-z0-9])chinese(?![a-z0-9])|中文|中国語",
    "social post": r"(?<![a-z0-9])linkedin(?![a-z0-9])|リンクトイン",
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
    tight=WAOU_SPACE.sub("",text)
    return (frozenset(latin),frozenset(jp),frozenset(k for k,p in PATTERNS.items() if p.search(text) or p.search(tight)))


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
    # A request that only mentions a video (a summary of one) is still matched by content.
    covered={i for i,op in video_by_part.items() if op!="video-unspecified"}
    for key,role_text,evidence,limit in active:
        span=set()
        if not key.startswith("video-"):
            first_start=whole.index(role_text)
            first=whole.count("。",0,first_start)
            last=whole.count("。",0,first_start+len(role_text))
            span=set(range(first,last+1))
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
            # A role that settles on no single skill leaves its clause to general retrieval.
            covered.update(span)
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
    return out[:20]

# Where a request points (a URL, an absolute path to save to) is not the work it asks for;
# its words ("github", "users") say nothing about which skill fits.
REFERENCE=re.compile(r"(?i)\b[a-z][a-z0-9+.-]*://\S+|(?<![\w.])(?:[a-z]:[\\/]|~[\\/]|/(?:users|home|mnt|tmp|var|opt)/)\S*")
# A file the request names says what format it is, not what the work is: "notes-en.md" -> "md".
FILE_NAME=re.compile(r"""[^\s"'「」『』()<>、。,]+\.(md|txt|py|tsx?|jsx|json|csv|tsv|xlsx|xls|docx|doc|pptx|pdf|ya?ml|toml|html?|css|java|go|rs|rb|php|sh|ps1|sql|log|png|jpe?g|gif|svg|mp4|mov|mp3|wav)(?![a-z0-9])""")

# The same, written in Latin letters only: the name a skill's text could also use.
LATIN_FILE=re.compile(r"(?<![a-z0-9_.-])[a-z0-9_][a-z0-9_.-]*\.(?:"+FILE_NAME.pattern.split(".(",1)[1].split(")(?!",1)[0]+r")(?![a-z0-9])")

def strip_references(text):
    return FILE_NAME.sub(" ",REFERENCE.sub(" ",normalize(text)))

def file_formats(text):
    return frozenset(m.group(1) for m in FILE_NAME.finditer(REFERENCE.sub(" ",normalize(text))))

# ---- General retrieval -------------------------------------------------------------
# A request is matched against each skill's own words: its name, the positive part of its
# description, and the keywords written for it at index time (Japanese terms for English
# skills). Terms are weighted by how rare they are in this catalogue, and Japanese words are
# also read through a bilingual vocabulary learned from the catalogue itself (skills whose
# keywords say "セキュリティ" tend to say "security"). No list of domains, products or skills
# is kept here; a new skill is routed by what it says about itself.

EN_STOP=frozenset("""a an the this that these those it its is are was were be been am to of in on at by for
from with as or and but if then than so not no do does did can could would should will shall may might
me my mine we our us you your he she they them their i what which who whom whose why how when where
there here all any some each every both few more most other such only own same too very just also
please help want need like make sure get got let lets using use via into onto out up down over under
again new one two three first last next per about after before because while once whether thing things""".split())
# Words skill names add to say what kind of skill it is, not what it works on.
ROLE_WORDS=frozenset("""expert pro patterns pattern best practices practice skill skills guide guidelines development
developer automation toolkit helper specialist engineer engineering architect official master mastery advanced
basics fundamentals workflow workflows assistant agent agents builder generator manager tool tools kit""".split())
WORD=re.compile(r"[a-z][a-z0-9+#]*")
DOTTED=re.compile(r"(?<![a-z0-9.])([a-z][a-z0-9]*)\.(js|ts)(?![a-z0-9])")
KATAKANA=re.compile(r"[ァ-ヿー]+")
JP_WORD_RUN=re.compile(r"[ァ-ヿー]+|[㐀-鿿]+")

@lru_cache(maxsize=50000)
def words(text):
    """Latin content words, inflections folded ("refactoring" -> "refactor")."""
    text=normalize(text)
    out={canonical(t) for t in WORD.findall(text) if len(t)>=2 and t not in EN_STOP}
    # A library written with its dot ("Three.js", "Node.js") is the word catalogues join ("threejs").
    out.update(a+b for a,b in DOTTED.findall(text))
    return frozenset(out)

@lru_cache(maxsize=65536)
def jp_words(text):
    """Japanese words without a dictionary: katakana runs, and kanji runs in two-character
    words ("新卒採用情報" -> 新卒/採用/情報); hiragana (particles, endings) separates them."""
    out=[]
    for run in JP_WORD_RUN.findall(normalize(text)):
        if KATAKANA.fullmatch(run):
            if len(run)>=2:out.append(run)
        elif len(run)>=4:
            cut=[run[i:i+2] for i in range(0,len(run)-len(run)%2,2)]
            if len(run)%2:cut[-1]+=run[-1]
            out.extend(cut)
        elif len(run)>=2:out.append(run)
    return frozenset(out)

def compact(text):
    return re.sub(r"\s+","",normalize(text))

LEAD_SENTENCE=re.compile(r"(?<=。)|(?<=[.!?])\s+(?=[A-Z])")

def prepare(row):
    """Everything the general retrieval needs from one skill row; cacheable per row."""
    n,d=row["name"],row["description"]
    positive=re.split(r"(?i)\b(?:do not use|not intended for|avoid using)\b",d,maxsplit=1)[0]
    keywords=keyword_terms(row)
    meta=features(n+" "+positive)
    name_words=frozenset(canonical(t) for t in normalize(n).split("-") if t)
    kw_words=frozenset().union(*(words(k) for k in keywords)) if keywords else frozenset()
    jp_kw=tuple(k for k in keywords if JP.search(k))
    # The named tasks a skill is specialised in: those its name says (changelog-automation),
    # or the one task its text names. A text naming several (a writing guide listing minutes,
    # PR descriptions and AI-sounding prose) covers them among others.
    told=meta[2]&TASKS
    special=(features(n.replace("-"," "))[2]&TASKS)|(told if len(told)==1 else frozenset())
    # Its scope, likewise, is what its name and first sentence say.
    lead=LEAD_SENTENCE.split(positive.strip(),maxsplit=1)[0][:160]
    # Of the description's features only its concepts are used here; its words are below.
    return (name_words, special, (frozenset(),frozenset(),meta[2]),
            frozenset(k for k,v in SCOPE.items() if v.search(n+" "+lead)),
            words(positive), kw_words, jp_kw, jp_words(positive).union(*(jp_words(k) for k in jp_kw)))

def pack(entry):
    nw,nc,meta,scope,dw,kw,jk,dg=entry
    return [sorted(nw),sorted(nc),[sorted(x) for x in meta],sorted(scope),sorted(dw),sorted(kw),list(jk),sorted(dg)]

def unpack(raw):
    nw,nc,meta,scope,dw,kw,jk,dg=raw
    return (frozenset(nw),frozenset(nc),tuple(frozenset(x) for x in meta),frozenset(scope),
            frozenset(dw),frozenset(kw),tuple(jk),frozenset(dg))

def catalog_counts(entries):
    """Document frequencies of Latin and Japanese words, and the bilingual vocabulary: for each
    Japanese word, the English words that occur in the same skills far more than by chance
    (cosine of their skill sets)."""
    wdf=Counter(); gdf=Counter(); co=Counter()
    for nw,nc,meta,scope,dw,kw,jk,dg in entries:
        latin=nw|dw|kw
        wdf.update(latin); gdf.update(dg); wdf.update("concept:"+c for c in meta[2])
        for j in dg:
            for e in latin:co[j,e]+=1
    lexicon={}
    for (j,e),c in co.items():
        if c<3 or e in ROLE_WORDS:continue
        cos=c/math.sqrt(gdf[j]*wdf[e])
        if cos>=.35:lexicon.setdefault(j,[]).append((e,round(cos,3)))
    for j in lexicon:lexicon[j]=sorted(lexicon[j],key=lambda x:-x[1])[:4]
    return wdf,gdf,lexicon

def postings(entries,counts):
    """term -> positions of the skills that have it: Latin words, Japanese words (and the
    catalogue words a katakana compound is made of), and named concepts."""
    gdf=counts[1]
    post={}
    def add(t,i):
        rows=post.setdefault(t,[])
        if not rows or rows[-1]!=i:rows.append(i)
    for i,(nw,nc,meta,scope,dw,kw,jk,dg) in enumerate(entries):
        for t in sorted(nw|dw|kw|dg):add(t,i)
        for c in sorted(meta[2]):add("concept:"+c,i)
        for w in sorted(dg):
            if len(w)>=5 and KATAKANA.fullmatch(w):
                for k in range(2,len(w)-1):
                    if gdf.get(w[:k]) and gdf.get(w[k:]):add(w[:k],i);add(w[k:],i)
    return post

ACTION_WORD=re.compile(r"(?:作成|生成|作り|作る|開発|設計|変換|編集|修正|分析|確認|管理|追加|整理|翻訳|添削|校正|要約|改善|最適化|検証|実装|構築|対応|調査|比較|削除|更新|自動化|化)$")

class Query:
    """One work clause plus the sentences around it that set its subject."""
    def __init__(self,part,context,counts):
        wdf,gdf,lexicon=counts
        self.part=strip_references(part)
        self.context=strip_references(context)
        whole=normalize(self.part+" "+self.context)
        self.text=whole
        self.compact=compact(whole)
        self.words=words(self.part)
        # A named file's format ("xlsx" of "経費.xlsx") is weak context, never a skill's name.
        self.context_words=(words(self.context)|file_formats(part+" "+context))-self.words
        self.jp=self.said_jp(whole,gdf)
        self.jp_part=self.said_jp(normalize(self.part),gdf)
        # Japanese words read in English through the catalogue's own vocabulary.
        self.translated={}
        for j in self.jp:
            for e,cos in lexicon.get(j,()):
                if e in self.words:continue
                f=cos*(.8 if j in self.jp_part else .4)
                if f>self.translated.get(e,(0,))[0]:self.translated[e]=(f,j)
        ql,qj,qc=features(whole)
        self.concepts=qc
        self.targets=qc-OPS
        self.operations=features(self.part)[2]&OPS
        self.scope={k for k,v in SCOPE.items() if v.search(whole)}
        self.runs=frozenset(r for r in KATAKANA.findall(whole) if len(r)>=3)
        # A file named whole ("CLAUDE.md", "docker-compose.yml") that a skill's text also names
        # identifies it; a file of the user's own ("経費.xlsx") is named by no skill.
        self.files=frozenset(m.group(0) for m in LATIN_FILE.finditer(REFERENCE.sub(" ",normalize(part)))
                             if len(m.group(0))>=6)
        self.file_df={}

    @staticmethod
    def said_jp(text,vocab):
        """Catalogue words the text says. A kanji word may sit inside a longer run; a katakana
        word must be a whole katakana run, or one with another catalogue word attached
        ("セキュリティ" in "セキュリティチェック", not "ログ" in "ログイン")."""
        out=set()
        for run in JP_WORD_RUN.findall(text):
            if KATAKANA.fullmatch(run):
                if run in vocab:out.add(run)
                for i in range(2,len(run)-1):
                    a,b=run[:i],run[i:]
                    if a in vocab and b in vocab:out.update((a,b))
            else:
                for n in range(2,min(6,len(run))+1):
                    out.update(x for x in (run[i:i+n] for i in range(len(run)-n+1)) if x in vocab)
        return frozenset(out)

def score_skill(q,entry,wdf,gdf,size,row):
    """Score and the distinct pieces of evidence (words, keywords, named tasks) behind it."""
    nw,nc,(dl,dj,dc),scope,dw,kw,jk,dg=entry
    def idf(t,df):return math.log(1+(size+.5)/(df[t]+.5))
    score=0.;evidence=set();strong=False
    shared=set(q.jp&dg) if dg else set()
    head=next((canonical(x) for x in normalize(row["name"]).split("-") if x),"")
    # A Japanese word and its English reading are one term: count the stronger match once.
    terms=[(t,1.) for t in q.words]+[(t,.5) for t in q.context_words]+[
        (e,f) for e,(f,j) in q.translated.items() if j not in shared]
    for t,factor in terms:
        in_name=t in nw; in_text=t in dw or t in kw
        if not (in_name or in_text):continue
        w=idf(t,wdf)
        score+=factor*w*(1.6*in_name+1.0*in_text)
        evidence.add(t)
        # A rare word the skill is named after (docker, mermaid, terraform) identifies it. A word
        # later in the name is part of a compound ("line" of subject-line-psychologist) and is
        # not, alone, the thing the skill is about.
        if in_name and t==head and factor>=.8 and w>=3.5 and t not in ROLE_WORDS:strong=True
    if dg:
        # A katakana compound of the skill ("セキュリティレビュー") is matched by a word of it the
        # request says ("セキュリティ") when the rest is a catalogue word too.
        for w in dg-shared:
            if len(w)>=5 and KATAKANA.fullmatch(w):
                for r in q.runs:
                    if r!=w and (w.startswith(r) and gdf.get(w[len(r):]) or w.endswith(r) and gdf.get(w[:-len(r)])):
                        shared.add(r)
        if shared:
            # Skills with many words share more by chance (BM25 length normalisation).
            g=sum(idf(x,gdf)*(1. if x in q.jp_part else .5) for x in shared)
            score+=JP_WEIGHT*g/(.25+.75*len(dg)/JP_DOC_AVG)
            evidence.update("jp:"+x for x in shared if idf(x,gdf)>=2.5)
    # A keyword said whole, with what it acts on ("テーブル設計"), names the task itself.
    best=0.
    for k in jk:
        kc=compact(k)
        if kc in q.compact and any(not ACTION_WORD.search(w) for w in jp_words(k)):
            best=max(best,sum(idf(x,gdf) for x in jp_words(k))/max(1,len(jp_words(k)))*min(1.,len(kc)/4))
    if best:
        score+=PHRASE_WEIGHT*best
        evidence.add("phrase")
        if best>=2.:strong=True
    # A request written for the skill at index time that shares several words with this one
    # ("取引先へのお詫びメールを書いて" for "納期遅れを取引先に詫びるメールを"): the closest one counts.
    if EXAMPLE_WEIGHT:
        near=0.
        for k in jk:
            same=q.jp_part&jp_words(k)
            if len(same)>=2:near=max(near,sum(idf(x,gdf) for x in same))
        if near:
            score+=EXAMPLE_WEIGHT*near
            evidence.add("example")
    # A task or medium named the same way by the request and the skill (commit, video), as
    # rare as it is in this catalogue.
    hit=q.targets&dc
    for key in hit:
        w=idf("concept:"+key,wdf)
        score+=w*(1.6 if key in TASKS else .6)
        evidence.add("concept:"+key)
        if key in TASKS:strong=True
    score+=.8*len(q.operations&dc)
    for f in q.files:
        # Only when the request works on it: "README.mdを開いて見せて" asks for no skill.
        if q.operations and q.file_df.get(f) and f in named_files(row):
            score+=1.6*idf(f,q.file_df)
            evidence.add("file:"+f);strong=True
    # A named task the candidate never mentions; a specialisation the request did not ask for.
    if q.targets&TASKS and not hit&TASKS:score-=3.
    score-=2.5*len(nc-q.concepts)
    score-=2.7*len(scope-q.scope)
    required=re.search(r"(?i)\bfrom\s+([a-z][a-z0-9_-]+)\s+(?:projects?|workspaces?)\b",row["description"])
    if required and required.group(1).lower() not in q.words|q.context_words:score-=3.
    # Name words the request never said (odoo-docker-deployment vs docker-expert).
    said=q.words|q.context_words|set(q.translated)
    rest=normalize(row["name"])
    for key in hit:rest=PATTERNS[key].sub(" ",rest)
    rest={canonical(t) for t in re.split(r"[-\s]+",rest) if t}
    score-=.35*len({t for t in rest if t not in ROLE_WORDS and len(t)>=3}-said)
    return score,evidence,strong

THRESHOLD=7.0
EXAMPLE_WEIGHT=.6
STRONG_THRESHOLD=5.0
JP_DOC_AVG=8
JP_WEIGHT=1.2
PHRASE_WEIGHT=1.5
SHORTLIST=5
# A candidate this far ahead of the next is handed alone; otherwise the close ones are listed
# and the agent, which reads the request, chooses among them by their descriptions.
LEAD=.25

def named_files(row):
    text=normalize(row["name"]+" "+row.get("description","")+" "+" ".join(map(str,row.get("keywords") or ())))
    return {m.group(0) for m in LATIN_FILE.finditer(text)}

def rank_general(q,catalog,counts,size=None):
    """[(score,row,alone)]: one clear candidate, or a shortlist of plausible ones."""
    wdf,gdf,_=counts
    size=size or len(catalog)
    if q.files:
        from collections import Counter
        q.file_df=Counter(f for row,_ in catalog for f in named_files(row) & q.files)
    pool=[]
    for row,entry in catalog:
        s,ev,strong=score_skill(q,entry,wdf,gdf,size,row)
        # One identifying piece of evidence (a rare name word, a named task, a keyword said
        # whole) is enough at a lower score; otherwise two pieces and a higher score.
        if not (strong and s>=STRONG_THRESHOLD or len(ev)>=2 and s>=THRESHOLD):continue
        pool.append((s,row,bool(entry[3]-q.scope)))
    pool.sort(key=lambda x:(-x[0],x[1]["name"],x[1]["path"]))
    # A skill specialised for something the request never names (Chinese text, SEO) is listed
    # beside a general one only when it scores nearly as well.
    if pool:
        top=pool[0][0]
        pool=[x for x in pool if not x[2] or x[0]>=.85*top]
    pool=[(s,row) for s,row,_ in pool]
    if not pool:return []
    best=pool[0][0]
    if len(pool)==1 or best-pool[1][0]>=LEAD*best:
        return [(best,pool[0][1],True)]
    return [(s,row,False) for s,row in pool[:int(SHORTLIST)] if s>=best*.5]

CONTEXT_SPLIT=re.compile(r"[。\n；;]|(?<=[a-z0-9)\]\"'])[.?!]\s+(?=[a-z])")

def context_of(prompt,parts):
    """Non-request sentences on the same line as a work clause: "Djangoの一覧ページが遅い。"
    names what "N+1を見て改善して" is about. Quoted text, code, negations and examples are left out."""
    clean=FENCE.sub(" ",normalize(prompt))
    out=[]
    for line in clean.splitlines():
        line=QUOTE.sub(" ",line)
        sents=[x.strip(" \t、・-*") for x in CONTEXT_SPLIT.split(line)]
        if not any(any(p in s or s in p for p in parts) for s in sents if s):continue
        for s in sents:
            if not s or any(s in p or p in s for p in parts):continue
            if NOT_REQUEST.search(s) or EXAMPLE.match(s) or SOURCE.search(s) or len(s)>160:continue
            out.append(s)
    return " ".join(out)

def posted(q,post):
    """Positions of the skills sharing a word with the query; only those can score."""
    terms=q.words|q.context_words|set(q.translated)|q.jp|q.runs|{"concept:"+t for t in q.targets}|{
        w for f in q.files for w in words(f.rsplit(".",1)[0])}
    return sorted({i for t in terms for i in post.get(t,())})

def rank_open(parts, records, known_for, top=8, explanations=None, prepared=None, prompt=None):
    """Retrieve by content and reject weak candidates.
    prepared: optional {path: prepare(row)} computed ahead of time."""
    primary,covered=role_picks(parts,records,explanations)
    context=context_of(prompt,parts) if prompt else ""
    parts=[p for idx,p in enumerate(parts) if idx not in covered and not known_for(p)]
    if not parts:return primary
    if prepared is None:prepared={}
    if not records:return primary
    post=getattr(prepared,"post",None)
    order=getattr(prepared,"order",None)
    if post is not None and order is not None and order==[r["path"] for r in records]:
        counts=prepared.counts
        full=None
    else:
        full=[(row,prepared.get(row["path"]) or prepare(row)) for row in records]
        counts=getattr(prepared,"counts",None) or catalog_counts(c[1] for c in full)
    out=list(primary)
    listed=[]
    for part in parts:
        q=Query(part,context,counts)
        if full is None:
            hits=posted(q,post)
            catalog=[(records[i],prepared[records[i]["path"]]) for i in hits]
        else:
            catalog=full
        picks=rank_general(q,catalog,counts,len(records))
        for i,(s,row,alone) in enumerate(picks):
            if alone:out.append(("open-search",row))
            else:listed.append((s,row))
            if explanations is not None:
                explanations.setdefault(row["path"],{
                    "operation":"general","basis":"metadata_match","score":round(s,3),
                    "runner_up":round(picks[1][0],3) if len(picks)>1 and i==0 else None})
    # Shortlists of several clauses are one list, strongest first, as long as one clause's.
    listed.sort(key=lambda x:(-x[0],x[1]["name"],x[1]["path"]))
    out.extend(("open-shortlist",row) for s,row in listed[:int(SHORTLIST)])
    return out
