"""Reading the controls currently on the phone's screen.

An agent cannot tap what it cannot name. This module turns the device's
accessibility tree into a flat, numbered list of controls, so every later
action can refer to "#7" instead of a pair of pixel coordinates that mean
nothing and break the moment the layout shifts.
"""

from __future__ import annotations

import contextlib
import os
import re
import uuid
import xml.etree.ElementTree as ElementTree
from dataclasses import dataclass, replace
from typing import Literal, TypedDict

from .adb import AndroidPhone
from .errors import PhoneCommandFailed

ControlKind = Literal[
    "button",
    "text_field",
    "list_item",
    "container",
    "icon",
    "switch",
    "other",
]


class ControlView(TypedDict):
    """One thing on screen, as a caller sees it.

    A faithful projection of the accessibility node rather than a sentence about
    it: the node's own fields, plus the two conveniences a caller wants, which
    are the category and a point to tap.
    """

    index: int
    label: str
    kind: ControlKind
    class_name: str
    text: str
    description: str
    hint: str
    resource_id: str
    package: str
    bounds: list[int]
    center_x: int
    center_y: int
    clickable: bool
    editable: bool
    scrollable: bool
    checkable: bool
    checked: bool
    enabled: bool
    focused: bool


BOUNDS_PATTERN = re.compile(r"\[(-?\d+),(-?\d+)\]\[(-?\d+),(-?\d+)\]")
DUMP_DIRECTORY = "/sdcard"
DUMP_NAME_PREFIX = "dsh_screen"

# uiautomator reports a node's runtime class, so an EditText subclass does not
# end in "EditText". AutoCompleteTextView and SearchView$SearchAutoComplete are
# ordinary search fields that a suffix check misses entirely.
EDITABLE_CLASS_MARKERS = (
    "EditText",
    "AutoCompleteTextView",
    "SearchAutoComplete",
)

MAX_LABEL_CHARACTERS = 140

# What the accessibility tree marks a field with when its contents must not be
# read out. Checked as substrings because the spelling varies by widget and by
# Android version, and a miss here means a password is sent to a model.
PASSWORD_MARKERS = ("password", "passwd", "passcode", "pin", "credential")


def is_a_password_field(node, class_name: str) -> bool:
    """Whether the phone marks this field as holding something secret."""
    named = f"{class_name} {node.get('resource-id') or ''}".casefold()
    if any(marker in named for marker in PASSWORD_MARKERS):
        return True
    spoken = f"{node.get('hint') or ''} {node.get('content-desc') or ''}".casefold()
    return any(marker in spoken for marker in PASSWORD_MARKERS)


MAX_CONTROLS = 220
CONTROL_DUMP_TIMEOUT_SECONDS = 45.0

# Matching weights: how strongly a query hit on one field counts.
_EXACT_MULTIPLIER = 4
_PREFIX_MULTIPLIER = 3
_SUBSTRING_MULTIPLIER = 2
_ACTIONABLE_BONUS = 5


@dataclass(frozen=True)
class ScreenArea:
    """A rectangle on the screen, in device pixels."""

    left: int
    top: int
    right: int
    bottom: int

    @classmethod
    def parse(cls, raw: str) -> ScreenArea | None:
        match = BOUNDS_PATTERN.fullmatch(raw.strip())
        if match is None:
            return None
        return cls(*(int(group) for group in match.groups()))

    @property
    def center_x(self) -> int:
        return (self.left + self.right) // 2

    @property
    def center_y(self) -> int:
        return (self.top + self.bottom) // 2

    @property
    def width(self) -> int:
        return self.right - self.left

    @property
    def height(self) -> int:
        return self.bottom - self.top

    @property
    def is_visible(self) -> bool:
        return self.width > 0 and self.height > 0


@dataclass(frozen=True)
class ScreenControl:
    """One named, addressable thing on the screen."""

    index: int
    label: str
    text: str
    description: str
    hint: str
    resource_id: str
    class_name: str
    package: str
    area: ScreenArea
    is_tappable: bool
    is_editable: bool
    is_scrollable: bool
    is_checkable: bool
    is_checked: bool
    is_enabled: bool
    is_focused: bool
    is_selected: bool
    # Set by the parser, because this is the one place that sees the raw node and
    # the one place a value must be dropped rather than passed on.
    is_password: bool = False
    # True when the label was borrowed from the node's descendants rather than being
    # the node's own name. Such a label is a concatenation of everything inside, and
    # the centre of a container is not where any of it is - measured on YouTube, where
    # a sponsored row spanning the top half of the screen was offered as a target with
    # the label "Sponsored - Your next Pixel goes above and beyond ...", and tapping
    # its centre opened nothing.
    label_from_children: bool = False

    @property
    def short_class_name(self) -> str:
        """The class name without its package prefix, for display."""
        return self.class_name.rsplit(".", 1)[-1]

    @property
    def kind(self) -> ControlKind:
        """A rough category, for reasoning about what this control is.

        A heuristic from the class name plus the node's own flags. The class name
        is an implementation detail of whichever toolkit drew the screen, and a
        model reasons better about "button" than about "AppCompatImageView".
        """
        if self.is_editable:
            return "text_field"
        if self.is_checkable:
            return "switch"
        simple = self.short_class_name
        if "Button" in simple:
            return "button"
        if simple in ("ImageView", "ImageButton") or simple.endswith("Icon"):
            return "icon"
        if simple.endswith(("RecyclerView", "ListView", "GridView")):
            return "container"
        if self.is_tappable:
            # A tappable node that is not a button is, in practice, a row.
            return "list_item"
        if "Layout" in simple or "Group" in simple or simple == "View":
            return "container"
        return "other"

    def as_view(self) -> ControlView:
        """This control as the tool surface reports it.

        Deliberately not the same as ``as_dict``: that one stays faithful to the
        accessibility tree for a decision model, while this one adds the derived
        conveniences a caller wants — a category, a centre to tap, a label.
        """
        return {
            "index": self.index,
            "label": self.label,
            "kind": self.kind,
            "class_name": self.class_name,
            "text": self.text,
            "description": self.description,
            "hint": self.hint,
            "resource_id": self.resource_id,
            "package": self.package,
            "bounds": [
                self.area.left,
                self.area.top,
                self.area.right,
                self.area.bottom,
            ],
            "center_x": self.area.center_x,
            "center_y": self.area.center_y,
            "clickable": self.is_tappable,
            "editable": self.is_editable,
            "scrollable": self.is_scrollable,
            "checkable": self.is_checkable,
            "checked": self.is_checked,
            "enabled": self.is_enabled,
            "focused": self.is_focused,
        }

    @property
    def is_actionable(self) -> bool:
        return (
            self.is_tappable
            or self.is_editable
            or self.is_scrollable
            or self.is_checkable
        )

    def as_dict(self) -> dict[str, object]:
        """This control in the tree's own terms, not rewritten into prose.

        A decision model scores a faithful projection of its source better than a
        summary derived from it, so the field names and values here are the ones
        the accessibility tree used. The prose in ``describe`` is for a human
        reading a listing, and is not what goes to a model.
        """
        return {
            "index": self.index,
            "class": self.class_name,
            "text": self.text,
            "content-desc": self.description,
            "hint": self.hint,
            "resource-id": self.resource_id,
            "package": self.package,
            "bounds": [
                self.area.left,
                self.area.top,
                self.area.right,
                self.area.bottom,
            ],
            "clickable": self.is_tappable,
            "editable": self.is_editable,
            "scrollable": self.is_scrollable,
            "enabled": self.is_enabled,
            "focused": self.is_focused,
            "selected": self.is_selected,
            "checked": self.is_checked if self.is_checkable else None,
        }

    def describe(self) -> str:
        """A compact phrase naming this control, for a decision model."""
        traits: list[str] = []
        if self.is_editable:
            traits.append("text field")
        elif self.is_tappable:
            traits.append("tappable")
        if self.is_checkable:
            traits.append("checked" if self.is_checked else "unchecked")
        if self.is_scrollable:
            traits.append("scrollable")
        if self.is_focused:
            traits.append("focused")
        if self.is_selected:
            traits.append("selected")
        if not self.is_enabled:
            traits.append("disabled")

        suffix = f" [{', '.join(traits)}]" if traits else ""
        origin = f"in {self.package}" if self.package else ""
        return (
            f'{self.short_class_name} labeled "{self.label}" at '
            f"({self.area.center_x},{self.area.center_y}) {origin}{suffix}"
        ).strip()

    def as_line(self) -> str:
        """One line of the numbered listing shown to an agent."""
        return f"#{self.index} {self.describe()}"

    def match_score(self, needle: str) -> int:
        """How well this control answers a lowercased query; 0 means no match."""
        best = 0
        for weight, value in (
            (4, self.text),
            (4, self.description),
            (3, self.hint),
            (2, self.label),
            (2, self.resource_id),
            (1, self.kind),
        ):
            folded = value.casefold().strip()
            if not folded:
                continue
            if folded == needle:
                score = weight * _EXACT_MULTIPLIER
            elif folded.startswith(needle):
                score = weight * _PREFIX_MULTIPLIER
            elif needle in folded:
                score = weight * _SUBSTRING_MULTIPLIER
            else:
                continue
            best = max(best, score)
        if best and self.is_actionable:
            best += _ACTIONABLE_BONUS
        return best


@dataclass(frozen=True)
class ScreenWindow:
    """One window in the accessibility window list.

    A plain dump reports only the active window, so a dialog or the keyboard can
    be visible to the user and absent from the reading. Asking for all windows
    gives each one's type, which is how the keyboard is recognised for certain
    rather than inferred.
    """

    index: int
    title: str
    window_type: str
    layer: int
    is_active: bool
    is_focused: bool
    has_contents: bool

    @property
    def is_keyboard(self) -> bool:
        return self.window_type == "TYPE_INPUT_METHOD"

    def as_view(self) -> dict[str, object]:
        return {
            "title": self.title,
            "type": self.window_type,
            "layer": self.layer,
            "active": self.is_active,
            "focused": self.is_focused,
            "has_contents": self.has_contents,
        }


@dataclass(frozen=True)
class Screen:
    """Everything nameable on the screen at one moment."""

    controls: tuple[ScreenControl, ...]
    width: int
    height: int
    package: str
    windows: tuple[ScreenWindow, ...] = ()
    # The keyboard's own keys. It is a separate window, so a reading that took
    # only the active one left them out - and they are what makes typing possible
    # without any text-injection API at all.
    keyboard_keys: tuple[ScreenControl, ...] = ()
    # The lines a picture of this screen gave up, when the accessibility tree would not
    # describe it at all. Empty on every ordinary reading, because a dump is cheaper
    # and gives structure that lines of text cannot. Read the note in
    # ``reading_a_picture`` for why this exists and what it cannot do.
    lines_read_from_a_picture: tuple[str, ...] = ()

    @property
    def has_keyboard_window(self) -> bool:
        """Whether a keyboard window is present with contents to read.

        Presence is not visibility: a hidden keyboard still owns a window. The
        verified signal is `status`'s, which reads `mHasSurface` from the window
        manager, so this is deliberately the weaker of the two claims.
        """
        return any(
            window.is_keyboard and window.has_contents for window in self.windows
        )

    def find(self, query: str) -> list[ScreenControl]:
        """Controls matching a query, strongest match first."""
        needle = query.casefold().strip()
        if not needle:
            return list(self.controls)
        scored = [(control.match_score(needle), control) for control in self.controls]
        matches = [pair for pair in scored if pair[0] > 0]
        matches.sort(key=lambda pair: (-pair[0], pair[1].index))
        return [control for _, control in matches]

    def best_match(self, query: str) -> ScreenControl | None:
        matches = self.find(query)
        return matches[0] if matches else None

    def control_at(self, index: int) -> ScreenControl | None:
        for control in self.controls:
            if control.index == index:
                return control
        return None

    def as_text(self) -> str:
        heading = (
            f"Screen {self.width}x{self.height}; foreground app "
            f"{self.package or 'unknown'}; {len(self.controls)} named controls."
        )
        if not self.controls:
            # A page the tree will not describe still has words on it, and this is the
            # rendering every tool hands back. Without this branch a photograph of the
            # screen was read, stored, and then thrown away by the last step: the tool
            # returned "No named controls were found" about a screen whose contents were
            # sitting right there. Measured on the notification shade, which is an
            # overlay that does not go idle and so cannot be dumped.
            if self.lines_read_from_a_picture:
                lines = "\n".join(
                    f"  {line}" for line in self.lines_read_from_a_picture
                )
                return (
                    f"{heading}\nThe phone would not describe this screen, so this is "
                    f"what a picture of it says:\n{lines}"
                )
            return heading + "\nNo named controls were found."
        listing = "\n".join(control.as_line() for control in self.controls)
        return f"{heading}\n{listing}"

    def as_structured(self) -> dict[str, object]:
        """The whole screen as one nested value, in the tree's own terms.

        This is the form to hand a decision model: the source, rather than a
        rendering derived from it.
        """
        view: dict[str, object] = {
            "screen": {"width": self.width, "height": self.height},
            "foreground_app": self.package,
            "controls": [control.as_dict() for control in self.controls],
        }
        if self.lines_read_from_a_picture:
            # The same gap as in `as_text`, in the form a decision model reads. A
            # screen with no controls and no lines is a claim that nothing is there;
            # a screen with lines is a reading, and the two must not look alike.
            view["read_from_a_picture_of_the_screen"] = list(
                self.lines_read_from_a_picture
            )
        return view


def _gather_subtree_text(node: ElementTree.Element, into: dict[int, str]) -> str:
    """Record the concatenated text of each subtree, children first."""
    own = (node.get("text") or "").strip() or (node.get("content-desc") or "").strip()
    child_texts = [_gather_subtree_text(child, into) for child in node]
    parts = [own, *(text for text in child_texts if text)]
    combined = " ".join(dict.fromkeys(part for part in parts if part))
    into[id(node)] = combined
    return combined


def _gather_subtree_switch(
    node: ElementTree.Element, into: dict[int, bool]
) -> bool | None:
    """Record the position of the nearest switch inside each subtree.

    The row that turns Bluetooth on is a clickable container with a label on one
    side and an `android.widget.Switch` on the other. Measured on this device, from
    a dump of that page: the switch node carries `checkable="true"` and the position,
    but it is itself `clickable="false"`, so it is never the control offered as a
    target - the container is. The offered control then reads "list_item 'Use
    Bluetooth'" and says nothing about whether Bluetooth is already on.

    That gap has a measured cost, and it was paid twice. The decider tapped the same
    switch five times over a phone that had turned Bluetooth on at the second tap,
    because nothing in the state said the switch had moved; and because five taps is
    an odd number it happened to end on, while the run reported failure over a goal
    the phone had reached. The row borrows its label from its children already, so it
    borrows its position the same way.
    """
    if node.get("checkable") == "true":
        position = node.get("checked") == "true"
        into[id(node)] = position
        return position
    for child in node:
        found = _gather_subtree_switch(child, into)
        if found is not None:
            into[id(node)] = found
            return found
    return None


def _build_control(
    node: ElementTree.Element,
    index: int,
    subtree_text: dict[int, str],
    subtree_switch: dict[int, bool],
) -> ScreenControl | None:
    area = ScreenArea.parse(node.get("bounds") or "")
    if area is None or not area.is_visible:
        return None

    own_text = (node.get("text") or "").strip()
    description = (node.get("content-desc") or "").strip()
    # An empty field shows its placeholder rather than any text, so `hint` is the
    # only name an untouched search box has. Without it the field is unnameable
    # and never gets offered.
    hint = (node.get("hint") or "").strip()
    class_name = node.get("class") or "View"
    is_tappable = node.get("clickable") == "true"
    is_editable = any(marker in class_name for marker in EDITABLE_CLASS_MARKERS)
    is_scrollable = node.get("scrollable") == "true"
    is_checkable = node.get("checkable") == "true"
    is_checked = node.get("checked") == "true"

    if (
        not (is_tappable or is_editable or is_scrollable or is_checkable)
        and not own_text
        and not description
    ):
        return None

    # A row whose switch is a separate node borrows that switch's position, so the
    # control a decider is offered carries the fact the goal is about. Only a
    # tappable control borrows: a label or an icon beside a switch is not the thing
    # that turns it on, and reporting a position against it would invite a tap on
    # text that does nothing.
    if not is_checkable and is_tappable and id(node) in subtree_switch:
        is_checkable = True
        is_checked = subtree_switch[id(node)]

    # A tappable container usually holds no text of its own; borrow the text of
    # its children so the control can be named and matched.
    label = own_text or description or hint
    label_from_children = False
    if not label and is_tappable:
        label = subtree_text.get(id(node), "")
        label_from_children = bool(label)

    # A password field's contents are the one thing on a screen that must never
    # leave the phone, and `text` is where they arrive. They are dropped here, at
    # the point every other reader gets its copy, rather than in each of the
    # places that might send them somewhere: a state, a log, a run folder, a
    # request to a model. Measured cost of not doing it: the value appeared in the
    # control list sent to the decider, under the field's own name, because the
    # label falls back to the text it holds.
    holds_a_password = is_a_password_field(node, class_name)
    if holds_a_password:
        own_text = ""
        label = description or hint

    return ScreenControl(
        index=index,
        label=label[:MAX_LABEL_CHARACTERS],
        text=own_text,
        description=description,
        hint=hint,
        resource_id=node.get("resource-id") or "",
        class_name=class_name,
        package=node.get("package") or "",
        area=area,
        is_tappable=is_tappable,
        is_editable=is_editable,
        is_scrollable=is_scrollable,
        is_checkable=is_checkable,
        is_checked=is_checked,
        is_enabled=node.get("enabled") != "false",
        is_focused=node.get("focused") == "true",
        is_selected=node.get("selected") == "true",
        is_password=holds_a_password,
        label_from_children=label_from_children,
    )


def _keep_most_useful(controls: list[ScreenControl]) -> list[ScreenControl]:
    """Cap the listing, dropping decoration before anything actionable."""
    if len(controls) <= MAX_CONTROLS:
        return controls
    actionable = [control for control in controls if control.is_actionable]
    decorative = [control for control in controls if not control.is_actionable]
    if len(actionable) >= MAX_CONTROLS:
        # Even a screen made entirely of controls has to fit the budget.
        kept = actionable[:MAX_CONTROLS]
    else:
        kept = actionable + decorative[: MAX_CONTROLS - len(actionable)]
    kept.sort(key=lambda control: control.index)
    return kept


def _window_from(element: ElementTree.Element) -> ScreenWindow:
    return ScreenWindow(
        index=int(element.get("index") or 0),
        title=(element.get("title") or "").strip(),
        window_type=element.get("type") or "",
        layer=int(element.get("layer") or 0),
        is_active=element.get("active") == "true",
        is_focused=element.get("focused") == "true",
        # A window whose root node is null is emitted with no <hierarchy> child,
        # which is itself the signal that it exists but cannot be read.
        has_contents=element.find("hierarchy") is not None,
    )


def _the_window_to_read(root: ElementTree.Element) -> ElementTree.Element | None:
    """The hierarchy worth reporting: the active one, else the focused one.

    Falling back to any window with contents means a reading still says something
    on a device where nothing is marked active.
    """
    windows = list(root.iter("window"))
    for attribute in ("active", "focused"):
        for window in windows:
            if window.get(attribute) == "true" and window.find("hierarchy") is not None:
                return window.find("hierarchy")
    for window in windows:
        if window.find("hierarchy") is not None:
            return window.find("hierarchy")
    return None


def parse_screen(xml_text: str) -> Screen:
    """Turn a uiautomator dump into a numbered control listing.

    Handles both document shapes the dumper produces: a plain dump is rooted at
    ``<hierarchy>``, while ``--windows`` is rooted at ``<displays>`` and carries
    one ``<window>`` per window. On a device too old to know the flag it is
    ignored, so the plain shape arrives and is handled by the same function.
    """
    root = ElementTree.fromstring(xml_text)
    if root.tag != "displays":
        return _screen_from_hierarchy(root, ())

    windows = tuple(_window_from(element) for element in root.iter("window"))
    keyboard_keys = _keys_in_the_keyboard_window(root)
    hierarchy = _the_window_to_read(root)
    if hierarchy is None:
        # Every window exists but none can be read.
        return Screen(
            controls=(),
            width=0,
            height=0,
            package="",
            windows=windows,
            keyboard_keys=keyboard_keys,
        )
    return replace(
        _screen_from_hierarchy(hierarchy, windows), keyboard_keys=keyboard_keys
    )


def _keys_in_the_keyboard_window(
    root: ElementTree.Element,
) -> tuple[ScreenControl, ...]:
    """The keys of an on-screen keyboard, which lives in a window of its own.

    A key is an ordinary button carrying its character in ``content-desc``, not in
    ``text``, which is why looking for text finds nothing. Gboard reports each
    letter twice, so identical labels are collapsed to the largest, which is the
    one a person would press.
    """
    for window in root.iter("window"):
        if window.get("type") != "TYPE_INPUT_METHOD":
            continue
        hierarchy = window.find("hierarchy")
        if hierarchy is None:
            continue
        keys = _screen_from_hierarchy(hierarchy, ()).controls
        by_label: dict[str, ScreenControl] = {}
        for key in keys:
            if not key.label:
                continue
            held = by_label.get(key.label)
            if (
                held is None
                or key.area.width * key.area.height > held.area.width * held.area.height
            ):
                by_label[key.label] = key
        return tuple(by_label.values())
    return ()


def _screen_from_hierarchy(
    root: ElementTree.Element, windows: tuple[ScreenWindow, ...]
) -> Screen:
    subtree_text: dict[int, str] = {}
    _gather_subtree_text(root, subtree_text)
    subtree_switch: dict[int, bool] = {}
    _gather_subtree_switch(root, subtree_switch)

    collected: list[ScreenControl] = []
    stack = [root]
    while stack:
        node = stack.pop()
        control = _build_control(node, len(collected), subtree_text, subtree_switch)
        if control is not None:
            collected.append(control)
        stack.extend(reversed(list(node)))

    kept = _keep_most_useful(collected)
    renumbered = [_renumber(control, position) for position, control in enumerate(kept)]

    frame = _screen_frame(root)
    frame_area = ScreenArea.parse(frame.get("bounds") or "")
    return Screen(
        controls=tuple(renumbered),
        width=frame_area.width if frame_area else 0,
        height=frame_area.height if frame_area else 0,
        package=frame.get("package") or "",
        windows=windows,
    )


def _screen_frame(root: ElementTree.Element) -> ElementTree.Element:
    """The outermost node carrying the screen bounds and package.

    A uiautomator dump puts only the rotation on the ``<hierarchy>`` element, so
    the frame that describes the screen is its first child. A dump rooted
    straight at a node is handled as well.
    """
    if root.get("bounds"):
        return root
    for child in root:
        if child.get("bounds"):
            return child
    return root


def _renumber(control: ScreenControl, index: int) -> ScreenControl:
    """The same control, with a new index.

    Built by ``dataclasses.replace`` rather than by listing the fields. The listed
    version silently dropped any field added later: a password value went on being
    redacted while the flag saying *why* it had been redacted was reset, so the
    state reported a password field as empty rather than as a password. A copy
    that cannot forget a field is worth more than a copy that reads neatly.
    """
    return replace(control, index=index)


def _fresh_dump_path() -> str:
    """A dump path unique to one call.

    A fixed path is the wrong choice twice over. A failed dump does not remove
    the file and does not necessarily fail the command, so the previous screen
    would still be sitting there and would parse as if it were the current one --
    the agent would then act on a screen that no longer exists. And two calls in
    flight together would read each other's half-written file.
    """
    return (
        f"{DUMP_DIRECTORY}/{DUMP_NAME_PREFIX}_{os.getpid()}_{uuid.uuid4().hex[:8]}.xml"
    )


def _discard_dump(phone: AndroidPhone, remote_path: str) -> None:
    """Remove the dump file, ignoring any failure to do so."""
    with contextlib.suppress(Exception):
        phone.run(["shell", "rm", "-f", remote_path])


def _the_helper_is_wedged(phone: AndroidPhone) -> bool:
    """Whether the dump helper is answering at all.

    The distinction that matters is between a screen that is not ready - which
    retrying fixes - and a helper that has stopped working, which retrying never
    fixes and which looks identical from the outside.
    """
    probe_path = _fresh_dump_path()
    try:
        said = phone.shell(f"uiautomator dump {probe_path} 2>&1")
    except Exception:
        return False
    finally:
        _discard_dump(phone, probe_path)
    return "could not get idle state" in said.casefold()


def _clear_the_helper(phone: AndroidPhone) -> None:
    """Kill a wedged dump helper. Ignored when it is not there to kill."""
    for command in (
        "pkill -f uiautomator",
        "am force-stop com.github.uiautomator",
    ):
        with contextlib.suppress(Exception):
            phone.shell(command)


def read_screen(phone: AndroidPhone, recover: bool = True) -> Screen:
    """Dump the accessibility tree from the device and parse it.

    ``--windows`` is requested so the reading covers every window rather than
    only the active one: a dialog or the keyboard is a window of its own, and a
    plain dump would leave it invisible while it still swallows taps. A device
    too old to know the flag ignores it and returns the plain shape, which the
    parser handles too.

    ``recover`` is for a caller that has a cheaper second way of reading the screen
    and wants to try that before paying for this one. Measured: a dump of a page that
    will not go idle takes 11.4 seconds to fail, the recovery below costs two more
    dumps on top of it, and a picture of the same page costs 0.58 - and a page of
    that kind is dumpable *some* of the time rather than never, so failure says
    nothing about whether waiting would help. The loop therefore reads cheaply first,
    photographs the screen if that fails, and only comes back here with ``recover``
    when the picture gave nothing either. Callers that just want the screen take the
    default and keep the old behaviour.
    """
    remote_path = _fresh_dump_path()
    try:
        phone.run(
            ["shell", "uiautomator", "dump", "--windows", remote_path],
            timeout=CONTROL_DUMP_TIMEOUT_SECONDS,
        )
        raw = phone.run_binary(["exec-out", "cat", remote_path])
    finally:
        # The file is per-call, so it is always ours to clean up. Left behind it
        # would accumulate one file per screen read.
        _discard_dump(phone, remote_path)

    xml_text = raw.decode("utf-8", "replace")
    if recover and "<hierarchy" not in xml_text and _the_helper_is_wedged(phone):
        # Tried once more with the helper cleared, and only after a dump has already
        # failed - an extra dump on every read would cost as much as the read itself.
        #
        # Measured on a Pixel 8a: after a run of many dumps the helper stops answering
        # with "ERROR: could not get idle state." and goes on saying it, while the same
        # screen dumps perfectly from a fresh shell. Nothing else can reach the screen
        # while that lasts, and killing the helper is the remedy for it.
        _clear_the_helper(phone)
        remote_path = _fresh_dump_path()
        try:
            phone.run(["shell", "uiautomator", "dump", "--windows", remote_path])
            raw = phone.shell_binary(f"cat {remote_path}")
            xml_text = raw.decode("utf-8", "replace")
        finally:
            _discard_dump(phone, remote_path)

    if "<hierarchy" not in xml_text:
        raise PhoneCommandFailed(
            "uiautomator dump",
            "the screen dump contained no UI hierarchy",
            fix=(
                "The screen may still be animating, or the phone is locked. "
                "Wake and unlock the phone, then retry."
            ),
        )
    try:
        return parse_screen(xml_text)
    except ElementTree.ParseError as error:
        raise PhoneCommandFailed(
            "uiautomator dump",
            f"the screen dump was not valid XML ({error})",
            fix=(
                "The dump was most likely cut short. Check the phone's screen, "
                "then retry."
            ),
        ) from error
