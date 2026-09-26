# Security

## Reporting

Open a [private security advisory](https://docs.github.com/en/code-security/security-advisories/guidance-on-reporting-and-writing-information-about-vulnerabilities)
rather than a public issue. If you cannot, email the maintainer listed in
`pyproject.toml`. Please include the smallest reproduction you have; a phone
transcript or a run folder from `~/.phone-control/runs` is ideal.

## What this program can do

It sends commands to a real phone over adb and acts on what it sees, so the
interesting attack surface is not the usual one. In scope:

- **Command injection into the device shell.** Text that arrives from a model — a
  typed string, a URL, an app name — reaches `adb shell`. It is quoted through
  `quote_for_device_shell` at every such point, and a report of a path where it is
  not is a real finding.
- **Secrets.** The Jev API key is read from the environment or a file outside this
  repository and is never written to a state, a log, a run folder or a transcript.
  A path where it can leak is in scope. Note that a *client* mounting this server as
  a stdio child may strip `JEV_API_KEY` or `OPENROUTER_API_KEY` from the environment,
  since a bridge often scrubs anything matching `/KEY|PASSWORD|SECRET|TOKEN/i`; that is
  the client's behaviour and the README says so.
- **Personal data in committed files.** `tests/recorded/` holds real phone output.
  The recorder redacts this device's identifiers by asking the device for them, and
  a test refuses an email address or a serial-shaped token. A transcript that leaks
  something those rules cannot see — a display name, a message body — is in scope.
- **Screen text treated as instruction.** Everything on the screen is app-authored
  text, including sentences that read like an order. The state says so explicitly,
  and a change that lets screen text steer a run is a finding.

## What is not

- The security of the phone itself, the apps on it, or adb.
- The Jev decision service, or the model behind it.
- A phone that is rooted, or USB debugging left on deliberately.
- Anything an agent does with the tools it is given. This server carries out what it
  is asked to; the skill tells agents to state what they are about to do and not to
  take an instruction further than it was given, but the boundary is the caller's.

## What we do about it

`scripts/check.sh` runs the whole suite with a 95% coverage gate, the pre-commit hook
refuses a commit on a red suite, and CI pins every action by commit rather than by
tag. None of that is a security guarantee; it is what keeps the guarantees above from
quietly lapsing.
