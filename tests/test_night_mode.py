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
        self.render_count = 0
        self.reasons: list[str | None] = []

    def render(self, state: RenderState, reason: str | None = None) -> None:
        self.last_state = state
        self.render_count += 1
        self.reasons.append(reason)


class NightModeTest(unittest.TestCase):
    def make_controller(self, tmp: str, *, enabled: bool = True) -> NightstandController:
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
                    source_id="button-1",
                    file_path="demo://button-1/002",
                    title="Track 002",
                    sort_key="002",
                    duration_seconds=120,
                ),
                MediaItem(
                    source_id="button-2",
                    file_path="demo://button-2/001",
                    title="Other 001",
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
            night_mode_enabled=enabled,
            night_mode_start="00:00",
            night_mode_end="00:00",
            night_mode_wake_timeout_seconds=30,
            night_mode_display_lock=True,
            ambient_mode_enabled=False,
        )

    def test_starts_on_sleep_screen_during_night_mode(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            controller = self.make_controller(tmp)
            controller.tick(datetime(2026, 1, 1, 23, 0))

            self.assertTrue(controller.is_night_mode_active)
            self.assertTrue(controller.is_sleep_screen_locked)
            self.assertIsNotNone(controller.display.last_state)
            self.assertEqual(controller.display.last_state.mode, UIMode.SLEEP_SCREEN)

    def test_source_button_works_without_waking_display(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            controller = self.make_controller(tmp)
            controller.tick(datetime(2026, 1, 1, 23, 0))
            render_count = controller.display.render_count

            controller.handle_event(InputEvent("source", "button-1"))
            controller.tick(datetime(2026, 1, 1, 23, 0, 1))

            self.assertEqual(controller.player.status().state, PlaybackState.PLAYING)
            self.assertEqual(controller.player.status().source_id, "button-1")
            self.assertTrue(controller.is_sleep_screen_locked)
            self.assertEqual(controller.display.render_count, render_count)

            controller.handle_event(InputEvent("source", "button-1"))
            controller.tick(datetime(2026, 1, 1, 23, 0, 2))

            self.assertEqual(controller.player.status().state, PlaybackState.PAUSED)
            self.assertEqual(controller.display.render_count, render_count)

    def test_single_knob_press_wakes_then_timeout_returns_to_sleep_screen(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            controller = self.make_controller(tmp)
            now = datetime(2026, 1, 1, 23, 0)
            controller.tick(now)

            controller.handle_event(InputEvent("press"))
            controller._flush_pending_home_press(force=True)
            controller.tick(now + timedelta(seconds=1))

            self.assertFalse(controller.is_sleep_screen_locked)
            self.assertEqual(controller.display.last_state.mode, UIMode.HOME)

            controller.last_display_wake_at = now
            controller.tick(now + timedelta(seconds=31))

            self.assertTrue(controller.is_sleep_screen_locked)
            self.assertEqual(controller.display.last_state.mode, UIMode.SLEEP_SCREEN)

    def test_outside_night_mode_normal_ui_is_unchanged(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            controller = self.make_controller(tmp, enabled=False)
            controller.tick(datetime(2026, 1, 1, 23, 0))

            self.assertFalse(controller.is_night_mode_active)
            self.assertFalse(controller.is_sleep_screen_locked)
            self.assertEqual(controller.display.last_state.mode, UIMode.HOME)


if __name__ == "__main__":
    unittest.main()
