#!/usr/bin/env bash
# Nightly Trellis database backup.
# Dumps Postgres into the Obsidian vault's hidden .backups folder so any
# vault backup/sync (Time Machine, iCloud, Syncthing) carries the DB too.
# Keeps the last 14 dumps. Requires the trellis compose stack to be running.
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VAULT="${OBSIDIAN_VAULT:-$HOME/Documents/Second-Brain/second-brain}"
BACKUP_DIR="$VAULT/.backups"
STAMP="$(date +%Y-%m-%d)"
OUT="$BACKUP_DIR/trellis-$STAMP.sql.gz"

mkdir -p "$BACKUP_DIR"
cd "$REPO_DIR"

docker compose exec -T postgres pg_dump -U trellis trellis | gzip > "$OUT"

# Sanity check: a valid dump is never tiny
if [ "$(stat -f%z "$OUT")" -lt 1024 ]; then
    echo "backup suspiciously small: $OUT" >&2
    exit 1
fi

# Rotate: keep the newest 14
ls -t "$BACKUP_DIR"/trellis-*.sql.gz 2>/dev/null | tail -n +15 | xargs rm -f

echo "backed up: $OUT ($(du -h "$OUT" | cut -f1))"
