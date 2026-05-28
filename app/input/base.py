from __future__ import annotations

from abc import ABC, abstractmethod

from app.models import InputEvent


class InputAdapter(ABC):
    @abstractmethod
    def poll(self, timeout_seconds: float = 0.25) -> InputEvent | None:
        raise NotImplementedError
