<!-- Generated from skills/android-phone-control/SKILL.md by scripts/render_skill_for_agents.py. Edit that file, not this one. -->

> Read by the Gemini CLI.
>
> The instructions below are written for any agent. The phone is driven
> through an MCP server, so any client that speaks MCP can use it: the
> tools appear here under the `android` server namespace as
> `mcp__android__read_screen` and so on, and your client may present that
> name differently.

# Doing things on an Android phone

Say what you want done. The phone works out how.

The phone is attached over USB. **You do not need to know anything about how it
is driven** — no screen-reading, no tapping, no accessibility trees. That is the
server's job, and a decision model inside it works out each step and tells you
when the instruction is complete.

## State the goal

Call `run_task` with the outcome you want, in plain language:

```
run_task("open the email app")
run_task("turn on aeroplane mode")
run_task("set an alarm for 7am")
run_task("send a text to Alice saying I'll be late")
```

That one call replaces a whole back-and-forth. The phone reads its own screen,
decides what to do, does it, checks the result, and repeats until the goal is
reached or it cannot get there — then reports back.

**Phrase the goal as the outcome, not the steps.** "open the email app", not
"tap the Gmail icon". You are describing what should be true when it finishes.

**Keep it short, and ask for one thing at a time.** A goal that names the app and the
thing you want, and no more, is the one that works:

```
run_task("open the Play Store")
run_task("search for YouTube")
run_task("open YouTube")
```

Three calls, each waiting for the one before it, beat a single request that spans
them: "find YouTube in the app store and install it" asks the phone to hold a plan
across several screens, and leaves you with one report to guess at instead of three
you can read.

Multi-step work is fine — send the steps one at a time, and send the next once the
one before has come back. When something fails you will know which step it was, and
so will the person you are working for.

`max_steps` bounds how much it will try. Leave it alone unless the task is
genuinely long.

## Read the report

`run_task` returns what happened, and the two fields that matter most are at the
top:

| Field | Meaning |
| --- | --- |
| `achieved` | Whether the goal was reached. **Trust this, not the phrasing.** |
| `outcome` | How it ended, as a name: `done`, `nothing_helps`, `stalled`, `step_limit`, `not_carried_out`. Branch on this rather than reading `reason`. |
| `summary` | One sentence you can relay to the user as-is. |
| `reason` | Why it stopped — especially useful when `achieved` is false. |
| `steps` | Every action taken, in order, with what decided each one. |
| `final_foreground_app` | What ended up in front, for your own sanity check. |
| `goal_achieved_probability` | How likely it judged the goal already met, asked separately from the action it chose. A low number on a failed run says it knew it had not got there. |
| `decided_by` | Which decision model ran, so you know what to expect. |

**`achieved` is not one model's opinion.** The work is only declared done when
the chosen action says so *and* a separate question about the same screen agrees.
If the two disagree the run carries on, because a wrong "done" ends everything and
reports success that never happened.

**A run that fails says so.** It does not claim success for something it could
not do. If `achieved` is false, `reason` says why — most often that it could not
get there from the screen it reached, or that it was about to repeat itself.

## If it comes back unfinished

Say so. `achieved` false with `reason` is the honest answer, and the report names
the screen it stopped on and every action it took — which is what the person
holding the phone needs in order to take it from there, and what you need in order
to explain what happened.

Report it rather than trying the job again by hand. There is no set of smaller
tools to reach for: the phone is driven by describing the outcome, and working out
which control to touch from a screen is the server's job. An agent that starts
choosing controls instead is doing that job with less of the information.

## Things worth knowing

**The phone is real and someone is holding it.** Sending a message, deleting
something or spending money has consequences. Say what you are about to do
before you do it, and do not take an instruction further than it was given.

**A locked phone cannot be worked around.** If the run reports the phone is
locked, ask the user to unlock it. A PIN cannot and should not be entered for
them.

**Non-ASCII text cannot always be typed as keystrokes.** If a goal involves
accented text, emoji or a non-Latin script and it fails, that is usually why —
say so rather than retrying.

**A goal the phone cannot do will be reported as such.** If the user asks for
something no phone can do, `achieved` will be false. Relay that; do not invent a
success.

## When something fails

Failures come back with a plain explanation and, where there is one, a concrete
next step. Relay it rather than retrying blindly.

Two need the user's hands on the phone, so say so plainly instead of retrying:

- **no Android device is attached** — the cable is unplugged, or USB debugging
  has been switched off
- **the phone has not authorised this computer** — it is showing an *Allow USB
  debugging?* prompt that needs a physical tap

## Full reference

[`skills/android-phone-control/references/tools-reference.md`](skills/android-phone-control/references/tools-reference.md) has every tool
with its arguments, and the keys `press_key` accepts.
