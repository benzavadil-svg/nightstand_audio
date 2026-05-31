from __future__ import annotations

import os

from app.config import get_settings
from app.display.waveshare_display import WaveshareDisplay


os.environ.setdefault("GPIOZERO_PIN_FACTORY", "lgpio")


def main() -> None:
    settings = get_settings()
    display = WaveshareDisplay(
        width=settings.display_width,
        height=settings.display_height,
        display_model=settings.display_model,
        rotate_degrees=settings.epd_rotate_degrees,
        clear_on_exit=False,
        full_clear_interval=0,
        force_update=settings.force_epd_update,
        reinit_every_update=settings.epd_reinit_every_update,
        clear_before_update=settings.clear_before_epd_update,
        partial_update_enabled=settings.epd_partial_update_enabled,
        disable_partial=settings.epd_disable_partial,
        region_partial_enabled=settings.epd_region_partial_enabled,
    )
    display.clear(sleep_after=True)


if __name__ == "__main__":
    main()
