# android-jev

**Control your Android phone through Jev.**

You say what you want done — *open the Play Store*, *turn on aeroplane mode*, *take a
photo* — and this does it. It plugs into any MCP client as a tool called `run_task`;
inside, it reads the phone's own screen, asks
[Jev](https://typesafe.ai/blog/introducing-system-one-models-and-jev) what to do next,
does it, checks the result, and tells you honestly whether it worked.

Everything runs over `adb`. Nothing is installed on the phone.

<!-- mcp-name: io.github.FZ2000/android-jev -->

```
run_task("open the Play Store")   →  achieved: true   done, 3 steps
run_task("search for YouTube")    →  achieved: true   done, 5 steps
run_task("open YouTube")          →  achieved: true   done, 3 steps
```

## Quick start

### What you need

- **A phone attached by USB**, with a data-capable cable. It stays plugged in while
  it works.
- **USB debugging on** — one setting, below.
- **macOS with Python 3.11+**, and `uv`. The picture route uses macOS Vision, which
  installs on macOS as a dependency; on Linux the loop works and reads the
  accessibility tree, and leaves the fallback out.
- **Verified on a Pixel 8a running Android 17.** The project has only been measured
  on that one, so treat other phones as untested rather than unsupported.

### 1. Connect the phone

Plug it in, and on the phone:

1. **Settings → About phone → tap "Build number" seven times.** Developer options
   appear.
2. **Settings → System → Developer options → USB debugging → on.**
3. Replug the cable. The phone shows **Allow USB debugging?** — tick *Always allow*
   and tap **Allow**. Nothing works until that tap happens, and it is deliberate: a
   computer cannot authorise itself.

Check the connection:

```bash
adb devices          # the phone should say "device", not "unauthorized"
```

### 2. Install the server

```bash
git clone https://github.com/FZ2000/android-jev ~/src/android-jev
cd ~/src/android-jev
uv venv --python 3.12 .venv
uv pip install --python .venv/bin/python -e .
```

The distribution is `android-jev`, the import is `phone_control` and the command is
`phone-control`: three names, each read somewhere different. The command also answers
to `android-jev`, so that `uvx android-jev` — the spelling a registry client derives
from the distribution name — starts the server.

No `adb`? `PHONE_CONTROL_ADB` points at one, and the usual SDK locations are
searched:

```bash
curl -sSL -o /tmp/pt.zip \
  https://dl.google.com/android/repository/platform-tools-latest-darwin.zip
unzip -q /tmp/pt.zip -d ~/.local/opt
```

### 3. Give it a decision model

`run_task` works without one, using a keyword fallback, and is markedly better with
Jev: goals that do not name a control — *turn off Bluetooth*, *open the settings
app* — need it.

Either of two keys works, and the key's own shape says which gateway serves it: an
OpenRouter key beginning `sk-or-`, or a TypeSafe key beginning `apikey_`. Nothing in
the configuration says which you are using, because sending one gateway's model to the
other is a 400.

The server reads it from `JEV_API_KEY` — or `OPENROUTER_API_KEY`, if that is the name
already in use here — or from a file named `jev-api-key` or `openrouter-api-key` in the
secrets directory of whichever harness starts it. In DeepSeek Harness that is
`$DSH_HOME/secrets/`:

```bash
printf %s "$JEV_API_KEY" > "$DSH_HOME/secrets/jev-api-key"
chmod 600 "$DSH_HOME/secrets/jev-api-key"
```

Whichever way it reaches the server, the key never appears in a state, a log, a run
folder or a transcript.

### 4. Mount it in your agent

**In Claude Code, this repository is also a plugin**, which mounts the server and
installs the skill in one step — steps 4 and 5 both:

```bash
claude plugin marketplace add FZ2000/android-jev
claude plugin install android-jev@android-jev
```

It asks for the Jev key once and keeps it in the system's credential store; leave it
empty to use `JEV_API_KEY` or `OPENROUTER_API_KEY` from your environment instead. The
server runs from the plugin's own copy of this repository through `uv`, so what it
does always matches the skill that describes it. It still needs `uv` and `adb`, and
the phone set up as in step 1.

For any other client, or to run from your own clone:

Any MCP client can mount this server; it speaks MCP over stdio. Send the agent this,
with the path changed to wherever you cloned the repository — it says what the
repository is and where its own documentation lives, because an agent that has to guess
either of those will guess wrong. A prompt rather than a snippet per client: each client
keeps its MCP configuration somewhere different, and changes the format between
versions.

> The repository at `~/src/android-jev` is an MCP server that drives an
> Android phone attached by USB, together with the instructions for using it. Mount it
> for me, and tell me when you have.
>
> - Start it as a local stdio server: the absolute path
>   `~/src/android-jev/.venv/bin/python`, the single argument `-m
>   phone_control`, run from that directory.
> - Call it `android`.
> - It needs the Jev key in its environment, under `JEV_API_KEY` — or `OPENROUTER_API_KEY`
>   if that is the name already in use here. Either an OpenRouter `sk-or-…` key or a
>   TypeSafe `apikey_…` one: the shape of the key decides which gateway serves it. If
>   your MCP bridge scrubs `KEY`, `PASSWORD`, `SECRET` or `TOKEN` out of a child
>   process's environment, name the variable in the server's own `env` rather than
>   expecting it to be inherited.
> - Give it a generous per-call timeout, a couple of minutes: one of its tools waits for
>   the phone by design.
> - **Read `README.md` at the repository root first.** Its quick start covers connecting
>   the phone, enabling USB debugging, installing the server and supplying the key;
>   `docs/` has the longer explanations. If the phone is not attached or the server will
>   not start, read that rather than guessing.
> - The instructions for *using* it are `skills/android-phone-control/SKILL.md`, with
>   every tool and argument in `references/tools-reference.md` beside it.
>
> When it is mounted, list its tools and tell me whether I need to start a new session
> before you can see them.

Three things catch people out, and the prompt is written the way it is to survive all
three:

- **The interpreter path has to be absolute.** A client starts the server itself and
  does not inherit this project's virtualenv, so a bare `python -m phone_control`
  resolves to the wrong interpreter or to none at all.
- **The key cannot always be inherited.** DeepSeek Harness, for one, strips every
  variable whose name matches `/KEY|PASSWORD|SECRET|TOKEN/i` from a stdio child, so the
  key has to be named in the server's own `env` or it arrives empty.
- **A mounted server is not a visible one.** Most clients need a new session, or a
  restart, before a model sees the tools at all — which is why the prompt asks.

The tools surface under whatever namespace your client uses — `mcp__android__run_task`
in DeepSeek Harness, something else elsewhere. Nothing in these instructions depends on
the exact spelling, and neither should you.

### 5. Teach your agent how to use it

The skill is one body of text, `skills/android-phone-control/SKILL.md`, and this
repository already ships it rendered for every harness that reads instructions from the
working directory — which file each one reads is in
[The skill, for any agent](#the-skill-for-any-agent) below. **If you work in this
repository, your agent has it already and there is nothing to install.**

For any other harness, send it this:

> Install the phone-control skill in `~/src/android-jev` for yourself, so
> that you can drive my phone in any session. The instructions are
> `skills/android-phone-control/SKILL.md`, with one reference file beside it at
> `references/tools-reference.md`. Put both wherever you read skills from, and tell me
> whether you need to be restarted before it takes effect.

If your harness has nowhere to keep a skill — a hosted assistant with an instructions
box and nothing else — paste this instead, which is the part that matters:

> The phone is driven by an MCP server mounted as `android`. To do anything on it, call
> `run_task` with the outcome you want in plain language — "open the Play Store",
> "turn on aeroplane mode". Ask for one thing at a time and wait for each result.
> Read `achieved` and `reason` from the reply, and relay them rather than retrying.

### 6. Sanity test: take a photograph

With the phone unlocked and the screen on, ask your agent:

```
run_task("open the camera app and take a photo")
```

Then check the phone's own evidence rather than the report:

```bash
adb shell ls -t /sdcard/DCIM/Camera | head -3      # a new file at the top
```

**A new file and a report that it could not confirm the goal are both correct here,
and this is the most useful thing the quick start can show you.** A photograph leaves
no trace on the screen — measured, the tree is byte-identical before and after the
shutter — so the run can see that the camera opened and cannot see the picture. It
says so instead of claiming a success it did not verify.

Run against the phone this was written on, through a fresh clone of this repository,
that is what it looks like: five steps, three of them pressing the shutter, and then

```
"achieved": false,   "outcome": "stalled",
"reason": "3 actions in a row left the screen exactly as it was, so the run was
           going round rather than on"
```

and three new files in `DCIM/Camera`. It took the photograph and could not know it
had, so it stopped rather than pressing the shutter forever. That is the honest
answer to a goal whose result is off-screen, and it is why the last line of this
test is one whose result *is* on the screen:

```
run_task("open the calculator app")     # achieved: true, done, 3 steps, ~13s
```

## Where to go next

- **[`docs/engineering-decisions.md`](docs/engineering-decisions.md)** — why the code
  is the way it is, with the measurement that forced each decision.
- **[`docs/code-map.md`](docs/code-map.md)** — every module, what it is for, and how
  they depend on each other.
- **[`docs/android-apis.md`](docs/android-apis.md)** — what this phone will and will
  not tell you, measured, including the reliable ways to prove an end state.
- **[`docs/debugging.md`](docs/debugging.md)** — one run failing, and what to read.
- **[`docs/tool-contract.md`](docs/tool-contract.md)** — the tool surface as a
  contract, including what is deliberately not built.
- **[`docs/what-we-got-wrong.md`](docs/what-we-got-wrong.md)** — what was tried and
  did not work.
- **[`CONTRIBUTING.md`](CONTRIBUTING.md)** — the rules the tests live by.

## Why it is built this way

Most adb wrappers expose `tap(x, y)`, which is useless to an agent that cannot
see. This one exposes what is actually on screen:

```
#5 ImageButton labeled "Send message" at (970,2170) [tappable]
#4 EditText labeled "Type a message" at (450,2170) [text field, focused]
```

Four decisions follow from that, and each cost something to learn.

**The reading is structured, not a screenshot.** `read_screen` reports every
window with its type, whether the keyboard is up, and a typed description of every
control — its category, label, bounds and a point to tap. An image is the fallback
for genuinely visual screens, not the default. See
[`docs/android-apis.md`](docs/android-apis.md) for what is extractable and what is
not.

**It reports what has focus, not what is merely running.** When a system window
such as the lock screen or the notification shade is in front, `status` says so
and reports no foreground app — rather than naming the app behind it, which is what
`mFocusedApp` would have said.

**A control is addressed by name, then by number, and only then by coordinate.**
Every action tool re-reads the screen before it acts, so a name or a number is
resolved against the screen as it is *now*; a coordinate is not. Read the reply —
it names exactly what was acted on.

**The agent states the goal; the server owns the loop.** `run_task("open the
email app")` is one call. The server reads its own screen, decides each step,
checks its own work, and repeats until the goal is reached or cannot be — then
reports what it did and why it stopped. An agent needs to know nothing about
accessibility trees, windows or coordinates to use it.

**Each step is several typed questions, asked together.** Inside that loop,
[Jev](https://typesafe.ai/blog/introducing-system-one-models-and-jev), TypeSafe's
System One decision model, answers four questions in one request and only the
answer belonging to the chosen operation is used:

| question | what it settles |
| --- | --- |
| `operation` | which kind of action, from what this screen can actually do |
| `target` | which control, if the operation taps one |
| `app` | which installed app, if the operation opens one |
| `completion` | whether a fresh reading shows the goal met |

Splitting them is the part that mattered. One flat list holding operations, app
names and screen controls made the decider unsure on a launcher screen — it
answered `give_up` at 0.63 — and the reason is mechanical: **confidence measures
how concentrated a choice is, so overlapping options always read as doubt**,
whichever of them is right. Once the operations had a question of their own,
`open_app` won the same screen at 0.74.

**Only what can be carried out is offered.** Typing needs a focused field,
scrolling needs something that scrolls, a website needs a value the device can
resolve, a tap needs a control to tap. An option the loop cannot execute is a
guaranteed dead step and worse than that — it reads as the model being unsure when
the model was never at fault. A web address with no scheme was offered once;
Android could not resolve it, the action did nothing, and the 0.41 confidence on
that step was read as doubt for days.

**The state is facts computed from the phone, not a dump to interpret.** What the
focused field holds, where each control sits in coarse terms, what tells two
identical labels apart, what has already been tried on *this* screen. This is what
makes a threshold unnecessary rather than merely wrong: asked whether a goal was
complete, Jev scored **0.42 to 0.47** on a screen where it genuinely was and
**0.51** on one where it was not, because in neither case did the state say whether
the query had been sent or was still in the box. No number separates those two; a
state that says what is on the screen does.

**A low number is not a reason to stop, and the loop does not decide.** Every rule
that used to refuse an answer for scoring low is gone, because each one was
measured ending a run that was working. What is left is a stop rule about the
*phone* — three actions in a row that left the screen as it was, or two already
taken on the same screen, means the run is going round rather than on — and both
err toward running on, because a futile run costs a minute and a working run cut
short costs the task.

**One exception, and it is about safety rather than sureness.** An action that
would have a material effect — sending, deleting, paying, granting a permission —
is not carried out unless the goal explicitly asks for it. That gate exists because
a real run tapped *Allow Chrome to record audio* while pursuing an unrelated goal.

**When the loop stops short, a reader takes over.** With no reader configured a
stop ends the run as it always did. With one, it reads the screen and says whether
the goal is met, and may set *one* next move for the decider to aim at — never
choosing the action itself. The reference implementation measured **0.39 becoming
0.92** on the same capture once a focus was set, which is the argument in one
number: the fix belongs in what the decider is told, not in a threshold.

**The phone is read once per step.** Reading it was 84% of a step, half of that a
second read whose only job was to see what the action had done. The post-action
observation is now the next step's reading:

```
before   step 7.29s   reading 2.91s   watching_the_result 3.18s   deciding 0.26s
after    step 3.75s   reading 2.18s   watching_the_result 0.00s   deciding 0.24s
```

**What is sent is the source, not a summary of it.** Jev's state carries the
accessibility tree's own field names and values, and each option is a word
(`open_app`) rather than our numbering. The
[measured prompting evidence](https://github.com/willkelly/jev-evaluation/blob/main/PROMPTING.md)
is blunt: the same programs scored **0.894** as source, **0.598** as a syntax tree
and **0.530** as a control-flow graph, even though the graph carried more
information for 2.5× the tokens.

## Running it directly

```bash
.venv/bin/python -m phone_control
```

It speaks MCP over stdio and waits. Nothing to configure; the tools appear as
`mcp__android__*` once a client mounts it, as described in the quick start.

## The tools

| | |
| --- | --- |
| **Seeing** | `status`, `read_screen`, `take_screenshot` |
| **Acting** | `tap`, `type_text`, `scroll`, `swipe`, `press_key`, `open_app`, `open_url` |
| **System** | `list_apps`, `wait_for`, `read_notifications`, `clipboard`, `run_shell`, `transfer_file` |
| **Doing the whole job** | `run_task` — say the goal, get a report |
| **Stepping by hand** | `decide_next_action`, `ask_jev` |

Full argument reference:
[`skills/android-phone-control/references/tools-reference.md`](skills/android-phone-control/references/tools-reference.md).

## The skill, for any agent

[`skills/android-phone-control/`](skills/android-phone-control/) teaches the
loop: start with the goal rather than the taps, when to read the tree rather than
take a screenshot, and how to read Jev's confidence. The body names no agent, so
the same instructions work anywhere.

Each tool reads a different file, so the skill is rendered into all of them from
one canonical source — `skills/android-phone-control/SKILL.md`:

| File | Read by |
| --- | --- |
| `AGENTS.md` | Codex, Cursor, Copilot agent mode, and the wider AGENTS.md convention |
| `GEMINI.md` | Gemini CLI |
| `.github/copilot-instructions.md` | GitHub Copilot |
| `.cursor/rules/android-phone-control.mdc` | Cursor |
| `.claude/skills/android-phone-control/` | Claude Code |

Edit the canonical skill and re-render; a test fails if any copy has drifted:

```bash
.venv/bin/python scripts/render_skill_for_agents.py
```

For DeepSeek Harness, symlink or copy `skills/android-phone-control/` into a
scanned skill root — `$DSH_HOME/skills/` for every session, or `.dsh/skills/` in a
project. Discovery is live; no restart.

## Environment

| Variable | Effect |
| --- | --- |
| `DSH_PHONE_SERIAL` | Which device to drive when several are attached. |
| `JEV_API_KEY` | The Jev key, enabling the two decision tools. An OpenRouter `sk-or-…` key or a TypeSafe `apikey_…` one — its own shape routes it. |
| `OPENROUTER_API_KEY` | The same key under its older name, still read. |
| `PHONE_CONTROL_ADB` | Path to `adb` when it is not on `PATH`. |

## Known limits

- **Whether a screen can be read at all varies moment to moment.** This is the
  limit to know about, because it produces failures that look like the model's fault
  and are not. Measured on one page, read twice in a row: **2.9 seconds and 29
  controls, then 73.8 seconds and a failure.** The page cannot be dumped *while it is
  doing something*, and a page carrying a live figure is doing something most of the
  time and not all of it. So the same goal can succeed on one attempt and fail on the
  next, with nothing changed but the timing. Everything else below is a consequence or
  a neighbour of this one.
- **A screen hosting embedded Android Views inside Compose can read as almost
  empty.** The interop wrapper is marked `NO_HIDE_DESCENDANTS`, which hides its
  whole subtree from every accessibility client — no flag or setting overrides it,
  and Appium's workaround exists only because it ships its own dumper inside an
  installed APK. Measured, this is the *common* case on this device's Settings pages
  rather than the exception.
- **When the tree will not describe a page, a photograph of it is read instead.**
  That is the second route, and it reaches the tools as well as the loop: a page that
  cannot be dumped still comes back with its words. What it cannot do is give
  *controls* — the reading is lines of text with no tappable targets on them — so on
  such a page a run can conclude the goal and leave, and cannot act further. A page
  whose dump fails and whose photograph says nothing either is reported as a failure,
  not as an empty screen.
- **`dumpsys activity top` is not a foreground oracle.** It reads as though it should
  be, and a probe built on it will silently read another app's state: asked while the
  Settings Battery page was in front, it reported fragments belonging to *Photos*.
  `dumpsys window` names the page that is actually in front, and for a Settings page
  reached by tapping that is the generic `SubSettings` — the page's own title, read
  from the tree, is the reliable identity.
- **A sleeping screen reads as empty.** The display going off leaves one bare node.
  `read_screen` says which of asleep, locked or canvas-only it is.
- **Non-ASCII typing.** `input text` goes through the virtual keyboard's character
  map, which returns null for anything it cannot generate — AOSP's own comment says
  *"For robust text entry, do not use this function."* Non-ASCII is routed through
  the clipboard and a paste instead. The proper fix is `ACTION_SET_TEXT`.
- **`dumpsys` output is internal and drifts between releases.** Parsing is tested
  against dump shapes captured from real reports and exercised on a real device;
  [`docs/android-apis.md`](docs/android-apis.md) separates what was confirmed from
  what the device corrected.

## Documentation

| | |
| --- | --- |
| [`docs/android-apis.md`](docs/android-apis.md) | What each route to an Android phone offers and costs, the parsing traps, and what a real device confirmed |
| [`docs/tool-contract.md`](docs/tool-contract.md) | Where the tool surface is going, with the measurements — failures now raise, so `is_error` is true |
| [`docs/what-we-got-wrong.md`](docs/what-we-got-wrong.md) | A diagnosis against a reference implementation of the same loop: the state and the questions are structurally wrong, and tuning will not fix them |
| [`docs/debugging.md`](docs/debugging.md) | **Start here when a run goes wrong** — what to read, in what order, and how to re-decide a recorded step without a phone |
| [`tests/test_what_the_phone_actually_does.py`](tests/test_what_the_phone_actually_does.py) | Every belief this code holds about Android, proven against the phone. A fake can only repeat what its author believed; these cannot |
| [`docs/repository-settings.md`](docs/repository-settings.md) | What protects `main`, what GitHub refuses until the repository is public, and why a contribution is safe here already |

## Testing

```bash
scripts/check.sh --as-ci                        # what CI runs: no phone, no key
scripts/check.sh                                # everything, with the 95% coverage gate
.venv/bin/python -m pytest -q -m "not device"   # everything that needs no phone
.venv/bin/python -m pytest -m device -q         # everything that needs a real phone
```

`--as-ci` is the one to run before pushing. It runs the fast suite on a machine that
has never been set up — no adb, no decision-model key, no home directory holding
either — because that is what the runner is, and the difference is not obvious from
a machine that has both. The suite passed here for weeks while main was red for
exactly that reason.

The default suite runs without a device; device tests skip themselves when none is
attached, and when the phone is asleep or locked — that is a precondition, not a
code property. The same is true of three other things the suite needs and cannot
provide: a Jev key, a network that holds for the length of a run, and macOS's Vision
framework, which is the only way to read a picture of a screen. A test that needs any
of them stands itself aside with the reason rather than failing, because a dropped
connection is not a fact about this code.

That last one is worth knowing when reading a build: on the Linux runner, every test
that photographs a screen stands aside, so the picture route is verified on a Mac
rather than in CI. `scripts/check.sh --as-ci` reproduces the missing adb and the
missing key, because it runs on a Mac it cannot reproduce the missing framework.

The counts are deliberately left out of this list. They change with every commit
that adds a test, and a number in a README is stale the moment after it is written
— these commands print the real ones. What does not change is the shape: the
majority of the suite needs no phone, and the device suite is the part that cannot
be faked.

Coverage has to be read from both suites together, against a gate of **95%**: most of
this package only executes with a phone attached, so the fast suite alone fails a gate
on code it had no way to reach. `scripts/check.sh` runs both. CI runs the fast suite
only, because a hosted runner has no phone.

### The usage scenarios

`tests/scenarios/` is the specification: all 47 scenarios, each a thing a person asks
a phone to do, from one launch to goals no phone can reach. Each carries its own
starting state, its own ground truth read from the phone, and a screenshot at every
stage.

```bash
scripts/run_scenarios.py --tier 1            # a tier at a time
scripts/run_scenarios.py --id bluetooth-off  # or one, by name
scripts/run_scenarios.py --all               # about half an hour
```

Artefacts land in `artifacts/scenarios/<id>/`. See
[`docs/debugging.md`](docs/debugging.md) for how to read them.

## The rules that keep the tests honest

```bash
scripts/install_git_hooks.sh      # once: refuse a commit whose suite is red
.venv/bin/python -m pytest tests/test_no_xfail.py -q   # no test may be allowed to fail
```

Four times in this project a fake agreed with its author and the belief was wrong:
that a bare domain opens a browser, that `am start -d` resolves it, that
`cmd clipboard set` places something, that a settings toggle is checkable. Each cost
real work. Hence two rules rather than good intentions — a test allowed to fail
reports green while the behaviour it describes does not work, and a belief about
Android belongs in
[`tests/test_what_the_phone_actually_does.py`](tests/test_what_the_phone_actually_does.py),
where the phone gets asked.

## Watching a run afterwards

An MCP reply is one shot and a run leaves nothing behind unless you ask it to:

```bash
export PHONE_CONTROL_RUNS=~/.phone-control/runs
```

Every run then writes a folder — the report, the exact request and answer for
every step, and where the seconds went — and the tool's reply names it. A recorded
step can be re-decided offline, without the phone:

```bash
.venv/bin/python scripts/replay_run.py ~/.phone-control/runs/<folder> --step 0
```

## Status

Working and device-validated: opening apps, reading and acting on screens,
scrolling, typing, window and keyboard state, screenshots.

### Measured, on a Pixel 8a running Android 17

`tests/test_jev_does_the_job.py` asks for things a person would ask for and then
checks the phone rather than the report. Every goal below was independently
confirmed against the focused window, which the decision never sees:

| Goal | Reported | Verified | Steps | Time |
| --- | --- | --- | --- | --- |
| "open the settings app" | achieved | `com.android.settings` | 2 | 9.6 s |
| "open the clock app" | achieved | `com.google.android.deskclock` | 2 | 8.5 s |
| "open the calculator app" | achieved | `com.google.android.calculator` | 2 | 8.3 s |

**No false successes**: nothing claimed a result the phone did not reach. The
completion check agreed with the chosen action every time, at confidences of
0.97, 1.00 and 0.99.

One honest failure. Asked to "order a large pepperoni pizza", which no phone can
do, it correctly reported the goal unmet — but it spent all ten steps exploring
Chrome's interface first, including tapping *"Allow Chrome to record audio"*.
It explores before it concedes, and some of that exploration is consequential.
Bounding that is open work.

Not yet done, and named rather than implied: the tool contract migration described
in [`docs/tool-contract.md`](docs/tool-contract.md), and any device run of the Jev
decision path, which needs a Jev key.
