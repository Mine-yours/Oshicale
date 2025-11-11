import json
import os
from pathlib import Path
from typing import Optional

from flask import url_for
from google.auth.transport.requests import Request
from google.oauth2 import credentials as oauth_credentials
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build

SCOPES = ["https://www.googleapis.com/auth/calendar.events"]
BASE_DIR = Path(__file__).resolve().parent.parent
DEFAULT_TOKEN_FILE = BASE_DIR / "token.json"


class GoogleCalendarNotConfigured(Exception):
    """Googleカレンダー連携に必要な設定が不足している場合の例外"""


def _get_client_secrets_path() -> Path:
    env_path = os.environ.get("GOOGLE_CLIENT_SECRETS_FILE")
    if env_path:
        return Path(env_path)
    return BASE_DIR / "credentials" / "client_secret.json"


def _get_token_path() -> Path:
    env_path = os.environ.get("GOOGLE_TOKEN_FILE")
    if env_path:
        return Path(env_path)
    return DEFAULT_TOKEN_FILE


def build_flow(redirect_endpoint: str, state: Optional[str] = None) -> Flow:
    client_secrets = _get_client_secrets_path()
    if not client_secrets.exists():
        raise GoogleCalendarNotConfigured(
            f"Google APIのクライアントシークレットが見つかりません: {client_secrets}"
        )

    redirect_uri = url_for(redirect_endpoint, _external=True)
    return Flow.from_client_secrets_file(
        str(client_secrets),
        scopes=SCOPES,
        state=state,
        redirect_uri=redirect_uri,
    )


def load_credentials() -> oauth_credentials.Credentials:
    token_path = _get_token_path()
    if token_path.exists():
        creds = oauth_credentials.Credentials.from_authorized_user_file(
            str(token_path), scopes=SCOPES
        )
    else:
        raise GoogleCalendarNotConfigured("Googleカレンダーとの連携が未設定です。先に認証を行ってください。")

    if creds and creds.expired and creds.refresh_token:
        creds.refresh(Request())
        store_credentials(creds)

    if not creds or not creds.valid:
        raise GoogleCalendarNotConfigured("Googleカレンダーとの認証が有効ではありません。再認証してください。")

    return creds


def store_credentials(credentials: oauth_credentials.Credentials) -> None:
    token_path = _get_token_path()
    token_path.parent.mkdir(parents=True, exist_ok=True)
    token_data = {
        "token": credentials.token,
        "refresh_token": credentials.refresh_token,
        "token_uri": credentials.token_uri,
        "client_id": credentials.client_id,
        "client_secret": credentials.client_secret,
        "scopes": credentials.scopes,
    }
    token_path.write_text(json.dumps(token_data), encoding="utf-8")


def get_calendar_service():
    creds = load_credentials()
    return build("calendar", "v3", credentials=creds, cache_discovery=False)

