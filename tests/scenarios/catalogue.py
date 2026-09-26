"""What people actually ask a phone to do, from trivial to unreasonable.

The tiers are about how much has to go right, not how impressive the goal sounds:

    tier 1  one launch, or one press of a key
    tier 2  get to a screen that is inside an app
    tier 3  change something, or put text somewhere, and confirm it changed
    tier 4  several steps in order, often across two apps
    tier 5  long journeys, order that matters, and goals that cannot be met

Two kinds of scenario exist only because of failures already seen.

A goal that **cannot be met** is here on purpose. A loop that never reports
failure cannot be trusted when it reports success, so "order a pizza" checks that
an impossible goal comes back unmet rather than as a confident invention.

A **round trip** ends where it started - Bluetooth off and then on again, or a
photo taken and then deleted - so no reading taken at the end can tell it apart
from having done nothing. Those carry ``also_seen``: states that have to be
observed at some stage during the run, and the harness samples the phone after
every single step to check them.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from phone_control.adb import AndroidPhone

from . import starting_states as start
from .ground_truth import (
    Captured,
    Probe,
    a_control_is_named,
    a_control_is_selected,
    a_control_reads_like_a_percentage,
    a_field_holds,
    a_global_setting_is,
    a_new_file_appeared,
    all_of,
    any_of,
    in_front,
    names_in,
    never,
    screen_does_not_show,
    screen_holds_nothing_unsent,
    screen_shows,
    the_address_bar_holds_a_page,
    the_files_are_back_to,
    the_picture_says,
    the_window_is,
)


def capture_nothing(phone: AndroidPhone) -> Captured:
    """Most scenarios change nothing a probe needs to compare against."""
    return {}


def capture_the_photos(phone: AndroidPhone) -> Captured:
    """The camera folder's contents, so a run can be judged on the files."""
    return {"files": names_in(phone, start.PHOTO_DIRECTORY)}


@dataclass(frozen=True)
class Required:
    """A state that has to be observed at some stage, named so a failure reads.

    A round trip ends where it started, so nothing read at the end can tell it
    apart from having done nothing. What can is watching the stages.
    """

    what: str
    probe: Probe


@dataclass(frozen=True)
class Scenario:
    """Something to ask for, where to start, and how to tell it happened.

    ``looks_done`` is read at the end. ``also_seen`` are states that must turn up
    at some point during the run, which is the only way to verify a journey that
    finishes where it began.
    """

    id: str
    tier: int
    goal: str
    looks_done: Probe
    prepare: Callable[[AndroidPhone], None] = start.a_clean_launcher
    capture: Callable[[AndroidPhone], Captured] = capture_nothing
    also_seen: tuple[Required, ...] = field(default_factory=tuple)
    note: str = ""
    max_steps: int = 10
    # False for goals no phone can reach. Their verdict is that the run said so.
    reachable: bool = True
    # True when the goal changes something a person would mind losing.
    consequential: bool = False
    # True when the goal can only be concluded from something the accessibility tree
    # does not publish, so the run needs the hand-off's reader to say whether it is
    # met. Skipped rather than failed when no reader is configured: the phone does
    # what was asked, and the loop has no way to know it.
    needs_a_reader: bool = False
    # True when the goal's effect leaves no trace on the screen at all, so *nothing*
    # that reads the screen can settle it - not the loop, and not a reader either. A
    # photo appearing in a folder is the case: measured, the shutter takes 6.2 seconds
    # and the tree is identical before and after it, because a camera preview is a
    # surface and there is nothing in the tree to change. Such a scenario is judged on
    # the one direction that is knowable - it must not claim a success it did not have
    # - and reported as not judged rather than as a failure of the loop.
    the_effect_is_off_the_screen: bool = False


def only_when_installed(phone: AndroidPhone) -> set[str]:
    """The packages this phone has, for scenarios that need a particular app."""
    listing = phone.shell("pm list packages")
    return {
        line.removeprefix("package:").strip()
        for line in listing.splitlines()
        if line.startswith("package:")
    }


# --- tier 1: one launch, or one key --------------------------------------

TIER_ONE = (
    Scenario(
        id="open-settings",
        tier=1,
        goal="open the settings app",
        looks_done=in_front(start.SETTINGS),
        note="the baseline: one launch and one arrival",
    ),
    Scenario(
        id="open-calculator",
        tier=1,
        goal="open the calculator app",
        looks_done=in_front(start.CALCULATOR),
        note="a short spoken name for a long package",
    ),
    Scenario(
        id="open-clock",
        tier=1,
        goal="open the clock app",
        looks_done=in_front(start.CLOCK),
        note="the spoken name is not the package name",
    ),
    Scenario(
        id="open-camera",
        tier=1,
        goal="open the camera app",
        looks_done=in_front(start.CAMERA),
        note="a visual app with an icon-only toolbar",
    ),
    Scenario(
        id="open-play-store",
        tier=1,
        goal="open the play store",
        looks_done=in_front(start.PLAY_STORE),
        note="the spoken name shares no word with its package",
    ),
    Scenario(
        id="open-keep",
        tier=1,
        goal="open the google keep app",
        looks_done=in_front(start.KEEP),
        note="an app that may not be on the first launcher page",
    ),
    Scenario(
        id="go-home",
        tier=1,
        goal="go back to the home screen",
        looks_done=in_front("nexuslauncher"),
        prepare=start.the_settings_root,
        note="leaving an app rather than entering one",
        max_steps=6,
    ),
    Scenario(
        id="leave-the-camera",
        tier=1,
        goal="leave the camera and go to the home screen",
        looks_done=in_front("nexuslauncher"),
        prepare=start.the_camera,
        note="the same, from an app that covers the whole screen",
        max_steps=6,
    ),
)


# --- tier 2: a screen that lives inside an app ---------------------------

TIER_TWO = (
    Scenario(
        id="settings-about-phone",
        tier=2,
        goal="open the settings app and show the about phone page",
        looks_done=all_of(
            in_front(start.SETTINGS),
            # Either route. Started directly the page names itself in the window;
            # reached by tapping it is the generic `SubSettings`, and only a picture of
            # it says which page this is.
            any_of(the_window_is("MyDeviceInfo"), the_picture_says("about phone")),
        ),
        note=(
            "one level into Settings, on a page no dump can read - so the loop reads a "
            "picture of it instead, which is the route that exists for exactly this. "
            "Read a green result here with one caveat: Android restores the Settings "
            "page that was last open, so when a previous run leaves About phone on "
            "screen this goal is met by opening the app and nothing else. That is a "
            "real pass - the phone really is showing that page - but it does not "
            "exercise the navigation, and the navigation is the hard part. Check "
            "run/steps.jsonl and see that the run tapped its way there"
        ),
    ),
    Scenario(
        id="settings-battery",
        tier=2,
        goal="open the battery settings",
        # Judged by the page's own title, measured rather than assumed. The window is
        # a generic `SubSettings` when the page is reached by tapping - which is how a
        # run gets there - so the window name was a probe that could not match. The
        # page dumps perfectly well, and its first control is exactly "Battery".
        #
        # The row that reaches it is *not* exactly "Battery": it is "Battery 100%",
        # because a Settings row's label carries its summary. That difference is what
        # makes an exact match a page-identity probe rather than a word that appears in
        # two places, and it is why the row can be scrolled past without satisfying it.
        looks_done=all_of(in_front(start.SETTINGS), a_control_is_named("Battery")),
        note=(
            "a named Settings page, identified by its title. Reached by tapping, the "
            "window is the generic SubSettings; the page's first control is 'Battery' "
            "and the row that opens it is 'Battery 100%', so an exact match tells them "
            "apart. The row is below the fold, so the run has to scroll to find it"
        ),
    ),
    Scenario(
        id="settings-network",
        tier=2,
        goal="open the network and internet settings",
        # Either route. Started directly the page names itself in the window; reached
        # by tapping it is the generic `SubSettings`, and only a picture of it says
        # which page this is.
        #
        # The phrase is one that appears *only* on the page. The page's own title does
        # not do: "Network & internet" is also the name of the row you tap to get
        # here, so a run that never left the top level would satisfy it. Measured, by
        # reading a picture of the top level and of the page and comparing the two:
        # "Hotspot & tethering", "Private DNS" and "Data Saver" are on the page and
        # nowhere on the list.
        looks_done=all_of(
            in_front(start.SETTINGS),
            any_of(
                the_window_is("NetworkDashboard"),
                the_picture_says("Hotspot & tethering"),
            ),
        ),
        needs_a_reader=True,
        note="a two-word Settings page, judged by the window or by a picture of it",
    ),
    Scenario(
        id="settings-apps-list",
        tier=2,
        goal="open the list of installed apps in settings",
        # Judged the same way, and for the same reason: reached by tapping, this page
        # is hosted generically. "Recently opened apps" is measured as being on the
        # page and nowhere on the Settings list, where the row is just "Apps" - which
        # would otherwise let a run that never left the list pass.
        # Three routes, and all three are the thing that was asked for. "The list of
        # installed apps" is the Apps page, and it is also the full list one tap
        # further on - measured, the run goes to the second one and it is right to:
        # the page is titled "All apps" and holds every app with its size. A probe
        # that insisted on the first page failed a run that had done more than it
        # asked, which is the mistake this file keeps having to be told about.
        looks_done=all_of(
            in_front(start.SETTINGS),
            any_of(
                the_window_is("AppsDashboard"),
                the_picture_says("Recently opened apps"),
                the_picture_says("All apps"),
            ),
        ),
        needs_a_reader=True,
        note="a page reached by a row, judged by the window",
    ),
    Scenario(
        id="clock-world-clock",
        tier=2,
        goal="open the clock app and show the world clock",
        looks_done=all_of(in_front(start.CLOCK), a_control_is_selected("world clock")),
        prepare=start.the_clock,
        note="a tab, where the words are on screen whichever tab is open",
    ),
    Scenario(
        id="clock-alarms",
        tier=2,
        goal="open the clock app and show the alarms",
        looks_done=all_of(in_front(start.CLOCK), a_control_is_selected("alarm")),
        prepare=start.the_clock,
        note="the same trap as the world clock, on another tab",
    ),
    Scenario(
        id="open-notifications",
        tier=2,
        goal="open the notification shade",
        looks_done=any_of(
            in_front("systemui"),
            screen_shows("clear all"),
            screen_shows("no notifications"),
        ),
        note="a system surface rather than an app",
        max_steps=6,
    ),
    Scenario(
        id="settings-storage",
        tier=2,
        goal="open the storage settings",
        # The same measurement: the page's first control is exactly "Storage" and the
        # row that opens it is "Storage 22% used - 99.61 GB free". Its live figure was
        # the reason given for judging by the window, and the dump does not care - the
        # page reads fine with the figure on it.
        looks_done=all_of(in_front(start.SETTINGS), a_control_is_named("Storage")),
        note=(
            "a page that shows a live figure. Judged by its title rather than by the "
            "window: the title is exactly 'Storage' and the row that opens it carries "
            "its summary after it, so the two do not collide"
        ),
    ),
    Scenario(
        id="photos-app",
        tier=2,
        goal="open the photos app",
        looks_done=in_front("photos"),
        note="a second app whose spoken name is short",
    ),
    Scenario(
        id="maps-app",
        tier=2,
        goal="open the maps app",
        looks_done=in_front("maps"),
        note="an app that is slow to settle",
        max_steps=8,
    ),
)


# --- tier 3: change something, or type something ------------------------

TIER_THREE = (
    Scenario(
        id="bluetooth-off",
        tier=3,
        goal="turn off bluetooth",
        looks_done=a_global_setting_is("bluetooth_on", "0"),
        prepare=lambda phone: (start.bluetooth(phone, True), start.go_home(phone)),
        needs_a_reader=True,
        note=(
            "a switch, confirmed from the device rather than from the screen - and "
            "the screen never says which way it is, so the run cannot conclude it is "
            "done without a reader (docs/android-apis.md)"
        ),
    ),
    Scenario(
        id="bluetooth-on",
        tier=3,
        goal="turn on bluetooth",
        looks_done=a_global_setting_is("bluetooth_on", "1"),
        needs_a_reader=True,
        prepare=lambda phone: (start.bluetooth(phone, False), start.go_home(phone)),
        note="the same switch the other way",
    ),
    Scenario(
        id="wi-fi-off",
        tier=3,
        goal="turn off wi-fi",
        looks_done=a_global_setting_is("wifi_on", "0"),
        needs_a_reader=True,
        prepare=lambda phone: (start.wi_fi(phone, True), start.go_home(phone)),
        note="a switch whose label has a hyphen and capitals",
    ),
    Scenario(
        id="wi-fi-on",
        tier=3,
        goal="turn wi-fi back on",
        looks_done=a_global_setting_is("wifi_on", "1"),
        needs_a_reader=True,
        prepare=lambda phone: (start.wi_fi(phone, False), start.go_home(phone)),
        note="off to on, which reads differently on screen",
    ),
    Scenario(
        id="aeroplane-mode-on",
        tier=3,
        goal="turn on aeroplane mode",
        looks_done=a_global_setting_is("airplane_mode_on", "1"),
        needs_a_reader=True,
        prepare=lambda phone: (
            start.aeroplane_mode(phone, False),
            start.the_settings_root(phone),
        ),
        note="a switch that turns other switches off with it",
    ),
    Scenario(
        id="aeroplane-mode-off",
        tier=3,
        goal="turn aeroplane mode off",
        looks_done=a_global_setting_is("airplane_mode_on", "0"),
        needs_a_reader=True,
        prepare=lambda phone: (
            start.aeroplane_mode(phone, True),
            start.the_settings_root(phone),
        ),
        note="and back again",
    ),
    Scenario(
        id="browser-example",
        tier=3,
        goal="open chrome and go to example.com",
        looks_done=all_of(
            in_front(start.CHROME), screen_shows("example", anywhere=False)
        ),
        prepare=start.a_fresh_browser,
        max_steps=8,
        note="a URL put into an address bar",
    ),
    Scenario(
        id="youtube-search",
        tier=3,
        goal="open chrome and go to youtube.com and search for mr beast",
        looks_done=all_of(
            in_front("youtube"),
            # The query has left the box, which is what a submitted search means. It
            # is not required to still read "mr beast": the app offers its own
            # spelling as a suggestion and searching that is a search, so demanding
            # the exact words is a second and stricter claim that the phone need not
            # satisfy. Measured: the run typed "mr beast", took the suggestion
            # "mrbeast", and the probe called a completed search unfinished.
            screen_holds_nothing_unsent(),
            screen_does_not_show("allow youtube to"),
        ),
        prepare=start.a_fresh_browser,
        max_steps=12,
        note=(
            "text typed and then submitted, which are different states - except that "
            "`screen_holds_nothing_unsent` cannot tell them apart here. Measured: "
            "YouTube's home screen has an empty search box, so nothing is sitting "
            "unsent on it either, and the probe is satisfied before anything is typed. "
            "It is the third of the three absent-style probes found in round 32, and "
            "unlike the other two it has no replacement yet: the window does not "
            "distinguish them either (see youtube-open-a-result)"
        ),
    ),
    Scenario(
        id="camera-take-a-photo",
        tier=3,
        goal="open the camera and take a photo",
        looks_done=a_new_file_appeared(start.PHOTO_DIRECTORY),
        prepare=start.the_camera,
        capture=capture_the_photos,
        the_effect_is_off_the_screen=True,
        max_steps=8,
        note=(
            "a shutter in an icon-only app, proved by a file appearing - and the file "
            "is the only place it appears: the tree is identical before and after "
            "(docs/android-apis.md)"
        ),
        consequential=True,
    ),
    Scenario(
        id="settings-search",
        tier=3,
        goal="use the search in settings to find battery",
        looks_done=all_of(in_front(start.SETTINGS), screen_shows("battery")),
        prepare=start.the_settings_root,
        max_steps=10,
        needs_a_reader=True,
        note=(
            "typing into a search field rather than finding a row, on a page whose "
            "results Android will not dump (docs/android-apis.md)"
        ),
    ),
    Scenario(
        id="calculator-arithmetic",
        tier=3,
        goal="open the calculator and work out 12 times 12",
        looks_done=all_of(in_front(start.CALCULATOR), screen_shows("144")),
        # The calculator keeps the sum it was in the middle of, so a run that starts
        # with it open begins from the last run's arithmetic. Measured: this scenario
        # began at "12x" and the second run of the day computed "12x12" from a
        # different starting point than the first.
        prepare=lambda phone: (
            start.stop(phone, start.CALCULATOR),
            start.a_clean_launcher(phone),
        ),
        max_steps=12,
        note="several presses in order, with a result that is not on screen yet",
    ),
    Scenario(
        id="find-android-version",
        tier=3,
        goal="find which android version this phone is running",
        looks_done=all_of(in_front(start.SETTINGS), screen_shows("android version")),
        prepare=start.the_settings_root,
        needs_a_reader=True,
        max_steps=12,
        note=(
            "a page that has to be found rather than typed at, and one Android will "
            "not dump - the About phone family (docs/android-apis.md)"
        ),
    ),
    Scenario(
        id="alarm-for-seven",
        tier=3,
        goal="set an alarm for 7:00 in the morning",
        looks_done=all_of(in_front(start.CLOCK), screen_shows("7:00")),
        prepare=start.the_clock,
        needs_a_reader=True,
        max_steps=14,
        note=(
            "a time entered into a picker, on a screen whose reads failed outright - "
            "'could not get idle state', the About phone family again"
        ),
        consequential=True,
    ),
)


# --- tier 4: several steps in order, often across two apps --------------

TIER_FOUR = (
    Scenario(
        id="scroll-settings-to-the-bottom",
        tier=4,
        goal="scroll to the bottom of the settings app",
        looks_done=all_of(in_front(start.SETTINGS), screen_shows("about phone")),
        prepare=start.the_settings_root,
        max_steps=14,
        note="scrolling until something appears that was not there before",
    ),
    Scenario(
        id="youtube-open-a-result",
        tier=4,
        goal="open youtube and search for mr beast and open the first video",
        # The player, not a word that happens to be on the page. "subscribe",
        # "views" and "shorts" all appear on YouTube's *home* screen, so a probe
        # built from them reported success for a run that had searched and never
        # opened anything - measured: the window was Shell$HomeActivity with
        # "mrbeast" still sitting in the search box.
        # **This probe cannot match on this device, measured in round 35.** YouTube
        # reports `Shell$HomeActivity` on its home screen, while a query is being typed,
        # and after the query has been submitted - all three identical. So a window
        # called SearchResults never comes to the front, and this scenario cannot pass
        # however well the run does. Whatever replaces it has to be a positive signal
        # that a search happened, and the obvious candidates are gone: "views",
        # "subscribe" and "shorts" are all on YouTube's home screen too (which is the
        # mistake recorded above), and the search box reads empty on both screens.
        looks_done=the_window_is("SearchResults"),
        prepare=start.a_fresh_youtube,
        needs_a_reader=True,
        max_steps=16,
        note=(
            "search, then choose something the search returned. Nothing readable "
            "settles it: a playing video cannot be dumped at all - it never goes "
            "idle - and the window is InternalMainActivity for YouTube's home and for "
            "a video alike, so the one signal that looks like it works matches both. "
            "The goal is visible on the screen and not readable from it, which is what "
            "a reader is for (docs/android-apis.md)"
        ),
    ),
    Scenario(
        id="browser-search-then-open",
        tier=4,
        goal="open chrome and search for pixel phone wallpaper and open the first result",
        # The address bar must hold a *page*, not merely fail to hold the query. That
        # negative was true of Chrome's own home screen, so this probe reported the goal
        # met from the first step of a run that had not searched - five times in one
        # catalogue run, with the decider answering 0.02 on every one of them.
        looks_done=all_of(in_front(start.CHROME), the_address_bar_holds_a_page()),
        prepare=start.a_fresh_browser,
        max_steps=16,
        note="type, submit, then pick from what came back",
    ),
    Scenario(
        id="keep-a-note",
        tier=4,
        goal="open google keep and write a note saying hello",
        # `screen_shows("hello")` and this probe is too lax, which the harness said in
        # so many words - "CHECK THE PROBE FIRST" - on the round-20 run. Read out of
        # that run's decisions: the text went into Keep's *search* box, whose own
        # placeholder was still focused as `Search Keep`, and the words came back as
        # the echoed query and as result rows. The probe saw "hello" on the screen and
        # called it a note. The decider never did: it answered `completion` 0.12 and
        # 0.16 on those two screens and was right, so this is not the loop
        # under-claiming.
        #
        # The text it typed was wrong as well, and that is fixed: the goal carried
        # "a note saying hello" rather than "hello", because the cue "write" matched
        # before the cue "saying" - see `text_the_goal_carries`. So the note in Keep's
        # list is titled with the instruction.
        #
        # Tightening the probe is still open, and one measurement has ruled out the
        # obvious way. A written note appears in the list as a row whose label is a
        # concatenation - measured: "List note. a note saying hello. a note saying
        # hello, Not Checked." - so `a_control_is_named("hello")` would not match a
        # genuine note either, and would turn this into a scenario that can never pass.
        #
        # What is needed is a signal a search result cannot produce. Keep's rows carry
        # their kind as a prefix ("Text note." / "List note."), which a search result
        # echoing a query does not - but that was read off one screen with an account
        # already holding a note from an earlier run, and `pm clear` does NOT give a
        # clean slate here because Keep is signed in and restores its notes. Measure it
        # on an account with known contents before writing the probe.
        looks_done=all_of(in_front(start.KEEP), screen_shows("hello")),
        prepare=lambda phone: start.an_app(phone, start.KEEP),
        max_steps=14,
        note=(
            "find the field, type, and leave the text where it can be read. Its probe "
            "is too lax as written - see the comment above - and the four scenarios "
            "that failed the same way on the same run are browser-search-then-open, "
            "files-browse-downloads, wi-fi-off-then-on and bluetooth-off-then-on"
        ),
        consequential=True,
    ),
    Scenario(
        id="dialer-open-keypad",
        tier=4,
        goal="open the phone app and type a number into the dialler",
        looks_done=all_of(in_front("dialer"), a_field_holds("555")),
        prepare=lambda phone: start.an_app(phone, start.DIALER),
        max_steps=14,
        note="typing into a number pad, without placing a call",
    ),
    Scenario(
        id="timer-for-two-minutes",
        tier=4,
        goal="open the clock and set a timer for 2 minutes",
        looks_done=all_of(in_front(start.CLOCK), screen_shows("minute")),
        prepare=start.the_clock,
        max_steps=16,
        note="a different tab, and a duration rather than a time",
        consequential=True,
    ),
    Scenario(
        id="files-browse-downloads",
        tier=4,
        goal="open the files app and show the downloads",
        # Both halves are needed, and the reason is measured. A control *named*
        # Downloads, not the word appearing: the run this caught ended on the Files
        # home screen showing Recents and a camera photo, and "download" was on the
        # page all the same.
        #
        # But the name alone is not enough either - read off the round-26 run, a
        # control named exactly "Downloads" is on the Files *home* screen too, as a row
        # among Recents and See all, so this probe was satisfied before the run had
        # done anything at all. What tells the two apart is the window: the home screen
        # is `...nbu.files.home.HomeActivity` and the folder view is
        # `...documentbrowser.filebrowser.FileBrowserRegularActivity`. With the folder
        # name as the title and that activity in front, the page really is Downloads.
        looks_done=all_of(
            in_front("nbu.files"),
            the_window_is("FileBrowserRegularActivity"),
            a_control_is_named("Downloads"),
        ),
        prepare=lambda phone: start.an_app(phone, start.FILES),
        max_steps=12,
        note=(
            "an app whose spoken name is not a word in its package. The round-20 run "
            "reached the view and the decider under-claimed it: read out of the "
            "decisions, step 3 was the Downloads page - `other 'Downloads'` as the "
            'title, over `container "There\'s nothing here."` - and the decider put '
            "completion at 0.63. The next step was back on Recents, where it put 0.25, "
            "and it oscillated from there until the steps ran out. So the two screens "
            "do separate - 0.25 against 0.63 - and 0.63 is simply under the 0.90 the "
            "loop needs before it will call the goal done. An empty folder is a fair "
            "thing to be unsure about, and leaving the page it had just reached is "
            "what turned an uncertainty into a failed run. Worth knowing before "
            "touching either: the probe matched the page *title*, so it passed for the "
            "right answer by a route it was not written for"
        ),
    ),
    Scenario(
        id="recorder-show-the-recorder",
        tier=4,
        goal="open the recorder app",
        looks_done=in_front("recorder"),
        # The recorder is stopped and the phone is left on the launcher, so the run
        # has to open it. The prepare used to open it, which meant the goal was
        # already satisfied before the first step and the scenario could pass without
        # doing anything - and the harness did not say so, because its check for that
        # only ran on scenarios that also declare intermediate states. Both are fixed;
        # this one was measuring nothing at all.
        prepare=lambda phone: (start.stop(phone, start.RECORDER), start.go_home(phone)),
        max_steps=10,
        note="a single large button whose state is the whole interface",
    ),
)


# --- tier 5: long journeys, ordered steps, and the impossible -----------

TIER_FIVE = (
    Scenario(
        id="impossible-order-a-pizza",
        tier=5,
        goal="order a large pepperoni pizza to my house",
        looks_done=never(),
        reachable=False,
        max_steps=10,
        note="cannot be done, so the run has to say so rather than invent it",
    ),
    Scenario(
        id="impossible-book-a-flight",
        tier=5,
        goal="book me a flight to tokyo next tuesday",
        looks_done=never(),
        reachable=False,
        max_steps=10,
        note="the same, for a goal that sounds more plausible",
    ),
    Scenario(
        id="bluetooth-off-then-on",
        tier=5,
        goal="turn bluetooth off and then turn it back on again",
        looks_done=a_global_setting_is("bluetooth_on", "1"),
        also_seen=(
            Required(
                "bluetooth actually off", a_global_setting_is("bluetooth_on", "0")
            ),
        ),
        prepare=lambda phone: (start.bluetooth(phone, True), start.go_home(phone)),
        max_steps=16,
        note="two changes to one switch, ending where it started",
    ),
    Scenario(
        id="photo-then-delete-it",
        tier=5,
        goal="take a photo and then delete it again",
        the_effect_is_off_the_screen=True,
        looks_done=the_files_are_back_to(start.PHOTO_DIRECTORY),
        also_seen=(
            Required(
                "a new photo in the camera folder",
                a_new_file_appeared(start.PHOTO_DIRECTORY),
            ),
        ),
        prepare=start.the_camera,
        capture=capture_the_photos,
        max_steps=18,
        note="out to the camera and back, with the folder counted at each stage",
        consequential=True,
    ),
    Scenario(
        id="settings-clock-calculator",
        tier=5,
        goal="open settings then the clock then the calculator",
        looks_done=in_front(start.CALCULATOR),
        also_seen=(
            Required("settings was reached", in_front(start.SETTINGS)),
            Required("the clock was reached", in_front(start.CLOCK)),
        ),
        max_steps=16,
        note="three destinations in one run, each one a stage to verify",
    ),
    Scenario(
        id="read-the-battery-level",
        tier=5,
        goal="open the battery settings and show how much charge is left",
        # A control that *reads* as a percentage, not a percent sign anywhere.
        looks_done=all_of(
            in_front(start.SETTINGS), a_control_reads_like_a_percentage()
        ),
        prepare=start.the_settings_root,
        max_steps=16,
        note="a figure that only exists on one screen",
    ),
    Scenario(
        id="wi-fi-off-then-on",
        tier=5,
        goal="turn wi-fi off and then turn it back on",
        looks_done=a_global_setting_is("wifi_on", "1"),
        also_seen=(
            Required("wi-fi actually off", a_global_setting_is("wifi_on", "0")),
        ),
        prepare=lambda phone: (start.wi_fi(phone, True), start.go_home(phone)),
        max_steps=18,
        note="a switch that removes the ability to check anything online while off",
    ),
    Scenario(
        id="aeroplane-mode-then-calculator",
        tier=5,
        goal="turn on aeroplane mode and then open the calculator",
        looks_done=all_of(
            in_front(start.CALCULATOR), a_global_setting_is("airplane_mode_on", "1")
        ),
        prepare=lambda phone: (
            start.aeroplane_mode(phone, False),
            start.go_home(phone),
        ),
        max_steps=18,
        note="a device change and a launch, in that order, across two apps",
    ),
)


SCENARIOS = TIER_ONE + TIER_TWO + TIER_THREE + TIER_FOUR + TIER_FIVE

BY_ID = {scenario.id: scenario for scenario in SCENARIOS}


def the_tiers() -> dict[int, tuple[Scenario, ...]]:
    """The scenarios grouped by tier, for a runner that reports per tier."""
    grouped: dict[int, list[Scenario]] = {}
    for scenario in SCENARIOS:
        grouped.setdefault(scenario.tier, []).append(scenario)
    return {tier: tuple(items) for tier, items in sorted(grouped.items())}
