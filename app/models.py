from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from enum import Enum
from pathlib import Path
from typing import Callable


AUDIO_EXTENSIONS = {".mp3", ".m4a", ".aac", ".flac", ".wav"}


@dataclass(frozen=True)
class SourceDefinition:
    id: str
    relative_dir: Path
    legacy_relative_dir: Path | None = None


SOURCE_DEFINITIONS: dict[str, SourceDefinition] = {
    "button-1": SourceDefinition(
        id="button-1",
        relative_dir=Path("buttons/button-1"),
        legacy_relative_dir=Path("podcasts/bible-in-a-year"),
    ),
    "button-2": SourceDefinition(
        id="button-2",
        relative_dir=Path("buttons/button-2"),
        legacy_relative_dir=Path("podcasts/sleep-baseball"),
    ),
    "button-3": SourceDefinition(
        id="button-3",
        relative_dir=Path("buttons/button-3"),
        legacy_relative_dir=Path("music/night-albums"),
    ),
    "sounds": SourceDefinition(
        id="sounds",
        relative_dir=Path("sounds"),
    ),
}


@dataclass(frozen=True)
class SourceMetadata:
    source_id: str
    display_name: str
    source_type: str = "playlist"
    ordering: str = "filename_asc"
    resume_policy: str = "resume_playlist"
    completion_threshold_percent: int = 95
    end_behavior: str = "stop"
    loop_enabled: bool = False


@dataclass
class MediaItem:
    source_id: str
    file_path: str
    title: str
    artist: str | None = None
    duration_seconds: int | None = None
    sort_key: str = ""
    id: int | None = None
    last_position_seconds: float = 0
    completed: bool = False
    play_count: int = 0
    last_played_at: datetime | None = None


class PlaybackState(str, Enum):
    STOPPED = "stopped"
    PLAYING = "playing"
    PAUSED = "paused"


class MediaCommand(str, Enum):
    PLAY_PAUSE = "PLAY_PAUSE"
    NEXT_TRACK = "NEXT_TRACK"
    PREVIOUS_TRACK = "PREVIOUS_TRACK"
    VOLUME_UP = "VOLUME_UP"
    VOLUME_DOWN = "VOLUME_DOWN"


@dataclass
class PlaybackStatus:
    state: PlaybackState = PlaybackState.STOPPED
    source_id: str | None = None
    item_id: int | None = None
    title: str = "Clock"
    subtitle: str = ""
    position_seconds: float = 0
    duration_seconds: int | None = None
    volume: int = 35
    track_index: int | None = None
    queue_length: int | None = None
    ended: bool = False
    exit_returncode: int | None = None

    @property
    def is_audio_active(self) -> bool:
        return self.state in {PlaybackState.PLAYING, PlaybackState.PAUSED}


@dataclass
class QueueEntry:
    item: MediaItem
    index: int


@dataclass
class PlaybackSession:
    source_id: str
    current_track_id: int | None = None
    current_track_index: int = 0
    last_position_seconds: float = 0
    is_playing: bool = False
    stop_reason: str | None = None
    queue_order: list[int] = field(default_factory=list)
    updated_at: datetime | None = None


DEFAULT_BLUETOOTH_DEVICE_NAME = "Headphones"


@dataclass
class BluetoothDevice:
    mac: str
    name: str
    connected: bool = False
    trusted: bool = False


@dataclass
class BluetoothRuntimeState:
    trusted_device_name: str = DEFAULT_BLUETOOTH_DEVICE_NAME
    preferred_output: str = "dac"
    active_sink: str = "dac"
    connected: bool = False
    reconnecting: bool = False
    reconnect_started_at: datetime | None = None
    reconnect_timeout_seconds: int = 30
    last_message: str = ""
    phase: str = "UNPAIRED"
    preferred_device_name: str = DEFAULT_BLUETOOTH_DEVICE_NAME
    preferred_device_mac: str = ""
    bluetooth_audio_device: str = ""
    last_successful_connection_at: datetime | None = None
    discovered_devices: list[BluetoothDevice] = field(default_factory=list)
    selected_device_index: int = 0


@dataclass
class AlarmConfig:
    enabled: bool = False
    hour: int = 7
    minute: int = 0
    source_id: str = "button-1"
    target_volume: int = 40
    fade_in_seconds: int = 60
    snooze_minutes: int = 9
    last_triggered_date: date | None = None
    wake_enabled: bool = True
    wake_lead_minutes: int = 30
    wake_stages: int = 4
    stage_volume_curve: list[int] = field(default_factory=lambda: [5, 10, 20, 35])
    stage_source: str = "sounds"
    interrupt_active_playback: bool = False
    last_dismissed_date: date | None = None

    def label(self) -> str:
        suffix = "AM" if self.hour < 12 else "PM"
        hour = self.hour % 12 or 12
        return f"{hour}:{self.minute:02d} {suffix}"


@dataclass
class AlarmRuntimeState:
    phase: str = "IDLE"
    active: bool = False
    fading: bool = False
    fade_volume: int = 0
    snoozed_until: datetime | None = None
    wake_stage: int = 0
    wake_stages: int = 0
    stage_label: str = ""
    target_volume: int = 0
    queued: bool = False
    output_label: str = "Alarm Speaker"

    @property
    def is_engaged(self) -> bool:
        return self.phase in {"WAKE_STAGE", "ALARM_ACTIVE", "SNOOZE"}


class UIMode(str, Enum):
    AMBIENT = "AMBIENT"
    HOME = "HOME"
    SLEEP_SCREEN = "SLEEP_SCREEN"
    MENU = "MENU"
    SOURCE_DETAIL = "SOURCE_DETAIL"
    SLEEP_TIMER = "SLEEP_TIMER"
    ALARM = "ALARM"
    OUTPUT_SELECT = "OUTPUT_SELECT"
    BLUETOOTH_PAIRING = "BLUETOOTH_PAIRING"


@dataclass
class MenuItem:
    id: str
    label: str
    action: str
    children: list["MenuItem"] = field(default_factory=list)


@dataclass
class InputEvent:
    type: str
    value: str | int | None = None


@dataclass
class RenderState:
    now: datetime
    mode: UIMode
    playback: PlaybackStatus
    current_source_label: str
    sleep_timer_label: str
    alarm: AlarmConfig
    alarm_runtime: AlarmRuntimeState
    menu_title: str = "Home"
    menu_items: list[MenuItem] = field(default_factory=list)
    selected_index: int = 0
    detail_title: str = ""
    detail_subtitle: str = ""
    output_label: str = "Headphones"
    track_index: int | None = None
    queue_length: int | None = None
    progress_label: str = ""
    alarm_source_label: str = ""
    source_complete: bool = False
    completed_count: int = 0
    bluetooth: BluetoothRuntimeState = field(default_factory=BluetoothRuntimeState)
    is_night_mode_active: bool = False
    is_sleep_screen_locked: bool = False
    last_display_wake_at: datetime | None = None
    is_ambient_mode_active: bool = False
    is_active_mode_active: bool = False
    last_active_interaction_at: datetime | None = None
    ambient_show_playback_glyph: bool = True


ActionHandler = Callable[[str, str | int | None], None]
