"""The clipboard route, which is where a silent lie would be most expensive.

`cmd clipboard` is not implemented on every Android build, and a build without it
still exits 0 while printing "No shell command implementation" on stderr. An
agent told the clipboard was set will paste stale content and never notice, so
these tests pin that the exit code is not treated as evidence.
"""

from __future__ import annotations

import pytest

from conftest import FakePhone
from phone_control.text_entry import read_device_clipboard, set_device_clipboard

UNSUPPORTED = "No shell command implementation"


def phone_without_clipboard_support() -> FakePhone:
    return FakePhone(complaints={"cmd clipboard": UNSUPPORTED})


def test_storing_text_reports_failure_when_the_phone_has_no_clipboard_command():
    assert set_device_clipboard(phone_without_clipboard_support(), "hello") is False


def test_storing_text_uses_the_plain_set_subcommand():
    phone = FakePhone(answers={"cmd clipboard get": "hello"})

    set_device_clipboard(phone, "hello")

    assert "shell cmd clipboard set 'hello'" in phone.commands


def test_storing_text_is_confirmed_by_reading_the_value_back():
    phone = FakePhone(answers={"cmd clipboard get": "hello"})

    assert set_device_clipboard(phone, "hello") is True
    assert "shell cmd clipboard get" in phone.commands


def test_a_write_that_did_not_take_is_not_reported_as_success():
    """The clip was never stored, so a paste would silently use the old one."""
    phone = FakePhone(answers={"cmd clipboard get": "something else"})

    assert set_device_clipboard(phone, "hello") is False


def test_a_write_is_trusted_when_the_phone_will_not_let_it_be_read_back():
    phone = FakePhone(complaints={"cmd clipboard get": UNSUPPORTED})

    assert set_device_clipboard(phone, "hello") is True


def test_reading_reports_nothing_when_the_phone_has_no_clipboard_command():
    assert read_device_clipboard(phone_without_clipboard_support()) is None


def test_reading_returns_the_stored_text():
    phone = FakePhone(answers={"cmd clipboard get": "  hello there  "})

    assert read_device_clipboard(phone) == "hello there"


def test_reading_an_empty_clipboard_returns_an_empty_string():
    assert read_device_clipboard(FakePhone()) == ""


@pytest.mark.parametrize(
    "complaint",
    [
        pytest.param("No shell command implementation", id="android-15-and-later"),
        pytest.param("Unknown command: clipboard", id="older-build"),
        pytest.param("not enough data", id="some-oem-builds"),
    ],
)
def test_every_known_way_of_saying_unsupported_is_recognised(complaint):
    assert (
        set_device_clipboard(FakePhone(complaints={"clipboard": complaint}), "x")
        is False
    )
