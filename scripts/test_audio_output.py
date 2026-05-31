from __future__ import annotations

import argparse
import math
import os
import shutil
import struct
import subprocess
import tempfile
import wave
from pathlib import Path

from app.config import get_settings


def main() -> None:
    parser = argparse.ArgumentParser(
        description="List ALSA outputs and play a short test tone through the configured device."
    )
    parser.add_argument("--device", default=None, help="ALSA device, e.g. default, hw:0,0, hw:1,0")
    parser.add_argument("--backend", default=None, help="Audio backend. Currently supports alsa.")
    parser.add_argument("--wav", type=Path, default=None, help="Optional WAV file to play instead of tone.")
    parser.add_argument("--duration", type=float, default=1.0, help="Tone duration in seconds.")
    parser.add_argument("--frequency", type=float, default=440.0, help="Tone frequency in Hz.")
    parser.add_argument("--list-only", action="store_true", help="Only list devices; do not play audio.")
    args = parser.parse_args()

    settings = get_settings()
    backend = (args.backend or os.getenv("AUDIO_BACKEND") or settings.audio_backend).lower()
    device = args.device or os.getenv("AUDIO_DEVICE") or settings.audio_device

    print("Nightstand Audio Output Test")
    print(f"Backend: {backend}")
    print(f"Selected device: {device}")
    print()
    _list_alsa_devices()

    if args.list_only:
        return
    if backend != "alsa":
        raise SystemExit(f"Unsupported AUDIO_BACKEND={backend!r}; currently supported: alsa")
    if not shutil.which("aplay"):
        raise SystemExit("aplay not found. Install alsa-utils: sudo apt install -y alsa-utils")

    wav_path = args.wav if args.wav else _write_test_tone(args.duration, args.frequency)
    try:
        _play_alsa(wav_path, device)
    finally:
        if args.wav is None:
            try:
                wav_path.unlink()
            except OSError:
                pass


def _list_alsa_devices() -> None:
    if not shutil.which("aplay"):
        print("aplay not found; cannot list ALSA devices.")
        print()
        return
    for command in (["aplay", "-l"], ["aplay", "-L"]):
        print("$ " + " ".join(command))
        result = subprocess.run(command, text=True, capture_output=True, check=False)
        output = result.stdout.strip() or result.stderr.strip()
        print(output or "(no output)")
        print()


def _write_test_tone(duration_seconds: float, frequency: float) -> Path:
    sample_rate = 44_100
    amplitude = 0.25
    frame_count = max(1, int(sample_rate * max(0.1, duration_seconds)))
    handle = tempfile.NamedTemporaryFile(prefix="nightstand-test-tone-", suffix=".wav", delete=False)
    path = Path(handle.name)
    handle.close()
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)
        for frame in range(frame_count):
            sample = amplitude * math.sin(2 * math.pi * frequency * frame / sample_rate)
            wav.writeframes(struct.pack("<h", int(sample * 32767)))
    return path


def _play_alsa(path: Path, device: str) -> None:
    command = ["aplay", "-D", device, str(path)]
    print("$ " + " ".join(command))
    result = subprocess.run(command, text=True, capture_output=True, check=False)
    if result.stdout:
        print(result.stdout.strip())
    if result.stderr:
        print(result.stderr.strip())
    if result.returncode:
        raise SystemExit(result.returncode)


if __name__ == "__main__":
    main()
