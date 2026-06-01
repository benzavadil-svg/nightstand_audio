from __future__ import annotations

import unittest
from types import ModuleType, SimpleNamespace
from unittest.mock import Mock, patch

from app.display.gpio_safety import _epdconfig_conflicts, verify_gpio18_pcm_clk


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


if __name__ == "__main__":
    unittest.main()
