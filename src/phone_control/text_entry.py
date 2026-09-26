"""Putting text into the phone's focused field.

``input text`` only understands ASCII and has its own idea of what a space is,
so the encoding and the shell quoting are separated here and tested on their
own rather than buried in the tool surface.
"""

from __future__ import annotations

from .adb import AndroidPhone, CommandResult, quote_for_device_shell

ENTER_KEYCODE = 66
PASTE_KEYCODE = 279

# `cmd clipboard` is not implemented on every build, and a build without it still
# exits 0 while printing this. Two independent adb projects hit the same trap, so
# the exit code alone is never treated as evidence that the clip was stored.
CLIPBOARD_UNSUPPORTED_MARKERS = (
    "no shell command implementation",
    "no shell command",
    "unknown command",
    "not enough data",
)

NON_ASCII_FIX = (
    "Android's `input text` only types ASCII. Either type the ASCII part and "
    "leave the rest, or install the ADBKeyboard app, enable it as the current "
    "input method, and retry."
)


def encode_for_input_text(text: str) -> str:
    """Encode one line for ``input text``, which spells a space as ``%s``.

    Line breaks are dropped rather than encoded: ``input text`` has no way to
    send one, and a stray newline would corrupt the argument the device shell
    receives. Callers wanting a line break send an Enter keypress instead.
    """
    return text.replace("\r", "").replace("\n", "").replace(" ", "%s")


def needs_non_ascii_workaround(text: str) -> bool:
    return any(ord(character) > 127 for character in text)


def type_text_on_device(
    phone: AndroidPhone, text: str, *, submit: bool = False
) -> None:
    """Type text into whatever field currently has focus."""
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    for position, line in enumerate(lines):
        if line:
            phone.run(
                [
                    "shell",
                    "input",
                    "text",
                    quote_for_device_shell(encode_for_input_text(line)),
                ]
            )
        if position < len(lines) - 1:
            phone.run(["shell", "input", "keyevent", str(ENTER_KEYCODE)])
    if submit:
        phone.run(["shell", "input", "keyevent", str(ENTER_KEYCODE)])


def _clipboard_is_unsupported(result: CommandResult) -> bool:
    complaint = result.error_text.casefold()
    return any(marker in complaint for marker in CLIPBOARD_UNSUPPORTED_MARKERS)


def read_device_clipboard(phone: AndroidPhone) -> str | None:
    """The phone's clipboard text, or None when the phone will not say."""
    result = phone.run_result(["shell", "cmd", "clipboard", "get"])
    if _clipboard_is_unsupported(result):
        return None
    return result.text.strip()


def set_device_clipboard(phone: AndroidPhone, text: str) -> bool:
    """Put text on the phone's clipboard, reporting whether it actually landed.

    A build without `cmd clipboard` exits zero while printing "No shell command
    implementation", so the exit code proves nothing, and the value is read back
    to confirm. Claiming a clipboard that was never set is the expensive
    mistake: the paste that follows is silently wrong rather than absent.
    """
    result = phone.run_result(
        ["shell", "cmd", "clipboard", "set", quote_for_device_shell(text)]
    )
    if _clipboard_is_unsupported(result):
        return False

    stored = read_device_clipboard(phone)
    if stored is None:
        # Reading is blocked on some builds even where writing works. Give the
        # write the benefit of the doubt rather than losing the route entirely.
        return True
    return stored == text.strip()


def paste_device_clipboard(phone: AndroidPhone) -> None:
    phone.run(["shell", "input", "keyevent", str(PASTE_KEYCODE)])


# How many delete keycodes one `input keyevent` command may carry. A single command
# has a limit, and the earlier version quietly left a prefix behind on a long field
# while reporting that it had been cleared.
KEYCODES_PER_COMMAND = 20
SELECT_ALL_KEYCODES = ("113", "29")


def clear_the_focused_field(
    phone: AndroidPhone,
    existing_text: str,
    *,
    keycodes_per_command: int = KEYCODES_PER_COMMAND,
) -> None:
    """Empty the focused field, however much text is in it.

    Select-all then delete removes any amount of text, and the chunked deletes that
    follow are a backstop for fields that ignore select-all. Lives here rather than in
    the tool surface because the loop needs it too: a goal states what a field should
    hold, so typing into a field that already holds something replaces it, and two
    implementations of "clear the field" would drift.
    """
    phone.run(["shell", "input", "keycombination", *SELECT_ALL_KEYCODES])
    remaining = len(existing_text) + 4
    while remaining > 0:
        batch = min(remaining, keycodes_per_command)
        phone.run(["shell", "input", "keyevent", "123", *(["67"] * batch)])
        remaining -= batch
