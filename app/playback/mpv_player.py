from __future__ import annotations

import json
import os
import shlex
import signal
import socket
import subprocess
import tempfile
import time
from pathlib import Path

from app.models import MediaItem, PlaybackState, PlaybackStatus
from app.playback.base import PlaybackAdapter
from app.services.logger import get_logger


class MPVPlayer(PlaybackAdapter):
    """MPV-backed playback adapter for Raspberry Pi appliance mode."""

    def __init__(self, audio_device: str, executable: str = "mpv") -> None:
        self.audio_device = audio_device
        self.executable = executable
        self._status = PlaybackStatus()
        self._process: subprocess.Popen | None = None
        self._ipc_socket_path: Path | None = None
        self._started_monotonic: float | None = None
        self._base_position = 0.0
        self._stderr_logged = False
        self.log = get_logger("PLAYBACK")
        self.log.info("backend=mpv")
        self.log.info("device=%s", self.audio_device)

    def play(self, item: MediaItem, start_position_seconds: float = 0) -> None:
        self.stop()
        resolved_path = str(Path(item.file_path).expanduser().resolve(strict=False))
        exists = Path(resolved_path).exists()
        self.log.info("file=%s", resolved_path)
        self.log.info("exists=%s", str(exists).lower())
        if not exists:
            self._status = PlaybackStatus(volume=self._status.volume)
            self.log.error("Playback rejected missing file=%s exists=false", resolved_path)
            return
        self._base_position = max(0, float(start_position_seconds))
        self._started_monotonic = time.monotonic()
        self._ipc_socket_path = _ipc_socket_path()
        command = self._build_command(resolved_path, self._base_position, self._ipc_socket_path)
        self.log.info("backend=mpv")
        self.log.info("device=%s", self.audio_device)
        self.log.info("command=%s", shlex.join(command))
        self._status = PlaybackStatus(
            state=PlaybackState.PLAYING,
            source_id=item.source_id,
            item_id=item.id,
            title=item.title,
            subtitle=item.artist or "",
            position_seconds=self._base_position,
            duration_seconds=item.duration_seconds,
            volume=self._status.volume,
        )
        try:
            self._process = subprocess.Popen(
                command,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                text=True,
            )
            self._stderr_logged = False
        except FileNotFoundError:
            self._process = None
            self._started_monotonic = None
            self._status.state = PlaybackState.STOPPED
            self.log.error("mpv executable not found command=%s", self.executable)
            return
        except OSError as exc:
            self._process = None
            self._started_monotonic = None
            self._status.state = PlaybackState.STOPPED
            self.log.error("mpv launch failed file=%s error=%s", resolved_path, exc)
            return
        self.log.info("pid=%s", self._process.pid)
        self.log.info(
            "Track start source=%s item_id=%s title=%s position=%.1fs",
            item.source_id,
            item.id,
            item.title,
            self._base_position,
        )

    def pause(self) -> None:
        self.tick()
        if self._status.state != PlaybackState.PLAYING:
            return
        self._base_position = self._status.position_seconds
        self._started_monotonic = None
        if not self._send_mpv_command(["set_property", "pause", True]):
            self._signal_process(signal.SIGSTOP)
        self._status.state = PlaybackState.PAUSED
        self.log.info(
            "Track paused source=%s item_id=%s position=%.1fs",
            self._status.source_id,
            self._status.item_id,
            self._status.position_seconds,
        )

    def resume(self) -> None:
        if self._status.state != PlaybackState.PAUSED:
            return
        if not self._send_mpv_command(["set_property", "pause", False]):
            self._signal_process(signal.SIGCONT)
        self._started_monotonic = time.monotonic()
        self._status.state = PlaybackState.PLAYING
        self.log.info(
            "Track resume source=%s item_id=%s position=%.1fs",
            self._status.source_id,
            self._status.item_id,
            self._status.position_seconds,
        )

    def stop(self) -> None:
        process = self._process
        if self._status.state != PlaybackState.STOPPED:
            self.tick()
            self.log.info(
                "Track stop source=%s item_id=%s position=%.1fs",
                self._status.source_id,
                self._status.item_id,
                self._status.position_seconds,
            )
        if process and process.poll() is None:
            if not self._send_mpv_command(["quit"]):
                process.terminate()
            try:
                process.wait(timeout=1.0)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=1.0)
        self._cleanup_ipc_socket()
        volume = self._status.volume
        self._status = PlaybackStatus(volume=volume)
        self._base_position = 0
        self._started_monotonic = None
        self._process = None
        self._stderr_logged = False

    def toggle_play_pause(self) -> None:
        if self._status.state == PlaybackState.PLAYING:
            self.pause()
        elif self._status.state == PlaybackState.PAUSED:
            self.resume()

    def set_volume(self, volume: int) -> None:
        self._status.volume = max(0, min(100, int(volume)))
        self._send_mpv_command(["set_property", "volume", self._status.volume])
        self.log.debug("Volume set value=%s", self._status.volume)

    def adjust_volume(self, delta: int) -> None:
        self.set_volume(self._status.volume + int(delta))

    def status(self) -> PlaybackStatus:
        self.tick()
        return self._status

    def tick(self) -> None:
        if self._process and self._process.poll() is not None:
            self._log_unexpected_exit()
            self._status.state = PlaybackState.STOPPED
            self._started_monotonic = None
            self._cleanup_ipc_socket()
            return
        if self._status.state != PlaybackState.PLAYING or self._started_monotonic is None:
            return
        elapsed = time.monotonic() - self._started_monotonic
        position = self._base_position + elapsed
        if self._status.duration_seconds and position >= self._status.duration_seconds:
            self._status.position_seconds = float(self._status.duration_seconds)
            self._status.state = PlaybackState.STOPPED
            self._started_monotonic = None
        else:
            self._status.position_seconds = position

    def _log_unexpected_exit(self) -> None:
        if not self._process or self._stderr_logged:
            return
        return_code = self._process.returncode
        stderr_text = ""
        if self._process.stderr:
            try:
                stderr_text = self._process.stderr.read() or ""
            except OSError:
                stderr_text = ""
        self._stderr_logged = True
        if return_code not in (0, None):
            self.log.error(
                "mpv exited unexpectedly returncode=%s stderr=%s",
                return_code,
                stderr_text.strip(),
            )

    def _build_command(
        self,
        file_path: str,
        start_position_seconds: float,
        ipc_socket_path: Path,
    ) -> list[str]:
        command = [
            self.executable,
            "--no-video",
            "--no-audio-display",
            f"--audio-device={_mpv_audio_device(self.audio_device)}",
            f"--input-ipc-server={ipc_socket_path}",
            f"--volume={self._status.volume}",
        ]
        if start_position_seconds > 0:
            command.append(f"--start={start_position_seconds:.3f}")
        command.append(file_path)
        return command

    def _send_mpv_command(self, command: list[object]) -> bool:
        if not self._ipc_socket_path:
            return False
        deadline = time.monotonic() + 0.5
        payload = json.dumps({"command": command}).encode("utf-8") + b"\n"
        while time.monotonic() < deadline:
            try:
                with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
                    client.connect(str(self._ipc_socket_path))
                    client.sendall(payload)
                return True
            except (FileNotFoundError, ConnectionRefusedError, OSError):
                time.sleep(0.05)
        return False

    def _signal_process(self, signal_number: int) -> None:
        if self._process and self._process.poll() is None:
            try:
                self._process.send_signal(signal_number)
            except OSError:
                pass

    def _cleanup_ipc_socket(self) -> None:
        if not self._ipc_socket_path:
            return
        try:
            self._ipc_socket_path.unlink()
        except OSError:
            pass
        self._ipc_socket_path = None


def _mpv_audio_device(audio_device: str) -> str:
    if audio_device.startswith("alsa/"):
        return audio_device
    return f"alsa/{audio_device}"


def _ipc_socket_path() -> Path:
    return Path(tempfile.gettempdir()) / f"nightstand-mpv-{os.getpid()}-{time.time_ns()}.sock"
