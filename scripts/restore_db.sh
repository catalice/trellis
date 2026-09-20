#!/usr/bin/env bash
# Restore a Trellis database dump made by scripts/backup_db.sh.
#
#   scripts/restore_db.sh <dump.sql.gz>            # rehearsal: restore into a
#                                                  # scratch database, report, drop it
#   scripts/restore_db.sh <dump.sql.gz> --live     # replace the live database
#
# The rehearsal touches nothing live — run it now and then, so the first real
# restore isn't the first time the backup is read. --live stops the bot,
# replaces the `trellis` database, and starts the bot again; it asks first.
set -euo pipefail

DUMP="${1:-}"
MODE="${2:-}"
if [[ -z "$DUMP" || ! -f "$DUMP" ]]; then
    echo "usage: scripts/restore_db.sh <dump.sql.gz> [--live]" >&2
    exit 2
fi
REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DUMP="$(cd "$(dirname "$DUMP")" && pwd)/$(basename "$DUMP")"
cd "$REPO_DIR"

gzip -t "$DUMP"
psql_in() { docker compose exec -T postgres psql -U trellis -v ON_ERROR_STOP=1 -q "$@"; }

if [[ "$MODE" == "--live" ]]; then
    read -r -p "Replace the LIVE trellis database with $(basename "$DUMP")? Type 'restore': " answer
    [[ "$answer" == "restore" ]] || { echo "cancelled"; exit 1; }
    docker compose stop trellis
    psql_in -d postgres -c "DROP DATABASE IF EXISTS trellis;" -c "CREATE DATABASE trellis;"
    gzip -dc "$DUMP" | psql_in -d trellis > /dev/null
    docker compose start trellis
    echo "restored the live database from $DUMP"
    exit 0
fi

SCRATCH="trellis_restore_check"
psql_in -d postgres -c "DROP DATABASE IF EXISTS $SCRATCH;" -c "CREATE DATABASE $SCRATCH;"
trap 'psql_in -d postgres -c "DROP DATABASE IF EXISTS '"$SCRATCH"';" || true' EXIT
gzip -dc "$DUMP" | psql_in -d "$SCRATCH" > /dev/null
echo "rehearsal restore of $(basename "$DUMP") succeeded:"
psql_in -d "$SCRATCH" -At -c "
    SELECT '  tables: ' || count(*) FROM information_schema.tables WHERE table_schema = 'public';" -c "
    SELECT '  migrations applied: ' || count(*) FROM schema_migrations;" -c "
    SELECT '  conversation turns: ' || count(*) FROM conversation_turns;" -c "
    SELECT '  tracking rows: ' || count(*) FROM tracking_log;"
