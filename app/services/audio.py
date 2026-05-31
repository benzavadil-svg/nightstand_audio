from __future__ import annotations

import re
import shutil
import subprocess
from dataclasses import dataclass

from app.services.logger import get_logger


PREFERRED_DAC_PATTERNS = (
    "bossdac",
    "snd_rpi_hifiberry_dacplus",
    "hifiberry",
    "pcm512",
    "innomaker",
)


@dataclass(frozen=True)
class AudioSelection:
    backend: str
    requested_device: str
    selected_device: str
    hardware_dac_detected: str | None = None
    card_index: int | None = None
    fallback_used: bool = False


class AudioOutputSelector:
    def __init__(self, backend: str = "alsa", requested_device: str = "auto") -> None:
        self.backend = backend.lower()
        self.requested_device = requested_device
        self.log = get_logger("AUDIO")

    def select(self) -> AudioSelection:
        if self.backend != "alsa":
            selection = AudioSelection(
                backend=self.backend,
                requested_device=self.requested_device,
                selected_device=self.requested_device,
            )
            self._log_selection(selection)
            return selection
        if self.requested_device and self.requested_device.lower() != "auto":
            detected = self._detect_preferred_dac()
            selection = AudioSelection(
                backend="alsa",
                requested_device=self.requested_device,
                selected_device=self.requested_device,
                hardware_dac_detected=detected.name,
                card_index=detected.card_index,
            )
            self._log_selection(selection)
            return selection

        detected = self._detect_preferred_dac()
        if detected.name and detected.card_index is not None:
            selection = AudioSelection(
                backend="alsa",
                requested_device=self.requested_device,
                selected_device=f"plughw:{detected.card_index},0",
                hardware_dac_detected=detected.name,
                card_index=detected.card_index,
            )
            self._log_selection(selection)
            return selection

        selection = AudioSelection(
            backend="alsa",
            requested_device=self.requested_device,
            selected_device="default",
            fallback_used=True,
        )
        self._log_selection(selection)
        return selection

    def _detect_preferred_dac(self) -> "_DetectedDac":
        output = read_aplay_cards()
        return detect_preferred_dac(output)

    def _log_selection(self, selection: AudioSelection) -> None:
        self.log.info("Backend: %s", selection.backend)
        if selection.hardware_dac_detected:
            self.log.info("Hardware DAC detected: %s", selection.hardware_dac_detected)
        elif selection.backend == "alsa":
            self.log.warning("Hardware DAC not detected; using fallback audio device.")
        self.log.info("Selected ALSA device: %s", selection.selected_device)


@dataclass(frozen=True)
class _DetectedDac:
    name: str | None = None
    card_index: int | None = None


def read_aplay_cards() -> str:
    if not shutil.which("aplay"):
        return ""
    result = subprocess.run(["aplay", "-l"], text=True, capture_output=True, check=False)
    return "\n".join(part for part in (result.stdout, result.stderr) if part)


def detect_preferred_dac(aplay_output: str) -> _DetectedDac:
    lines = list(aplay_output.splitlines())
    for pattern in PREFERRED_DAC_PATTERNS:
        for line in lines:
            if pattern not in line.lower():
                continue
            match = re.search(r"card\s+(\d+):\s*([^\s\[]+)", line, re.IGNORECASE)
            if match:
                return _DetectedDac(name=match.group(2), card_index=int(match.group(1)))
            card_match = re.search(r"card\s+(\d+):", line, re.IGNORECASE)
            return _DetectedDac(
                name=line.strip(),
                card_index=int(card_match.group(1)) if card_match else None,
            )
    return _DetectedDac()
