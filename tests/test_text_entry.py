"""Typing text: the encoding adb needs, and the quoting the device shell needs."""

from __future__ import annotations

from phone_control.adb import quote_for_device_shell
from phone_control.text_entry import (
    encode_for_input_text,
    needs_non_ascii_workaround,
    type_text_on_device,
)


def test_spaces_are_spelled_the_way_input_text_expects_them():
    assert encode_for_input_text("hello there") == "hello%sthere"


def test_a_line_break_is_removed_from_the_text_that_goes_to_input_text():
    assert "\n" not in encode_for_input_text("one\ntwo")


def test_ascii_text_needs_no_workaround():
    assert not needs_non_ascii_workaround("hello there")


def test_accented_text_is_flagged_as_needing_the_workaround():
    assert needs_non_ascii_workaround("café")


def test_emoji_are_flagged_as_needing_the_workaround():
    assert needs_non_ascii_workaround("nice 👍")


def test_typing_a_single_sentence_costs_one_command(fake_phone):
    type_text_on_device(fake_phone, "hello there")

    assert fake_phone.commands_matching("input text") == [
        "shell input text 'hello%sthere'"
    ]


def test_a_newline_becomes_an_enter_keypress(fake_phone):
    type_text_on_device(fake_phone, "hello\nthere")

    assert len(fake_phone.commands_matching("input text")) == 2
    assert len(fake_phone.commands_matching("keyevent 66")) == 1


def test_submitting_adds_a_final_enter(fake_phone):
    type_text_on_device(fake_phone, "hello", submit=True)

    assert len(fake_phone.commands_matching("keyevent 66")) == 1


def test_an_empty_string_types_nothing_at_all(fake_phone):
    type_text_on_device(fake_phone, "")

    assert fake_phone.commands_matching("input text") == []


def test_a_quote_in_the_text_cannot_escape_the_device_shell():
    quoted = quote_for_device_shell("it's fine")

    assert quoted == "'it'\\''s fine'"


def test_a_shell_metacharacter_is_neutralised_by_quoting():
    quoted = quote_for_device_shell("a; rm -rf /")

    assert quoted.startswith("'")
    assert quoted.endswith("'")
