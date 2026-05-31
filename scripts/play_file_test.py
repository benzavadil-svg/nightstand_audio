from __future__ import annotations

import argparse
import time
from pathlib import Path

from app.config import get_settings
from app.models import MediaItem, PlaybackState
from app.playback.factory import build_playback_adapter
from app.services.audio import AudioOutputSelector


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Play one file using the same MPV playback adapter as appliance mode."
    )
    parser.add_argument("file", type=Path, help="Audio file to play.")
    parser.add_argument("--start", type=float, default=0.0, help="Start position in seconds.")
    args = parser.parse_args()

    file_path = args.file.expanduser().resolve(strict=False)
    if not file_path.exists():
        raise SystemExit(f"File not found: {file_path}")

    settings = get_settings()
    audio_selection = AudioOutputSelector(
        settings.audio_backend,
        settings.audio_device,
    ).select()
    player = build_playback_adapter(settings, audio_selection, force_backend="mpv")
    item = MediaItem(
        source_id="manual-test",
        file_path=str(file_path),
        title=file_path.stem,
    )
    try:
        player.play(item, args.start)
        while player.status().state == PlaybackState.PLAYING:
            time.sleep(0.25)
    except KeyboardInterrupt:
        pass
    finally:
        player.stop()


if __name__ == "__main__":
    main()
