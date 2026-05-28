from __future__ import annotations

from app.input.base import InputAdapter
from app.models import InputEvent, MediaCommand


class BluetoothMediaInputAdapter(InputAdapter):
    """Future Bluetooth headphone media-control input adapter.

    This adapter is intentionally optional. Physical knob/buttons remain the
    primary controls, and the device must work fully when Bluetooth controls are
    unavailable.

    Expected normalized mapping:
    - Play/pause command -> MediaCommand.PLAY_PAUSE
    - Next command -> MediaCommand.NEXT_TRACK
    - Previous command -> MediaCommand.PREVIOUS_TRACK
    - Volume up/down commands -> MediaCommand.VOLUME_UP / VOLUME_DOWN

    Nothing Ear (a) expected behavior:
    - pinch/play-pause toggles playback
    - double pinch/next skips forward
    - triple pinch/previous restarts or goes previous using the same controller
      logic as the box knob
    - pinch-hold is deliberately not captured so ANC/transparency remains on
      the earbuds

    TODO: Implement Raspberry Pi media-key capture using BlueZ/PipeWire/MPRIS.
    TODO: Decide whether MPD should receive AVRCP/MPRIS commands directly or
    whether this app should translate them into PlaybackAdapter calls.
    TODO: Keep all Bluetooth media input optional and separate from source
    button/menu navigation.
    """

    def poll(self, timeout_seconds: float = 0.25) -> InputEvent | None:
        raise NotImplementedError("Bluetooth media controls are not implemented yet.")

    def event_for_command(self, command: MediaCommand) -> InputEvent:
        return InputEvent("media_command", command)
