from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

from app.display.base import DisplayAdapter
from app.media_library import MediaLibrary
from app.models import InputEvent, MediaCommand, RenderState, UIMode
from app.playback.mock_player import MockPlayer
from app.services.bluetooth import BluetoothManager
from app.services.controller import NightstandController
from app.state_store import StateStore


class MemoryDisplay(DisplayAdapter):
    def __init__(self) -> None:
        self.last_state: RenderState | None = None

    def render(self, state: RenderState, reason: str | None = None) -> None:
        self.last_state = state


class BluetoothManagerTest(unittest.TestCase):
    def test_fake_success_switches_sink_and_persists_preference(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = StateStore(Path(tmp) / "test.sqlite")
            manager = BluetoothManager(store)

            manager.begin_reconnect(datetime(2026, 5, 25, 8, 0))
            manager.fake_success()

            self.assertTrue(manager.state.connected)
            self.assertEqual(manager.state.active_sink, "bluetooth")
            self.assertEqual(store.get_app_state_value("preferred_output"), "bluetooth")
            self.assertIn("Connected", manager.state.last_message)

    def test_timeout_preserves_previous_sink(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = StateStore(Path(tmp) / "test.sqlite")
            manager = BluetoothManager(store)
            now = datetime(2026, 5, 25, 8, 0)

            manager.begin_reconnect(now)
            changed = manager.tick(now + timedelta(seconds=31))

            self.assertTrue(changed)
            self.assertFalse(manager.state.connected)
            self.assertEqual(manager.state.active_sink, "dac")
            self.assertEqual(manager.state.last_message, "Earbuds Not Found")

    def test_triple_clicking_any_source_button_starts_reconnect_workflow(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = StateStore(root / "test.sqlite")
            library = MediaLibrary(root / "media", store)
            library.ensure_demo_library()
            controller = NightstandController(
                store=store,
                library=library,
                player=MockPlayer(),
                display=MemoryDisplay(),
            )

            controller.handle_event(InputEvent("source", "button-1"))
            controller.handle_event(InputEvent("source", "button-2"))
            controller.handle_event(InputEvent("source", "button-3"))

            self.assertTrue(controller.bluetooth.state.reconnecting)
            self.assertIn("Connecting", controller.bluetooth.state.last_message)

    def test_bluetooth_media_commands_use_playback_logic_without_menu_navigation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = StateStore(root / "test.sqlite")
            library = MediaLibrary(root / "media", store)
            library.ensure_demo_library()
            controller = NightstandController(
                store=store,
                library=library,
                player=MockPlayer(),
                display=MemoryDisplay(),
            )
            controller.handle_event(InputEvent("source", "button-1"))
            controller.handle_event(InputEvent("long_press"))
            self.assertEqual(controller.nav.current_mode, UIMode.MENU)

            controller.handle_event(InputEvent("media_command", MediaCommand.NEXT_TRACK))
            self.assertEqual(controller.player.status().title, "Slot 1 Episode 002")
            self.assertEqual(controller.nav.current_mode, UIMode.MENU)

            controller.player.status().position_seconds = 10
            controller.handle_event(InputEvent("media_command", MediaCommand.PREVIOUS_TRACK))
            self.assertLess(controller.player.status().position_seconds, 1)
            self.assertEqual(controller.nav.current_mode, UIMode.MENU)

            controller.handle_event(InputEvent("media_command", MediaCommand.PLAY_PAUSE))
            self.assertEqual(controller.player.status().state.value, "paused")
            self.assertEqual(controller.nav.current_mode, UIMode.MENU)


if __name__ == "__main__":
    unittest.main()
