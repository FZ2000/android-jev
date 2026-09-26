"""Browsing a real app end to end: open it, move around inside it, come back.

Unit tests prove each tool does what it says. This proves the tools compose into
the thing an agent actually has to do — get into an app, find something, go
deeper, and get back out — against whatever the phone happens to be showing.

Settings is the subject because it is on every device, needs no account, and has
a stable nested structure to walk into and back out of.
"""

from __future__ import annotations

import time

import pytest

from phone_control.adb import AndroidPhone
from phone_control.device_state import foreground_package, read_device_state
from phone_control.errors import PhoneCommandFailed
from phone_control.screen import Screen, read_screen
from scenarios.starting_states import open_settings_at

pytestmark = pytest.mark.device

SETTINGS_PACKAGE = "com.android.settings"
HOME_KEYCODE = "3"
BACK_KEYCODE = "4"
WAKE_KEYCODE = "224"

SETTLE_SECONDS = 1.5


def phone_is_attached() -> bool:
    try:
        return AndroidPhone().is_connected()
    except Exception:
        return False


@pytest.fixture(scope="module")
def phone() -> AndroidPhone:
    if not phone_is_attached():
        pytest.skip("no Android phone is attached over USB")
    handle = AndroidPhone()
    handle.run(["shell", "input", "keyevent", WAKE_KEYCODE])
    state = read_device_state(handle)
    if state.is_screen_on is False or state.is_locked:
        pytest.skip("the phone must be awake and unlocked to browse an app")
    return handle


def settle() -> None:
    time.sleep(SETTLE_SECONDS)


def open_settings(phone: AndroidPhone) -> None:
    """Open Settings at its top level, whatever it was showing before.

    One implementation, shared with the usage scenarios, because this is the third
    time the same lesson has been learned in a different place: launching the app
    is not enough. Android restores the last Settings screen you were on, and the
    search screen is a different package that restores the last *query* as well.

    This file used to run the intent itself and the weaker start is what it got.
    The scroll test had already failed once for this reason - a run that had
    wandered into a device-pairing page came back to twenty rows that fit on one
    screen with nothing to scroll - and the fix was applied there rather than
    here, so this file kept the bug and only passed when the phone happened to be
    where the test assumed.
    """
    open_settings_at(phone)


def the_tappable_rows(screen: Screen) -> list:
    """Controls that lead somewhere: tappable, named, and of a plausible size."""
    return [
        control
        for control in screen.controls
        if control.is_tappable
        and control.label
        and not control.is_scrollable
        and control.area.height >= 40
        and control.area.width >= 200
    ]


def tap(phone: AndroidPhone, control) -> None:
    """Tap a control where it actually is, then let the screen settle."""
    phone.run(
        [
            "shell",
            "input",
            "tap",
            str(control.area.center_x),
            str(control.area.center_y),
        ]
    )
    settle()


def test_settings_can_be_opened(phone):
    open_settings(phone)

    assert foreground_package(phone) == SETTINGS_PACKAGE


def test_the_settings_screen_can_be_read(phone):
    open_settings(phone)

    screen = read_screen(phone)

    assert screen.package == SETTINGS_PACKAGE
    assert the_tappable_rows(screen), (
        "no tappable rows were found in Settings; the tree may be thin"
    )


def test_browsing_into_a_settings_section_and_back_out(phone):
    """The full loop: open, read, tap deeper, confirm, come back.

    The row is found by what tapping it does rather than picked in advance, and
    that is not fastidiousness - it is the only way this test can be honest.
    Measured on this device, the top of the Settings list holds a search row, a
    banner and an account row before the first real section, and the search row is
    structurally identical to a section row (`list_item`, no editable text, same
    size). Tapping it opens `com.google.android.settings.intelligence`, where BACK
    does not return to Settings at all, so a test that taps whatever comes first
    is asserting the behaviour of the search box while claiming to test browsing.
    """
    open_settings(phone)
    root = read_screen(phone)
    rows = the_tappable_rows(root)
    assert rows, "Settings showed nothing to open"

    for target in rows[:6]:
        tap(phone, target)
        try:
            deeper = read_screen(phone)
        except PhoneCommandFailed:
            # A page Android will not dump. It cannot be the section this test is
            # looking for, because this test reads the screen, so put the phone back
            # and try the next row rather than failing on a page it never wanted.
            open_settings(phone)
            continue
        if deeper.package == SETTINGS_PACKAGE and deeper.as_text() != root.as_text():
            break
        # Somewhere else, or nowhere. Put the phone back and try the next row.
        #
        # This used to press BACK, which is enough for a page that was pushed onto
        # Settings and not enough for the search screen: that is a different package,
        # and one BACK does not reliably return from it. The test then went on tapping
        # coordinates it had read from the Settings list while looking at search
        # results, and failed at the end on a screen it had walked to itself. Asking
        # for the top level again is slower and says what it means.
        open_settings(phone)
    else:
        pytest.fail(
            "no row in the top level of Settings led to another Settings page; "
            f"tried {[row.label for row in rows[:6]]}"
        )

    # Back out of the section that did lead somewhere. This one is a page pushed onto
    # Settings, which is what BACK is for.
    phone.run(["shell", "input", "keyevent", BACK_KEYCODE])
    settle()

    back_out = read_screen(phone)
    assert back_out.package == SETTINGS_PACKAGE
    assert back_out.as_text() == root.as_text(), (
        f"going back from {target.label!r} did not return to the Settings list"
    )


def test_scrolling_a_settings_screen_changes_what_is_listed(phone):
    """The list has to have more below the fold, and this test now says so.

    It used to assume the top level of Settings is longer than the screen. On
    this device it is, but only when Settings opens at the top level and not on a
    remembered sub-page - so the assertion is made only after checking that there
    is something to scroll.
    """
    open_settings(phone)
    before = read_screen(phone)

    scrollable = [control for control in before.controls if control.is_scrollable]
    assert scrollable, (
        "the top level of Settings has nothing scrollable on this device, so this "
        "screen cannot show whether scrolling works"
    )

    height = before.height
    phone.run(
        [
            "shell",
            "input",
            "swipe",
            str(before.width // 2),
            str(int(height * 0.75)),
            str(before.width // 2),
            str(int(height * 0.25)),
            "300",
        ]
    )
    settle()

    after = read_screen(phone)

    assert after.as_text() != before.as_text(), "scrolling did not move the list"


def test_home_returns_to_the_launcher(phone):
    phone.run(["shell", "input", "keyevent", HOME_KEYCODE])
    settle()

    assert foreground_package(phone) != SETTINGS_PACKAGE
    assert read_screen(phone).controls or read_device_state(phone).is_locked is False
