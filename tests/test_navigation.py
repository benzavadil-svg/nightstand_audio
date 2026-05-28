from __future__ import annotations

import unittest
from datetime import datetime, timedelta

from app.models import UIMode
from app.services.navigation import NavigationController


class NavigationControllerTest(unittest.TestCase):
    def test_home_knob_turn_controls_volume_and_press_toggles_playback(self) -> None:
        nav = NavigationController(timeout_seconds=15)

        self.assertEqual(nav.current_mode, UIMode.HOME)
        self.assertEqual(nav.handle_turn(-1).action, "volume_delta")
        self.assertEqual(nav.handle_turn(-1).value, 4)
        self.assertEqual(nav.handle_press().action, "toggle_play")

    def test_long_press_opens_menu_and_menu_turn_moves_selection(self) -> None:
        nav = NavigationController(timeout_seconds=15)

        result = nav.handle_long_press()
        self.assertEqual(result.action, "render")
        self.assertEqual(nav.current_mode, UIMode.MENU)

        nav.handle_turn(1)
        self.assertEqual(nav.selected_index, 1)
        self.assertEqual(nav.handle_press().action, "open_source_tracks")

        nav.handle_long_press()
        self.assertEqual(nav.current_mode, UIMode.HOME)

    def test_menu_times_out_to_home(self) -> None:
        nav = NavigationController(timeout_seconds=15)
        nav.open_menu()
        nav.last_input_at = datetime.now() - timedelta(seconds=16)

        self.assertTrue(nav.timeout_to_home(datetime.now()))
        self.assertEqual(nav.current_mode, UIMode.HOME)


if __name__ == "__main__":
    unittest.main()
