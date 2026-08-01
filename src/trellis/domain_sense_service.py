"""
Sense service — the Mind/monitoring room. Owns how the user IS: energy/mood state
logs, body/context events (meds, sleep, period), cycle day, and the reading of
synced Garmin health/readiness (sleep, HRV, body battery, resting HR). Thin: it
validates + delegates to the repo/health-reader and returns typed data — string
formatting belongs to the tool handler. Move (coach) BORROWS readiness from here.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, tzinfo
from typing import Any, Protocol
from uuid import UUID, uuid4

from trellis.domain_sense_models import StateLog, TrackingEvent, TrackingEventType

_log = logging.getLogger(__name__)


class StateRepository(Protocol):
    def save_state(self, log: StateLog) -> StateLog: ...
    def save_event(self, event: TrackingEvent) -> TrackingEvent: ...
    def list_states_since(self, user_id: UUID, *, since: datetime) -> list[StateLog]: ...
    def list_events_since(self, user_id: UUID, *, since: datetime) -> list[TrackingEvent]: ...
    def last_period_start(self, user_id: UUID) -> TrackingEvent | None: ...
    def delete_state(self, user_id: UUID, log_id: UUID) -> bool: ...
    def delete_event(self, user_id: UUID, event_id: UUID) -> bool: ...


class HealthReader(Protocol):
    """Latest synced daily health/readiness (see infra_tracking.PostgresHealthRepository)."""
    def latest_daily_health(self, user_id: UUID) -> Any: ...


class VaultProjection(Protocol):
    """Write-only Obsidian view. Must never raise — a failed vault write must not
    break the bot."""
    def state_logged(self, log: StateLog) -> None: ...
    def tracking_changed(self, user_id: UUID) -> None: ...


class SenseService:
    def __init__(
        self,
        repo: StateRepository,
        tz: tzinfo,
        projection: VaultProjection | None = None,
        health_reader: HealthReader | None = None,
    ) -> None:
        self._repo = repo
        self._tz = tz
        self._projection = projection
        self._health = health_reader

    # -- state / mood / energy ------------------------------------------------

    def log_state(
        self,
        user_id: UUID,
        note: str,
        *,
        energy: int | None,
        mood: int | None,
        now: datetime,
        felt_at: datetime | None = None,
    ) -> StateLog:
        log = self._repo.save_state(StateLog(
            id=uuid4(),
            user_id=user_id,
            note=note,
            energy=_clamp_score(energy),
            mood=_clamp_score(mood),
            felt_at=felt_at or now,
            logged_at=now,
        ))
        if self._projection:
            self._projection.state_logged(log)
            self._projection.tracking_changed(user_id)
        return log

    def log_event(
        self,
        user_id: UUID,
        event_type: TrackingEventType,
        *,
        detail: str | None = None,
        value: float | None = None,
        occurred_at: datetime,
    ) -> TrackingEvent:
        event = self._repo.save_event(TrackingEvent(
            id=uuid4(),
            user_id=user_id,
            event_type=event_type,
            detail=detail,
            value=value,
            occurred_at=occurred_at,
        ))
        if self._projection:
            self._projection.tracking_changed(user_id)
        return event

    def today(self, user_id: UUID, now: datetime) -> list[StateLog]:
        start = now.astimezone(self._tz).replace(hour=0, minute=0, second=0, microsecond=0)
        return self._repo.list_states_since(user_id, since=start)

    def recent_states(self, user_id: UUID, *, days: int, now: datetime) -> list[StateLog]:
        since = now - timedelta(days=days)
        return self._repo.list_states_since(user_id, since=since)

    def recent_events(self, user_id: UUID, *, days: int, now: datetime) -> list[TrackingEvent]:
        since = now - timedelta(days=days)
        return self._repo.list_events_since(user_id, since=since)

    def delete_entry(self, user_id: UUID, entry_id: UUID) -> bool:
        """Remove a state log or tracking event (whichever the id matches)."""
        deleted = self._repo.delete_state(user_id, entry_id) or self._repo.delete_event(user_id, entry_id)
        if deleted and self._projection:
            self._projection.tracking_changed(user_id)
        return deleted

    def cycle_day(self, user_id: UUID, now: datetime) -> int | None:
        start = self._repo.last_period_start(user_id)
        if start is None:
            return None
        days = (now.astimezone(self._tz).date() - start.occurred_at.astimezone(self._tz).date()).days
        return days + 1 if 0 <= days < 60 else None

    def today_summary(self, user_id: UUID, now: datetime) -> str | None:
        """One compact line for Tier 2 context, e.g. 'State today: 09:12 e2/m4, 19:30 e4/m5'."""
        logs = self.today(user_id, now)
        if not logs:
            return None
        parts = []
        for log in logs:
            local = log.felt_at.astimezone(self._tz)
            scores = "/".join(
                s for s in (
                    f"e{log.energy}" if log.energy else "",
                    f"m{log.mood}" if log.mood else "",
                ) if s
            )
            parts.append(f"{local.strftime('%H:%M')} {scores or '·'}")
        return "State today: " + ", ".join(parts)

    # -- Garmin health / readiness (Sense owns the READ; Move borrows it) ------

    def recent_health(self, user_id: UUID) -> dict | None:
        """Latest synced daily health/readiness as a compact dict, or None if there's
        no health data (or no health reader wired). Best-effort — never raises."""
        if self._health is None:
            return None
        try:
            h = self._health.latest_daily_health(user_id)
        except Exception:
            _log.warning("recent_health load failed", exc_info=True)
            return None
        if h is None:
            return None
        out: dict[str, Any] = {}
        for key, attr in (
            ("date", "observed_on"), ("sleep_score", "sleep_score"),
            ("sleep_hours", "sleep_duration_minutes"), ("resting_hr", "resting_heart_rate"),
            ("hrv_last_night", "hrv_last_night"), ("hrv_status", "hrv_status"),
            ("body_battery_high", "body_battery_maximum"), ("body_battery_end", "body_battery_end"),
            ("avg_stress", "average_stress"),
        ):
            val = getattr(h, attr, None)
            if val is None:
                continue
            if attr == "observed_on":
                out[key] = val.isoformat()
            elif attr == "sleep_duration_minutes":
                out[key] = round(val / 60, 1)
            else:
                out[key] = val
        return out or None


def _clamp_score(value: int | None) -> int | None:
    if value is None:
        return None
    return max(1, min(5, int(value)))
