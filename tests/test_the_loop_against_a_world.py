"""The loop's rules, against a phone that does not exist.

The device scenarios are the truth and they are slow. This is the other half: the
same rules on a world of a few screens, run by the real loop, in seconds. A failure
here means the architecture cannot do that thing; a failure there means the phone
disagrees, which is a different and more interesting problem.
"""

from __future__ import annotations

from phone_control.goal import DONE, STALLED, STEP_LIMIT
from world import scenarios


def test_l1_the_goal_is_already_achieved(monkeypatch):
    happened = scenarios.the_goal_is_already_achieved(monkeypatch)

    assert happened.outcome == DONE
    assert happened.actions == ["finish"]
    assert happened.world.received == [], "it touched a phone with nothing to do"


def test_l2_one_tap_reaches_the_target(monkeypatch):
    happened = scenarios.one_tap_reaches_the_target(monkeypatch)

    assert happened.outcome == DONE
    assert happened.actions == ["tap_a_control", "finish"]
    assert happened.world.page.name == "tickets"


def test_l3_an_app_is_opened_by_name(monkeypatch):
    happened = scenarios.an_app_is_opened_by_name(monkeypatch)

    assert happened.outcome == DONE
    assert "launch:Clock" in happened.world.received


def test_l4_a_slow_screen_needs_waiting(monkeypatch):
    happened = scenarios.a_slow_screen_needs_waiting(monkeypatch)

    assert happened.outcome == DONE
    assert happened.actions == ["tap_a_control", "wait", "tap_a_control", "finish"]
    assert happened.world.page.name == "checkout"


def test_l5_a_dead_end_is_undone_with_go_back(monkeypatch):
    happened = scenarios.a_dead_end_is_undone_with_go_back(monkeypatch)

    assert happened.outcome == DONE
    assert happened.world.page.name == "tickets"
    assert any("keyevent 4" in command for command in happened.world.received)


def test_l6_a_long_list_is_scrolled_twice(monkeypatch):
    """Two scrolls are the same action on two different screens, so neither is a
    repeat: only the same action on the same screen is."""
    happened = scenarios.a_long_list_is_scrolled_twice(monkeypatch)

    assert happened.outcome == DONE
    assert happened.actions == [
        "scroll_forward",
        "scroll_forward",
        "tap_a_control",
        "finish",
    ]


def test_l7_a_cycle_between_two_screens_stops_as_stalled(monkeypatch):
    happened = scenarios.a_cycle_between_two_screens_stops_as_stalled(monkeypatch)

    assert happened.outcome == STALLED
    assert happened.report.achieved is False
    assert "going round" in happened.report.reason
    # Caught by the repeat rule long before the budget ran out.
    assert len(happened.actions) < 8


def test_l8_a_switch_says_which_way_it_is(monkeypatch):
    """The state has to say the switch moved, or the decider cannot know the goal
    is met and will keep tapping it."""
    happened = scenarios.a_switch_says_which_way_it_is(monkeypatch)

    assert happened.outcome == DONE
    assert "currently on" in str(happened.states[0]["controls_on_screen"])


def test_l9_typing_is_not_offered_without_a_field(monkeypatch):
    _happened, state = scenarios.typing_is_not_offered_without_a_field(monkeypatch)

    assert "type_the_text" not in state["operations"]
    assert "focused_field" not in state


def test_l10_a_field_that_is_focused_is_told_what_it_holds(monkeypatch):
    _happened, state = scenarios.a_field_that_is_focused_is_told_what_it_holds(
        monkeypatch
    )

    assert state["focused_field"]["holds"] == "bruno mars"
    assert "type_the_text" in state["operations"]


def test_l11_the_step_limit_ends_an_endless_run(monkeypatch):
    happened = scenarios.the_step_limit_ends_an_endless_run(monkeypatch)

    assert happened.outcome == STEP_LIMIT
    assert len(happened.actions) == 3


def test_l12_a_toggle_the_tree_does_not_publish(monkeypatch):
    """The state must not claim a switch position it cannot see.

    Android publishes no checkable node for a settings toggle, so "currently off"
    is a fact the loop does not have. Saying it anyway would be the state inventing
    something, which is worse than the gap: a decision built on it cannot be
    questioned.
    """
    happened, state = scenarios.a_toggle_the_tree_does_not_publish(monkeypatch)

    says = str(state["controls_on_screen"])
    assert "currently on" not in says
    assert "currently off" not in says
    assert happened.actions == ["tap_a_control", "finish"]
