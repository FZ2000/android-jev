# The tool contract

Why this server's tool surface is shaped the way it is, and what the measurements
say. Written as a specification before any of it was built; **"Where the code is
today" at the end says what shipped and the one place it deliberately did not.**

Everything below was measured against `mcp` 2.2.0 by running this server through
the official in-process client, or read from the SDK source. Claims that are not
verified say so.

---

## The one bug worth fixing first — fixed

**Failures used to look like successes.** Kept here because the reasoning is the
part worth keeping, and because the fix has a shape worth copying.

`_turns_failures_into_answers` catches a `PhoneControlError` and returns a string,
so the result carries `is_error: false` and `structured_content: {"result": "…"}`.
Measured:

```
scroll({'direction': 'sideways'})
   is_error          : False
   structured_content: {'result': 'direction must be one of: down, up, left, right.'}
```

To every client UI, and to the model, that was a tool that worked and whose answer
was a sentence. The SDK documentation is explicit:

> **Never** `return` an error message from a tool. A returned string has
> `is_error=False`, so to the model (and to every client UI) it looks like the
> tool worked and that string was the answer. `raise`. The flag is the signal.

The fix is `raise ToolError(message)`, and it is in the code. All twelve tool
bodies that *returned* a failure string now raise one too — a returned failure was
the same defect arriving by another door. Verified mapping:

| Raised | Client sees | Model sees |
| --- | --- | --- |
| `ToolError("No control labelled 'Send' is on screen.")` | `is_error=True` | `Error executing tool tap: No control labelled 'Send' is on screen.` |
| `MCPError(...)` | an MCP error at the caller | nothing |
| `RuntimeError("secret")` | `is_error=True` | `Error executing tool tap` — the detail stays server-side |

`MCPError` is for conditions the model cannot fix (no phone attached, adb
missing). `ToolError` is for everything it can ("no control matched", "the
direction is not one of these"). The test: *could a smarter model have avoided
this?*

The `Error executing tool <name>: ` prefix is added by the SDK and cannot be
removed, so the message should read as a sentence continuing from it.

## The result shape

One schema covering success **and** failure, so an error still conforms:

```python
class Outcome(TypedDict):
    """Every result, success or failure."""

    ok: bool
    summary: Annotated[str, Field(description="One line a model can act on.")]
    error: NotRequired[
        Annotated[str, Field(description="Stable code, e.g. 'no_match'.")]
    ]
    fix: NotRequired[Annotated[str, Field(description="Concrete next step.")]]


@server.tool()
async def tap(
    target: Annotated[
        str, Field(description="Control number or label from read_screen.")
    ],
) -> Annotated[CallToolResult, TapOutcome]: ...
```

**Only `ok` and `summary` may be required.** This is not a style preference: the
MCP layer validates the returned value against the published schema, and a schema
with extra required fields **rejects** a minimal failure value with
`UnexpectedToolError` — so a tool declaring required fields cannot report a
failure at all. Verified by experiment. Every outcome type therefore sets
`total=False`.

Returning `CallToolResult` yourself is what makes this work; it is also the only
way to return an image *plus* structured metadata in one result, which
`take_screenshot` needs and `-> Image` cannot do (`-> Image` publishes no
`outputSchema` at all).

### Both channels, always

> `content` is for the **model** — this is the only part of the result it sees.
> `structured_content` is for the **application**. `output_schema` is the contract
> between them.

So a control listing goes in **both**: the JSON text block and `structuredContent`.
Putting it only in the structured channel hides it from the model.

## Arguments

**No parameter description reaches the schema today.** In 2.2.0 the description is
`description or fn.__doc__ or ""` with **no docstring parser**, so the `Args:`
blocks land verbatim in one description blob while every parameter publishes
`description: None`. Every parameter needs `Annotated[T, Field(description=…)]`.

| Change | Why |
| --- | --- |
| `Literal[...]` for `press_key.key`, `scroll.direction`, `scroll.distance`, `clipboard.action`, `transfer_file.direction` | Publishes a real `enum`, so a bad value is rejected by schema rather than by prose |
| Bounds on `max_width`, `wait_seconds`, `timeout_seconds`, `duration_ms` | Otherwise unbounded |
| **Split `tap` into `tap(target)` and `tap_point(x, y)`** | A signature cannot express "`target` unless `x` and `y`". Today `tap({})` is a valid call that fails at runtime — an argument contract the schema cannot state is better expressed as two tools |
| `detail`/`limit` on `read_screen` | Nineteen fields per control, every control, every read |

## Annotations

All 19 tools currently publish `annotations: None`, which makes `run_shell` look
identical to `status`. Declare `readOnlyHint`, `destructiveHint`, `idempotentHint`
and `openWorldHint` on every tool — and note the spec calls them **hints** that
clients MUST treat as untrusted, so they inform confirmation UI and nothing else.
Safety is enforced in code.

Two worth calling out: `read_notifications` is **not** read-only (it opens the
shade), and `run_shell` is both destructive and open-world.

## What the evidence says about the shape of the surface

Eighteen tools is not the problem — the schemas and the error flag are. But the
measurements are worth knowing:

- Tool definitions are expensive: GitHub's 35 tools ≈ 26K tokens; 5 servers ≈ 55K.
- GitHub cut its default set 101 → 52 (−49% tools, −53% tokens) citing *"tool
  selection degradation: LLMs struggle to choose the right tool from too many
  options"*.
- Copilot VS Code went 40 → 13 core tools for **+2–5 pp** accuracy and −400 ms.
- Descriptions alone moved a model to SOTA on SWE-bench Verified.
- Anthropic's guidance: say **what comes back**, not just what it does — and build
  `schedule_event` rather than `list_users` + `list_events` + `create_event`.

Two ideas from comparable servers that this one lacks: **`mobile_batch_commands`**
(mobile-mcp) consolidates a read→act chain into one round trip, and Playwright
MCP's opaque stable **`ref`s** cannot silently match the wrong control the way a
label can.

---

## Where the code is today

The specification above was written before any of it was built. This is what
actually shipped, and the two places it differs.

**Failures raise.** Done, in both forms. The decorator raises `ToolError`, and
`MCPError` for the conditions a model cannot fix; and the twelve tool bodies that
*returned* a failure string now raise one too. A returned failure was the same
defect arriving by another door, and the tests that asserted the old behaviour were
rewritten rather than deleted, because "the tool says so in words" was the thing
being checked. A test now goes through the same handler a client's request goes
through, because the flag lives at that layer and reading the inner call was why
the defect went unseen.

**Every argument is described.** Done: forty-six of them had no description at all,
so an agent reading the schema learned the names and nothing about what to pass.
A test fails on any parameter declared without one.

**The Outcome envelope is not built, and the reason is worth recording.** Its
purpose was one schema covering success *and* failure so that an error still
conforms. With failures raising, the failure half has no work to do: the error path
is the exception, and the SDK's error result is the shape. Where the envelope does
earn its place is a result a caller branches on, and `run_task` already returns it —
`ok`, `summary`, `outcome`, `reason`, `steps`, `fix`.

So the honest state is: the envelope exists where branching happens and nowhere
else. Extending it to all nineteen tools would replace prose that a model reads well
with JSON it reads no better, and that is a change worth making only with a
measurement behind it. It is recorded here as a decision rather than left as an
omission.

**The skill names one tool, and that is the contract.** It used to list all nineteen
tools and to send an agent to `read_screen`, `tap`, `type_text` and `take_screenshot`
in three named cases - a run that came back unfinished, a screen that is visual rather
than textual, and a need to inspect something part-way. That guidance is gone at the
owner's direction, and the reasoning is the same one that makes the envelope question
small: `run_task` is the interface, and an agent that starts choosing controls is
doing the server's job with less of the information the server had. Naming the tools
invited exactly the step-by-step driving this project spends its effort removing.

What it costs, recorded so the trade is not discovered later: for a screen whose own
reading is thin - a photo, a map, a CAPTCHA, an icon-only toolbar - an agent no longer
has a documented route, and the honest answer becomes "the phone could not do this"
rather than a screenshot the agent could read. The tools remain published and
documented in `references/tools-reference.md`; they are simply not what the skill
tells an agent to reach for. `tests/test_skill_adapters.py` holds the line in both
directions - `run_task` must be named, and the others must not be.

**Confirmed, and on a simpler ground than the argument above.** `run_task` is the
interface an agent uses; the rest exist for deliberate driving — a run that came back
unfinished, a screen that is visual rather than textual, a need to inspect something
part-way — which is a person working the phone by hand, not the path an agent takes
by default. So the absence of a success field on `run_shell` or `press_key` costs
nothing anyone will notice. The envelope is where the branching is, and the branching
is `run_task`.

**What was worth fixing, and has been.** The *shapes* were undocumented. Nineteen
tools return four different things - JSON, a text listing, an image, a sentence - and
the reference beside the skill listed arguments and effect but never said which. That
is not a theoretical gap: reading the code to write this section produced a wrong
count of it (twelve prose tools, not eighteen, and three that return JSON), so an
agent reading only the docs had no chance. `references/tools-reference.md` now has a
"You get back" column for every tool, and says plainly that a failure arrives as an
error rather than as an answer, with `run_task` alone carrying an `ok`.
