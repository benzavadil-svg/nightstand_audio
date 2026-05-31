from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class Settings:
    project_root: Path
    media_dir: Path
    data_dir: Path
    db_path: Path
    screen_path: Path
    runtime_mode: str
    display_backend: str
    hardware_fallback_to_simulator: bool
    display_model: str
    display_width: int
    display_height: int
    menu_timeout_seconds: int
    use_real_epd: bool
    epd_rotate_degrees: int
    clear_epd_on_exit: bool
    epd_full_clear_interval: int
    force_epd_update: bool
    epd_reinit_every_update: bool
    clear_before_epd_update: bool
    epd_render_debounce_ms: int
    epd_volume_refresh_debounce_ms: int
    epd_refresh_on_volume_change: bool
    epd_partial_update_enabled: bool
    epd_disable_partial: bool
    epd_one_shot_major_transitions: bool
    epd_region_partial_enabled: bool
    epd_partial_streak_limit: int
    epd_partial_refresh_min_interval_ms: int
    epd_force_full_refresh: bool
    epd_force_clean_refresh: bool
    epd_clock_refresh_seconds: int
    epd_disable_clock_auto_refresh: bool
    night_mode_enabled: bool
    night_mode_start: str
    night_mode_end: str
    night_mode_wake_timeout_seconds: int
    night_mode_display_lock: bool
    ambient_mode_enabled: bool
    active_mode_timeout_seconds: int
    ambient_clock_refresh_seconds: int
    ambient_show_playback_glyph: bool
    audio_backend: str
    audio_device: str
    playback_backend: str
    restore_playback_on_startup: bool
    resume_on_startup: bool
    playback_restore_launch: bool
    validate_playlist_on_play: bool
    background_media_scan: bool

    @classmethod
    def from_env(cls) -> "Settings":
        _load_env_file(PROJECT_ROOT / ".env")
        data_dir = PROJECT_ROOT / "data"
        display_model = _normalize_display_model(
            os.getenv("DISPLAY_MODEL", "waveshare_5in83_v2")
        )
        default_width, default_height = _display_dimensions(display_model)
        return cls(
            project_root=PROJECT_ROOT,
            media_dir=Path(os.getenv("NIGHTSTAND_MEDIA_DIR", PROJECT_ROOT / "media")),
            data_dir=data_dir,
            db_path=Path(os.getenv("NIGHTSTAND_DB_PATH", data_dir / "nightstand.sqlite")),
            screen_path=Path(os.getenv("NIGHTSTAND_SCREEN_PATH", data_dir / "latest_screen.png")),
            runtime_mode=_normalize_choice(
                os.getenv("RUNTIME_MODE", "simulator"),
                {"simulator", "appliance"},
                "simulator",
            ),
            display_backend=_normalize_choice(
                os.getenv("DISPLAY_BACKEND", "png"),
                {"png", "waveshare"},
                "png",
            ),
            hardware_fallback_to_simulator=_env_bool("HARDWARE_FALLBACK_TO_SIMULATOR", True),
            display_model=display_model,
            display_width=int(os.getenv("NIGHTSTAND_DISPLAY_WIDTH", str(default_width))),
            display_height=int(os.getenv("NIGHTSTAND_DISPLAY_HEIGHT", str(default_height))),
            menu_timeout_seconds=int(os.getenv("NIGHTSTAND_MENU_TIMEOUT_SECONDS", "15")),
            use_real_epd=_env_bool("USE_REAL_EPD", False),
            epd_rotate_degrees=int(os.getenv("NIGHTSTAND_EPD_ROTATE", "0")),
            clear_epd_on_exit=_env_bool("CLEAR_EPD_ON_EXIT", False),
            epd_full_clear_interval=int(os.getenv("EPD_FULL_CLEAR_INTERVAL", "50")),
            force_epd_update=_env_bool("FORCE_EPD_UPDATE", False),
            epd_reinit_every_update=_env_bool(
                "EPD_REINIT_EVERY_UPDATE",
                _env_bool("EPD_REINIT_EACH_UPDATE", False),
            ),
            clear_before_epd_update=_env_bool("CLEAR_BEFORE_EPD_UPDATE", False),
            epd_render_debounce_ms=int(os.getenv("EPD_RENDER_DEBOUNCE_MS", "750")),
            epd_volume_refresh_debounce_ms=int(
                os.getenv("EPD_VOLUME_REFRESH_DEBOUNCE_MS", "600")
            ),
            epd_refresh_on_volume_change=_env_bool("EPD_REFRESH_ON_VOLUME_CHANGE", True),
            epd_partial_update_enabled=_env_bool("EPD_PARTIAL_UPDATE_ENABLED", True),
            epd_disable_partial=_env_bool("EPD_DISABLE_PARTIAL", False),
            epd_one_shot_major_transitions=_env_bool(
                "EPD_ONE_SHOT_MAJOR_TRANSITIONS",
                True,
            ),
            epd_region_partial_enabled=_env_bool("EPD_REGION_PARTIAL_ENABLED", True),
            epd_partial_streak_limit=int(os.getenv("EPD_PARTIAL_STREAK_LIMIT", "8")),
            epd_partial_refresh_min_interval_ms=int(
                os.getenv("EPD_PARTIAL_REFRESH_MIN_INTERVAL_MS", "500")
            ),
            epd_force_full_refresh=_env_bool("EPD_FORCE_FULL_REFRESH", False),
            epd_force_clean_refresh=_env_bool("EPD_FORCE_CLEAN_REFRESH", False),
            epd_clock_refresh_seconds=int(os.getenv("EPD_CLOCK_REFRESH_SECONDS", "60")),
            epd_disable_clock_auto_refresh=_env_bool("EPD_DISABLE_CLOCK_AUTO_REFRESH", False),
            night_mode_enabled=_env_bool("NIGHT_MODE_ENABLED", True),
            night_mode_start=os.getenv("NIGHT_MODE_START", "22:00"),
            night_mode_end=os.getenv("NIGHT_MODE_END", "06:00"),
            night_mode_wake_timeout_seconds=int(
                os.getenv("NIGHT_MODE_WAKE_TIMEOUT_SECONDS", "30")
            ),
            night_mode_display_lock=_env_bool("NIGHT_MODE_DISPLAY_LOCK", True),
            ambient_mode_enabled=_env_bool("AMBIENT_MODE_ENABLED", True),
            active_mode_timeout_seconds=int(os.getenv("ACTIVE_MODE_TIMEOUT_SECONDS", "30")),
            ambient_clock_refresh_seconds=int(os.getenv("AMBIENT_CLOCK_REFRESH_SECONDS", "60")),
            ambient_show_playback_glyph=_env_bool("AMBIENT_SHOW_PLAYBACK_GLYPH", True),
            audio_backend=os.getenv("AUDIO_BACKEND", "alsa"),
            audio_device=os.getenv("AUDIO_DEVICE", "auto"),
            playback_backend=_normalize_choice(
                os.getenv("PLAYBACK_BACKEND", "auto"),
                {"auto", "mock", "mpv"},
                "auto",
            ),
            restore_playback_on_startup=_env_bool("RESTORE_PLAYBACK_ON_STARTUP", True),
            resume_on_startup=_env_bool("RESUME_ON_STARTUP", False),
            playback_restore_launch=_env_bool("PLAYBACK_RESTORE_LAUNCH", False),
            validate_playlist_on_play=_env_bool("VALIDATE_PLAYLIST_ON_PLAY", False),
            background_media_scan=_env_bool("BACKGROUND_MEDIA_SCAN", True),
        )

    def ensure_dirs(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.media_dir.mkdir(parents=True, exist_ok=True)
        self.screen_path.parent.mkdir(parents=True, exist_ok=True)


def get_settings() -> Settings:
    settings = Settings.from_env()
    settings.ensure_dirs()
    return settings


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _load_env_file(path: Path) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


def _normalize_display_model(value: str) -> str:
    return _normalize_choice(
        value,
        {"waveshare_5in83_v2", "waveshare_4in2_v2"},
        "waveshare_5in83_v2",
    )


def _display_dimensions(display_model: str) -> tuple[int, int]:
    if display_model == "waveshare_4in2_v2":
        return 400, 300
    return 600, 448


def _normalize_choice(value: str, allowed: set[str], default: str) -> str:
    normalized = value.strip().lower()
    return normalized if normalized in allowed else default
