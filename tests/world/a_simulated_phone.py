"""A phone that does not exist, for testing the loop rather than the phone.

The device scenarios are the truth: they ask a real Pixel and read the answer from
it. They are also thirty minutes for the lot and one machine at a time, which makes
every change to the loop expensive to evaluate, and 44 of the 47 were never run.

So this is the other half. A world is a handful of screens, the controls on each,
and what happens when one is used - driven through the *real* loop by a policy
standing in for the decider. Assertions are about three things: the outcome the
loop named, the screen the world ended on, and the actions the world received.
Seconds to run, no phone, no key, and every stop rule and state rule is exercised
on the way.

Two things make it worth more than a mock.

**The screens are real XML, parsed by the real parser.** A world screen is built as
a uiautomator dump and read back with ``parse_screen``, so a change to how
attributes are read fails here rather than being papered over by a fake that
returns the objects a test wanted.

**The loop is the real loop.** Nothing is stubbed except the four functions that
talk to hardware - reading the screen, reading the focused window, launching an
app, and typing. Stall rules, the state builder, the option builder and the
execution path are all the ones that run against a phone.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from phone_control import goal as goal_module
from phone_control.device_state import Focus
from phone_control.goal import Choice, TaskReport, run_task
from phone_control.options import (
    FINISH,
    GIVE_UP,
    TAP_A_CONTROL,
    WAIT,
)
from phone_control.screen import parse_screen

SCREEN_WIDTH = 1080
SCREEN_HEIGHT = 2400


@dataclass(frozen=True)
class AControl:
    """One control on a world screen.

    The fields are the ones the loop actually reads: what it says, whether it can
    be tapped, whether it is a switch and which way, whether it takes text.
    """

    label: str
    kind: str = "button"
    tappable: bool = True
    # A switch. Left off by default on purpose: measured on a Pixel 8a running
    # Android 17, a settings toggle publishes neither ``checkable`` nor a switch
    # class at all, so the state cannot say which way it is and the loop cannot
    # conclude a goal like "turn off Bluetooth" from the screen. A world that
    # modelled every toggle as checkable would test a phone that does not exist -
    # which is how the checkable-switch fix came to pass its test and never fire on
    # the device. `docs/android-apis.md` has the dump.
    checkable: bool = False
    checked: bool = False
    editable: bool = False
    focused: bool = False
    # An EditText's ``text`` is its value and its ``hint`` is the placeholder, so a
    # world field carries both. That difference is what tells a query typed into a
    # box from one already submitted.
    holds: str = ""
    scrollable: bool = False
    enabled: bool = True
    # Which row it sits in, so a test can put two identical labels on one screen
    # and see whether the loop can tell them apart.
    row: int = 0
    column: int = 0


@dataclass
class AScreen:
    """A screen, and what using its controls does.

    ``on`` maps an action to the screen it leads to: ``"tap:Next"``, ``"key:home"``,
    ``"enter"`` for the return key, ``"scroll_down"``. Anything not named leaves the
    world where it is, which is how a dead control is written.
    """

    name: str
    app: str = "com.example"
    controls: tuple[AControl, ...] = ()
    on: dict[str, str] = field(default_factory=dict)
    # For a screen that is still loading: how many readings until it is ready.
    loads_after: int = 0
    # What the screen says while it is loading.
    loading_label: str = "Loading…"


class AWorld:
    """The phone: where it is, what it has received, and what it shows."""

    def __init__(self, screens: list[AScreen], start: str | None = None) -> None:
        self.screens = {screen.name: screen for screen in screens}
        self.page = self.screens[start or screens[0].name]
        self.received: list[str] = []
        self.typed: dict[str, str] = {}
        self.ticks = 0
        self.readings = 0
        self._waits_left = 0
        self.opened_app: str | None = None

    # --- what the loop asks of a phone -----------------------------------

    def a_screen(self):
        """The current screen, as the real parser would read it from a dump."""
        self.readings += 1
        self.ticks += 1
        return parse_screen(self._as_a_dump())

    def the_focused_window(self) -> Focus:
        return Focus(package=self.page.app, window=f"{self.page.app}/.Main")

    def shell(self, command: str, timeout: float | None = None) -> str:
        """Only the two questions the loop asks of a shell are answered."""
        if "pm list packages" in command:
            return "\n".join(f"package:com.example.{index}" for index in range(3))
        if "query-activities" in command:
            return "\n".join(
                f"com.example.{name}/.Main" for name in ("Clock", "Settings")
            )
        return ""

    def run(self, arguments, timeout: float | None = None) -> str:
        """A command from the loop, recorded and acted on."""
        command = " ".join(str(one) for one in arguments)
        self.received.append(command)
        if "input tap" in command:
            self._a_tap_at(int(arguments[3]), int(arguments[4]))
        elif "input keyevent" in command:
            self._a_key(str(arguments[3]))
        elif "input swipe" in command:
            self._a_scroll(arguments)
        return ""

    def run_binary(self, arguments, timeout: float | None = None) -> bytes:
        return b""

    # --- what happens -----------------------------------------------------

    def _a_tap_at(self, x: int, y: int) -> None:
        for control in self.page.controls:
            left, top, right, bottom = _bounds_of(control)
            if left <= x <= right and top <= y <= bottom:
                self._using(control)
                return

    def _using(self, control: AControl) -> None:
        if control.editable:
            self._waits_left = 0
            return
        if control.checkable:
            # A switch flips where it stands, which is how a switch works and why
            # the screen changes without the page changing.
            flipped = not control.checked
            self.screens[self.page.name] = _with_the_switch_flipped(
                self.page, control, flipped
            )
            self.page = self.screens[self.page.name]
            return
        self._go(self.page.on.get(f"tap:{control.label}"))

    def _a_key(self, keycode: str) -> None:
        if keycode == "3":
            self._go(self.page.on.get("key:home"))
        elif keycode == "4":
            self._go(self.page.on.get("key:back"))
        elif keycode == "66":
            self._go(self.page.on.get("enter"))

    def _a_scroll(self, arguments) -> None:
        # The y coordinates are the 5th and 7th arguments; the 4th and 6th are the
        # x pair and are equal, so comparing those said "up" every time and the
        # world never scrolled.
        forward = int(arguments[4]) > int(arguments[6]) if len(arguments) > 6 else True
        self._go(self.page.on.get("scroll_down" if forward else "scroll_up"))

    def _go(self, where: str | None) -> None:
        if where and where in self.screens:
            self.page = self.screens[where]
            self._waits_left = self.page.loads_after
            if self._waits_left:
                self._loading = True

    def _a_reading_of_the_page(self):
        """The page as it is now, which for a loading one is not itself yet."""
        return self.page

    def _as_a_dump(self) -> str:
        page = self.page
        controls = page.controls
        if self._waits_left > 0:
            self._waits_left -= 1
            controls = (AControl(page.loading_label, tappable=False),)
        return _a_dump_of(page, controls)


def _bounds_of(control: AControl) -> tuple[int, int, int, int]:
    width = 400
    height = 100
    left = 40 + control.column * (width + 60)
    top = 300 + control.row * (height + 40)
    return left, top, left + width, top + height


def _a_dump_of(page: AScreen, controls) -> str:
    nodes = []
    for index, control in enumerate(controls):
        left, top, right, bottom = _bounds_of(control)
        attributes = [
            f"index='{index}'",
            f"text='{control.holds if control.editable else ('' if control.checkable else control.label)}'",
            f"class='{_a_class_for(control)}'",
            f"package='{page.app}'",
            f"clickable='{'true' if control.tappable else 'false'}'",
            f"enabled='{'true' if control.enabled else 'false'}'",
            f"focused='{'true' if control.focused else 'false'}'",
            f"bounds='[{left},{top}][{right},{bottom}]'",
        ]
        if control.editable:
            attributes.append(f"hint='{control.label}'")
        if control.checkable:
            attributes += [
                "checkable='true'",
                f"checked='{'true' if control.checked else 'false'}'",
            ]
            attributes.append(f"content-desc='{control.label}'")
        if control.scrollable:
            attributes.append("scrollable='true'")
        nodes.append("<node " + " ".join(attributes) + "/>")
    return (
        "<hierarchy rotation='0'>"
        f"<node index='0' text='' class='android.widget.FrameLayout' "
        f"package='{page.app}' clickable='false' "
        f"bounds='[0,0][{SCREEN_WIDTH},{SCREEN_HEIGHT}]'>"
        + "".join(nodes)
        + "</node></hierarchy>"
    )


def _a_class_for(control: AControl) -> str:
    if control.editable:
        return "android.widget.EditText"
    if control.checkable:
        return "android.widget.Switch"
    if control.scrollable:
        return "android.widget.ScrollView"
    return "android.widget.Button"


def _with_the_field_holding(page: AScreen, control: AControl, text: str) -> AScreen:
    controls = tuple(
        AControl(**{**one.__dict__, "holds": text}) if one is control else one
        for one in page.controls
    )
    return AScreen(**{**page.__dict__, "controls": controls})


def _with_the_switch_flipped(
    page: AScreen, control: AControl, flipped: bool
) -> AScreen:
    controls = tuple(
        AControl(**{**one.__dict__, "checked": flipped}) if one is control else one
        for one in page.controls
    )
    return AScreen(**{**page.__dict__, "controls": controls})


# --- running the loop against it -----------------------------------------


@dataclass
class WhatHappened:
    """The loop's report, and what the world made of it."""

    report: TaskReport
    world: AWorld
    states: list[dict]
    policy: Any = None

    @property
    def outcome(self) -> str:
        return self.report.outcome

    @property
    def actions(self) -> list[str]:
        return [step.action for step in self.report.steps]

    @property
    def tapped(self) -> list[str]:
        """The controls the world was actually asked to use, in order."""
        return [
            step.description
            for step in self.report.steps
            if step.action == TAP_A_CONTROL
        ]


def drive(
    world: AWorld,
    policy: Callable[[dict], Choice],
    goal: str = "open the clock app",
    max_steps: int = 10,
    monkeypatch=None,
    hand_off: Any = None,
) -> WhatHappened:
    """Run the real loop against a world, with a policy standing in for Jev."""
    import asyncio

    states: list[dict] = []

    async def a_decider(state):
        states.append(state)
        return policy(state)

    if monkeypatch is not None:
        monkeypatch.setattr(
            goal_module, "read_screen", lambda phone, recover=True: world.a_screen()
        )
        monkeypatch.setattr(
            goal_module, "read_focused_window", lambda phone: world.the_focused_window()
        )
        monkeypatch.setattr(
            goal_module,
            "launch_app",
            lambda phone, apps, name: world.received.append(f"launch:{name}"),
        )
        monkeypatch.setattr(
            goal_module,
            "open_link",
            lambda phone, url: world.received.append(f"open:{url}"),
        )
        monkeypatch.setattr(
            goal_module,
            "type_text_on_device",
            lambda phone, text: _type_into(world, text),
        )

    report = asyncio.run(
        run_task(
            world,
            goal,
            a_decider,
            max_steps=max_steps,
            hand_off=hand_off,
        )
    )
    return WhatHappened(report=report, world=world, states=states, policy=policy)


def _type_into(world: AWorld, text: str) -> None:
    """Typing goes into whichever field is focused, as it does on a phone."""
    for control in world.page.controls:
        if control.editable and control.focused:
            world.typed[control.label] = text
            # The phone's own field holds it now, which is what the loop reads back
            # on the next step.
            world.page = _with_the_field_holding(world.page, control, text)
            world.received.append(f"typed:{text}")
            return
    world.received.append(f"typed-nowhere:{text}")


# --- policies, which stand in for the decider ----------------------------


def a_policy_of(*answers) -> Callable[[dict], Choice]:
    """Answers from a script, so a test says what the decider decided and when."""
    remaining = list(answers)

    def policy(state: dict) -> Choice:
        if not remaining:
            return Choice(GIVE_UP, "the policy ran out")
        answer = remaining.pop(0)
        return answer(state) if callable(answer) else answer

    return policy


def tapping(label: str, confidence: float = 0.9) -> Callable[[dict], Choice]:
    """Tap the control whose description names this label.

    Written as a function of the state rather than a fixed key, because the keys
    are positions and a test should not have to know them.
    """

    def choose(state: dict) -> Choice:
        for key, text in (state.get("targets") or {}).items():
            if label in text:
                return Choice(
                    TAP_A_CONTROL,
                    text,
                    confidence=confidence,
                    probabilities={TAP_A_CONTROL: confidence},
                    target=key,
                )
        return Choice(GIVE_UP, f"nothing on this screen says {label!r}")

    return choose


def opening(app: str, confidence: float = 0.9) -> Callable[[dict], Choice]:
    def choose(state: dict) -> Choice:
        return Choice(
            "open_app",
            f"open {app}",
            confidence=confidence,
            probabilities={"open_app": confidence},
            app=app,
        )

    return choose


def waiting() -> Choice:
    return Choice(WAIT, "the screen is still loading", probabilities={WAIT: 0.8})


def finished(confidence: float = 0.95) -> Choice:
    return Choice(
        FINISH,
        "the goal is already achieved on this screen",
        confidence=confidence,
        probabilities={FINISH: confidence},
        goal_achieved=0.95,
    )


def giving_up(why: str = "nothing here helps") -> Choice:
    return Choice(GIVE_UP, why, probabilities={GIVE_UP: 0.8})
