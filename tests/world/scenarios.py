"""The loop's rules, each shown on a world small enough to read.

These are the same rules the device scenarios test, and they are here as well
because a rule about the loop should not need a phone to check. Tier 1 upward,
written in the order the rules were learned - almost every one of them is a run
that went wrong on a real device first.
"""

from __future__ import annotations

from .a_simulated_phone import (
    AControl,
    AScreen,
    AWorld,
    a_policy_of,
    finished,
    giving_up,
    opening,
    tapping,
    waiting,
)

A_SCROLLABLE_LIST = AControl("Event A", tappable=False, scrollable=True)


def the_goal_is_already_achieved(monkeypatch) -> tuple:
    """L1. Nothing to do, and the loop must not invent an action."""
    from .a_simulated_phone import drive

    world = AWorld([AScreen("done", controls=(AControl("Order 4821"),))])

    return drive(world, a_policy_of(finished()), monkeypatch=monkeypatch)


def one_tap_reaches_the_target(monkeypatch) -> tuple:
    """L2. A tap, and the world ending where the tap led."""
    from .a_simulated_phone import drive

    world = AWorld(
        [
            AScreen(
                "home",
                controls=(AControl("Tickets", row=0), AControl("About", row=1)),
                on={"tap:Tickets": "tickets"},
            ),
            AScreen("tickets", controls=(AControl("Buy", row=0),)),
        ]
    )

    return drive(
        world,
        a_policy_of(tapping("Tickets"), finished()),
        monkeypatch=monkeypatch,
    )


def an_app_is_opened_by_name(monkeypatch) -> tuple:
    """L3. The operation the regex used to get wrong twice over."""
    from .a_simulated_phone import drive

    world = AWorld([AScreen("home", controls=(AControl("Play Store", row=0),))])

    return drive(
        world,
        a_policy_of(opening("Clock"), finished()),
        goal="open the clock app",
        monkeypatch=monkeypatch,
    )


def a_slow_screen_needs_waiting(monkeypatch) -> tuple:
    """L4. A screen still loading is not a screen that cannot be used."""
    from .a_simulated_phone import drive

    world = AWorld(
        [
            AScreen(
                "home",
                controls=(AControl("Tickets", row=0),),
                on={"tap:Tickets": "tickets"},
            ),
            AScreen(
                "tickets",
                controls=(AControl("Buy", row=0),),
                on={"tap:Buy": "checkout"},
                # Counted in readings rather than in seconds, so it moved when the
                # loop stopped reading the screen twice per step. One wait is now
                # enough; the rule being tested is that waiting works at all.
                loads_after=1,
            ),
            AScreen("checkout", controls=(AControl("Pay now", row=0),)),
        ]
    )

    return drive(
        world,
        a_policy_of(tapping("Tickets"), waiting(), tapping("Buy"), finished()),
        monkeypatch=monkeypatch,
    )


def a_dead_end_is_undone_with_go_back(monkeypatch) -> tuple:
    """L5. Going somewhere unhelpful and getting out of it."""
    from phone_control.goal import GO_BACK, Choice

    from .a_simulated_phone import drive

    world = AWorld(
        [
            AScreen(
                "home",
                controls=(AControl("Blog", row=0), AControl("Tickets", row=1)),
                on={"tap:Blog": "blog", "tap:Tickets": "tickets"},
            ),
            AScreen(
                "blog",
                controls=(AControl("Older posts", row=0),),
                on={"key:back": "home"},
            ),
            AScreen("tickets", controls=(AControl("Buy", row=0),)),
        ]
    )

    return drive(
        world,
        a_policy_of(
            tapping("Blog"),
            Choice(
                GO_BACK, "back to where the tickets were", probabilities={GO_BACK: 0.8}
            ),
            tapping("Tickets"),
            finished(),
        ),
        monkeypatch=monkeypatch,
    )


def a_long_list_is_scrolled_twice(monkeypatch) -> tuple:
    """L6. The same action twice, on two different screens, is not a repeat."""
    from phone_control.goal import SCROLL_FORWARD, Choice

    from .a_simulated_phone import drive

    def scrolling() -> Choice:
        return Choice(
            SCROLL_FORWARD, "scroll down", probabilities={SCROLL_FORWARD: 0.8}
        )

    world = AWorld(
        [
            AScreen(
                "list1",
                controls=(A_SCROLLABLE_LIST, AControl("Event B", tappable=False)),
                on={"scroll_down": "list2"},
            ),
            AScreen(
                "list2",
                controls=(A_SCROLLABLE_LIST, AControl("Event D", tappable=False)),
                on={"scroll_down": "list3"},
            ),
            AScreen(
                "list3",
                controls=(A_SCROLLABLE_LIST, AControl("Buy", row=1)),
                on={"tap:Buy": "checkout"},
            ),
            AScreen("checkout", controls=(AControl("Pay now", row=0),)),
        ]
    )

    return drive(
        world,
        a_policy_of(scrolling(), scrolling(), tapping("Buy"), finished()),
        monkeypatch=monkeypatch,
    )


def a_cycle_between_two_screens_stops_as_stalled(monkeypatch) -> tuple:
    """L7. The failure that made the stall rules necessary.

    On a real phone the run cycled between a settings search and the back button
    for all ten steps and never opened the app it was asked for.
    """
    from .a_simulated_phone import drive

    world = AWorld(
        [
            AScreen("a", controls=(AControl("Next", row=0),), on={"tap:Next": "b"}),
            AScreen("b", controls=(AControl("Back", row=0),), on={"tap:Back": "a"}),
        ]
    )

    from phone_control.goal import Choice

    def first_control(state: dict) -> Choice:
        for key, text in (state.get("targets") or {}).items():
            return Choice(
                "tap_a_control",
                text,
                confidence=0.8,
                probabilities={"tap_a_control": 0.8},
                target=key,
            )
        return giving_up()

    return drive(world, a_policy_of(*[first_control] * 8), monkeypatch=monkeypatch)


def a_switch_says_which_way_it_is(monkeypatch) -> tuple:
    """L8. The fact that decides whether "turn off Bluetooth" is finished.

    Without it on a real phone the decider tapped the same switch five times - the
    goal was reached on the second tap - and the run ended as a stall over a goal
    that had been met.
    """
    from .a_simulated_phone import drive

    # Modelled as checkable because the loop must handle a screen that does publish
    # one. The device does not, for its settings toggles - see the scenario below,
    # which is the reality this one is the counterpart to.
    world = AWorld(
        [
            AScreen(
                "home",
                controls=(AControl("Bluetooth", checkable=True, checked=True),),
            ),
        ]
    )

    return drive(
        world,
        a_policy_of(tapping("Bluetooth"), finished()),
        goal="turn off bluetooth",
        monkeypatch=monkeypatch,
    )


def typing_is_not_offered_without_a_field(monkeypatch) -> tuple:
    """L9. An operation the loop cannot carry out is never offered.

    Measured cost of getting this wrong: a goal containing "search" offered typing
    whatever was on screen, and the option that could not be carried out read as the
    model being unsure when the model was never at fault.
    """
    from .a_simulated_phone import drive

    world = AWorld([AScreen("home", controls=(AControl("Nothing useful", row=0),))])

    happened = drive(
        world,
        a_policy_of(giving_up()),
        goal="search for the bruno mars tour",
        monkeypatch=monkeypatch,
    )
    return happened, happened.states[0]


def a_field_that_is_focused_is_told_what_it_holds(monkeypatch) -> tuple:
    """L10. The fact that makes a typed-but-unsent query tellable from a sent one."""
    from .a_simulated_phone import drive

    world = AWorld(
        [
            AScreen(
                "search",
                controls=(
                    AControl(
                        "Search",
                        editable=True,
                        focused=True,
                        tappable=False,
                        holds="bruno mars",
                    ),
                    AControl("Go", row=1),
                ),
                on={"tap:Go": "results"},
            ),
            AScreen("results", controls=(AControl("Bruno Mars", row=0),)),
        ]
    )
    # The goal has to carry the words, too: typing is offered when a field is ready
    # *and* there is something to put in it, and neither half is a fact about the
    # other.
    happened = drive(
        world,
        a_policy_of(giving_up()),
        goal="search for bruno mars",
        monkeypatch=monkeypatch,
    )
    return happened, happened.states[0]


def the_step_limit_ends_an_endless_run(monkeypatch) -> tuple:
    """L11. A run that keeps finding new screens spends its budget, honestly."""
    from phone_control.goal import SCROLL_FORWARD, Choice

    from .a_simulated_phone import drive

    def scrolling() -> Choice:
        return Choice(
            SCROLL_FORWARD, "scroll down", probabilities={SCROLL_FORWARD: 0.8}
        )

    screens = [
        AScreen(
            f"feed{n}",
            controls=(A_SCROLLABLE_LIST, AControl(f"Post {n}", tappable=False)),
            on={"scroll_down": f"feed{n + 1}"},
        )
        for n in range(1, 8)
    ]

    return drive(
        AWorld(screens),
        a_policy_of(*[scrolling() for _ in range(8)]),
        max_steps=3,
        monkeypatch=monkeypatch,
    )


def a_toggle_the_tree_does_not_publish(monkeypatch) -> tuple:
    """L12. The device's reality, which the world used to hide.

    Measured on Android 17: the Bluetooth toggle has no ``checkable`` attribute and
    no switch class, so the only thing that changes with it is the text beside it.
    The loop must still be able to do the job, and it must not pretend the state
    says something it does not.
    """
    from .a_simulated_phone import drive

    world = AWorld(
        [
            AScreen(
                "bluetooth",
                controls=(
                    AControl("Use Bluetooth", checkable=False, row=0),
                    AControl(
                        "Turn on Bluetooth to connect to other devices.",
                        tappable=False,
                        row=1,
                    ),
                ),
            )
        ]
    )

    happened = drive(
        world,
        a_policy_of(tapping("Use Bluetooth"), finished()),
        goal="turn off bluetooth",
        monkeypatch=monkeypatch,
    )
    return happened, happened.states[0]
