from __future__ import annotations

import os

from app.config import get_settings
from app.display.renderer import EInkRenderer
from app.display.simulator_display import SimulatorDisplay
from app.display.waveshare_display import WaveshareDisplay, display_model_spec
from app.input.keyboard_input import KeyboardInput
from app.media_library import MediaLibrary
from app.playback.mock_player import MockPlayer
from app.services.audio import AudioOutputSelector
from app.services.controller import NightstandController
from app.services.logger import get_logger, log_startup_banner
from app.state_store import StateStore


def build_simulator_controller() -> NightstandController:
    settings = get_settings()
    display_spec = display_model_spec(settings.display_model)
    audio_selection = AudioOutputSelector(
        settings.audio_backend,
        settings.audio_device,
    ).select()
    log_startup_banner(
        runtime_mode=settings.runtime_mode,
        display_backend=settings.display_backend,
        display_model=settings.display_model,
        display=display_spec.label,
        resolution=f"{settings.display_width}x{settings.display_height}",
        gpio_backend=os.getenv("GPIOZERO_PIN_FACTORY", "default"),
        audio=audio_selection.backend
        if settings.runtime_mode == "appliance"
        else "simulator",
        audio_device=audio_selection.selected_device,
        live_epd=settings.use_real_epd,
    )
    store = StateStore(settings.db_path)
    library = MediaLibrary(settings.media_dir, store)
    library.scan()
    library.ensure_demo_library()
    renderer = EInkRenderer(settings.display_width, settings.display_height)
    physical_display = None
    if settings.use_real_epd or settings.display_backend == "waveshare":
        get_logger("EPD").info("Live e-paper output enabled.")
        physical_display = WaveshareDisplay(
            width=settings.display_width,
            height=settings.display_height,
            display_model=settings.display_model,
            rotate_degrees=settings.epd_rotate_degrees,
            clear_on_exit=settings.clear_epd_on_exit,
            full_clear_interval=settings.epd_full_clear_interval,
            force_update=settings.force_epd_update,
            reinit_every_update=settings.epd_reinit_every_update,
            clear_before_update=settings.clear_before_epd_update,
            partial_update_enabled=settings.epd_partial_update_enabled,
            disable_partial=settings.epd_disable_partial,
            one_shot_major_transitions=settings.epd_one_shot_major_transitions,
            region_partial_enabled=settings.epd_region_partial_enabled,
            allow_hardware_fallback=settings.hardware_fallback_to_simulator,
        )
    else:
        get_logger("DISPLAY").info("Live e-paper output disabled; PNG-only mode.")
    get_logger("DISPLAY").info("Backend: %s", settings.display_backend)
    get_logger("DISPLAY").info("Model: %s", settings.display_model)
    get_logger("DISPLAY").info(
        "Partial policy: %s",
        "enabled" if settings.epd_partial_update_enabled and not settings.epd_disable_partial else "disabled",
    )
    display = SimulatorDisplay(
        renderer,
        settings.screen_path,
        physical_display=physical_display,
        physical_debounce_ms=settings.epd_render_debounce_ms,
        volume_debounce_ms=settings.epd_volume_refresh_debounce_ms,
        refresh_on_volume_change=settings.epd_refresh_on_volume_change,
        full_clear_interval=settings.epd_full_clear_interval,
        partial_update_enabled=(
            settings.epd_partial_update_enabled and not settings.epd_disable_partial
        ),
        partial_min_interval_ms=settings.epd_partial_refresh_min_interval_ms,
        force_full_refresh=settings.epd_force_full_refresh,
        force_clean_refresh=settings.epd_force_clean_refresh,
        one_shot_major_transitions=settings.epd_one_shot_major_transitions,
        region_partial_enabled=settings.epd_region_partial_enabled,
        partial_streak_limit=settings.epd_partial_streak_limit,
    )
    player = MockPlayer()
    keyboard = KeyboardInput()
    return NightstandController(
        store=store,
        library=library,
        player=player,
        display=display,
        keyboard=keyboard,
        menu_timeout_seconds=settings.menu_timeout_seconds,
        clock_refresh_seconds=settings.epd_clock_refresh_seconds,
        disable_clock_auto_refresh=settings.epd_disable_clock_auto_refresh,
        night_mode_enabled=settings.night_mode_enabled,
        night_mode_start=settings.night_mode_start,
        night_mode_end=settings.night_mode_end,
        night_mode_wake_timeout_seconds=settings.night_mode_wake_timeout_seconds,
        night_mode_display_lock=settings.night_mode_display_lock,
        ambient_mode_enabled=settings.ambient_mode_enabled,
        active_mode_timeout_seconds=settings.active_mode_timeout_seconds,
        ambient_clock_refresh_seconds=settings.ambient_clock_refresh_seconds,
        ambient_show_playback_glyph=settings.ambient_show_playback_glyph,
    )


def run_simulator() -> None:
    build_simulator_controller().run()


def render_once() -> None:
    controller = build_simulator_controller()
    try:
        controller.render()
        print(f"Rendered {controller.display.output_path}")
    finally:
        controller.shutdown()


def seed_library() -> None:
    settings = get_settings()
    log = get_logger("STATE")
    store = StateStore(settings.db_path)
    library = MediaLibrary(settings.media_dir, store)
    scanned = library.scan()
    demo = library.ensure_demo_library()
    log.info("Media library seeded scanned=%s demo_created=%s db=%s", scanned, demo, settings.db_path)
    print(f"Scanned {scanned} media files.")
    if demo:
        print(f"Created {demo} demo tracks.")
    print(f"SQLite database: {settings.db_path}")
