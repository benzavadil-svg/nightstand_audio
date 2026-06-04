from __future__ import annotations

from app.config import Settings
from app.playback.base import PlaybackAdapter
from app.playback.mock_player import MockPlayer
from app.playback.mpv_player import MPVPlayer
from app.services.audio import AudioSelection, detect_usb_audio_device, read_aplay_cards
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


def build_alarm_playback_adapter(
    settings: Settings,
    normal_player: PlaybackAdapter,
    force_backend: str | None = None,
) -> PlaybackAdapter:
    requested_device = _alarm_requested_device(settings)
    if not requested_device:
        return normal_player
    backend = _select_playback_backend(settings, force_backend)
    audio_log = get_logger("AUDIO")
    audio_log.info("Alarm playback backend: %s", backend)
    if backend != "mpv":
        return MockPlayer()
    selected_device = _select_alarm_audio_device(requested_device)
    if selected_device is None:
        audio_log.error(
            "Alarm USB DAC not detected; alarm playback disabled rather than routing to BossDAC/Bluetooth."
        )
        return MockPlayer()
    audio_log.info("Alarm ALSA device: %s", selected_device)
    return MPVPlayer(audio_device=selected_device)


def _select_playback_backend(settings: Settings, force_backend: str | None = None) -> str:
    if force_backend:
        return force_backend
    if settings.runtime_mode == "appliance":
        return "mpv"
    if settings.playback_backend in {"mock", "mpv"}:
        return settings.playback_backend
    return "mock"


def _alarm_requested_device(settings: Settings) -> str:
    requested = settings.alarm_audio_device.strip()
    if requested:
        return requested
    if settings.runtime_mode == "appliance":
        return "auto_usb"
    return ""


def _select_alarm_audio_device(requested_device: str) -> str | None:
    normalized = requested_device.strip().lower()
    if normalized in {"auto", "auto_usb", "usb"}:
        detected = detect_usb_audio_device(read_aplay_cards())
        if detected.name and detected.card_index is not None:
            get_logger("AUDIO").info("Alarm USB DAC detected: %s", detected.name)
            return f"plughw:{detected.card_index},0"
        return None
    return requested_device
