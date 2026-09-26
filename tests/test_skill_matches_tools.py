"""The shipped skill must describe the tools that actually exist.

Documentation drifts silently, and an agent following a skill that names a tool
which is not mounted fails in a way that looks like a bug in the phone. Both
directions are checked: no undocumented tool, and no documented tool that has
been renamed or removed.
"""

from __future__ import annotations

import asyncio
import re
from pathlib import Path

from phone_control.server import server

SKILL_DIRECTORY = (
    Path(__file__).resolve().parents[1] / "skills" / "android-phone-control"
)
REFERENCE = SKILL_DIRECTORY / "references" / "tools-reference.md"

# The first cell of a reference table row, which is always a tool name.
TABLE_TOOL_ROW = re.compile(r"^\|\s*`([a-z][a-z_]+)`", re.MULTILINE)


def registered_tool_names() -> set[str]:
    return {tool.name for tool in asyncio.run(server.list_tools())}


def documented_tool_names() -> set[str]:
    return set(TABLE_TOOL_ROW.findall(REFERENCE.read_text(encoding="utf-8")))


def test_the_skill_bundle_is_where_the_repo_says_it_is():
    assert (SKILL_DIRECTORY / "SKILL.md").is_file()
    assert REFERENCE.is_file()


def test_every_registered_tool_is_documented():
    reference = REFERENCE.read_text(encoding="utf-8")

    undocumented = sorted(
        name for name in registered_tool_names() if f"`{name}`" not in reference
    )

    assert not undocumented, f"tools missing from the reference: {undocumented}"


def test_the_reference_names_no_tool_that_is_not_registered():
    stale = sorted(documented_tool_names() - registered_tool_names())

    assert not stale, f"the reference names tools that do not exist: {stale}"


def test_the_skill_body_exists_and_carries_frontmatter():
    body = (SKILL_DIRECTORY / "SKILL.md").read_text(encoding="utf-8")

    assert body.startswith("---\n")
    assert "\nname: android-phone-control\n" in body
    # A body that has grown past the loader's budget would be pruned.
    assert len(body) < 8000, f"SKILL.md is {len(body)} characters"


def test_the_skill_body_names_the_reference_file_it_points_at():
    body = (SKILL_DIRECTORY / "SKILL.md").read_text(encoding="utf-8")

    assert "references/tools-reference.md" in body
