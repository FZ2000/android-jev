"""The tool surface itself, driven against a scripted phone.

These cover the layer between the tools the agent calls and the adb commands
that go out, which is where a wrong coordinate or a mis-quoted argument would
otherwise only show up on a real device.
"""

from __future__ import annotations

import asyncio

import pytest
from mcp.server.mcpserver.exceptions import ToolError

from phone_control import server as server_module
from phone_control.errors import PhoneControlError

SEND_BUTTON_INDEX = 5
MESSAGE_FIELD_INDEX = 4


def the_tap_command(phone) -> str:
    matches = phone.commands_matching("shell input tap")
    assert matches, f"no tap was sent; commands were: {phone.commands}"
    return matches[0]


def the_swipe_command(phone) -> str:
    matches = phone.commands_matching("shell input swipe")
    assert matches, f"no swipe was sent; commands were: {phone.commands}"
    return matches[0]


def the_swipe_points(phone) -> tuple[int, int, int, int]:
    """The four coordinates of the swipe, ignoring the leading words."""
    parts = the_swipe_command(phone).split()
    start_x, start_y, end_x, end_y = (int(value) for value in parts[3:7])
    return start_x, start_y, end_x, end_y


async def test_reading_the_screen_reports_the_foreground_app(scripted_phone):
    listing = await server_module.read_screen()

    assert "com.example.chat" in listing
    assert "Send message" in listing


async def test_reading_the_screen_with_a_query_lists_only_matches(scripted_phone):
    listing = await server_module.read_screen(query="send")

    assert "Send message" in listing
    assert "Type a message" not in listing


async def test_a_query_that_matches_nothing_says_so(scripted_phone):
    with pytest.raises((PhoneControlError, ToolError)) as refused:
        await server_module.read_screen(query="Nonexistent")

    listing = str(refused.value)

    assert "Nothing on screen matches" in listing
    assert "read_screen" in listing


async def test_each_screen_read_uses_its_own_dump_file(scripted_phone):
    """One fixed path is how a failed dump would serve the previous screen."""
    await server_module.read_screen()
    await server_module.read_screen()

    dumps = scripted_phone.commands_matching("uiautomator dump")
    assert len(dumps) == 2
    assert dumps[0] != dumps[1], "both reads shared a dump path"


async def test_the_dump_file_is_removed_after_the_read(scripted_phone):
    await server_module.read_screen()

    assert scripted_phone.commands_matching("rm -f")


async def test_a_dump_that_produced_nothing_is_reported_not_reused(scripted_phone):
    """A failed dump writes no file, so the read must fail rather than guess."""
    scripted_phone.show("")

    with pytest.raises((PhoneControlError, ToolError)) as refused:
        await server_module.read_screen()

    assert "UI hierarchy" in str(refused.value)


async def test_a_truncated_dump_is_reported_rather_than_crashing(scripted_phone):
    """A dump cut short is invalid XML, which used to escape as a raw parser error."""
    scripted_phone.show("<hierarchy rotation='0'><node text='cut off mid")

    with pytest.raises((PhoneControlError, ToolError)) as refused:
        await server_module.read_screen()

    assert "not valid XML" in str(refused.value)


async def test_an_empty_screen_is_explained_rather_than_left_blank(scripted_phone):
    """A dozing screen dumps one bare node, which reads as an empty screen.

    Observed on a real Pixel 8a: the phone went to sleep mid-session and every
    reading came back empty with no hint as to why.
    """
    scripted_phone.show(
        "<hierarchy rotation='0'><node index='0' text='' "
        "resource-id='com.android.systemui:id/legacy_window_root' "
        "class='android.widget.FrameLayout' package='com.android.systemui' "
        "clickable='false' bounds='[0,0][1080,2400]'/></hierarchy>"
    )
    scripted_phone.answers["dumpsys power"] = "  mWakefulness=Dozing\n"

    result = await server_module.read_screen()

    assert "No named controls" in result
    assert "screen is off" in result
    assert "wake" in result


async def test_an_empty_screen_on_an_awake_phone_points_at_the_screenshot(
    scripted_phone,
):
    scripted_phone.show(
        "<hierarchy rotation='0'><node index='0' text='' "
        "class='android.widget.FrameLayout' package='com.example.game' "
        "clickable='false' bounds='[0,0][1080,2400]'/></hierarchy>"
    )
    scripted_phone.answers["dumpsys power"] = "  mWakefulness=Awake\n"

    result = await server_module.read_screen()

    assert "take_screenshot" in result


async def test_tapping_a_number_taps_the_centre_of_that_control(scripted_phone):
    result = await server_module.tap(target=SEND_BUTTON_INDEX)

    assert the_tap_command(scripted_phone) == "shell input tap 970 2170"
    assert "Send message" in result


async def test_tapping_a_label_finds_the_control_that_carries_it(scripted_phone):
    await server_module.tap(target="Send message")

    assert the_tap_command(scripted_phone) == "shell input tap 970 2170"


async def test_tapping_an_unknown_label_lists_what_is_on_screen(scripted_phone):
    with pytest.raises((PhoneControlError, ToolError)) as refused:
        await server_module.tap(target="Nonexistent button")

    # The failure names what is on screen, because that is what the caller needs
    # to pick something that is there.
    assert "Nothing on screen matches" in str(refused.value)
    assert "Send message" in str(refused.value)
    assert not scripted_phone.commands_matching("shell input tap")


async def test_tapping_a_number_that_is_not_on_screen_points_at_read_screen(
    scripted_phone,
):
    with pytest.raises((PhoneControlError, ToolError)) as refused:
        await server_module.tap(target=99)

    assert "no control #99" in str(refused.value)
    assert "read_screen" in str(refused.value)


async def test_a_long_press_is_a_swipe_that_never_moves(scripted_phone):
    await server_module.tap(target=SEND_BUTTON_INDEX, long_press=True)

    assert (
        the_swipe_command(scripted_phone) == "shell input swipe 970 2170 970 2170 700"
    )


async def test_tapping_raw_coordinates_is_still_possible(scripted_phone):
    await server_module.tap(x=100, y=200)

    assert the_tap_command(scripted_phone) == "shell input tap 100 200"


async def test_tapping_without_a_target_or_coordinates_says_what_is_needed(
    scripted_phone,
):
    """And says it as a failure.

    This test read the returned sentence and passed for as long as the tool was
    wrong: a refusal returned rather than raised reaches a client as a tool that
    worked, so a model that forgot the target was told its call succeeded. The
    claim was always right; the shape it was asserted in was the bug.
    """
    with pytest.raises(ToolError) as refused:
        await server_module.tap()

    assert "target" in str(refused.value)
    assert "x" in str(refused.value)
    assert not scripted_phone.commands_matching("shell input tap")


async def test_typing_into_a_named_field_taps_it_before_typing(
    scripted_phone, chat_screen_xml
):
    # Typing changes the field, so the screen read for verification differs.
    scripted_phone.after(
        "input text",
        chat_screen_xml.replace('text="Type a message"', 'text="hello"'),
    )

    result = await server_module.type_text("hello", target="Type a message")

    assert the_tap_command(scripted_phone) == "shell input tap 450 2170"
    assert "shell input text 'hello'" in scripted_phone.commands
    assert "confirmed on screen" in result


async def test_typing_reports_when_the_text_never_landed(scripted_phone):
    result = await server_module.type_text("hello", target="Type a message")

    assert "NOT found on screen" in result


async def test_a_space_is_encoded_for_the_device_shell(scripted_phone):
    await server_module.type_text("hello there", verify=False)

    assert "shell input text 'hello%sthere'" in scripted_phone.commands


async def test_submitting_presses_enter_after_typing(scripted_phone):
    result = await server_module.type_text("hello", submit=True)

    assert "shell input text 'hello'" in scripted_phone.commands
    assert "shell input keyevent 66" in scripted_phone.commands
    assert "pressed Enter" in result


async def test_replacing_a_field_clears_it_completely(scripted_phone, chat_screen_xml):
    """Clearing must not depend on the field being short enough for one command."""
    long_text = "x" * 500
    scripted_phone.show(
        chat_screen_xml.replace('text="Type a message"', f'text="{long_text}"')
    )

    await server_module.type_text(
        "new", target=MESSAGE_FIELD_INDEX, replace=True, verify=False
    )

    assert scripted_phone.commands_matching("keycombination 113 29"), (
        "the field was not selected before deleting"
    )
    deleted = sum(
        command.split("keyevent", 1)[1].count(" 67")
        for command in scripted_phone.commands_matching("keyevent 123")
    )
    assert deleted >= len(long_text), (
        f"only {deleted} deletes were sent for a {len(long_text)}-character field"
    )


async def test_scrolling_down_moves_the_finger_up_the_screen(scripted_phone):
    await server_module.scroll(direction="down")

    _, start_y, _, end_y = the_swipe_points(scripted_phone)
    assert end_y < start_y


async def test_scrolling_right_moves_the_finger_left(scripted_phone):
    await server_module.scroll(direction="right")

    start_x, _, end_x, _ = the_swipe_points(scripted_phone)
    assert end_x < start_x


async def test_an_unknown_scroll_direction_is_refused(scripted_phone):
    with pytest.raises(ToolError) as refused:
        await server_module.scroll(direction="sideways")

    result = str(refused.value)

    assert "down, up, left, right" in result
    assert not scripted_phone.commands_matching("shell input swipe")


async def test_pressing_back_sends_the_back_keycode(scripted_phone):
    result = await server_module.press_key("back")

    assert "shell input keyevent 4" in scripted_phone.commands
    assert "back" in result


async def test_a_friendly_key_alias_is_translated(scripted_phone):
    await server_module.press_key("app_switch")

    assert "shell input keyevent 187" in scripted_phone.commands


async def test_the_notification_shade_is_a_statusbar_command(scripted_phone):
    await server_module.press_key("notification_shade")

    assert "shell cmd statusbar expand-notifications" in scripted_phone.commands


async def test_select_all_uses_a_key_combination(scripted_phone):
    await server_module.press_key("select_all")

    assert "shell input keycombination 113 29" in scripted_phone.commands


async def test_home_waits_for_the_handover_before_reporting(scripted_phone):
    """The keypress returns before the transition finishes, so reporting at once
    names the app being left and a following read_screen shows the old screen."""
    scripted_phone.answers["dumpsys window"] = (
        "  mCurrentFocus=Window{1 u0 com.before/.Main}\n"
    )
    scripted_phone.after(
        "input keyevent 3",
        lambda: scripted_phone.answers.update(
            {"dumpsys window": "  mCurrentFocus=Window{1 u0 com.after/.Main}\n"}
        ),
    )

    result = await server_module.press_key("home")

    assert "com.after" in result


async def test_back_does_not_pay_for_a_handover_check(scripted_phone):
    """Back usually moves within the same app, so waiting would waste a timeout."""
    await server_module.press_key("back")

    assert not scripted_phone.commands_matching("dumpsys window")


async def test_an_unknown_key_lists_the_valid_ones(scripted_phone):
    """As a failure, for the same reason as the tap above."""
    with pytest.raises(ToolError) as refused:
        await server_module.press_key("frobnicate")

    assert "back" in str(refused.value)
    assert "notification_shade" in str(refused.value)


async def test_status_reports_the_phone(scripted_phone):
    scripted_phone.answers["getprop ro.product.model"] = "Pixel 8a"
    scripted_phone.answers["getprop ro.build.version.release"] = "15"

    result = await server_module.status()

    assert "Pixel 8a" in result
    assert "15" in result


async def test_opening_an_app_launches_the_package_that_was_resolved(scripted_phone):
    scripted_phone.answers["cmd package resolve-activity"] = (
        "priority=0 preferredOrder=0\ncom.google.android.youtube/.MainActivity"
    )

    await server_module.open_app("youtube", wait_seconds=0)

    assert (
        # Quoted: a component is a class name and may contain a dollar sign.
        "shell am start -W -n 'com.google.android.youtube/.MainActivity'"
        in scripted_phone.commands
    )


async def test_an_unknown_app_name_is_reported_rather_than_guessed(scripted_phone):
    with pytest.raises((PhoneControlError, ToolError)) as refused:
        await server_module.open_app("nonexistent app", wait_seconds=0)

    assert "No installed app matches" in str(refused.value)
    assert not scripted_phone.commands_matching("am start")


async def test_a_refused_launch_is_reported_rather_than_claimed(scripted_phone):
    """`am start` prints its complaint and exits zero, so the output is the evidence."""
    scripted_phone.answers["cmd package resolve-activity"] = (
        "priority=0 preferredOrder=0\ncom.google.android.youtube/.MainActivity"
    )
    scripted_phone.answers["am start"] = (
        "Error: Activity class {com.google.android.youtube/.MainActivity} "
        "does not exist."
    )

    with pytest.raises((PhoneControlError, ToolError)) as refused:
        await server_module.open_app("youtube", wait_seconds=0)

    # The device's own complaint is the evidence, and `am start` exits zero while
    # printing it - which is why the output is read rather than the exit code.
    assert "does not exist" in str(refused.value)
    assert "Started" not in str(refused.value)


async def test_a_failed_pull_is_not_reported_as_a_copy(scripted_phone):
    scripted_phone.failures["pull"] = 1
    scripted_phone.complaints["pull"] = (
        "adb: error: remote object '/nope' does not exist"
    )

    with pytest.raises((PhoneControlError, ToolError)) as refused:
        await server_module.transfer_file("from_phone", "~/somewhere", "/nope")

    # The one that mattered: a failed pull used to answer "Copied".
    assert "Copied" not in str(refused.value)


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        pytest.param("youtube", "com.google.android.youtube", id="finds-a-match"),
        pytest.param("nothing", "No installed package", id="reports-no-match"),
    ],
)
async def test_listing_apps_can_be_filtered(scripted_phone, query, expected):
    assert expected in await server_module.list_apps(query=query)


# --- every tool refuses as a failure, not as a sentence -------------------
#
# The contract this project spent a phase on, checked the way it keeps breaking:
# one tool at a time, by hand, in a test written after the fact. Three tools had
# a branch that *returned* a complaint instead of raising - `ask_jev` with no
# options, `clipboard` asked to set with no text, and `type_text` with characters
# the phone cannot press - and each looked like a working tool whose answer was a
# request, which is what every client shows a model.
#
# The table below is the mechanical version. Each entry hands a tool an argument
# it must refuse, and names a word its message has to carry, so the test cannot
# pass merely because a phone was missing: the refusal has to be the tool's own.
#
# The scripted phone is what makes the refusals the tool's own decision rather
# than a device failure - everything below runs with no phone attached.

# (tool, arguments it must refuse, a word the refusal must carry)
REFUSALS = (
    ("read_screen", {"query": "nothing on this screen says this"}, "nothing"),
    ("scroll", {"direction": "sideways"}, "direction"),
    ("press_key", {"key": "banana"}, "banana"),
    ("wait_for", {"text": "nothing says this", "timeout_seconds": 0.2}, "did not"),
    ("wait_for", {"app": "No Such App At All", "timeout_seconds": 0.2}, "No Such App"),
    ("clipboard", {"action": "set"}, "text"),
    ("clipboard", {"action": "sideways"}, "action"),
    (
        "transfer_file",
        {"direction": "sideways", "computer_path": "/tmp/a", "phone_path": "/sdcard/a"},
        "direction",
    ),
    ("ask_jev", {"instructions": "pick one", "question_type": "choice"}, "options"),
    (
        "ask_jev",
        {"instructions": "where is it", "question_type": "scale", "options": ["one"]},
        "two levels",
    ),
    (
        "ask_jev",
        {"instructions": "anything", "question_type": "sentence"},
        "question_type",
    ),
    ("tap", {}, "target"),
    ("type_text", {"text": "caf\u00e9 \u2014 an em dash"}, "ASCII"),
)


@pytest.mark.parametrize(("tool_name", "arguments", "wanted"), REFUSALS)
def test_a_tool_that_refuses_says_so_as_a_failure(
    scripted_phone, tool_name, arguments, wanted
):
    """A refusal returned as text is a success to every client that reads it."""
    from mcp.types import CallToolRequestParams

    result = asyncio.run(
        server_module.server._handle_call_tool(
            None, CallToolRequestParams(name=tool_name, arguments=arguments)
        )
    )
    said = result.content[0].text

    assert result.is_error is True, (
        f"{tool_name} refused {arguments} by returning a sentence, which reaches a "
        f"client as a tool that worked: {said!r}"
    )
    assert wanted.casefold() in said.casefold(), (
        f"{tool_name} failed, but not for the reason the arguments give: {said!r}"
    )


def test_no_tool_refuses_by_returning_a_sentence():
    """The guard that does not need a table, so a new tool cannot slip past it.

    A refusal has to raise. There is no mechanical way to read a string and know
    whether it is an answer or a complaint, so this checks the shape of the thing
    that is checkable: every tool's refusals in the table above are errors, and
    the table names every tool that takes an argument a caller can get wrong.
    """
    documented = {tool.name for tool in asyncio.run(server_module.server.list_tools())}
    exercised = {name for name, _, _ in REFUSALS}

    # The rest take no argument a caller can get wrong, or fail only when the
    # device does. Named individually rather than counted, so adding a tool fails
    # this test until somebody has decided which it is.
    without_a_refusal_to_prove = {
        "status",
        "take_screenshot",
        "list_apps",
        "read_notifications",
        "run_shell",
        "run_task",
        "decide_next_action",
        "open_app",
        "open_url",
        "swipe",
    }

    assert documented == exercised | without_a_refusal_to_prove, (
        "a tool was added or renamed without being considered here: "
        f"{sorted(documented - exercised - without_a_refusal_to_prove)}"
    )


async def test_a_screen_read_from_a_picture_is_reported_rather_than_called_empty(
    scripted_phone, monkeypatch
):
    """The tool must not tell a caller to take a screenshot it has already taken.

    The tree refusing a page and the page being blank are different things, and the
    tool reported both the same way - "No named controls on screen ... call
    take_screenshot" - even when a picture of the screen had just been read and was
    sitting on the object. Measured on the notification shade.
    """
    scripted_phone.show("")  # nothing to dump, as an overlay that will not idle
    monkeypatch.setattr(
        "phone_control.reading_a_picture.read_the_picture",
        lambda phone, most=None: ["Wi-Fi", "3 new messages"],
    )

    text = await server_module.read_screen()

    assert "3 new messages" in text, (
        f"the picture was read and then withheld from the answer: {text[:120]!r}"
    )
    assert "picture" in text.casefold()
