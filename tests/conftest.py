from __future__ import annotations

from pathlib import Path

import pytest
from testcontainers.postgres import PostgresContainer

from trellis.postgres import PostgresDatabase

MIGRATIONS_DIR = Path(__file__).parent.parent / "src" / "trellis" / "migrations"


@pytest.fixture(scope="session")
def pg_database():
    """
    Spin up a real Postgres container for the test session, run all migrations,
    and return a connected PostgresDatabase. Shared across all integration tests
    in the session to keep startup cost low.
    """
    with PostgresContainer("postgres:16") as pg:
        # testcontainers returns a SQLAlchemy-style URL; strip the driver specifier
        url = pg.get_connection_url().replace("+psycopg2", "")
        database = PostgresDatabase(url)
        database.migrate(MIGRATIONS_DIR)
        yield database
