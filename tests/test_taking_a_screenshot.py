"""Taking a screenshot, and the two things that can go wrong doing it.

A full-resolution Pixel screenshot is several megabytes, so the default path
downscales it before it reaches a model. None of this was tested: not the refusal
when the phone answers something that is not a picture, not the case where no
scaling is needed, and not the case where the library that would do the scaling is
not installed at all.

That last one is why the fallback exists, and it is a fallback rather than an error:
a screenshot that comes back full size is a bigger request, not a failed one.

The picture used here is the real one this repository already keeps for the OCR
tests, so the scaling is exercised on a real PNG rather than a hand-built header.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from phone_control.errors import PhoneCommandFailed
from phone_control.screenshots import (
    JPEG_QUALITY,
    PNG_MAGIC,
    _downscale_to_jpeg,
    capture_screen,
)

THE_PICTURE = Path(__file__).parent / "pictures" / "about-phone.png"


class APhoneShowing:
    """A phone whose screen comes back as whatever bytes a test hands over."""

    def __init__(self, image: bytes) -> None:
        self.image = image
        self.commands: list[str] = []

    def shell_binary(self, command: str, timeout=None) -> bytes:
        self.commands.append(command)
        return self.image


def a_screenshot() -> bytes:
    return THE_PICTURE.read_bytes()


# --- taking it -----------------------------------------------------------


def test_the_screen_comes_back_as_a_png_when_no_scaling_is_wanted():
    """`max_width=0` means the caller wants the real thing, pixels and all."""
    phone = APhoneShowing(a_screenshot())

    image, mime = capture_screen(phone, max_width=0)

    assert image == a_screenshot()
    assert mime == "image/png"
    assert phone.commands == ["screencap -p"]


def test_a_wide_screenshot_is_downscaled_before_it_is_sent():
    """The whole reason this module exists: several megabytes is not affordable."""
    phone = APhoneShowing(a_screenshot())

    image, mime = capture_screen(phone, max_width=200)

    assert mime == "image/jpeg"
    assert len(image) < len(a_screenshot())

    import io

    from PIL import Image

    with Image.open(io.BytesIO(image)) as scaled:
        assert scaled.width == 200, "the picture was not scaled to the width asked for"


def test_a_screenshot_that_is_already_small_enough_is_left_alone():
    """Re-encoding it would lose quality for nothing."""
    phone = APhoneShowing(a_screenshot())

    image, mime = capture_screen(phone, max_width=100_000)

    assert image == a_screenshot()
    assert mime == "image/png"


def test_without_the_scaling_library_the_screenshot_is_still_taken(monkeypatch):
    """A missing library is a bigger request, not a failed one."""
    monkeypatch.setitem(sys.modules, "PIL", None)
    monkeypatch.setitem(sys.modules, "PIL.Image", None)
    phone = APhoneShowing(a_screenshot())

    image, mime = capture_screen(phone)

    assert image == a_screenshot()
    assert mime == "image/png"


def test_something_that_is_not_a_picture_is_refused_with_a_next_step():
    """A locked phone can answer `screencap` with an error message rather than an
    image, and passing that on as a picture would fail much later and worse."""
    phone = APhoneShowing(b"error: cannot take screenshot while locked\n")

    with pytest.raises(PhoneCommandFailed) as refused:
        capture_screen(phone)

    assert "PNG" in str(refused.value)
    assert "locked" in refused.value.fix.casefold()


def test_an_empty_answer_is_refused_rather_than_sent_on():
    phone = APhoneShowing(b"")

    with pytest.raises(PhoneCommandFailed):
        capture_screen(phone)


# --- and the scaling itself ----------------------------------------------


def test_scaling_is_skipped_when_it_would_not_change_anything():
    """The picture is 900 wide, so 900 is the boundary rather than a scale."""
    assert _downscale_to_jpeg(a_screenshot(), max_width=900) is None
    assert _downscale_to_jpeg(a_screenshot(), max_width=901) is None
    assert _downscale_to_jpeg(a_screenshot(), max_width=899) is not None


def test_scaling_keeps_the_shape_of_the_picture():
    scaled = _downscale_to_jpeg(a_screenshot(), max_width=300)

    import io

    from PIL import Image

    with Image.open(THE_PICTURE) as original:
        shape = original.height / original.width
    with Image.open(io.BytesIO(scaled)) as resized:
        assert resized.height / resized.width == pytest.approx(shape, abs=0.02)


def test_a_picture_with_no_height_to_it_does_not_produce_a_zero_height():
    """A one-pixel-tall sliver would make an invalid image; the floor is one."""
    import io

    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", (400, 1), "white").save(buffer, format="PNG")

    scaled = _downscale_to_jpeg(buffer.getvalue(), max_width=10)

    with Image.open(io.BytesIO(scaled)) as resized:
        assert resized.width == 10
        assert resized.height == 1


def test_the_magic_bytes_are_the_ones_a_png_starts_with():
    """The check is against this constant, so it has to be right."""
    assert a_screenshot().startswith(PNG_MAGIC)
    assert JPEG_QUALITY < 100, "the quality is chosen to keep the file small"
