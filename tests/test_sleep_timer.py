from __future__ import annotations

import unittest
from datetime import datetime, timedelta

from app.services.sleep_timer import SleepTimer


class SleepTimerTest(unittest.TestCase):
    def test_cycles_through_sleep_options_then_off(self) -> None:
        timer = SleepTimer()
        now = datetime(2026, 5, 24, 22, 0)

        expected_minutes = [15, 30, 45, 60]
        for minutes in expected_minutes:
            timer.cycle(now)
            self.assertTrue(timer.is_active())
            self.assertEqual(timer.deadline, now + timedelta(minutes=minutes))

        timer.cycle(now)
        self.assertFalse(timer.is_active())
        self.assertEqual(timer.label(now), "Sleep off")


if __name__ == "__main__":
    unittest.main()
