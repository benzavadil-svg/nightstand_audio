from __future__ import annotations

import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from app.display.simulator_display import SimulatorDisplay, _screen_signature
from app.models import AlarmConfig, AlarmRuntimeState, PlaybackState, PlaybackStatus, RenderState, UIMode


class FakePhysicalDisplay:
    def __init__(self) -> None:
        self.one_shot_calls: list[tuple[str, str | None, str | None]] = []
        self.render_path_calls: list[tuple[str, str, str | None, bool]] = []

    def one_shot_render_path(
        self,
        path: str,
        reason: str | None = None,
        displayed_hash: str | None = None,
    ) -> bool:
        self.one_shot_calls.append((path, reason, displayed_hash))
        return True

    def render_path(
        self,
        path: str,
        update_mode: str = "full",
        reason: str | None = None,
        clean_refresh: bool = False,
        region=None,
    ) -> bool:
        self.render_path_calls.append((path, update_mode, reason, clean_refresh))
        return True


class EpaperRefreshPolicyTest(unittest.TestCase):
    def make_display(self) -> SimulatorDisplay:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        return SimulatorDisplay(
            renderer=None,
            output_path=Path(tmp.name) / "screen.png",
            partial_update_enabled=True,
            full_clear_interval=50,
        )

    def test_startup_and_source_changes_are_full_updates(self) -> None:
        display = self.make_display()

        self.assertEqual(display._classify_update("startup")[:2], ("full", False))
        display._last_pushed_hash = "already-rendered"
        self.assertEqual(display._classify_update("source_change")[:2], ("full", False))

    def test_playback_home_playlist_switch_can_use_partial_main_content(self) -> None:
        physical = FakePhysicalDisplay()
        display = SimulatorDisplay(
            renderer=None,
            output_path=Path(tempfile.mkdtemp()) / "screen.png",
            physical_display=physical,
            one_shot_major_transitions=True,
        )
        display._last_pushed_hash = "old-hash"
        display._last_pushed_screen_signature = ("HOME", "Bible in a Year", "playback_home")

        self.assertEqual(
            display._classify_update(
                "source_change",
                ("HOME", "Sleep Baseball", "playback_home"),
            )[:3],
            ("partial", False, "same_playback_layout_playlist_switch"),
        )

        display._request_physical_update(
            "new-hash",
            "source_change",
            ("HOME", "Sleep Baseball", "playback_home"),
        )

        self.assertEqual(display._pending_update_mode, "partial")
        self.assertEqual(display._pending_dirty_region.name, "main_content")
        self.assertEqual(physical.one_shot_calls, [])

    def test_playback_playlist_switch_obeys_partial_streak_limit(self) -> None:
        display = self.make_display()
        display._last_pushed_hash = "old-hash"
        display._last_pushed_screen_signature = ("HOME", "Bible in a Year", "playback_home")
        display._partial_since_clean = 8

        self.assertEqual(
            display._classify_update(
                "source_change",
                ("HOME", "Sleep Baseball", "playback_home"),
            )[:2],
            ("full", True),
        )

    def test_menu_and_clock_changes_are_partial_updates(self) -> None:
        display = self.make_display()
        display._last_pushed_hash = "already-rendered"
        menu_screen = ("MENU", "Home", "menu")
        home_screen = ("HOME", "Clock", "idle_home")
        display._last_pushed_screen_signature = menu_screen

        self.assertEqual(
            display._classify_update("menu_navigation", menu_screen)[:2],
            ("partial", False),
        )
        display._last_pushed_screen_signature = home_screen
        self.assertEqual(
            display._classify_update("clock_refresh", home_screen)[:2],
            ("partial", False),
        )

    def test_screen_mode_or_title_change_forces_clean_full_update(self) -> None:
        display = self.make_display()
        display._last_pushed_hash = "already-rendered"
        display._last_pushed_screen_signature = ("HOME", "Clock", "idle_home")

        self.assertEqual(
            display._classify_update("menu_navigation", ("MENU", "Home", "menu"))[:2],
            ("full", True),
        )

    def test_major_layout_reason_with_mode_change_forces_clean_full_update(self) -> None:
        display = self.make_display()
        display._last_pushed_hash = "already-rendered"
        display._last_pushed_screen_signature = ("HOME", "Clock", "idle_home")

        self.assertEqual(
            display._classify_update("major_layout_transition", ("MENU", "Home", "menu"))[:2],
            ("full", True),
        )

    def test_partial_can_be_disabled_as_fast_fallback(self) -> None:
        display = SimulatorDisplay(
            renderer=None,
            output_path=Path(tempfile.mkdtemp()) / "screen.png",
            partial_update_enabled=False,
        )
        display._last_pushed_hash = "already-rendered"
        display._last_pushed_screen_signature = ("MENU", "Home", "menu")

        self.assertEqual(
            display._classify_update("menu_navigation", ("MENU", "Home", "menu"))[:2],
            ("full", False),
        )

    def test_periodic_ghosting_cleanup_forces_clean_full_update(self) -> None:
        display = self.make_display()
        display._last_pushed_hash = "already-rendered"
        display._last_pushed_screen_signature = ("HOME", "Clock", "idle_home")
        display._physical_update_count = 49

        self.assertEqual(
            display._classify_update("volume_change", ("HOME", "Clock", "idle_home"))[:2],
            ("full", True),
        )

    def test_eight_partial_updates_force_clean_full_update(self) -> None:
        display = self.make_display()
        display._last_pushed_hash = "already-rendered"
        display._last_pushed_screen_signature = ("MENU", "Home", "menu")
        display._partial_since_clean = 8

        self.assertEqual(
            display._classify_update("menu_navigation", ("MENU", "Home", "menu"))[:2],
            ("full", True),
        )

    def test_partial_streak_limit_is_configurable(self) -> None:
        display = SimulatorDisplay(
            renderer=None,
            output_path=Path(tempfile.mkdtemp()) / "screen.png",
            partial_update_enabled=True,
            partial_streak_limit=2,
        )
        display._last_pushed_hash = "already-rendered"
        display._last_pushed_screen_signature = ("MENU", "Home", "menu")
        display._partial_since_clean = 2

        self.assertEqual(
            display._classify_update("menu_navigation", ("MENU", "Home", "menu"))[:2],
            ("full", True),
        )

    def test_volume_change_physical_update_is_enabled_by_default(self) -> None:
        display = self.make_display()
        display._last_pushed_hash = "already-rendered"
        display._last_pushed_screen_signature = ("HOME", "Clock", "idle_home")

        display._request_physical_update(
            "new-hash",
            "volume_change",
            ("HOME", "Clock", "idle_home"),
        )

        self.assertEqual(display._pending_hash, "new-hash")
        self.assertEqual(display._pending_update_mode, "partial")
        self.assertEqual(display._pending_dirty_region.name, "bottom_bar")
        self.assertGreaterEqual(display._pending_wait_seconds(), 0.5)

    def test_volume_change_can_be_opted_out(self) -> None:
        display = SimulatorDisplay(
            renderer=None,
            output_path=Path(tempfile.mkdtemp()) / "screen.png",
            partial_update_enabled=True,
            refresh_on_volume_change=False,
        )
        display._request_physical_update(
            "new-hash",
            "volume_change",
            ("HOME", "Clock", "idle_home"),
        )

        self.assertIsNone(display._pending_hash)
        self.assertEqual(display._skipped_count, 1)

    def test_volume_change_uses_configurable_settled_debounce(self) -> None:
        display = SimulatorDisplay(
            renderer=None,
            output_path=Path(tempfile.mkdtemp()) / "screen.png",
            partial_update_enabled=True,
            volume_debounce_ms=900,
        )
        display._last_pushed_hash = "already-rendered"
        display._last_pushed_screen_signature = ("HOME", "Clock", "idle_home")

        display._request_physical_update(
            "new-hash",
            "volume_change",
            ("HOME", "Clock", "idle_home"),
        )

        self.assertEqual(display._pending_update_mode, "partial")
        self.assertEqual(display._pending_reason, "volume_change")
        self.assertGreaterEqual(display._pending_wait_seconds(), 0.8)

    def test_volume_change_is_full_when_partial_disabled_but_refresh_opted_in(self) -> None:
        display = SimulatorDisplay(
            renderer=None,
            output_path=Path(tempfile.mkdtemp()) / "screen.png",
            partial_update_enabled=False,
            refresh_on_volume_change=True,
        )
        display._last_pushed_hash = "already-rendered"
        display._last_pushed_screen_signature = ("HOME", "Clock", "idle_home")

        self.assertEqual(
            display._classify_update("volume_change", ("HOME", "Clock", "idle_home"))[:2],
            ("full", False),
        )

    def test_major_transition_uses_one_shot_and_cancels_pending_update(self) -> None:
        physical = FakePhysicalDisplay()
        display = SimulatorDisplay(
            renderer=None,
            output_path=Path(tempfile.mkdtemp()) / "screen.png",
            physical_display=physical,
            one_shot_major_transitions=True,
        )
        display._last_pushed_hash = "old-hash"
        display._last_pushed_screen_signature = ("SLEEP_TIMER", "Sleep Timer", "sleep_timer")
        display._pending_hash = "stale-hash"
        display._pending_reason = "sleep_timer"

        display._request_physical_update(
            "home-hash",
            "major_layout_transition",
            ("HOME", "Clock", "idle_home"),
        )

        self.assertIsNone(display._pending_hash)
        self.assertEqual(display._last_pushed_hash, "home-hash")
        self.assertEqual(display._last_pushed_screen_signature, ("HOME", "Clock", "idle_home"))
        self.assertEqual(len(physical.one_shot_calls), 1)
        self.assertEqual(physical.one_shot_calls[0][1:], ("major_layout_transition", "home-hash"))
        self.assertEqual(physical.render_path_calls, [])

    def test_minor_same_screen_update_still_uses_debounced_fast_path(self) -> None:
        physical = FakePhysicalDisplay()
        display = SimulatorDisplay(
            renderer=None,
            output_path=Path(tempfile.mkdtemp()) / "screen.png",
            physical_display=physical,
            one_shot_major_transitions=True,
        )
        display._last_pushed_hash = "old-hash"
        display._last_pushed_screen_signature = ("MENU", "Home", "menu")

        display._request_physical_update(
            "menu-hash",
            "menu_navigation",
            ("MENU", "Home", "menu"),
        )

        self.assertEqual(display._pending_hash, "menu-hash")
        self.assertEqual(display._pending_dirty_region.name, "menu_list")
        self.assertEqual(physical.one_shot_calls, [])

    def test_dirty_regions_merge_for_quick_partial_changes(self) -> None:
        physical = FakePhysicalDisplay()
        display = SimulatorDisplay(
            renderer=None,
            output_path=Path(tempfile.mkdtemp()) / "screen.png",
            physical_display=physical,
            one_shot_major_transitions=True,
        )
        display._last_pushed_hash = "old-hash"
        display._last_pushed_screen_signature = ("HOME", "Clock", "idle_home")

        display._request_physical_update(
            "clock-hash",
            "clock_refresh",
            ("HOME", "Clock", "idle_home"),
        )
        display._request_physical_update(
            "play-hash",
            "playback_toggle",
            ("HOME", "Clock", "idle_home"),
        )

        self.assertEqual(display._pending_update_mode, "partial")
        self.assertEqual(display._pending_dirty_region.name, "clock+bottom_bar")
        self.assertEqual(display._pending_dirty_region.bounds, (0, 0, 600, 448))

    def test_screen_signature_tracks_layout_type(self) -> None:
        base = {
            "now": datetime(2026, 5, 28, 6, 1),
            "alarm": AlarmConfig(),
            "alarm_runtime": AlarmRuntimeState(),
            "sleep_timer_label": "Sleep off",
        }
        idle = RenderState(
            **base,
            mode=UIMode.HOME,
            playback=PlaybackStatus(state=PlaybackState.STOPPED),
            current_source_label="",
        )
        playback = RenderState(
            **base,
            mode=UIMode.HOME,
            playback=PlaybackStatus(state=PlaybackState.PLAYING),
            current_source_label="Sleep Baseball",
        )
        sleep_timer = RenderState(
            **base,
            mode=UIMode.SLEEP_TIMER,
            playback=PlaybackStatus(state=PlaybackState.STOPPED),
            current_source_label="",
        )

        self.assertEqual(_screen_signature(idle), ("HOME", "Clock", "idle_home"))
        self.assertEqual(
            _screen_signature(playback),
            ("HOME", "Sleep Baseball", "playback_home"),
        )
        self.assertEqual(
            _screen_signature(sleep_timer),
            ("SLEEP_TIMER", "Sleep Timer", "sleep_timer"),
        )


if __name__ == "__main__":
    unittest.main()
