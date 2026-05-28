from __future__ import annotations

from abc import ABC, abstractmethod

from app.models import MediaItem, PlaybackStatus


class PlaybackAdapter(ABC):
    @abstractmethod
    def play(self, item: MediaItem, start_position_seconds: float = 0) -> None:
        raise NotImplementedError

    @abstractmethod
    def pause(self) -> None:
        raise NotImplementedError

    @abstractmethod
    def resume(self) -> None:
        raise NotImplementedError

    @abstractmethod
    def stop(self) -> None:
        raise NotImplementedError

    @abstractmethod
    def toggle_play_pause(self) -> None:
        raise NotImplementedError

    @abstractmethod
    def set_volume(self, volume: int) -> None:
        raise NotImplementedError

    @abstractmethod
    def adjust_volume(self, delta: int) -> None:
        raise NotImplementedError

    @abstractmethod
    def status(self) -> PlaybackStatus:
        raise NotImplementedError

    @abstractmethod
    def tick(self) -> None:
        raise NotImplementedError
