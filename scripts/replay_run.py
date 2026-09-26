"""Re-decide a recorded step, without a phone.

A run folder keeps every request and every answer. This takes one request back
out of it, asks Jev the same question again, and prints what changed. No phone,
no screen reading, no waiting: a state or options change can be judged in
seconds, against the real decision that was really made at the time.

    scripts/replay_run.py <run folder>                    every step, one line each
    scripts/replay_run.py <run folder> --step 2           one step, in full
    scripts/replay_run.py <run folder> --diff             only where the answer moved

The value is in what it holds still. The screen cannot be re-read, so the state
that produced a bad decision stays exactly as it was: a change to the options or
the instructions is the only thing left that can move the answer, and that is
precisely what a debugging session is trying to change.

    # does the new wording of the action set fix this screen?
    scripts/replay_run.py runs/2026-09-25-1142-turn-off-bluetooth --diff

Exit codes: 0 when every replayed answer matched, 1 when any moved, 2 on a bad
argument. That makes it usable as a check in a loop that is tuning an interface.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from phone_control.jev import JevClient  # noqa: E402


def the_records(folder: Path) -> list[dict]:
    """Every request in a run folder, in order."""
    source = folder / "decisions.jsonl"
    if not source.exists():
        raise SystemExit(
            f"{folder} has no decisions.jsonl.\n"
            "A run only writes one when PHONE_CONTROL_RUNS was set before it ran."
        )
    records = []
    for line in source.read_text(encoding="utf-8").splitlines():
        if line.strip():
            records.append(json.loads(line))
    return records


def the_answer(record: dict) -> str:
    """What Jev chose, or the clearest thing available when it said nothing."""
    answers = record.get("answers") or {}
    chosen = answers.get("next_action") or answers.get("kind") or {}
    return str(chosen.get("choice", "(no choice)"))


def the_numbers(record: dict) -> str:
    """The two numbers, plus the completion answer, on one line."""
    answers = record.get("answers") or {}
    chosen = answers.get("next_action") or answers.get("kind") or {}
    probabilities = chosen.get("probabilities") or {}
    picked = chosen.get("choice")
    parts = []
    if picked in probabilities:
        parts.append(f"probability {probabilities[picked]:.2f}")
    if chosen.get("confidence") is not None:
        parts.append(f"confidence {chosen['confidence']:.2f}")
    finished = answers.get("goal_achieved") or {}
    if finished.get("noul") is not None:
        parts.append(f"goal_achieved {finished['noul']:.2f}")
    return "  ".join(parts)


async def a_fresh_answer(client: JevClient, record: dict):
    """Ask the same question of the same state again."""
    return await client.ask(record["state"], record["questions"])


def show_one(record: dict, fresh) -> bool:
    """Print one step in full, and say whether the answer moved."""
    before = the_answer(record)
    after = fresh.choice("next_action").option
    moved = before != after

    print(f"step {record['step']}")
    print(f"  it chose then : {before}")
    print(f"  it says now   : {after}")
    print(f"  the numbers   : {the_numbers(record)}")
    if moved:
        print("  MOVED")
    print(
        f"  the options offered ({len(record.get('questions', {}).get('next_action', {}).get('criteria', {}))}):"
    )
    for name, description in (
        record.get("questions", {}).get("next_action", {}).get("criteria", {}) or {}
    ).items():
        mark = "*" if name == before else " "
        print(f"   {mark} {name:<22} {str(description)[:88]}")
    print(
        f"  asked: {record['questions'].get('next_action', {}).get('instructions', '')[:200]}"
    )
    return moved


async def replay(folder: Path, only: int | None, differences_only: bool) -> int:
    client = JevClient()
    if not client.is_configured:
        raise SystemExit("no Jev key is configured, so nothing can be re-decided")

    records = the_records(folder)
    if only is not None:
        records = [one for one in records if one["step"] == only]
        if not records:
            raise SystemExit(f"{folder} has no step {only}")

    moved = 0
    for record in records:
        fresh = await a_fresh_answer(client, record)
        if only is not None:
            if show_one(record, fresh):
                moved += 1
            continue
        before = the_answer(record)
        after = fresh.choice("next_action").option
        if before == after:
            if not differences_only:
                print(f"  step {record['step']:<3} {before:<22} unchanged")
            continue
        moved += 1
        print(f"  step {record['step']:<3} {before:<22} -> {after}   MOVED")

    print(
        f"\n{len(records)} step(s) replayed, {moved} answer(s) moved"
        + ("" if moved == 0 else "  (a moved answer is what a change is meant to do)")
    )
    return 1 if moved else 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("folder", type=Path, help="a run folder to re-decide")
    parser.add_argument("--step", type=int, help="only this step, shown in full")
    parser.add_argument(
        "--diff",
        action="store_true",
        help="only steps whose answer moved, for a loop that is tuning an interface",
    )
    args = parser.parse_args()

    if not args.folder.is_dir():
        raise SystemExit(f"{args.folder} is not a directory")
    return asyncio.run(replay(args.folder, args.step, args.diff))


if __name__ == "__main__":
    raise SystemExit(main())
