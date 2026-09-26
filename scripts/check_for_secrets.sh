#!/usr/bin/env bash
#
# Look for secrets in every commit and in the working tree.
#
# Two scans, because they answer different questions. The history scan asks "was a
# credential ever committed here", which is the one that matters: a key in a commit
# is a key that stays in the repository after the file is cleaned up, and the only
# remedy is rotating it. The tree scan catches the state right now, including files
# that are not committed yet.
#
# The scanner is downloaded rather than taken from PATH, at a pinned version with a
# recorded checksum. That is the same lesson the linter taught: this machine had
# ruff 0.15.5 while CI installed 0.16.9, and the difference turned a green local run
# into a red push. A scanner whose rules differ between the two is a scanner whose
# answer means nothing.
#
#   scripts/check_for_secrets.sh
#
set -euo pipefail

cd "$(dirname "$0")/.."

GITLEAKS_VERSION=8.30.1
TOOLS="${TMPDIR:-/tmp}/phone-control-tools"
SCANNER="$TOOLS/gitleaks-$GITLEAKS_VERSION"

# Recorded from the published release assets. A download that does not match is a
# download this script refuses to run, which is the point of pinning it.
case "$(uname -s)-$(uname -m)" in
    Darwin-arm64)  PLATFORM=darwin_arm64; EXPECTED=b40ab0ae55c505963e365f271a8d3846efbc170aa17f2607f13df610a9aeb6a5 ;;
    Linux-x86_64)  PLATFORM=linux_x64;    EXPECTED=551f6fc83ea457d62a0d98237cbad105af8d557003051f41f3e7ca7b3f2470eb ;;
    *)
        echo "check_for_secrets: no pinned scanner for $(uname -s)-$(uname -m)." >&2
        echo "check_for_secrets: add its checksum to this script rather than scanning" >&2
        echo "check_for_secrets: with whatever happens to be on PATH." >&2
        exit 2
        ;;
esac

if [ ! -x "$SCANNER" ]; then
    mkdir -p "$TOOLS"
    ARCHIVE="$TOOLS/gitleaks-$GITLEAKS_VERSION.tar.gz"
    URL="https://github.com/gitleaks/gitleaks/releases/download/v$GITLEAKS_VERSION"
    curl -sSL -o "$ARCHIVE" "$URL/gitleaks_${GITLEAKS_VERSION}_${PLATFORM}.tar.gz"

    # `shasum` on macOS, `sha256sum` on Linux; both print the digest first.
    if command -v shasum >/dev/null 2>&1; then
        ACTUAL=$(shasum -a 256 "$ARCHIVE" | cut -d' ' -f1)
    else
        ACTUAL=$(sha256sum "$ARCHIVE" | cut -d' ' -f1)
    fi
    if [ "$ACTUAL" != "$EXPECTED" ]; then
        echo "check_for_secrets: the scanner downloaded from GitHub does not match the" >&2
        echo "check_for_secrets: checksum recorded here. Refusing to scan with it." >&2
        echo "check_for_secrets:   expected $EXPECTED" >&2
        echo "check_for_secrets:   got      $ACTUAL" >&2
        rm -f "$ARCHIVE"
        exit 2
    fi
    tar -xzf "$ARCHIVE" -C "$TOOLS" gitleaks
    mv "$TOOLS/gitleaks" "$SCANNER"
    rm -f "$ARCHIVE"
fi

# `.gitleaks.toml` holds rules, not suppressions: it extends the defaults with the
# two shapes this project's own credentials take. That distinction is the whole
# reason the file is allowed to exist - a scan that honours an allowlist is testing
# the allowlist, but a scan that adds rules finds strictly more than the defaults.
# The first version of this script used no config and passed a staged key-shaped
# secret, which is how the file came to exist.
#
# `--redact` so that a finding in a public CI log does not repeat the secret it found.
echo "== every commit ever made here =="
"$SCANNER" detect --source . --config .gitleaks.toml --redact --no-banner

# The tree as it would be published: the tracked files, in a directory of their own.
# Scanning the checkout instead would walk the virtualenv - 66 MB of somebody else's
# code, slower, and findings in it would be about a dependency rather than about
# this repository. `checkout-index` writes what git tracks, which is the same source
# the release export uses.
PUBLISHED="${TMPDIR:-/tmp}/phone-control-secret-scan"
rm -rf "$PUBLISHED" && mkdir -p "$PUBLISHED"
git checkout-index -a -f --prefix="$PUBLISHED/"
echo "== the tracked tree, as it would be published =="
"$SCANNER" detect --source "$PUBLISHED" --no-git \
    --config "$PWD/.gitleaks.toml" --redact --no-banner
rm -rf "$PUBLISHED"

echo "no secrets found in the history or the tracked tree"
