from __future__ import annotations

import time
from contextlib import nullcontext
from datetime import datetime, time as clock_time
from pathlib import Path

from app.display.base import DisplayAdapter
from app.input.base import InputAdapter
from app.input.keyboard_input import KeyboardInput
from app.media_library import MediaLibrary
from app.models import (
    InputEvent,
    MediaCommand,
    MenuItem,
    PlaybackSession,
    PlaybackState,
    PlaybackStatus,
    RenderState,
    UIMode,
)
from app.playback.base import PlaybackAdapter
from app.services.alarm import AlarmService
from app.services.bluetooth import BluetoothManager
from app.services.logger import get_logger
from app.services.navigation import NavigationController, NavigationResult
from app.services.sleep_timer import SleepTimer
from app.state_store import StateStore


class NightstandController:
    def __init__(
        self,
        store: StateStore,
        library: MediaLibrary,
        player: PlaybackAdapter,
        display: DisplayAdapter,
        keyboard: InputAdapter | None = None,
        menu_timeout_seconds: int = 15,
        clock_refresh_seconds: int = 60,
        disable_clock_auto_refresh: bool = False,
        night_mode_enabled: bool = True,
        night_mode_start: str = "22:00",
        night_mode_end: str = "06:00",
        night_mode_wake_timeout_seconds: int = 30,
        night_mode_display_lock: bool = True,
        ambient_mode_enabled: bool = True,
        active_mode_timeout_seconds: int = 30,
        ambient_clock_refresh_seconds: int = 60,
        ambient_show_playback_glyph: bool = True,
        restore_playback_on_startup: bool = True,
        resume_on_startup: bool = False,
        playback_restore_launch: bool = False,
    ) -> None:
        self.store = store
        self.library = library
        self.player = player
        self.display = display
        self.keyboard = keyboard
        self.sleep_timer = SleepTimer()
        self.alarm = AlarmService(store, library, player)
        self.bluetooth = BluetoothManager(store)
        self.nav = NavigationController(menu_timeout_seconds)
        self.log_input = get_logger("INPUT")
        self.log_state = get_logger("STATE")
        self.log_playback = get_logger("PLAYBACK")
        self.log_display = get_logger("DISPLAY")
        self.log_sim = get_logger("SIM")
        self.log_night = get_logger("NIGHT")
        self.log_ambient = get_logger("AMBIENT")
        self.log_active = get_logger("ACTIVE")
        self.clock_refresh_seconds = max(0, clock_refresh_seconds)
        self.disable_clock_auto_refresh = disable_clock_auto_refresh
        self.night_mode_enabled = night_mode_enabled
        self.night_mode_start = self._parse_night_time(night_mode_start, clock_time(22, 0))
        self.night_mode_end = self._parse_night_time(night_mode_end, clock_time(6, 0))
        self.night_mode_wake_timeout_seconds = max(1, night_mode_wake_timeout_seconds)
        self.night_mode_display_lock = night_mode_display_lock
        self.ambient_mode_enabled = ambient_mode_enabled
        self.active_mode_timeout_seconds = max(1, active_mode_timeout_seconds)
        self.ambient_clock_refresh_seconds = max(0, ambient_clock_refresh_seconds)
        self.ambient_show_playback_glyph = ambient_show_playback_glyph
        self.restore_playback_on_startup = restore_playback_on_startup
        self.resume_on_startup = resume_on_startup
        self.playback_restore_launch = playback_restore_launch
        self._startup_initializing = True
        self.is_night_mode_active = False
        self.is_sleep_screen_locked = False
        self.last_display_wake_at: datetime | None = None
        self.is_ambient_mode_active = False
        self.is_active_mode_active = False
        self.last_active_interaction_at: datetime | None = None
        self._last_playback_was_playing = False
        self.current_source_id: str | None = None
        self.output_label = self.bluetooth.output_label()
        self._dirty = True
        self._dirty_reason = "startup"
        self._last_clock_refresh_at = 0.0
        self._last_position_save = 0.0
        self._last_completed_item_id: int | None = None
        self._pending_home_press_count = 0
        self._last_home_press_at = 0.0
        self._press_window_seconds = 0.45
        self._source_button_click_count = 0
        self._last_source_button_click_at = 0.0
        self._source_button_click_window_seconds = 1.0
        self._last_logged_mode = self.nav.current_mode
        self._last_logged_playback_state: PlaybackState | None = None
        self._restored_status: PlaybackStatus | None = None
        self.startup_profiler = None
        self._startup_summary_logged = False
        self.start_background_media_scan_after_first_render = False
        self._background_media_scan_started = False
        self._restore_active_session()
        self._startup_initializing = False
        self._refresh_main_menu_labels()
        self._evaluate_night_mode(datetime.now(), initial=True)
        if not self.is_night_mode_active:
            self._enter_ambient_mode("startup")

    def run(self) -> None:
        self.log_sim.info("Simulator startup. Press q to quit.")
        output_path = getattr(self.display, "output_path", "configured display")
        self.log_sim.info("Rendering UI to %s", output_path)
        max_steps = int(__import__("os").environ.get("NIGHTSTAND_SIM_STEPS", "0"))
        steps = 0
        keyboard = self.keyboard or KeyboardInput()
        input_context = keyboard.raw_mode() if hasattr(keyboard, "raw_mode") else nullcontext()
        try:
            with input_context:
                while True:
                    now = datetime.now()
                    event = keyboard.poll(0.25)
                    if event and self.handle_event(event):
                        break
                    self.tick(now)
                    steps += 1
                    if max_steps and steps >= max_steps:
                        break
        except KeyboardInterrupt:
            self.log_sim.info("Simulator interrupted by Ctrl+C.")
            self._save_position()
        finally:
            self.shutdown()

    def shutdown(self) -> None:
        self._save_position()
        self.display.shutdown()
        self.log_sim.info("Simulator shutdown complete.")

    def handle_event(self, event: InputEvent) -> bool:
        self._log_input_event(event)
        if event.type != "press":
            self._flush_pending_home_press(force=True)
        if event.type != "source":
            self._source_button_click_count = 0

        if event.type == "quit":
            self._save_position()
            return True
        if self._handle_sleep_screen_locked_event(event):
            return False
        if self._handle_ambient_event(event):
            return False
        if self.is_night_mode_active and not self.is_sleep_screen_locked:
            self._touch_display_wake()
        if self.is_active_mode_active:
            self._touch_active_interaction()
        if event.type == "media_command":
            self.handle_media_command(event.value)
            return False
        if event.type == "source":
            if self.alarm.runtime.active:
                self.alarm.snooze()
                self._mark_dirty("alarm_toggle")
                return False
            if self._record_source_button_click():
                self.bluetooth.begin_reconnect()
                self._mark_dirty("bluetooth_reconnect")
                return False
            self.log_input.info("Button source selected source=%s", event.value)
            if not self.is_night_mode_active:
                self._enter_active_mode("button_press")
            self._handle_source_selection(str(event.value))
            return False
        if event.type == "turn":
            self._apply_nav_result(self.nav.handle_turn(int(event.value or 0)))
        elif event.type == "press":
            if self.alarm.runtime.active:
                self.alarm.stop()
                self.nav.go_home()
                self._mark_dirty("major_layout_transition")
            elif self.nav.current_mode == UIMode.HOME:
                self._queue_home_press()
            else:
                self._apply_nav_result(self.nav.handle_press())
        elif event.type == "long_press":
            self._pending_home_press_count = 0
            previous_mode = self.nav.current_mode
            self._apply_nav_result(self.nav.handle_long_press())
            if self.nav.current_mode != previous_mode:
                self._dirty_reason = "major_layout_transition"
            if self.nav.current_mode == UIMode.MENU and self.nav.menu_source_id is None:
                self._refresh_main_menu_labels()
        elif event.type == "play_pause":
            self.toggle_play_pause_or_resume()
        elif event.type == "sleep_timer":
            self.cycle_sleep_timer()
        elif event.type == "alarm_toggle":
            self.alarm.toggle_enabled()
            self._mark_dirty("alarm_toggle")
        elif event.type == "alarm_adjust":
            self.alarm.adjust_time(int(event.value or 0))
            self._mark_dirty("alarm_adjust")
        elif event.type == "snooze":
            self.alarm.snooze()
            self._mark_dirty("alarm_toggle")
        elif event.type == "stop_alarm":
            self.alarm.stop()
            self._mark_dirty("major_layout_transition")
        elif event.type == "render":
            self._mark_dirty("manual_render")
        elif event.type == "bluetooth_reconnect":
            self.bluetooth.begin_reconnect()
            self._mark_dirty("bluetooth_reconnect")
        elif event.type == "bluetooth_success":
            self.bluetooth.begin_reconnect()
            self.bluetooth.fake_success()
            self._mark_dirty("bluetooth_reconnect")
        elif event.type == "bluetooth_failure":
            self.bluetooth.begin_reconnect()
            self.bluetooth.fake_failure()
            self._mark_dirty("bluetooth_reconnect")
        return False

    def handle_media_command(self, command: MediaCommand | str | int | None) -> None:
        if isinstance(command, str):
            command = MediaCommand(command)
        if command == MediaCommand.PLAY_PAUSE:
            self.toggle_play_pause_or_resume()
        elif command == MediaCommand.NEXT_TRACK:
            self.next_track()
        elif command == MediaCommand.PREVIOUS_TRACK:
            self.previous_or_restart_track()
        elif command == MediaCommand.VOLUME_UP:
            self.player.adjust_volume(4)
            self._mark_dirty("volume_change")
        elif command == MediaCommand.VOLUME_DOWN:
            self.player.adjust_volume(-4)
            self._mark_dirty("volume_change")

    def tick(self, now: datetime | None = None) -> None:
        now = now or datetime.now()
        self._flush_pending_home_press()
        self.player.tick()
        self._handle_completed_playback()
        self._evaluate_night_mode(now)
        self._track_playback_passive_transition(now)
        if self.alarm.tick(now):
            self._mark_dirty("major_layout_transition")
        if self.bluetooth.tick(now):
            self._mark_dirty("bluetooth_reconnect")
        if self.sleep_timer.expired(now):
            self.player.pause()
            self.sleep_timer.clear()
            self._save_session_from_status(is_playing=False)
            self.log_state.info("Sleep timer expired; playback paused.")
            self._mark_dirty("playback_stop")
        if self.nav.timeout_to_home(now):
            self._mark_dirty("menu_timeout")
        self._return_to_passive_if_due(now)

        if self._clock_refresh_due():
            self._mark_dirty("clock_refresh")

        if time.monotonic() - self._last_position_save > 5:
            self._save_position()
            self._last_position_save = time.monotonic()

        if self._dirty:
            self.render()
        self.display.tick()

    def render(self) -> None:
        state = self.build_render_state()
        self._log_render_state(state)
        reason = self._dirty_reason or "state_changed"
        self.log_display.debug(
            "Render triggered mode=%s source=%s reason=%s",
            state.mode.value,
            state.current_source_label,
            reason,
        )
        self.display.render(state, reason=reason)
        if reason == "startup" and self.startup_profiler and not self._startup_summary_logged:
            self.startup_profiler.total()
            self._startup_summary_logged = True
            self._start_background_media_scan_if_needed()
        self._dirty = False
        self._dirty_reason = None
        self._last_clock_refresh_at = time.monotonic()

    def _mark_dirty(self, reason: str) -> None:
        if self._suppress_display_refresh(reason):
            self.log_night.info(
                "Tactile action handled without display wake reason=%s",
                reason,
            )
            return
        self._dirty = True
        if not self._dirty_reason:
            self._dirty_reason = reason

    def _force_dirty(self, reason: str) -> None:
        self._dirty = True
        if not self._dirty_reason:
            self._dirty_reason = reason

    def _clock_refresh_due(self) -> bool:
        refresh_seconds = (
            self.ambient_clock_refresh_seconds
            if self.is_ambient_mode_active
            else self.clock_refresh_seconds
        )
        if self.disable_clock_auto_refresh or refresh_seconds <= 0:
            return False
        if self._last_clock_refresh_at <= 0:
            return False
        return time.monotonic() - self._last_clock_refresh_at >= refresh_seconds

    def _handle_sleep_screen_locked_event(self, event: InputEvent) -> bool:
        if not self._sleep_screen_controls_locked():
            return False
        if event.type == "turn":
            self.player.adjust_volume(-int(event.value or 0) * 4)
            self.log_night.info("Tactile action handled without display wake reason=volume_change")
            return True
        if event.type == "press":
            self._queue_home_press()
            return True
        if event.type == "long_press":
            self.log_night.info("Tactile action handled without display wake reason=long_press_ignored")
            return True
        if event.type == "sleep_timer":
            self.sleep_timer.cycle()
            self.log_state.info("Sleep timer changed label=%s", self.sleep_timer.label())
            self.log_night.info("Tactile action handled without display wake reason=sleep_timer")
            return True
        return False

    def _handle_ambient_event(self, event: InputEvent) -> bool:
        if not self.is_ambient_mode_active:
            return False
        if event.type == "press":
            self._enter_active_mode("knob_press")
            return True
        if event.type == "long_press":
            self._enter_active_mode("menu_request")
            self._apply_nav_result(self.nav.handle_long_press())
            return True
        if event.type == "turn":
            self._enter_active_mode("knob_turn")
            self._apply_nav_result(self.nav.handle_turn(int(event.value or 0)))
            return True
        if event.type == "source":
            if self._record_source_button_click():
                self.bluetooth.begin_reconnect()
                self._enter_active_mode("bluetooth_reconnect")
                self._mark_dirty("bluetooth_reconnect")
                return True
            self.log_input.info("Button source selected source=%s", event.value)
            self._enter_active_mode("button_press")
            self._handle_source_selection(str(event.value))
            return True
        if event.type == "play_pause":
            self._enter_active_mode("play_pause")
            self.toggle_play_pause_or_resume()
            return True
        if event.type == "media_command":
            self._enter_active_mode("media_command")
            self.handle_media_command(event.value)
            return True
        if event.type in {
            "sleep_timer",
            "alarm_toggle",
            "alarm_adjust",
            "snooze",
            "stop_alarm",
            "bluetooth_reconnect",
            "bluetooth_success",
            "bluetooth_failure",
            "render",
        }:
            self._enter_active_mode(event.type)
        return False

    def _parse_night_time(self, value: str, default: clock_time) -> clock_time:
        try:
            hour_text, minute_text = value.strip().split(":", 1)
            return clock_time(int(hour_text), int(minute_text))
        except (TypeError, ValueError):
            self.log_night.warning("Invalid night mode time value=%s; using default=%s", value, default)
            return default

    def _evaluate_night_mode(self, now: datetime, initial: bool = False) -> None:
        active = self.night_mode_enabled and self._is_in_night_hours(now)
        if active != self.is_night_mode_active or initial:
            self.log_night.info(
                "Night mode %s start=%s end=%s display_lock=%s",
                "active" if active else "inactive",
                self.night_mode_start.strftime("%H:%M"),
                self.night_mode_end.strftime("%H:%M"),
                self.night_mode_display_lock,
            )
        if active and not self.is_night_mode_active:
            self.is_night_mode_active = True
            if self.night_mode_display_lock:
                self._lock_sleep_screen("night_mode_enter")
        elif not active and self.is_night_mode_active:
            self.is_night_mode_active = False
            was_locked = self.is_sleep_screen_locked
            self.is_sleep_screen_locked = False
            if was_locked:
                self.log_night.info("Night mode inactive; sleep screen unlocked.")
            self.log_night.info("Leaving Night Mode -> Ambient Mode")
            self._enter_ambient_mode("night_mode_exit")
        elif initial and active:
            self.is_night_mode_active = True
            if self.night_mode_display_lock:
                self._lock_sleep_screen("night_mode_enter")

    def _is_in_night_hours(self, now: datetime) -> bool:
        current = now.time().replace(second=0, microsecond=0)
        if self.night_mode_start == self.night_mode_end:
            return True
        if self.night_mode_start < self.night_mode_end:
            return self.night_mode_start <= current < self.night_mode_end
        return current >= self.night_mode_start or current < self.night_mode_end

    def _lock_sleep_screen(self, reason: str) -> None:
        if self.alarm.runtime.active:
            return
        if not self.is_sleep_screen_locked:
            self.log_night.info("Sleep screen locked reason=%s", reason)
        self.is_sleep_screen_locked = True
        self.last_display_wake_at = None
        self.is_ambient_mode_active = False
        self.is_active_mode_active = False
        self.last_active_interaction_at = None
        self.nav.go_home()
        self._force_dirty(reason)

    def _wake_display_from_sleep_screen(self) -> None:
        if not self.is_sleep_screen_locked:
            return
        self.is_sleep_screen_locked = False
        self._touch_display_wake()
        self._enter_active_mode("knob_press")
        self.log_night.info("Display wake requested by knob press")
        self._force_dirty("night_mode_wake")

    def _touch_display_wake(self) -> None:
        self.last_display_wake_at = datetime.now()

    def _enter_ambient_mode(self, reason: str) -> None:
        if not self.ambient_mode_enabled or self.is_night_mode_active:
            return
        if not self.is_ambient_mode_active:
            self.log_ambient.info("Ambient Mode entered reason=%s", reason)
        self.is_ambient_mode_active = True
        self.is_active_mode_active = False
        self.last_active_interaction_at = None
        self.is_sleep_screen_locked = False
        self.nav.go_home()
        self._force_dirty("ambient_mode_enter")

    def _enter_active_mode(self, reason: str) -> None:
        if self.is_sleep_screen_locked:
            self.is_sleep_screen_locked = False
        was_active = self.is_active_mode_active and not self.is_ambient_mode_active
        self.is_ambient_mode_active = False
        self.is_active_mode_active = True
        self._touch_active_interaction()
        if not was_active:
            self.log_active.info("Active Mode entered reason=%s", reason)
            self._force_dirty("active_mode_enter")

    def _touch_active_interaction(self) -> None:
        self.last_active_interaction_at = datetime.now()

    def _return_to_passive_if_due(self, now: datetime) -> None:
        if self.is_night_mode_active:
            self._return_to_sleep_screen_if_due(now)
            return
        self._return_to_ambient_if_due(now)

    def _return_to_sleep_screen_if_due(self, now: datetime) -> None:
        if not self.night_mode_display_lock:
            return
        if self.is_sleep_screen_locked or self.alarm.runtime.active:
            return
        last_wake = self.last_display_wake_at or self.last_active_interaction_at or now
        if (now - last_wake).total_seconds() >= self.night_mode_wake_timeout_seconds:
            self.log_night.info("Active timeout -> Sleep Screen")
            self._lock_sleep_screen("night_mode_timeout")

    def _return_to_ambient_if_due(self, now: datetime) -> None:
        if not self.ambient_mode_enabled or not self.is_active_mode_active:
            return
        if self.player.status().state == PlaybackState.PLAYING:
            return
        last_interaction = self.last_active_interaction_at or now
        if (now - last_interaction).total_seconds() >= self.active_mode_timeout_seconds:
            self.log_active.info("Timeout expired; returning to Ambient")
            self._enter_ambient_mode("active_timeout")

    def _track_playback_passive_transition(self, now: datetime) -> None:
        is_playing = self.player.status().state == PlaybackState.PLAYING
        if is_playing and not self._last_playback_was_playing:
            if self.is_active_mode_active:
                self.log_active.info("Playback started; Active Mode persistence enabled")
        if self._last_playback_was_playing and not is_playing:
            if self.is_active_mode_active and not self.is_night_mode_active:
                self.last_active_interaction_at = now
                self.log_active.info("Playback stopped; scheduling Ambient timeout")
        self._last_playback_was_playing = is_playing

    def _sleep_screen_controls_locked(self) -> bool:
        return self.is_night_mode_active and self.is_sleep_screen_locked and not self.alarm.runtime.active

    def _sleep_screen_should_render(self) -> bool:
        return self._sleep_screen_controls_locked()

    def _render_mode(self) -> UIMode:
        if self._sleep_screen_should_render():
            return UIMode.SLEEP_SCREEN
        if self.is_ambient_mode_active:
            return UIMode.AMBIENT
        return self.nav.current_mode

    def _suppress_display_refresh(self, reason: str) -> bool:
        if not self._sleep_screen_controls_locked():
            return False
        return reason not in {
            "startup",
            "clock_refresh",
            "manual_render",
            "night_mode_enter",
            "night_mode_exit",
            "night_mode_wake",
            "night_mode_timeout",
        }

    def _handle_source_selection(self, source_id: str) -> None:
        self.log_playback.info("Playback flow step=button_press source=%s", source_id)
        status = self.player.status()
        active_source = status.source_id or self.current_source_id
        if active_source == source_id and status.state in {PlaybackState.PLAYING, PlaybackState.PAUSED}:
            self.log_playback.info("Playback flow step=toggle_existing_source source=%s", source_id)
            self.toggle_play_pause_or_resume()
            return
        self.log_playback.info("Playback flow step=start_source source=%s", source_id)
        self.start_source(source_id)

    def start_source(self, source_id: str) -> None:
        self.library.ensure_source_ready(source_id)
        if self.library.is_source_complete(source_id):
            self._show_completed_source(source_id)
            return
        item, index, position = self._session_cursor(source_id)
        if not item:
            return
        self._save_position()
        previous_source = self.current_source_id
        self.current_source_id = source_id
        self.store.set_current_source_id(source_id)
        self._last_completed_item_id = None
        self.log_playback.info(
            "Playback flow step=player_launch source=%s file=%s position=%.1fs",
            source_id,
            item.file_path,
            position,
        )
        if not self._play_item_through_adapter(item, position):
            return
        if item.id:
            self.store.mark_started(item.id)
        self._save_session(source_id, item.id, index, position, is_playing=True)
        self._detail_title = self.library.get_source_label(source_id)
        self._detail_subtitle = "Now playing"
        self.nav.go_home()
        if previous_source != source_id:
            self.log_state.info("Active source changed from=%s to=%s", previous_source, source_id)
        self.log_playback.info(
            "Playlist stream resumed source=%s track_index=%s position=%.1fs",
            source_id,
            index,
            position,
        )
        self._mark_dirty("source_change" if previous_source != source_id else "playback_start")

    def resume_last(self) -> None:
        status = self.player.status()
        if status.item_id:
            self.player.resume()
            self._save_session_from_status(is_playing=True)
            self.nav.go_home()
            self.log_playback.info("Playback resumed from current item.")
            self._mark_dirty("playback_start")
            return
        self.start_source(self.store.get_current_source_id() or "button-1")

    def cycle_sleep_timer(self) -> None:
        self.sleep_timer.cycle()
        self.nav.open_mode(UIMode.SLEEP_TIMER)
        self.log_state.info("Sleep timer changed label=%s", self.sleep_timer.label())
        self._mark_dirty("sleep_timer")

    def build_render_state(self) -> RenderState:
        now = datetime.now()
        status = self._status_with_queue_context()
        source_label = ""
        if status.source_id:
            source_label = self.library.get_source_label(status.source_id)
        selected_source_id = status.source_id or self.current_source_id
        if not source_label and selected_source_id:
            source_label = self.library.get_source_label(selected_source_id)
        return RenderState(
            now=now,
            mode=self._render_mode(),
            playback=status,
            current_source_label=source_label,
            sleep_timer_label=self.sleep_timer.label(now),
            alarm=self.alarm.config,
            alarm_runtime=self.alarm.runtime,
            menu_title=self.nav.menu_title,
            menu_items=self.nav.current_menu,
            selected_index=self.nav.selected_index,
            detail_title=getattr(self, "_detail_title", source_label),
            detail_subtitle=getattr(self, "_detail_subtitle", ""),
            output_label=self.bluetooth.output_label(),
            track_index=status.track_index,
            queue_length=status.queue_length
            if status.queue_length is not None
            else (len(self.library.get_queue(selected_source_id)) if selected_source_id else None),
            progress_label=self._progress_label(status),
            alarm_source_label=self.library.get_source_label(self.alarm.config.source_id),
            source_complete=self.library.is_source_complete(selected_source_id or ""),
            completed_count=self.library.get_completed_count(selected_source_id or "")
            if selected_source_id
            else 0,
            bluetooth=self.bluetooth.state,
            is_night_mode_active=self.is_night_mode_active,
            is_sleep_screen_locked=self.is_sleep_screen_locked,
            last_display_wake_at=self.last_display_wake_at,
            is_ambient_mode_active=self.is_ambient_mode_active,
            is_active_mode_active=self.is_active_mode_active,
            last_active_interaction_at=self.last_active_interaction_at,
            ambient_show_playback_glyph=self.ambient_show_playback_glyph,
        )

    def toggle_play_pause_or_resume(self) -> None:
        status = self.player.status()
        if status.state in {PlaybackState.PLAYING, PlaybackState.PAUSED}:
            self.player.toggle_play_pause()
            self._save_session_from_status(
                is_playing=self.player.status().state == PlaybackState.PLAYING
            )
            self.log_playback.info("Playback toggled new_state=%s", self.player.status().state.value)
        else:
            source_id = self.current_source_id or self.store.get_current_source_id() or "button-1"
            self.start_source(source_id)
        self._mark_dirty("playback_toggle")

    def next_track(self) -> None:
        status = self.player.status()
        source_id = status.source_id or self.current_source_id or self.store.get_current_source_id()
        if not source_id:
            return
        index = self._current_index(source_id, status.item_id)
        self.log_playback.info("Next track requested source=%s current_index=%s", source_id, index)
        self._start_track_at_index(
            source_id,
            index + 1,
            return_home=self.nav.current_mode == UIMode.HOME,
        )

    def previous_or_restart_track(self) -> None:
        status = self.player.status()
        source_id = status.source_id or self.current_source_id or self.store.get_current_source_id()
        if not source_id:
            return
        index = self._current_index(source_id, status.item_id)
        if status.position_seconds > 5:
            item = self.library.get_item_at_index(source_id, index)
            if item:
                if not self._play_item_through_adapter(item, 0):
                    return
                if item.id:
                    self.store.update_playback_position(item.id, 0, completed=False)
                self._save_session(source_id, item.id, index, 0, is_playing=True)
                self.log_playback.info("Restart current track source=%s index=%s", source_id, index)
        else:
            self.log_playback.info("Previous track requested source=%s current_index=%s", source_id, index)
            self._start_track_at_index(
                source_id,
                max(0, index - 1),
                return_home=self.nav.current_mode == UIMode.HOME,
            )
        self._mark_dirty("track_change")

    def open_track_menu(self, source_id: str) -> None:
        queue = self.library.get_queue(source_id)
        session = self.store.get_playback_session(source_id)
        selected_index = 0 if self.library.is_source_complete(source_id) else session.current_track_index
        items = []
        if self.library.is_source_complete(source_id):
            items.append(MenuItem(id=source_id, label="Restart Playlist", action="restart_source"))
        items.extend([
            MenuItem(
                id=str(item.id or index),
                label=self._track_menu_label(item.title, index, item.id == session.current_track_id),
                action="track",
            )
            for index, item in enumerate(queue)
        ])
        self.nav.open_track_menu(
            source_id=source_id,
            title=self.library.get_source_label(source_id),
            items=items,
            selected_index=selected_index,
        )
        self.log_state.info("Screen transition mode=%s title=%s", self.nav.current_mode.value, self.nav.menu_title)
        self._mark_dirty("major_layout_transition")

    def start_track(self, source_id: str, item_id: int) -> None:
        queue = self.library.get_queue(source_id)
        for index, item in enumerate(queue):
            if item.id == item_id:
                self._play_item(source_id, item, index, item.last_position_seconds)
                self.nav.go_home()
                return

    def _apply_nav_result(self, result: NavigationResult) -> None:
        if result.action == "render":
            self._mark_dirty("menu_navigation")
        elif result.action == "volume_delta":
            self.player.adjust_volume(int(result.value or 0))
            self._mark_dirty("volume_change")
        elif result.action == "toggle_play":
            self.toggle_play_pause_or_resume()
        elif result.action == "source":
            self.start_source(str(result.value))
        elif result.action == "open_source_tracks":
            self.open_track_menu(str(result.value))
        elif result.action == "track":
            if self.nav.menu_source_id:
                self.start_track(self.nav.menu_source_id, int(result.value or 0))
        elif result.action == "restart_source":
            self.restart_source(str(result.value))
        elif result.action == "resume_last":
            self.resume_last()
        elif result.action == "sleep_timer":
            self.cycle_sleep_timer()
        elif result.action == "open_sleep_timer":
            self.nav.open_mode(UIMode.SLEEP_TIMER)
            self._mark_dirty("major_layout_transition")
        elif result.action == "open_alarm":
            self.nav.open_mode(UIMode.ALARM)
            self._mark_dirty("major_layout_transition")
        elif result.action == "open_output":
            self.nav.open_mode(UIMode.OUTPUT_SELECT)
            self._mark_dirty("major_layout_transition")
        elif result.action == "alarm_toggle":
            self.alarm.toggle_enabled()
            self._mark_dirty("alarm_toggle")
        elif result.action == "alarm_adjust":
            self.alarm.adjust_time(int(result.value or 0))
            self._mark_dirty("alarm_adjust")

    def _play_item(self, source_id: str, item, index: int, position: float) -> None:
        self._save_position()
        previous_source = self.current_source_id
        self.current_source_id = source_id
        self.store.set_current_source_id(source_id)
        self._last_completed_item_id = None
        if not self._play_item_through_adapter(item, position):
            return
        if item.id:
            self.store.mark_started(item.id)
        self._save_session(source_id, item.id, index, position, is_playing=True)
        self._detail_title = self.library.get_source_label(source_id)
        self._detail_subtitle = "Now playing"
        if previous_source != source_id:
            self.log_state.info("Active source changed from=%s to=%s", previous_source, source_id)
        self.log_playback.info(
            "Track start from menu source=%s index=%s position=%.1fs title=%s",
            source_id,
            index,
            position,
            item.title,
        )
        self._mark_dirty("source_change" if previous_source != source_id else "track_change")

    def _start_track_at_index(self, source_id: str, index: int, return_home: bool = True) -> None:
        item = self.library.get_item_at_index(source_id, index)
        if not item:
            return
        self._play_item(source_id, item, index, item.last_position_seconds)
        if return_home:
            self.nav.go_home()

    def _advance_after_completion(self, source_id: str | None, item_id: int) -> None:
        if not source_id:
            return
        index = self._current_index(source_id, item_id)
        next_item = self.library.get_item_at_index(source_id, index + 1)
        if not next_item and self.library.should_loop(source_id):
            next_item = self.library.get_item_at_index(source_id, 0)
            index = -1
        if not next_item:
            self.player.stop()
            self._save_session(source_id, item_id, index, 0, is_playing=False)
            if self.library.is_source_complete(source_id):
                self.log_playback.info("Playlist complete source=%s", source_id)
                self._show_completed_source(source_id)
            return
        self.log_playback.info("Track complete; advancing source=%s next_index=%s", source_id, index + 1)
        if not self._play_item_through_adapter(next_item, 0):
            return
        if next_item.id:
            self.store.mark_started(next_item.id)
            self.store.update_playback_position(next_item.id, 0, completed=False)
        self._save_session(source_id, next_item.id, index + 1, 0, is_playing=True)

    def _save_position(self) -> None:
        status = self._status_with_queue_context()
        if status.item_id:
            completed = self._is_completed(status)
            self.store.update_playback_position(status.item_id, status.position_seconds, completed)
            self._save_session_from_status(is_playing=status.state == PlaybackState.PLAYING)
            self.log_playback.debug(
                "Playback timestamp source=%s item_id=%s position=%.1fs completed=%s",
                status.source_id,
                status.item_id,
                status.position_seconds,
                completed,
            )

    def _handle_completed_playback(self) -> None:
        status = self._status_with_queue_context()
        if not status.item_id or status.item_id == self._last_completed_item_id:
            return
        if not self._is_completed(status):
            return
        self.store.update_playback_position(status.item_id, status.position_seconds, completed=True)
        self.log_playback.info(
            "Track complete source=%s item_id=%s position=%.1fs",
            status.source_id,
            status.item_id,
            status.position_seconds,
        )
        self._last_completed_item_id = status.item_id
        self._advance_after_completion(status.source_id, status.item_id)
        self._mark_dirty("track_change")

    def _session_cursor(self, source_id: str):
        queue = self.library.get_queue(source_id)
        session = self.store.get_playback_session(source_id)
        item = None
        index = session.current_track_index
        if session.current_track_id:
            found_index = self.library.index_for_item(source_id, session.current_track_id)
            if found_index is not None:
                index = found_index
                item = self.library.get_item_at_index(source_id, index)
        if item is None:
            item = self.library.get_resume_item(source_id)
            index = self.library.index_for_item(source_id, item.id) if item else 0
        if item is None:
            return None, 0, 0.0
        position = (
            session.last_position_seconds
            if session.current_track_id == item.id
            else item.last_position_seconds
        )
        if session.queue_order != [item.id for item in queue if item.id is not None]:
            self._save_session(source_id, item.id, index or 0, position, session.is_playing)
        return item, index or 0, position

    def _current_index(self, source_id: str, item_id: int | None) -> int:
        index = self.library.index_for_item(source_id, item_id)
        if index is not None:
            return index
        return self.store.get_playback_session(source_id).current_track_index

    def _save_session(
        self,
        source_id: str,
        item_id: int | None,
        track_index: int,
        position: float,
        is_playing: bool,
    ) -> None:
        queue = self.library.get_queue(source_id)
        self.store.save_playback_session(
            PlaybackSession(
                source_id=source_id,
                current_track_id=item_id,
                current_track_index=track_index,
                last_position_seconds=position,
                is_playing=is_playing,
                queue_order=[item.id for item in queue if item.id is not None],
            )
        )

    def _save_session_from_status(self, is_playing: bool) -> None:
        status = self._status_with_queue_context()
        if not status.source_id or not status.item_id:
            return
        self._save_session(
            status.source_id,
            status.item_id,
            self._current_index(status.source_id, status.item_id),
            status.position_seconds,
            is_playing,
        )

    def _restore_active_session(self) -> None:
        if not self.restore_playback_on_startup:
            self.log_playback.info(
                "restored_state source=None title=None position=0.0s launch=false disabled=true"
            )
            return
        source_id = self.store.get_current_source_id()
        if not source_id:
            return
        session = self.store.get_playback_session(source_id)
        if not session.current_track_id:
            return
        index = self.library.index_for_item(source_id, session.current_track_id)
        restored_index = index if index is not None else session.current_track_index
        item = self.library.get_item_at_index(source_id, restored_index)
        if not item:
            return
        self.current_source_id = source_id
        self._restored_status = PlaybackStatus(
            state=PlaybackState.PAUSED if session.is_playing else PlaybackState.STOPPED,
            source_id=source_id,
            item_id=item.id,
            title=item.title,
            subtitle=item.artist or "",
            position_seconds=session.last_position_seconds,
            duration_seconds=item.duration_seconds,
            volume=PlaybackStatus().volume,
            track_index=restored_index,
            queue_length=len(self.library.get_queue(source_id)),
        )
        self.log_playback.info(
            "restored_state source=%s title=%s position=%.1fs track_id=%s index=%s "
            "launch=false resume_on_startup=%s playback_restore_launch=%s",
            source_id,
            item.title,
            session.last_position_seconds,
            item.id,
            restored_index,
            str(self.resume_on_startup).lower(),
            str(self.playback_restore_launch).lower(),
        )
        self._mark_dirty("startup")

    def _play_item_through_adapter(self, item, position: float) -> bool:
        if self._startup_initializing:
            self.log_playback.warning(
                "startup playback launch blocked source=%s item_id=%s launch=false",
                item.source_id,
                item.id,
            )
            return False
        resolved_item = self.library.resolve_item(item)
        exists = resolved_item.file_path.startswith("demo://") or Path(resolved_item.file_path).exists()
        self.log_playback.info(
            "launching_current_track source=%s index=%s file=%s exists=%s",
            item.source_id,
            self.library.index_for_item(item.source_id, item.id),
            resolved_item.file_path,
            str(exists).lower(),
        )
        if not exists:
            self.log_playback.error(
                "Playback rejected invalid media path source=%s item_id=%s file=%s exists=false",
                item.source_id,
                item.id,
                resolved_item.file_path,
            )
            return False
        self._restored_status = None
        self.player.play(resolved_item, position)
        return True

    def _start_background_media_scan_if_needed(self) -> None:
        if not self.start_background_media_scan_after_first_render:
            return
        if self._background_media_scan_started:
            return
        self._background_media_scan_started = True
        if self.startup_profiler:
            started = time.perf_counter()
            self.library.start_background_scan()
            self.startup_profiler.record(
                "background_media_scan_start",
                (time.perf_counter() - started) * 1000,
            )
            return
        self.library.start_background_scan()

    def _status_with_queue_context(self) -> PlaybackStatus:
        status = self.player.status()
        if not status.source_id and self._restored_status is not None:
            status = self._restored_status
        if not status.source_id:
            return status
        status.track_index = self._current_index(status.source_id, status.item_id)
        status.queue_length = len(self.library.get_queue(status.source_id))
        return status

    def _queue_home_press(self) -> None:
        self._pending_home_press_count += 1
        self._last_home_press_at = time.monotonic()

    def _record_source_button_click(self) -> bool:
        now = time.monotonic()
        if now - self._last_source_button_click_at > self._source_button_click_window_seconds:
            self._source_button_click_count = 0
        self._source_button_click_count += 1
        self._last_source_button_click_at = now
        if self._source_button_click_count >= 3:
            self._source_button_click_count = 0
            self.log_input.info("Source button triple-click detected; starting Bluetooth reconnect.")
            return True
        self.log_input.debug("Source button click count=%s", self._source_button_click_count)
        return False

    def _flush_pending_home_press(self, force: bool = False) -> None:
        if not self._pending_home_press_count:
            return
        if not force and time.monotonic() - self._last_home_press_at < self._press_window_seconds:
            return
        count = self._pending_home_press_count
        self._pending_home_press_count = 0
        self.log_input.info("Knob home multi-click count=%s", count)
        if self._sleep_screen_controls_locked():
            if count == 1:
                self._wake_display_from_sleep_screen()
            elif count == 2:
                self.log_night.info("Tactile action handled without display wake reason=next_track")
                self.next_track()
            else:
                self.log_night.info("Tactile action handled without display wake reason=previous_track")
                self.previous_or_restart_track()
        elif count == 1:
            self.toggle_play_pause_or_resume()
        elif count == 2:
            self.next_track()
        else:
            self.previous_or_restart_track()

    def _progress_label(self, status: PlaybackStatus) -> str:
        if not status.is_audio_active:
            return ""
        if status.duration_seconds:
            return f"{self._format_time(status.position_seconds)} / {self._format_time(status.duration_seconds)}"
        if status.position_seconds > 0:
            return self._format_time(status.position_seconds)
        return ""

    def _format_time(self, seconds: float) -> str:
        total = max(0, int(seconds))
        return f"{total // 60}:{total % 60:02d}"

    def _track_menu_label(self, title: str, index: int, is_current: bool) -> str:
        prefix = "*" if is_current else " "
        return f"{prefix} {index + 1:02d}. {title}"

    def restart_source(self, source_id: str) -> None:
        self.library.reset_source_progress(source_id)
        self.log_state.info("Playlist progress reset source=%s", source_id)
        self.current_source_id = source_id
        self.store.set_current_source_id(source_id)
        self.start_source(source_id)

    def _show_completed_source(self, source_id: str) -> None:
        self._save_position()
        self.current_source_id = source_id
        self.store.set_current_source_id(source_id)
        self.player.stop()
        self._detail_title = self.library.get_source_label(source_id)
        completed = self.library.get_completed_count(source_id)
        total = len(self.library.get_queue(source_id))
        self._detail_subtitle = f"Completed {completed} / {total} listened"
        self.nav.go_home()
        self.log_playback.info("Playlist complete screen shown source=%s completed=%s total=%s", source_id, completed, total)
        self._mark_dirty("playlist_complete")

    def _is_completed(self, status: PlaybackStatus) -> bool:
        if not status.duration_seconds:
            return False
        threshold = self.library.completion_threshold(status.source_id)
        return status.position_seconds >= status.duration_seconds * threshold

    def _refresh_main_menu_labels(self) -> None:
        for item in self.nav.current_menu:
            if item.action == "open_source_tracks":
                item.label = self.library.get_source_label(item.id)

    def _log_input_event(self, event: InputEvent) -> None:
        if event.type == "turn":
            self.log_input.info("Encoder rotation delta=%s", event.value)
        elif event.type == "press":
            self.log_input.info("Encoder press")
        elif event.type == "long_press":
            self.log_input.info("Encoder long press")
        elif event.type == "source":
            self.log_input.info("Button press source=%s", event.value)
        else:
            self.log_input.debug("Input event type=%s value=%s", event.type, event.value)

    def _log_render_state(self, state: RenderState) -> None:
        if state.mode != self._last_logged_mode:
            self.log_state.info(
                "Screen transition from=%s to=%s title=%s",
                self._last_logged_mode.value,
                state.mode.value,
                state.menu_title,
            )
            self._last_logged_mode = state.mode
        if state.playback.state != self._last_logged_playback_state:
            self.log_state.info(
                "Playback state changed state=%s source=%s title=%s",
                state.playback.state.value,
                state.playback.source_id,
                state.playback.title,
            )
            self._last_logged_playback_state = state.playback.state
