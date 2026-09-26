"""Talking to Jev: the request it receives and the typed answers it returns."""

from __future__ import annotations

import json

import httpx
import pytest

from phone_control.errors import JevNotConfigured, JevRequestFailed
from phone_control.jev import (
    JEV_MAX_ALTERNATIVES,
    JEV_MAX_STATE_CHARACTERS,
    KEY_FILE_NAME,
    SECRETS_DIRECTORY_VARIABLE,
    JevClient,
    choice_question,
    find_api_key,
    scale_question,
    yes_or_no_question,
)

CANNED_RESPONSE = {
    "id": "gen-dec-test",
    "model": "typesafe/jev-1.13-20260917",
    "provider": "TypeSafe",
    "answers": {
        "is_bug": {"type": "noul", "noul": 0.96},
        "team": {
            "type": "choice",
            "choice": "payments",
            "confidence": 0.67,
            "probabilities": {"payments": 0.78, "frontend": 0.22, "account": 0},
        },
        "urgency": {
            "type": "score",
            "score": 1.99,
            "confidence": 0.99,
            "probabilities": {"0": 0, "1": 0, "2": 1},
            "legend": {"0": "Can wait", "1": "This week", "2": "Right now"},
        },
    },
    "usage": {"input_tokens": 476, "output_tokens": 70, "cost": 0.000019992},
}


def client_answering(body, status_code=200, seen_requests=None) -> JevClient:
    def handler(request: httpx.Request) -> httpx.Response:
        if seen_requests is not None:
            seen_requests.append(request)
        return httpx.Response(status_code, json=body)

    return JevClient(api_key="test-key", transport=httpx.MockTransport(handler))


async def test_a_choice_comes_back_with_its_probabilities():
    decision = await client_answering(CANNED_RESPONSE).ask(
        {"ticket": "checkout is blank"},
        {
            "team": choice_question(
                "Which team?", {"payments": "money", "frontend": "ui"}
            )
        },
    )

    answer = decision.choice("team")

    assert answer.option == "payments"
    assert answer.confidence == 0.67
    assert answer.probabilities["frontend"] == 0.22


async def test_a_yes_or_no_answer_is_the_probability_of_yes():
    decision = await client_answering(CANNED_RESPONSE).ask(
        {"ticket": "checkout is blank"},
        {
            "is_bug": yes_or_no_question(
                "Is it a bug?", when_true="yes", when_false="no"
            )
        },
    )

    assert decision.yes_or_no("is_bug").probability_yes == 0.96


async def test_a_scale_answer_keeps_its_legend():
    decision = await client_answering(CANNED_RESPONSE).ask(
        {"ticket": "checkout is blank"},
        {"urgency": scale_question("How urgent?", ["low", "medium", "high"])},
    )

    answer = decision.scale("urgency")

    assert answer.position == 1.99
    assert answer.legend["2"] == "Right now"


async def test_the_request_carries_the_key_the_model_and_the_questions():
    seen: list[httpx.Request] = []
    client = client_answering(CANNED_RESPONSE, seen_requests=seen)

    await client.ask(
        {"goal": "send the message"},
        {"team": choice_question("Which?", {"payments": "money"})},
    )

    request = seen[0]
    body = json.loads(request.content)
    assert request.headers["authorization"] == "Bearer test-key"
    assert body["model"] == "typesafe/jev-1.13"
    assert body["state"] == {"goal": "send the message"}
    assert body["questions"]["team"]["type"] == "choice"


async def test_a_rejected_request_says_which_status_came_back():
    client = client_answering({"error": "no credit"}, status_code=402)

    with pytest.raises(JevRequestFailed) as raised:
        await client.ask({"goal": "x"}, {"q": choice_question("Which?", {"a": "a"})})

    assert "402" in str(raised.value)


async def test_asking_without_a_key_explains_where_to_get_one(monkeypatch):
    # Both sources have to be shut off: the environment and the documented file,
    # which really does exist on a machine where the tools are in use.
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.setattr("phone_control.jev.find_api_key", lambda: "")
    client = JevClient()

    with pytest.raises(JevNotConfigured) as raised:
        await client.ask({"goal": "x"}, {"q": choice_question("Which?", {"a": "a"})})

    assert "openrouter.ai" in str(raised.value)


async def test_an_answer_outside_the_offered_question_is_refused():
    decision = await client_answering(CANNED_RESPONSE).ask(
        {"x": 1}, {"team": choice_question("Which?", {"payments": "money"})}
    )

    with pytest.raises(JevRequestFailed):
        decision.choice("never_asked")


def test_more_alternatives_than_jev_accepts_is_refused_before_sending():
    too_many = {f"option_{index}": "x" for index in range(JEV_MAX_ALTERNATIVES + 1)}

    with pytest.raises(ValueError, match="accepts at most 255 options"):
        choice_question("Which?", too_many)


def test_a_choice_question_with_no_options_is_refused():
    with pytest.raises(ValueError, match="needs at least one option"):
        choice_question("Which?", {})


def test_a_scale_with_a_single_level_is_refused():
    with pytest.raises(ValueError, match="needs at least two levels"):
        scale_question("How urgent?", ["only one"])


async def test_a_response_that_is_not_json_is_reported_as_such():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="<html>not json</html>")

    client = JevClient(api_key="test-key", transport=httpx.MockTransport(handler))

    with pytest.raises(JevRequestFailed):
        await client.ask({"goal": "x"}, {"q": choice_question("Which?", {"a": "a"})})


# --- the two gateways name the same model differently --------------------
#
# OpenRouter routes it as `typesafe/jev-1.13`; TypeSafe's own API serves plain
# `jev-latest`. Sending one gateway's name to the other is a 400, so the endpoint
# and the model have to travel together, chosen from the key's shape.


def test_an_openrouter_key_selects_the_openrouter_pair():
    client = JevClient(api_key="sk-or-v1-abc")

    assert client.provider.name == "openrouter"
    assert client.endpoint == "https://openrouter.ai/api/alpha/decisions"
    assert client.model == "typesafe/jev-1.13"


def test_a_typesafe_key_selects_the_typesafe_pair():
    client = JevClient(api_key="apikey_abc_def")

    assert client.provider.name == "typesafe"
    assert client.endpoint == "https://api.typesafe.ai/v1/systemone"
    assert client.model == "jev-latest"


def test_an_explicit_model_still_wins_over_the_provider_default():
    client = JevClient(api_key="apikey_abc_def", model="jev-preview")

    assert client.model == "jev-preview"
    assert client.endpoint == "https://api.typesafe.ai/v1/systemone"


def a_yes_or_no() -> dict:
    """The shape the client is asked to answer in, written once."""
    return yes_or_no_question(
        "is it a bug?",
        when_true="It is a bug.",
        when_false="It is not a bug.",
    )


# --- where the key comes from --------------------------------------------


def test_the_key_in_the_environment_wins(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "from-the-environment")

    assert find_api_key() == "from-the-environment"


def test_the_key_is_read_from_the_documented_file(monkeypatch, tmp_path):
    """The file is the second place, and it is what a machine without the
    variable set relies on."""
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    secrets = tmp_path / "secrets"
    secrets.mkdir()
    (secrets / KEY_FILE_NAME).write_text("from-the-file\n", encoding="utf-8")
    monkeypatch.setenv(SECRETS_DIRECTORY_VARIABLE, str(tmp_path))

    assert find_api_key() == "from-the-file"


def test_a_file_that_is_not_there_is_not_an_error(monkeypatch, tmp_path):
    """No key is a state, not a crash: `status` reports it and the tools say so."""
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.setenv(SECRETS_DIRECTORY_VARIABLE, str(tmp_path))
    monkeypatch.setattr(
        "phone_control.jev.FALLBACK_SECRETS_DIRECTORIES", (str(tmp_path / "none"),)
    )

    assert find_api_key() == ""


def test_an_empty_key_file_is_not_a_key(monkeypatch, tmp_path):
    """A file that exists but holds whitespace must not be taken as configured."""
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    secrets = tmp_path / "secrets"
    secrets.mkdir()
    (secrets / KEY_FILE_NAME).write_text("   \n", encoding="utf-8")
    monkeypatch.setenv(SECRETS_DIRECTORY_VARIABLE, str(tmp_path))
    monkeypatch.setattr("phone_control.jev.FALLBACK_SECRETS_DIRECTORIES", ())

    assert find_api_key() == ""


# --- asking, and what happens when the ask cannot be made ----------------


async def test_asking_nothing_at_all_is_refused_before_anything_is_sent():
    client = client_answering(CANNED_RESPONSE)

    with pytest.raises(ValueError, match="At least one question is required"):
        await client.ask({"goal": "anything"}, {})


async def test_a_network_that_will_not_carry_the_request_says_so():
    def refuse(request):
        raise httpx.ConnectError("no route to host")

    client = JevClient(api_key="test-key", transport=httpx.MockTransport(refuse))

    with pytest.raises(JevRequestFailed) as refused:
        await client.ask({"goal": "g"}, {"is_bug": a_yes_or_no()})

    assert "could not be completed" in str(refused.value)
    assert refused.value.fix, (
        "a network failure with no next step leaves the caller stuck"
    )


async def test_a_state_too_long_to_send_is_cut_rather_than_refused():
    """A screen can be enormous; the cap is what keeps the request sendable."""
    seen: list[httpx.Request] = []
    client = client_answering(CANNED_RESPONSE, seen_requests=seen)
    enormous = "x" * (JEV_MAX_STATE_CHARACTERS + 5_000)

    await client.ask(enormous, {"is_bug": a_yes_or_no()})

    sent = json.loads(seen[0].content)
    assert len(sent["state"]) == JEV_MAX_STATE_CHARACTERS


async def test_what_was_asked_and_answered_is_handed_to_the_recorder():
    """A run's record comes from the wire, not a reconstruction of the intent."""
    seen: list[tuple] = []
    client = JevClient(
        api_key="test-key",
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, json=CANNED_RESPONSE)
        ),
    )
    client.on_exchange = lambda state, questions, decision, seconds: seen.append(
        (state, questions, decision)
    )

    await client.ask({"goal": "g"}, {"is_bug": a_yes_or_no()})

    assert len(seen) == 1
    state, questions, decision = seen[0]
    assert state == {"goal": "g"}
    assert "is_bug" in questions
    assert decision.yes_or_no("is_bug").probability_yes == 0.96


async def test_a_recorder_that_falls_over_does_not_take_the_run_with_it():
    """The log is for whoever debugs afterwards; the run must still happen."""

    def explode(*arguments):
        raise RuntimeError("the disk is full")

    client = JevClient(
        api_key="test-key",
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, json=CANNED_RESPONSE)
        ),
    )
    client.on_exchange = explode

    decision = await client.ask({"goal": "g"}, {"is_bug": a_yes_or_no()})

    assert decision.yes_or_no("is_bug").probability_yes == 0.96
