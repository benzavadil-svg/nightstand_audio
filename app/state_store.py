from __future__ import annotations

import sqlite3
import json
from contextlib import contextmanager
from datetime import date, datetime
from pathlib import Path
from typing import Iterator, Iterable

from app.models import AlarmConfig, MediaItem, PlaybackSession


LEGACY_SOURCE_IDS = {
    "bible-in-a-year": "button-1",
    "sleep-baseball": "button-2",
    "night-albums": "button-3",
}


class StateStore:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.initialize()

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def initialize(self) -> None:
        with self.connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS media_items (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    source_id TEXT NOT NULL,
                    file_path TEXT NOT NULL UNIQUE,
                    title TEXT NOT NULL,
                    artist TEXT,
                    duration_seconds INTEGER,
                    sort_key TEXT NOT NULL DEFAULT '',
                    last_position_seconds REAL NOT NULL DEFAULT 0,
                    completed INTEGER NOT NULL DEFAULT 0,
                    play_count INTEGER NOT NULL DEFAULT 0,
                    last_played_at TEXT
                );

                CREATE INDEX IF NOT EXISTS idx_media_source
                ON media_items(source_id, completed, last_played_at);

                CREATE TABLE IF NOT EXISTS alarm_config (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    enabled INTEGER NOT NULL DEFAULT 0,
                    hour INTEGER NOT NULL DEFAULT 7,
                    minute INTEGER NOT NULL DEFAULT 0,
                    source_id TEXT NOT NULL DEFAULT 'button-1',
                    target_volume INTEGER NOT NULL DEFAULT 40,
                    fade_in_seconds INTEGER NOT NULL DEFAULT 60,
                    snooze_minutes INTEGER NOT NULL DEFAULT 9,
                    last_triggered_date TEXT
                );

                INSERT OR IGNORE INTO alarm_config (id) VALUES (1);

                CREATE TABLE IF NOT EXISTS playback_sessions (
                    source_id TEXT PRIMARY KEY,
                    current_track_id INTEGER,
                    current_track_index INTEGER NOT NULL DEFAULT 0,
                    last_position_seconds REAL NOT NULL DEFAULT 0,
                    is_playing INTEGER NOT NULL DEFAULT 0,
                    queue_order TEXT NOT NULL DEFAULT '[]',
                    updated_at TEXT
                );

                CREATE TABLE IF NOT EXISTS app_state (
                    key TEXT PRIMARY KEY,
                    value TEXT
                );
                """
            )
            self._ensure_column(conn, "media_items", "sort_key", "TEXT NOT NULL DEFAULT ''")

    def _ensure_column(
        self, conn: sqlite3.Connection, table_name: str, column_name: str, declaration: str
    ) -> None:
        columns = {
            row["name"]
            for row in conn.execute(f"PRAGMA table_info({table_name})").fetchall()
        }
        if column_name not in columns:
            conn.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {declaration}")

    def upsert_media_items(self, items: Iterable[MediaItem]) -> int:
        count = 0
        with self.connect() as conn:
            for item in items:
                conn.execute(
                    """
                    INSERT INTO media_items (
                        source_id, file_path, title, artist, duration_seconds, sort_key
                    )
                    VALUES (?, ?, ?, ?, ?, ?)
                    ON CONFLICT(file_path) DO UPDATE SET
                        source_id = excluded.source_id,
                        title = excluded.title,
                        artist = excluded.artist,
                        duration_seconds = excluded.duration_seconds,
                        sort_key = excluded.sort_key
                    """,
                    (
                        item.source_id,
                        item.file_path,
                        item.title,
                        item.artist,
                        item.duration_seconds,
                        item.sort_key,
                    ),
                )
                count += 1
        return count

    def media_count(self) -> int:
        with self.connect() as conn:
            row = conn.execute("SELECT COUNT(*) AS count FROM media_items").fetchone()
        return int(row["count"])

    def list_media(self, source_id: str | None = None) -> list[MediaItem]:
        sql = "SELECT * FROM media_items"
        args: tuple[str, ...] = ()
        if source_id:
            sql += " WHERE source_id = ?"
            args = (source_id,)
        sql += " ORDER BY sort_key, file_path"
        with self.connect() as conn:
            rows = conn.execute(sql, args).fetchall()
        return [self._row_to_media_item(row) for row in rows]

    def get_resume_item(self, source_id: str) -> MediaItem | None:
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT * FROM media_items
                WHERE source_id = ?
                ORDER BY
                    CASE WHEN completed = 0 THEN 0 ELSE 1 END,
                    CASE WHEN last_played_at IS NULL THEN 1 ELSE 0 END,
                    datetime(last_played_at) DESC,
                    sort_key ASC,
                    file_path ASC
                LIMIT 1
                """,
                (source_id,),
            ).fetchone()
        return self._row_to_media_item(row) if row else None

    def get_source_queue(self, source_id: str) -> list[MediaItem]:
        return self.list_media(source_id)

    def get_completed_count(self, source_id: str) -> int:
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT COUNT(*) AS count
                FROM media_items
                WHERE source_id = ? AND completed = 1
                """,
                (source_id,),
            ).fetchone()
        return int(row["count"])

    def reset_source_progress(self, source_id: str) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE media_items
                SET last_position_seconds = 0,
                    completed = 0
                WHERE source_id = ?
                """,
                (source_id,),
            )
            conn.execute(
                """
                UPDATE playback_sessions
                SET current_track_id = NULL,
                    current_track_index = 0,
                    last_position_seconds = 0,
                    is_playing = 0,
                    updated_at = ?
                WHERE source_id = ?
                """,
                (datetime.now().isoformat(timespec="seconds"), source_id),
            )

    def get_next_item(self, source_id: str, current_track_id: int) -> MediaItem | None:
        queue = self.get_source_queue(source_id)
        for index, item in enumerate(queue):
            if item.id == current_track_id and index + 1 < len(queue):
                return queue[index + 1]
        return None

    def get_podcast_resume_item(self, source_id: str) -> MediaItem | None:
        with self.connect() as conn:
            partial = conn.execute(
                """
                SELECT * FROM media_items
                WHERE source_id = ?
                  AND completed = 0
                  AND last_position_seconds > 0
                ORDER BY datetime(last_played_at) DESC, sort_key ASC, file_path ASC
                LIMIT 1
                """,
                (source_id,),
            ).fetchone()
            if partial:
                return self._row_to_media_item(partial)

            next_unplayed = conn.execute(
                """
                SELECT * FROM media_items
                WHERE source_id = ?
                  AND completed = 0
                ORDER BY sort_key ASC, file_path ASC
                LIMIT 1
                """,
                (source_id,),
            ).fetchone()
            if next_unplayed:
                return self._row_to_media_item(next_unplayed)

            first = conn.execute(
                """
                SELECT * FROM media_items
                WHERE source_id = ?
                ORDER BY sort_key ASC, file_path ASC
                LIMIT 1
                """,
                (source_id,),
            ).fetchone()
        return self._row_to_media_item(first) if first else None

    def get_item(self, item_id: int) -> MediaItem | None:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM media_items WHERE id = ?", (item_id,)).fetchone()
        return self._row_to_media_item(row) if row else None

    def mark_started(self, item_id: int) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE media_items
                SET play_count = play_count + 1, last_played_at = ?
                WHERE id = ?
                """,
                (datetime.now().isoformat(timespec="seconds"), item_id),
            )

    def update_playback_position(
        self, item_id: int, position_seconds: float, completed: bool = False
    ) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE media_items
                SET last_position_seconds = ?, completed = ?
                WHERE id = ?
                """,
                (max(0, position_seconds), int(completed), item_id),
            )

    def get_playback_session(self, source_id: str) -> PlaybackSession:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM playback_sessions WHERE source_id = ?",
                (source_id,),
            ).fetchone()
        if not row:
            return PlaybackSession(source_id=source_id)
        return PlaybackSession(
            source_id=str(row["source_id"]),
            current_track_id=int(row["current_track_id"]) if row["current_track_id"] else None,
            current_track_index=int(row["current_track_index"]),
            last_position_seconds=float(row["last_position_seconds"]),
            is_playing=bool(row["is_playing"]),
            queue_order=json.loads(row["queue_order"] or "[]"),
            updated_at=datetime.fromisoformat(row["updated_at"]) if row["updated_at"] else None,
        )

    def save_playback_session(self, session: PlaybackSession) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO playback_sessions (
                    source_id,
                    current_track_id,
                    current_track_index,
                    last_position_seconds,
                    is_playing,
                    queue_order,
                    updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(source_id) DO UPDATE SET
                    current_track_id = excluded.current_track_id,
                    current_track_index = excluded.current_track_index,
                    last_position_seconds = excluded.last_position_seconds,
                    is_playing = excluded.is_playing,
                    queue_order = excluded.queue_order,
                    updated_at = excluded.updated_at
                """,
                (
                    session.source_id,
                    session.current_track_id,
                    session.current_track_index,
                    max(0, session.last_position_seconds),
                    int(session.is_playing),
                    json.dumps(session.queue_order),
                    datetime.now().isoformat(timespec="seconds"),
                ),
            )

    def set_current_source_id(self, source_id: str | None) -> None:
        self.set_app_state_value("current_source_id", source_id)

    def get_current_source_id(self) -> str | None:
        value = self.get_app_state_value("current_source_id")
        if not value:
            return None
        return LEGACY_SOURCE_IDS.get(value, value)

    def set_app_state_value(self, key: str, value: str | None) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO app_state (key, value)
                VALUES (?, ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value
                """,
                (key, value),
            )

    def get_app_state_value(self, key: str) -> str | None:
        with self.connect() as conn:
            row = conn.execute("SELECT value FROM app_state WHERE key = ?", (key,)).fetchone()
        if not row or not row["value"]:
            return None
        return str(row["value"])

    def get_alarm_config(self) -> AlarmConfig:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM alarm_config WHERE id = 1").fetchone()
        if not row:
            return AlarmConfig()
        source_id = LEGACY_SOURCE_IDS.get(str(row["source_id"]), str(row["source_id"]))
        return AlarmConfig(
            enabled=bool(row["enabled"]),
            hour=int(row["hour"]),
            minute=int(row["minute"]),
            source_id=source_id,
            target_volume=int(row["target_volume"]),
            fade_in_seconds=int(row["fade_in_seconds"]),
            snooze_minutes=int(row["snooze_minutes"]),
            last_triggered_date=date.fromisoformat(row["last_triggered_date"])
            if row["last_triggered_date"]
            else None,
        )

    def save_alarm_config(self, alarm: AlarmConfig) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE alarm_config
                SET enabled = ?,
                    hour = ?,
                    minute = ?,
                    source_id = ?,
                    target_volume = ?,
                    fade_in_seconds = ?,
                    snooze_minutes = ?,
                    last_triggered_date = ?
                WHERE id = 1
                """,
                (
                    int(alarm.enabled),
                    alarm.hour,
                    alarm.minute,
                    alarm.source_id,
                    alarm.target_volume,
                    alarm.fade_in_seconds,
                    alarm.snooze_minutes,
                    alarm.last_triggered_date.isoformat() if alarm.last_triggered_date else None,
                ),
            )

    def _row_to_media_item(self, row: sqlite3.Row) -> MediaItem:
        return MediaItem(
            id=int(row["id"]),
            source_id=str(row["source_id"]),
            file_path=str(row["file_path"]),
            title=str(row["title"]),
            artist=str(row["artist"]) if row["artist"] else None,
            duration_seconds=int(row["duration_seconds"]) if row["duration_seconds"] else None,
            sort_key=str(row["sort_key"] or row["file_path"]),
            last_position_seconds=float(row["last_position_seconds"]),
            completed=bool(row["completed"]),
            play_count=int(row["play_count"]),
            last_played_at=datetime.fromisoformat(row["last_played_at"])
            if row["last_played_at"]
            else None,
        )
