import json
import os
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from urllib.parse import urlparse

import google.generativeai as genai
import requests
from bs4 import BeautifulSoup
from google.api_core import exceptions as google_exceptions
from google.generativeai import types as genai_types

DEFAULT_DURATION = timedelta(hours=2)
DEFAULT_START_HOUR = int(os.environ.get("EVENT_DEFAULT_START_HOUR", "18"))
REQUEST_TIMEOUT = int(os.environ.get("EVENT_SOURCE_TIMEOUT", "15"))
USER_AGENT = os.environ.get(
    "EVENT_SOURCE_USER_AGENT",
    "OshicaleBot/0.1 (+https://github.com/oshicale)",
)
MAX_TEXT_LENGTH = int(os.environ.get("EVENT_SOURCE_MAX_TEXT", "15000"))
DEFAULT_DEBUG_DIR = Path(__file__).resolve().parent / "static" / "output"


@dataclass
class Event:
    source_id: str
    title: str
    start: datetime
    end: datetime
    location: Optional[str] = None
    url: Optional[str] = None
    description: Optional[str] = None


class EventSourceError(Exception):
    """イベント情報の取得に失敗した場合の例外"""


def _get_timezone() -> timezone:
    try:
        offset_hours = int(os.environ.get("EVENT_TIMEZONE_OFFSET", "9"))
    except ValueError:
        offset_hours = 9
    return timezone(timedelta(hours=offset_hours))


def _ensure_timezone(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=_get_timezone())
    return value


def _parse_iso_datetime(value: str, fallback_date: Optional[datetime] = None) -> Optional[datetime]:
    if not value:
        return None

    cleaned = value.strip()
    if cleaned.endswith("Z"):
        cleaned = cleaned[:-1] + "+00:00"

    try:
        parsed = datetime.fromisoformat(cleaned)
        return _ensure_timezone(parsed)
    except ValueError:
        pass

    if fallback_date is not None:
        try:
            parsed_time = datetime.strptime(cleaned, "%H:%M")
            combined = fallback_date.replace(hour=parsed_time.hour, minute=parsed_time.minute)
            return _ensure_timezone(combined)
        except ValueError:
            return None

    return None


def _load_remote_resource(source_url: Optional[str] = None) -> str:
    remote_url = source_url or os.environ.get("EVENT_SOURCE_URL")
    if not remote_url:
        raise EventSourceError("イベント情報を取得するURLが指定されていません。")

    headers = {"User-Agent": USER_AGENT}
    try:
        response = requests.get(remote_url, headers=headers, timeout=REQUEST_TIMEOUT)
        response.raise_for_status()
    except requests.RequestException as exc:
        raise EventSourceError(f"イベント情報の取得に失敗しました: {exc}") from exc

    return response.text


def _load_remote_resource_browser(source_url: str) -> str:
    wait_selector = os.environ.get("EVENT_SOURCE_BROWSER_WAIT_SELECTOR")
    timeout_ms = int(os.environ.get("EVENT_SOURCE_BROWSER_TIMEOUT", "10000"))
    headless = os.environ.get("EVENT_SOURCE_BROWSER_HEADFUL", "").lower() not in {"1", "true"}

    try:
        from playwright.sync_api import TimeoutError as PlaywrightTimeoutError  # type: ignore[import]
        from playwright.sync_api import sync_playwright  # type: ignore[import]
    except Exception as exc:  # noqa: BLE001
        raise EventSourceError(
            "Playwright を読み込めませんでした。`pip install playwright` と `playwright install` を実行してください。"
        ) from exc

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=headless)
            context = browser.new_context(user_agent=USER_AGENT)
            page = context.new_page()
            page.goto(source_url, wait_until="networkidle", timeout=timeout_ms)
            if wait_selector:
                page.wait_for_selector(wait_selector, timeout=timeout_ms)
            html = page.content()
            browser.close()
            return html
    except PlaywrightTimeoutError as exc:
        raise EventSourceError(
            f"ヘッドレスブラウザでの取得がタイムアウトしました（{timeout_ms}ms, selector={wait_selector or 'なし'}）。"
        ) from exc
    except Exception as exc:  # noqa: BLE001
        raise EventSourceError(f"ヘッドレスブラウザでの取得に失敗しました: {exc}") from exc


def _prepare_prompt_text(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    target = soup.body if soup.body else soup
    text = target.get_text("\n", strip=True)
    if len(text) > MAX_TEXT_LENGTH:
        text = text[:MAX_TEXT_LENGTH]
    return text


def _call_gemini(prompt: str) -> Tuple[List[dict], str]:
    api_key = os.environ.get("GOOGLE_API_KEY")
    if not api_key:
        raise EventSourceError("GOOGLE_API_KEY が設定されていません。")

    model_name = os.environ.get("GEMINI_MODEL", "models/gemini-2.0-flash-lite")

    system_instruction = (
        "あなたは音楽ライブ情報の専門家です。与えられたテキストからイベントの一覧を抽出し、"
        "JSON配列のみで返してください。各イベントは以下のキーを含めます: \n"
        "- title: イベント名（必須）\n"
        "- start: ISO8601形式の開始日時（例: 2025-11-16T18:00:00+09:00）。時刻不明なら18:00を推定し +09:00 を付与してください。\n"
        "- end: ISO8601形式の終了日時。情報が無ければ開始から2時間後を推定してください。\n"
        "- location: 会場名や都道府県名（分かる範囲で）\n"
        "- url: 詳細ページ等のURL（無ければ空文字）\n"
        "- description: 開場/開演情報や問い合わせ先などの補足。\n"
        "- source_id: 開催日とタイトルを基にした短い識別子（例: 20251116-super-beaver-okinawa）。\n"
        "JSON以外の説明や余分なテキストは出力しないでください。"
    )

    genai.configure(api_key=api_key)
    model = genai.GenerativeModel(
        model_name,
        system_instruction=system_instruction,
        generation_config=genai_types.GenerationConfig(
            response_mime_type="application/json",
            temperature=float(os.environ.get("GEMINI_TEMPERATURE", "0.2")),
        ),
    )

    try:
        response = model.generate_content(prompt)
    except google_exceptions.ResourceExhausted as exc:
        raise EventSourceError(
            "Gemini API の利用制限に達しました。時間をおいて再度お試しください。"
        ) from exc
    except google_exceptions.TooManyRequests as exc:
        raise EventSourceError(
            "Gemini API の呼び出し回数制限に達しました。しばらく待ってから再実行してください。"
        ) from exc
    except google_exceptions.GoogleAPICallError as exc:
        details = getattr(exc, "message", None) or getattr(exc, "reason", None) or str(exc)
        raise EventSourceError(f"Gemini API の呼び出しに失敗しました: {details}") from exc
    except Exception as exc:  # noqa: BLE001
        raise EventSourceError(f"Gemini へのリクエスト中にエラーが発生しました: {exc}") from exc

    if not response.candidates:
        prompt_feedback = getattr(response, "prompt_feedback", None)
        if prompt_feedback and getattr(prompt_feedback, "block_reason", None):
            block_reason = prompt_feedback.block_reason
            if block_reason == genai_types.BlockReason.BLOCK_REASON_UNSPECIFIED:
                raise EventSourceError("Gemini の応答がブロックされました。時間をおいて再試行してください。")
            if block_reason == genai_types.BlockReason.OTHER:
                raise EventSourceError("Gemini の応答がモデル制限によりブロックされました。しばらく待ってから再度お試しください。")
            raise EventSourceError(
                f"Gemini の応答がブロックされました（理由: {block_reason.name}）。プロンプトを見直してください。"
            )
        raise EventSourceError("Gemini の応答に候補が含まれていません。")

    raw_text = (response.text or "").strip()
    text = raw_text
    if text.startswith("```"):
        text = text.strip("`")
        if text.startswith("json\n"):
            text = text[5:]
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise EventSourceError(f"Gemini からの応答をJSONとして解析できません: {exc}") from exc

    if not isinstance(data, list):
        raise EventSourceError("Gemini からの応答が配列ではありません。")

    return data, raw_text


def _extract_events_with_gemini(html: str, source_url: str) -> Tuple[List[Event], str]:
    text = _prepare_prompt_text(html)

    instructions = (
        "以下はアーティスト公式サイト等から取得したライブ・イベント情報です。"
        "テキストを分析して、開催日や会場、開場/開演時間を可能な限り抽出してください。"
        "元ページのURL: " + source_url + "\n\n" + text
    )

    raw_events, raw_text = _call_gemini(instructions)

    tz = _get_timezone()
    events: List[Event] = []
    for item in raw_events:
        if not isinstance(item, dict):
            continue

        title = (item.get("title") or "").strip()
        if not title:
            continue

        start_raw = item.get("start") or item.get("start_time")
        start_dt = _parse_iso_datetime(start_raw)
        if start_dt is None:
            # fallback: 推測用に日付単体が渡されたケース
            date_only = item.get("date")
            if date_only:
                try:
                    base = datetime.fromisoformat(date_only)
                except ValueError:
                    try:
                        base = datetime.strptime(date_only, "%Y-%m-%d")
                    except ValueError:
                        base = None
                if base:
                    base = _ensure_timezone(base)
                    start_dt = base.replace(hour=DEFAULT_START_HOUR, minute=0)
        if start_dt is None:
            continue

        end_raw = item.get("end") or item.get("end_time")
        end_dt = _parse_iso_datetime(end_raw, fallback_date=start_dt)
        if end_dt is None:
            end_dt = start_dt + DEFAULT_DURATION

        location = (item.get("location") or "").strip() or None
        url_field = (item.get("url") or "").strip() or None
        description = (item.get("description") or "").strip() or None
        source_id = (item.get("source_id") or "").strip() or None
        if not source_id:
            source_id = f"{start_dt.strftime('%Y%m%d')}-{title[:40].lower().replace(' ', '-') }"

        events.append(
            Event(
                source_id=source_id,
                title=title,
                start=_ensure_timezone(start_dt),
                end=_ensure_timezone(end_dt),
                location=location,
                url=url_field or source_url,
                description=description,
            )
        )

    return events, raw_text


def _make_debug_slug(source_url: str) -> str:
    if not source_url:
        return "unknown"
    parsed = urlparse(source_url)
    candidate = (parsed.netloc + parsed.path).strip("/")
    if not candidate:
        candidate = parsed.netloc or "source"
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", candidate).strip("-")
    if not slug:
        slug = "source"
    return slug[:50]


def _serialize_event_for_debug(event: Event) -> dict:
    return {
        "source_id": event.source_id,
        "title": event.title,
        "start": event.start.isoformat(),
        "end": event.end.isoformat(),
        "location": event.location,
        "url": event.url,
        "description": event.description,
    }


def _store_debug_artifacts(
    source_url: str,
    raw_content: str,
    raw_llm_output: str,
    events: List[Event],
    *,
    fetch_mode: str,
) -> None:
    debug_dir_setting = os.environ.get("EVENT_SOURCE_DEBUG_DIR")
    debug_dir = Path(debug_dir_setting) if debug_dir_setting else DEFAULT_DEBUG_DIR
    try:
        debug_dir.mkdir(parents=True, exist_ok=True)
    except OSError:
        return

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    slug = _make_debug_slug(source_url)
    mode_suffix = "browser" if fetch_mode == "browser" else "static"
    base_name = f"{timestamp}_{slug}_{mode_suffix}"

    raw_path = debug_dir / f"{base_name}.html"
    json_path = debug_dir / f"{base_name}.json"

    try:
        raw_path.write_text(raw_content, encoding="utf-8")
    except OSError:
        pass

    payload = {
        "source_url": source_url,
        "timestamp": timestamp,
        "raw_llm_output": raw_llm_output,
        "fetch_mode": fetch_mode,
        "parsed_events": [_serialize_event_for_debug(event) for event in events],
    }

    try:
        json_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except OSError:
        pass


def fetch_events(
    source_url: Optional[str] = None,
    *,
    use_browser: bool = False,
) -> List[Event]:
    remote_url = source_url or os.environ.get("EVENT_SOURCE_URL")
    if not remote_url:
        raise EventSourceError("イベント情報を取得するURLが指定されていません。")

    default_mode = os.environ.get("EVENT_SOURCE_DEFAULT_FETCH_MODE", "static").lower()
    effective_use_browser = use_browser or default_mode == "browser"

    if effective_use_browser:
        html = _load_remote_resource_browser(remote_url)
        fetch_mode = "browser"
    else:
        html = _load_remote_resource(remote_url)
        fetch_mode = "static"
    events, raw_llm_output = _extract_events_with_gemini(html, remote_url)
    if not events:
        raise EventSourceError("Gemini からイベント情報を取得できませんでした。")
    sorted_events = sorted(events, key=lambda event: event.start)
    _store_debug_artifacts(
        remote_url,
        html,
        raw_llm_output,
        sorted_events,
        fetch_mode=fetch_mode,
    )
    return sorted_events

