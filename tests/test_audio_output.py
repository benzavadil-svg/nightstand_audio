from __future__ import annotations

import subprocess
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts import test_audio_output
from app.services.audio import AudioOutputSelector, detect_preferred_dac, detect_usb_audio_device


APLAY_OUTPUT = """
card 0: vc4hdmi0 [vc4-hdmi-0], device 0: MAI PCM i2s-hifi-0 [MAI PCM i2s-hifi-0]
  Subdevices: 1/1
card 1: snd_rpi_hifiberry_dacplus [snd_rpi_hifiberry_dacplus], device 0: HiFiBerry DAC+ Pro HiFi pcm512x-hifi-0 [HiFiBerry DAC+ Pro HiFi pcm512x-hifi-0]
  Subdevices: 1/1
"""

BOSSDAC_APLAY_OUTPUT = """
card 0: vc4hdmi0 [vc4-hdmi-0], device 0: MAI PCM i2s-hifi-0 [MAI PCM i2s-hifi-0]
  Subdevices: 1/1
card 1: BossDAC [BossDAC], device 0: Boss DAC HiFi pcm512x-hifi-0 [Boss DAC HiFi pcm512x-hifi-0]
  Subdevices: 1/1
"""

MIXED_DAC_APLAY_OUTPUT = """
card 1: snd_rpi_hifiberry_dacplus [snd_rpi_hifiberry_dacplus], device 0: HiFiBerry DAC+ Pro HiFi pcm512x-hifi-0 [HiFiBerry DAC+ Pro HiFi pcm512x-hifi-0]
  Subdevices: 1/1
card 2: BossDAC [BossDAC], device 0: Boss DAC HiFi pcm512x-hifi-0 [Boss DAC HiFi pcm512x-hifi-0]
  Subdevices: 1/1
"""

USB_AUDIO_APLAY_OUTPUT = """
card 0: vc4hdmi0 [vc4-hdmi-0], device 0: MAI PCM i2s-hifi-0 [MAI PCM i2s-hifi-0]
  Subdevices: 1/1
card 1: BossDAC [BossDAC], device 0: Boss DAC HiFi pcm512x-hifi-0 [Boss DAC HiFi pcm512x-hifi-0]
  Subdevices: 1/1
card 2: Device [USB Audio Device], device 0: USB Audio [USB Audio]
  Subdevices: 1/1
"""


class AudioOutputSelectionTest(unittest.TestCase):
    def test_dac_detection_prefers_hifiberry_pcm512x_card(self) -> None:
        detected = detect_preferred_dac(APLAY_OUTPUT)

        self.assertEqual(detected.name, "snd_rpi_hifiberry_dacplus")
        self.assertEqual(detected.card_index, 1)

    def test_dac_detection_prefers_bossdac_card(self) -> None:
        detected = detect_preferred_dac(BOSSDAC_APLAY_OUTPUT)

        self.assertEqual(detected.name, "BossDAC")
        self.assertEqual(detected.card_index, 1)

    def test_bossdac_wins_if_multiple_pcm512x_overlays_are_visible(self) -> None:
        detected = detect_preferred_dac(MIXED_DAC_APLAY_OUTPUT)

        self.assertEqual(detected.name, "BossDAC")
        self.assertEqual(detected.card_index, 2)

    def test_usb_audio_detection_ignores_bossdac_and_hdmi(self) -> None:
        detected = detect_usb_audio_device(USB_AUDIO_APLAY_OUTPUT)

        self.assertEqual(detected.name, "Device")
        self.assertEqual(detected.card_index, 2)

    def test_usb_audio_detection_does_not_return_bossdac(self) -> None:
        detected = detect_usb_audio_device(BOSSDAC_APLAY_OUTPUT)

        self.assertIsNone(detected.name)
        self.assertIsNone(detected.card_index)

    def test_auto_selects_plughw_for_bossdac(self) -> None:
        with patch("app.services.audio.read_aplay_cards", return_value=BOSSDAC_APLAY_OUTPUT):
            selection = AudioOutputSelector("alsa", "auto").select()

        self.assertEqual(selection.selected_device, "plughw:1,0")
        self.assertEqual(selection.hardware_dac_detected, "BossDAC")

    def test_auto_selects_plughw_for_detected_dac(self) -> None:
        with patch("app.services.audio.read_aplay_cards", return_value=APLAY_OUTPUT):
            selection = AudioOutputSelector("alsa", "auto").select()

        self.assertEqual(selection.selected_device, "plughw:1,0")
        self.assertEqual(selection.hardware_dac_detected, "snd_rpi_hifiberry_dacplus")

    def test_audio_device_override_wins_over_auto_detection(self) -> None:
        with patch("app.services.audio.read_aplay_cards", return_value=APLAY_OUTPUT):
            selection = AudioOutputSelector("alsa", "hw:1,0").select()

        self.assertEqual(selection.selected_device, "hw:1,0")
        self.assertEqual(selection.hardware_dac_detected, "snd_rpi_hifiberry_dacplus")

    def test_test_audio_output_uses_selected_device(self) -> None:
        calls: list[list[str]] = []

        def fake_run(command, text=True, capture_output=True, check=False):
            calls.append(command)
            return subprocess.CompletedProcess(command, 0, stdout=APLAY_OUTPUT, stderr="")

        with (
            patch("scripts.test_audio_output.shutil.which", return_value="/usr/bin/aplay"),
            patch("app.services.audio.shutil.which", return_value="/usr/bin/aplay"),
            patch("subprocess.run", side_effect=fake_run),
            patch("scripts.test_audio_output._write_test_tone", return_value=Path("/tmp/tone.wav")),
            patch("scripts.test_audio_output.Path.unlink", return_value=None),
            patch("app.services.audio.read_aplay_cards", return_value=APLAY_OUTPUT),
            patch.dict("os.environ", {"AUDIO_DEVICE": "auto"}, clear=False),
            patch("sys.argv", ["test_audio_output.py"]),
        ):
            test_audio_output.main()

        self.assertIn(["aplay", "-D", "plughw:1,0", "/tmp/tone.wav"], calls)


if __name__ == "__main__":
    unittest.main()
