import os
import sqlite3
from datetime import datetime
from typing import Dict, List, Optional

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
        try:
            conn.execute("ALTER TABLE event_sources ADD COLUMN mode TEXT NOT NULL DEFAULT 'static'")
        except sqlite3.OperationalError:
            pass
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


def get_event_sources() -> List[Dict[str, str]]:
    with sqlite3.connect(DATABASE) as conn:
        cursor = conn.execute(
            """
            SELECT id, oshi_name, url, mode, created_at
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
            }
            for row in cursor.fetchall()
        ]


def get_event_source(source_id: int) -> Optional[Dict[str, str]]:
    with sqlite3.connect(DATABASE) as conn:
        cursor = conn.execute(
            """
            SELECT id, oshi_name, url, mode, created_at
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
