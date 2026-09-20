#!/usr/bin/env bash
# Restore a Trellis database dump made by scripts/backup_db.sh.
#
#   scripts/restore_db.sh <dump.sql.gz>            # rehearsal: restore into a
#                                                  # scratch database, report, drop it
#   scripts/restore_db.sh <dump.sql.gz> --live     # replace the live database
#
# The rehearsal touches nothing live — run it now and then, so the first real
# restore isn't the first time the backup is read.
#
# --live never makes things worse. The dump is restored into a separate staging
# database and checked there FIRST; if that fails, the live database has not
# been touched. Only then is the bot stopped and the two swapped by rename —
# the database being replaced is kept as trellis_before_restore_<time>, never
# dropped. Removing it afterwards is your decision.
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
# Every call reads /dev/null unless SQL is piped in — the prompt below owns stdin.
psql_cmd() { docker compose exec -T postgres psql -U trellis -v ON_ERROR_STOP=1 -q "$@" </dev/null; }
psql_import() { gzip -dc "$DUMP" | docker compose exec -T postgres psql -U trellis -v ON_ERROR_STOP=1 -q -d "$1" >/dev/null; }

# Restore into `target` and prove it holds a Trellis database. Leaves nothing
# behind on failure.
restore_and_check() {
    local target="$1"
    psql_cmd -d postgres -c "DROP DATABASE IF EXISTS $target;" -c "CREATE DATABASE $target;"
    if ! psql_import "$target"; then
        echo "restore failed: the dump did not import cleanly. Nothing live was touched." >&2
        psql_cmd -d postgres -c "DROP DATABASE IF EXISTS $target;" || true
        return 1
    fi
    local migrations
    migrations="$(psql_cmd -d "$target" -At -c "SELECT count(*) FROM schema_migrations;" 2>/dev/null || echo 0)"
    if [[ ! "$migrations" =~ ^[0-9]+$ || "$migrations" -lt 1 ]]; then
        echo "restore failed: the dump imported but holds no Trellis database. Nothing live was touched." >&2
        psql_cmd -d postgres -c "DROP DATABASE IF EXISTS $target;" || true
        return 1
    fi
    echo "  migrations applied: $migrations"
}

if [[ "$MODE" == "--live" ]]; then
    STAGING="trellis_restore_staging"
    echo "checking $(basename "$DUMP") in a staging database first:"
    restore_and_check "$STAGING"
    read -r -p "It restores cleanly. Replace the LIVE trellis database with it? Type 'restore': " answer || answer=""
    if [[ "$answer" != "restore" ]]; then
        psql_cmd -d postgres -c "DROP DATABASE IF EXISTS $STAGING;" || true
        echo "cancelled — nothing live was touched"
        exit 1
    fi
    KEPT="trellis_before_restore_$(date +%Y%m%d_%H%M%S)"
    docker compose stop trellis </dev/null
    trap 'docker compose start trellis </dev/null || true' EXIT
    psql_cmd -d postgres -c "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname IN ('trellis', '$STAGING') AND pid <> pg_backend_pid();" >/dev/null
    psql_cmd -d postgres -c "ALTER DATABASE trellis RENAME TO $KEPT;"
    if ! psql_cmd -d postgres -c "ALTER DATABASE $STAGING RENAME TO trellis;"; then
        psql_cmd -d postgres -c "ALTER DATABASE $KEPT RENAME TO trellis;"
        echo "restore failed at the swap; the original database is back in place" >&2
        exit 1
    fi
    echo "restored the live database from $DUMP"
    echo "the database it replaced is kept as: $KEPT"
    echo "  when you're sure, remove it with:"
    echo "  docker compose exec -T postgres psql -U trellis -d postgres -c 'DROP DATABASE $KEPT;'"
    exit 0
fi

SCRATCH="trellis_restore_check"
trap 'psql_cmd -d postgres -c "DROP DATABASE IF EXISTS '"$SCRATCH"';" || true' EXIT
echo "rehearsal restore of $(basename "$DUMP"):"
restore_and_check "$SCRATCH"
psql_cmd -d "$SCRATCH" -At -c "
    SELECT '  tables: ' || count(*) FROM information_schema.tables WHERE table_schema = 'public';" -c "
    SELECT '  conversation turns: ' || count(*) FROM conversation_turns;" -c "
    SELECT '  tracking rows: ' || count(*) FROM tracking_log;"
echo "rehearsal succeeded — nothing live was touched"
