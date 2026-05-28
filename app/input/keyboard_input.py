from __future__ import annotations

import select
import sys
import termios
import tty
from collections.abc import Iterator
from contextlib import contextmanager

from app.input.base import InputAdapter
from app.models import InputEvent, MediaCommand
from app.services.logger import get_logger


class KeyboardInput(InputAdapter):
    def __init__(self) -> None:
        self.log = get_logger("SIM")
        self.input_log = get_logger("INPUT")

    @contextmanager
    def raw_mode(self) -> Iterator[None]:
        if not sys.stdin.isatty():
            yield
            return
        old_settings = termios.tcgetattr(sys.stdin)
        try:
            tty.setcbreak(sys.stdin.fileno())
            yield
        finally:
            termios.tcsetattr(sys.stdin, termios.TCSADRAIN, old_settings)

    def poll(self, timeout_seconds: float = 0.25) -> InputEvent | None:
        if not sys.stdin.isatty():
            return None
        readable, _, _ = select.select([sys.stdin], [], [], timeout_seconds)
        if not readable:
            return None
        char = sys.stdin.read(1)
        if char == "\x1b":
            sequence = sys.stdin.read(2)
            if sequence == "[A":
                return self._event(InputEvent("turn", -1), "keyboard up")
            if sequence == "[B":
                return self._event(InputEvent("turn", 1), "keyboard down")
            return None
        if char in {"\r", "\n"}:
            return self._event(InputEvent("press"), "keyboard enter")
        if char in {"\x7f", "\b"}:
            return self._event(InputEvent("long_press"), "keyboard backspace")
        if char == " ":
            return self._event(InputEvent("play_pause"), "keyboard space")
        mapping = {
            "1": InputEvent("source", "button-1"),
            "2": InputEvent("source", "button-2"),
            "3": InputEvent("source", "button-3"),
            "t": InputEvent("sleep_timer"),
            "a": InputEvent("alarm_toggle"),
            "[": InputEvent("alarm_adjust", -5),
            "]": InputEvent("alarm_adjust", 5),
            "s": InputEvent("snooze"),
            "x": InputEvent("stop_alarm"),
            "p": InputEvent("media_command", MediaCommand.PLAY_PAUSE),
            "n": InputEvent("media_command", MediaCommand.NEXT_TRACK),
            "b": InputEvent("media_command", MediaCommand.PREVIOUS_TRACK),
            "y": InputEvent("bluetooth_success"),
            "u": InputEvent("bluetooth_failure"),
            "r": InputEvent("render"),
            "q": InputEvent("quit"),
        }
        event = mapping.get(char)
        return self._event(event, f"keyboard {char}") if event else None

    def _event(self, event: InputEvent | None, source: str) -> InputEvent | None:
        if event:
            self.log.info(
                "Keyboard command received source=%s event=%s value=%s",
                source,
                event.type,
                event.value,
            )
            self.input_log.debug("Input event event=%s value=%s", event.type, event.value)
        return event
