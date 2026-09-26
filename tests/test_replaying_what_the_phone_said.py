"""Reading real phone output, in the default suite, with no phone attached.

Everything here runs against adb's own bytes, captured from a real Pixel 8a and
committed under `tests/recorded/`. Nobody chose this output, which is the point: the
hand-written fixtures elsewhere in this suite say what their author believed a phone
returns, and those beliefs have been wrong before - about a bare domain opening a
browser, about three spoken app names, about which node carries a switch's position.

Read `tests/recorded/a_transcript.py` for how the recording and the replay work. The
rule that makes it worth anything is at the end of this file: a command the
transcript never saw raises rather than answering.
"""

from __future__ import annotations

import pytest

from phone_control.apps import InstalledApps
from phone_control.device_state import read_device_state
from phone_control.screen import read_screen
from recorded import APhoneOnRecord, ATranscript, NotRecorded, replay
from recorded.a_transcript import a_command_that_can_be_matched, the_serial_in


def a_recorded_phone(name: str) -> APhoneOnRecord:
    return APhoneOnRecord(replay(name))


# --- the screen reader, against a real Compose dump ----------------------


def test_a_real_settings_dump_parses_into_controls():
    """44 KB of Compose output, from the phone, read by the real parser.

    The screen this comes from is the one this project has had the most trouble
    with: Android restores the page Settings was last on, and its rows are nested
    deeply enough that hand-written fixtures have twice been wrong about the shape.
    """
    screen = read_screen(a_recorded_phone("the-settings-list"))

    assert screen.package == "com.android.settings"
    assert len(screen.controls) > 10, (
        f"a real Settings screen parsed into only {len(screen.controls)} controls"
    )
    assert screen.width == 1080
    assert screen.height == 2400


def test_a_control_from_the_real_screen_can_be_found_by_name():
    screen = read_screen(a_recorded_phone("the-settings-list"))

    found = screen.best_match("Search Settings")

    assert found is not None, "the search row could not be found on a real screen"
    assert found.is_tappable or found.label


def test_every_control_on_the_real_screen_is_inside_the_screen():
    """A control outside the frame would be tapped off the edge of the phone."""
    screen = read_screen(a_recorded_phone("the-settings-list"))

    outside = [
        control
        for control in screen.controls
        if control.area.left < 0
        or control.area.top < 0
        or control.area.right > screen.width
        or control.area.bottom > screen.height
    ]

    assert outside == [], (
        f"{len(outside)} controls lie outside the screen: {outside[:3]}"
    )


def test_the_real_screen_has_no_password_in_it_that_should_not_be_there():
    """The parser drops a password field's contents, and this is a real screen."""
    screen = read_screen(a_recorded_phone("the-settings-list"))

    assert not any(control.is_password for control in screen.controls), (
        "the Settings list should have no password field on it"
    )


# --- the device state, against real dumpsys ------------------------------


def test_real_dumpsys_output_is_understood():
    """Four different dumpsys formats, parsed from what the phone actually printed."""
    state = read_device_state(a_recorded_phone("what-status-reads"))

    assert state.model, "the model did not come out of getprop"
    assert state.android_version, "the Android version did not come out of getprop"
    assert (state.screen_width, state.screen_height) != (0, 0), (
        "the screen size did not come out of `wm size`"
    )
    assert state.is_screen_on is not None, "`dumpsys power` was not understood"
    assert state.is_locked is not None, "the keyguard state was not understood"
    assert state.battery_percent is not None, "`dumpsys battery` was not understood"
    assert 0 <= state.battery_percent <= 100


def test_the_foreground_app_comes_out_of_the_real_window_dump():
    state = read_device_state(a_recorded_phone("what-status-reads"))

    assert state.foreground_package, (
        "no foreground package was read, so the window dump was not understood"
    )
    assert "." in state.foreground_package, (
        f"{state.foreground_package!r} does not look like a package id"
    )


# --- the app list, against the real phone's own list ---------------------


def test_real_app_names_resolve_to_real_packages():
    """The three names that were once wrong are checked against the real list."""
    apps = InstalledApps(a_recorded_phone("the-installed-apps"))
    installed = set(apps.packages())

    assert installed, "the recorded package list came back empty"

    for spoken, expected in (
        ("clock", "com.google.android.deskclock"),
        ("settings", "com.android.settings"),
        ("chrome", "com.android.chrome"),
    ):
        if expected not in installed:
            continue
        assert apps.resolve(spoken) == expected, (
            f"{spoken!r} resolved to {apps.resolve(spoken)!r} on the real list"
        )


def test_the_real_launcher_list_is_a_small_part_of_everything_installed():
    """The point of the launcher query: a few hundred packages is not a question."""
    apps = InstalledApps(a_recorded_phone("the-installed-apps"))

    assert apps.launchable(), "the launcher query came back empty"
    assert len(apps.launchable()) < len(apps.packages()), (
        "everything installed is launchable, which is not true of a real phone"
    )


# --- and the rule that makes a transcript worth believing -----------------


def test_a_command_that_was_never_recorded_raises_rather_than_answering():
    """The whole value of this layer.

    A fake that answers an unknown command with an empty string agrees with every
    question it is asked, including the wrong ones: the options are built from what
    a screen holds, and an empty screen builds no options and fails silently.
    """
    phone = a_recorded_phone("the-settings-list")

    with pytest.raises(NotRecorded) as refused:
        phone.shell("input keyevent 26")

    assert "input keyevent 26" in str(refused.value)
    assert "the-settings-list" in str(refused.value), (
        "the failure does not say which transcript was being replayed"
    )


def test_the_transcript_says_what_it_is_and_where_it_came_from():
    transcript = replay("the-settings-list")

    assert "Settings" in transcript.what_it_was()
    assert transcript.commands(), "the transcript holds no commands"


def test_the_serial_a_transcript_was_recorded_on_does_not_matter():
    """Otherwise a transcript would only replay on the phone it came from."""
    assert the_serial_in(
        ["/usr/bin/adb", "-s", "4C1F8A2E9D7B305", "shell", "true"]
    ) == [
        "shell",
        "true",
    ]


def test_a_dump_path_that_differs_every_run_still_matches():
    """The dump file is named with the process id and a random suffix, on purpose.

    Without matching that away, no screen-read transcript could ever be replayed -
    and the two commands around it, the dump and the read, both carry the path.
    """
    first = the_serial_in(
        ["adb", "exec-out", "cat", "/sdcard/dsh_screen_1234_ab12cd34.xml"]
    )
    second = the_serial_in(
        ["adb", "exec-out", "cat", "/sdcard/dsh_screen_9999_ff99ee88.xml"]
    )

    assert first == second


def test_two_different_commands_do_not_match_each_other():
    """Normalising must not make unrelated commands interchangeable."""
    dumped = a_command_that_can_be_matched(
        ["shell", "uiautomator dump /sdcard/dsh_screen_1_aa.xml"]
    )
    removed = a_command_that_can_be_matched(
        ["shell", "rm -f /sdcard/dsh_screen_1_aa.xml"]
    )

    assert dumped != removed


def test_asking_twice_gives_what_the_phone_said_each_time_in_order():
    """A screen read before and after an action is two different answers.

    Written against a transcript built here rather than a recorded one, because the
    recordings each ask for their commands once - and this is about the replayer's
    own rule, which decides whether a test replays a sequence or a single moment.
    """
    transcript = ATranscript(
        "a-made-up-session",
        [
            {"command": ["shell", "cat /tmp/screen.xml"], "stdout": "before"},
            {"command": ["shell", "cat /tmp/screen.xml"], "stdout": "after"},
        ],
    )
    phone = APhoneOnRecord(transcript)

    assert phone.shell("cat /tmp/screen.xml") == "before"
    assert phone.shell("cat /tmp/screen.xml") == "after"


def test_the_two_ways_of_running_a_command_are_not_interchangeable():
    """`shell` sends the whole line as one argument; `run` sends it split.

    A transcript is matched on what was really sent, so a test cannot pass by
    asking in the other shape - which is right, because adb would not treat the two
    the same either.
    """
    transcript = ATranscript(
        "a-made-up-session",
        [{"command": ["shell", "cat /tmp/screen.xml"], "stdout": "one way only"}],
    )
    phone = APhoneOnRecord(transcript)

    assert phone.shell("cat /tmp/screen.xml") == "one way only"
    with pytest.raises(NotRecorded):
        phone.run(["shell", "cat", "/tmp/screen.xml"])


def test_asking_more_times_than_were_recorded_repeats_the_last_answer():
    """Which is what polling a screen that has stopped moving looks like.

    Insisting a test declare this would be saying more about the fake than about
    the phone, so it repeats - and the transcript still counts every ask, which is
    how a test can tell how many times the loop looked.
    """
    transcript = ATranscript(
        "a-made-up-session",
        [{"command": ["shell", "cat /tmp/screen.xml"], "stdout": "the only answer"}],
    )
    phone = APhoneOnRecord(transcript)

    assert phone.shell("cat /tmp/screen.xml") == "the only answer"
    assert phone.shell("cat /tmp/screen.xml") == "the only answer"
    assert transcript.asked == [["shell", "cat /tmp/screen.xml"]] * 2
