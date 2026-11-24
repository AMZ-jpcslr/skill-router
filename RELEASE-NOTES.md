# Skill Router 0.1.0-beta.7

Claude CodeとCodex Desktop（Windows）で、依頼に合う導入済みスキルや作業ガイドを、名前を指定せずに使えるようにするβ版です。自動選択は初期状態でオフです。使いたい作業フォルダでオンにできます。

- **標準では見えないスキルも使える**：Claude Codeが自分では読まない `~/.agents/skills` などのスキルからも、依頼に合うものを渡します。
- **Claude CodeとCodexの両方で使える**：同じプラグインを両方に導入でき、設定と索引も共通です。
- **2,000件規模でもスキル一覧の文字数枠を消費しない**：スキル一覧をまとめて渡さず、依頼ごとに合う候補だけを渡します。

## 主な変更

- Claude Codeに対応しました。
- 日本語の検索語をスキルごとに作れるようにしました（`router.py index refresh --keywords`）。
- 資料にない事実の追加、本文外の不要な付記、語数の超過を減らす書き方のルールを足しました。

## 導入

Claude Codeは `/plugin marketplace add AMZ-jpcslr/skill-router` と `/plugin install skill-router@skill-router-preview` で導入します。Codex Desktopは配布ZIPを展開してそのフォルダを残し、[README](README.md)の手順で導入してください。以前の版を入れている場合は、先に `codex plugin marketplace remove skill-router-preview` を実行します。導入後、検索語を1回作ってください。

この配布物は [MITライセンス](LICENSE) です。最適な候補が見つからない場合や、候補が見つかってもエージェントが使わない場合があります。スキルの自動インストールは行いません。配布ZIPのSHA-256は同梱のチェックサムファイルで照合できます。
