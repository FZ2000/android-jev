#!/usr/bin/env python
"""Record what a real phone says, so the default suite can replay it.

Run with a phone attached. Each recording below is a short, honest session: the
commands a piece of the project really sends, in the order it sends them, so the
transcript holds exactly what a test needs and nothing chosen by hand.

    scripts/record_transcripts.py            # all of them
    scripts/record_transcripts.py status     # just one

The transcripts are committed, so this only needs running when the recording itself
is wrong - a new command has been added to the code, or the output shape changed.
`tests/recorded/` explains how they are replayed.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tests"))

from phone_control.adb import AndroidPhone
from phone_control.apps import InstalledApps
from phone_control.device_state import read_device_state
from phone_control.screen import read_screen
from recorded import (
    recording_what_the_phone_says,
    write_a_transcript,
)

SETTINGS = "com.android.settings"
SETTINGS_SEARCH = "com.google.android.settings.intelligence"
SETTINGS_TOP_LEVEL = "android.settings.SETTINGS"


def what_status_reads(phone: AndroidPhone) -> None:
    """Everything the status tool asks the phone, and nothing else."""
    read_device_state(phone)


def the_settings_list(phone: AndroidPhone) -> None:
    """The top level of Settings: a dump, the window, and the screen size.

    Reached the way the loop reaches it - the app stopped first, because Android
    otherwise reopens Settings on whatever page it was last on.
    """
    phone.run(["shell", "am", "force-stop", SETTINGS, SETTINGS_SEARCH])
    time.sleep(1.0)
    phone.run(["shell", "am", "start", "-a", SETTINGS_TOP_LEVEL])
    time.sleep(3.0)
    read_screen(phone)


def the_installed_apps(phone: AndroidPhone) -> None:
    """What an agent gets when it asks what is installed, and what can be launched."""
    apps = InstalledApps(phone)
    apps.packages()
    apps.launchable()


RECORDINGS = {
    "what-status-reads": (
        "Every command the status tool sends, with the phone awake and unlocked.",
        what_status_reads,
    ),
    "the-settings-list": (
        "The top level of Settings: the dump, the focused window and the screen "
        "size. Real Compose output, which is what the parser has to cope with.",
        the_settings_list,
    ),
    "the-installed-apps": (
        "The package list and the launcher query, which is everything app "
        "resolution is built on.",
        the_installed_apps,
    ),
}


def main(argv: list[str]) -> int:
    wanted = argv[1:] or list(RECORDINGS)
    unknown = [name for name in wanted if name not in RECORDINGS]
    if unknown:
        print(f"no such recording: {', '.join(unknown)}", file=sys.stderr)
        print(f"the recordings are: {', '.join(RECORDINGS)}", file=sys.stderr)
        return 2

    phone = AndroidPhone()
    if not phone.is_connected():
        print("no phone is attached, so there is nothing to record", file=sys.stderr)
        return 2

    for name in wanted:
        note, session = RECORDINGS[name]
        with recording_what_the_phone_says() as records:
            session(phone)
        if not records:
            print(f"{name}: the phone was never asked anything", file=sys.stderr)
            return 1
        path = write_a_transcript(name, records, note)
        print(f"{name}: {len(records)} commands -> {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
