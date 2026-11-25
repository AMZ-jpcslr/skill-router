---
name: skill-router-settings
description: Manage Skill Router beta scope only when explicitly invoked. Show status and switch the current project, all local projects, or folderless chats on or off, and refresh the local installed-skill index.
disable-model-invocation: true
---
# Skill Router 設定
この SKILL.md と同じフォルダにある router.py を使う。実行には Windows では `python -I -B -X utf8`、macOS/Linux では `python3 -I -B -X utf8` を使う。スクリプトパスはこのスキルの場所から解決する。作業ディレクトリは現在のチャットの作業フォルダを維持する。
- 状態を確認: `router.py status`
- このプロジェクトだけを有効/無効: `router.py project on` / `router.py project off`。対象は現在のチャットの作業フォルダそのもの。親のGitルート・別フォルダは登録しない。別フォルダで有効にするなら、そのフォルダのチャットで操作する。結果の `current_project` と `current_project_enabled` を確認する。全プロジェクトがオンなら個別解除後も全体の対象になることを伝える。
- 全プロジェクトを有効/無効: `router.py all-projects on` / `router.py all-projects off`
- フォルダなしを有効/無効: `router.py folderless on` / `router.py folderless off`
- 返答の最後に使ったスキルを1行で出す表示（`Skill Router: …`）をオン/オフ: `router.py show-picks on` / `router.py show-picks off`。初期状態はオン。`router.py off` をしても、この設定は保たれる。
- すべてを無効: `router.py off`。個別登録も削除する。「両モードだけオフ」なら個別にオフにする。
設定の変更は利用者が明示したときだけ行い、曖昧なら状態を返すか確認する。設定の反映とフックの信頼は別。レビューを回避しない。スクリプトが失敗したら設定が変わったと言わない。確認は処理結果に従い、依頼やファイルをこのスクリプトに渡さない。

## 導入済みスキルの索引
- 索引は自動で更新される。自動選択がオンの作業フォルダで依頼を送ると、フックがスキル置き場の一覧だけを見て、追加・削除・置き換えのあったスキルを読み直す。索引がないとき、プラグイン更新で索引の形式が変わったとき、置き場が増えたとき、1日1回はすべてを確かめる（変更のないファイルは再読込しない）。
- 「索引を更新」「入っているスキルを登録して」など、利用者が明示したときは router.py index refresh を呼ぶ。自動更新を待たずにすぐ反映したいとき用で、通常は不要と伝えてよい。
- router.py index status で件数と追加の置き場を確認する。明示指定があれば router.py index add-root <絶対パス> / router.py index remove-root <絶対パス> で切り替える。権限を変更せず、利用者が指定したスキル保管フォルダだけを設定する。追加・削除後は index refresh を実行する。
- 日本語の依頼でも候補が見つかるよう、Claude Codeで利用者が「キーワードも作って」など明示したときは `router.py index refresh --keywords` を呼ぶ。新規・変更のあったスキルだけについて、スキル名と説明の抜粋を `claude -p`（既定はhaiku、ツールなし）に渡し、日本語のキーワードと別名を書かせて索引の隣に保存する。利用者の依頼文は送らない。件数が多いと数分かかり、利用者のClaude Codeの利用枠を使うことを先に伝える。`--limit <件数>` で一度に作る件数を絞れる。一度作ったあとは、新規・変更のあったスキルやプラグイン更新で作り直しが要る分を、フックが裏で（別プロセスで、依頼の処理を待たせずに）作る。止めたいときは環境変数 SKILL_ROUTER_NO_AUTO_KEYWORDS=1 を設定する。`router.py index status` の `auto` で最後の自動更新と裏での生成結果を確認できる。結果の `keywords_failed` が0でなければ、失敗した件数をそのまま伝える。`keywords_error` が返ったときはキーワードが1件も作られていないので、その理由（claude が見つからない場合は環境変数 CLAUDE_EXE で場所を指定できる）を伝える。
- 「スキルを診断して」と明示されたら `router.py doctor` を実行する。読み方と直し案の出し方は skill-suggest の SKILL.md の「スキルの健康診断」に従う。SKILL.md は書き換えない。
