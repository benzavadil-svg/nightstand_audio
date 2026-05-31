from __future__ import annotations

import argparse
from inspect import signature

from app.config import get_settings
from app.display.waveshare_display import (
    PARTIAL_DISPLAY_METHODS,
    PARTIAL_INIT_METHODS,
    WaveshareDisplay,
    available_epd_methods,
    display_model_spec,
)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Inspect the installed Waveshare EPD Python driver methods."
    )
    parser.add_argument(
        "--model",
        default=None,
        help="Display model, for example waveshare_4in2_v2 or waveshare_5in83_v2.",
    )
    args = parser.parse_args()

    settings = get_settings()
    model = args.model or settings.display_model
    spec = display_model_spec(model)
    display = WaveshareDisplay(
        width=spec.width,
        height=spec.height,
        display_model=spec.model,
        allow_hardware_fallback=True,
    )

    print(f"display_model={spec.model}")
    print(f"driver={spec.driver_name}")
    try:
        module = display._load_driver()
        epd = module.EPD()
    except Exception as exc:
        print(f"error=failed_to_load_driver detail={exc}")
        raise SystemExit(1) from exc

    print(f"module={getattr(module, '__name__', spec.driver_name)}")
    print(f"module_file={getattr(module, '__file__', 'unknown')}")
    print(f"width={getattr(epd, 'width', getattr(module, 'EPD_WIDTH', 'unknown'))}")
    print(f"height={getattr(epd, 'height', getattr(module, 'EPD_HEIGHT', 'unknown'))}")

    methods = available_epd_methods(epd)
    print("methods:")
    for method_name in methods:
        marker = ""
        if method_name in PARTIAL_INIT_METHODS:
            marker = " [partial-init-candidate]"
        elif method_name in PARTIAL_DISPLAY_METHODS or _looks_like_partial_display(method_name):
            marker = " [partial-display-candidate]"
        print(f"  - {method_name}{marker}{_signature_suffix(getattr(epd, method_name))}")


def _looks_like_partial_display(method_name: str) -> bool:
    lowered = method_name.lower()
    return "display" in lowered and "part" in lowered and "init" not in lowered


def _signature_suffix(method) -> str:
    try:
        return f" {signature(method)}"
    except (TypeError, ValueError):
        return ""


if __name__ == "__main__":
    main()
