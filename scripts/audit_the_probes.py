#!/usr/bin/env python
"""Find probes that cannot fail, or cannot pass.

Three rounds of scenario triage in this project each ended at a probe rather than at the
loop, and every one was found by hand while chasing something else:

- a probe that asked for a query *not* to be in the address bar, which is true of
  Chrome's home screen, so it reported the goal met from the first step of a run that
  had not searched;
- `screen_holds_nothing_unsent`, which an empty search box satisfies before anything is
  typed - still in place in youtube-search, with no replacement found;
- `the_window_is("SearchResults")`, a window this device never brings to the front, so
  the scenario could not pass however well the run did.

A probe is the cheapest thing here to get wrong and the most expensive to notice, because
it presents as a scenario failing. So this looks for the three shapes by machine.

    scripts/audit_the_probes.py            # what it found, worst first
    scripts/audit_the_probes.py --all      # every scenario, not just the flagged ones

It reads the catalogue and the recorded runs. It does not touch a phone, and it does not
decide anything: every line it prints is a question for a person.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CATALOGUE = ROOT / "tests" / "scenarios" / "catalogue.py"
ARTIFACTS = ROOT / "artifacts" / "scenarios"

# Probes that are satisfied by something being *absent*, and absent is the state before
# anything has happened.
AN_ABSENCE = (
    "screen_does_not_show(",
    "screen_holds_nothing_unsent(",
    "no_field_holds(",
)

# Probes that name a window the phone must bring to the front.
A_WINDOW = re.compile(r'the_window_is\(\s*"([^"]+)"')


def the_scenarios() -> dict[str, str]:
    """Each scenario's source block, by id."""
    source = CATALOGUE.read_text(encoding="utf-8")
    found = {}
    for block in re.split(r"\n    Scenario\(", source)[1:]:
        match = re.search(r'id="([^"]+)"', block)
        if match:
            found[match.group(1)] = block
    return found


def the_windows_ever_seen() -> set[str]:
    """Every window name any recorded run has been in front of."""
    seen: set[str] = set()
    for decisions in ARTIFACTS.glob("*/run/decisions.jsonl"):
        for line in decisions.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                window = (json.loads(line).get("state") or {}).get("foreground_window")
            except ValueError:
                continue
            if window:
                seen.add(str(window))
    return seen


def started_finished(scenario: str) -> str:
    """What the first reading of the last run said, when it already satisfied the goal."""
    run_json = ARTIFACTS / scenario / "run.json"
    if not run_json.is_file():
        return ""
    stages = json.loads(run_json.read_text(encoding="utf-8")).get("stages") or []
    if stages and stages[0].get("goal_met_here"):
        return str(stages[0].get("because") or "")
    return ""


def what_is_wrong_with(scenario: str, block: str, windows: set[str]) -> list[str]:
    complaints = []

    for helper in AN_ABSENCE:
        if helper in block:
            complaints.append(
                f"asks for something to be ABSENT ({helper.rstrip('(')}), which is true "
                "before the work starts - check it against the screen the scenario begins on"
            )

    for named in A_WINDOW.findall(block):
        if windows and not any(
            named.casefold() in window.casefold() for window in windows
        ):
            complaints.append(
                f"looks for a window called {named!r}, which no recorded run has ever "
                f"been in front of ({len(windows)} window names seen) - it may be a probe "
                "that cannot pass"
            )

    met_from_the_start = started_finished(scenario)
    if met_from_the_start:
        complaints.append(
            f"starts in its own end state (the harness flags this too): {met_from_the_start[:70]}"
        )

    return complaints


def main(argv: list[str]) -> int:
    everything = "--all" in argv
    scenarios = the_scenarios()
    windows = the_windows_ever_seen()
    if not windows:
        print("no recorded runs to check window names against; run the scenarios first")

    flagged = 0
    for name, block in sorted(scenarios.items()):
        complaints = what_is_wrong_with(name, block, windows)
        if not complaints and not everything:
            continue
        if complaints:
            flagged += 1
        print(f"{name}")
        for complaint in complaints or ["nothing found"]:
            print(f"    - {complaint}")

    print()
    print(f"{flagged} of {len(scenarios)} scenarios have something to answer for")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
