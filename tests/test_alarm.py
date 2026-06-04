from __future__ import annotations

import tempfile
import unittest
from datetime import date, datetime
from pathlib import Path

from app.media_library import MediaLibrary
from app.playback.mock_player import MockPlayer
from app.services.alarm import AlarmService
from app.state_store import StateStore


class VolumeSpyPlayer(MockPlayer):
    def __init__(self) -> None:
        super().__init__()
        self.volume_values: list[int] = []

    def set_volume(self, volume: int) -> None:
        super().set_volume(volume)
        self.volume_values.append(self.status().volume)


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

    def test_gentle_wake_stage_transitions_and_volume_ramp(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = StateStore(root / "test.sqlite")
            library = MediaLibrary(root / "media", store)
            library.ensure_demo_library()
            player = VolumeSpyPlayer()
            alarm = store.get_alarm_config()
            alarm.enabled = True
            alarm.hour = 7
            alarm.minute = 0
            alarm.wake_enabled = True
            alarm.wake_lead_minutes = 30
            alarm.wake_stages = 4
            alarm.stage_volume_curve = [5, 10, 20, 35]
            alarm.stage_source = "sounds"
            alarm.interrupt_active_playback = True
            store.save_alarm_config(alarm)
            service = AlarmService(store, library, player)

            self.assertTrue(service.tick(datetime(2026, 5, 24, 6, 30)))
            self.assertEqual(service.runtime.phase, "WAKE_STAGE")
            self.assertEqual(service.runtime.wake_stage, 1)
            self.assertEqual(player.status().source_id, "sounds")
            self.assertLessEqual(player.status().volume, 5)

            self.assertFalse(service.tick(datetime(2026, 5, 24, 6, 33)))
            self.assertGreater(player.status().volume, 3)
            self.assertLessEqual(player.status().volume, 5)

            self.assertTrue(service.tick(datetime(2026, 5, 24, 6, 38)))
            self.assertEqual(service.runtime.wake_stage, 2)
            self.assertLessEqual(player.status().volume, 10)

            self.assertTrue(service.tick(datetime(2026, 5, 24, 7, 0)))
            self.assertEqual(service.runtime.phase, "ALARM_ACTIVE")
            self.assertTrue(service.runtime.active)
            self.assertEqual(service.config.last_triggered_date, date(2026, 5, 24))

    def test_wake_schedule_crosses_midnight(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = StateStore(root / "test.sqlite")
            library = MediaLibrary(root / "media", store)
            library.ensure_demo_library()
            alarm = store.get_alarm_config()
            alarm.enabled = True
            alarm.hour = 0
            alarm.minute = 10
            alarm.wake_lead_minutes = 30
            store.save_alarm_config(alarm)
            service = AlarmService(store, library, MockPlayer())

            schedule = service.schedule_for_now(datetime(2026, 5, 24, 23, 45))

            self.assertIsNotNone(schedule)
            self.assertEqual(schedule.target_at, datetime(2026, 5, 25, 0, 10))
            self.assertEqual(schedule.wake_start_at, datetime(2026, 5, 24, 23, 40))

    def test_reboot_during_wake_stage_reconstructs_stage_from_clock(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = StateStore(root / "test.sqlite")
            library = MediaLibrary(root / "media", store)
            library.ensure_demo_library()
            alarm = store.get_alarm_config()
            alarm.enabled = True
            alarm.hour = 7
            alarm.minute = 0
            alarm.wake_lead_minutes = 30
            alarm.wake_stages = 4
            store.save_alarm_config(alarm)

            service = AlarmService(store, library, MockPlayer())
            service.tick(datetime(2026, 5, 24, 6, 46))

            self.assertEqual(service.runtime.phase, "WAKE_STAGE")
            self.assertEqual(service.runtime.wake_stage, 3)

    def test_alarm_snooze_and_dismiss_for_day(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = StateStore(root / "test.sqlite")
            library = MediaLibrary(root / "media", store)
            library.ensure_demo_library()
            alarm = store.get_alarm_config()
            alarm.enabled = True
            alarm.hour = 7
            alarm.minute = 0
            alarm.snooze_minutes = 9
            store.save_alarm_config(alarm)
            service = AlarmService(store, library, MockPlayer())

            self.assertTrue(service.tick(datetime(2026, 5, 24, 7, 0)))
            service.snooze(datetime(2026, 5, 24, 7, 1))
            self.assertEqual(service.runtime.phase, "SNOOZE")
            self.assertEqual(service.runtime.snoozed_until, datetime(2026, 5, 24, 7, 10))

            self.assertTrue(service.tick(datetime(2026, 5, 24, 7, 10)))
            self.assertEqual(service.runtime.phase, "ALARM_ACTIVE")

            service.dismiss_for_day(datetime(2026, 5, 24, 7, 11))
            self.assertEqual(service.config.last_dismissed_date, date(2026, 5, 24))
            self.assertEqual(service.runtime.phase, "ALARM_DISMISSED")

    def test_dedicated_alarm_player_does_not_interrupt_normal_playback(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = StateStore(root / "test.sqlite")
            library = MediaLibrary(root / "media", store)
            library.ensure_demo_library()
            alarm = store.get_alarm_config()
            alarm.enabled = True
            alarm.hour = 7
            alarm.minute = 0
            alarm.wake_lead_minutes = 30
            alarm.interrupt_active_playback = False
            store.save_alarm_config(alarm)
            normal_player = MockPlayer()
            alarm_player = MockPlayer()
            normal_item = store.list_media("button-1")[0]
            normal_player.play(normal_item)
            service = AlarmService(store, library, normal_player, alarm_player=alarm_player)

            service.tick(datetime(2026, 5, 24, 6, 30))

            self.assertEqual(normal_player.status().state.value, "playing")
            self.assertEqual(alarm_player.status().state.value, "stopped")
            self.assertTrue(service.runtime.queued)


if __name__ == "__main__":
    unittest.main()
