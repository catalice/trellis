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

# As a pre-push hook, git hands us the refs being pushed on stdin — check the
# TREES OF THOSE COMMITS, not just the worktree: a marker removed from the
# checkout but alive in a pushed commit's tree would otherwise ride out.
REFS_TO_CHECK=()
if [[ ! -t 0 ]]; then
    while read -r _local_ref local_sha _remote_ref _remote_sha; do
        [[ -z "${local_sha:-}" ]] && continue
        [[ "$local_sha" =~ ^0+$ ]] && continue   # deletion push
        REFS_TO_CHECK+=("$local_sha")
    done || true
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
    for sha in "${REFS_TO_CHECK[@]+"${REFS_TO_CHECK[@]}"}"; do
        if hits=$(cd "$REPO_DIR" && git grep -iEn "$pattern" "$sha" -- \
                ':!scripts/check_public_hygiene.sh' 2>/dev/null); then
            echo "hygiene: personal marker '$pattern' in pushed commit $sha:"
            echo "$hits" | head -5
            FAILED=1
        fi
    done
done < "$DENYLIST"

if [[ "$FAILED" == 1 ]]; then
    echo ""
    echo "PUSH BLOCKED: personal markers in the tracked tree (see Build public in CLAUDE.md)."
    exit 1
fi
echo "hygiene: clean"
