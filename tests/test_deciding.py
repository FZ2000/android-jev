"""Asking Jev to choose, and turning its choice into one concrete action.

The shape of what is sent matters as much as the choosing. These tests pin the
three decisions that come from the measured prompting evidence: send the source
rather than a summary of it, name options in words rather than codes, and ask one
flat choice instead of several questions that can contradict each other.
"""

from __future__ import annotations

import json
import re

import pytest

from phone_control import server as server_module
from phone_control.jev import Decision
from phone_control.options import (
    GIVE_UP,
    SCROLL_FORWARD,
    TAP_A_CONTROL,
    WAIT,
)

SEND_BUTTON_INDEX = 5
MESSAGE_FIELD_INDEX = 4
CONVERSATION_ROW_INDEX = 1

SEND_OPTION = "send_message"
MESSAGE_FIELD_OPTION = "type_a_message"
CONVERSATION_ROW_OPTION = "alice_see_you_at_six"

# A long run of base64 characters is what an inlined image would look like.
BASE64_RUN = re.compile(r"[A-Za-z0-9+/]{120,}={0,2}")


class StubJev:
    """A Jev that answers exactly the way a test needs it to.

    Two questions are answered, because the tool asks two: which operation, and
    which control if the operation is to tap one. They are separate questions on
    purpose - one list holding both made a choice look unsure, because confidence
    measures how concentrated a choice is - so a stub has to answer both.
    """

    def __init__(
        self,
        option: str = TAP_A_CONTROL,
        confidence: float = 0.91,
        probabilities: dict[str, float] | None = None,
        target: str | None = None,
    ) -> None:
        self.option = option
        self.confidence = confidence
        self.probabilities = probabilities or {option: confidence}
        self.target = target

    def the_target(self, questions) -> str:
        """The first target offered, which is what most of these tests mean."""
        if self.target is not None:
            return self.target
        offered = questions.get("target", {}).get("criteria", {})
        return next(iter(offered), "")

    async def ask(self, state, questions):
        self.state = state
        self.questions = questions
        answers = {
            "operation": {
                "type": "choice",
                "choice": self.option,
                "confidence": self.confidence,
                "probabilities": self.probabilities,
            },
            "completion": {"type": "noul", "noul": 0.02},
        }
        if "target" in questions:
            answers["target"] = {
                "type": "choice",
                "choice": self.the_target(questions),
                "confidence": self.confidence,
                "probabilities": {self.the_target(questions): self.confidence},
            }
        return Decision(
            answers=answers,
            model="typesafe/jev-1.13-test",
            cost_usd=0.00002,
            input_tokens=476,
        )


@pytest.fixture
def live_screen(monkeypatch, chat_screen):
    """Make the tool surface read the canned chat screen instead of a phone."""

    async def current_screen():
        return chat_screen

    monkeypatch.setattr(server_module, "_current_screen", current_screen)
    return chat_screen


def use_jev(monkeypatch, stub: StubJev) -> StubJev:
    monkeypatch.setattr(server_module, "_jev", lambda: stub)
    return stub


def the_target_for(label: str) -> str:
    """The key of the offered target whose description names this label.

    Targets are keyed by position rather than by a name built from the label:
    a short key carries nothing the model has to decode, and the description is
    where the meaning is.
    """
    from conftest import CHAT_SCREEN_XML
    from phone_control.options import the_targets_offered
    from phone_control.screen import parse_screen

    described, _controls = the_targets_offered(parse_screen(CHAT_SCREEN_XML))
    for key, text in described.items():
        if label in text:
            return key
    raise AssertionError(f"no target names {label!r}; offered: {described}")


def offered_options(stub: StubJev) -> dict[str, str]:
    """The operations offered, which is the question about what to do."""
    return stub.questions["operation"]["criteria"]


def offered_targets(stub: StubJev) -> dict[str, str]:
    """The controls offered, which is a different question from the operations."""
    return stub.questions.get("target", {}).get("criteria", {})


async def test_a_confident_choice_becomes_a_tap_on_that_control(
    live_screen, monkeypatch
):
    use_jev(monkeypatch, StubJev(target=the_target_for("Send message")))

    reply = json.loads(await server_module.decide_next_action("send the message"))

    assert reply["next_action"] == {
        "tool": "tap",
        "arguments": {"target": SEND_BUTTON_INDEX},
        "element_label": "Send message",
        "element_kind": "button",
    }
    assert "advice" not in reply


# --- what is sent --------------------------------------------------------


async def test_the_screen_is_sent_in_the_accessibility_trees_own_terms(
    live_screen, monkeypatch
):
    """A derived summary scores far worse than the source it was derived from."""
    stub = use_jev(monkeypatch, StubJev())

    await server_module.decide_next_action("send the message")

    send_button = next(
        control
        for control in stub.state["controls"]
        if control["index"] == SEND_BUTTON_INDEX
    )
    # The tree's field names and values, not a sentence written about them.
    assert send_button["content-desc"] == "Send message"
    assert send_button["class"] == "android.widget.ImageButton"
    assert send_button["resource-id"] == "com.example.chat:id/send"
    assert send_button["bounds"] == [900, 2100, 1040, 2240]
    assert send_button["clickable"] is True


async def test_the_goal_travels_in_the_state_not_the_question(live_screen, monkeypatch):
    """The state has a field of its own; the question's instructions do not."""
    stub = use_jev(monkeypatch, StubJev())

    await server_module.decide_next_action("send the message")

    assert stub.state["goal"] == "send the message"
    assert "send the message" not in stub.questions["operation"]["instructions"]


async def test_the_state_carries_the_screen_it_was_read_from(live_screen, monkeypatch):
    stub = use_jev(monkeypatch, StubJev())

    await server_module.decide_next_action("send the message")

    assert stub.state["foreground_app"] == "com.example.chat"
    assert stub.state["screen"] == {"width": 1080, "height": 2400}


async def test_jev_is_sent_json_and_never_an_image(live_screen, monkeypatch):
    """Jev takes structured text. Serialising the state proves there are no bytes,
    and the pattern check catches an inlined image that is still valid JSON."""
    stub = use_jev(monkeypatch, StubJev())

    await server_module.decide_next_action("send the message")

    rendered = json.dumps(stub.state)
    assert "data:image" not in rendered
    assert not BASE64_RUN.search(rendered), "the state looks like it holds an image"


async def test_the_number_of_tokens_jev_actually_used_is_reported(
    live_screen, monkeypatch
):
    use_jev(monkeypatch, StubJev())

    reply = json.loads(await server_module.decide_next_action("send the message"))

    assert reply["jev_input_tokens"] == 476


# --- how the options are offered -----------------------------------------


async def test_options_are_named_in_words_rather_than_codes(live_screen, monkeypatch):
    """Described options beat bitstrings, so an option id must say what it means."""
    stub = use_jev(monkeypatch, StubJev())

    await server_module.decide_next_action("send the message")

    operations = offered_options(stub)
    targets = offered_targets(stub)

    # The operations are words, and the controls are described rather than keyed by
    # a code: a short key carries nothing to decode, and the description is where
    # the meaning is.
    assert TAP_A_CONTROL in operations
    assert not [key for key in operations if re.fullmatch(r"control_\d+", key)]
    assert "Send message" in " ".join(targets.values())


async def test_only_controls_that_can_be_acted_on_are_offered(live_screen, monkeypatch):
    stub = use_jev(monkeypatch, StubJev())

    await server_module.decide_next_action("send the message")

    targets = offered_targets(stub)
    described = " ".join(targets.values())
    assert "Send message" in described
    assert "Type a message" in described
    assert "Alice" in described
    # "Messages" is a plain heading, so offering it would be noise. It is not
    # tappable, so it is not a target at all.
    assert "labeled 'Messages'" not in described


async def test_the_scrolling_and_goal_options_sit_in_the_same_choice(
    live_screen, monkeypatch
):
    """One flat choice, so the alternatives cannot contradict each other."""
    stub = use_jev(monkeypatch, StubJev())

    await server_module.decide_next_action("send the message")

    offered = offered_options(stub)
    for option in ("go_back", "go_home", "finish", "give_up", "wait"):
        assert option in offered
    # Scrolling is offered only where something can actually scroll, and the
    # chat screen cannot, so offering it would be an action that does nothing.
    assert "scroll_forward" not in offered
    # Two questions, because operations and controls are asked apart: one list
    # holding both made a choice look unsure, since confidence measures how
    # concentrated a choice is.
    # Two questions for this tool: which operation, and which control. The goal
    # loop asks a third, for whether the screen shows the goal met, because it is
    # the loop that has to decide when to stop; this tool reports the probability
    # of the finish operation instead and lets the caller judge.
    assert sorted(stub.questions) == ["operation", "target"]


async def test_two_controls_with_the_same_words_get_distinct_option_ids(
    live_screen, monkeypatch
):
    from phone_control.screen import parse_screen

    twins = parse_screen(
        "<hierarchy rotation='0'>"
        "<node text='' class='android.widget.FrameLayout' package='com.x' "
        "clickable='false' bounds='[0,0][1080,2400]'>"
        "<node text='Send' class='android.widget.Button' package='com.x' "
        "clickable='true' bounds='[0,0][100,50]'/>"
        "<node text='Send' class='android.widget.Button' package='com.x' "
        "clickable='true' bounds='[0,100][100,150]'/>"
        "</node></hierarchy>"
    )

    async def current_screen():
        return twins

    monkeypatch.setattr(server_module, "_current_screen", current_screen)
    stub = use_jev(monkeypatch, StubJev(option="send"))

    await server_module.decide_next_action("send")

    targets = offered_targets(stub)
    described = " ".join(targets.values())
    assert len(targets) == 2, f"both buttons should be offered, got {targets}"
    # Two options the decider cannot tell apart always read as doubt, so the ones
    # whose coarse position and label collide are given their exact centre.
    assert "Send" in described
    assert len(set(targets.values())) == 2, f"they read identically: {targets}"


# --- what comes back -----------------------------------------------------


async def test_a_low_confidence_choice_tells_the_agent_to_look_at_the_screen(
    live_screen, monkeypatch
):
    use_jev(monkeypatch, StubJev(confidence=0.31))

    reply = json.loads(await server_module.decide_next_action("send the message"))

    assert "take_screenshot" in reply["advice"]
    # The action is still reported, so the caller can judge for itself. Advice
    # about the numbers is advice, not a veto: every wrong turn this project has
    # taken came from a rule that refused a correct answer for scoring low.
    assert reply["next_action"]["tool"] == "tap"


async def test_low_confidence_is_described_as_a_close_choice_not_a_unclear_screen(
    live_screen, monkeypatch
):
    """Confidence is how near the runners-up were, not whether Jev could answer."""
    use_jev(monkeypatch, StubJev(confidence=0.31))

    reply = json.loads(await server_module.decide_next_action("send the message"))

    assert "coin flip" in reply["advice"]
    assert "probably visual" not in reply["advice"]


async def test_a_middling_confidence_choice_asks_for_confirmation(
    live_screen, monkeypatch
):
    use_jev(monkeypatch, StubJev(confidence=0.55))

    reply = json.loads(await server_module.decide_next_action("send the message"))

    assert "read_screen" in reply["advice"]


async def test_a_choice_of_scroll_becomes_a_scroll_action(monkeypatch):
    """The scroll action maps to a scroll tool call, on a screen that can scroll."""
    from phone_control.screen import parse_screen

    scrollable = parse_screen(
        "<hierarchy rotation='0'>"
        "<node text='' class='android.widget.FrameLayout' package='com.example' "
        "clickable='false' bounds='[0,0][1080,2400]'>"
        "<node text='Messages' class='androidx.recyclerview.widget.RecyclerView' "
        "package='com.example' clickable='true' scrollable='true' "
        "bounds='[0,0][1080,2000]'/>"
        "</node></hierarchy>"
    )

    async def current_screen():
        return scrollable

    monkeypatch.setattr(server_module, "_current_screen", current_screen)
    use_jev(monkeypatch, StubJev(option="scroll_forward", confidence=0.8))

    reply = json.loads(await server_module.decide_next_action("find older messages"))

    assert reply["next_action"] == {
        "tool": "scroll",
        "arguments": {"direction": "down"},
    }


async def test_a_goal_that_is_already_achieved_produces_no_action(
    live_screen, monkeypatch
):
    use_jev(
        monkeypatch,
        StubJev(
            option="finish",
            probabilities={"finish": 0.94, SEND_OPTION: 0.05},
        ),
    )

    reply = json.loads(await server_module.decide_next_action("read the message"))

    assert reply["next_action"] is None
    assert reply["goal_already_achieved_probability"] == 0.94
    assert "already achieved" in reply["summary"]


async def test_the_runners_up_are_reported_as_alternatives(live_screen, monkeypatch):
    use_jev(
        monkeypatch,
        StubJev(
            probabilities={
                TAP_A_CONTROL: 0.7,
                WAIT: 0.2,
                SCROLL_FORWARD: 0.0,
            }
        ),
    )

    reply = json.loads(await server_module.decide_next_action("send the message"))

    assert reply["alternatives"] == [{"option": WAIT, "probability": 0.2}]


async def test_a_screen_with_nothing_actionable_points_at_the_screenshot(monkeypatch):
    from phone_control.screen import parse_screen

    empty = parse_screen(
        "<hierarchy rotation='0'>"
        "<node text='' class='android.widget.FrameLayout' package='com.x' "
        "clickable='false' bounds='[0,0][1080,2400]'/>"
        "</hierarchy>"
    )

    async def current_screen():
        return empty

    monkeypatch.setattr(server_module, "_current_screen", current_screen)
    # Recording is off, so no decision should be asked for at all: with nothing to
    # act on and no field to type into, there is no choice to put to a model.
    use_jev(monkeypatch, StubJev(option=GIVE_UP, probabilities={GIVE_UP: 0.8}))

    answer = await server_module.decide_next_action("do something")

    # An empty screen still has the operations that end a run, so the tool answers
    # with those rather than crashing on a choice with nothing in it.
    assert "cannot be advanced" in answer


@pytest.mark.parametrize(
    "option",
    [
        pytest.param("control_not_a_number", id="a-code-we-never-sent"),
        pytest.param("control_99", id="a-number-we-never-sent"),
        pytest.param("something_else_entirely", id="an-unrelated-word"),
    ],
)
async def test_an_option_that_was_never_offered_is_refused(
    live_screen, monkeypatch, option
):
    """Jev's answer is model output, so an unexpected one must not crash the tool."""
    use_jev(monkeypatch, StubJev(option=option))

    reply = json.loads(await server_module.decide_next_action("send the message"))

    assert reply["next_action"] is None
    assert "not an operation that was offered" in reply["summary"]


@pytest.mark.parametrize(
    ("options", "expected"),
    [
        pytest.param(
            ["urgent: the user needs this now", "later: it can wait"],
            {"urgent": "the user needs this now", "later": "it can wait"},
            id="key-colon-meaning",
        ),
        pytest.param(
            ["the user needs this now", "it can wait"],
            {"0": "the user needs this now", "1": "it can wait"},
            id="bare-meaning-keyed-by-position",
        ),
        pytest.param(
            ["https://a.com", "https://b.com"],
            {"0": "https://a.com", "1": "https://b.com"},
            id="a-url-is-not-a-key-value-pair",
        ),
        pytest.param(
            ["open https://example.com now"],
            {"0": "open https://example.com now"},
            id="a-url-inside-a-sentence",
        ),
        pytest.param(
            ["yes: the first", "yes: the second"],
            {"yes": "the first", "1": "yes: the second"},
            id="a-repeated-key-falls-back-to-its-position",
        ),
    ],
)
def test_options_are_read_into_named_criteria(options, expected):
    assert server_module._named_options(options) == expected


# --- a control must not be able to steal an action's name -----------------

TWIN_ACTION_SCREEN = (
    "<hierarchy rotation='0'>"
    "<node text='' class='android.widget.FrameLayout' package='com.example' "
    "clickable='false' bounds='[0,0][1080,2400]'>"
    "<node text='Scroll Down' class='android.widget.Button' package='com.example' "
    "clickable='true' bounds='[0,0][300,100]'/>"
    "<node text='Wait' class='android.widget.Button' package='com.example' "
    "clickable='true' bounds='[0,120][300,220]'/>"
    "</node></hierarchy>"
)


@pytest.fixture
def screen_named_like_the_built_in_actions(monkeypatch):
    """A screen with ordinary buttons whose text reads like the built-in actions."""
    from phone_control.screen import parse_screen

    screen = parse_screen(TWIN_ACTION_SCREEN)

    async def current_screen():
        return screen

    monkeypatch.setattr(server_module, "_current_screen", current_screen)
    return screen


async def test_a_control_named_like_an_action_does_not_take_that_action_over(
    screen_named_like_the_built_in_actions, monkeypatch
):
    """A button named like an action must not take that action over."""
    use_jev(monkeypatch, StubJev(option="wait"))

    reply = json.loads(await server_module.decide_next_action("wait for this"))

    assert reply["next_action"] == {
        "tool": "wait_for",
        "arguments": {"timeout_seconds": 5},
    }


async def test_a_control_named_like_an_action_is_still_offered(
    screen_named_like_the_built_in_actions, monkeypatch
):
    """It must keep an option of its own rather than vanishing from the choice."""
    stub = use_jev(monkeypatch, StubJev())

    await server_module.decide_next_action("scroll down")

    targets = offered_targets(stub)
    assert len(targets) == 2, f"both buttons should still be offered, got {targets}"
    # And the operations are untouched by controls that happen to be labelled the
    # same way: the two live in different questions now, so a control cannot take
    # an operation's place by being called after it.
    assert set(offered_options(stub)) >= {
        "finish",
        "give_up",
        "wait",
        "go_back",
        "go_home",
    }


def test_the_control_budget_leaves_room_for_the_built_in_actions():
    """Too many options raise a ValueError, which is not a phone error, so it
    would reach the agent as an internal crash rather than a readable message."""
    from phone_control.jev import JEV_MAX_ALTERNATIVES

    # Operations and targets are separate questions, so neither has to leave room
    # for the other: the only limit each faces is what a choice accepts.
    assert server_module.MAX_OFFERED_CONTROLS <= JEV_MAX_ALTERNATIVES
