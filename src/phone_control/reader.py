"""When the decider stops, a stronger reader looks and the run goes on.

A decision model is good at one thing: picking from a list, fast and cheap. It is
bad at two things that a run needs and that no amount of tuning fixes. It cannot
write a sentence, so it cannot say *what it found* when a goal asks for a fact
rather than an action. And when two moves both look right it splits between them,
which reads as low confidence - and a loop that treats low confidence as a reason
to act fails differently from one that treats it as a reason to stop.

Neither is really a decision problem. "Is the goal now met" and "which of these
two good moves comes first" are questions a reader of the screen can answer, and
this project measured the cost of asking the small model instead: on a screen where
the goal genuinely was met, its completion answer read 0.42 to 0.47, and on one
where it was not, 0.51.

So a stop is not the end. A reader that can write looks at the screen and returns:

    achieved    whether the goal is now met, and the answer if the goal asked for one
    focus       ONE next move, in terms of the screen
    question    something only the person holding the phone can settle

The focus joins the decider's state and the run carries on. The reference
implementation measured 0.39 becoming 0.92 on the same capture once a focus was
set, which is the whole argument: the fix belongs in what the decider is told, not
in a threshold.

Three things keep it bounded and honest.

**The reader never chooses the action.** It says what to aim at; the decider still
picks every operation. A stronger model steering the small one is a division of
labour; a stronger model clicking is a different design, and a worse one.

**Every exchange is bounded.** Hand-offs are capped, questions are capped, and a
stop on a screen the run has already been stopped on is not read twice. A reader
that always has another suggestion cannot keep a run alive for ever.

**It is optional.** With no reader configured, a stop ends the run exactly as it
did before, and nothing here is on the path. That is the default, so a missing key
cannot make a run behave mysteriously.

The reader is any model behind an OpenAI- or Anthropic-compatible endpoint, named
by the environment, in the same shape the reference uses:

    PHONE_CONTROL_READER_BASE_URL   default https://api.anthropic.com
    PHONE_CONTROL_READER_API_KEY    no key by default
    PHONE_CONTROL_READER_MODEL      the model to ask
    PHONE_CONTROL_READER_API        anthropic (the default) or openai

Jev is a decision model and cannot write, so a reader is a second model rather
than a second use of the first. That is a real cost and worth being plain about:
the reader is a small number of calls next to a run's decisions, and the point of
counting them separately is to see whether it is earning its place.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any

import httpx

# How many times the reader may hand a run back, and how many things it may ask
# the person holding the phone. Both are counted per run and both are hard stops:
# a reader with another idea every time must not be able to keep a run alive.
MAXIMUM_HAND_OFFS = 4
MAXIMUM_QUESTIONS = 2

BASE_URL_VARIABLE = "PHONE_CONTROL_READER_BASE_URL"
API_KEY_VARIABLE = "PHONE_CONTROL_READER_API_KEY"
MODEL_VARIABLE = "PHONE_CONTROL_READER_MODEL"
API_VARIABLE = "PHONE_CONTROL_READER_API"

ANTHROPIC = "anthropic"
OPENAI = "openai"

READER_TIMEOUT_SECONDS = 30.0


@dataclass(frozen=True)
class WhatTheReaderSaid:
    """The reader's reading of a stopped screen.

    ``focus`` is one move and not a plan, which is the reference's rule and a good
    one: the decider is asked what to do next, and a plan handed to it would be a
    decision it did not make. ``question`` is asked of the person holding the
    phone, and the reply joins the state for the rest of the run.
    """

    achieved: bool = False
    answer: str = ""
    focus: str = ""
    question: str = ""
    because: str = ""

    @property
    def has_a_focus(self) -> bool:
        return bool(self.focus.strip())

    @property
    def asks_something(self) -> bool:
        return bool(self.question.strip())

    def as_state(
        self, the_person_said: list[dict[str, str]] | None = None
    ) -> dict[str, Any]:
        """What joins the decider's state, and only what is needed.

        A focus is written as the next move so the decider can work toward it; a
        question and its reply are facts about the goal that the screen cannot
        show, and go in under their own name so neither is mistaken for the goal.
        """
        added: dict[str, Any] = {}
        if self.has_a_focus:
            added["the_next_move_to_aim_at"] = self.focus.strip()
        if the_person_said:
            added["what_the_person_said"] = the_person_said
        return added


class AReaderThatCannotWrite:
    """The default: no reader, so a stop ends the run as it always did."""

    is_configured = False

    async def read(self, packet: dict[str, Any]) -> WhatTheReaderSaid:
        return WhatTheReaderSaid(because="no reader is configured")


def reader_from_the_environment() -> Any:
    """The reader to use: a written model if one is configured, else eyes.

    A reader that can write is better - it can answer a question, not only judge one -
    and it is used when a model is named. With nothing named, this machine can still
    read a screen *by looking at it*, and that is enough for the one thing the hand-off
    is mostly for: deciding whether the goal is met.
    """
    model = os.environ.get(MODEL_VARIABLE, "").strip()
    if model:
        return AReaderOfModels(
            base_url=os.environ.get(
                BASE_URL_VARIABLE, "https://api.anthropic.com"
            ).strip(),
            api_key=os.environ.get(API_KEY_VARIABLE, "").strip(),
            model=model,
            speaks=os.environ.get(API_VARIABLE, ANTHROPIC).strip().casefold(),
        )
    return AReaderThatLooksAtTheScreen()


class AReaderThatLooksAtTheScreen:
    """A reader that reads the screen with its eyes, and judges with the decider.

    No key and no second model: it photographs the phone, reads the text in the
    picture with macOS Vision, and asks the decider the one question a reader mostly
    exists to answer - does this screen show the goal already achieved. Asked as the
    same Noul the loop asks, so the answer carries a probability rather than a
    sentence, and the threshold on it is the one already measured.

    It cannot write a focus or ask a question, and it says so by leaving both empty:
    what it can do is the thing that was missing. Measured on the About phone page,
    which no dump can read, the picture gives back "About phone", the device name and
    the account - everything needed to know the screen is the goal's.
    """

    def __init__(self, client: Any = None, achieved_above: float = 0.90) -> None:
        self.client = client
        self.achieved_above = achieved_above
        self.looks = 0

    @property
    def is_configured(self) -> bool:
        from .reading_a_picture import macos_can_read_pictures

        return macos_can_read_pictures()

    async def read(self, packet: dict[str, Any]) -> WhatTheReaderSaid:
        from .jev import JevClient, yes_or_no_question
        from .reading_a_picture import read_the_picture

        phone = packet.get("_phone")
        goal = str(packet.get("goal") or "")
        if phone is None:
            return WhatTheReaderSaid(because="there is no phone to look at")

        lines = read_the_picture(phone)
        if not lines:
            return WhatTheReaderSaid(because="the picture gave up no text")
        self.looks += 1

        client = self.client or JevClient()
        if not client.is_configured:
            return WhatTheReaderSaid(
                because="the picture was read but there is no decider to judge it"
            )

        decision = await client.ask(
            {
                # What the screen says, and nothing else: this reader has no controls,
                # no window and no history. The goal is the whole of what it can judge
                # against, which is honest for a question about completion.
                "goal": goal,
                "what_the_screen_says": lines,
                "how_this_was_read": (
                    "The phone would not describe this screen, so these lines were read "
                    "from a photograph of it."
                ),
            },
            {
                "achieved": yes_or_no_question(
                    "Does the screen described above show the goal already achieved, so "
                    "that nothing further needs to happen?",
                    when_true=(
                        "The screen itself shows the finished result of the goal."
                    ),
                    when_false=(
                        "It shows something else, or a step on the way to the goal."
                    ),
                )
            },
        )
        probability = decision.yes_or_no("achieved").probability_yes
        return WhatTheReaderSaid(
            achieved=probability >= self.achieved_above,
            answer=(
                "the screen shows the goal complete"
                if probability >= self.achieved_above
                else ""
            ),
            because=f"read from a picture: {probability:.2f}",
        )


# The shape the reader is told to answer in, spelled out for both the request and
# the prompt: an endpoint that ignores structured output has to be able to get it
# right from the words alone, which is the reference's lesson and a real one -
# local models drop `response_format` without saying so.
THE_SHAPE = (
    '{"achieved": true or false, "answer": "the fact the goal asked for, or an '
    'empty string", "focus": "one next move in terms of the screen, or an empty '
    'string", "question": "something only the person holding the phone can '
    'settle, or an empty string"}'
)

INSTRUCTIONS = (
    "You are reading a phone screen on behalf of a decision model that has "
    "stopped. Answer with JSON only, in exactly this shape: " + THE_SHAPE + "\n\n"
    "Say `achieved` true only when the screen itself shows the completed result of "
    "the goal. Give `focus` as ONE move, in the words of the screen ('tap the "
    "'Use Bluetooth' switch'), never a plan, and only when a move would help. Ask "
    "a `question` only when the screen cannot settle something the goal depends "
    "on and a person could. Never ask for a password or a credential. Leave a "
    "field as an empty string when it does not apply."
)


class AReaderOfModels:
    """Reads a stopped screen with a model that can write.

    Both wire formats are supported because the reader is the one place a run needs
    a writing model, and pinning it to one vendor would make the whole loop
    unusable without that vendor. The key never travels to a different host than
    the one it was configured for.
    """

    def __init__(
        self,
        base_url: str,
        model: str,
        api_key: str = "",
        speaks: str = ANTHROPIC,
        timeout: float = READER_TIMEOUT_SECONDS,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.api_key = api_key
        self.speaks = speaks if speaks in (ANTHROPIC, OPENAI) else ANTHROPIC
        self.timeout = timeout
        self.calls = 0

    @property
    def is_configured(self) -> bool:
        return True

    async def read(self, packet: dict[str, Any]) -> WhatTheReaderSaid:
        """Ask the reader what it makes of a screen, and take what it says."""
        self.calls += 1
        body, headers = self._the_request(packet)
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.post(
                    body["url"], headers=headers, json=body["json"]
                )
        except httpx.HTTPError as error:
            return WhatTheReaderSaid(
                because=f"the reader could not be reached: {error}"
            )

        if response.status_code != 200:
            return WhatTheReaderSaid(
                because=f"the reader answered HTTP {response.status_code}"
            )
        try:
            written = response.json()
        except ValueError:
            return WhatTheReaderSaid(
                because="the reader answered something that is not JSON"
            )
        return read_what_it_said(written, self.speaks)

    def _the_request(
        self, packet: dict[str, Any]
    ) -> tuple[dict[str, Any], dict[str, str]]:
        # Underscored keys are for us, not for a model: one of them is the phone.
        told = json.dumps(
            {key: value for key, value in packet.items() if not key.startswith("_")},
            indent=2,
            default=str,
        )
        if self.speaks == OPENAI:
            return (
                {
                    "url": f"{self.base_url}/v1/chat/completions",
                    "json": {
                        "model": self.model,
                        "messages": [
                            {"role": "system", "content": INSTRUCTIONS},
                            {"role": "user", "content": told},
                        ],
                        "response_format": {"type": "json_object"},
                        "temperature": 0,
                    },
                },
                {"Authorization": f"Bearer {self.api_key}"},
            )
        return (
            {
                "url": f"{self.base_url}/v1/messages",
                "json": {
                    "model": self.model,
                    "max_tokens": 700,
                    "system": INSTRUCTIONS,
                    "messages": [{"role": "user", "content": told}],
                },
            },
            {"x-api-key": self.api_key, "anthropic-version": "2023-06-01"},
        )


def read_what_it_said(
    written: dict[str, Any], speaks: str = ANTHROPIC
) -> WhatTheReaderSaid:
    """The JSON out of either wire format, or a reasoned refusal.

    A reply that cannot be read refuses the step it was for and the run carries on,
    which is the reference's rule: a reader that cannot be understood is not a
    reason to trust it.
    """
    text = _the_text_of(written, speaks)
    stripped = _the_json_in(text)
    if stripped is None:
        return WhatTheReaderSaid(because="the reader's answer was not readable")
    try:
        said = json.loads(stripped)
    except ValueError:
        return WhatTheReaderSaid(because="the reader's answer was not valid JSON")
    if not isinstance(said, dict):
        return WhatTheReaderSaid(
            because="the reader answered something that is not an object"
        )
    return WhatTheReaderSaid(
        achieved=bool(said.get("achieved")),
        answer=str(said.get("answer") or "").strip(),
        focus=str(said.get("focus") or "").strip(),
        question=str(said.get("question") or "").strip(),
    )


def _the_text_of(written: dict[str, Any], speaks: str) -> str:
    if speaks == OPENAI:
        choices = written.get("choices") or []
        if choices:
            return str((choices[0].get("message") or {}).get("content") or "")
        return ""
    blocks = written.get("content") or []
    return "".join(
        str(block.get("text") or "") for block in blocks if isinstance(block, dict)
    )


def _the_json_in(text: str) -> str | None:
    """The object in a reply, with any code fence or sentence around it removed.

    Local and older endpoints wrap JSON in prose or a fence even when asked not to,
    and refusing those would make the reader useless on exactly the endpoints
    people run at home.
    """
    if not text.strip():
        return None
    fenced = text.split("```")
    if len(fenced) >= 3:
        inside = fenced[1]
        inside = inside.split("\n", 1)[1] if "\n" in inside else inside
        return inside.strip()
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end < start:
        return None
    return text[start : end + 1]


@dataclass
class TheHandOff:
    """What the reader has been asked, and how much of it is left.

    Counted here rather than in the loop, so the bound belongs to the thing being
    bounded: a run cannot exceed it by taking another path to a stop.
    """

    reader: Any = None
    hand_offs: int = 0
    questions: int = 0
    the_person_said: list[dict[str, str]] = None  # type: ignore[assignment]
    already_read: set[str] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.reader is None:
            self.reader = AReaderThatCannotWrite()
        if self.the_person_said is None:
            self.the_person_said = []
        if self.already_read is None:
            self.already_read = set()

    @property
    def can_read(self) -> bool:
        return bool(getattr(self.reader, "is_configured", False))

    @property
    def may_go_round_again(self) -> bool:
        return self.hand_offs < MAXIMUM_HAND_OFFS

    def a_screen_already_read(self, signature: str) -> bool:
        """Whether this exact screen has already been read at a stop.

        Re-reading it would put the same question to the same screen and get the
        same answer, which is how a hand-off turns into a loop.
        """
        return signature in self.already_read

    def answer_for(self, question: str) -> str:
        """Ask the person, when there is one, and remember what they said.

        No terminal means no question: an unattended run cannot be asked, and
        waiting for an answer that will never come is worse than carrying on.
        """
        if self.questions >= MAXIMUM_QUESTIONS:
            return ""
        self.questions += 1
        try:
            reply = input(f"\nThe phone needs to know: {question}\n> ").strip()
        except (EOFError, KeyboardInterrupt):
            reply = ""
        if reply:
            self.the_person_said.append({"asked": question, "replied": reply})
        return reply
