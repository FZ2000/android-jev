"""Reading the foreground app from dumpsys, across the shapes Android emits.

The window-manager focus line has changed: on Android 14 it can read
`mCurrentFocus=null` before the real window, and `mResumedActivity` is absent
from `dumpsys activity activities` on some releases. These fixtures are the
shapes seen in those reports, so the parser is exercised against what devices
actually print rather than one convenient sample.
"""

from __future__ import annotations

from phone_control.device_state import foreground_package, read_focus

ANDROID_14_WINDOW_DUMP = """
  Window #0 Window{2f1b8de u0 NotificationShade}:
    mCurrentFocus=null
    mFocusedApp=null
  Window #3 Window{c838dbe u0 com.example.chat/com.example.chat.MainActivity}:
    mCurrentFocus=Window{4397ca7 u0 com.example.chat/com.example.chat.MainActivity}
    mFocusedApp=ActivityRecord{6a1c3b2 u0 com.example.chat/.MainActivity t57}
"""

CLASSIC_WINDOW_DUMP = """
  mCurrentFocus=Window{8d0c629 u0 com.example.myapplication/com.example.myapplication.MainActivity}
  mFocusedApp=AppWindowToken{6926e80 token=Token{a2cbf03 ActivityRecord{6a1c3b2 u0 com.example.myapplication/.MainActivity t5430}}}
"""

TOP_RESUMED_WINDOW_DUMP = """
  Window #25 Window{c838dbe u0 tv.danmaku.bili/tv.danmaku.bili.MainActivityV2}:
    mDisplayId=0 rootTaskId=57 mSession=Session{8e5857d 8821:u0a10178}
    topResumedActivity=ActivityRecord{d9b8281 u0 tv.danmaku.bili/tv.danmaku.bili.MainActivityV2} t57}
"""

TOP_RESUMED_ACTIVITY_DUMP = """
ACTIVITY MANAGER TOP-RESUMED (dumpsys activity top-resumed)
  ACTIVITY com.example.chat/.MainActivity 1c9fc79 pid=8821
    Local Activity 8e5857d  Token{9f2a1c3 ActivityRecord{6a1c3b2 u0 com.example.chat/.MainActivity t57}}
"""

NOTHING_USEFUL = """
  Window #0 Window{aaa u0 SomeWindow}:
    mCurrentFocus=null
"""


def test_a_null_focus_line_is_skipped_in_favour_of_the_real_one():
    assert read_focus(ANDROID_14_WINDOW_DUMP).package == "com.example.chat"


def test_the_classic_focus_line_is_read():
    assert read_focus(CLASSIC_WINDOW_DUMP).package == "com.example.myapplication"


def test_a_top_resumed_activity_line_is_read():
    assert read_focus(TOP_RESUMED_WINDOW_DUMP).package == "tv.danmaku.bili"


def test_nothing_is_reported_when_no_line_names_a_package():
    assert read_focus(NOTHING_USEFUL).package == ""


def test_the_window_dump_is_preferred_over_the_activity_manager():
    phone = _phone(
        {
            "dumpsys window": ANDROID_14_WINDOW_DUMP,
            "dumpsys activity top-resumed": TOP_RESUMED_ACTIVITY_DUMP,
        }
    )

    assert foreground_package(phone) == "com.example.chat"
    assert not phone.commands_matching("top-resumed")


def test_the_activity_manager_is_asked_when_the_window_dump_says_nothing():
    phone = _phone(
        {
            "dumpsys window": NOTHING_USEFUL,
            "dumpsys activity top-resumed": TOP_RESUMED_ACTIVITY_DUMP,
        }
    )

    assert foreground_package(phone) == "com.example.chat"


def test_no_app_is_reported_when_neither_source_names_one():
    phone = _phone(
        {
            "dumpsys window": NOTHING_USEFUL,
            "dumpsys activity top-resumed": "ACTIVITY MANAGER TOP-RESUMED",
        }
    )

    assert foreground_package(phone) == ""


# --- lock state ----------------------------------------------------------
#
# `mShowingLockscreen` has not existed since Android 8. DisplayPolicy prints
# `mShowingDream=... mDreamingLockscreen=...` on one line and `isKeyguardShowing=`
# on the next, so testing a whole line for "=true" reports a locked phone
# whenever a screensaver is up.

DREAMING_ONLY = "  mShowingDream=true mDreamingLockscreen=false\n"
KEYGUARD_BOTH = (
    "  mShowingDream=false mDreamingLockscreen=true isKeyguardShowing=true\n"
)


def test_a_screensaver_does_not_read_as_a_locked_phone():
    assert lock_state(_phone({}), DREAMING_ONLY) is False


def test_the_dreaming_lockscreen_field_is_read():
    assert lock_state(_phone({}), "  mDreamingLockscreen=true\n") is True


def test_the_keyguard_field_wins_over_the_dreaming_field():
    assert lock_state(_phone({}), KEYGUARD_BOTH) is True


def test_the_keyguard_service_is_asked_when_the_window_dump_says_nothing():
    phone = _phone({"dumpsys keyguard": "  showing=true\n"})

    assert lock_state(phone, "nothing useful here") is True


def test_the_keyguard_service_can_report_unlocked():
    phone = _phone({"dumpsys keyguard": "  showing=false\n"})

    assert lock_state(phone, "") is False


def test_lock_state_is_unknown_when_nothing_says():
    assert lock_state(_phone({}), "") is None


def lock_state(phone, window_dump: str) -> bool | None:
    from phone_control.device_state import _read_lock_state

    return _read_lock_state(phone, window_dump)


def _phone(answers: dict[str, str]):
    from conftest import FakePhone

    return FakePhone(answers)


# --- windows, and the keyboard that lives in one -------------------------
#
# The input-method service's own flags disagree with each other on Android 17
# (`mInputShown=false` beside `mIsInputViewShown=true`), so the window list is
# the trustworthy source for whether the keyboard is up.

WINDOW_DUMP_WITH_KEYBOARD = """
  Window #5 Window{792f052 u0 Taskbar}:
    mHasSurface=true mIsReadyForDisplay=true
    mAttrs={(0,0)(fillxfill) ty=NAVIGATION_BAR}
  Window #9 Window{aa11bb u0 InputMethod}:
    mHasSurface=true
    mAttrs={(0,1400)(fillx1000) ty=INPUT_METHOD}
  Window #12 Window{cc22dd u0 com.example.chat/com.example.chat.MainActivity}:
    mHasSurface=true
    mAttrs={(0,0)(fillxfill) ty=BASE_APPLICATION}
"""

WINDOW_DUMP_WITHOUT_KEYBOARD = """
  Window #9 Window{aa11bb u0 InputMethod}:
    mHasSurface=false
    mAttrs={(0,1400)(fillx1000) ty=INPUT_METHOD}
  Window #12 Window{cc22dd u0 com.example.chat/com.example.chat.MainActivity}:
    mHasSurface=true
    mAttrs={(0,0)(fillxfill) ty=BASE_APPLICATION}
"""

WINDOW_DUMP_WITH_AN_OVERLAY = """
  Window #3 Window{ee33ff u0 PermissionController}:
    mHasSurface=true
    mAttrs={(0,0)(fillxfill) ty=APPLICATION_OVERLAY}
  Window #12 Window{cc22dd u0 com.example.chat/com.example.chat.MainActivity}:
    mHasSurface=true
    mAttrs={(0,0)(fillxfill) ty=BASE_APPLICATION}
"""


def windows_of(dump: str):
    from phone_control.device_state import parse_windows

    return parse_windows(dump)


def test_windows_are_parsed_with_their_type_and_visibility():
    windows = windows_of(WINDOW_DUMP_WITH_KEYBOARD)

    assert [w.window_type for w in windows] == [
        "NAVIGATION_BAR",
        "INPUT_METHOD",
        "BASE_APPLICATION",
    ]
    assert all(w.has_surface for w in windows)


def test_an_input_method_window_with_a_surface_means_the_keyboard_is_up():
    keyboard = [
        w
        for w in windows_of(WINDOW_DUMP_WITH_KEYBOARD)
        if w.window_type == "INPUT_METHOD" and w.has_surface
    ]

    assert keyboard, "the keyboard window was not seen"


def test_an_input_method_window_without_a_surface_is_not_the_keyboard():
    keyboard = [
        w
        for w in windows_of(WINDOW_DUMP_WITHOUT_KEYBOARD)
        if w.window_type == "INPUT_METHOD" and w.has_surface
    ]

    assert not keyboard, "a hidden keyboard window was mistaken for a visible one"


def test_a_permanent_system_window_is_not_reported_as_an_overlay():
    """The taskbar and status bar are always present, so naming them is noise."""
    overlays = [w for w in windows_of(WINDOW_DUMP_WITH_KEYBOARD) if w.is_overlay]

    assert [w.window_type for w in overlays] == ["INPUT_METHOD"]


def test_an_application_overlay_is_reported_as_sitting_above_the_app():
    overlays = [w for w in windows_of(WINDOW_DUMP_WITH_AN_OVERLAY) if w.is_overlay]

    assert [w.window_type for w in overlays] == ["APPLICATION_OVERLAY"]


def test_a_dump_with_no_windows_yields_nothing_rather_than_guessing():
    assert windows_of("") == ()


# --- the two focus lines disagree, and the app line is the one that lies ----
#
# Observed on a real Pixel 8a with the lock screen up: mCurrentFocus named
# NotificationShade while mFocusedApp still named Settings behind it. Reporting
# the app tells a caller the user is somewhere they are not.

LOCK_SCREEN_OVER_AN_APP = """
  mCurrentFocus=Window{cab81a9 u0 NotificationShade}
  mFocusedApp=ActivityRecord{208861715 u0 com.android.settings/.homepage.SettingsHomepageActivity t15}
"""

APP_WITH_FOCUS = """
  mCurrentFocus=Window{5664954 u0 com.example.chat/com.example.chat.MainActivity}
  mFocusedApp=ActivityRecord{1 u0 com.example.chat/.MainActivity t2}
"""


def test_a_system_window_in_front_reports_no_app_rather_than_the_app_behind_it():
    focus = read_focus(LOCK_SCREEN_OVER_AN_APP)

    assert focus.package == "", (
        "the app behind the lock screen was reported as being in front"
    )
    assert focus.window == "NotificationShade"
    assert focus.is_an_app is False


def test_an_app_with_focus_reports_its_package():
    focus = read_focus(APP_WITH_FOCUS)

    assert focus.package == "com.example.chat"
    assert focus.is_an_app is True
