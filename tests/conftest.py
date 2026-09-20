from __future__ import annotations

import os
from pathlib import Path
from uuid import uuid4

import pytest

from trellis.infra_postgres import PostgresDatabase

MIGRATIONS_DIR = Path(__file__).parent.parent / "src" / "trellis" / "migrations"


@pytest.fixture(scope="session")
def pg_database():
    """
    A real Postgres (pgvector, as deployed) for the session, all migrations
    applied. Tests that use it prove behaviour at the storage boundary, which
    fake repositories cannot. Skipped when Docker isn't reachable.
    """
    try:
        from testcontainers.postgres import PostgresContainer
        container = PostgresContainer("pgvector/pgvector:pg16")
        container.start()
    except Exception as error:   # no Docker, no image, no network
        pytest.skip(f"real-database tests need Docker: {error}")
    try:
        # testcontainers returns a SQLAlchemy-style URL; strip the driver specifier
        url = container.get_connection_url().replace("+psycopg2", "")
        database = PostgresDatabase(url)
        database.migrate(MIGRATIONS_DIR)
        yield database
    finally:
        container.stop()


@pytest.fixture
def pg_user(pg_database):
    """A fresh user row — every table hangs off one."""
    return pg_database.ensure_user(int(uuid4().int % 10**9), os.getenv("TRELLIS_TIMEZONE", "UTC"))
