"""Does Jev do the job, or only say it did?

Each case here is something a person would plausibly ask for. The run reports
whether it succeeded; **these tests do not believe it.** They read the phone's own
state afterwards — the focused window, which the decision never sees — so a run
that claims success without achieving it is counted as a failure rather than a
pass.

That distinction is the point. This project's worst bugs have all been the same
shape: a tool reporting success for something that did not happen. A run that
announces "Done" over a lock screen is not a passing test, however confident it
sounds, so `test_a_claim_of_success_is_never_wrong` is the one that matters most.

Run with a key and a phone:

    .venv/bin/python -m pytest -m "device and jev" -q -s
"""

from __future__ import annotations

import time
from dataclasses import dataclass

import pytest

from phone_control.adb import AndroidPhone
from phone_control.apps import InstalledApps
from phone_control.device_state import read_focused_window
from phone_control.goal import DEFAULT_MAX_STEPS, JevDecider, TaskReport, run_task
from phone_control.jev import JevClient

pytestmark = [pytest.mark.device, pytest.mark.jev]

WAKE_KEYCODE = "224"
HOME_KEYCODE = "3"
SETTLE_SECONDS = 2.0


@dataclass(frozen=True)
class GoalCase:
    """Something a person might ask for, and how to tell it happened."""

    goal: str
    app: str | None
    note: str


# The apps are named the way a person says them, and resolved against what is
# actually installed, so a phone without one of them fails as "not installed"
# rather than as a mysterious wrong answer.
GOALS = (
    GoalCase("open the settings app", "settings", "a system app, always present"),
    GoalCase("open the clock app", "clock", "spoken name differs from its package"),
    GoalCase("open the calculator app", "calculator", "short name, no alias help"),
)


def a_phone_is_ready() -> tuple[bool, str]:
    try:
        phone = AndroidPhone()
        if not phone.is_connected():
            return False, "no Android phone is attached over USB"
        if read_focused_window(phone).window and read_focused_window(phone).package:
            return True, ""
        return True, ""
    except Exception as error:  # pragma: no cover - environment dependent
        return False, str(error)


READY, WHY_NOT = a_phone_is_ready()


@pytest.fixture(scope="module")
def phone() -> AndroidPhone:
    if not READY:
        pytest.skip(WHY_NOT)
    handle = AndroidPhone()
    handle.run(["shell", "input", "keyevent", WAKE_KEYCODE])
    return handle


@pytest.fixture(scope="module")
def jev() -> JevClient:
    client = JevClient()
    if not client.is_configured:
        pytest.skip("no Jev key is configured")
    return client


def go_home(phone: AndroidPhone) -> None:
    """Start every case from the same place, so one cannot flatter another."""
    phone.run(["shell", "input", "keyevent", HOME_KEYCODE])
    time.sleep(SETTLE_SECONDS)


def perform(
    phone: AndroidPhone, client: JevClient, goal: str
) -> tuple[TaskReport, float]:
    """Run one goal through Jev and time it."""
    import asyncio

    decider = JevDecider(client)
    started = time.monotonic()
    report = asyncio.run(
        run_task(phone, goal, decider.choose, max_steps=DEFAULT_MAX_STEPS)
    )
    return report, time.monotonic() - started


def the_app_the_goal_names(phone: AndroidPhone, case: GoalCase) -> str:
    """The package a person would expect to end up in front."""
    return InstalledApps(phone).resolve(case.app or "")


@pytest.mark.parametrize("case", GOALS, ids=lambda case: case.app or case.goal)
def test_jev_reaches_the_app_the_goal_names(phone, jev, case):
    """The claim and the phone's own state have to agree, and agree on success."""
    go_home(phone)
    expected = the_app_the_goal_names(phone, case)

    report, seconds = perform(phone, jev, case.goal)
    arrived = read_focused_window(phone).package

    print(
        f"\n  {case.goal!r} ({case.note})"
        f"\n    claimed  : {report.achieved}  ({report.reason})"
        f"\n    actual   : {arrived or '(no app in front)'}"
        f"\n    expected : {expected}"
        f"\n    steps    : {len(report.steps)} in {seconds:.1f}s"
    )
    for step in report.steps:
        confidence = (
            f" conf={step.confidence:.2f}" if step.confidence is not None else ""
        )
        print(
            f"      {step.index}. {step.action:<16}{confidence}  {step.description[:46]}"
        )

    assert arrived == expected, (
        f"the goal was {case.goal!r}, so {expected} should be in front, "
        f"but {arrived or 'nothing'} is"
    )


def test_a_claim_of_success_is_never_wrong(phone, jev):
    """The invariant this whole file exists for.

    A run may fail. It may not say it succeeded when the phone did not move. Every
    false success this project has shipped was invisible for exactly as long as
    nobody checked the phone afterwards.
    """
    false_successes = []

    for case in GOALS:
        go_home(phone)
        expected = the_app_the_goal_names(phone, case)
        report, _seconds = perform(phone, jev, case.goal)
        arrived = read_focused_window(phone).package

        if report.achieved and arrived != expected:
            false_successes.append(
                f"{case.goal!r}: claimed achieved, but {arrived or 'nothing'} is in "
                f"front rather than {expected}"
            )

    assert not false_successes, (
        "reported success without achieving it:\n  " + "\n  ".join(false_successes)
    )


def test_a_goal_that_cannot_be_met_is_reported_as_unmet(phone, jev):
    """The other half: it must not claim success for something impossible.

    Nothing on an Android phone can order a pizza, so a run that reports this as
    achieved has invented a capability rather than used one.
    """
    go_home(phone)

    report, _ = perform(phone, jev, "order a large pepperoni pizza to my house")

    print(f"\n  claimed: {report.achieved} ({report.reason})")
    for step in report.steps:
        print(f"    {step.index}. {step.action:<16} {step.description[:50]}")

    assert not report.achieved, (
        "the phone reported ordering a pizza, which it cannot do"
    )


# --- a journey, not a single destination --------------------------------
#
# Opening an app is the easy case: one launch and one verification. This asks for
# a screen *inside* an app, then away, then back - so the loop has to navigate
# rather than launch, and has to reach the right place twice in different
# starting states.

JOURNEY = (
    ("open the settings app", "settings", None),
    ("open the world clock in the clock app", "clock", "World clock"),
    ("open the calculator app", "calculator", None),
    ("open the settings app", "settings", None),
    ("open the world clock", "clock", "World clock"),
)


def where_the_journey_should_end(phone, app: str) -> str:
    return InstalledApps(phone).resolve(app)


def that_tab_is_open(phone, tab: str) -> bool:
    """Whether a named tab is the one selected, not merely present.

    Checking that the words "World clock" appear on screen proves nothing: they
    are a tab label, so they are there whichever tab is open. Measured on a real
    device, "World clock" was still on screen after switching to the Alarm tab.
    The selected tab is the one the accessibility tree marks as selected.
    """
    from phone_control.screen import read_screen

    wanted = tab.casefold()
    return any(
        wanted in (control.label or "").casefold() and control.is_selected
        for control in read_screen(phone).controls
    )


def test_the_journey_reaches_every_destination(phone, jev):
    """Each step is verified against the phone, in the state the last one left."""
    results = []
    for goal, app, expected_text in JOURNEY:
        expected_package = where_the_journey_should_end(phone, app)
        report, seconds = perform(phone, jev, goal)

        arrived = read_focused_window(phone).package
        text_found = that_tab_is_open(phone, expected_text) if expected_text else True

        results.append(
            (
                goal,
                report,
                arrived,
                expected_package,
                seconds,
                text_found,
                expected_text,
            )
        )

    lines = []
    for goal, report, arrived, expected, seconds, text_found, expected_text in results:
        verdict = "ok" if (arrived == expected and text_found) else "NOT REACHED"
        print(
            f"\n  {goal!r}  -> {verdict}"
            f"\n    claimed  : {report.achieved}  ({report.reason})"
            f"\n    actual   : {arrived or '(none)'}"
            f"\n    expected : {expected}"
            + (
                f"\n    wanted   : {expected_text!r} to be the selected tab"
                if expected_text
                else ""
            )
            + f"\n    steps    : {len(report.steps)} in {seconds:.1f}s"
        )
        for step in report.steps:
            confidence = (
                f" conf={step.confidence:.2f}" if step.confidence is not None else ""
            )
            print(
                f"      {step.index}. {step.action:<26}{confidence} {step.description[:40]}"
            )

        if arrived != expected:
            lines.append(
                f"{goal!r}: ended on {arrived or 'nothing'}, wanted {expected}"
            )
        elif not text_found:
            lines.append(
                f"{goal!r}: reached the app but {expected_text!r} was not the "
                "selected tab"
            )

    assert not lines, "the journey did not reach:\n  " + "\n  ".join(lines)


def test_no_step_of_the_journey_claims_what_it_did_not_do(phone, jev):
    """The invariant, over a longer run where more can go wrong."""
    false_successes = []

    for goal, app, _expected_text in JOURNEY:
        expected = where_the_journey_should_end(phone, app)
        report, _ = perform(phone, jev, goal)
        arrived = read_focused_window(phone).package

        if report.achieved and arrived != expected:
            false_successes.append(
                f"{goal!r}: claimed achieved, but {arrived or 'nothing'} is in front"
            )

    assert not false_successes, (
        "claimed success without reaching it:\n  " + "\n  ".join(false_successes)
    )
