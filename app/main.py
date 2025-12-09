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
        "category": event.category or "",
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
        category=data.get("category") or None,
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


@app.route("/calendar")
def calendar_view():
    import json as json_module
    from datetime import datetime as dt
    
    # アーカイブ表示の切り替え
    show_archived = request.args.get("archived", "false").lower() == "true"
    
    # データベースから保存されたイベントを取得
    if show_archived:
        # アーカイブ済みイベントのみを取得
        app_events = db.get_archived_events()
    else:
        # 通常のイベント（アーカイブされていないもの）を取得
        app_events = db.get_app_events()
    
    # Eventオブジェクトに変換
    events: List[Event] = []
    for event_data in app_events:
        try:
            start_dt = dt.fromisoformat(event_data["start"])
            end_dt = dt.fromisoformat(event_data["end"])
            # タイムゾーン情報がない場合は追加
            if start_dt.tzinfo is None:
                from datetime import timezone
                start_dt = start_dt.replace(tzinfo=timezone.utc)
            if end_dt.tzinfo is None:
                from datetime import timezone
                end_dt = end_dt.replace(tzinfo=timezone.utc)
            
            event = Event(
                source_id=event_data["source_event_id"],
                title=event_data["title"],
                start=start_dt,
                end=end_dt,
                category=event_data.get("category"),
                location=event_data.get("location"),
                url=event_data.get("url"),
                description=event_data.get("description"),
            )
            events.append(event)
        except (ValueError, KeyError) as e:
            print(f"イベント変換エラー: {e}")
            continue
    
    # FullCalendar用にイベントをJSON形式に変換
    events_json = json_module.dumps([
        {
            "source_id": event.source_id,
            "title": event.title,
            "category": event.category or "その他",
            "start": event.start.isoformat(),
            "end": event.end.isoformat(),
            "location": event.location or "",
            "url": event.url or "",
            "description": event.description or "",
        }
        for event in events
    ], ensure_ascii=False)
    
    return render_template(
        "calendar.html",
        events=events,
        events_json=events_json,
        oshi_name=None,  # 保存されたイベントは複数の推しが混在する可能性があるため
        show_archived=show_archived,
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
        events = fetch_events(source["url"])
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

        # 日時情報と共に記録
        db.record_synced_event_with_datetime(
            event.source_id,
            created_event.get("id", ""),
            event.start.isoformat(),
            event.end.isoformat(),
            event.title,
        )
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


@app.route("/save-to-app-calendar", methods=["POST"])
def save_to_app_calendar():
    """選択したイベントをアプリ上のカレンダーに保存"""
    preview_data = session.get("preview_events")
    if not preview_data:
        flash("保存する前に、URLからイベントを取得してください。", "warning")
        return redirect(url_for("index"))

    selected_ids = request.form.getlist("selected_events")
    if not selected_ids:
        flash("保存するイベントを選択してください。", "warning")
        return redirect(url_for("index"))

    events_map = {
        item["source_id"]: _deserialize_event(item) for item in preview_data.get("events", [])
    }

    target_events = [events_map[event_id] for event_id in selected_ids if event_id in events_map]

    if not target_events:
        flash("保存するイベントを選択してください。", "warning")
        return redirect(url_for("index"))

    now = datetime.now(timezone.utc)
    inserted = 0
    skipped_past = 0
    skipped_existing = 0

    for event in target_events:
        # 過去のイベントはスキップ
        if event.end < now:
            skipped_past += 1
            continue

        # 重複チェック（タイトルと日時が同じイベントを検出）
        duplicate = db.check_duplicate_events(event.title, event.start.isoformat())
        if duplicate:
            # 重複が見つかった場合はマージ
            db.merge_duplicate_events(
                duplicate["source_event_id"],
                {
                    "title": event.title,
                    "category": event.category,
                    "start": event.start.isoformat(),
                    "end": event.end.isoformat(),
                    "location": event.location,
                    "url": event.url,
                    "description": event.description,
                },
            )
            skipped_existing += 1
            continue

        # 既に保存されているかチェック（source_idベース）
        if db.is_app_event_saved(event.source_id):
            skipped_existing += 1
            continue

        # アプリ上のカレンダーに保存
        db.save_app_event(
            event.source_id,
            event.title,
            event.category,
            event.start.isoformat(),
            event.end.isoformat(),
            event.location,
            event.url,
            event.description,
        )
        inserted += 1

    if inserted == 0 and skipped_past == 0 and skipped_existing == 0:
        flash("保存対象の新しいイベントはありませんでした。", "info")
    else:
        message = f"{inserted}件のイベントをアプリ上のカレンダーに保存しました。"
        if skipped_past:
            message += f"（過去イベント {skipped_past}件はスキップ）"
        if skipped_existing:
            message += f"（既存イベント {skipped_existing}件はスキップ）"
        flash(message, "success")

    return redirect(url_for("index"))


@app.route("/calendar/clear-all", methods=["POST"])
def clear_all_calendar_events():
    """すべてのアプリカレンダーイベントを削除"""
    count = db.clear_all_app_events()
    flash(f"{count}件のイベントを削除しました。", "success")
    return redirect(url_for("calendar_view"))


@app.route("/calendar/clear-past", methods=["POST"])
def clear_past_calendar_events():
    """過去のアプリカレンダーイベントを削除"""
    count = db.clear_past_app_events()
    flash(f"{count}件の過去イベントを削除しました。", "success")
    return redirect(url_for("calendar_view"))


@app.route("/events/<event_id>/archive", methods=["POST"])
def archive_event(event_id: str):
    """イベントをアーカイブ"""
    db.archive_app_event(event_id)
    flash("イベントをアーカイブしました。", "success")
    return redirect(request.referrer or url_for("calendar_view"))


@app.route("/events/<event_id>/unarchive", methods=["POST"])
def unarchive_event(event_id: str):
    """アーカイブから復元"""
    db.unarchive_app_event(event_id)
    flash("イベントを復元しました。", "success")
    return redirect(request.referrer or url_for("calendar_view"))


@app.route("/calendar/events/<event_id>/edit", methods=["GET", "POST"])
def edit_calendar_event(event_id: str):
    """カレンダーイベントの編集"""
    # データベースからイベントを取得
    app_events = db.get_app_events(include_archived=True)
    event_data = next((e for e in app_events if e["source_event_id"] == event_id), None)
    
    if not event_data:
        flash("イベントが見つかりませんでした。", "error")
        return redirect(url_for("calendar_view"))
    
    if request.method == "POST":
        title = request.form.get("title", "").strip()
        category = request.form.get("category", "").strip()
        start_str = request.form.get("start", "").strip()
        end_str = request.form.get("end", "").strip()
        location = request.form.get("location", "").strip()
        description = request.form.get("description", "").strip()
        url = request.form.get("url", "").strip()
        
        if not title:
            flash("タイトルは必須です。", "error")
            return redirect(url_for("edit_calendar_event", event_id=event_id))
        
        # 日時をISO形式に変換
        from datetime import datetime as dt
        try:
            if start_str:
                start_dt = dt.fromisoformat(start_str.replace("Z", "+00:00"))
                start_iso = start_dt.astimezone(timezone.utc).isoformat()
            else:
                start_iso = event_data["start"]
            
            if end_str:
                end_dt = dt.fromisoformat(end_str.replace("Z", "+00:00"))
                end_iso = end_dt.astimezone(timezone.utc).isoformat()
            else:
                end_iso = event_data["end"]
        except (ValueError, AttributeError) as e:
            flash(f"日時の形式が正しくありません: {e}", "error")
            return redirect(url_for("edit_calendar_event", event_id=event_id))
        
        # イベントを更新
        db.update_app_event(
            source_event_id=event_id,
            title=title,
            category=category or None,
            start=start_iso,
            end=end_iso,
            location=location or None,
            url=url or None,
            description=description or None,
        )
        
        flash("イベントを編集しました。", "success")
        return redirect(url_for("calendar_view"))
    
    # 日時をdatetime-local形式に変換
    def format_datetime_local(dt_str):
        if not dt_str:
            return ""
        try:
            dt_obj = dt.fromisoformat(dt_str.replace("Z", "+00:00"))
            local_dt = dt_obj.astimezone()
            return local_dt.strftime("%Y-%m-%dT%H:%M")
        except (ValueError, AttributeError):
            return ""
    
    # 表示用データにフォーマット済み日時を追加
    display_data = dict(event_data)
    display_data["start_formatted"] = format_datetime_local(event_data.get("start"))
    display_data["end_formatted"] = format_datetime_local(event_data.get("end"))
    
    return render_template("edit_calendar_event.html", event=display_data, event_id=event_id)


@app.route("/calendar/events/delete-selected", methods=["POST"])
def delete_selected_calendar_events():
    """選択したカレンダーイベントを一括削除"""
    selected_ids = request.form.getlist("selected_events")
    
    if not selected_ids:
        flash("削除するイベントを選択してください。", "warning")
        return redirect(url_for("calendar_view"))
    
    count = db.delete_app_events_bulk(selected_ids)
    flash(f"{count}件のイベントを削除しました。", "success")
    return redirect(url_for("calendar_view"))


@app.route("/events/email/<token>/add", methods=["GET"])
def add_event_from_email(token: str):
    """メール内のリンクからイベントをカレンダーに追加（単一イベント用、後方互換性のため残す）"""
    event_data = db.get_pending_email_event(token)
    
    if not event_data:
        flash("リンクが無効または期限切れです。", "error")
        return redirect(url_for("index"))
    
    # 既に保存されているかチェック
    if db.is_app_event_saved(event_data["source_event_id"]):
        db.delete_pending_email_event(token)
        flash("このイベントは既にカレンダーに追加されています。", "info")
        return redirect(url_for("calendar_view"))
    
    # イベントをカレンダーに保存
    try:
        db.save_app_event(
            source_event_id=event_data["source_event_id"],
            title=event_data["title"],
            category=event_data.get("category"),
            start=event_data["start"],
            end=event_data["end"],
            location=event_data.get("location"),
            url=event_data.get("url"),
            description=event_data.get("description"),
        )
        
        # 一時保存データを削除
        db.delete_pending_email_event(token)
        
        flash(f"「{event_data['title']}」をカレンダーに追加しました。", "success")
        return redirect(url_for("calendar_view"))
    except Exception as e:
        print(f"イベント追加エラー: {e}")
        flash("イベントの追加に失敗しました。", "error")
        return redirect(url_for("index"))


@app.route("/events/email/select/<token>/add", methods=["POST"])
def add_selected_events_from_email(token: str):
    """メール内のフォームから選択したイベントをカレンダーに追加"""
    events_data = db.get_pending_email_events_bulk(token)
    
    if not events_data:
        flash("リンクが無効または期限切れです。", "error")
        return redirect(url_for("index"))
    
    # フォームから選択されたイベントIDを取得
    selected_ids = request.form.getlist("selected_events")
    
    if not selected_ids:
        flash("追加するイベントを選択してください。", "warning")
        # 選択ページにリダイレクト（後で実装する場合は）
        return redirect(url_for("index"))
    
    # 選択されたイベントのみをフィルタリング
    selected_events_data = [
        event_data for event_data in events_data 
        if event_data["source_event_id"] in selected_ids
    ]
    
    now = datetime.now(timezone.utc)
    inserted = 0
    skipped_past = 0
    skipped_existing = 0
    
    # 選択されたイベントをカレンダーに保存
    for event_data in selected_events_data:
        # 既に保存されているかチェック
        if db.is_app_event_saved(event_data["source_event_id"]):
            skipped_existing += 1
            continue
        
        # 過去のイベントはスキップ
        try:
            end_dt = datetime.fromisoformat(event_data["end"])
            if end_dt.tzinfo is None:
                end_dt = end_dt.replace(tzinfo=timezone.utc)
            if end_dt < now:
                skipped_past += 1
                continue
        except Exception:
            pass
        
        # イベントをカレンダーに保存
        try:
            db.save_app_event(
                source_event_id=event_data["source_event_id"],
                title=event_data["title"],
                category=event_data.get("category"),
                start=event_data["start"],
                end=event_data["end"],
                location=event_data.get("location"),
                url=event_data.get("url"),
                description=event_data.get("description"),
            )
            inserted += 1
        except Exception as e:
            print(f"イベント追加エラー (event_id={event_data['source_event_id']}): {e}")
    
    # 結果メッセージ
    if inserted == 0:
        if skipped_existing > 0:
            flash(f"選択したイベントは既にカレンダーに追加されています。", "info")
        elif skipped_past > 0:
            flash(f"選択したイベントは過去のイベントのためスキップされました。", "info")
        else:
            flash("追加できるイベントがありませんでした。", "info")
    else:
        message = f"{inserted}件のイベントをカレンダーに追加しました。"
        if skipped_past > 0:
            message += f"（過去イベント {skipped_past}件はスキップ）"
        if skipped_existing > 0:
            message += f"（既存イベント {skipped_existing}件はスキップ）"
        flash(message, "success")
    
    return redirect(url_for("calendar_view"))


@app.route("/events/email/bulk/<token>/add", methods=["GET"])
def add_events_bulk_from_email(token: str):
    """メール内のリンクから複数のイベントを一括でカレンダーに追加"""
    events_data = db.get_pending_email_events_bulk(token)
    
    if not events_data:
        flash("リンクが無効または期限切れです。", "error")
        return redirect(url_for("index"))
    
    now = datetime.now(timezone.utc)
    inserted = 0
    skipped_past = 0
    skipped_existing = 0
    
    # 各イベントをカレンダーに保存
    for event_data in events_data:
        # 既に保存されているかチェック
        if db.is_app_event_saved(event_data["source_event_id"]):
            skipped_existing += 1
            continue
        
        # 過去のイベントはスキップ
        try:
            end_dt = datetime.fromisoformat(event_data["end"])
            if end_dt.tzinfo is None:
                end_dt = end_dt.replace(tzinfo=timezone.utc)
            if end_dt < now:
                skipped_past += 1
                continue
        except Exception:
            pass
        
        # イベントをカレンダーに保存
        try:
            db.save_app_event(
                source_event_id=event_data["source_event_id"],
                title=event_data["title"],
                category=event_data.get("category"),
                start=event_data["start"],
                end=event_data["end"],
                location=event_data.get("location"),
                url=event_data.get("url"),
                description=event_data.get("description"),
            )
            inserted += 1
        except Exception as e:
            print(f"イベント追加エラー (event_id={event_data['source_event_id']}): {e}")
    
    # 一時保存データを削除
    db.delete_pending_email_event(token)
    
    # 結果メッセージ
    if inserted == 0:
        if skipped_existing > 0:
            flash(f"すべてのイベントは既にカレンダーに追加されています。", "info")
        elif skipped_past > 0:
            flash(f"すべてのイベントは過去のイベントのためスキップされました。", "info")
        else:
            flash("追加できるイベントがありませんでした。", "info")
    else:
        message = f"{inserted}件のイベントをカレンダーに追加しました。"
        if skipped_past > 0:
            message += f"（過去イベント {skipped_past}件はスキップ）"
        if skipped_existing > 0:
            message += f"（既存イベント {skipped_existing}件はスキップ）"
        flash(message, "success")
    
    return redirect(url_for("calendar_view"))


@app.route("/events/<event_id>/share", methods=["POST"])
def create_share_link(event_id: str):
    """共有URLを生成"""
    expires_days = request.form.get("expires_days")
    expires = int(expires_days) if expires_days and expires_days.isdigit() else None
    
    token = db.create_share_token(event_id, expires_days=expires)
    share_url = url_for("view_shared_event", token=token, _external=True)
    flash(f"共有URLを生成しました: {share_url}", "success")
    return redirect(request.referrer or url_for("calendar_view"))


@app.route("/share/<token>")
def view_shared_event(token: str):
    """共有URLでイベントを表示"""
    token_info = db.get_share_token_info(token)
    if not token_info:
        flash("共有リンクが無効または期限切れです。", "error")
        return redirect(url_for("index"))
    
    # イベント情報を取得
    app_events = db.get_app_events(include_archived=True)
    event_data = next((e for e in app_events if e["source_event_id"] == token_info["source_event_id"]), None)
    
    if not event_data:
        flash("イベントが見つかりませんでした。", "error")
        return redirect(url_for("index"))
    
    from datetime import datetime as dt
    start_dt = dt.fromisoformat(event_data["start"])
    end_dt = dt.fromisoformat(event_data["end"])
    if start_dt.tzinfo is None:
        from datetime import timezone
        start_dt = start_dt.replace(tzinfo=timezone.utc)
    if end_dt.tzinfo is None:
        from datetime import timezone
        end_dt = end_dt.replace(tzinfo=timezone.utc)
    
    event = Event(
        source_id=event_data["source_event_id"],
        title=event_data["title"],
        start=start_dt,
        end=end_dt,
        category=event_data.get("category"),
        location=event_data.get("location"),
        url=event_data.get("url"),
        description=event_data.get("description"),
    )
    
    return render_template("shared_event.html", event=event, token=token)


@app.route("/events/<event_id>/memo", methods=["POST"])
def save_event_memo(event_id: str):
    """イベントのメモを保存"""
    memo_text = request.form.get("memo_text", "").strip()
    if memo_text:
        db.save_event_memo(event_id, memo_text)
        flash("メモを保存しました。", "success")
    else:
        db.delete_event_memo(event_id)
        flash("メモを削除しました。", "success")
    return redirect(request.referrer or url_for("calendar_view"))


@app.route("/api/events/<event_id>/memo", methods=["GET"])
def get_event_memo_api(event_id: str):
    """イベントのメモを取得（API）"""
    memo = db.get_event_memo(event_id)
    return {"memo": memo or ""}


@app.route("/dashboard")
def dashboard():
    """ダッシュボードページ"""
    from datetime import datetime as dt
    
    today_events_data = db.get_today_events()
    week_summary = db.get_week_summary()
    upcoming_events_data = db.get_upcoming_important_events()
    
    # Eventオブジェクトに変換
    today_events: List[Event] = []
    for event_data in today_events_data:
        try:
            start_dt = dt.fromisoformat(event_data["start"])
            end_dt = dt.fromisoformat(event_data["end"])
            if start_dt.tzinfo is None:
                from datetime import timezone
                start_dt = start_dt.replace(tzinfo=timezone.utc)
            if end_dt.tzinfo is None:
                from datetime import timezone
                end_dt = end_dt.replace(tzinfo=timezone.utc)
            
            event = Event(
                source_id=event_data["source_event_id"],
                title=event_data["title"],
                start=start_dt,
                end=end_dt,
                category=event_data.get("category"),
                location=event_data.get("location"),
                url=event_data.get("url"),
                description=event_data.get("description"),
            )
            today_events.append(event)
        except (ValueError, KeyError):
            continue
    
    upcoming_events: List[Event] = []
    for event_data in upcoming_events_data:
        try:
            start_dt = dt.fromisoformat(event_data["start"])
            end_dt = dt.fromisoformat(event_data["end"])
            if start_dt.tzinfo is None:
                from datetime import timezone
                start_dt = start_dt.replace(tzinfo=timezone.utc)
            if end_dt.tzinfo is None:
                from datetime import timezone
                end_dt = end_dt.replace(tzinfo=timezone.utc)
            
            event = Event(
                source_id=event_data["source_event_id"],
                title=event_data["title"],
                start=start_dt,
                end=end_dt,
                category=event_data.get("category"),
                location=event_data.get("location"),
                url=event_data.get("url"),
                description=event_data.get("description"),
            )
            upcoming_events.append(event)
        except (ValueError, KeyError):
            continue
    
    return render_template(
        "dashboard.html",
        today_events=today_events,
        week_summary=week_summary,
        upcoming_events=upcoming_events,
    )


@app.route("/global-memos", methods=["GET", "POST"])
def global_memos():
    """全体メモの管理"""
    if request.method == "POST":
        action = request.form.get("action")
        if action == "create":
            memo_text = request.form.get("memo_text", "").strip()
            if memo_text:
                db.save_global_memo(memo_text)
                flash("メモを追加しました。", "success")
        elif action == "update":
            memo_id = request.form.get("memo_id")
            memo_text = request.form.get("memo_text", "").strip()
            if memo_id and memo_text:
                db.update_global_memo(int(memo_id), memo_text)
                flash("メモを更新しました。", "success")
        elif action == "delete":
            memo_id = request.form.get("memo_id")
            if memo_id:
                db.delete_global_memo(int(memo_id))
                flash("メモを削除しました。", "success")
        return redirect(url_for("global_memos"))
    
    memos = db.get_global_memos()
    return render_template("global_memos.html", memos=memos)


@app.route("/sources/<int:source_id>/auto-fetch", methods=["POST"])
def update_auto_fetch_settings(source_id: int):
    """定期取得設定を更新"""
    source = db.get_event_source(source_id)
    if not source:
        flash("指定されたイベントソースが見つかりませんでした。", "error")
        return redirect(url_for("index"))
    
    interval_minutes = request.form.get("interval_minutes")
    enabled = request.form.get("enabled") == "1"
    
    if interval_minutes:
        try:
            interval = int(interval_minutes)
            # 1時間（60分）〜1週間（10080分）の範囲に制限
            if interval < 60:
                interval = 60
            elif interval > 10080:
                interval = 10080
            db.update_event_source_auto_fetch(source_id, interval, enabled)
            flash(f"「{source['oshi_name']}」の定期取得設定を更新しました。", "success")
        except ValueError:
            flash("取得間隔は数値で入力してください。", "error")
    else:
        db.update_event_source_auto_fetch(source_id, 1440, enabled)
        flash(f"「{source['oshi_name']}」の定期取得設定を更新しました。", "success")
    
    return redirect(url_for("index"))


@app.route("/notifications/settings", methods=["GET", "POST"])
def notification_settings():
    """通知設定ページ"""
    if request.method == "POST":
        # テストメール送信
        if request.form.get("action") == "test_email":
            test_email = request.form.get("test_email", "").strip()
            if not test_email:
                flash("テストメールの送信先を入力してください。", "error")
                return redirect(url_for("notification_settings"))
            
            from app.notifications import send_email_notification
            success = send_email_notification(
                subject="推しカレ - メール通知テスト",
                body="""これはメール通知のテストです。

メール通知機能が正常に動作しています。

このメールが届いたということは、メール設定が正しく行われています。
""",
                recipient=test_email,
            )
            
            if success:
                flash(f"テストメールを {test_email} に送信しました。", "success")
            else:
                flash("テストメールの送信に失敗しました。環境変数の設定を確認してください。", "error")
            return redirect(url_for("notification_settings"))
        
        # 通常の設定更新
        try:
            notify_new_events = request.form.get("notify_new_events") == "1"
            notify_reminders = request.form.get("notify_reminders") == "1"
            reminder_hours = int(request.form.get("reminder_hours", "24"))
            email_enabled = request.form.get("email_enabled") == "1"
            email_address = request.form.get("email_address", "").strip()
            custom_message_template = request.form.get("custom_message_template", "").strip()
            scheduled_notification_time = request.form.get("scheduled_notification_time", "").strip()
            
            # メール通知が有効な場合、メールアドレスが必須
            if email_enabled and not email_address:
                flash("メール通知を有効にする場合は、メールアドレスを入力してください。", "error")
                return redirect(url_for("notification_settings"))
            
            db.update_notification_settings(
                notify_new_events=notify_new_events,
                notify_reminders=notify_reminders,
                reminder_hours=reminder_hours,
                email_enabled=email_enabled,
                email_address=email_address if email_enabled else None,
                custom_message_template=custom_message_template if custom_message_template else None,
                scheduled_notification_time=scheduled_notification_time if scheduled_notification_time else None,
            )
            flash("通知設定を更新しました。", "success")
        except Exception as e:
            print(f"通知設定の保存エラー: {e}")
            flash(f"通知設定の保存に失敗しました: {str(e)}", "error")
        
        return redirect(url_for("notification_settings"))
    
    settings = db.get_notification_settings()
    return render_template("notification_settings.html", settings=settings)


@app.route("/api/notifications", methods=["GET"])
def get_notifications_api():
    """通知一覧を取得（JSON API）"""
    unread_only = request.args.get("unread_only", "false").lower() == "true"
    notifications = db.get_notifications(unread_only=unread_only, limit=50)
    return {
        "notifications": notifications,
        "unread_count": len([n for n in notifications if not n.get("read_at")]),
    }


@app.route("/api/notifications/<int:notification_id>/read", methods=["POST"])
def mark_notification_read_api(notification_id: int):
    """通知を既読にする"""
    db.mark_notification_read(notification_id)
    return {"success": True}


@app.route("/stats")
def stats():
    """統計・分析ページ"""
    from collections import Counter, defaultdict
    from datetime import datetime, timedelta
    
    synced_events = db.get_synced_events()
    event_sources = db.get_event_sources()
    preview_data = session.get("preview_events", {})
    preview_events = preview_data.get("events", [])
    
    # カテゴリ別の集計
    category_counts = Counter()
    for event in preview_events:
        category = event.get("category") or "その他"
        category_counts[category] += 1
    
    # 推し別の集計
    oshi_counts = Counter()
    for source in event_sources:
        oshi_counts[source["oshi_name"]] += 1
    
    # 月別の集計（簡易実装：現在のプレビューイベントのみ）
    monthly_counts = defaultdict(int)
    for event in preview_events:
        try:
            start_str = event.get("start", "")
            if start_str:
                dt = datetime.fromisoformat(start_str.replace("Z", "+00:00"))
                month_key = dt.strftime("%Y-%m")
                monthly_counts[month_key] += 1
        except (ValueError, AttributeError):
            pass
    
    # 同期済みイベントの割合
    total_events = len(preview_events)
    synced_count = len([e for e in preview_events if e.get("source_id") in synced_events])
    sync_rate = (synced_count / total_events * 100) if total_events > 0 else 0
    
    return render_template(
        "stats.html",
        category_counts=dict(category_counts),
        oshi_counts=dict(oshi_counts),
        monthly_counts=dict(monthly_counts),
        total_events=total_events,
        synced_count=synced_count,
        sync_rate=round(sync_rate, 1),
        event_sources_count=len(event_sources),
    )


@app.route("/events/<event_id>/edit", methods=["GET", "POST"])
def edit_event(event_id: str):
    """イベント編集"""
    preview_data = session.get("preview_events")
    if not preview_data:
        flash("イベントが見つかりません。", "error")
        return redirect(url_for("index"))
    
    events_map = {
        item["source_id"]: item for item in preview_data.get("events", [])
    }
    
    if event_id not in events_map:
        flash("イベントが見つかりません。", "error")
        return redirect(url_for("index"))
    
    event_data = events_map[event_id]
    edited_event = db.get_edited_event(event_id)
    
    if request.method == "POST":
        # 編集内容を保存
        title = request.form.get("title", "").strip()
        category = request.form.get("category", "").strip()
        start_str = request.form.get("start", "").strip()
        end_str = request.form.get("end", "").strip()
        location = request.form.get("location", "").strip()
        description = request.form.get("description", "").strip()
        
        if not title:
            flash("タイトルは必須です。", "error")
            return redirect(url_for("edit_event", event_id=event_id))
        
        db.save_edited_event(
            source_event_id=event_id,
            title=title,
            category=category or None,
            start=start_str or None,
            end=end_str or None,
            location=location or None,
            description=description or None,
        )
        
        # セッションのイベントデータも更新
        for item in preview_data["events"]:
            if item["source_id"] == event_id:
                item["title"] = title
                item["category"] = category or item.get("category", "")
                if start_str:
                    item["start"] = start_str
                if end_str:
                    item["end"] = end_str
                item["location"] = location or ""
                item["description"] = description or ""
                break
        
        session["preview_events"] = preview_data
        flash("イベントを編集しました。", "success")
        return redirect(url_for("index"))
    
    # 編集済みデータがあればそれを使用、なければ元のデータ
    display_data = edited_event if edited_event else event_data
    
    # 日時をdatetime-local形式に変換
    def format_datetime_local(dt_str):
        if not dt_str:
            return ""
        try:
            if isinstance(dt_str, str):
                dt = datetime.fromisoformat(dt_str.replace("Z", "+00:00"))
            else:
                dt = dt_str
            # ローカルタイムゾーンに変換してからフォーマット
            local_dt = dt.astimezone()
            return local_dt.strftime("%Y-%m-%dT%H:%M")
        except (ValueError, AttributeError):
            return ""
    
    # 表示用データにフォーマット済み日時を追加
    display_data = dict(display_data)
    display_data["start_formatted"] = format_datetime_local(display_data.get("start") or event_data.get("start"))
    display_data["end_formatted"] = format_datetime_local(display_data.get("end") or event_data.get("end"))
    
    return render_template("edit_event.html", event=display_data, event_id=event_id)


@app.route("/events/<event_id>/delete-edit", methods=["POST"])
def delete_event_edit(event_id: str):
    """イベント編集を削除（元に戻す）"""
    db.delete_edited_event(event_id)
    
    # セッションからも削除
    preview_data = session.get("preview_events")
    if preview_data:
        for item in preview_data.get("events", []):
            if item["source_id"] == event_id:
                # 元のデータに戻す（簡易実装）
                pass
    
    flash("編集を元に戻しました。", "success")
    return redirect(url_for("index"))
