"""
Backfill the meaning index for existing rows.

Reconciles memory_index with the records (captures, efforts, open seeds) via the
same MemoryIndex the app uses: files what is missing, re-files what changed or
was never embedded, removes orphans. Re-running is a no-op once they agree. Text
composition is delegated to the model methods, so it can never drift from
embed-on-write.

RUN IT WITH THE BOT STOPPED. It reads the records, then the index, then repairs:
a write landing in between is undone — a fresh capture removed as an orphan, a
rename put back to its old words. The bot is the only other index writer.

    docker compose stop trellis          # Postgres stays up
    uv run python scripts/backfill_embeddings.py ; echo "exit $?"
    docker compose start trellis         # only after exit 0; on 1, re-run first
"""
from __future__ import annotations

import os
import sys

from dotenv import load_dotenv

sys.path.insert(0, "src")
from trellis.domain_focus_models import Capture, Effort, Task
from trellis.infra_embeddings import LocalEmbedder
from trellis.infra_memory import MemoryIndex
from trellis.infra_postgres import PostgresDatabase

# Host-reachable by default (compose publishes Postgres on this machine's 5433).
# Read lazily, in main(): the .env that holds the credentials is loaded there.
def _db_url() -> str:
    from trellis.core_config import database_dsn
    return os.getenv("BACKFILL_DATABASE_URL", "").strip() or database_dsn()


def main() -> int:
    load_dotenv()
    # Local embedder — no key needed.
    url = _db_url()
    if not url:
        print("No database credentials: set POSTGRES_PASSWORD (or DATABASE_URL) in .env", file=sys.stderr)
        return 2
    database = PostgresDatabase(url)
    memory = MemoryIndex(database, LocalEmbedder())

    # What the RECORDS say should be in the index: every capture, effort and
    # open seed, with its current words.
    expected: dict = {}
    with database.connect() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT id, user_id, summary, synthesis, raw FROM captures")
            for cid, user_id, summary, synthesis, raw in cur.fetchall():
                expected[("capture", cid)] = (user_id, Capture.compose_embedding_text(summary, synthesis, raw))
            cur.execute("SELECT id, user_id, title, notes FROM efforts")
            for eid, user_id, title, notes in cur.fetchall():
                expected[("effort", eid)] = (user_id, Effort.compose_embedding_text(title, notes))
            cur.execute("SELECT id, user_id, title, description FROM tasks WHERE kind = 'seed' AND status = 'open'")
            for sid, user_id, title, description in cur.fetchall():
                expected[("seed", sid)] = (user_id, Task.compose_embedding_text(title, description))

    # Reconcile: missing rows, rows whose words changed, rows with words but no
    # vector (the old repair only looked for MISSING rows, so after vectors were
    # cleared it called a searchless index up to date), and orphans.
    report = memory.reconcile(expected)
    print("index reconciled — "
          f"missing {report['missing']}, stale {report['stale']}, never embedded {report['unembedded']}, "
          f"orphaned {report['orphaned']}; filed {report['filed']}, removed {report['removed']}")
    if report["failed"]:
        print(f"{report['failed']} row(s) could NOT be repaired — the index is still out of step. Re-run.",
              file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
