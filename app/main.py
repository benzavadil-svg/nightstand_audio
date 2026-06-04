from __future__ import annotations

import os

from app.config import get_settings
from app.display.renderer import EInkRenderer
from app.display.gpio_safety import validate_appliance_gpio_config
from app.display.simulator_display import SimulatorDisplay
from app.display.waveshare_display import WaveshareDisplay, display_model_spec
from app.input.composite_input import CompositeInput
from app.input.gpio_input_stub import GPIOInput, GPIOInputUnavailableError
from app.input.keyboard_input import KeyboardInput
from app.media_library import MediaLibrary
from app.playback.factory import build_alarm_playback_adapter, build_playback_adapter
from app.services.audio import AudioOutputSelector
from app.services.controller import NightstandController
from app.services.logger import get_logger, log_startup_banner
from app.services.startup import StartupProfiler
from app.state_store import StateStore


def build_simulator_controller() -> NightstandController:
    profiler = StartupProfiler()
    with profiler.span("config_load"):
        settings = get_settings()
    display_spec = display_model_spec(settings.display_model)
    with profiler.span("audio_device_detection"):
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
    if settings.runtime_mode == "appliance":
        validate_appliance_gpio_config(get_logger("INPUT"))
    if settings.runtime_mode == "appliance":
        with profiler.span("media_cache_load"):
            library.prepare_startup_index()
    else:
        with profiler.span("media_library_scan"):
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
            protect_i2s_gpio=bool(audio_selection.hardware_dac_detected),
        )
        physical_display.startup_profiler = profiler
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
        audio_start_display_grace_ms=settings.audio_start_display_grace_ms,
        suppress_while_audio_playing=settings.epd_suppress_while_audio_playing,
        menu_navigation_update_mode=settings.epd_menu_navigation_update_mode,
        clock_partial_update_enabled=settings.epd_clock_partial_update_enabled,
    )
    display.startup_profiler = profiler
    with profiler.span("playback_service_init"):
        player = build_playback_adapter(settings, audio_selection)
        alarm_player = build_alarm_playback_adapter(settings, player)
        input_adapter = _build_input_adapter(settings.input_backend, settings.runtime_mode)
        controller = NightstandController(
            store=store,
            library=library,
            player=player,
            display=display,
            alarm_player=alarm_player,
            keyboard=input_adapter,
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
            restore_playback_on_startup=settings.restore_playback_on_startup,
            resume_on_startup=settings.resume_on_startup,
            playback_restore_launch=settings.playback_restore_launch,
            validate_playlist_on_play=settings.validate_playlist_on_play,
            sleep_fade_seconds=settings.sleep_fade_seconds,
            sleep_fade_steps=settings.sleep_fade_steps,
            bossdac_audio_device=audio_selection.selected_device,
            bluetooth_auto_reconnect_cooldown_seconds=(
                settings.bluetooth_auto_reconnect_cooldown_seconds
            ),
        )
    controller.startup_profiler = profiler
    if settings.runtime_mode == "appliance" and settings.background_media_scan:
        controller.start_background_media_scan_after_first_render = True
    elif settings.runtime_mode == "appliance":
        get_logger("MEDIA").info("Background scan disabled")
    return controller


def _build_input_adapter(input_backend: str, runtime_mode: str):
    selected = input_backend
    if selected == "auto":
        selected = "gpio" if runtime_mode == "appliance" else "keyboard"
    get_logger("INPUT").info("Input backend: %s", selected)
    if selected == "gpio":
        return GPIOInput()
    if selected == "gpio_keyboard":
        try:
            return CompositeInput(GPIOInput(), KeyboardInput())
        except GPIOInputUnavailableError as exc:
            get_logger("INPUT").warning(
                "GPIO input unavailable; continuing with keyboard input only error=%s",
                exc,
            )
            return KeyboardInput()
    return KeyboardInput()


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
