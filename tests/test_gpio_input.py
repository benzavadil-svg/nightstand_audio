from __future__ import annotations

import unittest
from unittest.mock import patch

from app.hardware.pin_map import DEFAULT_PI_PIN_MAP
from app.input.gpio_input_stub import GPIOInput, GPIOInputUnavailableError
from app.input.keyboard_input import KeyboardInput
from app.main import _build_input_adapter


class FakeButton:
    def __init__(self, pin: int, **kwargs) -> None:
        self.pin = pin
        self.kwargs = kwargs
        self.when_pressed = None
        self.when_released = None
        self.when_held = None
        self.closed = False

    def press(self) -> None:
        if self.when_pressed:
            self.when_pressed()

    def release(self) -> None:
        if self.when_released:
            self.when_released()

    def click(self) -> None:
        self.press()
        self.release()

    def hold(self) -> None:
        if self.when_held:
            self.when_held()

    def close(self) -> None:
        self.closed = True


class FakeRotary:
    def __init__(self, a: int, b: int, **kwargs) -> None:
        self.a = a
        self.b = b
        self.kwargs = kwargs
        self.when_rotated_clockwise = None
        self.when_rotated_counter_clockwise = None
        self.closed = False

    def clockwise(self) -> None:
        if self.when_rotated_clockwise:
            self.when_rotated_clockwise()

    def counter_clockwise(self) -> None:
        if self.when_rotated_counter_clockwise:
            self.when_rotated_counter_clockwise()

    def close(self) -> None:
        self.closed = True


class GPIOInputTest(unittest.TestCase):
    def make_input(self):
        buttons: list[FakeButton] = []
        rotaries: list[FakeRotary] = []

        def button_factory(pin: int, **kwargs):
            button = FakeButton(pin, **kwargs)
            buttons.append(button)
            return button

        def rotary_factory(a: int, b: int, **kwargs):
            rotary = FakeRotary(a, b, **kwargs)
            rotaries.append(rotary)
            return rotary

        adapter = GPIOInput(
            DEFAULT_PI_PIN_MAP,
            button_factory=button_factory,
            rotary_factory=rotary_factory,
        )
        return adapter, buttons, rotaries

    def test_source_buttons_map_to_button_sources(self) -> None:
        adapter, buttons, _ = self.make_input()
        by_pin = {button.pin: button for button in buttons}

        by_pin[22].click()
        by_pin[23].click()
        by_pin[26].click()

        self.assertEqual(adapter.poll(0).value, "button-1")
        self.assertEqual(adapter.poll(0).value, "button-2")
        self.assertEqual(adapter.poll(0).value, "button-3")

    def test_rotary_maps_to_turn_and_click_events(self) -> None:
        adapter, buttons, rotaries = self.make_input()
        switch = {button.pin: button for button in buttons}[16]
        rotary = rotaries[0]

        rotary.clockwise()
        rotary.counter_clockwise()
        switch.click()
        switch.hold()
        switch.release()

        first = adapter.poll(0)
        second = adapter.poll(0)
        third = adapter.poll(0)
        fourth = adapter.poll(0)

        self.assertEqual((first.type, first.value), ("turn", 1))
        self.assertEqual((second.type, second.value), ("turn", -1))
        self.assertEqual(third.type, "press")
        self.assertEqual(fourth.type, "long_press")

    def test_button_three_hold_maps_to_sleep_timer(self) -> None:
        adapter, buttons, _ = self.make_input()
        button_three = {button.pin: button for button in buttons}[26]

        button_three.hold()
        button_three.release()

        self.assertEqual(adapter.poll(0).type, "sleep_timer")
        self.assertIsNone(adapter.poll(0))

    def test_close_closes_gpio_devices(self) -> None:
        adapter, buttons, rotaries = self.make_input()

        adapter.close()

        self.assertTrue(all(button.closed for button in buttons))
        self.assertTrue(all(rotary.closed for rotary in rotaries))

    def test_pin_claim_failure_raises_gpio_unavailable_and_closes_claimed_devices(self) -> None:
        buttons: list[FakeButton] = []

        def button_factory(pin: int, **kwargs):
            if pin == 23:
                raise RuntimeError("GPIO busy")
            button = FakeButton(pin, **kwargs)
            buttons.append(button)
            return button

        with self.assertRaises(GPIOInputUnavailableError) as raised:
            GPIOInput(
                DEFAULT_PI_PIN_MAP,
                button_factory=button_factory,
                rotary_factory=FakeRotary,
            )

        self.assertIn("GPIO input unavailable", str(raised.exception))
        self.assertTrue(all(button.closed for button in buttons))

    def test_gpio_keyboard_falls_back_to_keyboard_if_gpio_is_busy(self) -> None:
        with patch(
            "app.main.GPIOInput",
            side_effect=GPIOInputUnavailableError("GPIO input unavailable"),
        ):
            adapter = _build_input_adapter("gpio_keyboard", "appliance")

        self.assertIsInstance(adapter, KeyboardInput)


if __name__ == "__main__":
    unittest.main()
