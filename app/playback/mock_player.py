from __future__ import annotations

import time

from app.models import MediaItem, PlaybackState, PlaybackStatus
from app.playback.base import PlaybackAdapter
from app.services.logger import get_logger


class MockPlayer(PlaybackAdapter):
    def __init__(self) -> None:
        self._status = PlaybackStatus()
        self._started_monotonic: float | None = None
        self._base_position = 0.0
        self.log = get_logger("PLAYBACK")

    def play(self, item: MediaItem, start_position_seconds: float = 0) -> None:
        self._base_position = max(0, float(start_position_seconds))
        self._started_monotonic = time.monotonic()
        self._status = PlaybackStatus(
            state=PlaybackState.PLAYING,
            source_id=item.source_id,
            item_id=item.id,
            title=item.title,
            subtitle=item.artist or "",
            position_seconds=self._base_position,
            duration_seconds=item.duration_seconds,
            volume=self._status.volume,
        )
        self.log.info(
            "Track start source=%s item_id=%s title=%s position=%.1fs",
            item.source_id,
            item.id,
            item.title,
            self._base_position,
        )

    def pause(self) -> None:
        self.tick()
        if self._status.state == PlaybackState.PLAYING:
            self._base_position = self._status.position_seconds
            self._started_monotonic = None
            self._status.state = PlaybackState.PAUSED
            self.log.info(
                "Track paused source=%s item_id=%s position=%.1fs",
                self._status.source_id,
                self._status.item_id,
                self._status.position_seconds,
            )

    def resume(self) -> None:
        if self._status.state == PlaybackState.PAUSED:
            self._started_monotonic = time.monotonic()
            self._status.state = PlaybackState.PLAYING
            self.log.info(
                "Track resume source=%s item_id=%s position=%.1fs",
                self._status.source_id,
                self._status.item_id,
                self._status.position_seconds,
            )

    def stop(self) -> None:
        if self._status.state != PlaybackState.STOPPED:
            self.log.info(
                "Track stop source=%s item_id=%s position=%.1fs",
                self._status.source_id,
                self._status.item_id,
                self._status.position_seconds,
            )
        volume = self._status.volume
        self._status = PlaybackStatus(volume=volume)
        self._base_position = 0
        self._started_monotonic = None

    def toggle_play_pause(self) -> None:
        if self._status.state == PlaybackState.PLAYING:
            self.pause()
        elif self._status.state == PlaybackState.PAUSED:
            self.resume()

    def set_volume(self, volume: int) -> None:
        self._status.volume = max(0, min(100, int(volume)))
        self.log.debug("Volume set value=%s", self._status.volume)

    def adjust_volume(self, delta: int) -> None:
        self.set_volume(self._status.volume + int(delta))

    def status(self) -> PlaybackStatus:
        self.tick()
        return self._status

    def tick(self) -> None:
        if self._status.state != PlaybackState.PLAYING or self._started_monotonic is None:
            return
        elapsed = time.monotonic() - self._started_monotonic
        position = self._base_position + elapsed
        if self._status.duration_seconds and position >= self._status.duration_seconds:
            self._status.position_seconds = float(self._status.duration_seconds)
            self._status.state = PlaybackState.STOPPED
            self._started_monotonic = None
        else:
            self._status.position_seconds = position
