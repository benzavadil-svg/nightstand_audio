from __future__ import annotations

import shutil
import subprocess
from importlib import import_module
from types import ModuleType

from app.hardware.pin_map import (
    DEFAULT_PI_PIN_MAP,
    I2S_PROTECTED_PINS,
    UnsafePinMapError,
    allow_unsafe_gpio,
    pin_map_conflicts,
)

WAVESHARE_CONTROL_PINS = ("PWR_PIN", "RST_PIN", "DC_PIN", "CS_PIN", "BUSY_PIN")


class UnsafeGpioConfigError(RuntimeError):
    pass


def allow_unsafe_epd_gpio() -> bool:
    return allow_unsafe_gpio()


def validate_appliance_gpio_config(log, allow_unsafe: bool | None = None) -> None:
    allow = allow_unsafe_gpio() if allow_unsafe is None else allow_unsafe
    conflicts = pin_map_conflicts(DEFAULT_PI_PIN_MAP)
    if not conflicts:
        log.info("Pi appliance GPIO pin map safety check passed.")
        return
    message = "Unsafe Pi appliance GPIO config: " + "; ".join(conflicts)
    if allow:
        log.critical("%s ALLOW_UNSAFE_GPIO=true override is active.", message)
        return
    log.critical(message)
    raise UnsafePinMapError(message)


def validate_waveshare_gpio_config(log, allow_unsafe: bool | None = None) -> None:
    allow = allow_unsafe_epd_gpio() if allow_unsafe is None else allow_unsafe
    try:
        epdconfig = import_module("waveshare_epd.epdconfig")
    except Exception as exc:
        log.warning("Unable to inspect Waveshare epdconfig GPIO pins: %s", exc)
        return
    conflicts = _epdconfig_conflicts(epdconfig)
    if not conflicts:
        log.info("Waveshare GPIO safety check passed; BossDAC I2S pins are protected.")
        return
    message = "; ".join(conflicts)
    if allow:
        log.critical("%s ALLOW_UNSAFE_GPIO=true override is active.", message)
        return
    log.critical(message)
    raise UnsafeGpioConfigError(message)


def verify_gpio18_pcm_clk(log, allow_unsafe: bool | None = None) -> bool:
    allow = allow_unsafe_epd_gpio() if allow_unsafe is None else allow_unsafe
    pinctrl = shutil.which("pinctrl")
    if not pinctrl:
        log.warning("pinctrl unavailable; cannot verify GPIO18 remains PCM_CLK.")
        return True
    try:
        result = subprocess.run(
            [pinctrl, "get", "18"],
            check=False,
            capture_output=True,
            text=True,
            timeout=2,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        log.warning("GPIO18 pinctrl check failed: %s", exc)
        return True
    output = " ".join(part.strip() for part in (result.stdout, result.stderr) if part.strip())
    if "PCM_CLK" in output or "PCM" in output:
        log.info("GPIO18 pinmux safety check passed: %s", output)
        return True
    lowered = output.lower()
    unsafe_output = " op " in f" {lowered} " or "output" in lowered or "gpio" in lowered
    if unsafe_output:
        message = (
            "GPIO18 no longer appears to be PCM_CLK while BossDAC is active; "
            f"pinctrl output={output!r}. Stopping physical EPD updates."
        )
        if allow:
            log.critical("%s ALLOW_UNSAFE_GPIO=true override is active.", message)
            return True
        log.critical(message)
        return False
    log.warning("GPIO18 pinctrl output did not identify PCM_CLK clearly: %s", output)
    return True


def _epdconfig_conflicts(epdconfig: ModuleType) -> list[str]:
    conflicts = []
    for name in WAVESHARE_CONTROL_PINS:
        value = getattr(epdconfig, name, None)
        try:
            pin = int(value)
        except (TypeError, ValueError):
            continue
        if pin in I2S_PROTECTED_PINS:
            if name == "PWR_PIN" and pin == 18:
                conflicts.append(
                    "Unsafe Waveshare GPIO config: PWR_PIN=18 conflicts with BossDAC I2S PCM_CLK. "
                    "Change PWR_PIN to GPIO5 or another safe GPIO."
                )
            else:
                conflicts.append(
                    f"Unsafe Waveshare GPIO config: {name}={pin} conflicts with "
                    f"{I2S_PROTECTED_PINS[pin]}. Use a safe non-I2S GPIO."
                )
    return conflicts
