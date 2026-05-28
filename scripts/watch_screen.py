from __future__ import annotations

import argparse
import subprocess
import sys
import time
from pathlib import Path

from app.config import get_settings


def main() -> None:
    parser = argparse.ArgumentParser(description="Watch the rendered e-ink simulator PNG.")
    parser.add_argument(
        "--open",
        action="store_true",
        help="Open the PNG with the macOS default image viewer when it changes.",
    )
    parser.add_argument("--interval", type=float, default=0.5, help="Polling interval in seconds.")
    args = parser.parse_args()

    settings = get_settings()
    path = settings.screen_path
    last_mtime: float | None = None
    print(f"Watching {path}")
    print("Press Ctrl-C to stop.")

    try:
        while True:
            if path.exists():
                mtime = path.stat().st_mtime
                if last_mtime is None or mtime != last_mtime:
                    last_mtime = mtime
                    timestamp = time.strftime("%H:%M:%S")
                    print(f"[{timestamp}] updated {path}")
                    if args.open:
                        _open_path(path)
            time.sleep(args.interval)
    except KeyboardInterrupt:
        print("\nStopped.")


def _open_path(path: Path) -> None:
    if sys.platform == "darwin":
        subprocess.run(["open", str(path)], check=False)
    else:
        print("--open is only wired for macOS right now.")


if __name__ == "__main__":
    main()
