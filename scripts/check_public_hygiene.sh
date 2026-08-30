#!/usr/bin/env bash
# Build-public backstop: grep the tracked tree for personal markers before a push.
#
# The denylist lives in .hygiene-denylist (gitignored — the markers themselves
# are personal data). One case-insensitive extended-regex pattern per line;
# blank lines and #comments ignored. Create yours with the things that must
# never ship: surname, employer, medication names, home city, friends' names.
#
# Install as a pre-push hook:
#   ln -s ../../scripts/check_public_hygiene.sh .git/hooks/pre-push
#
# No denylist file -> warn and pass (a fork without one shouldn't be blocked).
set -euo pipefail

# git rev-parse, not BASH_SOURCE: as a pre-push hook this script runs via a
# symlink from .git/hooks/, so its own path resolves inside .git/.
REPO_DIR="$(git rev-parse --show-toplevel)"
DENYLIST="$REPO_DIR/.hygiene-denylist"

if [[ ! -f "$DENYLIST" ]]; then
    echo "hygiene: no .hygiene-denylist found — skipping (create one; see script header)"
    exit 0
fi

FAILED=0
while IFS= read -r pattern; do
    [[ -z "$pattern" || "$pattern" == \#* ]] && continue
    # Search every tracked file except this denylist mechanism itself.
    if hits=$(cd "$REPO_DIR" && git grep -iEn "$pattern" -- \
            ':!scripts/check_public_hygiene.sh' 2>/dev/null); then
        echo "hygiene: personal marker '$pattern' found:"
        echo "$hits" | head -5
        FAILED=1
    fi
done < "$DENYLIST"

if [[ "$FAILED" == 1 ]]; then
    echo ""
    echo "PUSH BLOCKED: personal markers in the tracked tree (see Build public in CLAUDE.md)."
    exit 1
fi
echo "hygiene: clean"
