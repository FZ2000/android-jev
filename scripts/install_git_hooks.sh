#!/usr/bin/env bash
#
# Point git at the hooks in scripts/, so the rules are in the repository rather
# than in one person's .git directory.
#
# A hook written straight into .git/hooks is invisible to everyone else and is lost
# when the repository is cloned again. This makes .git/hooks/pre-commit a symlink to
# a file that is reviewed like any other.
#
set -euo pipefail

cd "$(dirname "$0")/.."

hooks="$(git rev-parse --git-path hooks)"
mkdir -p "$hooks"

for rule in pre-commit; do
    if [ -e "$hooks/$rule" ] && [ ! -L "$hooks/$rule" ]; then
        echo "refusing to overwrite $hooks/$rule, which is not a symlink" >&2
        exit 1
    fi
    ln -sf "../../scripts/$rule" "$hooks/$rule"
    echo "installed $rule"
done

echo
echo "The fast suite and the no-xfail guard now run on every commit."
echo "Run scripts/check.sh before pushing: it adds the device suite and coverage."
