from __future__ import annotations

import os
import sys
import time
from dataclasses import dataclass
from inspect import Parameter, signature
from importlib import import_module
from pathlib import Path
from types import ModuleType
from typing import Any, Callable

from PIL import Image

from app.display.base import ImageDisplayAdapter
from app.services.logger import get_logger, is_debug_enabled


DEFAULT_DISPLAY_MODEL = "waveshare_5in83_v2"
MODE_FULL = "FULL"
MODE_PARTIAL = "PARTIAL"
PARTIAL_DISPLAY_METHODS = (
    "display_Partial",
    "display_part",
    "DisplayPart",
    "displayPartial",
    "Display_Partial",
)
PARTIAL_INIT_METHODS = (
    "init_Part",
    "init_part",
    "Init_Part",
    "initPartial",
    "init_partial",
)


@dataclass(frozen=True)
class DisplayModelSpec:
    model: str
    driver_name: str
    width: int
    height: int
    label: str


@dataclass(frozen=True)
class PartialApi:
    supported: bool
    display_method_name: str | None = None
    init_method_name: str | None = None
    display_argument_count: int | None = None
    signature_text: str | None = None


DISPLAY_MODELS: dict[str, DisplayModelSpec] = {
    "waveshare_5in83_v2": DisplayModelSpec(
        model="waveshare_5in83_v2",
        driver_name="epd5in83_V2",
        width=600,
        height=448,
        label='Waveshare 5.83" V2',
    ),
    "waveshare_4in2_v2": DisplayModelSpec(
        model="waveshare_4in2_v2",
        driver_name="epd4in2_V2",
        width=400,
        height=300,
        label='Waveshare 4.2" V2',
    ),
}


class WaveshareDisplay(ImageDisplayAdapter):
    """Reusable Waveshare e-paper image adapter for the Raspberry Pi path."""

    def __init__(
        self,
        width: int = 600,
        height: int = 448,
        display_model: str = DEFAULT_DISPLAY_MODEL,
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
        allow_hardware_fallback: bool = True,
    ) -> None:
        self.model_spec = display_model_spec(display_model)
        self.display_model = self.model_spec.model
        self.width = width or self.model_spec.width
        self.height = height or self.model_spec.height
        self.rotate_degrees = rotate_degrees % 360
        self.clear_on_exit = clear_on_exit
        self.full_clear_interval = max(0, full_clear_interval)
        self.force_update = force_update
        self.reinit_every_update = reinit_every_update
        self.clear_before_update = clear_before_update
        self.partial_update_enabled = partial_update_enabled
        self.one_shot_major_transitions = one_shot_major_transitions
        self.region_partial_enabled = region_partial_enabled
        self.allow_hardware_fallback = allow_hardware_fallback
        self.disable_partial = (
            _env_bool("EPD_DISABLE_PARTIAL", False)
            if disable_partial is None
            else disable_partial
        )
        self.driver_name = self.model_spec.driver_name
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
        self._partial_api: PartialApi | None = None
        self._last_full_buffer: Any = None
        self._last_display_buffer: Any = None
        self._full_durations_ms: list[float] = []
        self._partial_durations_ms: list[float] = []
        self.log = get_logger("EPD")
        self.startup_profiler = None

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
            epd_driver = self._epd_module or self._load_driver()
            init_started = time.perf_counter()
            epd = epd_driver.EPD()
            epd.init()
            epd_width = int(getattr(epd, "width", getattr(epd_driver, "EPD_WIDTH", self.width)))
            epd_height = int(getattr(epd, "height", getattr(epd_driver, "EPD_HEIGHT", self.height)))
            self.width = epd_width
            self.height = epd_height
            if self.startup_profiler:
                self.startup_profiler.record(
                    "display_init",
                    (time.perf_counter() - init_started) * 1000,
                )
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
            if not self.allow_hardware_fallback:
                raise
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
            self.log.info(
                "Partial update disabled; using true full display mode selected_policy=partial physical_mode=full_fallback reason=%s",
                reason,
            )
            self.full_update(image, reason=reason, clean_refresh=False)
            return
        if not self._partial_api_supported():
            api = self._partial_api or PartialApi(supported=False)
            self.log.info(
                "Partial update unsupported by driver; using true full display mode selected_policy=partial physical_mode=full_fallback reason=%s partial_supported=%s partial_api=%s init_part_api=%s",
                reason,
                api.supported,
                api.display_method_name or "none",
                api.init_method_name or "none",
            )
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
            self.log.info(
                "Partial update fallback selected_policy=partial physical_mode=full_fallback reason=%s",
                reason,
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
                "%s display write start reason=%s update=%s epd_width=%s epd_height=%s clean_refresh=%s selected_policy=%s physical_mode=%s",
                _update_label(update_mode),
                reason,
                self._render_count,
                self.width,
                self.height,
                clear_before_display,
                update_mode,
                update_mode,
            )
            buffer = self._epd.getbuffer(prepared)
            if update_mode == "partial":
                region_real = False
                region_emulated = bool(region)
                api = self._partial_api or self._discover_partial_api()
                self.log.info(
                    "%s() called selected_policy=partial physical_mode=partial partial_api=%s",
                    api.display_method_name,
                    api.display_method_name,
                )
                if region is not None:
                    self.log.info(
                        "Partial dirty region applied name=%s bounds=%s region_emulated=%s",
                        _region_name(region),
                        _region_bounds(region),
                        region_emulated,
                    )
                self._call_partial_display(buffer)
            else:
                region_real = False
                region_emulated = False
                self.log.info("display() called")
                self._epd.display(buffer)
                self._last_full_buffer = buffer
            duration_ms = (time.perf_counter() - started) * 1000
            self._last_display_buffer = buffer
            if update_mode == "full":
                self.log.info(
                    "True full display write complete reason=%s duration_ms=%.1f",
                    reason,
                    duration_ms,
                )
            self._record_update_duration(update_mode, duration_ms)
            self.log.info(
                "%s display write complete reason=%s duration_ms=%.1f full_count=%s partial_count=%s clear_count=%s avg_full_ms=%.1f avg_partial_ms=%.1f selected_policy=%s physical_mode=%s",
                _update_label(update_mode),
                reason,
                duration_ms,
                self._full_update_count,
                self._partial_update_count,
                self._clear_count,
                _average(self._full_durations_ms),
                _average(self._partial_durations_ms),
                update_mode,
                update_mode,
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
            if update_mode == "partial":
                raise

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
            epd_driver = self._epd_module or self._load_driver()
            self._epd = epd_driver.EPD()
            self.width = int(getattr(self._epd, "width", getattr(epd_driver, "EPD_WIDTH", self.width)))
            self.height = int(getattr(self._epd, "height", getattr(epd_driver, "EPD_HEIGHT", self.height)))
            self._epd.init()
            self._initialized = True
            self._sleeping = False
            self._display_mode = MODE_FULL
            self._partial_supported = None
            self._partial_api = None
            self._partial_api = self._discover_partial_api()
            self._partial_supported = self._partial_api.supported
            init_duration_ms = (time.perf_counter() - started) * 1000
            if self.startup_profiler:
                self.startup_profiler.record("display_init", init_duration_ms)
            self.log.info(
                "Waveshare %s initialized successfully model=%s resolution=%sx%s duration_ms=%.1f",
                self.driver_name,
                self.display_model,
                self.width,
                self.height,
                init_duration_ms,
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
            if not self.allow_hardware_fallback:
                raise RuntimeError(
                    f"Waveshare {self.driver_name} initialization failed and hardware fallback is disabled"
                ) from exc
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
            epd_driver = self._epd_module or self._load_driver()
            self._epd = epd_driver.EPD()
            self.width = int(getattr(self._epd, "width", getattr(epd_driver, "EPD_WIDTH", self.width)))
            self.height = int(getattr(self._epd, "height", getattr(epd_driver, "EPD_HEIGHT", self.height)))
            self._epd.init()
            self._initialized = True
            self._sleeping = False
            self._display_mode = MODE_FULL
            self._partial_supported = None
            self._partial_api = None
            self._partial_api = self._discover_partial_api()
            self._partial_supported = self._partial_api.supported
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
            if not self.allow_hardware_fallback:
                raise RuntimeError(
                    f"Waveshare {self.driver_name} forced reinit failed and hardware fallback is disabled"
                ) from exc
            return False

    def _partial_api_supported(self) -> bool:
        if self._partial_api is None:
            self._partial_api = self._discover_partial_api()
            self._partial_supported = self._partial_api.supported
        return self._partial_supported

    def _discover_partial_api(self) -> PartialApi:
        method_names = available_epd_methods(self._epd)
        self.log.info(
            "Available EPD methods display_model=%s driver=%s methods=%s",
            self.display_model,
            self.driver_name,
            ",".join(method_names),
        )
        display_method_name = _choose_method_name(self._epd, PARTIAL_DISPLAY_METHODS)
        if display_method_name is None:
            display_method_name = _choose_method_name(
                self._epd,
                _partial_display_candidates(method_names),
            )
        init_method_name = _choose_method_name(self._epd, PARTIAL_INIT_METHODS)
        display_argument_count: int | None = None
        signature_text: str | None = None
        if display_method_name:
            method = getattr(self._epd, display_method_name)
            signature_text = _safe_signature_text(method)
            display_argument_count = _supported_partial_argument_count(method)
        api = PartialApi(
            supported=display_method_name is not None and display_argument_count is not None,
            display_method_name=display_method_name,
            init_method_name=init_method_name,
            display_argument_count=display_argument_count,
            signature_text=signature_text,
        )
        self.log.info(
            "Partial refresh support display_model=%s driver=%s partial_supported=%s partial_api=%s init_part_api=%s partial_signature=%s",
            self.display_model,
            self.driver_name,
            api.supported,
            api.display_method_name or "none",
            api.init_method_name or "none",
            api.signature_text or "unknown",
        )
        return api

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
        api = self._partial_api or self._discover_partial_api()
        if not api.supported:
            raise RuntimeError("Partial refresh is unsupported by the selected Waveshare driver")
        previous = self._display_mode or "UNINITIALIZED"
        started = time.perf_counter()
        self.log.info("Switching display mode from %s to PARTIAL reason=%s", previous, reason)
        init_part_called = False
        if api.init_method_name:
            getattr(self._epd, api.init_method_name)()
            init_part_called = True
        self._display_mode = MODE_PARTIAL
        self._sleeping = False
        self.log.info(
            "Display mode PARTIAL ready duration_ms=%.1f partial_supported=%s partial_api=%s init_part_api=%s init_part_called=%s",
            (time.perf_counter() - started) * 1000,
            api.supported,
            api.display_method_name or "none",
            api.init_method_name or "none",
            init_part_called,
        )

    def _call_partial_display(self, buffer: Any) -> None:
        api = self._partial_api or self._discover_partial_api()
        if not api.supported or api.display_method_name is None:
            raise RuntimeError("Partial refresh is unsupported by the selected Waveshare driver")
        method = getattr(self._epd, api.display_method_name)
        if api.display_argument_count == 2:
            base_buffer = self._last_full_buffer or self._last_display_buffer
            if base_buffer is None:
                raise RuntimeError(
                    f"{api.display_method_name} requires a base buffer, but no previous full frame exists"
                )
            method(base_buffer, buffer)
            return
        method(buffer)

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
        started = time.perf_counter()
        self._add_waveshare_paths()
        module = import_module(f"waveshare_epd.{self.driver_name}")
        duration_ms = (time.perf_counter() - started) * 1000
        if self.startup_profiler:
            self.startup_profiler.record("display_driver_import", duration_ms)
        self._epd_module = module
        self.log.info(
            "Using Waveshare e-paper driver module=%s model=%s",
            self.driver_name,
            self.display_model,
        )
        return module

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


def available_epd_methods(epd: object) -> list[str]:
    return sorted(
        name
        for name in dir(epd)
        if not name.startswith("_") and callable(getattr(epd, name, None))
    )


def _choose_method_name(epd: object, candidates: tuple[str, ...]) -> str | None:
    for name in candidates:
        if callable(getattr(epd, name, None)):
            return name
    return None


def _partial_display_candidates(method_names: list[str]) -> tuple[str, ...]:
    candidates = []
    for name in method_names:
        lowered = name.lower()
        if "display" in lowered and "part" in lowered and "init" not in lowered:
            candidates.append(name)
    return tuple(candidates)


def _safe_signature_text(method: Callable[..., Any]) -> str:
    try:
        return str(signature(method))
    except (TypeError, ValueError):
        return "unknown"


def _supported_partial_argument_count(method: Callable[..., Any]) -> int | None:
    try:
        method_signature = signature(method)
    except (TypeError, ValueError):
        return 1
    parameters = [
        parameter
        for parameter in method_signature.parameters.values()
        if parameter.kind
        in (Parameter.POSITIONAL_ONLY, Parameter.POSITIONAL_OR_KEYWORD)
    ]
    has_varargs = any(
        parameter.kind == Parameter.VAR_POSITIONAL
        for parameter in method_signature.parameters.values()
    )
    required_count = sum(
        parameter.default is Parameter.empty
        for parameter in parameters
    )
    max_count = None if has_varargs else len(parameters)
    if required_count <= 1 and (max_count is None or max_count >= 1):
        return 1
    if required_count <= 2 and (max_count is None or max_count >= 2):
        return 2
    return None


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


def display_model_spec(display_model: str | None) -> DisplayModelSpec:
    if display_model and display_model.strip().lower() in DISPLAY_MODELS:
        return DISPLAY_MODELS[display_model.strip().lower()]
    return DISPLAY_MODELS[DEFAULT_DISPLAY_MODEL]
