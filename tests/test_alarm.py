from __future__ import annotations

import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from app.media_library import MediaLibrary
from app.playback.mock_player import MockPlayer
from app.services.alarm import AlarmService
from app.state_store import StateStore


class AlarmServiceTest(unittest.TestCase):
    def test_alarm_triggers_once_snoozes_and_stops(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = StateStore(root / "test.sqlite")
            library = MediaLibrary(root / "media", store)
            library.ensure_demo_library()
            player = MockPlayer()
            alarm = store.get_alarm_config()
            alarm.enabled = True
            alarm.hour = 7
            alarm.minute = 30
            alarm.fade_in_seconds = 60
            alarm.target_volume = 40
            store.save_alarm_config(alarm)

            service = AlarmService(store, library, player)
            now = datetime(2026, 5, 24, 7, 30)

            self.assertTrue(service.tick(now))
            self.assertTrue(service.runtime.active)
            self.assertEqual(service.config.last_triggered_date, now.date())
            self.assertEqual(player.status().source_id, service.config.source_id)

            service.snooze(now)
            self.assertFalse(service.runtime.active)
            self.assertIsNotNone(service.runtime.snoozed_until)

            service.tick(service.runtime.snoozed_until)
            self.assertTrue(service.runtime.active)

            service.stop()
            self.assertFalse(service.runtime.active)
            self.assertIsNone(service.runtime.snoozed_until)


if __name__ == "__main__":
    unittest.main()
