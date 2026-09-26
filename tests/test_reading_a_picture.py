"""Reading a screen by looking at it, tested without a phone.

This route is what made a page Android refuses to dump - About phone - into
something the loop can judge. It had no test at all, so nothing checked that it
reads anything, that it returns nothing instead of raising when it cannot, or that
it asks for a picture at full width.

The picture it reads is a real one: `tests/pictures/about-phone.png` holds three
lines of text drawn with the same framework that reads them, so the recognition is
exercised rather than stubbed. It was made by drawing the words offscreen with
AppKit and saving the PNG - no phone, no screenshot, and the same file every run.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

import phone_control.reading_a_picture as pictures
from phone_control.reading_a_picture import (
    MOST_LINES,
    macos_can_read_pictures,
    read_the_picture,
    the_lines_in,
    the_lines_in_a_file,
)

THE_PICTURE = Path(__file__).parent / "pictures" / "about-phone.png"
THE_WORDS_ON_IT = ["About phone", "Device name", "Pixel 8a"]


needs_eyes = pytest.mark.skipif(
    not macos_can_read_pictures(),
    reason="this machine has no Vision framework, so there is no route to test",
)


def test_the_framework_is_installed_where_the_route_is_promised():
    """On macOS the framework is a dependency, so it has to be importable.

    The tests above stand aside when Vision is missing, which is right on Linux
    and wrong on macOS: there it means the installation is broken, and eight
    skips look exactly like eight tests that do not apply to this platform.
    That is how `pyobjc-framework-Vision` stayed undeclared, with the picture
    route quietly unavailable on every machine except the one it was written on.
    """
    if sys.platform != "darwin":
        pytest.skip("Vision is a macOS framework, so there is nothing to check")

    assert macos_can_read_pictures(), (
        "this is macOS, and pyobjc-framework-Vision is a declared dependency, yet\n"
        "the framework cannot be imported. The picture route is unavailable, so\n"
        "every page the accessibility tree will not describe is unreadable.\n"
        "Reinstall with: uv sync --extra dev"
    )


class APhoneThatShows:
    """A phone whose screenshot is whatever a test hands over."""

    def __init__(self) -> None:
        self.asked_for: list[dict] = []


def capturing(monkeypatch, image: bytes, mime: str = "image/png") -> APhoneThatShows:
    phone = APhoneThatShows()

    def capture(the_phone, max_width=None):
        the_phone.asked_for.append({"max_width": max_width})
        return image, mime

    monkeypatch.setattr("phone_control.screenshots.capture_screen", capture)
    return phone


# --- what is on the picture ----------------------------------------------


@needs_eyes
def test_the_words_in_a_picture_are_read_back():
    """The whole point: text on a screen, turned into lines."""
    lines = the_lines_in_a_file(str(THE_PICTURE))

    assert lines == THE_WORDS_ON_IT


@needs_eyes
def test_only_as_many_lines_as_are_asked_for():
    """The bound is real, and it is what keeps a bad screen from costing a fortune."""
    assert len(the_lines_in_a_file(str(THE_PICTURE), most=2)) == 2
    assert MOST_LINES > 2


def test_a_picture_that_is_not_there_gives_back_nothing():
    """A missing file is not a crash: this whole module is a fallback."""
    assert the_lines_in_a_file("/nowhere/at/all.png") == []


@needs_eyes
def test_something_that_is_not_a_picture_gives_back_nothing():
    """The route must leave the caller where it was, not take the run down."""
    assert the_lines_in(b"this is not a png") == []
    assert the_lines_in(b"") == []


@needs_eyes
def test_being_handed_the_wrong_sort_of_thing_gives_back_nothing():
    assert the_lines_in(None) == []
    assert the_lines_in("a string, not bytes") == []


# --- whether this machine can do it at all -------------------------------


def test_this_machine_can_read_pictures_and_says_so_as_a_flag():
    assert isinstance(macos_can_read_pictures(), bool)


def test_without_the_framework_it_says_no_rather_than_failing(monkeypatch):
    """Nothing here is a hard dependency: no pyobjc must not break an import."""
    monkeypatch.setitem(sys.modules, "Vision", None)

    assert macos_can_read_pictures() is False


@needs_eyes
def test_without_the_framework_a_picture_route_gives_back_nothing(monkeypatch):
    monkeypatch.setattr(pictures, "macos_can_read_pictures", lambda: False)

    assert read_the_picture(APhoneThatShows()) == []


# --- reading the phone's own screen --------------------------------------


@needs_eyes
def test_the_phone_is_photographed_and_its_words_come_back(monkeypatch):
    """End to end except for the camera: real bytes, real recognition."""
    image = THE_PICTURE.read_bytes()
    phone = capturing(monkeypatch, image)

    assert read_the_picture(phone) == THE_WORDS_ON_IT


@needs_eyes
def test_the_picture_is_taken_at_full_width(monkeypatch):
    """Downscaling is what a screenshot normally does, and it costs the small text.

    This route exists for the screens that have nothing else, and those screens are
    settings pages made of small text, so asking for a cheap picture would defeat it.
    """
    phone = capturing(monkeypatch, THE_PICTURE.read_bytes())

    read_the_picture(phone)

    assert phone.asked_for == [{"max_width": 0}]


@needs_eyes
def test_a_phone_that_will_not_be_photographed_gives_back_nothing(monkeypatch):
    """A screenshot that fails must not end the run that was falling back to it."""

    def refuse(the_phone, max_width=None):
        raise RuntimeError("the cable came out")

    monkeypatch.setattr("phone_control.screenshots.capture_screen", refuse)

    assert read_the_picture(APhoneThatShows()) == []
