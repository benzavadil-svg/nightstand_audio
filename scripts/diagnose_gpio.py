from __future__ import annotations

import shutil
import sys
import subprocess
from collections.abc import Iterable

from app.hardware.pin_map import DEFAULT_PI_PIN_MAP, active_assignments


def main() -> None:
    print("Nightstand Audio GPIO diagnostics")
    print()
    print("Configured control/display pins:")
    for assignment in active_assignments(DEFAULT_PI_PIN_MAP):
        physical = f"physical {assignment.physical_pin}" if assignment.physical_pin else "physical n/a"
        print(f"- GPIO{assignment.gpio:<2} {physical:<12} {assignment.name} ({assignment.group})")

    print()
    _run_pinctrl([assignment.gpio for assignment in active_assignments(DEFAULT_PI_PIN_MAP)])
    print()
    _run_first_available(
        "GPIO line info",
        [
            ["gpioinfo", "/dev/gpiochip0"],
            ["gpioinfo"],
        ],
    )
    print()
    if sys.platform.startswith("linux"):
        _run_first_available(
            "GPIO device users",
            [
                ["fuser", "-v", "/dev/gpiochip0"],
                ["fuser", "-v", "/dev/gpiochip1"],
                ["fuser", "-v", "/dev/gpiochip2"],
            ],
            allow_all=True,
        )
    else:
        print("GPIO device users:")
        print("Linux GPIO device checks are skipped on this host.")
    print()
    _run_command("Nightstand processes", ["ps", "aux"])


def _run_pinctrl(gpios: Iterable[int]) -> None:
    if not shutil.which("pinctrl"):
        print("Pinmux state: pinctrl not found")
        return
    print("Pinmux state:")
    for gpio in gpios:
        result = _completed(["pinctrl", "get", str(gpio)])
        output = _combined_output(result)
        print(f"$ pinctrl get {gpio}")
        print(output or f"(exit {result.returncode}, no output)")


def _run_first_available(
    label: str,
    commands: list[list[str]],
    *,
    allow_all: bool = False,
) -> None:
    print(f"{label}:")
    ran_any = False
    for command in commands:
        if not command or not shutil.which(command[0]):
            continue
        ran_any = True
        _print_command(command)
        if not allow_all:
            return
    if not ran_any:
        print(f"{label}: required command not found")


def _run_command(label: str, command: list[str]) -> None:
    print(f"{label}:")
    if not command or not shutil.which(command[0]):
        print(f"{label}: command not found")
        return
    _print_command(command, grep="nightstand-audio")


def _print_command(command: list[str], grep: str | None = None) -> None:
    result = _completed(command)
    output = _combined_output(result)
    if grep:
        output = "\n".join(line for line in output.splitlines() if grep in line)
    print("$ " + " ".join(command))
    print(output or f"(exit {result.returncode}, no output)")


def _completed(command: list[str]) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            timeout=8,
        )
    except subprocess.TimeoutExpired as exc:
        stdout = _decode(exc.stdout)
        stderr = _decode(exc.stderr)
        return subprocess.CompletedProcess(command, 124, stdout=stdout, stderr=stderr)
    except OSError as exc:
        return subprocess.CompletedProcess(command, 1, stdout="", stderr=str(exc))


def _combined_output(result: subprocess.CompletedProcess[str]) -> str:
    return "\n".join(part.strip() for part in (result.stdout, result.stderr) if part.strip())


def _decode(value) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode(errors="replace")
    return str(value)


if __name__ == "__main__":
    main()
