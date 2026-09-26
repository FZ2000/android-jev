# Repository settings, and what makes contributing safe

What to switch on, why each one is there, and what is refused until the repository
is public. Every state below was read back from the GitHub API on 2026-09-27 rather
than assumed, and the refusals are quoted as GitHub gave them.

`scripts/secure_the_repository.py` checks and applies all of it:

```bash
.venv/bin/python scripts/secure_the_repository.py --check    # report only
.venv/bin/python scripts/secure_the_repository.py --apply    # set, then read back
```

## What a branch protection rule is

A branch protection rule (or, in the newer form, a ruleset) is a condition GitHub
enforces *at the moment of a push or a merge* for one branch. Without one, `main` is
just a branch: anyone with write access can push to it directly, and a pull request
can be merged while its checks are red, out of date, or still running.

It is the difference between "our CI runs" and "nothing reaches `main` that CI has
not passed". CI on its own is advice — it reports, and somebody has to look.

The rule is enforced by GitHub, not by the repository, which is why none of it lives
in a file here and why this document exists to say what it should be.

## What is on now, while the repository is private

| Setting | State | Why |
| --- | --- | --- |
| Vulnerability alerts | **on** | Tells you when a dependency has a published advisory. |
| Dependabot security updates | **on** | Opens a PR for those, rather than only telling you. |
| Dependabot version updates | **on** | `.github/dependabot.yml`, weekly, grouped into one PR per ecosystem. |

That is everything GitHub's free plan allows on a private repository.

## What is refused until it is public

| Setting | The answer GitHub gives | Cost of the alternative |
| --- | --- | --- |
| Branch protection | `403 Upgrade to GitHub Pro or make this repository public to enable this feature.` | GitHub Pro |
| Rulesets | the same `403` | GitHub Pro |
| Secret scanning | `422 Secret scanning is not available for this repository.` | Advanced Security |
| Push protection | as above — it is the same feature | Advanced Security |
| Private vulnerability reporting | `404`, the endpoint does not exist for this repository | Advanced Security |

**All five are free on a public repository**, which is the practical argument for
making the move you are already planning: the account pays for them, and the
repository is the thing that has to change.

## The rule to set the moment it is public

```bash
gh api -X PUT repos/FZ2000/android-phone-control/branches/main/protection \
  -H "Accept: application/vnd.github+json" --input - <<'JSON'
{
  "required_status_checks": { "strict": true, "contexts": ["ci-gate"] },
  "enforce_admins": true,
  "required_pull_request_reviews": {
    "required_approving_review_count": 0,
    "dismiss_stale_reviews": true
  },
  "restrictions": null,
  "allow_force_pushes": false,
  "allow_deletions": false,
  "required_conversation_resolution": true,
  "required_linear_history": true
}
JSON
```

Each line of that, and why it is there rather than being obvious:

- **`strict: true`** — the branch must be up to date with `main` before merging. This
  is the one that catches the case nobody expects: two pull requests, each green on
  its own, which merge into a broken `main` with no textual conflict at all. One
  removes an import as unused (correctly), the other adds its first use.
- **`contexts: ["ci-gate"]`** — exactly one required check. `ci-gate` aggregates
  lint, both Python versions and the packaging job, so the matrix can change without
  anybody editing this rule. Requiring the matrix jobs by name would mean editing
  protection every time a version is added.
- **`enforce_admins: true`** — the rule applies to you. A protection rule you can
  bypass by pushing is a rule you will bypass at 11pm on the day it annoys you.
- **`required_approving_review_count: 0`** — a pull request is required, a human
  approval is not. On a project with one maintainer, requiring an approval means
  requiring somebody who does not exist; requiring the *pull request* still gets you
  the thing that matters, which is that CI ran on the merge.
- **`required_conversation_resolution: true`** — a review comment has to be answered
  or resolved before merging, not merely noticed.
- **`required_linear_history: true`** — `main` stays a line, which suits a project
  whose commits are one coherent change each with a message that explains it.
- **`allow_force_pushes: false`, `allow_deletions: false`** — `main` cannot be
  rewritten or removed, by anyone, including by a mistake.
- **`restrictions: null`** — no push allowlist, because nobody pushes; that is what
  the rule above is for.

Then, separately, because they are different features:

```bash
# Secret scanning and push protection: the only control that acts before a secret
# leaves the machine. A scan afterwards means the credential is already public and
# rotation is the only remedy.
gh api -X PATCH repos/FZ2000/android-phone-control --input - <<'JSON'
{"security_and_analysis":{"secret_scanning":{"status":"enabled"},
 "secret_scanning_push_protection":{"status":"enabled"}}}
JSON

# Private vulnerability reporting, so SECURITY.md has somewhere to point.
gh api -X PUT repos/FZ2000/android-phone-control/private-vulnerability-reporting
```

## Why a contribution is safe here already

Independent of the settings above, and worth knowing before anybody forks anything:

- **CI runs on pull requests** — the workflow has no `pull_request_target`, so a fork's
  code runs with a read-only token and no access to secrets. There are no secrets in
  CI to leak: the only credential is the automatic `GITHUB_TOKEN`, and the workflow
  asks for `contents: read`.
- **The drift check and the suite need no phone and no key**, so a contributor gets a
  real answer on their own machine with `scripts/check.sh --as-ci`, which is the same
  command CI runs.
- **The live discoverability check is guarded against forks.** It reads the
  repository's description and topics back from the API, and in a fork's run
  `GITHUB_REPOSITORY` is the *fork* — which has neither, because its owner never set
  them. Without the guard, every contributor's first pull request would fail on
  metadata they cannot change. The offline half of those rules still runs there, as a
  test, which is the part about their change.

## Optional, and the argument against each

- **Signed commits** (`required_signatures`) — proves a commit came from the holder
  of a key. It would also block your own commits until signing is configured, and it
  does not stop a bad change, only a spoofed author. Worth it only if that is the
  threat you are defending against.
- **Rulesets rather than branch protection** — the same protections plus tag rules
  (making a release tag immutable) and a bypass list you can leave empty. The
  commands above use the older branch-protection API because it is the one every
  reader recognises; rulesets are the better long-term home once releases exist.
- **CODEOWNERS** — pointless on a solo project, and its paths fail silently when they
  match nothing, so a stale one reads as review coverage that does not exist.
- **A second dependency updater** — one is configured. Two open duplicate pull
  requests against the same manifests, and duplicate one-line bumps are how people
  learn to merge without reading.
