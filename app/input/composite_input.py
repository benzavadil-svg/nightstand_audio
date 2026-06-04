from __future__ import annotations

from app.input.base import InputAdapter
from app.models import InputEvent


class CompositeInput(InputAdapter):
    """Poll multiple input adapters as one normalized input stream."""

    def __init__(self, *adapters: InputAdapter) -> None:
        self.adapters = [adapter for adapter in adapters if adapter is not None]

    def poll(self, timeout_seconds: float = 0.25) -> InputEvent | None:
        if not self.adapters:
            return None
        per_adapter_timeout = timeout_seconds / len(self.adapters)
        for adapter in self.adapters:
            event = adapter.poll(per_adapter_timeout)
            if event:
                return event
        return None

    def raw_mode(self):
        for adapter in self.adapters:
            raw_mode = getattr(adapter, "raw_mode", None)
            if callable(raw_mode):
                return raw_mode()
        raise AttributeError("No child input adapter supports raw_mode().")

    def close(self) -> None:
        for adapter in self.adapters:
            close = getattr(adapter, "close", None)
            if callable(close):
                close()

