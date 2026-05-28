from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from app.models import MenuItem, SOURCE_DEFINITIONS, UIMode


@dataclass(frozen=True)
class NavigationResult:
    action: str
    value: str | int | None = None


class NavigationController:
    def __init__(self, timeout_seconds: int = 15) -> None:
        self.timeout_seconds = timeout_seconds
        self.current_mode = UIMode.HOME
        self.selected_index = 0
        self.current_menu = self._main_menu()
        self.menu_title = "Home"
        self.menu_source_id: str | None = None
        self.last_input_at = datetime.now()

    def handle_turn(self, delta: int) -> NavigationResult:
        self._touch()
        if self.current_mode == UIMode.HOME:
            return NavigationResult("volume_delta", -delta * 4)
        if self.current_mode == UIMode.MENU and self.current_menu:
            self.selected_index = (self.selected_index + delta) % len(self.current_menu)
            return NavigationResult("render")
        if self.current_mode == UIMode.SLEEP_TIMER:
            return NavigationResult("sleep_timer")
        if self.current_mode == UIMode.ALARM:
            return NavigationResult("alarm_adjust", 5 if delta > 0 else -5)
        return NavigationResult("render")

    def handle_press(self) -> NavigationResult:
        self._touch()
        if self.current_mode == UIMode.HOME:
            return NavigationResult("toggle_play")
        if self.current_mode == UIMode.MENU and self.current_menu:
            item = self.current_menu[self.selected_index]
            return NavigationResult(item.action, item.id)
        if self.current_mode == UIMode.SLEEP_TIMER:
            return NavigationResult("sleep_timer")
        if self.current_mode == UIMode.ALARM:
            return NavigationResult("alarm_toggle")
        if self.current_mode == UIMode.OUTPUT_SELECT:
            return NavigationResult("render")
        if self.current_mode == UIMode.SOURCE_DETAIL:
            return NavigationResult("toggle_play")
        return NavigationResult("render")

    def handle_long_press(self) -> NavigationResult:
        self._touch()
        if self.current_mode == UIMode.HOME:
            self.open_menu()
        else:
            self.go_home()
        return NavigationResult("render")

    def open_menu(self) -> None:
        self.current_mode = UIMode.MENU
        self.selected_index = 0
        self.current_menu = self._main_menu()
        self.menu_title = "Home"
        self.menu_source_id = None
        self._touch()

    def open_track_menu(
        self, source_id: str, title: str, items: list[MenuItem], selected_index: int = 0
    ) -> None:
        self.current_mode = UIMode.MENU
        self.current_menu = items
        self.selected_index = max(0, min(selected_index, len(items) - 1)) if items else 0
        self.menu_title = title
        self.menu_source_id = source_id
        self._touch()

    def open_mode(self, mode: UIMode) -> None:
        self.current_mode = mode
        self._touch()

    def go_home(self) -> None:
        self.current_mode = UIMode.HOME
        self.menu_title = "Home"
        self.menu_source_id = None
        self._touch()

    def timeout_to_home(self, now: datetime | None = None) -> bool:
        now = now or datetime.now()
        if self.current_mode == UIMode.HOME:
            return False
        if (now - self.last_input_at).total_seconds() >= self.timeout_seconds:
            self.current_mode = UIMode.HOME
            return True
        return False

    def _touch(self) -> None:
        self.last_input_at = datetime.now()

    def _main_menu(self) -> list[MenuItem]:
        source_items = [
            MenuItem(source_id, source_id.replace("-", " ").title(), "open_source_tracks")
            for source_id in SOURCE_DEFINITIONS
        ]
        return [
            MenuItem("resume-last", "Resume Last", "resume_last"),
            *source_items,
            MenuItem("sleep-timer", "Sleep Timer", "open_sleep_timer"),
            MenuItem("alarm", "Alarm", "open_alarm"),
            MenuItem("output", "Output", "open_output"),
        ]
