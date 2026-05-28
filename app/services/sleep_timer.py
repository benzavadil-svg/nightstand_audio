from __future__ import annotations

from datetime import datetime, timedelta


class SleepTimer:
    OPTIONS_MINUTES = [15, 30, 45, 60, 0]

    def __init__(self) -> None:
        self._option_index = len(self.OPTIONS_MINUTES) - 1
        self.deadline: datetime | None = None

    def cycle(self, now: datetime | None = None) -> None:
        now = now or datetime.now()
        self._option_index = (self._option_index + 1) % len(self.OPTIONS_MINUTES)
        minutes = self.OPTIONS_MINUTES[self._option_index]
        self.deadline = now + timedelta(minutes=minutes) if minutes else None

    def is_active(self) -> bool:
        return self.deadline is not None

    def expired(self, now: datetime | None = None) -> bool:
        now = now or datetime.now()
        return self.deadline is not None and now >= self.deadline

    def clear(self) -> None:
        self.deadline = None
        self._option_index = len(self.OPTIONS_MINUTES) - 1

    def label(self, now: datetime | None = None) -> str:
        now = now or datetime.now()
        if not self.deadline:
            return "Sleep off"
        remaining = max(0, int((self.deadline - now).total_seconds() // 60) + 1)
        return f"Sleep {remaining}m"
