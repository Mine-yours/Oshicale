import os
from flask import Flask

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'dev-secret-key')

from app import db  # noqa: E402  pylint: disable=wrong-import-position
from app import main  # noqa: E402  pylint: disable=wrong-import-position

db.create_table()