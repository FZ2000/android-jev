"""Reaching one Android device through the adb command line.

This module owns everything that knows adb exists. The rest of the package
speaks in phones and screens, so swapping adb for another transport later
touches only this file.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

from .errors import (
    AdbNotFound,
    NoPhoneConnected,
    PhoneCommandFailed,
    PhoneControlError,
    PhoneNotAuthorized,
)

ADB_SEARCH_LOCATIONS = (
    "~/Library/Android/sdk/platform-tools/adb",
    "~/.local/opt/platform-tools/adb",
    "/opt/homebrew/bin/adb",
    "/usr/local/bin/adb",
)

DEFAULT_COMMAND_TIMEOUT_SECONDS = 30.0
DEVICE_LIST_TIMEOUT_SECONDS = 15.0
SERIAL_REUSE_SECONDS = 5.0

PLATFORM_TOOLS_DOWNLOAD = (
    "curl -sSL -o /tmp/platform-tools.zip "
    "https://dl.google.com/android/repository/platform-tools-latest-darwin.zip "
    "&& unzip -q -o /tmp/platform-tools.zip -d ~/.local/opt"
)


def find_adb_program() -> str:
    """Return the adb executable, preferring PATH over bundled copies."""
    override = os.environ.get("PHONE_CONTROL_ADB")
    if override:
        candidate = Path(override).expanduser()
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate)
        raise AdbNotFound(
            f"PHONE_CONTROL_ADB points at {candidate}, which is not an "
            "executable file.",
            fix="Unset PHONE_CONTROL_ADB, or point it at the adb binary.",
        )
    on_path = shutil.which("adb")
    if on_path:
        return on_path
    for location in ADB_SEARCH_LOCATIONS:
        candidate = Path(location).expanduser()
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate)
    raise AdbNotFound(
        "The `adb` program was not found on PATH or in any of the usual "
        "Android SDK locations.",
        fix=f"Install Android platform-tools: {PLATFORM_TOOLS_DOWNLOAD}",
    )


def quote_for_device_shell(text: str) -> str:
    """Quote one argument for the shell that runs on the device.

    ``adb shell`` forwards its arguments to the device's own shell, so a URL
    containing ``&`` or a message containing a space must be quoted here, or the
    device will split it into pieces.
    """
    return "'" + text.replace("'", "'\\''") + "'"


@dataclass(frozen=True)
class CommandResult:
    """Raw output from one adb invocation."""

    stdout: bytes
    stderr: bytes
    return_code: int

    @property
    def text(self) -> str:
        return self.stdout.decode("utf-8", "replace")

    @property
    def error_text(self) -> str:
        return self.stderr.decode("utf-8", "replace")


@dataclass(frozen=True)
class AttachedDevice:
    """One row of ``adb devices -l``."""

    serial: str
    state: str
    model: str | None
    product: str | None

    @property
    def is_ready(self) -> bool:
        return self.state == "device"

    @property
    def friendly_name(self) -> str:
        if self.model:
            return self.model.replace("_", " ")
        return self.serial


def _parse_device_lines(output: str) -> list[AttachedDevice]:
    devices: list[AttachedDevice] = []
    for line in output.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("List of devices"):
            continue
        if stripped.startswith("*"):
            continue
        fields = stripped.split()
        if len(fields) < 2:
            continue
        serial, state = fields[0], fields[1]
        extra = {
            key: value
            for field in fields[2:]
            if ":" in field
            for key, value in [field.split(":", 1)]
        }
        devices.append(
            AttachedDevice(
                serial=serial,
                state=state,
                model=extra.get("model"),
                product=extra.get("product"),
            )
        )
    return devices


def _error_for_state(device: AttachedDevice) -> PhoneControlError:
    if device.state == "unauthorized":
        return PhoneNotAuthorized(
            f"The phone ({device.friendly_name}) is attached but has not "
            "authorized this computer for USB debugging.",
            fix=(
                "Unlock the phone and tap Allow on the 'Allow USB debugging?' "
                "prompt, ticking 'Always allow from this computer'."
            ),
        )
    if device.state == "offline":
        return PhoneCommandFailed(
            "adb",
            "the device is in the offline state",
            fix="Replug the USB cable, or run `adb kill-server` and retry.",
        )
    return NoPhoneConnected(
        f"The phone ({device.friendly_name}) reports state "
        f"'{device.state}' and cannot accept commands."
    )


class AndroidPhone:
    """Sends commands to one attached Android device."""

    def __init__(
        self,
        adb_program: str | None = None,
        serial: str | None = None,
        command_timeout: float = DEFAULT_COMMAND_TIMEOUT_SECONDS,
    ) -> None:
        self.adb_program = adb_program or find_adb_program()
        self.pinned_serial = serial or os.environ.get("DSH_PHONE_SERIAL") or None
        self.command_timeout = command_timeout
        self._cached_serial: str | None = None
        self._cached_at = 0.0

    # --- device discovery -------------------------------------------------

    def attached_devices(self) -> list[AttachedDevice]:
        """Every device adb currently knows about, ready or not."""
        try:
            completed = subprocess.run(
                [self.adb_program, "devices", "-l"],
                capture_output=True,
                timeout=DEVICE_LIST_TIMEOUT_SECONDS,
            )
        except subprocess.TimeoutExpired as error:
            raise PhoneCommandFailed(
                "adb devices",
                f"timed out after {DEVICE_LIST_TIMEOUT_SECONDS:g}s",
                fix="Run `adb kill-server` and retry.",
            ) from error
        return _parse_device_lines(completed.stdout.decode("utf-8", "replace"))

    def serial_number(self) -> str:
        """The serial of the device to talk to, resolved and briefly cached."""
        now = time.monotonic()
        if self._cached_serial and now - self._cached_at < SERIAL_REUSE_SECONDS:
            return self._cached_serial
        serial = self._choose_serial()
        self._cached_serial = serial
        self._cached_at = now
        return serial

    def _choose_serial(self) -> str:
        devices = self.attached_devices()
        if self.pinned_serial:
            for device in devices:
                if device.serial == self.pinned_serial:
                    return device.serial if device.is_ready else self._reject(device)
            raise NoPhoneConnected(
                f"No attached device has the serial '{self.pinned_serial}'.",
                fix="Unset DSH_PHONE_SERIAL or set it to one of: "
                + (", ".join(d.serial for d in devices) or "no devices attached"),
            )
        ready = [device for device in devices if device.is_ready]
        if len(ready) == 1:
            return ready[0].serial
        if len(ready) > 1:
            raise NoPhoneConnected(
                "More than one Android device is attached ("
                + ", ".join(device.serial for device in ready)
                + ").",
                fix="Set DSH_PHONE_SERIAL to the serial you want to control.",
            )
        if devices:
            return self._reject(devices[0])
        raise NoPhoneConnected(
            "No Android device is attached over USB.",
            fix=(
                "Connect the phone with a data-capable USB cable. If it is "
                "already connected, open Settings > Developer options and turn "
                "on USB debugging, then replug and tap Allow on the phone."
            ),
        )

    @staticmethod
    def _reject(device: AttachedDevice) -> str:
        raise _error_for_state(device)

    def _forget_serial(self) -> None:
        self._cached_serial = None
        self._cached_at = 0.0

    def is_connected(self) -> bool:
        """Whether a command could be sent right now."""
        try:
            self.serial_number()
        except PhoneControlError:
            return False
        return True

    # --- running commands -------------------------------------------------

    def _invoke(
        self, arguments: list[str], timeout: float | None = None
    ) -> CommandResult:
        limit = self.command_timeout if timeout is None else timeout
        try:
            completed = subprocess.run(
                [self.adb_program, *arguments],
                capture_output=True,
                timeout=limit,
            )
        except subprocess.TimeoutExpired as error:
            raise PhoneCommandFailed(
                " ".join(arguments),
                f"timed out after {limit:g}s",
                fix=(
                    "The phone may be busy or showing a dialog. Check its "
                    "screen, then retry."
                ),
            ) from error
        result = CommandResult(
            stdout=completed.stdout,
            stderr=completed.stderr,
            return_code=completed.returncode,
        )
        self._raise_for_device_problem(arguments, result)
        return result

    def _raise_for_device_problem(
        self, arguments: list[str], result: CommandResult
    ) -> None:
        problem = result.error_text.lower()
        if "unauthorized" in problem:
            self._forget_serial()
            raise PhoneNotAuthorized(
                "The phone has withdrawn USB debugging authorization.",
                fix="Unlock the phone and tap Allow on the USB debugging prompt.",
            )
        if (
            "no devices/emulators found" in problem
            or "device not found" in problem
            or "device offline" in problem
        ):
            self._forget_serial()
            raise NoPhoneConnected(
                "The phone is no longer reachable over USB.",
                fix="Replug the USB cable and confirm the phone is unlocked.",
            )

    def run(self, arguments: list[str], timeout: float | None = None) -> str:
        """Run an adb subcommand for this device and return its stdout text."""
        return self._invoke(["-s", self.serial_number(), *arguments], timeout).text

    def run_result(
        self, arguments: list[str], timeout: float | None = None
    ) -> CommandResult:
        """Run an adb subcommand and keep stdout, stderr and the exit code.

        Some device commands report failure only on stderr while still exiting
        zero, so a caller that needs to know whether the work happened cannot
        look at stdout alone.
        """
        return self._invoke(["-s", self.serial_number(), *arguments], timeout)

    def run_binary(self, arguments: list[str], timeout: float | None = None) -> bytes:
        """Run an adb subcommand and return its raw stdout bytes."""
        return self._invoke(["-s", self.serial_number(), *arguments], timeout).stdout

    def run_checked(self, arguments: list[str], timeout: float | None = None) -> str:
        """Run a command, raising when adb itself reports failure on the device."""
        result = self._invoke(["-s", self.serial_number(), *arguments], timeout)
        message = result.error_text.strip()
        if result.return_code != 0 or "Exception" in message or "Error:" in message:
            raise PhoneCommandFailed(" ".join(arguments), message or result.text)
        return result.text

    # --- shell conveniences ----------------------------------------------

    def shell(self, command: str, timeout: float | None = None) -> str:
        """Run a shell command on the device and return its stdout."""
        return self.run(["shell", command], timeout)

    def shell_binary(self, command: str, timeout: float | None = None) -> bytes:
        """Run a shell command on the device, preserving raw binary stdout."""
        return self.run_binary(["exec-out", command], timeout)


_shared_phone: AndroidPhone | None = None


def shared_phone() -> AndroidPhone:
    """The single phone handle used by the tool surface."""
    global _shared_phone
    if _shared_phone is None:
        _shared_phone = AndroidPhone()
    return _shared_phone
