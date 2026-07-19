from __future__ import annotations

from pathlib import Path
from uuid import UUID

import psycopg2
from psycopg2.extras import register_uuid

register_uuid()


class PostgresDatabase:
    def __init__(self, database_url: str):
        self.database_url = database_url

    def connect(self):
        return psycopg2.connect(self.database_url)

    def migrate(self, migrations_dir: Path) -> None:
        with self.connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    CREATE TABLE IF NOT EXISTS schema_migrations (
                        filename TEXT PRIMARY KEY,
                        applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                    )
                    """
                )

        for migration in sorted(migrations_dir.glob("*.sql")):
            with self.connect() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT 1 FROM schema_migrations WHERE filename = %s",
                        (migration.name,),
                    )
                    if cur.fetchone() is not None:
                        continue
                    cur.execute(migration.read_text(encoding="utf-8"))
                    cur.execute(
                        "INSERT INTO schema_migrations (filename) VALUES (%s)"
                        " ON CONFLICT DO NOTHING",
                        (migration.name,),
                    )

    def ensure_user(self, telegram_user_id: int, timezone: str) -> UUID:
        with self.connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO trellis_users (telegram_user_id, timezone)
                    VALUES (%s, %s)
                    ON CONFLICT (telegram_user_id)
                    DO UPDATE SET timezone = EXCLUDED.timezone, updated_at = NOW()
                    RETURNING id
                    """,
                    (telegram_user_id, timezone),
                )
                return cursor.fetchone()[0]

    def list_users(self) -> list[tuple[UUID, int]]:
        with self.connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT id, telegram_user_id FROM trellis_users ORDER BY created_at"
                )
                return [(row[0], row[1]) for row in cursor.fetchall()]
