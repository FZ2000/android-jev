# Contributing

Thanks for looking. This is a small project with an unusually strict idea of what
counts as a passing test, so most of this file is about that.

## Getting set up

```bash
uv venv --python 3.12 .venv
uv pip install --python .venv/bin/python -e '.[dev]'
scripts/check.sh --as-ci                           # what CI runs: no phone, no key
scripts/install_git_hooks.sh                       # refuses a red commit
```

`scripts/check.sh --as-ci` runs the fast suite as a machine that has never been set
up — no adb on the path, no decision-model key, no home directory holding either —
which is what the runner is. Run it before pushing. It exists because the suite once
passed on a developer's machine for weeks while main was red: the machine had adb,
the runner did not, and a test that reached for adb failed there instead of testing
what it claimed.

Maintainers: what protects `main`, and what GitHub refuses until this repository is
public, is in [`docs/repository-settings.md`](docs/repository-settings.md).

With a phone attached, `scripts/check.sh` runs both suites and enforces the 95%
coverage gate. That is the gate CI cannot run, because a hosted runner has no phone,
and it cannot be given one — see decision 12 in
[`docs/engineering-decisions.md`](docs/engineering-decisions.md).

## The rules the tests live by

These are not style preferences. Each one exists because breaking it cost this
project real time, and each is enforced by a test rather than by good intentions.

**No test may be marked as an expected failure.** `tests/test_no_xfail.py` fails the
build on one. A test allowed to fail reports green while the behaviour it describes
does not work, and the report is the whole product here.

**An assertion has to be able to fail.** `assert True`, `x is not None or True` and
a probe that cannot fail are worse than no test: they read as coverage. If you find
yourself writing one, the test is telling you its claim is unsettled — settle it.

**A probe must be able to be wrong.** Every scenario probe is written so that a run
which did not do the work fails it. A probe that asks for something being *absent*
("the address bar does not hold the query") is satisfied before the work starts, and
`scripts/audit_the_probes.py` looks for that shape and two others.

**A test that asserts the bug is worse than a missing test.** When the behaviour
changes on purpose, update the test to assert the new claim *and say why in it*. Git
history is full of tests here that passed for a year by asserting the wrong thing.

## Adding a scenario

`tests/scenarios/catalogue.py` holds the usage scenarios, in tiers by difficulty.
A scenario needs a goal, a way to judge it, and a starting state that does not
already satisfy it. The harness refuses to count one that begins in its own end
state, because such a scenario cannot fail.

Run one with `.venv/bin/python scripts/run_scenarios.py --id <id>`, and read
`docs/debugging.md` before believing a failure — several were the probe, not the
loop, and the harness now says so in capitals.

## Recording a transcript

`tests/recorded/` holds real device output replayed by the default suite. Record
with `scripts/record_transcripts.py` and a phone attached. It redacts this device's
identifiers by asking the device what they are, and a test enforces that nothing
identifying survives. One thing no rule can catch is a display name, so **read what
you recorded before committing it** and set
`PHONE_CONTROL_TRANSCRIPT_REDACTIONS` for anything a rule cannot find.

## Code

Plain-English names, one implementation per idea, and no logic that special-cases a
particular app or phone. Where a device fact is unavoidable — a package name, a
window title, a Settings row — put it in a table with a general fallback and say what
the fallback is, rather than branching on it.

Comments explain why, not what. If a paragraph of comment describes a measurement,
keep the number in it: the numbers are the reason the code looks odd, and without
them somebody will helpfully simplify it back into a bug.

### Style, and the two commands that settle it

```bash
uv run ruff format .        # writes the layout
uv run ruff check .         # the rules, with the reasoning in pyproject.toml
```

Both are enforced in CI, so a pull request that skips them comes back red. The ruff
version is fixed by `uv.lock`, which matters more than it sounds: run whatever your
machine has and a formatter upgrade can reformat the repository in a pull request
that was supposed to change one line.

The rule selection is deliberate rather than "everything", because a rule that fires
on a matter of taste teaches people to ignore the output. What it does enforce is the
kind of thing this project has been bitten by: an unused import, a `raise` inside an
`except` that drops the cause, an assertion that cannot fail on its own.

### What CI runs, and what it cannot

Three jobs and a gate: lint and format, the fast suite on every Python version
`requires-python` claims, and the built wheel installed somewhere outside the checkout
to prove the packaging is not just working because `pythonpath` says so. `ci-gate`
aggregates them into the single check that branch protection requires.

The device suite is not in CI and cannot be. It needs the phone, and the scenarios are
written against one with Google's applications on it, so an emulator would fail them
for the absence of Chrome rather than for a defect here. Run `scripts/check.sh` on the
machine with the phone; that is where the 95% coverage gate is read.
