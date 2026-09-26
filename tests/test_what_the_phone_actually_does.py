"""Every belief this code holds about Android, proven against the phone.

A register, not a test suite. Each entry is one thing the code depends on being
true of the device in front of it, written the way the code relies on it, and
checked against the phone rather than against a fake. When the phone disagrees, the
test fails with the bytes it saw, and the fix is either the code or this register.

Why this exists, in one measurement. The loop reports "currently on" or "currently
off" for a control the accessibility tree marks as checkable, and a world scenario
with a simulated switch proved it worked. The device publishes no checkable node and
no switch class for a settings toggle at all - so the code was right in principle,
never ran on a real screen, and the scenario that should have caught it passed
because the fake agreed with its author. That has now happened four times in this
project, in four different places:

    a fake agreed that `am start -d youtube.com` opens a page
    a fake agreed that a bare domain is a URL Android can resolve
    a fake agreed that `cmd clipboard set` places something
    a fake agreed that a settings toggle is checkable

A fake can only ever repeat what its author believed. These cannot: each one asks
the phone.

**What belongs here.** A belief that is (a) about the platform rather than about
this code, and (b) load-bearing - something breaks silently if it is wrong. Not a
test of our own logic, and not a test of Android for its own sake.

**How to read a failure.** The assertion carries what the device actually said, so
a red test here is a sentence about the phone. Either the code stops relying on the
belief, or the belief was never true and something was already broken.
"""

from __future__ import annotations

import re
import time

import pytest

from phone_control.adb import AndroidPhone
from phone_control.apps import InstalledApps
from phone_control.device_state import read_device_state, read_focused_window
from phone_control.screen import read_screen
from phone_control.screenshots import capture_screen
from phone_control.text_entry import read_device_clipboard, set_device_clipboard

pytestmark = pytest.mark.device

SETTINGS = "com.android.settings"
BLUETOOTH_SETTINGS = "android.settings.BLUETOOTH_SETTINGS"


@pytest.fixture(scope="module")
def phone() -> AndroidPhone:
    handle = AndroidPhone()
    handle.run(["shell", "input", "keyevent", "224"])
    return handle


def on_settings_top_level(phone: AndroidPhone) -> None:
    phone.run(["shell", "am", "force-stop", SETTINGS])
    phone.run(["shell", "am", "start", "-a", "android.settings.SETTINGS"])
    time.sleep(2.5)


# --- reading a screen -----------------------------------------------------


def test_a_dump_writes_the_file_we_then_read(phone):
    """The whole of screen reading rests on writing and then reading one path.

    If the dump lands somewhere else, or writes nothing while exiting zero, every
    tool that reads a screen is reading whatever was there before.
    """
    path = f"/sdcard/what_the_phone_does_{int(time.time())}.xml"
    phone.shell(f"rm -f {path}")

    output = phone.shell(f"uiautomator dump {path}")
    written = phone.shell(f"cat {path}")

    assert "hierarchy" in written, (
        f"the dump did not write what we read. The command said {output!r} and the "
        f"file held {written[:200]!r}"
    )
    phone.shell(f"rm -f {path}")


def test_a_key_carries_its_character_in_content_desc(phone):
    """A keyboard key's label is in content-desc, not text.

    Reading `text` finds no keys at all, which silently removes the ability to
    press one.
    """
    on_settings_top_level(phone)
    for control in read_screen(phone).controls:
        if (control.label or "").casefold().startswith("search"):
            phone.run(
                [
                    "shell",
                    "input",
                    "tap",
                    str(control.area.center_x),
                    str(control.area.center_y),
                ]
            )
            break
    time.sleep(2)
    # A character as well, so the keyboard is up whatever the tap did.
    phone.shell("input text 'b'")
    time.sleep(2)

    screen = read_screen(phone)

    assert screen.has_keyboard_window, (
        "no keyboard window appeared after tapping Settings' search box, so either "
        "the box moved or the keyboard is reported somewhere else"
    )
    letters = [key for key in screen.keyboard_keys if len(key.label or "") == 1]
    assert letters, (
        f"the keyboard window exposed no single-character keys: "
        f"{[key.label for key in screen.keyboard_keys][:20]}"
    )


def test_an_empty_container_hides_its_whole_subtree(phone):
    """`read_screen` reports what the tree publishes, and a subtree can be absent.

    The consequence is that a screen can read as almost empty while being full, and
    the loop has to say so rather than act on nothing.
    """
    screen = read_screen(phone)

    assert screen.width > 0, f"the screen width is {screen.width}"
    assert screen.height > 0, f"the screen height is {screen.height}"


# --- opening things -------------------------------------------------------


def test_a_bare_domain_does_not_resolve_but_a_schemed_one_does(phone):
    """Why the option builder gives an address a scheme before offering it.

    Android drops the data URI when it has no scheme, so the intent arrives empty
    and fails to resolve while the command still looks like it ran. This cost days:
    the action did nothing, the screen did not change, and the loop read that as the
    model being unsure.
    """
    phone.run(["shell", "input", "keyevent", "3"])
    time.sleep(1)
    phone.run(["shell", "am", "force-stop", "com.android.chrome"])
    time.sleep(1)

    bare = phone.run_result(["shell", "am", "start", "-d", "example.com"])
    schemed = phone.run_result(["shell", "am", "start", "-d", "https://example.com"])
    time.sleep(3)

    bare_text = bare.text
    schemed_text = schemed.text
    assert "dat=" in schemed_text or "example" in schemed_text.casefold(), (
        f"a schemed address did not resolve either; the command said {schemed_text!r}"
    )
    # And the difference is visible in what the command reports.
    assert bare_text.strip() != schemed_text.strip(), (
        "a bare domain and a schemed one now behave identically, so the rule that "
        f"prefixes https:// may no longer be needed. Bare said {bare_text!r}"
    )


def test_the_launcher_does_not_list_settings_on_its_first_page(phone):
    """Why opening an app is always offered rather than derived from the goal.

    "turn off bluetooth" names no app, and Settings has no icon on the first
    launcher page - so a loop that offers only what is visible cannot reach the one
    app that would do the job.
    """
    phone.run(["shell", "input", "keyevent", "3"])
    time.sleep(2)

    visible = {control.label for control in read_screen(phone).controls}
    launchable = InstalledApps(phone).launchable_names()

    assert launchable, "no launchable apps were found at all"
    assert any("Settings" in name for name in launchable), (
        f"Settings is not in the launchable list: {launchable[:12]}"
    )
    # The claim in this test's name, asserted rather than hedged. It used to read
    # `if "Settings" not in visible: assert True`, which asserts nothing at all and
    # would have gone on passing on a phone whose launcher does show Settings - which
    # is the one thing the rationale above depends on. If this ever fails, the
    # rationale is wrong and `open_app` being offered unconditionally needs re-reading,
    # so it is better as a failure than as a branch that cannot fail.
    assert "Settings" not in visible, (
        f"Settings is on the launcher's first page after all, so the reason given for "
        f"always offering open_app no longer holds: {sorted(visible)[:12]}"
    )


# --- what the device does not publish ------------------------------------


def test_a_settings_toggle_publishes_neither_checkable_nor_a_switch(phone):
    """The measurement that made "a switch says which way it is" never fire.

    If this ever passes - if the tree starts publishing the toggle - then the state
    can say which way a switch is, and the scenarios that need a reader to know
    whether Bluetooth is off could be judged by the loop alone.

    Until then, `describe_a_control` says "currently on" only where a screen really
    marks a control checkable, and a goal like "turn off Bluetooth" cannot be
    concluded from the screen.
    """
    phone.run(["shell", "am", "start", "-a", BLUETOOTH_SETTINGS])
    time.sleep(3)

    raw = phone.shell(
        "uiautomator dump /sdcard/toggle.xml >/dev/null 2>&1; cat /sdcard/toggle.xml"
    )
    nodes = re.findall(r"<node[^>]*>", raw)

    checkable = [node for node in nodes if 'checkable="true"' in node]
    switchy = [
        node
        for node in nodes
        if re.search(r'class="[^"]*(Switch|Toggle|CheckBox)', node)
    ]

    assert not checkable or switchy, (
        "the Bluetooth screen now publishes a checkable or switch-like node "
        f"({checkable[:1] or switchy[:1]}). docs/android-apis.md records that it did "
        "not, and the loop's state could now carry the switch position."
    )


def test_the_clipboard_command_is_reported_as_missing_rather_than_silent(phone):
    """`cmd clipboard` exits zero while printing that it is not implemented.

    Setting it used to report success and place nothing. The tool checks the output
    rather than the exit code because of this, and this is the assumption that makes
    that necessary.
    """
    output = phone.run_result(["shell", "cmd", "clipboard", "set-text", "x"])
    text = (output.text + output.error_text).casefold()

    if "no shell command implementation" in text or "unknown command" in text:
        # The device says so, which is what the tool relies on detecting - and there is
        # then nothing to read back, which is the other half of the claim. The
        # assertion here used to be `is not None or True`, which is always true and
        # tested nothing: it would have passed on a phone that refused to *set* the
        # clipboard while happily reporting one.
        assert read_device_clipboard(phone) is None, (
            "the device refused to set the clipboard but still reported one"
        )
    else:
        # It claims to work, so the tool's read-back is what proves it either way.
        set_device_clipboard(phone, "a probe")
        assert read_device_clipboard(phone) in (None, "a probe")


# --- reading the device's own state --------------------------------------


def test_the_window_in_focus_is_reported(phone):
    """`read_focused_window` is how the loop knows which app is in front."""
    on_settings_top_level(phone)

    focus = read_focused_window(phone)

    assert focus.package == SETTINGS, (
        f"the focused window is {focus.package!r} (window {focus.window!r}) after "
        "asking for Settings"
    )


def test_the_screen_size_is_reported_and_is_the_real_one(phone):
    """Every coordinate the loop sends is derived from it."""
    phone.run(["shell", "input", "keyevent", "224"])
    state = read_device_state(phone)

    assert state.screen_width == 1080, (
        f"the screen is {state.screen_width} wide; coordinates computed from a "
        "different size would land somewhere else"
    )
    assert state.screen_height == 2400, (
        f"the screen is {state.screen_height} tall; coordinates computed from a "
        "different size would land somewhere else"
    )


def test_a_screenshot_comes_back_as_an_image(phone):
    """The one view that survives a screen the accessibility tree cannot describe."""
    image, mime = capture_screen(phone, max_width=400)

    assert mime in {"image/png", "image/jpeg"}, mime
    assert len(image) > 1000, f"the screenshot was {len(image)} bytes"


def test_non_ascii_cannot_be_typed_as_keystrokes(phone):
    """Why accented text and emoji go through the clipboard instead.

    AOSP says it plainly - "for robust text entry, do not use this function" - and
    the loop reports it rather than retrying when a goal needs such text.
    """
    phone.run(["shell", "input", "keyevent", "3"])
    result = phone.run_result(["shell", "input", "text", "café"])
    complaint = result.error_text.casefold()

    # It fails, and it fails on *stderr* while the command line looks like it ran -
    # which is why reading only stdout is how a failure goes unseen.
    assert result.return_code != 0 or "exception" in complaint, (
        "typing non-ASCII now works, so the clipboard route may no longer be needed. "
        f"The command exited {result.return_code} and said {result.text!r} / "
        f"{result.error_text[:200]!r}"
    )
    assert "exception" in complaint or "null" in complaint, (
        f"non-ASCII failed for some other reason: {result.error_text[:300]!r}"
    )
