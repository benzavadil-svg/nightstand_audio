from __future__ import annotations

from datetime import datetime

from app.models import BluetoothRuntimeState
from app.services.logger import get_logger
from app.state_store import StateStore


class BluetoothManager:
    """Simulator-facing Bluetooth workflow with future Pi integration points.

    TODO: Use bluetoothctl to pair/trust/connect the configured device on Raspberry Pi.
    TODO: Use PipeWire/PulseAudio APIs or CLI tools to discover Bluetooth sinks.
    TODO: Periodically attempt reconnect during the reconnect timeout window.
    TODO: Switch the normal playback sink when Bluetooth becomes available.
    TODO: Keep alarm routing separate; alarm defaults to the internal speaker sink.
    """

    def __init__(
        self,
        store: StateStore,
        trusted_device_name: str = "Nothing Ear (a)",
        reconnect_timeout_seconds: int = 30,
    ) -> None:
        self.store = store
        self.log = get_logger("AUDIO")
        preferred_output = store.get_app_state_value("preferred_output") or "dac"
        self.state = BluetoothRuntimeState(
            trusted_device_name=trusted_device_name,
            preferred_output=preferred_output,
            active_sink=preferred_output if preferred_output in {"bluetooth", "dac"} else "dac",
            reconnect_timeout_seconds=reconnect_timeout_seconds,
        )
        if self.state.active_sink == "bluetooth":
            self.state.active_sink = "dac"
            self.state.last_message = "Bluetooth unavailable"
        self.log.info(
            "Current sink selected sink=%s preferred=%s",
            self.state.active_sink,
            preferred_output,
        )

    def begin_reconnect(self, now: datetime | None = None) -> None:
        now = now or datetime.now()
        self.state.reconnecting = True
        self.state.reconnect_started_at = now
        self.state.last_message = f"Connecting: {self.state.trusted_device_name}"
        self.log.info("Bluetooth reconnect started device=%s", self.state.trusted_device_name)

    def fake_success(self) -> None:
        self.state.connected = True
        self.state.reconnecting = False
        self.state.active_sink = "bluetooth"
        self.state.preferred_output = "bluetooth"
        self.state.last_message = f"Connected: {self.state.trusted_device_name}"
        self.store.set_app_state_value("preferred_output", "bluetooth")
        self.log.info("Bluetooth connected device=%s sink=bluetooth", self.state.trusted_device_name)

    def fake_failure(self) -> None:
        previous_sink = self.state.active_sink if self.state.active_sink != "bluetooth" else "dac"
        self.state.connected = False
        self.state.reconnecting = False
        self.state.active_sink = previous_sink
        self.state.last_message = "Earbuds Not Found"
        self.log.warning("Bluetooth reconnect failed; preserving sink=%s", previous_sink)

    def disconnect(self) -> None:
        self.state.connected = False
        if self.state.active_sink == "bluetooth":
            self.state.active_sink = "dac"
        self.state.last_message = "Bluetooth disconnected"
        self.log.info("Bluetooth disconnected; active_sink=%s", self.state.active_sink)

    def tick(self, now: datetime | None = None) -> bool:
        now = now or datetime.now()
        if not self.state.reconnecting or not self.state.reconnect_started_at:
            return False
        elapsed = (now - self.state.reconnect_started_at).total_seconds()
        if elapsed >= self.state.reconnect_timeout_seconds:
            self.fake_failure()
            return True
        return False

    def output_label(self) -> str:
        if self.state.active_sink == "bluetooth" and self.state.connected:
            return "Bluetooth"
        if self.state.active_sink == "speaker":
            return "Speaker"
        return "Headphones"
