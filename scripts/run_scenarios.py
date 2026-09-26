"""Run the scenario catalogue against a real phone and report what happened.

Each scenario is prepared, run, sampled at every stage, and judged. The output
says which scenarios were fulfilled and, for the ones that were not, which part
of the exchange was wrong - the state sent, the options offered, the reply, or
what the loop did about it.

    .venv/bin/python scripts/run_scenarios.py --tier 1
    .venv/bin/python scripts/run_scenarios.py --id open-settings --id bluetooth-off
    .venv/bin/python scripts/run_scenarios.py --all

Artefacts land in ``artifacts/scenarios/<scenario-id>/``: a screenshot per stage,
the full decision log, and a readable report. Nothing here writes to the phone
that a scenario did not already ask for.
"""

from __future__ import annotations

import argparse
import sys
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

from phone_control.adb import AndroidPhone  # noqa: E402
from phone_control.device_state import read_device_state  # noqa: E402
from phone_control.jev import JevClient  # noqa: E402
from scenarios.catalogue import SCENARIOS, Scenario  # noqa: E402
from scenarios.harness import (  # noqa: E402
    AScenarioThatCannotBeJudged,
    ScenarioRun,
    run_a_scenario,
)

ARTIFACTS = ROOT / "artifacts" / "scenarios"


def chosen(args: argparse.Namespace) -> list[Scenario]:
    """The scenarios this run was asked for."""
    if args.all or (not args.tier and not args.id):
        return list(SCENARIOS)
    wanted: list[Scenario] = []
    if args.tier:
        wanted.extend(
            scenario for scenario in SCENARIOS if scenario.tier in set(args.tier)
        )
    if args.id:
        by_id = {scenario.id: scenario for scenario in SCENARIOS}
        for name in args.id:
            if name not in by_id:
                raise SystemExit(f"no scenario called {name!r}")
            wanted.append(by_id[name])
    # Keep catalogue order, and never run one twice.
    unique = {scenario.id: scenario for scenario in wanted}
    return [scenario for scenario in SCENARIOS if scenario.id in unique]


def a_usable_phone() -> AndroidPhone:
    """The phone, checked before anything is attempted against it."""
    try:
        phone = AndroidPhone()
        if not phone.is_connected():
            raise SystemExit("no Android phone is attached over USB")
    except SystemExit:
        raise
    except Exception as error:
        raise SystemExit(f"the phone could not be reached: {error}") from error

    state = read_device_state(phone)
    if state.is_screen_on is False:
        phone.run(["shell", "input", "keyevent", "224"])
    phone.run(["shell", "input", "keyevent", "224"])
    state = read_device_state(phone)
    if state.is_locked:
        raise SystemExit("the phone is locked; unlock it and run this again")
    return phone


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--all", action="store_true", help="every scenario")
    parser.add_argument("--tier", type=int, action="append", help="a tier to run")
    parser.add_argument("--id", action="append", help="a scenario id to run")
    parser.add_argument("--quiet", action="store_true", help="only the summary")
    args = parser.parse_args()

    scenarios = chosen(args)
    if not scenarios:
        raise SystemExit("nothing to run")

    phone = a_usable_phone()
    client = JevClient()
    if not client.is_configured:
        raise SystemExit("no Jev key is configured, so no scenario can be decided")

    print(f"running {len(scenarios)} scenario(s) against {phone.serial_number()}")
    print(f"artefacts: {ARTIFACTS}\n")

    runs: list[ScenarioRun] = []
    skipped: list[tuple[str, str]] = []
    crashed: list[tuple[str, str]] = []
    for scenario in scenarios:
        try:
            run = run_a_scenario(scenario, phone, client, ARTIFACTS)
        except AScenarioThatCannotBeJudged as cannot:
            skipped.append((scenario.id, str(cannot)))
            print(f"  {scenario.id:<34} tier {scenario.tier}  NOT JUDGED")
            continue
        except Exception as error:
            # A scenario that crashes is a finding, and it must not cost the rest of
            # the tier: losing nine results to the tenth is how a run hides what it
            # learned. Reported with its traceback and carried on past.
            crashed.append((scenario.id, f"{type(error).__name__}: {error}"))
            print(f"  {scenario.id:<34} tier {scenario.tier}  CRASHED")
            traceback.print_exc()
            continue
        runs.append(run)
        verdict = "fulfilled" if run.fulfilled else "FAILED"
        print(f"  {scenario.id:<34} tier {scenario.tier}  {verdict}")
        if not args.quiet and not run.fulfilled:
            for line in run.as_report().splitlines():
                print(f"    {line}")

    fulfilled = [run for run in runs if run.fulfilled]
    print(f"\n{len(fulfilled)}/{len(runs)} fulfilled")

    by_tier: dict[int, list[ScenarioRun]] = {}
    for run in runs:
        by_tier.setdefault(run.scenario.tier, []).append(run)
    for tier, items in sorted(by_tier.items()):
        done = sum(1 for run in items if run.fulfilled)
        print(f"  tier {tier}: {done}/{len(items)}")

    if crashed:
        print(f"\n{len(crashed)} scenario(s) crashed:")
        for name, why in crashed:
            print(f"  {name}: {why}")

    if skipped:
        print(f"\n{len(skipped)} scenario(s) could not be judged here:")
        for name, reason in skipped:
            print(f"  {name}: {reason.splitlines()[0]}")

    if fulfilled != runs:
        print("\nstill failing:")
        for run in runs:
            if run.fulfilled:
                continue
            print(f"  {run.scenario.id}")
            for complaint in run.complaints:
                print(f"    - {complaint}")

    return 0 if len(fulfilled) == len(runs) and not crashed else 1


if __name__ == "__main__":
    raise SystemExit(main())
