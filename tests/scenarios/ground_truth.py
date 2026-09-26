"""Reading the truth about the phone, without asking the thing being tested.

Every scenario's verdict comes from here, never from the run's own report. The
distinction is the whole point: a run can say it succeeded, and the phone is the
only witness that cannot be talked into agreeing. Two failures of this suite came
from not keeping that line - one check was satisfied by an address-bar suggestion
list, and one run was reported as a failure over a photo that had genuinely been
deleted.

Every probe returns the answer *and* what it saw, so a failure explains itself
instead of leaving the next reader to guess which of six assertions failed.

A probe is also asked after every single step, not only at the end. That is what
makes a stage-by-stage claim possible: a round trip such as "turn Bluetooth off
and then back on" ends where it started, so no end-of-run reading can tell it
apart from having done nothing at all. Watching each stage can.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from typing import Any, Protocol

from phone_control.adb import AndroidPhone
from phone_control.device_state import read_focused_window
from phone_control.goal import a_screen_whose_tree_cannot_be_read
from phone_control.screen import Screen, read_screen

HOME_KEYCODE = "3"
WAKE_KEYCODE = "224"
SETTLE_SECONDS = 1.5

# What a scenario read from the phone before the run began, so a probe can say
# what changed. A count of photos, the name of the newest one, and so on.
Captured = dict[str, Any]


@dataclass(frozen=True)
class Verdict:
    """Whether the phone is in the state a scenario asked for, and why."""

    met: bool
    because: str

    def __bool__(self) -> bool:
        return self.met


class Probe(Protocol):
    """Reads one fact from the phone, optionally against what it read before."""

    def __call__(self, phone: AndroidPhone, before: Captured) -> Verdict: ...


def settle(seconds: float = SETTLE_SECONDS) -> None:
    time.sleep(seconds)


def the_screen(phone: AndroidPhone, attempts: int = 5):
    """The screen, waiting for it if it is mid-animation.

    `uiautomator dump` returns no hierarchy while a screen is changing, and a probe
    that raises on that turns a phone doing the right thing into a crashed test run.
    A probe can afford to wait: it is not the thing being timed.
    """
    from phone_control.errors import PhoneCommandFailed

    failed: Exception | None = None
    for attempt in range(attempts):
        try:
            return read_screen(phone)
        except PhoneCommandFailed as error:
            failed = error
            # The phone says which of the two failures this is, and they want
            # opposite things. "Could not get idle state" means this page will never
            # be captured, however long we wait - Android will not dump a screen that
            # keeps moving, and some Settings pages keep moving for as long as they
            # are open. Waiting the full five attempts on one of those costs eight
            # seconds per reading for nothing, and the harness takes a reading per
            # stage, so it turns a fast scenario into a timed-out one. Anything else
            # is a screen mid-animation, which is worth waiting for.
            if "idle state" in str(error):
                break
            if attempt + 1 < attempts:
                time.sleep(2.0)

    # The tree will not describe this page at all. Some pages are like that for as
    # long as they are open - Android refuses to capture a screen that never goes
    # idle - and the loop reads exactly those from a picture. The ground truth has
    # to look at the phone the same way, or it is blind precisely where the loop can
    # see: two scenarios (settings-search and find-android-version) crashed the whole
    # harness here, over pages that were plainly on the screen, which made them
    # unjudgeable rather than failed and hid them from the tally.
    by_picture = a_screen_whose_tree_cannot_be_read(phone, an_empty_screen())
    if by_picture.lines_read_from_a_picture:
        return by_picture

    # Raised outside the handler, so it has to be named: a bare `raise` there has no
    # exception to re-raise and fails with "no active exception to reraise".
    #
    # The phone's own words are carried along. "The screen could not be read" without
    # them is what made this take three attempts to chase: the dump command says
    # something every time, and it is the only thing that distinguishes a sleeping
    # phone from a screen that is genuinely absent.
    said = _what_the_dump_said(phone)
    raise RuntimeError(
        f"the screen could not be read after {attempts} attempts, and a picture of "
        f"it gave up no text either. {failed} The dump command said: {said!r}"
    ) from failed


def an_empty_screen() -> Screen:
    """A screen with nothing known about it, for a reading that starts from scratch."""
    return Screen(controls=(), width=0, height=0, package="")


def _what_the_dump_said(phone: AndroidPhone) -> str:
    """Whatever `uiautomator dump` prints, for a failure that explains itself."""
    try:
        return phone.shell(
            "uiautomator dump /sdcard/probe_for_a_failure.xml 2>&1"
        ).strip()
    except Exception as error:  # pragma: no cover - diagnostics only
        return f"(the dump command itself failed: {error})"


# --- what is in front -----------------------------------------------------


def in_front(fragment: str) -> Probe:
    """The focused window belongs to the app whose package contains ``fragment``."""

    def probe(phone: AndroidPhone, before: Captured) -> Verdict:
        package = read_focused_window(phone).package or ""
        return Verdict(
            fragment.casefold() in package.casefold(),
            f"the app in front is {package or 'nothing'}",
        )

    return probe


# --- what the screen says -------------------------------------------------


def screen_shows(*phrases: str, anywhere: bool = True) -> Probe:
    """Every phrase appears on the screen, ignoring case.

    ``anywhere`` False requires the words to be inside a single control's own
    label, which is stricter where a phrase is short enough to be assembled from
    unrelated parts of the screen by accident.
    """

    def probe(phone: AndroidPhone, before: Captured) -> Verdict:
        screen = the_screen(phone)
        text = screen.as_text().casefold()
        if not anywhere:
            text = " ".join(
                f"{control.label or ''} {control.text or ''}"
                for control in screen.controls
            ).casefold()
        missing = [phrase for phrase in phrases if phrase.casefold() not in text]
        return Verdict(
            not missing,
            (
                f"all of {list(phrases)} are on screen"
                if not missing
                else f"{missing} missing from {len(screen.controls)} controls"
            ),
        )

    return probe


def screen_does_not_show(*phrases: str) -> Probe:
    """None of these phrases appear. For ruling out a wrong or half-done state."""

    def probe(phone: AndroidPhone, before: Captured) -> Verdict:
        text = the_screen(phone).as_text().casefold()
        found = [phrase for phrase in phrases if phrase.casefold() in text]
        return Verdict(
            not found,
            f"{found} should not be on screen" if found else "none of them are present",
        )

    return probe


# What the address bar holds in the three states that matter. Chrome's own home holds
# a prompt, a results page holds a search address with the query in it, and a page the
# search led to holds that page's address. Only the third is the goal.
A_SEARCH_ADDRESS = re.compile(
    r"(?:^|//|\b)(?:www\.)?(?:google|bing|duckduckgo|yahoo|baidu)\.[a-z.]+/"
    r"(?:search|s\?|s/)|[?&]q=|/url\?",
    re.IGNORECASE,
)
A_PROMPT = re.compile(
    r"^(?:search|search or type|type a url|enter a search|search or type url)",
    re.IGNORECASE,
)
A_HOSTNAME = re.compile(r"^[a-z0-9][a-z0-9.-]*\.[a-z]{2,}(?:[/:?#].*)?$", re.IGNORECASE)


def is_a_page_address(held: str) -> bool:
    """Whether what the address bar holds is the address of a page.

    False for the two states that are not the goal, and the two are false for
    different reasons: a prompt has spaces and no hostname, a search address has a
    hostname but is a search. Written as a plain function because this is the whole
    decision and it can be argued about with strings rather than a phone.
    """
    said = held.strip()
    if not said or " " in said:
        return False
    if A_PROMPT.match(said):
        return False
    # A search address is a page address by shape, so the search has to be ruled out
    # before the hostname test rather than after it - and by *host*, not by the word
    # "search" anywhere in the path. A result can be a site's own search page:
    # pexels.com/search/pixel%20wallpaper/ is a page the search led to, and treating
    # every "/search" as a search engine rejected it.
    if A_SEARCH_ADDRESS.search(said):
        return False
    return bool(A_HOSTNAME.match(re.sub(r"^https?://", "", said, flags=re.IGNORECASE)))


def the_address_bar_holds_a_page() -> Probe:
    """The browser is showing a page, and not the search that led to it.

    The probe this replaces asked only that the address bar *not* hold the query, and
    that is true of Chrome's own home screen as well - so it reported the goal met
    from the first step of a run that had not searched anything, five times in one
    catalogue run, and the decider answered 0.02 on every one of those screens. A
    probe for something being absent is a probe that cannot tell "left the results"
    from "never got there".
    """

    def probe(phone: AndroidPhone, before: Captured) -> Verdict:
        held = [
            (control.text or "").strip()
            for control in the_screen(phone).controls
            if control.is_editable
        ]
        showing = next((one for one in held if is_a_page_address(one)), "")
        return Verdict(
            bool(showing),
            (
                f"the address bar holds {showing[:50]!r}, which is a page"
                if showing
                else f"the address bar holds no page address: {held}"
            ),
        )

    return probe


def the_picture_says(*phrases: str) -> Probe:
    """A photograph of the screen says all of these.

    The ground truth for a screen the accessibility tree will not describe. Measured on
    the About phone page, which no dump can read and whose window is the generic
    `SubSettings` when it is reached by tapping: a picture of it reads "About phone",
    the device name and the account, which is exactly what the page is.

    It costs a screenshot and a recognition pass, so a probe built on it is the slowest
    kind - worth it only where nothing cheaper can tell.
    """
    from phone_control.reading_a_picture import read_the_picture

    def probe(phone: AndroidPhone, before: Captured) -> Verdict:
        lines = " / ".join(read_the_picture(phone)).casefold()
        missing = [phrase for phrase in phrases if phrase.casefold() not in lines]
        return Verdict(
            not missing,
            (
                f"a picture of the screen says {list(phrases)}"
                if not missing
                else f"a picture of the screen does not say {missing}"
            ),
        )

    return probe


def a_control_is_named(exactly: str) -> Probe:
    """A control's own label is this and nothing else.

    Stronger than a word appearing somewhere on the screen, which is how a probe
    accepted "download" on a page showing Recents. Use it where the word in question
    is common enough to appear on screens that are not the goal.
    """

    def probe(phone: AndroidPhone, before: Captured) -> Verdict:
        wanted = exactly.casefold()
        for control in the_screen(phone).controls:
            if (control.label or "").strip().casefold() == wanted:
                return Verdict(True, f"a control is named {control.label!r}")
        return Verdict(False, f"no control is named exactly {exactly!r}")

    return probe


def a_control_reads_like_a_percentage() -> Probe:
    """Some control says a percentage, rather than a percent sign being anywhere.

    The battery page is the goal, and a "%" appears in storage figures, in build
    strings and in a dozen other places on the same app.
    """

    def probe(phone: AndroidPhone, before: Captured) -> Verdict:
        for control in the_screen(phone).controls:
            said = f"{control.label or ''} {control.text or ''}".strip()
            if "%" in said and any(character.isdigit() for character in said):
                return Verdict(True, f"a control reads {said[:40]!r}")
        return Verdict(False, "nothing on screen reads as a percentage")

    return probe


def screen_holds_nothing_unsent() -> Probe:
    """No editable field anywhere holds anything.

    The evidence that a search, a form or a message was *sent* rather than typed.
    What was typed is the app's business: a suggestion list, a spelling correction or
    a normalised query all count as the search having happened.
    """

    def probe(phone: AndroidPhone, before: Captured) -> Verdict:
        fields = [
            (control.text or "").strip()
            for control in the_screen(phone).controls
            if control.is_editable
        ]
        held = [text for text in fields if text]
        return Verdict(
            not held,
            (
                "nothing is sitting unsent in a field"
                if not held
                else f"a field still holds {held}"
            ),
        )

    return probe


def the_window_is(activity_fragment: str) -> Probe:
    """The window in front names this activity.

    The route for a screen the accessibility tree cannot describe at all. Some
    Settings pages never go idle, so `uiautomator dump` refuses them for ever -
    measured, and written up in docs/android-apis.md - while `dumpsys window`, which
    needs no idle, names the page perfectly.
    """

    def probe(phone: AndroidPhone, before: Captured) -> Verdict:
        window = read_focused_window(phone).window or ""
        return Verdict(
            activity_fragment.casefold() in window.casefold(),
            f"the window in front is {window or 'nothing'}",
        )

    return probe


def a_control_is_selected(label_fragment: str) -> Probe:
    """A control whose label contains this text is marked selected.

    Being on screen proves nothing for a tab: the Clock app keeps the words
    "World clock" in the tree whichever tab is open, so only the selected flag
    says which one is really showing. Measured on a real device.
    """

    def probe(phone: AndroidPhone, before: Captured) -> Verdict:
        wanted = label_fragment.casefold()
        for control in the_screen(phone).controls:
            if wanted in (control.label or "").casefold() and control.is_selected:
                return Verdict(True, f"{control.label!r} is the selected one")
        return Verdict(False, f"nothing matching {label_fragment!r} is selected")

    return probe


def a_field_holds(text: str) -> Probe:
    """Some editable field on screen contains this text."""

    def probe(phone: AndroidPhone, before: Captured) -> Verdict:
        wanted = text.casefold()
        for control in the_screen(phone).controls:
            if control.is_editable and wanted in (control.text or "").casefold():
                return Verdict(True, f"a field holds {control.text!r}")
        return Verdict(False, f"no editable field holds {text!r}")

    return probe


def no_field_holds(text: str) -> Probe:
    """The text is on screen but no longer sitting unsent in a field.

    This is what separates a submitted search from a typed one, and it was
    measured rather than guessed: counting the rows on screen gives nine
    submitted and ten not submitted, which tells you nothing at all.
    """

    def probe(phone: AndroidPhone, before: Captured) -> Verdict:
        screen = the_screen(phone)
        wanted = text.casefold()
        in_a_field = " ".join(
            (control.text or "") for control in screen.controls if control.is_editable
        ).casefold()
        if wanted in in_a_field:
            return Verdict(False, f"{text!r} is still sitting unsent in a field")
        everywhere = screen.as_text().casefold()
        return Verdict(
            wanted in everywhere,
            f"{text!r} is {'on screen with nothing holding it' if wanted in everywhere else 'nowhere on screen'}",
        )

    return probe


# --- what the device itself reports ---------------------------------------


def a_global_setting_is(name: str, value: str) -> Probe:
    """An Android global setting reads exactly this."""

    def probe(phone: AndroidPhone, before: Captured) -> Verdict:
        read = phone.shell(f"settings get global {name}").strip()
        return Verdict(read == value, f"settings get global {name} reads {read!r}")

    return probe


def names_in(phone: AndroidPhone, directory: str) -> set[str]:
    """The names in a device directory, read by a capture or a probe."""
    listing = phone.shell(f"ls {directory}")
    return {
        line.strip()
        for line in listing.splitlines()
        if line.strip() and "No such file" not in line
    }


def a_new_file_appeared(directory: str) -> Probe:
    """The directory holds more files than it did before the run.

    The only proof that a shutter was actually pressed. A camera screen showing
    what looks like a photo is not evidence that one was written.
    """

    def probe(phone: AndroidPhone, before: Captured) -> Verdict:
        was = before.get("files", set())
        now = names_in(phone, directory)
        return Verdict(
            len(now) > len(was),
            f"{directory} held {len(was)} files and now holds {len(now)}",
        )

    return probe


def the_files_are_back_to(directory: str) -> Probe:
    """The directory holds exactly what it held before the run."""

    def probe(phone: AndroidPhone, before: Captured) -> Verdict:
        was = before.get("files", set())
        now = names_in(phone, directory)
        appeared = sorted(now - was)
        vanished = sorted(was - now)
        return Verdict(
            now == was,
            (
                f"{directory} is as it was"
                if now == was
                else f"appeared {appeared}, vanished {vanished}"
            ),
        )

    return probe


# --- conditions combined --------------------------------------------------


def all_of(*probes: Probe) -> Probe:
    """Every probe has to hold; the first that does not names itself."""

    def probe(phone: AndroidPhone, before: Captured) -> Verdict:
        seen = []
        for one in probes:
            verdict = one(phone, before)
            if not verdict.met:
                return verdict
            seen.append(verdict.because)
        return Verdict(True, "; ".join(seen))

    return probe


def any_of(*probes: Probe) -> Probe:
    """At least one probe holds. For states reachable more than one way."""

    def probe(phone: AndroidPhone, before: Captured) -> Verdict:
        reasons = []
        for one in probes:
            verdict = one(phone, before)
            if verdict.met:
                return Verdict(True, verdict.because)
            reasons.append(verdict.because)
        return Verdict(False, "; ".join(reasons))

    return probe


def never() -> Probe:
    """A goal no phone can reach, so no reading of the phone can show it done.

    Used by the scenarios that ask for the impossible. Their verdict is not that
    the phone reached the goal, which it cannot, but that the run said so.
    """

    def probe(phone: AndroidPhone, before: Captured) -> Verdict:
        return Verdict(False, "this goal is not something a phone can do")

    return probe
