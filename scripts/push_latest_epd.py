from __future__ import annotations

import argparse
import os

from app.config import get_settings
from app.display.waveshare_display import WaveshareDisplay


os.environ.setdefault("GPIOZERO_PIN_FACTORY", "lgpio")
os.environ.setdefault("FORCE_EPD_UPDATE", "true")
os.environ.setdefault("EPD_REINIT_EVERY_UPDATE", "true")
os.environ.setdefault("CLEAR_BEFORE_EPD_UPDATE", "false")


def main() -> None:
    parser = argparse.ArgumentParser(description="Push data/latest_screen.png to the Waveshare display.")
    parser.add_argument("--full", action="store_true", help="Force a full display update.")
    parser.add_argument("--partial", action="store_true", help="Request a partial display update.")
    parser.add_argument("--clean", action="store_true", help="Clear before the display update.")
    args = parser.parse_args()

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
        allow_hardware_fallback=settings.hardware_fallback_to_simulator,
    )
    try:
        update_mode = "partial" if args.partial and not args.full else "full"
        display.render_path(
            str(settings.screen_path),
            update_mode=update_mode,
            reason="manual_push",
            clean_refresh=args.clean or args.full,
        )
    finally:
        display.sleep()


if __name__ == "__main__":
    main()
