"""The run folder: what a failed run leaves behind, and whether it is usable.

Two things are being checked here, and the second matters more than the first.

The folder is ordinary code - it writes a report, a line per request, a line per
step, and a README - and it has to survive being written to by a run that then
crashes, because a crash is the run somebody actually needs to read.

The second thing is the promise that nothing is recorded when nobody asked. A
tool that quietly fills a disk is not one anybody leaves switched on, so the
default is checked as carefully as the feature.
"""

from __future__ import annotations

import json
from typing import ClassVar

import pytest

from phone_control.goal import Step, TaskReport
from phone_control.runs import (
    RUNS_DIRECTORY_VARIABLE,
    SCREENSHOTS_VARIABLE,
    RunFolder,
    Timings,
    a_name_for,
)


class AnAnswer:
    """What a Jev response looks like to the recorder."""

    answers: ClassVar[dict] = {"next_action": {"choice": "wi_fi", "confidence": 0.8}}
    model = "jev-latest"
    input_tokens = 4200
    cost_usd = 0.0002


def a_step(index: int = 0, **overrides) -> Step:
    fields = {
        "index": index,
        "action": "wi_fi",
        "description": "tapped 'Wi-Fi'",
        "foreground_app": "com.android.settings",
        "confidence": 0.8,
        "probability": 0.85,
        "changed_the_screen": True,
        "timings": Timings(reading_the_screen=1.1, deciding=0.2, acting=0.05),
    }
    fields.update(overrides)
    return Step(**fields)


def a_report(*steps: Step) -> TaskReport:
    return TaskReport(
        goal="turn off bluetooth",
        achieved=False,
        reason="it used all 4 steps",
        steps=steps or (a_step(),),
        final_foreground_app="com.android.settings",
    )


@pytest.fixture(autouse=True)
def no_recording_by_default(monkeypatch):
    """Every test starts with recording off, whatever the machine's environment says."""
    monkeypatch.delenv(RUNS_DIRECTORY_VARIABLE, raising=False)
    monkeypatch.delenv(SCREENSHOTS_VARIABLE, raising=False)


# --- nothing is written unless it was asked for --------------------------


def test_no_folder_is_created_when_recording_is_off(tmp_path, monkeypatch):
    """The default has to be silent, or nobody leaves the feature switched on."""
    assert RunFolder.for_a_run("turn off bluetooth", root=None) is None
    assert list(tmp_path.iterdir()) == []


def test_setting_the_variable_turns_recording_on(tmp_path, monkeypatch):
    monkeypatch.setenv(RUNS_DIRECTORY_VARIABLE, str(tmp_path))

    folder = RunFolder.for_a_run("turn off bluetooth")

    assert folder is not None
    assert folder.directory.parent == tmp_path


def test_an_explicit_root_does_not_need_the_variable(tmp_path):
    """So a test or a script can record one run without changing the environment."""
    folder = RunFolder.for_a_run("open the calculator", root=tmp_path)

    assert folder is not None
    assert folder.directory.parent == tmp_path


# --- the name says what and when ----------------------------------------


def test_the_folder_name_carries_the_time_and_the_goal():
    name = a_name_for("Turn OFF Bluetooth!")

    assert name.endswith("turn-off-bluetooth")
    assert name[:4].isdigit()


def test_a_very_long_goal_is_shortened():
    name = a_name_for("open " + "very " * 40 + "long")

    assert len(name) < 70


def test_a_goal_with_nothing_usable_in_it_still_names_a_folder():
    assert a_name_for("!!!").split("-")[-1][:0] == ""


def test_two_runs_in_the_same_second_do_not_overwrite_each_other(tmp_path):
    first = RunFolder.for_a_run("open the clock", root=tmp_path)
    first.start("open the clock")
    second = RunFolder.for_a_run("open the clock", root=tmp_path)

    assert second.directory != first.directory
    assert first.directory.exists()


# --- what a folder holds -------------------------------------------------


def test_the_readme_says_what_the_files_are(tmp_path):
    """Whoever finds the folder may not be the person who turned recording on."""
    folder = RunFolder.for_a_run("turn off bluetooth", root=tmp_path)
    folder.start("turn off bluetooth", {"model": "jev-latest"})

    readme = (folder.directory / "README.txt").read_text()

    assert "turn off bluetooth" in readme
    assert "decisions.jsonl" in readme
    assert "replay_run.py" in readme
    assert "jev-latest" in readme


def test_each_request_is_appended_with_the_question_and_the_answer(tmp_path):
    folder = RunFolder.for_a_run("turn off bluetooth", root=tmp_path)
    folder.start("turn off bluetooth")
    state = {"goal": "turn off bluetooth", "on_screen": [{"label": "Bluetooth"}]}
    questions = {"next_action": {"type": "choice", "criteria": {"wi_fi": "tap Wi-Fi"}}}

    folder.note_exchange(state, questions, AnAnswer(), seconds=0.31)

    line = json.loads((folder.directory / "decisions.jsonl").read_text().strip())
    assert line["step"] == 0
    assert line["state"] == state
    assert line["questions"] == questions
    assert line["answers"] == AnAnswer.answers
    assert line["seconds"] == 0.31
    assert line["model"] == "jev-latest"


def test_requests_are_numbered_in_order(tmp_path):
    folder = RunFolder.for_a_run("turn off bluetooth", root=tmp_path)
    folder.start("turn off bluetooth")

    for _ in range(3):
        folder.note_exchange({}, {}, AnAnswer())

    steps = [
        json.loads(line)["step"]
        for line in (folder.directory / "decisions.jsonl").read_text().splitlines()
    ]
    assert steps == [0, 1, 2]


def test_a_step_is_appended_with_its_numbers_and_where_the_seconds_went(tmp_path):
    folder = RunFolder.for_a_run("turn off bluetooth", root=tmp_path)
    folder.start("turn off bluetooth")

    folder.note_step(0, a_step())

    line = json.loads((folder.directory / "steps.jsonl").read_text().strip())
    assert line["action"] == "wi_fi"
    assert line["changed_the_screen"] is True
    assert line["timings"]["reading_the_screen"] == 1.1
    assert line["timings"]["total"] == pytest.approx(1.35)


def test_the_report_is_written_last(tmp_path):
    """So a run that died still leaves every step up to the point it died."""
    folder = RunFolder.for_a_run("turn off bluetooth", root=tmp_path)
    folder.start("turn off bluetooth")
    folder.note_step(0, a_step())

    assert not (folder.directory / "run.json").exists()

    folder.finish(a_report())

    written = json.loads((folder.directory / "run.json").read_text())
    assert written["achieved"] is False
    assert written["run_folder"] == str(folder.directory)
    assert written["steps"][0]["action"] == "wi_fi"


def test_the_timing_summary_names_the_phases_and_the_worst_of_each(tmp_path):
    folder = RunFolder.for_a_run("turn off bluetooth", root=tmp_path)
    folder.start("turn off bluetooth")

    folder.finish(
        a_report(
            a_step(0, timings=Timings(reading_the_screen=1.0, deciding=0.2)),
            a_step(1, timings=Timings(reading_the_screen=3.0, deciding=0.4)),
        )
    )

    summary = (folder.directory / "timings.txt").read_text()
    assert "reading_the_screen" in summary
    assert "mean 2.000s" in summary
    assert "max 3.000s" in summary


def test_a_run_with_no_timings_says_so_rather_than_printing_nothing(tmp_path):
    folder = RunFolder.for_a_run("turn off bluetooth", root=tmp_path)
    folder.start("turn off bluetooth")

    folder.finish(a_report(a_step(timings=None)))

    assert "no timings" in (folder.directory / "timings.txt").read_text()


# --- and it must not break a run ----------------------------------------


def test_a_folder_that_cannot_be_written_to_does_not_stop_the_run(tmp_path):
    """Recording is for whoever reads it afterwards; the phone still has to be driven."""
    folder = RunFolder.for_a_run(
        "turn off bluetooth", root=tmp_path / "nope" / "deeper"
    )
    # A path whose parent is a file can never be created.
    (tmp_path / "blocked").write_text("not a directory")
    folder.directory = tmp_path / "blocked" / "run"

    folder.start("turn off bluetooth")
    folder.note_exchange({}, {}, AnAnswer())
    folder.note_step(0, a_step())
    folder.finish(a_report())

    # Nothing was written and nothing was raised, which is the whole contract.
    assert (tmp_path / "blocked").read_text() == "not a directory"


def test_a_screenshot_is_not_taken_unless_it_was_asked_for(tmp_path):
    """A picture costs more than everything else in a step put together."""
    folder = RunFolder.for_a_run("turn off bluetooth", root=tmp_path)
    folder.start("turn off bluetooth")

    class APhoneThatWouldNotBeAsked:
        def run(self, *arguments, **keywords):  # pragma: no cover
            raise AssertionError(
                "the phone was asked for a picture it was not meant to take"
            )

        def shell_binary(self, *arguments, **keywords):  # pragma: no cover
            raise AssertionError(
                "the phone was asked for a picture it was not meant to take"
            )

    folder.note_screen(0, APhoneThatWouldNotBeAsked())

    assert list(folder.directory.glob("*.jpg")) == []


def test_a_screenshot_that_fails_does_not_stop_the_run(tmp_path, monkeypatch):
    folder = RunFolder.for_a_run("turn off bluetooth", root=tmp_path)
    folder.start("turn off bluetooth")
    folder.keep_screenshots = True

    class APhoneThatRefuses:
        def shell_binary(self, *arguments, **keywords):
            raise RuntimeError("the phone said no")

    folder.note_screen(1, APhoneThatRefuses())

    assert list(folder.directory.glob("*.jpg")) == []
