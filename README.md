# Skill Router

**導入済みスキルを勝手に使ってくれる。**

Skill Routerは、依頼に合うスキルの参照先と作業ガイドを自動で渡す、AIエージェント向けプラグインです。

![Skill Routerの紹介画像](assets/readme-hero-light.png)

**公開版：Codex Desktop（Windows）β.6** ｜ **開発中：Claude Code版**

[β.6のリリースページ](https://github.com/AMZ-jpcslr/skill-router/releases/tag/v0.1.0-beta.6) · [紹介動画（19秒・MP4）](assets/skill-router-demo-light.mp4)

## 1. 導入する

Codex Desktop、[プラグイン管理に対応したCodex CLI](https://developers.openai.com/codex/cli)、PATHに登録したPython 3.10以上を用意します。PowerShellで `python --version` を実行すると、バージョンを確認できます。

1. [リリースページ](https://github.com/AMZ-jpcslr/skill-router/releases/tag/v0.1.0-beta.6)から配布ZIPを取得し、展開します。
2. 展開したフォルダをPowerShellで開き、次の2行を実行します。

   ```powershell
   codex plugin marketplace add .
   codex plugin add skill-router@skill-router-preview
   ```

展開先はプラグインの配布元として使うため、導入後も残しておきます。

### Claude Code（開発中のプレビュー）

Claude Codeでは、次の2行で導入できます。macOS・Linuxでは `python3`、Windowsでは `python3` として呼べるPython 3.10以上が必要です（Windowsでの通し確認は未了です）。

```text
/plugin marketplace add AMZ-jpcslr/skill-router
/plugin install skill-router@skill-router-preview
```

設定は `/skill-router:skill-router-settings`、おすすめは `/skill-router:skill-suggest` で呼び出します。設定と索引はCodex版と共通（`~/.skill-router-beta/`）で、`~/.claude/skills` も索引の対象です。

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
- **スキルのおすすめ**：**Skill Suggest**（`$skill-suggest`）を呼び出すと、用途に合う導入済みスキルを探せます。公開候補も見たいときは、その旨を添えます。候補の紹介は[単体版](standalone/skill-suggest/)でも使えます。
- **ローカル索引**：`~/.agents/skills` と `~/.codex/skills` から、スキル名・説明・ファイルの場所を登録します。別の保管フォルダは設定から追加できます。索引と設定の保存先は `~/.skill-router-beta/` です。

作業傾向の記録は任意です。記録を依頼したときに作業分類・日付・指定した利用状況を保存します。外部スキルは各配布元から入手し、各ライセンスに従って利用してください。

[MITライセンス](LICENSE) · [変更履歴](CHANGELOG.md)
