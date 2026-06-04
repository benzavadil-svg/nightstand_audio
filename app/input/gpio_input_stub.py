from __future__ import annotations

import threading
from collections import deque
from collections.abc import Callable
from typing import Any

from app.hardware.pin_map import DEFAULT_PI_PIN_MAP, PiAppliancePinMap, validate_pin_map
from app.input.base import InputAdapter
from app.models import InputEvent
from app.services.logger import get_logger


class GPIOInput(InputAdapter):
    """Raspberry Pi GPIO input adapter for the appliance hardware.

    The adapter emits the same normalized events as the keyboard simulator:
    - rotary turn -> InputEvent("turn", +/-1)
    - rotary click -> InputEvent("press")
    - rotary hold -> InputEvent("long_press")
    - source buttons -> InputEvent("source", "button-1" / "button-2" / "button-3")

    GPIO numbering is BCM/GPIO numbering, not physical pin numbering.
    """

    def __init__(
        self,
        pin_map: PiAppliancePinMap = DEFAULT_PI_PIN_MAP,
        *,
        button_factory: Callable[..., Any] | None = None,
        rotary_factory: Callable[..., Any] | None = None,
        bounce_time: float = 0.03,
        hold_time: float = 0.75,
    ) -> None:
        validate_pin_map(pin_map)
        self.pin_map = pin_map
        self.log = get_logger("INPUT")
        self._events: deque[InputEvent] = deque()
        self._condition = threading.Condition()
        self._devices: list[Any] = []
        self._held_buttons: dict[str, bool] = {}
        Button, RotaryEncoder = self._load_gpiozero(button_factory, rotary_factory)
        try:
            self._setup_source_buttons(Button, bounce_time, hold_time)
            self._setup_rotary_encoder(RotaryEncoder, Button, bounce_time, hold_time)
            self._log_assignments()
        except Exception as exc:
            self.close()
            raise GPIOInputUnavailableError(
                "GPIO input unavailable while claiming pins. "
                f"{_gpio_diagnostic_hint()}"
            ) from exc

    def poll(self, timeout_seconds: float = 0.25) -> InputEvent | None:
        with self._condition:
            if not self._events:
                self._condition.wait(timeout_seconds)
            if not self._events:
                return None
            return self._events.popleft()

    def close(self) -> None:
        for device in self._devices:
            close = getattr(device, "close", None)
            if callable(close):
                close()

    def _load_gpiozero(
        self,
        button_factory: Callable[..., Any] | None,
        rotary_factory: Callable[..., Any] | None,
    ) -> tuple[Callable[..., Any], Callable[..., Any]]:
        if button_factory and rotary_factory:
            return button_factory, rotary_factory
        try:
            from gpiozero import Button, RotaryEncoder
        except ImportError as exc:
            raise RuntimeError(
                "GPIO input requires gpiozero. Install gpiozero/lgpio on the Pi or use "
                "INPUT_BACKEND=keyboard for simulator input."
            ) from exc
        return button_factory or Button, rotary_factory or RotaryEncoder

    def _setup_source_buttons(
        self,
        Button: Callable[..., Any],
        bounce_time: float,
        hold_time: float,
    ) -> None:
        for source_id, assignment in self.pin_map.source_buttons.items():
            button = self._claim_button(
                Button,
                assignment.gpio,
                pull_up=True,
                bounce_time=bounce_time,
                hold_time=hold_time,
            )
            self._held_buttons[source_id] = False
            button.when_released = self._source_button_release_callback(source_id)
            button.when_held = self._source_button_hold_callback(source_id)
            self._devices.append(button)

    def _setup_rotary_encoder(
        self,
        RotaryEncoder: Callable[..., Any],
        Button: Callable[..., Any],
        bounce_time: float,
        hold_time: float,
    ) -> None:
        rotary_a = self.pin_map.rotary["A"].gpio
        rotary_b = self.pin_map.rotary["B"].gpio
        try:
            rotary = self._claim_rotary(RotaryEncoder, rotary_a, rotary_b, max_steps=0)
        except TypeError:
            rotary = self._claim_rotary(RotaryEncoder, rotary_a, rotary_b)
        self._wire_rotary_callbacks(rotary)
        self._devices.append(rotary)

        switch = self._claim_button(
            Button,
            self.pin_map.rotary["SW"].gpio,
            pull_up=True,
            bounce_time=bounce_time,
            hold_time=hold_time,
        )
        self._held_buttons["rotary-sw"] = False
        switch.when_released = self._rotary_switch_release_callback()
        switch.when_held = self._rotary_switch_hold_callback()
        self._devices.append(switch)

    def _wire_rotary_callbacks(self, rotary: Any) -> None:
        if hasattr(rotary, "when_rotated_clockwise"):
            rotary.when_rotated_clockwise = self._callback(InputEvent("turn", 1))
        if hasattr(rotary, "when_rotated_counter_clockwise"):
            rotary.when_rotated_counter_clockwise = self._callback(InputEvent("turn", -1))
        if hasattr(rotary, "when_rotated") and not hasattr(rotary, "when_rotated_clockwise"):
            previous_steps = int(getattr(rotary, "steps", 0))

            def rotated() -> None:
                nonlocal previous_steps
                current_steps = int(getattr(rotary, "steps", previous_steps))
                delta = current_steps - previous_steps
                previous_steps = current_steps
                if delta:
                    self._enqueue(InputEvent("turn", 1 if delta > 0 else -1))

            rotary.when_rotated = rotated

    def _callback(self, event: InputEvent) -> Callable[[], None]:
        def callback() -> None:
            self._enqueue(event)

        return callback

    def _source_button_hold_callback(self, source_id: str) -> Callable[[], None]:
        def callback() -> None:
            self._held_buttons[source_id] = True
            if source_id == "button-3":
                self._enqueue(InputEvent("sleep_timer"))

        return callback

    def _source_button_release_callback(self, source_id: str) -> Callable[[], None]:
        def callback() -> None:
            if self._held_buttons.get(source_id):
                self._held_buttons[source_id] = False
                return
            self._enqueue(InputEvent("source", source_id))

        return callback

    def _rotary_switch_hold_callback(self) -> Callable[[], None]:
        def callback() -> None:
            self._held_buttons["rotary-sw"] = True
            self._enqueue(InputEvent("long_press"))

        return callback

    def _rotary_switch_release_callback(self) -> Callable[[], None]:
        def callback() -> None:
            if self._held_buttons.get("rotary-sw"):
                self._held_buttons["rotary-sw"] = False
                return
            self._enqueue(InputEvent("press"))

        return callback

    def _enqueue(self, event: InputEvent) -> None:
        with self._condition:
            self._events.append(event)
            self._condition.notify()
        if event.type == "turn":
            self.log.info("Encoder rotation delta=%s", event.value)
        elif event.type == "press":
            self.log.info("Encoder press")
        elif event.type == "long_press":
            self.log.info("Encoder long press")
        elif event.type == "source":
            self.log.info("Button press source=%s", event.value)
        else:
            self.log.info("GPIO input event=%s value=%s", event.type, event.value)

    def _log_assignments(self) -> None:
        for source_id, assignment in self.pin_map.source_buttons.items():
            self.log.info(
                "GPIO source button source=%s gpio=%s physical_pin=%s",
                source_id,
                assignment.gpio,
                assignment.physical_pin,
            )
        for name, assignment in self.pin_map.rotary.items():
            self.log.info(
                "GPIO rotary pin=%s gpio=%s physical_pin=%s",
                name,
                assignment.gpio,
                assignment.physical_pin,
            )
        self.log.info("Speaker GPIO assignment: none; speaker uses USB DAC audio.")

    def _claim_button(self, Button: Callable[..., Any], gpio: int, **kwargs) -> Any:
        try:
            return Button(gpio, **kwargs)
        except Exception as exc:
            self.log.error(
                "GPIO claim failed gpio=%s error=%s hint='%s'",
                gpio,
                exc,
                _gpio_diagnostic_hint(),
            )
            raise

    def _claim_rotary(self, RotaryEncoder: Callable[..., Any], gpio_a: int, gpio_b: int, **kwargs) -> Any:
        try:
            return RotaryEncoder(gpio_a, gpio_b, **kwargs)
        except TypeError:
            raise
        except Exception as exc:
            self.log.error(
                "GPIO rotary claim failed gpio_a=%s gpio_b=%s error=%s hint='%s'",
                gpio_a,
                gpio_b,
                exc,
                _gpio_diagnostic_hint(),
            )
            raise


class GPIOInputUnavailableError(RuntimeError):
    pass


def _gpio_diagnostic_hint() -> str:
    return (
        "Check for another running app or a reserved pin: "
        "`python -m scripts.diagnose_gpio`, `pinctrl get 22`, "
        "`gpioinfo` or `gpioinfo /dev/gpiochip0`, and "
        "`sudo fuser -v /dev/gpiochip*`."
    )
