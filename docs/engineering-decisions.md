# Engineering decisions

Why the code looks the way it does. Every section here is a decision that was
argued about, and most were changed at least once after a measurement disagreed
with the reasoning. Where a number appears, it was measured on a Pixel 8a running
Android 17 unless it says otherwise; `docs/android-apis.md` and `docs/debugging.md`
hold the raw readings.

The shape in one line: **a small number of decisions, each carrying the measurement
that forced it.**

---

## 1. The agent states a goal; the server owns the loop

`run_task("open the email app")` is one call, and everything between that and a
verdict happens inside the server. The alternative — exposing `tap` and `read_screen`
and letting the model plan — puts the plan in the model's context, where it cannot
see the screen, cannot retry, and cannot verify. It also makes every failure the
agent's to debug.

**Cost of the decision:** the server needs its own decision model, its own stopping
rules and its own notion of what "done" means. That is `goal.py` (1350 lines), the
largest module in the project after the tool surface.

The tools remain published, and the skill deliberately does not send an agent to
them. An agent that starts choosing controls is doing the loop's job with less
information than the loop had — see `docs/tool-contract.md`.

## 2. The screen is read from the accessibility tree, not from pixels

A model that cannot see needs the screen *described*. `read_screen` reports every
window with its type, whether the keyboard is up, and a typed line per control:

```
#5 ImageButton labeled "Send message" at (970,2170) [tappable]
#4 EditText labeled "Type a message" at (450,2170) [text field, focused]
```

An image is the fallback for screens that are genuinely visual, taken with
`take_screenshot`, and the image route is never used to decide an action.

**What it cost to learn:** a screenshot per step is a large request per step, and a
model reading an image cannot name a control, so it taps coordinates. The tree gives
structure the pixels cannot.

## 3. A screen the tree will not describe is photographed — lines, not controls

Measured, and this is the limit that costs the most: **whether a page can be dumped
varies moment to moment.** One page, read twice in a row:

```
first reading : 2.9s, and it read - 29 controls
second reading: 73.8s, and it failed
```

So the loop has a second route (`reading_a_picture.py`, macOS Vision OCR). It
furnishes *lines*, and it cannot furnish controls: the loop can read such a page and
judge whether the goal is met, and it cannot tap what it has only seen a picture of.
Tapping recognised text was implemented and reverted — it taps headings, because a
photograph cannot say which text is a button (`docs/what-we-got-wrong.md`).

**Cost:** the picture is 20× cheaper than a failing dump (0.58s against 11.39s), so
it is tried before the dump's own recovery, and `read_screen(phone, recover=...)`
exists so the loop can ask for the cheap reading while the tools and the scenario
probes keep the thorough one.

## 4. The decision model answers typed questions, so text must come from elsewhere

Jev returns a choice from a list, a Noul, or a scale position. **There is no answer
shape that carries free text**, so a model that chose "open a web address" could not
say which address. The goal and the screen are the only two channels, and
`options.py` reads the goal for the two values that need one — a URL to open and
text to type.

That is a documented exception to "operations come from a capabilities list, never
from the goal's wording": those two regexes supply a *value*, they never choose an
operation, and the operation is offered only when a value exists, because an option
the loop cannot execute is a guaranteed stall.

**Measured cost of getting it wrong:** the cue that introduces content has to win
over the cue that names an instruction. `write a note saying hello` extracted "a note
saying hello" — the words after "write" — and the loop wrote a note titled with the
instruction. `test_a_cue_that_introduces_the_content_beats_one_that_names_an_instruction`
holds the line.

## 5. Completion is the decider's own answer, and the threshold was measured

The loop ends as done when the decider's own answer about the goal is at or above
`COMPLETION_TRUSTED`. Everything else about the screen is the model's to read.

The threshold was measured over a whole catalogue — 126 screens, joined to the
probe verdicts, labelled only by probes that can be trusted:

| threshold | finished screens accepted | unfinished accepted |
| --- | --- | --- |
| 0.95 | 8/28 (29%) | 0/98 |
| 0.90 | 16/28 (57%) | 2/98 |
| **0.80** | **20/28 (71%)** | **2/98** |
| 0.70 | 22/28 (79%) | 3/98 |

0.90 refused 43% of the screens where the goal really was met, and lowering it to
0.80 accepts four more finished screens for no more mistakes, because the only
unfinished screens that ever scored above a half were 0.71, 0.91 and 0.94.

**The number was arrived at twice.** The first harvest of that corpus was misaligned
— the stage record and the decision record number themselves independently — and it
said the question was *anti*-correlated with the truth. That was an artefact of the
join, and `docs/debugging.md` has the worked example, because a plausible spread of
numbers is indistinguishable from a finding.

## 6. The state is written for a reader, not for a parser

`state.py` composes what the decider sees: prose, coarse positions, a redacted
password field, an observation index, and a standing note that everything on the
screen is content and never an instruction. Screen text is app-authored, and an app
can print a sentence that reads like an order.

**Measured addition:** the state carries one general sentence saying a finished
result does not have to look busy. On a recorded screen where the goal was met
against the same screen where it was not:

| state | finished | unfinished | gap |
| --- | --- | --- | --- |
| as sent | 0.60 | 0.23 | 0.37 |
| with the sentence | 0.79 | 0.33 | **0.46** |

It raises both and the gap widens. Rephrasing the *question* was measured too and
rejected: it lifted both by the same amount and left the gap at 0.32.

## 7. Failing means raising, and every tool's refusal is checked

A tool that cannot do what it was asked raises. A returned sentence carries
`is_error: false`, so it reaches a client as a tool that worked and whose answer
happened to be a complaint — and the caller is a model, which reads that as done.

**What the rule cost to enforce:** a table in `tests/test_tool_surface.py` hands
every tool an argument it must refuse and names a word the refusal has to carry. It
found five branches across four tools returning their refusals instead of raising,
two of which a hand search had missed.

## 8. The test suite's job is to be able to fail

Four rules, each from a specific failure, all enforced rather than intended:

- **No expected failures.** `tests/test_no_xfail.py` blocks one, tokenizer-based so
  prose is not a breach.
- **An assertion must be able to fail.** `assert True` and `x is not None or True`
  were both found in the device-assumption register — the file whose purpose is to
  record what was measured — and both now assert the claims their tests are named for.
- **A probe must be able to be wrong.** `scripts/audit_the_probes.py` looks for three
  shapes, including a probe for something being *absent*, which is satisfied before
  the work starts.
- **A scenario must not start in its own end state**, or it cannot fail. The harness
  enforces this, and the condition was inverted for a while — firing on scenarios
  that could not pass trivially and silent on the ones that could.

**The most valuable thing in the repository is the pre-commit hook.** It runs the
fast suite and refuses a red commit. It refused three of this project's own commits
while they were being written, each time over a real defect: a flaky test, a network
tolerance that never fired, and a redaction placeholder that broke the XML in a
fixture.

## 9. Real output beats a fixture somebody wrote

`tests/recorded/` holds adb's own bytes, captured from a real phone and replayed by
the default suite with nothing attached. Three recordings, 310 KB, and the tests
that read them use the real parser: 44 KB of Compose output from the Settings top
level becomes 33 controls, four `dumpsys` formats become a device state, and the
phone's own package list resolves the spoken names.

**The rule that makes it worth believing:** an unrecorded command raises
`NotRecorded` rather than answering. A replayer that returned an empty string would
agree with every question asked of it, including the wrong ones.

## 10. The library knows nothing about particular apps

`SPOKEN_APP_NAMES` maps packages to the names people use, because `nbu.files` is not
a word anybody says. It is **data with a general fallback**: `humanize_package`
derives a name for anything not in the table, and a spoken name is verified against
the installed list before use. No logic anywhere branches on a specific app or
package — checked by grep, because a general solution is the point.

Device facts that cannot be derived live where device facts belong: in the tests, in
tables with fallbacks, or in `docs/android-apis.md` with the measurement beside them.

## 11. `run_task` is one long function on purpose

About 350 lines of code and as many again in comment, which is longer than anything
else here by a factor of three. A reader's first instinct is to split it, and the
docstring now says why that has not been done: every phase reads and writes the same
handful of locals, several of them can end the run, and the stop rules that live
among them are the rules most easily broken by a rearrangement. The tests would
probably catch a mistake; this project has a documented history of tests that did
not, and of loop changes that made three scenarios worse while making the code
tidier.

What it has instead is a map: the docstring lists the phases and their order, so a
reader knows what they are walking through. Where a phase *could* be lifted out
without carrying the run's state with it, it has been — `_a_reading`,
`with_a_picture_of_the_screen`, `a_screen_whose_tree_cannot_be_read`,
`the_postcondition`, `how_many_in_a_row_changed_nothing`, `how_many_repeated_here`
and the whole of `options.py` and `state.py` are all pieces of it that left.

---

## 12. Continuous integration has no phone, and no emulator either

The runner is a container with nothing attached to it. The obvious suggestion is to
give it an Android emulator, and it does not work here for two separate reasons.

The first is hardware. The emulator needs a kernel virtual-machine device to boot an
Android image at usable speed, and a container on this host has none — the runner sits
inside Docker on a machine whose kernel is not Linux. There is no configuration of the
workflow that fixes that.

The second is more interesting, and it would still apply on a Linux runner with
acceleration. The scenarios are written against a real phone with Google's
applications on it: the catalogue opens Chrome and searches, opens YouTube and plays a
result, deletes a photo from Photos, finds an app in the Play Store. An emulator image
either has none of those or has a substitute that behaves differently, so those
scenarios would fail for the absence of an application rather than for a defect in
this code. A job that is red for a reason nobody can act on trains people to ignore
it, which is worse than the coverage it appears to add.

So the split is deliberate. Integration runs everything that needs no hardware, and
the device suite runs on the machine that has the phone, through `scripts/check.sh`,
which is also where the coverage gate lives — a fast-suite-only measurement reports
81% because most of this package only executes with a phone attached.

**The lesson this cost.** For four commits, main was red with three of them unrelated
to the change that broke it. The suite passed on the author's machine the whole time,
because that machine has adb installed and a decision-model key configured, and the
runner has neither: the failure was a test that reached for adb before it checked
whether a key existed, so on the runner it failed instead of testing what it claimed.
Nothing about that was visible locally.

Two things now prevent it, and the second is the one that matters:

- `run_task` checks the decision model before it acquires the phone, because that is
  the cheaper question and the more useful answer when both are missing.
- `scripts/check.sh --as-ci` runs the fast suite as a machine that has never been set
  up — no adb on the path, no key, and a home directory of its own that holds neither.
  Continuous integration runs the same script, so the two cannot drift into different
  commands.

The general rule it encodes: a check that only passes on a machine somebody has
configured is not a check, and the way to find out is to run it on a machine nobody
has.

---

## What was built, by size

| | files | lines |
| --- | --- | --- |
| library (`src/phone_control`) | 19 | 7,656 |
| tests (`tests/`) | 50 | 14,492 |
| documentation, written by hand | 12 | 3,465 |
| the skill copied to each agent's own path, generated and checked in | 5 | 641 |

The documentation row is the README, `docs/`, `CONTRIBUTING.md`, `SECURITY.md` and
the canonical skill. It is counted separately from the generated copies because
those are build output that happens to be committed — `AGENTS.md`, `GEMINI.md`,
`.github/copilot-instructions.md` and the two files under `.claude/` are all
rendered from `skills/android-phone-control/SKILL.md`, and editing one is a
mistake the drift check will catch.

`tests/test_the_size_tables_are_true.py` measures the repository and fails if these
numbers stop matching, along with every line count in `docs/code-map.md` — because
a table like this is wrong the moment after it is written, and nobody notices.

Tests are nearly twice the library. That is not a boast — it is where the cost of
"the report must be trustworthy" lands. Most of the test volume is the scenario
catalogue and the recorded transcripts, which exist because a fake can only repeat
what its author believed, and this project's beliefs about phones have been wrong
repeatedly and expensively.
