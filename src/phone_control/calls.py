"""How many requests each model took, and what share of them it was.

The reference implementation reports this at the end of every run and says why:
*the classifier's share is the number the design stands on. A task it falls on is
a task the writer had to steer at every turn, and the fix belongs in the state the
classifier reads, not in more hand-offs.*

That is the whole argument for counting. A run that needed the reader six times is
not a run that needs a better reader; it is a run whose state is missing something
the decision model needed. Counting the two together would hide which of those is
happening, which is exactly the mistake this project has already made once - a
threshold tuned against a state that was not saying enough.

Two models, because a decision model cannot write and the reader exists to:
``classifier`` is the model that picks each operation, ``reader`` is the one that
reads a screen when the classifier stops.
"""

from __future__ import annotations

from dataclasses import dataclass, field

CLASSIFIER = "classifier"
READER = "reader"


@dataclass
class TheCalls:
    """One count per model, and the share each of them took."""

    counts: dict[str, int] = field(default_factory=dict)
    seconds: dict[str, float] = field(default_factory=dict)

    def note(self, who: str, seconds: float = 0.0) -> None:
        self.counts[who] = self.counts.get(who, 0) + 1
        self.seconds[who] = self.seconds.get(who, 0.0) + seconds

    @property
    def total(self) -> int:
        return sum(self.counts.values())

    def as_dict(self) -> dict[str, object]:
        total = self.total or 1
        return {
            who: {
                "calls": count,
                "share": round(count / total, 3),
                "seconds": round(self.seconds.get(who, 0.0), 2),
            }
            for who, count in sorted(self.counts.items())
        }

    def as_line(self) -> str:
        """One line, in the shape a run is judged by at a glance."""
        if not self.counts:
            return "calls: none"
        total = self.total or 1
        parts = [
            f"{who} {count} ({count / total:.0%}, {self.seconds.get(who, 0.0):.1f}s)"
            for who, count in sorted(self.counts.items())
        ]
        return "calls: " + "  ".join(parts)
