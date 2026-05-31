from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import patch

from app.models import MediaItem, PlaybackState
from app.playback.mpv_player import MPVPlayer


class FakeProcess:
    def __init__(self, command):
        self.command = command
        self.returncode = None
        self.signals: list[int] = []
        self.terminated = False
        self.killed = False

    def poll(self):
        return self.returncode

    def send_signal(self, signal_number):
        self.signals.append(signal_number)

    def terminate(self):
        self.terminated = True
        self.returncode = 0

    def kill(self):
        self.killed = True
        self.returncode = -9

    def wait(self, timeout=None):
        return self.returncode


class MPVPlayerTest(unittest.TestCase):
    def test_play_uses_verified_mpv_alsa_command(self) -> None:
        calls = []

        def fake_popen(command, stdout=None, stderr=None):
            calls.append(command)
            return FakeProcess(command)

        player = MPVPlayer(audio_device="plughw:1,0")
        item = MediaItem(source_id="button-1", file_path="/tmp/example.mp3", title="Example", id=42)

        with (
            patch("subprocess.Popen", side_effect=fake_popen),
            patch.object(MPVPlayer, "_send_mpv_command", return_value=True),
        ):
            player.play(item, 12.5)

        command = calls[0]
        self.assertEqual(command[0:4], ["mpv", "--no-video", "--no-audio-display", "--audio-device=alsa/plughw:1,0"])
        self.assertIn("--start=12.500", command)
        self.assertEqual(command[-1], str(Path("/tmp/example.mp3").resolve(strict=False)))
        self.assertEqual(player.status().state, PlaybackState.PLAYING)

    def test_audio_device_prefix_is_not_doubled(self) -> None:
        player = MPVPlayer(audio_device="alsa/plughw:1,0")
        command = player._build_command("/tmp/example.mp3", 0, Path("/tmp/mpv.sock"))

        self.assertIn("--audio-device=alsa/plughw:1,0", command)

    def test_missing_mpv_stops_cleanly(self) -> None:
        player = MPVPlayer(audio_device="plughw:1,0")
        item = MediaItem(source_id="button-1", file_path="/tmp/example.mp3", title="Example", id=42)

        with patch("subprocess.Popen", side_effect=FileNotFoundError):
            player.play(item)

        self.assertEqual(player.status().state, PlaybackState.STOPPED)


if __name__ == "__main__":
    unittest.main()
