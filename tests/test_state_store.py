from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from app.models import PlaybackSession
from app.state_store import StateStore


class StateStoreMigrationTest(unittest.TestCase):
    def test_legacy_playback_sessions_table_adds_stop_reason_on_save(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "legacy.sqlite"
            with sqlite3.connect(db_path) as conn:
                conn.executescript(
                    """
                    CREATE TABLE playback_sessions (
                        source_id TEXT PRIMARY KEY,
                        current_track_id INTEGER,
                        current_track_index INTEGER NOT NULL DEFAULT 0,
                        last_position_seconds REAL NOT NULL DEFAULT 0,
                        is_playing INTEGER NOT NULL DEFAULT 0,
                        queue_order TEXT NOT NULL DEFAULT '[]',
                        updated_at TEXT
                    );
                    """
                )

            store = StateStore(db_path)
            store.save_playback_session(
                PlaybackSession(
                    source_id="button-1",
                    current_track_id=123,
                    current_track_index=0,
                    last_position_seconds=42,
                    is_playing=False,
                    stop_reason="sleep",
                )
            )

            session = store.get_playback_session("button-1")

            self.assertEqual(session.stop_reason, "sleep")


if __name__ == "__main__":
    unittest.main()
