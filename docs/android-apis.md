# Android APIs for driving a phone

Research notes behind this project. The question they answer: **what can you
actually read from, and do to, an Android phone — and what does each route
cost?**

Everything here is verified against AOSP source, official documentation, or a
named project's reported experience. Where something is uncertain it says so.

---

## What has been validated, and on what

Everything above is from AOSP source, official documentation, or a named
project's reported experience. This section records what has since been checked
against a **real Pixel 8a running Android 17** over USB - which is past the top of
the range the API-level research covered (API 36), so some of it is the device
correcting the research.

**Confirmed working:** the accessibility tree parses (27 named controls on the
launcher, with correct labels, bounds and traits); `open_app` resolves a spoken
name to a package and launches it; `tap` by label; `press_key`; `take_screenshot`
(`screencap -p` downscaled to a 65 KB JPEG); `dumpsys activity top-resumed` exists
and its `mActivityComponent=pkg/activity` line parses; `isKeyguardShowing`,
`mDreamingLockscreen` and `mCurrentFocus` are all present on this build.

**Corrected by the device:**

- **`dump --windows` works**, and the first conclusion here that it was inert was
  wrong - see the trap noted in section 1. It re-roots the document at
  `<displays>` and reports every window with its type, which is how the keyboard
  is now detected for certain.
- **`cmd clipboard` really is unimplemented** - this build answers "No shell
  command implementation.", so the silent-false-success bug this project fixed was
  live on the test device, not theoretical.
- **`screencap` lists only `[-ahp] [-d display-id]`** here; there is no `-j` in the
  usage text.
- **Node attributes on Android 17** are exactly: `bounds checkable checked class
  clickable content-desc drawing-order enabled focusable focused hint index
  long-clickable package password resource-id rotation scrollable selected text`.
  There is a **`hint`** attribute - a field's placeholder - which this parser
  originally ignored, leaving an untouched search box unnameable. There is no
  `window-id`.
- **A dozing screen dumps a single bare node.** With `mWakefulness=Dozing` the
  whole tree is one `legacy_window_root` with nothing on it, so a reading comes
  back legitimately empty. `read_screen` now says which of asleep, locked or
  canvas-only it is rather than reporting a blank screen.
- **The Compose root cause is narrower than "Compose is thin".** The interop layer
  sets `IMPORTANT_FOR_ACCESSIBILITY_NO_HIDE_DESCENDANTS` on the
  `ViewFactoryHolder`/`AndroidViewsHandler` wrapper that hosts embedded Android
  Views, hiding that whole subtree from every accessibility client.
  `FLAG_INCLUDE_NOT_IMPORTANT_VIEWS` does not override it, and neither does
  `findAccessibilityNodeInfosByText`. This is the interop case specifically, not
  Compose's own semantics tree.

---

## The three tiers

| Tier | What runs | Install cost | Reach |
| --- | --- | --- | --- |
| **1. `adb shell`** | Nothing on the phone | None | Accessibility tree, input injection, screen capture, `dumpsys`, `cmd`, `content` |
| **2. `app_process`** | A jar pushed to `/data/local/tmp`, run as the shell user | Push a file | Everything the shell user may do, from Java - including a raw `UiAutomation`: node tree, event stream, input injection |
| **3. Accessibility service** | A real installed app | Install + enable an app | Full node tree, window list, event subscription, gestures, screenshots |

This project uses tier 1, because "works on any phone with nothing installed on
it" is the property worth protecting. Tiers 2 and 3 buy real capability and are
worth knowing about.

---

## 1. Reading the screen

### `uiautomator dump` - what this project uses

```
adb shell uiautomator dump /sdcard/window_dump.xml
adb exec-out cat /sdcard/window_dump.xml
```

Emits the accessibility tree as XML. Needs nothing installed, and that property is
verified rather than assumed: `uiautomator` is an **unconditional**
`PRODUCT_PACKAGES` entry in `build/target/product/base_system.mk` (identical in
Android 10, 14 and 15), so it ships on retail user builds, not only on userdebug
ones.

It needs no instrumentation and no APK either -
`UiAutomationShellWrapper.connect()` constructs
`new UiAutomation(looper, new UiAutomationConnection())` directly. That is *why* it
works on a bare device, and it is the whole architectural difference from
AndroidX's `UiDevice`, which requires an `Instrumentation` and therefore an
installed app.

Two things to know about the node tree:

- The `<hierarchy>` element carries **only** `rotation`. The screen bounds and the
  package live on its **first child**. Reading them off `<hierarchy>` silently
  yields zeros and an empty package. (This was a real bug here.)
- `bounds` is `getVisibleBoundsInScreen`, **clipped to what is actually shown**,
  not `getBoundsInScreen`. For tapping that is the better of the two: the centre
  of a half-scrolled-off row is the centre of its visible part.
- An invisible container **drops its whole subtree** and recursion stops there.
  This is the same root cause as Appium's documented Jetpack Compose
  `ViewFactoryHolder` problem
  ([Google issue 354958193](https://issuetracker.google.com/issues/354958193)), so
  a Compose screen can yield a tree far thinner than what is on it.

#### The subcommands, and a trap in the arguments

They are exactly `help`, `runtest`, `dump` and `events`. **`events` prints the
accessibility event stream, with timestamps, until killed** - a zero-install way
to test whether waiting on an event beats polling, without writing any app.

`dump` accepts exactly three arguments:

| Argument | Effect |
| --- | --- |
| `--compressed` | Clears `FLAG_INCLUDE_NOT_IMPORTANT_VIEWS`. The **default is the opposite**, so a plain dump includes views not marked important for accessibility. |
| `--windows` | **API 30+, and it works.** It re-roots the document: the root becomes `<displays>`, holding one `<window>` per window, each with `index title bounds active focused accessibility-focused id layer type` and its own nested `<hierarchy>`. This is the answer to "is the keyboard up / what is on top". A device too old to know the flag ignores it and returns the plain shape, so one parser handles both. |
| `<path>` | Where to write. Defaults to `/sdcard/window_dump.xml`. |

`--verbose` is **advertised in the help text but inert** - it is already the
default - and `--windows` is **implemented but undocumented**, which is worse:
the flag is absent from `uiautomator help` even on Android 17, so the only way to
find it is to read AOSP. An unknown `-`-prefixed argument is **silently
swallowed**, so a misspelled option produces an ordinary dump and no warning
whatsoever.

**A trap worth recording, because it cost real time here.** `--windows` was
briefly believed to be inert on Android 17. It is not. Comparing the two dumps by
counting `<node>` elements made them look identical, because the node payload is
largely the same and only the *envelope* changes - `<hierarchy>` at the root
becomes `<displays>` with `<window>` children. The window `id` attribute also
increments on every invocation, so consecutive `--windows` dumps are never
byte-identical and a byte-diff reads as noise. **Inspect the root element, and
compare structurally rather than by diffing bytes.**

#### Two failure modes to respect

**A dump that times out is not a dump that failed loudly.** `dump` calls
`waitForIdle(1000, 10000)` and prints `ERROR: could not get idle state.` when that
expires. A failed dump **does not remove the output file and does not necessarily
fail the command**, so a fixed output path silently serves the *previous* screen.
That is the bug this project fixed by writing each dump to a path unique to the
call.

**The XML is not public API.** It is emitted by a `/** @hide */` dumper class with
no published contract, and Appium does not use it at all - it maintains its own
traversal - which is itself evidence that the schema is not treated as stable.
Parsing has to be defensive, and this is the strongest argument for the
real-capture-per-API-level discipline in section 7.

### Focus: `mCurrentFocus` and `mFocusedApp` disagree, and the app line lies

`mCurrentFocus` is the trustworthy one. `mFocusedApp` names the activity the
activity manager last considered focused, which is not the same as what the user
is looking at. Observed live on the test device with the lock screen up:

```
mCurrentFocus=Window{cab81a9 u0 NotificationShade}      <- the truth
mFocusedApp=ActivityRecord{... com.android.settings/...} <- an app behind it
```

This server's own `status` reported `com.android.settings` as the foreground app
while the user was looking at the lock screen, because the focused window was a
system window with no `pkg/activity` to match and the parser fell through to the
app line. **When the focused window is a system window, the honest answer is that
no app is in front** - not the app behind it. Never trust a single "foreground
app" field; report the window name as well as the package.

Two related traps, both hit firsthand:

- **The IME service's own flags contradict each other.** `dumpsys input_method`
  prints `mIsInputViewShown=true` **while the keyboard is definitively hidden**,
  because that line belongs to the IME app's `InputMethodService` state rather
  than to window state. The reliable signals are `mIsImeShowing`, `mImeHeight`,
  the `type=ime` insets source, or `mHasSurface` on the `ty=INPUT_METHOD` window.
- **A keyboard window exists even when the keyboard is hidden.** The
  `ty=INPUT_METHOD` window stays in the list with `mHasSurface=false`. Presence is
  not visibility, and treating it as such reports a keyboard that is not there.

### `dumpsys activity top` - structure the accessibility tree throws away

Every row of the `View Hierarchy:` block is `View.toString()`, indented two spaces
per level of depth. The format is positional and fully decodable:

```
DecorView{hash I.E...... R.....ID 0,0-1080,2400 aid=1073741824}[SettingsHomepageActivity]
  LinearLayout{99230b4 V.E...... .......D 0,0-1080,2400}
    ViewStub{ab1ad2b G.E...... ......I. 0,0-0,0 #10201ff android:id/action_mode_bar_stub}
```

The flag groups decode visibility/focusable/enabled/draw/scrollbars/clickable/
long-clickable/context-clickable, then root-namespace/focused/selected/pressed/
hovered/activated/invalidated/dirty; then `left,top-right,bottom`, then `#<hex id>`
with its resource name, then an optional autofill id.

**Why it is worth knowing: it recurses unconditionally.** Unlike the accessibility
dumper - which skips `!child.isVisibleToUser()` *and that child's entire subtree* -
this walks every view. On a real Pixel 8a the Settings dump held **138 rows, of
which 42 had `0,0-0,0` bounds and 8 were `GONE`**, structure the accessibility tree
prunes completely. It recovers the existence, class, view id and geometry of hidden
subtrees, which is enough to know a region is populated and to aim a tap.

**And its limit is severe: it contains no text.** 17 of those rows were `TextView`s
and not one printed a value, because `View.toString()` never appends one. It can
tell you a label is there, never what it says. A claim elsewhere that this command
yields views "with text content" is contradicted by both the AOSP printer and this
device.

Two more boundaries: it covers only the **top activity** that still has a
`ViewRootImpl`, so a dialog window or the IME is absent; and there is **no
machine-readable variant** - `dumpsys activity --proto top` emits a megabyte of
protobuf containing zero view-hierarchy text, so it has to be line-parsed.

The practical reading: the accessibility tree is where the words are, this is where
the hidden structure is, and neither replaces a screenshot for a canvas.

### `cmd clipboard` - not implemented everywhere, and lies about it

`cmd clipboard set` / `get` / `clear` is the intended shell interface. On some
builds, including API 35 emulator images, it **exits 0 while printing "No shell
command implementation" on stderr**. Two independent projects hit this:

- [mcp-adb](https://git.supported.systems/MCP/mcp-adb/commit/e0c05dc72a12278f8ffcd56bc084750efc5c137e) ships a fix titled *"fixes clipboard_set false-positive on devices where cmd clipboard returns exit 0 but has no implementation"*.
- [auto-mobile](https://kaeawc.github.io/auto-mobile/design-docs/plat/android/clipboard/) reports `cmd clipboard` returning that message on API 35, and `dumpsys clipboard` returning nothing.

Consequences worth internalising: **the exit code is not evidence**, and a
clipboard that was never set is worse than one that failed loudly, because the
paste that follows silently uses stale content. This project treats every
"unsupported" phrasing as failure and reads the value back to confirm.

Reading the clipboard additionally requires focus on Android 10+ -
[AOSP `ClipboardService`](https://android.googlesource.com/platform/frameworks/base.git/+/master/services/core/java/com/android/server/clipboard/ClipboardService.java)
gates `OP_READ_CLIPBOARD` on `mWm.isUidFocused(uid)`, while `OP_WRITE_CLIPBOARD`
is allowed unfocused. The shell user is exempted for testing.

### Screen capture

| Route | Notes |
| --- | --- |
| `adb exec-out screencap -p` | No install, works on any device. ~300-700 ms for a 1080x2400 PNG. `exec-out` matters: `adb shell` would mangle the binary stream. |
| `AccessibilityService.takeScreenshot()` | **API 30.** Requires `android:canTakeScreenshot` in the **service XML** - capabilities are XML-only and `setServiceInfo` cannot add them at runtime. Returns a `HardwareBuffer` + `ColorSpace` + timestamp by callback, not a `Bitmap`. |
| `AccessibilityService.takeScreenshotOfWindow()` | API 34. Per-window. |
| `MediaProjection` | Needs a user consent dialog per session; Android 14 tightened reuse. Awkward for automation. |

Two `screencap` options worth knowing: `-a` captures **all active displays**
(with an integer postfix on the filename), and `-j` writes JPEG - it is present in
`getopt` but absent from the usage text.

The accessibility screenshot path is throttled by
`ACCESSIBILITY_TAKE_SCREENSHOT_REQUEST_INTERVAL_TIMES_MS`, which is `@hide` and
whose value **moved from 1000 ms on Android 11 to 333 ms on Android 14/15** -
evidence that the number is not a contract.

## 2. Structured device state via `dumpsys`

`dumpsys` is the richest no-install source of structured state, and it is
**undocumented internal output that changes between releases**. Treat every
parser as a guess until a real capture from that API level proves otherwise.

`adb shell dumpsys -l` lists every service. `adb shell dumpsys <service> -h`
shows that service's own options. `-c` asks certain services for a
"machine-friendly format" — undocumented per service, but worth probing.

| Command | Gives |
| --- | --- |
| `dumpsys window` | Focus, window list, rotation, insets, IME visibility |
| `dumpsys activity top-resumed` | The top resumed activity, purpose-built |
| `dumpsys activity activities` | Back stack. **Format drifts badly across releases** |
| `dumpsys notification --noredact` | Full notification content |
| `dumpsys media_session` | What is playing |
| `dumpsys power` | `mWakefulness=` — screen on/off |
| `dumpsys input_method` | Whether the soft keyboard is shown |
| `dumpsys battery` | Level, charging |
| `dumpsys connectivity`, `dumpsys telephony.registry` | Network state |
| `dumpsys clipboard` | Unreliable; often empty |
| `wm size`, `wm density` | Screen geometry, honouring overrides |
| `settings list system\|secure\|global` | Settings as key/value |
| `content query --uri content://…` | Structured provider queries |

### Three parsing traps, all verified

**`mCurrentFocus` can be `null`.** On Android 14 the line appears more than once
and the first can read `mCurrentFocus=null`
([uiautomator2#952](https://github.com/openatx/uiautomator2/issues/952)). A
parser must accept a line only when it resolves to a package.

**`mResumedActivity` is absent from some releases.** The same report shows
`mLastPausedActivity` where `mResumedActivity` used to be, and
`Window #N Window{…}` where `mCurrentFocus=Window{…}` used to be.

**`dumpsys activity` can time out.** On Android 14 with many backgrounded apps,
`dumpsys activity top` returns `*** SERVICE 'activity' DUMP TIMEOUT (10000ms)
EXPIRED ***` and then reports the *wrong* activity. A dedicated, cheap command
beats dumping everything: `dumpsys activity top-resumed` was
[added to AOSP](https://gitlab.e.foundation/e/os/android_frameworks_base/-/commit/7a1c9ba5c74a92313e677efee3d9d9ef9fa45fd4) for exactly this reason.

**The general lesson** comes from
[auto-mobile#4329](https://github.com/kaeawc/auto-mobile/issues/4329), an audit of
their own parser: it passed against fixtures *hand-authored from AOSP source*
that were never checked against a real device, while the real format had moved
`TaskRecord{…}` → `Task{…}` and `A=pkg` → `A=uid:pkg`. Their conclusion — **only a
real capture per API level tells you the parser is right** — applies directly
here: this project's `dumpsys` parsing is unverified until it runs on a real
device.

---

## 3. `cmd` services

`cmd <service> <command>` invokes a system service's shell interface. Some are
stable enough to rely on; all are internal.

**`cmd statusbar`** is the one genuinely well-behaved family. Verified against
[AOSP `StatusBarShellCommand`](https://android.googlesource.com/platform/frameworks/base/+/master/services/core/java/com/android/server/statusbar/StatusBarShellCommand.java),
it implements `expand-notifications`, `expand-settings`, `collapse`, `add-tile`,
`remove-tile`, `set-tiles`, `click-tile`, `check-support`, `get-status-icons`,
`disable-for-setup`, `send-disable-flag`, `tracing` and `run-gc`. Anything
unrecognised is passed through to SystemUI. `StatusBarManagerService` explicitly
lets the shell uid through:

```java
private void enforceStatusBarOrShell() {
    if (Binder.getCallingUid() == Process.SHELL_UID) { return; }
    enforceStatusBar();
}
```

`click-tile` and `set-tiles` are worth noting: they make Quick Settings
tiles — Wi-Fi, Bluetooth, torch, rotation lock, DND — drivable without a single
coordinate.

Other families: `cmd package` (`list packages`, `resolve-activity`),
`cmd notification`, `cmd uimode night`, `cmd wifi`, `cmd media_session`.

---

## 4. Input injection

`input <command>` supports `text`, `keyevent`, `tap`, `swipe`, `press`,
`draganddrop`, `roll`, `motionevent` and `keycombination` — availability varies
by release, so probe `input -h` on the device.

- **`input text` is ASCII only** and silently drops everything else. In it, `%s`
  means space (the legacy encoding this project uses).
- Non-ASCII needs another route. Two known ones:
  - **`yadb`** — a small binary pushed to the device and run via `app_process`.
    [midscene](https://github.com/malinkang/midscene) routes non-ASCII, emoji and
    `%`-containing strings to it, and plain ASCII to `input text`. This is the
    most promising fix for this project's biggest documented limitation.
  - **ADBKeyboard** — an IME installed on the phone that accepts text over
    broadcasts.
- `input text` arguments must be quoted for the *device* shell, since `adb shell`
  forwards its arguments to it. Single-quote wrapping with `'\''` escaping is
  what this project does.

---

## 5. Elevated access without root

### The shell user is more powerful than it looks

A process started by `adb shell` runs as uid 2000. The authoritative list of what
that means is AOSP's
[`packages/Shell/AndroidManifest.xml`](https://android.googlesource.com/platform/frameworks/base/+/refs/heads/main/packages/Shell/AndroidManifest.xml)
— the same file Shizuku's README cites as the definition of "what ADB can do".
Of the permissions that matter here, shell holds **all** of them:

| Permission | Buys |
| --- | --- |
| `DUMP` | Every `dumpsys` service |
| `READ_LOGS` | `logcat` |
| `WRITE_SECURE_SETTINGS` | `settings put secure …` |
| `INJECT_EVENTS` | Input injection |
| `GRANT_RUNTIME_PERMISSIONS` | `pm grant` |
| `CHANGE_CONFIGURATION` | Locale, density, orientation |
| `PACKAGE_USAGE_STATS` | Usage statistics |
| `RETRIEVE_WINDOW_CONTENT` | **`UiAutomation` — see below** |

Also present: `MANAGE_ACCESSIBILITY`, `INSTALL_PACKAGES`, `CLEAR_APP_USER_DATA`,
`FORCE_STOP_PACKAGES`, `READ_FRAME_BUFFER`, `REBOOT`, `MANAGE_APP_OPS_MODES`,
`MANAGE_ACTIVITY_TASKS`.

What it cannot do: read `/data/user/0/<pkg>` (the largest gap versus root), hold a
signature or privileged permission it was not granted, or act as an Android
application — a shell process has no package identity, so `getContentResolver`
and `registerReceiver` do not work. The list is also per-AOSP-release, and OEMs
restrict adb further (MIUI needs "USB debugging (Security options)"; ColorOS needs
"Permission monitoring" off).

### The finding that matters: a jar can drive `UiAutomation`

**A jar launched via `app_process` as uid 2000 can construct a raw
`android.app.UiAutomation` and get the full accessibility tree, the accessibility
event stream, and real input injection — with no APK, no instrumentation, no
accessibility service, and no user Settings toggle.**

AOSP's own `uiautomator dump` is implemented exactly this way:

```java
UiAutomationShellWrapper w = new UiAutomationShellWrapper();
w.connect();                                    // Looper.prepareMainLooper() first
UiAutomation uiAutomation = w.getUiAutomation();
uiAutomation.waitForIdle(1000, 10000);
AccessibilityNodeInfo info = uiAutomation.getRootInActiveWindow();
```

The whole authorization story is one annotation in `IAccessibilityManager.aidl`:

```aidl
@EnforcePermission("RETRIEVE_WINDOW_CONTENT")
void registerUiTestAutomationService(IBinder owner, IAccessibilityServiceClient client,
    in AccessibilityServiceInfo info, int userId, int flags);
```

`com.android.shell` declares `RETRIEVE_WINDOW_CONTENT`. That is it.

What this would buy over parsing `uiautomator dump` XML: the event stream
(`executeAndWaitForEvent`), `injectInputEvent` with no shell round-trip per tap,
`performGlobalAction`, all windows on all displays, and per-window IDs.

Two limits worth knowing before designing around it:

- **Only one `UiAutomation` client at a time.** `UiAutomationManager` throws
  `IllegalStateException("UiAutomationService … already registered!")`. It has to
  be one long-lived connection, not one per request — which is exactly what a
  `uiautomator dump` per call is not.
- **It suppresses other accessibility services by default.** Pass
  `FLAG_DONT_SUPPRESS_ACCESSIBILITY_SERVICES` to `connect(int)` if the user
  relies on TalkBack or Switch Access.

And the clean boundary: **an `AccessibilityService` cannot be registered from a
jar.** It must be a manifest-declared `<service>` in an installed APK, guarded by
`BIND_ACCESSIBILITY_SERVICE` and enabled by the user. That is what tier 3 is for.

### What a real accessibility service adds, and what it costs

Two concrete technical wins, both verified against AOSP:

**`ACTION_SET_TEXT` is not constrained by the keymap.** `adb shell input text` goes
through `KeyCharacterMap.load(VIRTUAL_KEYBOARD).getEvents(chars)`, which is
documented to return **null** for any character the keymap cannot generate - and
AOSP's own comment on it says *"For robust text entry, do not use this function."*
`ACTION_SET_TEXT` takes a `CharSequence` directly with no keymap in the way. That
is the proper fix for this project's non-ASCII limitation; the clipboard-and-paste
route is a workaround around the same wall.

**`ACTION_CLICK` is unreliable by the framework's own admission.** The
`dispatchGesture` javadoc concedes that "many apps do not appropriately support
ACTION_CLICK" and ships a tap-fallback sample. An accessibility-service tier
therefore needs a two-tier strategy from the start - try `ACTION_CLICK`, fall back
to a coordinate tap - rather than treating the action as a reliable primitive.

**The dominant cost is policy, not technology.** Google Play prohibits
accessibility-API use that enables an app to "autonomously initiate, plan, and
execute actions or decisions", while explicitly permitting "deterministic,
rule-based automation, where behavior follows a static, human-defined script". An
agent that chooses its own next action is the prohibited case; a recorded script
is not. Sideloading avoids Play but runs into Android 13+ restricted settings,
gated through `OP_ACCESS_RESTRICTED_SETTINGS` (absent in Android 12, present in
Android 13) via `EnhancedConfirmationManager`.

So the accessibility-service tier suits a personal, sideloaded, script-driven
setup, and not anything shipped to Play where a model picks the actions.

### A custom dumper with no install

`/system/bin/uiautomator` is not a binary. It is a small shell script ending in
`exec app_process ${base}/bin com.android.commands.uiautomator.Launcher`, and the
shell user (uid 2000) can run a class from an arbitrary jar the same way -
verified on the device by copying `uiautomator.jar` to `/data/local/tmp` and
launching it with an explicit `CLASSPATH`:

```
adb shell 'CLASSPATH=/data/local/tmp/probe.jar app_process /system/bin com.example.Dumper'
```

A purpose-built dumper could therefore ship as a dex in `/data/local/tmp` with **no
APK and no package install**. It would reach the `AccessibilityNodeInfo` fields the
XML drops, report windows with their focus flags, keep a traversal that does not
prune invisible subtrees, and emit JSON rather than XML.

**[UNVERIFIED]** end to end: producing the dex needs an Android SDK, an
`android.jar` and `d8`, none of which were present where this was researched. The
mechanism is confirmed; the build step is not.

### Running a jar with `app_process`

The exact shape, from [scrcpy](https://github.com/Genymobile/scrcpy):

```
adb shell CLASSPATH=/data/local/tmp/scrcpy-server.jar app_process / com.example.Server <params>
```

- `/data/local/tmp` is deliberate: readable and writable by `shell`, not
  world-writable, so another app cannot swap the jar between push and run.
- The `/` after `app_process` is a required but unused positional placeholder
  ("skip unused parent dir argument"). `app_process` recognises only `--zygote`,
  `--start-system-server`, `--application` and `--nice-name=`, and
  **`--nice-name` sets only the process name — it grants no privilege.** There is
  no `--user` option; that belongs to the `cmd` family.
- Use `app_process`, not `app_process64`: on 64-bit devices it is a symlink to
  the 64-bit binary, and hardcoding 64 breaks 32-bit-only devices.

**Android 14's "writable dex" rule does not bite here.** ART refuses to load a
dex the caller can write — except for uid 0, 1000 and 2000, with a comment saying
so: *"directly calling dalvikvm/app_process in ADB shell to run JARs with CLI is
allowed."* That is why scrcpy keeps working unchanged on modern Android.

The process inherits exactly the uid-2000 permission set above — no more. Its
lifecycle is the adb session: it dies with the cable or a reboot, nothing is
installed, and `adb shell rm` fully undoes it.

### Shizuku

Starts the same kind of uid-2000 process, then lends its binder to ordinary apps.
Worth it when an *installed phone app* needs adb-level access. For a
USB-tethered tool it is a poor trade: an install, a per-reboot recovery step
(**it does not survive reboot in adb mode**), and a standing privileged IPC
endpoint — all to reach a permission set your own jar already has. Note also that
authorising an app hands it the whole set; there is no partial grant.

### Device owner: easy to enter, hard to leave

`dpm set-device-owner` unlocks silent runtime-permission grants and kiosk modes.
Google's documentation says a factory reset is required, and AOSP is more
permissive than that for the adb path:

```java
boolean isAdb = isShellUid(caller) || isRootUid(caller);
if (isAdb) {
    if (hasUserSetupCompleted(ensureSetUpUser)) {
        if (nonTestNonPrecreatedUsersExist()) return STATUS_NONSYSTEM_USER_EXISTS;
        if (hasIncompatibleAccountsOrNonAdb) return STATUS_ACCOUNTS_NOT_EMPTY;
    }
    return STATUS_OK;              // no factory reset needed
}
```

So on a device with **no accounts and no extra users** it succeeds without a
reset. Leaving is the trap: `dpm remove-active-admin` throws
`SecurityException("Attempt to remove non-test admin …")` unless the admin
declared `android:testOnly="true"`, and `clearDeviceOwner` is callable only by
the device owner itself. **For a normal user it is effectively irreversible.** A
device owner can also silently grant any runtime permission to any app, which is
why Google split it away from legacy Device Admin. Do not do this on a personal
phone; use a `testOnly` DPC if you do it at all.

### Granting permissions

`pm grant` reaches **runtime (dangerous) permissions only**, for permissions the
app already declared. Signature and privileged permissions are checked against
the requesting app's signing certificate at grant time, so no adb-reachable path
can hand them out — which is precisely why tier 3 needs a real app rather than a
clever command. `pm install -g` grants all declared runtime permissions at
install.

For *special app access* — draw over other apps, usage access, install unknown
apps, battery exemption — `appops set` is the route, and shell holds
`MANAGE_APP_OPS_MODES` so it works from adb. It changes the mode of an operation
the app may already request; it is not a route to signature permissions.

---

## 6. App-side state is not a shortcut

It is tempting to read state from an agent app instead of from `dumpsys`. Two
API-level changes argue against relying on it:

- **`UsageStatsManager` foreground detection broke on Android 14** — it stops
  returning the most recent events, so the reported foreground app lags or is
  simply the launcher ([stackoverflow](https://stackoverflow.com/questions/77410929/getting-foreground-app-not-working-on-android-14), [Google issue 309104474](https://issuetracker.google.com/issues/309104474)).
- **Background clipboard reads were restricted in Android 10**, so an app cannot
  quietly read the clipboard it did not put there.

---

## 7. Recommendations for this project

Ordered by value against cost.

1. **Capture real `dumpsys` output per API level and pin the parsers to it.** First
   because it is cheap and it de-risks everything else. The auto-mobile audit is
   the cautionary tale: green tests against fixtures invented from AOSP source
   proved nothing, while the real format had moved. Little else here can be
   trusted until the parsing is anchored to captures from a real device.
2. **Add a long-lived `app_process` jar driving `UiAutomation`.** The largest
   capability jump available without an install or root: the accessibility event
   stream, real `injectInputEvent` with no shell round-trip per tap, all windows
   on all displays, and no `uiautomator dump` per read. The constraints are one
   client at a time (so one connection, held open) and the suppression flag. Keep
   the current `uiautomator dump` path as the fallback when the jar is not pushed.
3. ~~Find a way to see dialogs and the keyboard.~~ **Done: `read_screen` asks
   for `--windows`**, so every window is reported with its type and whether it
   has contents, and the keyboard is recognised from `TYPE_INPUT_METHOD` rather
   than inferred. Window `id` values are ignored, since they change every call.
4. **Try `uiautomator events` for waiting.** It is a zero-install accessibility
   event stream, which would let `wait_for` stop polling every 500 ms - and it can
   be evaluated without writing any app.
5. **Fix non-ASCII typing with `yadb`.** It is the largest functional gap and the
   known solution is a pushed binary, not an app.
6. **Enrich the state with system context** - rotation, window insets, IME
   visibility, wakefulness - from `dumpsys window` and `dumpsys input_method`. It
   answers questions the node tree cannot ("is the keyboard covering this
   field?"). Adopt auto-mobile's habit of **returning partial data on partial
   failure** rather than failing the whole observation.
7. **Adopt `cmd statusbar set-tiles` / `click-tile`** for Quick Settings, which
   removes a whole class of coordinate tapping.
8. **Consider the accessibility-service tier only if** the privilege must outlive
   the USB session, or a real `BIND_ACCESSIBILITY_SERVICE` is genuinely needed. A
   tiny APK enabled once with `settings put secure enabled_accessibility_services`
   beats Shizuku: no per-reboot restart, no standing privileged IPC endpoint.
9. **Do not use Shizuku** for a tethered tool - see section 5. **Do not set a
   device owner** on a personal device, and use a `testOnly` DPC if at all.

## A settings toggle's state is not in the accessibility tree

Measured on a Pixel 8a running Android 17, on the Bluetooth settings screen: no
node is `checkable="true"`, no node's class mentions `Switch`, `SwitchCompat`,
`ToggleButton` or `CheckBox`, and the only thing that changes with the switch is
the summary line beside it.

```
$ adb shell am start -a android.settings.BLUETOOTH_SETTINGS
$ adb shell uiautomator dump /sdcard/t.xml && adb shell cat /sdcard/t.xml
# nodes whose class mentions a switch: none
# nodes with checkable="true":        none
# the row:  list_item "Use Bluetooth"
# and beside it, when Bluetooth is off:
#   text="Turn on Bluetooth to connect to other devices."
```

This matters more than it looks. A loop that reads a screen and asks "is the goal
done" cannot answer it for `turn off Bluetooth`, because the one fact that settles
it - which way the switch is - is not published. The row can be tapped and the
setting does change; nothing in the tree says afterwards that it did.

So a state cannot state it, and a decision model cannot conclude from the screen
that the goal is met. Two things follow, and both are in the code now:

* `describe_a_control` still reports "currently on" or "currently off" for a
  checkable control, because some screens do publish one.
* A scenario whose goal is only provable from a setting the tree does not expose
  needs the hand-off: the reader reads the screen and says whether the goal is met,
  which is what it is for. `bluetooth-off` says so and is skipped when no reader is
  configured, rather than being reported as a failure of the loop.

The general lesson is the one this project keeps relearning: a simulated switch that
says it is checkable is not evidence that any switch does.

## Some screens can never be dumped at all

Measured on a Pixel 8a running Android 17, on the *About phone* page
(`android.settings.DEVICE_INFO_SETTINGS`):

```
$ adb shell am start -a android.settings.DEVICE_INFO_SETTINGS
$ adb shell uiautomator dump /sdcard/a.xml
ERROR: could not get idle state.

# nineteen seconds of retries, plain and --windows alike: the same error every time
$ adb shell uiautomator dump --windows /sdcard/b.xml
ERROR: could not get idle state.

# while these both work perfectly on the same screen
$ adb shell dumpsys window | grep mCurrentFocus
mCurrentFocus=Window{... com.android.settings/com.android.settings.Settings$MyDeviceInfoActivity}
$ adb shell screencap -p /sdcard/z.png     # fine
```

`uiautomator dump` waits for the interface to go *idle* before capturing, and some
screens never do — a live figure, an animating icon, a list that keeps
recomposing. When that happens the tool does not return a stale screen or a partial
one: it returns nothing at all, and goes on returning nothing however long you wait.

This is worse than the Compose gap below, which at least yields a thin tree. Here
there is no tree. `read_screen` cannot serve these screens at all, so:

* **A goal that requires reading one of them cannot be concluded from the tree.**
  The window in front still names the screen, and a screenshot still shows it, so a
  check that only needs *which page* is showing should ask
  `read_focused_window` rather than the screen text — which is what the scenarios
  for the Settings pages now do.
* **A loop that requires the tree will stall there**, honestly and after retrying.
  `read_screen` says exactly what the phone said rather than reporting an empty
  screen, because an empty screen is a claim about the phone and this is a failure
  to look.
* **A wedged helper looks identical from the outside.** The classic cause of this
  same message is a stale `uiautomator` process, and killing it is the remedy for
  that; here it is not wedged at all. `read_screen` tries the kill once, after a dump
  has already failed, and then believes the answer.

### The window names the page, except when it does not

The route that works for a screen the tree cannot describe is `dumpsys window`,
which needs no idle state. It is worth knowing its one limit, measured on the same
device:

```
# the page started directly: the activity names it
$ adb shell am start -a android.settings.DEVICE_INFO_SETTINGS
mCurrentFocus=...com.android.settings/com.android.settings.Settings$MyDeviceInfoActivity

# the same page reached by tapping the row: it does not
mCurrentFocus=...com.android.settings/com.android.settings.Settings$SubSettings
```

A Settings page opened by navigating is hosted in a generic `SubSettings`
container, so the window says *that something was opened* and not *what*. Combined
with a tree that will not dump, the page is then unreachable by both routes at once:
nothing on it can be read, and its name cannot be read either.

That is the honest reason five of the usage scenarios are marked `needs_a_reader`
rather than being counted as failures. The phone reaches the page; nothing the
server can look at says the goal is met; a reader that looks at the screen is what
settles it. Eleven of the forty-seven scenarios are in that position, which is worth
saying plainly: **the reader is load-bearing on this device, not a nicety.** The
right long-term answer is a second perception route rather than a second model — the
screenshot is already available and the tree is not, and an agent with eyes could
read what the tree refuses to describe.

## An effect that leaves no trace in the tree

Measured on the same device: the camera's shutter, tapped from the tree, takes **6.2
seconds** to write a photo to `/sdcard/DCIM/Camera` - and changes nothing whatsoever in
the accessibility tree while it does.

```
# tap the shutter control that read_screen reports, then watch the tree and the folder
shutter control: "Take Night Sight photo"
  a photo appeared after 6.2s
# the tree, before and after: identical
```

So there are two separate problems, and only one of them is about timing.

**Timing.** The loop settles for 0.8s and then asks whether anything changed. A phone
still working looks exactly like a phone that did nothing, so it taps the shutter
again - which cancels the exposure it just started. Three taps, no photo. That much
is fixable in principle by looking again before calling an action a no-op.

**No trace at all.** It is not fixable, and this is the part worth recording: after
the six seconds the photo exists and the tree is *still identical*. A camera preview
is a surface, so there is nothing in the tree to change. Looking again, however
patiently, is looking in the wrong place.

The loop therefore cannot tell a shutter that worked from one that did not, and no
choice of settle or retry changes that. This is the third distinct way the tree fails
to be enough:

    a page that never goes idle, so it cannot be dumped at all
    a page dumped and hosted in a generic container, so it cannot be named either
    an effect that leaves no trace in the tree at all

A read of the screen is what the loop's design rests on, and these are its edges. The
screenshot is the one route that covers all three, which is why `take_screenshot` is
in the tool surface rather than being an afterthought for agents.

### What was tried, and what it cost

Looking again before believing a no-op was written, measured, and reverted. It did not
fix the camera - see above - and it fired on every apparent no-op, which took the fast
suite from 55 seconds to 271. A change that fixes nothing and costs five times as much
to run is not a change worth keeping, and the numbers are the reason it is not here.

### A playing video: unreadable, and the window lies

Two measurements on the same device, both of which cost a wrong probe.

```
# a video playing
$ adb shell am start -a android.intent.action.VIEW -d https://www.youtube.com/watch?v=...
mCurrentFocus=...youtube.app.watchwhile.InternalMainActivity
$ adb shell uiautomator dump /sdcard/v.xml
ERROR: could not get idle state.        # a video never goes idle

# YouTube's home screen, no video
mCurrentFocus=...youtube.app.watchwhile.InternalMainActivity    # the same window
```

So neither route distinguishes them. The window is the same activity for the home
screen and for playback - `watchwhile` is in the package path, not a marker of a video
- and the screen while a video plays cannot be dumped at all.

A probe built on the window therefore matches *both*, which is the same mistake as a
probe built on a common word: it cannot fail. Measured on this project's own scenario,
where tapping a video row was followed by the run reporting `Shell$HomeActivity` - a
third activity again, belonging to search results - and a first fix that matched
`WatchWhile` would have called YouTube's home screen a playing video.

What would settle it is something that *looks*: the screenshot shows a video playing
plainly. `take_screenshot` is in the tool surface for this reason, and a reader that
reads text cannot do it either.

## What a page that will not dump costs to look at

Measured on a Pixel 8a, because this is what makes those scenarios expensive rather
than hard, and the numbers were not where they were assumed to be:

| | an ordinary page | a page that will not dump |
| --- | --- | --- |
| `uiautomator dump` | 2.59s | **11.39s**, and it fails |
| `uiautomator dump --windows` | - | 12.04s, and it fails |
| `screencap -p`, full width | 0.84s | 0.67s |
| a picture of it, read | 1.15s | **0.58s** |
| one `read_screen` in total | - | **~34s** |

The picture is twenty times cheaper than the dump that fails, which is the second
reason this route earns its place - the first being that it is the only one that
works there at all.

**Where the 34 seconds go**, for a page that will never go idle:

1. `uiautomator dump` waits for idle and gives up - 11.4s
2. `read_screen` then asks `_the_helper_is_wedged`, which is *another dump* - 11.4s
3. and if it decides the helper is wedged, clears it and dumps once more - 11.4s

The second step is deliberate and its own docstring says why: a helper that has
stopped answering and a page that will not go idle "look identical from the outside",
and killing the helper is the remedy for the first. It is also the step that makes
looking expensive, and it cannot tell the two apart - see `docs/debugging.md`.

Two more things this measurement settled, both of which were assumed wrongly first:

- **The retry in the loop was the largest single item.** `_a_reading` tried twice,
  so a page like this cost about 70s per reading rather than 34. It now stops after
  the first "could not get idle state", because the phone has just said this page
  will never be dumped and waiting eleven more seconds learns nothing.
- **A scenario step costs two of these readings, not one.** The harness samples a
  stage by reading the screen as well, and it does that *inside* the decider it
  wraps - so the number reported as `deciding` in `steps.jsonl` is mostly screen
  reading, and reading it as model latency is badly wrong. On one 16-step scenario
  that misreading sent a whole round of investigation after the Jev API, which was
  answering in about four seconds throughout.

### It is intermittently dumpable, not never

The shorthand everywhere in this repository is that some pages "never go idle". That
is wrong in a way that matters, and it was measured by reading the same screen twice
in a row:

```
first reading : 2.9s, and it read - 29 controls
second reading: 73.8s, and it failed
```

Same page, same second. So the property is not "this page cannot be dumped" but "this
page cannot be dumped *while it is doing something*", and on a page carrying a live
figure it is doing something most of the time and not all of the time.

That explains behaviour that had been read as flakiness in the scenario suite: the
same scenario reaching the same page and one run seeing 29 controls while the next
sees a picture. It also means an early success is not evidence that the page is fine -
the second read of it is what says so.

Two things follow, and they pull in opposite directions:

- A **probe** may be able to read such a page, so a ground truth built only on the
  picture is weaker than it needs to be. Retrying the dump a few times is worth it,
  which is exactly what `the_screen` in the scenarios does and why it says a probe can
  afford to wait.
- The **loop** must not wait, because it pays this per step. Hence stopping after the
  first idle-state failure and going to the picture, which is twenty times cheaper.

### YouTube reports one window for three different screens

Measured in round 35 while looking for a positive signal that a search had been
submitted:

```
YouTube's home screen          com.google.android.youtube/...honeycomb.Shell$HomeActivity
while a query is being typed   com.google.android.youtube/...honeycomb.Shell$HomeActivity
after the query is submitted   com.google.android.youtube/...honeycomb.Shell$HomeActivity
```

All three are the same window. So `the_window_is("SearchResults")` - the probe
`youtube-open-a-result` has been built on - cannot match, and that scenario cannot pass
however well the run does. It has been failing with "the goal was never reached: the
window in front is ...Shell$HomeActivity" for as long as it has been run, which reads
as a run that did not get there and is in fact a probe that never could.

The other signals are gone too. "views", "subscribe" and "shorts" are on the home screen
as well, which is the mistake already recorded above; the search box reads empty on the
home screen and after submitting; and the editable-field probe this note's sibling uses
is satisfied by the empty box before anything is typed at all.

What is needed is a positive signal that a search happened, on a screen whose window,
its empty fields and its chrome are all identical to the screen before it. Nothing
cheap has been found yet, and nothing has been guessed in its place.

## Reliable ways to prove an end state

Everything this project has judged so far has been judged by reading the accessibility
tree. That is not the only route, and for several goals it is the wrong one. Measured on
this phone, in order of how much they can be trusted.

### 1. The page's own title, exactly - for any Settings page

The best signal for a Settings page, and the one two scenarios should have been using
from the start. Measured on the Battery page, reached by tapping as a run reaches it:

```
the list row that opens it   "Battery 100%"                       exact match: no
the page's first control     "Battery"                            exact match: yes
the page's control count     25                                  so it dumps fine
```

A Settings **row** carries its summary after its label, and a Settings **page** is
titled with the bare word. So an exact match on the title is a page-identity probe that
cannot be satisfied from the list - which is what makes it usable where a word-search
and a window name both fail. Storage is the same: the row reads "Storage 22% used -
99.61 GB free" and the page reads "Storage".

The window is *not* usable for this. Reached by tapping, both pages report
`com.android.settings/.SubSettings` - the same generic activity - which is why the
window-name probes for them could never match.

### 2. Chrome's DevTools protocol - for anything in a browser

`adb forward tcp:9222 localabstract:chrome_devtools_remote`, then `GET /json`. It
answers with every open tab and, for each, the real URL, the real page title, and a
`webSocketDebuggerUrl` that accepts the full protocol - so the DOM and arbitrary
JavaScript are available, not just the URL.

Measured, with Chrome holding pages from earlier runs:

```
https://example.com/                                       "Example Domain"
https://en.wikipedia.org/wiki/MrBeast                      "MrBeast - Wikipedia"
https://www.reddit.com/r/phonewallpapers/comments/1weeut7  "Wallpaper : r/phonewallpapers"
https://www.google.com/search?q=pixel+phone+wallpaper+...  "... - Google Search"
```

That is a page's address as data, rather than a guess from the address-bar text: a
result page and a search page differ by their URLs and not by whether a word is on the
screen. Two things to know. It lists **every** tab, so which one is in front is not in
the answer - the address bar's text from the tree has to be matched against the list to
say. And the socket only exists while Chrome has a page open.

### 3. Service dumps, for state the screen never shows

- `dumpsys alarm` lists scheduled alarms and carries the owning package, so an alarm set
  by the clock app is checkable by package rather than by reading a dial.
- `dumpsys media_session` carries playback state and metadata - the route for "a video is
  actually playing", which no window name can indicate.
- `cmd notification list` returns structured rows (`package|id|tag|uid`).
- `dumpsys battery`, `dumpsys wifi` and `dumpsys bluetooth_manager` are already used.

**Not reliable, and worth naming because it looks like it should be:** `dumpsys activity
top`. Asked while the Settings Battery page was in front, it reported fragments belonging
to *Photos* (`DrawerFragment`, a `PHOTOS` tag, a Photos tab-bar mixin) - it is not a
foreground oracle, and a probe built on it would be reading another app's state.
