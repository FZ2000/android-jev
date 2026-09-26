"""Running a command on the phone, and what comes back when it goes wrong.

Every tool in this server ends up here, so the shapes this returns are the shapes
everything else reasons about. None of it was tested: not the four ways of running a
command, not the timeout, and not the two device problems that have to be told apart
from ordinary failures - a phone that has withdrawn USB debugging authorisation, and
a cable that has come out.

The distinction matters to whoever is holding the phone. One of those needs a tap on
the phone and the other needs the cable reseating, and an agent that cannot tell them
apart will ask for the wrong thing. Both forget the cached serial, because a phone
that has just been unplugged must not be talked to again under the old one.
"""

from __future__ import annotations

import subprocess

import pytest

from phone_control.adb import (
    AndroidPhone,
    NoPhoneConnected,
    PhoneCommandFailed,
    PhoneNotAuthorized,
)

A_SERIAL = "4C1F8A2E9D7B305"


def a_phone(**keywords) -> AndroidPhone:
    """A phone that will never actually run anything unless a test asks it to."""
    phone = AndroidPhone(adb_program="/bin/sh", serial=A_SERIAL, **keywords)
    phone._cached_serial = A_SERIAL
    phone._cached_at = 1e18
    return phone


class AFinishedCommand:
    def __init__(self, stdout: bytes, stderr: bytes, returncode: int) -> None:
        self.stdout = stdout
        self.stderr = stderr
        self.returncode = returncode


def answering(monkeypatch, stdout: bytes = b"", stderr: bytes = b"", code: int = 0):
    """Wire the phone to a command that answers this and nothing else.

    `subprocess.run` is replaced rather than `_invoke`, so the real path runs: the
    argument list adb is given, the result built out of it, and the check for a
    device problem. Patching `_invoke` would skip the part that tells a withdrawn
    authorisation from a cable that has come out, which is the point of the tests
    below that use this.
    """
    phone = a_phone()
    asked: list[list[str]] = []

    def run(arguments, capture_output=None, timeout=None):
        asked.append(list(arguments))
        return AFinishedCommand(stdout, stderr, code)

    monkeypatch.setattr("phone_control.adb.subprocess.run", run)
    return phone, asked


# --- the four ways of running one ----------------------------------------


def test_a_command_is_sent_to_the_named_device(monkeypatch):
    """Without `-s` adb picks a device itself, and with two attached that is a guess."""
    phone, asked = answering(monkeypatch, stdout=b"hello\n")

    assert phone.run(["shell", "echo", "hello"]) == "hello\n"
    assert asked == [["/bin/sh", "-s", A_SERIAL, "shell", "echo", "hello"]], (
        "the program, then the device it is for, then the command"
    )


def test_the_whole_result_is_kept_when_it_is_asked_for(monkeypatch):
    """stderr and the exit code, because some device commands fail quietly on stdout."""
    phone, _ = answering(monkeypatch, stdout=b"out", stderr=b"err", code=3)

    result = phone.run_result(["shell", "something"])

    assert (result.text, result.error_text, result.return_code) == ("out", "err", 3)


def test_raw_bytes_come_back_as_bytes(monkeypatch):
    """A screenshot is not text on this route, and decoding it would corrupt it."""
    phone, _ = answering(monkeypatch, stdout=b"\x89PNG\r\n\x1a\n\x00\xff")

    assert (
        phone.run_binary(["exec-out", "screencap", "-p"])
        == b"\x89PNG\r\n\x1a\n\x00\xff"
    )


def test_a_command_that_succeeded_returns_its_output(monkeypatch):
    phone, _ = answering(monkeypatch, stdout=b"fine")

    assert phone.run_checked(["shell", "true"]) == "fine"


@pytest.mark.parametrize(
    ("stderr", "code"),
    [
        (b"Error: something went wrong", 0),
        (b"java.lang.Exception: boom", 0),
        (b"", 1),
    ],
)
def test_a_command_that_failed_raises_rather_than_returning_its_complaint(
    monkeypatch, stderr, code
):
    """A complaint returned as a value is a success to every caller."""
    phone, _ = answering(monkeypatch, stdout=b"", stderr=stderr, code=code)

    with pytest.raises(PhoneCommandFailed):
        phone.run_checked(["shell", "false"])


def test_a_command_that_hangs_is_given_up_on_with_a_next_step(monkeypatch):
    """adb can wait for ever on a phone showing a dialog, and so would the run."""
    phone = a_phone(command_timeout=0.5)

    def hang(*arguments, **keywords):
        raise subprocess.TimeoutExpired(cmd="adb", timeout=0.5)

    monkeypatch.setattr(subprocess, "run", hang)

    with pytest.raises(PhoneCommandFailed) as refused:
        phone.run(["shell", "sleep", "1000"])

    assert "timed out" in str(refused.value)
    assert refused.value.fix, "a timeout with no next step leaves the caller stuck"


# --- telling the two device problems apart -------------------------------


def test_a_withdrawn_authorisation_is_reported_as_needing_a_tap(monkeypatch):
    phone, _ = answering(monkeypatch, stderr=b"error: device unauthorized.", code=1)
    phone._cached_serial = A_SERIAL

    with pytest.raises(PhoneNotAuthorized) as refused:
        phone.run(["shell", "true"])

    assert "Allow" in refused.value.fix
    assert phone._cached_serial is None, (
        "the serial stayed cached, so the next command would try the same device"
    )


@pytest.mark.parametrize(
    "complaint",
    ["error: no devices/emulators found", "device not found", "device offline"],
)
def test_a_phone_that_has_gone_is_reported_as_needing_the_cable(monkeypatch, complaint):
    phone, _ = answering(monkeypatch, stderr=complaint.encode(), code=1)
    phone._cached_serial = A_SERIAL

    with pytest.raises(NoPhoneConnected) as refused:
        phone.run(["shell", "true"])

    assert "cable" in refused.value.fix.casefold()
    assert phone._cached_serial is None


def test_an_ordinary_failure_is_not_dressed_up_as_a_device_problem(monkeypatch):
    """Most failures are the command, not the phone, and must stay that way."""
    phone, _ = answering(monkeypatch, stdout=b"", stderr=b"sh: no such file", code=1)

    result = phone.run_result(["shell", "nonsense"])

    assert result.return_code == 1


def test_whether_the_phone_could_be_commanded_at_all(monkeypatch):
    phone, _ = answering(monkeypatch)

    assert phone.is_connected() is True

    def refuse():
        raise NoPhoneConnected("gone")

    monkeypatch.setattr(phone, "serial_number", refuse)
    assert phone.is_connected() is False
