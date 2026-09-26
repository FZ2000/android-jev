"""Checking the conversation with the decider, exchange by exchange.

A scenario passing says the goal was reached. It says nothing about whether the
decider was asked a fair question, or whether the answer was used correctly, and
those are where this project's bugs have actually been:

* an option whose value the device could not act on, so the action did nothing
* a correct option refused, ending the run where it stood
* an option hidden for having once done nothing, taking the only route with it
* a ``finish`` accepted while the decider's own completion answer disagreed

Every one of those would have been caught here, at the exchange where it
happened, with the state and the answer attached. So these checks run on every
exchange of every scenario rather than on the ones that went wrong.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from phone_control.decisions import Exchange
from phone_control.goal import FINISH, GIVE_UP, Step

# A choice question accepts at most this many alternatives. Past it the request
# is rejected outright, so the builder must never cross it.
JEV_MAX_ALTERNATIVES = 255

# The fields the decider cannot answer without. A question asked without these is
# not a hard question, it is an unanswerable one.
REQUIRED_STATE_FIELDS = ("goal", "foreground_app", "screen", "controls_on_screen")

# How far the probabilities may be from summing to one before the distribution is
# treated as malformed rather than merely rounded.
PROBABILITY_TOLERANCE = 0.02


@dataclass(frozen=True)
class Complaint:
    """One thing wrong with an exchange, named so it can be looked up."""

    exchange: int
    about: str
    detail: str

    def __str__(self) -> str:
        return f"exchange {self.exchange} [{self.about}]: {self.detail}"


def complaints_about(
    exchange: Exchange, step: Step | None, screen_controls: int | None = None
) -> list[Complaint]:
    """Everything wrong with one exchange, in the order it matters."""

    def complaining(about: str, details: list[str]) -> list[Complaint]:
        return [Complaint(exchange.index, about, detail) for detail in details]

    return (
        complaining("the state", _state_complaints(exchange))
        + complaining("the options", _option_complaints(exchange, screen_controls))
        + complaining("the reply", _reply_complaints(exchange))
        + complaining("the execution", _execution_complaints(exchange, step))
    )


# --- what we sent: the state --------------------------------------------


def _state_complaints(exchange: Exchange) -> list[str]:
    """The state has to describe the phone well enough to answer about it."""
    state = exchange.state
    complaints = []

    for field in REQUIRED_STATE_FIELDS:
        if (
            field == "controls_on_screen"
            and "nothing_is_known_about_this_screen" in state
        ):
            continue
        if state.get(field) in (None, "", [], {}):
            complaints.append(f"{field!r} is missing, so the question is unanswerable")

    goal = str(state.get("goal") or "")
    if not goal.strip():
        complaints.append("the goal is blank")

    controls = state.get("controls_on_screen")
    described_as_unknown = "nothing_is_known_about_this_screen" in state
    if isinstance(controls, list) and not controls and not described_as_unknown:
        # An empty control list is a defect unless the state says why it is empty.
        # Some pages are never dumpable - Android will not capture a screen that never
        # goes idle - and saying "nothing is known here, and here is the window" is the
        # honest version of that, not the same claim as "there is nothing here".
        complaints.append(
            "the screen was described as having nothing on it, and not as a screen "
            "the phone would not describe"
        )

    size = state.get("screen")
    if isinstance(size, dict) and not (size.get("width") and size.get("height")):
        complaints.append(f"the screen size is not stated: {size!r}")

    return complaints


# --- what we sent: the options ------------------------------------------


def _option_complaints(exchange: Exchange, screen_controls: int | None) -> list[str]:
    """The options are the whole decision. They have to be answerable.

    The controls are a different question from the operations, and the check runs
    over both: an operation set that overlaps reads as doubt, and two controls
    described the same way are two the decider cannot tell apart.
    """
    options = exchange.options
    complaints = _complaints_about_one_choice(options, "operations", screen_controls)
    targets = exchange.targets
    if targets:
        complaints.extend(
            _complaints_about_one_choice(targets, "targets", None, forgiving=True)
        )
    return complaints


def _complaints_about_one_choice(
    options: dict[str, str],
    what: str,
    screen_controls: int | None,
    forgiving: bool = False,
) -> list[str]:
    complaints = []

    complaints: list[str] = []
    if not options:
        return [f"no {what} were offered, so there was nothing to choose"]

    if len(options) > JEV_MAX_ALTERNATIVES:
        complaints.append(
            f"{len(options)} {what} were offered, past the "
            f"{JEV_MAX_ALTERNATIVES} a choice question accepts"
        )

    unnamed = [name for name, description in options.items() if not name.strip()]
    if unnamed:
        complaints.append(f"{what} with no name: {unnamed}")

    unexplained = [
        name for name, description in options.items() if not (description or "").strip()
    ]
    if unexplained:
        complaints.append(
            f"{what} offered with no description of what choosing them means: "
            f"{unexplained}"
        )

    # Two options described the same way make the choice harder than it needs to
    # be, and the cost is measured rather than theoretical: resemblance between
    # alternatives is what degrades a decision, not how many there are.
    wordings: dict[str, list[str]] = {}
    for name, description in options.items():
        wordings.setdefault((description or "").strip().casefold(), []).append(name)
    duplicated = {
        wording: names
        for wording, names in wordings.items()
        if len(names) > 1 and wording
    }
    if duplicated:
        complaints.append(f"{what} described identically: {duplicated}")

    if forgiving:
        return complaints

    for required in (FINISH, GIVE_UP):
        if required not in options:
            complaints.append(
                f"{required!r} was not offered, so the run had no way to end itself"
            )

    if screen_controls == 0 and len(options) <= 2:
        complaints.append(
            "nothing actionable was on screen, so only the ending options exist"
        )

    return complaints


# --- what came back: the reply ------------------------------------------


def _reply_complaints(exchange: Exchange) -> list[str]:
    """The answer has to be one of the things we offered, and well formed."""
    complaints = []

    if exchange.choice not in exchange.options:
        complaints.append(
            f"the decider chose {exchange.choice!r}, which was not among the "
            f"options offered: {sorted(exchange.options)}"
        )

    if not exchange.probabilities:
        complaints.append(
            "no probabilities came back, so nothing can be said about how clear "
            "the choice was"
        )
        return complaints

    unknown = set(exchange.probabilities) - set(exchange.options)
    if unknown:
        complaints.append(
            f"probabilities were returned for options never offered: {sorted(unknown)}"
        )

    total = sum(exchange.probabilities.values())
    if abs(total - 1.0) > PROBABILITY_TOLERANCE:
        complaints.append(f"the probabilities sum to {total:.3f}, not one")

    # A choice question asks which option is best, so the one named should be the
    # one it rated highest. A disagreement is worth knowing about: it means the
    # answer and the numbers are saying different things.
    if exchange.choice in exchange.probabilities:
        best = max(
            exchange.probabilities, key=lambda name: exchange.probabilities[name]
        )
        chosen = exchange.probabilities[exchange.choice]
        if best != exchange.choice and exchange.probabilities[best] > chosen:
            complaints.append(
                f"it named {exchange.choice!r} at {chosen:.2f} while rating "
                f"{best!r} higher at {exchange.probabilities[best]:.2f}"
            )

    if exchange.confidence is None:
        complaints.append("no confidence came back with the choice")

    if exchange.goal_achieved is None:
        complaints.append(
            "no completion answer came back, so a finish could not be checked"
        )

    return complaints


# --- what we did about it: the execution --------------------------------


def _execution_complaints(exchange: Exchange, step: Step | None) -> list[str]:
    """The answer has to have been acted on, and the effect looked for."""
    if step is None:
        return [
            "the decider answered but the loop recorded no step for it, so the "
            "answer cannot be traced to anything that happened"
        ]

    complaints = []
    if step.action != exchange.choice:
        complaints.append(
            f"the decider chose {exchange.choice!r} but the loop recorded "
            f"{step.action!r}"
        )

    if step.confidence != exchange.confidence:
        complaints.append(
            f"the step reports confidence {step.confidence!r} where the reply "
            f"said {exchange.confidence!r}"
        )

    ending = step.action in (FINISH, GIVE_UP)
    if not ending:
        if not (step.description or "").strip():
            complaints.append("the action was carried out but not described")
        if step.changed_the_screen is None:
            complaints.append(
                "whether the action changed the screen was never recorded, so a "
                "no-op looks the same as progress"
            )
    return complaints


# --- the questions we asked ---------------------------------------------


def the_state_sent_is_readable(state: dict[str, Any]) -> list[str]:
    """A last look at the state as a whole, for a reader deciding what went wrong."""
    complaints = []
    if "options" in state:
        complaints.append(
            "'options' is still inside the state; the options belong with the "
            "question, not in the description of the phone"
        )
    return complaints
