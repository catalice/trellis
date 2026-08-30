"""Run one Watcher tick manually — via the same factory core_main uses.
The only sanctioned way to tick outside the daily loop; partial hand-wirings
caused a false failed-verification on 10 Aug (a missing memory index read as
a failed theme test). Run inside the bot container:

  docker compose exec trellis python scripts/watcher_tick.py
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone

sys.path.insert(0, "src")

from anthropic import Anthropic

from trellis.core_config import Settings
from trellis.core_main import build_watcher
from trellis.infra_postgres import PostgresDatabase


def main() -> int:
    s = Settings.from_env()
    db = PostgresDatabase(s.database_url)
    watcher = build_watcher(db, s, Anthropic(api_key=s.anthropic_api_key))
    now = datetime.now(timezone.utc)
    for uid, _tg in db.list_users():
        watcher.tick(uid, now)
        print(f"ticked {uid}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
