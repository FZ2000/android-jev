"""A client can mount this server, which is the only way anyone uses it.

Every other test in this suite calls a tool handler in-process, or calls
`run_task` as a function. None of them starts the server the way a harness does: as
a subprocess speaking MCP over stdio, with a command, arguments and an environment.
That leaves a whole surface untested — `__main__.py`, the entry point the wheel
installs, the handshake, and the tool list as a client actually receives it. If
`python -m phone_control` stopped working, the suite would stay green and nobody
could mount the thing.

No phone and no key are needed for any of this: mounting and asking what tools exist
are answered before the phone is touched, which is why this runs in CI.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

REPOSITORY_ROOT = Path(__file__).resolve().parent.parent

# How many tools the server publishes. Named here rather than counted from the
# server, because a number read from the thing being tested agrees with itself.
TOOLS_OFFERED = 19


def a_mounted_server():
    """The parameters a client would use, spelled the way a client spells them."""
    from mcp import StdioServerParameters

    return StdioServerParameters(
        command=sys.executable,
        args=["-m", "phone_control"],
        # A client that starts the server itself does not inherit this project's
        # virtualenv. Here the interpreter is already the right one, and `src` goes
        # on the path so the test does not depend on how the package was installed.
        env={**os.environ, "PYTHONPATH": str(REPOSITORY_ROOT / "src")},
        cwd=str(REPOSITORY_ROOT),
    )


async def test_a_client_can_mount_the_server_and_read_its_tool_list():
    """The handshake, and the tools a client is told about."""
    from mcp import ClientSession
    from mcp.client.stdio import stdio_client

    async with (
        stdio_client(a_mounted_server()) as (reading, writing),
        ClientSession(reading, writing) as session,
    ):
        hello = await session.initialize()
        offered = await session.list_tools()

    assert hello.server_info.name == "android", (
        f"the server introduced itself as {hello.server_info.name!r}; a client told to "
        f"mount `android` would not find it"
    )
    names = {tool.name for tool in offered.tools}
    assert "run_task" in names, (
        f"the one tool the skill tells an agent to use is not offered: {sorted(names)}"
    )
    assert len(names) == TOOLS_OFFERED, (
        f"the server offers {len(names)} tools; {TOOLS_OFFERED} were expected. If a "
        f"tool was added or removed, this number and the tool reference are the two "
        f"places that have to agree with it: {sorted(names)}"
    )


async def test_a_client_is_told_the_version_the_package_reports():
    """The third place a version is read from, and the one a client actually sees.

    It was a literal in `server.py` until the release milestone, three lines away
    from the package's own version and able to disagree with it silently.
    """
    from mcp import ClientSession
    from mcp.client.stdio import stdio_client

    import phone_control

    async with (
        stdio_client(a_mounted_server()) as (reading, writing),
        ClientSession(reading, writing) as session,
    ):
        hello = await session.initialize()

    assert hello.server_info.version == phone_control.__version__, (
        f"the server announces {hello.server_info.version!r} and the package reports "
        f"{phone_control.__version__!r}: a client would be told it has a different "
        f"release than the one installed"
    )


async def test_every_mounted_tool_explains_itself():
    """What a model reads when choosing between tools arrives over the wire."""
    from mcp import ClientSession
    from mcp.client.stdio import stdio_client

    async with (
        stdio_client(a_mounted_server()) as (reading, writing),
        ClientSession(reading, writing) as session,
    ):
        await session.initialize()
        offered = await session.list_tools()

    for tool in offered.tools:
        assert tool.description, f"{tool.name} is offered with no description"
        assert len(tool.description) > 40, (
            f"{tool.name}'s description is too short to choose between tools on"
        )
        assert tool.input_schema, f"{tool.name} is offered with no argument schema"


@pytest.mark.parametrize("missing", ["PHONE_CONTROL_ADB"])
async def test_mounting_does_not_need_a_phone(missing, monkeypatch):
    """Mounting is answered before the phone is touched, so it works without one.

    This is the property that lets the whole suite run in CI, and it is worth a test
    rather than a hope: pointing the server at an adb that does not exist is the
    cheapest way to be sure nothing on this path reaches for hardware.
    """
    from mcp import ClientSession
    from mcp.client.stdio import stdio_client

    monkeypatch.setenv(missing, "/nonexistent/adb")
    async with (
        stdio_client(a_mounted_server()) as (reading, writing),
        ClientSession(reading, writing) as session,
    ):
        hello = await session.initialize()
        offered = await session.list_tools()

    assert hello.server_info.name == "android"
    assert len(offered.tools) == TOOLS_OFFERED
