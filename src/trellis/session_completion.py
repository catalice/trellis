from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Protocol
from uuid import UUID, uuid4

from trellis.health import GarminActivityRecord
from trellis.training import SessionKind, TrainingSession, WeeklyPlan, date_for_day


@dataclass(frozen=True)
class WorkoutCheckin:
    id: UUID
    user_id: UUID
    session_kind: str
    checked_in_on: date
    perceived_effort: int | None = None
    feel_note: str | None = None
    soreness_note: str | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


class WorkoutCheckinRepository(Protocol):
    def save(self, checkin: WorkoutCheckin) -> WorkoutCheckin: ...
    def list_recent(self, user_id: UUID, *, limit: int) -> list[WorkoutCheckin]: ...


@dataclass(frozen=True)
class SessionCompletion:
    id: UUID
    user_id: UUID
    plan_id: UUID
    session_id: UUID
    garmin_activity_id: int | None
    session_kind: str
    planned_on: date
    completed_at: datetime | None
    created_at: datetime


class SessionCompletionRepository(Protocol):
    def save(self, completion: SessionCompletion) -> SessionCompletion: ...
    def list_for_week(self, user_id: UUID, week_start: date) -> list[SessionCompletion]: ...


class ActivitySource(Protocol):
    def latest_activities(
        self,
        user_id: UUID,
        *,
        limit: int,
        activity_type: str | None = None,
    ) -> tuple[GarminActivityRecord, ...]: ...


class PlanSource(Protocol):
    def latest_active(self, user_id: UUID, week_start: date) -> WeeklyPlan | None: ...


_RUN_KINDS = {
    SessionKind.EASY_RUN,
    SessionKind.HARD_RUN,
    SessionKind.LONG_RUN,
    SessionKind.SOCIAL_RUN,
}

_STRENGTH_TYPES = {"strength_training", "fitness_equipment", "weight_training"}


class SessionCompletionService:
    def __init__(
        self,
        repository: SessionCompletionRepository,
        activity_source: ActivitySource,
        plan_source: PlanSource,
    ) -> None:
        self.repository = repository
        self.activity_source = activity_source
        self.plan_source = plan_source

    def match_week(
        self, user_id: UUID, week_start: date, as_of: date
    ) -> list[SessionCompletion]:
        plan = self.plan_source.latest_active(user_id, week_start)
        if plan is None:
            return []

        all_activities = self.activity_source.latest_activities(user_id, limit=50)

        # Activities by date, filtered to <= as_of; mutable lists so we can consume
        available: dict[date, list[GarminActivityRecord]] = {}
        for activity in all_activities:
            adate = _activity_date(activity)
            if adate is None or adate > as_of:
                continue
            available.setdefault(adate, []).append(activity)

        # Only sessions with matchable kinds and planned on or before as_of
        sessions_to_match = [
            (session, date_for_day(week_start, session.day))
            for session in plan.sessions
            if session.kind in _RUN_KINDS or session.kind == SessionKind.STRENGTH
        ]
        sessions_to_match = [
            (s, d) for s, d in sessions_to_match if d <= as_of
        ]

        # Two global passes: same-day first (delta=0), then adjacents (delta=-1, +1).
        # Each consumed activity is removed from the pool — no double-matching.
        matched: dict[UUID, GarminActivityRecord] = {}
        for delta in (0, -1, 1):
            for session, planned_on in sessions_to_match:
                if session.id in matched:
                    continue
                activity = _find_and_consume(available, planned_on + timedelta(days=delta), session.kind)
                if activity is not None:
                    matched[session.id] = activity

        completions: list[SessionCompletion] = []
        for session, planned_on in sessions_to_match:
            if session.id not in matched:
                continue
            completion = _make_completion(matched[session.id], user_id, plan, session, planned_on)
            completions.append(self.repository.save(completion))

        return completions

    def refresh(self, user_id: UUID, week_start: date, as_of: date) -> None:
        """Run session matching and persist results. Call after a Garmin sync."""
        self.match_week(user_id, week_start, as_of)

    def summary(self, user_id: UUID, week_start: date) -> str:
        """Read pre-computed completions from DB and format. No computation."""
        stored = self.repository.list_for_week(user_id, week_start)
        plan = self.plan_source.latest_active(user_id, week_start)
        if plan is None:
            return ""
        done_session_ids = {c.session_id for c in stored}
        lines: list[str] = []
        for session in plan.sessions:
            planned_on = date_for_day(week_start, session.day)
            mark = "✓" if session.id in done_session_ids else "—"
            lines.append(f"{mark} {planned_on.strftime('%a %d %b')}  {session.title}")
        return "\n".join(lines)


def _find_and_consume(
    available: dict[date, list[GarminActivityRecord]],
    check_date: date,
    kind: SessionKind,
) -> GarminActivityRecord | None:
    candidates = available.get(check_date, [])
    for i, activity in enumerate(candidates):
        if _matches_kind(activity, kind):
            candidates.pop(i)
            return activity
    return None


def _matches_kind(activity: GarminActivityRecord, kind: SessionKind) -> bool:
    atype = (activity.activity_type or "").casefold()
    if kind in _RUN_KINDS:
        return "running" in atype
    if kind == SessionKind.STRENGTH:
        return atype in _STRENGTH_TYPES
    return False


def _activity_date(record: GarminActivityRecord) -> date | None:
    if record.start_time_epoch_seconds is None:
        return None
    return datetime.fromtimestamp(record.start_time_epoch_seconds, tz=timezone.utc).date()


class WorkoutCheckinService:
    def __init__(self, repository: WorkoutCheckinRepository) -> None:
        self.repository = repository

    def record(
        self,
        user_id: UUID,
        session_kind: str,
        checked_in_on: date,
        *,
        perceived_effort: int | None = None,
        feel_note: str | None = None,
        soreness_note: str | None = None,
    ) -> WorkoutCheckin:
        valid = {k.value for k in SessionKind}
        if session_kind not in valid:
            raise ValueError(f"Invalid session_kind '{session_kind}'. Valid: {sorted(valid)}")
        checkin = WorkoutCheckin(
            id=uuid4(),
            user_id=user_id,
            session_kind=session_kind,
            checked_in_on=checked_in_on,
            perceived_effort=perceived_effort,
            feel_note=feel_note,
            soreness_note=soreness_note,
        )
        return self.repository.save(checkin)

    def list_recent(self, user_id: UUID, *, limit: int = 5) -> list[WorkoutCheckin]:
        return self.repository.list_recent(user_id, limit=limit)


def _make_completion(
    activity: GarminActivityRecord,
    user_id: UUID,
    plan: WeeklyPlan,
    session: TrainingSession,
    planned_on: date,
) -> SessionCompletion:
    garmin_id: int | None = None
    try:
        garmin_id = int(activity.activity_id)
    except (ValueError, TypeError):
        pass

    completed_at = None
    if activity.start_time_epoch_seconds is not None:
        completed_at = datetime.fromtimestamp(
            activity.start_time_epoch_seconds, tz=timezone.utc
        )

    return SessionCompletion(
        id=uuid4(),
        user_id=user_id,
        plan_id=plan.id,
        session_id=session.id,
        garmin_activity_id=garmin_id,
        session_kind=session.kind.value,
        planned_on=planned_on,
        completed_at=completed_at,
        created_at=datetime.now(tz=timezone.utc),
    )
