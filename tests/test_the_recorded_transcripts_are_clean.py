"""A transcript is real phone output going into a public repository.

So it has to be free of anything that identifies the person holding the phone or the
handset itself. Measured on the first transcripts recorded here: the Settings list dump
carried the account holder's display name and the device list carried the serial, both
of which were committed before anyone looked.

The recorder redacts as it writes, by asking the device for its own identifiers - see
`tests/recorded/a_transcript.py`. These are the checks that it stayed true, because the
one thing a rule like that cannot catch is a name the device never declares.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

THE_TRANSCRIPTS = Path(__file__).resolve().parent / "recorded"
AN_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
# A device serial mixes capitals and digits. Both halves are what make this usable, and
# each was arrived at by getting it wrong first. The serial this was measured against is
# deliberately not repeated here: it belongs to a real phone, and a test below enforces
# that no transcript carries it. Digits
# alone are timestamps and task ids: the first pattern reported thirteen of them in one
# transcript. Letters alone are ordinary words: the second reported `CONFIGURATIONS`,
# a section heading in a window dump. A serial is neither, so it needs one of each.
A_SERIAL_SHAPE = re.compile(r"\b(?=[A-Z0-9]*[A-Z])(?=[A-Z0-9]*[0-9])[A-Z0-9]{12,}\b")
# What the identifiers look like once the recorder has been through them. Square
# brackets rather than angle ones, because the dumps inside a transcript are XML.
THE_MARK = "[redacted]"


def the_transcripts() -> list[Path]:
    return sorted(THE_TRANSCRIPTS.glob("*.jsonl"))


def test_there_are_transcripts_to_check():
    """Without this the rest of the file would pass on an empty directory."""
    assert the_transcripts(), f"no transcripts under {THE_TRANSCRIPTS}"


@pytest.mark.parametrize("transcript", the_transcripts(), ids=lambda path: path.name)
def test_no_transcript_carries_an_email_address(transcript):
    found = AN_EMAIL.findall(transcript.read_text(encoding="utf-8"))

    assert not found, f"{transcript.name} carries {found[:3]}"


@pytest.mark.parametrize("transcript", the_transcripts(), ids=lambda path: path.name)
def test_no_transcript_carries_a_serial_shaped_token(transcript):
    """Twelve or more capitals and digits in a row is what a serial looks like.

    Deliberately narrow. A rule wide enough to catch a phone number would also catch
    the timestamps and task ids a dump is made of, and the fixture would stop being the
    real output it exists to preserve.
    """
    found = [
        token
        for token in A_SERIAL_SHAPE.findall(transcript.read_text(encoding="utf-8"))
        if token != "REDACTED"
    ]

    assert not found, f"{transcript.name} carries serial-shaped tokens: {found[:3]}"


@pytest.mark.parametrize("transcript", the_transcripts(), ids=lambda path: path.name)
def test_a_transcript_says_it_was_redacted(transcript):
    """The first line is the note, and it has to admit what was done to the file."""
    first = transcript.read_text(encoding="utf-8").splitlines()[0]

    assert '"redacted": true' in first, (
        f"{transcript.name} does not say it was redacted, so nothing tells a reader "
        f"that identifiers were taken out: {first[:90]}"
    )
