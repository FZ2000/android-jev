"""A folder for every run, so a failure can be read after the fact.

An MCP reply is one shot. The caller sees a summary, and the state that produced
it, the options that were offered, the questions that were asked and every answer
that came back are gone the moment the next step overwrites them. That is fine
while a run works and useless the moment one does not, because the person who has
to fix it is usually not the process that watched it happen, and by then the
phone has moved on.

So a run can leave a folder behind:

    run.json          the report and the timing of every phase
    decisions.jsonl   one line per Jev request: exactly what was sent, what came back
    step-NN.jpg       the screen as the decision saw it
    README.txt        what these files are, written for whoever finds the folder

Three decisions shape it.

**The wire, not the intent.** What is recorded is the request as sent and the
answer as received, because that pair is the only thing that can settle an
argument about why a step went wrong. A reconstruction from what the code meant
to send would agree with the code.

**One line per step, appended as it happens.** A run that crashes, hangs or is
killed still leaves everything up to that point, which is exactly the run someone
needs to read. Nothing is buffered until the end.

**Screenshots are optional, everything else is not.** Reading the screen for a
picture is the single most expensive thing here, so it is off unless asked for.
The record itself costs microseconds and a few kilobytes.

Turning it on:

    PHONE_CONTROL_RUNS=~/.phone-control/runs phone-control

Every run then writes its own folder, named for the time it started and the goal
it was given, and the tool that ran it reports where the folder is. Nothing is
written when the variable is unset, because a tool that quietly fills a disk is
not a tool anyone leaves on.

**What is deliberately not recorded.** The clipboard, and the contents of any
field the phone marks as a password, never reach the state to begin with, so
they cannot reach a folder. The state does carry the text of whatever is on
screen, which on a page a person typed into may be their own words. A run folder
is therefore treated like a log of the screen: private by default, and worth
deleting when it has been read.
"""

from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

# Where run folders go. Unset means no recording at all.
RUNS_DIRECTORY_VARIABLE = "PHONE_CONTROL_RUNS"
# Whether to also keep a picture of each step's screen. Off by default: a
# screenshot costs far more than everything else in a step put together.
SCREENSHOTS_VARIABLE = "PHONE_CONTROL_RUN_SCREENSHOTS"

SCREENSHOT_WIDTH = 480


@dataclass(frozen=True)
class Timings:
    """The seconds spent in each phase of one step.

    Kept per step rather than as a run total, because the interesting question is
    never "how long did this take" but "which phase would I fix". On a phone the
    answer is nearly always the screen reading, and having it in numbers stops
    that being a guess.
    """

    reading_the_screen: float = 0.0
    deciding: float = 0.0
    acting: float = 0.0
    settling: float = 0.0
    watching_the_result: float = 0.0

    @property
    def total(self) -> float:
        return (
            self.reading_the_screen
            + self.deciding
            + self.acting
            + self.settling
            + self.watching_the_result
        )

    def as_dict(self) -> dict[str, float]:
        return {
            "reading_the_screen": round(self.reading_the_screen, 3),
            "deciding": round(self.deciding, 3),
            "acting": round(self.acting, 3),
            "settling": round(self.settling, 3),
            "watching_the_result": round(self.watching_the_result, 3),
            "total": round(self.total, 3),
        }

    def as_line(self) -> str:
        """One line, worst phase first, so the thing to fix is read first."""
        phases = self.as_dict()
        phases.pop("total")
        ordered = sorted(phases.items(), key=lambda pair: -pair[1])
        said = "  ".join(f"{name} {seconds:.2f}s" for name, seconds in ordered)
        return f"total {self.total:.2f}s  ({said})"


class AStopwatch:
    """Times a phase of a step, without the caller having to remember to stop it."""

    def __init__(self) -> None:
        self._started = time.monotonic()

    @property
    def seconds(self) -> float:
        return time.monotonic() - self._started


def a_name_for(goal: str, when: datetime | None = None) -> str:
    """A folder name that says when the run happened and what it was asked.

    Readable rather than unique: two runs of the same goal in the same second
    would collide, and a suffix is added for that instead of paying for a uuid
    nobody can scan.
    """
    moment = (when or datetime.now()).strftime("%Y-%m-%d-%H%M%S")
    words = re.sub(r"[^a-z0-9]+", "-", goal.casefold()).strip("-")[:48]
    return f"{moment}-{words}" if words else moment


@dataclass
class RunFolder:
    """A directory a run writes itself into as it goes.

    Everything here is best-effort by design. A run that cannot write its own
    record must still drive the phone: the recording is for the person debugging
    afterwards, and failing the task because a disk was full would be a worse
    trade than a missing log.
    """

    directory: Path
    keep_screenshots: bool = False
    _exchanges: int = field(default=0, init=False)
    _steps_written: set[int] = field(default_factory=set, init=False)

    @classmethod
    def for_a_run(cls, goal: str, root: Path | None = None) -> RunFolder | None:
        """A folder for this run, or None when recording is switched off."""
        chosen = root if root is not None else _configured_root()
        if chosen is None:
            return None
        return cls(
            directory=_a_free_name(chosen, a_name_for(goal)),
            keep_screenshots=os.environ.get(SCREENSHOTS_VARIABLE, "").strip()
            not in ("", "0", "false", "no"),
        )

    @property
    def is_recording(self) -> bool:
        return True

    def start(self, goal: str, extras: dict[str, Any] | None = None) -> None:
        """Create the folder and say what it is, before anything goes wrong."""
        try:
            self.directory.mkdir(parents=True, exist_ok=True)
            (self.directory / "README.txt").write_text(
                _what_this_is(goal, extras or {}), encoding="utf-8"
            )
        except OSError:
            pass

    def note_exchange(
        self, state: Any, questions: Any, decision: Any, seconds: float = 0.0
    ) -> None:
        """Append one Jev request and its answer, as they went over the wire."""
        record = {
            "step": self._exchanges,
            "asked_at": datetime.now().isoformat(timespec="seconds"),
            "seconds": round(seconds, 3),
            "questions": questions,
            "state": state,
            "answers": getattr(decision, "answers", None),
            "model": getattr(decision, "model", None),
            "input_tokens": getattr(decision, "input_tokens", None),
            "cost_usd": getattr(decision, "cost_usd", None),
        }
        self._exchanges += 1
        self._append("decisions.jsonl", record)

    def note_step(self, index: int, step: Any) -> None:
        """Append what the loop did about an answer, and what it cost."""
        entry = {
            "step": index,
            "action": getattr(step, "action", None),
            "description": getattr(step, "description", None),
            "foreground_app": getattr(step, "foreground_app", None),
            "confidence": getattr(step, "confidence", None),
            "probability": getattr(step, "probability", None),
            "goal_achieved": getattr(step, "goal_achieved", None),
            "changed_the_screen": getattr(step, "changed_the_screen", None),
            "observation": getattr(step, "observation", None),
            "timings": (
                step.timings.as_dict() if getattr(step, "timings", None) else None
            ),
        }
        self._append("steps.jsonl", entry)

    def note_screen(self, index: int, phone) -> None:
        """Keep the picture the decision was made against, when asked for."""
        if not self.keep_screenshots:
            return
        try:
            from .screenshots import capture_screen

            image, _mime = capture_screen(phone, max_width=SCREENSHOT_WIDTH)
            (self.directory / f"step-{index:02d}.jpg").write_bytes(image)
        except Exception:
            # A picture is a convenience. A run is not stopped for one.
            pass

    def finish(self, report: Any) -> None:
        """Write the report last, so a run that died has everything but this."""
        summary = dict(report.as_view())
        summary["run_folder"] = str(self.directory)
        self._write("run.json", summary)
        self._write(
            "timings.txt",
            "\n".join(
                [
                    _timing_summary(report),
                    "",
                    "Read this when a run is slow. `reading_the_screen` is the",
                    "uiautomator dump and parse, `deciding` is the Jev request,",
                    "`acting` is the command to the phone, and `settling` is the",
                    "deliberate pause that lets the screen finish moving.",
                ]
            ),
        )

    def _append(self, name: str, record: dict[str, Any]) -> None:
        try:
            with (self.directory / name).open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(record, default=str) + "\n")
        except (OSError, TypeError):
            pass

    def _write(self, name: str, content: Any) -> None:
        try:
            if isinstance(content, str):
                (self.directory / name).write_text(content, encoding="utf-8")
            else:
                (self.directory / name).write_text(
                    json.dumps(content, indent=2, default=str), encoding="utf-8"
                )
        except OSError:
            pass


def _configured_root() -> Path | None:
    configured = os.environ.get(RUNS_DIRECTORY_VARIABLE, "").strip()
    return Path(configured).expanduser() if configured else None


def _a_free_name(root: Path, wanted: str) -> Path:
    """The wanted name, or the same one with a counter, so nothing is overwritten."""
    candidate = root / wanted
    suffix = 2
    while candidate.exists():
        candidate = root / f"{wanted}-{suffix}"
        suffix += 1
    return candidate


def _timing_summary(report: Any) -> str:
    """Per-phase mean and worst, which is what a slow run is diagnosed from."""
    timings = [step.timings for step in report.steps if getattr(step, "timings", None)]
    if not timings:
        return "no timings were recorded for this run"
    lines = [f"{len(timings)} steps timed"]
    names = list(timings[0].as_dict())
    for name in names:
        values = [getattr(one, name) for one in timings]
        lines.append(
            f"  {name:<22} mean {sum(values) / len(values):.3f}s   max {max(values):.3f}s"
        )
    totals = [one.total for one in timings]
    lines.append(
        f"  {'step total':<22} mean {sum(totals) / len(totals):.3f}s   max {max(totals):.3f}s"
    )
    return "\n".join(lines)


def _what_this_is(goal: str, extras: dict[str, Any]) -> str:
    """A README in every folder, for whoever opens it with no context."""
    described = "\n".join(f"  {name}: {value}" for name, value in extras.items())
    return f"""This folder is one run of the Android phone.

  goal: {goal}
{described}

  run.json         the report: what it claimed, why it stopped, every step
  steps.jsonl      one line per step: the action, its two numbers, what changed
  decisions.jsonl  one line per Jev request: the state, the options, the
                   questions, and every probability that came back
  timings.txt      where the seconds went, per phase
  step-NN.jpg      the screen the decision was made against, when enabled

Reading it in order:

  1. run.json says what happened. `achieved` is what the run claimed;
     `reason` says why it stopped.
  2. steps.jsonl says what it did. `changed_the_screen: false` marks a step
     that had no effect, which is usually where a run starts going wrong.
  3. decisions.jsonl says what it was thinking. The record for a step is the
     state and the options exactly as sent, so a wrong decision can be told
     from a right decision made impossible by a poor question.

To re-decide a step without a phone:

  .venv/bin/python scripts/replay_run.py <this folder> --step 2

Written by phone-control. Nothing here is needed once the run has been read, and
the screen text it holds is whatever was on the phone at the time.
"""
