from __future__ import annotations

from app.display.base import DisplayAdapter
from app.models import RenderState


class EpaperDisplay(DisplayAdapter):
    """Future Waveshare e-paper display adapter.

    Contract:
    - Accept the same RenderState used by the Mac simulator.
    - Render through EInkRenderer or equivalent 1-bit image generation.
    - Push the final 600x448 black/white image to the 5.83 inch Waveshare HAT.

    TODO: Retire this stub once waveshare_display.py is fully validated on the Pi.
    TODO: Keep SPI bus setup and busy/reset/data-command pins isolated here.
    TODO: Add full-refresh-only behavior first, then evaluate partial refresh if the panel supports it.
    TODO: Account for panel rotation/orientation before pushing bytes to hardware.
    """

    def render(self, state: RenderState, reason: str | None = None) -> None:
        raise NotImplementedError("Waveshare e-paper display support is not implemented yet.")
