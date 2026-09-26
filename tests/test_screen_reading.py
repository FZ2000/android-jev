"""Reading a screen: what an agent can name, and therefore act on."""

from __future__ import annotations

import pytest

from phone_control.errors import PhoneCommandFailed
from phone_control.options import describe_a_control
from phone_control.screen import (
    MAX_CONTROLS,
    Screen,
    ScreenArea,
    parse_screen,
    read_screen,
)


def a_screen_of_buttons(count: int) -> str:
    """A synthetic hierarchy with nothing but tappable buttons on it."""
    buttons = "".join(
        f'<node index="{position}" text="Button {position}" '
        f'resource-id="app:id/b{position}" class="android.widget.Button" '
        f'package="com.example" clickable="true" enabled="true" '
        f'bounds="[0,{position * 10}][100,{position * 10 + 8}]" />'
        for position in range(count)
    )
    return (
        "<?xml version='1.0' encoding='UTF-8' standalone='yes' ?>"
        '<hierarchy rotation="0">'
        '<node index="0" text="" class="android.widget.FrameLayout" '
        'package="com.example" clickable="false" bounds="[0,0][1080,2400]">'
        f"{buttons}</node></hierarchy>"
    )


def test_controls_are_numbered_in_the_order_they_appear(chat_screen):
    assert [control.label for control in chat_screen.controls] == [
        "Messages",
        "Alice See you at six",
        "Alice",
        "See you at six",
        "Type a message",
        "Send message",
    ]


def test_a_tappable_container_borrows_the_text_of_its_children(chat_screen):
    container = chat_screen.control_at(1)

    assert container.kind == "list_item"
    assert container.is_tappable
    assert container.label == "Alice See you at six"


def test_a_field_is_recognised_by_its_class(chat_screen):
    field = chat_screen.best_match("Type a message")

    assert field is not None
    assert field.is_editable
    assert field.text == "Type a message"


def test_an_icon_button_is_named_by_its_description(chat_screen):
    button = chat_screen.best_match("Send message")

    assert button is not None
    assert button.is_tappable
    assert button.description == "Send message"
    assert (button.area.center_x, button.area.center_y) == (970, 2170)


def test_a_control_with_no_area_is_left_out(chat_screen):
    assert chat_screen.best_match("Hidden") is None


@pytest.mark.parametrize(
    "class_name",
    [
        pytest.param("android.widget.EditText", id="plain-edit-text"),
        pytest.param("android.widget.AutoCompleteTextView", id="auto-complete"),
        pytest.param("android.widget.SearchView$SearchAutoComplete", id="search-view"),
    ],
)
def test_every_kind_of_text_field_is_recognised(class_name):
    """uiautomator reports the runtime class, so a suffix check misses subclasses."""
    xml = (
        "<hierarchy rotation='0'>"
        "<node text='' class='android.widget.FrameLayout' package='com.example' "
        "clickable='false' bounds='[0,0][1080,2400]'>"
        f"<node text='Search' class='{class_name}' package='com.example' "
        "clickable='true' bounds='[0,0][500,100]'/>"
        "</node></hierarchy>"
    )

    field = parse_screen(xml).best_match("Search")

    assert field is not None
    assert field.is_editable


def test_an_exact_label_beats_a_container_that_merely_mentions_it(chat_screen):
    matches = chat_screen.find("Alice")

    assert [control.label for control in matches] == [
        "Alice",
        "Alice See you at six",
    ]


def test_a_query_that_matches_nothing_returns_nothing(chat_screen):
    assert chat_screen.find("Nonexistent button") == []


def test_the_screen_carries_its_size_and_foreground_app(chat_screen):
    assert (chat_screen.width, chat_screen.height) == (1080, 2400)
    assert chat_screen.package == "com.example.chat"


def test_matching_ignores_letter_case(chat_screen):
    matched = chat_screen.best_match("send MESSAGE")

    assert matched is not None, "nothing matched a query differing only in case"
    # Which control matched, not merely that one did. The weaker version of this
    # test passed for as long as `best_match` returned anything at all, including
    # the wrong control - which is the failure a case-insensitive search would
    # actually produce.
    assert matched.label == "Send message", (
        f"the query matched {matched.label!r} rather than the control it names"
    )


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        pytest.param("[0,0][100,200]", (0, 0, 100, 200), id="origin-at-top-left"),
        pytest.param(
            "[40,2100][860,2240]", (40, 2100, 860, 2240), id="inset-rectangle"
        ),
    ],
)
def test_bounds_are_parsed_into_a_rectangle(raw, expected):
    area = ScreenArea.parse(raw)

    assert (area.left, area.top, area.right, area.bottom) == expected


def test_bounds_that_are_not_a_rectangle_are_refused():
    assert ScreenArea.parse("not bounds") is None


def test_an_empty_field_is_named_by_its_placeholder():
    """An untouched search box carries no text at all; `hint` is its only name.

    Android emits `hint` for this, which the first version of this parser ignored,
    so an empty search field was unnameable and never got offered as an option.
    """
    xml = (
        "<hierarchy rotation='0'>"
        "<node text='' class='android.widget.FrameLayout' package='com.example' "
        "clickable='false' bounds='[0,0][1080,2400]'>"
        "<node text='' class='android.widget.EditText' package='com.example' "
        "hint='Search' clickable='true' bounds='[0,0][500,100]'/>"
        "</node></hierarchy>"
    )

    field = parse_screen(xml).best_match("Search")

    assert field is not None
    assert field.is_editable
    assert field.hint == "Search"
    assert field.as_dict()["hint"] == "Search"


def test_typed_text_still_wins_over_the_placeholder():
    xml = (
        "<hierarchy rotation='0'>"
        "<node text='' class='android.widget.FrameLayout' package='com.example' "
        "clickable='false' bounds='[0,0][1080,2400]'>"
        "<node text='hello' class='android.widget.EditText' package='com.example' "
        "hint='Search' clickable='true' bounds='[0,0][500,100]'/>"
        "</node></hierarchy>"
    )

    field = parse_screen(xml).controls[0]

    assert field.label == "hello"


def test_the_listing_names_the_foreground_app_and_every_control(chat_screen):
    listing = chat_screen.as_text()

    assert "com.example.chat" in listing
    assert "#5" in listing
    assert "Send message" in listing


def test_a_screen_with_more_controls_than_the_budget_is_capped():
    screen = parse_screen(a_screen_of_buttons(MAX_CONTROLS + 50))

    assert len(screen.controls) == MAX_CONTROLS


def test_the_numbers_stay_contiguous_after_a_cap():
    """A gap in the numbering would be an invitation to tap the wrong thing."""
    screen = parse_screen(a_screen_of_buttons(MAX_CONTROLS + 50))

    assert [control.index for control in screen.controls] == list(range(MAX_CONTROLS))


def test_a_control_can_still_be_looked_up_by_its_number_after_a_cap():
    screen = parse_screen(a_screen_of_buttons(MAX_CONTROLS + 50))

    assert screen.control_at(MAX_CONTROLS - 1) is not None
    assert screen.control_at(MAX_CONTROLS) is None


@pytest.mark.parametrize(
    ("class_name", "clickable", "attributes", "expected"),
    [
        pytest.param("android.widget.EditText", "true", "", "text_field", id="a-field"),
        pytest.param(
            "android.widget.Switch", "true", "checkable='true'", "switch", id="a-switch"
        ),
        pytest.param("android.widget.Button", "true", "", "button", id="a-button"),
        pytest.param(
            "android.widget.ImageButton", "true", "", "button", id="an-icon-button"
        ),
        pytest.param("android.widget.ImageView", "true", "", "icon", id="a-plain-icon"),
        pytest.param(
            "androidx.recyclerview.widget.RecyclerView",
            "true",
            "",
            "container",
            id="a-list",
        ),
        pytest.param(
            "android.widget.TextView",
            "true",
            "",
            "list_item",
            id="a-clickable-label-is-a-row",
        ),
        pytest.param(
            "android.widget.TextView", "false", "", "other", id="an-inert-label"
        ),
    ],
)
def test_a_control_is_classified_by_what_a_person_would_call_it(
    class_name, clickable, attributes, expected
):
    """The class name is a toolkit detail; "button" is what a model can reason about."""
    xml = (
        "<hierarchy rotation='0'>"
        "<node text='' class='android.widget.FrameLayout' package='com.example' "
        "clickable='false' bounds='[0,0][1080,2400]'>"
        f"<node text='Thing' class='{class_name}' package='com.example' "
        f"clickable='{clickable}' bounds='[0,0][500,100]' {attributes}/>"
        "</node></hierarchy>"
    )

    control = parse_screen(xml).controls[0]

    assert control.kind == expected
    assert control.as_view()["kind"] == expected


# --- the multi-window document -------------------------------------------
#
# `uiautomator dump --windows` is rooted at <displays>, not <hierarchy>, and
# carries one <window> per window. Reading only the root tag is what makes the
# difference visible: counting nodes makes the two shapes look identical.

DISPLAYS_XML = """<?xml version='1.0' encoding='UTF-8' standalone='yes' ?>
<displays>
  <display id="0">
    <window index="0" title=" " bounds="[0,0][1080,2400]" active="false"
            focused="false" accessibility-focused="false" id="595" layer="1"
            type="TYPE_SYSTEM">
      <hierarchy rotation="0">
        <node index="0" text="" class="android.widget.FrameLayout"
              package="com.android.systemui" clickable="false"
              bounds="[0,0][1080,2400]" />
      </hierarchy>
    </window>
    <window index="1" title="" bounds="[0,0][1080,2400]" active="true"
            focused="true" accessibility-focused="false" id="585" layer="0"
            type="TYPE_INPUT_METHOD">
      <hierarchy rotation="0">
        <node index="0" text="" class="android.widget.FrameLayout"
              package="com.google.android.inputmethod.latin" clickable="false"
              bounds="[0,0][1080,2400]">
          <node index="0" text="q" resource-id="keyboard/q"
                class="android.widget.Button" package="com.google.android.inputmethod.latin"
                clickable="true" bounds="[0,1400][100,1500]" />
        </node>
      </hierarchy>
    </window>
  </display>
</displays>
"""

DISPLAYS_XML_WITHOUT_CONTENTS = """<?xml version='1.0' encoding='UTF-8' standalone='yes' ?>
<displays>
  <display id="0">
    <window index="0" title="Inaccessible" bounds="[0,0][1080,2400]" active="true"
            focused="true" accessibility-focused="false" id="1" layer="0"
            type="TYPE_APPLICATION" />
  </display>
</displays>
"""


def test_a_multi_window_dump_lists_every_window_with_its_type():
    screen = parse_screen(DISPLAYS_XML)

    assert [w.window_type for w in screen.windows] == [
        "TYPE_SYSTEM",
        "TYPE_INPUT_METHOD",
    ]


def test_controls_come_from_the_active_window_not_the_first_one():
    """The first window is a system one holding nothing; the keyboard is active."""
    screen = parse_screen(DISPLAYS_XML)

    assert [c.label for c in screen.controls] == ["q"]
    assert screen.controls[0].package == "com.google.android.inputmethod.latin"


def test_a_keyboard_window_is_recognised_from_its_type():
    assert parse_screen(DISPLAYS_XML).has_keyboard_window is True


def test_a_window_with_no_contents_is_recorded_but_not_read():
    """A null root emits no <hierarchy>, which is a usable signal in itself."""
    screen = parse_screen(DISPLAYS_XML_WITHOUT_CONTENTS)

    assert screen.windows[0].has_contents is False
    assert screen.controls == ()
    assert screen.has_keyboard_window is False


def test_a_plain_dump_still_parses(chat_screen):
    """Devices too old to know --windows return the single-hierarchy shape."""
    assert chat_screen.controls
    assert chat_screen.windows == ()


# --- the keyboard's own keys ---------------------------------------------
#
# The keyboard is a separate window, so a reading that took only the active one
# left its keys out entirely. They carry their character in content-desc rather
# than text, which is why searching for text finds none of them.

DISPLAYS_WITH_A_KEYBOARD_XML = """<?xml version='1.0' encoding='UTF-8' standalone='yes' ?>
<displays>
  <display id="0">
    <window index="0" bounds="[0,0][1080,2400]" active="true" focused="true"
            layer="0" type="TYPE_APPLICATION">
      <hierarchy rotation="0">
        <node index="0" text="Search" class="android.widget.EditText"
              package="com.example" clickable="true" bounds="[0,0][1080,200]" />
      </hierarchy>
    </window>
    <window index="1" bounds="[0,1400][1080,2400]" active="false" focused="false"
            layer="1" type="TYPE_INPUT_METHOD">
      <hierarchy rotation="0">
        <node index="0" text="" class="android.widget.FrameLayout"
              package="com.google.android.inputmethod.latin" clickable="false"
              bounds="[0,1400][1080,2400]">
          <node index="0" text="" content-desc="q" class="android.widget.Button"
                package="com.google.android.inputmethod.latin" clickable="true"
                bounds="[0,1500][100,1600]" />
          <node index="1" text="" content-desc="q" class="android.widget.Button"
                package="com.google.android.inputmethod.latin" clickable="true"
                bounds="[10,1510][90,1590]" />
          <node index="2" text="" content-desc="w" class="android.widget.Button"
                package="com.google.android.inputmethod.latin" clickable="true"
                bounds="[100,1500][200,1600]" />
        </node>
      </hierarchy>
    </window>
  </display>
</displays>
"""


def test_the_keyboards_keys_are_read_from_their_own_window():
    """Gboard reports each letter twice; the larger of the two is the real key."""
    screen = parse_screen(DISPLAYS_WITH_A_KEYBOARD_XML)

    assert sorted(key.label for key in screen.keyboard_keys) == ["q", "w"]
    assert screen.controls[0].label == "Search", "the app window is still the reading"


def test_a_key_can_be_tapped_like_any_other_control():
    """The keys live in their own window, so they are not in ``screen.controls``.

    Leaving them out of the targets took away the ability to press one, which is
    something a person does: a key is an ordinary button carrying its character in
    content-desc rather than in text.
    """
    from phone_control.options import the_targets_offered

    screen = parse_screen(DISPLAYS_WITH_A_KEYBOARD_XML)
    described, controls = the_targets_offered(screen)

    keys = {
        text
        for text in described.values()
        if any(key.label == "q" for key in screen.keyboard_keys) and "'q'" in text
    }
    assert keys, f"the keyboard keys are not offered as targets: {described}"
    assert any(control in screen.keyboard_keys for control in controls.values())


def test_a_screen_with_no_keyboard_offers_no_keys(chat_screen):
    assert chat_screen.keyboard_keys == ()


# The node structure below is copied from a dump of the Bluetooth page on this
# device, with the irrelevant rows removed and nothing else changed. It is here
# because the shape is the whole point and it is not the shape anyone would guess:
# the label and the switch are *siblings*, the switch is not clickable, and the
# clickable node is their parent. A hand-written fixture would have put the
# checkable flag on the row and the bug would never have been reproduced.
BLUETOOTH_PAGE_WITH_THE_SWITCH_OFF = """
<hierarchy rotation="0">
  <node index="0" class="android.widget.ScrollView" package="com.android.settings"
        bounds="[0,121][1080,2337]" scrollable="true">
    <node index="0" class="android.widget.LinearLayout" package="com.android.settings"
          clickable="true" bounds="[0,320][1080,489]">
      <node index="0" text="Use Bluetooth" class="android.widget.TextView"
            resource-id="com.android.settings:id/switch_text"
            bounds="[126,376][412,432]" />
      <node index="1" class="android.widget.Switch" resource-id="android:id/switch_widget"
            checkable="true" checked="false" clickable="false"
            bounds="[848,341][985,467]" />
    </node>
    <node index="1" class="android.widget.LinearLayout" package="com.android.settings"
          clickable="true" bounds="[0,550][1080,719]">
      <node index="0" text="Device name" class="android.widget.TextView"
            bounds="[126,606][412,662]" />
    </node>
  </node>
</hierarchy>
"""


def the_tappable_row_called(screen, name: str):
    """The control a decider would tap to act on `name`, and not its label.

    Both the row and the text inside it carry the words, and only one of them can be
    tapped. Asking for the row by what it is, rather than by which one `best_match`
    happens to prefer, is what keeps these tests about the row.
    """
    rows = [
        control
        for control in screen.controls
        if control.is_tappable and control.label == name
    ]
    assert rows, (
        f"nothing tappable is called {name!r}; on screen: "
        f"{[(c.kind, c.label) for c in screen.controls]}"
    )
    return rows[0]


def test_a_row_reports_the_position_of_the_switch_beside_it():
    """The row that turns Bluetooth on has to say whether Bluetooth is on.

    Measured cost of not doing this: the decider tapped that row five times over a
    phone that had turned Bluetooth on at the second tap, and the run ended as a
    stall reporting failure over a goal the phone had reached.
    """
    row = the_tappable_row_called(
        parse_screen(BLUETOOTH_PAGE_WITH_THE_SWITCH_OFF), "Use Bluetooth"
    )

    assert row.is_tappable, "the row is what a decider taps"
    assert not row.is_checked, "the switch in the dump is off"


def test_the_offered_description_says_the_switch_is_off():
    """And in the words the decider is actually shown, not just as a flag."""
    screen = parse_screen(BLUETOOTH_PAGE_WITH_THE_SWITCH_OFF)
    row = the_tappable_row_called(screen, "Use Bluetooth")

    said = describe_a_control(row, list(screen.controls), screen)

    assert "currently off" in said, (
        f"the offered control does not say where the switch is: {said!r}"
    )


# --- recovering from a dump helper that has stopped answering -------------
#
# A page that will not go idle and a helper that has wedged look identical from the
# outside - `read_screen`'s own words - and the remedy for the second is to kill the
# helper and dump again. That path is expensive: measured on a Pixel 8a, one attempt
# costs a dump, the wedge probe costs another dump, and the recovery costs a third.
#
# The loop stopped taking it in round 18, because a picture of the same page costs a
# twentieth as much and works. That left the path with nothing exercising it, which is
# the kind of gap this project keeps finding later: the recovery is still what a caller
# that cannot fall back to a picture depends on, so it is tested here - against a phone
# that answers exactly as a wedged one does, and then stops answering that way once it
# has been killed.

THE_ANSWER_OF_A_WEDGED_HELPER = "ERROR: could not get idle state."


class APhoneWithAWedgedHelper:
    """A phone whose dump helper answers nothing until it is killed.

    Both failures are the same words: the page will not go idle, and the helper has
    stopped. This one is the second, and killing it is what makes dumping work again.
    """

    def __init__(self, hierarchy: str = "") -> None:
        self.hierarchy = hierarchy
        self.commands: list[str] = []
        self.is_wedged = True

    def _kill_the_helper(self) -> None:
        self.is_wedged = False

    def run(self, arguments, timeout=None) -> str:
        self.commands.append(" ".join(arguments))
        return ""

    def run_binary(self, arguments, timeout=None) -> bytes:
        self.commands.append(" ".join(arguments))
        # The first attempt reads the file the failed dump never wrote.
        return b""

    def shell(self, command: str, timeout=None) -> str:
        self.commands.append(command)
        if "pkill" in command or "force-stop" in command:
            self._kill_the_helper()
            return ""
        if "uiautomator dump" in command:
            # The probe: the same words the page gave, which is what makes a wedged
            # helper indistinguishable from a busy page without killing it.
            return THE_ANSWER_OF_A_WEDGED_HELPER if self.is_wedged else ""
        if command.strip().startswith("cat "):
            return "" if self.is_wedged else self.hierarchy
        return ""

    def shell_binary(self, command: str, timeout=None) -> bytes:
        self.commands.append(command)
        return ("" if self.is_wedged else self.hierarchy).encode("utf-8")


def the_chat_screen_xml() -> str:
    """The hierarchy the helper produces once it has been killed."""
    from conftest import CHAT_SCREEN_XML

    return CHAT_SCREEN_XML


def test_a_wedged_helper_is_killed_and_the_dump_tried_again():
    """The recovery, end to end, on a phone that behaves like a wedged one."""
    phone = APhoneWithAWedgedHelper(the_chat_screen_xml())

    screen = read_screen(phone)

    assert [command for command in phone.commands if "pkill" in command], (
        "the wedged helper was never killed, so the reading cannot have recovered"
    )
    assert screen.controls, "the screen came back empty after recovering"
    assert screen.package, "the recovered reading lost the package it came from"


def test_the_recovery_is_not_taken_when_it_was_not_asked_for():
    """Which is what makes the loop's cheap reading cheap.

    Without this the loop pays three dumps on every page it cannot read, and that
    measured seventy seconds per reading.
    """
    phone = APhoneWithAWedgedHelper(the_chat_screen_xml())

    with pytest.raises(PhoneCommandFailed):
        read_screen(phone, recover=False)

    assert not [command for command in phone.commands if "pkill" in command], (
        "the helper was killed even though the caller asked for the cheap reading"
    )
    dumps = [command for command in phone.commands if "uiautomator dump" in command]
    assert len(dumps) == 1, (
        f"the cheap reading made {len(dumps)} dumps; it should make one and stop"
    )


# --- a screen the tree would not describe --------------------------------
#
# Two routes read this phone: its own description of the screen, and a picture of it
# when that description does not come. The picture route was built into the loop and
# never reached the tools, so `read_notifications` failed outright on a shade that
# would not dump - a shade is an overlay that does not go idle - where it should have
# returned what a photograph of it says. The rendering below is the last step of that
# route: without it the picture was read, stored, and thrown away.


def a_screen_read_from_a_picture(*lines: str) -> Screen:
    return Screen(
        controls=(),
        width=1080,
        height=2400,
        package="com.android.systemui",
        lines_read_from_a_picture=lines,
    )


def test_a_picture_of_the_screen_is_what_the_text_says():
    written = a_screen_read_from_a_picture(
        "Wi-Fi", "Battery 100%", "3 new messages"
    ).as_text()

    assert "Wi-Fi" in written
    assert "Battery 100%" in written
    assert "picture of it says" in written, (
        "the reading does not say where it came from, so it reads as the phone's own "
        "description of the screen"
    )


def test_a_screen_with_nothing_on_it_still_says_so():
    """The distinction the rendering exists to keep: nothing there, versus not described.

    If a pictured screen and an empty one both rendered as "no named controls", a
    failed read would look exactly like a confident reading of an empty screen.
    """
    nothing = Screen(controls=(), width=1080, height=2400, package="com.example")

    assert "No named controls were found" in nothing.as_text()
    assert "picture" not in nothing.as_text()


def test_the_structured_reading_carries_the_picture_too():
    """The form a decision model reads must not be blinder than the prose."""
    as_a_model_sees_it = a_screen_read_from_a_picture(
        "Wi-Fi", "Battery 100%"
    ).as_structured()

    assert as_a_model_sees_it["read_from_a_picture_of_the_screen"] == [
        "Wi-Fi",
        "Battery 100%",
    ]
    assert as_a_model_sees_it["controls"] == []


def test_a_screen_the_tree_describes_normally_carries_no_picture():
    """The fallback costs nothing on an ordinary screen."""
    from conftest import CHAT_SCREEN_XML

    screen = parse_screen(CHAT_SCREEN_XML)

    assert "read_from_a_picture_of_the_screen" not in screen.as_structured()
