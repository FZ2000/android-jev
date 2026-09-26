# Debugging this

A run goes wrong and you have a sentence of explanation. This is how to get from
there to the cause, and what to look at when the answer is not obvious.

The order matters. Each step below narrows the question, and skipping to the
third one is how an afternoon gets spent fixing the wrong thing.

---

## 1. Ask the phone, not the run

The run's report says what it **claimed**. The phone says what **happened**, and
they disagree more often than either is wrong about the other.

```bash
.venv/bin/python -m pytest -m device -q          # does the phone still work?
.venv/bin/python scripts/run_scenarios.py --id open-settings   # one scenario, with its own verdict
```

Every scenario carries its own ground truth, read from the phone, and it is
checked in both directions: a run that claims success over an untouched phone
fails, and so does a run that reports failure over a phone that plainly did the
job. That second one is not hypothetical — two photo scenarios failed that way
and nobody noticed for as long as the assertions only looked at the device.

## 2. Turn on the record, then reproduce

A run leaves nothing behind unless you ask it to. Ask:

```bash
export PHONE_CONTROL_RUNS=~/.phone-control/runs
export PHONE_CONTROL_RUN_SCREENSHOTS=1     # optional, and the expensive part
```

Now every `run_task` writes a folder, and the tool's reply names it. Then
reproduce the failure and read the folder, which is described in its own
`README.txt` so it does not depend on this document.

For a scenario, the folder is `artifacts/scenarios/<id>/` and it holds more:

| file | what it answers |
| --- | --- |
| `report.txt` | the whole story on one screen: every stage, every step, every complaint |
| `run.json` | the same as JSON, for a script |
| `exchanges.jsonl` | what the loop asked its decider, and what it was told |
| `run/decisions.jsonl` | **what went to Jev, questions included**, and every probability back |
| `run/steps.jsonl` | the action, its two numbers, and whether the screen changed |
| `run/timings.txt` | where the seconds went |
| `stage-NN.jpg` | the screen, including after every single step |

## 3. Read it in this order

**`report.txt` or `run.json` first — what happened.** `achieved` is the claim,
`reason` says why it stopped, and `final_screen` says what the phone looked like
at the end.

**Then `steps.jsonl` — what it did.** The field to scan for is
`changed_the_screen`. A `false` marks a step that had no effect at all, and a run
that starts going wrong usually shows it here first. Two false steps against the
same action is a loop.

**Then `run/decisions.jsonl` — what it was thinking.** This is the one that
settles arguments, because it is the request as sent and the answer as received,
not a reconstruction of either. For the step that went wrong, look at:

- `questions.next_action.criteria` — the options it was offered. Are they
  mutually exclusive? Two that mean the same thing always read as doubt, because
  confidence measures how concentrated the choice is.
- `questions.next_action.instructions` — the question itself. A wrong answer to a
  badly worded question is not the model's fault.
- `state` — what it could see. Nearly every "why did it not know that" is here:
  the fact was not in the state, so it could not act on it.
- `answers` — the whole probability distribution, not just the winner. A winner
  at 0.3 with three options near it is a coin flip dressed as a decision.

**Then the numbers.** `confidence` is how sure it was of its own ranking;
`probability` is how likely the picked option was the best one. They are
different questions. Neither gates anything, so a run that stopped on a low
number anywhere other than `finish` is a bug worth reporting.

**Then `timings.txt`.** If a run is slow, this says which phase to fix, and on
this project the answer is not the model:

```
step total 7.29s   (reading_the_screen 2.91s  watching_the_result 3.18s
                    settling 0.80s  deciding 0.26s  acting 0.15s)
```

Jev is 3% of a step. Reading the phone is 84%, because the loop reads it twice:
once to decide and once to see what the action did.

**Then `stage-NN.jpg`.** Some things are only visible: a permission dialog, a
keyboard covering a control, a screen that never changed. If the run's account
and the picture disagree, the picture is right.

## 4. Re-decide it without a phone

This is the step that makes the rest cheap. A recorded step can be re-decided
offline, in seconds, for free of the phone entirely:

```bash
.venv/bin/python scripts/replay_run.py artifacts/scenarios/open-settings      # all steps, one line each
.venv/bin/python scripts/replay_run.py artifacts/scenarios/open-settings --step 0
.venv/bin/python scripts/replay_run.py ~/.phone-control/runs/<folder> --diff
```

`--step 0` prints the options, the instructions and the answer for that one
decision. `--diff` prints only the steps whose answer moved, and exits non-zero
when any did, so a change to an option set or a question can be judged in a loop.

What it holds still is the point. The screen cannot be re-read, so the state that
produced a bad decision stays exactly as it was — which means the only thing that
can move the answer is the thing you are changing.

## 4b. Prove a reading against what the phone really said

`tests/recorded/` holds adb's own bytes, captured from a real phone and replayed in
the default suite with nothing attached. It exists because every other fake here
answers from something a person wrote down, and those beliefs have been wrong before
— about a bare domain opening a browser, about three spoken app names, about which
node carries a switch's position.

```bash
.venv/bin/python scripts/record_transcripts.py            # needs a phone; rewrites them all
.venv/bin/python scripts/record_transcripts.py status     # or just one
.venv/bin/python -m pytest tests/test_replaying_what_the_phone_said.py -q   # replays them, no phone
```

Re-record when the recordings stop being true: a new command was added to the code,
or a phone's output changed shape. The suite will say so — a replayed command that
was never recorded raises `NotRecorded` rather than answering with an empty string,
which is the rule that makes the layer worth anything. An empty answer would agree
with every question asked of it, including the wrong ones.

One thing is worth knowing before you are surprised by it: the dump file a screen is
read from is named with the process id and a random suffix, so it differs every run.
Both the recording and the replay normalise that part away, which is why the one
transcript of a screen read works at all. Nothing else is normalised.

## 5. Reasons a run fails, and where each one lives

| what you see | most likely cause | where to look |
| --- | --- | --- |
| picked something that could not be carried out | an option was offered whose value the device cannot act on. The confidence on that step is usually fine, because the model was not at fault | `criteria` for the step; if the option takes a value, check it against the device |
| repeated the same action until the budget ran out | nothing tells it the action did nothing. It is asked afresh each step, so `recent_actions` has to say so | `state.recent_actions` in `decisions.jsonl` |
| went round two screens until the budget ran out | a cycle. It **is** detected - `how_many_repeated_here` catches a return to a screen with an action already taken there, and the run stops saying so - but detection does not redirect the run, so the budget can still be spent before the rule fires | `steps.jsonl`, two actions alternating |
| stopped while obviously unfinished | it answered `finish` too early, or a screen it could reach was not offered | the last `answers.next_action` and `criteria` |
| confident and wrong | the state did not carry a fact it needed. Confidence measures concentration, not correctness | `state` — find the missing fact |
| slow | screen reading, not the model | `timings.txt` |
| claimed success over an untouched phone | the harness should catch this. If it reached you, the ground-truth probe is looking at something the run also produces | the scenario's `looks_done` |

## 6. When it is the phone, not the code

```bash
adb devices                          # is it attached and authorised?
adb shell uiautomator dump /sdcard/x && adb shell cat /sdcard/x | head -c 400
.venv/bin/python -m pytest -m device -q
```

`docs/android-apis.md` is the record of what this device actually does, including
the things that cost runs to learn: a dump that is written where we do not read
it, a `cmd clipboard` that exits zero while doing nothing, a `dumpsys` field that
moved between Android versions.

## 7. Checking the checks

The per-decision checks are the instrument every scenario is judged with, so one
that never fires would make everything pass vacuously. Each is tested against a
deliberately bad exchange and has to complain:

```bash
.venv/bin/python -m pytest tests/test_decisions_and_checks.py -q
```

If you add a check, add its negative control in the same file. A check that
cannot fail is a comment.

## What is deliberately not recorded

The clipboard, and anything the phone marks as a password, never enter the state,
so they cannot enter a folder. The state does carry the text of the screen, which
on a page someone typed into may be their own words. Treat a run folder like a
screenshot of the phone: useful, private, and worth deleting once read.

## Before believing the harness, believe the phone

A scenario's verdict comes from a probe, and a probe is code like any other. When a
scenario says **"the run reported failure over a goal that was reached"**, that
sentence has two possible authors and the message does not distinguish them:

    the run under-claimed      it did the thing and did not say so
    the probe is too lax       it never did the thing and the probe said it had

The second is the cheaper mistake to have made and the more expensive to have
believed. Three rounds of this project went into loop changes - a re-observe rule, a
threshold, an idle-rule fix - for runs that had genuinely not reached their goal and
were reporting that correctly:

    youtube-open-a-result      probe accepted any of "subscribe", "views", "shorts"
                               on screen. All three appear on YouTube's *home*.
                               Measured at the end of the failing run: the window
                               was Shell$HomeActivity with "mrbeast" still in the
                               search box. No video was ever opened.
    files-browse-downloads     probe accepted the word "download" anywhere. The run
                               ended on HomeActivity showing Recents and a camera
                               photo.

Both are the first bug this project ever had, wearing a different hat: *text presence
is not evidence that a page loaded.* A word that appears on every screen of an app
distinguishes nothing, and `any_of(screen_shows(...))` over a handful of common words
is a probe that cannot fail.

**What to check, in order.**

1. The window. `read_focused_window` names the activity and needs no idle state, so
   `the_window_is("WatchWhile")` is a far stronger claim than any word on the screen.
   Prefer it wherever the goal is "be somewhere".
2. Whether the acceptable words appear on the *starting* screen too. If they do, the
   probe cannot tell success from doing nothing.
3. The final state in `run/decisions.jsonl`. It is the screen the run was actually
   looking at, and it answers the question in seconds.

The harness now says so in the complaint itself, because it did not and that cost
three rounds.

## The stage record and the decision record do not line up

The scenarios write three records of one run, and pairing the first with the third by
index is wrong. Measured on `open-calculator`, a run that was fulfilled and claimed
so:

```
run/decisions.jsonl   step 0  open_app  completion 0.02
                      step 1  finish    completion 0.96      <- the goal was met
run/steps.jsonl       step 0  open_app
run.json  stages      after_step=0  met=False   (before the run)
                      after_step=1  met=False
                      after_step=2  met=True
                      after_step=2  met=True    (after the run)
```

The decision that scored 0.96 - the one that ended the run as done, on a screen
showing the calculator - is paired by index with a stage saying the goal was **not**
met. There are three "before" stages and two decisions, and the two records number
themselves independently: the stage counter counts samples, and the decision counter
counts decisions.

**What this costs.** Any diagnosis that asks "what did the decider answer on the
screens where the goal was met" by matching `stages[i]` to `decisions[i]` gets a
misaligned answer, and the misalignment is silent - it produces a plausible spread of
numbers rather than an error. It did exactly that here: harvesting 219 answers across
47 runs and splitting them by the stage verdict said the completion question was
*anti*-correlated with the truth (goal met, mean 0.21; goal not met, mean 0.21, and
worse when restricted to the stricter probes), which would have been a headline
finding about the question. It was an artefact of the pairing, and it was caught only
by printing one run's three records side by side.

**Until this is fixed**, pair them the other way that is available and cheap: the
stage record's own `when` field and the step's own numbers in `steps.jsonl`, or read
one run end to end as above. Do not match by index across the two files.

**Fixed, and the key is the observation.** The loop already puts its own index for each
reading in the state it hands the decider, and `decisions.jsonl` keeps that state; the
stage record now keeps it too, as `observation`, and it is `None` for the two stages no
decision was made from. Join on that.

Re-measured on the first eight runs recorded after the fix, joined properly, the
completion question separates the way it was originally believed to:

```
goal met    n=8   mean 0.95   0.92 0.94 0.95 0.95 0.96 0.96 0.96 0.96
goal unmet  n=8   mean 0.03   0.02 0.02 0.02 0.02 0.02 0.03 0.03 0.04
```

At the threshold in the code - 0.90 - that accepts all eight of the finished screens
and none of the unfinished ones. So the misaligned harvest did not merely produce a
wrong number, it produced one that would have overturned a threshold which is working,
and the case for touching `COMPLETION_TRUSTED` rested on readings taken through the
same broken pairing. It stands.

What it does not explain is `files-browse-downloads`, where a screen that was finished
answered 0.63 rather than the 0.92-and-up this set shows. That is one screen, it was
measured before the probes around it were trustworthy, and it is worth one more look
before it is credited.

### What the completion question actually scores, over a whole catalogue

199 screens harvested from one full run of all 47 scenarios, joined on the observation,
and labelled by the stage verdict. Two corrections were needed to the harvest itself,
and both changed the answer:

- **A stage is only "finished" if the states the scenario insists on seeing have been
  seen as well.** Labelling by `goal_met_here` alone credits every screen of a scenario
  whose probe is true at the start - the two toggles each contributed a handful of
  "finished" screens that nothing had been done on, scoring 0.03. Mean 0.52 became 0.63.
- **Scenarios whose probe looks for a word anywhere on screen are labelled by a probe
  this project has already shown to be lax.** Dropping them leaves labels that can be
  trusted, and the numbers move again.

With those labels, over the strict-probe scenarios:

```
goal met    n=28   mean 0.76   median 0.92   12 of 28 below 0.90
goal unmet  n=98   mean 0.08   median 0.03    2 of 98 at or above 0.90
```

**The question works, and the threshold is conservative.** At 0.90 the loop accepts
16 of 28 genuinely finished screens and 2 of 98 unfinished ones: it misses **43%** of
the finished ones and is wrong about 2%. That is the under-claim that has been showing
up as "the run reported failure over a goal that was reached" - not a model that cannot
see, and not a state that hides the evidence, but a bar set high enough to refuse
nearly half of what it is shown.

**What is not done.** Lowering `COMPLETION_TRUSTED` is the obvious move and it is not
made here, because the number that decides it has not been measured: how many of the 98
unfinished screens would be accepted at 0.80 rather than 0.90. Two out of ninety-eight
at 0.90 is cheap; whether it stays cheap a tenth lower is the whole question, and
answering it from this corpus is a few lines rather than another run. Do that before
moving the bar.

### The threshold change did what it measured, and moved no scenario

`COMPLETION_TRUSTED` went from 0.90 to 0.80 on the distribution above. A full catalogue
run afterwards says the mechanism works and the tally does not care:

```
same 30/45, nothing recovered and nothing lost
finished screens this run: 33, of which 4 sat in the 0.80-0.90 band:
    bluetooth-off 0.84   clock-world-clock 0.86   wi-fi-on 0.85   youtube-search 0.89
```

So four screens that 0.90 refused are now accepted, exactly as the distribution
predicted. Not one scenario changed outcome, because those four screens belong to
scenarios that were already fulfilling - an earlier `finish` on a screen that satisfies
the probe is still the same probe satisfied.

The lesson is about what the tally measures, not about the threshold. **A run ending
earlier as done only becomes a fulfilled scenario if it happens to stop on a screen the
probe accepts**, and most of the under-claiming screens - 0.02, 0.04, 0.11, 0.18, 0.21
in that list - are nowhere near any threshold worth setting. The bar was refusing more
than it protected and is now better placed, and the scenarios still failing are failing
for reasons a threshold cannot reach.

### Three probes are satisfied before the work is done

The low tail of the finished distribution turned out to be labels, not under-claims.
Eight screens labelled "goal met" and scoring under 0.25 all came from three scenarios
whose probe is true the moment the app opens:

| scenario | probe | answered |
| --- | --- | --- |
| `browser-search-then-open` | Chrome in front **and the address bar does not hold** the query | 0.02 - 0.11 |
| `youtube-search` | YouTube in front and nothing is holding an unsent query | 0.04 |
| `bluetooth-on` | `bluetooth_on` reads 1, which it already did | 0.18, 0.21 |

Every one of those is a probe for something being *absent*, and absent is the state
before anything has happened. The decider answered 0.02 on all of them and was right
every time - which is how they were caught, because a model that is confidently wrong
eight times in a row is a much stronger signal than one that is unsure.

They are the same fault as the self-satisfying scenarios the harness now refuses to
count, in a form that check cannot see: not finished *at the start of the run*, but
finished at a step in the middle of one.

Anyone harvesting labels out of `run.json` has to exclude these, or the met group fills
with screens that are not met and every number computed from it is too pessimistic.

### The table, on labels the question is actually for

Two more exclusions are needed before the completion numbers mean anything, and one of
them is a class rather than a fault.

**Fourteen scenarios carry `needs_a_reader=True`**, and their own notes say why: the
screen cannot show whether the goal is met - a settings switch's position is not
published to the accessibility tree - so the reader is what concludes it. A low
completion answer there is the expected behaviour, not an under-claim, and leaving them
in the sample punishes the question for a job it was never given. `bluetooth-on` was
read as a contaminated label in the previous round; it prepares by turning Bluetooth
*off*, so the label was right and the low answer was the documented limitation.

**Seven scenarios have a probe that looks for a word anywhere**, already excluded.

What is left is the set the question is for:

```
goal met    n=26   mean 0.62   median 0.88
goal unmet  n=60   mean 0.06   max 0.41     <- nothing unfinished scores above 0.41
```

| threshold | finished accepted | unfinished accepted |
| --- | --- | --- |
| 0.95 | 7/26 (27%) | 0/60 |
| 0.90 | 12/26 (46%) | 0/60 |
| 0.85 | 14/26 (54%) | 0/60 |
| 0.80 | 14/26 (54%) | 0/60 |
| 0.70 | 16/26 (62%) | 0/60 |

Two things follow. On this evidence **a lower threshold costs nothing at all** - not one
unfinished screen scores anywhere near it - so 0.80 is if anything still cautious. And
the finished group is bimodal, which is the part worth chasing: eight samples scored
under 0.1 against a median of 0.88.

**Those eight are entirely accounted for by the two probes with the absent-style fault**,
seven from `browser-search-then-open` and one from `youtube-search`. The first was
repaired in round 33 - the artifacts above predate it - and the second is still to do.
So the low tail is not a property of the question, and it should disappear from the next
catalogue rather than be explained away.

## Auditing the probes by machine

Three rounds of triage each ended at a probe rather than at the loop, and every one was
found by hand while chasing something else. `scripts/audit_the_probes.py` looks for the
shapes by machine and needs no phone:

```bash
.venv/bin/python scripts/audit_the_probes.py          # what it found
.venv/bin/python scripts/audit_the_probes.py --all    # every scenario
```

It found 12 of 47. Reading them, the three shapes are **not equally trustworthy**, and
the difference matters:

**Probes for something being absent** - `youtube-search`, twice. These are faults, and
they are the ones this project has actually paid for: an empty search box satisfies
`screen_holds_nothing_unsent` before anything is typed.

**A window no recorded run has ever been in front of** - six scenarios:
`MyDeviceInfo`, `AppsDashboard`, `Battery`, `NetworkDashboard`, `StorageDashboard`,
`SearchResults`. This is a **question, not a verdict**. Five of the six are Settings
pages the runs only ever reach by tapping, which puts them in a generic `SubSettings`; the
window name would appear if the page were started directly, so what the flag really says
is that no run has ever got there that way. `settings-about-phone`, `settings-apps-list`
and `settings-network` also have a picture route now, so the stale window name inside
their `any_of` is harmless.

`SearchResults` is the exception and the reason the check is worth running at all: that
one is **proven impossible** by direct measurement rather than inferred from the corpus -
YouTube reports `Shell$HomeActivity` on its home screen, while typing and after
submitting, so no run could ever have seen it (docs/android-apis.md).

**Starts in its own end state** - five scenarios, and the harness already refuses to
count these. `keep-a-note` is there because Keep is signed in and restores notes from
earlier runs, one of which says "a note saying hello" and so contains "hello".

The lesson to carry is the middle one. A corpus can tell you a probe has never matched;
it cannot tell you whether that is because the probe is wrong or the runs are, and the
two want opposite responses. Only a measurement on the phone separates them.

### A scenario can still spend ten minutes on one page

Measured while fixing the crash above, and left unfixed because it needs its own run to
verify: `clock-alarms` and `clock-world-clock` were killed at ten minutes with **no steps
recorded at all**, so the time is all spent before the first decision.

Two readers are involved and both retry. The harness's `the_screen` makes five attempts
with a settle between them, and `_sample` calls it once for the scenario's verdict and
once per required state - so a stage can be several of those. The loop's reader used to
make two more, with its own helper recovery on top.

The loop's side is now one cheap attempt, and the crash is gone. The harness's side is
untouched and is the driver: five attempts at roughly twenty-five seconds a failed dump
is over two minutes for one probe, multiplied by the probes in a stage.

**What is not established is whether the dump fails here with the idle-state words or
without them.** `the_screen` breaks out early on "could not get idle state" and falls
back to a picture, which is cheap; if these pages fail some other way, every attempt is
paid in full. That is the first thing to check, and it is one command against a phone.
