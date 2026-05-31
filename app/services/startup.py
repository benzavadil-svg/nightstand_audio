from __future__ import annotations

import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Iterator

from app.services.logger import get_logger


@dataclass
class StartupProfiler:
    started_at: float = field(default_factory=time.perf_counter)
    _spans: dict[str, float] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.log = get_logger("STARTUP")

    @contextmanager
    def span(self, name: str) -> Iterator[None]:
        started = time.perf_counter()
        try:
            yield
        finally:
            self.record(name, (time.perf_counter() - started) * 1000)

    def record(self, name: str, duration_ms: float) -> None:
        self._spans[name] = duration_ms
        self.log.info("step=%s duration_ms=%.1f", name, duration_ms)

    def total(self) -> float:
        duration_ms = (time.perf_counter() - self.started_at) * 1000
        self.log.info("step=total_startup duration_ms=%.1f", duration_ms)
        self.log.info("total_ms=%.1f", duration_ms)
        return duration_ms
