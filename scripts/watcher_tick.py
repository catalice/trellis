"""Run one Watcher tick manually — wired EXACTLY like core_main (all deps).
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
from trellis.core_history import PostgresConversationHistory
from trellis.core_watcher import PostgresWatcherRepository, Watcher, WatcherDiscovery
from trellis.domain_focus_repo import (
    PostgresCaptureRepository,
    PostgresEffortRepository,
    PostgresTaskRepository,
)
from trellis.domain_move_repo import PostgresMoveRepository
from trellis.domain_sense_repo import PostgresStateRepository
from trellis.infra_embeddings import LocalEmbedder
from trellis.infra_memory import MemoryIndex
from trellis.infra_obsidian import ObsidianVault
from trellis.infra_postgres import PostgresDatabase
from trellis.infra_tracking import PostgresHealthRepository


def main() -> int:
    s = Settings.from_env()
    db = PostgresDatabase(s.database_url)
    vault = ObsidianVault(s.obsidian_vault, s.timezone, None, None, None)
    watcher = Watcher(
        PostgresWatcherRepository(db),
        WatcherDiscovery(Anthropic(api_key=s.anthropic_api_key), s.anthropic_model),
        state_repo=PostgresStateRepository(db),
        health_repo=PostgresHealthRepository(db),
        run_repo=PostgresMoveRepository(db),
        tz=s.timezone,
        vault=vault,
        task_repo=PostgresTaskRepository(db),
        capture_repo=PostgresCaptureRepository(db),
        effort_repo=PostgresEffortRepository(db),
        memory=MemoryIndex(db, LocalEmbedder()),
        history=PostgresConversationHistory(db, s.timezone),
    )
    now = datetime.now(timezone.utc)
    for uid, _tg in db.list_users():
        watcher.tick(uid, now)
        print(f"ticked {uid}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
