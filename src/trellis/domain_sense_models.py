"""
Sense (Mind) domain models — how the user IS: mood/energy state logs and the
body/context events (meds, sleep, period) that make up wellbeing tracking. Frozen
dataclasses only, no I/O. The monitoring/awareness side of Trellis.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
from uuid import UUID


class TrackingEventType(StrEnum):
    MEDS = "meds"
    SLEEP = "sleep"
    PERIOD_START = "period_start"
    PERIOD_END = "period_end"


@dataclass(frozen=True)
class StateLog:
    id: UUID
    user_id: UUID
    note: str                             # their words, verbatim
    energy: int | None                    # 1-5, derived from the note
    mood: int | None                      # 1-5, derived from the note
    felt_at: datetime                     # when the state was felt (may be retro)
    logged_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass(frozen=True)
class TrackingEvent:
    id: UUID
    user_id: UUID
    event_type: TrackingEventType
    detail: str | None = None             # meds name, sleep quality, symptom note
    value: float | None = None            # sleep hours
    occurred_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
