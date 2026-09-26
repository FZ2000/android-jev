"""What the phone says about itself, and what happens when it will not say.

`status` is the cheapest tool in the server and the first one an agent calls, and
the reading behind it is full of "ask, and if that fails ask another way, and if
that fails say you do not know". Those paths were untested: the fallback command
that only runs when the first one comes back empty, the two ways of asking whether
the phone is locked, the battery and screen readings that return None rather than
guessing, and the screen size that has to prefer an override.

Unknown is a real answer here and the tests say so: a phone that will not report
its battery must not be reported as flat.
"""

from __future__ import annotations

import pytest

from phone_control.device_state import (
    FALLBACK_FOREGROUND_COMMAND,
    FAST_FOREGROUND_COMMAND,
    DeviceState,
    Focus,
    WindowRecord,
    _read_battery_percent,
    _read_lock_state,
    _read_screen_on,
    foreground_package,
    read_focused_window,
    screen_size,
)


class APhoneThatSays:
    """A phone whose shell answers by what the command contains."""

    def __init__(self, **answers) -> None:
        self.answers = answers
        self.commands: list[str] = []

    def shell(self, command: str, timeout=None) -> str:
        self.commands.append(command)
        for fragment, answer in self.answers.items():
            if fragment in command:
                if isinstance(answer, Exception):
                    raise answer
                return answer
        return ""

    def serial_number(self) -> str:
        return "a-serial"


WINDOW_DUMP = "  mCurrentFocus=Window{1234 u0 com.example.app/com.example.app.Main}\n"


# --- looking at what is in front -----------------------------------------


def test_the_window_manager_is_asked_first():
    phone = APhoneThatSays(**{"dumpsys window": WINDOW_DUMP})

    focus = read_focused_window(phone)

    assert focus.window == "com.example.app/com.example.app.Main"
    assert focus.package == "com.example.app"
    assert FAST_FOREGROUND_COMMAND in phone.commands


def test_when_that_says_nothing_the_other_way_is_tried():
    """The fallback exists because `dumpsys window` can come back empty on a busy
    device, and the honest answer there is not "no app is in front"."""
    phone = APhoneThatSays(
        **{
            "dumpsys activity top-resumed": (
                "  topResumedActivity=ActivityRecord{1 u0 com.other.app/.Main}\n"
            )
        }
    )

    focus = read_focused_window(phone)

    assert focus.package == "com.other.app"
    assert FALLBACK_FOREGROUND_COMMAND in phone.commands


def test_when_both_ways_fail_it_says_nothing_is_in_front():
    phone = APhoneThatSays(
        **{
            "dumpsys window": RuntimeError("adb went away"),
            "dumpsys activity": RuntimeError("still gone"),
        }
    )

    focus = read_focused_window(phone)

    assert focus.package == ""
    assert focus.is_an_app is False
    assert foreground_package(phone) == ""


# --- locked, awake, charged ----------------------------------------------


@pytest.mark.parametrize(
    ("said", "expected"),
    [("isKeyguardShowing=true", True), ("isKeyguardShowing=false", False)],
)
def test_the_lock_state_is_read_from_the_window_dump(said, expected):
    phone = APhoneThatSays()

    assert _read_lock_state(phone, said) is expected


def test_with_nothing_in_the_window_dump_the_keyguard_service_is_asked():
    phone = APhoneThatSays(**{"dumpsys keyguard": "  showing=true\n"})

    assert _read_lock_state(phone, "") is True


def test_a_lock_state_that_cannot_be_read_is_unknown_rather_than_assumed():
    """Assuming "unlocked" would send a run into a phone that is showing a lock
    screen, and assuming "locked" would refuse one that is not."""
    phone = APhoneThatSays(**{"dumpsys keyguard": RuntimeError("no keyguard service")})

    assert _read_lock_state(phone, "") is None
    assert _read_lock_state(APhoneThatSays(), "dumpsys keyguard: nothing here") is None


@pytest.mark.parametrize(
    ("said", "expected"),
    [
        ("mWakefulness=Awake", True),
        ("mWakefulness=Asleep", False),
        ("mWakefulness=Dozing", False),
    ],
)
def test_whether_the_screen_is_on_is_read_from_the_power_dump(said, expected):
    phone = APhoneThatSays(**{"dumpsys power": said})

    assert _read_screen_on(phone) is expected


def test_a_power_dump_that_cannot_be_read_is_unknown():
    assert (
        _read_screen_on(APhoneThatSays(**{"dumpsys power": RuntimeError("no")})) is None
    )
    assert (
        _read_screen_on(APhoneThatSays(**{"dumpsys power": "nothing useful"})) is None
    )


def test_the_battery_level_is_read_when_it_is_there():
    phone = APhoneThatSays(**{"dumpsys battery": "  level: 87\n  scale: 100\n"})

    assert _read_battery_percent(phone) == 87


def test_a_battery_level_that_cannot_be_read_is_unknown_not_flat():
    """Reporting 0% would look like a phone about to die."""
    assert (
        _read_battery_percent(APhoneThatSays(**{"dumpsys battery": RuntimeError("x")}))
        is None
    )
    assert (
        _read_battery_percent(APhoneThatSays(**{"dumpsys battery": "no level here"}))
        is None
    )


# --- the screen size, and the override -----------------------------------


def test_an_override_is_the_size_actually_used():
    """`wm size` prints the physical size first and the override last, and the
    override is what the phone is really using - a run that measured against the
    physical size would tap the wrong places."""
    phone = APhoneThatSays(
        **{"wm size": "Physical size: 1080x2400\nOverride size: 720x1600\n"}
    )

    assert screen_size(phone) == (720, 1600)


def test_with_no_override_the_physical_size_is_the_size():
    phone = APhoneThatSays(**{"wm size": "Physical size: 1080x2400\n"})

    assert screen_size(phone) == (1080, 2400)


def test_a_size_that_cannot_be_read_is_zero_rather_than_a_guess():
    assert screen_size(APhoneThatSays(**{"wm size": RuntimeError("no")})) == (0, 0)
    assert screen_size(APhoneThatSays(**{"wm size": "unknown"})) == (0, 0)


# --- and how it is all written down --------------------------------------


def a_state(**overrides) -> DeviceState:
    fields = {
        "serial": "a-serial",
        "model": "Pixel 8a",
        "android_version": "17",
        "screen_width": 1080,
        "screen_height": 2400,
        "foreground_package": "com.android.settings",
        "focused_window": "com.android.settings/.Settings",
        "is_screen_on": True,
        "is_locked": False,
        "battery_percent": 87,
    }
    fields.update(overrides)
    return DeviceState(**fields)


def test_the_summary_names_the_phone_and_where_it_is():
    written = a_state().as_text()

    assert "Pixel 8a" in written
    assert "com.android.settings" in written
    assert "1080x2400" in written
    assert "87%" in written


@pytest.mark.parametrize(("flagged", "expected"), [(True, "yes"), (False, "no")])
def test_a_flag_is_written_in_words(flagged, expected):
    written = a_state(keyboard_shown=flagged).as_text()

    assert f"Keyboard shown: {expected}" in written


def test_a_flag_that_was_never_read_is_left_out_rather_than_described():
    """The keyboard line only appears when the keyboard was actually read.

    Saying "unknown" for something never asked about would be noise in the one
    place an agent looks first.
    """
    assert "Keyboard shown" not in a_state(keyboard_shown=None).as_text()
    assert "unknown" in a_state(is_locked=None).as_text()


def test_a_window_above_the_app_is_named_as_a_warning():
    """The keyboard or a dialog can swallow a tap, which is worth saying."""
    keyboard = WindowRecord(
        title="InputMethod",
        window_type="INPUT_METHOD",
        has_surface=True,
    )
    written = a_state(visible_windows=(keyboard,)).as_text()

    assert "Windows above the app" in written
    assert "InputMethod" in written
    assert "INPUT_METHOD" in written


def test_an_unnamed_window_above_the_app_is_still_reported():
    unnamed = WindowRecord(title="", window_type="TOAST", has_surface=True)

    assert "unnamed (TOAST)" in a_state(visible_windows=(unnamed,)).as_text()


def test_a_window_without_a_surface_is_not_in_the_way():
    ghost = WindowRecord(title="Ghost", window_type="TOAST", has_surface=False)

    assert ghost.is_on_screen is False
    assert ghost.is_overlay is False


def test_a_permanent_system_window_is_not_called_an_overlay():
    """The status bar is always there, so naming it would be noise, not a warning."""
    status_bar = WindowRecord(
        title="StatusBar", window_type="STATUS_BAR", has_surface=True
    )

    assert status_bar.is_overlay is False


def test_a_focus_with_no_package_is_not_an_app():
    assert Focus(package="", window="NotificationShade").is_an_app is False
    assert Focus(package="com.example", window="a").is_an_app is True
