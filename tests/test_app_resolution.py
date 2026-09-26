"""Resolving the name a person says into a package that is really installed."""

from __future__ import annotations

import pytest

from phone_control.apps import InstalledApps, humanize_package
from phone_control.errors import NoMatchingControl


def test_an_exact_package_id_is_used_as_given(fake_phone):
    apps = InstalledApps(fake_phone)

    assert apps.resolve("com.example.chat") == "com.example.chat"


def test_a_spoken_name_resolves_through_the_alias_table(fake_phone):
    apps = InstalledApps(fake_phone)

    assert apps.resolve("YouTube") == "com.google.android.youtube"


def test_a_name_matches_the_tail_of_a_package(fake_phone):
    apps = InstalledApps(fake_phone)

    assert apps.resolve("settings") == "com.android.settings"


def test_an_alias_is_ignored_when_that_app_is_not_installed(phone_with_one_app):
    """A curated guess must never beat what is actually on the phone."""
    apps = InstalledApps(phone_with_one_app)

    with pytest.raises(NoMatchingControl):
        apps.resolve("whatsapp")


def test_an_unknown_name_lists_what_is_actually_installed(fake_phone):
    apps = InstalledApps(fake_phone)

    with pytest.raises(NoMatchingControl) as raised:
        apps.resolve("nonexistent app")

    assert "chat" in str(raised.value)
    assert "list_apps" in str(raised.value)


def test_the_package_list_is_read_once_and_then_reused(fake_phone):
    apps = InstalledApps(fake_phone)

    apps.packages()
    apps.packages()

    assert len(fake_phone.commands_matching("pm list packages")) == 1


@pytest.mark.parametrize(
    ("package", "expected"),
    [
        pytest.param("com.google.android.youtube", "youtube", id="google-app"),
        pytest.param("com.android.settings", "settings", id="system-app"),
        pytest.param("org.thoughtcrime.securesms", "securesms", id="org-app"),
    ],
)
def test_a_readable_name_is_derived_from_the_package(package, expected):
    assert humanize_package(package) == expected


def test_a_package_of_only_generic_segments_keeps_its_full_name():
    assert humanize_package("com.google.android") == "com.google.android"


def test_a_launcher_component_is_quoted_for_the_device_shell(fake_phone):
    """An inner class carries a dollar sign, which the device shell would eat.

    Observed on YouTube: am was asked to start ...Shell because $HomeActivity had
    been expanded to nothing on the way through, and reported that the activity
    does not exist.
    """
    from phone_control.apps import InstalledApps, launch_app

    fake_phone.answers["cmd package resolve-activity"] = (
        "com.google.android.youtube/.app.honeycomb.Shell$HomeActivity"
    )
    fake_phone.answers["pm list packages"] = "package:com.google.android.youtube"

    launch_app(fake_phone, InstalledApps(fake_phone), "youtube")

    started = [c for c in fake_phone.commands if "am start" in c]
    assert started, "nothing was started"
    assert (
        "'com.google.android.youtube/.app.honeycomb.Shell$HomeActivity'" in started[0]
    )
