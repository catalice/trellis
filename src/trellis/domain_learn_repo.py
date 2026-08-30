"""Learn storage — threads and entries. Protocol at top, Postgres below.
Typed data, never strings."""
from __future__ import annotations

from typing import Any, Protocol
from uuid import UUID

from psycopg2.extras import RealDictCursor

from trellis.domain_learn_models import EntryKind, LearnEntry, LearnThread


class LearnRepository(Protocol):
    def save_thread(self, thread: LearnThread) -> LearnThread: ...
    def get_thread_by_title(self, user_id: UUID, title: str) -> LearnThread | None: ...
    def list_threads(self, user_id: UUID) -> list[LearnThread]: ...
    def set_position(self, user_id: UUID, thread_id: UUID, position: str) -> bool: ...
    def save_entry(self, entry: LearnEntry) -> LearnEntry: ...
    def list_entries(self, user_id: UUID, thread_id: UUID) -> list[LearnEntry]: ...


class PostgresLearnRepository:
    def __init__(self, database: Any) -> None:
        self._db = database

    def save_thread(self, thread: LearnThread) -> LearnThread:
        with self._db.connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO learn_threads (id, user_id, title, position, created_at, updated_at)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    """,
                    (thread.id, thread.user_id, thread.title, thread.position,
                     thread.created_at, thread.updated_at),
                )
        return thread

    def get_thread_by_title(self, user_id: UUID, title: str) -> LearnThread | None:
        with self._db.connect() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute(
                    "SELECT * FROM learn_threads WHERE user_id = %s AND lower(title) = lower(%s)",
                    (user_id, title.strip()),
                )
                row = cur.fetchone()
                return _thread(row) if row else None

    def list_threads(self, user_id: UUID) -> list[LearnThread]:
        with self._db.connect() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute(
                    "SELECT * FROM learn_threads WHERE user_id = %s ORDER BY updated_at DESC",
                    (user_id,),
                )
                return [_thread(r) for r in cur.fetchall()]

    def set_position(self, user_id: UUID, thread_id: UUID, position: str) -> bool:
        with self._db.connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE learn_threads SET position = %s, updated_at = NOW()"
                    " WHERE id = %s AND user_id = %s",
                    (position, thread_id, user_id),
                )
                return cur.rowcount > 0

    def save_entry(self, entry: LearnEntry) -> LearnEntry:
        with self._db.connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO learn_entries
                        (id, user_id, thread_id, kind, region, content,
                         source_url, source_title, created_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (entry.id, entry.user_id, entry.thread_id, str(entry.kind),
                     entry.region, entry.content, entry.source_url,
                     entry.source_title, entry.created_at),
                )
                cur.execute(
                    "UPDATE learn_threads SET updated_at = NOW() WHERE id = %s",
                    (entry.thread_id,),
                )
        return entry

    def list_entries(self, user_id: UUID, thread_id: UUID) -> list[LearnEntry]:
        with self._db.connect() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute(
                    "SELECT * FROM learn_entries WHERE user_id = %s AND thread_id = %s"
                    " ORDER BY created_at",
                    (user_id, thread_id),
                )
                return [_entry(r) for r in cur.fetchall()]


def _thread(row: dict) -> LearnThread:
    return LearnThread(
        id=row["id"], user_id=row["user_id"], title=row["title"],
        position=row.get("position"), created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _entry(row: dict) -> LearnEntry:
    return LearnEntry(
        id=row["id"], user_id=row["user_id"], thread_id=row["thread_id"],
        kind=EntryKind(row["kind"]), content=row["content"],
        region=row.get("region"), source_url=row.get("source_url"),
        source_title=row.get("source_title"), created_at=row["created_at"],
    )
