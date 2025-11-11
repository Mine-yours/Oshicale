# 推し活カレンダー同期

Gemini API で公式サイト等のテキストを解析し、抽出したイベントを Google カレンダーへ同期するためのフラス クアプリです。

## できること
- 推し（アーティストなど）の取得先URLを登録・削除
- Gemini（Google Generative AI）でイベント情報を抽出
- 取得結果をチェックしてから Google カレンダーへ同期
- 同期済みイベントを SQLite に保存して重複登録を防止

## セットアップ
```bash
python -m venv .venv
source .venv/bin/activate  # Windows は .venv\Scripts\activate
pip install -r requirements.txt
```

必須環境変数の例:
```bash
export SECRET_KEY="dev-secret"
export GOOGLE_CLIENT_SECRETS_FILE="/絶対パス/client_secret.json"
export GOOGLE_API_KEY="your-gemini-key"
```

初回アクセス時は `flask --app app run --debug` で起動し、トップページから Google アカウントと連携してください。取得先の登録→「通常取得」または「ブラウザ取得」でイベントをプレビュー→同期ボタンでカレンダー反映、という流れです。

## 注意事項
- `client_secret_*.json`、`token.json`、`database.db` は機密情報なのでGit管理から除外してください。
- Gemini API のレート制限に達した場合は、画面上に制限メッセージが表示されるので時間をおいて再試行してください。
