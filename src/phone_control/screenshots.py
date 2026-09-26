"""Capturing the phone's screen as an image an agent can look at.

A full-resolution Pixel screenshot is several megabytes. Downscaling before it
reaches the model keeps a screenshot affordable enough to take often, which is
what makes visual verification practical.
"""

from __future__ import annotations

import io

from .adb import AndroidPhone
from .errors import PhoneCommandFailed

DEFAULT_MAX_WIDTH = 900
JPEG_QUALITY = 82
PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


def _downscale_to_jpeg(png_bytes: bytes, max_width: int) -> bytes | None:
    """Re-encode wider-than-asked images as JPEG; None when no scaling is needed."""
    try:
        from PIL import Image as PillowImage
    except ImportError:
        return None

    with PillowImage.open(io.BytesIO(png_bytes)) as image:
        if image.width <= max_width:
            return None
        ratio = max_width / float(image.width)
        target = (max_width, max(1, int(image.height * ratio)))
        resized = image.convert("RGB").resize(target, PillowImage.LANCZOS)
        buffer = io.BytesIO()
        resized.save(buffer, format="JPEG", quality=JPEG_QUALITY)
        return buffer.getvalue()


def capture_screen(
    phone: AndroidPhone, max_width: int = DEFAULT_MAX_WIDTH
) -> tuple[bytes, str]:
    """Return ``(image_bytes, mime_type)`` for the phone's current screen."""
    raw = phone.shell_binary("screencap -p")
    if not raw.startswith(PNG_MAGIC):
        raise PhoneCommandFailed(
            "screencap -p",
            "the phone did not return a PNG image",
            fix="Wake the phone and try again; some devices refuse while locked.",
        )
    if max_width > 0:
        scaled = _downscale_to_jpeg(raw, max_width)
        if scaled is not None:
            return scaled, "image/jpeg"
    return raw, "image/png"
