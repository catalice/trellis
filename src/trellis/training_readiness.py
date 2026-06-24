from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from enum import StrEnum
from typing import Protocol

from trellis.readiness import ReadinessBand, ReadinessResult
from trellis.readiness_service import ReadinessSnapshot
from trellis.training import (
    Intensity,
    SessionKind,
    TrainingSession,
    WeeklyPlan,
    date_for_day,
)


class TrainingAdjustment(StrEnum):
    KEEP = "keep"
    REDUCE = "reduce"
    SWAP = "swap"
    REST = "rest"


class ReadinessLike(Protocol):
    score: int
    band: ReadinessBand
    confidence: str
    contributions: tuple
    rationale: tuple[str, ...]
    missing_metrics: tuple[str, ...]


@dataclass(frozen=True)
class TrainingReadinessRecommendation:
    action: TrainingAdjustment
    on_date: date
    readiness_score: int
    readiness_band: ReadinessBand
    confidence: str
    sessions: tuple[TrainingSession, ...]
    explanation: tuple[str, ...]
    suggested_change: str
    data_lines: tuple[str, ...] = ()
    missing_metrics: tuple[str, ...] = ()

    @property
    def has_training_today(self) -> bool:
        return bool(self.sessions)


class TrainingReadinessAdvisor:
    """Maps readiness to a same-day training recommendation without mutating plans."""

    def recommend(
        self,
        plan: WeeklyPlan,
        readiness: ReadinessSnapshot | ReadinessResult,
        *,
        on_date: date | None = None,
    ) -> TrainingReadinessRecommendation:
        target_date = on_date or _readiness_date(readiness)
        sessions = self._sessions_on_date(plan, target_date)

        if not sessions:
            return self._recommendation(
                TrainingAdjustment.KEEP,
                target_date,
                readiness,
                sessions,
                "No training is planned today.",
                "Keep the day open unless you want gentle mobility.",
            )

        priority_session = self._priority_session(sessions)

        if readiness.band == ReadinessBand.LOW:
            return self._low_readiness(
                plan,
                target_date,
                readiness,
                sessions,
                priority_session,
            )

        if readiness.band == ReadinessBand.STEADY:
            return self._steady_readiness(
                target_date,
                readiness,
                sessions,
                priority_session,
            )

        return self._recommendation(
            TrainingAdjustment.KEEP,
            target_date,
            readiness,
            sessions,
            self._session_reason(priority_session),
            "Keep the planned session.",
        )

    def _low_readiness(
        self,
        plan: WeeklyPlan,
        target_date: date,
        readiness: ReadinessLike,
        sessions: tuple[TrainingSession, ...],
        session: TrainingSession,
    ) -> TrainingReadinessRecommendation:
        if session.kind in (SessionKind.HARD_RUN, SessionKind.SOCIAL_RUN):
            return self._recommendation(
                TrainingAdjustment.SWAP,
                target_date,
                readiness,
                sessions,
                "Readiness is low and today contains hard running.",
                self._hard_swap_change(plan, target_date),
            )
        if session.kind == SessionKind.LONG_RUN:
            return self._recommendation(
                TrainingAdjustment.REDUCE,
                target_date,
                readiness,
                sessions,
                "Readiness is low and today contains the long run.",
                "Reduce the long run to 30-40 minutes easy or replace it with a walk.",
            )
        if session.kind == SessionKind.EASY_RUN:
            return self._recommendation(
                TrainingAdjustment.REDUCE,
                target_date,
                readiness,
                sessions,
                "Readiness is low, but today is only easy running.",
                "Reduce to 20-25 minutes easy or do mobility instead.",
            )
        if session.kind == SessionKind.STRENGTH:
            return self._recommendation(
                TrainingAdjustment.REDUCE,
                target_date,
                readiness,
                sessions,
                "Readiness is low and personal training is an external anchor.",
                "Keep the appointment, but tell the trainer to reduce load or intensity.",
            )
        return self._recommendation(
            TrainingAdjustment.REST,
            target_date,
            readiness,
            sessions,
            "Readiness is low and today has no essential training load.",
            "Rest or do only gentle mobility.",
        )

    def _hard_swap_change(self, plan: WeeklyPlan, target_date: date) -> str:
        next_day = self._next_suitable_run_date(plan, target_date)
        today_change = "Today: replace it with 20-25 minutes easy or mobility only."
        if next_day is None:
            return f"{today_change} Keep the hard run out of this week unless readiness improves."
        return (
            f"{today_change} Move the hard run to "
            f"{next_day.strftime('%A %d %b')}."
        )

    @staticmethod
    def _next_suitable_run_date(plan: WeeklyPlan, target_date: date) -> date | None:
        occupied_strength_dates = {
            date_for_day(plan.week_start, session.day)
            for session in plan.sessions
            if session.kind == SessionKind.STRENGTH
        }
        occupied_hard_dates = {
            date_for_day(plan.week_start, session.day)
            for session in plan.sessions
            if session.kind in (SessionKind.HARD_RUN, SessionKind.SOCIAL_RUN)
            and date_for_day(plan.week_start, session.day) != target_date
        }
        for offset in range(1, 5):
            candidate = target_date + timedelta(days=offset)
            if candidate > plan.week_start + timedelta(days=6):
                return None
            if candidate in occupied_strength_dates or candidate in occupied_hard_dates:
                continue
            return candidate
        return None

    def _steady_readiness(
        self,
        target_date: date,
        readiness: ReadinessLike,
        sessions: tuple[TrainingSession, ...],
        session: TrainingSession,
    ) -> TrainingReadinessRecommendation:
        if session.kind in (SessionKind.HARD_RUN, SessionKind.SOCIAL_RUN):
            return self._recommendation(
                TrainingAdjustment.REDUCE,
                target_date,
                readiness,
                sessions,
                "Readiness is steady, not strong, and today contains hard running.",
                "Keep the session shape but reduce the hard work by one repeat.",
            )
        if session.kind == SessionKind.LONG_RUN:
            return self._recommendation(
                TrainingAdjustment.REDUCE,
                target_date,
                readiness,
                sessions,
                "Readiness is steady and today contains the long run.",
                "Keep it easy and cap the run if effort drifts above the target.",
            )
        return self._recommendation(
            TrainingAdjustment.KEEP,
            target_date,
            readiness,
            sessions,
            self._session_reason(session),
            "Keep the planned session.",
        )

    @staticmethod
    def _sessions_on_date(
        plan: WeeklyPlan,
        target_date: date,
    ) -> tuple[TrainingSession, ...]:
        return tuple(
            session
            for session in plan.sessions
            if date_for_day(plan.week_start, session.day) == target_date
        )

    @staticmethod
    def _priority_session(sessions: tuple[TrainingSession, ...]) -> TrainingSession:
        priority = {
            SessionKind.HARD_RUN: 0,
            SessionKind.SOCIAL_RUN: 1,
            SessionKind.LONG_RUN: 2,
            SessionKind.STRENGTH: 3,
            SessionKind.EASY_RUN: 4,
            SessionKind.MOBILITY: 5,
        }
        return sorted(
            sessions,
            key=lambda session: (
                priority[session.kind],
                0 if session.intensity == Intensity.HARD else 1,
            ),
        )[0]

    @staticmethod
    def _session_reason(session: TrainingSession) -> str:
        if session.kind == SessionKind.HARD_RUN:
            return "Today contains the planned hard running stimulus."
        if session.kind == SessionKind.SOCIAL_RUN:
            return "Today contains the social run."
        if session.kind == SessionKind.LONG_RUN:
            return "Today contains the long easy run."
        if session.kind == SessionKind.STRENGTH:
            return "Today contains personal training."
        if session.kind == SessionKind.EASY_RUN:
            return "Today is an easy run day."
        return "Today is a low-load mobility day."

    @staticmethod
    def _recommendation(
        action: TrainingAdjustment,
        target_date: date,
        readiness: ReadinessLike,
        sessions: tuple[TrainingSession, ...],
        reason: str,
        suggested_change: str,
    ) -> TrainingReadinessRecommendation:
        explanation = [reason]
        explanation.append(
            (
                f"Readiness is {readiness.score}/100 "
                f"({readiness.band.value}, {readiness.confidence} confidence)."
            )
        )
        self_report_delta = _contribution_points(readiness, "self_report")
        if self_report_delta is not None:
            if self_report_delta > 0:
                explanation.append(
                    f"Self-report lifted readiness by {_points(self_report_delta)}."
                )
            elif self_report_delta < 0:
                explanation.append(
                    f"Self-report reduced readiness by {_points(abs(self_report_delta))}."
                )
            else:
                explanation.append("Self-report did not materially change readiness.")
        explanation.extend(readiness.rationale[:2])
        if readiness.confidence == "low":
            explanation.append(
                "Use this as a conservative suggestion because readiness confidence is low."
            )
        return TrainingReadinessRecommendation(
            action=action,
            on_date=target_date,
            readiness_score=readiness.score,
            readiness_band=readiness.band,
            confidence=readiness.confidence,
            sessions=sessions,
            explanation=tuple(dict.fromkeys(explanation)),
            suggested_change=suggested_change,
            data_lines=tuple(getattr(readiness, "data_lines", ())),
            missing_metrics=readiness.missing_metrics,
        )


def _contribution_points(readiness: ReadinessLike, name: str) -> int | None:
    for contribution in getattr(readiness, "contributions", ()):
        if contribution.name == name:
            return contribution.points
    return None


def _points(value: int) -> str:
    return f"{value} point" if value == 1 else f"{value} points"


def _readiness_date(readiness: ReadinessSnapshot | ReadinessResult) -> date:
    if isinstance(readiness, ReadinessSnapshot):
        return readiness.requested_on
    return readiness.date
