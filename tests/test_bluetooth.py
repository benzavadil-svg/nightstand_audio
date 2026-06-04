from __future__ import annotations

import tempfile
import subprocess
import time
import unittest
from datetime import datetime, timedelta
from pathlib import Path

from app.display.base import DisplayAdapter
from app.media_library import MediaLibrary
from app.models import BluetoothDevice, InputEvent, MediaCommand, RenderState, UIMode
from app.playback.mock_player import MockPlayer
from app.services.bluetooth import (
    BluetoothManager,
    BluetoothPairingResult,
    PHASE_PAIRED,
    SubprocessBluetoothBackend,
    _adapter_powered,
    _authentication_failed,
    _bluetooth_show_summary,
    _bluetooth_not_ready,
    _connect_succeeded,
    _looks_like_mac_label,
    _pairing_succeeded,
    _parse_bluetooth_devices,
    _parse_btmgmt_devices,
    _parse_wpctl_sinks,
    _profile_unavailable,
    _trust_succeeded,
    _wpctl_inspect_value,
)
from app.services.controller import NightstandController
from app.state_store import StateStore


class MemoryDisplay(DisplayAdapter):
    def __init__(self) -> None:
        self.last_state: RenderState | None = None

    def render(self, state: RenderState, reason: str | None = None) -> None:
        self.last_state = state


class FakeBluetoothBackend:
    def __init__(self) -> None:
        self.devices = [BluetoothDevice("AA:BB:CC:DD:EE:FF", "Ben's Headphones")]
        self.available = False
        self.connected = False
        self.connect_calls = 0
        self.disconnect_calls = 0
        self.trust_calls = 0
        self.stop_scan_calls = 0
        self.discover_calls = 0
        self.default_sink = ""
        self.pair_result = True
        self.on_pair = None

    def enable(self) -> None:
        pass

    def start_scan(self) -> None:
        self.available = True

    def stop_scan(self) -> None:
        self.stop_scan_calls += 1

    def discover_devices(self, preferred_name: str = "") -> list[BluetoothDevice]:
        self.discover_calls += 1
        return self.devices if self.available else []

    def pair_trust_connect(self, device: BluetoothDevice) -> bool:
        if self.on_pair:
            self.on_pair()
        self.available = True
        self.connected = self.pair_result
        return self.pair_result

    def trust(self, mac: str) -> bool:
        self.trust_calls += 1
        return True

    def connect(self, mac: str) -> bool:
        self.connect_calls += 1
        self.connected = self.available
        return self.connected

    def disconnect(self, mac: str) -> bool:
        self.disconnect_calls += 1
        self.connected = False
        return True

    def is_connected(self, mac: str) -> bool:
        return self.connected

    def is_available(self, mac: str) -> bool:
        return self.available

    def bluetooth_audio_device(self, mac: str, name: str) -> str:
        return "pulse/bluez_output.AA_BB_CC_DD_EE_FF.1"

    def switch_to_bluetooth(self, audio_device: str) -> bool:
        self.default_sink = audio_device
        return True

    def switch_to_bossdac(self) -> bool:
        self.default_sink = "bossdac"
        return True


class PairedWithoutAudioBackend(FakeBluetoothBackend):
    def pair_trust_connect(self, device: BluetoothDevice) -> BluetoothPairingResult:
        self.available = True
        self.connected = False
        return BluetoothPairingResult(
            device=device,
            paired=True,
            trusted=True,
            connected=False,
            connect_failed_profile_unavailable=True,
            error="Failed to connect: org.bluez.Error.Failed br-connection-profile-unavailable",
        )


class ConnectedWithoutAudioSinkBackend(FakeBluetoothBackend):
    def bluetooth_audio_device(self, mac: str, name: str) -> str:
        return ""


class FailingReconnectBackend(FakeBluetoothBackend):
    def connect(self, mac: str) -> bool:
        self.connect_calls += 1
        self.connected = False
        return False


class KnownDeviceBluetoothBackend(FakeBluetoothBackend):
    def known_devices(self) -> list[BluetoothDevice]:
        return self.devices if self.available else []


class DeviceSpyPlayer(MockPlayer):
    def __init__(self) -> None:
        super().__init__()
        self.audio_devices: list[str] = []

    def set_audio_device(self, audio_device: str) -> None:
        self.audio_devices.append(audio_device)


class ScanOutputBluetoothBackend(SubprocessBluetoothBackend):
    def __init__(self, post_scan_devices: str = "", btmgmt_output: str = "") -> None:
        super().__init__(timeout_seconds=0.1)
        self.commands: list[tuple[str, ...]] = []
        self.post_scan_devices = post_scan_devices
        self.btmgmt_output = btmgmt_output
        self.scan_sample_used = False
        self.sessions: list[tuple[str, ...]] = []
        self.paired = False
        self.connected = False
        self.wpctl_status_output = ""
        self.wpctl_inspect_output = ""
        self.wpctl_inspect_outputs: dict[str, str] = {}
        self.command_results: dict[tuple[str, ...], tuple[int, str, str]] = {}
        self.reconnect_sessions: list[str] = []

    def _bluetoothctl(self, *args: str) -> str:
        self.commands.append(args)
        if args == ("power", "on"):
            return "Changing power on succeeded\n"
        if args == ("show",):
            return "Powered: yes\n"
        if args == ("scan", "off"):
            return "Discovery stopped\n"
        if args == ("trust", "AA:BB:CC:DD:EE:FF"):
            return "Changing AA:BB:CC:DD:EE:FF trust succeeded\n"
        if args == ("info", "AA:BB:CC:DD:EE:FF"):
            return (
                f"Device AA:BB:CC:DD:EE:FF\nPaired: {'yes' if self.paired else 'no'}\n"
                f"Connected: {'yes' if self.connected else 'no'}\n"
            )
        if args == ("devices",) and self.post_scan_devices and self.scan_sample_used:
            return self.post_scan_devices
        return ""

    def _bluetoothctl_timeout(self, timeout_seconds: int, *args: str) -> str:
        self.commands.append(("--timeout", str(timeout_seconds), *args))
        self.scan_sample_used = True
        if args == ("pair", "AA:BB:CC:DD:EE:FF"):
            self.paired = True
            return "Pairing successful\n"
        if args == ("connect", "AA:BB:CC:DD:EE:FF"):
            self.connected = True
            return "Connection successful\n"
        if self.post_scan_devices:
            return ""
        return "[NEW] Device AA:BB:CC:DD:EE:FF Ben's Headphones\n"

    def _btmgmt_find(self, timeout_seconds: int) -> str:
        self.commands.append(("btmgmt", "find", str(timeout_seconds)))
        return self.btmgmt_output

    def _pactl(self, *args: str) -> str:
        self.commands.append(("pactl", *args))
        return ""

    def _wpctl(self, *args: str) -> str:
        self.commands.append(("wpctl", *args))
        if args == ("status",):
            return self.wpctl_status_output
        if len(args) == 2 and args[0] == "inspect":
            return self.wpctl_inspect_outputs.get(args[1], self.wpctl_inspect_output)
        return ""

    def _run(
        self,
        command: list[str],
        timeout_seconds: float | None = None,
        input_text: str | None = None,
    ):
        self.commands.append(tuple(command))
        result = self.command_results.get(tuple(command))
        if result:
            return subprocess.CompletedProcess(command, result[0], stdout=result[1], stderr=result[2])
        return super()._run(command, timeout_seconds=timeout_seconds, input_text=input_text)

    def _can_use_btmgmt(self) -> bool:
        return True

    def _bluetoothctl_session(
        self,
        commands: list[str],
        timeout_seconds: float,
    ) -> str:
        self.sessions.append(tuple(commands))
        return "Pairing successful\nChanging AA:BB:CC:DD:EE:FF trust succeeded\nConnection successful\n"

    def _bluetoothctl_interactive(
        self,
        commands: list[tuple[float, str]],
        timeout_seconds: float,
    ) -> str:
        self.sessions.append(tuple(command for _, command in commands))
        self.paired = True
        return "Pairing successful\n"

    def _bluetoothctl_pairing_session(
        self,
        mac: str,
        timeout_seconds: float = 90.0,
        scan_wait_seconds: float = 18.0,
    ) -> str:
        self.sessions.append(("pairing_session", mac))
        self.paired = True
        return "Pairing successful\n"

    def _bluetoothctl_reconnect_session(
        self,
        mac: str,
        timeout_seconds: float = 25.0,
        scan_wait_seconds: float = 12.0,
    ) -> str:
        self.reconnect_sessions.append(mac)
        self.connected = True
        return "Connection successful\n"


class NotReadyBluetoothBackend(ScanOutputBluetoothBackend):
    def __init__(self) -> None:
        super().__init__(post_scan_devices="Device AA:BB:CC:DD:EE:FF Ready Headphones\n")
        self.powered = False

    def _bluetoothctl(self, *args: str) -> str:
        self.commands.append(args)
        if args == ("power", "on"):
            self.powered = True
            return "Changing power on succeeded\n"
        if args == ("show",):
            return "Powered: yes\n" if self.powered else "Powered: no\n"
        if args == ("devices",) and self.scan_sample_used:
            return self.post_scan_devices
        return ""

    def _bluetoothctl_timeout(self, timeout_seconds: int, *args: str) -> str:
        self.commands.append(("--timeout", str(timeout_seconds), *args))
        self.scan_sample_used = True
        if self.powered:
            return ""
        return "Failed to start discovery: org.bluez.Error.NotReady\n"

    def _rfkill_unblock(self) -> None:
        self.commands.append(("rfkill", "unblock", "bluetooth"))

    def _can_use_btmgmt(self) -> bool:
        return False


class AuthRetryBluetoothBackend(ScanOutputBluetoothBackend):
    def __init__(self) -> None:
        super().__init__()
        self.pair_attempts = 0

    def _bluetoothctl(self, *args: str) -> str:
        self.commands.append(args)
        if args == ("remove", "AA:BB:CC:DD:EE:FF"):
            self.paired = False
            self.connected = False
            return "Device has been removed\n"
        return super()._bluetoothctl(*args)

    def _bluetoothctl_timeout(self, timeout_seconds: int, *args: str) -> str:
        self.commands.append(("--timeout", str(timeout_seconds), *args))
        if args == ("connect", "AA:BB:CC:DD:EE:FF"):
            self.connected = True
            return "Connection successful\n"
        if args == ("pair", "AA:BB:CC:DD:EE:FF"):
            self.pair_attempts += 1
            if self.pair_attempts == 1:
                self.connected = False
                return (
                    "Attempting to pair with AA:BB:CC:DD:EE:FF\n"
                    "[CHG] Device AA:BB:CC:DD:EE:FF Connected: yes\n"
                    "Failed to pair: org.bluez.Error.AuthenticationFailed\n"
                    "[CHG] Device AA:BB:CC:DD:EE:FF Connected: no\n"
                )
            self.paired = True
            return "Pairing successful\n"
        if args == ("scan", "on"):
            return "[NEW] Device AA:BB:CC:DD:EE:FF Ben's Headphones\n"
        return super()._bluetoothctl_timeout(timeout_seconds, *args)

    def _bluetoothctl_interactive(
        self,
        commands: list[tuple[float, str]],
        timeout_seconds: float,
    ) -> str:
        self.sessions.append(tuple(command for _, command in commands))
        self.pair_attempts += 1
        if self.pair_attempts == 1:
            self.connected = False
            return (
                "Attempting to pair with AA:BB:CC:DD:EE:FF\n"
                "[CHG] Device AA:BB:CC:DD:EE:FF Connected: yes\n"
                "Failed to pair: org.bluez.Error.AuthenticationFailed\n"
                "[CHG] Device AA:BB:CC:DD:EE:FF Connected: no\n"
            )
        self.paired = True
        return "Pairing successful\n"

    def _bluetoothctl_pairing_session(
        self,
        mac: str,
        timeout_seconds: float = 90.0,
        scan_wait_seconds: float = 18.0,
    ) -> str:
        self.sessions.append(("pairing_session", mac))
        self.pair_attempts += 1
        if self.pair_attempts == 1:
            self.connected = False
            return (
                "Attempting to pair with AA:BB:CC:DD:EE:FF\n"
                "[CHG] Device AA:BB:CC:DD:EE:FF Connected: yes\n"
                "Failed to pair: org.bluez.Error.AuthenticationFailed\n"
                "[CHG] Device AA:BB:CC:DD:EE:FF Connected: no\n"
            )
        self.paired = True
        return "Pairing successful\n"


class ProfileUnavailableBluetoothBackend(ScanOutputBluetoothBackend):
    def _bluetoothctl_pairing_session(
        self,
        mac: str,
        timeout_seconds: float = 90.0,
        scan_wait_seconds: float = 18.0,
    ) -> str:
        self.sessions.append(("pairing_session", mac))
        self.paired = True
        return "Pairing successful\n"

    def _bluetoothctl_timeout(self, timeout_seconds: int, *args: str) -> str:
        self.commands.append(("--timeout", str(timeout_seconds), *args))
        if args == ("pair", "AA:BB:CC:DD:EE:FF"):
            self.paired = True
            return "Pairing successful\n"
        if args == ("connect", "AA:BB:CC:DD:EE:FF"):
            self.connected = False
            return "Failed to connect: org.bluez.Error.Failed br-connection-profile-unavailable\n"
        if args == ("scan", "on"):
            return "[NEW] Device AA:BB:CC:DD:EE:FF Ben's Headphones\n"
        return super()._bluetoothctl_timeout(timeout_seconds, *args)


class ConnectedDuringPairBluetoothBackend(ScanOutputBluetoothBackend):
    def _bluetoothctl_pairing_session(
        self,
        mac: str,
        timeout_seconds: float = 90.0,
        scan_wait_seconds: float = 18.0,
    ) -> str:
        self.sessions.append(("pairing_session", mac))
        self.paired = True
        self.connected = True
        return (
            "Attempting to pair with AA:BB:CC:DD:EE:FF\n"
            "[CHG] Device AA:BB:CC:DD:EE:FF Connected: yes\n"
            "Pairing successful\n"
        )

    def _bluetoothctl_timeout(self, timeout_seconds: int, *args: str) -> str:
        self.commands.append(("--timeout", str(timeout_seconds), *args))
        if args == ("pair", "AA:BB:CC:DD:EE:FF"):
            self.paired = True
            self.connected = True
            return (
                "Attempting to pair with AA:BB:CC:DD:EE:FF\n"
                "[CHG] Device AA:BB:CC:DD:EE:FF Connected: yes\n"
                "Pairing successful\n"
            )
        if args == ("connect", "AA:BB:CC:DD:EE:FF"):
            return "Failed to connect: org.bluez.Error.Failed br-connection-profile-unavailable\n"
        if args == ("scan", "on"):
            return "[NEW] Device AA:BB:CC:DD:EE:FF Ben's Headphones\n"
        return super()._bluetoothctl_timeout(timeout_seconds, *args)


class CachedOnlySubprocessBackend(ScanOutputBluetoothBackend):
    def _bluetoothctl(self, *args: str) -> str:
        self.commands.append(args)
        if args == ("info", "AA:BB:CC:DD:EE:FF"):
            return (
                "Device AA:BB:CC:DD:EE:FF\n"
                "Name: Ben's Headphones\n"
                "Paired: yes\n"
                "Trusted: yes\n"
                "Connected: no\n"
            )
        return super()._bluetoothctl(*args)

    def _bluetoothctl_timeout(self, timeout_seconds: int, *args: str) -> str:
        self.commands.append(("--timeout", str(timeout_seconds), *args))
        if args == ("scan", "on"):
            return "[NEW] Device 11:22:33:44:55:66 Other Headphones\n"
        return super()._bluetoothctl_timeout(timeout_seconds, *args)


class VisibleSubprocessBackend(CachedOnlySubprocessBackend):
    def _bluetoothctl_timeout(self, timeout_seconds: int, *args: str) -> str:
        self.commands.append(("--timeout", str(timeout_seconds), *args))
        if args == ("scan", "on"):
            return "[NEW] Device AA:BB:CC:DD:EE:FF Ben's Headphones\n"
        return super()._bluetoothctl_timeout(timeout_seconds, *args)


class BluetoothManagerTest(unittest.TestCase):
    def _wait_for_pairing_result(
        self,
        controller: NightstandController,
        now: datetime,
        timeout_seconds: float = 1.0,
    ) -> None:
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            controller.tick(now)
            if controller.bluetooth.state.last_message in {
                "Pairing Failed",
                "Bluetooth Ready: Ben's Headphones",
                "Paired: Ben's Headphones",
            }:
                return
            time.sleep(0.01)
        controller.tick(now)

    def test_adapter_powered_and_not_ready_parsers(self) -> None:
        show_output = (
            "Controller AA:BB:CC:DD:EE:FF (public)\n"
            "Powered: yes\n"
            "Discovering: no\n"
            "Pairable: yes\n"
        )
        self.assertTrue(_adapter_powered(show_output))
        self.assertFalse(_adapter_powered("Powered: no\n"))
        self.assertTrue(_bluetooth_not_ready("Failed to start discovery: org.bluez.Error.NotReady"))
        self.assertEqual(
            _bluetooth_show_summary(show_output),
            "controller=AA:BB:CC:DD:EE:FF powered=yes discovering=no pairable=yes",
        )
        self.assertTrue(_pairing_succeeded("Pairing successful"))
        self.assertTrue(_pairing_succeeded("Failed to pair: org.bluez.Error.AlreadyExists"))
        self.assertTrue(_trust_succeeded("Changing AA trust succeeded"))
        self.assertTrue(_connect_succeeded("Connection successful"))
        self.assertTrue(_profile_unavailable("Failed to connect: org.bluez.Error.Failed br-connection-profile-unavailable"))
        self.assertTrue(_authentication_failed("Failed to pair: org.bluez.Error.AuthenticationFailed"))

    def test_parse_scan_output_devices(self) -> None:
        devices = _parse_bluetooth_devices(
            "[NEW] Device AA:BB:CC:DD:EE:FF Ben's Headphones\n"
            "[CHG] Device AA:BB:CC:DD:EE:FF RSSI: -48\n"
        )

        self.assertEqual(len(devices), 1)
        self.assertEqual(devices[0].mac, "AA:BB:CC:DD:EE:FF")
        self.assertEqual(devices[0].name, "Ben's Headphones")

    def test_parse_scan_output_merges_chg_name_and_returns_audio_devices(self) -> None:
        devices = _parse_bluetooth_devices(
            "[NEW] Device 70:FD:56:FF:13:B5 70-FD-56-FF-13-B5\n"
            "[NEW] Device 3C:B0:ED:B9:30:FC 3C-B0-ED-B9-30-FC\n"
            "[CHG] Device 3C:B0:ED:B9:30:FC RSSI: 0xffffffd9 (-39)\n"
            "[CHG] Device 3C:B0:ED:B9:30:FC Name: Nothing Ear (a)\n"
            "[CHG] Device 3C:B0:ED:B9:30:FC Alias: Nothing Ear (a)\n"
            "[CHG] Device 3C:B0:ED:B9:30:FC Icon: audio-headset\n"
            "[CHG] Device 3C:B0:ED:B9:30:FC UUIDs: 0000110b-0000-1000-8000-00805f9b34fb\n"
        )

        self.assertEqual(len(devices), 1)
        self.assertEqual(devices[0].mac, "3C:B0:ED:B9:30:FC")
        self.assertEqual(devices[0].name, "Nothing Ear (a)")
        self.assertTrue(_looks_like_mac_label("3C-B0-ED-B9-30-FC"))

    def test_parse_btmgmt_find_devices(self) -> None:
        devices = _parse_btmgmt_devices(
            "hci0 dev_found: AA:BB:CC:DD:EE:FF type BR/EDR rssi -45 flags 0x0000\n"
            "eir_len 24\n"
            "name Ben's Headphones\n"
            "hci0 dev_found: 11:22:33:44:55:66 type LE Random rssi -70 flags 0x0004\n"
        )

        self.assertEqual(len(devices), 2)
        self.assertEqual(devices[0].name, "Ben's Headphones")
        self.assertEqual(devices[1].name, "11:22:33:44:55:66")

    def test_parse_wpctl_sinks_and_inspect_values(self) -> None:
        sinks = _parse_wpctl_sinks(
            "Audio\n"
            " ├─ Sinks:\n"
            " │  *   42. BossDAC Analog Stereo [vol: 0.35]\n"
            " │      91. Nothing Ear (a) [vol: 0.35]\n"
            " ├─ Sources:\n"
        )

        self.assertEqual(sinks, [("42", "BossDAC Analog Stereo"), ("91", "Nothing Ear (a)")])
        self.assertEqual(
            _wpctl_inspect_value('node.name = "bluez_output.3C_B0_ED_B9_30_FC.1"', "node.name"),
            "bluez_output.3C_B0_ED_B9_30_FC.1",
        )

    def test_wpctl_display_name_sink_resolves_to_bluez_node_name(self) -> None:
        backend = ScanOutputBluetoothBackend()
        backend.wpctl_status_output = (
            "Audio\n"
            " ├─ Sinks:\n"
            " │  *   42. BossDAC Analog Stereo [vol: 0.35]\n"
            " │      91. Nothing Ear (a) [vol: 0.35]\n"
            " ├─ Sources:\n"
        )
        backend.wpctl_inspect_output = (
            'node.name = "bluez_output.3C_B0_ED_B9_30_FC.1"\n'
            'device.api = "bluez5"\n'
        )
        backend.wpctl_inspect_outputs = {
            "42": 'node.name = "alsa_output.platform-soc_sound.stereo-fallback"\n',
            "91": backend.wpctl_inspect_output,
        }

        audio_device = backend.bluetooth_audio_device("3C:B0:ED:B9:30:FC", "Nothing Ear (a)")

        self.assertEqual(audio_device, "pulse/bluez_output.3C_B0_ED_B9_30_FC.1")
        self.assertIn(("wpctl", "inspect", "91"), backend.commands)

    def test_missing_bluetooth_spa_package_is_checked_when_no_sink_exists(self) -> None:
        backend = ScanOutputBluetoothBackend()
        backend.wpctl_status_output = (
            "Audio\n"
            " ├─ Sinks:\n"
            " │  *   66. Built-in Audio Stereo [vol: 0.40]\n"
        )
        command = ("dpkg-query", "-W", "-f=${Status}", "libspa-0.2-bluetooth")
        backend.command_results[command] = (1, "", "no packages found matching libspa-0.2-bluetooth\n")

        audio_device = backend.bluetooth_audio_device("3C:B0:ED:B9:30:FC", "Nothing Ear (a)")

        self.assertEqual(audio_device, "")
        self.assertIn(command, backend.commands)

    def test_subprocess_backend_uses_scan_output_when_devices_cache_is_empty(self) -> None:
        backend = ScanOutputBluetoothBackend()

        devices = backend.discover_devices("")

        self.assertEqual([device.name for device in devices], ["Ben's Headphones"])
        self.assertIn(("--timeout", "5", "scan", "on"), backend.commands)

    def test_subprocess_backend_rereads_devices_after_scan_sample(self) -> None:
        backend = ScanOutputBluetoothBackend(
            post_scan_devices="Device 11:22:33:44:55:66 Fresh Headphones\n"
        )

        devices = backend.discover_devices("")

        self.assertEqual([device.name for device in devices], ["Fresh Headphones"])
        self.assertIn(("devices",), backend.commands)

    def test_subprocess_backend_uses_btmgmt_find_when_bluetoothctl_is_empty(self) -> None:
        backend = ScanOutputBluetoothBackend(
            btmgmt_output=(
                "hci0 dev_found: AA:BB:CC:DD:EE:FF type BR/EDR rssi -45 flags 0x0000\n"
                "name Ben's Headphones\n"
            )
        )

        devices = backend.discover_devices("")

        self.assertEqual([device.name for device in devices], ["Ben's Headphones"])
        self.assertIn(("btmgmt", "find", "5"), backend.commands)

    def test_subprocess_backend_powers_adapter_before_discovery(self) -> None:
        backend = NotReadyBluetoothBackend()

        devices = backend.discover_devices("")

        self.assertEqual([device.name for device in devices], ["Ready Headphones"])
        self.assertIn(("rfkill", "unblock", "bluetooth"), backend.commands)
        self.assertIn(("power", "on"), backend.commands)

    def test_pairing_uses_single_bluetoothctl_pairing_session(self) -> None:
        backend = ScanOutputBluetoothBackend()

        result = backend.pair_trust_connect(
            BluetoothDevice("AA:BB:CC:DD:EE:FF", "Ben's Headphones")
        )

        self.assertTrue(result.paired)
        self.assertTrue(result.connected)
        self.assertIn(("pairing_session", "AA:BB:CC:DD:EE:FF"), backend.sessions)
        self.assertNotIn(("--timeout", "45", "pair", "AA:BB:CC:DD:EE:FF"), backend.commands)
        self.assertIn(("trust", "AA:BB:CC:DD:EE:FF"), backend.commands)
        self.assertIn(("--timeout", "30", "connect", "AA:BB:CC:DD:EE:FF"), backend.commands)
        self.assertNotIn(("agent", "on"), backend.commands)
        self.assertNotIn(("default-agent",), backend.commands)

    def test_reconnect_uses_single_bluetoothctl_scan_connect_session(self) -> None:
        backend = ScanOutputBluetoothBackend()

        connected = backend.connect("AA:BB:CC:DD:EE:FF")

        self.assertTrue(connected)
        self.assertEqual(backend.reconnect_sessions, ["AA:BB:CC:DD:EE:FF"])
        self.assertNotIn(("connect", "AA:BB:CC:DD:EE:FF"), backend.commands)

    def test_pairing_retries_once_after_authentication_failed(self) -> None:
        backend = AuthRetryBluetoothBackend()

        result = backend.pair_trust_connect(
            BluetoothDevice("AA:BB:CC:DD:EE:FF", "Ben's Headphones")
        )

        self.assertTrue(result.paired)
        self.assertTrue(result.connected)
        self.assertEqual(backend.pair_attempts, 2)
        self.assertIn(("remove", "AA:BB:CC:DD:EE:FF"), backend.commands)
        self.assertIn(("--timeout", "15", "scan", "on"), backend.commands)
        self.assertIn(("trust", "AA:BB:CC:DD:EE:FF"), backend.commands)
        self.assertIn(("--timeout", "30", "connect", "AA:BB:CC:DD:EE:FF"), backend.commands)

    def test_explicit_pairing_reuses_existing_pairing_before_repairing(self) -> None:
        backend = ScanOutputBluetoothBackend()
        backend.paired = True

        result = backend.pair_trust_connect(
            BluetoothDevice("AA:BB:CC:DD:EE:FF", "Ben's Headphones")
        )

        self.assertTrue(result.paired)
        self.assertTrue(result.connected)
        self.assertNotIn(("remove", "AA:BB:CC:DD:EE:FF"), backend.commands)
        self.assertNotIn(("--timeout", "45", "pair", "AA:BB:CC:DD:EE:FF"), backend.commands)
        self.assertIn(("trust", "AA:BB:CC:DD:EE:FF"), backend.commands)
        self.assertIn(("--timeout", "30", "connect", "AA:BB:CC:DD:EE:FF"), backend.commands)

    def test_existing_connected_pairing_skips_redundant_connect(self) -> None:
        backend = ScanOutputBluetoothBackend()
        backend.paired = True
        backend.connected = True

        result = backend.pair_trust_connect(
            BluetoothDevice("AA:BB:CC:DD:EE:FF", "Ben's Headphones")
        )

        self.assertTrue(result.paired)
        self.assertTrue(result.connected)
        self.assertIn(("trust", "AA:BB:CC:DD:EE:FF"), backend.commands)
        self.assertNotIn(("--timeout", "30", "connect", "AA:BB:CC:DD:EE:FF"), backend.commands)

    def test_pairing_that_connects_during_pairing_skips_redundant_connect(self) -> None:
        backend = ConnectedDuringPairBluetoothBackend()

        result = backend.pair_trust_connect(
            BluetoothDevice("AA:BB:CC:DD:EE:FF", "Ben's Headphones")
        )

        self.assertTrue(result.paired)
        self.assertTrue(result.connected)
        self.assertIn(("trust", "AA:BB:CC:DD:EE:FF"), backend.commands)
        self.assertNotIn(("--timeout", "30", "connect", "AA:BB:CC:DD:EE:FF"), backend.commands)

    def test_pairing_profile_unavailable_still_reports_paired(self) -> None:
        backend = ProfileUnavailableBluetoothBackend()

        result = backend.pair_trust_connect(
            BluetoothDevice("AA:BB:CC:DD:EE:FF", "Ben's Headphones")
        )

        self.assertTrue(result.paired)
        self.assertTrue(result.trusted)
        self.assertFalse(result.connected)
        self.assertTrue(result.connect_failed_profile_unavailable)
        self.assertIn("connection-profile-unavailable", result.error)

    def test_subprocess_availability_ignores_cached_paired_device_without_discovery(self) -> None:
        backend = CachedOnlySubprocessBackend()

        self.assertFalse(backend.is_available("AA:BB:CC:DD:EE:FF"))
        self.assertIn(("--timeout", "4", "scan", "on"), backend.commands)

    def test_subprocess_availability_accepts_device_seen_during_scan(self) -> None:
        backend = VisibleSubprocessBackend()

        self.assertTrue(backend.is_available("AA:BB:CC:DD:EE:FF"))
        self.assertIn(("--timeout", "4", "scan", "on"), backend.commands)

    def test_fake_success_switches_sink_and_persists_preference(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = StateStore(Path(tmp) / "test.sqlite")
            manager = BluetoothManager(store, backend=FakeBluetoothBackend())

            manager.begin_reconnect(datetime(2026, 5, 25, 8, 0))
            manager.fake_success()

            self.assertTrue(manager.state.connected)
            self.assertEqual(manager.state.active_sink, "bluetooth")
            self.assertEqual(store.get_app_state_value("preferred_output"), "bluetooth")
            self.assertIn("Connected", manager.state.last_message)

    def test_reconnect_without_preferred_device_prompts_pairing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = StateStore(Path(tmp) / "test.sqlite")
            manager = BluetoothManager(store)
            now = datetime(2026, 5, 25, 8, 0)

            manager.begin_reconnect(now)

            self.assertFalse(manager.state.connected)
            self.assertEqual(manager.state.active_sink, "dac")
            self.assertEqual(manager.state.last_message, "Pair Headphones First")

    def test_pair_selected_device_persists_preferred_headphones(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = StateStore(Path(tmp) / "test.sqlite")
            backend = FakeBluetoothBackend()
            manager = BluetoothManager(store, backend=backend)

            manager.begin_pairing(datetime(2026, 5, 25, 8, 0))
            paired = manager.pair_selected_device(datetime(2026, 5, 25, 8, 1))

            self.assertTrue(paired)
            self.assertTrue(manager.state.connected)
            self.assertEqual(manager.state.preferred_device_name, "Ben's Headphones")
            self.assertEqual(
                store.get_app_state_value("preferred_bluetooth_device_mac"),
                "AA:BB:CC:DD:EE:FF",
            )

    def test_pair_selected_device_persists_when_audio_profile_is_not_ready(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = StateStore(Path(tmp) / "test.sqlite")
            backend = PairedWithoutAudioBackend()
            manager = BluetoothManager(store, backend=backend)

            manager.begin_pairing(datetime(2026, 5, 25, 8, 0))
            paired = manager.pair_selected_device(datetime(2026, 5, 25, 8, 1))

            self.assertTrue(paired)
            self.assertFalse(manager.state.connected)
            self.assertEqual(manager.state.phase, PHASE_PAIRED)
            self.assertEqual(manager.state.active_sink, "dac")
            self.assertEqual(manager.state.last_message, "Paired: Ben's Headphones")
            self.assertEqual(backend.default_sink, "bossdac")
            self.assertEqual(
                store.get_app_state_value("preferred_bluetooth_device_mac"),
                "AA:BB:CC:DD:EE:FF",
            )
            self.assertEqual(store.get_app_state_value("preferred_output"), "bluetooth")

    def test_connected_device_without_audio_sink_keeps_bossdac_active(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = StateStore(Path(tmp) / "test.sqlite")
            store.set_app_state_value("preferred_bluetooth_device_name", "Ben's Headphones")
            store.set_app_state_value("preferred_bluetooth_device_mac", "AA:BB:CC:DD:EE:FF")
            backend = ConnectedWithoutAudioSinkBackend()
            manager = BluetoothManager(store, backend=backend)

            backend.available = True
            changed = manager.tick(datetime(2026, 5, 25, 8, 0))

            self.assertTrue(changed)
            self.assertTrue(manager.state.connected)
            self.assertEqual(manager.state.active_sink, "dac")
            self.assertEqual(manager.playback_audio_device(), "plughw:1,0")
            self.assertEqual(manager.output_label(), "BossDAC")
            self.assertIn("audio pending", manager.state.last_message)

    def test_connected_device_without_audio_sink_waits_instead_of_cycling_connection(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = StateStore(Path(tmp) / "test.sqlite")
            store.set_app_state_value("preferred_bluetooth_device_name", "Ben's Headphones")
            store.set_app_state_value("preferred_bluetooth_device_mac", "AA:BB:CC:DD:EE:FF")
            backend = ConnectedWithoutAudioSinkBackend()
            manager = BluetoothManager(
                store,
                backend=backend,
                presence_interval_seconds=1,
            )
            now = datetime(2026, 5, 25, 8, 0)

            backend.available = True
            backend.connected = True
            manager.tick(now)
            changed = manager.tick(now + timedelta(seconds=6))

            self.assertTrue(changed)
            self.assertEqual(backend.disconnect_calls, 0)
            self.assertEqual(backend.connect_calls, 0)
            self.assertTrue(manager.state.connected)
            self.assertEqual(manager.state.active_sink, "dac")
            self.assertEqual(manager.state.last_message, "Bluetooth Audio Pending")

    def test_prepare_for_playback_adopts_existing_connected_known_device(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = StateStore(Path(tmp) / "test.sqlite")
            backend = KnownDeviceBluetoothBackend()
            backend.available = True
            backend.connected = True
            manager = BluetoothManager(store, backend=backend, bossdac_audio_device="plughw:1,0")

            audio_device = manager.prepare_for_playback(datetime(2026, 5, 25, 8, 0))

            self.assertEqual(audio_device, "pulse/bluez_output.AA_BB_CC_DD_EE_FF.1")
            self.assertEqual(manager.state.active_sink, "bluetooth")
            self.assertEqual(
                store.get_app_state_value("preferred_bluetooth_device_mac"),
                "AA:BB:CC:DD:EE:FF",
            )

    def test_prepare_for_playback_uses_saved_connected_preferred_device(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = StateStore(Path(tmp) / "test.sqlite")
            store.set_app_state_value("preferred_bluetooth_device_name", "Ben's Headphones")
            store.set_app_state_value("preferred_bluetooth_device_mac", "AA:BB:CC:DD:EE:FF")
            backend = FakeBluetoothBackend()
            backend.available = True
            backend.connected = True
            manager = BluetoothManager(store, backend=backend, bossdac_audio_device="plughw:1,0")

            audio_device = manager.prepare_for_playback(datetime(2026, 5, 25, 8, 0))

            self.assertEqual(audio_device, "pulse/bluez_output.AA_BB_CC_DD_EE_FF.1")
            self.assertEqual(manager.state.active_sink, "bluetooth")
            self.assertEqual(backend.connect_calls, 0)
            self.assertEqual(backend.default_sink, "pulse/bluez_output.AA_BB_CC_DD_EE_FF.1")

    def test_playback_start_adopts_existing_connected_known_device(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = StateStore(root / "test.sqlite")
            library = MediaLibrary(root / "media", store)
            library.ensure_demo_library()
            backend = KnownDeviceBluetoothBackend()
            backend.available = True
            backend.connected = True
            player = DeviceSpyPlayer()
            controller = NightstandController(
                store=store,
                library=library,
                player=player,
                display=MemoryDisplay(),
                bluetooth_backend=backend,
                bossdac_audio_device="plughw:1,0",
            )

            controller.handle_event(InputEvent("source", "button-1"))

            self.assertEqual(player.audio_devices[-1], "pulse/bluez_output.AA_BB_CC_DD_EE_FF.1")
            self.assertEqual(controller.bluetooth.state.active_sink, "bluetooth")

    def test_playback_start_uses_saved_connected_preferred_device(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = StateStore(root / "test.sqlite")
            store.set_app_state_value("preferred_bluetooth_device_name", "Ben's Headphones")
            store.set_app_state_value("preferred_bluetooth_device_mac", "AA:BB:CC:DD:EE:FF")
            library = MediaLibrary(root / "media", store)
            library.ensure_demo_library()
            backend = FakeBluetoothBackend()
            backend.available = True
            backend.connected = True
            player = DeviceSpyPlayer()
            controller = NightstandController(
                store=store,
                library=library,
                player=player,
                display=MemoryDisplay(),
                bluetooth_backend=backend,
                bossdac_audio_device="plughw:1,0",
            )

            controller.handle_event(InputEvent("source", "button-1"))

            self.assertEqual(player.audio_devices[-1], "pulse/bluez_output.AA_BB_CC_DD_EE_FF.1")
            self.assertEqual(controller.bluetooth.state.active_sink, "bluetooth")
            self.assertEqual(backend.connect_calls, 0)

    def test_pairing_tick_refreshes_late_scan_results_and_press_pairs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = StateStore(root / "test.sqlite")
            library = MediaLibrary(root / "media", store)
            backend = FakeBluetoothBackend()
            late_devices = [
                BluetoothDevice("11:22:33:44:55:66", "Other Device"),
                BluetoothDevice("AA:BB:CC:DD:EE:FF", "Ben's Headphones"),
            ]
            backend.devices = []
            backend.available = False
            controller = NightstandController(
                store=store,
                library=library,
                player=MockPlayer(),
                display=MemoryDisplay(),
                bluetooth_backend=backend,
            )

            controller.bluetooth.begin_pairing(datetime(2026, 5, 25, 8, 0))
            controller._open_bluetooth_pairing_menu()
            self.assertEqual(controller.nav.current_mode, UIMode.BLUETOOTH_PAIRING)
            self.assertEqual(controller.nav.current_menu, [])

            backend.available = True
            backend.devices = late_devices
            controller.tick(datetime(2026, 5, 25, 8, 0, 6))
            controller.handle_event(InputEvent("turn", 1))
            self.assertEqual(controller.nav.selected_index, 1)
            self.assertEqual(controller.nav.current_menu[1].label, "Ben's Headphones")

            controller.handle_event(InputEvent("press"))
            self._wait_for_pairing_result(controller, datetime(2026, 5, 25, 8, 0, 7))

            self.assertTrue(controller.bluetooth.state.connected)
            self.assertEqual(
                store.get_app_state_value("preferred_bluetooth_device_mac"),
                "AA:BB:CC:DD:EE:FF",
            )

    def test_async_pairing_persists_preferred_device_before_audio_profile_connects(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = StateStore(root / "test.sqlite")
            library = MediaLibrary(root / "media", store)
            backend = PairedWithoutAudioBackend()
            controller = NightstandController(
                store=store,
                library=library,
                player=MockPlayer(),
                display=MemoryDisplay(),
                bluetooth_backend=backend,
            )

            controller.bluetooth.begin_pairing(datetime(2026, 5, 25, 8, 0))
            controller._open_bluetooth_pairing_menu()
            controller.handle_event(InputEvent("press"))
            self._wait_for_pairing_result(controller, datetime(2026, 5, 25, 8, 1))

            self.assertEqual(controller.nav.current_mode, UIMode.BLUETOOTH_PAIRING)
            self.assertEqual(controller.bluetooth.state.phase, PHASE_PAIRED)
            self.assertEqual(controller.bluetooth.state.active_sink, "dac")
            self.assertEqual(controller.bluetooth.state.last_message, "Paired: Ben's Headphones")
            self.assertEqual(
                store.get_app_state_value("preferred_bluetooth_device_mac"),
                "AA:BB:CC:DD:EE:FF",
            )

    def test_bluetooth_pairing_turn_does_not_rescan_devices(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = StateStore(root / "test.sqlite")
            library = MediaLibrary(root / "media", store)
            backend = FakeBluetoothBackend()
            controller = NightstandController(
                store=store,
                library=library,
                player=MockPlayer(),
                display=MemoryDisplay(),
                bluetooth_backend=backend,
            )

            controller.bluetooth.begin_pairing(datetime(2026, 5, 25, 8, 0))
            controller._open_bluetooth_pairing_menu()
            discover_calls = backend.discover_calls
            controller.handle_event(InputEvent("turn", 1))

            self.assertEqual(backend.discover_calls, discover_calls)

    def test_pairing_tick_does_not_redraw_when_device_list_is_unchanged(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = StateStore(root / "test.sqlite")
            library = MediaLibrary(root / "media", store)
            backend = FakeBluetoothBackend()
            controller = NightstandController(
                store=store,
                library=library,
                player=MockPlayer(),
                display=MemoryDisplay(),
                bluetooth_backend=backend,
            )
            now = datetime(2026, 5, 25, 8, 0)

            controller.bluetooth.begin_pairing(now)
            controller._open_bluetooth_pairing_menu()
            controller._dirty = False
            controller.tick(now + timedelta(seconds=6))

            self.assertFalse(controller._dirty)

    def test_bluetooth_pairing_screen_does_not_active_timeout_to_ambient(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = StateStore(root / "test.sqlite")
            library = MediaLibrary(root / "media", store)
            backend = FakeBluetoothBackend()
            controller = NightstandController(
                store=store,
                library=library,
                player=MockPlayer(),
                display=MemoryDisplay(),
                bluetooth_backend=backend,
                active_mode_timeout_seconds=30,
            )
            now = datetime(2026, 5, 25, 8, 0)

            controller._enter_active_mode("test")
            controller.bluetooth.begin_pairing(now)
            controller._open_bluetooth_pairing_menu()
            controller.last_active_interaction_at = now
            controller.tick(now + timedelta(seconds=60))

            self.assertEqual(controller.nav.current_mode, UIMode.BLUETOOTH_PAIRING)
            self.assertFalse(controller.is_ambient_mode_active)
            self.assertEqual(backend.stop_scan_calls, 0)

    def test_bluetooth_pairing_long_press_stops_scan_and_returns_home(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = StateStore(root / "test.sqlite")
            library = MediaLibrary(root / "media", store)
            backend = FakeBluetoothBackend()
            controller = NightstandController(
                store=store,
                library=library,
                player=MockPlayer(),
                display=MemoryDisplay(),
                bluetooth_backend=backend,
            )

            controller.bluetooth.begin_pairing(datetime(2026, 5, 25, 8, 0))
            controller._open_bluetooth_pairing_menu()
            controller.handle_event(InputEvent("long_press"))

            self.assertEqual(controller.nav.current_mode, UIMode.HOME)
            self.assertEqual(backend.stop_scan_calls, 1)
            self.assertEqual(controller.bluetooth.state.discovered_devices, [])

    def test_pairing_failure_stays_on_pairing_screen_for_retry(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = StateStore(root / "test.sqlite")
            library = MediaLibrary(root / "media", store)
            backend = FakeBluetoothBackend()
            backend.pair_result = False
            controller = NightstandController(
                store=store,
                library=library,
                player=MockPlayer(),
                display=MemoryDisplay(),
                bluetooth_backend=backend,
            )

            controller._enter_active_mode("test")
            controller.bluetooth.begin_pairing(datetime(2026, 5, 25, 8, 0))
            controller._open_bluetooth_pairing_menu()
            controller.handle_event(InputEvent("press"))
            self._wait_for_pairing_result(controller, datetime(2026, 5, 25, 8, 1))

            self.assertEqual(controller.nav.current_mode, UIMode.BLUETOOTH_PAIRING)
            self.assertEqual(controller.bluetooth.state.last_message, "Pairing Failed")
            self.assertTrue(controller.is_active_mode_active)
            self.assertFalse(controller.is_ambient_mode_active)

    def test_pairing_press_starts_backend_before_display_flush(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = StateStore(root / "test.sqlite")
            library = MediaLibrary(root / "media", store)
            backend = FakeBluetoothBackend()
            display = MemoryDisplay()
            seen_messages: list[str] = []
            backend.on_pair = lambda: seen_messages.append(
                display.last_state.bluetooth.last_message if display.last_state else ""
            )
            controller = NightstandController(
                store=store,
                library=library,
                player=MockPlayer(),
                display=display,
                bluetooth_backend=backend,
            )

            controller.bluetooth.begin_pairing(datetime(2026, 5, 25, 8, 0))
            controller._open_bluetooth_pairing_menu()
            controller.handle_event(InputEvent("press"))

            deadline = time.monotonic() + 1.0
            while not seen_messages and time.monotonic() < deadline:
                time.sleep(0.01)

            self.assertEqual(controller.bluetooth.state.last_message, "Pairing: Ben's Headphones")
            self.assertEqual(seen_messages, [""])

    def test_presence_monitor_reconnects_when_preferred_device_appears(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = StateStore(Path(tmp) / "test.sqlite")
            store.set_app_state_value("preferred_bluetooth_device_name", "Ben's Headphones")
            store.set_app_state_value("preferred_bluetooth_device_mac", "AA:BB:CC:DD:EE:FF")
            backend = FakeBluetoothBackend()
            manager = BluetoothManager(store, backend=backend)
            now = datetime(2026, 5, 25, 8, 0)

            backend.available = True
            changed = manager.tick(now)

            self.assertTrue(changed)
            self.assertTrue(manager.state.connected)
            self.assertEqual(manager.state.active_sink, "bluetooth")

    def test_presence_monitor_does_not_reconnect_when_preferred_device_is_not_visible(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = StateStore(Path(tmp) / "test.sqlite")
            store.set_app_state_value("preferred_bluetooth_device_name", "Ben's Headphones")
            store.set_app_state_value("preferred_bluetooth_device_mac", "AA:BB:CC:DD:EE:FF")
            backend = FakeBluetoothBackend()
            manager = BluetoothManager(store, backend=backend)

            changed = manager.tick(datetime(2026, 5, 25, 8, 0))

            self.assertFalse(changed)
            self.assertFalse(manager.state.reconnecting)
            self.assertEqual(backend.connect_calls, 0)

    def test_failed_auto_reconnect_enters_cooldown_until_manual_reconnect(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = StateStore(Path(tmp) / "test.sqlite")
            store.set_app_state_value("preferred_bluetooth_device_name", "Ben's Headphones")
            store.set_app_state_value("preferred_bluetooth_device_mac", "AA:BB:CC:DD:EE:FF")
            backend = FailingReconnectBackend()
            backend.available = True
            manager = BluetoothManager(
                store,
                backend=backend,
                reconnect_timeout_seconds=1,
                presence_interval_seconds=1,
                auto_reconnect_cooldown_seconds=60,
            )
            now = datetime(2026, 5, 25, 8, 0)

            self.assertTrue(manager.tick(now))
            self.assertEqual(backend.connect_calls, 1)
            self.assertTrue(manager.state.reconnecting)

            self.assertTrue(manager.tick(now + timedelta(seconds=2)))
            self.assertFalse(manager.state.reconnecting)
            self.assertEqual(manager.state.active_sink, "dac")

            self.assertFalse(manager.tick(now + timedelta(seconds=3)))
            self.assertEqual(backend.connect_calls, 1)

            manager.begin_reconnect(now + timedelta(seconds=4), manual=True)
            manager.tick(now + timedelta(seconds=4))

            self.assertEqual(backend.connect_calls, 2)

    def test_disconnect_falls_back_to_bossdac_without_clearing_preference(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = StateStore(Path(tmp) / "test.sqlite")
            store.set_app_state_value("preferred_bluetooth_device_name", "Ben's Headphones")
            store.set_app_state_value("preferred_bluetooth_device_mac", "AA:BB:CC:DD:EE:FF")
            backend = FakeBluetoothBackend()
            manager = BluetoothManager(store, backend=backend)
            backend.available = True
            manager.tick(datetime(2026, 5, 25, 8, 0))

            backend.connected = False
            changed = manager.tick(datetime(2026, 5, 25, 8, 0, 20))

            self.assertTrue(changed)
            self.assertEqual(manager.state.active_sink, "dac")
            self.assertEqual(
                store.get_app_state_value("preferred_bluetooth_device_mac"),
                "AA:BB:CC:DD:EE:FF",
            )

    def test_triple_clicking_any_source_button_starts_reconnect_workflow(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = StateStore(root / "test.sqlite")
            store.set_app_state_value("preferred_bluetooth_device_name", "Ben's Headphones")
            store.set_app_state_value("preferred_bluetooth_device_mac", "AA:BB:CC:DD:EE:FF")
            library = MediaLibrary(root / "media", store)
            library.ensure_demo_library()
            controller = NightstandController(
                store=store,
                library=library,
                player=MockPlayer(),
                display=MemoryDisplay(),
            )

            controller.handle_event(InputEvent("source", "button-1"))
            controller.handle_event(InputEvent("source", "button-2"))
            controller.handle_event(InputEvent("source", "button-3"))

            self.assertTrue(controller.bluetooth.state.reconnecting)
            self.assertIn("Searching", controller.bluetooth.state.last_message)

    def test_playback_uses_bossdac_until_bluetooth_connects_then_switches(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = StateStore(root / "test.sqlite")
            store.set_app_state_value("preferred_bluetooth_device_name", "Ben's Headphones")
            store.set_app_state_value("preferred_bluetooth_device_mac", "AA:BB:CC:DD:EE:FF")
            library = MediaLibrary(root / "media", store)
            library.ensure_demo_library()
            backend = FakeBluetoothBackend()
            player = DeviceSpyPlayer()
            controller = NightstandController(
                store=store,
                library=library,
                player=player,
                display=MemoryDisplay(),
                bluetooth_backend=backend,
                bossdac_audio_device="plughw:1,0",
            )

            controller.handle_event(InputEvent("source", "button-1"))
            self.assertEqual(player.audio_devices[-1], "plughw:1,0")

            backend.available = True
            controller.tick(datetime.now() + timedelta(seconds=20))

            self.assertEqual(player.audio_devices[-1], "pulse/bluez_output.AA_BB_CC_DD_EE_FF.1")

    def test_paused_playback_reapplies_bluetooth_route_before_resume(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = StateStore(root / "test.sqlite")
            library = MediaLibrary(root / "media", store)
            library.ensure_demo_library()
            backend = FakeBluetoothBackend()
            player = DeviceSpyPlayer()
            controller = NightstandController(
                store=store,
                library=library,
                player=player,
                display=MemoryDisplay(),
                bluetooth_backend=backend,
                bossdac_audio_device="plughw:1,0",
            )

            controller.handle_event(InputEvent("source", "button-1"))
            controller.handle_event(InputEvent("source", "button-1"))
            self.assertEqual(controller.player.status().state.value, "paused")

            player.audio_devices.clear()
            controller.bluetooth.fake_success()
            controller.toggle_play_pause_or_resume()

            self.assertEqual(player.audio_devices[-1], "pulse/bluez_output.AA_BB_CC_DD_EE_FF.1")
            self.assertEqual(controller.player.status().state.value, "playing")

    def test_bluetooth_media_commands_use_playback_logic_without_menu_navigation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = StateStore(root / "test.sqlite")
            library = MediaLibrary(root / "media", store)
            library.ensure_demo_library()
            controller = NightstandController(
                store=store,
                library=library,
                player=MockPlayer(),
                display=MemoryDisplay(),
            )
            controller.handle_event(InputEvent("source", "button-1"))
            controller.handle_event(InputEvent("long_press"))
            self.assertEqual(controller.nav.current_mode, UIMode.MENU)

            controller.handle_event(InputEvent("media_command", MediaCommand.NEXT_TRACK))
            self.assertEqual(controller.player.status().title, "Slot 1 Episode 002")
            self.assertEqual(controller.nav.current_mode, UIMode.MENU)

            controller.player.status().position_seconds = 10
            controller.handle_event(InputEvent("media_command", MediaCommand.PREVIOUS_TRACK))
            self.assertLess(controller.player.status().position_seconds, 1)
            self.assertEqual(controller.nav.current_mode, UIMode.MENU)

            controller.handle_event(InputEvent("media_command", MediaCommand.PLAY_PAUSE))
            self.assertEqual(controller.player.status().state.value, "paused")
            self.assertEqual(controller.nav.current_mode, UIMode.MENU)


if __name__ == "__main__":
    unittest.main()
