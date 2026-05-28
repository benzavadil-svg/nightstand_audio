from __future__ import annotations

import logging
import os
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any


LOG_FILE = Path.home() / "nightstand-audio" / "logs" / "nightstand.log"
_CONFIGURED = False


class _SubsystemFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        if not hasattr(record, "subsystem"):
            record.subsystem = "APP"
        return True


class SubsystemLogger(logging.LoggerAdapter):
    def process(self, msg: str, kwargs: dict[str, Any]) -> tuple[str, dict[str, Any]]:
        extra = dict(kwargs.get("extra", {}))
        extra.setdefault("subsystem", self.extra["subsystem"])
        kwargs["extra"] = extra
        return msg, kwargs


def configure_logging() -> None:
    global _CONFIGURED
    if _CONFIGURED:
        return

    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    root = logging.getLogger("nightstand")
    root.setLevel(logging.DEBUG)
    root.propagate = False
    root.handlers.clear()

    formatter = logging.Formatter(
        fmt="%(asctime)s %(levelname)s [%(subsystem)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    subsystem_filter = _SubsystemFilter()

    console = logging.StreamHandler()
    console.setLevel(logging.DEBUG)
    console.setFormatter(formatter)
    console.addFilter(subsystem_filter)
    root.addHandler(console)

    file_handler = RotatingFileHandler(
        LOG_FILE,
        maxBytes=1_048_576,
        backupCount=5,
        encoding="utf-8",
    )
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(formatter)
    file_handler.addFilter(subsystem_filter)
    root.addHandler(file_handler)

    _CONFIGURED = True


def get_logger(subsystem: str) -> SubsystemLogger:
    configure_logging()
    normalized = subsystem.upper()
    logger = logging.getLogger(f"nightstand.{normalized}")
    logger.setLevel(_level_for_subsystem(normalized))
    return SubsystemLogger(logger, {"subsystem": normalized})


def is_debug_enabled(subsystem: str | None = None) -> bool:
    if _env_bool("LOG_LEVEL") == "debug":
        return True
    if subsystem:
        return _env_bool(f"DEBUG_{subsystem.upper()}")
    return False


def log_startup_banner(
    *,
    display: str,
    resolution: str,
    gpio_backend: str,
    audio: str,
    live_epd: bool,
) -> None:
    get_logger("SIM").info(
        "Nightstand Audio | Display: %s | Resolution: %s | GPIO backend: %s | "
        "Audio: %s | Live EPD: %s",
        display,
        resolution,
        gpio_backend,
        audio,
        "enabled" if live_epd else "disabled",
    )


def _level_for_subsystem(subsystem: str) -> int:
    global_level = os.getenv("LOG_LEVEL", "INFO").strip().upper()
    if global_level == "DEBUG" or _env_bool(f"DEBUG_{subsystem}"):
        return logging.DEBUG
    return getattr(logging, global_level, logging.INFO)


def _env_bool(name: str) -> bool:
    value = os.getenv(name)
    return bool(value and value.strip().lower() in {"1", "true", "yes", "on", "debug"})
