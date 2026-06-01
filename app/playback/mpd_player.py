from __future__ import annotations

from app.models import MediaItem, PlaybackStatus
from app.playback.base import PlaybackAdapter


class MPDPlayer(PlaybackAdapter):
    """Future MPD-backed playback adapter.

    Contract:
    - Expose the same play/pause/resume/stop/volume/status API as MockPlayer.
    - Treat MPD as the server-side local media playback engine.
    - Keep ALSA/PipeWire, BossDAC/InnoMaker setup, and Bluetooth setup out of controller logic.

    TODO: Connect to MPD using python-mpd2 or an equivalent client.
    TODO: Map local media paths into MPD's configured music directory.
    TODO: Document MPD output setup for the selected Pi audio sink.
    TODO: Add Bluetooth output support as a later selectable output target.
    """

    def play(self, item: MediaItem, start_position_seconds: float = 0) -> None:
        raise NotImplementedError("MPD playback is not implemented yet.")

    def pause(self) -> None:
        raise NotImplementedError("MPD playback is not implemented yet.")

    def resume(self) -> None:
        raise NotImplementedError("MPD playback is not implemented yet.")

    def stop(self) -> None:
        raise NotImplementedError("MPD playback is not implemented yet.")

    def toggle_play_pause(self) -> None:
        raise NotImplementedError("MPD playback is not implemented yet.")

    def set_volume(self, volume: int) -> None:
        raise NotImplementedError("MPD playback is not implemented yet.")

    def adjust_volume(self, delta: int) -> None:
        raise NotImplementedError("MPD playback is not implemented yet.")

    def status(self) -> PlaybackStatus:
        raise NotImplementedError("MPD playback is not implemented yet.")

    def tick(self) -> None:
        raise NotImplementedError("MPD playback is not implemented yet.")
