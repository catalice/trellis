"""An upgrade must never delete what it didn't carry over. Runs the real
migration files against a real Postgres, stopping part-way to plant legacy rows."""
from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import psycopg2
import pytest

from trellis.infra_postgres import PostgresDatabase

MIGRATIONS = sorted((Path(__file__).parent.parent / "src" / "trellis" / "migrations").glob("*.sql"))


@pytest.fixture
def old_install(pg_database):
    """A database migrated only up to 016 — the state just before the logbook."""
    name = f"upgrade_{uuid4().hex[:10]}"
    admin = psycopg2.connect(pg_database.database_url)
    admin.autocommit = True
    admin.cursor().execute(f'CREATE DATABASE "{name}"')
    admin.close()
    database = PostgresDatabase(pg_database.database_url.rsplit("/", 1)[0] + f"/{name}")
    _apply(database, [m for m in MIGRATIONS if m.name < "017"])
    return database


def _apply(database, files):
    with database.connect() as conn, conn.cursor() as cur:
        for migration in files:
            cur.execute(migration.read_text(encoding="utf-8"))


def _one(database, sql, *args):
    with database.connect() as conn, conn.cursor() as cur:
        cur.execute(sql, args)
        return cur.fetchone()


def _user(database):
    return database.ensure_user(int(uuid4().int % 10**9), "UTC")


def _rest(database):
    _apply(database, [m for m in MIGRATIONS if m.name >= "017"])


class TestLogbookMigration:
    def test_a_run_note_with_no_matching_activity_is_not_destroyed(self, old_install):
        uid = _user(old_install)
        with old_install.connect() as conn, conn.cursor() as cur:
            cur.execute("INSERT INTO training_runs (user_id, ran_on, note) VALUES (%s, '2026-02-01', %s)",
                        (uid, "Hill reps — knee felt odd on the descents"))
        _rest(old_install)
        kept = _one(old_install, "SELECT note FROM training_runs_legacy WHERE user_id = %s", uid)
        assert kept == ("Hill reps — knee felt odd on the descents",)

    def test_fully_carried_notes_leave_no_legacy_table(self, old_install):
        uid = _user(old_install)
        with old_install.connect() as conn, conn.cursor() as cur:
            cur.execute("INSERT INTO garmin_activities (user_id, activity_id, name, activity_type)"
                        " VALUES (%s, 'a1', 'Morning Run', 'running')", (uid,))
            cur.execute("INSERT INTO training_runs (user_id, ran_on, note, garmin_activity_id)"
                        " VALUES (%s, '2026-02-01', 'Morning Run — social run', 'a1')", (uid,))
        _rest(old_install)
        assert _one(old_install, "SELECT user_note FROM garmin_activities WHERE activity_id = 'a1'") == ("social run",)
        assert _one(old_install, "SELECT to_regclass('training_runs_legacy')") == (None,)
        assert _one(old_install, "SELECT to_regclass('training_runs')") == (None,)


class TestSelfReportsMigration:
    def test_a_populated_legacy_table_is_kept(self, old_install):
        uid = _user(old_install)
        with old_install.connect() as conn, conn.cursor() as cur:
            cur.execute("SELECT column_name FROM information_schema.columns WHERE table_name = 'health_self_reports'"
                        " AND is_nullable = 'NO' AND column_default IS NULL")
            required = [r[0] for r in cur.fetchall()]
        values = {"user_id": uid, "observed_on": "2026-02-01"}
        assert set(required) <= set(values), f"test needs values for {required}"
        with old_install.connect() as conn, conn.cursor() as cur:
            cur.execute("INSERT INTO health_self_reports (user_id, observed_on) VALUES (%s, %s)",
                        (uid, "2026-02-01"))
        _rest(old_install)
        assert _one(old_install, "SELECT count(*) FROM health_self_reports_legacy") == (1,)

    def test_an_empty_legacy_table_is_dropped(self, old_install):
        _rest(old_install)
        assert _one(old_install, "SELECT to_regclass('health_self_reports')") == (None,)
        assert _one(old_install, "SELECT to_regclass('health_self_reports_legacy')") == (None,)
