from __future__ import annotations

from app.config import Settings
from app.playback.base import PlaybackAdapter
from app.playback.mock_player import MockPlayer
from app.playback.mpv_player import MPVPlayer
from app.services.audio import AudioSelection
from app.services.logger import get_logger


def build_playback_adapter(
    settings: Settings,
    audio_selection: AudioSelection,
    force_backend: str | None = None,
) -> PlaybackAdapter:
    backend = _select_playback_backend(settings, force_backend)
    audio_log = get_logger("AUDIO")
    audio_log.info("Playback backend: %s", backend)
    if audio_selection.backend == "alsa":
        audio_log.info("ALSA device: %s", audio_selection.selected_device)
    if backend == "mpv":
        return MPVPlayer(audio_device=audio_selection.selected_device)
    return MockPlayer()


def _select_playback_backend(settings: Settings, force_backend: str | None = None) -> str:
    if force_backend:
        return force_backend
    if settings.runtime_mode == "appliance":
        return "mpv"
    if settings.playback_backend in {"mock", "mpv"}:
        return settings.playback_backend
    return "mock"
