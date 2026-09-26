"""How this repository is found is a check, not a memory.

GitHub's search matches the repository name, description and topics — not the
README — and those three fields live on GitHub rather than in this checkout. That
is the shape of claim this project has been wrong about before: configuration
outside the repository, described in a document nobody diffs.

`.github/repository-metadata.toml` holds the intent and says where every rule comes
from. The rules that need no network are checked here, so they run in the local
gate and in the suite; the half that reads GitHub back is
`scripts/check_findability.py --live`, which CI runs because a token is needed.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parent.parent

# The checker is a script rather than part of the installed package, so its
# directory goes on the path and it is imported by name — the same arrangement
# `tests/test_skill_adapters.py` uses for the skill renderer.
sys.path.insert(0, str(REPOSITORY_ROOT / "scripts"))

import check_findability  # noqa: E402


def the_complaints_about(metadata: dict) -> list[str]:
    complaints = check_findability.Complaints()
    check_findability.check_the_file(metadata, complaints)
    return complaints.problems


def test_the_metadata_file_follows_its_own_rules():
    """The description, the head, the terms and all twenty topics."""
    problems = the_complaints_about(check_findability.the_metadata())

    assert not problems, (
        "the discoverability metadata breaks its own rules:\n"
        + "\n".join(f"  * {problem}" for problem in problems)
    )


def test_the_rules_can_actually_fail():
    """A checker that passes on anything is a checker that does nothing.

    Four separate ways of being wrong, because they are four separate rules and a
    single mutation would only prove one of them is wired up.
    """
    intact = check_findability.the_metadata()

    no_topics = {**intact, "topics": []}
    assert the_complaints_about(no_topics), "an empty topic list passed the rules"

    bad_head = {**intact, "description": "A tool for phones", "head": "MCP server"}
    assert the_complaints_about(bad_head), "a description that misses its head passed"

    unmeasured = {
        **intact,
        "topics": [*intact["topics"][:-1], "something-nobody-searches"],
    }
    assert the_complaints_about(unmeasured), "a topic with no audience passed"

    missing_term = {
        **intact,
        "description": "MCP server for phones",
        "must_contain": ["MCP server", "Android"],
    }
    assert the_complaints_about(missing_term), "a description missing a term passed"


def test_the_topics_are_the_documented_maximum():
    """Twenty is free reach; using fewer is a decision that should be visible."""
    metadata = check_findability.the_metadata()

    assert len(metadata["topics"]) == check_findability.MOST_TOPICS
    assert len(set(metadata["topics"])) == len(metadata["topics"]), (
        "a topic is repeated"
    )


def test_every_topic_has_a_measured_audience():
    """The number beside each topic is why it is there rather than a decoration."""
    metadata = check_findability.the_metadata()

    for topic in metadata["topics"]:
        assert metadata["topic_audience"][topic] > 0, (
            f"{topic} has no recorded audience"
        )


def test_the_terms_a_searcher_types_are_in_the_description():
    """The vocabulary was read off the repositories that rank for these queries."""
    metadata = check_findability.the_metadata()
    description = metadata["description"].casefold()

    for term in metadata["must_contain"]:
        assert term.casefold() in description, (
            f"{term!r} is a term this is searched by and it is not in the description"
        )


def test_the_package_and_the_repository_are_one_name():
    """A searcher who finds one and installs the other should not have to check."""
    import tomllib

    manifest = tomllib.loads((REPOSITORY_ROOT / "pyproject.toml").read_text())
    repository = check_findability.the_metadata()["repository"]

    assert manifest["project"]["name"] == repository.split("/")[-1]
