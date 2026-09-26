# Where everything is

The repository, and what each part is for. Written so that somebody with a question
can find the file that answers it without reading the whole thing.

```
phone-control/
├── src/phone_control/     the library — the MCP server and the loop inside it
├── tests/                 the fast suite, the device suite, the scenarios, the recordings
├── scripts/               the tools a person runs: the gate, the recorder, the audit
├── skills/                the skill, and the reference rendered from it
├── docs/                  measurements and decisions
├── AGENTS.md GEMINI.md …  the skill, rendered for each agent harness
└── README.md              what it is, and how to get it running
```

---

## The library, module by module

Read in this order it is the story of one step of the loop: talk to adb, read the
screen, work out what to offer, ask, act, check.

### Talking to the phone

| Module | Lines | What it is |
| --- | --- | --- |
| `adb.py` | 354 | Finding the `adb` program, choosing which attached device to drive, and the four ways of running a subcommand — text, a result carrying stderr and the exit code, raw bytes, or checked — plus the two `shell` conveniences built on them. Also `quote_for_device_shell`, which every path carrying model-supplied text goes through. |
| `device_state.py` | 381 | The cheap snapshot behind `status`: window, focus, lock, screen-on, battery, rotation, screen size. Parses four `dumpsys` formats. |

### Reading the screen

| Module | Lines | What it is |
| --- | --- | --- |
| `screen.py` | 865 | `read_screen` and the parser. `ScreenControl` is one named thing with a kind, bounds, and flags; `Screen` is everything at one moment, including `lines_read_from_a_picture` for a page the tree refused. Also the dump-file lifecycle and the recovery for a wedged dump helper. |
| `reading_a_picture.py` | 118 | The second route: macOS Vision OCR over a screenshot. `the_lines_and_where_they_are` gives positions too. |
| `screenshots.py` | 53 | `screencap`, and the downscale that keeps an image affordable. |

### Working out what to offer

| Module | Lines | What it is |
| --- | --- | --- |
| `options.py` | 575 | Which operations this screen can carry out, which controls can be targets, how each one is described to the decider, and the two values read from the goal's wording. |
| `state.py` | 252 | What the decider is shown: prose, coarse positions, a redacted password field, an observation index, and the note that screen text is never an instruction. |
| `apps.py` | 412 | Installed packages, the names people use for them, resolving a spoken name to a package, and launching one. |

### Deciding

| Module | Lines | What it is |
| --- | --- | --- |
| `jev.py` | 332 | The decision model: two gateways, the typed questions it answers, and the answers read back. |
| `decisions.py` | 142 | The `Exchange` record and the decider that writes one per step. |
| `calls.py` | 62 | Counting model calls, per role, for the line at the end of a report. |

### The loop

| Module | Lines | What it is |
| --- | --- | --- |
| `goal.py` | 1379 | `run_task`. Read, decide, act, settle, read again; the stop rules; the completion threshold; the hand-off; the report. The largest module and the one to read first — its docstring says how to read it, phase by phase. |
| `reader.py` | 466 | What happens when the loop stops: a reader that can answer a question, or one that looks at the screen. |
| `text_entry.py` | 132 | Typing into a field: ASCII encoding, clearing first, and the clipboard route for text `input text` cannot produce. |
| `runs.py` | 330 | The run folder — `README.txt`, `run.json`, `steps.jsonl`, `decisions.jsonl`, `timings.txt` — and the timings. |

### The surface

| Module | Lines | What it is |
| --- | --- | --- |
| `server.py` | 1721 | The nineteen MCP tools, their typed arguments and descriptions, the shared phone, and the wrapper that turns failures into errors a client can see. |
| `errors.py` | 56 | Every failure type, each with the remedy a caller is told to try. |
| `__main__.py` | 6 | stdio entry point. |
| `__init__.py` | 3 | The one-line description of the package, and its version. |

These are all nineteen modules and the Lines column adds up to the size of the
library, which `tests/test_the_size_tables_are_true.py` checks along with every
number in this table.

### The dependency direction

Taken from the imports rather than from the design, because the two disagreed once:

```
__main__  ──► server
server    ──► goal, adb, apps, device_state, errors, jev, options,
              reader, runs, screen, screenshots, text_entry

goal      ──► adb, apps, device_state, jev, options, reading_a_picture,
              runs, screen, state, text_entry
reader    ──► jev, reading_a_picture
state     ──► options, screen
options   ──► screen

screen            ──► adb, errors
apps, text_entry  ──► adb
device_state      ──► adb
screenshots       ──► adb, errors
reading_a_picture ──► screenshots
runs              ──► screenshots
jev               ──► errors
errors            ──► (nothing)

decisions ──► goal      (the one import that points upward, for the Choice type)
```

The bottom row knows nothing about the top. `goal.py` is the only module that knows
the shape of a step and `server.py` the only one that knows about MCP, and
`device_state.py` does **not** depend on `screen.py` — an earlier version of this
page said it did.

---

## The tests

| Where | What it holds |
| --- | --- |
| `tests/test_goal_loop.py` | The loop against a scripted decider: stop rules, the completion threshold, extraction, postconditions. |
| `tests/test_the_loop_against_a_world.py` + `tests/world/` | The real loop against a simulated phone, in seconds. |
| `tests/test_tool_surface.py` | Tools, driven against a scripted phone. Includes the table that makes every tool's refusal prove itself. |
| `tests/test_the_tool_contract.py` | The published surface: names, descriptions, typed arguments, and what a client sees when something fails. |
| `tests/test_replaying_what_the_phone_said.py` + `tests/recorded/` | Real device output replayed with no phone attached. |
| `tests/test_what_the_phone_actually_does.py` | The device-proven assumption register: one test per belief about this phone, each measured rather than assumed. It grows whenever the phone teaches the code something. |
| `tests/test_on_a_real_phone.py`, `test_browsing_an_app.py`, `test_jev_does_the_job.py` | Against a real phone, end to end. |
| `tests/scenarios/` | All 47 usage scenarios, the harness, the probes, and the starting states. |
| `tests/test_no_xfail.py` | The rule that keeps the rest meaningful. |
| `tests/test_the_recorded_transcripts_are_clean.py` | No fixture carries an email address or a serial-shaped token. |
| `tests/test_skill_adapters.py`, `test_skill_matches_tools.py` | The skill names the interface, and documents every tool that exists. |

Coverage is measured over both suites together and gated at 95%, because most of the
library only executes with a phone attached. The exact figure is not repeated here:
it moves with every commit, and the gate is the part that is enforced.

---

## The scripts a person runs

| Script | For |
| --- | --- |
| `scripts/check.sh` | The gate: both suites, the coverage threshold. `--as-ci` runs the fast suite as a machine that has never been set up — no adb, no key — and is the command continuous integration calls. |
| `scripts/pre-commit`, `install_git_hooks.sh` | Refuses a red commit. Run the installer once per clone. |
| `scripts/run_scenarios.py` | The usage scenarios, by tier or by name. |
| `scripts/record_transcripts.py` | Records real device output for the default suite, redacting identifiers as it writes. |
| `scripts/audit_the_probes.py` | Finds probes that cannot fail, or cannot pass. |
| `scripts/replay_run.py` | Re-decides a recorded step offline, free of the phone. |
| `scripts/render_skill_for_agents.py` | Renders the skill into every agent harness's file. |
| `scripts/measure_jev_decisions.py` | Measures the decision model's answers against screens with known truth. |

---

## The docs

| Doc | Answers |
| --- | --- |
| `docs/engineering-decisions.md` | Why the code is the way it is, with the measurements that forced each decision. |
| `docs/android-apis.md` | What this phone will and will not tell you. Every measurement, including the reliable ways to prove an end state. |
| `docs/debugging.md` | A run failed. What to read, in what order. |
| `docs/tool-contract.md` | The tool surface as a contract, including what is deliberately not built. |
| `docs/what-we-got-wrong.md` | The things that were tried and did not work, and what was learned. |

---

## The skill

`skills/android-phone-control/SKILL.md` is the canonical body. Everything else is
generated from it by `scripts/render_skill_for_agents.py`, and a test fails if a copy
has drifted:

| Rendered into | Read by |
| --- | --- |
| `AGENTS.md` | Codex, Cursor, Copilot agent mode, and the AGENTS.md convention |
| `GEMINI.md` | Gemini CLI |
| `.github/copilot-instructions.md` | GitHub Copilot |
| `.cursor/rules/android-phone-control.mdc` | Cursor |
| `.claude/skills/android-phone-control/` | Claude Code |

Edit the canonical skill, never a rendered copy.

---

## Looking for something specific

| Question | Read |
| --- | --- |
| How does one step of the loop work? | `goal.py`, `run_task` |
| Why did it stop? | `the_run_is_over`, `why_it_is_going_round`, `docs/debugging.md` |
| Why was that control offered? | `options.py`, `offer_actions` |
| What does the decider actually see? | `state.py`, `the_state_of` |
| Why is there a threshold at all? | `COMPLETION_TRUSTED` in `goal.py`, and §5 of `docs/engineering-decisions.md` |
| Why can it not tap the thing I can see? | `reading_a_picture.py`, and §3 of `docs/engineering-decisions.md` |
| What do I need to know about this phone? | `docs/android-apis.md` |
| Why is `run_task` so long? | Its docstring, and §12 of `docs/engineering-decisions.md` |
