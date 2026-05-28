from __future__ import annotations

from datetime import datetime, timedelta

from app.media_library import MediaLibrary
from app.models import AlarmConfig, AlarmRuntimeState
from app.playback.base import PlaybackAdapter
from app.services.logger import get_logger
from app.state_store import StateStore


class AlarmService:
    def __init__(self, store: StateStore, library: MediaLibrary, player: PlaybackAdapter) -> None:
        self.store = store
        self.library = library
        self.player = player
        self.config = store.get_alarm_config()
        self.runtime = AlarmRuntimeState()
        self._triggered_at: datetime | None = None
        self.log = get_logger("STATE")

    def tick(self, now: datetime) -> bool:
        changed = False
        if self.runtime.active:
            changed = self._update_fade(now) or changed
            return changed

        if self.runtime.snoozed_until and now >= self.runtime.snoozed_until:
            self.runtime.snoozed_until = None
            self._trigger(now, from_snooze=True)
            return True

        if not self.config.enabled:
            return changed

        already_triggered_today = self.config.last_triggered_date == now.date()
        if already_triggered_today:
            return changed
        if now.hour == self.config.hour and now.minute == self.config.minute:
            self._trigger(now)
            return True
        return changed

    def toggle_enabled(self) -> None:
        self.config.enabled = not self.config.enabled
        self.store.save_alarm_config(self.config)
        self.log.info("Alarm toggled enabled=%s time=%s", self.config.enabled, self.config.label())

    def adjust_time(self, minutes: int) -> None:
        alarm_time = datetime(2000, 1, 1, self.config.hour, self.config.minute) + timedelta(
            minutes=minutes
        )
        self.config.hour = alarm_time.hour
        self.config.minute = alarm_time.minute
        self.store.save_alarm_config(self.config)
        self.log.info("Alarm adjusted minutes=%s new_time=%s", minutes, self.config.label())

    def snooze(self, now: datetime | None = None) -> None:
        now = now or datetime.now()
        if not self.runtime.active:
            return
        self.player.pause()
        self.runtime.active = False
        self.runtime.fading = False
        self.runtime.snoozed_until = now + timedelta(minutes=self.config.snooze_minutes)
        self.log.info("Alarm snoozed until=%s", self.runtime.snoozed_until.isoformat())

    def stop(self) -> None:
        self.runtime.active = False
        self.runtime.fading = False
        self.runtime.snoozed_until = None
        self.player.stop()
        self.log.info("Alarm stopped")

    def _trigger(self, now: datetime, from_snooze: bool = False) -> None:
        item = self.library.get_resume_item(self.config.source_id)
        if item:
            self.player.set_volume(0)
            self.player.play(item, item.last_position_seconds)
            if item.id:
                self.store.mark_started(item.id)
        self.runtime.active = True
        self.runtime.fading = self.config.fade_in_seconds > 0
        self.runtime.fade_volume = 0
        self._triggered_at = now
        self.log.info(
            "Alarm triggered source=%s from_snooze=%s fade_seconds=%s target_volume=%s",
            self.config.source_id,
            from_snooze,
            self.config.fade_in_seconds,
            self.config.target_volume,
        )
        if not from_snooze:
            self.config.last_triggered_date = now.date()
            self.store.save_alarm_config(self.config)

    def _update_fade(self, now: datetime) -> bool:
        if not self.runtime.fading or not self._triggered_at:
            return False
        elapsed = max(0, (now - self._triggered_at).total_seconds())
        if self.config.fade_in_seconds <= 0:
            target = self.config.target_volume
        else:
            ratio = min(1, elapsed / self.config.fade_in_seconds)
            target = round(self.config.target_volume * ratio)
        previous_volume = self.player.status().volume
        self.player.set_volume(target)
        self.runtime.fade_volume = target
        if target >= self.config.target_volume:
            self.runtime.fading = False
        return previous_volume != target
