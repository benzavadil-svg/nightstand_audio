from __future__ import annotations

import re
import os
import queue
import shutil
import subprocess
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Protocol

from app.models import DEFAULT_BLUETOOTH_DEVICE_NAME, BluetoothDevice, BluetoothRuntimeState
from app.services.logger import get_logger
from app.state_store import StateStore


PHASE_UNPAIRED = "UNPAIRED"
PHASE_PAIRING = "PAIRING"
PHASE_PAIRED = "PAIRED"
PHASE_CONNECTED = "CONNECTED"
PHASE_DISCONNECTED = "DISCONNECTED"
PHASE_RECONNECTING = "RECONNECTING"
PHASE_FALLBACK = "BOSSDAC_FALLBACK"


@dataclass(frozen=True)
class BluetoothPairingResult:
    device: BluetoothDevice
    paired: bool
    trusted: bool = False
    connected: bool = False
    connect_failed_profile_unavailable: bool = False
    error: str = ""


class BluetoothBackend(Protocol):
    def enable(self) -> None:
        ...

    def start_scan(self) -> None:
        ...

    def stop_scan(self) -> None:
        ...

    def discover_devices(self, preferred_name: str = "") -> list[BluetoothDevice]:
        ...

    def pair_trust_connect(self, device: BluetoothDevice) -> BluetoothPairingResult:
        ...

    def trust(self, mac: str) -> bool:
        ...

    def connect(self, mac: str) -> bool:
        ...

    def disconnect(self, mac: str) -> bool:
        ...

    def is_connected(self, mac: str) -> bool:
        ...

    def is_available(self, mac: str) -> bool:
        ...

    def bluetooth_audio_device(self, mac: str, name: str) -> str:
        ...

    def switch_to_bluetooth(self, audio_device: str) -> bool:
        ...

    def switch_to_bossdac(self) -> bool:
        ...


class SubprocessBluetoothBackend:
    """BlueZ/PipeWire subprocess backend for the Raspberry Pi appliance path."""

    def __init__(self, timeout_seconds: float = 4.0) -> None:
        self.timeout_seconds = timeout_seconds
        self.log = get_logger("BT")
        self._known_devices: dict[str, BluetoothDevice] = {}
        self._wpctl_sink_ids: dict[str, str] = {}
        self._last_empty_discovery_signature = ""
        self._rfkill_permission_warning_logged = False
        self._pipewire_bluetooth_support_checked = False

    def enable(self) -> None:
        self._rfkill_unblock()
        power_output = self._bluetoothctl("power", "on")
        self._bluetoothctl("pairable", "on")
        if not self._wait_for_adapter_ready(power_output=power_output):
            self.log.warning(
                "Bluetooth adapter unavailable after power on status=%s power_output=%s",
                _bluetooth_show_summary(self._bluetoothctl("show")),
                _compact_output(power_output),
            )

    def start_scan(self) -> None:
        self.enable()
        output = self._bluetoothctl("scan", "on")
        if _bluetooth_not_ready(output):
            self.log.warning("Bluetooth scan requested before adapter was ready; retrying.")
            self._wait_for_adapter_ready(force_power=True)
            self._bluetoothctl("scan", "on")

    def stop_scan(self) -> None:
        self._bluetoothctl("scan", "off")

    def discover_devices(self, preferred_name: str = "") -> list[BluetoothDevice]:
        devices_by_mac: dict[str, BluetoothDevice] = {}
        diagnostics: list[str] = []
        show_output = self._bluetoothctl("show")
        diagnostics.append(f"bluetoothctl show={_bluetooth_show_summary(show_output)}")
        if not _adapter_powered(show_output):
            self.enable()
            show_output = self._bluetoothctl("show")
            diagnostics.append(
                f"bluetoothctl show after enable={_bluetooth_show_summary(show_output)}"
            )
        for args in (
            ("devices",),
            ("devices", "Paired"),
            ("devices", "Trusted"),
            ("devices", "Connected"),
        ):
            output = self._bluetoothctl(*args)
            diagnostics.append(f"bluetoothctl {' '.join(args)}={_compact_output(output)}")
            devices_by_mac.update(_devices_by_mac(_parse_bluetooth_devices(output)))
        used_scan_sample = False
        if not devices_by_mac:
            # Some BlueZ builds show newly discovered devices in scan output before
            # they appear in the cached `bluetoothctl devices` list.
            output = self._bluetoothctl_timeout(5, "scan", "on")
            if _bluetooth_not_ready(output):
                self.log.warning("Bluetooth discovery scan returned NotReady; powering adapter and retrying.")
                self._wait_for_adapter_ready(force_power=True)
                output = self._bluetoothctl_timeout(5, "scan", "on")
            diagnostics.append(f"bluetoothctl scan={_compact_output(output)}")
            devices_by_mac.update(_devices_by_mac(_parse_bluetooth_devices(output)))
            post_scan_output = self._bluetoothctl("devices")
            diagnostics.append(f"bluetoothctl devices after scan={_compact_output(post_scan_output)}")
            devices_by_mac.update(_devices_by_mac(_parse_bluetooth_devices(post_scan_output)))
            btmgmt_output = self._btmgmt_find(5)
            if btmgmt_output:
                diagnostics.append(f"btmgmt find={_compact_output(btmgmt_output)}")
            devices_by_mac.update(_devices_by_mac(_parse_btmgmt_devices(btmgmt_output)))
            used_scan_sample = True
        if devices_by_mac:
            self._known_devices.update(devices_by_mac)
            self._last_empty_discovery_signature = ""
        else:
            self._log_empty_discovery_diagnostics(diagnostics)
        devices = sorted(self._known_devices.values(), key=lambda device: device.name.lower())
        self.log.info(
            "Bluetooth discovery devices=%s scan_sample=%s preferred_hint=%s names=%s",
            len(devices),
            used_scan_sample,
            preferred_name or "",
            ",".join(device.name for device in devices) or "-",
        )
        if preferred_name:
            preferred = preferred_name.lower()
            matching = [device for device in devices if preferred in device.name.lower()]
            return matching or devices
        return devices

    def known_devices(self) -> list[BluetoothDevice]:
        devices_by_mac: dict[str, BluetoothDevice] = {}
        for args in (
            ("devices", "Paired"),
            ("devices", "Trusted"),
            ("devices", "Connected"),
        ):
            output = self._bluetoothctl(*args)
            devices_by_mac.update(_devices_by_mac(_parse_bluetooth_devices(output)))
        devices = sorted(devices_by_mac.values(), key=lambda device: device.name.lower())
        self.log.info(
            "Bluetooth known devices=%s names=%s",
            len(devices),
            ",".join(device.name for device in devices) or "-",
        )
        return devices

    def pair_trust_connect(self, device: BluetoothDevice) -> BluetoothPairingResult:
        self.enable()
        if self._is_paired(device.mac):
            self.log.info(
                "Using existing Bluetooth pairing before explicit app pairing device=%s mac=%s",
                device.name,
                device.mac,
            )
            return self._trust_connect_paired_device(device)
        device = self._freshen_pairing_device(device)
        pair_output = ""
        paired = False
        connected_during_pair = False
        for attempt in range(1, 3):
            if attempt > 1:
                self.log.info(
                    "Retrying Bluetooth pairing after clean device remove device=%s attempt=%s",
                    device.name,
                    attempt,
                )
                self._bluetoothctl("remove", device.mac)
                time.sleep(1)
                device = self._freshen_pairing_device(device)
            pair_output = self._pair_device(device.mac)
            if _device_not_available(pair_output):
                fresh_device = self._freshen_pairing_device(device)
                if fresh_device.mac != device.mac:
                    self.log.info(
                        "Bluetooth pairing re-resolved stale device old_mac=%s new_mac=%s name=%s",
                        device.mac,
                        fresh_device.mac,
                        fresh_device.name,
                    )
                    device = fresh_device
                    pair_output = self._pair_device(device.mac)
            paired = _pairing_succeeded(pair_output) or self._is_paired(device.mac)
            connected_during_pair = _connect_succeeded(pair_output)
            self.log.info(
                "Bluetooth pairing attempt complete device=%s attempt=%s paired=%s connected_during_pair=%s auth_failed=%s",
                device.name,
                attempt,
                paired,
                connected_during_pair,
                _authentication_failed(pair_output),
            )
            if paired:
                break
            if not _authentication_failed(pair_output):
                break
        if not paired:
            self.log.warning("Bluetooth pairing failed output=%s", _compact_output(pair_output, limit=700))
            return BluetoothPairingResult(
                device=device,
                paired=False,
                error=_compact_output(pair_output, limit=700),
            )
        trust_output = self._bluetoothctl("trust", device.mac)
        trusted = _trust_succeeded(trust_output)
        if not trusted:
            self.log.warning("Bluetooth trust failed output=%s", _compact_output(trust_output, limit=500))
        if connected_during_pair or self.is_connected(device.mac):
            self.log.info("Bluetooth device already connected after pairing device=%s", device.name)
            return BluetoothPairingResult(
                device=device,
                paired=True,
                trusted=trusted,
                connected=True,
            )
        connect_output = self._bluetoothctl_timeout(30, "connect", device.mac)
        connected = self.is_connected(device.mac) or _connect_succeeded(connect_output)
        if not connected:
            self.log.warning("Bluetooth connect failed output=%s", _compact_output(connect_output, limit=500))
        profile_unavailable = _profile_unavailable(connect_output)
        if profile_unavailable:
            self.log.warning(
                "Bluetooth paired but audio profile is not ready yet device=%s",
                device.name,
            )
        return BluetoothPairingResult(
            device=device,
            paired=True,
            trusted=trusted,
            connected=connected,
            connect_failed_profile_unavailable=profile_unavailable,
            error="" if connected else _compact_output(connect_output, limit=700),
        )

    def _trust_connect_paired_device(self, device: BluetoothDevice) -> BluetoothPairingResult:
        trust_output = self._bluetoothctl("trust", device.mac)
        trusted = _trust_succeeded(trust_output)
        if not trusted:
            self.log.warning("Bluetooth trust failed output=%s", _compact_output(trust_output, limit=500))
        if self.is_connected(device.mac):
            self.log.info("Bluetooth paired device already connected device=%s", device.name)
            return BluetoothPairingResult(
                device=device,
                paired=True,
                trusted=trusted,
                connected=True,
            )
        connect_output = self._bluetoothctl_timeout(30, "connect", device.mac)
        connected = self.is_connected(device.mac) or _connect_succeeded(connect_output)
        if not connected:
            self.log.warning("Bluetooth connect failed output=%s", _compact_output(connect_output, limit=500))
        profile_unavailable = _profile_unavailable(connect_output)
        if profile_unavailable:
            self.log.warning(
                "Bluetooth paired but audio profile is not ready yet device=%s",
                device.name,
            )
        return BluetoothPairingResult(
            device=device,
            paired=True,
            trusted=trusted,
            connected=connected,
            connect_failed_profile_unavailable=profile_unavailable,
            error="" if connected else _compact_output(connect_output, limit=700),
        )

    def _freshen_pairing_device(self, device: BluetoothDevice) -> BluetoothDevice:
        scan_output = self._bluetoothctl_timeout(15, "scan", "on")
        devices = _parse_bluetooth_devices(scan_output)
        matched = _match_pairing_device(device, devices)
        if matched and matched.mac != device.mac:
            self.log.info(
                "Bluetooth pairing selected fresh discovered device name=%s old_mac=%s new_mac=%s",
                matched.name,
                device.mac,
                matched.mac,
            )
        return matched or device

    def _pair_device(self, mac: str) -> str:
        output = self._bluetoothctl_pairing_session(mac)
        if output.strip():
            return output
        output = self._bluetoothctl_timeout(45, "pair", mac)
        if output.strip():
            return output
        return self._interactive_pair_device(mac)

    def _interactive_pair_device(self, mac: str) -> str:
        return self._bluetoothctl_interactive(
            [
                (0.2, "power on"),
                (0.2, "pairable on"),
                (8.0, "scan on"),
                (45.0, f"pair {mac}"),
                (1.0, "scan off"),
            ],
            timeout_seconds=55,
        )

    def trust(self, mac: str) -> bool:
        output = self._bluetoothctl("trust", mac)
        return "succeeded" in output.lower() or "trust" in output.lower()

    def connect(self, mac: str) -> bool:
        output = self._bluetoothctl_reconnect_session(mac)
        if not output.strip():
            output = self._bluetoothctl_timeout(15, "connect", mac)
        return "successful" in output.lower() or self.is_connected(mac)

    def disconnect(self, mac: str) -> bool:
        output = self._bluetoothctl("disconnect", mac)
        lowered = output.lower()
        return (
            "successful" in lowered
            or "not connected" in lowered
            or not self.is_connected(mac)
        )

    def is_connected(self, mac: str) -> bool:
        output = self._bluetoothctl("info", mac)
        return bool(re.search(r"Connected:\s+yes", output, re.IGNORECASE))

    def _is_paired(self, mac: str) -> bool:
        output = self._bluetoothctl("info", mac)
        return bool(re.search(r"Paired:\s+yes", output, re.IGNORECASE))

    def is_available(self, mac: str) -> bool:
        if self.is_connected(mac):
            return True
        output = self._bluetoothctl("info", mac)
        lowered = output.lower()
        if not output.strip() or "not available" in lowered:
            return False
        if re.search(r"\bRSSI:\s+", output, re.IGNORECASE):
            return True
        if re.search(r"ServicesResolved:\s+yes", output, re.IGNORECASE):
            return True
        scan_output = self._bluetoothctl_timeout(4, "scan", "on")
        return any(device.mac.upper() == mac.upper() for device in _parse_bluetooth_devices(scan_output))

    def bluetooth_audio_device(self, mac: str, name: str) -> str:
        sinks = self._pactl("list", "short", "sinks")
        normalized_mac = mac.lower().replace(":", "_")
        for line in sinks.splitlines():
            lowered = line.lower()
            if "bluez" not in lowered:
                continue
            if normalized_mac in lowered or name.lower().replace(" ", "_") in lowered:
                parts = line.split()
                if len(parts) >= 2:
                    return f"pulse/{parts[1]}"
        for line in sinks.splitlines():
            lowered = line.lower()
            if "bluez" in lowered:
                parts = line.split()
                if len(parts) >= 2:
                    return f"pulse/{parts[1]}"
        audio_device = self._bluetooth_audio_device_from_wpctl(mac, name)
        if audio_device:
            return audio_device
        self.log.warning(
            "Bluetooth device connected but no audio sink found mac=%s name=%s",
            mac,
            name,
        )
        self._log_pipewire_bluetooth_support_hint()
        return ""

    def switch_to_bluetooth(self, audio_device: str) -> bool:
        if not audio_device:
            return False
        sink = audio_device.split("/", 1)[1] if "/" in audio_device else audio_device
        if shutil.which("pactl"):
            result = self._run(["pactl", "set-default-sink", sink])
            return result.returncode == 0
        if shutil.which("wpctl"):
            wpctl_id = self._wpctl_sink_ids.get(audio_device) or self._wpctl_sink_ids.get(sink)
            result = self._run(["wpctl", "set-default", wpctl_id or sink])
            return result.returncode == 0
        return False

    def _bluetooth_audio_device_from_wpctl(self, mac: str, name: str) -> str:
        output = self._wpctl("status")
        if not output.strip():
            return ""
        normalized_mac = mac.lower().replace(":", "_")
        normalized_name = _normalize_label(name)
        fallback: tuple[str, str, str] | None = None
        for sink_id, sink_label in _parse_wpctl_sinks(output):
            inspect_output = self._wpctl("inspect", sink_id)
            node_name = _wpctl_inspect_value(inspect_output, "node.name")
            candidate_name = node_name or sink_label
            lowered = candidate_name.lower()
            label_matches = normalized_name and normalized_name in _normalize_label(sink_label)
            inspect_matches = (
                "bluez" in inspect_output.lower()
                or normalized_mac in inspect_output.lower()
                or label_matches
            )
            if "bluez" in lowered or inspect_matches:
                fallback = fallback or (sink_id, sink_label, candidate_name)
            if normalized_mac in lowered or label_matches or inspect_matches:
                audio_device = f"pulse/{candidate_name}"
                self._wpctl_sink_ids[audio_device] = sink_id
                self._wpctl_sink_ids[candidate_name] = sink_id
                self.log.info(
                    "Bluetooth audio sink resolved via wpctl sink=%s label=%s id=%s",
                    candidate_name,
                    sink_label,
                    sink_id,
                )
                return audio_device
        if fallback:
            sink_id, sink_label, candidate_name = fallback
            audio_device = f"pulse/{candidate_name}"
            self._wpctl_sink_ids[audio_device] = sink_id
            self._wpctl_sink_ids[candidate_name] = sink_id
            self.log.info(
                "Bluetooth audio sink resolved via wpctl fallback sink=%s label=%s id=%s",
                candidate_name,
                sink_label,
                sink_id,
            )
            return audio_device
        self.log.info("Bluetooth wpctl sink diagnostics status=%s", _compact_output(output, limit=900))
        return ""

    def _log_pipewire_bluetooth_support_hint(self) -> None:
        if self._pipewire_bluetooth_support_checked:
            return
        self._pipewire_bluetooth_support_checked = True
        result = self._run(["dpkg-query", "-W", "-f=${Status}", "libspa-0.2-bluetooth"])
        if result.returncode == 0 and "install ok installed" in result.stdout:
            return
        self.log.warning(
            "PipeWire Bluetooth audio support is missing or not installed; install with "
            "`sudo apt install libspa-0.2-bluetooth`, then restart bluetooth/pipewire or reboot."
        )

    def switch_to_bossdac(self) -> bool:
        return True

    def _bluetoothctl(self, *args: str) -> str:
        if not shutil.which("bluetoothctl"):
            return ""
        result = self._run(["bluetoothctl", *args])
        return "\n".join(part for part in (result.stdout, result.stderr) if part)

    def _bluetoothctl_timeout(self, timeout_seconds: int, *args: str) -> str:
        if not shutil.which("bluetoothctl"):
            return ""
        result = self._run(
            ["bluetoothctl", "--timeout", str(timeout_seconds), *args],
            timeout_seconds=timeout_seconds + 1,
        )
        return "\n".join(part for part in (result.stdout, result.stderr) if part)

    def _bluetoothctl_session(
        self,
        commands: list[str],
        timeout_seconds: float,
    ) -> str:
        if not shutil.which("bluetoothctl"):
            return ""
        input_text = "\n".join(commands) + "\n"
        result = self._run(
            ["bluetoothctl"],
            timeout_seconds=timeout_seconds,
            input_text=input_text,
        )
        return "\n".join(part for part in (result.stdout, result.stderr) if part)

    def _bluetoothctl_interactive(
        self,
        commands: list[tuple[float, str]],
        timeout_seconds: float,
    ) -> str:
        if not shutil.which("bluetoothctl"):
            return ""
        process = None
        try:
            process = subprocess.Popen(
                ["bluetoothctl"],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            assert process.stdin is not None
            for delay_seconds, command in commands:
                process.stdin.write(command + "\n")
                process.stdin.flush()
                time.sleep(max(0.0, delay_seconds))
            process.stdin.write("quit\n")
            process.stdin.flush()
            stdout, stderr = process.communicate(timeout=timeout_seconds)
            return "\n".join(part for part in (stdout, stderr) if part)
        except subprocess.TimeoutExpired:
            if process is not None:
                process.kill()
                stdout, stderr = process.communicate()
                self.log.warning("Bluetooth interactive pairing command timed out mac command=bluetoothctl")
                return "\n".join(part for part in (stdout, stderr) if part)
            return ""
        except OSError as exc:
            self.log.warning("Bluetooth interactive pairing command failed error=%s", exc)
            return str(exc)

    def _bluetoothctl_pairing_session(
        self,
        mac: str,
        timeout_seconds: float = 90.0,
        scan_wait_seconds: float = 18.0,
    ) -> str:
        """Pair in one bluetoothctl session so discovery, agent, and pair overlap.

        Some earbuds only remain pairable while discovery is active, and some
        BlueZ agents ask for passkey confirmation even for headphone pairing.
        Keeping one process alive avoids losing the agent or the discovered
        device between short-lived bluetoothctl subprocess calls.
        """
        if not shutil.which("bluetoothctl"):
            return ""
        process: subprocess.Popen[str] | None = None
        output_queue: queue.Queue[str] = queue.Queue()
        output: list[str] = []

        def read_stream(stream) -> None:
            try:
                while True:
                    try:
                        line = stream.readline()
                    except (OSError, ValueError):
                        break
                    if line == "":
                        break
                    output_queue.put(line)
            finally:
                try:
                    stream.close()
                except (OSError, ValueError):
                    pass

        def send(command: str) -> bool:
            if process is None or process.stdin is None:
                return False
            try:
                process.stdin.write(command + "\n")
                process.stdin.flush()
                return True
            except (OSError, ValueError) as exc:
                output.append(f"bluetoothctl stdin closed while sending {command}: {exc}\n")
                return False

        def drain_pending_output() -> None:
            while True:
                try:
                    output.append(output_queue.get_nowait())
                except queue.Empty:
                    return

        try:
            process = subprocess.Popen(
                ["bluetoothctl"],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,
            )
            assert process.stdout is not None
            assert process.stderr is not None
            threading.Thread(target=read_stream, args=(process.stdout,), daemon=True).start()
            threading.Thread(target=read_stream, args=(process.stderr,), daemon=True).start()

            for command in (
                "power on",
                "agent NoInputNoOutput",
                "default-agent",
                "pairable on",
                "scan on",
            ):
                send(command)
                time.sleep(0.2)

            deadline = time.monotonic() + timeout_seconds
            scan_deadline = time.monotonic() + scan_wait_seconds
            pair_sent = False
            done = False
            while time.monotonic() < deadline and not done:
                if not pair_sent and time.monotonic() >= scan_deadline:
                    self.log.info(
                        "Bluetooth pairing scan wait elapsed; attempting pair anyway mac=%s",
                        mac,
                    )
                    send(f"pair {mac}")
                    pair_sent = True
                try:
                    line = output_queue.get(timeout=0.25)
                except queue.Empty:
                    if process.poll() is not None:
                        break
                    continue
                output.append(line)
                lowered = line.lower()
                if not pair_sent and mac.lower() in lowered:
                    self.log.info("Bluetooth pairing target discovered during scan mac=%s", mac)
                    send(f"pair {mac}")
                    pair_sent = True
                    continue
                if "confirm passkey" in lowered and "(yes/no)" in lowered:
                    self.log.info("Bluetooth pairing passkey confirmation accepted mac=%s", mac)
                    send("yes")
                    continue
                if "pairing successful" in lowered:
                    done = True
                    continue
                if "failed to pair" in lowered:
                    done = True
                    continue

            send("scan off")
            send("quit")
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
            drain_pending_output()
            return "".join(output)
        except OSError as exc:
            self.log.warning("Bluetooth pairing session failed error=%s", exc)
            return str(exc)

    def _bluetoothctl_reconnect_session(
        self,
        mac: str,
        timeout_seconds: float = 25.0,
        scan_wait_seconds: float = 12.0,
    ) -> str:
        """Reconnect in one bluetoothctl session while discovery is active."""
        if not shutil.which("bluetoothctl"):
            return ""
        process: subprocess.Popen[str] | None = None
        output_queue: queue.Queue[str] = queue.Queue()
        output: list[str] = []

        def read_stream(stream) -> None:
            try:
                while True:
                    try:
                        line = stream.readline()
                    except (OSError, ValueError):
                        break
                    if line == "":
                        break
                    output_queue.put(line)
            finally:
                try:
                    stream.close()
                except (OSError, ValueError):
                    pass

        def send(command: str) -> None:
            if process is None or process.stdin is None:
                return
            try:
                process.stdin.write(command + "\n")
                process.stdin.flush()
            except (OSError, ValueError) as exc:
                output.append(f"bluetoothctl stdin closed while sending {command}: {exc}\n")

        def drain_pending_output() -> None:
            while True:
                try:
                    output.append(output_queue.get_nowait())
                except queue.Empty:
                    return

        try:
            process = subprocess.Popen(
                ["bluetoothctl"],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,
            )
            assert process.stdout is not None
            assert process.stderr is not None
            threading.Thread(target=read_stream, args=(process.stdout,), daemon=True).start()
            threading.Thread(target=read_stream, args=(process.stderr,), daemon=True).start()

            for command in (
                "power on",
                f"trust {mac}",
                "scan on",
            ):
                send(command)
                time.sleep(0.2)

            deadline = time.monotonic() + timeout_seconds
            scan_deadline = time.monotonic() + scan_wait_seconds
            connect_sent = False
            done = False
            while time.monotonic() < deadline and not done:
                if not connect_sent and time.monotonic() >= scan_deadline:
                    self.log.info(
                        "Bluetooth reconnect scan wait elapsed; attempting connect anyway mac=%s",
                        mac,
                    )
                    send(f"connect {mac}")
                    connect_sent = True
                try:
                    line = output_queue.get(timeout=0.25)
                except queue.Empty:
                    if process.poll() is not None:
                        break
                    continue
                output.append(line)
                lowered = line.lower()
                if not connect_sent and mac.lower() in lowered:
                    self.log.info("Bluetooth reconnect target discovered during scan mac=%s", mac)
                    send(f"connect {mac}")
                    connect_sent = True
                    continue
                if "connection successful" in lowered or "connected: yes" in lowered:
                    done = True
                    continue
                if "failed to connect" in lowered:
                    done = True
                    continue

            send("scan off")
            send("quit")
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
            drain_pending_output()
            return "".join(output)
        except OSError as exc:
            self.log.warning("Bluetooth reconnect session failed error=%s", exc)
            output.append(str(exc))
            return "".join(output)

    def _btmgmt_find(self, timeout_seconds: int) -> str:
        if not self._can_use_btmgmt():
            return ""
        if not shutil.which("btmgmt"):
            return ""
        result = self._run(["btmgmt", "find"], timeout_seconds=timeout_seconds)
        return "\n".join(part for part in (result.stdout, result.stderr) if part)

    def _can_use_btmgmt(self) -> bool:
        return hasattr(os, "geteuid") and os.geteuid() == 0

    def _rfkill_unblock(self) -> None:
        if not shutil.which("rfkill"):
            return
        result = self._run(["rfkill", "unblock", "bluetooth"])
        if result.returncode != 0:
            error = _compact_output(result.stderr)
            if not self._rfkill_permission_warning_logged:
                self.log.warning(
                    "Bluetooth rfkill unblock failed error=%s fix='sudo rfkill unblock bluetooth'",
                    error,
                )
                self._rfkill_permission_warning_logged = True

    def _wait_for_adapter_ready(
        self,
        force_power: bool = False,
        power_output: str = "",
    ) -> bool:
        for attempt in range(6):
            if force_power or attempt == 0:
                power_output = self._bluetoothctl("power", "on")
            show_output = self._bluetoothctl("show")
            if _adapter_powered(show_output):
                if attempt:
                    self.log.info("Bluetooth adapter ready after attempts=%s", attempt + 1)
                return True
            if attempt == 0:
                self.log.warning(
                    "Bluetooth adapter not ready status=%s power_output=%s",
                    _bluetooth_show_summary(show_output),
                    _compact_output(power_output),
                )
            time.sleep(0.5)
        show_output = self._bluetoothctl("show")
        self.log.warning(
            "Bluetooth adapter still not ready status=%s",
            _bluetooth_show_summary(show_output),
        )
        return False

    def _pactl(self, *args: str) -> str:
        if not shutil.which("pactl"):
            return ""
        result = self._run(["pactl", *args])
        return "\n".join(part for part in (result.stdout, result.stderr) if part)

    def _wpctl(self, *args: str) -> str:
        if not shutil.which("wpctl"):
            return ""
        result = self._run(["wpctl", *args])
        return "\n".join(part for part in (result.stdout, result.stderr) if part)

    def _run(
        self,
        command: list[str],
        timeout_seconds: float | None = None,
        input_text: str | None = None,
    ) -> subprocess.CompletedProcess[str]:
        try:
            return subprocess.run(
                command,
                check=False,
                capture_output=True,
                text=True,
                timeout=timeout_seconds or self.timeout_seconds,
                input=input_text,
            )
        except subprocess.TimeoutExpired as exc:
            stdout = _decode_timeout_output(exc.stdout)
            stderr = _decode_timeout_output(exc.stderr)
            self.log.warning("Bluetooth command timed out command=%s", command)
            return subprocess.CompletedProcess(command, 124, stdout=stdout, stderr=stderr)
        except OSError as exc:
            self.log.warning("Bluetooth command failed command=%s error=%s", command, exc)
            return subprocess.CompletedProcess(command, 1, stdout="", stderr=str(exc))

    def _log_empty_discovery_diagnostics(self, diagnostics: list[str]) -> None:
        signature = " | ".join(diagnostics)
        if signature == self._last_empty_discovery_signature:
            return
        self._last_empty_discovery_signature = signature
        self.log.info("Bluetooth discovery empty diagnostics %s", signature)


class BluetoothManager:
    """One-device Bluetooth headphone manager for Nightstand Audio."""

    def __init__(
        self,
        store: StateStore,
        trusted_device_name: str = DEFAULT_BLUETOOTH_DEVICE_NAME,
        reconnect_timeout_seconds: int = 30,
        *,
        backend: BluetoothBackend | None = None,
        bossdac_audio_device: str = "plughw:1,0",
        presence_interval_seconds: int = 15,
        auto_reconnect_cooldown_seconds: int = 120,
    ) -> None:
        self.store = store
        self.log = get_logger("BT")
        self.backend = backend or SubprocessBluetoothBackend()
        self.bossdac_audio_device = bossdac_audio_device
        self.presence_interval_seconds = presence_interval_seconds
        self.auto_reconnect_cooldown_seconds = auto_reconnect_cooldown_seconds
        self._last_presence_check_at: datetime | None = None
        self._last_pairing_scan_at: datetime | None = None
        self._next_reconnect_attempt_at: datetime | None = None
        self._auto_reconnect_blocked_until: datetime | None = None
        self._audio_sink_missing_since: datetime | None = None
        self._next_audio_sink_recovery_at: datetime | None = None
        self._reconnect_attempts = 0
        self._pairing_lock = threading.Lock()
        self._pairing_thread: threading.Thread | None = None
        self._pairing_result: BluetoothPairingResult | None = None
        preferred_output = store.get_app_state_value("preferred_output") or "dac"
        preferred_name = (
            store.get_app_state_value("preferred_bluetooth_device_name")
            or trusted_device_name
        )
        preferred_mac = store.get_app_state_value("preferred_bluetooth_device_mac") or ""
        last_connected = _parse_iso_datetime(
            store.get_app_state_value("preferred_bluetooth_last_connected_at") or ""
        )
        phase = PHASE_DISCONNECTED if preferred_mac else PHASE_UNPAIRED
        self.state = BluetoothRuntimeState(
            trusted_device_name=preferred_name,
            preferred_output=preferred_output,
            active_sink="dac",
            reconnect_timeout_seconds=reconnect_timeout_seconds,
            phase=phase,
            preferred_device_name=preferred_name,
            preferred_device_mac=preferred_mac,
            last_successful_connection_at=last_connected,
        )
        self.log.info("Preferred device loaded name=%s", preferred_name)
        if preferred_mac:
            self.log.info("Preferred device loaded mac=%s", preferred_mac)
        self.log.info("Current sink selected sink=%s preferred=%s", self.state.active_sink, preferred_output)

    def begin_pairing(self, now: datetime | None = None) -> None:
        now = now or datetime.now()
        self.state.phase = PHASE_PAIRING
        self.state.last_message = "Scanning..."
        self.state.selected_device_index = 0
        self._last_pairing_scan_at = now
        self.log.info("Bluetooth pairing started")
        self.backend.start_scan()
        self.refresh_pairing_devices()

    def refresh_pairing_devices(self) -> bool:
        if self.state.phase != PHASE_PAIRING:
            return False
        previous_devices = [(device.mac, device.name) for device in self.state.discovered_devices]
        previous_selected = self.state.selected_device_index
        previous_message = self.state.last_message
        devices = self.backend.discover_devices("")
        self.state.discovered_devices = devices
        if self.state.selected_device_index >= len(devices):
            self.state.selected_device_index = max(0, len(devices) - 1)
        if devices:
            selected = devices[self.state.selected_device_index]
            if previous_message != "Pairing Failed":
                self.state.last_message = f"Selected: {selected.name}"
        else:
            self.state.last_message = "Scanning..."
        return (
            previous_devices != [(device.mac, device.name) for device in devices]
            or previous_selected != self.state.selected_device_index
            or previous_message != self.state.last_message
        )

    def move_pairing_selection(self, delta: int) -> None:
        if not self.state.discovered_devices:
            return
        self.state.selected_device_index = (
            self.state.selected_device_index + delta
        ) % len(self.state.discovered_devices)
        selected = self.state.discovered_devices[self.state.selected_device_index]
        self.state.last_message = f"Selected: {selected.name}"

    def mark_pairing_in_progress(self) -> BluetoothDevice | None:
        if not self.state.discovered_devices:
            self.state.last_message = "No headphones found"
            return None
        device = self.state.discovered_devices[self.state.selected_device_index]
        self.state.last_message = f"Pairing: {device.name}"
        self.log.info("Bluetooth pairing UI state entered device=%s mac=%s", device.name, device.mac)
        return device

    def pair_selected_device(self, now: datetime | None = None) -> bool:
        if not self.state.discovered_devices:
            self.state.last_message = "No headphones found"
            return False
        device = self.state.discovered_devices[self.state.selected_device_index]
        self.log.info("Pairing selected device name=%s mac=%s", device.name, device.mac)
        result = _coerce_pairing_result(device, self.backend.pair_trust_connect(device))
        if not result.paired:
            self.state.last_message = "Pairing Failed"
            self.log.warning("Pairing failed device=%s", result.device.name)
            return False
        self._persist_preferred_device(result.device)
        self.backend.stop_scan()
        if result.connected:
            self._mark_connected(result.device.mac, result.device.name, now or datetime.now())
            self.state.last_message = f"Bluetooth Ready: {result.device.name}"
        else:
            self._mark_paired_without_audio(result, now or datetime.now())
        return True

    def start_pair_selected_device(self) -> bool:
        if self._pairing_in_progress():
            self.log.info("Bluetooth pairing already in progress")
            return True
        device = self.mark_pairing_in_progress()
        if not device:
            return False

        def worker(selected: BluetoothDevice) -> None:
            result = BluetoothPairingResult(device=selected, paired=False)
            try:
                self.log.info("Pairing selected device name=%s mac=%s", selected.name, selected.mac)
                result = _coerce_pairing_result(selected, self.backend.pair_trust_connect(selected))
            except Exception as exc:  # pragma: no cover - defensive hardware boundary
                result = BluetoothPairingResult(device=selected, paired=False, error=str(exc))
                self.log.warning("Bluetooth pairing worker failed device=%s error=%s", selected.name, exc)
            with self._pairing_lock:
                self._pairing_result = result

        with self._pairing_lock:
            self._pairing_result = None
        self._pairing_thread = threading.Thread(
            target=worker,
            args=(device,),
            name="nightstand-bluetooth-pair",
            daemon=True,
        )
        self._pairing_thread.start()
        return True

    def cancel_pairing(self, reason: str = "cancelled") -> None:
        if self.state.phase != PHASE_PAIRING:
            return
        if self._pairing_in_progress():
            self.log.info("Bluetooth pairing cancel requested while worker is active reason=%s", reason)
        self.backend.stop_scan()
        self.state.phase = PHASE_DISCONNECTED if self.state.preferred_device_mac else PHASE_UNPAIRED
        self.state.discovered_devices = []
        self.state.selected_device_index = 0
        self.state.last_message = "Pairing Cancelled"
        self.log.info("Bluetooth pairing stopped reason=%s", reason)

    def begin_reconnect(self, now: datetime | None = None, manual: bool = True) -> None:
        now = now or datetime.now()
        if not self.state.preferred_device_mac:
            if not self._adopt_existing_preferred_device(now):
                self.state.last_message = "Pair Headphones First"
                self.state.phase = PHASE_UNPAIRED
                return
        if not manual and self._auto_reconnect_cooldown_active(now):
            remaining = int((self._auto_reconnect_blocked_until - now).total_seconds())
            self.log.info(
                "Automatic Bluetooth reconnect cooldown active remaining_seconds=%s",
                remaining,
            )
            return
        if manual:
            self._auto_reconnect_blocked_until = None
        self.state.reconnecting = True
        self.state.reconnect_started_at = now
        self.state.phase = PHASE_RECONNECTING
        self.state.last_message = f"Searching: {self.state.preferred_device_name}"
        self._reconnect_attempts = 0
        self._next_reconnect_attempt_at = now
        self.log.info("Attempting reconnect mac=%s manual=%s", self.state.preferred_device_mac, manual)

    def prepare_for_playback(self, now: datetime | None = None) -> str:
        now = now or datetime.now()
        if not self.state.preferred_device_mac:
            self._adopt_existing_preferred_device(now)
        mac = self.state.preferred_device_mac
        if mac and self.backend.is_connected(mac):
            name = self.state.preferred_device_name or self.state.trusted_device_name
            if not self.state.connected or not self.state.bluetooth_audio_device:
                self._mark_connected(mac, name, now)
            elif self.state.active_sink != "bluetooth":
                self._refresh_connected_audio_sink(now)
            return self.playback_audio_device()
        if self.state.connected:
            if not self.state.bluetooth_audio_device:
                self._refresh_connected_audio_sink(now)
            return self.playback_audio_device()
        if self.state.preferred_device_mac and not self.state.reconnecting:
            self.begin_reconnect(now, manual=False)
        return self.bossdac_audio_device

    def fake_success(self) -> None:
        mac = self.state.preferred_device_mac or "AA:BB:CC:DD:EE:FF"
        name = self.state.preferred_device_name or self.state.trusted_device_name
        self._persist_preferred_device(BluetoothDevice(mac=mac, name=name, connected=True, trusted=True))
        self._mark_connected(mac, name, datetime.now())

    def fake_failure(self) -> None:
        self._fallback_to_bossdac("Earbuds Not Found")

    def disconnect(self) -> None:
        self._mark_disconnected("Bluetooth disconnected")

    def forget_device(self) -> None:
        for key in (
            "preferred_bluetooth_device_name",
            "preferred_bluetooth_device_mac",
            "preferred_bluetooth_last_connected_at",
            "preferred_output",
        ):
            self.store.set_app_state_value(key, "")
        self.state = BluetoothRuntimeState(
            trusted_device_name=DEFAULT_BLUETOOTH_DEVICE_NAME,
            preferred_device_name=DEFAULT_BLUETOOTH_DEVICE_NAME,
            active_sink="dac",
            preferred_output="dac",
            phase=PHASE_UNPAIRED,
        )
        self.log.info("Preferred Bluetooth device forgotten")

    def tick(self, now: datetime | None = None) -> bool:
        now = now or datetime.now()
        changed = False
        changed = self._consume_pairing_result(now) or changed
        if (
            self.state.phase == PHASE_PAIRING
            and not self._pairing_in_progress()
            and self._pairing_scan_due(now)
        ):
            changed = self.refresh_pairing_devices() or changed
            self._last_pairing_scan_at = now
        if self.state.reconnecting:
            changed = self._tick_reconnect(now) or changed
        if self._presence_check_due(now):
            changed = self._check_presence(now) or changed
            self._last_presence_check_at = now
        return changed

    def _pairing_in_progress(self) -> bool:
        return bool(self._pairing_thread and self._pairing_thread.is_alive())

    def _consume_pairing_result(self, now: datetime) -> bool:
        with self._pairing_lock:
            result = self._pairing_result
            self._pairing_result = None
        if not result:
            return False
        if result.paired:
            self._persist_preferred_device(result.device)
            self.backend.stop_scan()
            if result.connected:
                self._mark_connected(result.device.mac, result.device.name, now)
                self.state.last_message = f"Bluetooth Ready: {result.device.name}"
            else:
                self._mark_paired_without_audio(result, now)
            return True
        self.state.last_message = "Pairing Failed"
        self.state.phase = PHASE_PAIRING
        if result.error:
            self.log.warning("Pairing failed device=%s error=%s", result.device.name, result.error)
        else:
            self.log.warning("Pairing failed device=%s", result.device.name)
        return True

    def playback_audio_device(self) -> str:
        if self.state.connected and self.state.bluetooth_audio_device:
            return self.state.bluetooth_audio_device
        return self.bossdac_audio_device

    def output_label(self) -> str:
        if self.state.active_sink == "bluetooth" and self.state.connected:
            return self.state.preferred_device_name or "Bluetooth"
        if self.state.phase == PHASE_RECONNECTING:
            return "Bluetooth..."
        return "BossDAC"

    def _tick_reconnect(self, now: datetime) -> bool:
        started_at = self.state.reconnect_started_at or now
        if (now - started_at).total_seconds() >= self.state.reconnect_timeout_seconds:
            self._fallback_to_bossdac("Headphones Not Found", now=now)
            return True
        if self._next_reconnect_attempt_at and now < self._next_reconnect_attempt_at:
            return False
        return self._attempt_reconnect(now)

    def _check_presence(self, now: datetime) -> bool:
        mac = self.state.preferred_device_mac
        if not mac:
            return False
        connected = self.backend.is_connected(mac)
        if connected and self.state.connected and not self.state.bluetooth_audio_device:
            if self._refresh_connected_audio_sink(now):
                return True
            return self._recover_missing_audio_sink(now)
        if connected and not self.state.connected:
            self._mark_connected(mac, self.state.preferred_device_name, now)
            return True
        if not connected and self.state.connected:
            self._mark_disconnected("Bluetooth lost")
            return True
        available = self.backend.is_available(mac)
        if not connected and not self.state.reconnecting and available:
            if self._auto_reconnect_cooldown_active(now):
                return False
            self.log.info("Preferred device became available")
            self.begin_reconnect(now, manual=False)
            if self.state.reconnecting:
                self._attempt_reconnect(now)
                return True
            return False
        return False

    def _attempt_reconnect(self, now: datetime) -> bool:
        mac = self.state.preferred_device_mac
        if not mac:
            return False
        self._reconnect_attempts += 1
        self.log.info("Attempting reconnect mac=%s attempt=%s", mac, self._reconnect_attempts)
        self.backend.trust(mac)
        if self.backend.connect(mac):
            self._mark_connected(mac, self.state.preferred_device_name, now)
            return True
        delays = [5, 15]
        delay = delays[min(self._reconnect_attempts - 1, len(delays) - 1)]
        self._next_reconnect_attempt_at = now + timedelta(seconds=delay)
        self.log.info(
            "Bluetooth reconnect attempt failed; using BossDAC until next attempt delay_seconds=%s",
            delay,
        )
        self.state.active_sink = "dac"
        return False

    def _recover_missing_audio_sink(self, now: datetime) -> bool:
        mac = self.state.preferred_device_mac
        name = self.state.preferred_device_name
        if not mac:
            return False
        if self._audio_sink_missing_since is None:
            self._audio_sink_missing_since = now
            self._next_audio_sink_recovery_at = now + timedelta(seconds=5)
            return False
        if self._next_audio_sink_recovery_at and now < self._next_audio_sink_recovery_at:
            return False
        if self._refresh_connected_audio_sink(now):
            return True
        self._next_audio_sink_recovery_at = now + timedelta(seconds=15)
        self.log.warning(
            "Bluetooth device connected without audio sink; waiting for PipeWire sink mac=%s name=%s",
            mac,
            name,
        )
        self.state.active_sink = "dac"
        self.state.bluetooth_audio_device = ""
        self.state.last_message = "Bluetooth Audio Pending"
        return True

    def _adopt_existing_preferred_device(self, now: datetime) -> bool:
        try:
            known_devices = getattr(self.backend, "known_devices", None)
            if callable(known_devices):
                devices = known_devices()
            else:
                devices = self.backend.discover_devices("")
        except Exception as exc:  # pragma: no cover - defensive hardware boundary
            self.log.warning("Bluetooth preferred device adoption failed error=%s", exc)
            return False
        if not devices:
            return False
        connected_devices = [
            device for device in devices if self.backend.is_connected(device.mac)
        ]
        available_devices = [
            device for device in devices if device not in connected_devices and self.backend.is_available(device.mac)
        ]
        device = (connected_devices or available_devices or devices)[0]
        self._persist_preferred_device(device)
        if connected_devices:
            self.log.info(
                "Adopted existing connected Bluetooth device name=%s mac=%s",
                device.name,
                device.mac,
            )
            self._mark_connected(device.mac, device.name, now)
        else:
            self.state.phase = PHASE_DISCONNECTED
            self.state.active_sink = "dac"
            self.state.last_message = f"Preferred: {device.name}"
            self.log.info(
                "Adopted existing Bluetooth device name=%s mac=%s connected=false",
                device.name,
                device.mac,
            )
        return True

    def _mark_connected(self, mac: str, name: str, now: datetime) -> None:
        audio_device = self.backend.bluetooth_audio_device(mac, name)
        self.state.connected = True
        self.state.reconnecting = False
        self._auto_reconnect_blocked_until = None
        self.state.phase = PHASE_CONNECTED
        self.state.preferred_output = "bluetooth"
        self.state.preferred_device_mac = mac
        self.state.preferred_device_name = name
        self.state.trusted_device_name = name
        self.state.bluetooth_audio_device = audio_device
        self.state.last_successful_connection_at = now
        self.store.set_app_state_value("preferred_output", "bluetooth")
        self.store.set_app_state_value("preferred_bluetooth_last_connected_at", now.isoformat())
        if audio_device:
            self._audio_sink_missing_since = None
            self._next_audio_sink_recovery_at = None
            self.state.active_sink = "bluetooth"
            self.state.last_message = f"Connected: {name}"
            switched = self.backend.switch_to_bluetooth(audio_device)
            self.log.info("Connected device=%s audio_device=%s", name, audio_device)
            self.log.info("Playback sink switched bluetooth success=%s", str(switched).lower())
            return
        self.state.active_sink = "dac"
        self.state.last_message = f"Connected, audio pending: {name}"
        if self._audio_sink_missing_since is None:
            self._audio_sink_missing_since = now
            self._next_audio_sink_recovery_at = now + timedelta(seconds=5)
        self.log.warning(
            "Connected device=%s but Bluetooth audio sink is not available yet; using BossDAC",
            name,
        )

    def _refresh_connected_audio_sink(self, now: datetime) -> bool:
        mac = self.state.preferred_device_mac
        name = self.state.preferred_device_name
        if not mac:
            return False
        audio_device = self.backend.bluetooth_audio_device(mac, name)
        if not audio_device:
            return False
        self._audio_sink_missing_since = None
        self._next_audio_sink_recovery_at = None
        self.state.bluetooth_audio_device = audio_device
        self.state.active_sink = "bluetooth"
        self.state.phase = PHASE_CONNECTED
        self.state.last_successful_connection_at = now
        self.state.last_message = f"Connected: {name}"
        self.store.set_app_state_value("preferred_bluetooth_last_connected_at", now.isoformat())
        switched = self.backend.switch_to_bluetooth(audio_device)
        self.log.info("Bluetooth audio sink became available device=%s audio_device=%s", name, audio_device)
        self.log.info("Playback sink switched bluetooth success=%s", str(switched).lower())
        return True

    def _mark_paired_without_audio(
        self,
        result: BluetoothPairingResult,
        now: datetime,
    ) -> None:
        self.state.connected = False
        self.state.reconnecting = False
        self.state.reconnect_started_at = None
        self.state.phase = PHASE_PAIRED
        self.state.active_sink = "dac"
        self.state.bluetooth_audio_device = ""
        self.state.preferred_device_mac = result.device.mac
        self.state.preferred_device_name = result.device.name
        self.state.trusted_device_name = result.device.name
        self.state.last_message = f"Paired: {result.device.name}"
        self._last_presence_check_at = now
        self._next_reconnect_attempt_at = now + timedelta(seconds=5)
        self.backend.switch_to_bossdac()
        if result.connect_failed_profile_unavailable:
            self.log.warning(
                "Paired device=%s; Bluetooth audio profile unavailable, using BossDAC until reconnect succeeds",
                result.device.name,
            )
        else:
            self.log.info(
                "Paired device=%s; using BossDAC until Bluetooth audio connects",
                result.device.name,
            )

    def _mark_disconnected(self, message: str) -> None:
        self.state.connected = False
        self.state.reconnecting = False
        self.state.phase = PHASE_FALLBACK
        self.state.active_sink = "dac"
        self.state.bluetooth_audio_device = ""
        self.state.last_message = message
        self.backend.switch_to_bossdac()
        self.log.info("Bluetooth lost; falling back to BossDAC")

    def _fallback_to_bossdac(self, message: str, now: datetime | None = None) -> None:
        now = now or datetime.now()
        self.state.connected = False
        self.state.reconnecting = False
        self.state.reconnect_started_at = None
        self._next_reconnect_attempt_at = None
        self._auto_reconnect_blocked_until = now + timedelta(
            seconds=self.auto_reconnect_cooldown_seconds
        )
        self.state.phase = PHASE_FALLBACK if self.state.preferred_device_mac else PHASE_UNPAIRED
        self.state.active_sink = "dac"
        self.state.bluetooth_audio_device = ""
        self.state.last_message = message
        self.backend.switch_to_bossdac()
        self.log.warning("Device unavailable; using BossDAC")

    def _auto_reconnect_cooldown_active(self, now: datetime) -> bool:
        return bool(
            self._auto_reconnect_blocked_until
            and now < self._auto_reconnect_blocked_until
        )

    def _persist_preferred_device(self, device: BluetoothDevice) -> None:
        self.state.preferred_device_name = device.name
        self.state.preferred_device_mac = device.mac
        self.state.trusted_device_name = device.name
        self.state.preferred_output = "bluetooth"
        self.store.set_app_state_value("preferred_bluetooth_device_name", device.name)
        self.store.set_app_state_value("preferred_bluetooth_device_mac", device.mac)
        self.store.set_app_state_value("preferred_output", "bluetooth")

    def _presence_check_due(self, now: datetime) -> bool:
        if self.state.phase == PHASE_PAIRING:
            return False
        return (
            self._last_presence_check_at is None
            or (now - self._last_presence_check_at).total_seconds()
            >= self.presence_interval_seconds
        )

    def _pairing_scan_due(self, now: datetime) -> bool:
        return (
            self._last_pairing_scan_at is None
            or (now - self._last_pairing_scan_at).total_seconds() >= 5
        )


def _parse_bluetooth_devices(output: str) -> list[BluetoothDevice]:
    devices: dict[str, BluetoothDevice] = {}
    audio_devices: set[str] = set()
    for line in output.splitlines():
        match = re.search(r"\bDevice\s+([0-9A-F:]{17})\s+(.+)", line.strip(), re.IGNORECASE)
        if not match:
            continue
        mac = match.group(1).upper()
        value = match.group(2).strip()
        if not value:
            continue
        field_match = re.match(r"([A-Za-z][A-Za-z0-9_-]*):\s*(.+)", value)
        if field_match:
            field = field_match.group(1).lower()
            field_value = field_match.group(2).strip()
            if field in {"name", "alias"} and field_value:
                devices[mac] = BluetoothDevice(mac=mac, name=field_value)
            elif field == "icon" and "audio" in field_value.lower():
                audio_devices.add(mac)
            continue
        devices.setdefault(mac, BluetoothDevice(mac=mac, name=value))

    audio_results = [device for device in devices.values() if device.mac in audio_devices]
    if audio_results:
        return sorted(audio_results, key=lambda device: device.name.lower())
    named_results = [
        device
        for device in devices.values()
        if not _looks_like_mac_label(device.name)
    ]
    return sorted(named_results, key=lambda device: device.name.lower())


def _parse_btmgmt_devices(output: str) -> list[BluetoothDevice]:
    devices: list[BluetoothDevice] = []
    current_mac = ""
    current_name = ""
    for raw_line in output.splitlines():
        line = raw_line.strip()
        found = re.search(r"\bdev_found:\s+([0-9A-F:]{17})\b", line, re.IGNORECASE)
        if found:
            if current_mac:
                devices.append(
                    BluetoothDevice(mac=current_mac.upper(), name=current_name or current_mac.upper())
                )
            current_mac = found.group(1).upper()
            current_name = ""
            continue
        if not current_mac:
            continue
        name_match = re.match(r"(?:name|short_name)\s+(.+)", line, re.IGNORECASE)
        if name_match and not current_name:
            current_name = name_match.group(1).strip()
    if current_mac:
        devices.append(BluetoothDevice(mac=current_mac.upper(), name=current_name or current_mac.upper()))
    return devices


def _parse_wpctl_sinks(output: str) -> list[tuple[str, str]]:
    sinks: list[tuple[str, str]] = []
    in_sinks = False
    for raw_line in output.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if re.search(r"\bSinks:\s*$", line, re.IGNORECASE):
            in_sinks = True
            continue
        if in_sinks and re.search(r"\b(Sources|Filters|Streams|Devices):\s*$", line, re.IGNORECASE):
            break
        if not in_sinks:
            continue
        match = re.search(r"\*?\s*(\d+)\.\s+(.+?)(?:\s+\[|$)", line)
        if not match:
            continue
        sinks.append((match.group(1), match.group(2).strip()))
    return sinks


def _wpctl_inspect_value(output: str, key: str) -> str:
    pattern = rf"\b{re.escape(key)}\s+=\s+\"?([^\"\n]+)\"?"
    match = re.search(pattern, output)
    return match.group(1).strip() if match else ""


def _normalize_label(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", value.lower())


def _devices_by_mac(devices: list[BluetoothDevice]) -> dict[str, BluetoothDevice]:
    return {device.mac.upper(): device for device in devices}


def _match_pairing_device(
    selected: BluetoothDevice,
    devices: list[BluetoothDevice],
) -> BluetoothDevice | None:
    selected_name = selected.name.strip().lower()
    for device in devices:
        if device.mac.upper() == selected.mac.upper():
            return device
    if selected_name:
        for device in devices:
            if device.name.strip().lower() == selected_name:
                return device
    if selected_name:
        for device in devices:
            name = device.name.strip().lower()
            if selected_name in name or name in selected_name:
                return device
    return None


def _compact_output(value: str, limit: int = 160) -> str:
    text = " ".join(value.split())
    if not text:
        return "-"
    if len(text) <= limit:
        return text
    return f"{text[:limit - 1]}..."


def _looks_like_mac_label(value: str) -> bool:
    return bool(re.fullmatch(r"[0-9A-F]{2}(?:[:-][0-9A-F]{2}){5}", value.strip(), re.IGNORECASE))


def _adapter_powered(output: str) -> bool:
    return bool(re.search(r"\bPowered:\s+yes\b", output, re.IGNORECASE))


def _bluetooth_show_summary(output: str) -> str:
    if not output.strip():
        return "-"
    fields = []
    for name in ("Powered", "Discovering", "Pairable", "Discoverable"):
        match = re.search(rf"\b{name}:\s+(.+)", output, re.IGNORECASE)
        if match:
            fields.append(f"{name.lower()}={match.group(1).strip()}")
    controller = re.search(r"\bController\s+([0-9A-F:]{17})", output, re.IGNORECASE)
    if controller:
        fields.insert(0, f"controller={controller.group(1).upper()}")
    return " ".join(fields) or _compact_output(output)


def _bluetooth_not_ready(output: str) -> bool:
    return "org.bluez.error.notready" in output.lower() or "not ready" in output.lower()


def _pairing_succeeded(output: str) -> bool:
    lowered = output.lower()
    return (
        "pairing successful" in lowered
        or "alreadyexists" in lowered
        or "already exists" in lowered
    )


def _trust_succeeded(output: str) -> bool:
    lowered = output.lower()
    return "trust succeeded" in lowered or "succeeded" in lowered


def _connect_succeeded(output: str) -> bool:
    lowered = output.lower()
    return "connection successful" in lowered or "connected: yes" in lowered


def _profile_unavailable(output: str) -> bool:
    lowered = output.lower()
    return (
        "br-connection-profile-unavailable" in lowered
        or "connection-profile-unavailable" in lowered
        or "profile unavailable" in lowered
    )


def _authentication_failed(output: str) -> bool:
    return "org.bluez.error.authenticationfailed" in output.lower()


def _device_not_available(output: str) -> bool:
    lowered = output.lower()
    return "not available" in lowered or "device set" in lowered


def _coerce_pairing_result(
    device: BluetoothDevice,
    value: BluetoothPairingResult | bool,
) -> BluetoothPairingResult:
    if isinstance(value, BluetoothPairingResult):
        return value
    return BluetoothPairingResult(
        device=device,
        paired=bool(value),
        trusted=bool(value),
        connected=bool(value),
    )


def _decode_timeout_output(value) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode(errors="replace")
    return str(value)


def _parse_iso_datetime(value: str) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None
