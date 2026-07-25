"""
Backfill the meaning index for existing rows.

One-time (safely re-runnable) maintenance: finds captures, efforts and seeds not
yet in memory_index and files each one via the same MemoryIndex the app uses.
Re-running is a no-op once everything is filed. Text composition is delegated to
the model methods, so it can never drift from embed-on-write.

Run:  uv run python scripts/backfill_embeddings.py
"""
from __future__ import annotations

import os
import sys

from dotenv import load_dotenv

sys.path.insert(0, "src")
from trellis.domain_second_brain_models import Capture, Effort, Task
from trellis.infra_embeddings import GitHubModelsEmbedder
from trellis.infra_memory import MemoryIndex
from trellis.postgres import PostgresDatabase

# Host-reachable URL for the running container (compose exposes 5432 -> 5433).
DB_URL = os.getenv(
    "BACKFILL_DATABASE_URL", "postgresql://trellis:trellis@localhost:5433/trellis"
)


def main() -> int:
    load_dotenv()
    token = os.getenv("GITHUB_TOKEN", "")
    if not token:
        print("GITHUB_TOKEN missing — set it in .env first.")
        return 1

    database = PostgresDatabase(DB_URL)
    memory = MemoryIndex(database, GitHubModelsEmbedder(token))

    # (entity_kind, entity_id, user_id, text) for everything not yet filed.
    targets: list[tuple[str, object, object, str]] = []
    with database.connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT c.id, c.user_id, c.summary, c.synthesis, c.raw
                FROM captures c
                LEFT JOIN memory_index m
                    ON m.entity_kind = 'capture' AND m.entity_id = c.id
                WHERE m.id IS NULL
                """
            )
            for cid, user_id, summary, synthesis, raw in cur.fetchall():
                targets.append(("capture", cid, user_id, Capture.compose_embedding_text(summary, synthesis, raw)))

            cur.execute(
                """
                SELECT e.id, e.user_id, e.title, e.notes
                FROM efforts e
                LEFT JOIN memory_index m
                    ON m.entity_kind = 'effort' AND m.entity_id = e.id
                WHERE m.id IS NULL
                """
            )
            for eid, user_id, title, notes in cur.fetchall():
                targets.append(("effort", eid, user_id, Effort.compose_embedding_text(title, notes)))

            cur.execute(
                """
                SELECT t.id, t.user_id, t.title, t.description
                FROM tasks t
                LEFT JOIN memory_index m
                    ON m.entity_kind = 'seed' AND m.entity_id = t.id
                WHERE t.kind = 'seed' AND m.id IS NULL
                """
            )
            for sid, user_id, title, description in cur.fetchall():
                targets.append(("seed", sid, user_id, Task.compose_embedding_text(title, description)))

    # remember_many batches the embeds, so the whole brain re-indexes in a couple
    # of requests, not one per row — which is what keeps us under 15 requests/min.
    items = [
        (user_id, entity_kind, entity_id, text)
        for entity_kind, entity_id, user_id, text in targets
        if text
    ]
    if not items:
        print("Nothing to backfill — the meaning index is up to date.")
        return 0

    filed = memory.remember_many(items)
    if filed == len(items):
        print(f"Done — filed {filed} row(s) into the meaning index.")
    else:
        print(f"Filed {filed}/{len(items)} row(s); the rest are pending (rate-limited) — re-run later.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
