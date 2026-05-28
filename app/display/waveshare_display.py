from __future__ import annotations

import os
import sys
import time
from pathlib import Path
from types import ModuleType

from PIL import Image

from app.display.base import ImageDisplayAdapter
from app.services.logger import get_logger, is_debug_enabled


EPD_DRIVER_NAME = "epd5in83_V2"
MODE_FULL = "FULL"
MODE_PARTIAL = "PARTIAL"


class WaveshareDisplay(ImageDisplayAdapter):
    """Reusable Waveshare e-paper image adapter for the Raspberry Pi path."""

    def __init__(
        self,
        width: int = 600,
        height: int = 448,
        rotate_degrees: int = 0,
        clear_on_exit: bool = False,
        full_clear_interval: int = 50,
        force_update: bool = True,
        reinit_every_update: bool = False,
        clear_before_update: bool = False,
        partial_update_enabled: bool = True,
        disable_partial: bool | None = None,
        one_shot_major_transitions: bool = True,
        region_partial_enabled: bool = True,
    ) -> None:
        self.width = width
        self.height = height
        self.rotate_degrees = rotate_degrees % 360
        self.clear_on_exit = clear_on_exit
        self.full_clear_interval = max(0, full_clear_interval)
        self.force_update = force_update
        self.reinit_every_update = reinit_every_update
        self.clear_before_update = clear_before_update
        self.partial_update_enabled = partial_update_enabled
        self.one_shot_major_transitions = one_shot_major_transitions
        self.region_partial_enabled = region_partial_enabled
        self.disable_partial = (
            _env_bool("EPD_DISABLE_PARTIAL", False)
            if disable_partial is None
            else disable_partial
        )
        self.driver_name = EPD_DRIVER_NAME
        self._epd_module: ModuleType | None = None
        self._epd = None
        self._initialized = False
        self._failed = False
        self._sleeping = False
        self._display_mode: str | None = None
        self._render_count = 0
        self._clear_count = 0
        self._full_update_count = 0
        self._partial_update_count = 0
        self._partial_supported: bool | None = None
        self._full_durations_ms: list[float] = []
        self._partial_durations_ms: list[float] = []
        self.log = get_logger("EPD")

    def render(self, image: Image.Image) -> None:
        if not self._ensure_ready_for_render():
            return
        prepared = self._prepare_image(image)
        self._display_prepared_image(prepared)

    def render_path(
        self,
        path: str,
        update_mode: str = "full",
        reason: str | None = None,
        clean_refresh: bool = False,
        region=None,
    ) -> bool:
        if not self._ensure_ready_for_render():
            return False
        image_path = Path(path)
        try:
            stat = image_path.stat()
            self.log.info(
                "Opening latest screen image path=%s mtime=%.6f size_bytes=%s",
                image_path,
                stat.st_mtime,
                stat.st_size,
            )
            with Image.open(image_path) as image:
                prepared = image.convert("1")
                epd_width = int(getattr(self._epd, "width", self.width))
                epd_height = int(getattr(self._epd, "height", self.height))
                self.width = epd_width
                self.height = epd_height
                prepared = prepared.resize((epd_width, epd_height), Image.Resampling.NEAREST)
                self.log.info(
                    "Prepared latest screen image source_size=%s epd_size=%sx%s force_update=%s",
                    image.size,
                    epd_width,
                    epd_height,
                    self.force_update,
                )
                if clean_refresh:
                    update_mode = "full"
                if update_mode == "partial":
                    self.partial_update(prepared, region=region, reason=reason)
                else:
                    self.full_update(prepared, reason=reason, clean_refresh=clean_refresh)
                return True
        except Exception as exc:
            self.log.error(
                "Waveshare display render_path failed: %s",
                exc,
                exc_info=is_debug_enabled("EPD"),
            )
            return False

    def one_shot_render_path(
        self,
        path: str,
        reason: str | None = None,
        displayed_hash: str | None = None,
    ) -> bool:
        image_path = Path(path)
        started = time.perf_counter()
        self.log.info(
            "One-shot major transition display start reason=%s displayed_hash=%s one_shot_major_transition=true",
            reason,
            displayed_hash,
        )
        epd = None
        try:
            if os.getenv("GPIOZERO_PIN_FACTORY") != "lgpio":
                self.log.warning(
                    "GPIOZERO_PIN_FACTORY=lgpio is recommended for live e-paper output."
                )
            epd5in83 = self._epd_module or self._load_driver()
            epd = epd5in83.EPD()
            epd.init()
            epd_width = int(getattr(epd, "width", getattr(epd5in83, "EPD_WIDTH", self.width)))
            epd_height = int(getattr(epd, "height", getattr(epd5in83, "EPD_HEIGHT", self.height)))
            self.width = epd_width
            self.height = epd_height
            stat = image_path.stat()
            self.log.info(
                "Opening latest screen image path=%s mtime=%.6f size_bytes=%s",
                image_path,
                stat.st_mtime,
                stat.st_size,
            )
            with Image.open(image_path) as image:
                prepared = image.convert("1")
                prepared = prepared.resize((epd_width, epd_height), Image.Resampling.NEAREST)
                self.log.info(
                    "Prepared one-shot image source_size=%s epd_size=%sx%s",
                    image.size,
                    epd_width,
                    epd_height,
                )
                epd.display(epd.getbuffer(prepared))
            epd.sleep()
            self._epd = epd
            self._initialized = True
            self._sleeping = True
            self._display_mode = MODE_FULL
            self._render_count += 1
            self._full_update_count += 1
            duration_ms = (time.perf_counter() - started) * 1000
            self._full_durations_ms.append(duration_ms)
            if len(self._full_durations_ms) > 20:
                self._full_durations_ms = self._full_durations_ms[-20:]
            self.log.info(
                "One-shot major transition display complete reason=%s duration_ms=%.1f displayed_hash=%s",
                reason,
                duration_ms,
                displayed_hash,
            )
            return True
        except Exception as exc:
            self.log.error(
                "One-shot major transition display failed reason=%s error=%s",
                reason,
                exc,
                exc_info=is_debug_enabled("EPD"),
            )
            try:
                if epd is not None:
                    epd.sleep()
            except Exception:
                pass
            return False

    def full_update(
        self,
        image: Image.Image,
        reason: str | None = None,
        clean_refresh: bool = False,
    ) -> None:
        self._display_prepared_image(
            image,
            update_mode="full",
            reason=reason,
            clean_refresh=clean_refresh,
        )

    def partial_update(self, image: Image.Image, region=None, reason: str | None = None) -> None:
        if self.disable_partial or not self.partial_update_enabled:
            self.log.info("Partial update disabled; using true full display mode reason=%s", reason)
            self.full_update(image, reason=reason, clean_refresh=False)
            return
        if not self._partial_api_supported():
            self.log.info("Partial update unsupported by driver; using true full display mode reason=%s", reason)
            self.full_update(image, reason=reason, clean_refresh=False)
            return
        try:
            if region is not None:
                self.log.info(
                    "Partial update dirty region name=%s bounds=%s region_partial_enabled=%s",
                    _region_name(region),
                    _region_bounds(region),
                    self.region_partial_enabled,
                )
            self._display_prepared_image(image, update_mode="partial", reason=reason, region=region)
        except Exception as exc:
            self.log.error(
                "Partial update failed, falling back to true full update reason=%s error=%s",
                reason,
                exc,
                exc_info=is_debug_enabled("EPD"),
            )
            self.full_update(image, reason=reason, clean_refresh=False)

    def _display_prepared_image(
        self,
        prepared: Image.Image,
        update_mode: str = "full",
        reason: str | None = None,
        clean_refresh: bool = False,
        region=None,
    ) -> None:
        try:
            self._render_count += 1
            switched_to_full = False
            if update_mode == "partial":
                self._switch_to_partial_mode(reason=reason)
            else:
                switched_to_full = self._switch_to_full_mode(reason=reason)
            clear_before_display = clean_refresh or self.clear_before_update or switched_to_full
            if clear_before_display:
                self._true_full_clear()
            elif (
                update_mode == "full"
                and self.full_clear_interval
                and self._render_count % self.full_clear_interval == 0
            ):
                self.log.info("Performing periodic full e-paper clear to reduce ghosting...")
                self.clear(sleep_after=False)
            started = time.perf_counter()
            if update_mode == "full":
                self.log.info("True full display write start reason=%s", reason)
            self.log.info(
                "%s display write start reason=%s update=%s epd_width=%s epd_height=%s clean_refresh=%s",
                _update_label(update_mode),
                reason,
                self._render_count,
                self.width,
                self.height,
                clear_before_display,
            )
            buffer = self._epd.getbuffer(prepared)
            if update_mode == "partial":
                region_real = False
                region_emulated = bool(region)
                self.log.info("display_Partial() called")
                if region is not None:
                    self.log.info(
                        "Partial dirty region applied name=%s bounds=%s region_emulated=%s",
                        _region_name(region),
                        _region_bounds(region),
                        region_emulated,
                    )
                self._epd.display_Partial(buffer)
            else:
                region_real = False
                region_emulated = False
                self.log.info("display() called")
                self._epd.display(buffer)
            duration_ms = (time.perf_counter() - started) * 1000
            if update_mode == "full":
                self.log.info(
                    "True full display write complete reason=%s duration_ms=%.1f",
                    reason,
                    duration_ms,
                )
            self._record_update_duration(update_mode, duration_ms)
            self.log.info(
                "%s display write complete reason=%s duration_ms=%.1f full_count=%s partial_count=%s clear_count=%s avg_full_ms=%.1f avg_partial_ms=%.1f",
                _update_label(update_mode),
                reason,
                duration_ms,
                self._full_update_count,
                self._partial_update_count,
                self._clear_count,
                _average(self._full_durations_ms),
                _average(self._partial_durations_ms),
            )
            if update_mode == "partial":
                self.log.info(
                    "Partial region result real=%s emulated=%s name=%s bounds=%s",
                    region_real,
                    region_emulated,
                    _region_name(region),
                    _region_bounds(region),
                )
            self.log.debug("BUSY wait duration is handled inside Waveshare %s driver.", self.driver_name)
            self._sleeping = False
        except Exception as exc:
            self.log.error(
                "Waveshare display render failed: %s",
                exc,
                exc_info=is_debug_enabled("EPD"),
            )

    def clear(self, sleep_after: bool = False) -> None:
        if not self._ensure_initialized():
            return
        try:
            self._switch_to_full_mode(reason="clear")
            self._true_full_clear()
        except AttributeError:
            try:
                self._switch_to_full_mode(reason="clear")
                started = time.perf_counter()
                self.log.info("True full clear start")
                self._epd.clear()
                self._sleeping = False
                self._clear_count += 1
                self.log.info(
                    "True full clear complete duration_ms=%.1f count=%s",
                    (time.perf_counter() - started) * 1000,
                    self._clear_count,
                )
            except Exception as exc:
                self.log.error(
                    "Waveshare display clear failed: %s",
                    exc,
                    exc_info=is_debug_enabled("EPD"),
                )
                return
        except Exception as exc:
            self.log.error(
                "Waveshare display clear failed: %s",
                exc,
                exc_info=is_debug_enabled("EPD"),
            )
            return
        if sleep_after:
            self.sleep()

    def _true_full_clear(self) -> None:
        started = time.perf_counter()
        self.log.info("True full clear start")
        self._epd.Clear()
        self._sleeping = False
        self._clear_count += 1
        self.log.info(
            "True full clear complete duration_ms=%.1f count=%s",
            (time.perf_counter() - started) * 1000,
            self._clear_count,
        )

    def sleep(self) -> None:
        if not self._initialized or self._sleeping:
            return
        try:
            started = time.perf_counter()
            self._epd.sleep()
            self._sleeping = True
            self.log.info(
                "Display sleeping; last image remains visible by design. duration_ms=%.1f",
                (time.perf_counter() - started) * 1000,
            )
        except Exception as exc:
            self.log.error(
                "Waveshare display sleep failed: %s",
                exc,
                exc_info=is_debug_enabled("EPD"),
            )

    def shutdown(self) -> None:
        if self.clear_on_exit:
            self.clear(sleep_after=False)
        self.sleep()

    def _ensure_initialized(self) -> bool:
        if self._initialized:
            return True
        if self._failed:
            return False
        try:
            if os.getenv("GPIOZERO_PIN_FACTORY") != "lgpio":
                self.log.warning(
                    "GPIOZERO_PIN_FACTORY=lgpio is recommended for live e-paper output."
                )
            started = time.perf_counter()
            epd5in83 = self._load_driver()
            self._epd = epd5in83.EPD()
            self.width = int(getattr(self._epd, "width", getattr(epd5in83, "EPD_WIDTH", self.width)))
            self.height = int(getattr(self._epd, "height", getattr(epd5in83, "EPD_HEIGHT", self.height)))
            self._epd.init()
            self._initialized = True
            self._sleeping = False
            self._display_mode = MODE_FULL
            self.log.info(
                "Waveshare %s initialized successfully resolution=%sx%s duration_ms=%.1f",
                self.driver_name,
                self.width,
                self.height,
                (time.perf_counter() - started) * 1000,
            )
            return True
        except Exception as exc:
            self._failed = True
            self.log.error(
                "Waveshare %s initialization failed: %s",
                self.driver_name,
                exc,
                exc_info=is_debug_enabled("EPD"),
            )
            self.log.warning("Continuing with PNG-only simulator output.")
            return False

    def _ensure_ready_for_render(self) -> bool:
        if self.reinit_every_update:
            return self._reinit_for_forced_update()
        return self._ensure_initialized()

    def _reinit_for_forced_update(self) -> bool:
        if not self.reinit_every_update:
            return self._initialized
        if self._failed:
            return False
        try:
            started = time.perf_counter()
            if os.getenv("GPIOZERO_PIN_FACTORY") != "lgpio":
                self.log.warning(
                    "GPIOZERO_PIN_FACTORY=lgpio is recommended for live e-paper output."
                )
            self.log.info(
                "Force update reinitializing %s before display write...",
                self.driver_name,
            )
            epd5in83 = self._epd_module or self._load_driver()
            self._epd = epd5in83.EPD()
            self.width = int(getattr(self._epd, "width", getattr(epd5in83, "EPD_WIDTH", self.width)))
            self.height = int(getattr(self._epd, "height", getattr(epd5in83, "EPD_HEIGHT", self.height)))
            self._epd.init()
            self._initialized = True
            self._sleeping = False
            self._display_mode = MODE_FULL
            self.log.info(
                "Force update reinit complete driver=%s epd_width=%s epd_height=%s duration_ms=%.1f",
                self.driver_name,
                self.width,
                self.height,
                (time.perf_counter() - started) * 1000,
            )
            return True
        except Exception as exc:
            self._failed = True
            self.log.error(
                "Waveshare %s forced reinit failed: %s",
                self.driver_name,
                exc,
                exc_info=is_debug_enabled("EPD"),
            )
            self.log.warning("Continuing with PNG-only simulator output.")
            return False

    def _partial_api_supported(self) -> bool:
        if self._partial_supported is None:
            self._partial_supported = all(
                hasattr(self._epd, name) for name in ("display_Partial", "init_Part")
            )
            self.log.info(
                "Partial refresh support driver=%s supported=%s",
                self.driver_name,
                self._partial_supported,
            )
        return self._partial_supported

    def _switch_to_full_mode(self, reason: str | None = None) -> bool:
        if self._display_mode == MODE_FULL and not self._sleeping:
            return False
        previous = "FULL_SLEEPING" if self._display_mode == MODE_FULL else (self._display_mode or "UNINITIALIZED")
        started = time.perf_counter()
        self.log.info("Switching display mode from %s to FULL reason=%s", previous, reason)
        self._epd.init()
        self._display_mode = MODE_FULL
        self._sleeping = False
        self.log.info(
            "Display mode FULL ready duration_ms=%.1f",
            (time.perf_counter() - started) * 1000,
        )
        return True

    def _switch_to_partial_mode(self, reason: str | None = None) -> None:
        if self.disable_partial:
            raise RuntimeError("Partial refresh is disabled by EPD_DISABLE_PARTIAL")
        if self._display_mode == MODE_PARTIAL:
            return
        previous = self._display_mode or "UNINITIALIZED"
        started = time.perf_counter()
        self.log.info("Switching display mode from %s to PARTIAL reason=%s", previous, reason)
        self._epd.init_Part()
        self._display_mode = MODE_PARTIAL
        self._sleeping = False
        self.log.info(
            "Display mode PARTIAL ready duration_ms=%.1f",
            (time.perf_counter() - started) * 1000,
        )

    def _record_update_duration(self, update_mode: str, duration_ms: float) -> None:
        if update_mode == "full":
            self._full_update_count += 1
            self._full_durations_ms.append(duration_ms)
            if len(self._full_durations_ms) > 20:
                self._full_durations_ms = self._full_durations_ms[-20:]
            return
        self._partial_update_count += 1
        self._partial_durations_ms.append(duration_ms)
        if len(self._partial_durations_ms) > 20:
            self._partial_durations_ms = self._partial_durations_ms[-20:]

    def _load_driver(self) -> ModuleType:
        self._add_waveshare_paths()
        from waveshare_epd import epd5in83_V2 as epd5in83

        self._epd_module = epd5in83
        self.log.info("Using Waveshare e-paper driver module=%s", self.driver_name)
        return epd5in83

    def _add_waveshare_paths(self) -> None:
        candidates = [
            os.getenv("WAVESHARE_EPD_PYTHON_PATH", ""),
            str(Path.home() / "e-Paper" / "RaspberryPi_JetsonNano" / "python"),
            str(Path.home() / "e-Paper" / "RaspberryPi_JetsonNano" / "python" / "lib"),
        ]
        for candidate in candidates:
            if candidate and Path(candidate).exists() and candidate not in sys.path:
                sys.path.append(candidate)

    def _prepare_image(self, image: Image.Image) -> Image.Image:
        prepared = image
        if self.rotate_degrees:
            prepared = prepared.rotate(self.rotate_degrees, expand=True)
        elif prepared.size == (self.height, self.width):
            prepared = prepared.rotate(90, expand=True)

        if prepared.size != (self.width, self.height):
            prepared = prepared.resize((self.width, self.height), Image.Resampling.NEAREST)

        return prepared.convert("1", dither=Image.Dither.NONE)


def _update_label(update_mode: str) -> str:
    if update_mode == "partial":
        return "Partial"
    return "Full"


def _average(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def _region_name(region) -> str:
    if isinstance(region, dict):
        return str(region.get("name", "unknown"))
    if isinstance(region, tuple) and len(region) == 2:
        return str(region[0])
    return "none" if region is None else "unknown"


def _region_bounds(region):
    if isinstance(region, dict):
        return region.get("bounds")
    if isinstance(region, tuple) and len(region) == 2:
        return region[1]
    return None


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}
