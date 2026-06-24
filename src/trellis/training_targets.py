from __future__ import annotations

from dataclasses import dataclass

from trellis.run_targets import RunTarget, RunTargetCalibration
from trellis.training import SessionKind, TrainingSession


@dataclass(frozen=True)
class WatchFriendlyTargets:
    session_title: str
    lines: tuple[str, ...]


class TrainingTargetFormatter:
    """Formats calibrated run targets without mutating the training plan."""

    def format(
        self,
        session: TrainingSession,
        calibration: RunTargetCalibration,
    ) -> WatchFriendlyTargets:
        if session.kind == SessionKind.HARD_RUN:
            return WatchFriendlyTargets(
                session_title=session.title,
                lines=self._hard_run(calibration),
            )
        if session.kind == SessionKind.EASY_RUN:
            return WatchFriendlyTargets(
                session_title=session.title,
                lines=self._steady_run(session, calibration.easy_run, "easy"),
            )
        if session.kind == SessionKind.LONG_RUN:
            return WatchFriendlyTargets(
                session_title=session.title,
                lines=self._steady_run(session, calibration.long_run, "long easy"),
            )
        if session.kind == SessionKind.SOCIAL_RUN:
            target = (
                calibration.interval
                if session.intensity.value == "hard"
                else calibration.easy_run
            )
            return WatchFriendlyTargets(
                session_title=session.title,
                lines=self._social_run(session, target),
            )

        return WatchFriendlyTargets(
            session_title=session.title,
            lines=("No running target needed for this session.",),
        )

    def _hard_run(self, calibration: RunTargetCalibration) -> tuple[str, ...]:
        easy = self._target_phrase(calibration.easy_run, fallback="easy by feel")
        hard = self._target_phrase(calibration.interval, fallback="time-only hard effort")
        recovery = self._target_phrase(
            calibration.easy_run,
            fallback="very easy jog or walk",
        )
        return (
            f"Warm up: 10:00 easy jog; target {easy}.",
            "Repeat 5 times:",
            f"Run: 3:00 hard; target {hard}.",
            f"Recover: 2:00 easy jog or walk; target {recovery}.",
            "Cool down: 10:00 easy walk/jog.",
            self._target_note(calibration.interval),
        )

    def _steady_run(
        self,
        session: TrainingSession,
        target: RunTarget,
        label: str,
    ) -> tuple[str, ...]:
        run_block = session.block("Run")
        return (
            f"Run: {run_block.duration_minutes}:00 {label}; target "
            f"{self._target_phrase(target, fallback=f'{label} by feel')}.",
            self._target_note(target),
        )

    def _social_run(
        self,
        session: TrainingSession,
        target: RunTarget,
    ) -> tuple[str, ...]:
        run_block = session.block("Run")
        return (
            f"Run: {run_block.duration_minutes}:00 with the group; target "
            f"{self._target_phrase(target, fallback='group run by feel')}.",
            self._target_note(target),
        )

    @staticmethod
    def _target_phrase(target: RunTarget, *, fallback: str) -> str:
        parts: list[str] = []
        if target.pace_range is not None and target.calibrated:
            parts.append(
                "pace "
                f"{_pace(target.pace_range.fast_seconds_per_km)}-"
                f"{_pace(target.pace_range.slow_seconds_per_km)}/km"
            )
        if target.heart_rate_range is not None and target.calibrated:
            parts.append(
                "HR "
                f"{target.heart_rate_range.low_bpm}-"
                f"{target.heart_rate_range.high_bpm} bpm"
            )
        if parts:
            return " or ".join(parts)
        if target.heart_rate_range is not None:
            return (
                f"provisional HR {target.heart_rate_range.low_bpm}-"
                f"{target.heart_rate_range.high_bpm} bpm; pace target not ready yet"
            )
        return f"{fallback}; no pace or HR target yet"

    @staticmethod
    def _target_note(target: RunTarget) -> str:
        if target.calibrated:
            return (
                f"Targets based on {target.sample_size} recent samples, "
                f"confidence {target.confidence:.0%}."
            )
        reason = target.reasons[0] if target.reasons else "Not enough data yet."
        return f"Targets not ready yet. {reason}"


def _pace(seconds_per_km: int) -> str:
    minutes, seconds = divmod(seconds_per_km, 60)
    return f"{minutes}:{seconds:02d}"
