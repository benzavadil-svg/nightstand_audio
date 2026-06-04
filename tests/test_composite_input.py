from __future__ import annotations

import unittest
from contextlib import contextmanager

from app.input.base import InputAdapter
from app.input.composite_input import CompositeInput
from app.models import InputEvent


class QueueInput(InputAdapter):
    def __init__(self, *events: InputEvent) -> None:
        self.events = list(events)
        self.closed = False

    def poll(self, timeout_seconds: float = 0.25) -> InputEvent | None:
        return self.events.pop(0) if self.events else None

    def close(self) -> None:
        self.closed = True


class RawModeInput(QueueInput):
    def __init__(self) -> None:
        super().__init__()
        self.raw_mode_entered = False

    @contextmanager
    def raw_mode(self):
        self.raw_mode_entered = True
        yield


class CompositeInputTest(unittest.TestCase):
    def test_poll_returns_first_available_child_event(self) -> None:
        first = QueueInput()
        second = QueueInput(InputEvent("source", "button-1"))
        composite = CompositeInput(first, second)

        event = composite.poll(0)

        self.assertEqual(event.type, "source")
        self.assertEqual(event.value, "button-1")

    def test_raw_mode_delegates_to_keyboard_like_child(self) -> None:
        first = QueueInput()
        second = RawModeInput()
        composite = CompositeInput(first, second)

        with composite.raw_mode():
            pass

        self.assertTrue(second.raw_mode_entered)

    def test_close_closes_all_children(self) -> None:
        first = QueueInput()
        second = QueueInput()
        composite = CompositeInput(first, second)

        composite.close()

        self.assertTrue(first.closed)
        self.assertTrue(second.closed)


if __name__ == "__main__":
    unittest.main()

