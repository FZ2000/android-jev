#!/usr/bin/env python3
"""Render the phone-control skill into the file each agent actually reads.

There is one canonical body, ``skills/android-phone-control/SKILL.md``. Every
other agent wants the same instructions in a different place, under a different
frontmatter, so this writes those files from it rather than keeping copies that
drift.

Run it after editing the canonical skill:

    .venv/bin/python scripts/render_skill_for_agents.py

``tests/test_skill_adapters.py`` re-runs the rendering and fails if what is
committed differs, so a copy cannot go stale unnoticed.
"""

from __future__ import annotations

import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parent.parent
CANONICAL_SKILL = REPOSITORY_ROOT / "skills" / "android-phone-control" / "SKILL.md"
REFERENCES = CANONICAL_SKILL.parent / "references"

GENERATED_NOTICE = (
    "<!-- Generated from skills/android-phone-control/SKILL.md by "
    "scripts/render_skill_for_agents.py. Edit that file, not this one. -->"
)


@dataclass(frozen=True)
class Adapter:
    """One file an agent reads, and the frontmatter it needs."""

    path: str
    frontmatter: str
    note: str
    # Where a reader of THIS file finds the reference, since a relative path that
    # works for a SKILL.md does not resolve from the repository root.
    reference_path: str = "skills/android-phone-control/references/tools-reference.md"


ADAPTERS = (
    Adapter(
        path="AGENTS.md",
        frontmatter="",
        note=(
            "Read by Codex, Cursor, Copilot's agent mode and a growing number of "
            "other tools, which is why it is the plainest of these files."
        ),
    ),
    Adapter(
        path="GEMINI.md",
        frontmatter="",
        note="Read by the Gemini CLI.",
    ),
    Adapter(
        path=".github/copilot-instructions.md",
        frontmatter="",
        note="Read by GitHub Copilot in this repository.",
    ),
    Adapter(
        path=".cursor/rules/android-phone-control.mdc",
        frontmatter=(
            "---\n"
            "description: Operating, driving or automating an Android phone over USB\n"
            "alwaysApply: false\n"
            "---\n"
        ),
        note="Read by Cursor. Attached on request rather than always, like the skill it mirrors.",
    ),
    Adapter(
        path=".claude/skills/android-phone-control/SKILL.md",
        frontmatter=(
            "---\n"
            "name: android-phone-control\n"
            "description: Use when the user asks to operate, drive, or control their "
            "Android phone from this computer — opening apps, tapping, typing, "
            "scrolling, reading the screen, taking screenshots, sending messages, "
            "filling forms, or automating anything on a USB-attached phone. Covers "
            "the mcp__android__* tools, choosing between the accessibility tree and "
            "screenshots, and Jev decisions for reliable taps.\n"
            "---\n"
        ),
        note="Read by Claude Code as a skill, with its reference file beside it.",
        reference_path="references/tools-reference.md",
    ),
)


def read_canonical() -> str:
    """The canonical body, without its own frontmatter."""
    text = CANONICAL_SKILL.read_text(encoding="utf-8")
    if not text.startswith("---\n"):
        return text
    closing = text.index("\n---\n", len("---\n"))
    return text[closing + len("\n---\n") :].lstrip("\n")


def render(adapter: Adapter, body: str) -> str:
    """One adapter file: a notice, any frontmatter, a note, then the body."""
    if adapter.path.endswith("SKILL.md"):
        # A SKILL.md is the canonical format already, so it keeps its own
        # frontmatter and needs no explanatory note.
        return adapter.frontmatter + "\n" + body

    note = (
        f"> {adapter.note}\n>\n"
        f"> The instructions below are written for any agent. The phone is driven\n"
        f"> through an MCP server, so any client that speaks MCP can use it: the\n"
        f"> tools appear here under the `android` server namespace as\n"
        f"> `mcp__android__read_screen` and so on, and your client may present that\n"
        f"> name differently.\n\n"
    )
    return (
        f"{GENERATED_NOTICE}\n"
        f"{adapter.frontmatter}"
        f"\n{note}"
        f"{body.replace('references/tools-reference.md', adapter.reference_path)}"
    )


def main() -> int:
    if not CANONICAL_SKILL.is_file():
        print(f"canonical skill not found: {CANONICAL_SKILL}", file=sys.stderr)
        return 1

    body = read_canonical()
    for adapter in ADAPTERS:
        target = REPOSITORY_ROOT / adapter.path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(render(adapter, body), encoding="utf-8")
        print(f"wrote {adapter.path}")

    # Claude Code resolves `references/tools-reference.md` beside its SKILL.md.
    claude_references = (
        REPOSITORY_ROOT / ".claude/skills/android-phone-control/references"
    )
    if REFERENCES.is_dir():
        claude_references.mkdir(parents=True, exist_ok=True)
        for reference in sorted(REFERENCES.glob("*.md")):
            shutil.copyfile(reference, claude_references / reference.name)
            print(
                f"wrote .claude/skills/android-phone-control/references/{reference.name}"
            )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
