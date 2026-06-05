from __future__ import annotations

import tempfile
import time
import unittest
from datetime import datetime, timedelta
from pathlib import Path

from app.display.base import DisplayAdapter
from app.media_library import MediaLibrary
from app.models import (
    InputEvent,
    MediaItem,
    PlaybackSession,
    PlaybackState,
    PlaybackStatus,
    RenderState,
    UIMode,
)
from app.playback.mock_player import MockPlayer
from app.services.controller import NightstandController
from app.state_store import StateStore


class MemoryDisplay(DisplayAdapter):
    def __init__(self) -> None:
        self.last_state: RenderState | None = None
        self.sleep_calls = 0
        self.shutdown_calls = 0

    def render(self, state: RenderState, reason: str | None = None) -> None:
        self.last_state = state

    def sleep(self) -> None:
        self.sleep_calls += 1

    def shutdown(self) -> None:
        self.shutdown_calls += 1
        self.sleep()


class GraceMemoryDisplay(MemoryDisplay):
    def __init__(self) -> None:
        super().__init__()
        self.audio_grace_calls = 0

    def begin_audio_start_grace(self) -> None:
        self.audio_grace_calls += 1


class SpyPlayer(MockPlayer):
    def __init__(self) -> None:
        super().__init__()
        self.play_calls = 0
        self.status_calls = 0

    def play(self, item: MediaItem, start_position_seconds: float = 0) -> None:
        self.play_calls += 1
        super().play(item, start_position_seconds)

    def status(self) -> PlaybackStatus:
        self.status_calls += 1
        return super().status()


class CountingStopPlayer(MockPlayer):
    def __init__(self) -> None:
        super().__init__()
        self.stop_calls = 0

    def stop(self) -> None:
        self.stop_calls += 1
        super().stop()


class CountingMediaLibrary(MediaLibrary):
    def __init__(self, media_dir: Path, store: StateStore) -> None:
        super().__init__(media_dir, store)
        self.resolved_paths: list[str] = []
        self.scan_source_calls = 0
        self.get_queue_calls = 0
        self.start_background_scan_calls = 0
        self.cancel_background_scan_reasons: list[str] = []

    def resolve_media_path(self, file_path: str) -> Path:
        self.resolved_paths.append(file_path)
        return super().resolve_media_path(file_path)

    def scan_source(self, source_id: str) -> int:
        self.scan_source_calls += 1
        return super().scan_source(source_id)

    def get_queue(self, source_id: str) -> list[MediaItem]:
        self.get_queue_calls += 1
        return super().get_queue(source_id)

    def start_background_scan(self) -> None:
        self.start_background_scan_calls += 1

    def cancel_background_scan(self, reason: str) -> None:
        self.cancel_background_scan_reasons.append(reason)


class PlaybackStreamsTest(unittest.TestCase):
    def make_controller(self, tmp: str) -> NightstandController:
        store = StateStore(Path(tmp) / "test.sqlite")
        store.upsert_media_items(
            [
                MediaItem(
                    source_id="button-1",
                    file_path="demo://bible/001",
                    title="Day 001",
                    sort_key="001",
                    duration_seconds=1,
                ),
                MediaItem(
                    source_id="button-1",
                    file_path="demo://bible/002",
                    title="Day 002",
                    sort_key="002",
                    duration_seconds=60,
                ),
                MediaItem(
                    source_id="button-1",
                    file_path="demo://bible/003",
                    title="Day 003",
                    sort_key="003",
                    duration_seconds=60,
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
        )

    def make_podcast_controller(self, tmp: str) -> NightstandController:
        metadata_dir = Path(tmp) / "media" / "buttons" / "button-1"
        metadata_dir.mkdir(parents=True, exist_ok=True)
        (metadata_dir / ".source.json").write_text(
            '{"display_name":"Daily Podcast","source_type":"podcast"}',
            encoding="utf-8",
        )
        return self.make_controller(tmp)

    def test_source_button_resumes_persistent_playlist_session_after_restart(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            controller = self.make_controller(tmp)
            controller.handle_event(InputEvent("source", "button-1"))
            first = controller.player.status()
            controller.player.pause()
            controller.store.save_playback_session(
                controller.store.get_playback_session("button-1")
            )
            controller.store.update_playback_position(first.item_id, 0.3, completed=False)
            controller._save_session("button-1", first.item_id, 0, 0.3, is_playing=False)

            restarted = self.make_controller(tmp)
            restarted.handle_event(InputEvent("source", "button-1"))
            status = restarted.player.status()

            self.assertEqual(status.item_id, first.item_id)
            self.assertGreaterEqual(status.position_seconds, 0.3)

    def test_same_source_button_toggles_pause_after_normal_start(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            controller = self.make_controller(tmp)

            controller.handle_event(InputEvent("source", "button-1"))
            self.assertEqual(controller.player.status().state, PlaybackState.PLAYING)

            controller.handle_event(InputEvent("source", "button-1"))

            self.assertEqual(controller.player.status().state, PlaybackState.PAUSED)

    def test_duplicate_source_press_after_slow_start_is_ignored(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            controller = self.make_controller(tmp)
            controller._slow_source_start_threshold_seconds = 0
            controller._post_slow_source_start_ignore_seconds = 2

            controller.handle_event(InputEvent("source", "button-1"))
            self.assertEqual(controller.player.status().state, PlaybackState.PLAYING)

            controller.handle_event(InputEvent("source", "button-1"))

            self.assertEqual(controller.player.status().state, PlaybackState.PLAYING)

    def test_startup_restore_never_touches_or_launches_player(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = StateStore(Path(tmp) / "test.sqlite")
            store.upsert_media_items(
                [
                    MediaItem(
                        source_id="button-1",
                        file_path="demo://bible/001",
                        title="Day 001",
                        sort_key="001",
                        duration_seconds=60,
                    )
                ]
            )
            item = store.list_media("button-1")[0]
            store.set_current_source_id("button-1")
            store.save_playback_session(
                PlaybackSession(
                    source_id="button-1",
                    current_track_id=item.id,
                    current_track_index=0,
                    last_position_seconds=12.5,
                    is_playing=True,
                    queue_order=[item.id],
                )
            )
            library = MediaLibrary(Path(tmp) / "media", store)
            player = SpyPlayer()

            controller = NightstandController(
                store=store,
                library=library,
                player=player,
                display=MemoryDisplay(),
                resume_on_startup=True,
                playback_restore_launch=True,
            )

            self.assertEqual(player.play_calls, 0)
            self.assertEqual(player.status_calls, 0)
            status = controller.build_render_state().playback
            self.assertEqual(player.status_calls, 1)
            self.assertEqual(status.source_id, "button-1")
            self.assertEqual(status.title, "Day 001")
            self.assertEqual(status.position_seconds, 12.5)
            self.assertEqual(status.state, PlaybackState.PAUSED)

    def test_startup_restore_can_be_disabled_without_player_touch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = StateStore(Path(tmp) / "test.sqlite")
            store.upsert_media_items(
                [
                    MediaItem(
                        source_id="button-1",
                        file_path="demo://bible/001",
                        title="Day 001",
                        sort_key="001",
                    )
                ]
            )
            item = store.list_media("button-1")[0]
            store.set_current_source_id("button-1")
            store.save_playback_session(
                PlaybackSession(
                    source_id="button-1",
                    current_track_id=item.id,
                    current_track_index=0,
                    last_position_seconds=9,
                    is_playing=True,
                    queue_order=[item.id],
                )
            )
            player = SpyPlayer()

            controller = NightstandController(
                store=store,
                library=MediaLibrary(Path(tmp) / "media", store),
                player=player,
                display=MemoryDisplay(),
                restore_playback_on_startup=False,
            )

            self.assertEqual(player.play_calls, 0)
            self.assertEqual(player.status_calls, 0)
            status = controller.build_render_state().playback
            self.assertEqual(player.status_calls, 1)
            self.assertIsNone(status.source_id)

    def test_source_button_lazy_scans_when_queue_contains_stale_host_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = StateStore(Path(tmp) / "test.sqlite")
            store.upsert_media_items(
                [
                    MediaItem(
                        source_id="button-1",
                        file_path="/Users/benzabs/dev_work/nightstand-audio/media/buttons/button-1/old.mp3",
                        title="Old Host Path",
                        sort_key="old",
                    )
                ]
            )
            media_file = Path(tmp) / "media" / "buttons" / "button-1" / "001-first.mp3"
            media_file.parent.mkdir(parents=True, exist_ok=True)
            media_file.write_text("", encoding="utf-8")
            library = MediaLibrary(Path(tmp) / "media", store)
            controller = NightstandController(
                store=store,
                library=library,
                player=MockPlayer(),
                display=MemoryDisplay(),
                menu_timeout_seconds=15,
                validate_playlist_on_play=True,
            )

            controller.handle_event(InputEvent("source", "button-1"))
            status = controller.player.status()

            self.assertEqual(status.title, "001 First")
            self.assertEqual(
                store.get_source_queue("button-1")[0].file_path,
                "buttons/button-1/001-first.mp3",
            )

    def test_source_button_resolves_only_current_track_before_launch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            media_dir = root / "media"
            button_dir = media_dir / "buttons" / "button-1"
            button_dir.mkdir(parents=True)
            current_file = button_dir / "001-first.mp3"
            current_file.write_text("", encoding="utf-8")
            store = StateStore(root / "test.sqlite")
            store.upsert_media_items(
                [
                    MediaItem(
                        source_id="button-1",
                        file_path=f"buttons/button-1/{index:03d}-track.mp3"
                        if index > 1
                        else "buttons/button-1/001-first.mp3",
                        title=f"Track {index:03d}",
                        sort_key=f"{index:03d}",
                    )
                    for index in range(1, 366)
                ]
            )
            library = CountingMediaLibrary(media_dir, store)
            controller = NightstandController(
                store=store,
                library=library,
                player=MockPlayer(),
                display=MemoryDisplay(),
                validate_playlist_on_play=False,
            )

            controller.handle_event(InputEvent("source", "button-1"))

            self.assertEqual(library.scan_source_calls, 0)
            self.assertEqual(library.resolved_paths, ["buttons/button-1/001-first.mp3"])
            self.assertEqual(library.cancel_background_scan_reasons, ["playback_active"])
            self.assertEqual(controller.player.status().title, "Track 001")

    def test_source_playback_starts_display_audio_grace(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = StateStore(Path(tmp) / "test.sqlite")
            store.upsert_media_items(
                [
                    MediaItem(
                        source_id="button-1",
                        file_path="demo://bible/001",
                        title="Day 001",
                        sort_key="001",
                    )
                ]
            )
            display = GraceMemoryDisplay()
            controller = NightstandController(
                store=store,
                library=MediaLibrary(Path(tmp) / "media", store),
                player=MockPlayer(),
                display=display,
            )

            controller.handle_event(InputEvent("source", "button-1"))

            self.assertEqual(display.audio_grace_calls, 1)

    def test_background_scan_is_skipped_while_playback_active(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = StateStore(Path(tmp) / "test.sqlite")
            library = CountingMediaLibrary(Path(tmp) / "media", store)
            player = MockPlayer()
            player.play(
                MediaItem(
                    source_id="button-1",
                    file_path="demo://bible/001",
                    title="Day 001",
                )
            )
            controller = NightstandController(
                store=store,
                library=library,
                player=player,
                display=MemoryDisplay(),
            )
            controller.start_background_media_scan_after_first_render = True

            controller._start_background_media_scan_if_needed()

            self.assertEqual(library.start_background_scan_calls, 0)

    def test_completion_advances_to_next_track_in_queue(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            controller = self.make_controller(tmp)
            controller.handle_event(InputEvent("source", "button-1"))
            first_id = controller.player.status().item_id

            time.sleep(1.1)
            controller.tick()
            status = controller.player.status()

            self.assertNotEqual(status.item_id, first_id)
            self.assertEqual(status.title, "Day 002")
            self.assertEqual(status.state, PlaybackState.PLAYING)

    def test_sleep_timer_fade_saves_start_position_without_completion_or_advance(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            controller = self.make_controller(tmp)
            controller.store.upsert_media_items(
                [
                    MediaItem(
                        source_id="button-1",
                        file_path="demo://bible/001",
                        title="Day 001",
                        sort_key="001",
                        duration_seconds=100,
                    )
                ]
            )
            controller.sleep_fade_seconds = 10
            controller.sleep_fade_steps = 5
            controller.handle_event(InputEvent("source", "button-1"))
            first_id = controller.player.status().item_id
            controller.player.status().position_seconds = 12.34
            controller.player._started_monotonic = None
            controller.sleep_timer.deadline = datetime.now() - timedelta(seconds=1)

            controller.tick()

            session = controller.store.get_playback_session("button-1")
            self.assertTrue(controller._sleep_transitioning)
            self.assertEqual(session.stop_reason, "sleep")
            self.assertFalse(session.is_playing)
            self.assertAlmostEqual(session.last_position_seconds, 12.34)
            self.assertFalse(controller.store.get_item(first_id).completed)

            controller._sleep_fade_started_at -= 11
            controller.tick()

            session = controller.store.get_playback_session("button-1")
            self.assertFalse(controller._sleep_transitioning)
            self.assertEqual(controller.player.status().state, PlaybackState.STOPPED)
            self.assertEqual(session.current_track_id, first_id)
            self.assertEqual(session.current_track_index, 0)
            self.assertEqual(session.stop_reason, "sleep")
            self.assertAlmostEqual(session.last_position_seconds, 12.34)
            self.assertFalse(controller.store.get_item(first_id).completed)
            self.assertEqual(controller.build_render_state().mode, UIMode.AMBIENT)

            controller.render()
            self.assertGreaterEqual(controller.display.sleep_calls, 1)

    def test_sleep_trigger_is_idempotent_during_fade(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = StateStore(Path(tmp) / "test.sqlite")
            store.upsert_media_items(
                [
                    MediaItem(
                        source_id="button-1",
                        file_path="demo://bible/001",
                        title="Day 001",
                        sort_key="001",
                        duration_seconds=60,
                    )
                ]
            )
            player = CountingStopPlayer()
            controller = NightstandController(
                store=store,
                library=MediaLibrary(Path(tmp) / "media", store),
                player=player,
                display=MemoryDisplay(),
                sleep_fade_seconds=10,
                sleep_fade_steps=5,
            )
            controller.handle_event(InputEvent("source", "button-1"))
            player.status().position_seconds = 8
            player._started_monotonic = None

            controller._begin_sleep_transition("manual_test")
            controller._begin_sleep_transition("duplicate_test")

            self.assertTrue(controller._sleep_transitioning)
            self.assertEqual(player.stop_calls, 0)
            self.assertAlmostEqual(
                store.get_playback_session("button-1").last_position_seconds,
                8,
            )

            controller._sleep_fade_started_at -= 11
            controller.tick()

            self.assertEqual(player.stop_calls, 1)
            self.assertFalse(controller._sleep_transitioning)

    def test_shutdown_stops_audio_saves_position_and_sleeps_display(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = StateStore(Path(tmp) / "test.sqlite")
            store.upsert_media_items(
                [
                    MediaItem(
                        source_id="button-1",
                        file_path="demo://bible/001",
                        title="Day 001",
                        sort_key="001",
                        duration_seconds=60,
                    )
                ]
            )
            player = CountingStopPlayer()
            display = MemoryDisplay()
            controller = NightstandController(
                store=store,
                library=MediaLibrary(Path(tmp) / "media", store),
                player=player,
                display=display,
            )
            controller.handle_event(InputEvent("source", "button-1"))
            player.status().position_seconds = 22.5
            player._started_monotonic = None

            controller.shutdown("sigterm")

            session = store.get_playback_session("button-1")
            self.assertEqual(player.stop_calls, 1)
            self.assertEqual(display.shutdown_calls, 1)
            self.assertEqual(display.sleep_calls, 1)
            self.assertFalse(session.is_playing)
            self.assertAlmostEqual(session.last_position_seconds, 22.5)

    def test_source_button_after_sleep_resumes_pre_fade_position_even_near_eof(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            controller = self.make_controller(tmp)
            first = controller.store.get_source_queue("button-1")[0]
            controller.sleep_fade_seconds = 0
            controller.handle_event(InputEvent("source", "button-1"))
            controller.player.status().position_seconds = 0.96
            controller.player._started_monotonic = None

            controller._begin_sleep_transition("manual_test")
            controller.handle_event(InputEvent("source", "button-1"))
            status = controller.player.status()

            self.assertEqual(status.item_id, first.id)
            self.assertGreaterEqual(status.position_seconds, 0.96)
            self.assertFalse(controller.store.get_item(first.id).completed)

    def test_saved_position_at_eof_advances_before_launch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            controller = self.make_controller(tmp)
            first = controller.store.get_source_queue("button-1")[0]
            controller.store.set_current_source_id("button-1")
            controller.store.save_playback_session(
                PlaybackSession(
                    source_id="button-1",
                    current_track_id=first.id,
                    current_track_index=0,
                    last_position_seconds=0.96,
                    is_playing=False,
                    queue_order=[item.id for item in controller.library.get_queue("button-1")],
                )
            )

            controller.handle_event(InputEvent("source", "button-1"))
            status = controller.player.status()
            completed_first = controller.store.get_item(first.id)

            self.assertTrue(completed_first.completed)
            self.assertEqual(status.title, "Day 002")
            self.assertLess(status.position_seconds, 0.1)

    def test_player_eof_with_null_duration_advances_to_next_track(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = StateStore(Path(tmp) / "test.sqlite")
            store.upsert_media_items(
                [
                    MediaItem(
                        source_id="button-1",
                        file_path="demo://bible/001",
                        title="Day 001",
                        sort_key="001",
                        duration_seconds=None,
                    ),
                    MediaItem(
                        source_id="button-1",
                        file_path="demo://bible/002",
                        title="Day 002",
                        sort_key="002",
                        duration_seconds=None,
                    ),
                ]
            )
            controller = NightstandController(
                store=store,
                library=MediaLibrary(Path(tmp) / "media", store),
                player=MockPlayer(),
                display=MemoryDisplay(),
            )
            controller.handle_event(InputEvent("source", "button-1"))
            first_id = controller.player.status().item_id
            controller.player.status().state = PlaybackState.STOPPED
            controller.player.status().ended = True
            controller.player.status().exit_returncode = 0

            controller.tick()

            self.assertTrue(store.get_item(first_id).completed)
            self.assertEqual(controller.player.status().title, "Day 002")
            self.assertEqual(
                store.get_playback_session("button-1").current_track_index,
                1,
            )

    def test_playlist_exhausted_persists_stopped_complete_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = StateStore(Path(tmp) / "test.sqlite")
            store.upsert_media_items(
                [
                    MediaItem(
                        source_id="button-1",
                        file_path="demo://bible/001",
                        title="Day 001",
                        sort_key="001",
                        duration_seconds=None,
                    )
                ]
            )
            metadata_dir = Path(tmp) / "media" / "buttons" / "button-1"
            metadata_dir.mkdir(parents=True, exist_ok=True)
            (metadata_dir / ".source.json").write_text(
                '{"display_name":"Daily Podcast","source_type":"podcast"}',
                encoding="utf-8",
            )
            controller = NightstandController(
                store=store,
                library=MediaLibrary(Path(tmp) / "media", store),
                player=MockPlayer(),
                display=MemoryDisplay(),
            )
            controller.handle_event(InputEvent("source", "button-1"))
            first_id = controller.player.status().item_id
            controller.player.status().state = PlaybackState.STOPPED
            controller.player.status().ended = True
            controller.player.status().exit_returncode = 0

            controller.tick()

            self.assertTrue(store.get_item(first_id).completed)
            self.assertFalse(store.get_playback_session("button-1").is_playing)
            self.assertTrue(controller.build_render_state().source_complete)

    def test_double_press_next_and_triple_press_restart_or_previous(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            controller = self.make_controller(tmp)
            controller.handle_event(InputEvent("source", "button-1"))
            controller.handle_event(InputEvent("press"))
            controller.handle_event(InputEvent("press"))
            controller._flush_pending_home_press(force=True)

            self.assertEqual(controller.player.status().title, "Day 002")

            controller.player.status().position_seconds = 10
            controller.handle_event(InputEvent("press"))
            controller.handle_event(InputEvent("press"))
            controller.handle_event(InputEvent("press"))
            controller._flush_pending_home_press(force=True)
            self.assertLess(controller.player.status().position_seconds, 1)

            controller.handle_event(InputEvent("press"))
            controller.handle_event(InputEvent("press"))
            controller.handle_event(InputEvent("press"))
            controller._flush_pending_home_press(force=True)
            self.assertEqual(controller.player.status().title, "Day 001")

    def test_progress_label_shows_time_without_repeating_track_context(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            controller = self.make_controller(tmp)
            status = PlaybackStatus(
                state=PlaybackState.PLAYING,
                position_seconds=125,
                track_index=1,
                queue_length=3,
            )

            self.assertEqual(controller._progress_label(status), "2:05")

            status.duration_seconds = 300
            self.assertEqual(controller._progress_label(status), "2:05 / 5:00")

    def test_menu_track_selection_moves_cursor_and_plays_selected_track(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            controller = self.make_controller(tmp)
            controller.handle_event(InputEvent("long_press"))
            self.assertEqual(controller.nav.current_mode, UIMode.MENU)

            controller.handle_event(InputEvent("turn", 1))
            controller.handle_event(InputEvent("press"))
            self.assertEqual(controller.nav.menu_source_id, "button-1")

            controller.handle_event(InputEvent("turn", 2))
            controller.handle_event(InputEvent("press"))
            status = controller.player.status()

            self.assertEqual(status.title, "Day 003")
            self.assertEqual(controller.nav.current_mode, UIMode.HOME)
            self.assertEqual(
                controller.store.get_playback_session("button-1").current_track_index,
                2,
            )

    def test_loop_enabled_source_wraps_at_playlist_end(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            controller = self.make_controller(tmp)
            metadata_dir = Path(tmp) / "media" / "buttons" / "button-1"
            metadata_dir.mkdir(parents=True, exist_ok=True)
            (metadata_dir / ".source.json").write_text('{"loop_enabled": true}', encoding="utf-8")
            controller.library._metadata_cache = {}
            controller._start_track_at_index("button-1", 2)
            controller.player._started_monotonic = None
            controller.player.status().position_seconds = 60
            controller.tick()

            self.assertEqual(controller.player.status().title, "Day 001")

    def test_completed_podcast_stops_and_requires_intentional_restart(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            controller = self.make_podcast_controller(tmp)
            queue = controller.library.get_queue("button-1")
            for item in queue:
                controller.store.update_playback_position(item.id, item.duration_seconds or 60, True)

            controller.handle_event(InputEvent("source", "button-1"))
            state = controller.build_render_state()

            self.assertTrue(state.source_complete)
            self.assertEqual(state.completed_count, 3)
            self.assertEqual(controller.player.status().state, PlaybackState.STOPPED)

            controller.open_track_menu("button-1")
            self.assertEqual(controller.nav.current_menu[0].label, "Restart Playlist")
            controller.handle_event(InputEvent("press"))

            self.assertFalse(controller.library.is_source_complete("button-1"))
            self.assertEqual(controller.player.status().title, "Day 001")

    def test_media_track_list_turn_only_moves_cached_selection(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = StateStore(Path(tmp) / "test.sqlite")
            store.upsert_media_items(
                [
                    MediaItem(
                        source_id="button-1",
                        file_path="demo://bible/001",
                        title="Day 001",
                        sort_key="001",
                    ),
                    MediaItem(
                        source_id="button-1",
                        file_path="demo://bible/002",
                        title="Day 002",
                        sort_key="002",
                    ),
                ]
            )
            library = CountingMediaLibrary(Path(tmp) / "media", store)
            controller = NightstandController(
                store=store,
                library=library,
                player=MockPlayer(),
                display=MemoryDisplay(),
            )
            controller._enter_active_mode("test")
            controller.open_track_menu("button-1")
            controller._dirty = False
            controller._dirty_reason = None
            get_queue_calls = library.get_queue_calls
            scan_source_calls = library.scan_source_calls

            controller.handle_event(InputEvent("turn", 1))

            self.assertEqual(controller.nav.selected_index, 1)
            self.assertEqual(library.get_queue_calls, get_queue_calls)
            self.assertEqual(library.scan_source_calls, scan_source_calls)
            self.assertEqual(controller._dirty_reason, "menu_navigation")


if __name__ == "__main__":
    unittest.main()
