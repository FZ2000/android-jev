# Tool reference

All tools live under the `mcp__android__` namespace. The server mounts as
`android`, so the bare names below are what the skill and the tool descriptions
use.

## Doing the whole job

| Tool | Arguments | Notes | You get back |
| --- | --- | --- | --- |
| `run_task` | `goal`, `max_steps?` | Give the phone a goal and let it work the whole thing out: read, decide, act, read again, until the goal is reported done or unreachable. One call replaces a whole step-by-step loop. Reports every step it took, what decided each one, and why it stopped. | JSON: `ok`, `summary`, `outcome`, `reason`, `steps`, `decided_by` |

**What you get back, and how you know it worked.** Every tool either returns one
of four shapes — JSON, a text listing, an image, or a single sentence — or it
*fails*. A failure is not an answer: it is raised, and reaches you with
`is_error` set and a plain explanation, so a tool that could not do what you asked
never looks like one that did. Only `run_task` carries a success flag (`ok`); for
everything else, the failure being an error is the signal.

Reach for `run_task` first for anything expressible as an outcome — "open the
email app", "turn on airplane mode". Use the step-by-step tools below when you
already know what to tap, when the screen is visual rather than textual, or when
you need to inspect something part-way through.

## Seeing

| Tool | Arguments | Notes | You get back |
| --- | --- | --- | --- |
| `status` | — | Model, Android version, screen size, foreground app, screen on, locked, battery. Changes nothing. | Several lines of facts, one per reading (`Model:`, `Screen:`, `Battery:` …) |
| `read_screen` | `query?` | Numbered, named listing of on-screen controls. With `query`, only matches are listed, best first. | A numbered listing, one line per control |
| `take_screenshot` | `max_width?` | Returns the screen as an image. Defaults to 900 px wide; pass `0` for full resolution. | An image |

## Acting

| Tool | Arguments | Notes | You get back |
| --- | --- | --- | --- |
| `tap` | `target?`, `x?`, `y?`, `long_press?` | `target` is a number from `read_screen` or a visible label. Use `x`/`y` only when nothing is nameable. | One sentence saying what happened |
| `type_text` | `text`, `target?`, `replace?`, `submit?`, `verify?` | Taps the target first when given. `replace` clears the field. `submit` presses Enter. `verify` re-reads the screen (default on). ASCII only. | One sentence saying what happened |
| `scroll` | `direction?`, `distance?`, `target?` | `direction` is where you want to look: `down`, `up`, `left`, `right`. `distance` is `small`, `page`, or `large`. | One sentence saying what happened |
| `swipe` | `start_x`, `start_y`, `end_x`, `end_y`, `duration_ms?` | Raw drag for gestures no control covers: dismiss, pull to refresh, pattern lock. | One sentence saying what happened |
| `press_key` | `key` | See the key table below. | One sentence saying what happened |
| `open_app` | `name`, `wait_seconds?` | Accepts "YouTube", "play store", or an exact package id. Waits for the foreground to change. | One sentence saying what happened |
| `open_url` | `url`, `wait_seconds?` | URLs and deep links, including `geo:`, `tel:`, `sms:`, `mailto:`. | One sentence saying what happened |
| `wait_for` | `text?`, `app?`, `timeout_seconds?` | With neither argument, waits for the screen to stop changing. | One sentence saying what happened |

## System

| Tool | Arguments | Notes | You get back |
| --- | --- | --- | --- |
| `list_apps` | `query?` | Installed packages as `readable name -> package id`. | One sentence saying what happened |
| `read_notifications` | `close_after?` | Opens the shade, lists it, closes it unless told otherwise. | A listing of what the shade is showing |
| `clipboard` | `action?`, `text?` | `get` or `set`. The route for typing non-ASCII text. | One sentence saying what happened |
| `run_shell` | `command` | Escape hatch; runs as the shell user on the device. | Whatever the device printed, unaltered |
| `transfer_file` | `direction`, `computer_path`, `phone_path` | `to_phone` or `from_phone`. | One sentence saying what happened |

## Decisions

| Tool | Arguments | Notes | You get back |
| --- | --- | --- | --- |
| `decide_next_action` | `goal`, `max_candidates?` | Jev chooses one control from the current screen and returns a directly executable action plus a confidence. | JSON: `summary`, `next_action`, `confidence`, `alternatives`, `advice` |
| `ask_jev` | `instructions`, `question_type?`, `options?`, `state?` | Ask a typed question — `choice`, `yes_or_no`, or `scale` — about the current screen. | JSON: `summary` plus the typed answer (`probability_yes`, `choice` or `position`) |

`decide_next_action` returns an `advice` field whenever the confidence is below
0.70; follow it.

## Keys accepted by `press_key`

`back`, `home`, `recents`, `enter`, `delete`, `escape`, `tab`, `space`,
`move_home`, `move_end`, `volume_up`, `volume_down`, `volume_mute`, `power`,
`wake`, `sleep`, `camera`, `play_pause`, `next_track`, `previous_track`, `copy`,
`cut`, `paste`, `select_all`, `search`, `menu`, `page_up`, `page_down`,
`notification_shade`, `quick_settings`, `collapse_shade`.

Friendly aliases also work: `app_switch`, `overview`, `backspace`, `del`,
`return`, `notifications`, `shade`, `settings_shade`, `screen_on`, `screen_off`.

## Failure text

Phone problems are returned as text, never as a crash, and always with a
`Next step:` line. The messages worth recognising:

| Message contains | Meaning |
| --- | --- |
| `No Android device is attached over USB` | Cable out, or USB debugging off. | One sentence saying what happened |
| `has not authorized this computer` | An *Allow USB debugging?* prompt is waiting on the phone. | One sentence saying what happened |
| `Nothing on screen matches` | The label drifted, or the screen has not finished loading. Re-read. | One sentence saying what happened |
| `This phone does not expose its clipboard` | Use `type_text` for ASCII instead. | One sentence saying what happened |
| `Only ASCII can be typed` | Use the clipboard-and-paste route. | One sentence saying what happened |
| `No Jev key is set` | Jev tools are unavailable; decide from `read_screen`/`take_screenshot` yourself. | One sentence saying what happened |

## Environment

| Variable | Effect |
| --- | --- |
| `DSH_PHONE_SERIAL` | Which device to drive when more than one is attached. | One sentence saying what happened |
| `JEV_API_KEY` | The Jev key, enabling `decide_next_action` and `ask_jev`. An OpenRouter `sk-or-` key or a TypeSafe `apikey_` one; its own shape routes it. | One sentence saying what happened |
| `OPENROUTER_API_KEY` | The same key under its older name; still read. | One sentence saying what happened |
| `PHONE_CONTROL_ADB` | Path to `adb` when it is not on `PATH`. | One sentence saying what happened |