---
name: skill-suggest
description: Use only when the user asks which skills to add, how to set up their skills (おすすめのスキル), or to check installed skills' descriptions (スキルの健康診断). Read-only; never installs or edits.
---
# スキルのおすすめ
このファイルと同じフォルダの `suggest.py` を使う。Windowsでは `python -I -B -X utf8`、macOS/Linuxでは `python3 -I -B -X utf8` で実行し、スクリプトのパスはこのSKILL.mdの場所から解決する。

## 使い方の振り返り（既定）
`suggest.py report` を実行する（期間は `--days 7-180`、既定60日）。Claude Code と Codex がこのPCに残している依頼履歴とセッション記録、Skill Router 自身の「合うスキルがなかった作業」の記録を、実行したときだけ読み取り専用で読む。保存も送信もしない。結果のJSONを次の4つに分けて、利用者の言語で短く伝える。

1. **設定のおすすめ**（`settings`）
   - `named_by_hand`: 毎回名前で呼んでいるスキル。`add_to_description` の語をそのSKILL.mdの description に足すと、名前なしで選ばれやすくなる。
   - `installed_but_not_selected`: 依頼に合うのに自動で選ばれなかった導入済みスキル。`action` があれば索引のキーワード作成（`router.py index refresh --keywords`）を案内する。
   - `claude_list_budget`: `~/.claude/skills` の説明がClaude Codeのスキル一覧の文字数枠を圧迫している。`unused` のスキルは `move_to`（`~/.agents/skills`）へ移しても Skill Router が見つける。
   - `duplicates`: 同じ名前が複数の場所にある。読み込まれるのは1つだけ。
   - `unused`: 期間中に使われず、依頼にも合わなかったスキルの件数。
   - `keywords_missing` / `router_off_for_folders` / `index_missing`: 書かれている `action` を案内する。
2. **足りていない作業**（`gaps`）: 合うスキルがない依頼によく出る語と日数。
3. **外部スキルのおすすめ**（`public`）: 2に合う公開スキル。名前・合う作業の日数・説明・リンク・配布元（`trust`: official / curated / community）と、`install` にある導入方法を示す。
4. **合うスキルがなかった作業**（`no_fit`）: 作業の終わりにどのスキルも使われなかった依頼の記録（Skill Router が語だけ残したもの）を、作業の種類（`themes`）ごとにまとめたもの。各 `theme` の `terms`（内容の語）・`days`・`requests` を示し、`public` は語の近い公開スキルの候補で、広めに拾っている。各候補の `description` を読み、主な用途がその作業に合うものだけを返す。合うものがなければ、`terms` を英語の短い言い換えと一緒に `find "<語> <英語>"` で1回探し直す。`installed_near` は近い導入済みスキルで、本当に合うなら説明の見直し（「スキルの健康診断」）を、合わなければ外部スキルを勧める。それでも見つからなければ `elsewhere` の案内（skills.sh）をする。記録を止めたい・消したいと言われたら `skill-router-settings/router.py record-gaps off` を案内する。

履歴が少ない（`starter` がある）ときは、そう伝えたうえで、`starter.public` の公式・厳選の未導入スキルを数件紹介し、`find`（作業を言う）や `topics`（分野の一覧）で探せることを案内する。

## 特定の作業に合うスキルを探す
「〜に使えるスキルは？」と聞かれたら `suggest.py find "作業の説明"` を実行する。作業の説明は利用者の言葉を短くまとめ、英語の短い言い換えを添える（例：`find "動画制作 video production"`。「スキル」という語は不要）。ファイルの中身や秘密情報は渡さない。結果は広めの候補なので、各候補の `description` を読み、主な用途が依頼の作業に合うものだけを返す。話題が近いだけ・語が重なるだけのものは外す。合うものがなければ、見つからなかったと伝える。`local`（導入済み）、`builtin_guides`（内蔵ガイド）、`public`（公開候補）を区別して返す。

## 分類から探す
「どんな分野のスキルがある？」には `suggest.py topics`（公開元が付けた分類と件数）を返す。分野や語で絞るときは `suggest.py browse "語"`（分類・名前・説明・検索語に一致する導入済みと公開スキル）。分類は配布元のもので、ないものは名前・説明・検索語で探す。

## 見つからないとき
結果に `elsewhere` があれば、ここの一覧では見つからなかったと伝え、より大きな公開登録先（skills.sh）を Find-skills（`npx skills find 英語の短い検索語`）で探せると案内する。`find_skills_installed` が真なら Find-skills スキルを使える。外部検索は利用者が望んだときだけ行い、送るのは利用者が了承した検索語だけで、履歴は送らない。

## スキルの健康診断
「スキルを診断して」「選ばれにくいスキルは？」「似ているスキルを整理したい」と頼まれたら、このフォルダの隣の `skill-router-settings/router.py doctor` を実行する（同じPython、同じ引数の付け方）。導入済みスキルの索引にある名前と説明だけを、ルーター本体と同じ採点で調べる。1件だけ詳しく見るときは `--skill <名前>`、件数は `--limit <件数>`。`error: index_missing` なら `router.py index refresh` を案内する。

結果の読み方：
- `hard_to_pick`: 依頼から選ばれにくいスキル。`issues` は `too_short`（説明が短い）、`few_specific_words`（そのスキルにしかない語が少なく、ほかと区別できない）、`generic_name`（名前がよくある語だけ）、`no_when_to_use`（いつ使うかが書かれていない）、`loses_own_examples`（索引時に作った「よくある頼まれ方」で、ほかのスキルに負ける）。`specific_words` はそのスキルを見分ける手がかりになっている語。
- `overlaps`: 役割が重なり、ルーターが見分けにくい組。`closeness` は片方の説明の1文目をもう片方がどれだけ同じくらい言い当てるか（1以上なら相手の方が高く採点される）、`similarity` は珍しい語の重なり、`shared_words` は共通の語、`only_<名前>` はそれぞれにしかない語。
- `without_japanese_words` と `action`: 日本語の語を持たないスキルの数。`action` があれば `router.py index refresh --keywords` を案内する。

直し案の出し方（上位5件ほど）：
1. 説明の直し案を、元の説明と同じ言語で1つずつ書く。最初の1文で「何をして何ができあがるか」を、`only_<名前>` や `specific_words` の語、またはそのSKILL.mdの本文から確かめた具体的な語で言う。次に「〜のときに使う」を書く。重なる組には「〜には <相手の名前> を使う」と使い分けを1文で添える。本文にない機能や用途は足さない。1024文字以内。
2. 案を見せる前に、1件ずつ `router.py doctor --skill <名前> --try-description "<案>"` で確かめる。`before` と `after` の `issues` と `overlaps` を比べ、良くなった案だけを、何が変わったか（例：重なりの `closeness` 1.14→0.62）と一緒に見せる。良くならなければ案を直して確かめ直す。
3. `similarity` が1に近い組は中身が同じ可能性が高い。どちらかを残す選択肢として伝えるだけにする。
4. SKILL.md は書き換えない。利用者が特定の案の反映をはっきり頼んだときだけ、そのファイルの `description` だけを書き換え、`router.py index refresh` を実行する。削除や移動はしない。

## 守ること
- 結果に依頼の本文を引用しない。語と件数だけを使う。
- 候補のインストール、本文の読込、実行、SKILL.md の書き換えは、利用者が別に頼んだときだけ行う。公開候補は安全性や互換性の保証ではないと添える。
- 履歴が見つからない（`note: no_history`）ときは、`find` で作業を指定する方法を案内する。
- シェルを使えない環境では、同梱の `catalog.json` の名前・説明・リンクだけで答え、利用傾向は確認できないと伝える。
