"""Turning "open the clock" into a package, and launching it.

This is where a spoken name becomes a real package id, and it has already been
wrong once in a way worth keeping a test for: three names resolved to the wrong
app - "files" to the documents provider rather than the Files app, "docs" to Drive,
"companion" to the watch app. The scoring below is what decides, so the tiers are
asserted rather than left to whoever changes them next.

The launch half holds the other recorded bug: a launcher activity is a Java class
name, and YouTube's is `...Shell$HomeActivity`. Unquoted, the device shell reads
`$HomeActivity` as a variable, expands it to nothing, and `am` is asked to start an
activity that does not exist. The quoting is asserted here because nothing else
would notice if it went.

Nothing here needs a phone: the stub answers the two calls this code makes.
"""

from __future__ import annotations

import pytest

from phone_control.apps import (
    InstalledApps,
    _package_match_score,
    _was_refused,
    launch_app,
    resolve_launcher_component,
    spoken_name,
)
from phone_control.errors import NoMatchingControl, PhoneCommandFailed

SOME_PACKAGES = [
    "com.google.android.deskclock",
    "com.google.android.apps.photos",
    "com.android.settings",
    "com.android.chrome",
    "com.google.android.apps.nbu.files",
]


class APhoneWithApps:
    """A phone that answers the two calls app resolution makes."""

    def __init__(self, packages=None, components=None, shell_says=None, run_says=None):
        self.packages = packages if packages is not None else SOME_PACKAGES
        self.components = components or {}
        self.shell_says = shell_says or {}
        self.run_says = run_says or ""
        self.ran: list[list[str]] = []
        self.shelled: list[str] = []

    def shell(self, command: str, timeout=None) -> str:
        self.shelled.append(command)
        # A test that says what the phone replies wins over the built-in answers,
        # which exist only so the common case needs no setup.
        for fragment, answer in self.shell_says.items():
            if fragment in command:
                return answer
        if command.startswith("cmd package query-activities"):
            return "\n".join(f"{package}/.Launcher" for package in self.packages)
        if command.startswith("cmd package resolve-activity"):
            package = command.split()[-1]
            return self.components.get(package, "")
        if command.startswith("pm list packages"):
            return "\n".join(f"package:{package}" for package in self.packages)
        return ""

    def run(self, arguments, timeout=None) -> str:
        self.ran.append(list(arguments))
        return self.run_says


# --- how well a spoken name fits a package -------------------------------


@pytest.mark.parametrize(
    ("spoken", "package"),
    [
        ("com.android.chrome", "com.android.chrome"),
        ("chrome", "com.android.chrome"),
        ("google android deskclock", "com.google.android.deskclock"),
        ("deskc", "com.google.android.deskclock"),
        ("nbu", "com.google.android.apps.nbu.files"),
    ],
)
def test_each_way_a_name_can_fit_is_scored_above_nothing(spoken, package):
    assert _package_match_score(spoken, package) > 0


def test_the_closest_kind_of_fit_scores_highest():
    """A whole package id beats its last word, which beats a prefix, which beats
    a mention somewhere inside - the order is what stops a loose match winning."""
    exact = _package_match_score("com.android.chrome", "com.android.chrome")
    tail = _package_match_score("chrome", "com.android.chrome")
    words = _package_match_score("com android chrome", "com.android.chrome")
    prefix = _package_match_score("chrom", "com.android.chrome")
    substring = _package_match_score("androidchrome", "com.android.chrome")

    assert exact > tail > words > prefix > substring > 0


def test_a_name_that_does_not_fit_at_all_scores_nothing():
    assert _package_match_score("nothing-like-this", "com.android.chrome") == 0
    assert _package_match_score("", "com.android.chrome") == 0


# --- resolving a name to a package ---------------------------------------


def the_apps(**keywords) -> InstalledApps:
    return InstalledApps(APhoneWithApps(**keywords))


def test_an_exact_package_id_is_taken_as_given():
    assert the_apps().resolve("com.android.chrome") == "com.android.chrome"


def test_a_spoken_name_that_is_an_app_is_resolved():
    assert the_apps().resolve("chrome") == "com.android.chrome"


def test_the_name_a_person_uses_wins_over_a_loose_match():
    """ "files" is the Files app, and it used to land on the documents provider."""
    assert the_apps().resolve("files") == "com.google.android.apps.nbu.files"


def test_an_empty_name_is_refused():
    with pytest.raises(ValueError, match="An app name is required"):
        the_apps().resolve("   ")


def test_a_name_that_matches_nothing_lists_what_is_there():
    """The recovery path: an agent that asked for an app that is not installed
    needs the names that are, or it cannot try again."""
    with pytest.raises(NoMatchingControl) as refused:
        the_apps().resolve("something-not-installed")

    assert "installed" in str(refused.value).casefold()


def test_the_package_list_is_read_once_and_remembered():
    phone = APhoneWithApps()
    apps = InstalledApps(phone)

    apps.packages()
    apps.packages()

    asked = [c for c in phone.shelled if c.startswith("pm list packages")]
    assert len(asked) == 1, "the installed packages were fetched twice"


# --- what the launcher can start -----------------------------------------


def test_the_launchable_apps_come_from_the_launcher_query():
    apps = the_apps()

    assert apps.launchable() == sorted(SOME_PACKAGES)
    assert len(apps.launchable_names()) == len(SOME_PACKAGES)


def test_launchable_ignores_commentary_the_command_prints():
    """ "priority" lines are printed alongside the components and are not apps."""
    phone = APhoneWithApps(
        shell_says={
            "query-activities": "priority=0\ncom.example.one/.Main\ncom.example.two/.Main"
        }
    )
    phone.packages = []

    assert InstalledApps(phone).launchable() == ["com.example.one", "com.example.two"]


def test_the_listing_names_every_package_in_words():
    listing = the_apps().as_listing()

    assert "Clock" in listing
    assert "com.google.android.deskclock" in listing
    assert listing.startswith(f"{len(SOME_PACKAGES)} installed packages:")


def test_a_package_is_named_the_way_a_person_would_say_it():
    assert spoken_name("com.google.android.deskclock") == "Clock"


# --- launching it --------------------------------------------------------


def test_a_component_is_asked_for_and_then_started():
    phone = APhoneWithApps(
        components={"com.android.chrome": "com.android.chrome/com.android.chrome.Main"}
    )

    launched = launch_app(phone, InstalledApps(phone), "chrome")

    assert launched == "com.android.chrome"
    assert [
        "shell",
        "am",
        "start",
        "-W",
        "-n",
        "'com.android.chrome/com.android.chrome.Main'",
    ] in phone.ran


def test_a_component_with_a_dollar_in_it_is_quoted():
    """The YouTube bug: unquoted, the device shell expands `$HomeActivity` to nothing."""
    phone = APhoneWithApps(
        components={
            "com.google.android.youtube": (
                "com.google.android.youtube/com.google.android.apps.youtube.app."
                "Shell$HomeActivity"
            )
        }
    )
    phone.packages = ["com.google.android.youtube"]

    launch_app(phone, InstalledApps(phone), "com.google.android.youtube")

    started = [command for command in phone.ran if "am" in command]
    assert started, "nothing was started"
    component = started[0][-1]
    assert "$HomeActivity" in component, "the component lost its inner class name"
    assert component.startswith("'"), (
        f"the component was not quoted, so the device shell will expand the dollar: "
        f"{component!r}"
    )
    assert component.endswith("'"), (
        f"the component was not quoted, so the device shell will expand the dollar: "
        f"{component!r}"
    )


def test_with_no_component_the_monkey_launcher_is_used():
    phone = APhoneWithApps(components={})

    launch_app(phone, InstalledApps(phone), "chrome")

    assert [
        "shell",
        "monkey",
        "-p",
        "com.android.chrome",
        "-c",
        "android.intent.category.LAUNCHER",
        "1",
    ] in phone.ran


def test_a_start_that_was_refused_is_reported_rather_than_assumed():
    """`am` prints its failure and exits zero, so the output is the evidence."""
    phone = APhoneWithApps(
        components={"com.android.chrome": "com.android.chrome/.Main"},
        run_says="Error: Activity class {com.android.chrome/.Main} does not exist.",
    )

    with pytest.raises(PhoneCommandFailed) as refused:
        launch_app(phone, InstalledApps(phone), "chrome")

    assert "launcher" in refused.value.fix.casefold()


def test_a_monkey_launch_that_was_refused_is_reported_too():
    phone = APhoneWithApps(components={}, run_says="aborted")

    with pytest.raises(PhoneCommandFailed) as refused:
        launch_app(phone, InstalledApps(phone), "chrome")

    assert "launcher" in refused.value.fix.casefold()


def test_a_component_that_cannot_be_asked_for_is_launched_the_other_way():
    """An adb failure here is not fatal: the app may still be startable by monkey."""

    class APhoneThatRefusesToResolve(APhoneWithApps):
        def shell(self, command, timeout=None):
            if command.startswith("cmd package resolve-activity"):
                raise RuntimeError("adb fell over")
            return super().shell(command, timeout=timeout)

    phone = APhoneThatRefusesToResolve()

    assert resolve_launcher_component(phone, "com.android.chrome") is None
    assert launch_app(phone, InstalledApps(phone), "chrome") == "com.android.chrome"


def test_a_resolution_with_nothing_useful_in_it_returns_nothing():
    phone = APhoneWithApps(
        shell_says={"resolve-activity": "priority=0\nNo activity found"}
    )

    assert resolve_launcher_component(phone, "com.example") is None


def test_refusal_markers_are_matched_as_words_in_the_output():
    assert _was_refused("Error: nothing here", ("Error:",)) is True
    assert _was_refused("Starting: Intent { ... }", ("Error:", "aborted")) is False
