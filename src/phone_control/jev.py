"""Asking Jev, TypeSafe's System One model, for a typed decision.

Jev does not write prose. It answers one of three typed questions about a state
you hand it: which of these options, does this hold, or where on this scale.
That is exactly the shape of "given what is on the phone screen, what should I
do next" -- and because the answer is constrained to options we supply, Jev
cannot invent a control that is not on the screen. The confidence it returns
tells the caller when to stop trusting it and go look at the screen directly.

Jev is served by two gateways, and the key's own shape says which one issued it: an
OpenRouter key beginning ``sk-or-`` goes to OpenRouter's Decisions API, and a TypeSafe
key beginning ``apikey_`` goes to TypeSafe's own. Endpoint and model name travel
together, because sending one gateway's model name to the other is a 400.
"""

from __future__ import annotations

import contextlib
import os
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

from .errors import JevNotConfigured, JevRequestFailed


@dataclass(frozen=True)
class JevProvider:
    """One gateway that serves Jev, with the endpoint and model name it uses.

    The two differ in both: OpenRouter routes the model as
    ``typesafe/jev-1.13`` under ``/api/alpha/decisions``, while TypeSafe's own
    API serves plain ``jev-latest`` under ``/v1/systemone``. Sending one
    gateway's model name to the other is a 400, so the pair travels together.
    """

    name: str
    endpoint: str
    model: str


OPENROUTER = JevProvider(
    name="openrouter",
    endpoint="https://openrouter.ai/api/alpha/decisions",
    model="typesafe/jev-1.13",
)

TYPESAFE = JevProvider(
    name="typesafe",
    endpoint="https://api.typesafe.ai/v1/systemone",
    model="jev-latest",
)

# A key's shape says which gateway issued it, which is otherwise unguessable and
# fails as a 401 that reads like a missing header.
KEY_PREFIXES = (
    ("sk-or-", OPENROUTER),
    ("apikey_", TYPESAFE),
)

DEFAULT_PROVIDER = OPENROUTER

JEV_TIMEOUT_SECONDS = 25.0


def provider_for(api_key: str) -> JevProvider:
    """Which gateway a key belongs to, judged by its prefix."""
    for prefix, provider in KEY_PREFIXES:
        if api_key.startswith(prefix):
            return provider
    return DEFAULT_PROVIDER


# Where the README tells a user to put the key. The DSH profile patch reads this same
# file and passes it in the environment, but reading it here too means the server
# works the documented way when run standalone.
#
# Both names are read, the gateway-neutral one first: a TypeSafe key is not an
# OpenRouter key, and asking somebody to put theirs in a variable named after the
# other gateway is the sort of thing that makes a route undiscoverable. The
# OpenRouter names stay because they are in every existing configuration.
SECRETS_DIRECTORY_VARIABLE = "DSH_HOME"
KEY_ENVIRONMENT_VARIABLES = ("JEV_API_KEY", "OPENROUTER_API_KEY")
KEY_FILE_NAMES = ("jev-api-key", "openrouter-api-key")
FALLBACK_SECRETS_DIRECTORIES = (
    "~/Library/Application Support/dsh-desktop/harness/secrets",
)


JEV_ENDPOINT = DEFAULT_PROVIDER.endpoint
JEV_MODEL = DEFAULT_PROVIDER.model


def find_api_key() -> str:
    """The Jev key, from the environment or from the documented file.

    Whichever gateway issued it: the key itself says which one serves it, so nothing
    here has to know.
    """
    for variable in KEY_ENVIRONMENT_VARIABLES:
        from_environment = os.environ.get(variable, "").strip()
        if from_environment:
            return from_environment

    directories = []
    if os.environ.get(SECRETS_DIRECTORY_VARIABLE):
        directories.append(Path(os.environ[SECRETS_DIRECTORY_VARIABLE]) / "secrets")
    directories.extend(Path(path).expanduser() for path in FALLBACK_SECRETS_DIRECTORIES)

    for directory in directories:
        for name in KEY_FILE_NAMES:
            try:
                stored = (directory / name).read_text(encoding="utf-8").strip()
            except OSError:
                continue
            if stored:
                return stored
    return ""


# Jev answers at most 255 alternatives in one Choice question.
JEV_MAX_ALTERNATIVES = 255
JEV_MAX_STATE_CHARACTERS = 90_000


def choice_question(instructions: str, criteria: dict[str, str]) -> dict[str, Any]:
    """A question whose answer is one key of ``criteria``."""
    if not criteria:
        raise ValueError("A choice question needs at least one option.")
    if len(criteria) > JEV_MAX_ALTERNATIVES:
        raise ValueError(
            f"A choice question accepts at most {JEV_MAX_ALTERNATIVES} options, "
            f"got {len(criteria)}."
        )
    return {"type": "choice", "instructions": instructions, "criteria": criteria}


def yes_or_no_question(
    instructions: str, *, when_true: str, when_false: str
) -> dict[str, Any]:
    """A question whose answer is the probability that it holds.

    Jev calls this primitive a "noul".
    """
    return {
        "type": "noul",
        "instructions": instructions,
        "criteria": {"true": when_true, "false": when_false},
    }


def scale_question(instructions: str, criteria: list[str]) -> dict[str, Any]:
    """A question placing the state on an ordered scale of at least two levels."""
    if len(criteria) < 2:
        raise ValueError("A scale question needs at least two levels.")
    return {"type": "score", "instructions": instructions, "criteria": criteria}


@dataclass(frozen=True)
class ChoiceAnswer:
    """Which option Jev picked, and how strongly."""

    option: str
    confidence: float
    probabilities: dict[str, float]


@dataclass(frozen=True)
class YesOrNoAnswer:
    """The probability that the condition holds."""

    probability_yes: float


@dataclass(frozen=True)
class ScaleAnswer:
    """Where the state fell on an ordered scale."""

    position: float
    confidence: float
    probabilities: dict[str, float]
    legend: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class Decision:
    """One Jev response, with typed accessors for each primitive."""

    answers: dict[str, Any]
    model: str
    cost_usd: float | None
    input_tokens: int | None

    def choice(self, name: str) -> ChoiceAnswer:
        answer = self._answer(name, "choice")
        probabilities = {
            str(key): float(value)
            for key, value in (answer.get("probabilities") or {}).items()
        }
        return ChoiceAnswer(
            option=str(answer.get("choice", "")),
            confidence=float(answer.get("confidence") or 0.0),
            probabilities=probabilities,
        )

    def yes_or_no(self, name: str) -> YesOrNoAnswer:
        answer = self._answer(name, "noul")
        return YesOrNoAnswer(probability_yes=float(answer.get("noul") or 0.0))

    def scale(self, name: str) -> ScaleAnswer:
        answer = self._answer(name, "score")
        probabilities = {
            str(key): float(value)
            for key, value in (answer.get("probabilities") or {}).items()
        }
        legend = {
            str(key): str(value) for key, value in (answer.get("legend") or {}).items()
        }
        return ScaleAnswer(
            position=float(answer.get("score") or 0.0),
            confidence=float(answer.get("confidence") or 0.0),
            probabilities=probabilities,
            legend=legend,
        )

    def _answer(self, name: str, expected_type: str) -> dict[str, Any]:
        answer = self.answers.get(name)
        if not isinstance(answer, dict):
            raise JevRequestFailed(
                f"Jev returned no answer named '{name}'. "
                f"Answers present: {', '.join(sorted(self.answers)) or 'none'}."
            )
        return answer


class JevClient:
    """A thin client for the Jev Decisions API."""

    def __init__(
        self,
        api_key: str | None = None,
        endpoint: str | None = None,
        model: str | None = None,
        timeout: float = JEV_TIMEOUT_SECONDS,
        transport: httpx.AsyncBaseTransport | None = None,
        on_exchange: Callable[[Any, Any, Any, float], None] | None = None,
    ) -> None:
        # Called with (state, questions, answer, seconds) after every request that
        # comes back. This is where a run's record of what it asked and what it
        # was told comes from: the wire, not a reconstruction of the intent,
        # because a reconstruction would agree with the code and the argument is
        # always about whether the code was right.
        self.on_exchange = on_exchange
        self.api_key = api_key or find_api_key()
        provider = provider_for(self.api_key)
        self.provider = provider
        # An explicit endpoint or model wins; otherwise the gateway's own pair is
        # used, so the two can never be mixed up.
        self.endpoint = endpoint or os.environ.get("JEV_ENDPOINT") or provider.endpoint
        self.model = model or os.environ.get("JEV_MODEL") or provider.model
        self.timeout = timeout
        self.transport = transport

    @property
    def is_configured(self) -> bool:
        return bool(self.api_key)

    def require_configured(self) -> None:
        if not self.is_configured:
            raise JevNotConfigured(
                "No Jev key is set, so Jev decisions are unavailable.",
                fix=(
                    "Add JEV_API_KEY to the phone MCP server's env block: an "
                    "OpenRouter key from https://openrouter.ai/settings/keys, or a "
                    "TypeSafe key. The shape of the key decides which gateway serves "
                    "it, and OPENROUTER_API_KEY is read too."
                ),
            )

    async def ask(
        self, state: dict[str, Any] | str, questions: dict[str, dict[str, Any]]
    ) -> Decision:
        """Send a state and one or more typed questions to Jev."""
        self.require_configured()
        if not questions:
            raise ValueError("At least one question is required.")

        rendered_state = state
        if isinstance(state, str) and len(state) > JEV_MAX_STATE_CHARACTERS:
            rendered_state = state[:JEV_MAX_STATE_CHARACTERS]

        payload = {"model": self.model, "state": rendered_state, "questions": questions}
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        started = time.monotonic()
        try:
            async with httpx.AsyncClient(
                timeout=self.timeout, transport=self.transport
            ) as client:
                response = await client.post(
                    self.endpoint, headers=headers, json=payload
                )
        except httpx.HTTPError as error:
            raise JevRequestFailed(
                f"The Jev request could not be completed: {error}",
                fix="Check this computer's network connection and retry.",
            ) from error

        if response.status_code != 200:
            raise JevRequestFailed(
                f"Jev rejected the request with HTTP {response.status_code}: "
                f"{response.text[:500]}",
                fix=(
                    "A 401 means the key is wrong or missing for the gateway its "
                    "shape selected; a 402 means that account is out of credit."
                ),
            )

        try:
            body = response.json()
        except ValueError as error:
            raise JevRequestFailed(
                "Jev returned a response that was not JSON."
            ) from error

        usage = body.get("usage") or {}
        decision = Decision(
            answers=body.get("answers") or {},
            model=str(body.get("model") or self.model),
            cost_usd=usage.get("cost"),
            input_tokens=usage.get("input_tokens"),
        )
        if self.on_exchange is not None:
            # Best effort: a run must not fail because its log could not be written.
            with contextlib.suppress(Exception):
                self.on_exchange(
                    rendered_state, questions, decision, time.monotonic() - started
                )
        return decision
