from __future__ import annotations

import os


os.environ.setdefault("USE_REAL_EPD", "true")
os.environ.setdefault("GPIOZERO_PIN_FACTORY", "lgpio")
os.environ.setdefault("RUNTIME_MODE", "appliance")
os.environ.setdefault("DISPLAY_BACKEND", "waveshare")
os.environ.setdefault("DISPLAY_MODEL", "waveshare_5in83_v2")
os.environ.setdefault("AUDIO_BACKEND", "alsa")
os.environ.setdefault("AUDIO_DEVICE", "auto")
os.environ.setdefault("PLAYBACK_BACKEND", "mpv")
os.environ.setdefault("HARDWARE_FALLBACK_TO_SIMULATOR", "false")
os.environ.setdefault("FORCE_EPD_UPDATE", "false")
os.environ.setdefault("EPD_REINIT_EVERY_UPDATE", "false")
os.environ.setdefault("CLEAR_BEFORE_EPD_UPDATE", "false")
os.environ.setdefault("EPD_CLOCK_REFRESH_SECONDS", "60")
os.environ.setdefault("EPD_RENDER_DEBOUNCE_MS", "750")
os.environ.setdefault("EPD_VOLUME_REFRESH_DEBOUNCE_MS", "600")
os.environ.setdefault("EPD_REFRESH_ON_VOLUME_CHANGE", "true")
os.environ.setdefault("EPD_FULL_CLEAR_INTERVAL", "50")
os.environ.setdefault("EPD_PARTIAL_UPDATE_ENABLED", "true")
os.environ.setdefault("EPD_DISABLE_PARTIAL", "false")
os.environ.setdefault("EPD_ONE_SHOT_MAJOR_TRANSITIONS", "true")
os.environ.setdefault("EPD_REGION_PARTIAL_ENABLED", "true")
os.environ.setdefault("EPD_PARTIAL_STREAK_LIMIT", "8")
os.environ.setdefault("EPD_PARTIAL_REFRESH_MIN_INTERVAL_MS", "500")
os.environ.setdefault("EPD_FORCE_FULL_REFRESH", "false")
os.environ.setdefault("EPD_FORCE_CLEAN_REFRESH", "false")

from app.main import run_simulator


if __name__ == "__main__":
    run_simulator()
