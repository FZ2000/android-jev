"""Finding the phone, and refusing it in words a person can act on.

None of this was tested. It is the part of the project that runs before anything
else can, and every failure here is a message an agent relays to whoever is holding
the phone - which is why the skill spells two of them out as things to say plainly
rather than retry: no device attached, and a device that has not authorised this
computer.

The stub below reports a device list instead of asking adb, so each refusal can be
provoked on purpose. Nothing here touches a phone.
"""

from __future__ import annotations

import stat

import pytest

import phone_control.adb as adb_module
from phone_control.adb import (
    AdbNotFound,
    AndroidPhone,
    AttachedDevice,
    NoPhoneConnected,
    PhoneCommandFailed,
    PhoneNotAuthorized,
    _error_for_state,
    _parse_device_lines,
    find_adb_program,
)

# Any executable will do: these tests never run it, they only need the constructor
# to be handed something that exists.
AN_EXECUTABLE = "/bin/sh"

A_DEVICE_LIST = """List of devices attached
4C1F8A2E9D7B305          device product:husky model:Pixel_8a device:husky transport_id:1
emulator-5554           offline
0123456789ABCDEF        unauthorized
"""


def a_device(serial: str, state: str = "device", **keywords) -> AttachedDevice:
    """One row of a device list, with the fields these tests do not care about left empty."""
    return AttachedDevice(
        serial=serial,
        state=state,
        model=keywords.get("model"),
        product=keywords.get("product"),
    )


def a_phone(**keywords) -> AndroidPhone:
    return AndroidPhone(adb_program=AN_EXECUTABLE, **keywords)


def reporting(*devices: AttachedDevice, monkeypatch) -> AndroidPhone:
    """A phone whose device list is whatever a test says it is."""
    monkeypatch.setattr(AndroidPhone, "attached_devices", lambda self: list(devices))
    return a_phone()


# --- reading what adb printed -------------------------------------------


def test_the_device_list_is_read_row_by_row():
    devices = _parse_device_lines(A_DEVICE_LIST)

    assert [device.serial for device in devices] == [
        "4C1F8A2E9D7B305",
        "emulator-5554",
        "0123456789ABCDEF",
    ]
    assert [device.state for device in devices] == ["device", "offline", "unauthorized"]
    assert devices[0].model == "Pixel_8a"
    assert devices[0].product == "husky"
    assert devices[0].is_ready is True
    assert devices[1].is_ready is False


def test_the_header_and_commentary_are_not_devices():
    """``List of devices attached`` and a ``* daemon started`` notice are not rows."""
    devices = _parse_device_lines(
        "List of devices attached\n* daemon not running; starting now at tcp:5037\n"
        "* daemon started successfully\n\nshort\n"
    )

    assert devices == []


def test_a_device_is_named_the_way_a_person_would_say_it():
    with_a_model = a_device("s", "device", model="Pixel_8a")
    without_one = a_device("s")

    assert with_a_model.friendly_name == "Pixel 8a"
    assert without_one.friendly_name == "s"


# --- what each state means for the person holding the phone --------------


def test_an_unauthorised_phone_asks_for_a_tap_on_the_phone():
    error = _error_for_state(a_device("s", "unauthorized"))

    assert isinstance(error, PhoneNotAuthorized)
    assert "Allow USB debugging" in error.fix, (
        "the fix does not name the prompt that has to be tapped"
    )


def test_an_offline_phone_asks_for_the_cable():
    error = _error_for_state(a_device("s", "offline"))

    assert isinstance(error, PhoneCommandFailed)
    assert "cable" in error.fix.casefold()


def test_any_other_state_is_reported_rather_than_guessed_at():
    error = _error_for_state(a_device("s", "bootloader"))

    assert isinstance(error, NoPhoneConnected)
    assert "bootloader" in str(error)


# --- choosing which one to talk to ---------------------------------------


def test_one_ready_device_is_the_one(monkeypatch):
    phone = reporting(a_device("the-one", "device"), monkeypatch=monkeypatch)

    assert phone.serial_number() == "the-one"


def test_a_pinned_serial_wins(monkeypatch):
    monkeypatch.setenv("DSH_PHONE_SERIAL", "second")
    phone = a_phone()
    monkeypatch.setattr(
        AndroidPhone,
        "attached_devices",
        lambda self: [
            a_device("first", "device"),
            a_device("second", "device"),
        ],
    )

    assert phone.serial_number() == "second"


def test_a_pinned_serial_that_is_not_attached_says_which_ones_are(monkeypatch):
    """The fix lists what is actually there, so the caller can pick one."""
    monkeypatch.setenv("DSH_PHONE_SERIAL", "missing")
    phone = a_phone()
    monkeypatch.setattr(
        AndroidPhone,
        "attached_devices",
        lambda self: [a_device("present", "device")],
    )

    with pytest.raises(NoPhoneConnected) as refused:
        phone.serial_number()

    assert "missing" in str(refused.value)
    assert "present" in refused.value.fix


def test_two_ready_devices_are_refused_rather_than_guessed_between(monkeypatch):
    phone = reporting(
        a_device("one", "device"),
        a_device("two", "device"),
        monkeypatch=monkeypatch,
    )

    with pytest.raises(NoPhoneConnected) as refused:
        phone.serial_number()

    assert "DSH_PHONE_SERIAL" in refused.value.fix


def test_a_single_phone_that_has_not_authorised_is_reported_as_such(monkeypatch):
    """The one case that needs a person's hands, and it must not be a generic error."""
    phone = reporting(a_device("s", "unauthorized"), monkeypatch=monkeypatch)

    with pytest.raises(PhoneNotAuthorized):
        phone.serial_number()


def test_no_phone_at_all_says_how_to_attach_one(monkeypatch):
    phone = reporting(monkeypatch=monkeypatch)

    with pytest.raises(NoPhoneConnected) as refused:
        phone.serial_number()

    assert "USB debugging" in refused.value.fix


def test_the_serial_is_asked_for_once_and_then_remembered(monkeypatch):
    """Re-resolving on every command would add a subprocess to every single one."""
    phone = reporting(a_device("the-one", "device"), monkeypatch=monkeypatch)
    asked: list[int] = []
    monkeypatch.setattr(
        AndroidPhone,
        "attached_devices",
        lambda self: asked.append(1) or [a_device("the-one", "device")],
    )

    first = phone.serial_number()
    second = phone.serial_number()

    assert first == second == "the-one"
    assert len(asked) == 1, "the device list was fetched again inside the reuse window"


# --- finding the program itself ------------------------------------------


def the_environment_has(monkeypatch, **values) -> None:
    for name in ("PHONE_CONTROL_ADB",):
        monkeypatch.delenv(name, raising=False)
    for name, value in values.items():
        monkeypatch.setenv(name, value)


def test_a_named_program_is_used_when_it_is_runnable(monkeypatch, tmp_path):
    program = tmp_path / "adb"
    program.write_text("#!/bin/sh\n")
    program.chmod(program.stat().st_mode | stat.S_IXUSR)
    the_environment_has(monkeypatch, PHONE_CONTROL_ADB=str(program))

    assert find_adb_program() == str(program)


def test_a_named_program_that_is_not_runnable_is_refused_with_the_variable_named(
    monkeypatch, tmp_path
):
    """A typo in the override must not fall back quietly to some other adb."""
    missing = tmp_path / "not-here"
    the_environment_has(monkeypatch, PHONE_CONTROL_ADB=str(missing))

    with pytest.raises(AdbNotFound) as refused:
        find_adb_program()

    assert "PHONE_CONTROL_ADB" in str(refused.value)


def test_a_program_on_the_path_is_preferred(monkeypatch):
    the_environment_has(monkeypatch)
    monkeypatch.setattr(adb_module.shutil, "which", lambda name: "/somewhere/adb")

    assert find_adb_program() == "/somewhere/adb"


def test_a_bundled_copy_is_found_when_the_path_has_none(monkeypatch, tmp_path):
    program = tmp_path / "adb"
    program.write_text("#!/bin/sh\n")
    program.chmod(program.stat().st_mode | stat.S_IXUSR)
    the_environment_has(monkeypatch)
    monkeypatch.setattr(adb_module.shutil, "which", lambda name: None)
    monkeypatch.setattr(adb_module, "ADB_SEARCH_LOCATIONS", (str(program),))

    assert find_adb_program() == str(program)


def test_with_nothing_anywhere_the_error_says_how_to_install_it(monkeypatch):
    the_environment_has(monkeypatch)
    monkeypatch.setattr(adb_module.shutil, "which", lambda name: None)
    monkeypatch.setattr(adb_module, "ADB_SEARCH_LOCATIONS", ())

    with pytest.raises(AdbNotFound) as refused:
        find_adb_program()

    assert "platform-tools" in refused.value.fix
