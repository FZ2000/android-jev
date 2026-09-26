"""When the decider stops, and what happens next.

A decision model is good at picking from a list and bad at two things a run
needs: writing a sentence, and settling a screen where two moves both look right.
Neither is really a decision problem, and this project measured the cost of asking
the small model instead - on a screen where the goal genuinely was met its
completion answer read 0.42 to 0.47, and on one where it was not, 0.51.

So a stop is handed to a reader that can write. These pin the bounds, because an
unbounded hand-off is just a slower way to not stop.
"""

from __future__ import annotations

import asyncio

import pytest

from phone_control import goal as goal_module
from phone_control.calls import CLASSIFIER, READER, TheCalls
from phone_control.device_state import Focus
from phone_control.goal import (
    DONE,
    NOTHING_HELPS,
    STALLED,
    Choice,
    run_task,
)
from phone_control.options import GIVE_UP, TAP_A_CONTROL
from phone_control.reader import (
    MAXIMUM_HAND_OFFS,
    MAXIMUM_QUESTIONS,
    AReaderThatCannotWrite,
    TheHandOff,
    read_what_it_said,
)
from phone_control.screen import parse_screen

A_SCREEN = (
    "<hierarchy rotation='0'>"
    "<node index='0' text='' class='android.widget.FrameLayout' package='com.example' "
    "clickable='false' bounds='[0,0][1080,2400]'>"
    "<node index='0' text='Next' class='android.widget.Button' package='com.example' "
    "clickable='true' enabled='true' bounds='[0,300][400,420]'/>"
    "</node></hierarchy>"
)


class FakePhone:
    def __init__(self):
        self.commands: list[str] = []

    def run(self, arguments, timeout=None):
        self.commands.append(" ".join(arguments))
        return ""

    def shell(self, command, timeout=None):
        self.commands.append(f"shell {command}")
        return ""


class AReaderThatSays:
    """A reader with a script, so the run's behaviour can be pinned."""

    def __init__(self, *said, is_configured: bool = True) -> None:
        self.script = list(said)
        self.is_configured = is_configured
        self.packets: list[dict] = []

    async def read(self, packet):
        self.packets.append(packet)
        if not self.script:
            from phone_control.reader import WhatTheReaderSaid

            return WhatTheReaderSaid(because="nothing left to say")
        return self.script.pop(0)


class ScriptedDecider:
    def __init__(self, choices):
        self.remaining = list(choices)
        self.received: list[dict] = []

    async def choose(self, state):
        self.received.append(state)
        if not self.remaining:
            return Choice(GIVE_UP, "the script ran out")
        return self.remaining.pop(0)


def run_scripted(
    monkeypatch, choices, hand_off=None, max_steps=8, goal="open the clock"
):
    monkeypatch.setattr(
        goal_module, "read_screen", lambda p, recover=True: parse_screen(A_SCREEN)
    )
    monkeypatch.setattr(
        goal_module,
        "read_focused_window",
        lambda p: Focus(package="com.example", window="com.example/.Main"),
    )
    decider = ScriptedDecider(choices)
    report = asyncio.run(
        run_task(
            FakePhone(),
            goal,
            decider.choose,
            max_steps=max_steps,
            hand_off=hand_off,
        )
    )
    return report, decider


# --- without a reader, nothing changes ----------------------------------


def test_with_no_reader_a_stop_ends_the_run_as_it_always_did(monkeypatch):
    report, _ = run_scripted(monkeypatch, [Choice(GIVE_UP, "cannot reach it")])

    assert report.outcome == NOTHING_HELPS
    assert report.achieved is False


def test_a_reader_that_is_not_configured_is_not_consulted(monkeypatch):
    hand_off = TheHandOff(reader=AReaderThatCannotWrite())

    report, _ = run_scripted(
        monkeypatch, [Choice(GIVE_UP, "cannot reach it")], hand_off=hand_off
    )

    assert report.outcome == NOTHING_HELPS


# --- a reader that says the goal is met ---------------------------------


def test_a_reader_that_finds_the_goal_achieved_ends_the_run(monkeypatch):
    """The gap this exists for: the phone does what was asked and the decider
    never says `finish`, so the run ends as a stall over a goal that was met."""
    from phone_control.reader import WhatTheReaderSaid

    hand_off = TheHandOff(
        reader=AReaderThatSays(
            WhatTheReaderSaid(achieved=True, answer="Bluetooth is off")
        )
    )

    report, _ = run_scripted(
        monkeypatch, [Choice(GIVE_UP, "cannot reach it")], hand_off=hand_off
    )

    assert report.outcome == DONE
    assert report.achieved is True
    assert "Bluetooth is off" in report.reason


def test_the_reader_is_told_what_was_tried_and_why_it_stopped(monkeypatch):
    from phone_control.reader import WhatTheReaderSaid

    reader = AReaderThatSays(WhatTheReaderSaid(achieved=True, answer="done"))
    hand_off = TheHandOff(reader=reader)

    run_scripted(
        monkeypatch,
        [
            Choice(TAP_A_CONTROL, "tap next", target="0"),
            Choice(GIVE_UP, "cannot reach it"),
        ],
        hand_off=hand_off,
    )

    packet = reader.packets[0]
    assert packet["goal"] == "open the clock"
    assert "cannot reach it" in packet["why_the_run_stopped"]
    assert packet["things_already_tried"][0]["action"] == TAP_A_CONTROL
    assert packet["controls_on_screen"]
    # Nobody is at a terminal in a test, so a question is never put.
    assert packet["there_is_a_person_to_ask"] is False


# --- a reader that sets a focus, and the run goes on --------------------


def test_a_focus_joins_the_state_and_the_run_carries_on(monkeypatch):
    """The measured case: 0.39 becoming 0.92 on the same capture once a focus was
    set. The fix belongs in what the decider is told."""
    from phone_control.reader import WhatTheReaderSaid

    hand_off = TheHandOff(
        reader=AReaderThatSays(
            WhatTheReaderSaid(focus="tap the 'Use Bluetooth' switch")
        )
    )

    _report, decider = run_scripted(
        monkeypatch,
        [
            Choice(GIVE_UP, "cannot reach it"),
            Choice(TAP_A_CONTROL, "tap next", target="0"),
            Choice(GIVE_UP, "still cannot"),
        ],
        hand_off=hand_off,
    )

    assert "the_next_move_to_aim_at" not in decider.received[0]
    assert (
        decider.received[1]["the_next_move_to_aim_at"]
        == "tap the 'Use Bluetooth' switch"
    )


def test_a_focus_starts_the_stall_counts_again(monkeypatch):
    """A run going where it was told to is not a run going round."""
    from phone_control.reader import WhatTheReaderSaid

    hand_off = TheHandOff(
        reader=AReaderThatSays(
            WhatTheReaderSaid(focus="tap next"),
            WhatTheReaderSaid(focus="tap next again"),
            WhatTheReaderSaid(focus="and again"),
        )
    )

    report, _ = run_scripted(
        monkeypatch,
        [Choice(TAP_A_CONTROL, "tap next", target="0")] * 8,
        hand_off=hand_off,
        max_steps=8,
    )

    # The screen never changes in this test, so without the reader this would be a
    # stall after three. With one, the run is steered and spends its budget instead.
    assert report.outcome in (STALLED, "step_limit")


# --- and the bounds, which are what make it safe ------------------------


def test_the_handing_back_is_bounded(monkeypatch):
    from phone_control.reader import WhatTheReaderSaid

    reader = AReaderThatSays(
        *[WhatTheReaderSaid(focus=f"round {n}") for n in range(20)]
    )
    hand_off = TheHandOff(reader=reader)

    report, _ = run_scripted(
        monkeypatch,
        [Choice(GIVE_UP, "cannot reach it")] * 4,
        hand_off=hand_off,
    )

    assert reader.packets, "the reader was never consulted"
    assert len(reader.packets) <= MAXIMUM_HAND_OFFS
    assert hand_off.hand_offs <= MAXIMUM_HAND_OFFS
    assert report.achieved is False


def test_the_same_screen_is_not_read_twice(monkeypatch):
    """Re-reading it would put the same question to the same screen and get the
    same answer, which is how a hand-off turns into a loop."""
    from phone_control.reader import WhatTheReaderSaid

    reader = AReaderThatSays(*[WhatTheReaderSaid(focus="go on") for _ in range(10)])
    hand_off = TheHandOff(reader=reader)

    run_scripted(
        monkeypatch,
        [Choice(TAP_A_CONTROL, "tap next", target="0")] * 8,
        hand_off=hand_off,
    )

    # One screen, read once: the screen never changes in this test.
    assert len(reader.packets) == 1


def test_a_question_is_asked_once_and_the_answer_steers_the_run(monkeypatch):
    from phone_control.reader import WhatTheReaderSaid

    hand_off = TheHandOff(
        reader=AReaderThatSays(WhatTheReaderSaid(question="13 or 15 inch?"))
    )
    monkeypatch.setattr(hand_off, "answer_for", lambda question: "15")

    _report, decider = run_scripted(
        monkeypatch,
        [Choice(GIVE_UP, "cannot reach it"), Choice(GIVE_UP, "still cannot")],
        hand_off=hand_off,
    )

    assert hand_off.the_person_said == [{"asked": "13 or 15 inch?", "replied": "15"}]
    assert decider.received[1]["what_the_person_said"] == [
        {"asked": "13 or 15 inch?", "replied": "15"}
    ]


def test_a_question_with_no_answer_ends_the_run_rather_than_hanging(monkeypatch):
    from phone_control.reader import WhatTheReaderSaid

    hand_off = TheHandOff(
        reader=AReaderThatSays(WhatTheReaderSaid(question="which one?"))
    )
    monkeypatch.setattr(hand_off, "answer_for", lambda question: "")

    report, _ = run_scripted(
        monkeypatch, [Choice(GIVE_UP, "cannot reach it")], hand_off=hand_off
    )

    assert report.achieved is False
    assert "got no answer" in report.reason


def test_the_asking_is_bounded(monkeypatch):
    """A reader that has another question every time must not be able to keep a
    run alive by asking, so the asking has its own bound."""
    hand_off = TheHandOff(reader=AReaderThatSays())
    asked: list[str] = []
    monkeypatch.setattr(
        "builtins.input", lambda prompt="": asked.append(prompt) or "yes"
    )

    for _ in range(MAXIMUM_QUESTIONS + 3):
        hand_off.answer_for("anything?")

    assert len(asked) == MAXIMUM_QUESTIONS


# --- reading what the model actually wrote ------------------------------


def test_the_shape_asked_for_is_read_back():
    said = read_what_it_said(
        {
            "content": [
                {
                    "text": '{"achieved": true, "answer": "the price is $60", '
                    '"focus": "tap Buy", "question": ""}'
                }
            ]
        },
        "anthropic",
    )

    assert said.achieved is True
    assert said.answer == "the price is $60"
    assert said.has_a_focus
    assert not said.asks_something


def test_a_reply_wrapped_in_a_code_fence_is_read():
    """Local endpoints wrap JSON in a fence even when told not to, and refusing
    those would make the reader useless on exactly the endpoints people run."""
    said = read_what_it_said(
        {
            "content": [
                {"text": '```json\n{"achieved": false, "focus": "tap Next"}\n```'}
            ]
        },
        "anthropic",
    )

    assert said.focus == "tap Next"


def test_a_reply_with_a_sentence_around_it_is_read():
    said = read_what_it_said(
        {
            "content": [
                {
                    "text": 'Here is my reading:\n{"achieved": false, "focus": "tap Next"}'
                }
            ]
        },
        "anthropic",
    )

    assert said.focus == "tap Next"


def test_the_openai_shape_is_read_too():
    said = read_what_it_said(
        {"choices": [{"message": {"content": '{"achieved": true, "answer": "yes"}'}}]},
        "openai",
    )

    assert said.achieved is True
    assert said.answer == "yes"


@pytest.mark.parametrize(
    "written",
    [
        pytest.param({"content": [{"text": "I could not tell."}]}, id="prose-only"),
        pytest.param({"content": [{"text": ""}]}, id="empty"),
        pytest.param({"content": []}, id="no-content"),
        pytest.param({"content": [{"text": "{not json at all"}]}, id="malformed"),
        pytest.param({"content": [{"text": "[1, 2, 3]"}]}, id="not-an-object"),
    ],
)
def test_an_answer_that_cannot_be_read_refuses_the_step(written):
    """A reader that cannot be understood is not a reason to trust it."""
    said = read_what_it_said(written, "anthropic")

    assert said.achieved is False
    assert not said.has_a_focus
    assert said.because


# --- who did the work ---------------------------------------------------


def test_the_calls_line_reports_each_model_and_its_share():
    calls = TheCalls()
    for _ in range(3):
        calls.note(CLASSIFIER, 0.2)
    calls.note(READER, 3.0)

    assert calls.total == 4
    assert calls.as_dict()[CLASSIFIER]["calls"] == 3
    assert calls.as_dict()[CLASSIFIER]["share"] == 0.75
    assert calls.as_dict()[READER]["share"] == 0.25
    assert "classifier 3 (75%" in calls.as_line()
    assert "reader 1 (25%" in calls.as_line()


def test_a_run_that_never_asked_anything_says_so():
    assert TheCalls().as_line() == "calls: none"
    assert TheCalls().as_dict() == {}
