"""Cheap questions about the phone's overall state.

These read system properties and dumpsys output rather than the accessibility
tree, so they stay fast and keep working when the UI is mid-animation or an app
has hung.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .adb import AndroidPhone

SCREEN_SIZE_PATTERN = re.compile(r"(?:Override|Physical) size:\s*(\d+)x(\d+)")
PACKAGE_ACTIVITY_PATTERN = re.compile(
    r"([A-Za-z][A-Za-z0-9_]*(?:\.[A-Za-z0-9_]+)+)/[A-Za-z0-9_.$]+"
)
BATTERY_LEVEL_PATTERN = re.compile(r"level:\s*(\d+)")

# Window blocks look like `Window #12 Window{ab12cd u0 Title}:` followed by that
# window's properties, so a record ends where the next one begins.
WINDOW_HEADER_PATTERN = re.compile(r"^\s*Window #\d+ Window\{[^}]*\}\s*:?\s*$")
WINDOW_TITLE_PATTERN = re.compile(
    r"Window #\d+ Window\{[^}]*\s([^}\s][^}]*)\}\s*:?\s*$"
)
WINDOW_TYPE_PATTERN = re.compile(r"\bty=([A-Z_]+)")
WINDOW_SURFACE_PATTERN = re.compile(r"\bmHasSurface=(\w+)")
ROTATION_PATTERN = re.compile(r"\bmRotation=(\d+)")

# The soft keyboard is its own window. The input-method service's own flags
# disagree with each other on Android 17 (`mInputShown=false` alongside
# `mIsInputViewShown=true`), so the window list is the trustworthy source.
KEYBOARD_WINDOW_TYPE = "INPUT_METHOD"

# Window types that sit above an app and can swallow a tap. The notification
# shade, taskbar and system bars are deliberately absent: they are permanent
# windows, present whether or not they are in the way, so naming them would be
# noise rather than a warning.
OVERLAY_WINDOW_TYPES = frozenset(
    {
        "APPLICATION_OVERLAY",
        "SYSTEM_ERROR",
        "SYSTEM_ALERT",
        "TOAST",
        "INPUT_METHOD",
        "APPLICATION_ATTACHED_DIALOG",
    }
)

FOCUS_MARKERS = (
    "mCurrentFocus",
    "mFocusedApp",
    "mResumedActivity",
    "topResumedActivity",
)

# `dumpsys window` is tried first because it is far quicker than
# `dumpsys activity`, which can hit its own ten-second dump timeout on a busy
# device and return the wrong answer rather than an error.
FAST_FOREGROUND_COMMAND = "dumpsys window"
FALLBACK_FOREGROUND_COMMAND = "dumpsys activity top-resumed"

STATE_READ_TIMEOUT_SECONDS = 20.0


@dataclass(frozen=True)
class WindowRecord:
    """One window the window manager knows about."""

    title: str
    window_type: str
    has_surface: bool

    @property
    def is_on_screen(self) -> bool:
        return self.has_surface

    @property
    def is_overlay(self) -> bool:
        """Whether this window can sit above an app and swallow a tap."""
        return self.window_type in OVERLAY_WINDOW_TYPES and self.has_surface


@dataclass(frozen=True)
class DeviceState:
    """A snapshot of the phone that costs little to collect."""

    serial: str
    model: str
    android_version: str
    screen_width: int
    screen_height: int
    foreground_package: str
    focused_window: str
    is_screen_on: bool | None
    is_locked: bool | None
    battery_percent: int | None
    rotation: int | None = None
    keyboard_shown: bool | None = None
    visible_windows: tuple[WindowRecord, ...] = ()

    @property
    def overlays(self) -> tuple[WindowRecord, ...]:
        """Windows sitting above the app, such as the keyboard or a dialog."""
        return tuple(window for window in self.visible_windows if window.is_overlay)

    def as_text(self) -> str:
        lines = [
            f"Model: {self.model or 'unknown'} (serial {self.serial})",
            f"Android: {self.android_version or 'unknown'}",
            f"Screen: {self.screen_width}x{self.screen_height}",
            (
                f"Foreground app: {self.foreground_package}"
                if self.foreground_package
                else (
                    f"Focus: {self.focused_window} (a system window, not an app)"
                    if self.focused_window
                    else "Foreground app: unknown"
                )
            ),
            f"Screen on: {_describe_flag(self.is_screen_on)}",
            f"Locked: {_describe_flag(self.is_locked)}",
        ]
        if self.rotation is not None:
            lines.append(f"Rotation: {self.rotation}")
        if self.keyboard_shown is not None:
            lines.append(f"Keyboard shown: {_describe_flag(self.keyboard_shown)}")
        if self.battery_percent is not None:
            lines.append(f"Battery: {self.battery_percent}%")
        if self.overlays:
            named = ", ".join(
                f"{window.title or 'unnamed'} ({window.window_type})"
                for window in self.overlays
            )
            lines.append(f"Windows above the app: {named}")
        return "\n".join(lines)


def _describe_flag(value: bool | None) -> str:
    if value is None:
        return "unknown"
    return "yes" if value else "no"


def parse_windows(window_dump: str) -> tuple[WindowRecord, ...]:
    """Every window in a `dumpsys window` dump, with its type and visibility.

    The keyboard and any overlay live in windows of their own, so a reading that
    only knows the foreground app cannot tell that the lower half of the screen
    is covered. Each window block runs until the next `Window #n` header.
    """
    windows: list[WindowRecord] = []
    title = ""
    window_type = ""
    has_surface = False
    collecting = False

    def finish() -> None:
        if collecting:
            windows.append(
                WindowRecord(
                    title=title,
                    window_type=window_type,
                    has_surface=has_surface,
                )
            )

    for line in window_dump.splitlines():
        if WINDOW_HEADER_PATTERN.match(line):
            finish()
            collecting = True
            match = WINDOW_TITLE_PATTERN.search(line)
            title = match.group(1).strip() if match else ""
            window_type = ""
            has_surface = False
            continue
        if not collecting:
            continue
        found_type = WINDOW_TYPE_PATTERN.search(line)
        if found_type:
            window_type = found_type.group(1)
        found_surface = WINDOW_SURFACE_PATTERN.search(line)
        if found_surface:
            has_surface = found_surface.group(1).casefold() == "true"
    finish()
    return tuple(windows)


@dataclass(frozen=True)
class Focus:
    """What the user is looking at.

    ``window`` is the focused window's name, which may be a system window such as
    ``NotificationShade`` rather than an app. ``package`` is empty in that case,
    and that emptiness is the honest answer: no app is in front, whatever the
    activity manager still lists as the focused app.
    """

    package: str
    window: str

    @property
    def is_an_app(self) -> bool:
        return bool(self.package)


def read_focus(window_dump: str) -> Focus:
    """The focused window, preferring ``mCurrentFocus`` over ``mFocusedApp``.

    These two disagree, and ``mFocusedApp`` is the one that lies: observed on a
    real device with the lock screen up, ``mCurrentFocus`` named
    ``NotificationShade`` while ``mFocusedApp`` still named Settings behind it.
    Reporting the app there tells the caller the user is somewhere they are not.
    """
    match = CURRENT_FOCUS_PATTERN.search(window_dump)
    if match:
        name = match.group(1).strip()
        if "/" in name:
            return Focus(package=name.split("/", 1)[0], window=name)
        # A bare name is a system window: real focus, but no app behind it.
        return Focus(package="", window=name)

    # No focus line at all, so the activity manager is the only witness left.
    fallback = FOCUSED_APP_PATTERN.search(window_dump)
    if fallback:
        return Focus(package=fallback.group(1), window=fallback.group(1))

    # `dumpsys activity top-resumed` names its activity as plain
    # `ACTIVITY pkg/activity` or `mActivityComponent=pkg/activity`, with neither
    # marker, so the last resort is any package-and-activity pair on any line.
    # Only reached when the window manager said nothing at all.
    anywhere = ANY_ACTIVITY_PATTERN.search(window_dump)
    if anywhere:
        return Focus(package=anywhere.group(1), window=anywhere.group(1))
    return Focus(package="", window="")


def read_focused_window(phone: AndroidPhone) -> Focus:
    """Ask the window manager what has focus."""
    try:
        dump = phone.shell(FAST_FOREGROUND_COMMAND, timeout=STATE_READ_TIMEOUT_SECONDS)
    except Exception:
        dump = ""

    focus = read_focus(dump)
    if focus.window:
        return focus

    try:
        fallback_dump = phone.shell(
            FALLBACK_FOREGROUND_COMMAND, timeout=STATE_READ_TIMEOUT_SECONDS
        )
    except Exception:
        return focus
    return read_focus(fallback_dump)


def foreground_package(phone: AndroidPhone) -> str:
    """The package in front of the user, or an empty string when none is."""
    return read_focused_window(phone).package


def screen_size(phone: AndroidPhone) -> tuple[int, int]:
    """The screen size the phone is actually using, honouring any override."""
    try:
        output = phone.shell("wm size", timeout=STATE_READ_TIMEOUT_SECONDS)
    except Exception:
        return 0, 0
    # An override, when present, is printed last and is the size actually used.
    matches = SCREEN_SIZE_PATTERN.findall(output)
    if not matches:
        return 0, 0
    width, height = matches[-1]
    return int(width), int(height)


ANY_ACTIVITY_PATTERN = re.compile(
    r"([A-Za-z][A-Za-z0-9_]*(?:\.[A-Za-z0-9_]+)+)/[A-Za-z0-9_.$]+"
)
CURRENT_FOCUS_PATTERN = re.compile(r"mCurrentFocus=Window\{[^}]*?\s([^}\s]+)\}")
# Both of these name an app in the `pkg/activity` form, and neither is as
# trustworthy as mCurrentFocus, so they are only consulted when it is absent.
FOCUSED_APP_PATTERN = re.compile(
    r"(?:mFocusedApp|topResumedActivity)=.*?"
    r"([A-Za-z][A-Za-z0-9_]*(?:\.[A-Za-z0-9_]+)+)/[A-Za-z0-9_.$]+"
)

# `mShowingLockscreen` has not existed since Android 8. What survives is
# DisplayPolicy's line, which carries two fields:
#     mShowingDream=false mDreamingLockscreen=true
#     isKeyguardShowing=true
# Testing the whole line for "=true" therefore reports a locked phone whenever a
# screensaver is up, so each field is matched by name rather than by line.
KEYGUARD_SHOWING_PATTERN = re.compile(r"\bisKeyguardShowing=(\w+)")
DREAMING_LOCKSCREEN_PATTERN = re.compile(r"\bmDreamingLockscreen=(\w+)")
KEYGUARD_SERVICE_SHOWING_PATTERN = re.compile(r"^\s*showing=(\w+)", re.MULTILINE)


def _read_lock_state(phone: AndroidPhone, window_dump: str) -> bool | None:
    """Whether the keyguard is up, read from the field that actually says so."""
    for pattern in (KEYGUARD_SHOWING_PATTERN, DREAMING_LOCKSCREEN_PATTERN):
        match = pattern.search(window_dump)
        if match:
            return match.group(1).casefold() == "true"

    try:
        keyguard_dump = phone.shell(
            "dumpsys keyguard", timeout=STATE_READ_TIMEOUT_SECONDS
        )
    except Exception:
        return None
    match = KEYGUARD_SERVICE_SHOWING_PATTERN.search(keyguard_dump)
    if match:
        return match.group(1).casefold() == "true"
    return None


def _read_screen_on(phone: AndroidPhone) -> bool | None:
    try:
        output = phone.shell("dumpsys power", timeout=STATE_READ_TIMEOUT_SECONDS)
    except Exception:
        return None
    for line in output.splitlines():
        if "mWakefulness=" in line:
            return "Awake" in line
    return None


def _read_battery_percent(phone: AndroidPhone) -> int | None:
    try:
        output = phone.shell("dumpsys battery", timeout=STATE_READ_TIMEOUT_SECONDS)
    except Exception:
        return None
    match = BATTERY_LEVEL_PATTERN.search(output)
    return int(match.group(1)) if match else None


def read_device_state(phone: AndroidPhone) -> DeviceState:
    """Collect a cheap snapshot of the phone."""
    serial = phone.serial_number()
    model = phone.shell("getprop ro.product.model").strip()
    android_version = phone.shell("getprop ro.build.version.release").strip()

    try:
        window_dump = phone.shell(
            FAST_FOREGROUND_COMMAND, timeout=STATE_READ_TIMEOUT_SECONDS
        )
    except Exception:
        window_dump = ""

    focus = read_focus(window_dump)
    if not focus.window:
        focus = read_focused_window(phone)

    width, height = screen_size(phone)
    windows = parse_windows(window_dump)
    keyboard_shown = (
        any(
            window.window_type == KEYBOARD_WINDOW_TYPE and window.has_surface
            for window in windows
        )
        if windows
        else None
    )
    rotation_match = ROTATION_PATTERN.search(window_dump)
    return DeviceState(
        serial=serial,
        model=model,
        android_version=android_version,
        screen_width=width,
        screen_height=height,
        foreground_package=focus.package,
        focused_window=focus.window,
        is_screen_on=_read_screen_on(phone),
        is_locked=_read_lock_state(phone, window_dump),
        battery_percent=_read_battery_percent(phone),
        rotation=int(rotation_match.group(1)) if rotation_match else None,
        keyboard_shown=keyboard_shown,
        visible_windows=windows,
    )
