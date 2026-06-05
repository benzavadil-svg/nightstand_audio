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

    def test_playback_home_playlist_switch_is_full_clean_by_default(self) -> None:
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
            ("full", True, "screen_mode_or_title_changed"),
        )

        display._request_physical_update(
            "new-hash",
            "source_change",
            ("HOME", "Sleep Baseball", "playback_home"),
        )

        self.assertEqual(display._pending_update_mode, "full")
        self.assertIsNone(display._pending_dirty_region)
        self.assertEqual(len(physical.one_shot_calls), 1)

    def test_playback_home_playlist_switch_partial_can_be_explicitly_enabled(self) -> None:
        physical = FakePhysicalDisplay()
        display = SimulatorDisplay(
            renderer=None,
            output_path=Path(tempfile.mkdtemp()) / "screen.png",
            physical_display=physical,
            one_shot_major_transitions=True,
            playlist_switch_partial_update_enabled=True,
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

    def test_menu_navigation_and_clock_are_full_by_default(self) -> None:
        display = self.make_display()
        display._last_pushed_hash = "already-rendered"
        menu_screen = ("MENU", "Home", "menu")
        home_screen = ("HOME", "Clock", "idle_home")
        display._last_pushed_screen_signature = menu_screen

        self.assertEqual(
            display._classify_update("menu_navigation", menu_screen)[:2],
            ("full", True),
        )
        display._last_pushed_screen_signature = home_screen
        self.assertEqual(
            display._classify_update("clock_refresh", home_screen)[:2],
            ("full", False),
        )

    def test_clock_partial_can_be_explicitly_enabled(self) -> None:
        display = SimulatorDisplay(
            renderer=None,
            output_path=Path(tempfile.mkdtemp()) / "screen.png",
            partial_update_enabled=True,
            full_clear_interval=50,
            clock_partial_update_enabled=True,
        )
        display._last_pushed_hash = "already-rendered"
        home_screen = ("HOME", "Clock", "idle_home")
        display._last_pushed_screen_signature = home_screen

        self.assertEqual(
            display._classify_update("clock_refresh", home_screen)[:3],
            ("partial", False, "same_layout_partial_reason"),
        )

    def test_menu_navigation_partial_can_be_explicitly_enabled(self) -> None:
        display = SimulatorDisplay(
            renderer=None,
            output_path=Path(tempfile.mkdtemp()) / "screen.png",
            partial_update_enabled=True,
            full_clear_interval=50,
            menu_navigation_update_mode="partial",
        )
        display._last_pushed_hash = "already-rendered"
        display._last_pushed_screen_signature = ("MENU", "Home", "menu")

        self.assertEqual(
            display._classify_update("menu_navigation", ("MENU", "Home", "menu"))[:3],
            ("partial", False, "same_layout_partial_reason"),
        )

    def test_menu_navigation_physical_update_can_be_skipped(self) -> None:
        physical = FakePhysicalDisplay()
        display = SimulatorDisplay(
            renderer=None,
            output_path=Path(tempfile.mkdtemp()) / "screen.png",
            physical_display=physical,
            menu_navigation_update_mode="skip",
        )
        display._last_pushed_hash = "old-hash"
        display._last_pushed_screen_signature = ("MENU", "Home", "menu")

        display._request_physical_update(
            "menu-hash",
            "menu_navigation",
            ("MENU", "Home", "menu"),
        )

        self.assertIsNone(display._pending_hash)
        self.assertEqual(display._skipped_count, 1)
        self.assertEqual(physical.one_shot_calls, [])
        self.assertEqual(physical.render_path_calls, [])

    def test_bluetooth_pairing_status_uses_clean_full_update(self) -> None:
        display = self.make_display()
        display._last_pushed_hash = "already-rendered"
        pairing_screen = ("BLUETOOTH_PAIRING", "Bluetooth Pairing", "bluetooth_pairing")
        display._last_pushed_screen_signature = pairing_screen

        self.assertEqual(
            display._classify_update("bluetooth_pairing_status", pairing_screen)[:3],
            ("full", True, "bluetooth_pairing_status_clean"),
        )

    def test_4in2_display_model_uses_same_refresh_policy_classification(self) -> None:
        display = self.make_display()
        display._last_pushed_hash = "already-rendered"
        display._last_pushed_screen_signature = ("SLEEP_TIMER", "Sleep Timer", "sleep_timer")

        self.assertEqual(
            display._classify_update("sleep_timer", ("SLEEP_TIMER", "Sleep Timer", "sleep_timer"))[:2],
            ("partial", False),
        )
        self.assertEqual(
            display._classify_update("major_layout_transition", ("HOME", "Clock", "idle_home"))[:2],
            ("full", True),
        )

    def test_playback_start_is_full_update(self) -> None:
        display = self.make_display()
        display._last_pushed_hash = "already-rendered"
        display._last_pushed_screen_signature = ("HOME", "Clock", "idle_home")

        self.assertEqual(
            display._classify_update("playback_start", ("HOME", "Sleep Baseball", "playback_home"))[:2],
            ("full", True),
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

    def test_menu_navigation_defers_partial_streak_cleanup(self) -> None:
        display = SimulatorDisplay(
            renderer=None,
            output_path=Path(tempfile.mkdtemp()) / "screen.png",
            partial_update_enabled=True,
            full_clear_interval=50,
            menu_navigation_update_mode="partial",
        )
        display._last_pushed_hash = "already-rendered"
        display._last_pushed_screen_signature = ("MENU", "Home", "menu")
        display._partial_since_clean = 8

        self.assertEqual(
            display._classify_update("menu_navigation", ("MENU", "Home", "menu"))[:2],
            ("partial", False),
        )

    def test_menu_navigation_defers_periodic_full_clear(self) -> None:
        display = SimulatorDisplay(
            renderer=None,
            output_path=Path(tempfile.mkdtemp()) / "screen.png",
            partial_update_enabled=True,
            full_clear_interval=50,
            menu_navigation_update_mode="partial",
        )
        display._last_pushed_hash = "already-rendered"
        display._last_pushed_screen_signature = ("MENU", "Home", "menu")
        display._physical_update_count = 49

        self.assertEqual(
            display._classify_update("menu_navigation", ("MENU", "Home", "menu"))[:3],
            ("partial", False, "same_layout_partial_reason"),
        )

    def test_non_menu_update_still_cleans_after_partial_streak_limit(self) -> None:
        display = self.make_display()
        display._last_pushed_hash = "already-rendered"
        display._last_pushed_screen_signature = ("HOME", "Clock", "idle_home")
        display._partial_since_clean = 8

        self.assertEqual(
            display._classify_update("playback_toggle", ("HOME", "Clock", "idle_home"))[:2],
            ("full", True),
        )

    def test_partial_streak_cleanup_uses_live_full_clear_not_one_shot(self) -> None:
        display = self.make_display()
        display._last_pushed_hash = "already-rendered"
        display._last_pushed_screen_signature = ("AMBIENT", "Ambient", "ambient")
        display._partial_since_clean = 8

        update_mode, clean_refresh, policy = display._classify_update(
            "clock_refresh",
            ("AMBIENT", "Ambient", "ambient"),
        )

        self.assertEqual((update_mode, clean_refresh, policy), ("full", True, "partial_streak_limit"))
        self.assertFalse(display._should_one_shot_major_transition(update_mode, clean_refresh, policy))

    def test_clock_partial_disabled_uses_live_full_write_not_one_shot(self) -> None:
        display = self.make_display()
        display._last_pushed_hash = "already-rendered"
        display._last_pushed_screen_signature = ("AMBIENT", "Ambient", "ambient")

        update_mode, clean_refresh, policy = display._classify_update(
            "clock_refresh",
            ("AMBIENT", "Ambient", "ambient"),
        )

        self.assertEqual((update_mode, clean_refresh, policy), ("full", False, "clock_partial_disabled"))
        self.assertFalse(display._should_one_shot_major_transition(update_mode, clean_refresh, policy))

    def test_periodic_full_clear_uses_live_full_clear_not_one_shot(self) -> None:
        display = self.make_display()
        display._last_pushed_hash = "already-rendered"
        display._last_pushed_screen_signature = ("AMBIENT", "Ambient", "ambient")
        display._physical_update_count = 49

        update_mode, clean_refresh, policy = display._classify_update(
            "clock_refresh",
            ("AMBIENT", "Ambient", "ambient"),
        )

        self.assertEqual((update_mode, clean_refresh, policy), ("full", True, "periodic_full_clear"))
        self.assertFalse(display._should_one_shot_major_transition(update_mode, clean_refresh, policy))

    def test_screen_transition_still_uses_one_shot_major_transition(self) -> None:
        display = self.make_display()
        display._last_pushed_hash = "already-rendered"
        display._last_pushed_screen_signature = ("AMBIENT", "Ambient", "ambient")

        update_mode, clean_refresh, policy = display._classify_update(
            "active_mode_enter",
            ("HOME", "Home", "idle_home"),
        )

        self.assertEqual((update_mode, clean_refresh, policy), ("full", True, "screen_mode_or_title_changed"))
        self.assertTrue(display._should_one_shot_major_transition(update_mode, clean_refresh, policy))

    def test_partial_streak_limit_is_configurable(self) -> None:
        display = SimulatorDisplay(
            renderer=None,
            output_path=Path(tempfile.mkdtemp()) / "screen.png",
            partial_update_enabled=True,
            partial_streak_limit=2,
            menu_navigation_update_mode="partial",
        )
        display._last_pushed_hash = "already-rendered"
        display._last_pushed_screen_signature = ("MENU", "Home", "menu")
        display._partial_since_clean = 2

        self.assertEqual(
            display._classify_update("menu_navigation", ("MENU", "Home", "menu"))[:2],
            ("partial", False),
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

    def test_audio_start_grace_defers_one_shot_physical_update_until_expired(self) -> None:
        physical = FakePhysicalDisplay()
        display = SimulatorDisplay(
            renderer=None,
            output_path=Path(tempfile.mkdtemp()) / "screen.png",
            physical_display=physical,
            one_shot_major_transitions=True,
            audio_start_display_grace_ms=5000,
        )
        display._last_pushed_hash = "old-hash"
        display._last_pushed_screen_signature = ("HOME", "Clock", "idle_home")

        display.begin_audio_start_grace()
        display._request_physical_update(
            "playback-hash",
            "source_change",
            ("HOME", "Sleep Baseball", "playback_home"),
        )

        self.assertEqual(physical.one_shot_calls, [])
        self.assertEqual(physical.render_path_calls, [])
        self.assertEqual(display._pending_hash, "playback-hash")
        self.assertTrue(display._pending_one_shot)
        self.assertTrue(display._pending_deferred_by_audio_grace)

        display._audio_start_grace_deadline = 0
        display.tick()

        self.assertEqual(len(physical.one_shot_calls), 1)
        self.assertEqual(physical.one_shot_calls[0][1:], ("source_change", "playback-hash"))
        self.assertIsNone(display._pending_hash)

    def test_audio_start_grace_keeps_latest_pending_screen_only(self) -> None:
        physical = FakePhysicalDisplay()
        display = SimulatorDisplay(
            renderer=None,
            output_path=Path(tempfile.mkdtemp()) / "screen.png",
            physical_display=physical,
            one_shot_major_transitions=True,
            audio_start_display_grace_ms=5000,
        )
        display._last_pushed_hash = "old-hash"
        display._last_pushed_screen_signature = ("HOME", "Clock", "idle_home")

        display.begin_audio_start_grace()
        display._request_physical_update(
            "first-hash",
            "source_change",
            ("HOME", "Bible in a Year", "playback_home"),
        )
        display._request_physical_update(
            "latest-hash",
            "playback_toggle",
            ("HOME", "Bible in a Year", "playback_home"),
        )
        display._audio_start_grace_deadline = 0
        display.tick()

        self.assertEqual(len(physical.one_shot_calls), 1)
        self.assertEqual(physical.one_shot_calls[0][2], "latest-hash")

    def test_zero_audio_start_grace_preserves_immediate_physical_update(self) -> None:
        physical = FakePhysicalDisplay()
        display = SimulatorDisplay(
            renderer=None,
            output_path=Path(tempfile.mkdtemp()) / "screen.png",
            physical_display=physical,
            one_shot_major_transitions=True,
            audio_start_display_grace_ms=0,
        )
        display._last_pushed_hash = "old-hash"
        display._last_pushed_screen_signature = ("HOME", "Clock", "idle_home")

        display.begin_audio_start_grace()
        display._request_physical_update(
            "playback-hash",
            "source_change",
            ("HOME", "Sleep Baseball", "playback_home"),
        )

        self.assertEqual(len(physical.one_shot_calls), 1)
        self.assertIsNone(display._pending_hash)

    def test_audio_playback_suppresses_physical_epd_writes(self) -> None:
        physical = FakePhysicalDisplay()
        display = SimulatorDisplay(
            renderer=None,
            output_path=Path(tempfile.mkdtemp()) / "screen.png",
            physical_display=physical,
            one_shot_major_transitions=True,
            suppress_while_audio_playing=True,
        )
        display._last_pushed_hash = "idle-hash"
        display._last_pushed_screen_signature = ("HOME", "Clock", "idle_home")

        display._request_physical_update(
            "playback-hash",
            "source_change",
            ("HOME", "Sleep Baseball", "playback_home"),
            audio_playing=True,
        )

        self.assertEqual(physical.one_shot_calls, [])
        self.assertEqual(physical.render_path_calls, [])
        self.assertEqual(display._pending_hash, "playback-hash")
        self.assertTrue(display._pending_suppressed_by_audio_playback)

    def test_audio_playback_suppression_keeps_newest_pending_screen(self) -> None:
        physical = FakePhysicalDisplay()
        display = SimulatorDisplay(
            renderer=None,
            output_path=Path(tempfile.mkdtemp()) / "screen.png",
            physical_display=physical,
            one_shot_major_transitions=True,
            suppress_while_audio_playing=True,
        )
        display._last_pushed_hash = "idle-hash"
        display._last_pushed_screen_signature = ("HOME", "Clock", "idle_home")

        display._request_physical_update(
            "first-hash",
            "source_change",
            ("HOME", "Bible in a Year", "playback_home"),
            audio_playing=True,
        )
        display._request_physical_update(
            "latest-hash",
            "clock_refresh",
            ("HOME", "Bible in a Year", "playback_home"),
            audio_playing=True,
        )

        self.assertEqual(display._pending_hash, "latest-hash")
        self.assertEqual(physical.one_shot_calls, [])
        self.assertEqual(physical.render_path_calls, [])

    def test_pending_audio_playback_update_flushes_once_after_audio_stops(self) -> None:
        physical = FakePhysicalDisplay()
        display = SimulatorDisplay(
            renderer=None,
            output_path=Path(tempfile.mkdtemp()) / "screen.png",
            physical_display=physical,
            one_shot_major_transitions=True,
            suppress_while_audio_playing=True,
        )
        display._last_pushed_hash = "idle-hash"
        display._last_pushed_screen_signature = ("HOME", "Clock", "idle_home")

        display._request_physical_update(
            "playing-hash",
            "source_change",
            ("HOME", "Sleep Baseball", "playback_home"),
            audio_playing=True,
        )
        display._request_physical_update(
            "paused-hash",
            "playback_toggle",
            ("HOME", "Sleep Baseball", "playback_home"),
            audio_playing=False,
        )

        self.assertEqual(len(physical.one_shot_calls), 1)
        self.assertEqual(physical.one_shot_calls[0][2], "paused-hash")
        self.assertEqual(physical.render_path_calls, [])
        self.assertIsNone(display._pending_hash)

    def test_progress_timer_changes_do_not_write_physical_epd_while_playing(self) -> None:
        physical = FakePhysicalDisplay()
        display = SimulatorDisplay(
            renderer=None,
            output_path=Path(tempfile.mkdtemp()) / "screen.png",
            physical_display=physical,
            suppress_while_audio_playing=True,
        )
        display._last_pushed_hash = "playback-hash"
        display._last_pushed_screen_signature = ("HOME", "Sleep Baseball", "playback_home")

        display._request_physical_update(
            "progress-hash",
            "progress_tick",
            ("HOME", "Sleep Baseball", "playback_home"),
            audio_playing=True,
        )

        self.assertEqual(physical.one_shot_calls, [])
        self.assertEqual(physical.render_path_calls, [])
        self.assertEqual(display._pending_hash, "progress-hash")

    def test_audio_playback_suppression_can_be_disabled(self) -> None:
        physical = FakePhysicalDisplay()
        display = SimulatorDisplay(
            renderer=None,
            output_path=Path(tempfile.mkdtemp()) / "screen.png",
            physical_display=physical,
            one_shot_major_transitions=True,
            audio_start_display_grace_ms=0,
            suppress_while_audio_playing=False,
        )
        display._last_pushed_hash = "idle-hash"
        display._last_pushed_screen_signature = ("HOME", "Clock", "idle_home")

        display._request_physical_update(
            "playback-hash",
            "source_change",
            ("HOME", "Sleep Baseball", "playback_home"),
            audio_playing=True,
        )

        self.assertEqual(len(physical.one_shot_calls), 1)
        self.assertIsNone(display._pending_hash)

    def test_minor_same_screen_menu_update_uses_clean_full_path_by_default(self) -> None:
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

        self.assertIsNone(display._pending_hash)
        self.assertEqual(len(physical.one_shot_calls), 1)

    def test_dirty_regions_merge_for_quick_partial_changes(self) -> None:
        physical = FakePhysicalDisplay()
        display = SimulatorDisplay(
            renderer=None,
            output_path=Path(tempfile.mkdtemp()) / "screen.png",
            physical_display=physical,
            one_shot_major_transitions=True,
            clock_partial_update_enabled=True,
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
