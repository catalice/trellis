"""What a sync stamps as freshly fetched: only readings it was actually given."""
from __future__ import annotations

from datetime import date, datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

from trellis.infra_garmin import GarminSyncService, _normalize_health

NOW = datetime(2026, 3, 10, 18, 0, tzinfo=timezone.utc)


class _Repo:
    def __init__(self):
        self.saved = []
    def start_sync(self, run):
        return run
    def finish_sync(self, run):
        return run
    def upsert_daily_health(self, record):
        self.saved.append(record)
        return record


def _sync(row: dict):
    client = SimpleNamespace(sync=lambda dump, start, end: (
        _normalize_health(row, fallback_date=None, location="test"),))
    repo = _Repo()
    service = GarminSyncService(connection_repository=None, health_repository=repo, client=client)
    count, unavailable = service._sync_daily_health(
        uuid4(), "session", start_date=date(2026, 3, 10), end_date=date(2026, 3, 10), now=NOW, chunk_days=7)
    return repo.saved[0].raw, unavailable


def test_an_answer_with_no_body_battery_stamps_no_body_battery():
    raw, unavailable = _sync({"date": "2026-03-10", "steps": 9000, "sleep_score": 81,
                              "body_battery_max": None, "body_battery_end": None})
    assert unavailable == {}                                   # nothing FAILED...
    assert set(raw["refreshed_at"]) == {"steps", "sleep_score"}  # ...and nothing absent is called fresh
    assert raw["refreshed_at"]["steps"] == NOW.isoformat()


def test_a_failed_request_is_reported_and_stamps_nothing_for_its_readings():
    raw, unavailable = _sync({"date": "2026-03-10", "steps": 9000, "unavailable": ["sleep"]})
    assert unavailable == {"2026-03-10": ("sleep",)}
    assert set(raw["refreshed_at"]) == {"steps"}
