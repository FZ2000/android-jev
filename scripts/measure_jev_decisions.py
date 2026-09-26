#!/usr/bin/env python3
"""Measure whether Jev picks the right option, on items whose answer we know.

The journey tests verify *outcomes*: they ask the phone to do something and then
check the phone's state. That is the right test for "did the job get done", and
it catches a false success. It cannot tell a correct decision from a lucky one,
because a run that reaches the right place by a wrong route still ends in the
right place.

This measures the decision itself. Each item is a screen built so that exactly one
option is correct *by construction*, so the answer is known before the question is
asked and agreement is not a matter of opinion.

Three conditions, because the published measurements say the thing that costs
accuracy is options that resemble each other, not how many there are:

    distinct    distractors share no words with the target
    similar     distractors share the target's words - the measured killer
    duplicated  a container and the label inside it, the case the option builder
                is supposed to remove before Jev ever sees it

It costs one Jev call per item and needs no phone.

    .venv/bin/python scripts/measure_jev_decisions.py [items-per-condition]
"""

from __future__ import annotations

import asyncio
import random
import sys
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from phone_control.jev import JevClient, choice_question
from phone_control.options import offer_actions
from phone_control.screen import parse_screen

TARGET_WORDS = ("inbox", "archive", "settings", "calendar", "photos", "wallet")

# Distractors that share the target's vocabulary, which is the condition the
# measurements say is expensive.
NEAR_WORDS = ("inbox zero", "archive all", "settings sync", "calendar week")

FAR_WORDS = ("torch", "compass", "metronome", "barometer", "altimeter")

# A goal that does not contain the label's own words, so the model has to know
# what the words mean rather than match strings.
SYNONYMS = (
    ("check my mail", "Inbox"),
    ("look at my pictures", "Photos"),
    ("see what I have to do today", "Calendar"),
    ("pay for something", "Wallet"),
)

# A plausible option sitting next to the right one. Choosing the near-miss is the
# realistic error, and it is invisible to any check that only asks "did it tap".
NEAR_MISS = (
    ("send the message now", "Send", "Send later"),
    ("delete this one", "Delete", "Delete all"),
    ("save my changes", "Save", "Save draft"),
)

# The goal is already true, so the only correct answer is to say so.
ALREADY_DONE = ("open the settings app", "com.android.settings")


@dataclass(frozen=True)
class Item:
    """One question with a known right answer."""

    condition: str
    goal: str
    correct_label: str
    xml: str


def a_screen(labels: list[str], nested: bool = False) -> str:
    """A screen of buttons, optionally with the first one nested in a container."""
    nodes = []
    if nested:
        # A tappable row whose own label contains a tappable label inside it.
        nodes.append(
            f"<node text='{labels[0]}' class='android.widget.LinearLayout' "
            f"package='com.example' clickable='true' bounds='[0,0][500,200]'>"
            f"<node text='{labels[0].split()[0]}' class='android.widget.TextView' "
            f"package='com.example' clickable='true' bounds='[20,40][300,120]'/>"
            f"</node>"
        )
        rest = labels[1:]
    else:
        rest = labels

    for position, label in enumerate(rest):
        top = 300 + position * 120
        nodes.append(
            f"<node text='{label}' class='android.widget.Button' "
            f"package='com.example' clickable='true' "
            f"bounds='[0,{top}][500,{top + 100}]'/>"
        )

    body = "".join(nodes)
    return (
        f"<hierarchy rotation='0'>"
        f"<node text='' class='android.widget.FrameLayout' package='com.example' "
        f"clickable='false' bounds='[0,0][1080,2400]'>{body}</node></hierarchy>"
    )


def a_one_button_screen(label: str) -> str:
    return a_screen([label])


def build_items(count: int, seed: int = 7) -> list[Item]:
    """Items whose correct option is fixed before Jev is asked."""
    random.seed(seed)
    items: list[Item] = []
    for index in range(count):
        target = TARGET_WORDS[index % len(TARGET_WORDS)]
        near = random.sample(NEAR_WORDS, 3)
        far = random.sample(FAR_WORDS, 3)

        items.append(
            Item("distinct", f"open {target}", target, a_screen([target, *far]))
        )
        items.append(
            Item("similar", f"open {target}", target, a_screen([target, *near]))
        )
        items.append(
            Item(
                "duplicated",
                f"open {target}",
                target,
                a_screen([target, *far], nested=True),
            )
        )

    # Conditions that can actually fail, which the three above largely cannot:
    # they hand the model the label's own words in the goal.
    for index in range(count):
        phrase, label = SYNONYMS[index % len(SYNONYMS)]
        items.append(
            Item(
                "semantic",
                phrase,
                label,
                a_screen([label, *random.sample(FAR_WORDS, 3)]),
            )
        )

        near_goal, right, wrong = NEAR_MISS[index % len(NEAR_MISS)]
        items.append(
            Item(
                "near-miss",
                near_goal,
                right,
                a_screen([right, wrong, *random.sample(FAR_WORDS, 2)]),
            )
        )

        done_goal, _package = ALREADY_DONE
        items.append(
            Item("already-done", done_goal, "", a_screen(random.sample(FAR_WORDS, 4)))
        )
    return items


def the_right_option(item: Item) -> set[str]:
    """Every option a person would accept, read off the built options.

    A set, because "the goal is unreachable" has two defensible answers.
    """
    if item.condition == "already-done":
        # The state says the goal is true, so the only correct answer is to say so.
        # "I cannot get there" is wrong: there is nothing to get to.
        return {"finish"}

    offered = offer_actions(parse_screen(item.xml))
    for action in offered:
        if (
            action.kind == "tap"
            and action.control is not None
            and action.control.label.casefold() == item.correct_label.casefold()
        ):
            return {action.option}
    raise AssertionError(f"the screen has no option for {item.correct_label!r}")


def the_goal_for(item: Item) -> str:
    """The instruction Jev is given, which is deliberately not always the label."""
    return item.goal


async def answer_one(client: JevClient, item: Item) -> tuple[str, float]:
    """Ask Jev to pick, and return what it picked and how sure it was."""
    offered = offer_actions(parse_screen(item.xml))
    criteria = {action.option: action.description for action in offered}

    decision = await client.ask(
        {
            "goal": the_goal_for(item),
            "foreground_app": (
                "com.android.settings"
                if item.condition == "already-done"
                else "com.example"
            ),
            "on_screen": [
                action.control.as_dict()
                for action in offered
                if action.control is not None
            ],
        },
        {
            "next_action": choice_question(
                "Which single option moves closest to the goal in the state? "
                "Choose 'finish' only if the goal is already achieved.",
                criteria,
            )
        },
    )
    answer = decision.choice("next_action")
    return answer.option, answer.confidence


async def main() -> int:
    per_condition = int(sys.argv[1]) if len(sys.argv) > 1 else 4
    client = JevClient()
    if not client.is_configured:
        print("no Jev key is configured", file=sys.stderr)
        return 1

    items = build_items(per_condition)
    print(f"{len(items)} items, {per_condition} per condition\n")

    tally: dict[str, list[bool]] = {}
    confidence_when_right: list[float] = []
    confidence_when_wrong: list[float] = []
    misses: list[str] = []

    for item in items:
        expected = the_right_option(item)
        chosen, confidence = await answer_one(client, item)
        correct = chosen in expected

        tally.setdefault(item.condition, []).append(correct)
        (confidence_when_right if correct else confidence_when_wrong).append(confidence)
        if not correct:
            misses.append(
                f"{item.condition:<11} wanted {'|'.join(sorted(expected))!r}, chose {chosen!r} "
                f"at confidence {confidence:.2f}"
            )
        print(
            f"  {item.condition:<11} {'ok ' if correct else 'MISS'} "
            f"conf={confidence:.2f}  wanted={'|'.join(sorted(expected))!r} chose={chosen!r}"
        )

    print()
    for condition in (
        "distinct",
        "similar",
        "duplicated",
        "semantic",
        "near-miss",
        "already-done",
    ):
        results = tally.get(condition, [])
        if results:
            right = sum(results)
            print(f"  {condition:<11} {right}/{len(results)} correct")

    def mean(values: list[float]) -> str:
        return f"{sum(values) / len(values):.2f}" if values else "n/a"

    print(f"\n  mean confidence when right: {mean(confidence_when_right)}")
    print(f"  mean confidence when wrong: {mean(confidence_when_wrong)}")
    if misses:
        print("\n  misses:")
        for miss in misses:
            print(f"    {miss}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
