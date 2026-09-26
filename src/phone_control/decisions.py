"""Recording the conversation with the decider, so a run can be audited.

The interface with Jev is where this project's bugs have lived. A run's outcome
says only whether it worked, never why it did not, and the causes found so far
were all invisible from the outcome alone:

* an option whose value the device could not act on, so the action did nothing
* a correct action refused for being unsure, which ended the run where it stood
* an option hidden for having once done nothing, which deleted the only route
* a ``finish`` accepted while the decider's own completion answer said otherwise

None of those can be seen in a pass or a fail. They are visible in what was sent
and what came back, which is what this keeps. One record per exchange, in the
order they happened, with the report's step of the same index carrying what the
loop then did about it.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from .goal import Choice


@dataclass(frozen=True)
class Exchange:
    """One question put to the decider, and the answer it gave.

    ``state`` is the state exactly as sent, options included, because "what did
    it actually see" is the first question asked of every failure.
    """

    index: int
    state: dict[str, Any]
    options: dict[str, str]
    choice: str
    description: str
    confidence: float | None
    probabilities: dict[str, float]
    goal_achieved: float | None
    # The operation and the controls are separate questions, so both are kept: a
    # wrong answer to one can be a right answer to a badly built other.
    targets: dict[str, str] = field(default_factory=dict)
    target: str | None = None
    app: str | None = None

    @property
    def probability(self) -> float | None:
        """How likely the option it named was the best one, when it said so."""
        return self.probabilities.get(self.choice)

    def as_dict(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "state": self.state,
            "options": self.options,
            "choice": self.choice,
            "description": self.description,
            "confidence": self.confidence,
            "probabilities": self.probabilities,
            "probability": self.probability,
            "goal_achieved": self.goal_achieved,
            "targets": self.targets,
            "target": self.target,
            "app": self.app,
        }


class Decider(Protocol):
    """Chooses one action, given the goal and what is on screen."""

    async def choose(self, state: dict[str, object]) -> Choice: ...


class RecordingDecider:
    """Wraps a decider and keeps every exchange it took part in.

    Transparent on purpose: the loop is handed this in place of the real decider
    and cannot tell the difference, so what is recorded is exactly what happened
    rather than what a second implementation of the same call would have done.
    """

    def __init__(self, decide: Callable[[dict[str, Any]], Any]) -> None:
        self.decide = decide
        self.exchanges: list[Exchange] = []

    async def choose(self, state: dict[str, object]) -> Choice:
        # Read before the call, not after. A decider is entitled to reshape what
        # it was handed, and one that took the options out for its own use left
        # this reading an empty list while the reply's probabilities still named
        # twenty-three of them.
        # Read before the call, not after: a decider is entitled to reshape what it
        # was handed, and one that took the options out for its own use left this
        # reading an empty list while the reply's probabilities still named
        # twenty-three of them.
        options = dict(state.get("options") or state.get("operations") or {})
        targets = dict(state.get("targets") or {})
        sent = _without_the_prompt_parts(state)

        choice = await self.decide(state)

        self.exchanges.append(
            Exchange(
                index=len(self.exchanges),
                state=sent,
                options=options,
                choice=choice.action,
                description=choice.description,
                confidence=choice.confidence,
                probabilities=dict(choice.probabilities),
                goal_achieved=choice.goal_achieved,
                targets=targets,
                target=choice.target,
                app=choice.app,
            )
        )
        return choice

    @property
    def last(self) -> Exchange | None:
        return self.exchanges[-1] if self.exchanges else None

    def write_to(self, path: Path) -> None:
        """Write one JSON object per line, which is readable and diffable."""
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as handle:
            for exchange in self.exchanges:
                handle.write(json.dumps(exchange.as_dict(), default=str) + "\n")


# The parts of the state that exist to carry the options, rather than to describe
# the phone. Kept in the record because they are what was offered, but removed
# from the copy of the state so a reader is not shown the same thing twice.
PROMPT_PARTS = ("options", "operations", "option_kinds", "targets", "apps")


def _without_the_prompt_parts(state: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in state.items() if key not in PROMPT_PARTS}
