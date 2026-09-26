"""Failures that tell the caller exactly what to do next.

Every error raised by this package carries a ``fix`` sentence when a human can
resolve it. The text is written to be read by an agent and relayed verbatim to
the person holding the phone, so it names the concrete tap or command rather
than restating the symptom.
"""

from __future__ import annotations


class PhoneControlError(Exception):
    """A phone-control failure, optionally carrying a concrete remedy."""

    def __init__(self, message: str, *, fix: str | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.fix = fix

    def __str__(self) -> str:
        if self.fix:
            return f"{self.message}\nHow to fix: {self.fix}"
        return self.message


class AdbNotFound(PhoneControlError):
    """The adb program is not installed or not on PATH."""


class NoPhoneConnected(PhoneControlError):
    """No usable Android device is attached, or several are and none was chosen."""


class PhoneNotAuthorized(PhoneControlError):
    """The device is attached but has not authorized this computer."""


class PhoneCommandFailed(PhoneControlError):
    """The device was reachable but the command itself failed."""

    def __init__(self, command: str, detail: str, *, fix: str | None = None) -> None:
        super().__init__(f"`{command}` failed on the phone: {detail.strip()}", fix=fix)
        self.command = command
        self.detail = detail


class NoMatchingControl(PhoneControlError):
    """Nothing currently on screen matched what the caller described."""


class JevNotConfigured(PhoneControlError):
    """No Jev key is available, so Jev decisions cannot be made."""


class JevRequestFailed(PhoneControlError):
    """The Jev decisions endpoint rejected or failed the request."""
