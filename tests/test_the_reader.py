"""The two readers, and the promise each one makes about what it sends.

A reader is consulted when the loop has stopped, so it is the one part of a run
that may ask a model something. Both readers were untested: the picture route had
no test at all, and the written route's network failures - unreachable, an error
status, an answer that is not JSON - had none either. Those are the paths that
decide whether a run carries on or gives up, and they are also where a screen can
accidentally leave the machine.

That last part is why the redaction test is here rather than nowhere. The packet
handed to a reader carries the phone object under an underscored key, and the
request builder is the only thing standing between it and a model.
"""

from __future__ import annotations

import asyncio
import json

import httpx

import phone_control.reader as the_reader_module
from phone_control.reader import (
    ANTHROPIC,
    API_KEY_VARIABLE,
    API_VARIABLE,
    BASE_URL_VARIABLE,
    MODEL_VARIABLE,
    OPENAI,
    AReaderOfModels,
    AReaderThatCannotWrite,
    AReaderThatLooksAtTheScreen,
    reader_from_the_environment,
)

# --- the stand-ins -------------------------------------------------------


class AnAnswer:
    """One Noul, as the decider's client returns it."""

    def __init__(self, probability: float) -> None:
        self.probability_yes = probability


class AFakeDecision:
    def __init__(self, probability: float) -> None:
        self.probability = probability

    def yes_or_no(self, name: str) -> AnAnswer:
        return AnAnswer(self.probability)


class ADeciderThatSays:
    """A decider client with one number, so a threshold can be pinned."""

    def __init__(self, probability: float = 0.95, is_configured: bool = True) -> None:
        self.probability = probability
        self.is_configured = is_configured
        self.asked: list[tuple[dict, dict]] = []

    async def ask(self, state, questions):
        self.asked.append((state, questions))
        return AFakeDecision(self.probability)


class AFakeHTTP:
    """A stand-in for whatever the reader reaches the network with.

    Used as a callable so it can replace ``httpx.AsyncClient`` itself: the reader
    builds its own client, so the only seam is the class.
    """

    def __init__(
        self, status_code: int = 200, payload=None, error: Exception | None = None
    ):
        self.status_code = status_code
        self.payload = payload
        self.error = error
        self.requests: list[dict] = []

    def __call__(self, *arguments, **keywords):
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exception):
        return False

    async def post(self, url, headers=None, json=None):
        self.requests.append({"url": url, "headers": headers, "json": json})
        if self.error is not None:
            raise self.error
        return self

    def json(self):
        if isinstance(self.payload, Exception):
            raise self.payload
        return self.payload


def lines_from_a_picture(monkeypatch, lines: list[str]) -> None:
    """Make the picture route give back these lines.

    Patched on `reading_a_picture` rather than on the reader, because the reader
    imports the function inside `read()`: the name is looked up when it is called,
    so this is the seam that works and the one a reader would really use.
    """
    import phone_control.reading_a_picture as pictures

    monkeypatch.setattr(pictures, "read_the_picture", lambda phone: lines)


# --- the reader that cannot write ---------------------------------------


def test_a_reader_that_cannot_write_says_so_rather_than_pretending():
    """The default, and it has to be honest: not configured, and it says why."""
    reader = AReaderThatCannotWrite()

    assert reader.is_configured is False
    said = asyncio.run(reader.read({"goal": "anything"}))
    assert said.achieved is False
    assert "no reader" in said.because


# --- choosing which reader ----------------------------------------------


def test_a_named_model_is_used_as_the_reader(monkeypatch):
    """A reader that can write is better, so naming one selects it."""
    monkeypatch.setenv(MODEL_VARIABLE, "some-model")
    monkeypatch.setenv(BASE_URL_VARIABLE, "https://example.invalid/")
    monkeypatch.setenv(API_KEY_VARIABLE, "a-key")
    monkeypatch.setenv(API_VARIABLE, OPENAI)

    reader = reader_from_the_environment()

    assert isinstance(reader, AReaderOfModels)
    assert reader.model == "some-model"
    assert reader.base_url == "https://example.invalid"
    assert reader.speaks == OPENAI


def test_with_no_model_named_the_reader_is_eyes(monkeypatch):
    """No key and no second model: this machine can still read a screen by looking."""
    monkeypatch.delenv(MODEL_VARIABLE, raising=False)

    assert isinstance(reader_from_the_environment(), AReaderThatLooksAtTheScreen)


def test_a_reader_that_does_not_speak_a_known_api_falls_back_to_anthropic():
    """An unusable value must not travel; the wire format has to be one of the two."""
    reader = AReaderOfModels(
        base_url="https://example.invalid", model="m", speaks="gemini"
    )

    assert reader.speaks == ANTHROPIC


def test_eyes_are_configured_only_when_this_machine_can_read_pictures(monkeypatch):
    reader = AReaderThatLooksAtTheScreen()

    monkeypatch.setattr(
        "phone_control.reading_a_picture.macos_can_read_pictures", lambda: True
    )
    assert reader.is_configured is True

    monkeypatch.setattr(
        "phone_control.reading_a_picture.macos_can_read_pictures", lambda: False
    )
    assert reader.is_configured is False


# --- the reader that looks at the screen ---------------------------------


def the_packet(goal: str = "show the about phone page") -> dict:
    return {"goal": goal, "_phone": object()}


def test_eyes_with_no_phone_to_look_at_say_so():
    reader = AReaderThatLooksAtTheScreen(client=ADeciderThatSays())

    said = asyncio.run(reader.read({"goal": "anything"}))

    assert said.achieved is False
    assert "no phone" in said.because


def test_eyes_that_read_no_text_out_of_the_picture_say_so(monkeypatch):
    lines_from_a_picture(monkeypatch, [])
    reader = AReaderThatLooksAtTheScreen(client=ADeciderThatSays())

    said = asyncio.run(reader.read(the_packet()))

    assert said.achieved is False
    assert "no text" in said.because


def test_eyes_with_no_decider_to_judge_with_say_so(monkeypatch):
    """The picture was read, and nothing can be concluded from it. Still honest."""
    lines_from_a_picture(monkeypatch, ["About phone"])
    reader = AReaderThatLooksAtTheScreen(client=ADeciderThatSays(is_configured=False))

    said = asyncio.run(reader.read(the_packet()))

    assert said.achieved is False
    assert "no decider" in said.because


def test_eyes_judge_completion_against_the_measured_threshold(monkeypatch):
    """Above the threshold it is done; the number comes back either way.

    The threshold is the one already measured for this question (0.02 on a screen
    that does not show the goal against 0.96 on one that does), so the interesting
    assertion is the boundary rather than the number.
    """
    lines_from_a_picture(monkeypatch, ["About phone", "Pixel 8a"])

    certain = ADeciderThatSays(probability=0.95)
    said = asyncio.run(AReaderThatLooksAtTheScreen(client=certain).read(the_packet()))
    assert said.achieved is True
    assert "0.95" in said.because
    assert said.answer, "a reader that says the goal is met should say what it saw"

    unsure = ADeciderThatSays(probability=0.40)
    said = asyncio.run(AReaderThatLooksAtTheScreen(client=unsure).read(the_packet()))
    assert said.achieved is False
    assert said.answer == ""


def test_eyes_send_the_lines_and_the_goal_and_nothing_they_do_not_have(monkeypatch):
    """The state is what the picture said, and it does not invent controls.

    This reader has no controls, no window and no history, and saying otherwise
    would invite the decider to reason about a tree that does not exist.
    """
    import asyncio

    lines_from_a_picture(monkeypatch, ["About phone", "Device name"])
    decider = ADeciderThatSays()

    asyncio.run(AReaderThatLooksAtTheScreen(client=decider).read(the_packet()))

    state, _ = decider.asked[0]
    assert state["goal"] == "show the about phone page"
    assert state["what_the_screen_says"] == ["About phone", "Device name"]
    assert "photograph" in state["how_this_was_read"]
    assert "controls_on_screen" not in state
    assert "foreground_window" not in state


# --- what actually leaves the machine ------------------------------------


def test_the_phone_never_travels_to_a_model():
    """The privacy promise, asserted where it can be broken.

    The packet carries the phone under an underscored key so the reader can take a
    picture with it. The request builder drops every such key, and it is the only
    thing between a live device handle and somebody else's server.
    """
    reader = AReaderOfModels(base_url="https://example.invalid", model="m", api_key="k")
    packet = {
        "goal": "send a text to Alice",
        "_phone": object(),
        "_anything_else": object(),
        "what_the_screen_says": ["Messages"],
    }

    body, _ = reader._the_request(packet)
    sent = json.dumps(body["json"])

    assert "_phone" not in sent
    assert "_anything_else" not in sent
    assert "object at 0x" not in sent, "a repr of a live object was sent"
    assert "send a text to Alice" in sent, (
        "the goal was dropped along with the private keys"
    )


def test_each_host_is_spoken_to_in_its_own_wire_format():
    """Two vendors, two shapes, and the key goes only to the host it belongs to."""
    openai_reader = AReaderOfModels(
        base_url="https://api.openai.invalid",
        model="m",
        api_key="the-key",
        speaks=OPENAI,
    )
    body, headers = openai_reader._the_request({"goal": "g"})

    assert body["url"] == "https://api.openai.invalid/v1/chat/completions"
    assert headers == {"Authorization": "Bearer the-key"}
    assert body["json"]["response_format"] == {"type": "json_object"}

    anthropic_reader = AReaderOfModels(
        base_url="https://api.anthropic.invalid", model="m", api_key="the-key"
    )
    body, headers = anthropic_reader._the_request({"goal": "g"})

    assert body["url"] == "https://api.anthropic.invalid/v1/messages"
    assert headers["x-api-key"] == "the-key"
    assert "Authorization" not in headers


# --- and when the network does not cooperate -----------------------------


def the_reader_reaching(monkeypatch, fake: AFakeHTTP) -> AReaderOfModels:
    monkeypatch.setattr(the_reader_module.httpx, "AsyncClient", fake)
    return AReaderOfModels(base_url="https://example.invalid", model="m", api_key="k")


def test_a_reader_that_cannot_be_reached_refuses_the_step(monkeypatch):
    reader = the_reader_reaching(
        monkeypatch, AFakeHTTP(error=httpx.ConnectError("no route to host"))
    )

    said = asyncio.run(reader.read({"goal": "g"}))

    assert said.achieved is False
    assert "could not be reached" in said.because


def test_a_reader_that_answers_an_error_status_refuses_the_step(monkeypatch):
    reader = the_reader_reaching(monkeypatch, AFakeHTTP(status_code=500))

    said = asyncio.run(reader.read({"goal": "g"}))

    assert said.achieved is False
    assert "500" in said.because


def test_a_reader_that_answers_something_that_is_not_json_refuses_the_step(monkeypatch):
    reader = the_reader_reaching(
        monkeypatch, AFakeHTTP(payload=ValueError("not json at all"))
    )

    said = asyncio.run(reader.read({"goal": "g"}))

    assert said.achieved is False
    assert "not JSON" in said.because


def test_a_reader_that_answers_properly_is_read(monkeypatch):
    """The happy path, through the same seam, so the failure tests mean something."""
    written = {"content": [{"text": json.dumps({"achieved": True, "answer": "done"})}]}
    reader = the_reader_reaching(monkeypatch, AFakeHTTP(payload=written))

    said = asyncio.run(reader.read({"goal": "g"}))

    assert said.achieved is True
    assert said.answer == "done"
    assert reader.calls == 1
