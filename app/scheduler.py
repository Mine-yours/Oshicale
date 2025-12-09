"""定期自動取得機能のスケジューラー"""
import hashlib
from datetime import datetime, timedelta, timezone
from typing import List, Set

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger

from app import db
from app.event_source import Event, EventSourceError, fetch_events, _fetch_markdown_with_firecrawl, _extract_events_with_gemini
from app.notifications import send_new_event_notifications, check_and_send_reminders, send_scheduled_notifications


def should_fetch(source: dict) -> bool:
    """取得が必要かどうかを判定"""
    if not source.get("enabled", True):
        return False
    
    interval_minutes = source.get("auto_fetch_interval", 1440)
    if interval_minutes <= 0:
        return False
    
    last_fetched = source.get("last_fetched_at")
    if not last_fetched:
        return True
    
    try:
        last_fetched_dt = datetime.fromisoformat(last_fetched)
        next_fetch_dt = last_fetched_dt + timedelta(minutes=interval_minutes)
        return datetime.now(timezone.utc) >= next_fetch_dt
    except (ValueError, TypeError):
        return True


def fetch_and_detect_new_events(source: dict) -> tuple[List[Event], List[Event]]:
    """イベントを取得し、新着イベントを検出（最適化版：内容が変更されていない場合はGemini APIをスキップ）
    
    Returns:
        tuple: (全イベントのリスト, 新着イベントのリスト)
    """
    try:
        # 1. Firecrawlでページ内容を取得
        markdown_content = _fetch_markdown_with_firecrawl(source["url"])
        
        # 2. 内容のハッシュを計算
        content_hash = hashlib.sha256(markdown_content.encode('utf-8')).hexdigest()
        
        # 3. 前回のハッシュと比較
        previous_hash = db.get_content_hash(source["id"])
        
        if previous_hash == content_hash:
            # 内容が変更されていない場合、前回のイベントIDを返す（新着なし）
            print(f"内容変更なし、Gemini APIをスキップ: {source['oshi_name']} (ID: {source['id']})")
            # 最終取得時刻を更新（次回のチェックでshould_fetchがFalseになるように）
            db.update_event_source_last_fetched(source["id"])
            previous_ids = db.get_fetched_event_ids(source["id"])
            # 空のリストを返す（新着イベントなし、全イベントもなし）
            return [], []
        
        # 4. 内容が変更された場合のみGemini APIを呼び出す
        print(f"内容変更を検出、Gemini APIで解析: {source['oshi_name']} (ID: {source['id']})")
        events, _ = _extract_events_with_gemini(markdown_content, source["url"])
        events = sorted(events, key=lambda e: e.start)
        
        # 5. 前回取得時のイベントIDを取得
        previous_ids: Set[str] = db.get_fetched_event_ids(source["id"])
        
        # 6. 新着イベントを検出（前回取得時に存在しなかったイベント）
        new_events = [event for event in events if event.source_id not in previous_ids]
        
        # 7. 今回取得したイベントIDを保存（すべてのイベント、新着だけでなく）
        current_ids = [event.source_id for event in events]
        db.save_fetched_event_ids(source["id"], current_ids)
        
        # 8. 内容ハッシュを保存
        db.save_content_hash(source["id"], content_hash)
        
        # 9. 最終取得時刻を更新
        db.update_event_source_last_fetched(source["id"])
        
        print(f"取得したイベント数: {len(events)}件、新着: {len(new_events)}件")
        return events, new_events
    except EventSourceError as e:
        print(f"イベント取得エラー (source_id={source['id']}): {e}")
        return [], []


def auto_fetch_job():
    """定期自動取得ジョブ"""
    sources = db.get_event_sources_for_auto_fetch()
    
    for source in sources:
        if not should_fetch(source):
            continue
        
        print(f"自動取得開始: {source['oshi_name']} (ID: {source['id']})")
        
        try:
            # 前回のハッシュとイベントIDを取得
            previous_hash = db.get_content_hash(source["id"])
            previous_ids = db.get_fetched_event_ids(source["id"])
            is_first_fetch = len(previous_ids) == 0
            
            # イベントを取得（全イベントと新着イベントの両方を取得）
            all_fetched_events, new_events = fetch_and_detect_new_events(source)
            
            # 取得履歴を記録
            all_events = db.get_app_events()
            events_count = len([e for e in all_events if e.get("source_event_id", "").startswith(f"{source['id']}-")])
            db.record_fetch_history(source["id"], events_count, success=True)
            
            # 内容が変更された場合、または初回取得の場合、app_eventsに存在しないものを保存
            if all_fetched_events or is_first_fetch:
                
                # 新着イベントのみをカレンダーに自動保存（メール通知が無効な場合のみ）
                # メール通知が有効な場合は、メール内のボタンで追加する方式
                settings = db.get_notification_settings()
                auto_save_enabled = not (settings.get("email_enabled") and settings.get("notify_new_events"))
                
                if auto_save_enabled:
                    # メール通知が無効な場合のみ自動保存
                    saved_count = 0
                    skipped_past = 0
                    skipped_existing = 0
                    
                    for event in all_fetched_events:
                        # 過去のイベントはスキップ
                        if event.end < datetime.now(timezone.utc):
                            skipped_past += 1
                            continue
                        
                        # 既にapp_eventsに存在するかチェック
                        if db.is_app_event_saved(event.source_id):
                            skipped_existing += 1
                            continue
                        
                        try:
                            db.save_app_event(
                                source_event_id=event.source_id,
                                title=event.title,
                                category=event.category,
                                start=event.start.isoformat(),
                                end=event.end.isoformat(),
                                location=event.location,
                                url=event.url,
                                description=event.description,
                            )
                            saved_count += 1
                        except Exception as save_error:
                            print(f"イベント保存エラー (event_id={event.source_id}): {save_error}")
                    
                    if saved_count > 0:
                        print(f"イベント {saved_count}件をカレンダーに自動保存: {source['oshi_name']}")
                    if skipped_past > 0:
                        print(f"過去イベント {skipped_past}件をスキップ: {source['oshi_name']}")
                    if skipped_existing > 0:
                        print(f"既存イベント {skipped_existing}件をスキップ: {source['oshi_name']}")
                else:
                    print(f"メール通知が有効なため、自動保存をスキップ（メール内のボタンで追加）: {source['oshi_name']}")
            
            # 新着イベントの通知を送信（メール内に追加ボタンを含む）
            if new_events:
                send_new_event_notifications(source["id"], new_events)
        except Exception as e:
            print(f"自動取得エラー (source_id={source['id']}): {e}")
            # エラーを記録
            db.record_fetch_error(source["id"], str(e), type(e).__name__)
            db.record_fetch_history(source["id"], 0, success=False)


def reminder_job():
    """リマインダーチェックジョブ"""
    try:
        check_and_send_reminders()
    except Exception as e:
        print(f"リマインダーチェックエラー: {e}")


def scheduled_notification_job():
    """時間指定通知ジョブ"""
    try:
        send_scheduled_notifications()
    except Exception as e:
        print(f"時間指定通知エラー: {e}")


def start_scheduler():
    """スケジューラーを開始"""
    scheduler = BackgroundScheduler()
    
    # 1分ごとに自動取得をチェック
    scheduler.add_job(
        auto_fetch_job,
        trigger=IntervalTrigger(minutes=1),
        id="auto_fetch_job",
        name="定期自動取得",
        replace_existing=True,
    )
    
    # 10分ごとにリマインダーをチェック
    scheduler.add_job(
        reminder_job,
        trigger=IntervalTrigger(minutes=10),
        id="reminder_job",
        name="リマインダーチェック",
        replace_existing=True,
    )
    
    # 1分ごとに時間指定通知をチェック
    scheduler.add_job(
        scheduled_notification_job,
        trigger=IntervalTrigger(minutes=1),
        id="scheduled_notification_job",
        name="時間指定通知",
        replace_existing=True,
    )
    
    scheduler.start()
    print("定期自動取得スケジューラーを開始しました")
    return scheduler

