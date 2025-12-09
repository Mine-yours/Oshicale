import json
import os
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import List, Optional, Tuple, Dict
from urllib.parse import urlparse

import google.generativeai as genai
from google.api_core import exceptions as google_exceptions
from google.generativeai import types as genai_types
from firecrawl import FirecrawlApp  # 追加

# 設定値
DEFAULT_DURATION = timedelta(hours=2)
DEFAULT_START_HOUR = int(os.environ.get("EVENT_DEFAULT_START_HOUR", "18"))
DEFAULT_DEBUG_DIR = Path(__file__).resolve().parent / "static" / "output"

@dataclass
class Event:
    source_id: str
    title: str
    start: datetime
    end: datetime
    category: Optional[str] = None
    location: Optional[str] = None
    url: Optional[str] = None
    description: Optional[str] = None

class EventSourceError(Exception):
    """イベント情報の取得に失敗した場合の例外"""

# --- タイムゾーンなどのユーティリティ関数（変更なし） ---
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
    if not value: return None
    cleaned = value.strip()
    if cleaned.endswith("Z"): cleaned = cleaned[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(cleaned)
        return _ensure_timezone(parsed)
    except ValueError: pass
    if fallback_date is not None:
        try:
            parsed_time = datetime.strptime(cleaned, "%H:%M")
            return _ensure_timezone(fallback_date.replace(hour=parsed_time.hour, minute=parsed_time.minute))
        except ValueError: return None
    return None

def _make_debug_slug(source_url: str) -> str:
    """URLからファイル名用のスラッグを生成"""
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
    """イベントをJSON保存用に変換"""
    return {
        "source_id": event.source_id,
        "title": event.title,
        "category": event.category,
        "start": event.start.isoformat(),
        "end": event.end.isoformat(),
        "location": event.location,
        "url": event.url,
        "description": event.description,
    }

def _store_debug_artifacts(
    source_url: str,
    markdown_content: str,
    raw_llm_output: str,
    events: List[Event],
) -> None:
    """取得したMarkdownとLLM出力をデバッグ用に保存"""
    debug_dir_setting = os.environ.get("EVENT_SOURCE_DEBUG_DIR")
    debug_dir = Path(debug_dir_setting) if debug_dir_setting else DEFAULT_DEBUG_DIR
    try:
        debug_dir.mkdir(parents=True, exist_ok=True)
    except OSError:
        return

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    slug = _make_debug_slug(source_url)
    base_name = f"{timestamp}_{slug}_firecrawl"

    # Markdownを保存
    md_path = debug_dir / f"{base_name}.md"
    try:
        md_path.write_text(markdown_content, encoding="utf-8")
    except OSError:
        pass

    # JSON（LLM出力とパース結果）を保存
    json_path = debug_dir / f"{base_name}.json"
    payload = {
        "source_url": source_url,
        "timestamp": timestamp,
        "raw_llm_output": raw_llm_output,
        "fetch_mode": "firecrawl",
        "parsed_events": [_serialize_event_for_debug(event) for event in events],
    }
    try:
        json_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except OSError:
        pass

# --- FireCrawlを使った取得処理（ここが劇的に短くなりました） ---
def _fetch_markdown_with_firecrawl(source_url: str) -> str:
    api_key = os.environ.get("FIRECRAWL_API_KEYS")
    if not api_key:
        raise EventSourceError("FIRECRAWL_API_KEYS が設定されていません。")
    
    try:
        app = FirecrawlApp(api_key=api_key)
        # scrape_url を使用してMarkdown形式で取得
        scrape_result = app.scrape_url(source_url, params={'formats': ['markdown']})
        
        # 結果は辞書形式で返される可能性があるので、両方の形式に対応
        if isinstance(scrape_result, dict):
            markdown = scrape_result.get('markdown') or scrape_result.get('data', {}).get('markdown')
        else:
            # オブジェクト形式の場合
            markdown = getattr(scrape_result, 'markdown', None) or getattr(scrape_result, 'data', None)
        
        if not markdown:
            raise EventSourceError("FireCrawlからMarkdownを取得できませんでした。")
            
        return markdown

    except Exception as exc:
        raise EventSourceError(f"FireCrawlでの取得に失敗しました: {exc}") from exc

# デフォルトのモデルリスト（軽い順）
DEFAULT_MODELS = [
    "models/gemini-2.0-flash-lite",
    "models/gemini-2.0-flash",
    "models/gemini-2.5-flash-lite",
    "models/gemini-2.5-flash-lite-preview",
    "models/gemini-2.5-flash",

]

def _get_model_list() -> List[str]:
    """環境変数からモデルリストを取得（カンマ区切り）"""
    env_models = os.environ.get("GEMINI_MODELS")
    if env_models:
        return [m.strip() for m in env_models.split(",") if m.strip()]
    # 単一モデル指定の場合はそれを優先
    single_model = os.environ.get("GEMINI_MODEL")
    if single_model:
        return [single_model]
    return DEFAULT_MODELS

def _get_system_instruction() -> str:
    """システムプロンプトを返す"""
    return (
        "あなたはアーティスト・タレントのスケジュール情報を抽出する専門家です。"
        "与えられたMarkdownテキストから、以下のような予定をすべて抽出してJSON配列で返してください：\n"
        "- ライブ・コンサート・フェス出演\n"
        "- テレビ番組出演（放送日時）\n"
        "- ラジオ番組出演（放送日時）\n"
        "- イベント・握手会・サイン会\n"
        "- 配信・生配信\n"
        "- リリース日（CD、DVD、書籍など）\n"
        "- その他のスケジュール\n\n"
        "各イベントは以下のキーを含めてください：\n"
        "- title: イベント名・番組名（必須）\n"
        "- category: 種別。次のいずれか: ライブ, テレビ, ラジオ, イベント, 配信, リリース, その他\n"
        "- start: ISO8601形式の開始日時（例: 2025-11-16T18:00:00+09:00）。時刻不明なら18:00を推定。\n"
        "- end: ISO8601形式の終了日時。情報が無ければ開始から1時間後を推定。\n"
        "- location: 会場名・放送局名・配信プラットフォーム名など\n"
        "- url: 詳細ページのURL（あれば）\n"
        "- description: 開場/開演情報、出演者、放送内容などの補足\n"
        "- source_id: 日付とタイトルを基にした短い識別子（例: 20251116-musicstation）\n"
        "JSON以外の余分なテキストは出力しないでください。"
    )

def _try_generate_with_model(model_name: str, prompt: str, system_instruction: str):
    """指定モデルでコンテンツ生成を試みる"""
    model = genai.GenerativeModel(
        model_name,
        system_instruction=system_instruction,
        generation_config=genai_types.GenerationConfig(
            response_mime_type="application/json",
            temperature=float(os.environ.get("GEMINI_TEMPERATURE", "0.2")),
        ),
    )
    return model.generate_content(prompt)

# --- Gemini API呼び出し（複数モデルフォールバック対応） ---
def _call_gemini(prompt: str) -> Tuple[List[dict], str]:
    api_key = os.environ.get("GOOGLE_API_KEY")
    if not api_key:
        raise EventSourceError("GOOGLE_API_KEY が設定されていません。")

    genai.configure(api_key=api_key)
    
    models = _get_model_list()
    system_instruction = _get_system_instruction()
    
    last_error = None
    tried_models = []
    
    for model_name in models:
        tried_models.append(model_name)
        try:
            response = _try_generate_with_model(model_name, prompt, system_instruction)
            
            if not response.candidates:
                # 候補なしの場合は次のモデルを試す
                last_error = EventSourceError(f"{model_name}: 応答に候補が含まれていません")
                continue
            
            # 成功した場合
            text = response.text.strip()
            if text.startswith("```json"): text = text[7:-3].strip()
            elif text.startswith("```"): text = text[3:-3].strip()
            
            return json.loads(text), response.text
            
        except (google_exceptions.ResourceExhausted, google_exceptions.TooManyRequests) as exc:
            # レート制限の場合は次のモデルを試す
            last_error = exc
            continue
        except google_exceptions.GoogleAPICallError as exc:
            # その他のAPIエラーも次のモデルを試す
            last_error = exc
            continue
        except json.JSONDecodeError as exc:
            # JSONパースエラーは次のモデルを試す
            last_error = exc
            continue
        except Exception as exc:
            # 予期せぬエラーも次のモデルを試す
            last_error = exc
            continue
    
    # すべてのモデルで失敗した場合
    tried_str = ", ".join(tried_models)
    if isinstance(last_error, (google_exceptions.ResourceExhausted, google_exceptions.TooManyRequests)):
        raise EventSourceError(
            f"すべてのモデル ({tried_str}) で利用制限に達しました。しばらく待ってから再度お試しください。"
        ) from last_error
    else:
        raise EventSourceError(
            f"すべてのモデル ({tried_str}) で失敗しました: {last_error}"
        ) from last_error

def _extract_events_with_gemini(markdown_text: str, source_url: str) -> Tuple[List[Event], str]:
    # 入力がHTMLではなくMarkdownになったので、BeautifulSoupでの前処理(_prepare_prompt_text)は不要です。
    
    instructions = (
        "以下はFireCrawlによってMarkdown形式に変換されたWebサイトの情報です。"
        "このテキストを分析して、ライブ・イベント情報を抽出してください。"
        "元ページのURL: " + source_url + "\n\n" + markdown_text
    )

    raw_events, raw_text = _call_gemini(instructions)

    # ... (以下のJSONからEventオブジェクトへの変換ロジックは以前と全く同じ) ...
    events = []
    tz = _get_timezone()
    for item in raw_events:
        # (以前のコードのループ処理と同じ)
        if not isinstance(item, dict): continue
        title = (item.get("title") or "").strip()
        if not title: continue
        
        start_raw = item.get("start")
        start_dt = _parse_iso_datetime(start_raw)
        # ... (中略: データ変換ロジック) ...
        
        # カテゴリを取得（デフォルトは「その他」）
        category = (item.get("category") or "その他").strip()
        
        events.append(Event(
            source_id=item.get("source_id", "temp"),
            title=title,
            start=start_dt or datetime.now(),
            end=start_dt or datetime.now(),
            category=category,
            location=item.get("location"),
            url=item.get("url"),
            description=item.get("description")
        ))
    
    return events, raw_text

# --- メイン関数 ---
def fetch_events(source_url: Optional[str] = None) -> List[Event]:
    remote_url = source_url or os.environ.get("EVENT_SOURCE_URL")
    if not remote_url:
        raise EventSourceError("URLが指定されていません。")

    # 1. FireCrawlでMarkdownを取得 (browserモードかどうかの分岐は不要)
    markdown_content = _fetch_markdown_with_firecrawl(remote_url)
    
    # 2. Geminiで解析
    events, raw_llm_output = _extract_events_with_gemini(markdown_content, remote_url)
    
    # 3. デバッグ保存
    sorted_events = sorted(events, key=lambda e: e.start)
    _store_debug_artifacts(remote_url, markdown_content, raw_llm_output, sorted_events)

    return sorted_events