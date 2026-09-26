"""Asking Jev a typed question, and what a client is told when the ask is wrong.

`ask_jev` is the tool an agent uses for judgements that are not "what do I tap" -
whether an action worked, whether the screen is an error state. None of it was
tested, including the two argument checks that quietly returned a sentence: a
returned string carries `is_error: false`, so a caller who passed no options was
told by a successful tool that it needed options. That is the same defect this
project removed from every other tool, and it was still here.

The two seams are injected rather than mocked away: the screen the context is built
from, and the decision model. What is left under test is the whole of the tool's
own work - the questions it builds, the reply it shapes, and what it does when it
cannot.
"""

from __future__ import annotations

import asyncio
import json

import pytest

import phone_control.server as the_server_module
from phone_control.screen import parse_screen
from phone_control.server import server

A_SCREEN = (
    "<hierarchy rotation='0'>"
    "<node index='0' text='' class='android.widget.FrameLayout' package='com.example' "
    "clickable='false' bounds='[0,0][1080,2400]'>"
    "<node index='0' text='Send' class='android.widget.Button' package='com.example' "
    "clickable='true' enabled='true' bounds='[0,300][400,420]'/>"
    "</node></hierarchy>"
)


# --- the stand-ins -------------------------------------------------------


class AnAnswerOf:
    def __init__(self, **fields) -> None:
        self.__dict__.update(fields)


class ADecisionOf:
    """Whatever the client said, in the shape the tool reads it back."""

    def __init__(self, kind: str = "choice", **answer_fields) -> None:
        self.kind = kind
        self.answer = AnAnswerOf(**answer_fields)
        self.model = "a-model"
        self.cost_usd = 0.001

    def yes_or_no(self, name):
        return self.answer

    def scale(self, name):
        return self.answer

    def choice(self, name):
        return self.answer


class AJevThatSays:
    def __init__(self, decision: ADecisionOf) -> None:
        self.decision = decision
        self.asked: list[tuple[dict, dict]] = []
        self.is_configured = True

    async def ask(self, state, questions):
        self.asked.append((state, questions))
        return self.decision


def asking(monkeypatch, decision: ADecisionOf) -> AJevThatSays:
    """Wire the tool to a fixed screen and a decision model with one answer."""
    jev = AJevThatSays(decision)
    monkeypatch.setattr(the_server_module, "_jev", lambda: jev)
    monkeypatch.setattr(the_server_module, "_current_screen", lambda: _a_screen_soon())
    return jev


async def _a_screen_soon():
    return parse_screen(A_SCREEN)


def the_result(name: str, **arguments):
    """What a client actually sees, error flag included."""
    from mcp.types import CallToolRequestParams

    return asyncio.run(
        server._handle_call_tool(
            None, CallToolRequestParams(name=name, arguments=arguments)
        )
    )


def the_reply(result) -> dict:
    return json.loads(result.content[0].text)


# --- the three shapes of answer ------------------------------------------


def test_a_yes_or_no_answer_comes_back_as_a_probability(monkeypatch):
    asking(monkeypatch, ADecisionOf("yes_or_no", probability_yes=0.83))

    result = the_result(
        "ask_jev", instructions="did the message send?", question_type="yes_or_no"
    )
    reply = the_reply(result)

    assert result.is_error is not True
    assert reply["probability_yes"] == 0.83
    assert "0.83" in reply["summary"]
    assert reply["jev_model"] == "a-model"


def test_a_scale_answer_comes_back_with_its_legend(monkeypatch):
    """A position means nothing without the levels it was placed on."""
    asking(
        monkeypatch,
        ADecisionOf(
            "scale",
            position=0.4,
            confidence=0.9,
            probabilities={"low": 0.6, "high": 0.4},
            legend=["low", "high"],
        ),
    )

    reply = the_reply(
        the_result(
            "ask_jev",
            instructions="how far through is it?",
            question_type="scale",
            options=["low", "high"],
        )
    )

    assert reply["position"] == 0.4
    assert reply["legend"] == ["low", "high"]
    assert reply["probabilities"] == {"low": 0.6, "high": 0.4}


def test_a_choice_answer_names_the_option_it_chose(monkeypatch):
    asking(
        monkeypatch,
        ADecisionOf(
            "choice", option="send", confidence=0.77, probabilities={"send": 0.77}
        ),
    )

    reply = the_reply(
        the_result(
            "ask_jev",
            instructions="which did the user mean?",
            options=["send: the send button", "share: the share button"],
        )
    )

    assert reply["choice"] == "send"
    assert reply["confidence"] == 0.77


def test_the_question_carries_the_screen_and_whatever_else_was_given(monkeypatch):
    """Jev reads text only, so the context is the whole of what it knows."""
    jev = asking(monkeypatch, ADecisionOf("yes_or_no", probability_yes=0.5))

    the_result(
        "ask_jev",
        instructions="is the button enabled?",
        question_type="yes_or_no",
        state="the goal was to send a message",
    )

    state, _ = jev.asked[0]
    assert state["foreground_app"] == "com.example"
    assert any("Send" in line for line in state["on_screen"])
    assert state["context"] == "the goal was to send a message"


def test_with_no_extra_state_the_context_is_left_out(monkeypatch):
    jev = asking(monkeypatch, ADecisionOf("yes_or_no", probability_yes=0.5))

    the_result("ask_jev", instructions="is it done?", question_type="yes_or_no")

    state, _ = jev.asked[0]
    assert "context" not in state


# --- and when the ask cannot be made -------------------------------------


def test_an_ask_with_no_options_is_a_failure_not_an_answer(monkeypatch):
    """The defect this file was written for.

    Returning the sentence meant a client saw a successful tool whose answer was a
    complaint, and an agent reading that would carry on as if it had been told
    something.
    """
    asking(monkeypatch, ADecisionOf("choice", option="x", confidence=0.5))

    result = the_result("ask_jev", instructions="pick one", question_type="choice")
    text = result.content[0].text

    assert result.is_error is True, (
        f"an ask that could not be made reported success: {text!r}"
    )
    assert "options" in text.casefold()


def test_a_scale_with_too_few_levels_is_a_failure_too(monkeypatch):
    """One level is not a scale, and the same rule applies to it."""
    asking(
        monkeypatch,
        ADecisionOf("scale", position=0.5, confidence=0.5, probabilities={}, legend=[]),
    )

    result = the_result(
        "ask_jev",
        instructions="where is it?",
        question_type="scale",
        options=["only one"],
    )

    assert result.is_error is True
    assert "two levels" in result.content[0].text


@pytest.mark.parametrize("question_type", ["", "sentence", "CHOICE "])
def test_a_question_type_that_is_not_one_of_the_three_is_a_failure(
    monkeypatch, question_type
):
    asking(monkeypatch, ADecisionOf("choice", option="x", confidence=0.5))

    result = the_result("ask_jev", instructions="anything", question_type=question_type)

    assert result.is_error is True
    assert "question_type" in result.content[0].text
