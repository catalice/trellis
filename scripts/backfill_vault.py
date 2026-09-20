"""
Backfill the vault's tracking views from the full DB history.

One-time (safely re-runnable) maintenance: stamps frontmatter properties on
every daily note that has tracking data (creating notes only where a day has
data but no file — bodies are never touched), writes every month's
Tracking/History file, refreshes Recent.md, the Base, and the training pages,
and re-projects every Learn map page (only Trellis's own marked region of a map
is ever replaced — writing done by hand on the page stays). Re-running just
rewrites the same views from the same truth.

Run (inside the bot container, where the vault is mounted):
  docker compose exec trellis python scripts/backfill_vault.py
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone

sys.path.insert(0, "src")
from trellis.core_config import Settings
from trellis.core_main import build_vault
from trellis.domain_learn_repo import PostgresLearnRepository
from trellis.domain_learn_service import LearnService
from trellis.domain_sense_repo import PostgresStateRepository
from trellis.infra_postgres import PostgresDatabase


def main() -> int:
    settings = Settings.from_env()
    database = PostgresDatabase(settings.database_url)
    states_repo = PostgresStateRepository(database)
    vault = build_vault(database, settings)
    learn = LearnService(PostgresLearnRepository(database), settings.timezone, projection=vault)

    epoch = datetime(2020, 1, 1, tzinfo=timezone.utc)
    total_days = 0
    for user_id, _tg in database.list_users():
        states = states_repo.list_states_since(user_id, since=epoch)
        events = states_repo.list_events_since(user_id, since=epoch)
        days = {s.felt_at.astimezone(settings.timezone).date() for s in states}
        days |= {e.occurred_at.astimezone(settings.timezone).date() for e in events}
        for day in sorted(days):
            vault._update_daily_properties(user_id, day)
        for year, month in sorted({(d.year, d.month) for d in days}):
            vault.write_tracking_month(user_id, year, month)
        vault.tracking_changed(user_id)
        vault.plan_changed(user_id)
        maps = learn.project_all(user_id)
        total_days += len(days)
        print(f"{user_id}: {len(days)} days stamped, "
              f"{len({(d.year, d.month) for d in days})} month files written, "
              f"{maps} map page(s) re-projected")
    print(f"done — {total_days} day(s) backfilled")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
