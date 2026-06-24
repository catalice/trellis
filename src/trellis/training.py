from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date, time, timedelta
from enum import IntEnum, StrEnum
from uuid import UUID, uuid4


class Weekday(IntEnum):
    MONDAY = 0
    TUESDAY = 1
    WEDNESDAY = 2
    THURSDAY = 3
    FRIDAY = 4
    SATURDAY = 5
    SUNDAY = 6


class SessionKind(StrEnum):
    STRENGTH = "strength"
    SOCIAL_RUN = "social_run"
    HARD_RUN = "hard_run"
    EASY_RUN = "easy_run"
    LONG_RUN = "long_run"
    MOBILITY = "mobility"


class Intensity(StrEnum):
    EASY = "easy"
    MODERATE = "moderate"
    HARD = "hard"


class SocialRunStatus(StrEnum):
    ATTENDING = "attending"
    PREDECLINED = "predeclined"
    MISSED = "missed"


class PlanMode(StrEnum):
    BUILD = "build"
    DELOAD = "deload"
    HOLIDAY = "holiday"


class UnsafePlanError(ValueError):
    pass


@dataclass(frozen=True)
class TrainingGoal:
    distance_km: float = 21.0975
    target: str = "complete"
    stretch_time_minutes: int = 120

    def __post_init__(self) -> None:
        if self.distance_km <= 0:
            raise ValueError("Goal distance must be positive")
        if self.stretch_time_minutes <= 0:
            raise ValueError("Stretch time must be positive")


@dataclass(frozen=True)
class SessionBlock:
    name: str
    duration_minutes: int
    instructions: tuple[str, ...]

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ValueError("Block name cannot be empty")
        if self.duration_minutes <= 0:
            raise ValueError("Block duration must be positive")
        if not self.instructions:
            raise ValueError("Block instructions must be explicit")


@dataclass(frozen=True)
class TrainingSession:
    id: UUID
    day: Weekday
    kind: SessionKind
    title: str
    intensity: Intensity
    blocks: tuple[SessionBlock, ...]
    start_time: time | None = None
    fixed_anchor: bool = False
    notes: tuple[str, ...] = ()

    @property
    def total_minutes(self) -> int:
        return sum(block.duration_minutes for block in self.blocks)

    def block(self, name: str) -> SessionBlock:
        for block in self.blocks:
            if block.name.casefold() == name.casefold():
                return block
        raise LookupError(name)


@dataclass(frozen=True)
class WeeklyPlan:
    id: UUID
    week_start: date
    goal: TrainingGoal
    mode: PlanMode
    sessions: tuple[TrainingSession, ...]
    rationale: tuple[str, ...]
    revision: int = 1

    def __post_init__(self) -> None:
        if self.week_start.weekday() != Weekday.MONDAY:
            raise ValueError("week_start must be a Monday")

    def sessions_on(self, day: Weekday) -> tuple[TrainingSession, ...]:
        return tuple(session for session in self.sessions if session.day == day)

    @property
    def total_minutes(self) -> int:
        return sum(session.total_minutes for session in self.sessions)

    @property
    def run_sessions(self) -> tuple[TrainingSession, ...]:
        run_kinds = {
            SessionKind.SOCIAL_RUN,
            SessionKind.HARD_RUN,
            SessionKind.EASY_RUN,
            SessionKind.LONG_RUN,
        }
        return tuple(session for session in self.sessions if session.kind in run_kinds)


@dataclass(frozen=True)
class PlanningRequest:
    week_start: date
    goal: TrainingGoal = TrainingGoal()
    strength_days: tuple[Weekday, ...] = (
        Weekday.MONDAY,
        Weekday.THURSDAY,
    )
    social_day: Weekday = Weekday.WEDNESDAY
    social_time: time = time(19, 0)
    social_status: SocialRunStatus = SocialRunStatus.ATTENDING
    social_run_is_hard: bool = True
    replacement_hard_day: Weekday | None = None
    replacement_hard_time: time | None = None
    mode: PlanMode = PlanMode.BUILD
    include_fourth_run: bool = False
    long_run_minutes: int = 60
    avoid_days: tuple[Weekday, ...] = ()
    claude_sessions: tuple["TrainingSession", ...] = ()

    def __post_init__(self) -> None:
        if self.week_start.weekday() != Weekday.MONDAY:
            raise ValueError("week_start must be a Monday")
        if len(set(self.strength_days)) != len(self.strength_days):
            raise ValueError("Strength anchor days must be unique")
        if self.long_run_minutes < 30:
            raise ValueError("Long run must be at least 30 minutes")


class TrainingPlanner:
    """Merges Claude-generated sessions with fixed anchors. No coaching logic."""

    def plan(self, request: PlanningRequest) -> WeeklyPlan:
        if request.mode in (PlanMode.HOLIDAY, PlanMode.DELOAD):
            return self._recovery_plan(request)

        sessions: list[TrainingSession] = [
            self._strength(day) for day in request.strength_days
        ]
        rationale = [
            "The half-marathon plan builds around trainer-owned strength sessions.",
            "The week contains no more than one hard running stimulus.",
        ]

        occupied_days = {s.day for s in sessions}
        for cs in request.claude_sessions:
            if cs.day not in occupied_days:
                sessions.append(self._with_social_time(cs, request))
                occupied_days.add(cs.day)

        self._validate(sessions)
        return WeeklyPlan(
            id=uuid4(),
            week_start=request.week_start,
            goal=request.goal,
            mode=request.mode,
            sessions=self._sorted(sessions),
            rationale=tuple(rationale),
        )

    def _recovery_plan(self, request: PlanningRequest) -> WeeklyPlan:
        sessions: list[TrainingSession] = []

        if request.mode == PlanMode.DELOAD:
            sessions.extend(self._strength(day) for day in request.strength_days)

        occupied = {s.day for s in sessions}
        for cs in request.claude_sessions:
            if cs.day not in occupied:
                sessions.append(self._with_social_time(cs, request))
                occupied.add(cs.day)

        self._validate(sessions)
        return WeeklyPlan(
            id=uuid4(),
            week_start=request.week_start,
            goal=request.goal,
            mode=request.mode,
            sessions=self._sorted(sessions),
            rationale=(
                "This is a recovery week, not a backlog.",
                "No hard session or missed mileage will be carried into the next week.",
            ),
        )

    @staticmethod
    def _strength(day: Weekday) -> TrainingSession:
        return TrainingSession(
            id=uuid4(),
            day=day,
            kind=SessionKind.STRENGTH,
            title="Personal training: strength",
            intensity=Intensity.MODERATE,
            blocks=(
                SessionBlock(
                    "Strength session",
                    60,
                    ("Complete the programme set by the personal trainer.",),
                ),
            ),
            fixed_anchor=True,
            notes=("Trellis plans around this session and does not redesign it.",),
        )

    @staticmethod
    def _with_social_time(session: "TrainingSession", request: "PlanningRequest") -> "TrainingSession":
        if session.kind == SessionKind.SOCIAL_RUN and session.start_time is None:
            return replace(session, start_time=request.social_time)
        return session

    @staticmethod
    def _sorted(sessions: list[TrainingSession]) -> tuple[TrainingSession, ...]:
        order = {
            SessionKind.STRENGTH: 0,
            SessionKind.MOBILITY: 1,
            SessionKind.EASY_RUN: 2,
            SessionKind.LONG_RUN: 3,
            SessionKind.SOCIAL_RUN: 4,
            SessionKind.HARD_RUN: 5,
        }
        return tuple(sorted(sessions, key=lambda s: (s.day, order[s.kind])))

    @staticmethod
    def _validate(sessions: list[TrainingSession]) -> None:
        hard_runs = [
            s for s in sessions
            if s.intensity == Intensity.HARD
            and s.kind in (SessionKind.SOCIAL_RUN, SessionKind.HARD_RUN)
        ]
        if len(hard_runs) > 1:
            raise UnsafePlanError("A week cannot contain more than one hard run")

        strength_days = {s.day for s in sessions if s.kind == SessionKind.STRENGTH}
        for s in hard_runs:
            if s.day in strength_days:
                raise UnsafePlanError("A hard run cannot be placed on a strength anchor day")

        run_sessions = [
            s for s in sessions
            if s.kind in {
                SessionKind.SOCIAL_RUN,
                SessionKind.HARD_RUN,
                SessionKind.EASY_RUN,
                SessionKind.LONG_RUN,
            }
        ]
        run_days = [s.day for s in run_sessions]
        if len(run_days) != len(set(run_days)):
            raise UnsafePlanError("Only one running session may be planned per day")


def date_for_day(week_start: date, day: Weekday) -> date:
    if week_start.weekday() != Weekday.MONDAY:
        raise ValueError("week_start must be a Monday")
    return week_start + timedelta(days=int(day))
