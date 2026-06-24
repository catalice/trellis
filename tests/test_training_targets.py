from __future__ import annotations

from datetime import date
import unittest

from trellis.run_targets import (
    HeartRateRange,
    PaceRange,
    RunTarget,
    RunTargetCalibration,
)
from trellis.training import (
    Intensity,
    SessionBlock,
    SessionKind,
    TrainingSession,
    Weekday,
)
from trellis.training_targets import TrainingTargetFormatter
from uuid import uuid4


class TrainingTargetFormatterTest(unittest.TestCase):
    def test_easy_run_uses_calibrated_pace_and_hr(self):
        session = _session(SessionKind.EASY_RUN)

        result = TrainingTargetFormatter().format(session, _calibration())

        self.assertEqual("easy run", result.session_title)
        self.assertIn("Run: 35:00 easy", result.lines[0])
        self.assertIn("pace 6:45-7:25/km", result.lines[0])
        self.assertIn("HR 138-152 bpm", result.lines[0])
        self.assertIn("Targets based on 4 recent samples", result.lines[1])

    def test_long_run_uses_long_run_target(self):
        session = _session(SessionKind.LONG_RUN)

        result = TrainingTargetFormatter().format(session, _calibration())

        self.assertIn("Run: 60:00 long easy", result.lines[0])
        self.assertIn("pace 7:05-8:10/km", result.lines[0])
        self.assertIn("HR 136-150 bpm", result.lines[0])

    def test_hard_run_uses_interval_target_for_repeats(self):
        session = _session(SessionKind.HARD_RUN)

        result = TrainingTargetFormatter().format(session, _calibration())

        self.assertIn("Warm up: 10:00", result.lines[0])
        self.assertIn("pace 6:45-7:25/km", result.lines[0])
        self.assertIn("Run: 3:00 hard", result.lines[2])
        self.assertIn("pace 5:45-6:10/km", result.lines[2])
        self.assertIn("HR 158-170 bpm", result.lines[2])

    def test_honest_fallback_when_targets_are_not_calibrated(self):
        session = _session(SessionKind.HARD_RUN)
        result = TrainingTargetFormatter().format(
            session,
            RunTargetCalibration(
                easy_run=_uncalibrated("easy_run"),
                long_run=_uncalibrated("long_run"),
                interval=_uncalibrated("interval"),
            ),
        )

        self.assertIn("easy by feel; no pace or HR target yet", result.lines[0])
        self.assertIn(
            "time-only hard effort; no pace or HR target yet",
            result.lines[2],
        )
        self.assertIn("Targets not ready yet", result.lines[-1])

    def test_provisional_hr_is_labelled_as_not_calibrated(self):
        session = _session(SessionKind.HARD_RUN)
        calibration = RunTargetCalibration(
            easy_run=_uncalibrated("easy_run"),
            long_run=_uncalibrated("long_run"),
            interval=RunTarget(
                name="interval",
                calibrated=False,
                confidence=0.35,
                heart_rate_range=HeartRateRange(148, 158),
                reasons=("Not enough structured interval segments.",),
                sample_size=5,
            ),
        )

        result = TrainingTargetFormatter().format(session, calibration)

        self.assertIn("provisional HR 148-158 bpm", result.lines[2])
        self.assertIn("pace target not ready yet", result.lines[2])
        self.assertIn("Targets not ready yet", result.lines[-1])


def _session(kind: SessionKind) -> TrainingSession:
    run_minutes = {
        SessionKind.EASY_RUN: 35,
        SessionKind.LONG_RUN: 60,
        SessionKind.HARD_RUN: 30,
    }[kind]
    intensity = Intensity.HARD if kind == SessionKind.HARD_RUN else Intensity.EASY
    return TrainingSession(
        id=uuid4(),
        day=Weekday.FRIDAY,
        kind=kind,
        title=kind.value.replace("_", " "),
        intensity=intensity,
        blocks=(
            SessionBlock("Activation", 10, ("Warm up.",)),
            SessionBlock("Run", run_minutes, ("Run.",)),
            SessionBlock("Cool-down and mobility", 10, ("Cool down.",)),
        ),
    )


def _calibration() -> RunTargetCalibration:
    return RunTargetCalibration(
        easy_run=RunTarget(
            name="easy_run",
            calibrated=True,
            confidence=0.78,
            pace_range=PaceRange(slow_seconds_per_km=445, fast_seconds_per_km=405),
            heart_rate_range=HeartRateRange(138, 152),
            reasons=("Calibrated from recent steady runs.",),
            sample_size=4,
        ),
        long_run=RunTarget(
            name="long_run",
            calibrated=True,
            confidence=0.62,
            pace_range=PaceRange(slow_seconds_per_km=490, fast_seconds_per_km=425),
            heart_rate_range=HeartRateRange(136, 150),
            reasons=("Calibrated from easy-run target and recent longer steady runs.",),
            sample_size=1,
        ),
        interval=RunTarget(
            name="interval",
            calibrated=True,
            confidence=0.70,
            pace_range=PaceRange(slow_seconds_per_km=370, fast_seconds_per_km=345),
            heart_rate_range=HeartRateRange(158, 170),
            reasons=("Calibrated from recent active interval segments.",),
            sample_size=3,
        ),
    )


def _uncalibrated(name: str) -> RunTarget:
    return RunTarget(
        name=name,
        calibrated=False,
        confidence=0.0,
        reasons=("Need more recent running data.",),
    )


if __name__ == "__main__":
    unittest.main()
