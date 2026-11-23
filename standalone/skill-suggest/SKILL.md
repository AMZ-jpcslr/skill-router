---
name: skill-suggest
description: Suggest relevant installed or curated public skills on explicit request. Optional manual category history; never install, apply, or run a candidate.
---
# スキルのおすすめ
このファイルと同じフォルダの `suggest.py` を使う。Windowsでは `python -I -B -X utf8`、macOS/Linuxでは `python3 -I -B -X utf8` で実行し、スクリプトのパスはこのSKILL.mdの場所から解決する。フックは不要。
- `suggest.py tasks` で対応する作業分類を確認する。
- 今回の作業: `suggest.py recommend --task CATEGORY --json`。用途が複数なら工程ごとに調べる。公開候補も求められたら `--include-public` を使う。
- 過去の傾向を見たいと言われた場合だけ、明示した履歴ファイルで `suggest.py --history HISTORY recommend --json`。履歴への記録を明示的に頼まれた場合は `suggest.py --history HISTORY record --task CATEGORY`。HISTORY は利用者のホームの `.skill-router-beta-suggestions.sqlite3` とし、試作の履歴を自動で取り込まない。
- ローカルと公開候補を区別し、候補が合う理由を返す。候補のインストール、本文読込、実行はしない。分類名以外の依頼本文や添付をスクリプトに渡さない。
- シェルやローカルの置き場にアクセスできない環境では、同梱の `catalog.json` の作業分類と公開リンクだけを使う。ローカル導入状況や利用傾向は確認できないと伝える。公開候補は安全性や互換性の保証ではない。
