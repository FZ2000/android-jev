"""The decision recorder, and the checks that read what it records.

Two things are being tested here and they are different in kind.

The recorder is ordinary code: it wraps a decider, keeps what was sent and what
came back, and writes it out. It has to survive a decider that reshapes its
input, because one did - ``JevDecider`` used to pop the options out of the state
dictionary it was handed, which left the record claiming no options had been
offered while the reply's probabilities named twenty-three of them.

The checks are the instrument the whole scenario suite is judged with, so a
check that never fires would make every scenario pass vacuously. Each one is
therefore shown a deliberately bad exchange and has to complain, and shown a good
one and has to stay quiet. That is the difference between a check and a comment.
"""

from __future__ import annotations

import json

import pytest

from phone_control.decisions import Exchange, RecordingDecider
from phone_control.goal import FINISH, GIVE_UP, TAP_A_CONTROL, Choice, Step
from scenarios.checks import complaints_about
from scenarios.ground_truth import is_a_page_address

GOOD_STATE = {
    "goal": "open the settings app",
    "foreground_app": "com.example.launcher",
    "screen": {"width": 1080, "height": 2400},
    "controls_on_screen": [{"i": 0, "says": "list_item 'Settings' (middle centre)"}],
}

# The operations and the controls are separate questions.
GOOD_OPTIONS = {
    FINISH: "the goal is already achieved on this screen",
    GIVE_UP: "the goal cannot be reached from here",
    TAP_A_CONTROL: "tap one of the controls on screen",
}

GOOD_TARGETS = {"0": "list_item 'Settings' (middle centre)"}


def a_good_exchange(**overrides) -> Exchange:
    """An exchange with nothing wrong with it, for tests to break one thing in."""
    fields = {
        "index": 0,
        "state": dict(GOOD_STATE),
        "options": dict(GOOD_OPTIONS),
        "choice": TAP_A_CONTROL,
        "description": "tap one of the controls on screen",
        "confidence": 0.8,
        "probabilities": {FINISH: 0.1, GIVE_UP: 0.05, TAP_A_CONTROL: 0.85},
        "goal_achieved": 0.1,
        "targets": dict(GOOD_TARGETS),
        "target": "0",
    }
    fields.update(overrides)
    return Exchange(**fields)


def a_good_step(**overrides) -> Step:
    fields = {
        "index": 0,
        "action": TAP_A_CONTROL,
        "description": "tapped 'Settings'",
        "foreground_app": "com.android.settings",
        "confidence": 0.8,
        "changed_the_screen": True,
    }
    fields.update(overrides)
    return Step(**fields)


def what_is_wrong(
    about: str, exchange: Exchange | None = None, step: Step | None = None
):
    """The complaints about one part of an exchange, as plain strings."""
    found = complaints_about(
        exchange if exchange is not None else a_good_exchange(),
        step if step is not None else a_good_step(),
    )
    return [one.detail for one in found if one.about == about]


# --- the checks stay quiet on a good exchange ----------------------------


def test_nothing_is_wrong_with_an_exchange_that_is_fine():
    assert complaints_about(a_good_exchange(), a_good_step()) == []


# --- and complain about each specific fault ------------------------------


@pytest.mark.parametrize(
    "missing", ["goal", "foreground_app", "screen", "controls_on_screen"]
)
def test_a_state_missing_something_the_question_needs_is_complained_about(missing):
    state = {key: value for key, value in GOOD_STATE.items() if key != missing}

    complaints = what_is_wrong("the state", a_good_exchange(state=state))

    assert any(missing in one for one in complaints), complaints


def test_a_goal_that_is_blank_is_complained_about():
    complaints = what_is_wrong(
        "the state", a_good_exchange(state={**GOOD_STATE, "goal": "  "})
    )

    assert any("blank" in one for one in complaints), complaints


def test_offering_no_options_at_all_is_complained_about():
    complaints = what_is_wrong(
        "the options", a_good_exchange(options={}, probabilities={})
    )

    assert any("no operations were offered" in one for one in complaints), complaints


def test_an_option_with_no_description_is_complained_about():
    options = {**GOOD_OPTIONS, "tap_a_control": "  "}

    complaints = what_is_wrong("the options", a_good_exchange(options=options))

    assert any("no description" in one for one in complaints), complaints


def test_two_options_described_the_same_way_are_complained_about():
    """Resemblance between options is what degrades a decision, not their count."""
    options = {**GOOD_OPTIONS, "wait_but_different": GOOD_OPTIONS["tap_a_control"]}

    complaints = what_is_wrong("the options", a_good_exchange(options=options))

    assert any("identically" in one for one in complaints), complaints


def test_missing_ways_to_end_the_run_are_complained_about():
    complaints = what_is_wrong(
        "the options", a_good_exchange(options={TAP_A_CONTROL: "tap a control"})
    )

    assert any(FINISH in one for one in complaints), complaints
    assert any(GIVE_UP in one for one in complaints), complaints


def test_too_many_options_are_complained_about():
    options = {f"control_{index}": f"control {index}" for index in range(256)}
    options[FINISH] = "done"
    options[GIVE_UP] = "stop"

    complaints = what_is_wrong("the options", a_good_exchange(options=options))

    assert any("255" in one for one in complaints), complaints


def test_choosing_an_option_that_was_never_offered_is_complained_about():
    complaints = what_is_wrong("the reply", a_good_exchange(choice="send_message"))

    assert any("not among the options" in one for one in complaints), complaints


def test_probabilities_that_do_not_sum_to_one_are_complained_about():
    complaints = what_is_wrong(
        "the reply", a_good_exchange(probabilities={TAP_A_CONTROL: 0.4, FINISH: 0.1})
    )

    assert any("sum to" in one for one in complaints), complaints


def test_naming_an_option_the_numbers_ranked_lower_is_complained_about():
    """A choice question asks which is best, so the named one should be the best."""
    complaints = what_is_wrong(
        "the reply",
        a_good_exchange(probabilities={FINISH: 0.7, GIVE_UP: 0.1, TAP_A_CONTROL: 0.2}),
    )

    assert any("rating" in one for one in complaints), complaints


def test_a_reply_with_no_probabilities_at_all_is_complained_about():
    complaints = what_is_wrong("the reply", a_good_exchange(probabilities={}))

    assert any("no probabilities" in one for one in complaints), complaints


def test_a_reply_with_no_completion_answer_is_complained_about():
    complaints = what_is_wrong("the reply", a_good_exchange(goal_achieved=None))

    assert any("completion answer" in one for one in complaints), complaints


def test_an_answer_with_no_recorded_step_is_complained_about():
    complaints = [one.detail for one in complaints_about(a_good_exchange(), None)]

    assert any("no step" in one for one in complaints), complaints


def test_a_step_for_a_different_action_is_complained_about():
    complaints = what_is_wrong("the execution", step=a_good_step(action="go_home"))

    assert any("recorded" in one for one in complaints), complaints


def test_a_step_that_never_looked_for_an_effect_is_complained_about():
    complaints = what_is_wrong(
        "the execution", step=a_good_step(changed_the_screen=None)
    )

    assert any("changed the screen" in one for one in complaints), complaints


def test_a_complaint_says_which_exchange_and_which_part():
    complaint = complaints_about(
        a_good_exchange(index=7, choice="nonsense"), a_good_step()
    )[0]

    assert complaint.exchange == 7
    assert complaint.about == "the reply"
    assert "exchange 7" in str(complaint)


# --- the recorder --------------------------------------------------------


class ADeciderThatAnswers:
    """Answers with whatever it was told to, and remembers being asked."""

    def __init__(self, choice: Choice) -> None:
        self.choice = choice
        self.asked: list[dict] = []

    async def choose(self, state):
        self.asked.append(state)
        return self.choice


async def test_the_recorder_keeps_what_was_sent_and_what_came_back():
    decider = ADeciderThatAnswers(
        Choice(
            TAP_A_CONTROL,
            "tap the settings row",
            confidence=0.8,
            probabilities={TAP_A_CONTROL: 0.85},
            target="0",
        )
    )
    recorder = RecordingDecider(decider.choose)

    answer = await recorder.choose({**GOOD_STATE, "options": GOOD_OPTIONS})

    assert answer.action == TAP_A_CONTROL
    assert len(recorder.exchanges) == 1
    exchange = recorder.last
    assert exchange.index == 0
    assert exchange.choice == TAP_A_CONTROL
    assert exchange.options == GOOD_OPTIONS
    assert exchange.probability == 0.85
    assert exchange.state["goal"] == "open the settings app"


async def test_the_recorded_state_does_not_show_the_options_twice():
    """The options went with the question, so the state should not repeat them."""
    decider = ADeciderThatAnswers(Choice(TAP_A_CONTROL, "the settings row", target="0"))
    recorder = RecordingDecider(decider.choose)

    await recorder.choose(
        {**GOOD_STATE, "options": GOOD_OPTIONS, "option_kinds": {TAP_A_CONTROL: "tap"}}
    )

    assert "options" not in recorder.last.state
    assert "option_kinds" not in recorder.last.state
    assert recorder.last.options == GOOD_OPTIONS


async def test_the_recorder_survives_a_decider_that_empties_the_state():
    """Which is not hypothetical: JevDecider used to pop the options out.

    The record then claimed no options had been offered while the reply's
    probabilities named twenty-three of them, and every scenario failed a check
    that was right about the symptom and wrong about the cause.
    """

    class ADeciderThatReshapesItsInput:
        async def choose(self, state):
            state.pop("options", None)
            state.pop("option_kinds", None)
            return Choice(
                TAP_A_CONTROL, "the settings row", probabilities={TAP_A_CONTROL: 0.9}
            )

    recorder = RecordingDecider(ADeciderThatReshapesItsInput().choose)

    await recorder.choose({**GOOD_STATE, "options": GOOD_OPTIONS})

    assert recorder.last.options == GOOD_OPTIONS


async def test_the_recorder_numbers_exchanges_in_order():
    decider = ADeciderThatAnswers(Choice(TAP_A_CONTROL, "the settings row", target="0"))
    recorder = RecordingDecider(decider.choose)

    for _ in range(3):
        await recorder.choose({**GOOD_STATE, "options": GOOD_OPTIONS})

    assert [exchange.index for exchange in recorder.exchanges] == [0, 1, 2]


def test_a_recorder_that_was_never_asked_has_no_last_exchange():
    assert RecordingDecider(lambda state: None).last is None


async def test_the_record_is_written_as_one_json_object_per_line(tmp_path):
    """Readable and diffable, because the first question asked of a failure is
    what was actually sent."""
    decider = ADeciderThatAnswers(
        Choice(
            TAP_A_CONTROL,
            "the settings row",
            confidence=0.7,
            probabilities={TAP_A_CONTROL: 0.9},
        )
    )
    recorder = RecordingDecider(decider.choose)
    await recorder.choose({**GOOD_STATE, "options": GOOD_OPTIONS})

    destination = tmp_path / "nested" / "decisions.jsonl"
    recorder.write_to(destination)

    lines = destination.read_text().splitlines()
    assert len(lines) == 1
    record = json.loads(lines[0])
    assert record["choice"] == TAP_A_CONTROL
    assert record["probability"] == 0.9
    assert record["state"]["goal"] == "open the settings app"
    assert record["options"] == GOOD_OPTIONS


def test_an_empty_screen_is_a_defect_unless_the_state_says_why():
    """Some pages are never dumpable, and "nothing is known here, and here is the
    window" is a different claim from "there is nothing here"."""
    state = {**GOOD_STATE, "controls_on_screen": []}

    without = what_is_wrong("the state", a_good_exchange(state=state))
    assert any("nothing on it" in one for one in without), without

    with_the_reason = {
        **state,
        "nothing_is_known_about_this_screen": "the phone would not describe it",
    }
    assert what_is_wrong("the state", a_good_exchange(state=with_the_reason)) == []


# --- what the address bar is showing --------------------------------------
#
# Three states, and a probe has to tell the third from the other two: Chrome's own
# home, a results page, and a page the search led to. The probe this replaces asked
# only that the bar *not* hold the query - true of the home screen as well - and it
# reported the goal met from the first step of a run that had not searched, five
# times in one catalogue run. The decision is a pure function of what the bar holds,
# so it is argued about with strings rather than with a phone.


@pytest.mark.parametrize(
    ("held", "expected", "what"),
    [
        ("Search or type URL", False, "Chrome's own home screen"),
        ("Search", False, "a shorter prompt"),
        ("pixel phone wallpaper", False, "the query, typed but not submitted"),
        ("google.com/search?q=pixel+phone+wallpaper", False, "the results page"),
        (
            "https://www.google.com/search?q=pixel+phone+wallpaper",
            False,
            "the same, with a scheme",
        ),
        ("", False, "nothing at all"),
        ("en.wikipedia.org/wiki/Pixel_8a", True, "a page the search led to, no scheme"),
        (
            "https://www.androidauthority.com/best-pixel-wallpapers-123/",
            True,
            "a result page",
        ),
        (
            "https://www.pexels.com/search/pixel%20wallpaper/",
            True,
            "a site's own search page, reached from a search",
        ),
    ],
)
def test_what_the_address_bar_holds_that_is_a_page(held, expected, what):
    assert is_a_page_address(held) is expected, what
