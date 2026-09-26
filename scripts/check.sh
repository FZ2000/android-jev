#!/usr/bin/env bash
#
# The gate: everything that can be run, run, with coverage.
#
# Two suites, because they answer different questions and one cannot stand in
# for the other. The fast suite is pure logic and runs on any machine. The device
# suite asks the phone, and is the only one that can: a fake can only ever repeat
# what its author believed, and the fake that agreed a bare domain opens a
# browser was believed for months.
#
# Coverage is read from both together and has to clear 95%, because most of this
# package only executes with a phone attached. Running coverage over the fast
# suite alone reports 81% and fails a gate on code it had no way to reach.
#
# pytest is invoked as a program rather than as `python -m pytest`. The two differ
# in whether the working directory lands on sys.path, and therefore in whether a
# test that imports something from the repository root passes here. Continuous
# integration uses the program, so this does too — and main went red once already
# for exactly that reason.
#
#   scripts/check.sh              everything, with the 95% gate
#   scripts/check.sh --fast       skip the phone, no gate (for a machine with none)
#   scripts/check.sh --as-ci      what continuous integration runs, on this machine
#   scripts/check.sh --scenarios  also run all 47 usage scenarios (slow)
#
set -euo pipefail

cd "$(dirname "$0")/.."

PYTHON=".venv/bin/python"
PYTEST=".venv/bin/pytest"
RUFF=".venv/bin/ruff"
if [ ! -x "$PYTHON" ] || [ ! -x "$PYTEST" ] || [ ! -x "$RUFF" ]; then
    echo "no virtualenv at $PYTHON; create one and install '.[dev]'" >&2
    exit 2
fi

FAST_ONLY=0
WITH_SCENARIOS=0
AS_CI=0
for argument in "$@"; do
    case "$argument" in
        --fast) FAST_ONLY=1 ;;
        --as-ci) AS_CI=1 ;;
        --scenarios) WITH_SCENARIOS=1 ;;
        *) echo "unknown option: $argument" >&2; exit 2 ;;
    esac
done

# Make this machine look like the one continuous integration runs on: a machine
# that has never been set up. No Android tools, no decision-model key, no home
# directory of its own to keep either in.
#
# This exists because the suite passed here for weeks while main was red there.
# This machine has adb and a key; a runner has neither, so a test that reaches for
# adb failed on the runner and passed here, and nothing said so. Reproducing the
# bare conditions locally is what makes "green here" mean "green there".
as_a_machine_that_has_never_been_set_up() {
    BARE_HOME="$(mktemp -d)"
    trap 'rm -rf "$BARE_HOME"' EXIT
    export HOME="$BARE_HOME"
    unset OPENROUTER_API_KEY DSH_HOME PHONE_CONTROL_ADB || true

    # Every directory holding an adb is dropped from the path, rather than
    # guessing where a reader keeps theirs. A home directory of its own already
    # covers the copies adb is looked for inside one.
    local kept="" directory
    while IFS= read -r directory; do
        if [ -z "$directory" ] || [ -x "$directory/adb" ]; then
            continue
        fi
        kept="${kept:+$kept:}$directory"
    done < <(printf '%s\n' "$PATH" | tr ':' '\n')
    export PATH="$kept"

    if command -v adb >/dev/null 2>&1; then
        echo "an adb is still reachable, so this run would not reproduce CI" >&2
        exit 2
    fi
}

# Both of these run before any suite, in every mode, because they are seconds and
# because a lint failure is not worth discovering after a two-minute test run.
the_lint_is_clean() {
    echo "== linted and formatted, by the ruff uv.lock fixes =="
    "$RUFF" check .
    "$RUFF" format --check .
}

if [ "$AS_CI" = 1 ]; then
    if [ "$FAST_ONLY" = 1 ] || [ "$WITH_SCENARIOS" = 1 ]; then
        echo "--as-ci is what CI runs, so it does not combine with --fast or --scenarios" >&2
        exit 2
    fi
    as_a_machine_that_has_never_been_set_up
    the_lint_is_clean
    echo "== as continuous integration sees it: no adb, no key, no home directory =="
    echo "== the rule first, then the fast suite =="
    "$PYTEST" tests/test_no_xfail.py -q
    exec "$PYTEST" -q -m "not device"
fi

if [ "$FAST_ONLY" = 1 ]; then
    the_lint_is_clean
    echo "== the fast suite, no phone, no coverage gate =="
    exec "$PYTEST" -q -m "not device"
fi

the_lint_is_clean

echo "== every test that can run here, with the coverage gate =="
IGNORED=()
if [ "$WITH_SCENARIOS" = 0 ]; then
    # The usage scenarios take roughly half an hour on their own. They are the
    # specification rather than the unit suite, and scripts/run_scenarios.py is
    # the better way to work through them.
    IGNORED+=(--ignore=tests/test_scenarios_on_the_phone.py)
fi

"$PYTEST" -q "${IGNORED[@]}" --cov=phone_control --cov-report=term-missing
