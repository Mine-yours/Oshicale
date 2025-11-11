import os
from datetime import datetime, timezone
from typing import Dict, List

from flask import (
    flash,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from googleapiclient.errors import HttpError

from app import app
from app import db
from app.event_source import Event, EventSourceError, fetch_events
from app.google_calendar import (
    GoogleCalendarNotConfigured,
    build_flow,
    get_calendar_service,
    load_credentials,
    store_credentials,
)


def _serialize_event(event: Event) -> Dict[str, str]:
    return {
        "source_id": event.source_id,
        "title": event.title,
        "start": event.start.isoformat(),
        "end": event.end.isoformat(),
        "location": event.location or "",
        "url": event.url or "",
        "description": event.description or "",
    }


def _deserialize_event(data: Dict[str, str]) -> Event:
    def _parse_datetime(value: str) -> datetime:
        parsed = datetime.fromisoformat(value)
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=timezone.utc)
        return parsed

    return Event(
        source_id=data["source_id"],
        title=data["title"],
        start=_parse_datetime(data["start"]),
        end=_parse_datetime(data["end"]),
        location=data.get("location") or None,
        url=data.get("url") or None,
        description=data.get("description") or None,
    )


@app.route("/", methods=["GET"])
def index():
    synced_events = db.get_synced_events()
    event_sources = db.get_event_sources()

    calendar_connected = True
    try:
        load_credentials()
    except GoogleCalendarNotConfigured:
        calendar_connected = False

    preview_data = session.get("preview_events")
    preview_events: List[Event] = []
    preview_source = None
    preview_selected_ids: List[str] = []

    if preview_data:
        preview_events = [_deserialize_event(item) for item in preview_data.get("events", [])]
        preview_source = {
            "source_id": preview_data.get("source_id"),
            "oshi_name": preview_data.get("oshi_name"),
            "url": preview_data.get("source_url"),
            "fetched_at": preview_data.get("fetched_at"),
            "use_browser": preview_data.get("use_browser", False),
            "mode": preview_data.get("mode"),
        }
        preview_selected_ids = preview_data.get("selected_ids", [])

    calendar_id = os.environ.get("GOOGLE_CALENDAR_ID", "primary")

    return render_template(
        "index.html",
        event_sources=event_sources,
        preview_events=preview_events,
        preview_source=preview_source,
        preview_selected_ids=preview_selected_ids,
        synced_events=synced_events,
        calendar_connected=calendar_connected,
        calendar_id=calendar_id,
    )


@app.route("/authorize")
def authorize():
    try:
        flow = build_flow("oauth2callback")
    except GoogleCalendarNotConfigured as exc:
        flash(str(exc), "error")
        return redirect(url_for("index"))

    authorization_url, state = flow.authorization_url(
        access_type="offline",
        include_granted_scopes="true",
        prompt="consent",
    )
    session["state"] = state
    return redirect(authorization_url)


@app.route("/oauth2callback")
def oauth2callback():
    state = session.get("state")
    try:
        flow = build_flow("oauth2callback", state=state)
    except GoogleCalendarNotConfigured as exc:
        flash(str(exc), "error")
        return redirect(url_for("index"))

    try:
        flow.fetch_token(authorization_response=request.url)
    except Exception as exc:  # noqa: BLE001
        flash(f"Googleアカウントの認証に失敗しました: {exc}", "error")
        return redirect(url_for("index"))

    credentials = flow.credentials
    store_credentials(credentials)

    flash("Googleカレンダーと連携しました。", "success")
    return redirect(url_for("index"))


@app.route("/sources", methods=["POST"])
def add_event_source():
    oshi_name = (request.form.get("oshi_name") or "").strip()
    url = (request.form.get("url") or "").strip()

    if not oshi_name or not url:
        flash("推しの名前と取得したいURLを入力してください。", "error")
        return redirect(url_for("index"))

    if not url.startswith(("http://", "https://")):
        flash("URLは http:// または https:// で始まる形式で入力してください。", "error")
        return redirect(url_for("index"))

    try:
        db.add_event_source(oshi_name, url)
    except Exception as exc:  # noqa: BLE001
        flash(f"イベントソースの追加に失敗しました: {exc}", "error")
        return redirect(url_for("index"))

    flash(f"「{oshi_name}」の取得先を追加しました。", "success")
    return redirect(url_for("index"))


@app.route("/sources/<int:source_id>/delete", methods=["POST"])
def delete_event_source(source_id: int):
    source = db.get_event_source(source_id)
    if not source:
        flash("指定されたイベントソースが見つかりませんでした。", "error")
        return redirect(url_for("index"))

    try:
        db.delete_event_source(source_id)
    except Exception as exc:  # noqa: BLE001
        flash(f"イベントソースの削除に失敗しました: {exc}", "error")
        return redirect(url_for("index"))

    preview_data = session.get("preview_events")
    if preview_data and preview_data.get("source_id") == source_id:
        session.pop("preview_events", None)

    flash(f"「{source['oshi_name']}」の取得先を削除しました。", "info")
    return redirect(url_for("index"))


@app.route("/sources/<int:source_id>/preview", methods=["POST"])
def preview_events(source_id: int):
    source = db.get_event_source(source_id)
    if not source:
        flash("指定されたイベントソースが見つかりませんでした。", "error")
        return redirect(url_for("index"))

    source_mode = (source.get("mode") or "static").lower()
    use_browser = request.form.get("use_browser") == "1"

    if not use_browser:
        if source_mode == "browser":
            use_browser = True

    try:
        events = fetch_events(source["url"], use_browser=use_browser)
    except EventSourceError as exc:
        flash(str(exc), "error")
        return redirect(url_for("index"))

    serialized = [_serialize_event(event) for event in events]
    selected_ids = [item["source_id"] for item in serialized]
    session["preview_events"] = {
        "source_id": source["id"],
        "oshi_name": source["oshi_name"],
        "source_url": source["url"],
        "mode": source_mode,
        "events": serialized,
        "selected_ids": selected_ids,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "use_browser": use_browser,
    }

    if use_browser:
        mode_label = "ヘッドレスブラウザ"
    else:
        mode_label = "HTTPリクエスト"
    flash(
        f"「{source['oshi_name']}」の予定を取得しました（{mode_label}）。同期するイベントを選択してください。",
        "info",
    )
    return redirect(url_for("index"))


@app.route("/sync", methods=["POST"])
def sync_events():
    preview_data = session.get("preview_events")
    if not preview_data:
        flash("同期する前に、保存したURLからイベントを取得してください。", "warning")
        return redirect(url_for("index"))

    selected_ids = request.form.getlist("selected_events")
    preview_data["selected_ids"] = selected_ids
    session["preview_events"] = preview_data

    try:
        calendar_service = get_calendar_service()
    except GoogleCalendarNotConfigured as exc:
        flash(str(exc), "warning")
        return redirect(url_for("index"))

    events_map = {
        item["source_id"]: _deserialize_event(item) for item in preview_data.get("events", [])
    }

    target_events = [events_map[event_id] for event_id in selected_ids if event_id in events_map]

    if not target_events:
        flash("同期するイベントを選択してください。", "warning")
        return redirect(url_for("index"))

    calendar_id = os.environ.get("GOOGLE_CALENDAR_ID", "primary")
    synced_events = db.get_synced_events()

    now = datetime.now(timezone.utc)
    inserted = 0
    skipped_past = 0
    skipped_existing = 0

    for event in target_events:
        if event.source_id in synced_events:
            skipped_existing += 1
            continue

        if event.end < now:
            skipped_past += 1
            continue

        body = {
            "summary": event.title,
            "start": {"dateTime": event.start.astimezone(timezone.utc).isoformat()},
            "end": {"dateTime": event.end.astimezone(timezone.utc).isoformat()},
        }

        if event.location:
            body["location"] = event.location

        description_lines = []
        if event.description:
            description_lines.append(event.description)
        if event.url:
            description_lines.append(f"詳細: {event.url}")
        if description_lines:
            body["description"] = "\n".join(description_lines)

        try:
            created_event = (
                calendar_service.events().insert(calendarId=calendar_id, body=body).execute()
            )
        except HttpError as exc:
            flash(f"Googleカレンダーへの登録に失敗しました: {exc}", "error")
            return redirect(url_for("index"))

        db.record_synced_event(event.source_id, created_event.get("id", ""))
        inserted += 1

    session.pop("preview_events", None)

    if inserted == 0 and skipped_past == 0 and skipped_existing == 0:
        flash("同期対象の新しいイベントはありませんでした。", "info")
    else:
        message = f"{inserted}件のイベントを同期しました。"
        if skipped_past:
            message += f"（過去イベント {skipped_past}件はスキップ）"
        if skipped_existing:
            message += f"（既存イベント {skipped_existing}件はスキップ）"
        flash(message, "success")

    return redirect(url_for("index"))
