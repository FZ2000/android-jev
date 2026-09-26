"""Running one scenario and judging everything about it.

A pass or a fail is not enough to act on. This runs a scenario and keeps the
whole conversation: what the phone looked like at every stage as a screenshot and
as a ground-truth reading, what was sent to the decider, what came back, and what
the loop did about it. Then it reports each of those separately, so a failure
points at the state, the options, the reply or the execution instead of only
saying the goal was missed.

Both directions are checked, because both have been wrong here before. A run that
claims success over an untouched phone is the worse failure, and a run that
reports failure over a phone that plainly did the job is the other one - two
photo scenarios failed that way, and the Bluetooth scenario failed a phone that
was working perfectly.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from phone_control.decisions import Exchange, RecordingDecider
from phone_control.errors import JevRequestFailed
from phone_control.goal import JevDecider, TaskReport, run_task
from phone_control.reader import MODEL_VARIABLE as READER_MODEL_VARIABLE
from phone_control.runs import RunFolder
from phone_control.screenshots import capture_screen

from .catalogue import Scenario
from .checks import complaints_about
from .ground_truth import Captured, Verdict
from .starting_states import awake_and_unlocked

# Small enough that a whole run of every scenario is a browsable pile of images
# rather than something nobody will open.
SCREENSHOT_WIDTH = 420


class AScenarioThatCannotBeJudged(RuntimeError):
    """A scenario whose goal the loop cannot conclude without a reader.

    Raised rather than failed, and it carries the measurement: the phone does what
    was asked and the screen does not say so, so a red result would be reporting a
    platform limit as a bug in the loop.
    """


@dataclass(frozen=True)
class Stage:
    """What the phone looked like at one point in the run.

    ``after_step`` is the step that had just finished, so 0 means before the run
    began. ``verdict`` is the scenario's own ground truth read at that moment,
    and ``required`` holds the verdict of each state the scenario insists must be
    visible at some stage. Reading them all per stage is what makes a
    stage-by-stage claim possible rather than only an end-of-run one.
    """

    after_step: int
    when: str
    foreground: str
    verdict: Verdict
    required: tuple[Verdict, ...]
    screenshot: Path | None
    # The loop's own index for the reading this stage is of, copied from the state the
    # decider was given - and None for a stage that no decision was made from, such as
    # the one taken before the run and the one taken after it.
    #
    # This is the key that joins a stage to its decision. Without it the two records
    # number themselves independently - the stage counter counts samples and the
    # decision counter counts decisions - and `stages[i]` does not describe the screen
    # `decisions[i]` was decided from. Measured on open-calculator: the decision that
    # scored 0.96 and ended a fulfilled run was paired that way with a stage saying the
    # goal was not met, and a corpus harvested on that pairing said the completion
    # question was anti-correlated with the truth (docs/debugging.md).
    observation: int | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "after_step": self.after_step,
            "when": self.when,
            "observation": self.observation,
            "foreground": self.foreground,
            "goal_met_here": self.verdict.met,
            "because": self.verdict.because,
            "required_states_met_here": [one.met for one in self.required],
            "screenshot": self.screenshot.name if self.screenshot else None,
        }


@dataclass(frozen=True)
class ScenarioRun:
    """One scenario, everything it did, and what was wrong with it."""

    scenario: Scenario
    report: TaskReport
    stages: tuple[Stage, ...]
    exchanges: tuple[Exchange, ...]
    complaints: tuple[str, ...]
    directory: Path

    @property
    def final_verdict(self) -> Verdict:
        return self.stages[-1].verdict if self.stages else Verdict(False, "never read")

    @property
    def ever_met(self) -> bool:
        return any(stage.verdict.met for stage in self.stages)

    @property
    def fulfilled(self) -> bool:
        return not self.complaints

    def as_dict(self) -> dict[str, Any]:
        return {
            "scenario": self.scenario.id,
            "tier": self.scenario.tier,
            "goal": self.scenario.goal,
            "reachable": self.scenario.reachable,
            "fulfilled": self.fulfilled,
            "claimed_achieved": self.report.achieved,
            "reason": self.report.reason,
            "goal_met_at_the_end": self.final_verdict.met,
            "goal_met_at_some_stage": self.ever_met,
            "final_foreground": self.report.final_foreground_app,
            "steps": len(self.report.steps),
            "complaints": list(self.complaints),
            "stages": [stage.as_dict() for stage in self.stages],
        }

    def as_report(self) -> str:
        """The lines a person reads to understand the run."""
        lines = [
            f"{self.scenario.id}  (tier {self.scenario.tier})  "
            f"{'FULFILLED' if self.fulfilled else 'FAILED'}",
            f"  goal      : {self.scenario.goal}",
            f"  claimed   : {self.report.achieved}  ({self.report.reason})",
            f"  phone says: {self.final_verdict.because}",
            f"  artefacts : {self.directory}",
        ]
        for stage in self.stages:
            mark = "met" if stage.verdict.met else "   "
            required = "".join(" yes" if one.met else " no" for one in stage.required)
            lines.append(
                f"    {stage.when:<16} [{mark}] {stage.foreground or '(none)':<34}"
                f" {stage.verdict.because}"
                + (f"   required:{required}" if stage.required else "")
            )
        for step in self.report.steps:
            numbers = ""
            if step.confidence is not None:
                numbers += f" conf={step.confidence:.2f}"
            if step.probability is not None:
                numbers += f" prob={step.probability:.2f}"
            lines.append(
                f"    step {step.index}: {step.action:<18}{numbers} "
                f"{step.description[:52]}"
            )
        if self.complaints:
            lines.append("  what was wrong:")
            lines.extend(f"    - {complaint}" for complaint in self.complaints)
        return "\n".join(lines)


def a_reader_is_configured() -> bool:
    """Whether the hand-off has a reader, which some goals need to be concluded."""
    from phone_control.reader import reader_from_the_environment

    return bool(reader_from_the_environment().is_configured)


def run_a_scenario(
    scenario: Scenario,
    phone,
    client,
    artifacts: Path,
    max_steps: int | None = None,
) -> ScenarioRun:
    """Prepare, run, sample every stage, and judge the result."""
    if scenario.the_effect_is_off_the_screen:
        raise AScenarioThatCannotBeJudged(
            f"{scenario.id} cannot be judged from the screen at all: {scenario.note}. "
            "The phone may well do it; nothing readable says whether it did."
        )

    if scenario.needs_a_reader and not a_reader_is_configured():
        # Not a failure of the loop and not a pass either. The phone does what was
        # asked; the screen never says so, because Android does not publish a
        # settings toggle's state to the accessibility tree - measured, and written
        # up in docs/android-apis.md. The reader is what concludes it.
        raise AScenarioThatCannotBeJudged(
            f"{scenario.id} needs a reader to know the goal is met "
            f"({scenario.note}). Set {READER_MODEL_VARIABLE}, or run the scenarios "
            "that do not need one."
        )

    why_not = awake_and_unlocked(phone)
    if why_not:
        # A precondition, not a result. Every scenario establishes its own starting
        # state; that it can be read at all is the floor under all of them.
        raise AScenarioThatCannotBeJudged(
            f"{scenario.id} cannot run: {why_not}. Unlock the phone and run again."
        )

    directory = artifacts / scenario.id
    directory.mkdir(parents=True, exist_ok=True)

    scenario.prepare(phone)
    before: Captured = scenario.capture(phone)

    stages: list[Stage] = []
    # The stage before anything happens, which also proves the scenario did not
    # start already finished. A scenario that begins in its own end state
    # measures nothing, and would pass on a loop that did nothing at all.
    stages.append(
        _sample(
            scenario,
            phone,
            before,
            after_step=0,
            when="before the run",
            number=0,
            directory=directory,
        )
    )

    # A wire-level record as well as the loop-level one, so a scenario step can be
    # re-decided offline with scripts/replay_run.py. The two are kept apart on
    # purpose: `exchanges.jsonl` is what the loop asked its decider, and
    # `run/decisions.jsonl` is what went to Jev, questions included.
    # A fresh record for this run. A fixed path appended across runs stacks two
    # runs' steps on top of each other with the numbering starting again, which makes
    # the one artefact a failure is diagnosed from actively misleading - and it did,
    # for a whole diagnosis, before being noticed.
    recording = RunFolder(directory=a_fresh_run_folder(directory / "run"))
    recording.start(scenario.goal, {"scenario": scenario.id, "tier": scenario.tier})
    client.on_exchange = recording.note_exchange

    def sampling_choose(state: dict[str, Any]) -> Any:
        # Called at the top of every step, so this reads the phone once the
        # previous action has settled: one sample per stage, exactly.
        step_number = len(stages)
        # The loop puts its own index for this reading in the state, and it is the only
        # thing the two records agree on: `after_step` counts stages, and the decisions
        # are numbered by a counter of their own. Copied here so a stage can be joined
        # to the decision made from it without counting either.
        stages.append(
            _sample(
                scenario,
                phone,
                before,
                after_step=step_number,
                when=f"before step {step_number}",
                number=step_number,
                directory=directory,
                observation=state.get("observation"),
            )
        )
        return JevDecider(client).choose(state)

    recorder = RecordingDecider(sampling_choose)
    try:
        report = _drive(
            phone,
            scenario,
            recorder,
            max_steps or scenario.max_steps,
            recording=recording,
        )
    except JevRequestFailed as lost:
        # A dropped connection is not a result about this scenario. Left as a crash it
        # reads as one and takes the scenario out of the tally entirely - measured on a
        # full run where a tier-1 scenario vanished this way and the summary read 31/44
        # with nothing said about why the denominator had moved. It is the same class
        # the unit tests tolerate through `a_goal_that_needs_the_network` in conftest,
        # and the scenarios were simply never given the same treatment.
        raise AScenarioThatCannotBeJudged(
            f"{scenario.id} could not be judged because the network carrying its "
            f"decisions dropped part-way through: {lost}"
        ) from lost
    finally:
        client.on_exchange = None

    # One more reading, after the loop has stopped, so the end state is its own
    # stage rather than an inference from the last step.
    stages.append(
        _sample(
            scenario,
            phone,
            before,
            after_step=len(report.steps),
            when="after the run",
            number=len(stages),
            directory=directory,
        )
    )

    complaints = _judge(scenario, report, recorder.exchanges, stages)

    run = ScenarioRun(
        scenario=scenario,
        report=report,
        stages=tuple(stages),
        exchanges=tuple(recorder.exchanges),
        complaints=tuple(complaints),
        directory=directory,
    )
    recording.finish(report)
    recorder.write_to(directory / "exchanges.jsonl")
    (directory / "run.json").write_text(
        json.dumps(run.as_dict(), indent=2, default=str), encoding="utf-8"
    )
    (directory / "report.txt").write_text(run.as_report() + "\n", encoding="utf-8")
    return run


def _drive(
    phone,
    scenario: Scenario,
    recorder: RecordingDecider,
    max_steps: int,
    recording: RunFolder | None = None,
):
    import asyncio

    return asyncio.run(
        run_task(
            phone,
            scenario.goal,
            recorder.choose,
            max_steps=max_steps,
            recording=recording,
        )
    )


def _sample(
    scenario: Scenario,
    phone,
    before: Captured,
    after_step: int,
    when: str,
    number: int,
    directory: Path,
    observation: int | None = None,
) -> Stage:
    """Read the ground truth, the required stages, and the picture at one stage."""
    from phone_control.device_state import read_focused_window

    verdict = scenario.looks_done(phone, before)
    required = tuple(one.probe(phone, before) for one in scenario.also_seen)
    foreground = read_focused_window(phone).package or ""
    # Numbered by position rather than by step, so the picture taken after the
    # run cannot overwrite the one taken before the last step of it.
    screenshot = _screenshot(phone, directory / f"stage-{number:02d}.jpg")
    return Stage(
        after_step=after_step,
        when=when,
        foreground=foreground,
        verdict=verdict,
        required=required,
        screenshot=screenshot,
        observation=observation,
    )


def a_fresh_run_folder(directory: Path) -> Path:
    """The run folder, emptied of whatever an earlier run of this scenario left."""
    import shutil

    if directory.exists():
        shutil.rmtree(directory, ignore_errors=True)
    return directory


def _screenshot(phone, path: Path) -> Path | None:
    """Save a picture of this stage. A failure nobody can look at is half a report."""
    try:
        image_bytes, _mime = capture_screen(phone, max_width=SCREENSHOT_WIDTH)
    except Exception:
        return None
    path.write_bytes(image_bytes)
    return path


def _judge(
    scenario: Scenario,
    report: TaskReport,
    exchanges: list[Exchange],
    stages: list[Stage],
) -> list[str]:
    """Everything wrong with this run, in the order it matters."""
    complaints: list[str] = []
    final = stages[-1].verdict

    # --- did the goal happen, and did the run tell the truth about it? ---

    if scenario.reachable:
        if not final.met:
            complaints.append(f"the goal was never reached: {final.because}")
        if not report.achieved:
            complaints.append(
                f"the run reported failure over a goal that "
                f"{'was reached' if final.met else 'was not reached'}: {report.reason}"
                + (
                    "  -- CHECK THE PROBE FIRST: this is either a run that "
                    "under-claimed or a probe that is too lax, and the probe is the "
                    "cheaper mistake to have made. Three rounds of triage went into "
                    "loop changes for runs that had genuinely not reached their goal, "
                    "because this message reads as a loop failure and was not."
                    if final.met
                    else ""
                )
            )
    else:
        if report.achieved:
            complaints.append(
                "the run claimed to have achieved something no phone can do: "
                f"{report.reason}"
            )
        if final.met:
            complaints.append(f"the impossible goal reads as met: {final.because}")

    # --- states that had to be visible at some stage ---

    for position, required in enumerate(scenario.also_seen):
        if not any(stage.required[position].met for stage in stages):
            complaints.append(
                f"this scenario requires seeing {required.what!r} at some stage, "
                f"and that stage never happened"
            )

    # --- the very first stage: a scenario must not start finished ---
    #
    # Only a scenario with *nothing else to observe* can pass on its first reading
    # alone. One that also requires an intermediate state - the bluetooth scenario
    # must be seen off on the way to being on again - cannot pass without doing the
    # work, however its first reading looks, because that state has to be witnessed.
    #
    # The condition was the other way round: it fired only on scenarios that *had*
    # intermediate requirements, which are the ones that cannot pass trivially, and it
    # stayed silent on the ones that could. Measured over a full catalogue run, it
    # flagged both toggles and said nothing about `alarm-for-seven` or
    # `files-browse-downloads` - where the first reading already satisfies the goal and
    # there is nothing else to observe. An alarm at 7am and a control named "Downloads"
    # are both left on this phone by earlier runs.
    if scenario.reachable and not scenario.also_seen and stages[0].verdict.met:
        complaints.append(
            "this scenario starts in its own end state and has nothing else to "
            "observe, so it could pass without doing anything: "
            f"{stages[0].verdict.because}"
        )

    # --- every exchange, one at a time ---

    for exchange in exchanges:
        step = (
            report.steps[exchange.index] if exchange.index < len(report.steps) else None
        )
        controls = exchange.state.get("on_screen")
        for complaint in complaints_about(
            exchange, step, len(controls) if isinstance(controls, list) else None
        ):
            complaints.append(str(complaint))

    # --- the run has to have moved the phone at all ---

    if (
        scenario.reachable
        and exchanges
        and report.steps
        and not any(step.changed_the_screen for step in report.steps)
    ):
        if all(step.changed_the_screen is None for step in report.steps):
            complaints.append(
                "no step's effect could be read at all, so nothing the run did can be "
                "judged"
            )
        else:
            complaints.append(
                "no step changed the screen, so nothing the run did had any effect"
            )

    return complaints
