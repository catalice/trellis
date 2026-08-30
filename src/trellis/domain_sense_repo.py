"""
Sense storage — mood/energy state logs + tracking events (meds, sleep, period).
Protocol at top, Postgres impl below. Reads/writes state_logs + tracking_events
(the same tables as before — Sense just owns them now). Typed data, never strings.
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any
from uuid import UUID

from psycopg2.extras import RealDictCursor

from trellis.domain_sense_models import StateLog, TrackingEvent, TrackingEventType

_log = logging.getLogger(__name__)


class PostgresStateRepository:
    def __init__(self, database: Any) -> None:
        self._db = database

    def save_state(self, log: StateLog) -> StateLog:
        with self._db.connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO state_logs (id, user_id, note, energy, mood, felt_at, logged_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                    """,
                    (log.id, log.user_id, log.note, log.energy, log.mood, log.felt_at, log.logged_at),
                )
        return log

    def delete_state(self, user_id: UUID, log_id: UUID) -> bool:
        with self._db.connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "DELETE FROM state_logs WHERE id = %s AND user_id = %s",
                    (log_id, user_id),
                )
                return cur.rowcount > 0

    def delete_event(self, user_id: UUID, event_id: UUID) -> bool:
        with self._db.connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "DELETE FROM tracking_events WHERE id = %s AND user_id = %s",
                    (event_id, user_id),
                )
                return cur.rowcount > 0

    def save_event(self, event: TrackingEvent) -> TrackingEvent:
        with self._db.connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO tracking_events (id, user_id, event_type, detail, value, occurred_at)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    """,
                    (
                        event.id, event.user_id, str(event.event_type),
                        event.detail, event.value, event.occurred_at,
                    ),
                )
        return event

    def list_states_since(self, user_id: UUID, *, since: datetime) -> list[StateLog]:
        with self._db.connect() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute(
                    """
                    SELECT * FROM state_logs
                    WHERE user_id = %s AND felt_at >= %s
                    ORDER BY felt_at
                    """,
                    (user_id, since),
                )
                return [_state_log(r) for r in cur.fetchall()]

    def list_events_since(self, user_id: UUID, *, since: datetime) -> list[TrackingEvent]:
        with self._db.connect() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute(
                    """
                    SELECT * FROM tracking_events
                    WHERE user_id = %s AND occurred_at >= %s
                    ORDER BY occurred_at
                    """,
                    (user_id, since),
                )
                return [_tracking_event(r) for r in cur.fetchall()]

    def last_period_start(self, user_id: UUID) -> TrackingEvent | None:
        with self._db.connect() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute(
                    """
                    SELECT * FROM tracking_events
                    WHERE user_id = %s AND event_type = 'period_start'
                    ORDER BY occurred_at DESC
                    LIMIT 1
                    """,
                    (user_id,),
                )
                row = cur.fetchone()
                return _tracking_event(row) if row else None


def _state_log(row: dict) -> StateLog:
    return StateLog(
        id=row["id"],
        user_id=row["user_id"],
        note=row["note"],
        energy=row.get("energy"),
        mood=row.get("mood"),
        felt_at=row["felt_at"],
        logged_at=row["logged_at"],
    )


def _tracking_event(row: dict) -> TrackingEvent:
    return TrackingEvent(
        id=row["id"],
        user_id=row["user_id"],
        event_type=TrackingEventType(row["event_type"]),
        detail=row.get("detail"),
        value=float(row["value"]) if row.get("value") is not None else None,
        occurred_at=row["occurred_at"],
    )
