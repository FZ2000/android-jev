"""The MCP Registry entry says only what the package and the repository say.

`server.json` is what the official MCP Registry — and every directory that mirrors
it — shows a stranger about this project. It is a hand-written file about other
files, which is the shape this project keeps finding to be wrong, so each claim in it
is checked against the thing it describes:

- the version, against `phone_control.__version__`;
- the package, against the distribution `pyproject.toml` builds;
- the command a registry client will run, `uvx <identifier>`, against the scripts
  the distribution actually installs;
- the name, against the `mcp-name:` marker the registry looks for in the README,
  which PyPI publishes as the package description. A mismatch there is not caught
  until a release has already reached PyPI and the registry refuses the entry.

The registry's own limits are checked too, so that a description edited past them
fails here rather than in the release job.
"""

from __future__ import annotations

import json
import re
import tomllib
from pathlib import Path

import phone_control

REPOSITORY_ROOT = Path(__file__).resolve().parent.parent


def the_entry() -> dict:
    return json.loads((REPOSITORY_ROOT / "server.json").read_text())


def the_manifest() -> dict:
    return tomllib.loads((REPOSITORY_ROOT / "pyproject.toml").read_text())


def the_package() -> dict:
    (package,) = the_entry()["packages"]
    return package


def test_the_entry_announces_the_version_the_package_reports():
    version = phone_control.__version__
    assert the_entry()["version"] == version
    assert the_package()["version"] == version, (
        "The registry pins the PyPI release by this number. One that disagrees with "
        "the package names a release that either does not exist or is not this one."
    )


def test_the_entry_names_the_distribution_this_repository_builds():
    assert the_package()["registryType"] == "pypi"
    assert the_package()["identifier"] == the_manifest()["project"]["name"]


def test_the_command_a_registry_client_runs_is_one_the_package_installs():
    """`uvx android-jev` runs the script called `android-jev`."""
    package = the_package()
    assert package["runtimeHint"] == "uvx"
    scripts = the_manifest()["project"]["scripts"]
    assert package["identifier"] in scripts, (
        f"A client told to run `uvx {package['identifier']}` looks for a command of "
        f"that name, and the package installs only {sorted(scripts)}."
    )
    assert scripts[package["identifier"]] == scripts["phone-control"]


def test_the_name_is_under_this_repository_owners_namespace():
    """GitHub authentication grants `io.github.<owner>/*`, case and all."""
    metadata = tomllib.loads(
        (REPOSITORY_ROOT / ".github" / "repository-metadata.toml").read_text()
    )
    owner, repository = metadata["repository"].split("/")
    assert the_entry()["name"] == f"io.github.{owner}/{repository}"
    assert (
        the_entry()["repository"]["url"]
        == (the_manifest()["project"]["urls"]["Homepage"])
    )


def test_the_readme_carries_the_marker_the_registry_verifies_ownership_by():
    """The registry reads the PyPI description, which is this README, for the name.

    The marker has to be followed by a boundary - whitespace, a tag, or the end of an
    HTML comment - or the registry's match fails.
    """
    readme = (REPOSITORY_ROOT / "README.md").read_text()
    name = re.escape(the_entry()["name"])
    assert re.search(rf"mcp-name:\s*{name}(?=\s|<|-->|$)", readme), (
        f"README.md needs `<!-- mcp-name: {the_entry()['name']} -->`; without it the "
        f"registry refuses the entry after the package is already on PyPI."
    )
    assert the_manifest()["project"]["readme"] == "README.md"


def test_the_entry_fits_the_registrys_limits():
    entry = the_entry()
    assert 1 <= len(entry["description"]) <= 100, len(entry["description"])
    assert 1 <= len(entry["title"]) <= 100
    assert entry["$schema"].startswith(
        "https://static.modelcontextprotocol.io/schemas/"
    )


def test_the_key_is_declared_secret_and_optional():
    """The key is optional, because `run_task` has a keyword fallback without it."""
    variables = {
        variable["name"]: variable for variable in the_package()["environmentVariables"]
    }
    assert variables["JEV_API_KEY"]["isSecret"] is True
    assert variables["JEV_API_KEY"]["isRequired"] is False
