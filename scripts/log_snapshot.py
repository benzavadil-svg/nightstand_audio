from __future__ import annotations

import json
import os
from pathlib import Path

from app.config import get_settings
from app.models import SOURCE_DEFINITIONS
from app.services.logger import LOG_FILE
from app.state_store import StateStore


def main() -> None:
    settings = get_settings()
    store = StateStore(settings.db_path)
    source_id = store.get_current_source_id()
    session = store.get_playback_session(source_id) if source_id else None
    current_track = store.get_item(session.current_track_id) if session and session.current_track_id else None

    snapshot = {
        "app_state": {
            "db_path": str(settings.db_path),
            "media_dir": str(settings.media_dir),
            "current_source_id": source_id,
            "preferred_output": store.get_app_state_value("preferred_output") or "dac",
            "alarm_enabled": store.get_alarm_config().enabled,
            "alarm_time": store.get_alarm_config().label(),
        },
        "display_state": {
            "screen_path": str(settings.screen_path),
            "screen_exists": settings.screen_path.exists(),
            "screen_size_bytes": _file_size(settings.screen_path),
            "display_model": settings.display_model,
            "resolution": f"{settings.display_width}x{settings.display_height}",
            "use_real_epd": settings.use_real_epd,
            "clear_epd_on_exit": settings.clear_epd_on_exit,
            "epd_full_clear_interval": settings.epd_full_clear_interval,
            "epd_render_debounce_ms": settings.epd_render_debounce_ms,
            "epd_volume_refresh_debounce_ms": settings.epd_volume_refresh_debounce_ms,
            "epd_refresh_on_volume_change": settings.epd_refresh_on_volume_change,
            "epd_partial_update_enabled": settings.epd_partial_update_enabled,
            "epd_disable_partial": settings.epd_disable_partial,
            "epd_one_shot_major_transitions": settings.epd_one_shot_major_transitions,
            "epd_region_partial_enabled": settings.epd_region_partial_enabled,
            "epd_partial_streak_limit": settings.epd_partial_streak_limit,
            "epd_partial_refresh_min_interval_ms": settings.epd_partial_refresh_min_interval_ms,
            "epd_force_full_refresh": settings.epd_force_full_refresh,
            "epd_force_clean_refresh": settings.epd_force_clean_refresh,
            "epd_clock_refresh_seconds": settings.epd_clock_refresh_seconds,
            "epd_disable_clock_auto_refresh": settings.epd_disable_clock_auto_refresh,
            "epd_reinit_every_update": settings.epd_reinit_every_update,
            "clear_before_epd_update": settings.clear_before_epd_update,
            "epd_rotate_degrees": settings.epd_rotate_degrees,
        },
        "audio": {
            "active_or_preferred_sink": store.get_app_state_value("preferred_output") or "dac",
            "audio_backend": settings.audio_backend,
            "audio_device": settings.audio_device,
            "bluetooth_debug": _env_bool("DEBUG_AUDIO"),
        },
        "playlist": {
            "active_source": source_id,
            "track_index": session.current_track_index if session else None,
            "last_position_seconds": session.last_position_seconds if session else None,
            "is_playing": session.is_playing if session else False,
            "queue_length": len(store.get_source_queue(source_id)) if source_id else 0,
            "current_track": {
                "id": current_track.id,
                "title": current_track.title,
                "file_path": current_track.file_path,
                "completed": current_track.completed,
            }
            if current_track
            else None,
        },
        "gpio": {
            "pin_factory": os.getenv("GPIOZERO_PIN_FACTORY", "default"),
            "button_slots": {
                source_id: str(settings.media_dir / definition.relative_dir)
                for source_id, definition in SOURCE_DEFINITIONS.items()
            },
        },
        "refresh_counters": _refresh_counters(LOG_FILE),
        "log_file": str(LOG_FILE),
    }
    print(json.dumps(snapshot, indent=2, sort_keys=True))


def _file_size(path: Path) -> int | None:
    return path.stat().st_size if path.exists() else None


def _refresh_counters(path: Path) -> dict[str, int | str | None]:
    if not path.exists():
        return {
            "display_updates": 0,
            "periodic_clears": 0,
            "manual_clears": 0,
            "last_display_event": None,
        }
    text = path.read_text(encoding="utf-8", errors="replace")
    lines = text.splitlines()
    display_lines = [
        line
        for line in lines
        if "[DISPLAY]" in line or "[EPD]" in line
    ]
    return {
        "display_updates": text.count("Physical e-paper update finish")
        + text.count("Physical display update duration_ms="),
        "full_updates": text.count("[EPD] Full update"),
        "partial_updates": text.count("[EPD] Partial update"),
        "skipped_unchanged": text.count("Skipped physical e-paper update because image unchanged"),
        "debounced_updates": text.count("Physical e-paper update coalesced/debounced"),
        "periodic_clears": text.count("Performing periodic full e-paper clear"),
        "manual_clears": text.count("Clearing physical e-paper display"),
        "last_display_event": display_lines[-1] if display_lines else None,
    }


def _env_bool(name: str) -> bool:
    value = os.getenv(name)
    return bool(value and value.strip().lower() in {"1", "true", "yes", "on"})


if __name__ == "__main__":
    main()
