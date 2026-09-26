"""Stand-ins that let the logic be tested without a phone attached.

The stand-ins below are for pure logic only - option naming, screen parsing,
signature comparison. Anything that asks a question about Android itself has to
be answered by the phone, because a fake can only ever repeat what its author
believed: the fake used to agree that a bare domain opens a browser, and it was
wrong for as long as nobody asked the device.
"""

from __future__ import annotations

import pytest

import phone_control.adb as adb_module
from phone_control.adb import AndroidPhone, CommandResult
from phone_control.device_state import read_device_state
from phone_control.errors import PhoneCommandFailed
from phone_control.jev import JevClient
from phone_control.screen import Screen, parse_screen

WAKE_KEYCODE = "224"


def a_phone_is_attached() -> bool:
    try:
        return AndroidPhone().is_connected()
    except Exception:
        return False


@pytest.fixture(scope="session")
def phone() -> AndroidPhone:
    """The attached phone, awake and unlocked, or a skip explaining why not.

    Session-scoped because connecting is slow, and every scenario is responsible
    for putting the phone where it needs it before it starts.
    """
    if not a_phone_is_attached():
        pytest.skip("no Android phone is attached over USB")

    handle = AndroidPhone()
    handle.run(["shell", "input", "keyevent", WAKE_KEYCODE])

    state = read_device_state(handle)
    if state.is_locked:
        pytest.skip("the phone is locked, so only the lock screen is readable")
    return handle


@pytest.fixture(scope="session")
def jev() -> JevClient:
    """A configured Jev client, or a skip when no key is present."""
    client = JevClient()
    if not client.is_configured:
        pytest.skip("no Jev key is configured")
    return client


CHAT_SCREEN_XML = """<?xml version='1.0' encoding='UTF-8' standalone='yes' ?>
<hierarchy rotation="0">
  <node index="0" text="" resource-id="" class="android.widget.FrameLayout" package="com.example.chat" content-desc="" checkable="false" checked="false" clickable="false" enabled="true" focusable="false" focused="false" scrollable="false" long-clickable="false" password="false" selected="false" bounds="[0,0][1080,2400]">
    <node index="0" text="Messages" resource-id="com.example.chat:id/title" class="android.widget.TextView" package="com.example.chat" content-desc="" clickable="false" enabled="true" focused="false" scrollable="false" checkable="false" checked="false" selected="false" bounds="[40,120][420,200]" />
    <node index="1" text="" resource-id="" class="android.widget.LinearLayout" package="com.example.chat" content-desc="" clickable="true" enabled="true" focused="false" scrollable="false" checkable="false" checked="false" selected="false" bounds="[0,300][1080,600]">
      <node index="0" text="Alice" resource-id="com.example.chat:id/sender" class="android.widget.TextView" package="com.example.chat" content-desc="" clickable="false" enabled="true" bounds="[40,320][300,380]" />
      <node index="1" text="See you at six" resource-id="com.example.chat:id/body" class="android.widget.TextView" package="com.example.chat" content-desc="" clickable="false" enabled="true" bounds="[40,390][620,450]" />
    </node>
    <node index="2" text="Type a message" resource-id="com.example.chat:id/input" class="android.widget.EditText" package="com.example.chat" content-desc="" clickable="true" enabled="true" focusable="true" focused="true" scrollable="false" checkable="false" checked="false" selected="false" bounds="[40,2100][860,2240]" />
    <node index="3" text="" resource-id="com.example.chat:id/send" class="android.widget.ImageButton" package="com.example.chat" content-desc="Send message" clickable="true" enabled="true" focused="false" scrollable="false" checkable="false" checked="false" selected="false" bounds="[900,2100][1040,2240]" />
    <node index="4" text="Hidden" resource-id="com.example.chat:id/ghost" class="android.widget.TextView" package="com.example.chat" content-desc="" clickable="false" enabled="true" bounds="[0,0][0,0]" />
  </node>
</hierarchy>
"""

INSTALLED_PACKAGES_OUTPUT = "\n".join(
    [
        "package:com.android.settings",
        "package:com.example.chat",
        "package:com.google.android.youtube",
        "package:com.google.android.apps.youtube.music",
        "package:org.thoughtcrime.securesms",
    ]
)


class FakePhone:
    """Answers adb commands from a lookup table and records what it was asked.

    The lookup matches on a fragment of the command line, so a test declares
    only the commands it cares about.
    """

    def __init__(
        self,
        answers: dict[str, str] | None = None,
        complaints: dict[str, str] | None = None,
        failures: dict[str, int] | None = None,
    ) -> None:
        self.answers = answers or {}
        # Some device commands report failure only on stderr while still exiting
        # zero, so a fake has to be able to produce that.
        self.complaints = complaints or {}
        # Others do exit non-zero, which is a different failure path.
        self.failures = failures or {}
        self.commands: list[str] = []

    def _reply(self, command: str) -> str:
        for fragment, response in self.answers.items():
            if fragment in command:
                return response
        return ""

    def _complaint(self, command: str) -> str:
        for fragment, complaint in self.complaints.items():
            if fragment in command:
                return complaint
        return ""

    def _exit_code(self, command: str) -> int:
        for fragment, code in self.failures.items():
            if fragment in command:
                return code
        return 0

    def run_result(self, arguments, timeout=None) -> CommandResult:
        command = " ".join(arguments)
        self.commands.append(command)
        return CommandResult(
            stdout=self._reply(command).encode("utf-8"),
            stderr=self._complaint(command).encode("utf-8"),
            return_code=self._exit_code(command),
        )

    def run(self, arguments, timeout=None) -> str:
        return self.run_result(arguments, timeout=timeout).text

    def run_checked(self, arguments, timeout=None) -> str:
        """Mirror AndroidPhone.run_checked: non-zero exit is a failure."""
        result = self.run_result(arguments, timeout=timeout)
        message = result.error_text.strip()
        if result.return_code != 0 or "Exception" in message or "Error:" in message:
            raise PhoneCommandFailed(" ".join(arguments), message or result.text)
        return result.text

    def shell(self, command: str, timeout=None) -> str:
        return self.run(["shell", command], timeout=timeout)

    def run_binary(self, arguments, timeout=None) -> bytes:
        return self.run_result(arguments, timeout=timeout).stdout

    def serial_number(self) -> str:
        return "FAKE0000"

    def commands_matching(self, fragment: str) -> list[str]:
        return [command for command in self.commands if fragment in command]


@pytest.fixture
def fake_phone() -> FakePhone:
    return FakePhone({"pm list packages": INSTALLED_PACKAGES_OUTPUT})


class ScriptedPhone(FakePhone):
    """A phone whose screen a test can change partway through."""

    def __init__(self, screen_xml: str = CHAT_SCREEN_XML) -> None:
        super().__init__(
            {
                "wm size": "Physical size: 1080x2400",
                "pm list packages": INSTALLED_PACKAGES_OUTPUT,
            }
        )
        self.screen_xml = screen_xml
        self.reactions: list[tuple[str, str]] = []

    def show(self, screen_xml: str) -> None:
        """Change what the next screen reading will return."""
        self.screen_xml = screen_xml

    def after(self, command_fragment: str, screen_xml) -> None:
        """Show a different screen once this command has been sent.

        This is how a test models an action having an effect: typing into a field
        makes the next reading of that field different. Pass a callable instead of
        a screen to change something other than the screen.
        """
        self.reactions.append((command_fragment, screen_xml))

    def run(self, arguments, timeout=None) -> str:
        result = super().run(arguments, timeout=timeout)
        command = " ".join(arguments)
        for fragment, reaction in list(self.reactions):
            if fragment in command:
                if callable(reaction):
                    reaction()
                else:
                    self.screen_xml = reaction
                self.reactions.remove((fragment, reaction))
        return result

    def _reply(self, command: str) -> str:
        if "cat /sdcard" in command:
            return self.screen_xml
        return super()._reply(command)


@pytest.fixture
def scripted_phone(monkeypatch) -> ScriptedPhone:
    """Wire the canned screen into the tool surface, with no device involved."""
    phone = ScriptedPhone()
    monkeypatch.setattr(adb_module, "_shared_phone", phone)
    # The app list is cached on a module-level handle, so reset it per test.
    import phone_control.server as server_module

    monkeypatch.setattr(server_module, "_installed_apps", None)
    return phone


@pytest.fixture
def phone_with_one_app() -> FakePhone:
    """A phone that has almost nothing installed."""
    return FakePhone({"pm list packages": "package:com.example.chat"})


@pytest.fixture
def chat_screen_xml() -> str:
    """The raw dump, for tests that need to alter it before showing it."""
    return CHAT_SCREEN_XML


@pytest.fixture
def chat_screen() -> Screen:
    return parse_screen(CHAT_SCREEN_XML)


# --- the one failure that is not the code's fault -------------------------
#
# A device test that drives a real goal talks to Jev over the network for every
# step, so it can fail because a connection dropped rather than because anything
# here is wrong. That has now happened twice in two rounds, to two different tests:
# `run_task` asking whether an impossible goal was reached, and a photo deletion.
# Neither reproduced afterwards - the second one passed four times in a row while
# being investigated - which is what a lost packet looks like from here.
#
# So it is tolerated in exactly one place, for exactly one failure. `JevRequestFailed`
# is what a request that never arrived raises, and nothing else is caught: a wrong
# answer, a bad screen or an assertion failure still fails the test, because those
# are things this project can fix.


def a_goal_that_needs_the_network(work, what: str):
    """Run `work`, giving a dropped connection one more try and then standing aside.

    A lost connection is not always the exception that arrives. Called straight
    through `run_task` it is `JevRequestFailed`; called through the MCP tool the
    server wraps every failure in a `ToolError` whose cause is that, so the test sees
    the wrapper and a helper matching only the inner type never fires - which is
    exactly what happened, and the pre-commit hook refused the commit. The whole
    cause chain is walked instead, and anything else propagates on the first attempt.
    """
    from phone_control.errors import JevRequestFailed

    def was_the_network(raised: BaseException) -> bool:
        seen = raised
        while seen is not None:
            if isinstance(seen, JevRequestFailed):
                return True
            seen = seen.__cause__ or seen.__context__
        return False

    for attempt in (1, 2):
        try:
            return work()
        except Exception as raised:
            if not was_the_network(raised):
                raise
            if attempt == 2:
                pytest.skip(
                    f"{what} needs the network, which is not carrying it: {raised}"
                )

    # Unreachable: the first attempt returns, and the second either returns,
    # re-raises or stands the test aside. Stated rather than left to be
    # inferred, because a silent None here would look like a passing goal.
    raise AssertionError("the retry loop finished without an answer")
