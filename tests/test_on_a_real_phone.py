"""End-to-end checks that need the phone actually plugged in.

Skipped automatically when no device is attached, so the fast suite still runs
on a machine with no phone. Run just these with ``-m device``.

These exercise the real screen, which is the only way to know the accessibility
tree parsing matches what this Android version emits. They cover the surface
that unit tests against recorded fixtures cannot: the live `uiautomator` dump,
the live `dumpsys` shapes behind foreground and lock detection, and the real
`input` argument path.
"""

from __future__ import annotations

import pytest

from phone_control.adb import AndroidPhone
from phone_control.apps import InstalledApps
from phone_control.device_state import read_device_state
from phone_control.screen import read_screen
from phone_control.screenshots import capture_screen

HOME_KEYCODE = "3"


WAKE_KEYCODE = "224"  # KEYCODE_WAKEUP


def a_phone_is_attached() -> bool:
    try:
        return AndroidPhone().is_connected()
    except Exception:
        return False


pytestmark = [
    pytest.mark.device,
    pytest.mark.skipif(
        not a_phone_is_attached(), reason="no Android phone is attached over USB"
    ),
]


@pytest.fixture(scope="module")
def phone() -> AndroidPhone:
    handle = AndroidPhone()
    # A dozing screen dumps a single bare node, which would look like a parser
    # failure rather than a phone that is simply asleep.
    handle.run(["shell", "input", "keyevent", WAKE_KEYCODE])
    return handle


def require_a_readable_screen(phone: AndroidPhone) -> None:
    """Skip when the phone is asleep or locked; that is a precondition, not a bug."""
    state = read_device_state(phone)
    if state.is_screen_on is False:
        pytest.skip("the phone's screen is off")
    if state.is_locked:
        pytest.skip("the phone is locked, so only the lock screen is readable")


def test_the_phone_reports_its_model_and_screen_size(phone):
    state = read_device_state(phone)

    assert state.model
    assert state.screen_width > 0
    assert state.screen_height > 0


def test_reading_the_screen_finds_named_controls(phone):
    require_a_readable_screen(phone)

    screen = read_screen(phone)

    assert screen.width > 0
    assert screen.height > 0
    assert screen.controls, (
        "No controls were found. If the phone is on its lock screen, unlock it "
        "and run this again."
    )


def test_every_listed_control_lies_inside_the_screen(phone):
    require_a_readable_screen(phone)

    screen = read_screen(phone)

    for control in screen.controls:
        assert 0 <= control.area.center_x <= screen.width
        assert 0 <= control.area.center_y <= screen.height


def test_the_installed_app_list_is_not_empty(phone):
    assert len(InstalledApps(phone).packages()) > 5


def test_a_screenshot_comes_back_as_a_real_image(phone):
    image_bytes, mime_type = capture_screen(phone, max_width=600)

    assert mime_type in {"image/png", "image/jpeg"}
    assert len(image_bytes) > 1000


def test_pressing_home_leaves_a_screen_that_can_be_read(phone):
    require_a_readable_screen(phone)

    phone.run(["shell", "input", "keyevent", HOME_KEYCODE])

    assert read_screen(phone).controls
