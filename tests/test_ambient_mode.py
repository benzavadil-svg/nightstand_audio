from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

from app.display.base import DisplayAdapter
from app.media_library import MediaLibrary
from app.models import InputEvent, MediaItem, PlaybackState, RenderState, UIMode
from app.playback.mock_player import MockPlayer
from app.services.controller import NightstandController
from app.state_store import StateStore


class MemoryDisplay(DisplayAdapter):
    def __init__(self) -> None:
        self.last_state: RenderState | None = None

    def render(self, state: RenderState, reason: str | None = None) -> None:
        self.last_state = state


class AmbientModeTest(unittest.TestCase):
    def make_controller(self, tmp: str) -> NightstandController:
        store = StateStore(Path(tmp) / "test.sqlite")
        store.upsert_media_items(
            [
                MediaItem(
                    source_id="button-1",
                    file_path="demo://button-1/001",
                    title="Track 001",
                    sort_key="001",
                    duration_seconds=120,
                ),
                MediaItem(
                    source_id="button-2",
                    file_path="demo://button-2/001",
                    title="Track 002",
                    sort_key="001",
                    duration_seconds=120,
                ),
            ]
        )
        library = MediaLibrary(Path(tmp) / "media", store)
        return NightstandController(
            store=store,
            library=library,
            player=MockPlayer(),
            display=MemoryDisplay(),
            menu_timeout_seconds=15,
            night_mode_enabled=False,
            ambient_mode_enabled=True,
            active_mode_timeout_seconds=30,
        )

    def test_daytime_defaults_to_ambient_mode(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            controller = self.make_controller(tmp)
            controller.tick(datetime(2026, 5, 28, 12, 0))

            self.assertTrue(controller.is_ambient_mode_active)
            self.assertFalse(controller.is_active_mode_active)
            self.assertEqual(controller.display.last_state.mode, UIMode.AMBIENT)

    def test_ambient_knob_press_enters_active_without_starting_playback(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            controller = self.make_controller(tmp)
            controller.tick(datetime(2026, 5, 28, 12, 0))

            controller.handle_event(InputEvent("press"))
            controller.last_active_interaction_at = datetime(2026, 5, 28, 12, 0, 1)
            controller.tick(datetime(2026, 5, 28, 12, 0, 1))

            self.assertTrue(controller.is_active_mode_active)
            self.assertFalse(controller.is_ambient_mode_active)
            self.assertEqual(controller.player.status().state, PlaybackState.STOPPED)
            self.assertEqual(controller.display.last_state.mode, UIMode.HOME)

    def test_active_without_playback_times_out_to_ambient(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            controller = self.make_controller(tmp)
            now = datetime(2026, 5, 28, 12, 0)
            controller.tick(now)
            controller.handle_event(InputEvent("press"))
            controller.last_active_interaction_at = now

            controller.tick(now + timedelta(seconds=62))

            self.assertTrue(controller.is_ambient_mode_active)
            self.assertFalse(controller.is_active_mode_active)
            self.assertEqual(controller.display.last_state.mode, UIMode.AMBIENT)

    def test_source_button_enters_active_and_playback_keeps_active_visible(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            controller = self.make_controller(tmp)
            now = datetime(2026, 5, 28, 12, 0)
            controller.tick(now)

            controller.handle_event(InputEvent("source", "button-2"))
            controller.last_active_interaction_at = now
            controller.tick(now + timedelta(seconds=31))

            self.assertEqual(controller.player.status().state, PlaybackState.PLAYING)
            self.assertTrue(controller.is_active_mode_active)
            self.assertFalse(controller.is_ambient_mode_active)
            self.assertEqual(controller.display.last_state.mode, UIMode.HOME)

    def test_paused_playback_returns_to_ambient_after_timeout(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            controller = self.make_controller(tmp)
            now = datetime(2026, 5, 28, 12, 0)
            controller.tick(now)
            controller.handle_event(InputEvent("source", "button-1"))
            controller.tick(now + timedelta(seconds=1))

            controller.handle_event(InputEvent("source", "button-1"))
            controller.tick(now + timedelta(seconds=2))
            controller.tick(now + timedelta(seconds=33))

            self.assertEqual(controller.player.status().state, PlaybackState.PAUSED)
            self.assertTrue(controller.is_ambient_mode_active)
            self.assertEqual(controller.display.last_state.mode, UIMode.AMBIENT)

    def test_source_button_snoozes_active_alarm(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            controller = self.make_controller(tmp)
            controller.alarm.runtime.phase = "ALARM_ACTIVE"

            controller.handle_event(InputEvent("source", "button-1"))

            self.assertEqual(controller.alarm.runtime.phase, "SNOOZE")
            self.assertIsNotNone(controller.alarm.runtime.snoozed_until)

    def make_night_controller_with_alarm(
        self,
        tmp: str,
        *,
        enabled: bool,
        hour: int,
        minute: int,
        wake_lead_minutes: int,
        dismissed: bool = False,
    ) -> NightstandController:
        store = StateStore(Path(tmp) / "test.sqlite")
        store.upsert_media_items(
            [
                MediaItem(
                    source_id="sounds",
                    file_path="demo://sounds/001",
                    title="Soft Rain",
                    sort_key="001",
                    duration_seconds=120,
                )
            ]
        )
        alarm = store.get_alarm_config()
        alarm.enabled = enabled
        alarm.hour = hour
        alarm.minute = minute
        alarm.wake_enabled = wake_lead_minutes > 0
        alarm.wake_lead_minutes = wake_lead_minutes
        if dismissed:
            alarm.last_dismissed_date = datetime(2026, 5, 29).date()
        store.save_alarm_config(alarm)
        return NightstandController(
            store=store,
            library=MediaLibrary(Path(tmp) / "media", store),
            player=MockPlayer(),
            display=MemoryDisplay(),
            night_mode_enabled=True,
            night_mode_start="22:00",
            night_mode_end="06:00",
            ambient_mode_enabled=True,
        )

    def test_no_alarm_ambient_starts_at_standard_time(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            controller = self.make_night_controller_with_alarm(
                tmp,
                enabled=False,
                hour=4,
                minute=0,
                wake_lead_minutes=30,
            )

            controller.tick(datetime(2026, 5, 29, 5, 59))
            self.assertTrue(controller.is_night_mode_active)

            controller.tick(datetime(2026, 5, 29, 6, 0))
            self.assertFalse(controller.is_night_mode_active)
            self.assertTrue(controller.is_ambient_mode_active)

    def test_early_alarm_moves_ambient_start_to_wake_sequence(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            controller = self.make_night_controller_with_alarm(
                tmp,
                enabled=True,
                hour=4,
                minute=0,
                wake_lead_minutes=30,
            )

            controller.tick(datetime(2026, 5, 29, 3, 29))
            self.assertTrue(controller.is_night_mode_active)

            controller.tick(datetime(2026, 5, 29, 3, 30))
            self.assertFalse(controller.is_night_mode_active)
            self.assertEqual(controller.alarm.runtime.phase, "WAKE_STAGE")

    def test_later_alarm_keeps_standard_ambient_start(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            controller = self.make_night_controller_with_alarm(
                tmp,
                enabled=True,
                hour=8,
                minute=0,
                wake_lead_minutes=30,
            )

            controller.tick(datetime(2026, 5, 29, 6, 0))

            self.assertFalse(controller.is_night_mode_active)
            self.assertTrue(controller.is_ambient_mode_active)

    def test_wake_lead_crossing_midnight_can_start_ambient_previous_day(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            controller = self.make_night_controller_with_alarm(
                tmp,
                enabled=True,
                hour=0,
                minute=10,
                wake_lead_minutes=30,
            )

            controller.tick(datetime(2026, 5, 28, 23, 39))
            self.assertTrue(controller.is_night_mode_active)

            controller.tick(datetime(2026, 5, 28, 23, 40))
            self.assertFalse(controller.is_night_mode_active)

    def test_dismissed_alarm_does_not_return_display_to_night_mode(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            controller = self.make_night_controller_with_alarm(
                tmp,
                enabled=True,
                hour=4,
                minute=0,
                wake_lead_minutes=30,
                dismissed=True,
            )

            controller.tick(datetime(2026, 5, 29, 4, 5))

            self.assertFalse(controller.is_night_mode_active)
            self.assertTrue(controller.is_ambient_mode_active)


if __name__ == "__main__":
    unittest.main()
