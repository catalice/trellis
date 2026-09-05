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
        extra: dict | None = None,
    ) -> StateLog:
        log = self._repo.save_state(StateLog(
            id=uuid4(),
            user_id=user_id,
            note=note,
            energy=_clamp_score(energy),
            mood=_clamp_score(mood),
            felt_at=felt_at or now,
            logged_at=now,
            extra={str(k): v for k, v in (extra or {}).items()
                   if str(k).strip()} or None,
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

    def day_rows(self, user_id: UUID, *, since, until, now) -> dict:
        """The Watcher's day-by-day view, on demand — one dict per day of
        everything tracked. Borrow the slow mind's eyes (her design)."""
        from datetime import datetime as _dt, time as _t, timezone as _tz
        from trellis.core_watcher import build_daily_frame
        start_dt = _dt.combine(since, _t.min, tzinfo=self._tz)
        states = [s for s in self._repo.list_states_since(user_id, since=start_dt)
                  if s.felt_at.astimezone(self._tz).date() <= until]
        events = [e for e in self._repo.list_events_since(user_id, since=start_dt)
                  if e.occurred_at.astimezone(self._tz).date() <= until]
        health = []
        if self._health is not None:
            try:
                health = [h for h in self._health.daily_health_since(user_id, since=since)
                          if h.observed_on <= until]
            except Exception:
                _log.warning("day_rows health failed", exc_info=True)
        frame = build_daily_frame(
            user_id, states=states, events=events, health_rows=health,
            runs=(), tz=self._tz, today=until,
        )
        return {d: row for d, row in frame.items() if since <= d <= until}

    def cycle_summary(self, user_id: UUID) -> dict | None:
        """Computed from the FULL period history — never typed, never stale.
        Returns starts, average/min/max cycle length, and the next expected
        window. None until two starts exist."""
        starts = self._repo.period_starts(user_id)
        if len(starts) < 2:
            return None
        gaps = [(b - a).days for a, b in zip(starts, starts[1:])]
        gaps = [g for g in gaps if 15 <= g <= 60]   # ignore data glitches
        if not gaps:
            return None
        avg = sum(gaps) / len(gaps)
        from datetime import timedelta
        last = starts[-1]
        return {
            "starts": starts,
            "avg_days": round(avg, 1),
            "min_days": min(gaps),
            "max_days": max(gaps),
            "last_start": last,
            "next_expected": last + timedelta(days=round(avg)),
            "window_start": last + timedelta(days=min(gaps)),
            "window_end": last + timedelta(days=max(gaps)),
        }

    def tracked_kinds(self, user_id: UUID) -> list[str]:
        return self._repo.tracked_kinds(user_id)

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

    def recent_health(self, user_id: UUID, now: datetime | None = None) -> dict | None:
        """Latest synced daily health/readiness as a compact dict, or None if there's
        no health data (or no health reader wired). Best-effort — never raises.

        When `now` is given, the dict includes `stale_days` (0 = the record is for
        today in the user's timezone). Staleness is a FACT, so Python computes it —
        the model must never have to do date math to notice yesterday's data."""
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
        if out and now is not None and getattr(h, "observed_on", None) is not None:
            today = now.astimezone(self._tz).date()
            out["stale_days"] = max(0, (today - h.observed_on).days)
        # WHEN the record was synced (local HH:MM) — day-granular staleness isn't
        # enough for intraday metrics: body battery/steps move all day, so a
        # morning sync's numbers are "as of then", not "now". Facts are Python's.
        synced = getattr(h, "updated_at", None)
        if out and synced is not None:
            try:
                out["synced_at"] = synced.astimezone(self._tz).strftime("%H:%M")
            except (ValueError, OSError):
                pass
        return out or None


def _clamp_score(value: int | None) -> int | None:
    if value is None:
        return None
    return max(1, min(5, int(value)))
