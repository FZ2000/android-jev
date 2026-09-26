"""The two tools that do the whole job, called the way a client calls them.

Every other test of the tool surface exercises one action: tap this, read that.
These two are the server's reason for existing - `run_task` carries out a whole
goal, and `decide_next_action` turns a goal into one concrete step - and until this
file existed neither was called by any test. They were published, documented, and
in the case of `run_task` several hundred lines long, with nothing checking that
calling them works at all. That is how a tool ships broken while every test passes.

Both are asserted in both directions, because both have failed in both: a run that
did the job and reported failure, and a run that reported success over a phone that
had not moved.
"""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path

import phone_control.server as the_server_module
from conftest import a_goal_that_needs_the_network
from phone_control.adb import AndroidPhone
from phone_control.device_state import foreground_package
from phone_control.server import server
from scenarios.starting_states import go_home, stop

CALCULATOR = "com.google.android.calculator"
CHROME = "com.android.chrome"


def call(tool_name: str, **arguments):
    """Call a tool the way a client would."""
    return asyncio.run(server.call_tool(tool_name, arguments))


def the_text(result) -> str:
    """The text a client would show a model, out of an MCP result."""
    parts = []
    for block in result.content:
        text = getattr(block, "text", None)
        if text is not None:
            parts.append(text)
    return "\n".join(parts)


def the_reply(result) -> dict:
    """The JSON a tool returns, parsed, so the assertions read as claims."""
    return json.loads(the_text(result))


def a_goal_through_the_tool(goal: str, what: str) -> dict:
    """Call `run_task` and parse the reply, standing aside if the model is down.

    Every test in this file drives a real run, so every one of them needs the
    decision model to answer. A dropped connection and a 5xx from the service are the
    same event from here - `JevRequestFailed` either way - and both were seen taking
    the suite red and the pre-commit hook refusing a commit over something that is not
    in this repository. The tolerance is shared rather than written once per test.
    """
    reply = a_goal_that_needs_the_network(lambda: call("run_task", goal=goal), what)
    return the_reply(reply)


def a_fresh_launcher(phone: AndroidPhone) -> None:
    """Home, with the apps these tests open stopped first."""
    stop(phone, CALCULATOR, CHROME)
    go_home(phone)
    time.sleep(1.0)


def test_run_task_carries_out_a_goal_and_says_so(phone, jev):
    """The whole point of the server, called the way MCP calls it.

    Asserted both ways on purpose: the phone has to be on the app, and the report
    has to claim it. A test that checked only the phone would pass while the tool
    told its caller the job had failed, which is what an agent relays to the user.
    """
    a_fresh_launcher(phone)

    reply = a_goal_through_the_tool(
        "open the calculator app", "a run of the calculator goal"
    )

    assert reply["achieved"] is True, (
        f"the phone is on {foreground_package(phone)}, but the tool reported "
        f"{reply['outcome']}: {reply['reason']}"
    )
    assert reply["outcome"] == "done"
    assert foreground_package(phone) == CALCULATOR, (
        "the tool claimed the goal was reached and the phone is not on the app"
    )
    assert reply["final_foreground_app"] == CALCULATOR
    assert reply["decided_by"].startswith("jev")
    assert reply["steps"], "a run that did something reported no steps"


def test_the_run_task_reply_carries_what_the_docstring_promises(phone, jev):
    """Every field the skill tells an agent to read is actually in the answer.

    The skill is generated from the same repository as the tools, so the two
    drifting apart is this project's to prevent. An agent told to branch on
    `outcome` needs `outcome` to be there.
    """
    a_fresh_launcher(phone)

    reply = a_goal_through_the_tool(
        "open the calculator app", "a run of the calculator goal"
    )

    for field in (
        "ok",
        "summary",
        "goal",
        "achieved",
        "outcome",
        "reason",
        "step_count",
        "steps",
        "final_foreground_app",
        "decided_by",
    ):
        assert field in reply, f"the reply is missing {field!r}, which the skill names"

    assert reply["goal"] == "open the calculator app"
    assert reply["step_count"] == len(reply["steps"])
    assert isinstance(reply["summary"], str)
    assert reply["summary"]


def test_run_task_writes_a_folder_a_person_can_read_afterwards(
    phone, jev, tmp_path, monkeypatch
):
    """The record has to be on disk and named in the reply.

    Debugging this project has twice come down to reading what a run actually
    asked and was told, and both times the evidence was in one of these files. A
    folder that is written but not named is one the caller has to guess at.
    """
    a_fresh_launcher(phone)
    # Recording is off unless a directory is named, which is the documented default:
    # a run must not scatter folders over someone's home directory.
    monkeypatch.setenv("PHONE_CONTROL_RUNS", str(tmp_path))

    reply = a_goal_through_the_tool(
        "open the calculator app", "a run of the calculator goal"
    )

    assert "run_folder" in reply, (
        "the reply does not name the folder the run wrote, so nobody can go and "
        "read what it did"
    )
    folder = Path(reply["run_folder"])
    assert folder.is_dir(), f"{folder} was named but does not exist"
    for name in ("README.txt", "steps.jsonl", "decisions.jsonl", "run.json"):
        assert (folder / name).is_file(), f"{name} was not written"


def test_run_task_reports_a_goal_it_cannot_reach_as_unreached(phone, jev):
    """A goal no run can complete must come back as not achieved, and say why.

    The dangerous direction is a run that invents success. This is the one place
    the server is asked for something impossible on a real phone, so the claim is
    worth making here rather than against a fake.

    This one reaches its conclusion over several steps plus a hand-off, so it needs
    the network to hold for the length of a run. A dropped connection is tolerated
    once and then stands the test aside, for the reason set out in conftest beside
    the helper - it was written after the suite went red on an httpx.ConnectTimeout
    and the pre-commit hook refused the commit, which is how it got noticed.
    """
    a_fresh_launcher(phone)

    reply = the_reply(
        a_goal_that_needs_the_network(
            lambda: call("run_task", goal="order a large pepperoni pizza to my house"),
            "a run that has to conclude a goal is unreachable",
        )
    )

    assert reply["achieved"] is False, (
        "a goal no phone can carry out was reported as achieved"
    )
    assert reply["outcome"] != "done"
    assert reply["reason"], "the run failed without saying anything about why"
    assert reply["ok"] is False


def test_decide_next_action_answers_with_something_that_can_be_carried_out(phone, jev):
    """The tool an agent uses to fetch one step has to give a whole step.

    A summary and the confidence that chose it, because the number is what tells the
    caller whether to act or to go and look at the screen. And when it names a tool,
    that tool has to be one this server publishes with the arguments it was given -
    the reference counts an option the loop cannot execute as a guaranteed stall, and
    so is a `next_action` naming a tool that does not exist.

    `next_action` is legitimately None in two cases, both explained in the summary:
    Jev reports the goal is already met, or it chose the operation "open an app",
    which this tool cannot name an app for and says so rather than guessing.
    """
    a_fresh_launcher(phone)

    reply = the_reply(
        a_goal_that_needs_the_network(
            lambda: call("decide_next_action", goal="open the calculator app"),
            "a step from the decision model",
        )
    )

    assert reply["summary"], "the tool answered without saying what it decided"
    assert isinstance(reply["confidence"], float)
    assert 0.0 <= reply["confidence"] <= 1.0
    assert "next_action" in reply

    next_action = reply["next_action"]
    if next_action is not None:
        published = {tool.name for tool in asyncio.run(server.list_tools())}
        assert next_action["tool"] in published, (
            f"the tool names {next_action['tool']!r}, which this server does not "
            f"publish, so the caller cannot carry out the step it was given"
        )
        assert isinstance(next_action.get("arguments"), dict)


def test_run_task_says_so_when_no_decision_model_is_configured(monkeypatch):
    """Without a key the tool fails as a failure, not as a sentence.

    Fast rather than device: the check happens before the phone is touched, and the
    shape of the failure is the contract - a returned string would reach the client
    as a successful tool whose answer was an explanation.
    """
    from mcp.types import CallToolRequestParams

    import phone_control.jev as the_jev_module

    monkeypatch.setattr(the_jev_module, "find_api_key", lambda: "")
    monkeypatch.setattr(the_server_module, "_jev_client", None, raising=False)

    result = asyncio.run(
        the_server_module.server._handle_call_tool(
            None,
            CallToolRequestParams(
                name="run_task", arguments={"goal": "open the clock"}
            ),
        )
    )
    text = result.content[0].text

    assert result.is_error is True, (
        f"a tool with no model behind it reported success: {text!r}"
    )
    assert "key" in text.casefold(), (
        f"the failure does not say what to do about it: {text!r}"
    )
