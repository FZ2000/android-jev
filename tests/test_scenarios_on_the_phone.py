"""Every scenario in the catalogue, run against a real phone.

The catalogue is the specification: each entry says what to ask for, where to
start, and how to tell from the phone itself that it happened. This file only
runs them and reports the verdict, so adding a scenario is adding a line to
``scenarios/catalogue.py`` and nothing else.

Run them with a phone attached and a key configured:

    .venv/bin/python -m pytest -m "device and jev" -q -k scenarios

Each failure prints the state sent, the options offered, the reply, and the stage
screenshots, so a red test says what to fix rather than only that something is
wrong. ``scripts/run_scenarios.py`` does the same thing with a nicer summary and
is the quicker way to work through a tier.
"""

from __future__ import annotations

import pytest

from scenarios.catalogue import SCENARIOS
from scenarios.harness import AScenarioThatCannotBeJudged, run_a_scenario

pytestmark = [pytest.mark.device, pytest.mark.jev]


@pytest.fixture(scope="session")
def artifacts(tmp_path_factory) -> object:
    """Where the screenshots and decision logs for this session go."""
    return tmp_path_factory.mktemp("scenarios")


@pytest.mark.parametrize("scenario", SCENARIOS, ids=lambda one: one.id)
def test_the_scenario_is_fulfilled(scenario, phone, jev, artifacts, record_property):
    try:
        run = run_a_scenario(scenario, phone, jev, artifacts)
    except AScenarioThatCannotBeJudged as cannot:
        pytest.skip(str(cannot))

    record_property("goal", scenario.goal)
    record_property("claimed", run.report.achieved)
    record_property("phone_says", run.final_verdict.because)
    record_property("artifacts", str(run.directory))

    assert run.fulfilled, f"\n{run.as_report()}\n\nartefacts: {run.directory}\n"
