from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import patch

from app.config import Settings
from app.playback.factory import build_alarm_playback_adapter, build_playback_adapter
from app.playback.mock_player import MockPlayer
from app.playback.mpv_player import MPVPlayer
from app.services.audio import AudioSelection


USB_APLAY_OUTPUT = """
card 1: BossDAC [BossDAC], device 0: Boss DAC HiFi pcm512x-hifi-0 [Boss DAC HiFi pcm512x-hifi-0]
  Subdevices: 1/1
card 2: Device [USB Audio Device], device 0: USB Audio [USB Audio]
  Subdevices: 1/1
"""

BOSSDAC_ONLY_APLAY_OUTPUT = """
card 1: BossDAC [BossDAC], device 0: Boss DAC HiFi pcm512x-hifi-0 [Boss DAC HiFi pcm512x-hifi-0]
  Subdevices: 1/1
"""


class PlaybackFactoryTest(unittest.TestCase):
    def test_appliance_mode_uses_mpv_even_when_backend_auto(self) -> None:
        settings = _settings(runtime_mode="appliance", playback_backend="auto")
        player = build_playback_adapter(settings, _audio_selection())

        self.assertIsInstance(player, MPVPlayer)

    def test_simulator_mode_defaults_to_mock(self) -> None:
        settings = _settings(runtime_mode="simulator", playback_backend="auto")
        player = build_playback_adapter(settings, _audio_selection())

        self.assertIsInstance(player, MockPlayer)

    def test_appliance_alarm_uses_separate_usb_dac(self) -> None:
        settings = _settings(runtime_mode="appliance", playback_backend="auto")
        normal_player = MockPlayer()

        with patch("app.playback.factory.read_aplay_cards", return_value=USB_APLAY_OUTPUT):
            alarm_player = build_alarm_playback_adapter(settings, normal_player)

        self.assertIsInstance(alarm_player, MPVPlayer)
        self.assertIsNot(alarm_player, normal_player)
        self.assertEqual(alarm_player.audio_device, "plughw:2,0")

    def test_appliance_alarm_does_not_fallback_to_normal_dac(self) -> None:
        settings = _settings(runtime_mode="appliance", playback_backend="auto")
        normal_player = MockPlayer()

        with patch("app.playback.factory.read_aplay_cards", return_value=BOSSDAC_ONLY_APLAY_OUTPUT):
            alarm_player = build_alarm_playback_adapter(settings, normal_player)

        self.assertIsInstance(alarm_player, MockPlayer)
        self.assertIsNot(alarm_player, normal_player)


def _audio_selection() -> AudioSelection:
    return AudioSelection(
        backend="alsa",
        requested_device="auto",
        selected_device="plughw:1,0",
        hardware_dac_detected="BossDAC",
        card_index=1,
    )


def _settings(runtime_mode: str, playback_backend: str) -> Settings:
    root = Path("/tmp/nightstand")
    return Settings(
        project_root=root,
        media_dir=root / "media",
        data_dir=root / "data",
        db_path=root / "data/nightstand.sqlite",
        screen_path=root / "data/latest_screen.png",
        runtime_mode=runtime_mode,
        display_backend="png",
        hardware_fallback_to_simulator=True,
        display_model="waveshare_4in2_v2",
        display_width=400,
        display_height=300,
        menu_timeout_seconds=15,
        use_real_epd=False,
        epd_rotate_degrees=0,
        clear_epd_on_exit=False,
        epd_full_clear_interval=50,
        force_epd_update=False,
        epd_reinit_every_update=False,
        clear_before_epd_update=False,
        epd_render_debounce_ms=750,
        epd_volume_refresh_debounce_ms=600,
        epd_refresh_on_volume_change=True,
        epd_partial_update_enabled=True,
        epd_disable_partial=False,
        epd_one_shot_major_transitions=True,
        epd_region_partial_enabled=True,
        epd_partial_streak_limit=8,
        epd_partial_refresh_min_interval_ms=500,
        epd_force_full_refresh=False,
        epd_force_clean_refresh=False,
        epd_menu_navigation_update_mode="full",
        epd_clock_partial_update_enabled=False,
        epd_clock_refresh_seconds=60,
        epd_disable_clock_auto_refresh=False,
        night_mode_enabled=True,
        night_mode_start="22:00",
        night_mode_end="06:00",
        night_mode_wake_timeout_seconds=30,
        night_mode_display_lock=True,
        ambient_mode_enabled=True,
        active_mode_timeout_seconds=30,
        ambient_clock_refresh_seconds=60,
        ambient_show_playback_glyph=True,
        input_backend="keyboard",
        audio_backend="alsa",
        audio_device="auto",
        alarm_audio_device="auto_usb",
        playback_backend=playback_backend,
        restore_playback_on_startup=True,
        resume_on_startup=False,
        playback_restore_launch=False,
        validate_playlist_on_play=False,
        background_media_scan=True,
        audio_start_display_grace_ms=5000,
        epd_suppress_while_audio_playing=True,
        sleep_fade_seconds=30,
        sleep_fade_steps=20,
    )


if __name__ == "__main__":
    unittest.main()
