"""Reading a screen the phone will not describe, by looking at it.

There are screens Android will not dump. Not "dumps badly" - will not dump at all.
`uiautomator` waits for the interface to go *idle* before capturing, and a page with a
live figure, an animating icon or a playing video never does, so the tool returns
nothing for ever however long you wait. Measured on a Pixel 8a:

    $ adb shell am start -a android.settings.DEVICE_INFO_SETTINGS
    $ adb shell uiautomator dump /sdcard/a.xml
    ERROR: could not get idle state.        # plain and --windows alike, 19 seconds

and the same for the alarm picker, the battery page, and anything playing video. The
window in front still names *something* - `dumpsys window` needs no idle - but for a
Settings page reached by tapping it is a generic `SubSettings`, and for a video it is
the same activity as the home screen. So neither route tells the loop what is there.

A screenshot does. This reads the text in one with macOS Vision, which is the same
technique the reference implementation uses on the desktop, and hands back the lines.

**What it is for, and what it is not.** It is a *fallback*, tried only after the
accessibility tree has already failed, because a screenshot and a recognition pass cost
far more than a dump and the tree gives structure that text lines cannot. And it
furnishes lines rather than controls: the loop can then read a screen and judge whether
a goal is met, but it cannot tap what it has only seen a picture of. That is a real
limit and the honest way round it is that the loop treads carefully on such a screen -
it can conclude, and it can leave.

Nothing here is a hard dependency. Without pyobjc the function returns no lines, the
loop behaves exactly as it did before, and no import fails.
"""

from __future__ import annotations

from typing import Any

# Set below `accurate` and above `fast`: the default is accurate and slow, and a phone
# screenshot of a settings page is a small, clean image.
RECOGNITION_LEVEL_ACCURATE = 1

# Enough for any screen. A page of settings is 20 to 40 lines; past a few hundred
# something has gone wrong and the state would be paying for it.
MOST_LINES = 200


def macos_can_read_pictures() -> bool:
    """Whether this machine has the framework this route needs."""
    try:
        import Vision  # noqa: F401

        return True
    except Exception:
        return False


def the_lines_in(image_bytes: bytes, most: int = MOST_LINES) -> list[str]:
    """Every line of text in a picture, in the order the reader found them.

    Returns nothing rather than raising when the route is unavailable or the picture
    cannot be read: a fallback that fails must leave the caller where it was, not take
    the run down with it.
    """
    try:
        import Vision
        from Foundation import NSData
    except Exception:
        return []

    try:
        data = NSData.dataWithBytes_length_(image_bytes, len(image_bytes))
        handler = Vision.VNImageRequestHandler.alloc().initWithData_options_(data, None)
        request = Vision.VNRecognizeTextRequest.alloc().init()
        request.setRecognitionLevel_(RECOGNITION_LEVEL_ACCURATE)
        handler.performRequests_error_([request], None)

        lines: list[str] = []
        for observation in request.results() or []:
            candidates = observation.topCandidates_(1)
            if candidates and len(candidates):
                said = str(candidates[0].string()).strip()
                if said:
                    lines.append(said)
            if len(lines) >= most:
                break
        return lines
    except Exception:
        return []


def read_the_picture(phone: Any, most: int = MOST_LINES) -> list[str]:
    """The text on whatever the phone is showing, read from a picture of it."""
    if not macos_can_read_pictures():
        return []
    try:
        from .screenshots import capture_screen

        # Full width. Downscaling is what makes a screenshot cheap enough to take
        # often, and its cost is exactly the small text a settings page is made of -
        # and this route exists for the screens that have nothing else.
        image, _mime = capture_screen(phone, max_width=0)
    except Exception:
        return []
    return the_lines_in(image, most=most)


# --- reading a saved picture, for the tests ---------------------------------


def the_lines_in_a_file(path: str, most: int = MOST_LINES) -> list[str]:
    """The same reading, from a picture on disk.

    Exists so the route can be tested against a screenshot taken once, rather than
    needing a phone held at a particular screen for every run of the suite.
    """
    try:
        with open(path, "rb") as handle:
            return the_lines_in(handle.read(), most=most)
    except OSError:
        return []
