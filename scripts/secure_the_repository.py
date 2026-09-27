#!/usr/bin/env python3
"""Check — and optionally set — the settings that make contributing safe.

These live on GitHub rather than in the checkout, which is the same shape of claim
this project keeps finding to be wrong: configuration outside the repository,
described in a document nobody diffs. `docs/repository-settings.md` explains what
each one is for and why it is set the way it is; this checks that the repository
agrees with it.

    python scripts/secure_the_repository.py --check    # report only, changes nothing
    python scripts/secure_the_repository.py --apply    # set what is missing, read back

A refusal is reported with GitHub's own words. Most of these are plan-gated on a
private repository — branch protection, rulesets, secret scanning, push protection
and private vulnerability reporting are all free once it is public — and a script
that hid that behind "unavailable" would teach nobody anything.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys

REPOSITORY = "FZ2000/android-jev"

# The protections `main` should carry, and the reason each is there. Kept beside the
# payload rather than in prose somewhere else, so the two cannot drift.
BRANCH_PROTECTION = {
    "required_status_checks": {
        # One required check, aggregating the rest, so the matrix can change without
        # anybody editing the rule.
        "strict": True,
        "contexts": ["ci-gate"],
    },
    # Applies to the maintainer too: a rule you can push through is one you will.
    "enforce_admins": True,
    "required_pull_request_reviews": {
        # A pull request is required; a human approval is not, because on a project
        # with one maintainer that means requiring somebody who does not exist. The
        # pull request is what gets CI to run on the merge.
        "required_approving_review_count": 0,
        "dismiss_stale_reviews": True,
    },
    "restrictions": None,
    "allow_force_pushes": False,
    "allow_deletions": False,
    "required_conversation_resolution": True,
    "required_linear_history": True,
}


def github(
    *arguments: str, method: str = "GET", payload: dict | None = None
) -> tuple[int, str]:
    """Ask GitHub. Returns the status code and the raw body, never raising on a refusal."""
    command = ["gh", "api", "-X", method, *arguments]
    if payload is not None:
        command += ["--input", "-"]
    result = subprocess.run(
        command,
        input=json.dumps(payload) if payload is not None else None,
        capture_output=True,
        text=True,
        env={**os.environ, "GH_TOKEN": os.environ.get("GH_TOKEN", "")},
    )
    return result.returncode, (result.stdout + result.stderr).strip()


def the_reason(body: str) -> str:
    """GitHub's own sentence, which is the only useful thing to report.

    `gh` appends its own summary after the JSON, so the body is not always
    parseable as it stands and the sentence has to be dug out of it.
    """
    opening, closing = body.find("{"), body.rfind("}")
    if opening != -1 and closing > opening:
        try:
            return json.loads(body[opening : closing + 1]).get("message") or body[:120]
        except ValueError:
            pass
    return body.splitlines()[0][:120]


def report(what: str, state: str, note: str = "") -> None:
    print(f"  {what:34} {state:10} {note}")


def check_the_settings(apply: bool) -> int:
    print(f"== {REPOSITORY} ==\n")
    refused: list[str] = []

    # --- what a private repository on the free plan already has -------------
    print("== on, or settable, now ==")

    code, _ = github(f"repos/{REPOSITORY}/vulnerability-alerts")
    if code != 0 and apply:
        github(f"repos/{REPOSITORY}/vulnerability-alerts", method="PUT")
        code, _ = github(f"repos/{REPOSITORY}/vulnerability-alerts")
    report("vulnerability alerts", "on" if code == 0 else "OFF")

    code, body = github(f"repos/{REPOSITORY}/automated-security-fixes")
    enabled = code == 0 and json.loads(body or "{}").get("enabled") is True
    if not enabled and apply:
        github(f"repos/{REPOSITORY}/automated-security-fixes", method="PUT")
        code, body = github(f"repos/{REPOSITORY}/automated-security-fixes")
        enabled = json.loads(body or "{}").get("enabled") is True
    report("dependabot security updates", "on" if enabled else "OFF")

    # --- the rule that protects main ---------------------------------------
    print("\n== the rule on main ==")
    code, body = github(f"repos/{REPOSITORY}/branches/main/protection")
    if code == 0:
        live = json.loads(body)
        checks = live.get("required_status_checks") or {}
        report(
            "pull request required",
            "on" if live.get("required_pull_request_reviews") else "off",
        )
        report(
            "required check",
            (checks.get("contexts", []) and ", ".join(checks["contexts"])) or "NONE",
        )
        report("must be up to date", "yes" if checks.get("strict") else "NO")
        report(
            "applies to admins",
            "yes" if live.get("enforce_admins", {}).get("enabled") else "NO",
        )
        report(
            "force pushes",
            "blocked"
            if not live.get("allow_force_pushes", {}).get("enabled")
            else "ALLOWED",
        )
    elif apply:
        code, body = github(
            f"repos/{REPOSITORY}/branches/main/protection",
            method="PUT",
            payload=BRANCH_PROTECTION,
        )
        if code == 0:
            report("branch protection", "applied", "re-run --check to see it")
        else:
            report("branch protection", "refused", the_reason(body))
            refused.append(f"branch protection: {the_reason(body)}")
    else:
        report("branch protection", "gated", the_reason(body))
        refused.append(f"branch protection: {the_reason(body)}")

    # --- the controls that act before a secret leaves the machine ----------
    print("\n== scanning ==")
    wanted = {
        "security_and_analysis": {
            "secret_scanning": {"status": "enabled"},
            "secret_scanning_push_protection": {"status": "enabled"},
        }
    }
    code, body = (
        github(f"repos/{REPOSITORY}", method="PATCH", payload=wanted)
        if apply
        else github(f"repos/{REPOSITORY}")
    )
    if apply and code != 0:
        report("secret scanning + push protection", "refused", the_reason(body))
        refused.append(f"secret scanning: {the_reason(body)}")
    elif apply:
        live = json.loads(body).get("security_and_analysis") or {}
        state = (live.get("secret_scanning") or {}).get("status", "not reported")
        report("secret scanning + push protection", state)
    else:
        # Read as a state, not as JSON. The first version handed the dict straight to
        # a format string, which printed a TypeError the first time these were
        # actually enabled - the path had only ever run while they were unavailable.
        live = json.loads(body).get("security_and_analysis") or {}
        scanning = (live.get("secret_scanning") or {}).get("status")
        pushing = (live.get("secret_scanning_push_protection") or {}).get("status")
        report(
            "secret scanning + push protection",
            f"{scanning or 'off'}/{pushing or 'off'}",
            ""
            if scanning == "enabled"
            else "(needs Advanced Security, or a public repository)",
        )

    code, body = github(f"repos/{REPOSITORY}/private-vulnerability-reporting")
    if code != 0 and apply:
        code, body = github(
            f"repos/{REPOSITORY}/private-vulnerability-reporting", method="PUT"
        )
    report(
        "private vulnerability reporting",
        "on" if code == 0 else "unavailable",
        "" if code == 0 else the_reason(body),
    )

    # --- what to do about all of that --------------------------------------
    if refused:
        print("\n== refused, and what that means ==")
        for refusal in refused:
            print(f"  * {refusal}")
        print(
            "\n  All of these are free on a public repository. Until then the only\n"
            "  thing protecting main is that nobody else can push to it."
        )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--check", action="store_true", help="report, change nothing")
    group.add_argument(
        "--apply", action="store_true", help="set what is missing, then read back"
    )
    arguments = parser.parse_args()
    return check_the_settings(apply=arguments.apply)


if __name__ == "__main__":
    sys.exit(main())
