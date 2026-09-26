"""Four things a person would actually ask for, verified against the phone.

Each has ground truth the decision never sees: a setting read from the phone, a
file appearing on disk, that file disappearing again. So a run is scored on what
happened to the device, not on what it said happened.

These four are chosen because they are harder than opening an app:

    browser + website   needs text typed into an address bar
    bluetooth toggle    needs a switch found and flipped, and the change confirmed
    take a photo        needs a shutter pressed in a visual app
    delete that photo   needs the same file found again and removed

They also change the phone, so each records what it was beforehand and puts it
back: bluetooth is restored, and the photo this suite took is removed by the test
itself if the run did not manage it.

    .venv/bin/python -m pytest -m "device and jev" -q -s -k scenarios
"""

from __future__ import annotations

import time

import pytest

from conftest import a_goal_that_needs_the_network
from phone_control.adb import AndroidPhone
from phone_control.device_state import read_device_state
from phone_control.goal import JevDecider, TaskReport, run_task
from phone_control.jev import JevClient
from phone_control.screen import read_screen

pytestmark = [pytest.mark.device, pytest.mark.jev]

PHOTO_DIRECTORY = "/sdcard/DCIM/Camera"
WAKE_KEYCODE = "224"
HOME_KEYCODE = "3"
SETTLE_SECONDS = 2.0
RUN_SECONDS = 45


def a_phone_is_ready() -> tuple[bool, str]:
    try:
        handle = AndroidPhone()
        return (True, "") if handle.is_connected() else (False, "no phone attached")
    except Exception as error:
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


def photo_files(phone: AndroidPhone) -> set[str]:
    listed = phone.shell(f"ls {PHOTO_DIRECTORY}")
    return {
        name.strip()
        for name in listed.splitlines()
        if name.strip() and "No such file" not in name
    }


def bluetooth_is_on(phone: AndroidPhone) -> bool:
    return phone.shell("settings get global bluetooth_on").strip() == "1"


def go_home(phone: AndroidPhone) -> None:
    phone.run(["shell", "input", "keyevent", HOME_KEYCODE])
    time.sleep(SETTLE_SECONDS)


def perform(
    phone: AndroidPhone, jev: JevClient, goal: str, max_steps: int
) -> TaskReport:
    """Run one goal to the end, tolerating a dropped connection and nothing else.

    Every step of this asks Jev over the network, so a run can fail because a
    request never arrived. See `a_goal_that_needs_the_network` in conftest for why
    that is tolerated here and what is deliberately still allowed to fail.
    """
    import asyncio

    return a_goal_that_needs_the_network(
        lambda: asyncio.run(
            run_task(phone, goal, JevDecider(jev).choose, max_steps=max_steps)
        ),
        f"the goal {goal!r}",
    )


def show(goal: str, report: TaskReport, verdict: str) -> None:
    print(f"\n  {goal!r}  -> {verdict}")
    print(f"    claimed : {report.achieved}  ({report.reason})")
    print(f"    steps   : {len(report.steps)}")
    for step in report.steps:
        changed = step.changed_the_screen
        confidence = (
            f" conf={step.confidence:.2f}" if step.confidence is not None else ""
        )
        print(
            f"      {step.index}. {step.action:<18}{confidence} "
            f"changed={changed!s:<5} {step.description[:32]}"
        )


def test_scenario_browser_and_website(phone, jev):
    """Typing a URL needs the address bar found and text entered into it."""
    go_home(phone)
    goal = "open chrome and go to example.com"

    report = perform(phone, jev, goal, max_steps=8)
    state = read_device_state(phone)
    browser = state.foreground_package
    listing = read_screen(phone).as_text()

    is_browser = "chrome" in browser or "browser" in browser
    on_site = (
        "example.com" in listing.casefold() or "example domain" in listing.casefold()
    )
    show(goal, report, f"browser={is_browser} on-site={on_site} ({browser})")

    assert is_browser, f"expected a browser in front, found {browser or 'nothing'}"
    assert on_site, "the page for example.com does not appear to be showing"


def test_scenario_bluetooth_toggle(phone, jev):
    """A toggle is only done if the setting actually flipped, not if it was tapped."""
    # The starting state has to be established, not inherited. Without this the
    # run began wherever the previous test had left the phone - on one run that
    # was a YouTube search screen, and Jev correctly answered that the goal could
    # not be reached from there. The test then failed a phone that was behaving
    # perfectly.
    go_home(phone)
    was_on = bluetooth_is_on(phone)
    goal = "turn off bluetooth" if was_on else "turn on bluetooth"
    wanted = not was_on

    try:
        report = perform(phone, jev, goal, max_steps=10)
        became = bluetooth_is_on(phone)
        show(goal, report, f"bluetooth_on {was_on} -> {became}, wanted {wanted}")

        assert became is wanted, (
            f"bluetooth was {was_on} and should now be {wanted}, but reads {became}"
        )
        # And the other direction: it must not report failure over a phone that
        # did the job. Two runs of this suite under-claimed exactly that way.
        assert report.achieved, (
            f"bluetooth reads {became} as asked, but the run reported failure: "
            f"{report.reason}"
        )
    finally:
        # Put the phone back the way it was found, whatever happened.
        phone.shell(f"settings put global bluetooth_on {1 if was_on else 0}")
        if was_on:
            phone.shell("svc bluetooth enable")
        else:
            phone.shell("svc bluetooth disable")


@pytest.mark.needs_the_real_phone  # the camera on this phone
@pytest.mark.device
def test_scenario_take_a_photo(phone, jev):
    """A new file on disk is the only proof a shutter was actually pressed."""
    go_home(phone)
    before = photo_files(phone)
    goal = "open the camera and take a photo"

    report = perform(phone, jev, goal, max_steps=10)
    time.sleep(3)  # the camera writes the file after the shutter
    after = photo_files(phone)
    taken = after - before
    show(goal, report, f"{len(before)} photos -> {len(after)}, new: {sorted(taken)}")

    assert taken, "no new photo appeared in the camera folder"


def test_scenario_delete_the_photo_taken(phone, jev):
    """Removing the same file is the proof; deleting some other photo is not."""
    before = photo_files(phone)
    newest = sorted(before)[-1] if before else None
    if newest is None:
        pytest.skip("there is no photo to delete")

    goal = "delete the most recent photo in the camera app"

    report = perform(phone, jev, goal, max_steps=14)
    after = photo_files(phone)
    gone = newest not in after
    show(goal, report, f"{newest} deleted={gone}")

    if not gone:
        # Leave the phone as it was found rather than leaving our photo behind.
        phone.shell(f"rm -f {PHOTO_DIRECTORY}/{newest}")

    assert gone, f"{newest} is still there"


# --- strict enough to fail ------------------------------------------------
#
# Two earlier versions of this check were worse than useless and both are worth
# recording, because each one passed a run that had not done the job.
#
# The first asked whether "youtube" and "mr beast" appeared in the screen
# listing. Both were true of an address-bar suggestion list on a run that never
# loaded a page.
#
# The second required the browser's address bar to hold the URL, which cannot
# happen here: Android hands a verified domain to the app that claims it, so
# youtube.com opens the YouTube app and no address bar is involved at all. It
# asserted a property the platform does not have.
#
# What separates a submitted search from a typed one was measured on the device
# rather than assumed, and the obvious guess was wrong: counting the rows on
# screen gives 9 submitted and 10 not submitted, which tells you nothing. What
# does separate them is where the query is. Typed-but-not-submitted leaves it
# sitting in an editable field; a submitted search takes it out of the field and
# puts it in the search bar, with the field gone.


def the_search_was_submitted(phone: AndroidPhone, query: str) -> tuple[bool, str]:
    """Whether the query has been sent, not merely typed.

    Returns the answer and what the screen showed, so a failure explains itself.
    """
    screen = read_screen(phone)
    words = [word for word in query.casefold().split() if word]
    in_a_field = " ".join(
        (control.text or "") for control in screen.controls if control.is_editable
    ).casefold()
    if all(word in in_a_field for word in words):
        return False, f"the query is still sitting unsent in a field: {in_a_field!r}"
    everywhere = " ".join(
        f"{control.label or ''} {control.text or ''}" for control in screen.controls
    ).casefold()
    missing = [word for word in words if word not in everywhere]
    if missing:
        return False, f"the query is nowhere on screen; missing {missing}"
    return True, "the query is on screen with nothing left holding it unsent"


@pytest.mark.needs_the_real_phone  # the YouTube app on this phone
@pytest.mark.device
def test_scenario_youtube_search_is_strictly_verified(phone, jev):
    """Open youtube.com and search for a video, and check the search was sent."""
    # A browser keeps whatever was typed into it last, so a stale query from an
    # earlier run could otherwise be read as this run's work.
    phone.run(["shell", "am", "force-stop", "com.android.chrome"])
    phone.run(["shell", "am", "force-stop", "com.google.android.youtube"])
    time.sleep(1)
    go_home(phone)
    goal = "open chrome and go to youtube.com and search for mr beast"

    report = perform(phone, jev, goal, max_steps=12)
    foreground = read_device_state(phone).foreground_package
    submitted, because = the_search_was_submitted(phone, "mr beast")
    shows_youtube = "youtube" in foreground

    show(goal, report, f"on-youtube={shows_youtube} searched={submitted}")
    print(f"      foreground: {foreground}")
    print(f"      search    : {because}")

    assert shows_youtube, (
        f"nothing showing YouTube is in front; the foreground is {foreground!r}"
    )
    assert submitted, (
        f"the search was never sent - {because} (foreground {foreground!r})"
    )
    # And the other direction: a phone that did the job must not be reported as
    # a failure. This run reached a submitted search once and still said "not
    # achieved", because the loop judged completion from the screen alone.
    assert report.achieved, (
        f"the search is done and {foreground!r} is in front, but the run reported "
        f"failure: {report.reason}"
    )
