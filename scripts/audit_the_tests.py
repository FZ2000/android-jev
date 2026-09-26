#!/usr/bin/env python3
"""Find tests that cannot fail, and other ways a passing suite lies.

The failure mode this project keeps meeting is not a missing test. It is a test that
exists, runs, reports green, and checks nothing — an assertion that cannot be false,
a test whose body is a call and nothing else, a skip with no reason that quietly
sits out every run. Those are worse than no test, because they get counted.

This reads the test suite as syntax, not as text, so it can tell an assertion from a
string that contains the word "assert". Four shapes are reported:

  * **no assertion at all** - the test cannot fail, whatever the code does. A call
    to `pytest.raises`, `pytest.skip`, `pytest.fail` or a helper that raises counts
    as checking something, so the check is written against what the function can do
    rather than against the literal word `assert`.
  * **an assertion about a constant** - `assert True`, `assert 1`, `assert "x"`.
    Always true, so it tests the parser rather than the code.
  * **`is not None` on its own** - the weakest assertion there is, because it
    claims only that something came back. `is None` is not reported: that is a
    value, and "refuses" or "matches nothing" is often the whole claim. Where
    `is not None` really is all that can be said, add `# noqa: weak` with a reason
    and this stops reporting it.
  * **a skip with no reason** - a test that stands aside has to say why, because the
    reason is the only thing that tells a reader whether it should have run.

Provenance of the rules: the first three are this project's own history — two
unfailable assertions were found by hand in `test_what_the_phone_actually_does.py`,
and the threshold work turned up tests asserting a value they had just constructed.
The fourth is the suite's existing convention, which was not enforced anywhere.

Run it directly, or from `scripts/check.sh`:

    .venv/bin/python scripts/audit_the_tests.py
"""

from __future__ import annotations

import argparse
import ast
import sys
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parent.parent
TESTS = REPOSITORY_ROOT / "tests"

# A constant assertion is `assert True` and friends. Anything built from names or
# calls is a real claim, so only literals count.
CONSTANTS = (ast.Constant,)

# Things a test can do that are not `assert` but still check or stand aside. A test
# containing one of these is doing something, even with no assert statement.
CHECKS_THAT_ARE_NOT_ASSERTS = {
    "raises",
    "fail",
    "skip",
    "xfail",
    "warns",
    "deprecated_call",
}

WEAK_EXCUSE = "# noqa: weak"


def shown(path: Path) -> str:
    """The path as a reader wants it: relative when it is inside the repository.

    Which it is not when this script is being tested against fixtures somewhere
    else, and `relative_to` raising there is a crash in the reporting rather than a
    finding about a test.
    """
    try:
        return str(path.relative_to(REPOSITORY_ROOT))
    except ValueError:
        return str(path)


class Complaints:
    def __init__(self) -> None:
        self.problems: list[tuple[Path, int, str, str]] = []
        self.tests = 0

    def about(self, path: Path, node: ast.FunctionDef, kind: str, detail: str) -> None:
        self.problems.append((path, node.lineno, kind, detail))

    def report(self) -> int:
        if not self.problems:
            print(
                f"{self.tests} tests read; every one of them can fail, and every skip says why."
            )
            return 0
        print(f"{len(self.problems)} question(s) about {self.tests} tests:\n")
        by_file: dict[Path, list[tuple[int, str, str]]] = {}
        for path, line, kind, detail in self.problems:
            by_file.setdefault(path, []).append((line, kind, detail))
        for path, problems in sorted(by_file.items()):
            print(f"  {shown(path)}")
            for line, kind, detail in sorted(problems):
                print(f"    line {line:4}  {kind:16} {detail}")
        print(
            "\n  A finding is not automatically a defect: `is not None` is sometimes all\n"
            "  that can be said, and a test can be worth writing for the crash it would\n"
            "  produce. What is not allowed is a test that reports green having checked\n"
            "  nothing, so each of these needs either a fix or `# noqa: weak` and a\n"
            "  sentence saying why it is the best available."
        )
        return 1


def test_functions(tree: ast.Module) -> list[ast.FunctionDef]:
    """Every `test_*` function in the module, at any level."""
    found = []
    for node in ast.walk(tree):
        if isinstance(
            node, (ast.FunctionDef, ast.AsyncFunctionDef)
        ) and node.name.startswith("test_"):
            found.append(node)
    return found


def assertions_within(node: ast.FunctionDef) -> list[ast.Assert]:
    """The asserts this test makes, not the ones inside a nested definition."""
    found = []
    for child in ast.walk(node):
        if (
            isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef))
            and child is not node
        ):
            continue
        if isinstance(child, ast.Assert):
            found.append(child)
    return found


def checks_within(node: ast.FunctionDef) -> list[str]:
    """Every `pytest.<something>` this test calls, by the attribute name."""
    found = []
    for child in ast.walk(node):
        if (
            isinstance(child, ast.Call)
            and isinstance(child.func, ast.Attribute)
            and isinstance(child.func.value, ast.Name)
            and child.func.value.id == "pytest"
        ):
            found.append(child.func.attr)
    return found


def is_constant(expression: ast.expr) -> bool:
    """A literal, or a literal in a comparison's clothing."""
    return isinstance(expression, CONSTANTS)


def test_a_is_not_none_on_its_own(
    asserts: list[ast.Assert], source: str
) -> list[ast.Assert]:
    """The `is not None` asserts, when the test asserts nothing else.

    `is None` is deliberately not reported. It is a claim about a value - "this
    refuses", "nothing matched", "unknown rather than assumed" - and a test named
    for that is doing exactly what it says. `is not None` claims only that the
    result is one of infinitely many things, which is almost always less than the
    name above it promises.
    """
    weak = []
    stronger = [a for a in asserts if not _is_not_none_check(a.test)]
    if stronger:
        return []
    for assertion in asserts:
        if (
            _is_not_none_check(assertion.test)
            and WEAK_EXCUSE not in source.splitlines()[assertion.lineno - 1]
        ):
            weak.append(assertion)
    return weak


def _is_not_none_check(expression: ast.expr) -> bool:
    return (
        isinstance(expression, ast.Compare)
        and len(expression.ops) == 1
        and isinstance(expression.ops[0], ast.IsNot)
        and len(expression.comparators) == 1
        and isinstance(expression.comparators[0], ast.Constant)
        and expression.comparators[0].value is None
    )


def audit(path: Path, complaints: Complaints) -> None:
    source = path.read_text(encoding="utf-8")
    try:
        tree = ast.parse(source)
    except SyntaxError as error:
        complaints.about(
            path,
            ast.FunctionDef(name="?", lineno=error.lineno or 1),
            "unparseable",
            str(error),
        )
        return

    for test in test_functions(tree):
        complaints.tests += 1
        asserts = assertions_within(test)
        checks = checks_within(test)

        # 1. Nothing to check. A fixture that raises, a `pytest.raises` block, a
        #    deliberate skip - all count. A bare call does not.
        if not asserts and not set(checks) & CHECKS_THAT_ARE_NOT_ASSERTS:
            complaints.about(
                path,
                test,
                "cannot fail",
                f"{test.name} asserts nothing and expects no failure",
            )
            continue

        # 2. Asserting a constant.
        for assertion in asserts:
            if is_constant(assertion.test):
                complaints.about(
                    path,
                    test,
                    "constant assert",
                    f"{test.name}: {ast.unparse(assertion.test)} is always the same",
                )

        # 3. `is not None` and nothing else.
        for assertion in test_a_is_not_none_on_its_own(asserts, source):
            complaints.about(
                path,
                test,
                "weak assert",
                f"{test.name}: only asserts {ast.unparse(assertion.test)}",
            )

        # 4. A skip with no reason, which is a test that has to be read as an
        #    absence every time somebody counts the suite.
        for child in ast.walk(test):
            if (
                isinstance(child, ast.Call)
                and isinstance(child.func, ast.Attribute)
                and child.func.attr == "skip"
                and not child.args
                and not child.keywords
            ):
                complaints.about(
                    path, test, "silent skip", f"{test.name} skips with no reason"
                )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--tests", type=Path, default=TESTS, help="where the tests are")
    parser.add_argument(
        "--minimum",
        type=int,
        default=100,
        help=(
            "refuse to report if fewer tests than this were read. A scan that reads "
            "nothing passes, which is the failure this script exists to find; the "
            "floor is lowered only when the scan is being tested against fixtures."
        ),
    )
    arguments = parser.parse_args()

    complaints = Complaints()
    files = sorted(arguments.tests.rglob("*.py"))
    if not files:
        print(f"no tests found under {arguments.tests}", file=sys.stderr)
        return 2
    for path in files:
        audit(path, complaints)

    # A scan that read nothing would pass, which is the failure this script is about.
    if complaints.tests < arguments.minimum:
        print(
            f"only {complaints.tests} tests found, fewer than the {arguments.minimum} "
            f"this scan expects; it is not looking at the suite",
            file=sys.stderr,
        )
        return 2
    return complaints.report()


if __name__ == "__main__":
    sys.exit(main())
