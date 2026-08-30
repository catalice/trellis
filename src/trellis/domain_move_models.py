"""
Training model — one lean record per user. The coach (Claude) reads the goal +
baseline + real calendar and authors the plan JSON in conversation; this only
carries it. No enums, no session/plan-status machinery — that judgment is Claude's.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Any
from uuid import UUID


@dataclass(frozen=True)
class TrainingPlan:
    user_id: UUID
    goal_id: UUID | None
    baseline: str | None
    plan: dict[str, Any]          # Claude-authored: {"arc": "...", "week": [{date,type,detail}, ...]}
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass(frozen=True)
class RunLog:
    """A logbook view over a recorded activity (since migration 017 these are
    garmin_activities rows — the watch's record IS the record). note composes
    the Garmin name with the user's words; user_note is their words alone."""
    id: UUID
    user_id: UUID
    ran_on: date
    note: str                     # "Long Run (avg HR 152) — felt strong"
    distance_km: float | None = None
    garmin_activity_id: str | None = None   # the activity's real identity
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    activity_type: str | None = None        # running / strength_training / hiit / ...
    user_note: str | None = None            # the user's layer, sync can't touch it
    name: str | None = None                 # the Garmin name alone
    duration_min: float | None = None
    avg_hr: int | None = None
    max_hr: int | None = None
