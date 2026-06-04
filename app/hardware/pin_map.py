from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Iterable


@dataclass(frozen=True)
class GpioAssignment:
    name: str
    gpio: int
    physical_pin: int | None
    role: str
    group: str
    source_id: str | None = None


@dataclass(frozen=True)
class PiAppliancePinMap:
    epd: dict[str, GpioAssignment]
    bossdac_i2s: dict[str, GpioAssignment]
    rotary: dict[str, GpioAssignment]
    source_buttons: dict[str, GpioAssignment]
    speaker_uses_gpio: bool = False
    speaker_note: str = (
        "MonkMakes amplified speaker uses 5V, GND, and dedicated USB DAC audio; no GPIO."
    )


I2S_PROTECTED_PINS = {
    18: "BossDAC I2S PCM_CLK",
    19: "BossDAC I2S PCM_FS",
    20: "BossDAC I2S PCM_DIN",
    21: "BossDAC I2S PCM_DOUT",
}


DEFAULT_PI_PIN_MAP = PiAppliancePinMap(
    epd={
        "RST": GpioAssignment("EPD RST", 17, 11, "Waveshare reset", "epd"),
        "BUSY": GpioAssignment("EPD BUSY", 24, 18, "Waveshare busy", "epd"),
        "DIN": GpioAssignment("EPD DIN/MOSI", 10, 19, "SPI MOSI", "epd"),
        "DC": GpioAssignment("EPD DC", 25, 22, "Waveshare data/command", "epd"),
        "CLK": GpioAssignment("EPD CLK/SCLK", 11, 23, "SPI clock", "epd"),
        "CS": GpioAssignment("EPD CS/CE0", 8, 24, "SPI chip select", "epd"),
        "PWR": GpioAssignment("EPD PWR safety", 5, 29, "Safe Waveshare PWR_PIN", "epd"),
    },
    bossdac_i2s={
        "PCM_CLK": GpioAssignment("BossDAC PCM_CLK", 18, 12, "I2S bit clock", "bossdac"),
        "PCM_FS": GpioAssignment("BossDAC PCM_FS", 19, 35, "I2S frame sync", "bossdac"),
        "PCM_DIN": GpioAssignment("BossDAC PCM_DIN", 20, 38, "I2S data in", "bossdac"),
        "PCM_DOUT": GpioAssignment("BossDAC PCM_DOUT", 21, 40, "I2S data out", "bossdac"),
    },
    rotary={
        "A": GpioAssignment("Rotary A/CLK", 12, 32, "Encoder phase A", "rotary"),
        "B": GpioAssignment("Rotary B/DT", 13, 33, "Encoder phase B", "rotary"),
        "SW": GpioAssignment("Rotary SW", 16, 36, "Encoder push switch", "rotary"),
    },
    source_buttons={
        "button-1": GpioAssignment(
            "Button 1",
            22,
            15,
            "Source button to media/buttons/button-1",
            "source_button",
            "button-1",
        ),
        "button-2": GpioAssignment(
            "Button 2",
            23,
            16,
            "Source button to media/buttons/button-2",
            "source_button",
            "button-2",
        ),
        "button-3": GpioAssignment(
            "Button 3",
            26,
            37,
            "Source button to media/buttons/button-3",
            "source_button",
            "button-3",
        ),
    },
)


class UnsafePinMapError(RuntimeError):
    pass


def allow_unsafe_gpio() -> bool:
    for name in ("ALLOW_UNSAFE_GPIO", "ALLOW_UNSAFE_EPD_GPIO"):
        value = os.getenv(name, "")
        if value.strip().lower() in {"1", "true", "yes", "on"}:
            return True
    return False


def active_assignments(pin_map: PiAppliancePinMap = DEFAULT_PI_PIN_MAP) -> list[GpioAssignment]:
    assignments: list[GpioAssignment] = []
    assignments.extend(pin_map.epd.values())
    assignments.extend(pin_map.rotary.values())
    assignments.extend(pin_map.source_buttons.values())
    return assignments


def all_assignments(pin_map: PiAppliancePinMap = DEFAULT_PI_PIN_MAP) -> list[GpioAssignment]:
    assignments = active_assignments(pin_map)
    assignments.extend(pin_map.bossdac_i2s.values())
    return assignments


def describe_assignment(assignment: GpioAssignment) -> str:
    physical = f"physical pin {assignment.physical_pin}" if assignment.physical_pin else "no physical pin"
    return f"{assignment.name}=GPIO{assignment.gpio} ({physical}; {assignment.role})"


def validate_pin_map(
    pin_map: PiAppliancePinMap = DEFAULT_PI_PIN_MAP,
    *,
    allow_unsafe: bool | None = None,
) -> None:
    allow = allow_unsafe_gpio() if allow_unsafe is None else allow_unsafe
    errors = pin_map_conflicts(pin_map)
    if not errors:
        return
    message = "Unsafe GPIO pin map: " + "; ".join(errors)
    if allow:
        return
    raise UnsafePinMapError(message)


def pin_map_conflicts(pin_map: PiAppliancePinMap = DEFAULT_PI_PIN_MAP) -> list[str]:
    errors: list[str] = []
    protected = pin_map.bossdac_i2s
    protected_gpios = {assignment.gpio: assignment for assignment in protected.values()}
    for assignment in active_assignments(pin_map):
        if assignment.gpio in protected_gpios:
            errors.append(
                f"{describe_assignment(assignment)} conflicts with "
                f"{describe_assignment(protected_gpios[assignment.gpio])}"
            )
    errors.extend(_duplicate_gpio_errors(active_assignments(pin_map)))
    if pin_map.speaker_uses_gpio:
        errors.append("Speaker path must not allocate GPIO; use USB DAC audio plus 5V/GND.")
    return errors


def _duplicate_gpio_errors(assignments: Iterable[GpioAssignment]) -> list[str]:
    by_gpio: dict[int, list[GpioAssignment]] = {}
    for assignment in assignments:
        by_gpio.setdefault(assignment.gpio, []).append(assignment)
    errors: list[str] = []
    for gpio, matches in by_gpio.items():
        if len(matches) <= 1:
            continue
        errors.append(
            f"GPIO{gpio} is assigned more than once: "
            + ", ".join(match.name for match in matches)
        )
    return errors

