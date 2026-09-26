"""The counts and sizes in the documentation describe this repository.

Two documents state how large things are. ``docs/engineering-decisions.md`` ends
with a table of the project's parts, and ``docs/code-map.md`` gives the line count
of every module in the library. Both are measured here instead of being maintained
by hand, because a table like that is correct for about as long as it takes to read
it, and it is the sort of claim a reader uses to decide whether to trust the rest of
the document.

The prose counts are checked too. A sentence saying "all nineteen tools" is making
the same kind of claim as a table, and nothing else here would notice a twentieth
tool. Each check derives its groups rather than listing them: the library is
whatever is in the package, the tests are whatever is under ``tests/``, the
generated copies of the skill are whatever the renderer's own adapter list says it
writes, and a count is compared against the thing it counts.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

REPOSITORY_ROOT = Path(__file__).resolve().parent.parent

# The renderer is a script rather than part of the installed package, so its
# directory goes on the path and it is imported by name. Importing it as
# `scripts.render_skill_for_agents` appears to work under `python -m pytest`,
# which puts the working directory on the path, and fails under the plain pytest
# a continuous-integration run uses. `tests/test_skill_adapters.py` does the same.
sys.path.insert(0, str(REPOSITORY_ROOT / "scripts"))

import render_skill_for_agents as renderer  # noqa: E402

PACKAGE = REPOSITORY_ROOT / "src" / "phone_control"
NOTES = REPOSITORY_ROOT / "docs" / "engineering-decisions.md"
CODE_MAP = REPOSITORY_ROOT / "docs" / "code-map.md"
TABLE_HEADING = "## What was built, by size"
CLAUDE_SKILL = REPOSITORY_ROOT / ".claude" / "skills" / "android-phone-control"

# Directories that hold something other than the project: version-control data,
# an installed environment, caches, and build output. Excluded by rule rather
# than by listing the files, so a new module is counted the moment it is written.
NOT_THE_PROJECT = frozenset(
    {
        ".git",
        ".venv",
        "venv",
        "node_modules",
        "__pycache__",
        ".pytest_cache",
        ".mypy_cache",
        ".ruff_cache",
        "artifacts",
        "htmlcov",
        "dist",
        "build",
    }
)


def _the_files_under(directory: Path, suffix: str) -> list[Path]:
    """Every file with this suffix below here, outside the excluded directories."""
    return [
        path
        for path in directory.rglob(f"*{suffix}")
        if not NOT_THE_PROJECT.intersection(path.relative_to(REPOSITORY_ROOT).parts)
    ]


def _the_lines_in(paths: list[Path]) -> int:
    return sum(len(path.read_text(encoding="utf-8").splitlines()) for path in paths)


def _the_generated_copies() -> list[Path]:
    """The files the renderer writes, asked of the renderer itself."""
    written = [
        REPOSITORY_ROOT / adapter.path
        for adapter in renderer.ADAPTERS
        if adapter.path.endswith(".md")
    ]
    # Claude Code resolves the reference beside its own SKILL.md, so that copy is
    # written from the same canonical file by the same script.
    references = CLAUDE_SKILL / "references"
    if references.is_dir():
        written.extend(sorted(references.glob("*.md")))
    return written


def _what_was_built() -> list[tuple[str, int, int]]:
    """The four groups, in the order the table gives them."""
    library = _the_files_under(REPOSITORY_ROOT / "src" / "phone_control", ".py")
    tests = _the_files_under(REPOSITORY_ROOT / "tests", ".py")
    generated = _the_generated_copies()
    documentation = [
        path
        for path in _the_files_under(REPOSITORY_ROOT, ".md")
        if path not in generated
    ]
    return [
        ("library", len(library), _the_lines_in(library)),
        ("tests", len(tests), _the_lines_in(tests)),
        (
            "documentation, written by hand",
            len(documentation),
            _the_lines_in(documentation),
        ),
        (
            "the skill copied to each agent's own path",
            len(generated),
            _the_lines_in(generated),
        ),
    ]


def _the_table() -> list[tuple[str, int, int]]:
    """The table as the notes state it, with the thousands separators removed."""
    text = NOTES.read_text(encoding="utf-8")
    assert TABLE_HEADING in text, (
        f"{NOTES.name} no longer has a {TABLE_HEADING!r} heading, so the size table "
        f"this test guards cannot be found. Restore the heading or point this test "
        f"at wherever the table moved to."
    )
    body = text.split(TABLE_HEADING, 1)[1]

    rows = []
    for line in body.splitlines():
        if not line.startswith("|"):
            continue
        cells = [cell.strip() for cell in line.strip("|").split("|")]
        if len(cells) != 3 or cells[0] in ("", "---") or set(cells[1]) <= {"-"}:
            continue
        label, files, lines = cells
        rows.append((label, int(files.replace(",", "")), int(lines.replace(",", ""))))
    return rows


def test_the_table_lists_the_groups_in_the_order_they_are_measured():
    """A renamed or reordered row would otherwise make this test compare nothing."""
    stated = [row[0] for row in _the_table()]
    measured = [group[0] for group in _what_was_built()]

    assert len(stated) == len(measured), (
        f"the size table has {len(stated)} rows and this test measures {len(measured)} "
        f"groups; add the missing group to one of them"
    )
    for position, (in_the_notes, here) in enumerate(
        zip(stated, measured, strict=True), start=1
    ):
        assert in_the_notes.startswith(here), (
            f"row {position} of the size table is {in_the_notes!r}, but this test "
            f"measures the group {here!r}. If the table was reorganised, update this "
            f"test to match rather than leaving it comparing unlike things."
        )


@pytest.mark.parametrize(
    "position", range(4), ids=lambda position: _what_was_built()[position][0]
)
def test_the_size_table_matches_the_repository(position: int):
    """The number of files and lines each group actually has, right now."""
    label, files_here, lines_here = _what_was_built()[position]
    _, files_stated, lines_stated = _the_table()[position]

    assert (files_stated, lines_stated) == (files_here, lines_here), (
        f"the size table says {label} is {files_stated} files / {lines_stated} lines; "
        f"it is {files_here} files / {lines_here} lines. Update the table in "
        f"{NOTES.relative_to(REPOSITORY_ROOT)}."
    )


def test_the_table_is_read_rather_than_silently_empty():
    """The parser is the weak point: a table that stops parsing would pass vacuously."""
    rows = _the_table()

    assert len(rows) >= 4, f"only {len(rows)} rows of the size table could be read"
    assert all(files > 0 and lines > files for _, files, lines in rows), (
        f"a row of the size table is not plausible: {rows}"
    )


def test_the_generated_copies_are_real_files():
    """Otherwise the fourth group would count files that are not there."""
    generated = _the_generated_copies()

    assert len(renderer.ADAPTERS) >= 4, "the renderer has stopped declaring adapters"
    for path in generated:
        assert path.is_file(), (
            f"{path.relative_to(REPOSITORY_ROOT)} is counted as a generated copy of the "
            f"skill but does not exist; run scripts/render_skill_for_agents.py"
        )


def test_the_documentation_group_is_written_by_hand():
    """The rule that separates the two documentation rows has to actually hold."""
    generated = set(_the_generated_copies())
    documentation = [
        path
        for path in _the_files_under(REPOSITORY_ROOT, ".md")
        if path not in generated
    ]

    assert documentation, "no hand-written documentation was found at all"
    for path in documentation:
        first_line = path.read_text(encoding="utf-8").splitlines()[:1]
        assert not first_line or "Generated from" not in first_line[0], (
            f"{path.relative_to(REPOSITORY_ROOT)} is generated but is being counted as "
            f"hand-written documentation. The renderer's adapter list is the rule this "
            f"test uses, so a generated file missing from it will be miscounted."
        )


def test_every_markdown_file_is_counted_exactly_once():
    """No file may fall outside all four groups, and none may be counted twice."""
    counted: set[Path] = set()
    generated = _the_generated_copies()
    documentation = [
        path
        for path in _the_files_under(REPOSITORY_ROOT, ".md")
        if path not in generated
    ]
    counted.update(documentation)
    counted.update(generated)
    every_markdown_file = set(_the_files_under(REPOSITORY_ROOT, ".md"))

    assert counted == every_markdown_file, (
        f"these Markdown files are in the repository but in neither documentation row: "
        f"{sorted(str(p.relative_to(REPOSITORY_ROOT)) for p in every_markdown_file - counted)}"
    )
    assert len(documentation) + len(generated) == len(every_markdown_file), (
        "a Markdown file is being counted in both documentation rows"
    )


def test_no_number_in_the_table_is_a_round_guess():
    """A row of round numbers is the signature of a table nobody measured."""
    for label, _files, lines in _the_table():
        assert not re.fullmatch(r"\d000", str(lines)), (
            f"the size table gives {label} exactly {lines} lines, which looks estimated "
            f"rather than measured. Run this test to get the real number."
        )


# The module table in docs/code-map.md, which states each module's line count.
MODULE_ROW = re.compile(r"^\| `([a-z_]+\.py)` \| ([0-9,]+) \|", re.MULTILINE)


def _the_module_sizes_stated_in_the_code_map() -> dict[str, int]:
    return {
        module: int(claimed.replace(",", ""))
        for module, claimed in MODULE_ROW.findall(CODE_MAP.read_text(encoding="utf-8"))
    }


def _the_modules_that_exist() -> dict[str, int]:
    return {
        path.name: len(path.read_text(encoding="utf-8").splitlines())
        for path in sorted(PACKAGE.glob("*.py"))
    }


def test_the_code_map_describes_every_module_in_the_package():
    """A module that exists but is not in the table is a map with a hole in it."""
    stated = _the_module_sizes_stated_in_the_code_map()
    existing = _the_modules_that_exist()

    assert existing, f"no modules found in {PACKAGE.relative_to(REPOSITORY_ROOT)}"
    assert set(stated) == set(existing), (
        f"{CODE_MAP.name} does not describe the same modules as the package.\n"
        f"  in the package but not the table: {sorted(set(existing) - set(stated))}\n"
        f"  in the table but not the package: {sorted(set(stated) - set(existing))}"
    )


def test_the_code_map_line_counts_are_current():
    """Every number in that table, against the file it describes."""
    stated = _the_module_sizes_stated_in_the_code_map()
    existing = _the_modules_that_exist()

    stale = {
        module: (stated[module], existing[module])
        for module in stated
        if module in existing and stated[module] != existing[module]
    }
    assert not stale, (
        f"{CODE_MAP.name} is out of date. claimed and actual line counts:\n"
        + "\n".join(
            f"  {module}: claimed {claimed}, actual {actual}"
            for module, (claimed, actual) in sorted(stale.items())
        )
    )


def test_the_code_map_table_sums_to_the_library():
    """The table claims to be the whole library, so it has to add up to it."""
    total = sum(_the_module_sizes_stated_in_the_code_map().values())
    _, library_files, library_lines = _what_was_built()[0]

    assert total == library_lines, (
        f"the module table in {CODE_MAP.name} adds up to {total} lines, but the library "
        f"is {library_lines} lines across {library_files} modules. Either a module is "
        f"missing from the table or a count is wrong."
    )


# The prose counts as well as the tables. A document that says "all nineteen tools"
# is making a claim that a twentieth tool would falsify, and it is written in a
# sentence rather than a table, so nothing else here would notice.
COUNTED_THINGS = {
    "tools": "the tools the MCP server publishes",
    "modules": "the modules in the library",
    "scenarios": "the usage scenarios in the catalogue",
    "steps": "the default step limit",
}


def _the_count_of(what: str) -> int:
    """How many of this thing there are, asked of the thing itself."""
    if what == "tools":
        import asyncio

        from phone_control.server import server

        return len(asyncio.run(server.list_tools()))
    if what == "modules":
        return len(_the_modules_that_exist())
    if what == "scenarios":
        from scenarios.catalogue import SCENARIOS

        return len(SCENARIOS)
    if what == "steps":
        from phone_control.goal import DEFAULT_MAX_STEPS

        return DEFAULT_MAX_STEPS
    raise AssertionError(f"no way to count {what!r}")


# "all nineteen tools", "all 47 scenarios", "all the twelve tools" — the number may
# be a digit or an English word, and one adjective may sit between it and the noun.
SPOKEN_UNDER_TWENTY = {
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
    "eleven": 11,
    "twelve": 12,
    "thirteen": 13,
    "fourteen": 14,
    "fifteen": 15,
    "sixteen": 16,
    "seventeen": 17,
    "eighteen": 18,
    "nineteen": 19,
}
SPOKEN_TENS = {
    "twenty": 20,
    "thirty": 30,
    "forty": 40,
    "fifty": 50,
    "sixty": 60,
    "seventy": 70,
    "eighty": 80,
    "ninety": 90,
}
A_COUNT_IN_PROSE = re.compile(
    r"\ball (?:the )?(?:([0-9]+)|([a-z]+(?:-[a-z]+)?))(?: [a-z]+)? ("
    + "|".join(COUNTED_THINGS)
    + r")\b"
)


def _the_number_spoken(word: str) -> int | None:
    """Read an English number below a hundred, hyphenated or not."""
    tens, _, units = word.partition("-")
    if not units:
        return SPOKEN_UNDER_TWENTY.get(tens) or SPOKEN_TENS.get(tens)
    if tens in SPOKEN_TENS and units in SPOKEN_UNDER_TWENTY:
        return SPOKEN_TENS[tens] + SPOKEN_UNDER_TWENTY[units]
    return None


def _the_pages_a_person_reads() -> list[Path]:
    """The documentation, and the shell scripts — both are read, and both make claims."""
    generated = set(_the_generated_copies())
    pages = [
        page
        for page in _the_files_under(REPOSITORY_ROOT, ".md")
        if page not in generated
    ]
    pages.extend(_the_files_under(REPOSITORY_ROOT / "scripts", ".sh"))
    return pages


def _the_counts_stated_in_prose() -> list[tuple[str, str, int]]:
    """Every "all <number> <thing>" in the documentation and the scripts."""
    claimed = []
    for page in _the_pages_a_person_reads():
        for digits, word, thing in A_COUNT_IN_PROSE.findall(
            page.read_text(encoding="utf-8")
        ):
            number = int(digits) if digits else _the_number_spoken(word)
            if number is not None:
                claimed.append((str(page.relative_to(REPOSITORY_ROOT)), thing, number))
    return claimed


def test_the_counts_stated_in_prose_are_current():
    """A sentence that counts something has to count it correctly."""
    wrong = [
        (where, thing, claimed, _the_count_of(thing))
        for where, thing, claimed in _the_counts_stated_in_prose()
        if claimed != _the_count_of(thing)
    ]

    assert not wrong, (
        "these sentences state a count that is no longer true:\n"
        + "\n".join(
            f"  {where}: says {claimed} {thing}, there are {actual}"
            for where, thing, claimed, actual in wrong
        )
    )


def test_the_prose_scan_finds_something_to_check():
    """A scan that matches nothing would pass without looking at anything."""
    claimed = _the_counts_stated_in_prose()

    assert len(claimed) >= 4, (
        f"only {len(claimed)} counts were found in the documentation, so this check is "
        f"close to vacuous. Either the phrasing changed or the documents stopped stating "
        f"counts; if the latter, delete these two tests rather than leaving them here "
        f"looking busy. Found: {claimed}"
    )
    assert {thing for _, thing, _ in claimed} >= {"tools", "modules", "scenarios"}, (
        f"the scan no longer sees a count of something important: {claimed}"
    )
