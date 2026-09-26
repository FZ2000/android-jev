"""Putting the phone into a known state before a scenario runs.

A test that does not establish where it starts is measuring whatever the last
test left behind. Three failures in this suite came from exactly that: a scroll
check that ran on a remembered Settings sub-page, a search check that read a
query left in the address bar by an earlier run, and a Bluetooth check that began
on a YouTube screen and failed a phone that was working perfectly.

Two rules follow. Every scenario states its own starting state here, and every
scenario is checked to *not* already satisfy its goal before the run begins - a
scenario that starts finished measures nothing at all.
"""

from __future__ import annotations

import time

WAKE_KEYCODE = "224"
HOME_KEYCODE = "3"
BACK_KEYCODE = "4"
SETTLE_SECONDS = 2.0

# Where a Settings screen is reached by intent rather than by tapping. The
# activity names are the ones this Android version actually exposes; asking for
# the top level by intent is the only way to be sure the app did not reopen a
# page it remembered from last time.
SETTINGS_TOP_LEVEL = "android.settings.SETTINGS"

# Packages this device actually has, read from it rather than guessed. A
# scenario that names an app it does not have would fail as a mysterious wrong
# answer instead of as "this phone has no such app".
SETTINGS = "com.android.settings"
# Settings search is its own package, and it is the one that remembers the query.
SETTINGS_SEARCH = "com.google.android.settings.intelligence"
CLOCK = "com.google.android.deskclock"
CALCULATOR = "com.google.android.calculator"
CAMERA = "com.google.android.GoogleCamera"
CHROME = "com.android.chrome"
YOUTUBE = "com.google.android.youtube"
KEEP = "com.google.android.keep"
CALENDAR = "com.google.android.calendar"
PHOTOS = "com.google.android.apps.photos"
MAPS = "com.google.android.apps.maps"
FILES = "com.google.android.apps.nbu.files"
RECORDER = "com.google.android.apps.recorder"
MESSAGES = "com.google.android.apps.messaging"
DIALER = "com.google.android.dialer"
PLAY_STORE = "com.android.vending"

CLOCK_ACTIVITY = f"{CLOCK}/com.android.deskclock.DeskClock"
CAMERA_ACTIVITY = (
    "com.google.android.GoogleCamera/com.android.camera.CameraImageActivity"
)

PHOTO_DIRECTORY = "/sdcard/DCIM/Camera"


def go_home(phone, settle: float = SETTLE_SECONDS) -> None:
    """The launcher, with nothing in front of it."""
    phone.run(["shell", "input", "keyevent", HOME_KEYCODE])
    time.sleep(settle)


def wake(phone) -> None:
    phone.run(["shell", "input", "keyevent", WAKE_KEYCODE])
    time.sleep(0.5)


def awake_and_unlocked(phone) -> str:
    """Make sure the phone can be read, and say why not when it cannot.

    Returns an empty string when the phone is ready, or the reason it is not.

    This is a precondition rather than a test of anything, and it was missing: a
    tier run began with the phone asleep - it had been idle while the code was being
    edited - and `am start` on a sleeping phone starts the activity without drawing
    it, so `uiautomator dump` returned no hierarchy at all. Five scenarios in a row
    crashed on that, and the crash said "the screen could not be read" without saying
    why, because nothing had checked.
    """
    from phone_control.device_state import read_device_state

    wake(phone)
    state = read_device_state(phone)
    if state.is_screen_on is False:
        return "the phone's screen is off and would not wake"
    if state.is_locked:
        return "the phone is locked, so only the lock screen is readable"
    return ""


def stop(phone, *packages: str, settle: float = 1.0) -> None:
    """Force-stop apps, so a scenario cannot inherit their last screen."""
    for package in packages:
        phone.run(["shell", "am", "force-stop", package])
    time.sleep(settle)


def open_settings_at(
    phone, action: str = SETTINGS_TOP_LEVEL, settle: float = 2.5
) -> None:
    """Start a specific Settings screen by intent, from a clean app state.

    Stopping Settings is not enough on its own, and the reason is worth knowing:
    the search screen is a *different package*, `com.google.android.settings.
    intelligence`, and it remembers the last query typed into it. Measured: with
    only Settings stopped, launching it came back to the search screen holding a
    stale "GT", because Settings had been left on search and search had kept the
    text. An explicit intent is not a clean starting state on its own - the app
    has to be stopped, and so does everything that holds the state it restores.
    """
    stop(phone, SETTINGS, SETTINGS_SEARCH)
    phone.run(["shell", "am", "start", "-a", action])
    time.sleep(settle)


def a_clean_launcher(phone) -> None:
    """Home, with the apps a scenario might touch stopped first."""
    stop(phone, CHROME, YOUTUBE)
    go_home(phone)


def a_fresh_browser(phone) -> None:
    """Chrome stopped and the launcher showing, so nothing stale is on screen.

    Chrome reopens the tab it was last on. A previous scenario's address bar is
    how an earlier version of the search check passed on a run that had navigated
    nowhere.
    """
    stop(phone, CHROME, YOUTUBE)
    go_home(phone)


def a_fresh_youtube(phone) -> None:
    stop(phone, YOUTUBE, CHROME)
    go_home(phone)


def the_settings_root(phone) -> None:
    open_settings_at(phone)


def bluetooth(phone, on: bool) -> None:
    """Set Bluetooth by the device's own switch, so the state is certain.

    Settings is force-stopped as well, because Android reopens it on the page it was
    last on. Without this the run starts on whatever sub-page an earlier scenario
    left behind and spends its steps navigating back to the top level - which is
    what the bluetooth scenario did, reaching the goal only after the repeat rule
    had already called it a stall.
    """
    phone.shell(f"settings put global bluetooth_on {1 if on else 0}")
    phone.shell(f"svc bluetooth {'enable' if on else 'disable'}")
    stop(phone, SETTINGS, settle=0.5)
    time.sleep(1.0)


def wi_fi(phone, on: bool) -> None:
    phone.shell(f"svc wifi {'enable' if on else 'disable'}")
    phone.shell(f"settings put global wifi_on {1 if on else 0}")
    stop(phone, SETTINGS, settle=0.5)
    time.sleep(2.0)


def aeroplane_mode(phone, on: bool) -> None:
    phone.shell(f"settings put global airplane_mode_on {1 if on else 0}")
    phone.shell(
        f"am broadcast -a android.intent.action.AIRPLANE_MODE --ez state {'true' if on else 'false'}"
    )
    stop(phone, SETTINGS, settle=0.5)
    time.sleep(2.0)


def the_camera(phone, settle: float = 4.0) -> None:
    """The camera app, stopped and then started, ready to take a photo.

    The activity is the one this device resolves for STILL_IMAGE_CAMERA, read
    from it rather than assumed: the launcher activity name is not the one that
    answers that intent.
    """
    stop(phone, CAMERA)
    phone.run(
        [
            "shell",
            "am",
            "start",
            "-a",
            "android.media.action.STILL_IMAGE_CAMERA",
            "-n",
            CAMERA_ACTIVITY,
        ]
    )
    time.sleep(settle)


def the_clock(phone, settle: float = 2.5) -> None:
    stop(phone, CLOCK)
    phone.run(["shell", "am", "start", "-n", CLOCK_ACTIVITY])
    time.sleep(settle)


def an_app(phone, package: str, settle: float = 3.0) -> None:
    """Start a named app from a stopped state, so it opens where it always does."""
    stop(phone, package)
    phone.run(
        [
            "shell",
            "monkey",
            "-p",
            package,
            "-c",
            "android.intent.category.LAUNCHER",
            "1",
        ]
    )
    time.sleep(settle)
