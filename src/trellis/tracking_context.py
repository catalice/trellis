"""
Context loader for the tracking cross-layer.

Always loaded every turn (not routed). Provides a brief health and cycle
summary so the oracle always has current state without domain routing.

Usage in main.py:
    from trellis.tracking_context import tracking_context_loader
    tracking_summary=("tracking", tracking_context_loader(health_repository, cycle_service)),
"""
from __future__ import annotations

import logging
from datetime import date, datetime
from typing import Protocol
from uuid import UUID

from trellis.registry import ContextLoader

_log = logging.getLogger(__name__)


# --- Protocols (structural — no imports from service files) -----------------

class _HealthRepo(Protocol):
    def latest_daily_health(self, user_id: UUID): ...


class _CycleService(Protocol):
    def get_status(self, user_id: UUID, today: date) -> str: ...


# --- Factory ----------------------------------------------------------------

def tracking_context_loader(
    health_repository: _HealthRepo,
    cycle_service: _CycleService,
) -> ContextLoader:
    def loader(user_id: UUID, now: datetime) -> str | None:
        today = now.date()
        parts: list[str] = []

        try:
            health = health_repository.latest_daily_health(user_id)
            if health is not None:
                parts.append(_format_garmin_compact(health))
        except Exception:
            _log.warning("tracking_context: health load failed", exc_info=True)

        try:
            cycle_status = cycle_service.get_status(user_id, today)
            if cycle_status:
                parts.append(cycle_status)
        except Exception:
            _log.warning("tracking_context: cycle load failed", exc_info=True)

        if not parts:
            return None
        return "[Tracking]\n" + "\n".join(parts)

    return loader


# --- Formatting helpers -----------------------------------------------------

def _format_garmin_compact(record) -> str:
    segments: list[str] = []
    if record.hrv_last_night is not None:
        segments.append(f"HRV {record.hrv_last_night:g}ms")
    if record.sleep_duration_minutes is not None:
        h, m = divmod(record.sleep_duration_minutes, 60)
        sleep_str = f"sleep {h}h{m:02d}m"
        if record.sleep_score is not None:
            sleep_str += f" ({record.sleep_score})"
        segments.append(sleep_str)
    bb = record.body_battery_end or record.body_battery_maximum
    if bb is not None:
        segments.append(f"battery {bb}")
    if record.resting_heart_rate is not None:
        segments.append(f"rHR {record.resting_heart_rate}")
    if not segments:
        return f"Garmin ({record.observed_on.isoformat()}): no data"
    return f"Garmin ({record.observed_on.isoformat()}): " + ", ".join(segments)
