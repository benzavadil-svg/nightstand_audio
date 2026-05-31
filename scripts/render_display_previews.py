from __future__ import annotations

import argparse
from datetime import datetime
from pathlib import Path

from PIL import Image, ImageDraw

from app.display.renderer import EInkRenderer
from app.display.waveshare_display import display_model_spec
from app.models import AlarmConfig, AlarmRuntimeState, PlaybackState, PlaybackStatus, RenderState, UIMode


def main() -> None:
    parser = argparse.ArgumentParser(description="Render static display previews for a Waveshare model.")
    parser.add_argument("--model", default="waveshare_4in2_v2")
    parser.add_argument("--output-dir", type=Path, default=Path("data/previews"))
    args = parser.parse_args()

    spec = display_model_spec(args.model)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    renderer = EInkRenderer(spec.width, spec.height)
    states = _preview_states()
    rendered = []
    for name, state in states.items():
        path = args.output_dir / f"{spec.model}_{name}_{spec.width}x{spec.height}.png"
        renderer.render_to_file(state, path)
        rendered.append(path)
        print(f"Rendered {path}")
    contact = args.output_dir / f"{spec.model}_contact_sheet_{spec.width}x{spec.height}.png"
    _contact_sheet(rendered, contact)
    print(f"Rendered {contact}")


def _preview_states() -> dict[str, RenderState]:
    now = datetime(2026, 5, 28, 9, 46)
    base = {
        "now": now,
        "alarm": AlarmConfig(),
        "alarm_runtime": AlarmRuntimeState(),
    }
    return {
        "ambient": RenderState(
            **base,
            mode=UIMode.AMBIENT,
            current_source_label="",
            sleep_timer_label="Sleep off",
            playback=PlaybackStatus(state=PlaybackState.STOPPED, title="Clock", volume=18),
            is_ambient_mode_active=True,
        ),
        "idle": RenderState(
            **base,
            mode=UIMode.HOME,
            current_source_label="",
            sleep_timer_label="Sleep off",
            playback=PlaybackStatus(state=PlaybackState.STOPPED, title="Clock", volume=18),
        ),
        "playback": RenderState(
            **base,
            mode=UIMode.HOME,
            sleep_timer_label="Sleep off",
            playback=PlaybackStatus(
                state=PlaybackState.PLAYING,
                source_id="button-2",
                title="Lake City Loons vs South Haven Ravens",
                subtitle="Ep 040",
                position_seconds=2530,
                duration_seconds=4320,
                volume=18,
                track_index=3,
                queue_length=51,
            ),
            current_source_label="Sleep Baseball",
            track_index=3,
            queue_length=51,
            progress_label="42:10 / 1:12:00",
            is_active_mode_active=True,
        ),
        "night": RenderState(
            **base,
            mode=UIMode.SLEEP_SCREEN,
            current_source_label="",
            playback=PlaybackStatus(state=PlaybackState.PAUSED, title="Clock", volume=12),
            sleep_timer_label="Sleep 30m",
            is_night_mode_active=True,
            is_sleep_screen_locked=True,
        ),
    }


def _contact_sheet(paths: list[Path], output_path: Path) -> None:
    images = [Image.open(path).convert("1") for path in paths]
    if not images:
        return
    width, height = images[0].size
    label_height = 24
    sheet = Image.new("1", (width * 2, (height + label_height) * 2), 1)
    draw = ImageDraw.Draw(sheet)
    for index, (path, image) in enumerate(zip(paths, images)):
        left = (index % 2) * width
        top = (index // 2) * (height + label_height)
        draw.text((left + 8, top + 6), path.stem, fill=0)
        sheet.paste(image, (left, top + label_height))
    sheet.save(output_path)
    for image in images:
        image.close()


if __name__ == "__main__":
    main()
