from __future__ import annotations

import os


os.environ.setdefault("USE_REAL_EPD", "true")
os.environ.setdefault("GPIOZERO_PIN_FACTORY", "lgpio")
os.environ.setdefault("RUNTIME_MODE", "appliance")
os.environ.setdefault("DISPLAY_BACKEND", "waveshare")
os.environ.setdefault("DISPLAY_MODEL", "waveshare_4in2_v2")
os.environ.setdefault("AUDIO_BACKEND", "alsa")
os.environ.setdefault("AUDIO_DEVICE", "auto")
os.environ.setdefault("ALARM_AUDIO_DEVICE", "auto_usb")
os.environ.setdefault("INPUT_BACKEND", "gpio_keyboard")
os.environ.setdefault("PLAYBACK_BACKEND", "mpv")
os.environ.setdefault("HARDWARE_FALLBACK_TO_SIMULATOR", "false")
os.environ.setdefault("BACKGROUND_MEDIA_SCAN", "false")
os.environ.setdefault("VALIDATE_PLAYLIST_ON_PLAY", "false")
os.environ.setdefault("PLAYBACK_RESTORE_LAUNCH", "false")
os.environ.setdefault("RESUME_ON_STARTUP", "false")
os.environ.setdefault("EPD_SUPPRESS_WHILE_AUDIO_PLAYING", "false")
os.environ.setdefault("AUDIO_START_DISPLAY_GRACE_MS", "5000")
os.environ.setdefault("EPD_MENU_NAVIGATION_UPDATE_MODE", "full")

from app.main import run_simulator


if __name__ == "__main__":
    run_simulator()
