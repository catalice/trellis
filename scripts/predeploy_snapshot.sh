#!/usr/bin/env bash
# Before a release: a database dump and a copy of the vault that NOTHING later
# will overwrite.
#
# The nightly backup names its file by date, so another successful run the same
# day replaces it — it is not a release snapshot. This writes a timestamped dump
# and a vault archive into their own folder, outside the vault and outside the
# nightly rotation, and prints where they are.
#
#   scripts/predeploy_snapshot.sh            # -> ~/trellis-snapshots/<time>-<revision>/
#   SNAPSHOT_ROOT=/some/where scripts/predeploy_snapshot.sh
#
# The vault copy is what "did my hand-written pages survive?" is checked against
# after the release.
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
if [[ -z "${OBSIDIAN_VAULT:-}" && -f "$REPO_DIR/.env" ]]; then
    OBSIDIAN_VAULT="$(grep -E '^OBSIDIAN_VAULT=' "$REPO_DIR/.env" | tail -1 | cut -d= -f2- | sed -e 's/^"//' -e 's/"$//')"
fi
VAULT="${OBSIDIAN_VAULT:-$REPO_DIR/vault}"
cd "$REPO_DIR"

STAMP="$(date +%Y%m%d-%H%M%S)"
REVISION="$(git rev-parse --short HEAD 2>/dev/null || echo unknown)"
DEST="${SNAPSHOT_ROOT:-$HOME/trellis-snapshots}/$STAMP-$REVISION"
if [[ -e "$DEST" ]]; then
    echo "snapshot: $DEST already exists — refusing to overwrite it" >&2
    exit 1
fi
mkdir -p "$DEST"

DUMP="$DEST/trellis-$STAMP.sql.gz"
docker compose exec -T postgres pg_dump -U trellis trellis | gzip > "$DUMP.partial"
if ! gzip -dc "$DUMP.partial" | tail -5 | grep -q "PostgreSQL database dump complete"; then
    echo "snapshot failed: the database dump is incomplete" >&2
    rm -f "$DUMP.partial"
    exit 1
fi
mv "$DUMP.partial" "$DUMP"

# The vault as it is now, without the dumps it already carries.
tar -czf "$DEST/vault-$STAMP.tar.gz" -C "$(dirname "$VAULT")" --exclude="$(basename "$VAULT")/.backups" "$(basename "$VAULT")"

{
    echo "taken:    $STAMP"
    echo "revision: $REVISION ($(git log -1 --format=%s 2>/dev/null || true))"
    echo "database: $(basename "$DUMP")"
    echo "vault:    vault-$STAMP.tar.gz"
} > "$DEST/README.txt"
chmod -R a-w "$DEST"          # a snapshot is read-only: nothing replaces it by accident

echo "snapshot: $DEST"
ls -lh "$DEST" | tail -n +2 | awk '{print "  " $5 "  " $9}'
