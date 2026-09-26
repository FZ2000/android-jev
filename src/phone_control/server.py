"""The tool surface an agent uses to drive an Android phone.

The headline is ``run_task``: the caller states an outcome in plain language and
the server owns the loop -- read the screen, decide, act, check, repeat -- until
the goal is reached or cannot be, and then reports what happened. An agent does
not need to know anything about accessibility trees, windows or coordinates to
use it.

The remaining tools are the primitives that loop is built from, exposed for the
cases it does not cover: a run that came back unfinished, a screen too visual for
the phone's own reading, or a need to inspect something part-way through.

Two decision models can drive the loop. Jev, a System One model, is asked one
constrained choice per step and returns a calibrated confidence alongside it; a
keyword matcher runs when no key is configured, so the server is useful without
one. The options are built in ``options``, the screen reading lives in ``screen``
and ``device_state``, and the loop itself is in ``goal``.
"""

from __future__ import annotations

import functools
import json
import re
import time
from typing import Annotated

import anyio
from mcp.server.mcpserver import Image, MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.shared.exceptions import MCPError
from pydantic import Field

from . import __version__
from .adb import AndroidPhone, shared_phone
from .apps import InstalledApps, launch_app, open_link, spoken_name
from .device_state import foreground_package, read_device_state, screen_size
from .errors import (
    AdbNotFound,
    JevNotConfigured,
    NoMatchingControl,
    NoPhoneConnected,
    PhoneCommandFailed,
    PhoneControlError,
    PhoneNotAuthorized,
)
from .jev import (
    JEV_MAX_ALTERNATIVES,
    JevClient,
    choice_question,
    scale_question,
    yes_or_no_question,
)
from .screen import Screen, ScreenControl, read_screen as read_screen_from_device
from .screenshots import DEFAULT_MAX_WIDTH, capture_screen
from .text_entry import (
    NON_ASCII_FIX,
    clear_the_focused_field,
    needs_non_ascii_workaround,
    paste_device_clipboard,
    read_device_clipboard,
    set_device_clipboard,
    type_text_on_device,
)

SERVER_INSTRUCTIONS = """\
This server drives an Android phone attached to this computer over USB.

Say what you want done and let the phone work out how. `run_task(goal)` hands
over the whole job: it reads the screen, decides each step, checks its own work
and repeats until the goal is reached or cannot be, then reports what it did and
why it stopped. It is the right first move for anything expressible as an
outcome -- "open the email app", "turn on aeroplane mode".

The other tools cover what it does not. Drive them a step at a time when a run
came back unfinished, when the screen is visual rather than textual and needs
your own eyes through `take_screenshot`, or when you need to look at something
part-way through. `read_screen` lists what is on screen by number and name;
`tap`, `type_text`, `scroll` and `press_key` act on it.

You do not need to know how the phone is driven. State the goal, read the report,
and relay it.
"""

SCROLL_DISTANCE_FRACTIONS = {"small": 0.15, "page": 0.4, "large": 0.7}

SWIPE_DURATION_MS = 400
LONG_PRESS_DURATION_MS = 700
POLL_INTERVAL_SECONDS = 0.5
FIELD_FOCUS_SETTLE_SECONDS = 0.35
TYPING_SETTLE_SECONDS = 0.3

KEY_CODES = {
    "back": 4,
    "home": 3,
    "recents": 187,
    "enter": 66,
    "delete": 67,
    "escape": 111,
    "tab": 61,
    "space": 62,
    "move_home": 122,
    "move_end": 123,
    "volume_up": 24,
    "volume_down": 25,
    "volume_mute": 164,
    "power": 26,
    "wake": 224,
    "sleep": 223,
    "camera": 27,
    "play_pause": 85,
    "next_track": 87,
    "previous_track": 88,
    "copy": 278,
    "cut": 277,
    "paste": 279,
    "search": 84,
    "menu": 82,
    "page_up": 92,
    "page_down": 93,
}

KEY_ALIASES = {
    "app_switch": "recents",
    "overview": "recents",
    "backspace": "delete",
    "del": "delete",
    "return": "enter",
    "notifications": "notification_shade",
    "notification": "notification_shade",
    "shade": "notification_shade",
    "settings_shade": "quick_settings",
    "quick_settings_shade": "quick_settings",
    "screen_off": "sleep",
    "screen_on": "wake",
}

STATUS_BAR_COMMANDS = {
    "notification_shade": "expand-notifications",
    "quick_settings": "expand-settings",
    "collapse_shade": "collapse",
}

SELECT_ALL_KEYCODES = ("113", "29")  # Ctrl + A

# One command can carry only so many keycodes, so longer work is batched.
KEYCODES_PER_COMMAND = 200

# Navigation keys that always hand the screen to a different app, so the handover
# is worth waiting for. `back` is deliberately absent: it usually moves within the
# same app, and waiting would cost a timeout every time.
KEYS_THAT_CHANGE_THE_APP = {"home", "recents"}
NAVIGATION_SETTLE_SECONDS = 3.0

server = MCPServer(
    name="android",
    title="Android phone",
    # The package's own version, not a copy of it. A client is told this on every
    # mount, and a second hand-maintained copy is a number that drifts.
    version=__version__,
    instructions=SERVER_INSTRUCTIONS,
)

_installed_apps: InstalledApps | None = None
_jev_client: JevClient | None = None


THE_USERS_TO_FIX = (AdbNotFound, NoPhoneConnected, PhoneNotAuthorized)


def _turns_failures_into_errors(function):
    """Raise a failure, so the client is told the tool did not work."""

    @functools.wraps(function)
    async def wrapper(*arguments, **keywords):
        try:
            return await function(*arguments, **keywords)
        except THE_USERS_TO_FIX as error:
            raise MCPError(code=0, message=_a_readable_failure(error)) from error
        except PhoneControlError as error:
            raise ToolError(_a_readable_failure(error)) from error

    return wrapper


def _a_readable_failure(error: PhoneControlError) -> str:
    """The message, with the remedy as a second sentence.

    Kept short on purpose: the caller relays this to a person, and the remedy is
    the part they need first.
    """
    return f"{error.message} Next step: {error.fix}" if error.fix else error.message


async def _without_blocking_the_server(function, *arguments, **keywords):
    """Run blocking adb work on a worker thread."""
    return await anyio.to_thread.run_sync(
        functools.partial(function, *arguments, **keywords)
    )


def _apps() -> InstalledApps:
    global _installed_apps
    if _installed_apps is None:
        _installed_apps = InstalledApps(shared_phone())
    return _installed_apps


def _jev() -> JevClient:
    global _jev_client
    if _jev_client is None:
        _jev_client = JevClient()
    return _jev_client


async def _current_screen():
    """The screen as best it can be read, by the tree or by a picture of it.

    Every tool that looks at the phone comes through here, so the second way of
    reading is taken once rather than once per tool. It matters because a page
    Android will not describe is a property of the page rather than of the tool
    asking: the notification shade is an overlay that does not go idle, so
    `read_notifications` returned a failure where it should have returned what a
    photograph of the shade says, and every other tool that reads was the same.

    Three cases, in the order they cost. An ordinary screen is read from the tree and
    nothing else happens. A screen the tree describes as empty - embedded views inside
    Compose, measured as the common case on this device - is photographed, because
    there is nothing to act on otherwise. A screen the tree refuses outright is named
    by its window, which needs no idle state, and photographed as well.
    """
    from .device_state import screen_size
    from .goal import a_screen_whose_tree_cannot_be_read, with_a_picture_of_the_screen
    from .screen import Screen

    phone = shared_phone()
    try:
        screen = await _without_blocking_the_server(read_screen_from_device, phone)
    except PhoneCommandFailed:
        # The tree failed. Whether that becomes a reading or a failure depends on
        # whether the picture says anything: a fallback that yields evidence is worth
        # having, and a fallback that yields none must not turn a broken dump into a
        # confident-looking empty screen. Two tests hold that line - a dump that
        # produced nothing and a dump cut off mid-XML both have to be *reported* - and
        # they caught this branch swallowing the failure rather than adding to it.
        width, height = await _without_blocking_the_server(screen_size, phone)
        read_by_picture = await _without_blocking_the_server(
            a_screen_whose_tree_cannot_be_read,
            phone,
            Screen(controls=(), width=width, height=height, package=""),
        )
        if not read_by_picture.lines_read_from_a_picture:
            raise
        return read_by_picture
    if screen.controls:
        return screen
    return await _without_blocking_the_server(
        with_a_picture_of_the_screen, phone, screen
    )


async def _describe_an_empty_screen(screen: Screen) -> str:
    """Explain an empty listing, which is usually the phone rather than the app.

    A sleeping screen dumps a single bare node, so an agent that is told only
    "no controls" cannot tell a blank app from a phone that is simply asleep.
    """
    if screen.lines_read_from_a_picture:
        # The picture has already been taken by the time this is reached. Saying "no
        # named controls, take a screenshot" over a screen that has just been
        # photographed and read is telling the caller to do work this server has done -
        # and it was doing exactly that on the notification shade, which is an overlay
        # that does not go idle.
        read = "\n".join(f"  {line}" for line in screen.lines_read_from_a_picture)
        return (
            f"The phone would not describe this screen, so it was read from a picture "
            f"of it instead. Foreground app: {screen.package or 'unknown'}.\n{read}"
        )

    state = await _without_blocking_the_server(read_device_state, shared_phone())
    lines = [
        f"No named controls on screen. Foreground app: {screen.package or 'unknown'}."
    ]
    if state.is_screen_on is False:
        lines.append(
            "The screen is off, so there is nothing to read. Call "
            "press_key('wake') to turn it on first."
        )
    elif state.is_locked:
        lines.append(
            "The phone is locked, so only the lock screen is available. Ask the "
            "user to unlock it — a PIN cannot be entered from here."
        )
    else:
        lines.append(
            "The screen is on and unlocked, so this is likely an app drawing to a "
            "canvas (a game, a map, a photo viewer) or a Compose screen whose "
            "containers are not exposed. Call take_screenshot to see it."
        )
    return "\n".join(lines)


async def _resolve_control(target: int | str) -> ScreenControl:
    """Find the control a caller named by number or by label."""
    screen = await _current_screen()
    if isinstance(target, int):
        control = screen.control_at(target)
        if control is None:
            raise NoMatchingControl(
                f"There is no control #{target} on screen right now.",
                fix="Call read_screen to see the numbers that are current.",
            )
        return control
    match = screen.best_match(target)
    if match is None:
        visible = ", ".join(
            control.label for control in screen.controls if control.label
        )[:400]
        raise NoMatchingControl(
            f"Nothing on screen matches '{target}'.",
            fix=f"On screen now: {visible or 'no named controls'}.",
        )
    return match


async def _wait_until(predicate, timeout_seconds: float):
    """Poll an async predicate until it returns something truthy or time runs out."""
    deadline = time.monotonic() + timeout_seconds
    while True:
        outcome = await predicate()
        if outcome:
            return outcome
        if time.monotonic() >= deadline:
            return None
        await anyio.sleep(POLL_INTERVAL_SECONDS)


async def _foreground_is(package: str) -> bool:
    current = await _without_blocking_the_server(foreground_package, shared_phone())
    return current == package


# --- seeing the phone ----------------------------------------------------


@server.tool()
@_turns_failures_into_errors
async def status() -> str:
    """Check the phone connection and see what it is currently doing.

    Call this first when a command fails, and before starting anything
    multi-step. It reports the model, Android version, screen size, foreground
    app, whether the screen is on or locked, and the battery level. It changes
    nothing on the phone.
    """
    state = await _without_blocking_the_server(read_device_state, shared_phone())
    return state.as_text()


@server.tool()
@_turns_failures_into_errors
async def read_screen(
    query: Annotated[
        str | None,
        Field(
            description=(
                "Only list controls whose text or description contains this. "
                "Omit to list everything on the screen."
            )
        ),
    ] = None,
) -> str:
    """List the controls currently on the phone's screen, by number and name.

    This is the main way to see what is on screen, and it is far cheaper than a
    screenshot. Each control gets a number you can hand to `tap`, and a label
    you can match on.

    Every action tool re-reads the screen before it acts, so both forms are
    resolved against the screen as it is at that moment. A number is precise --
    it disambiguates two controls carrying the same text. A label is stable --
    it survives the screen being redrawn. Use the number for a control you have
    just seen and are about to act on, and the label when the screen may have
    changed since you read it.

    Args:
        query: Optional filter. Only controls whose text, description, or type
            contains this text are listed, best match first. Use it to ask "is
            the Send button up yet?" in one call.
    """
    screen = await _current_screen()
    if not query:
        if not screen.controls:
            return await _describe_an_empty_screen(screen)
        return screen.as_text()

    matches = screen.find(query)
    if not matches:
        raise ToolError(
            f"Nothing on screen matches '{query}'.\n"
            f"Foreground app: {screen.package or 'unknown'}\n"
            f"{len(screen.controls)} other controls are present; call read_screen "
            "with no query to see them all."
        )
    lines = "\n".join(control.as_line() for control in matches)
    return f"{len(matches)} controls match '{query}':\n{lines}"


@server.tool()
@_turns_failures_into_errors
async def take_screenshot(
    max_width: Annotated[
        int,
        Field(
            description="Downscale the image to this width in pixels. Lower costs fewer tokens to look at."
        ),
    ] = DEFAULT_MAX_WIDTH,
) -> Image:
    """Capture the phone's screen and look at it.

    Try `read_screen` first: it names things so you can act on the result, and
    it costs far less. Take a screenshot when the screen is genuinely visual --
    a photo, a map, a game, a CAPTCHA, an icon-only toolbar -- when `read_screen`
    returns nothing useful, or when a decision came back with low confidence.

    Args:
        max_width: Downscale the image to this width in pixels before returning
            it. Lower it to spend fewer tokens; 0 keeps full resolution.
    """
    image_bytes, mime_type = await _without_blocking_the_server(
        capture_screen, shared_phone(), max_width
    )
    return Image(data=image_bytes, format=mime_type.removeprefix("image/"))


# --- touching the phone --------------------------------------------------


@server.tool()
@_turns_failures_into_errors
async def tap(
    target: Annotated[
        int | str | None,
        Field(
            description="Which control, by its number from read_screen or by its visible label."
        ),
    ] = None,
    x: Annotated[
        int | None,
        Field(
            description="Horizontal pixel coordinate, for tapping a point instead of a control."
        ),
    ] = None,
    y: Annotated[
        int | None,
        Field(
            description="Vertical pixel coordinate, for tapping a point instead of a control."
        ),
    ] = None,
    long_press: Annotated[
        bool,
        Field(
            description="Hold instead of tapping, for a context menu or a drag handle."
        ),
    ] = False,
) -> str:
    """Tap, or long-press, something on the phone screen.

    Args:
        target: The number from `read_screen` (for example 7), or the visible
            label of the control ("Send", "Sign in"). A label is matched against
            the control's text and description, and the best match wins. The
            screen is re-read first, so the target is resolved against what is
            on screen now; the reply names exactly what was tapped.
        x: Horizontal pixel coordinate. Use only when nothing on screen is
            nameable, such as a spot on a map or photo.
        y: Vertical pixel coordinate, used together with `x`.
        long_press: Hold instead of tapping, for context menus and drag handles.
    """
    phone = shared_phone()
    if target is not None:
        control = await _resolve_control(target)
        point_x, point_y = control.area.center_x, control.area.center_y
        described = control.label or control.kind
    elif x is not None and y is not None:
        point_x, point_y = x, y
        described = "that point"
    else:
        # A caller's mistake, so it has to arrive as one. Returned, it reached a
        # client as a tool that worked and whose answer was an instruction to the
        # caller - and the caller is a model, which reads that as done.
        raise ToolError(
            "Give either a target (a number or label from read_screen) or both x "
            "and y. Next step: call read_screen and pass one of its numbers."
        )

    if long_press:
        await _without_blocking_the_server(
            phone.run,
            [
                "shell",
                "input",
                "swipe",
                str(point_x),
                str(point_y),
                str(point_x),
                str(point_y),
                str(LONG_PRESS_DURATION_MS),
            ],
        )
        verb = "Long-pressed"
    else:
        await _without_blocking_the_server(
            phone.run,
            ["shell", "input", "tap", str(point_x), str(point_y)],
        )
        verb = "Tapped"

    return f"{verb} {described} at ({point_x}, {point_y})."


@server.tool()
@_turns_failures_into_errors
async def type_text(
    text: Annotated[
        str,
        Field(
            description="The text to type, or the text to replace the clipboard with."
        ),
    ],
    target: Annotated[
        int | str | None,
        Field(
            description="Which control, by its number from read_screen or by its visible label."
        ),
    ] = None,
    replace: Annotated[
        bool,
        Field(description="Clear the field first instead of adding to what it holds."),
    ] = False,
    submit: Annotated[
        bool,
        Field(description="Press Enter afterwards, for search bars and message boxes."),
    ] = False,
    verify: Annotated[
        bool,
        Field(
            description="Read the screen back afterwards and report whether the text landed."
        ),
    ] = True,
) -> str:
    """Type text into a field on the phone.

    Args:
        text: The text to type. Use "\\n" for a line break.
        target: The field to type into, as a number or label from `read_screen`.
            When omitted, the text goes to whatever already has focus.
        replace: Clear the field first instead of appending to it.
        submit: Press Enter afterwards, for search bars and message boxes.
        verify: Re-read the screen afterwards and report whether the text
            actually landed, which catches a tap that missed the field.
    """
    phone = shared_phone()
    notes: list[str] = []

    if target is not None:
        control = await _resolve_control(target)
        await _without_blocking_the_server(
            phone.run,
            [
                "shell",
                "input",
                "tap",
                str(control.area.center_x),
                str(control.area.center_y),
            ],
        )
        await anyio.sleep(FIELD_FOCUS_SETTLE_SECONDS)
        if replace:
            await _clear_focused_field(phone, control.text)
            notes.append("cleared the field first")

    if needs_non_ascii_workaround(text):
        if not await _without_blocking_the_server(set_device_clipboard, phone, text):
            # The text was not typed. Returning the advice made this look like a tool
            # that worked and whose answer was a sentence about accents, which is the
            # shape this project spent a phase removing - and the one case a caller
            # most needs to see, because the alternative is retyping it for ever.
            raise ToolError(
                "Only ASCII can be typed as keystrokes, and this phone's clipboard "
                "cannot be set either, so the text was not typed. " + NON_ASCII_FIX
            )
        await _without_blocking_the_server(paste_device_clipboard, phone)
        notes.append("sent through the clipboard because the text is not ASCII")
    else:
        await _without_blocking_the_server(
            type_text_on_device, phone, text, submit=submit
        )

    if submit:
        notes.append("pressed Enter")
        return "Typed the text and pressed Enter. " + _describe_notes(notes)

    if not verify:
        return "Typed the text. " + _describe_notes(notes)

    await anyio.sleep(TYPING_SETTLE_SECONDS)
    screen = await _current_screen()
    needle = text.strip().splitlines()[0][:24].casefold() if text.strip() else ""
    landed = bool(needle) and any(
        needle in f"{control.text} {control.label}".casefold()
        for control in screen.controls
    )
    if landed:
        notes.append("confirmed on screen")
    else:
        notes.append(
            "NOT found on screen afterwards, so the text may have gone "
            "elsewhere; call read_screen to check"
        )
    return "Typed the text. " + _describe_notes(notes)


def _describe_notes(notes: list[str]) -> str:
    return f"({'; '.join(notes)})." if notes else ""


async def _clear_focused_field(phone: AndroidPhone, existing_text: str) -> None:
    """Clear the focused field. The implementation lives with the typing it serves."""
    await _without_blocking_the_server(clear_the_focused_field, phone, existing_text)


@server.tool()
@_turns_failures_into_errors
async def scroll(
    direction: Annotated[
        str,
        Field(description="Which way to move, or which way the copy goes for a file."),
    ] = "down",
    distance: Annotated[
        str,
        Field(description="How far: a nudge, about one screenful, or most of the way."),
    ] = "page",
    target: Annotated[
        int | str | None,
        Field(
            description="Which control, by its number from read_screen or by its visible label."
        ),
    ] = None,
) -> str:
    """Scroll the content on screen.

    Args:
        direction: Where you want to look, not which way the finger moves.
            "down" reveals content further down the page, "up" goes back toward
            the top, and "left" and "right" move sideways.
        distance: "small" nudges, "page" moves about one screenful (the
            default), "large" jumps most of the way.
        target: Optional number or label of the region to scroll, when the
            screen has more than one scrollable area.
    """
    if direction not in {"down", "up", "left", "right"}:
        raise ToolError("direction must be one of: down, up, left, right.")

    phone = shared_phone()
    if target is not None:
        control = await _resolve_control(target)
        left, top = control.area.left, control.area.top
        width, height = control.area.width, control.area.height
    else:
        width, height = await _without_blocking_the_server(screen_size, phone)
        left, top = 0, 0

    if width <= 0 or height <= 0:
        raise ToolError(
            "Could not work out the screen size, so there is nothing to scroll "
            "against. Pass a target from read_screen, or check with status."
        )

    fraction = SCROLL_DISTANCE_FRACTIONS.get(distance, 0.4)
    reach_x = max(int(width * fraction / 2), 1)
    reach_y = max(int(height * fraction / 2), 1)
    centre_x = left + width // 2
    centre_y = top + height // 2

    offsets = {
        "down": (0, reach_y, 0, -reach_y),
        "up": (0, -reach_y, 0, reach_y),
        "right": (reach_x, 0, -reach_x, 0),
        "left": (-reach_x, 0, reach_x, 0),
    }[direction]

    start_x = _clamp(centre_x + offsets[0], left + 1, left + width - 1)
    start_y = _clamp(centre_y + offsets[1], top + 1, top + height - 1)
    end_x = _clamp(centre_x + offsets[2], left + 1, left + width - 1)
    end_y = _clamp(centre_y + offsets[3], top + 1, top + height - 1)

    await _without_blocking_the_server(
        phone.run,
        [
            "shell",
            "input",
            "swipe",
            str(start_x),
            str(start_y),
            str(end_x),
            str(end_y),
            str(SWIPE_DURATION_MS),
        ],
    )
    return f"Scrolled {direction} by {distance}."


def _clamp(value: int, low: int, high: int) -> int:
    return max(low, min(high, value))


@server.tool()
@_turns_failures_into_errors
async def swipe(
    start_x: Annotated[
        int,
        Field(description="Horizontal pixel where the drag starts."),
    ],
    start_y: Annotated[
        int,
        Field(description="Vertical pixel where the drag starts."),
    ],
    end_x: Annotated[
        int,
        Field(description="Horizontal pixel where the drag ends."),
    ],
    end_y: Annotated[
        int,
        Field(description="Vertical pixel where the drag ends."),
    ],
    duration_ms: Annotated[
        int,
        Field(
            description="How long the drag takes. Slower is more likely to be read as a drag than a fling."
        ),
    ] = 300,
) -> str:
    """Drag between two points, for gestures no named control covers.

    Use `scroll` for ordinary page movement. Reach for this to dismiss a card,
    pull to refresh, draw a pattern lock, or drag a slider.

    Args:
        start_x: Horizontal pixel where the finger lands.
        start_y: Vertical pixel where the finger lands.
        end_x: Horizontal pixel where the finger lifts.
        end_y: Vertical pixel where the finger lifts.
        duration_ms: How long the drag takes. Longer is slower and more likely
            to be read as a drag rather than a fling.
    """
    await _without_blocking_the_server(
        shared_phone().run,
        [
            "shell",
            "input",
            "swipe",
            str(start_x),
            str(start_y),
            str(end_x),
            str(end_y),
            str(duration_ms),
        ],
    )
    return f"Swiped from ({start_x}, {start_y}) to ({end_x}, {end_y})."


@server.tool()
@_turns_failures_into_errors
async def press_key(
    key: Annotated[
        str,
        Field(description="Which key, from the list in the description above."),
    ],
) -> str:
    """Press a navigation, hardware, or system key.

    Args:
        key: One of back, home, recents, enter, delete, escape, tab, space,
            move_home, move_end, volume_up, volume_down, power, wake, sleep,
            camera, play_pause, next_track, previous_track, copy, cut, paste,
            select_all, search, menu, page_up, page_down, notification_shade,
            quick_settings, collapse_shade.
    """
    phone = shared_phone()
    wanted = KEY_ALIASES.get(key.strip().casefold(), key.strip().casefold())

    if wanted in STATUS_BAR_COMMANDS:
        await _without_blocking_the_server(
            phone.run, ["shell", "cmd", "statusbar", STATUS_BAR_COMMANDS[wanted]]
        )
        return f"Pressed {wanted.replace('_', ' ')}."

    if wanted == "select_all":
        await _without_blocking_the_server(
            phone.run, ["shell", "input", "keycombination", *SELECT_ALL_KEYCODES]
        )
        return "Selected all text in the focused field."

    code = KEY_CODES.get(wanted)
    if code is None:
        raise ToolError(
            f"'{key}' is not a key I know. Valid keys: "
            + ", ".join(sorted({*KEY_CODES, *STATUS_BAR_COMMANDS, "select_all"}))
            + "."
        )

    changes_the_app = wanted in KEYS_THAT_CHANGE_THE_APP
    if changes_the_app:
        before = await _without_blocking_the_server(foreground_package, phone)

    await _without_blocking_the_server(
        phone.run, ["shell", "input", "keyevent", str(code)]
    )

    if not changes_the_app:
        return f"Pressed {wanted.replace('_', ' ')}."

    # The keypress returns before the transition finishes, so reporting straight
    # away names the app being left rather than the one arrived at -- and a
    # read_screen taken now would describe the old screen.
    async def the_app_changed() -> bool:
        current = await _without_blocking_the_server(foreground_package, phone)
        return bool(current) and current != before

    moved = await _wait_until(
        the_app_changed, timeout_seconds=NAVIGATION_SETTLE_SECONDS
    )
    current = await _without_blocking_the_server(foreground_package, phone)
    if moved:
        return (
            f"Pressed {wanted.replace('_', ' ')}; {current} is now in the foreground."
        )
    return (
        f"Pressed {wanted.replace('_', ' ')}; the foreground app is still "
        f"{current or 'unknown'}."
    )


# --- apps and links ------------------------------------------------------


@server.tool()
@_turns_failures_into_errors
async def open_app(
    name: Annotated[
        str,
        Field(
            description="The app, by the name a person would say: 'settings', 'Play Store', 'com.android.chrome'."
        ),
    ],
    wait_seconds: Annotated[
        float,
        Field(
            description="How long to wait for it to reach the foreground before giving up."
        ),
    ] = 8.0,
) -> str:
    """Bring an app to the front, by the name a person would say.

    Accepts "YouTube", "youtube", "play store", or an exact package id. The name
    is checked against what is actually installed before anything is launched,
    and it waits for the app to reach the foreground before returning.

    Args:
        name: The app to open.
        wait_seconds: How long to wait for it to reach the foreground.
    """
    phone = shared_phone()
    package = await _without_blocking_the_server(launch_app, phone, _apps(), name)
    reached = await _wait_until(
        lambda: _foreground_is(package), timeout_seconds=wait_seconds
    )
    if reached:
        return f"Opened {name} ({package}), which is now in the foreground."
    current = await _without_blocking_the_server(foreground_package, phone)
    return (
        f"Started {name} ({package}), but the foreground app is still "
        f"{current or 'unknown'} after {wait_seconds:g}s. It may still be "
        "loading, or it may have opened behind something; call read_screen to see."
    )


@server.tool()
@_turns_failures_into_errors
async def open_url(
    url: Annotated[
        str,
        Field(
            description="A web address such as 'https://example.com', or a deep link such as 'geo:37.8,-122.4'."
        ),
    ],
    wait_seconds: Annotated[
        float,
        Field(
            description="How long to wait for it to reach the foreground before giving up."
        ),
    ] = 6.0,
) -> str:
    """Open a web address or deep link on the phone.

    Args:
        url: A URL such as "https://example.com", or a deep link such as
            "geo:37.8,-122.4", "tel:+15551234567", or "sms:+15551234567".
    """
    phone = shared_phone()
    before = await _without_blocking_the_server(foreground_package, phone)
    # The screen as well as the app. A link followed from inside a browser changes
    # the page and not the app, and asking only whether the app changed reported a
    # failure for every link opened in the browser it was already open in - which is
    # most of them.
    screen_before = await _current_screen()
    await _without_blocking_the_server(open_link, phone, url)

    async def another_app_came_forward() -> bool:
        current = await _without_blocking_the_server(foreground_package, phone)
        if current and current != before:
            return True
        now = await _current_screen()
        return now.as_text() != screen_before.as_text()

    moved = await _wait_until(
        another_app_came_forward, timeout_seconds=max(wait_seconds, 0.5)
    )
    current = await _without_blocking_the_server(foreground_package, phone)
    if moved:
        return f"Opened {url}. The foreground app is now {current}."
    raise ToolError(
        f"Asked the phone to open {url}, but the foreground app is still "
        f"{current or 'unknown'} after {wait_seconds:g}s. It may have opened "
        "inside the same app, or been refused; call read_screen to see."
    )


@server.tool()
@_turns_failures_into_errors
async def list_apps(
    query: Annotated[
        str | None,
        Field(description="Only list what matches this text. Omit to list everything."),
    ] = None,
) -> str:
    """List the apps installed on the phone, as a readable name and package id.

    Use this when you are unsure what an app is called on this phone, or when
    `open_app` could not find the name you tried.

    Args:
        query: Optional filter, matched against the package id.
    """
    apps = _apps()
    packages = await _without_blocking_the_server(apps.packages)
    if query:
        needle = query.casefold()
        packages = [package for package in packages if needle in package.casefold()]
        if not packages:
            return f"No installed package contains '{query}'."
    # The spoken names the unfiltered listing uses: an agent that cannot find an app
    # by name is sent here to recover, and "vending" is not an answer to which app
    # the Play Store is. Two formatters for one list is how they drift apart.
    lines = [f"{spoken_name(package)}  ->  {package}" for package in packages]
    return f"{len(lines)} packages:\n" + "\n".join(lines)


# --- waiting and system surfaces -----------------------------------------


@server.tool()
@_turns_failures_into_errors
async def wait_for(
    text: Annotated[
        str | None,
        Field(
            description="The text to type, or the text to replace the clipboard with."
        ),
    ] = None,
    app: Annotated[
        str | None,
        Field(description="Wait until this app is in the foreground."),
    ] = None,
    timeout_seconds: Annotated[
        float,
        Field(description="Give up and report the timeout after this long."),
    ] = 15.0,
) -> str:
    """Wait until something becomes true, instead of sleeping a fixed time.

    Use this after an action that triggers loading, so the next step reads a
    settled screen instead of one mid-animation. With no arguments it waits for
    the screen to stop changing.

    Args:
        text: Wait until this text appears anywhere on screen.
        app: Wait until this app (a name or a package id) is in the foreground.
        timeout_seconds: Give up and report the timeout after this long.
    """

    if app:
        package = await _without_blocking_the_server(_apps().resolve, app)
        reached = await _wait_until(
            lambda: _foreground_is(package), timeout_seconds=timeout_seconds
        )
        if reached:
            return f"{app} ({package}) is now in the foreground."
        raise ToolError(
            f"{app} did not reach the foreground within {timeout_seconds:g}s."
        )

    if text:
        needle = text.casefold()

        async def text_is_present() -> bool:
            screen = await _current_screen()
            return any(
                needle
                in f"{control.text} {control.description} {control.label}".casefold()
                for control in screen.controls
            )

        found = await _wait_until(text_is_present, timeout_seconds=timeout_seconds)
        if found:
            return f"'{text}' is now on screen."
        raise ToolError(f"'{text}' did not appear within {timeout_seconds:g}s.")

    previous = ""
    stable_readings = 0

    async def screen_has_settled() -> bool:
        nonlocal previous, stable_readings
        current = (await _current_screen()).as_text()
        stable_readings = stable_readings + 1 if current == previous else 0
        previous = current
        return stable_readings >= 1

    settled = await _wait_until(screen_has_settled, timeout_seconds=timeout_seconds)
    if settled:
        return "The screen has stopped changing."
    return f"The screen was still changing after {timeout_seconds:g}s."


@server.tool()
@_turns_failures_into_errors
async def read_notifications(
    close_after: Annotated[
        bool,
        Field(
            description="Set false to leave the shade open, when you intend to tap a notification."
        ),
    ] = True,
) -> str:
    """Open the notification shade, read what is in it, then close it again.

    Args:
        close_after: Set False to leave the shade open, when you intend to tap
            one of the notifications.
    """
    phone = shared_phone()
    await _without_blocking_the_server(
        phone.run, ["shell", "cmd", "statusbar", "expand-notifications"]
    )
    await anyio.sleep(1.2)
    screen = await _current_screen()
    listing = screen.as_text()
    if close_after:
        await _without_blocking_the_server(
            phone.run, ["shell", "cmd", "statusbar", "collapse"]
        )
    return listing


@server.tool()
@_turns_failures_into_errors
async def clipboard(
    action: Annotated[
        str,
        Field(
            description="What to do: 'get' to read the clipboard, 'set' to replace it."
        ),
    ] = "get",
    text: Annotated[
        str | None,
        Field(
            description="The text to type, or the text to replace the clipboard with."
        ),
    ] = None,
) -> str:
    """Read or replace the phone's clipboard.

    Useful for moving text between the phone and this conversation, and for
    putting non-ASCII text where a paste will pick it up, since adb can only
    type ASCII as keystrokes.

    Args:
        action: "get" to read the clipboard, "set" to replace it.
        text: The text to place on the clipboard when setting.
    """
    phone = shared_phone()

    if action == "set":
        if text is None:
            # A caller's mistake, so it is reported as one. Returning the request
            # made this a successful tool whose answer was "give me the text", which
            # every client shows as a tool that worked.
            raise ToolError(
                "action='set' needs the text to put on the clipboard. Next step: "
                "pass text, or use action='get' to read what is on it."
            )
        stored = await _without_blocking_the_server(set_device_clipboard, phone, text)
        if not stored:
            raise ToolError(
                "This phone does not implement `cmd clipboard`, so its clipboard "
                "cannot be set from here. Type the text with `type_text` instead, "
                "or copy it on the phone and paste with `press_key`."
            )
        return "Put the text on the phone's clipboard, and read it back to confirm."

    if action != "get":
        raise ToolError("action must be 'get' or 'set'.")

    stored = await _without_blocking_the_server(read_device_clipboard, phone)
    if stored is None:
        raise ToolError(
            "This phone does not implement `cmd clipboard`, so the clipboard "
            "cannot be read from here."
        )
    return f"Clipboard contents: {stored}" if stored else "The clipboard is empty."


@server.tool()
@_turns_failures_into_errors
async def run_shell(
    command: Annotated[
        str,
        Field(description="The shell command, run on the phone as the shell user."),
    ],
) -> str:
    """Run a shell command on the phone and return its output.

    The escape hatch for whatever the named tools do not cover: reading a
    setting, listing files, checking what is installed, granting a permission.
    Commands run as the shell user, which can read most of a stock phone but
    cannot change protected settings.

    Args:
        command: The shell command line to run on the device.
    """
    output = await _without_blocking_the_server(shared_phone().shell, command)
    return output.strip() or "(the command produced no output)"


@server.tool()
@_turns_failures_into_errors
async def transfer_file(
    direction: Annotated[
        str,
        Field(description="Which way to move, or which way the copy goes for a file."),
    ],
    computer_path: Annotated[
        str,
        Field(description="Path on this computer. '~' is expanded."),
    ],
    phone_path: Annotated[
        str,
        Field(
            description="Path on the phone, for example /sdcard/Download/report.pdf."
        ),
    ],
) -> str:
    """Copy a file between this computer and the phone.

    Args:
        direction: "to_phone" or "from_phone".
        computer_path: Path on this computer. "~" is expanded.
        phone_path: Path on the phone, for example /sdcard/Download/report.pdf.
    """
    from pathlib import Path

    local = str(Path(computer_path).expanduser())
    phone = shared_phone()

    if direction == "to_phone":
        await _without_blocking_the_server(
            phone.run_checked, ["push", local, phone_path]
        )
        return f"Copied {local} to the phone at {phone_path}."
    if direction == "from_phone":
        await _without_blocking_the_server(
            phone.run_checked, ["pull", phone_path, local]
        )
        return f"Copied {phone_path} from the phone to {local}."

    raise ToolError("direction must be 'to_phone' or 'from_phone'.")


# --- asking Jev for a decision -------------------------------------------

ACTION_SENTINELS = {
    "scroll_down": ("scroll", {"direction": "down"}),
    "scroll_up": ("scroll", {"direction": "up"}),
    "press_back": ("press_key", {"key": "back"}),
    "wait": ("wait_for", {"timeout_seconds": 5}),
}

LOW_CONFIDENCE = 0.45
GOOD_CONFIDENCE = 0.70

# The options a decision model is offered, and how they are named, live in
# ``options`` so that the step-by-step tools and the goal loop cannot drift apart
# on what they call things.
from .options import (  # noqa: E402  (kept beside the choice it configures)
    FINISH,
    GO_HOME,
    SCROLL_FORWARD,
    as_criteria as options_as_criteria,
    offer_actions,
    the_targets_offered,
)

STATE_CHARACTER_BUDGET = 80_000

# Targets are their own question now, so the only limit that binds is what a
# choice question accepts. Offering more would raise a ValueError, which is not a
# phone error and would reach the agent as an internal crash.
MAX_OFFERED_CONTROLS = JEV_MAX_ALTERNATIVES


def jev_state_for(
    screen: Screen, candidates: list[ScreenControl], goal: str
) -> dict[str, object]:
    """The screen in the accessibility tree's own terms, for a decision model.

    Sending the source beats sending a summary derived from it by a wide margin:
    over paired instances the same programs scored 0.894 as source, 0.598 as a
    syntax tree and 0.530 as a control-flow graph, and the graph carried *more*
    information for 2.5 times the tokens. So this sends the tree's own field
    names and values rather than a sentence written about each control.

    The goal travels in the state rather than in the question, because the state
    has a field of its own and the question's instructions do not.
    """
    controls = [control.as_dict() for control in candidates]
    state = screen.as_structured()
    state["controls"] = controls
    state["goal"] = goal

    while len(controls) > 1 and len(json.dumps(state)) > STATE_CHARACTER_BUDGET:
        controls = controls[: int(len(controls) * 0.8)]
        state["controls"] = controls
    if len(controls) < len(candidates):
        state["note"] = (
            f"Only the first {len(controls)} of {len(candidates)} controls are "
            "listed, to stay inside the request budget."
        )
    return state


@server.tool()
@_turns_failures_into_errors
async def decide_next_action(
    goal: Annotated[
        str,
        Field(
            description="What you want done, in plain language. Phrase it as the outcome, not the steps."
        ),
    ],
    max_candidates: Annotated[
        int,
        Field(
            description="The most controls to offer at once. Lower only to reduce token cost."
        ),
    ] = MAX_OFFERED_CONTROLS,
) -> str:
    """Ask Jev which single control on screen best advances a goal.

    Turns "send the message" or "log in" into one concrete action. Jev is a
    System One decision model: it answers with a typed choice drawn from the
    controls actually on screen, so it cannot invent a control that is not
    there, and it returns a calibrated confidence alongside the answer.

    Jev reads **text only**. It is sent the numbered listing of on-screen
    controls plus your goal, and it never sees a screenshot — so ask it what to
    tap, not what the screen looks like. Anything about appearance (colour,
    layout, what a photo shows) is yours to judge from `take_screenshot`.

    Read the confidence before acting. It describes how far the leading option
    stands from the others — it is not whether Jev could answer, and not whether
    the action is safe:
      - 0.70 and above: carry out the returned action.
      - 0.45 to 0.70: carry it out, then confirm with `read_screen`.
      - below 0.45: the leading options are near a coin flip. Do not act on it;
        call `take_screenshot` and decide from the image yourself.

    Args:
        goal: What the user wants, in plain language. For example "reply to the
            most recent message saying I will be late".
        max_candidates: The most controls to offer in one choice. The default
            leaves room for the six built-in actions inside Jev's 255-option
            limit, so every control on screen is normally offered: measured over
            paired problems, accuracy held from 2 options to 255, and what costs
            accuracy is options that resemble each other, not how many there
            are. Lower this only to reduce token cost.
    """
    screen = await _current_screen()
    offered = offer_actions(screen)
    target_descriptions, target_controls = the_targets_offered(screen)
    if len(target_descriptions) > max(1, min(max_candidates, MAX_OFFERED_CONTROLS)):
        keep = max(1, min(max_candidates, MAX_OFFERED_CONTROLS))
        target_descriptions = dict(list(target_descriptions.items())[:keep])

    if not target_descriptions and not offered:
        raise ToolError(
            "There is nothing on screen to act on and no operation available, so "
            "there is nothing to choose between. Call take_screenshot to look at "
            "the screen yourself."
        )

    # Two questions rather than one list. Operations and targets together made a
    # choice look unsure: confidence measures how concentrated a choice is, so a
    # list holding several plausible things reads as doubt whichever is right.
    questions: dict[str, dict[str, object]] = {
        "operation": choice_question(
            "Which single operation moves the user closest to the goal stated in "
            "the state? Choose one that can be carried out from this screen now in "
            "preference to one that needs the screen to change first.",
            options_as_criteria(offered),
        )
    }
    if target_descriptions:
        questions["target"] = choice_question(
            "If the operation is to tap a control, which one? Answer even if you "
            "chose a different operation.",
            target_descriptions,
        )

    decision = await _jev().ask(
        jev_state_for(
            screen,
            list(target_controls.values()),
            goal,
        ),
        questions,
    )
    chosen = decision.choice("operation")
    target_key = decision.choice("target").option if "target" in questions else None

    reply: dict[str, object] = {
        "goal": goal,
        "confidence": round(chosen.confidence, 3),
        "goal_already_achieved_probability": round(
            chosen.probabilities.get(FINISH, 0.0), 3
        ),
        "jev_model": decision.model,
    }
    if decision.input_tokens is not None:
        reply["jev_input_tokens"] = decision.input_tokens

    chosen_action = next((a for a in offered if a.option == chosen.option), None)

    if chosen_action is None:
        reply["next_action"] = None
        reply["summary"] = (
            f"Jev chose {chosen.option!r}, which is not an operation that was "
            "offered. Re-read the screen and ask again."
        )
    elif chosen_action.kind == "tap":
        control = target_controls.get(target_key or "")
        if control is None:
            reply["next_action"] = None
            reply["summary"] = (
                "Jev chose to tap a control but named one that is not on screen. "
                "Re-read the screen and ask again."
            )
        else:
            reply["next_action"] = {
                "tool": "tap",
                "arguments": {"target": control.index},
                "element_label": control.label,
                "element_kind": control.kind,
            }
            reply["summary"] = (
                f'Jev chose to tap #{control.index} "{control.label}" '
                f"(confidence {chosen.confidence:.2f})."
            )
    else:
        reply["next_action"] = _the_tool_for(chosen_action)
        reply["summary"] = (
            f"Jev chose to {chosen_action.description} "
            f"(confidence {chosen.confidence:.2f})."
        )
        if chosen_action.kind == "finish":
            reply["next_action"] = None
            reply["summary"] = "Jev reports the goal is already achieved."
        elif chosen_action.kind == "give_up":
            reply["next_action"] = None
            reply["summary"] = (
                "Jev reports the goal cannot be advanced from this screen."
            )

    alternatives = sorted(
        (
            (option, probability)
            for option, probability in chosen.probabilities.items()
            if option != chosen.option and probability > 0
        ),
        key=lambda pair: -pair[1],
    )[:3]
    if alternatives:
        reply["alternatives"] = [
            {"option": option, "probability": round(probability, 3)}
            for option, probability in alternatives
        ]

    # Advice about the numbers, and only about the numbers. A low confidence is a
    # reason to look closer, not a reason for this tool to withhold the action:
    # every wrong turn this project has taken came from a rule that refused a
    # correct answer for scoring low.
    if chosen.confidence < LOW_CONFIDENCE:
        reply["advice"] = (
            "The leading operations are close together, so this is near a coin "
            "flip rather than a judgement that the screen is unclear. Carry it out "
            "and then call read_screen to confirm, or take_screenshot and decide "
            "from the image yourself."
        )
    elif chosen.confidence < GOOD_CONFIDENCE:
        reply["advice"] = (
            "The options are not well separated. Carry the action out, then "
            "call read_screen to confirm it did what you expected."
        )

    if decision.cost_usd is not None:
        reply["cost_usd"] = decision.cost_usd

    return json.dumps(reply, indent=2, ensure_ascii=False)


# --- doing the whole job ------------------------------------------------


@server.tool()
@_turns_failures_into_errors
async def run_task(
    goal: Annotated[
        str,
        Field(
            description="What you want done, in plain language. Phrase it as the outcome, not the steps."
        ),
    ],
    max_steps: Annotated[
        int,
        Field(description="How many actions to allow before giving up."),
    ] = 10,
) -> str:
    """Give the phone a goal and let it work the whole thing out itself.

    Use this instead of driving the phone a step at a time. It reads the screen,
    decides what to do, does it, and reads again, until the goal is reported done
    or unreachable — so "open the email app" is one call here rather than ten
    round trips through you.

    Every step reports the action, what came of it, and the two numbers Jev gave
    for the choice: `probability` is how likely that option was the best one and
    `confidence` is how sure it was of its own ranking. They are reported rather
    than gated on, so a run that succeeded on a thin lead says so, and a run that
    stopped for a reason other than reaching the goal says that instead.

    `achieved` is what the run claimed. Read the steps for what actually
    happened. If a run needs looking at afterwards, set `PHONE_CONTROL_RUNS` to a
    directory and each run leaves a folder there with the full record of what it
    asked Jev and what came back; the reply then names the folder.

    Prefer the step-by-step tools when you already know exactly what to tap, when
    the screen is visual rather than textual, or when you need to inspect
    something part-way through.

    Args:
        goal: What to achieve, in plain language. For example "open the email
            app" or "turn on airplane mode".
        max_steps: How many actions to allow before giving up. Reaching it ends
            the run honestly as unfinished rather than as a failure of the goal.
    """
    from .goal import JevDecider, run_task as run_the_task
    from .reader import TheHandOff, reader_from_the_environment
    from .runs import RunFolder

    # The decision model is checked before the phone is. It is the cheaper question
    # — no adb process is spawned to answer it — and the more useful answer to give
    # when both are missing: a phone with no model behind it can decide nothing, and
    # a caller who has configured no key is told to configure one rather than sent
    # looking for a cable. The docstring on the test for this says the check happens
    # before the phone is touched, and it is this line that makes that true.
    client = _jev()
    if not client.is_configured:
        raise JevNotConfigured(
            "No Jev key is configured, so nothing can decide what to do next.",
            fix=(
                "Set OPENROUTER_API_KEY, or point the server at a key file, then "
                "try again. `status` reports whether a key was found."
            ),
        )

    phone = shared_phone()

    # A folder per run when asked for one, and nothing at all when not.
    recording = RunFolder.for_a_run(goal)
    if recording is not None:
        recording.start(goal, {"model": client.model, "started": "just now"})
        client.on_exchange = recording.note_exchange

    # A reader that can write, when one is configured. Without it a stop ends the
    # run as it always did, which is the default: a missing key must not change how
    # a run behaves.
    hand_off = TheHandOff(reader=reader_from_the_environment())

    try:
        report = await run_the_task(
            phone,
            goal,
            JevDecider(client).choose,
            max_steps=max(1, min(max_steps, 30)),
            recording=recording,
            hand_off=hand_off,
        )
    finally:
        if recording is not None:
            # The record of the requests is already on disk by now, so a run that
            # raised still leaves everything up to the point it died.
            client.on_exchange = None

    answer = report.as_view()
    answer["decided_by"] = f"jev ({client.model})"
    if recording is not None:
        recording.finish(report)
        # Named in the reply, because the whole point of the folder is that the
        # caller can go and read it without having known about it beforehand.
        answer["run_folder"] = str(recording.directory)
    return json.dumps(answer, indent=2, ensure_ascii=False)


@server.tool()
@_turns_failures_into_errors
async def ask_jev(
    instructions: Annotated[
        str,
        Field(description="The question, in plain language."),
    ],
    question_type: Annotated[
        str,
        Field(
            description="Which shape of answer: 'choice' picks one option, 'yes_or_no' gives a probability, 'scale' places the state on a range."
        ),
    ] = "choice",
    options: Annotated[
        list[str] | None,
        Field(
            description="For 'choice' and 'scale', the alternatives. For 'choice' write each as 'short_key: what it means'."
        ),
    ] = None,
    state: Annotated[
        str | None,
        Field(
            description="Extra context to judge against. The current screen is always included."
        ),
    ] = None,
) -> str:
    """Ask Jev a typed question about what is on the phone screen.

    Use this for judgements that are not "what do I tap next": whether an action
    worked, whether the screen is an error state, or which of several visible
    items the user meant.

    Jev reads **text only** — the control listing, plus whatever `state` you give
    it. It cannot see the screenshot, so keep the question about what the
    controls say and do, not about how they look.

    Args:
        instructions: The question, in plain language.
        question_type: "choice" to pick one option, "yes_or_no" for the
            probability that something holds, or "scale" to place the screen on
            an ordered scale.
        options: For "choice", the alternatives. For "scale", the levels from
            lowest to highest. Write each as "short_key: what it means" to name
            the answer, or give just the meaning to key it by position.
        state: Extra context to judge against. The current screen reading is
            always included; use this for the goal or instruction being checked.
    """
    screen = await _current_screen()
    context: dict[str, object] = {
        "foreground_app": screen.package,
        "on_screen": [control.as_line() for control in screen.controls],
    }
    if state:
        context["context"] = state

    if question_type == "yes_or_no":
        questions = {
            "question": yes_or_no_question(
                instructions,
                when_true="The statement is true of what is on screen.",
                when_false="The statement is not true of what is on screen.",
            )
        }
    elif question_type == "scale":
        levels = [option.strip() for option in (options or []) if option.strip()]
        if len(levels) < 2:
            # An argument this tool cannot work with, so it is a failure like the
            # unknown question_type below. It used to return the sentence, which
            # reaches a client as `is_error: false` - a tool that worked and whose
            # answer happened to be a complaint. That is the shape this project
            # spent a phase removing.
            raise ToolError(
                "A scale question needs at least two levels in options. Next "
                "step: pass options as the levels, lowest first."
            )
        questions = {"question": scale_question(instructions, levels)}
    elif question_type == "choice":
        if not options:
            raise ToolError(
                "A choice question needs options. Next step: pass the "
                "alternatives in options."
            )
        criteria = _named_options(options)
        questions = {"question": choice_question(instructions, criteria)}
    else:
        raise ToolError("question_type must be 'choice', 'yes_or_no', or 'scale'.")

    decision = await _jev().ask(context, questions)

    if question_type == "yes_or_no":
        answer = decision.yes_or_no("question")
        reply: dict[str, object] = {
            "probability_yes": round(answer.probability_yes, 3),
            "summary": (f"Jev puts the probability at {answer.probability_yes:.2f}."),
        }
    elif question_type == "scale":
        answer = decision.scale("question")
        reply = {
            "position": round(answer.position, 3),
            "confidence": round(answer.confidence, 3),
            "probabilities": answer.probabilities,
            "legend": answer.legend,
        }
    else:
        answer = decision.choice("question")
        reply = {
            "choice": answer.option,
            "confidence": round(answer.confidence, 3),
            "probabilities": answer.probabilities,
        }

    reply["jev_model"] = decision.model
    if decision.cost_usd is not None:
        reply["cost_usd"] = decision.cost_usd
    return json.dumps(reply, indent=2, ensure_ascii=False)


OPTION_KEY_PATTERN = re.compile(r"^[a-z][a-z0-9_]*$")


def _the_tool_for(action) -> dict[str, object] | None:
    """The tool call that carries out a built-in option.

    A tapped control is already a tool call by itself; these are the options that
    are not controls, and each maps to the primitive that performs it.
    """
    if action.kind == "scroll":
        direction = "down" if action.option == SCROLL_FORWARD else "up"
        return {"tool": "scroll", "arguments": {"direction": direction}}
    if action.kind == "key":
        key = "home" if action.option == GO_HOME else "back"
        return {"tool": "press_key", "arguments": {"key": key}}
    if action.kind == "wait":
        return {"tool": "wait_for", "arguments": {"timeout_seconds": 5}}
    if action.kind == "url" and action.value:
        return {"tool": "open_url", "arguments": {"url": action.value}}
    if action.kind == "type" and action.value:
        return {"tool": "type_text", "arguments": {"text": action.value}}
    if action.kind == "open_app":
        # The app is a separate question in the goal loop; asked this way there is
        # no app to name, so the caller is told to use list_apps and open_app.
        return None
    return None


def _named_options(options: list[str]) -> dict[str, str]:
    """Turn "key: meaning" strings into criteria, keying bare ones by position.

    A pair is only treated as one when it really is one: the key has to read as
    ``short_key`` and a space has to follow the colon, which is the documented
    spelling. Without both, ``https://a.com`` reads as the key ``https`` and two
    options collapse into one under a name the caller never wrote. A key that is
    already taken falls back to its position as well.
    """
    criteria: dict[str, str] = {}
    for position, option in enumerate(options):
        text = option.strip()
        if not text:
            continue
        head, separator, tail = text.partition(":")
        key = head.strip()
        if (
            separator
            and tail.startswith(" ")
            and tail.strip()
            and OPTION_KEY_PATTERN.fullmatch(key)
            and key not in criteria
        ):
            criteria[key] = tail.strip()
        else:
            criteria[str(position)] = text
    return criteria


def main() -> None:
    """Run the server over stdio, the transport an MCP client spawns."""
    server.run("stdio")


if __name__ == "__main__":
    main()
