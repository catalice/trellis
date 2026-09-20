#!/usr/bin/env bash
# Nightly Trellis database backup.
# Dumps Postgres into the Obsidian vault's hidden .backups folder so any
# vault backup/sync (Time Machine, iCloud, Syncthing) carries the DB too.
# Keeps the last 14 dumps. Requires the trellis compose stack to be running.
#
# A run that fails leaves every earlier dump exactly as it was: the dump is
# written beside its destination, checked, and only then moved into place.
# Restore: scripts/restore_db.sh. What else must be backed up: docs/SETUP.md.
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# The vault path: the environment, else the project's .env, else the default.
if [[ -z "${OBSIDIAN_VAULT:-}" && -f "$REPO_DIR/.env" ]]; then
    OBSIDIAN_VAULT="$(grep -E '^OBSIDIAN_VAULT=' "$REPO_DIR/.env" | tail -1 | cut -d= -f2- | sed -e 's/^"//' -e 's/"$//')"
fi
VAULT="${OBSIDIAN_VAULT:-$REPO_DIR/vault}"
BACKUP_DIR="$VAULT/.backups"
STAMP="$(date +%Y-%m-%d)"
OUT="$BACKUP_DIR/trellis-$STAMP.sql.gz"
PARTIAL="$OUT.partial"

mkdir -p "$BACKUP_DIR"
cd "$REPO_DIR"
trap 'rm -f "$PARTIAL"' EXIT

docker compose exec -T postgres pg_dump -U trellis trellis | gzip > "$PARTIAL"

# A dump is whole only if it decompresses and pg_dump reached its last line.
if ! gzip -t "$PARTIAL" 2>/dev/null; then
    echo "backup failed: dump is not valid gzip; earlier backups untouched" >&2
    exit 1
fi
if ! gzip -dc "$PARTIAL" | tail -5 | grep -q "PostgreSQL database dump complete"; then
    echo "backup failed: dump is incomplete; earlier backups untouched" >&2
    exit 1
fi

mv -f "$PARTIAL" "$OUT"

# Rotate: keep the newest 14. Names sort by date; the loop is safe for any path.
dumps=()
while IFS= read -r -d '' file; do
    dumps+=("$file")
done < <(find "$BACKUP_DIR" -maxdepth 1 -name 'trellis-*.sql.gz' -print0 | sort -z)
excess=$(( ${#dumps[@]} - 14 ))
for (( i = 0; i < excess; i++ )); do
    rm -f -- "${dumps[$i]}"
done

echo "backed up: $OUT ($(du -h "$OUT" | cut -f1))"
