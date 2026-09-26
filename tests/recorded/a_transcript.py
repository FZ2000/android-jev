"""Recording what the phone actually said, and playing it back without a phone.

Every other fake in this repository answers from something a person wrote down.
That is the weakness the project keeps paying for: a fake can only ever repeat what
its author believed, and the fake that agreed a bare domain opens a browser was
believed for months. `tests/world/` and `tests/conftest.py` are both like that, and
both are useful - but neither of them is evidence about a real device.

A transcript is. It is adb's own command line and adb's own bytes, captured once
while a real phone answered, and replayed afterwards on any machine with no phone
attached. The parsing, the option building and the loop can then be tested in the
default suite against output nobody chose.

**The replayer refuses to invent.** A fake that answers an unrecorded command with
an empty string agrees with every question it is asked, including the wrong ones, so
`replay()` raises `NotRecorded` instead. That is the whole value: a test that has
drifted away from what a phone does should go red, not quietly pass.

What this is not: it is not a simulator. It cannot answer a command that was not
recorded, it does not notice a tap changing a screen, and it has no idea what the
phone would do next. Where a test needs the phone to *react*, `tests/world/` is the
right tool. This one is for proving that real output is read correctly.
"""

from __future__ import annotations

import base64
import contextlib
import json
import re
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from phone_control.errors import PhoneCommandFailed

THE_TRANSCRIPTS = Path(__file__).parent


class NotRecorded(Exception):
    """A command was asked for that this transcript never saw.

    Raised rather than answered, because answering it would be inventing what a
    phone would have said.
    """


# The one part of a command that is different every run by design. A screen dump
# goes to a path holding the process id and a random suffix, so that two dumps in
# flight cannot read each other's half-written file - which is right, and which
# would otherwise mean no transcript could ever be replayed.
AVOLATILE_PATH = re.compile(r"/sdcard/dsh_screen_\d+_[0-9a-f]+\.xml")


def a_command_that_can_be_matched(arguments: list[str]) -> list[str]:
    """The command with the parts that are different every run taken out.

    Both the recording and the replay go through this, so the two agree on what a
    command *is*. Only genuinely per-run values are replaced, and each one is named
    here rather than being pattern-matched loosely: a transcript that matched the
    wrong command would answer a question that was never asked.
    """
    return [AVOLATILE_PATH.sub("<A_DUMP_FILE>", argument) for argument in arguments]


def the_serial_in(arguments: list[str]) -> list[str]:
    """The command without the program or the `-s <serial>` naming the device.

    Both are stripped so a transcript recorded on one phone replays on any machine:
    the serial is in the file under its own name for whoever reads it, and it is not
    part of what the phone was asked.
    """
    rest = list(arguments)
    if rest and not rest[0].startswith("-"):
        rest = rest[1:]
    if len(rest) >= 2 and rest[0] == "-s":
        rest = rest[2:]
    return a_command_that_can_be_matched(rest)


def as_lines(record: dict[str, Any]) -> str:
    """One record, written small enough to read in a diff."""
    return json.dumps(record, ensure_ascii=False, sort_keys=True)


class ATranscript:
    """The commands one session asked for, in the order they were asked."""

    def __init__(self, name: str, records: list[dict[str, Any]]) -> None:
        self.name = name
        self.records = records
        self.asked: list[list[str]] = []
        # Keyed on the matchable form, not on the file's own text: the file keeps
        # the command exactly as it was run - including the dump path that differs
        # every time - and the matching is done on what does not differ.
        self._by_command: dict[str, list[dict[str, Any]]] = {}
        for record in records:
            key = as_lines(a_command_that_can_be_matched(record["command"]))
            self._by_command.setdefault(key, []).append(record)
        # Answers are handed out in the order they were recorded, so a test that
        # reads a screen twice sees what the phone said the first time and then what
        # it said the second.
        self._taken: dict[str, int] = {}

    def answer_for(self, command: list[str]) -> dict[str, Any]:
        self.asked.append(list(command))
        key = as_lines(a_command_that_can_be_matched(command))
        available = self._by_command.get(key)
        if not available:
            raise NotRecorded(
                f"nothing in the transcript {self.name!r} answers {command}. "
                f"It holds: {sorted(self._by_command)}"
            )
        taken = self._taken.get(key, 0)
        self._taken[key] = taken + 1
        # Past the end the last answer repeats: most commands here are a screen
        # being read again while nothing has moved, and making a test declare that
        # would say more about the fake than about the phone.
        return available[min(taken, len(available) - 1)]

    def result_for(self, command: list[str]) -> _ARecordedResult:
        return _ARecordedResult(self.answer_for(command))

    def commands(self) -> list[list[str]]:
        return [record["command"] for record in self.records]

    def what_it_was(self) -> str:
        """The note the recording was made with, for whoever reads a failure."""
        first = self.records[0] if self.records else {}
        return str(first.get("note") or "no note was written with this transcript")


class _ARecordedResult:
    """One recorded answer, in the shape `AndroidPhone` returns them."""

    def __init__(self, record: dict[str, Any]) -> None:
        self.stdout: bytes = _the_bytes(record, "stdout")
        self.stderr: bytes = _the_bytes(record, "stderr")
        self.return_code: int = int(record.get("returncode") or 0)
        self._record = record

    @property
    def text(self) -> str:
        return self.stdout.decode("utf-8", "replace")

    @property
    def error_text(self) -> str:
        return self.stderr.decode("utf-8", "replace")


def _the_bytes(record: dict[str, Any], which: str) -> bytes:
    """Text where the output was text, base64 where it was not.

    A dump of a screen is worth keeping readable in a diff; a screenshot is not
    text and would be corrupted by pretending otherwise. The recording decides which
    it is, once, rather than every reader guessing.
    """
    if f"{which}_base64" in record:
        return base64.b64decode(record[f"{which}_base64"])
    return str(record.get(which) or "").encode("utf-8")


def _the_record(command: list[str], completed: Any) -> dict[str, Any]:
    record: dict[str, Any] = {"command": the_serial_in(command)}
    for which in ("stdout", "stderr"):
        raw = getattr(completed, which) or b""
        try:
            record[which] = raw.decode("utf-8")
        except UnicodeDecodeError:
            record[f"{which}_base64"] = base64.b64encode(raw).decode("ascii")
    record["returncode"] = getattr(completed, "returncode", 0)
    return record


@contextlib.contextmanager
def recording_what_the_phone_says() -> Iterator[list[dict[str, Any]]]:
    """Record every adb command the phone is given, into the list handed back.

    It captures at `subprocess.run` - the boundary where the real bytes arrive -
    rather than at any one of the four ways of running a command, so nothing can
    slip past it unrecorded.
    """
    import phone_control.adb as adb_module

    records: list[dict[str, Any]] = []
    real = adb_module.subprocess.run

    def record_it(arguments, **keywords):
        completed = real(arguments, **keywords)
        records.append(_the_record(list(arguments), completed))
        return completed

    adb_module.subprocess.run = record_it
    try:
        yield records
    finally:
        adb_module.subprocess.run = real


def write_a_transcript(
    name: str,
    records: list[dict[str, Any]],
    note: str,
    identifiers: list[str] | None = None,
) -> Path:
    """Save a recording where `replay` will find it, with a line saying what it is.

    Written redacted, always: see `without_identifiers`. A transcript is a phone's own
    output going into a public repository, and scrubbing at write time is the only
    version of this that cannot be forgotten later.
    """
    path = THE_TRANSCRIPTS / f"{name}.jsonl"
    known = identifiers or []
    lines = [
        as_lines(
            {
                "note": without_identifiers(note, known),
                "recorded_from": without_identifiers(
                    " ".join(records[0]["command"]) if records else "", known
                ),
                "redacted": True,
            }
        )
    ]
    for record in records:
        scrubbed = dict(record)
        for field in ("stdout", "stderr"):
            if isinstance(scrubbed.get(field), str):
                scrubbed[field] = without_identifiers(scrubbed[field], known)
        scrubbed["command"] = [
            without_identifiers(part, known) for part in record["command"]
        ]
        lines.append(as_lines(scrubbed))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def replay(name: str) -> ATranscript:
    """The transcript of that name, ready to answer commands."""
    path = THE_TRANSCRIPTS / f"{name}.jsonl"
    if not path.is_file():
        raise NotRecorded(
            f"there is no transcript called {name!r}. Record one with "
            f"scripts/record_transcripts.py, which needs a phone attached."
        )
    records = [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    note, *commands = records
    for record in commands:
        record.setdefault("note", note.get("note"))
    return ATranscript(name, commands)


class APhoneOnRecord:
    """A phone that answers from a transcript, and says so when it cannot.

    Implements the four ways this project runs a command, plus the serial and the
    connection check, which is everything the screen reader, the device state and
    the app list ask for. Nothing here talks to a device.
    """

    def __init__(
        self, transcript: ATranscript, serial: str = "a-recorded-phone"
    ) -> None:
        self.transcript = transcript
        self.serial = serial

    # --- the phone's own surface -----------------------------------------

    def serial_number(self) -> str:
        return self.serial

    def is_connected(self) -> bool:
        return True

    def run(self, arguments: list[str], timeout: float | None = None) -> str:
        return self.transcript.result_for(arguments).text

    def run_result(self, arguments: list[str], timeout: float | None = None):
        return self.transcript.result_for(arguments)

    def run_binary(self, arguments: list[str], timeout: float | None = None) -> bytes:
        return self.transcript.result_for(arguments).stdout

    def run_checked(self, arguments: list[str], timeout: float | None = None) -> str:
        """The same rule the real phone applies: a failed command raises."""
        result = self.transcript.result_for(arguments)
        message = result.error_text.strip()
        if result.return_code != 0 or "Exception" in message or "Error:" in message:
            raise PhoneCommandFailed(" ".join(arguments), message or result.text)
        return result.text

    def shell(self, command: str, timeout: float | None = None) -> str:
        return self.transcript.result_for(["shell", command]).text

    def shell_binary(self, command: str, timeout: float | None = None) -> bytes:
        return self.transcript.result_for(["shell", command]).stdout


# --- what must not be written down ----------------------------------------
#
# A transcript is a phone's own output, committed to a public repository, so anything
# the phone knows about its owner travels with it. Measured on the first transcripts
# recorded here: the Settings list dump carried the account holder's display name, and
# the device list carried the serial - both of which identify a particular person and a
# particular handset.
#
# The rule is discovery rather than a list of words, because a list is exactly the kind
# of thing that goes stale silently. The phone will say what its identifiers are:
#
#   adb shell getprop ro.serialno                 the serial
#   adb shell settings get secure android_id      the installation id
#   adb shell dumpsys account                     every account name, and its local part
#
# A display name is the one thing no device reports as an identifier, so it cannot be
# found this way; the variable named below is where a reviewer puts what a reading of the
# file turns up.

# A display name is the one thing no device reports as an identifier, so it cannot be
# discovered and must not be written here either - a name in the source is the same
# disclosure as a name in the fixture. A reviewer sets this while recording on their own
# phone, having read what came out:
#
#   PHONE_CONTROL_TRANSCRIPT_REDACTIONS="Firstname,another term" scripts/record_transcripts.py
#
# Everything else is found by asking the device, which is why this is the only list.
THE_VARIABLE_FOR_REVIEWED_TERMS = "PHONE_CONTROL_TRANSCRIPT_REDACTIONS"


def the_terms_a_reviewer_added() -> tuple[str, ...]:
    """Whatever a person decided by hand, from the environment rather than the source."""
    from os import environ

    said = environ.get(THE_VARIABLE_FOR_REVIEWED_TERMS, "")
    return tuple(term.strip() for term in said.split(",") if term.strip())


# Square brackets, and the reason is not cosmetic: what a transcript holds is XML dumps
# inside JSON strings, so a placeholder containing `<` or `>` is not well-formed XML and
# breaks every test that replays the recording. Measured the hard way - the first version
# of this was `<redacted>` and the suite went red on "the screen dump was not valid XML",
# which is the recordings being replayed and finding the damage.
A_REDACTED_IDENTIFIER = "[redacted]"
AN_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")

# A bare run of digits is deliberately *not* redacted, and the reason is worth knowing
# before somebody adds it back. It cannot be told from what a dump is full of: the
# transcripts recorded here contain `mTouchEndedTimestampNanos=49211271709981` and
# `mId=1790354791785`, and a rule wide enough to catch a written phone number has to
# catch those too. Redacting them would not protect anybody and would shred the real
# output the fixture exists to preserve. A phone number the device knows is caught by
# the account and identifier rules above; one merely displayed on a screen is for the
# reviewer, which is what EXTRA_TERMS is for.


def the_things_the_phone_calls_itself(phone: Any) -> list[str]:
    """Every string this phone would rather not have written down."""
    found: list[str] = []

    def remember(said: str) -> None:
        said = (said or "").strip()
        if len(said) >= 4 and said not in found:
            found.append(said)

    for command in ("getprop ro.serialno", "settings get secure android_id"):
        with contextlib.suppress(Exception):
            remember(phone.shell(command))

    try:
        # `Account {name=someone@example.com, type=com.google}` - the address and the
        # part before the @, which is usually the person's own handle.
        for match in re.finditer(
            r"name=([^,\s}]+)", phone.shell("dumpsys account") or ""
        ):
            remember(match.group(1))
            remember(match.group(1).split("@")[0])
    except Exception:
        pass

    for term in the_terms_a_reviewer_added():
        remember(term)
    return found


def without_identifiers(text: str, identifiers: list[str]) -> str:
    """The same text with this phone's identifiers taken out.

    Longest first, so a serial is not half-replaced by a shorter term that happens to
    be inside it. Then by pattern, for the things that identify somebody without the
    device being asked: an email address, and anything shaped like a phone number.
    """
    redacted = text
    for identifier in sorted(identifiers, key=len, reverse=True):
        redacted = redacted.replace(identifier, A_REDACTED_IDENTIFIER)
    return AN_EMAIL.sub(A_REDACTED_IDENTIFIER, redacted)
