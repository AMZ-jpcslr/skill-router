# Skill Router

**導入済みスキルを勝手に使ってくれる。**

Skill Routerは、依頼に合うスキルの参照先と作業ガイドを自動で渡す、AIエージェント向けプラグインです。

![Skill Routerの紹介画像](assets/readme-hero-light.png)

- **標準では見えないスキルも使える**：Claude Codeが自分では読まない `~/.agents/skills` などのスキルからも、依頼に合うものを渡します。
- **Claude CodeとCodexの両方で使える**：同じプラグインを両方に導入でき、設定と索引も共通です。
- **2,000件規模でもスキル一覧の文字数枠を消費しない**：スキル一覧をまとめて渡さず、依頼ごとに合う候補だけを渡します。

**β.8：Claude Code・Codex Desktop（Windows）**

[β.8のリリースページ](https://github.com/AMZ-jpcslr/skill-router/releases/tag/v0.1.0-beta.8) · [紹介動画（19秒・MP4）](assets/skill-router-demo-light.mp4)

## 1. 導入する

PATHに登録したPython 3.10以上が必要です。PowerShellで `python --version` を実行すると、バージョンを確認できます。`python3` が使えない環境（WindowsのMicrosoft Storeの代替コマンドなど）では `python` を使います。

### Claude Code

1. Claude Codeのコマンド（`claude`）がまだ使えない場合は、PowerShellで導入します。

   ```powershell
   irm https://claude.ai/install.ps1 | iex
   ```

   導入先の `%USERPROFILE%\.local\bin` がPATHにないと、`claude` が見つかりません。次の1行でPATHに加え、PowerShellを開き直します。

   ```powershell
   [Environment]::SetEnvironmentVariable("Path", [Environment]::GetEnvironmentVariable("Path", "User") + ";$env:USERPROFILE\.local\bin", "User")
   ```

2. Claude Codeのチャットで、次の2行を実行します。

   ```text
   /plugin marketplace add AMZ-jpcslr/skill-router
   /plugin install skill-router@skill-router-preview
   ```

設定は `/skill-router:skill-router-settings`、おすすめは `/skill-router:skill-suggest`（または「おすすめのスキルを教えて」）で呼び出します。Claudeデスクトップアプリでは「Code」タブのローカルセッションで使えます。Coworkは `~/.claude` のプラグインを読まないため、別途「カスタマイズ > プラグイン > マーケットプレイスを追加」で `AMZ-jpcslr/skill-router` を追加します。`~/.claude/skills` も索引の対象です。

### Codex Desktop

1. [プラグイン管理に対応したCodex CLI](https://developers.openai.com/codex/cli)がまだない場合は、Node.jsを入れたうえで導入します。

   ```powershell
   npm install -g @openai/codex
   ```

2. [リリースページ](https://github.com/AMZ-jpcslr/skill-router/releases/tag/v0.1.0-beta.8)から配布ZIPを取得し、展開します。
3. 展開したフォルダをPowerShellで開き、次を実行します。以前の版を入れていた場合は、先に1行目で古い配布元を外します。

   ```powershell
   codex plugin marketplace remove skill-router-preview
   codex plugin marketplace add .
   codex plugin add skill-router@skill-router-preview
   ```

展開先はプラグインの配布元として使うため、導入後も残しておきます。導入後はCodex Desktopを再起動します。

### 検索語を作る（初回に1回）

日本語の依頼でも合うスキルを見つけやすくするため、スキルごとの検索語をClaude（haiku）で作ります。送るのはスキル名と説明の一部だけで、依頼の内容は送りません。数分かかり、少し利用料がかかります。Claude Codeのコマンド（`claude`）が必要です。

```powershell
$r = (Get-ChildItem "$env:USERPROFILE\.claude\plugins", "$env:USERPROFILE\.codex\plugins" -Recurse -Filter router.py -ErrorAction SilentlyContinue | Where-Object FullName -like "*skill-router-settings*" | Sort-Object LastWriteTime -Descending | Select-Object -First 1).FullName
python $r index refresh --keywords
```

設定・索引・検索語はClaude CodeとCodexで共通（`~/.skill-router-beta/`）なので、1回で両方に効きます。スキルを追加・変更したら、もう一度実行します。

## 2. 初回セットアップ

1. Codex Desktopで使いたい作業フォルダを開き、新規チャットを始めます。
2. **Review hooks** が表示されたら、[同梱フック](https://learn.chatgpt.com/docs/hooks#plugin-bundled-hooks)の `UserPromptSubmit` の内容を確認し、使う場合は承認します。
3. **Skill Router 設定**（`$skill-router-settings`）を選び、次のように頼みます。

   > この作業フォルダだけ自動選択をオンにして、導入済みスキルの索引を更新して。

## 3. 使ってみる

スキル名を指定せず、作業を頼みます。まずは次の依頼で試せます。

> READMEの説明文を初めて読む人にも自然な日本語に直して。

Skill RouterはREADME用のガイドを渡し、導入済みスキルに推敲向けの候補があれば参照先を添えます。どの候補を使うかはAIエージェントが作業内容に合わせて判断します。採用・参考・見送りの内訳を返答で見たいときは、依頼の末尾に「スキル判断を表示」と1行追加します。

## 4. 設定を変える

設定は **Skill Router 設定**（`$skill-router-settings`）で変更します。初期状態はオフです。

| 頼み方 | 設定 |
| --- | --- |
| この作業フォルダだけオン／オフ | 開いているフォルダを個別に設定。親・子・兄弟フォルダは別の作業場所として扱います |
| 全プロジェクトをオン／オフ | フォルダを開いているチャット全体を設定。オンの間は個別設定より優先します |
| フォルダなしのチャットをオン／オフ | フォルダなしと判定したチャットを設定 |
| 状態を教えて／全部オフ | 現在の設定を表示／すべての自動選択をオフにして個別登録を解除 |
| 索引を更新して | 導入済みスキルの追加・変更・削除を反映 |

## 5. 機能

- **自動選択**：依頼の目的と作業内容に合う候補を探します。例えば、動画制作と既存動画からの文字抽出は別の作業として扱います。手順を聞いただけなら説明が中心になり、制作向け候補の追加は見送ります。
- **内蔵ガイド**：README編集、原因調査、回帰テスト、応募文、Wordの変更履歴に短い作業ガイドが付属します。
- **スキルのおすすめ**：**Skill Suggest**（Claude Codeは `/skill-router:skill-suggest`、Codexは `$skill-suggest`）を呼び出すか、「おすすめのスキルを教えて」と頼むと、普段の依頼の傾向から、スキル設定の見直し（毎回名前で呼んでいるスキルの説明に足す語、使われていないスキル、重複、Claude Codeの一覧枠を圧迫しているスキルの移し先）と、合うスキルがない作業に向く公開スキルを提案します。「〜に使えるスキルは？」と聞けば、その作業に合う導入済み・公開スキルを探します。配布元の分類から分野ごとに見ることもでき、同梱の一覧で見つからないときは Find-skills（skills.sh）での検索を案内します。
- **ローカル索引**：`~/.agents/skills` と `~/.codex/skills` から、スキル名・説明・ファイルの場所を登録します。別の保管フォルダは設定から追加できます。索引と設定の保存先は `~/.skill-router-beta/` です。

おすすめの材料は、Claude CodeとCodexがもともとPCに残している依頼履歴とセッション記録（既定で直近60日）です。Skill Suggestを呼び出したときだけ読み取り専用で読み、新たな記録・保存・送信はしません。結果には依頼の本文を出さず、語と件数だけを使います。公開スキルは同梱の一覧（名前・説明・リンクのみ）から選び、インストールはしません。外部スキルは各配布元から入手し、各ライセンスに従って利用してください。

[MITライセンス](LICENSE) · [変更履歴](CHANGELOG.md)
