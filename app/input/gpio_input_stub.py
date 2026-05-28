from __future__ import annotations

from app.input.base import InputAdapter


class GPIOInput(InputAdapter):
    """Future Raspberry Pi GPIO input adapter.

    Contract:
    - Rotary turn emits InputEvent("turn", +/-1).
    - Rotary short press emits InputEvent("press").
    - Rotary long press emits InputEvent("long_press").
    - Double/triple press detection stays in the controller so keyboard and GPIO behave identically.
    - Preset buttons emit InputEvent("source", source_id).
    - Long-press Button 3 emits InputEvent("sleep_timer").

    TODO: Choose gpiozero, lgpio, or another Bookworm-friendly GPIO library.
    TODO: Debounce the rotary encoder CLK/DT transitions.
    TODO: Debounce all momentary buttons.
    TODO: Keep pin numbers and pull-up/down config in Pi-specific config, not controller logic.
    """

    def poll(self, timeout_seconds: float = 0.25):
        raise NotImplementedError("GPIO input is not implemented yet.")
