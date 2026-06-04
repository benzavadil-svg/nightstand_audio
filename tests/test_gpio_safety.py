from __future__ import annotations

import unittest
from dataclasses import replace
from types import ModuleType, SimpleNamespace
from unittest.mock import Mock, patch

from app.display.gpio_safety import _epdconfig_conflicts, verify_gpio18_pcm_clk
from app.hardware.pin_map import DEFAULT_PI_PIN_MAP, GpioAssignment, UnsafePinMapError, validate_pin_map


class GpioSafetyTest(unittest.TestCase):
    def test_detects_waveshare_pwr_pin_conflict_with_bossdac_pcm_clk(self) -> None:
        epdconfig = ModuleType("epdconfig")
        epdconfig.PWR_PIN = 18
        epdconfig.RST_PIN = 17
        epdconfig.DC_PIN = 25
        epdconfig.CS_PIN = 8
        epdconfig.BUSY_PIN = 24

        conflicts = _epdconfig_conflicts(epdconfig)

        self.assertEqual(len(conflicts), 1)
        self.assertIn("PWR_PIN=18 conflicts with BossDAC I2S PCM_CLK", conflicts[0])
        self.assertIn("GPIO5", conflicts[0])

    def test_gpio18_pinctrl_check_rejects_output_mode(self) -> None:
        result = SimpleNamespace(stdout="18: op dh | hi // GPIO18 = output", stderr="")

        with (
            patch("shutil.which", return_value="/usr/bin/pinctrl"),
            patch("subprocess.run", return_value=result),
        ):
            safe = verify_gpio18_pcm_clk(Mock(), allow_unsafe=False)

        self.assertFalse(safe)

    def test_gpio18_pinctrl_check_accepts_pcm_clk(self) -> None:
        result = SimpleNamespace(stdout="18: a0 pn | hi // GPIO18 = PCM_CLK", stderr="")

        with (
            patch("shutil.which", return_value="/usr/bin/pinctrl"),
            patch("subprocess.run", return_value=result),
        ):
            safe = verify_gpio18_pcm_clk(Mock(), allow_unsafe=False)

        self.assertTrue(safe)

    def test_default_pin_map_has_no_input_or_display_i2s_overlap(self) -> None:
        validate_pin_map(DEFAULT_PI_PIN_MAP, allow_unsafe=False)

    def test_unsafe_input_assignment_to_gpio18_fails(self) -> None:
        unsafe_buttons = dict(DEFAULT_PI_PIN_MAP.source_buttons)
        unsafe_buttons["button-1"] = GpioAssignment(
            "Unsafe Button 1",
            18,
            12,
            "bad source button",
            "source_button",
            "button-1",
        )
        unsafe_map = replace(DEFAULT_PI_PIN_MAP, source_buttons=unsafe_buttons)

        with self.assertRaises(UnsafePinMapError):
            validate_pin_map(unsafe_map, allow_unsafe=False)

    def test_speaker_path_does_not_allocate_gpio(self) -> None:
        self.assertFalse(DEFAULT_PI_PIN_MAP.speaker_uses_gpio)


if __name__ == "__main__":
    unittest.main()
