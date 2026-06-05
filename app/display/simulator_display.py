from __future__ import annotations

import hashlib
import os
import time
from dataclasses import dataclass
from pathlib import Path

from app.display.base import DisplayAdapter, ImageDisplayAdapter
from app.display.renderer import EInkRenderer
from app.models import PlaybackState, RenderState, UIMode
from app.services.logger import get_logger


FULL_UPDATE_REASONS = {
    "startup",
    "source_change",
    "track_change",
    "playback_start",
    "playback_stop",
    "playlist_complete",
    "restart_source",
    "major_layout_transition",
    "manual_render",
    "menu_timeout",
    "bluetooth_reconnect",
    "bluetooth_pairing_status",
    "night_mode_enter",
    "night_mode_exit",
    "night_mode_wake",
    "night_mode_timeout",
    "ambient_mode_enter",
    "active_mode_enter",
    "active_timeout",
    "alarm_stage",
    "alarm_stop",
    "alarm_dismissed",
}

PARTIAL_REASON_ALIASES = {
    "alarm_adjust": "alarm_value",
    "alarm_toggle": "alarm_value",
    "clock_refresh": "clock_minute",
    "menu_navigation": "menu_highlight",
    "playback_toggle": "play_pause",
    "sleep_timer": "sleep_timer_value",
    "source_change": "playlist_switch",
    "volume_change": "volume_settled",
}

PARTIAL_UPDATE_REASONS = set(PARTIAL_REASON_ALIASES) - {"source_change"}


@dataclass(frozen=True)
class DirtyRegion:
    name: str
    bounds: tuple[int, int, int, int]

    def as_payload(self) -> dict[str, object]:
        return {"name": self.name, "bounds": self.bounds}


class SimulatorDisplay(DisplayAdapter):
    def __init__(
        self,
        renderer: EInkRenderer,
        output_path: Path,
        physical_display: ImageDisplayAdapter | None = None,
        physical_debounce_ms: int = 750,
        volume_debounce_ms: int = 600,
        refresh_on_volume_change: bool = True,
        full_clear_interval: int = 50,
        partial_update_enabled: bool = True,
        partial_min_interval_ms: int = 500,
        force_full_refresh: bool = False,
        force_clean_refresh: bool = False,
        one_shot_major_transitions: bool = True,
        region_partial_enabled: bool = True,
        partial_streak_limit: int = 8,
        audio_start_display_grace_ms: int = 0,
        suppress_while_audio_playing: bool = True,
        menu_navigation_update_mode: str = "full",
        clock_partial_update_enabled: bool = False,
        playlist_switch_partial_update_enabled: bool = False,
    ) -> None:
        self.renderer = renderer
        self.output_path = output_path
        self.physical_display = physical_display
        self.physical_debounce_seconds = max(0, physical_debounce_ms) / 1000
        self.volume_debounce_seconds = max(0, volume_debounce_ms) / 1000
        self.refresh_on_volume_change = refresh_on_volume_change
        self.full_clear_interval = max(0, full_clear_interval)
        self.partial_update_enabled = partial_update_enabled
        self.partial_min_interval_seconds = max(0, partial_min_interval_ms) / 1000
        self.force_full_refresh = force_full_refresh
        self.force_clean_refresh = force_clean_refresh
        self.one_shot_major_transitions = one_shot_major_transitions
        self.region_partial_enabled = region_partial_enabled
        self.partial_streak_limit = max(1, partial_streak_limit)
        self.audio_start_display_grace_seconds = max(0, audio_start_display_grace_ms) / 1000
        self.suppress_while_audio_playing = suppress_while_audio_playing
        self.menu_navigation_update_mode = _normalize_menu_navigation_update_mode(
            menu_navigation_update_mode
        )
        self.clock_partial_update_enabled = clock_partial_update_enabled
        self.playlist_switch_partial_update_enabled = playlist_switch_partial_update_enabled
        self._last_pushed_hash: str | None = None
        self._pending_hash: str | None = None
        self._pending_reason: str | None = None
        self._pending_screen_signature: tuple[str, str, str] | None = None
        self._pending_dirty_region: DirtyRegion | None = None
        self._pending_update_mode = "full"
        self._pending_clean_refresh = False
        self._pending_one_shot = False
        self._pending_deferred_by_audio_grace = False
        self._pending_suppressed_by_audio_playback = False
        self._pending_requested_at = 0.0
        self._audio_start_grace_deadline = 0.0
        self._audio_playback_active = False
        self._last_push_finished_at = 0.0
        self._last_partial_finished_at = 0.0
        self._last_pushed_screen_signature: tuple[str, str, str] | None = None
        self._last_update_mode: str | None = None
        self._partial_since_clean = 0
        self._physical_update_count = 0
        self._skipped_count = 0
        self._cancelled_pending_count = 0
        self._durations_ms: list[float] = []
        self.log = get_logger("DISPLAY")
        self.epd_log = get_logger("EPD")
        self.startup_profiler = None

    def begin_audio_start_grace(self) -> None:
        if self.audio_start_display_grace_seconds <= 0:
            return
        self._audio_start_grace_deadline = max(
            self._audio_start_grace_deadline,
            time.monotonic() + self.audio_start_display_grace_seconds,
        )

    def render(self, state: RenderState, reason: str | None = None) -> None:
        total_started = time.perf_counter()
        render_started = time.perf_counter()
        image = self.renderer.render(state)
        image_hash = _image_hash(image)
        render_ms = (time.perf_counter() - render_started) * 1000

        png_started = time.perf_counter()
        self.output_path.parent.mkdir(parents=True, exist_ok=True)
        image.save(self.output_path)
        png_ms = (time.perf_counter() - png_started) * 1000
        if self.startup_profiler and reason == "startup":
            self.startup_profiler.record("initial_render_png", render_ms + png_ms)
        self.log.debug("PNG generated path=%s duration_ms=%.1f", self.output_path, png_ms)

        push_ms = 0.0
        if self.physical_display:
            self._audio_playback_active = state.playback.state == PlaybackState.PLAYING
            push_started = time.perf_counter()
            self._request_physical_update(
                image_hash,
                reason or "state_changed",
                _screen_signature(state),
                audio_playing=self._audio_playback_active,
            )
            push_ms = (time.perf_counter() - push_started) * 1000

        total_ms = (time.perf_counter() - total_started) * 1000
        if _show_render_timings():
            message = (
                f"render={render_ms:.1f}ms display_push={push_ms:.1f}ms "
                f"total_refresh={total_ms:.1f}ms"
            )
            print(message)
            self.log.info(message)
        else:
            self.log.debug(
                "Render timings render_ms=%.1f png_ms=%.1f display_push_ms=%.1f total_ms=%.1f",
                render_ms,
                png_ms,
                push_ms,
                total_ms,
            )

    def tick(self) -> None:
        if not self.physical_display or not self._pending_hash:
            return
        if self._should_suppress_for_audio_playback(self._audio_playback_active):
            return
        if self._audio_grace_active():
            return
        if self._pending_deferred_by_audio_grace:
            self.log.info(
                "Audio startup grace period expired; applying deferred display update"
            )
            self._pending_deferred_by_audio_grace = False
            self._flush_physical_update()
            return
        if not self._pending_debounce_elapsed():
            return
        self._flush_physical_update()

    def flush(self) -> None:
        if not self.physical_display or not self._pending_hash:
            return
        if self._should_suppress_for_audio_playback(self._audio_playback_active):
            return
        if self._audio_grace_active():
            return
        if self._pending_deferred_by_audio_grace:
            self.log.info(
                "Audio startup grace period expired; applying deferred display update"
            )
            self._pending_deferred_by_audio_grace = False
        self._flush_physical_update()

    def sleep(self) -> None:
        cancelled = self._cancel_pending_update()
        if cancelled:
            self.log.info("Cancelled pending physical display update before sleep count=%s", cancelled)
        if self.physical_display:
            self.physical_display.sleep()

    def shutdown(self) -> None:
        cancelled = self._cancel_pending_update()
        if cancelled:
            self.log.info("Cancelled pending physical display update during shutdown count=%s", cancelled)
        if self.physical_display:
            self.physical_display.sleep()

    def _request_physical_update(
        self,
        image_hash: str,
        reason: str,
        next_screen: tuple[str, str, str] | None = None,
        audio_playing: bool = False,
    ) -> None:
        apply_suppressed_after_request = (
            self._pending_suppressed_by_audio_playback and not audio_playing
        )
        if reason == "menu_navigation" and self.menu_navigation_update_mode == "skip":
            self._skipped_count += 1
            self.log.info("Skipping physical update for menu_navigation")
            return
        if reason == "volume_change" and not self.refresh_on_volume_change:
            self._skipped_count += 1
            self.log.info("Skipping physical update for volume_change")
            return
        if image_hash == self._last_pushed_hash:
            if apply_suppressed_after_request and self._pending_hash:
                self.log.info("Audio stopped; applying pending physical display update")
                self._flush_physical_update()
                return
            self._skipped_count += 1
            self.epd_log.info(
                "Skipped update reason=image_unchanged source_reason=%s selected_update_mode=skipped previous=%s next=%s",
                reason,
                _format_screen(self._last_pushed_screen_signature),
                _format_screen(next_screen),
            )
            return

        update_mode, clean_refresh, policy_reason = self._classify_update(reason, next_screen)
        dirty_region = (
            self._dirty_region_for_reason(reason)
            if update_mode == "partial" and self.region_partial_enabled
            else None
        )
        one_shot = self._should_one_shot_major_transition(update_mode, clean_refresh, policy_reason)
        if self._should_suppress_for_audio_playback(audio_playing):
            self._store_pending_update(
                image_hash=image_hash,
                reason=reason,
                next_screen=next_screen,
                dirty_region=dirty_region,
                update_mode=update_mode,
                clean_refresh=clean_refresh,
                one_shot=one_shot,
                deferred_by_audio_grace=False,
                suppressed_by_audio_playback=True,
                replace_existing=True,
            )
            self.log.info("Physical update suppressed because audio is playing")
            return

        if one_shot:
            if apply_suppressed_after_request:
                self.log.info("Audio stopped; applying pending physical display update")
            if self._audio_grace_active() and not apply_suppressed_after_request:
                self._store_pending_update(
                    image_hash=image_hash,
                    reason=reason,
                    next_screen=next_screen,
                    dirty_region=dirty_region,
                    update_mode=update_mode,
                    clean_refresh=clean_refresh,
                    one_shot=True,
                    deferred_by_audio_grace=True,
                    suppressed_by_audio_playback=False,
                    replace_existing=False,
                )
                self.log.info(
                    "Physical update deferred during audio startup grace period remaining_ms=%.0f",
                    self._audio_grace_remaining_ms(),
                )
                return
            cancelled = self._cancel_pending_update()
            self.log.info(
                "One-shot major transition requested reason=%s policy=%s pending_updates_cancelled=%s displayed_hash=%s one_shot_major_transition=true",
                reason,
                policy_reason,
                cancelled,
                image_hash,
            )
            self._push_one_shot_major_transition(image_hash, reason, next_screen)
            return

        already_pending = self._pending_hash is not None
        update_mode, clean_refresh, dirty_region = self._store_pending_update(
            image_hash=image_hash,
            reason=reason,
            next_screen=next_screen,
            dirty_region=dirty_region,
            update_mode=update_mode,
            clean_refresh=clean_refresh,
            one_shot=False,
            deferred_by_audio_grace=self._audio_grace_active(),
            suppressed_by_audio_playback=False,
            replace_existing=False,
        )
        self.log.info(
            "EPD refresh classified previous=%s next=%s selected=%s selected_update_mode=%s clean=%s reason=%s normalized_reason=%s policy=%s dirty_region=%s bounds=%s partial_streak=%s",
            _format_screen(self._last_pushed_screen_signature),
            _format_screen(next_screen),
            update_mode.upper(),
            update_mode,
            clean_refresh,
            reason,
            _normalize_partial_reason(reason),
            policy_reason,
            dirty_region.name if dirty_region else "none",
            dirty_region.bounds if dirty_region else None,
            self._partial_since_clean,
        )

        if apply_suppressed_after_request:
            self.log.info("Audio stopped; applying pending physical display update")
            self._flush_physical_update()
            return

        if self._audio_grace_active():
            self.log.info(
                "Physical update deferred during audio startup grace period remaining_ms=%.0f",
                self._audio_grace_remaining_ms(),
            )
            return

        if self._last_pushed_hash is None and reason == "startup":
            self._flush_physical_update()
            return
        if self.physical_debounce_seconds <= 0:
            self._flush_physical_update()
            return

        remaining_ms = self._pending_wait_seconds() * 1000
        if already_pending:
            self.log.info(
                "Physical e-paper update coalesced/debounced reason=%s mode=%s wait_ms=%.0f",
                reason,
                update_mode,
                remaining_ms,
            )
        else:
            self.log.info(
                "Physical e-paper update debounced reason=%s mode=%s wait_ms=%.0f",
                reason,
                update_mode,
                remaining_ms,
            )

    def _flush_physical_update(self) -> None:
        if not self.physical_display or not self._pending_hash:
            return
        reason = self._pending_reason or "state_changed"
        update_mode = self._pending_update_mode
        clean_refresh = self._pending_clean_refresh
        dirty_region = self._pending_dirty_region
        if self._pending_one_shot:
            image_hash = self._pending_hash
            next_screen = self._pending_screen_signature
            result = self._push_one_shot_major_transition(image_hash, reason, next_screen)
            self._clear_pending_update()
            if result is False:
                self.log.warning("Deferred one-shot display update failed reason=%s", reason)
            return
        self.epd_log.info(
            "%s update reason=%s normalized_reason=%s dirty_region=%s bounds=%s partial_streak=%s",
            "Full" if update_mode == "full" else "Partial",
            reason,
            _normalize_partial_reason(reason),
            dirty_region.name if dirty_region else "none",
            dirty_region.bounds if dirty_region else None,
            self._partial_since_clean,
        )
        self.log.info(
            "Physical e-paper update start reason=%s mode=%s clean_refresh=%s dirty_region=%s bounds=%s",
            reason,
            update_mode,
            clean_refresh,
            dirty_region.name if dirty_region else "none",
            dirty_region.bounds if dirty_region else None,
        )
        started = time.perf_counter()
        result = self.physical_display.render_path(
            str(self.output_path),
            update_mode=update_mode,
            reason=reason,
            clean_refresh=clean_refresh,
            region=dirty_region.as_payload() if dirty_region else None,
        )
        push_ms = (time.perf_counter() - started) * 1000
        if self.startup_profiler and reason == "startup":
            self.startup_profiler.record("first_physical_epd_update", push_ms)
        if result is False:
            self._last_push_finished_at = time.monotonic()
            self._clear_pending_update()
            self.log.warning(
                "Physical e-paper update failed reason=%s duration_ms=%.1f",
                reason,
                push_ms,
            )
            return
        self._last_pushed_hash = self._pending_hash
        self._last_pushed_screen_signature = self._pending_screen_signature
        self._clear_pending_update()
        self._last_push_finished_at = time.monotonic()
        if update_mode == "partial":
            self._last_partial_finished_at = self._last_push_finished_at
            self._partial_since_clean += 1
        else:
            self._partial_since_clean = 0
        self._last_update_mode = update_mode
        self._physical_update_count += 1
        self._durations_ms.append(push_ms)
        if len(self._durations_ms) > 20:
            self._durations_ms = self._durations_ms[-20:]
        average_ms = sum(self._durations_ms) / len(self._durations_ms)
        self.log.info(
            "Physical e-paper update finish reason=%s mode=%s duration_ms=%.1f avg_ms=%.1f updates=%s skipped=%s partial_streak=%s",
            reason,
            update_mode,
            push_ms,
            average_ms,
            self._physical_update_count,
            self._skipped_count,
            self._partial_since_clean,
        )
        if reason == "volume_change":
            self.log.info("Debounced volume update final_value=latest")

    def _push_one_shot_major_transition(
        self,
        image_hash: str,
        reason: str,
        next_screen: tuple[str, str, str] | None,
    ) -> bool | None:
        if not self.physical_display:
            return None
        started = time.perf_counter()
        if hasattr(self.physical_display, "one_shot_render_path"):
            result = self.physical_display.one_shot_render_path(
                str(self.output_path),
                reason=reason,
                displayed_hash=image_hash,
            )
        else:
            result = self.physical_display.render_path(
                str(self.output_path),
                update_mode="full",
                reason=reason,
                clean_refresh=True,
            )
        push_ms = (time.perf_counter() - started) * 1000
        if self.startup_profiler and reason == "startup":
            self.startup_profiler.record("first_physical_epd_update", push_ms)
        if result is False:
            self._last_push_finished_at = time.monotonic()
            self.log.warning(
                "One-shot major transition failed reason=%s duration_ms=%.1f displayed_hash=%s",
                reason,
                push_ms,
                image_hash,
            )
            return False
        self._last_pushed_hash = image_hash
        self._last_pushed_screen_signature = next_screen
        self._pending_dirty_region = None
        self._last_push_finished_at = time.monotonic()
        self._last_update_mode = "full"
        self._partial_since_clean = 0
        self._physical_update_count += 1
        self._durations_ms.append(push_ms)
        if len(self._durations_ms) > 20:
            self._durations_ms = self._durations_ms[-20:]
        self.log.info(
            "One-shot major transition complete reason=%s duration_ms=%.1f displayed_hash=%s",
            reason,
            push_ms,
            image_hash,
        )
        return True

    def _cancel_pending_update(self) -> int:
        if not self._pending_hash:
            return 0
        self._clear_pending_update()
        self._cancelled_pending_count += 1
        return 1

    def _clear_pending_update(self) -> None:
        self._pending_hash = None
        self._pending_reason = None
        self._pending_screen_signature = None
        self._pending_dirty_region = None
        self._pending_update_mode = "full"
        self._pending_clean_refresh = False
        self._pending_one_shot = False
        self._pending_deferred_by_audio_grace = False
        self._pending_suppressed_by_audio_playback = False
        self._pending_requested_at = 0.0

    def _store_pending_update(
        self,
        image_hash: str,
        reason: str,
        next_screen: tuple[str, str, str] | None,
        dirty_region: DirtyRegion | None,
        update_mode: str,
        clean_refresh: bool,
        one_shot: bool,
        deferred_by_audio_grace: bool,
        suppressed_by_audio_playback: bool,
        replace_existing: bool,
    ) -> tuple[str, bool, DirtyRegion | None]:
        already_pending = self._pending_hash is not None and not replace_existing
        if already_pending and (self._pending_one_shot or one_shot):
            one_shot = self._pending_one_shot or one_shot
            update_mode = "full"
            clean_refresh = clean_refresh or self._pending_clean_refresh
            dirty_region = None
        elif already_pending and self._pending_update_mode == "full":
            update_mode = "full"
            clean_refresh = clean_refresh or self._pending_clean_refresh
            dirty_region = None
        elif already_pending and self._pending_update_mode == "partial" and update_mode == "partial":
            dirty_region = self._merge_dirty_regions(self._pending_dirty_region, dirty_region)
        self._pending_hash = image_hash
        self._pending_reason = reason
        self._pending_screen_signature = next_screen
        self._pending_dirty_region = dirty_region
        self._pending_update_mode = update_mode
        self._pending_clean_refresh = clean_refresh
        self._pending_one_shot = one_shot
        self._pending_deferred_by_audio_grace = (
            self._pending_deferred_by_audio_grace or deferred_by_audio_grace
        )
        self._pending_suppressed_by_audio_playback = (
            self._pending_suppressed_by_audio_playback or suppressed_by_audio_playback
        )
        self._pending_requested_at = time.monotonic()
        return update_mode, clean_refresh, dirty_region

    def _should_suppress_for_audio_playback(self, audio_playing: bool) -> bool:
        return self.suppress_while_audio_playing and audio_playing

    def _audio_grace_active(self) -> bool:
        return self._audio_grace_remaining_ms() > 0

    def _audio_grace_remaining_ms(self) -> float:
        if self._audio_start_grace_deadline <= 0:
            return 0.0
        return max(0.0, (self._audio_start_grace_deadline - time.monotonic()) * 1000)

    def _pending_debounce_elapsed(self) -> bool:
        wait_seconds = self._pending_wait_seconds()
        if wait_seconds <= 0:
            return True
        return time.monotonic() - self._pending_requested_at >= wait_seconds

    def _pending_wait_seconds(self) -> float:
        if self._pending_reason == "volume_change":
            wait_seconds = self.volume_debounce_seconds
        elif self._pending_reason == "sleep_timer":
            wait_seconds = max(0.5, self.partial_min_interval_seconds)
        elif self._pending_reason == "menu_navigation":
            wait_seconds = max(0.5, self.partial_min_interval_seconds)
        else:
            wait_seconds = self.physical_debounce_seconds
        if self._pending_update_mode == "partial" and self._last_partial_finished_at > 0:
            partial_remaining = self.partial_min_interval_seconds - (
                time.monotonic() - self._last_partial_finished_at
            )
            wait_seconds = max(wait_seconds, partial_remaining)
        return max(0.0, wait_seconds)

    def _classify_update(
        self,
        reason: str,
        next_screen: tuple[str, str, str] | None = None,
    ) -> tuple[str, bool, str]:
        if self.force_clean_refresh:
            return "full", True, "force_clean_refresh"
        if self.force_full_refresh or not self.partial_update_enabled:
            return "full", False, "partial_disabled_or_forced_full"
        next_update_number = self._physical_update_count + 1
        if (
            self.full_clear_interval
            and next_update_number > 1
            and next_update_number % self.full_clear_interval == 0
            and reason != "menu_navigation"
        ):
            return "full", True, "periodic_full_clear"
        if self._last_pushed_hash is None:
            return "full", False, "startup_or_first_render"
        if self._partial_since_clean >= self.partial_streak_limit and reason != "menu_navigation":
            return "full", True, "partial_streak_limit"
        if reason == "bluetooth_pairing_status":
            return "full", True, "bluetooth_pairing_status_clean"
        if self._last_pushed_screen_signature != next_screen:
            if self._allows_same_layout_playlist_partial(reason, next_screen):
                return "partial", False, "same_playback_layout_playlist_switch"
            clean = self._last_pushed_screen_signature is not None
            return "full", clean, "screen_mode_or_title_changed"
        if reason == "menu_navigation":
            if self.menu_navigation_update_mode == "partial":
                return "partial", False, "same_layout_partial_reason"
            return "full", True, "menu_navigation_full_clean"
        if reason == "clock_refresh" and not self.clock_partial_update_enabled:
            return "full", False, "clock_partial_disabled"
        if self._partial_since_clean >= self.partial_streak_limit:
            return "partial", False, "menu_navigation_defers_partial_streak_cleanup"
        if reason in FULL_UPDATE_REASONS:
            clean = reason != "startup" and self._last_update_mode == "partial"
            return "full", clean, "explicit_full_reason"
        if reason in PARTIAL_UPDATE_REASONS:
            return "partial", False, "same_layout_partial_reason"
        return "full", self._last_update_mode == "partial", "unclassified_full_safety"

    def _dirty_region_for_reason(self, reason: str) -> DirtyRegion:
        region_name = {
            "menu_navigation": "menu_list",
            "sleep_timer": "sleep_timer_value",
            "volume_change": "bottom_bar",
            "alarm_adjust": "main_content",
            "alarm_toggle": "main_content",
            "playback_toggle": "bottom_bar",
            "clock_refresh": "clock",
        }.get(reason, "main_content")
        return DirtyRegion(region_name, _region_bounds(region_name, self.renderer))

    def _merge_dirty_regions(
        self,
        current: DirtyRegion | None,
        incoming: DirtyRegion | None,
    ) -> DirtyRegion | None:
        if current is None:
            return incoming
        if incoming is None:
            return current
        left = min(current.bounds[0], incoming.bounds[0])
        top = min(current.bounds[1], incoming.bounds[1])
        right = max(current.bounds[2], incoming.bounds[2])
        bottom = max(current.bounds[3], incoming.bounds[3])
        if current.name == incoming.name:
            name = current.name
        else:
            name = f"{current.name}+{incoming.name}"
        return DirtyRegion(name, (left, top, right, bottom))

    def _allows_same_layout_playlist_partial(
        self,
        reason: str,
        next_screen: tuple[str, str, str] | None,
    ) -> bool:
        if not self.playlist_switch_partial_update_enabled:
            return False
        if reason != "source_change":
            return False
        if not self._last_pushed_screen_signature or not next_screen:
            return False
        return (
            self._last_pushed_screen_signature[0] == "HOME"
            and next_screen[0] == "HOME"
            and self._last_pushed_screen_signature[2] == "playback_home"
            and next_screen[2] == "playback_home"
        )

    def _should_one_shot_major_transition(
        self,
        update_mode: str,
        clean_refresh: bool,
        policy_reason: str,
    ) -> bool:
        if not self.one_shot_major_transitions:
            return False
        if update_mode != "full":
            return False
        if policy_reason in {"partial_streak_limit", "periodic_full_clear"}:
            return False
        return clean_refresh or policy_reason in {
            "screen_mode_or_title_changed",
            "explicit_full_reason",
            "unclassified_full_safety",
        }


def _show_render_timings() -> bool:
    value = os.getenv("SHOW_RENDER_TIMINGS")
    return bool(value and value.strip().lower() in {"1", "true", "yes", "on"})


def _normalize_menu_navigation_update_mode(value: str) -> str:
    normalized = str(value or "").strip().lower()
    if normalized in {"full", "partial", "skip"}:
        return normalized
    return "full"


def _image_hash(image) -> str:
    hasher = hashlib.sha256()
    hasher.update(image.mode.encode("utf-8"))
    hasher.update(str(image.size).encode("utf-8"))
    hasher.update(image.tobytes())
    return hasher.hexdigest()


def _screen_signature(state: RenderState) -> tuple[str, str, str]:
    layout_type = _layout_type(state)
    if layout_type == "playback_home":
        title = state.current_source_label or "Audio"
    elif layout_type == "idle_home":
        title = "Clock"
    elif layout_type == "menu":
        title = state.menu_title or "Menu"
    elif layout_type == "source_list":
        title = state.menu_title or state.current_source_label or "Source"
    elif layout_type == "sleep_timer":
        title = "Sleep Timer"
    elif layout_type == "output":
        title = "Output"
    elif layout_type == "bluetooth_pairing":
        title = "Bluetooth Pairing"
    elif layout_type == "alarm":
        title = "Alarm"
    elif layout_type == "alarm_active":
        title = "ALARM"
    elif layout_type == "gentle_wake":
        title = "Gentle Wake"
    elif layout_type == "sleep_screen":
        title = "Sleep Screen"
    elif layout_type == "ambient":
        title = "Ambient"
    else:
        title = state.detail_title or state.current_source_label or state.menu_title or ""
    return (state.mode.value, title, layout_type)


def _layout_type(state: RenderState) -> str:
    if state.alarm_runtime.phase == "WAKE_STAGE":
        return "gentle_wake"
    if state.alarm_runtime.active:
        return "alarm_active"
    if state.mode == UIMode.AMBIENT:
        return "ambient"
    if state.mode == UIMode.HOME:
        if state.source_complete:
            return "completed_home"
        if state.playback.state in {PlaybackState.PLAYING, PlaybackState.PAUSED}:
            return "playback_home"
        return "idle_home"
    if state.mode == UIMode.SLEEP_SCREEN:
        return "sleep_screen"
    if state.mode == UIMode.MENU:
        return "source_list" if state.menu_title and state.menu_title != "Home" else "menu"
    if state.mode == UIMode.SLEEP_TIMER:
        return "sleep_timer"
    if state.mode == UIMode.OUTPUT_SELECT:
        return "output"
    if state.mode == UIMode.BLUETOOTH_PAIRING:
        return "bluetooth_pairing"
    if state.mode == UIMode.ALARM:
        return "alarm"
    if state.mode == UIMode.SOURCE_DETAIL:
        return "source_detail"
    return state.mode.value.lower()


def _format_screen(signature: tuple[str, str, str] | None) -> str:
    if signature is None:
        return "none"
    mode, title, layout_type = signature
    return f"{mode}/{title}/{layout_type}"


def _normalize_partial_reason(reason: str) -> str:
    return PARTIAL_REASON_ALIASES.get(reason, reason)


def _region_bounds(region_name: str, renderer: EInkRenderer | None) -> tuple[int, int, int, int]:
    width = getattr(renderer, "width", 600) or 600
    height = getattr(renderer, "height", 448) or 448
    regions = {
        "clock": (0, 0, width, round(height * 0.36)),
        "main_content": (0, round(height * 0.25), width, round(height * 0.84)),
        "bottom_bar": (0, round(height * 0.84), width, height),
        "menu_list": (0, round(height * 0.18), width, round(height * 0.88)),
        "sleep_timer_value": (0, round(height * 0.25), width, round(height * 0.72)),
    }
    return regions.get(region_name, regions["main_content"])
