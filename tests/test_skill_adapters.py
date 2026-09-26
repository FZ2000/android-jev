"""Every agent's copy of the skill must match the one canonical body.

Five files carry the same instructions in different wrappers, because each agent
reads a different path. A copy that has drifted is worse than a missing one: the
agent follows stale instructions and nothing anywhere says so. These tests
re-render from the canonical skill and compare, so editing the wrong file fails
loudly instead of silently.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPOSITORY_ROOT / "scripts"))

import render_skill_for_agents as renderer  # noqa: E402


def test_the_canonical_skill_exists():
    assert renderer.CANONICAL_SKILL.is_file(), (
        "the canonical skill is the source every other copy is rendered from"
    )


@pytest.mark.parametrize("adapter", renderer.ADAPTERS, ids=lambda item: item.path)
def test_each_agent_copy_matches_the_canonical_body(adapter):
    expected = renderer.render(adapter, renderer.read_canonical())
    on_disk = (REPOSITORY_ROOT / adapter.path).read_text(encoding="utf-8")

    assert on_disk == expected, (
        f"{adapter.path} is stale. Edit skills/android-phone-control/SKILL.md and "
        "run scripts/render_skill_for_agents.py"
    )


def test_every_agent_file_a_person_would_look_for_is_rendered():
    paths = {adapter.path for adapter in renderer.ADAPTERS}

    assert {
        "AGENTS.md",
        "GEMINI.md",
        ".github/copilot-instructions.md",
        ".cursor/rules/android-phone-control.mdc",
        ".claude/skills/android-phone-control/SKILL.md",
    } <= paths


def test_the_claude_code_reference_copy_matches_the_canonical_one():
    source = renderer.REFERENCES / "tools-reference.md"
    copy = (
        REPOSITORY_ROOT
        / ".claude/skills/android-phone-control/references/tools-reference.md"
    )

    assert copy.is_file(), "Claude Code resolves the reference beside its SKILL.md"
    assert copy.read_text(encoding="utf-8") == source.read_text(encoding="utf-8")


def test_no_rendered_copy_is_dsh_specific():
    """The body has to read for any agent, not just the one it was written in."""
    for adapter in renderer.ADAPTERS:
        rendered = renderer.render(adapter, renderer.read_canonical())

        assert "DSH_HOME" not in rendered, adapter.path
        assert "cordis.patch.yml" not in rendered, adapter.path


def test_the_canonical_body_names_the_one_tool_an_agent_calls():
    """``run_task`` and nothing else.

    This used to require the body to name `read_screen`, `tap`, `type_text` and
    `wait_for` as well, on the grounds that a skill which never mentions them leaves
    an agent unable to do anything when a run comes back unfinished. That is the wrong
    way round: the skill tells an agent to describe an outcome, and an agent that
    starts choosing controls is doing the server's job with less of the information
    the server had. Naming those tools invited exactly the step-by-step driving this
    project spends its effort removing, and the report is what to relay instead.

    The check is kept rather than deleted, because a body that stopped mentioning
    `run_task` would be a skill with no interface at all.
    """
    body = renderer.read_canonical()

    assert "run_task" in body, "the skill never mentions the one tool an agent calls"
    for tool in ("read_screen", "tap", "type_text", "wait_for", "take_screenshot"):
        assert f"`{tool}`" not in body, (
            f"the skill names {tool}, which is not the agent's interface - see "
            "docs/tool-contract.md"
        )
