"""No test may be marked as an expected failure.

A test that is allowed to fail is not a test. It reports green while the thing it
describes does not work, and the reason it carries is read once and then never
again - so the defect it records quietly becomes permanent. This project has
already shipped one of those: the tool surface returned its failures as answers,
making every tool error invisible to every client, and the single test that said
so was an ``xfail`` that everyone had stopped reading.

The rule is therefore absolute: fix it or delete it. A test describing behaviour
that is wanted but not built belongs in a design note, not in a suite that reports
success.

``skip`` is a different thing and is not blocked here: a device test that cannot
run without a phone is skipped for a reason outside anyone's control, and the suite
still means what it says.
"""

from __future__ import annotations

import ast
import io
import re
import tokenize
from pathlib import Path

TESTS = Path(__file__).parent

# Anything that marks a test as expected to fail, as a pattern over the file's
# *code*. Comments and strings are skipped, because this file's own docstring
# names the thing it forbids and so does the one it replaced - a guard that fires
# on prose is a guard somebody turns off.
THE_WAYS_IN = (
    re.compile(r"\bxfail\b"),
    re.compile(r"\bx_fail\b"),
    re.compile(r"xfail_strict"),
    re.compile(r"mark\.skipif\(\s*False"),
)

# This file names the thing it forbids, so it is exempt from matching itself.
ITSELF = "test_no_xfail.py"


def the_test_files() -> list[Path]:
    return sorted(
        path
        for path in TESTS.rglob("*.py")
        if path.name != ITSELF and "__pycache__" not in path.parts
    )


def the_code_in(path: Path) -> list[tuple[int, str]]:
    """Every real code line, with its number. Strings and comments left out.

    Reading the file line by line reports a docstring that explains the rule as a
    breach of it, and a guard that cries wolf is one somebody deletes.
    """
    source = path.read_text(encoding="utf-8")
    lines = source.splitlines()
    found: list[tuple[int, str]] = []
    for token in tokenize.generate_tokens(io.StringIO(source).readline):
        if token.type in (
            tokenize.COMMENT,
            tokenize.STRING,
            tokenize.NL,
            tokenize.NEWLINE,
        ):
            continue
        if token.type == tokenize.NAME or token.type == tokenize.OP:
            found.append((token.start[0], lines[token.start[0] - 1].strip()))
    return found


def test_no_test_is_marked_as_an_expected_failure():
    offenders: list[str] = []
    for path in the_test_files():
        for number, line in the_code_in(path):
            if any(pattern.search(line) for pattern in THE_WAYS_IN):
                offenders.append(f"{path.relative_to(TESTS)}:{number}: {line}")

    assert not offenders, (
        "these tests are allowed to fail, which is not a test:\n  "
        + "\n  ".join(offenders)
        + "\n\nFix the behaviour or delete the test. A wanted-but-unbuilt behaviour "
        "belongs in a design note, not in a suite that reports success."
    )


def test_the_suite_configures_no_expected_failures():
    """A marker can be set from the config as well as from a test."""
    configuration = (TESTS.parent / "pyproject.toml").read_text(encoding="utf-8")
    settings = configuration.split("[tool.pytest.ini_options]", 1)
    assert len(settings) == 2, "pytest is not configured in pyproject.toml"

    for pattern in THE_WAYS_IN:
        assert not pattern.search(settings[1]), (
            f"{pattern.pattern} appears in the pytest configuration"
        )


def test_the_checks_that_would_say_so_are_still_being_run():
    """A skipped-by-default test is a test nobody runs.

    The suite deselects device tests by default, which is deliberate and is why
    ``scripts/check.sh`` runs everything. What must not happen is a *fast* test
    being silently skipped, because then the guard against expected failures is
    itself not running.
    """
    tree = ast.parse(Path(__file__).read_text(encoding="utf-8"))
    functions = [
        node.name
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name.startswith("test_")
    ]

    assert functions, "this guard has no tests in it, so it guards nothing"

    # Read as a tree rather than searched for as a word: the word appears in this
    # very assertion, which is how a substring check reports itself.
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                assert getattr(target, "id", "") != "pytestmark", (
                    "this file must run wherever the suite runs, so it cannot be "
                    "marked device or jev"
                )
        if isinstance(node, ast.ClassDef):
            continue
