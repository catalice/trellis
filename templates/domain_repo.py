"""
TEMPLATE: domain_repo.py
Copy to: src/trellis/{domain}_repo.py

Rules:
- Protocol at the top — defines the interface, no implementation details
- PostgresImpl below — one class, no logic beyond SQL
- No business logic here, ever
"""
from __future__ import annotations

from typing import Protocol
from uuid import UUID

from trellis.domain_models import ExampleRecord


# --- Protocol -----------------------------------------------------------

class ExampleRepository(Protocol):
    def get(self, record_id: UUID) -> ExampleRecord | None: ...
    def list_for_user(self, user_id: UUID) -> list[ExampleRecord]: ...
    def save(self, record: ExampleRecord) -> ExampleRecord: ...


# --- Postgres implementation --------------------------------------------

class PostgresExampleRepository:
    def __init__(self, conn_factory) -> None:
        self._conn = conn_factory

    def get(self, record_id: UUID) -> ExampleRecord | None:
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT * FROM example WHERE id = %s",
                    (str(record_id),),
                )
                row = cur.fetchone()
        return self._row(row) if row else None

    def list_for_user(self, user_id: UUID) -> list[ExampleRecord]:
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT * FROM example WHERE user_id = %s ORDER BY created_at DESC",
                    (str(user_id),),
                )
                rows = cur.fetchall()
        return [self._row(r) for r in rows]

    def save(self, record: ExampleRecord) -> ExampleRecord:
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO example (id, user_id, value, created_at)
                    VALUES (%s, %s, %s, %s)
                    ON CONFLICT (id) DO UPDATE SET value = EXCLUDED.value
                    """,
                    (str(record.id), str(record.user_id), record.value, record.created_at),
                )
        return record

    @staticmethod
    def _row(row: dict) -> ExampleRecord:
        return ExampleRecord(
            id=UUID(row["id"]),
            user_id=UUID(row["user_id"]),
            value=row["value"],
            created_at=row["created_at"],
        )
