"""
Sense storage — ONE tracking log (migration 020, her design): a row is one
entry — when, their words, and a FACTS map ({"mood": 3, "energy": 2},
{"meds": "..."}, {"sleep_hours": 7.5}, {"anxiety": 2}, ...). A new trackable
dimension is a new key: data, never schema.

StateLog and TrackingEvent survive as VIEWS composed from the rows (the
logbook pattern), so every consumer — service, vault, Watcher — reads the
shapes it always has. States = rows carrying feeling facts or bare words;
events = rows carrying meds/sleep/period facts.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from psycopg2.extras import Json, RealDictCursor

from trellis.domain_sense_models import StateLog, TrackingEvent, TrackingEventType

_EVENT_KEYS = ("meds", "sleep", "sleep_hours", "period")


class PostgresStateRepository:
    def __init__(self, database: Any) -> None:
        self._db = database

    # -- writes ---------------------------------------------------------------

    def save_state(self, log: StateLog) -> StateLog:
        facts: dict[str, Any] = {}
        if log.mood is not None:
            facts["mood"] = log.mood
        if log.energy is not None:
            facts["energy"] = log.energy
        if log.extra:
            facts.update(log.extra)
        self._insert(log.id, log.user_id, log.felt_at, log.logged_at, log.note, facts)
        return log

    def save_event(self, event: TrackingEvent) -> TrackingEvent:
        if event.event_type == TrackingEventType.MEDS:
            words, facts = None, {"meds": event.detail or "meds"}
        elif event.event_type == TrackingEventType.SLEEP:
            words, facts = event.detail, {"sleep": True}
            if event.value is not None:
                facts["sleep_hours"] = event.value
        else:  # period start/end
            words = event.detail
            facts = {"period": "started"
                     if event.event_type == TrackingEventType.PERIOD_START
                     else "ended"}
        self._insert(event.id, event.user_id, event.occurred_at,
                     event.occurred_at, words, facts)
        return event

    def _insert(self, row_id, user_id, felt_at, logged_at, words, facts) -> None:
        with self._db.connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO tracking_log (id, user_id, felt_at, logged_at, words, facts)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    """,
                    (row_id, user_id, felt_at, logged_at, words, Json(facts)),
                )

    def delete_state(self, user_id: UUID, log_id: UUID) -> bool:
        return self._delete(user_id, log_id)

    def delete_event(self, user_id: UUID, event_id: UUID) -> bool:
        return self._delete(user_id, event_id)

    def _delete(self, user_id: UUID, row_id: UUID) -> bool:
        with self._db.connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "DELETE FROM tracking_log WHERE id = %s AND user_id = %s",
                    (row_id, user_id),
                )
                return cur.rowcount > 0

    # -- views (the shapes every consumer has always read) --------------------

    def list_states_since(self, user_id: UUID, *, since: datetime) -> list[StateLog]:
        rows = self._rows_since(user_id, since=since)
        return [_state_view(r) for r in rows if _is_state(r)]

    def list_events_since(self, user_id: UUID, *, since: datetime) -> list[TrackingEvent]:
        out: list[TrackingEvent] = []
        for r in self._rows_since(user_id, since=since):
            out.extend(_event_views(r))
        return out

    def last_period_start(self, user_id: UUID) -> TrackingEvent | None:
        with self._db.connect() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute(
                    """
                    SELECT * FROM tracking_log
                    WHERE user_id = %s AND facts->>'period' = 'started'
                    ORDER BY felt_at DESC LIMIT 1
                    """,
                    (user_id,),
                )
                row = cur.fetchone()
                views = _event_views(row) if row else []
                return views[0] if views else None

    def period_starts(self, user_id: UUID) -> list:
        """Distinct dates of period starts, oldest first (dupes from the old
        double-logging era collapse here)."""
        with self._db.connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT DISTINCT felt_at::date FROM tracking_log"
                    " WHERE user_id = %s AND facts->>'period' = 'started'"
                    " ORDER BY 1",
                    (user_id,),
                )
                return [r[0] for r in cur.fetchall()]

    def tracked_kinds(self, user_id: UUID) -> list[str]:
        """Every fact key ever logged — so the model reuses kind names instead
        of minting synonyms, and the Watcher can correlate any kind by name."""
        with self._db.connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT DISTINCT jsonb_object_keys(facts) FROM tracking_log"
                    " WHERE user_id = %s",
                    (user_id,),
                )
                return sorted(k for (k,) in cur.fetchall() if k != "sleep")

    def _rows_since(self, user_id: UUID, *, since: datetime) -> list[dict]:
        with self._db.connect() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute(
                    """
                    SELECT * FROM tracking_log
                    WHERE user_id = %s AND felt_at >= %s
                    ORDER BY felt_at
                    """,
                    (user_id, since),
                )
                return list(cur.fetchall())


def _is_state(row: dict) -> bool:
    facts = row.get("facts") or {}
    if any(k in facts for k in _EVENT_KEYS):
        return False
    return bool(row.get("words")) or bool(facts)


def _state_view(row: dict) -> StateLog:
    facts = dict(row.get("facts") or {})
    mood = facts.pop("mood", None)
    energy = facts.pop("energy", None)
    return StateLog(
        id=row["id"],
        user_id=row["user_id"],
        note=row.get("words") or "",
        energy=int(energy) if energy is not None else None,
        mood=int(mood) if mood is not None else None,
        felt_at=row["felt_at"],
        logged_at=row["logged_at"],
        extra=facts or None,
    )


def _event_views(row: dict) -> list[TrackingEvent]:
    """One log row can carry several event facts; each becomes the event view
    its consumers expect."""
    facts = row.get("facts") or {}
    out: list[TrackingEvent] = []
    if "meds" in facts:
        out.append(TrackingEvent(
            id=row["id"], user_id=row["user_id"],
            event_type=TrackingEventType.MEDS,
            detail=str(facts["meds"]), occurred_at=row["felt_at"],
        ))
    if "sleep" in facts or "sleep_hours" in facts:
        hours = facts.get("sleep_hours")
        out.append(TrackingEvent(
            id=row["id"], user_id=row["user_id"],
            event_type=TrackingEventType.SLEEP,
            detail=row.get("words"),
            value=float(hours) if hours is not None else None,
            occurred_at=row["felt_at"],
        ))
    if "period" in facts:
        out.append(TrackingEvent(
            id=row["id"], user_id=row["user_id"],
            event_type=(TrackingEventType.PERIOD_START
                        if facts["period"] == "started"
                        else TrackingEventType.PERIOD_END),
            detail=row.get("words"), occurred_at=row["felt_at"],
        ))
    return out
