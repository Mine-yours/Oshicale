import os
from flask import Flask
from dotenv import load_dotenv

# .flaskenvファイルを読み込む（明示的に指定）
load_dotenv('.flaskenv')

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'dev-secret-key')

from app import db  # noqa: E402  pylint: disable=wrong-import-position
from app import main  # noqa: E402  pylint: disable=wrong-import-position
from app.notifications import init_mail  # noqa: E402
from app.scheduler import start_scheduler  # noqa: E402

db.create_table()
init_mail(app)

# スケジューラーを開始（デフォルトで有効、環境変数で無効化可能）
if os.environ.get("DISABLE_SCHEDULER", "false").lower() != "true":
    scheduler = start_scheduler()
    print("定期自動取得スケジューラーが有効になりました")
else:
    print("定期自動取得スケジューラーは無効です（DISABLE_SCHEDULER=true）")