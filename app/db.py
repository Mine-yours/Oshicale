import os
import sqlite3
from datetime import datetime
from typing import Dict, List, Optional, Set, Tuple

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
DATABASE = os.path.join(BASE_DIR, "database.db")


def create_tables() -> None:
    os.makedirs(os.path.dirname(DATABASE), exist_ok=True)
    with sqlite3.connect(DATABASE) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS synced_events (
                source_event_id TEXT PRIMARY KEY,
                calendar_event_id TEXT NOT NULL,
                inserted_at TEXT NOT NULL
            );
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS event_sources (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                oshi_name TEXT NOT NULL,
                url TEXT NOT NULL,
                mode TEXT NOT NULL DEFAULT 'static',
                created_at TEXT NOT NULL
            );
            """
        )
        # 既存テーブルのカラム追加（エラーは無視）
        for column_sql in [
            "ALTER TABLE event_sources ADD COLUMN mode TEXT NOT NULL DEFAULT 'static'",
            "ALTER TABLE event_sources ADD COLUMN auto_fetch_interval INTEGER DEFAULT 1440",
            "ALTER TABLE event_sources ADD COLUMN last_fetched_at TEXT",
            "ALTER TABLE event_sources ADD COLUMN enabled INTEGER DEFAULT 1",
            "ALTER TABLE event_sources ADD COLUMN content_hash TEXT",
            "ALTER TABLE synced_events ADD COLUMN event_start TEXT",
            "ALTER TABLE synced_events ADD COLUMN event_end TEXT",
            "ALTER TABLE synced_events ADD COLUMN event_title TEXT",
            "ALTER TABLE app_events ADD COLUMN archived INTEGER DEFAULT 0",
            "ALTER TABLE app_events ADD COLUMN merged_from TEXT",
            "ALTER TABLE notification_settings ADD COLUMN custom_message_template TEXT",
            "ALTER TABLE notification_settings ADD COLUMN scheduled_notification_time TEXT",
        ]:
            try:
                conn.execute(column_sql)
            except sqlite3.OperationalError:
                pass
        
        # pending_email_eventsテーブルのUNIQUE制約を修正（既存テーブルの場合）
        try:
            # 既存のテーブルがあるかチェック
            cursor = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='pending_email_events'")
            if cursor.fetchone():
                # 既存のテーブルを再作成（UNIQUE制約を削除）
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS pending_email_events_new (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        token TEXT NOT NULL,
                        source_event_id TEXT NOT NULL,
                        title TEXT NOT NULL,
                        category TEXT,
                        start TEXT NOT NULL,
                        end TEXT NOT NULL,
                        location TEXT,
                        url TEXT,
                        description TEXT,
                        event_source_id INTEGER,
                        created_at TEXT NOT NULL,
                        expires_at TEXT,
                        UNIQUE(token, source_event_id)
                    );
                """)
                # データを移行
                conn.execute("""
                    INSERT INTO pending_email_events_new 
                    SELECT * FROM pending_email_events;
                """)
                # 古いテーブルを削除
                conn.execute("DROP TABLE pending_email_events;")
                # 新しいテーブルをリネーム
                conn.execute("ALTER TABLE pending_email_events_new RENAME TO pending_email_events;")
        except sqlite3.OperationalError as e:
            # エラーは無視（既に修正済みの可能性がある）
            pass

        # 新規テーブル作成
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS edited_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_event_id TEXT NOT NULL UNIQUE,
                title TEXT,
                category TEXT,
                start TEXT,
                end TEXT,
                location TEXT,
                description TEXT,
                edited_at TEXT NOT NULL
            );
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS notifications (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_source_id INTEGER,
                event_id TEXT,
                type TEXT NOT NULL,
                sent_at TEXT NOT NULL,
                read_at TEXT,
                FOREIGN KEY (event_source_id) REFERENCES event_sources(id)
            );
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS notification_settings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                notify_new_events INTEGER DEFAULT 1,
                notify_reminders INTEGER DEFAULT 1,
                reminder_hours INTEGER DEFAULT 24,
                email_enabled INTEGER DEFAULT 0,
                email_address TEXT
            );
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS fetched_event_ids (
                event_source_id INTEGER NOT NULL,
                event_id TEXT NOT NULL,
                fetched_at TEXT NOT NULL,
                PRIMARY KEY (event_source_id, event_id),
                FOREIGN KEY (event_source_id) REFERENCES event_sources(id)
            );
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS app_events (
                source_event_id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                category TEXT,
                start TEXT NOT NULL,
                end TEXT NOT NULL,
                location TEXT,
                url TEXT,
                description TEXT,
                saved_at TEXT NOT NULL,
                archived INTEGER DEFAULT 0,
                merged_from TEXT
            );
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS fetch_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_source_id INTEGER NOT NULL,
                fetched_at TEXT NOT NULL,
                events_count INTEGER DEFAULT 0,
                success INTEGER DEFAULT 1,
                FOREIGN KEY (event_source_id) REFERENCES event_sources(id)
            );
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS fetch_errors (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_source_id INTEGER,
                error_message TEXT NOT NULL,
                error_type TEXT,
                occurred_at TEXT NOT NULL,
                FOREIGN KEY (event_source_id) REFERENCES event_sources(id)
            );
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS event_memos (
                source_event_id TEXT PRIMARY KEY,
                memo_text TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS global_memos (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                memo_text TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS share_tokens (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                token TEXT UNIQUE NOT NULL,
                source_event_id TEXT NOT NULL,
                created_at TEXT NOT NULL,
                expires_at TEXT
            );
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS pending_email_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                token TEXT NOT NULL,
                source_event_id TEXT NOT NULL,
                title TEXT NOT NULL,
                category TEXT,
                start TEXT NOT NULL,
                end TEXT NOT NULL,
                location TEXT,
                url TEXT,
                description TEXT,
                event_source_id INTEGER,
                created_at TEXT NOT NULL,
                expires_at TEXT,
                UNIQUE(token, source_event_id)
            );
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS share_tokens (
                token TEXT PRIMARY KEY,
                source_event_id TEXT NOT NULL,
                created_at TEXT NOT NULL,
                expires_at TEXT
            );
            """
        )
        conn.commit()


def create_table() -> None:
    create_tables()


def get_synced_events() -> Dict[str, str]:
    with sqlite3.connect(DATABASE) as conn:
        cursor = conn.execute(
            "SELECT source_event_id, calendar_event_id FROM synced_events"
        )
        return {row[0]: row[1] for row in cursor.fetchall()}


def record_synced_event(source_event_id: str, calendar_event_id: str) -> None:
    with sqlite3.connect(DATABASE) as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO synced_events (source_event_id, calendar_event_id, inserted_at)
            VALUES (?, ?, ?)
            """,
            (source_event_id, calendar_event_id, datetime.utcnow().isoformat()),
        )
        conn.commit()


def add_event_source(oshi_name: str, url: str, mode: str = "static") -> None:
    with sqlite3.connect(DATABASE) as conn:
        conn.execute(
            """
            INSERT INTO event_sources (oshi_name, url, mode, created_at)
            VALUES (?, ?, ?, ?)
            """,
            (oshi_name, url, mode, datetime.utcnow().isoformat()),
        )
        conn.commit()


def get_event_sources() -> List[Dict[str, any]]:
    with sqlite3.connect(DATABASE) as conn:
        cursor = conn.execute(
            """
            SELECT id, oshi_name, url, mode, created_at, auto_fetch_interval, last_fetched_at, enabled
            FROM event_sources
            ORDER BY created_at DESC
            """
        )
        return [
            {
                "id": row[0],
                "oshi_name": row[1],
                "url": row[2],
                "mode": row[3],
                "created_at": row[4],
                "auto_fetch_interval": row[5] if len(row) > 5 else 1440,
                "last_fetched_at": row[6] if len(row) > 6 else None,
                "enabled": bool(row[7]) if len(row) > 7 else True,
            }
            for row in cursor.fetchall()
        ]


def get_event_source(source_id: int) -> Optional[Dict[str, any]]:
    with sqlite3.connect(DATABASE) as conn:
        cursor = conn.execute(
            """
            SELECT id, oshi_name, url, mode, created_at, auto_fetch_interval, last_fetched_at, enabled
            FROM event_sources
            WHERE id = ?
            """,
            (source_id,),
        )
        row = cursor.fetchone()
        if not row:
            return None
        return {
            "id": row[0],
            "oshi_name": row[1],
            "url": row[2],
            "mode": row[3],
            "created_at": row[4],
            "auto_fetch_interval": row[5] if len(row) > 5 else 1440,
            "last_fetched_at": row[6] if len(row) > 6 else None,
            "enabled": bool(row[7]) if len(row) > 7 else True,
        }


def delete_event_source(source_id: int) -> None:
    with sqlite3.connect(DATABASE) as conn:
        conn.execute("DELETE FROM event_sources WHERE id = ?", (source_id,))
        conn.commit()


def update_event_source_mode(source_id: int, mode: str) -> None:
    with sqlite3.connect(DATABASE) as conn:
        conn.execute(
            "UPDATE event_sources SET mode = ? WHERE id = ?",
            (mode, source_id),
        )
        conn.commit()


def update_event_source_auto_fetch(source_id: int, interval_minutes: int, enabled: bool = True) -> None:
    """定期取得の設定を更新"""
    with sqlite3.connect(DATABASE) as conn:
        conn.execute(
            "UPDATE event_sources SET auto_fetch_interval = ?, enabled = ? WHERE id = ?",
            (interval_minutes, 1 if enabled else 0, source_id),
        )
        conn.commit()


def update_event_source_last_fetched(source_id: int) -> None:
    """最終取得時刻を更新"""
    with sqlite3.connect(DATABASE) as conn:
        conn.execute(
            "UPDATE event_sources SET last_fetched_at = ? WHERE id = ?",
            (datetime.utcnow().isoformat(), source_id),
        )
        conn.commit()


def get_event_sources_for_auto_fetch() -> List[Dict[str, any]]:
    """定期取得が必要なイベントソースを取得"""
    with sqlite3.connect(DATABASE) as conn:
        cursor = conn.execute(
            """
            SELECT id, oshi_name, url, mode, auto_fetch_interval, last_fetched_at, enabled, content_hash
            FROM event_sources
            WHERE enabled = 1 AND auto_fetch_interval > 0
            """
        )
        return [
            {
                "id": row[0],
                "oshi_name": row[1],
                "url": row[2],
                "mode": row[3],
                "auto_fetch_interval": row[4],
                "last_fetched_at": row[5],
                "enabled": bool(row[6]) if len(row) > 6 else True,
                "content_hash": row[7] if len(row) > 7 else None,
            }
            for row in cursor.fetchall()
        ]


# 編集済みイベント関連
def save_edited_event(source_event_id: str, title: Optional[str] = None, category: Optional[str] = None,
                     start: Optional[str] = None, end: Optional[str] = None, location: Optional[str] = None,
                     description: Optional[str] = None) -> None:
    """編集済みイベントを保存"""
    with sqlite3.connect(DATABASE) as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO edited_events 
            (source_event_id, title, category, start, end, location, description, edited_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (source_event_id, title, category, start, end, location, description, datetime.utcnow().isoformat()),
        )
        conn.commit()


def get_edited_event(source_event_id: str) -> Optional[Dict[str, any]]:
    """編集済みイベントを取得"""
    with sqlite3.connect(DATABASE) as conn:
        cursor = conn.execute(
            "SELECT source_event_id, title, category, start, end, location, description, edited_at FROM edited_events WHERE source_event_id = ?",
            (source_event_id,),
        )
        row = cursor.fetchone()
        if not row:
            return None
        return {
            "source_event_id": row[0],
            "title": row[1],
            "category": row[2],
            "start": row[3],
            "end": row[4],
            "location": row[5],
            "description": row[6],
            "edited_at": row[7],
        }


def delete_edited_event(source_event_id: str) -> None:
    """編集済みイベントを削除"""
    with sqlite3.connect(DATABASE) as conn:
        conn.execute("DELETE FROM edited_events WHERE source_event_id = ?", (source_event_id,))
        conn.commit()


# 通知関連
def create_notification(event_source_id: Optional[int], event_id: str, notification_type: str) -> int:
    """通知を作成"""
    with sqlite3.connect(DATABASE) as conn:
        cursor = conn.execute(
            """
            INSERT INTO notifications (event_source_id, event_id, type, sent_at)
            VALUES (?, ?, ?, ?)
            """,
            (event_source_id, event_id, notification_type, datetime.utcnow().isoformat()),
        )
        conn.commit()
        return cursor.lastrowid


def get_notifications(unread_only: bool = False, limit: int = 50) -> List[Dict[str, any]]:
    """通知を取得"""
    with sqlite3.connect(DATABASE) as conn:
        query = "SELECT id, event_source_id, event_id, type, sent_at, read_at FROM notifications"
        if unread_only:
            query += " WHERE read_at IS NULL"
        query += " ORDER BY sent_at DESC LIMIT ?"
        cursor = conn.execute(query, (limit,))
        return [
            {
                "id": row[0],
                "event_source_id": row[1],
                "event_id": row[2],
                "type": row[3],
                "sent_at": row[4],
                "read_at": row[5],
            }
            for row in cursor.fetchall()
        ]


def mark_notification_read(notification_id: int) -> None:
    """通知を既読にする"""
    with sqlite3.connect(DATABASE) as conn:
        conn.execute(
            "UPDATE notifications SET read_at = ? WHERE id = ?",
            (datetime.utcnow().isoformat(), notification_id),
        )
        conn.commit()


# 通知設定関連
def get_notification_settings() -> Dict[str, any]:
    """通知設定を取得（シングルトン）"""
    with sqlite3.connect(DATABASE) as conn:
        cursor = conn.execute("SELECT id, notify_new_events, notify_reminders, reminder_hours, email_enabled, email_address, custom_message_template, scheduled_notification_time FROM notification_settings LIMIT 1")
        row = cursor.fetchone()
        if row:
            return {
                "id": row[0],
                "notify_new_events": bool(row[1]),
                "notify_reminders": bool(row[2]),
                "reminder_hours": row[3],
                "email_enabled": bool(row[4]),
                "email_address": row[5],
                "custom_message_template": row[6] if len(row) > 6 else None,
                "scheduled_notification_time": row[7] if len(row) > 7 else None,
            }
        # デフォルト設定を作成
        conn.execute(
            """
            INSERT INTO notification_settings (notify_new_events, notify_reminders, reminder_hours, email_enabled)
            VALUES (1, 1, 24, 0)
            """
        )
        conn.commit()
        return {
            "id": 1,
            "notify_new_events": True,
            "notify_reminders": True,
            "reminder_hours": 24,
            "email_enabled": False,
            "email_address": None,
            "custom_message_template": None,
            "scheduled_notification_time": None,
        }


def update_notification_settings(notify_new_events: bool = True, notify_reminders: bool = True,
                                 reminder_hours: int = 24, email_enabled: bool = False,
                                 email_address: Optional[str] = None,
                                 custom_message_template: Optional[str] = None,
                                 scheduled_notification_time: Optional[str] = None) -> None:
    """通知設定を更新"""
    with sqlite3.connect(DATABASE) as conn:
        # 設定が存在しない場合は作成
        cursor = conn.execute("SELECT id FROM notification_settings LIMIT 1")
        if not cursor.fetchone():
            conn.execute(
                """
                INSERT INTO notification_settings (notify_new_events, notify_reminders, reminder_hours, email_enabled, email_address, custom_message_template, scheduled_notification_time)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (1 if notify_new_events else 0, 1 if notify_reminders else 0, reminder_hours, 1 if email_enabled else 0, email_address, custom_message_template, scheduled_notification_time),
            )
        else:
            conn.execute(
                """
                UPDATE notification_settings SET 
                notify_new_events = ?, notify_reminders = ?, reminder_hours = ?, email_enabled = ?, email_address = ?, custom_message_template = ?, scheduled_notification_time = ?
                WHERE id = 1
                """,
                (1 if notify_new_events else 0, 1 if notify_reminders else 0, reminder_hours, 1 if email_enabled else 0, email_address, custom_message_template, scheduled_notification_time),
            )
        conn.commit()


# 前回取得時のイベントID管理
def save_fetched_event_ids(event_source_id: int, event_ids: List[str]) -> None:
    """取得したイベントIDを保存"""
    with sqlite3.connect(DATABASE) as conn:
        # 既存のIDを削除
        conn.execute("DELETE FROM fetched_event_ids WHERE event_source_id = ?", (event_source_id,))
        
        # 新しいIDを保存
        fetched_at = datetime.utcnow().isoformat()
        for event_id in event_ids:
            conn.execute(
                "INSERT OR REPLACE INTO fetched_event_ids (event_source_id, event_id, fetched_at) VALUES (?, ?, ?)",
                (event_source_id, event_id, fetched_at),
            )
        conn.commit()


def get_fetched_event_ids(event_source_id: int) -> Set[str]:
    """前回取得時のイベントIDを取得"""
    with sqlite3.connect(DATABASE) as conn:
        cursor = conn.execute(
            "SELECT event_id FROM fetched_event_ids WHERE event_source_id = ?",
            (event_source_id,),
        )
        return {row[0] for row in cursor.fetchall()}


def save_content_hash(event_source_id: int, content_hash: str) -> None:
    """ページ内容のハッシュを保存"""
    with sqlite3.connect(DATABASE) as conn:
        conn.execute(
            "UPDATE event_sources SET content_hash = ? WHERE id = ?",
            (content_hash, event_source_id),
        )
        conn.commit()


def get_content_hash(event_source_id: int) -> Optional[str]:
    """前回取得時のページ内容ハッシュを取得"""
    with sqlite3.connect(DATABASE) as conn:
        cursor = conn.execute(
            "SELECT content_hash FROM event_sources WHERE id = ?",
            (event_source_id,),
        )
        row = cursor.fetchone()
        return row[0] if row and row[0] else None


# 同期済みイベントの日時情報管理
def record_synced_event_with_datetime(source_event_id: str, calendar_event_id: str, 
                                      event_start: str, event_end: str, event_title: str) -> None:
    """同期済みイベントを日時情報と共に記録"""
    with sqlite3.connect(DATABASE) as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO synced_events 
            (source_event_id, calendar_event_id, inserted_at, event_start, event_end, event_title)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (source_event_id, calendar_event_id, datetime.utcnow().isoformat(), event_start, event_end, event_title),
        )
        conn.commit()


# アプリ上のカレンダー関連
def save_app_event(source_event_id: str, title: str, category: Optional[str], 
                   start: str, end: str, location: Optional[str] = None,
                   url: Optional[str] = None, description: Optional[str] = None) -> None:
    """アプリ上のカレンダーにイベントを保存"""
    with sqlite3.connect(DATABASE) as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO app_events 
            (source_event_id, title, category, start, end, location, url, description, saved_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (source_event_id, title, category, start, end, location, url, description, datetime.utcnow().isoformat()),
        )
        conn.commit()


def get_app_events(include_archived: bool = False) -> List[Dict[str, any]]:
    """アプリ上のカレンダーのイベントを取得"""
    with sqlite3.connect(DATABASE) as conn:
        if include_archived:
            cursor = conn.execute(
                """
                SELECT source_event_id, title, category, start, end, location, url, description, saved_at, archived
                FROM app_events
                ORDER BY start ASC
                """
            )
        else:
            cursor = conn.execute(
                """
                SELECT source_event_id, title, category, start, end, location, url, description, saved_at, archived
                FROM app_events
                WHERE archived = 0
                ORDER BY start ASC
                """
            )
        return [
            {
                "source_event_id": row[0],
                "title": row[1],
                "category": row[2],
                "start": row[3],
                "end": row[4],
                "location": row[5],
                "url": row[6],
                "description": row[7],
                "saved_at": row[8],
                "archived": bool(row[9]) if len(row) > 9 else False,
            }
            for row in cursor.fetchall()
        ]


def get_archived_events() -> List[Dict[str, any]]:
    """アーカイブ済みイベントを取得"""
    with sqlite3.connect(DATABASE) as conn:
        cursor = conn.execute(
            """
            SELECT source_event_id, title, category, start, end, location, url, description, saved_at
            FROM app_events
            WHERE archived = 1
            ORDER BY start ASC
            """
        )
        return [
            {
                "source_event_id": row[0],
                "title": row[1],
                "category": row[2],
                "start": row[3],
                "end": row[4],
                "location": row[5],
                "url": row[6],
                "description": row[7],
                "saved_at": row[8],
            }
            for row in cursor.fetchall()
        ]


def archive_app_event(source_event_id: str) -> None:
    """イベントをアーカイブ"""
    with sqlite3.connect(DATABASE) as conn:
        conn.execute(
            "UPDATE app_events SET archived = 1 WHERE source_event_id = ?",
            (source_event_id,),
        )
        conn.commit()


def unarchive_app_event(source_event_id: str) -> None:
    """アーカイブから復元"""
    with sqlite3.connect(DATABASE) as conn:
        conn.execute(
            "UPDATE app_events SET archived = 0 WHERE source_event_id = ?",
            (source_event_id,),
        )
        conn.commit()


def delete_app_event(source_event_id: str) -> None:
    """アプリ上のカレンダーからイベントを削除"""
    with sqlite3.connect(DATABASE) as conn:
        conn.execute("DELETE FROM app_events WHERE source_event_id = ?", (source_event_id,))
        conn.commit()


def update_app_event(source_event_id: str, title: str, category: Optional[str] = None,
                     start: Optional[str] = None, end: Optional[str] = None,
                     location: Optional[str] = None, url: Optional[str] = None,
                     description: Optional[str] = None) -> None:
    """アプリ上のカレンダーのイベントを更新"""
    with sqlite3.connect(DATABASE) as conn:
        # 既存のデータを取得
        cursor = conn.execute(
            "SELECT title, category, start, end, location, url, description FROM app_events WHERE source_event_id = ?",
            (source_event_id,),
        )
        existing = cursor.fetchone()
        if not existing:
            return
        
        # 更新する値（Noneの場合は既存の値を使用）
        update_title = title if title else existing[0]
        update_category = category if category is not None else existing[1]
        update_start = start if start else existing[2]
        update_end = end if end else existing[3]
        update_location = location if location is not None else existing[4]
        update_url = url if url is not None else existing[5]
        update_description = description if description is not None else existing[6]
        
        conn.execute(
            """
            UPDATE app_events 
            SET title = ?, category = ?, start = ?, end = ?, location = ?, url = ?, description = ?
            WHERE source_event_id = ?
            """,
            (update_title, update_category, update_start, update_end, update_location, update_url, update_description, source_event_id),
        )
        conn.commit()


def delete_app_events_bulk(source_event_ids: List[str]) -> int:
    """複数のアプリ上のカレンダーイベントを一括削除"""
    if not source_event_ids:
        return 0
    with sqlite3.connect(DATABASE) as conn:
        placeholders = ','.join(['?'] * len(source_event_ids))
        cursor = conn.execute(
            f"DELETE FROM app_events WHERE source_event_id IN ({placeholders})",
            source_event_ids,
        )
        conn.commit()
        return cursor.rowcount


def is_app_event_saved(source_event_id: str) -> bool:
    """イベントがアプリ上のカレンダーに保存されているかチェック"""
    with sqlite3.connect(DATABASE) as conn:
        cursor = conn.execute(
            "SELECT 1 FROM app_events WHERE source_event_id = ? LIMIT 1",
            (source_event_id,),
        )
        return cursor.fetchone() is not None


def clear_all_app_events() -> int:
    """すべてのアプリイベントを削除"""
    with sqlite3.connect(DATABASE) as conn:
        cursor = conn.execute("SELECT COUNT(*) FROM app_events")
        count = cursor.fetchone()[0]
        conn.execute("DELETE FROM app_events")
        conn.commit()
        return count


def clear_past_app_events() -> int:
    """過去のアプリイベントを削除"""
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()
    with sqlite3.connect(DATABASE) as conn:
        cursor = conn.execute(
            "SELECT COUNT(*) FROM app_events WHERE end < ?",
            (now,),
        )
        count = cursor.fetchone()[0]
        conn.execute("DELETE FROM app_events WHERE end < ?", (now,))
        conn.commit()
        return count


# 重複チェック機能
def check_duplicate_events(title: str, start: str) -> Optional[Dict[str, any]]:
    """タイトルと日時が同じイベントを検出"""
    with sqlite3.connect(DATABASE) as conn:
        cursor = conn.execute(
            """
            SELECT source_event_id, title, category, start, end, location, url, description, saved_at, merged_from
            FROM app_events
            WHERE title = ? AND start = ? AND archived = 0
            LIMIT 1
            """,
            (title, start),
        )
        row = cursor.fetchone()
        if not row:
            return None
        return {
            "source_event_id": row[0],
            "title": row[1],
            "category": row[2],
            "start": row[3],
            "end": row[4],
            "location": row[5],
            "url": row[6],
            "description": row[7],
            "saved_at": row[8],
            "merged_from": row[9],
        }


def merge_duplicate_events(existing_event_id: str, new_event_data: Dict[str, any]) -> None:
    """重複イベントを自動マージ（最新の情報を優先）"""
    # 既存のイベントを取得
    existing = check_duplicate_events(new_event_data["title"], new_event_data["start"])
    if not existing:
        return
    
    # マージ元のIDを記録（既存のmerged_fromがあれば保持、なければ既存のIDを設定）
    merged_from = existing.get("merged_from") or existing["source_event_id"]
    
    # 最新の情報で更新（新しいデータの方が詳細な場合があるため）
    with sqlite3.connect(DATABASE) as conn:
        conn.execute(
            """
            UPDATE app_events SET
                category = COALESCE(?, category),
                end = COALESCE(?, end),
                location = COALESCE(?, location),
                url = COALESCE(?, url),
                description = COALESCE(?, description),
                merged_from = ?
            WHERE source_event_id = ?
            """,
            (
                new_event_data.get("category"),
                new_event_data.get("end"),
                new_event_data.get("location"),
                new_event_data.get("url"),
                new_event_data.get("description"),
                merged_from,
                existing["source_event_id"],
            ),
        )
        conn.commit()


# 共有・メモ機能
def save_event_memo(source_event_id: str, memo_text: str) -> None:
    """イベントのメモを保存"""
    with sqlite3.connect(DATABASE) as conn:
        now = datetime.utcnow().isoformat()
        conn.execute(
            """
            INSERT OR REPLACE INTO event_memos 
            (source_event_id, memo_text, created_at, updated_at)
            VALUES (?, ?, COALESCE((SELECT created_at FROM event_memos WHERE source_event_id = ?), ?), ?)
            """,
            (source_event_id, memo_text, source_event_id, now, now),
        )
        conn.commit()


def get_event_memo(source_event_id: str) -> Optional[str]:
    """イベントのメモを取得"""
    with sqlite3.connect(DATABASE) as conn:
        cursor = conn.execute(
            "SELECT memo_text FROM event_memos WHERE source_event_id = ?",
            (source_event_id,),
        )
        row = cursor.fetchone()
        return row[0] if row else None


def delete_event_memo(source_event_id: str) -> None:
    """イベントのメモを削除"""
    with sqlite3.connect(DATABASE) as conn:
        conn.execute("DELETE FROM event_memos WHERE source_event_id = ?", (source_event_id,))
        conn.commit()


def save_global_memo(memo_text: str) -> int:
    """全体メモを保存（新規作成）"""
    with sqlite3.connect(DATABASE) as conn:
        now = datetime.utcnow().isoformat()
        cursor = conn.execute(
            "INSERT INTO global_memos (memo_text, created_at, updated_at) VALUES (?, ?, ?)",
            (memo_text, now, now),
        )
        conn.commit()
        return cursor.lastrowid


def get_global_memos() -> List[Dict[str, any]]:
    """全体メモを取得"""
    with sqlite3.connect(DATABASE) as conn:
        cursor = conn.execute(
            "SELECT id, memo_text, created_at, updated_at FROM global_memos ORDER BY created_at DESC"
        )
        return [
            {
                "id": row[0],
                "memo_text": row[1],
                "created_at": row[2],
                "updated_at": row[3],
            }
            for row in cursor.fetchall()
        ]


def update_global_memo(memo_id: int, memo_text: str) -> None:
    """全体メモを更新"""
    with sqlite3.connect(DATABASE) as conn:
        conn.execute(
            "UPDATE global_memos SET memo_text = ?, updated_at = ? WHERE id = ?",
            (memo_text, datetime.utcnow().isoformat(), memo_id),
        )
        conn.commit()


def delete_global_memo(memo_id: int) -> None:
    """全体メモを削除"""
    with sqlite3.connect(DATABASE) as conn:
        conn.execute("DELETE FROM global_memos WHERE id = ?", (memo_id,))
        conn.commit()


def create_share_token(source_event_id: str, expires_days: Optional[int] = None) -> str:
    """共有トークンを生成"""
    import secrets
    token = secrets.token_urlsafe(32)
    now = datetime.utcnow().isoformat()
    expires_at = None
    if expires_days:
        from datetime import timedelta
        expires_at = (datetime.utcnow() + timedelta(days=expires_days)).isoformat()
    
    with sqlite3.connect(DATABASE) as conn:
        conn.execute(
            "INSERT INTO share_tokens (token, source_event_id, created_at, expires_at) VALUES (?, ?, ?, ?)",
            (token, source_event_id, now, expires_at),
        )
        conn.commit()
    return token


def get_share_token_info(token: str) -> Optional[Dict[str, any]]:
    """共有トークンの情報を取得"""
    with sqlite3.connect(DATABASE) as conn:
        cursor = conn.execute(
            "SELECT source_event_id, created_at, expires_at FROM share_tokens WHERE token = ?",
            (token,),
        )
        row = cursor.fetchone()
        if not row:
            return None
        
        # 有効期限チェック
        if row[2]:
            from datetime import datetime
            expires_at = datetime.fromisoformat(row[2])
            if datetime.utcnow() > expires_at:
                return None
        
        return {
            "source_event_id": row[0],
            "created_at": row[1],
            "expires_at": row[2],
        }


def delete_share_token(token: str) -> None:
    """共有トークンを削除"""
    with sqlite3.connect(DATABASE) as conn:
        conn.execute("DELETE FROM share_tokens WHERE token = ?", (token,))
        conn.commit()


# メール通知用の一時イベント保存
def save_pending_email_event(event, event_source_id: int, expires_days: int = 7) -> str:
    """メール通知用のイベント情報を一時保存し、トークンを返す"""
    import secrets
    token = secrets.token_urlsafe(32)
    now = datetime.utcnow().isoformat()
    from datetime import timedelta
    expires_at = (datetime.utcnow() + timedelta(days=expires_days)).isoformat()
    
    with sqlite3.connect(DATABASE) as conn:
        conn.execute(
            """
            INSERT INTO pending_email_events 
            (token, source_event_id, title, category, start, end, location, url, description, event_source_id, created_at, expires_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                token,
                event.source_id,
                event.title,
                event.category,
                event.start.isoformat(),
                event.end.isoformat(),
                event.location,
                event.url,
                event.description,
                event_source_id,
                now,
                expires_at,
            ),
        )
        conn.commit()
    return token


def get_pending_email_event(token: str) -> Optional[Dict[str, any]]:
    """メール通知用のイベント情報を取得"""
    with sqlite3.connect(DATABASE) as conn:
        cursor = conn.execute(
            """
            SELECT source_event_id, title, category, start, end, location, url, description, event_source_id, expires_at
            FROM pending_email_events
            WHERE token = ?
            """,
            (token,),
        )
        row = cursor.fetchone()
        if not row:
            return None
        
        # 有効期限チェック
        if row[9]:
            expires_at = datetime.fromisoformat(row[9])
            if datetime.utcnow() > expires_at:
                return None
        
        return {
            "source_event_id": row[0],
            "title": row[1],
            "category": row[2],
            "start": row[3],
            "end": row[4],
            "location": row[5],
            "url": row[6],
            "description": row[7],
            "event_source_id": row[8],
        }


def save_pending_email_events_bulk(events, event_source_id: int, expires_days: int = 7) -> Tuple[str, Dict[str, str]]:
    """複数のイベントをメール通知用に一時保存し、一括用トークンと個別用トークンの辞書を返す
    
    Returns:
        tuple: (一括用トークン, {source_event_id: 個別用トークン})
    """
    import secrets
    bulk_token = secrets.token_urlsafe(32)
    now = datetime.utcnow().isoformat()
    from datetime import timedelta
    expires_at = (datetime.utcnow() + timedelta(days=expires_days)).isoformat()
    
    individual_tokens = {}
    
    with sqlite3.connect(DATABASE) as conn:
        for event in events:
            # 個別用トークンを生成
            individual_token = secrets.token_urlsafe(32)
            individual_tokens[event.source_id] = individual_token
            
            # 一括用トークンで保存
            conn.execute(
                """
                INSERT INTO pending_email_events 
                (token, source_event_id, title, category, start, end, location, url, description, event_source_id, created_at, expires_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    bulk_token,
                    event.source_id,
                    event.title,
                    event.category,
                    event.start.isoformat(),
                    event.end.isoformat(),
                    event.location,
                    event.url,
                    event.description,
                    event_source_id,
                    now,
                    expires_at,
                ),
            )
            
            # 個別用トークンでも保存（個別追加用）
            conn.execute(
                """
                INSERT INTO pending_email_events 
                (token, source_event_id, title, category, start, end, location, url, description, event_source_id, created_at, expires_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    individual_token,
                    event.source_id,
                    event.title,
                    event.category,
                    event.start.isoformat(),
                    event.end.isoformat(),
                    event.location,
                    event.url,
                    event.description,
                    event_source_id,
                    now,
                    expires_at,
                ),
            )
        conn.commit()
    return bulk_token, individual_tokens


def get_pending_email_events_bulk(token: str) -> List[Dict[str, any]]:
    """メール通知用の複数のイベント情報を取得"""
    with sqlite3.connect(DATABASE) as conn:
        cursor = conn.execute(
            """
            SELECT source_event_id, title, category, start, end, location, url, description, event_source_id, expires_at
            FROM pending_email_events
            WHERE token = ?
            ORDER BY start ASC
            """,
            (token,),
        )
        rows = cursor.fetchall()
        if not rows:
            return []
        
        # 有効期限チェック（最初の行のexpires_atを確認）
        if rows[0][9]:
            expires_at = datetime.fromisoformat(rows[0][9])
            if datetime.utcnow() > expires_at:
                return []
        
        return [
            {
                "source_event_id": row[0],
                "title": row[1],
                "category": row[2],
                "start": row[3],
                "end": row[4],
                "location": row[5],
                "url": row[6],
                "description": row[7],
                "event_source_id": row[8],
            }
            for row in rows
        ]


def delete_pending_email_event(token: str) -> None:
    """メール通知用のイベント情報を削除"""
    with sqlite3.connect(DATABASE) as conn:
        conn.execute("DELETE FROM pending_email_events WHERE token = ?", (token,))
        conn.commit()


# ダッシュボード機能
def get_today_events() -> List[Dict[str, any]]:
    """今日のイベントを取得"""
    from datetime import datetime, timezone, timedelta
    now = datetime.now(timezone.utc)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
    today_end = (now.replace(hour=23, minute=59, second=59, microsecond=999999)).isoformat()
    
    with sqlite3.connect(DATABASE) as conn:
        cursor = conn.execute(
            """
            SELECT source_event_id, title, category, start, end, location, url, description
            FROM app_events
            WHERE archived = 0
            AND start >= ? AND start <= ?
            ORDER BY start ASC
            """,
            (today_start, today_end),
        )
        return [
            {
                "source_event_id": row[0],
                "title": row[1],
                "category": row[2],
                "start": row[3],
                "end": row[4],
                "location": row[5],
                "url": row[6],
                "description": row[7],
            }
            for row in cursor.fetchall()
        ]


def get_week_summary() -> Dict[str, any]:
    """今週のイベントサマリーを取得"""
    from datetime import datetime, timezone, timedelta
    now = datetime.now(timezone.utc)
    week_start_dt = (now - timedelta(days=now.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
    week_start = week_start_dt.isoformat()
    week_end = (week_start_dt + timedelta(days=7)).isoformat()
    
    with sqlite3.connect(DATABASE) as conn:
        cursor = conn.execute(
            """
            SELECT COUNT(*) as total,
                   COUNT(CASE WHEN category = 'ライブ' THEN 1 END) as live,
                   COUNT(CASE WHEN category = 'テレビ' THEN 1 END) as tv,
                   COUNT(CASE WHEN category = 'ラジオ' THEN 1 END) as radio,
                   COUNT(CASE WHEN category = 'イベント' THEN 1 END) as event,
                   COUNT(CASE WHEN category = '配信' THEN 1 END) as stream,
                   COUNT(CASE WHEN category = 'リリース' THEN 1 END) as release
            FROM app_events
            WHERE archived = 0
            AND start >= ? AND start < ?
            """,
            (week_start, week_end),
        )
        row = cursor.fetchone()
        return {
            "total": row[0],
            "live": row[1],
            "tv": row[2],
            "radio": row[3],
            "event": row[4],
            "stream": row[5],
            "release": row[6],
        }


def get_upcoming_important_events(days: int = 7) -> List[Dict[str, any]]:
    """重要なイベント（近々開催）を取得"""
    from datetime import datetime, timezone, timedelta
    now = datetime.now(timezone.utc)
    future = (now + timedelta(days=days)).isoformat()
    
    with sqlite3.connect(DATABASE) as conn:
        cursor = conn.execute(
            """
            SELECT source_event_id, title, category, start, end, location, url, description
            FROM app_events
            WHERE archived = 0
            AND start > ? AND start <= ?
            AND category IN ('ライブ', 'イベント', '配信')
            ORDER BY start ASC
            LIMIT 10
            """,
            (now.isoformat(), future),
        )
        return [
            {
                "source_event_id": row[0],
                "title": row[1],
                "category": row[2],
                "start": row[3],
                "end": row[4],
                "location": row[5],
                "url": row[6],
                "description": row[7],
            }
            for row in cursor.fetchall()
        ]


# イベントソース管理機能
def record_fetch_history(event_source_id: int, events_count: int, success: bool = True) -> None:
    """取得履歴を記録"""
    with sqlite3.connect(DATABASE) as conn:
        conn.execute(
            """
            INSERT INTO fetch_history (event_source_id, fetched_at, events_count, success)
            VALUES (?, ?, ?, ?)
            """,
            (event_source_id, datetime.utcnow().isoformat(), events_count, 1 if success else 0),
        )
        conn.commit()


def record_fetch_error(event_source_id: Optional[int], error_message: str, error_type: Optional[str] = None) -> None:
    """取得エラーを記録"""
    with sqlite3.connect(DATABASE) as conn:
        conn.execute(
            """
            INSERT INTO fetch_errors (event_source_id, error_message, error_type, occurred_at)
            VALUES (?, ?, ?, ?)
            """,
            (event_source_id, error_message, error_type, datetime.utcnow().isoformat()),
        )
        conn.commit()


def get_fetch_history(event_source_id: Optional[int] = None, limit: int = 50) -> List[Dict[str, any]]:
    """取得履歴を取得"""
    with sqlite3.connect(DATABASE) as conn:
        if event_source_id:
            cursor = conn.execute(
                """
                SELECT id, event_source_id, fetched_at, events_count, success
                FROM fetch_history
                WHERE event_source_id = ?
                ORDER BY fetched_at DESC
                LIMIT ?
                """,
                (event_source_id, limit),
            )
        else:
            cursor = conn.execute(
                """
                SELECT id, event_source_id, fetched_at, events_count, success
                FROM fetch_history
                ORDER BY fetched_at DESC
                LIMIT ?
                """,
                (limit,),
            )
        return [
            {
                "id": row[0],
                "event_source_id": row[1],
                "fetched_at": row[2],
                "events_count": row[3],
                "success": bool(row[4]),
            }
            for row in cursor.fetchall()
        ]


def get_fetch_errors(event_source_id: Optional[int] = None, limit: int = 50) -> List[Dict[str, any]]:
    """取得エラーを取得"""
    with sqlite3.connect(DATABASE) as conn:
        if event_source_id:
            cursor = conn.execute(
                """
                SELECT id, event_source_id, error_message, error_type, occurred_at
                FROM fetch_errors
                WHERE event_source_id = ?
                ORDER BY occurred_at DESC
                LIMIT ?
                """,
                (event_source_id, limit),
            )
        else:
            cursor = conn.execute(
                """
                SELECT id, event_source_id, error_message, error_type, occurred_at
                FROM fetch_errors
                ORDER BY occurred_at DESC
                LIMIT ?
                """,
                (limit,),
            )
        return [
            {
                "id": row[0],
                "event_source_id": row[1],
                "error_message": row[2],
                "error_type": row[3],
                "occurred_at": row[4],
            }
            for row in cursor.fetchall()
        ]


def get_source_statistics(event_source_id: int) -> Dict[str, any]:
    """イベントソースの統計情報を取得"""
    with sqlite3.connect(DATABASE) as conn:
        # 取得回数
        cursor = conn.execute(
            "SELECT COUNT(*) FROM fetch_history WHERE event_source_id = ?",
            (event_source_id,),
        )
        total_fetches = cursor.fetchone()[0]
        
        # 成功回数
        cursor = conn.execute(
            "SELECT COUNT(*) FROM fetch_history WHERE event_source_id = ? AND success = 1",
            (event_source_id,),
        )
        successful_fetches = cursor.fetchone()[0]
        
        # エラー回数
        cursor = conn.execute(
            "SELECT COUNT(*) FROM fetch_errors WHERE event_source_id = ?",
            (event_source_id,),
        )
        error_count = cursor.fetchone()[0]
        
        # 最終取得時刻
        cursor = conn.execute(
            "SELECT fetched_at FROM fetch_history WHERE event_source_id = ? ORDER BY fetched_at DESC LIMIT 1",
            (event_source_id,),
        )
        last_fetch = cursor.fetchone()
        last_fetch_at = last_fetch[0] if last_fetch else None
        
        # 平均取得イベント数
        cursor = conn.execute(
            "SELECT AVG(events_count) FROM fetch_history WHERE event_source_id = ? AND success = 1",
            (event_source_id,),
        )
        avg_events = cursor.fetchone()[0]
        avg_events = round(avg_events, 1) if avg_events else 0
        
        return {
            "total_fetches": total_fetches,
            "successful_fetches": successful_fetches,
            "error_count": error_count,
            "success_rate": round((successful_fetches / total_fetches * 100) if total_fetches > 0 else 0, 1),
            "last_fetch_at": last_fetch_at,
            "avg_events": avg_events,
        }


def get_synced_events_for_reminder(reminder_hours: int) -> List[Dict[str, any]]:
    """リマインダー対象の同期済みイベントを取得
    
    現在時刻から reminder_hours 後までの間に開始時刻があるイベントを取得
    """
    from datetime import timedelta
    
    now = datetime.utcnow()
    reminder_time = now + timedelta(hours=reminder_hours)
    now_str = now.isoformat()
    reminder_time_str = reminder_time.isoformat()
    
    with sqlite3.connect(DATABASE) as conn:
        cursor = conn.execute(
            """
            SELECT source_event_id, calendar_event_id, event_start, event_end, event_title
            FROM synced_events
            WHERE event_start IS NOT NULL 
            AND event_start > ?
            AND event_start <= ?
            """,
            (now_str, reminder_time_str),
        )
        return [
            {
                "source_event_id": row[0],
                "calendar_event_id": row[1],
                "event_start": row[2],
                "event_end": row[3],
                "event_title": row[4],
            }
            for row in cursor.fetchall()
        ]
