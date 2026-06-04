from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time as clock_time, timedelta

from app.media_library import MediaLibrary
from app.models import AlarmConfig, AlarmRuntimeState, PlaybackState
from app.playback.base import PlaybackAdapter
from app.services.logger import get_logger
from app.state_store import StateStore


PHASE_IDLE = "IDLE"
PHASE_WAKE_STAGE = "WAKE_STAGE"
PHASE_ALARM_ACTIVE = "ALARM_ACTIVE"
PHASE_SNOOZE = "SNOOZE"
PHASE_DISMISSED = "ALARM_DISMISSED"


@dataclass(frozen=True)
class AlarmSchedule:
    target_at: datetime
    wake_start_at: datetime
    stage_index: int
    stage_started_at: datetime
    stage_ends_at: datetime


class AlarmService:
    def __init__(
        self,
        store: StateStore,
        library: MediaLibrary,
        player: PlaybackAdapter,
        alarm_player: PlaybackAdapter | None = None,
    ) -> None:
        self.store = store
        self.library = library
        self.normal_player = player
        self.player = alarm_player or player
        self._uses_dedicated_alarm_player = alarm_player is not None and alarm_player is not player
        self.config = store.get_alarm_config()
        self.runtime = AlarmRuntimeState()
        self._triggered_at: datetime | None = None
        self._stage_started_at: datetime | None = None
        self._last_stage_index = 0
        self.log = get_logger("STATE")

    def tick(self, now: datetime) -> bool:
        changed = False
        if self.runtime.phase == PHASE_ALARM_ACTIVE:
            self._update_alarm_fade(now)
            return False

        if self.runtime.phase == PHASE_WAKE_STAGE:
            schedule = self.schedule_for_now(now)
            if not schedule:
                self._reset_runtime()
                return True
            if now >= schedule.target_at:
                self._trigger_alarm(now)
                return True
            changed = self._enter_or_update_wake_stage(schedule, now) or changed
            self._update_wake_volume(schedule, now)
            return changed

        if self.runtime.snoozed_until:
            if now >= self.runtime.snoozed_until:
                self.runtime.snoozed_until = None
                self._trigger_alarm(now, from_snooze=True)
                return True
            return False

        if not self.config.enabled:
            return False

        schedule = self.schedule_for_now(now)
        if not schedule:
            return False
        if self._dismissed_or_triggered(schedule.target_at.date()):
            return False
        if now >= schedule.target_at:
            self._trigger_alarm(now)
            return True
        changed = self._enter_or_update_wake_stage(schedule, now)
        self._update_wake_volume(schedule, now)
        return changed

    def schedule_for_now(self, now: datetime) -> AlarmSchedule | None:
        if not self.config.enabled:
            return None
        for day_offset in (-1, 0, 1):
            target_date = now.date() + timedelta(days=day_offset)
            target_at = self.target_datetime(target_date)
            wake_start_at = self.wake_start_datetime(target_at)
            if wake_start_at <= now < target_at + timedelta(minutes=1):
                return self._schedule_for_target(target_at, now)
        return None

    def target_datetime(self, target_date: date) -> datetime:
        return datetime.combine(target_date, clock_time(self.config.hour, self.config.minute))

    def wake_start_datetime(self, target_at: datetime) -> datetime:
        lead_minutes = self.config.wake_lead_minutes if self.config.wake_enabled else 0
        return target_at - timedelta(minutes=max(0, lead_minutes))

    def wake_start_for_date(self, target_date: date, include_dismissed: bool = False) -> datetime | None:
        if not self.config.enabled:
            return None
        target_at = self.target_datetime(target_date)
        if self._dismissed_or_triggered(target_at.date()) and not include_dismissed:
            return None
        return self.wake_start_datetime(target_at)

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
        if not self.runtime.is_engaged:
            return
        self.player.pause()
        self.runtime = AlarmRuntimeState(
            phase=PHASE_SNOOZE,
            snoozed_until=now + timedelta(minutes=self.config.snooze_minutes),
            output_label="Alarm Speaker",
        )
        self.log.info("Alarm snoozed until=%s", self.runtime.snoozed_until.isoformat())

    def stop(self) -> None:
        self.player.stop()
        self._reset_runtime()
        self.log.info("Alarm stopped")

    def dismiss_for_day(self, now: datetime | None = None) -> None:
        now = now or datetime.now()
        schedule = self.schedule_for_now(now)
        dismissed_date = schedule.target_at.date() if schedule else now.date()
        self.config.last_dismissed_date = dismissed_date
        self.store.save_alarm_config(self.config)
        self.player.stop()
        self.runtime = AlarmRuntimeState(phase=PHASE_DISMISSED)
        self.log.info("Alarm dismissed for day date=%s", dismissed_date)

    def _schedule_for_target(self, target_at: datetime, now: datetime) -> AlarmSchedule:
        wake_start_at = self.wake_start_datetime(target_at)
        stage_count = max(1, self.config.wake_stages)
        lead_seconds = max(0.0, (target_at - wake_start_at).total_seconds())
        if lead_seconds <= 0:
            stage_index = stage_count
            stage_started_at = target_at
            stage_ends_at = target_at
        else:
            stage_seconds = lead_seconds / stage_count
            elapsed = max(0.0, (now - wake_start_at).total_seconds())
            stage_index = min(stage_count, int(elapsed // stage_seconds) + 1)
            stage_started_at = wake_start_at + timedelta(seconds=stage_seconds * (stage_index - 1))
            stage_ends_at = min(target_at, stage_started_at + timedelta(seconds=stage_seconds))
        return AlarmSchedule(
            target_at=target_at,
            wake_start_at=wake_start_at,
            stage_index=stage_index,
            stage_started_at=stage_started_at,
            stage_ends_at=stage_ends_at,
        )

    def _enter_or_update_wake_stage(self, schedule: AlarmSchedule, now: datetime) -> bool:
        changed = self.runtime.phase != PHASE_WAKE_STAGE or self._last_stage_index != schedule.stage_index
        if changed:
            self.runtime.phase = PHASE_WAKE_STAGE
            self.runtime.active = False
            self.runtime.fading = True
            self.runtime.wake_stage = schedule.stage_index
            self.runtime.wake_stages = max(1, self.config.wake_stages)
            self.runtime.stage_label = f"Wake Stage {schedule.stage_index}"
            self.runtime.target_volume = self._stage_target_volume(schedule.stage_index)
            self.runtime.output_label = "Alarm Speaker"
            self.runtime.queued = False
            self._stage_started_at = schedule.stage_started_at
            self._last_stage_index = schedule.stage_index
            self.log.info(
                "Gentle wake stage entered stage=%s/%s target=%s wake_start=%s alarm_target=%s",
                schedule.stage_index,
                self.runtime.wake_stages,
                self.runtime.target_volume,
                schedule.wake_start_at.isoformat(),
                schedule.target_at.isoformat(),
            )
            self._start_stage_audio_if_allowed(now)
        return changed

    def _start_stage_audio_if_allowed(self, now: datetime) -> None:
        status = self.normal_player.status()
        if status.state == PlaybackState.PLAYING and not self.config.interrupt_active_playback:
            self.runtime.queued = True
            self.log.info("Gentle wake audio queued because playback is already active.")
            return
        item = self.library.get_resume_item(self.config.stage_source)
        if not item:
            self.log.warning("Gentle wake source has no playable item source=%s", self.config.stage_source)
            return
        self.player.set_volume(0)
        self.player.play(item, item.last_position_seconds)
        if item.id:
            self.store.mark_started(item.id)

    def _trigger_alarm(self, now: datetime, from_snooze: bool = False) -> None:
        if self._dismissed_or_triggered(now.date()) and not from_snooze:
            return
        status = self.normal_player.status()
        wake_audio_active = (
            self.runtime.phase == PHASE_WAKE_STAGE
            and self.player.status().source_id == self.config.stage_source
        )
        if (
            status.state == PlaybackState.PLAYING
            and not wake_audio_active
            and not self.config.interrupt_active_playback
            and not from_snooze
        ):
            self.runtime.phase = PHASE_ALARM_ACTIVE
            self.runtime.active = True
            self.runtime.queued = True
            self.runtime.output_label = "Alarm Speaker"
            self.log.info("Alarm queued because playback is already active.")
            return
        item = self.library.get_resume_item(self.config.source_id)
        if item:
            self.player.set_volume(0)
            self.player.play(item, item.last_position_seconds)
            if item.id:
                self.store.mark_started(item.id)
        self.runtime.phase = PHASE_ALARM_ACTIVE
        self.runtime.active = True
        self.runtime.fading = self.config.fade_in_seconds > 0
        self.runtime.fade_volume = 0
        self.runtime.target_volume = self.config.target_volume
        self.runtime.wake_stage = 0
        self.runtime.stage_label = "Alarm"
        self.runtime.output_label = "Alarm Speaker"
        self.runtime.queued = False
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

    def _update_wake_volume(self, schedule: AlarmSchedule, now: datetime) -> None:
        if self.runtime.queued:
            return
        target = self._stage_target_volume(schedule.stage_index)
        stage_seconds = max(1.0, (schedule.stage_ends_at - schedule.stage_started_at).total_seconds())
        elapsed = max(0.0, (now - schedule.stage_started_at).total_seconds())
        ratio = min(1.0, elapsed / stage_seconds)
        minimum = min(3, target) if schedule.stage_index == 1 else self._stage_target_volume(schedule.stage_index - 1)
        volume = round(minimum + (target - minimum) * ratio)
        self.player.set_volume(volume)
        self.runtime.fade_volume = volume

    def _update_alarm_fade(self, now: datetime) -> None:
        if self.runtime.queued:
            return
        if not self.runtime.fading or not self._triggered_at:
            return
        elapsed = max(0, (now - self._triggered_at).total_seconds())
        if self.config.fade_in_seconds <= 0:
            target = self.config.target_volume
        else:
            ratio = min(1, elapsed / self.config.fade_in_seconds)
            target = round(self.config.target_volume * ratio)
        self.player.set_volume(target)
        self.runtime.fade_volume = target
        if target >= self.config.target_volume:
            self.runtime.fading = False

    def _stage_target_volume(self, stage_index: int) -> int:
        curve = self.config.stage_volume_curve or [5, 10, 20, 35]
        if stage_index <= len(curve):
            return max(0, min(100, int(curve[stage_index - 1])))
        if self.config.wake_stages <= 1:
            return max(0, min(100, curve[-1]))
        ratio = stage_index / max(1, self.config.wake_stages)
        return max(0, min(100, round(35 * ratio)))

    def _dismissed_or_triggered(self, target_date: date) -> bool:
        return (
            self.config.last_triggered_date == target_date
            or self.config.last_dismissed_date == target_date
        )

    def _reset_runtime(self) -> None:
        self.runtime = AlarmRuntimeState()
        self._triggered_at = None
        self._stage_started_at = None
        self._last_stage_index = 0
