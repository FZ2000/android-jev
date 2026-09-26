#!/usr/bin/env python3
"""Check that this repository can be found by the people looking for it.

GitHub's search matches the repository name, description and topics — not the
README — so those three fields are the whole of the discoverability surface, and
they live on GitHub rather than in this checkout. That is exactly the shape of
claim this project has been wrong about before: something configured outside the
repository, described in a document nobody diffs, and quietly wrong.

So the intent lives in ``.github/repository-metadata.toml`` and this checks two
things:

* the file itself follows the rules, which needs no network and always runs
* GitHub actually carries what the file says, which needs a token

Every rule below says where it comes from. "Documented" is a platform limit
somebody can look up; "measured" is a number this project took; "inferred" is a
judgement, and says so, because a reader is entitled to argue with a judgement.

    python scripts/check_findability.py            # the file only
    python scripts/check_findability.py --live     # and GitHub, with a token
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parent.parent
METADATA = REPOSITORY_ROOT / ".github" / "repository-metadata.toml"

# Documented by GitHub: the description is capped at 350 characters and a
# repository may carry 20 topics.
LONGEST_DESCRIPTION = 350
MOST_TOPICS = 20

# Measured: this is how much of a description survives in a search-result title
# before it is cut. The head of the description is therefore chosen, not incidental.
SEARCH_TITLE_CHARACTERS = 36

# Documented by GitHub: topic names are lowercase, may contain hyphens, and are
# limited to 50 characters.
LONGEST_TOPIC = 50


class Complaints:
    """Everything wrong, collected rather than raised, so one run reports all of it."""

    def __init__(self) -> None:
        self.problems: list[str] = []
        self.checks = 0

    def that(self, holds: bool, complaint: str) -> None:
        self.checks += 1
        if not holds:
            self.problems.append(complaint)

    def report(self) -> int:
        if self.problems:
            print(
                f"\n{len(self.problems)} problem(s) with how this repository is found:\n"
            )
            for problem in self.problems:
                print(f"  * {problem}")
            return 1
        print(f"\n{self.checks} checks passed.")
        return 0


def the_metadata() -> dict:
    """The file that says how this repository is meant to be found."""
    import tomllib

    if not METADATA.is_file():
        raise SystemExit(f"no {METADATA.relative_to(REPOSITORY_ROOT)} to check")
    return tomllib.loads(METADATA.read_text(encoding="utf-8"))


def check_the_file(metadata: dict, complaints: Complaints) -> None:
    """The rules that hold whatever GitHub currently says."""
    description = (metadata.get("description") or "").strip()
    topics = metadata.get("topics") or []

    # Documented: a description is required, and capped.
    complaints.that(bool(description), "the metadata file has no description")
    complaints.that(
        len(description) <= LONGEST_DESCRIPTION,
        f"the description is {len(description)} characters; GitHub allows "
        f"{LONGEST_DESCRIPTION}",
    )

    # Measured: the head of the description is what a searcher sees.
    head = metadata.get("head") or ""
    complaints.that(
        bool(head) and description.startswith(head),
        f"the description does not open with {head!r}, so the part that reaches a "
        f"search-result title is not the part that was chosen",
    )
    complaints.that(
        len(head) <= SEARCH_TITLE_CHARACTERS,
        f"the head {head!r} is {len(head)} characters; only about "
        f"{SEARCH_TITLE_CHARACTERS} survive in a search-result title",
    )

    # Measured: the vocabulary a searcher for this would actually type.
    for term in metadata.get("must_contain") or []:
        complaints.that(
            term.casefold() in description.casefold(),
            f"the description does not contain {term!r}, which is a term these "
            f"repositories are searched by",
        )

    # Documented: twenty topics is the maximum, and using fewer leaves reach on the
    # table. Every one of these has an audience, measured and recorded beside it.
    complaints.that(
        len(topics) == MOST_TOPICS,
        f"{len(topics)} topics; GitHub allows {MOST_TOPICS} and they are free",
    )
    complaints.that(
        len(set(topics)) == len(topics),
        "the same topic is listed twice",
    )

    audience = metadata.get("topic_audience") or {}
    for topic in topics:
        complaints.that(
            topic == topic.casefold(),
            f"the topic {topic!r} is not lowercase, so GitHub will not match it",
        )
        complaints.that(
            len(topic) <= LONGEST_TOPIC and " " not in topic,
            f"the topic {topic!r} is not a valid topic name",
        )
        # Measured, and the reason a topic has to be in this table at all: a topic
        # nobody searches for is a decoration rather than a way in.
        complaints.that(
            audience.get(topic, 0) > 0,
            f"the topic {topic!r} has no recorded audience; measure it with "
            f"`topic:{topic}` before claiming it reaches anybody",
        )
    for topic, count in audience.items():
        complaints.that(
            topic in topics,
            f"{topic!r} is in the audience table (audience {count}) but not in the "
            f"topic list, so it is doing nothing",
        )

    # Inferred: the package and the repository should be one name, because a
    # searcher who finds one and installs the other should not have to work out
    # whether they are the same thing. Compared against the repository declared in
    # this file rather than the directory the checker is running in: a clone is
    # named after the repository, but a development checkout need not be.
    import tomllib

    manifest = tomllib.loads((REPOSITORY_ROOT / "pyproject.toml").read_text())
    distribution = manifest["project"]["name"]
    slug = metadata.get("repository") or ""
    complaints.that(
        bool(slug) and "/" in slug,
        "the metadata file does not declare which repository it describes",
    )
    complaints.that(
        distribution == slug.split("/")[-1],
        f"the distribution is {distribution!r} and the repository is {slug!r}",
    )

    # Inferred: registry search reads the keywords, so they should carry the same
    # vocabulary as the description rather than a second, thinner set.
    keywords = [word.casefold() for word in manifest["project"].get("keywords", [])]
    complaints.that(
        len(keywords) >= 8,
        f"only {len(keywords)} package keywords; registry search reads these",
    )
    for term in ("android", "adb", "mcp"):
        complaints.that(
            term in keywords,
            f"the package keywords do not include {term!r}",
        )


def the_repository_on_github(declared: str) -> tuple[str, list[str]] | None:
    """What GitHub currently carries, or None when there is no way to ask."""
    # `GITHUB_REPOSITORY` is set by the workflow and is authoritative there; the
    # declared slug is what to ask about from a development checkout.
    slug = os.environ.get("GITHUB_REPOSITORY") or declared
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN") or ""
    command = [
        "gh",
        "api",
        f"repos/{slug}",
        "--jq",
        "{description: .description, topics: [.topics[]]}",
    ]
    environment = {**os.environ, "GH_TOKEN": token} if token else None
    try:
        result = subprocess.run(
            command, capture_output=True, text=True, env=environment
        )
    except FileNotFoundError:
        return None
    if result.returncode != 0:
        print(f"  could not reach GitHub for {slug}: {result.stderr.strip()[:200]}")
        return None
    carried = json.loads(result.stdout)
    return carried.get("description") or "", carried.get("topics") or []


def check_github(metadata: dict, complaints: Complaints) -> bool:
    """That GitHub carries what the file says. False when it could not be asked."""
    print("\n== what GitHub carries ==")
    live = the_repository_on_github(metadata.get("repository") or "")
    if live is None:
        print("  not checked: no GitHub remote and token to ask with.")
        print("  Run with `gh auth login`, or set GITHUB_TOKEN, to check this half.")
        return False

    description, topics = live
    print(f"  description: {description!r}")
    print(f"  topics:      {len(topics)}")

    complaints.that(
        description.strip() == (metadata.get("description") or "").strip(),
        f"GitHub's description is not the one in "
        f"{METADATA.relative_to(REPOSITORY_ROOT)}. Apply it with:\n"
        f'      gh repo edit --description "<the description from that file>"',
    )
    complaints.that(
        sorted(topics) == sorted(metadata.get("topics") or []),
        "GitHub's topics are not the ones in the metadata file. Missing: "
        f"{sorted(set(metadata.get('topics') or []) - set(topics))}; "
        f"extra: {sorted(set(topics) - set(metadata.get('topics') or []))}",
    )
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--live",
        action="store_true",
        help="also check what GitHub carries, which needs a token",
    )
    arguments = parser.parse_args()

    metadata = the_metadata()
    complaints = Complaints()

    print("== the metadata file ==")
    check_the_file(metadata, complaints)

    if arguments.live:
        # Asked for and not done is a failure, not a shrug: the whole reason this
        # script exists is that these fields live on GitHub, where nothing else in
        # the suite can see them. A check that quietly does nothing when it cannot
        # reach the thing it checks is the failure mode it was written to prevent.
        complaints.that(
            check_github(metadata, complaints),
            "the live check was asked for and could not be performed, so nothing "
            "has verified what GitHub carries",
        )

    return complaints.report()


if __name__ == "__main__":
    sys.exit(main())
