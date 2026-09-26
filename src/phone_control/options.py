"""What the phone can do from here, and how to ask about it in one request.

This module decides two things and nothing else: which operations a screen can
actually carry out, and how each of them is described to the decider.

**Only what can be executed is offered.** An option the loop cannot carry out is
a guaranteed dead step, and worse than that: it reads as the model being unsure
when the model was never at fault. A web address with no scheme was offered once,
Android could not resolve it, the action did nothing, and the 0.41 confidence on
that step was read as doubt for days. So typing is offered only when a field is
already focused, scrolling only when something can scroll, a website only when
the goal named one, and a tap only when a control can be tapped.

**The operations are mutually exclusive, and targets are a different question.**
These used to share one list, and that is where the confidence went. On a launcher
screen the decider was handed thirty-five app names, twenty-seven controls and ten
operations together and answered ``give_up`` at 0.63 - because confidence measures
how concentrated a choice is, so a list holding several plausible things reads as
doubt whichever of them is right. The reference implementation puts it plainly:
*every stall found while building this was from two options that meant the same
thing.*

So one request carries four questions and only the answer belonging to the chosen
operation is used:

    operation   which kind of action to take
    target      which control, if the operation taps one
    app         which installed app, if the operation opens one
    completion  whether a fresh reading shows the goal met

Asking them together costs almost nothing and buys something specific: the app
answer is given without knowing that opening an app was already chosen, so it
stays a reading of the goal rather than a justification of a decision.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .screen import Screen, ScreenArea, ScreenControl

# The operations. Short keys, because a model reads them as words, and mutually
# exclusive, because overlapping ones are what make a choice look unsure.
FINISH = "finish"
GIVE_UP = "give_up"
WAIT = "wait"
SCROLL_FORWARD = "scroll_forward"
SCROLL_BACKWARD = "scroll_backward"
GO_BACK = "go_back"
GO_HOME = "go_home"
TAP_A_CONTROL = "tap_a_control"
OPEN_APP = "open_app"
OPEN_URL = "open_url"
TYPE_TEXT = "type_the_text"

# The operations that answer the question rather than moving the phone.
THE_ANSWERS = (FINISH, GIVE_UP)

# What is always available, whatever is on screen. Each description says what
# choosing it means for the goal, and where it is the only route to something,
# says that too - a definition alone does not stop the wrong choice.
THE_BASELINE: tuple[tuple[str, str, str], ...] = (
    (
        FINISH,
        "The goal is already achieved on this screen. Choose this only when the "
        "screen itself shows the finished result and nothing further is needed - "
        "not when you are close, and not when the next action would probably "
        "finish it.",
        "finish",
    ),
    (
        GIVE_UP,
        "Nothing available can reach the goal from here, including any app that "
        "could be opened or any control that could be scrolled to. Choose this "
        "only when no operation in this list would make progress - not merely "
        "because the way forward is not visible on screen right now.",
        "give_up",
    ),
    (
        WAIT,
        "Do nothing yet: the screen is still loading or moving, and a fresh "
        "reading of it will show something different.",
        "wait",
    ),
    (
        GO_HOME,
        "Leave whatever is in front and go to the home screen, where the apps are.",
        "key",
    ),
    (
        GO_BACK,
        "Go back to the previous screen. Use it when the last step went somewhere "
        "that does not help and the screen before it did.",
        "key",
    ),
)

SCROLLING: tuple[tuple[str, str], ...] = (
    (SCROLL_FORWARD, "Scroll down to reveal more of this screen."),
    (SCROLL_BACKWARD, "Scroll up, to reveal what is above."),
)

OPENS_A_CONTROL = (
    TAP_A_CONTROL,
    "Tap one of the controls on screen. Which one is chosen in the target "
    "question. This is how a button, a row, a switch or a tab is used.",
)

OPENS_AN_APP = (
    OPEN_APP,
    "Open one of the apps installed on this phone. Which one is chosen in the app "
    "question. Use it when the thing needed is in another app, or when the app "
    "that would do this job is not the one in front. An app can be opened from "
    "anywhere, including the home screen.",
)

MAX_OPTION_WORDS = 40

# Where a control sits, coarsely. An exact rectangle is noise to a decision and
# the phone's layout leaking out; a column and a row are enough to tell two
# similar labels apart, which is the only job the position has.
COLUMNS = ("left", "centre", "right")
ROWS = ("top", "middle", "bottom")


@dataclass(frozen=True)
class OfferedAction:
    """One operation, with everything needed to carry it out.

    ``control`` and ``value`` are the operation's parameters. They are not sent
    with the option: they travel in their own question, so the operation choice
    stays free of screen noise.
    """

    option: str
    description: str
    kind: str
    control: ScreenControl | None = None
    value: str | None = None


# --- which operations this screen can really carry out -------------------


def something_can_be_scrolled(screen: Screen) -> bool:
    return any(control.is_scrollable for control in screen.controls)


def the_controls_that_can_be_tapped(screen: Screen) -> list[ScreenControl]:
    """The controls worth offering as targets.

    Tappable, enabled, and named, with a control that is only a container for one
    carrying the same words taken out. Both halves matter: a row whose own text
    repeats its child's goes to the same place as that child, and offering both is
    two options that mean one thing.
    """
    # The keys of an on-screen keyboard count. They live in their own window and
    # so outside ``screen.controls``, and leaving them out took away the ability to
    # press one: a key is an ordinary button carrying its character in
    # content-desc, and it is something a person taps.
    candidates = [
        control
        for control in (*screen.controls, *screen.keyboard_keys)
        if control.is_tappable and control.is_enabled and (control.label or "").strip()
    ]
    return without_containers_that_only_gather(without_nested_duplicates(candidates))


def without_containers_that_only_gather(
    controls: list[ScreenControl],
) -> list[ScreenControl]:
    """Drop a container offered under a label assembled out of what is inside it.

    Such a label is not a name, and the centre of a container is not where any of the
    thing it names is. Measured on YouTube, where a sponsored row spanning the top half
    of the screen - `[0,268][1080,1835]`, centre `(540,1051)` - was offered as a target
    called "Sponsored - Your next Pixel goes above and beyond …". The run tapped it at
    confidence 0.98 and nothing happened, because the middle of that rectangle is not
    the link.

    Its children are the real targets and they are offered in their own right, so
    dropping the container removes a wrong option rather than a route. This is the same
    rule the accessibility walk in the reference applies - "skip nameless AXGroup
    layout boxes, even pressable ones" - and it is deliberately narrower than the
    duplicate rule above: only a label the parser had to *borrow* from descendants is
    suspect, because a container with text of its own is usually a real row.
    """
    kept: list[ScreenControl] = []
    for control in controls:
        gathers_its_children = control.label_from_children and any(
            other is not control and _contains(control.area, other.area)
            for other in controls
        )
        if not gathers_its_children:
            kept.append(control)
    return kept


def offer_actions(
    screen: Screen,
    apps_that_can_be_opened: list[str] | None = None,
    text_to_type: str | None = None,
    url_to_open: str | None = None,
    a_field_is_focused: bool = False,
) -> list[OfferedAction]:
    """Every operation this screen can carry out, in the order to present them.

    Each one is here because it can be done, not because the goal's wording
    resembles it. ``a_field_is_focused`` comes from the phone: typing is offered
    when something is ready to receive text, which is a fact about the screen and
    not about the sentence.

    **The two valued operations are the one place the goal's words decide an
    offering, and the reason is worth stating rather than leaving to be found.**
    `text_to_type` and `url_to_open` are read out of the goal, and if the goal names
    neither, the operation is not offered. That looks like the rule this file is
    built on being broken - the reference design offers operations from a capability
    list and never from an analysis of the goal - so here is the difference.

    It is not choosing an operation; it is supplying a value to one. And the value
    cannot come from the decider, because **Jev answers in typed questions only**:
    a choice among offered options, a Noul, or a position on a scale. There is no
    answer shape that carries free text, so a model that chose "open a web address"
    would have no way to say which address. The alternatives are the goal, or the
    screen - and a screen is only ever read, never authored.

    Offering them regardless would be worse, and the reference says why in its own
    words: an option the loop cannot execute is a guaranteed stall. An "open a web
    address" with no address, put in front of a decider that is asked to pick the
    best available move, is exactly that - and it would read as the model's doubt
    when the model was never at fault.
    """
    offered = [
        OfferedAction(option, description, kind)
        for option, description, kind in THE_BASELINE
    ]

    if the_controls_that_can_be_tapped(screen):
        offered.append(OfferedAction(*OPENS_A_CONTROL, "tap"))

    if apps_that_can_be_opened:
        offered.append(OfferedAction(*OPENS_AN_APP, "open_app"))

    if something_can_be_scrolled(screen):
        offered.extend(
            OfferedAction(option, description, "scroll")
            for option, description in SCROLLING
        )

    # Offered last, and only when the phone is ready for them: these are the
    # operations that carry a value, and a value that cannot be used is what turns
    # an offered option into a dead step.
    if a_field_is_focused and text_to_type:
        offered.append(
            OfferedAction(
                TYPE_TEXT,
                f'type "{text_to_type}" into the field that is focused on this screen',
                "type",
                value=text_to_type,
            )
        )

    if url_to_open:
        offered.append(
            OfferedAction(
                OPEN_URL,
                f"open {url_to_open} in a browser and show that page",
                "url",
                value=url_to_open,
            )
        )

    return offered


def as_criteria(offered: list[OfferedAction]) -> dict[str, str]:
    """The operations as the keys and descriptions a choice question takes."""
    return {action.option: _trim(action.description) for action in offered}


def _trim(description: str) -> str:
    words = description.split()
    if len(words) <= MAX_OPTION_WORDS:
        return description
    return " ".join(words[:MAX_OPTION_WORDS]) + "…"


def the_targets_offered(
    screen: Screen,
) -> tuple[dict[str, str], dict[str, ScreenControl]]:
    """The targets, and the control each refers to, as their own question.

    Keyed by position, so the key is short and carries nothing the model has to
    decode: the description is where the meaning is.
    """
    controls = at_distinct_points(the_controls_that_can_be_tapped(screen))
    # Neighbours come from every control on screen, not only the tappable ones: the
    # thing that tells one "Buy" from another is the text beside it, and that text
    # is a label, not a button. Looking only among the targets found no neighbours
    # at all, and the two Buys read identically.
    described = {
        str(index): describe_a_control(control, screen.controls, screen)
        for index, control in enumerate(controls)
    }

    # Two options described the same way are two options that mean the same thing
    # as far as the decider can tell, and that always reads as doubt whichever it
    # picks. Two buttons labelled "Send" in the same corner of a screen are the
    # ordinary case, and the coarse position cannot separate them - so the ones
    # that collide get their exact centre, and only those.
    for keys in _grouped_by_description(described).values():
        if len(keys) < 2:
            continue
        # The group key is folded for comparison, so the text to show is taken from
        # the description itself - rendering the key would lowercase the label.
        for key in keys:
            control = controls[int(key)]
            described[key] = (
                f"{described[key]} - at exactly ({control.area.center_x}, "
                f"{control.area.center_y})"
            )

    by_position = {str(index): control for index, control in enumerate(controls)}
    return described, by_position


def at_distinct_points(controls: list[ScreenControl]) -> list[ScreenControl]:
    """One control per point on the screen.

    Two controls whose centres are the same are one tap: the loop sends a
    coordinate, so offering both offers the same action twice under two
    descriptions. Measured on a camera screen, where an icon and the label around
    it share a centre - and the descriptions could not be told apart even after the
    collision rule gave them their exact positions, because the positions were
    identical.

    The one with more to say wins, since a target is chosen by reading it.
    """
    seen: dict[tuple[int, int], ScreenControl] = {}
    for control in controls:
        point = (control.area.center_x, control.area.center_y)
        held = seen.get(point)
        if held is None or len(control.label or "") > len(held.label or ""):
            seen[point] = control
    return list(seen.values())


def _grouped_by_description(described: dict[str, str]) -> dict[str, list[str]]:
    """The targets that read the same, grouped by what a reader would see.

    Case-folded, because two options differing only in capitalisation are two options
    the decider cannot tell apart - which is the whole thing this exists to prevent.
    Grouping by exact text let them through: the check that caught it folds the case
    and the fix did not, and a run was offered two targets reading "Pixel homescreen
    wallpaper recommendations" and "pixel homescreen wallpaper recommendations".
    """
    grouped: dict[str, list[str]] = {}
    for key, text in described.items():
        grouped.setdefault(text.casefold(), []).append(key)
    return grouped


def the_apps_offered(apps: list[str]) -> dict[str, str]:
    """The apps as their own question, keyed by the name a person would use."""
    return {name: f"the {name} app, which is installed on this phone" for name in apps}


# --- saying where a control is, and what tells it apart ------------------


def where_a_control_is(control: ScreenControl, screen: Screen) -> str:
    """A row and a column. Coarse on purpose: see COLUMNS and ROWS above."""
    width = max(1, screen.width)
    height = max(1, screen.height)
    column = COLUMNS[min(2, max(0, control.area.center_x * 3 // width))]
    row = ROWS[min(2, max(0, control.area.center_y * 3 // height))]
    return f"{row} {column}"


def what_tells_it_apart(
    control: ScreenControl, others: list[ScreenControl]
) -> str | None:
    """The neighbours of a label that something else on screen also uses.

    Three rows of events each ending in "Buy": the label alone says nothing about
    which one, and the row does. That is a fact the layout holds and the model
    cannot see, so the code reads it and hands it over. The reference does the
    same, and found it the same way - a duplicated label splitting the vote. Two
    labels that share a row get only a coarse region hint, which is a real limit
    of reading the screen rather than of this function.
    """
    own = (control.label or "").strip().casefold()
    if not own:
        return None
    if not any(
        other is not control and (other.label or "").strip().casefold() == own
        for other in others
    ):
        return None

    half_height = max(1.0, control.area.height) / 2
    beside = sorted(
        (
            other
            for other in others
            if other is not control
            and (other.label or "").strip()
            and abs(other.area.center_y - control.area.center_y) < half_height
        ),
        key=lambda one: one.area.center_x,
    )
    names = [one.label.strip() for one in beside[:3]]
    return "beside " + ", ".join(repr(name) for name in names) if names else None


def describe_a_control(
    control: ScreenControl, others: list[ScreenControl], screen: Screen
) -> str:
    """One line per control: what it says, where it is, and what tells it apart.

    The position is always given, because it is the only thing separating two
    controls carrying the same words, and a control with no label is described by
    its kind and position rather than left nameless.
    """
    label = (control.label or "").strip() or f"an unnamed {control.kind}"
    parts = [f"{control.kind} {label!r}", where_a_control_is(control, screen)]
    apart = what_tells_it_apart(control, others)
    if apart:
        parts.append(apart)
    if control.is_selected:
        parts.append("currently selected")
    # A switch's own state is the fact that decides whether a goal like "turn off
    # Bluetooth" is finished, and it is the one thing a screen full of labels does
    # not say. Measured: with it missing, the decider tapped the same switch five
    # times - the goal was reached on the second tap - because nothing told it the
    # switch had moved, and the run ended as a stall over a phone that had done
    # exactly what was asked.
    if control.is_checkable:
        parts.append("currently on" if control.is_checked else "currently off")
    if control.is_editable:
        parts.append("a field you can type into")
    if not control.is_enabled:
        parts.append("disabled")
    return f"{parts[0]} ({', '.join(parts[1:])})"


# --- telling a container from its children -------------------------------


def _contains(outer: ScreenArea, inner: ScreenArea) -> bool:
    return (
        outer.left <= inner.left
        and outer.top <= inner.top
        and outer.right >= inner.right
        and outer.bottom >= inner.bottom
    )


def without_nested_duplicates(controls: list[ScreenControl]) -> list[ScreenControl]:
    """Drop a control that is only a container for one carrying the same words.

    Only an *exact* match collapses. A label merely contained in a container's is
    a different thing entirely, and treating it as a duplicate deleted both
    buttons of a permission dialog once: a dialog's own label is the
    concatenation of its children's text, so "Allow" is a substring of it, and
    "Allow" and "Don't allow" both went with it.
    """
    kept: list[ScreenControl] = []
    for control in controls:
        own = (control.label or "").strip().casefold()
        is_only_a_frame = any(
            other is not control
            and (other.label or "").strip().casefold() == own
            and control.area != other.area
            and _contains(control.area, other.area)
            for other in controls
        )
        if not is_only_a_frame:
            kept.append(control)
    return kept


# --- what the goal carries, for filling a field rather than choosing a move ---

WEB_ADDRESS = re.compile(
    r"""(?:https?://[^\s,;'"]+)     # a full address, as written
      | (?:\b[a-z0-9-]+(?:\.[a-z0-9-]+)+(?:/[^\s,;'"]*)?)""",  # or a bare domain
    re.IGNORECASE | re.VERBOSE,
)

QUOTED_TEXT = re.compile(r'["\']([^"\']{2,})["\']')
# Cues that introduce the text itself. "saying hello" carries the word hello and
# nothing else, whatever else the sentence says.
CONTENT_CUES = re.compile(
    r"\b(?:saying|that says|says|with the (?:text|message|note))\s+(.{2,120}?)\s*$",
    re.IGNORECASE,
)

# Cues that name an *instruction*, whose object may be a description of the text
# rather than the text. "type a number into the dialler" names no number at all, and
# "write a note saying hello" names the note rather than its contents.
INSTRUCTION_CUES = re.compile(
    r"\b(?:search for|search|look up|type|write|enter|fill in)\s+(.{2,120}?)\s*$",
    re.IGNORECASE,
)


def web_address_the_goal_names(goal: str) -> str | None:
    """A web address in the goal, given a scheme the device can actually resolve.

    The scheme is the point of this function. ``am start -d youtube.com`` arrives
    with an empty data URI and fails to resolve, while ``-d https://youtube.com``
    opens the page - both verified on the device. A bare domain is how a person
    writes an address and is not something an intent can use.
    """
    match = WEB_ADDRESS.search(goal)
    if not match:
        return None
    address = match.group(0).rstrip(".,;:")
    if not address.casefold().startswith(("http://", "https://")):
        address = f"https://{address}"
    return address


# A second instruction wearing the same sentence. "search for pixel phone wallpaper
# and open the first result" carries one thing to type and one thing to do afterwards,
# and a regex cannot tell them apart from the grammar - so this cuts at the words that
# most often begin the second one.
A_SECOND_INSTRUCTION = re.compile(
    r"\s+(?:and|then)\s+(?:then\s+)?"
    r"(?:open|click|tap|press|submit|go|show|find|select|choose|take|send|scroll|"
    r"swipe|read|play|call|share|delete|close)\b",
    re.IGNORECASE,
)


def text_the_goal_carries(goal: str) -> str | None:
    """The words a goal wants typed, when it plainly carries some.

    Used to *fill* a field once one is focused, never to decide that typing is the
    move: the phone says whether anything can receive text, and a sentence cannot.

    **This is the weakest thing in the loop, and it is here because nothing else can
    be.** A decision model returns a choice from a list and cannot write, so the text
    to type has to come from somewhere, and the somewhere is the user's own sentence.
    The reference implementation's answer is a *writer model* - "the classifier never
    generates text" - which is a second model rather than a second regex, and that is
    the durable fix.

    Measured cost of the regex, so the next person knows what they are inheriting:
    "open chrome and search for pixel phone wallpaper and open the first result"
    typed all of it, including the second instruction, and the run searched for a
    sentence. The cut below is a patch on a known-limited approach, not a solution.
    """
    quoted = QUOTED_TEXT.search(goal)
    if quoted:
        return quoted.group(1).strip()

    # A cue that introduces the content is tried first, across the whole sentence,
    # because an instruction cue earlier in the same sentence will otherwise win by
    # being earlier. Measured: "open google keep and write a note saying hello" gave
    # "a note saying hello" - the words after "write" - and the loop typed that into
    # the note, so the note it wrote was titled with the instruction rather than
    # saying hello. The sentence names the content plainly, and only the order of the
    # cues was wrong.
    stripped = goal.strip().rstrip(".!?")
    match = CONTENT_CUES.search(stripped) or INSTRUCTION_CUES.search(stripped)
    if not match:
        return None

    carried = A_SECOND_INSTRUCTION.split(match.group(1).strip())[0].strip()
    if carried and not carried.casefold().startswith(("the app", "to the")):
        return carried
    return None
