"""通知機能"""
import os
from datetime import datetime, timedelta, timezone
from typing import List, Optional

from flask import url_for
from flask_mail import Mail, Message

from app import app, db
from app.event_source import Event

mail = Mail()


def init_mail(app):
    """メール機能を初期化"""
    app.config["MAIL_SERVER"] = os.environ.get("MAIL_SERVER", "smtp.gmail.com")
    app.config["MAIL_PORT"] = int(os.environ.get("MAIL_PORT", "587"))
    app.config["MAIL_USE_TLS"] = os.environ.get("MAIL_USE_TLS", "true").lower() == "true"
    app.config["MAIL_USERNAME"] = os.environ.get("MAIL_USERNAME")
    app.config["MAIL_PASSWORD"] = os.environ.get("MAIL_PASSWORD")
    app.config["MAIL_DEFAULT_SENDER"] = os.environ.get("MAIL_DEFAULT_SENDER")
    
    mail.init_app(app)


def send_browser_notification(title: str, body: str, icon: Optional[str] = None):
    """ブラウザ通知を送信（クライアント側で実装）"""
    # 実際の実装はクライアント側のJavaScriptで行う
    pass


def send_email_notification(subject: str, body: str, recipient: str, html_body: Optional[str] = None):
    """メール通知を送信"""
    with app.app_context():
        if not app.config.get("MAIL_USERNAME"):
            print(f"メール送信スキップ: MAIL_USERNAMEが設定されていません")
            return False
        
        try:
            msg = Message(
                subject=subject,
                recipients=[recipient],
                body=body,
                html=html_body,
                sender=app.config.get("MAIL_DEFAULT_SENDER") or app.config.get("MAIL_USERNAME"),
            )
            mail.send(msg)
            print(f"メール送信成功: {recipient} に「{subject}」を送信しました")
            return True
        except Exception as e:
            import traceback
            print(f"メール送信エラー ({recipient}): {e}")
            print(f"エラー詳細: {traceback.format_exc()}")
            return False


def format_custom_notification(template: str, event: Event) -> str:
    """カスタム通知メッセージをフォーマット"""
    if not template:
        return None
    
    message = template
    message = message.replace("{{title}}", event.title)
    message = message.replace("{{category}}", event.category or "その他")
    message = message.replace("{{start}}", event.start.strftime('%Y年%m月%d日 %H:%M'))
    message = message.replace("{{end}}", event.end.strftime('%H:%M'))
    message = message.replace("{{location}}", event.location or "未定")
    message = message.replace("{{description}}", event.description or "")
    message = message.replace("{{url}}", event.url or "")
    return message


def send_new_event_notifications(event_source_id: int, events: List[Event]):
    """新着イベントの通知を送信（1つのメールですべてのイベントを追加可能）"""
    settings = db.get_notification_settings()
    
    if not settings.get("notify_new_events", True):
        return
    
    if not events:
        return
    
    # 通知履歴に記録（ブラウザ通知はクライアント側でポーリングして取得）
    for event in events:
        db.create_notification(event_source_id, event.source_id, "new_event")
    
    # メール通知
    if settings.get("email_enabled") and settings.get("email_address"):
        # 複数のイベントを一時保存してトークンを生成（一括用と個別用）
        bulk_token, individual_tokens = db.save_pending_email_events_bulk(events, event_source_id, expires_days=7)
        
        # メール内の追加URLを生成
        try:
            with app.app_context():
                # SERVER_NAMEが設定されている場合はurl_forを使用
                if app.config.get("SERVER_NAME"):
                    bulk_add_url = url_for("add_events_bulk_from_email", token=bulk_token, _external=True)
                else:
                    # 環境変数から取得、またはデフォルト値を使用
                    base_url = os.environ.get("BASE_URL", "http://localhost:5001")
                    bulk_add_url = f"{base_url.rstrip('/')}/events/email/bulk/{bulk_token}/add"
        except Exception:
            # url_forが失敗した場合、環境変数から直接構築
            base_url = os.environ.get("BASE_URL", "http://localhost:5001")
            bulk_add_url = f"{base_url.rstrip('/')}/events/email/bulk/{bulk_token}/add"
        
        # 個別追加用URLを生成
        base_url = os.environ.get("BASE_URL", "http://localhost:5001")
        individual_add_urls = {}
        for event in events:
            if event.source_id in individual_tokens:
                try:
                    with app.app_context():
                        if app.config.get("SERVER_NAME"):
                            individual_add_urls[event.source_id] = url_for("add_event_from_email", token=individual_tokens[event.source_id], _external=True)
                        else:
                            individual_add_urls[event.source_id] = f"{base_url.rstrip('/')}/events/email/{individual_tokens[event.source_id]}/add"
                except Exception:
                    individual_add_urls[event.source_id] = f"{base_url.rstrip('/')}/events/email/{individual_tokens[event.source_id]}/add"
        
        # 件名
        if len(events) == 1:
            subject = f"新着イベント: {events[0].title}"
        else:
            subject = f"新着イベント: {len(events)}件"
        
        # テキスト形式のメール本文
        body_text = f"新着イベントが{len(events)}件検出されました。\n\n"
        for i, event in enumerate(events, 1):
            body_text += f"{i}. {event.title}\n"
            body_text += f"   カテゴリ: {event.category or 'その他'}\n"
            body_text += f"   日時: {event.start.strftime('%Y年%m月%d日 %H:%M')} 〜 {event.end.strftime('%H:%M')}\n"
            if event.location:
                body_text += f"   場所: {event.location}\n"
            if event.url:
                body_text += f"   詳細ページ: {event.url}\n"
            body_text += "\n"
        
        # フォーム送信用URLを生成
        try:
            with app.app_context():
                if app.config.get("SERVER_NAME"):
                    form_action_url = url_for("add_selected_events_from_email", token=bulk_token, _external=True)
                else:
                    base_url = os.environ.get("BASE_URL", "http://localhost:5001")
                    form_action_url = f"{base_url.rstrip('/')}/events/email/select/{bulk_token}/add"
        except Exception:
            base_url = os.environ.get("BASE_URL", "http://localhost:5001")
            form_action_url = f"{base_url.rstrip('/')}/events/email/select/{bulk_token}/add"
        
        # HTML形式のメール本文を作成
        html_body = f"""
<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
</head>
<body style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; line-height: 1.6; color: #333;">
    <div style="max-width: 600px; margin: 0 auto; padding: 20px;">
        <h2 style="color: #6366f1; border-bottom: 2px solid #6366f1; padding-bottom: 10px;">
            新着イベントが{len(events)}件検出されました
        </h2>
        
        <form method="POST" action="{form_action_url}" style="margin: 0;">
"""
        for i, event in enumerate(events, 1):
            html_body += f"""
        <div style="background: #f8fafc; padding: 20px; border-radius: 8px; margin: 20px 0; border-left: 4px solid #6366f1;">
            <label style="display: flex; align-items: flex-start; cursor: pointer; margin: 0;">
                <input type="checkbox" name="selected_events" value="{event.source_id}" 
                       style="margin-right: 12px; margin-top: 4px; width: 18px; height: 18px; cursor: pointer; flex-shrink: 0;">
                <div style="flex: 1;">
                    <h3 style="margin-top: 0; color: #1e293b;">{i}. {event.title}</h3>
                    
                    <table style="width: 100%; border-collapse: collapse;">
                        <tr>
                            <td style="padding: 8px 0; color: #64748b; width: 100px;">カテゴリ:</td>
                            <td style="padding: 8px 0;"><strong>{event.category or 'その他'}</strong></td>
                        </tr>
                        <tr>
                            <td style="padding: 8px 0; color: #64748b;">日時:</td>
                            <td style="padding: 8px 0;">{event.start.strftime('%Y年%m月%d日 %H:%M')} 〜 {event.end.strftime('%H:%M')}</td>
                        </tr>
"""
            if event.location:
                html_body += f"""
                        <tr>
                            <td style="padding: 8px 0; color: #64748b;">場所:</td>
                            <td style="padding: 8px 0;">{event.location}</td>
                        </tr>
"""
            html_body += """
                    </table>
"""
            if event.description:
                html_body += f"""
                    <div style="margin-top: 16px; padding-top: 16px; border-top: 1px solid #e2e8f0;">
                        <p style="color: #64748b; margin: 0 0 8px 0;">詳細:</p>
                        <p style="margin: 0; white-space: pre-wrap;">{event.description}</p>
                    </div>
"""
            if event.url:
                html_body += f"""
                    <div style="margin-top: 16px;">
                        <a href="{event.url}" style="color: #6366f1; text-decoration: none;" onclick="event.stopPropagation();">詳細ページを開く →</a>
                    </div>
"""
            # 個別追加ボタン（フォーム外に配置）
            if event.source_id in individual_add_urls:
                html_body += f"""
                    <div style="text-align: center; margin-top: 16px;">
                        <a href="{individual_add_urls[event.source_id]}" 
                           style="display: inline-block; background: linear-gradient(135deg, #10b981, #059669); 
                                  color: white; padding: 10px 20px; text-decoration: none; 
                                  border-radius: 6px; font-weight: 500; font-size: 14px;"
                           onclick="event.stopPropagation();">
                            📅 このイベントを追加
                        </a>
                    </div>
"""
            html_body += """
                </div>
            </label>
        </div>
"""
        
        html_body += f"""
            <div style="margin: 30px 0; padding: 20px; background: #f1f5f9; border-radius: 8px;">
                <div style="margin-bottom: 16px;">
                    <label style="display: flex; align-items: center; cursor: pointer; font-weight: 500;">
                        <input type="checkbox" id="selectAll" 
                               style="margin-right: 8px; width: 18px; height: 18px; cursor: pointer;"
                               onchange="document.querySelectorAll('input[name=\\'selected_events\\']').forEach(cb => cb.checked = this.checked);">
                        <span>すべて選択</span>
                    </label>
                </div>
                <button type="submit" 
                        style="width: 100%; background: linear-gradient(135deg, #6366f1, #4f46e5); 
                               color: white; padding: 14px 28px; border: none; 
                               border-radius: 8px; font-weight: 600; font-size: 16px; cursor: pointer;">
                    📅 選択したイベントをカレンダーに追加
                </button>
            </div>
        </form>
        
        <div style="text-align: center; margin: 30px 0; padding-top: 20px; border-top: 2px solid #e2e8f0;">
            <a href="{bulk_add_url}" 
               style="display: inline-block; background: linear-gradient(135deg, #10b981, #059669); 
                      color: white; padding: 14px 28px; text-decoration: none; 
                      border-radius: 8px; font-weight: 600; font-size: 16px;">
                📅 すべてのイベントをカレンダーに追加
            </a>
        </div>
        
        <p style="color: #94a3b8; font-size: 12px; text-align: center; margin-top: 30px;">
            このリンクは7日間有効です
        </p>
    </div>
</body>
</html>
"""
        
        success = send_email_notification(subject, body_text, settings["email_address"], html_body=html_body)
        if not success:
            print(f"新着イベント通知のメール送信に失敗しました: {len(events)}件")
        else:
            print(f"新着イベント通知のメールを送信しました: {len(events)}件 → {settings['email_address']}")


def check_and_send_reminders():
    """イベント前日のリマインダーをチェックして送信"""
    settings = db.get_notification_settings()
    
    if not settings.get("notify_reminders", True):
        return
    
    reminder_hours = settings.get("reminder_hours", 24)
    
    # リマインダー対象のイベントを取得
    reminder_events = db.get_synced_events_for_reminder(reminder_hours)
    
    for event_data in reminder_events:
        # 既にリマインダーを送信済みかチェック（過去24時間以内に送信済みか）
        from datetime import timedelta
        cutoff_time = (datetime.utcnow() - timedelta(hours=24)).isoformat()
        
        existing_notifications = db.get_notifications(unread_only=False, limit=1000)
        already_notified = any(
            n["event_id"] == event_data["source_event_id"] 
            and n["type"] == "reminder"
            and n.get("sent_at", "") > cutoff_time
            for n in existing_notifications
        )
        
        if already_notified:
            continue
        
            # リマインダー通知を送信
        try:
            event_start = datetime.fromisoformat(event_data["event_start"])
            event_title = event_data.get("event_title", "イベント")
            
            # 通知履歴に記録（ブラウザ通知はクライアント側でポーリングして取得）
            db.create_notification(None, event_data["source_event_id"], "reminder")
            
            # メール通知
            if settings.get("email_enabled") and settings.get("email_address"):
                subject = f"リマインダー: {event_title}"
                body = f"""
イベントのリマインダーです。

イベント名: {event_title}
開始時刻: {event_start.strftime('%Y年%m月%d日 %H:%M')}
"""
                success = send_email_notification(subject, body, settings["email_address"])
                if not success:
                    print(f"リマインダーメール送信に失敗しました: {event_title}")
        except (ValueError, KeyError) as e:
            print(f"リマインダー送信エラー: {e}")


def get_unread_notification_count() -> int:
    """未読通知数を取得"""
    notifications = db.get_notifications(unread_only=True)
    return len(notifications)


def send_scheduled_notifications():
    """時間指定通知を送信"""
    settings = db.get_notification_settings()
    
    scheduled_time = settings.get("scheduled_notification_time")
    if not scheduled_time:
        return
    
    # 現在時刻と比較（HH:MM形式）
    from datetime import datetime
    now = datetime.now()
    current_time = now.strftime("%H:%M")
    
    if current_time != scheduled_time:
        return
    
    # 今日のイベントを取得
    today_events = db.get_today_events()
    
    if not today_events:
        return
    
    # 通知を送信
    custom_template = settings.get("custom_message_template")
    
    for event_data in today_events:
        # Eventオブジェクトに変換
        from app.event_source import Event
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
        
        # 通知履歴に記録
        db.create_notification(None, event.source_id, "scheduled")
        
        # メール通知
        if settings.get("email_enabled") and settings.get("email_address"):
            if custom_template:
                body = format_custom_notification(custom_template, event)
                subject = f"今日のイベント: {event.title}"
            else:
                subject = f"今日のイベント: {event.title}"
                body = f"""
今日のイベントです。

イベント名: {event.title}
カテゴリ: {event.category or 'その他'}
日時: {event.start.strftime('%H:%M')} 〜 {event.end.strftime('%H:%M')}
場所: {event.location or '未定'}
"""
            
            if body:
                success = send_email_notification(subject, body, settings["email_address"])
                if not success:
                    print(f"スケジュール通知メール送信に失敗しました: {event.title}")



