"""Every published tool, called once for real, against its documented contract.

This is the surface an agent actually meets. The unit tests drive the same
functions directly, which proves the logic; this goes through the server object
the MCP client talks to, so the schema, the argument names and the published
descriptions are exercised too. A tool whose parameter is named something else
than the documentation says is broken for every caller and works perfectly in
every unit test.

The failures are checked as carefully as the successes. A tool that cannot do what
it was asked must arrive as a failure, and the first item in
``docs/tool-contract.md`` records what it cost when they did not: the decorator
returned the message, the result carried ``is_error: false``, and to every client
that was a tool which worked and whose answer was a sentence. A returned string is
the worst shape a failure can take, and this file now goes through the same handler
a client's request goes through, because the flag lives at that layer - reading the
inner call was a check of the wrong thing, and it is how the defect went unseen.
"""

from __future__ import annotations

import asyncio

import pytest

import phone_control.server as the_server_module
from phone_control.server import server
from scenarios.starting_states import open_settings_at

pytestmark = pytest.mark.device

# The tools an agent can see. Kept here as a list rather than counted, so adding
# a tool without documenting it fails this test.
PUBLISHED_TOOLS = (
    "ask_jev",
    "clipboard",
    "decide_next_action",
    "list_apps",
    "open_app",
    "open_url",
    "press_key",
    "read_notifications",
    "read_screen",
    "run_shell",
    "run_task",
    "scroll",
    "status",
    "swipe",
    "take_screenshot",
    "tap",
    "transfer_file",
    "type_text",
    "wait_for",
)


def the_tools():
    """The published tools, read once per test that needs them."""
    return asyncio.run(server.list_tools())


def a_known_screen(phone=None) -> None:
    """Put the phone on one screen a test can rely on, the way everything else does.

    Inherited state is the trap this suite keeps falling into: a test that says
    "open Settings" gets the page Settings was last on, which may be Bluetooth, and
    then fails a phone that is behaving perfectly.

    This used to run the intent itself, which is the third copy of that logic in the
    repository and the weakest of the three - it stopped Settings but not the search
    provider, which is a different package that remembers its last query and can put
    Settings back on a search screen holding it. The shared helper does both, so
    there is one implementation and this file is no longer the one that gets it
    wrong.
    """
    from phone_control.server import shared_phone

    open_settings_at(phone if phone is not None else shared_phone())


def call(tool_name: str, **arguments):
    """Call a tool the way a client would, and return the whole result.

    The parameter is named ``tool_name`` because one of the tools takes an
    argument called ``name``, and a helper that shadowed it could not be used on
    that tool at all.
    """
    return asyncio.run(server.call_tool(tool_name, arguments))


def the_failure(tool_name: str, **arguments) -> str:
    """What a client sees when a tool cannot do what it was asked.

    A failure is checked by going through the same handler a client's request goes
    through, rather than by catching the exception: the flag lives at that layer,
    and a test that reads the inner call is a test of the wrong thing.
    """
    result = the_result_a_client_sees(tool_name, **arguments)
    assert result.is_error is True, (
        f"{tool_name} was asked something it could not do and reported success: "
        f"{result.content[0].text!r}"
    )
    return result.content[0].text


def the_text(result) -> str:
    """The text a client would show a model, out of an MCP result."""
    parts = []
    for block in result.content:
        text = getattr(block, "text", None)
        if text is not None:
            parts.append(text)
    return "\n".join(parts)


# --- the surface itself --------------------------------------------------


def test_every_documented_tool_is_published():
    names = {tool.name for tool in the_tools()}

    assert names == set(PUBLISHED_TOOLS), (
        f"missing from the server: {sorted(set(PUBLISHED_TOOLS) - names)}; "
        f"published but undocumented: {sorted(names - set(PUBLISHED_TOOLS))}"
    )


def test_every_tool_explains_itself_in_words_a_model_can_use():
    for tool in the_tools():
        assert tool.description, tool.name
        assert len(tool.description) > 40, tool.name
        # The first line is what a model reads when choosing between tools.
        assert tool.description.strip().splitlines()[0].strip(), tool.name


def test_every_tool_declares_the_arguments_it_takes():
    """Every argument is described, because a caller chooses between tools by
    reading their arguments.

    Forty-six of them had no description at all - the whole published surface.
    `tap.target`, `type_text.text`, `run_task.goal`, every argument of every tool:
    an agent reading the schema learned the names and nothing about what to pass.

    A union type has ``anyOf`` rather than a single ``type``, which is correct
    JSON Schema and not a gap, so the check is that a type or a union is declared -
    and that the description is there either way.
    """
    for tool in the_tools():
        schema = tool.input_schema
        assert schema.get("type") == "object", tool.name
        for name, prop in schema.get("properties", {}).items():
            assert prop.get("description"), (
                f"{tool.name}.{name} is declared with no description, so a caller "
                "cannot know what to pass"
            )
            assert prop.get("type") or prop.get("anyOf"), (
                f"{tool.name}.{name} declares neither a type nor a union"
            )


# --- the tools that only read -------------------------------------------


def test_status_reports_the_model_and_the_screen_size():
    text = the_text(call("status"))

    assert "4C1F8A2E9D7B305" in text or "Pixel" in text
    assert "1080" in text
    assert "2400" in text


def test_read_screen_lists_what_is_on_the_screen():
    a_known_screen()

    text = the_text(call("read_screen"))

    assert "com.android.settings" in text
    assert "control" in text.lower() or "#0" in text


def test_read_screen_can_be_asked_for_one_thing():
    a_known_screen()

    text = the_text(call("read_screen", query="network"))

    assert "network" in text.casefold()


def test_a_screenshot_comes_back_as_an_image_block():
    result = call("take_screenshot", max_width=200)

    images = [block for block in result.content if block.type == "image"]
    assert images, "no image was returned"
    assert images[0].mime_type in {"image/png", "image/jpeg"}
    assert images[0].data, "the image block carried no data"


def test_list_apps_names_what_is_installed():
    """Under the name a person would use: this listing is how an agent recovers
    from an app it could not find, and "vending" is not an answer to which app
    the Play Store is."""
    text = the_text(call("list_apps", query="chrome"))

    assert "com.android.chrome" in text
    assert "Chrome" in text


def test_list_apps_can_be_narrowed():
    text = the_text(call("list_apps", query="chrome"))

    assert "com.android.chrome" in text
    assert "com.google.android.keep" not in text


def test_run_shell_returns_what_the_device_said():
    text = the_text(call("run_shell", command="echo hello from the test"))

    assert "hello from the test" in text


def test_the_clipboard_says_so_when_the_phone_does_not_have_one():
    """This phone does not implement `cmd clipboard`, and the honest answer is a
    failure rather than a success.

    Setting it used to be worse than absent: ``cmd clipboard set-text`` exits zero
    while printing "No shell command implementation", so the tool reported success
    and placed nothing. It now reports the phone's limitation as a failure and says
    what to do instead.
    """
    text = the_failure("clipboard", action="set", text="phone control wrote this")

    assert "does not implement" in text
    assert "type_text" in text


def test_transfer_file_moves_a_file_to_the_phone_and_back(tmp_path):
    source = tmp_path / "to_the_phone.txt"
    source.write_text("a file that travelled")

    call(
        "transfer_file",
        direction="to_phone",
        computer_path=str(source),
        phone_path="/sdcard/Download/from_the_test.txt",
    )
    assert "a file that travelled" in the_text(
        call("run_shell", command="cat /sdcard/Download/from_the_test.txt")
    )

    returned = tmp_path / "back_again.txt"
    call(
        "transfer_file",
        direction="from_phone",
        computer_path=str(returned),
        phone_path="/sdcard/Download/from_the_test.txt",
    )
    assert returned.read_text() == "a file that travelled"

    call("run_shell", command="rm -f /sdcard/Download/from_the_test.txt")


# --- the tools that move the phone --------------------------------------


def test_open_app_brings_an_app_to_the_front():
    call("press_key", key="home")

    text = the_text(call("open_app", name="Calculator", wait_seconds=8))

    assert "calculator" in text.casefold()
    assert "com.google.android.calculator" in the_text(call("status"))


def test_open_app_says_so_when_no_app_matches():
    text = the_failure("open_app", name="a thing that is not installed")

    assert "no installed app matches" in text.casefold()
    # And it names what to do next, because the caller relays this to a person.
    assert "list_apps" in text


def test_tap_can_act_on_a_named_control():
    a_known_screen()

    text = the_text(call("tap", target="Network & internet"))

    assert "network" in text.casefold()
    assert "network" in the_text(call("read_screen")).casefold()


def test_tap_can_act_on_a_point():
    call("press_key", key="home")

    text = the_text(call("tap", x=540, y=1200))

    assert text.strip()


def test_tap_says_so_when_no_control_matches():
    a_known_screen()

    text = the_failure("tap", target="a control that is not here")

    assert "no control" in text.casefold() or "nothing" in text.casefold()


def test_scroll_moves_the_list():
    a_known_screen()
    before = the_text(call("read_screen"))

    call("scroll", direction="down")
    after = the_text(call("read_screen"))

    assert after != before, "scrolling did not move anything"


def test_swipe_drags_between_two_points():
    a_known_screen()

    text = the_text(call("swipe", start_x=540, start_y=1800, end_x=540, end_y=600))

    assert text.strip()


def test_press_key_can_go_home():
    a_known_screen()

    call("press_key", key="home")

    assert "nexuslauncher" in the_text(call("status"))


def test_press_key_refuses_a_key_it_does_not_have():
    """Read through the handler, because the refusal is the thing being checked.

    This read `call`'s return value and asserted the sentence in it, which passed
    for as long as the tool *returned* its refusal instead of raising. Every other
    failure test in this file goes through the handler; this one did not, and that
    is exactly why it did not notice.
    """
    result = the_result_a_client_sees("press_key", key="an_invented_key")

    assert result.is_error is True
    assert "an_invented_key" in result.content[0].text


def test_type_text_puts_text_into_a_field():
    """Typed into a field the test put on screen itself.

    Settings' search box is the field to use here: it is on the top level of a
    screen the test asks for, whereas an app like Keep opens on an introduction
    that has no field at all, and the test then fails a phone that is fine.
    """
    a_known_screen()
    call("tap", target="Search Settings")

    text = the_text(call("type_text", text="bluetooth", submit=False))

    assert "typed" in text.casefold()
    assert "bluetooth" in the_text(call("read_screen")).casefold()


def test_wait_for_returns_when_the_text_appears():
    a_known_screen()

    text = the_text(call("wait_for", text="Network", timeout_seconds=10))

    assert "network" in text.casefold()


def test_asking_for_a_thing_that_is_not_there_is_a_failure_and_not_an_answer():
    """The measured case: a tool that could not do what it was asked used to reply
    with a sentence, and a sentence reads as an answer."""
    a_known_screen()

    text = the_failure("read_screen", query="a phrase that is not on this screen")

    assert "nothing on screen matches" in text.casefold()


def test_wait_for_says_so_when_the_text_never_appears():
    a_known_screen()

    text = the_failure(
        "wait_for", text="a phrase that never appears", timeout_seconds=3
    )

    assert "did not" in text.casefold() or "never" in text.casefold()


def test_read_notifications_opens_and_closes_the_shade():
    call("press_key", key="home")

    text = the_text(call("read_notifications", close_after=True))

    assert text.strip()
    assert "nexuslauncher" in the_text(call("status")), "the shade was left open"


def test_open_url_reaches_the_site():
    call("press_key", key="home")
    call("run_shell", command="am force-stop com.android.chrome")
    call("open_app", name="Chrome", wait_seconds=8)

    text = the_text(call("open_url", url="https://example.com", wait_seconds=8))

    assert "example" in text.casefold()
    assert "example" in the_text(call("read_screen")).casefold()


# --- the failures, and how they are reported ----------------------------


def the_result_a_client_sees(tool_name: str, **arguments):
    """Call a tool the way a client's request is handled, not the way a test is.

    ``server.call_tool`` raises: a failure is an exception there, and the flag a
    client reads is set further out, by the handler that turns the exception into a
    result. Going through that handler is the only way to assert what a caller
    actually sees, and getting this wrong is how the defect went unnoticed - every
    test called the inner function and read its return value, and the return value
    was always a success.
    """
    from mcp.types import CallToolRequestParams

    return asyncio.run(
        server._handle_call_tool(
            None, CallToolRequestParams(name=tool_name, arguments=arguments)
        )
    )


def test_a_failed_tool_reports_itself_as_an_error():
    """A failure has to arrive as a failure.

    Returning the message was the worst shape it could take: a returned string
    carries ``is_error: false``, so to the model and to every client UI it is a
    tool that worked and whose answer was a sentence. That is how a failure becomes
    invisible, and how this project shipped a tool reporting "Copied" over a file
    that never moved.
    """
    result = the_result_a_client_sees("scroll", direction="sideways")

    assert result.is_error is True, (
        "a tool that could not do what it was asked reported success, so to a "
        f"client it looks like it worked: {result.content[0].text!r}"
    )


def test_the_error_reads_as_a_sentence_after_the_prefix():
    """The SDK adds ``Error executing tool <name>: ``, so the message continues it."""
    text = the_result_a_client_sees("scroll", direction="sideways").content[0].text

    assert text.startswith("Error executing tool scroll: ")
    assert "down" in text, text
    assert "up" in text, text


def test_a_tool_that_needs_a_phone_reports_that_the_user_must_act():
    """Two kinds of failure, because they ask different things of a caller.

    A missing phone or an unauthorised one is the user's to fix, and the model
    cannot retry its way past it. Those go out as protocol errors rather than as
    tool errors, so a client is not invited to try again.
    """
    from phone_control.errors import AdbNotFound, NoPhoneConnected, PhoneNotAuthorized

    for kind in (NoPhoneConnected, PhoneNotAuthorized, AdbNotFound):
        assert kind in the_server_module.THE_USERS_TO_FIX, kind
    # And a condition the model can avoid is a tool error instead, so a client is
    # not told to go and find a cable over a wrong argument.
    from phone_control.errors import NoMatchingControl

    assert NoMatchingControl not in the_server_module.THE_USERS_TO_FIX


def test_every_tool_that_can_fail_is_wrapped_to_raise():
    """A tool without the decorator lets a phone failure out as a traceback, which
    reaches the client as an internal error rather than as a readable one."""
    import inspect

    for tool in the_tools():
        function = getattr(server, tool.name, None)
        if function is None:
            continue
        source = inspect.getsource(function)
        assert "@_turns_failures_into_errors" in source, (
            f"{tool.name} is not wrapped, so its failures are not readable"
        )
