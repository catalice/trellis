"""
Backfill the vault's tracking views from the full DB history.

One-time (safely re-runnable) maintenance: stamps frontmatter properties on
every daily note that has tracking data (creating notes only where a day has
data but no file — bodies are never touched), writes every month's
Tracking/History file, and refreshes Recent.md, the Base, and the training
pages. Re-running just rewrites the same views from the same truth.

Run (inside the bot container, where the vault is mounted):
  docker compose exec trellis python scripts/backfill_vault.py
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone

sys.path.insert(0, "src")
from trellis.core_config import Settings
from trellis.domain_move_repo import PostgresMoveRepository
from trellis.domain_sense_repo import PostgresStateRepository
from trellis.infra_obsidian import ObsidianVault
from trellis.infra_postgres import PostgresDatabase


def main() -> int:
    settings = Settings.from_env()
    database = PostgresDatabase(settings.database_url)
    states_repo = PostgresStateRepository(database)
    vault = ObsidianVault(
        settings.obsidian_vault, settings.timezone,
        None, None, None,
        state_repo=states_repo,
        move_repo=PostgresMoveRepository(database),
    )

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
        total_days += len(days)
        print(f"{user_id}: {len(days)} days stamped, "
              f"{len({(d.year, d.month) for d in days})} month files written")
    print(f"done — {total_days} day(s) backfilled")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
