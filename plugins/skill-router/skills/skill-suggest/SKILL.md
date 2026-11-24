---
name: skill-suggest
description: Use only when the user asks which skills to add or how to set up their skills (おすすめのスキル). Reviews local usage read-only; never installs.
---
# スキルのおすすめ
このファイルと同じフォルダの `suggest.py` を使う。Windowsでは `python -I -B -X utf8`、macOS/Linuxでは `python3 -I -B -X utf8` で実行し、スクリプトのパスはこのSKILL.mdの場所から解決する。

## 使い方の振り返り（既定）
`suggest.py report` を実行する（期間は `--days 7-180`、既定60日）。Claude Code と Codex がこのPCに残している依頼履歴とセッション記録を、実行したときだけ読み取り専用で読む。保存も送信もしない。結果のJSONを次の3つに分けて、利用者の言語で短く伝える。

1. **設定のおすすめ**（`settings`）
   - `named_by_hand`: 毎回名前で呼んでいるスキル。`add_to_description` の語をそのSKILL.mdの description に足すと、名前なしで選ばれやすくなる。
   - `installed_but_not_selected`: 依頼に合うのに自動で選ばれなかった導入済みスキル。`action` があれば索引のキーワード作成（`router.py index refresh --keywords`）を案内する。
   - `claude_list_budget`: `~/.claude/skills` の説明がClaude Codeのスキル一覧の文字数枠を圧迫している。`unused` のスキルは `move_to`（`~/.agents/skills`）へ移しても Skill Router が見つける。
   - `duplicates`: 同じ名前が複数の場所にある。読み込まれるのは1つだけ。
   - `unused`: 期間中に使われず、依頼にも合わなかったスキルの件数。
   - `keywords_missing` / `router_off_for_folders` / `index_missing`: 書かれている `action` を案内する。
2. **足りていない作業**（`gaps`）: 合うスキルがない依頼によく出る語と日数。
3. **外部スキルのおすすめ**（`public`）: 2に合う公開スキル。名前・合う作業の日数・説明・リンク・配布元（`trust`: official / curated / community）と、`install` にある導入方法を示す。

## 特定の作業に合うスキルを探す
「〜に使えるスキルは？」と聞かれたら `suggest.py find "作業の説明"` を実行する。作業の説明は利用者の言葉を短くまとめたものだけにし、ファイルの中身や秘密情報は渡さない。`local`（導入済み）、`builtin_guides`（内蔵ガイド）、`public`（公開候補）を区別して返す。

## 分類から探す
「どんな分野のスキルがある？」には `suggest.py topics`（公開元が付けた分類と件数）を返す。分野や語で絞るときは `suggest.py browse "語"`（分類・名前・説明・検索語に一致する導入済みと公開スキル）。分類は配布元のもので、ないものは名前・説明・検索語で探す。

## 見つからないとき
結果に `elsewhere` があれば、ここの一覧では見つからなかったと伝え、より大きな公開登録先（skills.sh）を Find-skills（`npx skills find 英語の短い検索語`）で探せると案内する。`find_skills_installed` が真なら Find-skills スキルを使える。外部検索は利用者が望んだときだけ行い、送るのは利用者が了承した検索語だけで、履歴は送らない。

## 守ること
- 結果に依頼の本文を引用しない。語と件数だけを使う。
- 候補のインストール、本文の読込、実行、SKILL.md の書き換えは、利用者が別に頼んだときだけ行う。公開候補は安全性や互換性の保証ではないと添える。
- 履歴が見つからない（`note: no_history`）ときは、`find` で作業を指定する方法を案内する。
- シェルを使えない環境では、同梱の `catalog.json` の名前・説明・リンクだけで答え、利用傾向は確認できないと伝える。
