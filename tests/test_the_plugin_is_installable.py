"""The repository is a Claude Code plugin marketplace, and the plugin in it works.

`claude plugin marketplace add FZ2000/android-jev` reads
`.claude-plugin/marketplace.json`, which lists one plugin whose root is this
repository's root: the canonical skill under `skills/` and the server in `src/` are
what it installs, not copies of them. These tests hold the manifests to that.

`claude plugin validate .` checks the manifest's shape, and was run when these files
were written; it is not run here because CI has no Claude Code to run it with.
"""

from __future__ import annotations

import json
import tomllib
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parent.parent
MANIFESTS = REPOSITORY_ROOT / ".claude-plugin"


def the_marketplace() -> dict:
    return json.loads((MANIFESTS / "marketplace.json").read_text())


def the_plugin() -> dict:
    return json.loads((MANIFESTS / "plugin.json").read_text())


def test_the_marketplace_lists_this_repository_as_its_one_plugin():
    (entry,) = the_marketplace()["plugins"]
    assert entry["source"] == "./"
    # The install id is `<entry name>@<marketplace name>` and skills are namespaced
    # under the manifest name; two names that differ make the README's command fail.
    assert entry["name"] == the_plugin()["name"]


def test_the_plugin_installs_the_canonical_skill():
    """A plugin root's `skills/` is scanned by default, and that is the original."""
    assert (REPOSITORY_ROOT / "skills" / "android-phone-control" / "SKILL.md").is_file()
    assert "skills" not in the_plugin(), "the default scan already finds it"


def test_the_plugin_carries_no_version_of_its_own():
    """Without one, an update follows the commit, and the package version stays single."""
    assert "version" not in the_plugin()


def test_the_server_is_mounted_as_the_readme_names_it():
    servers = the_plugin()["mcpServers"]
    assert list(servers) == ["android"]


def test_the_server_command_starts_a_script_the_package_installs():
    (script,) = the_plugin()["mcpServers"]["android"]["args"][1:]
    scripts = tomllib.loads((REPOSITORY_ROOT / "pyproject.toml").read_text())[
        "project"
    ]["scripts"]
    assert script.rstrip().endswith(" phone-control")
    assert "phone-control" in scripts


def test_the_key_is_sensitive_and_never_written_into_a_command_line():
    """The key reaches the server through the environment, not through `args`.

    An argument is visible to every process on the machine that can list processes,
    and a value substituted into a shell string is a value the shell parses.
    """
    key = the_plugin()["userConfig"]["jev_api_key"]
    assert key["sensitive"] is True
    server = the_plugin()["mcpServers"]["android"]
    assert "user_config" not in json.dumps(server["args"])
    assert server["env"]["PLUGIN_JEV_API_KEY"] == "${user_config.jev_api_key}"
