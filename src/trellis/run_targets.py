from __future__ import annotations

from dataclasses import dataclass
from math import floor
from statistics import median
from typing import Any, Protocol
from uuid import UUID

from trellis.health import GarminActivityRecord


@dataclass(frozen=True)
class PaceRange:
    slow_seconds_per_km: int
    fast_seconds_per_km: int


@dataclass(frozen=True)
class HeartRateRange:
    low_bpm: int
    high_bpm: int


@dataclass(frozen=True)
class RunTarget:
    name: str
    calibrated: bool
    confidence: float
    pace_range: PaceRange | None = None
    heart_rate_range: HeartRateRange | None = None
    reasons: tuple[str, ...] = ()
    sample_size: int = 0


@dataclass(frozen=True)
class RunTargetCalibration:
    easy_run: RunTarget
    long_run: RunTarget
    interval: RunTarget

    @property
    def calibrated(self) -> bool:
        return (
            self.easy_run.calibrated
            or self.long_run.calibrated
            or self.interval.calibrated
        )


class RunTargetRepository(Protocol):
    def latest_activities(
        self,
        user_id: UUID,
        *,
        limit: int,
        activity_type: str | None = None,
    ) -> tuple[GarminActivityRecord, ...]:
        ...

    def activity_detail(self, user_id: UUID, activity_id: str) -> dict[str, Any] | None:
        ...


@dataclass(frozen=True)
class _RunSample:
    activity_id: str
    duration_minutes: float
    distance_km: float
    pace_seconds_per_km: float
    average_heart_rate: int | None
    maximum_heart_rate: int | None
    interval_segments: tuple[dict[str, float], ...]

    @property
    def has_interval_segments(self) -> bool:
        return len(self.interval_segments) >= 2


class RunTargetCalibrationService:
    def __init__(
        self,
        repository: RunTargetRepository,
        *,
        activity_limit: int = 20,
    ):
        self.repository = repository
        self.activity_limit = activity_limit

    def calibrate(self, user_id: UUID) -> RunTargetCalibration:
        activities = self.repository.latest_activities(
            user_id,
            limit=self.activity_limit,
            activity_type="running",
        )
        samples = self._samples(user_id, activities)
        steady_runs = tuple(sample for sample in samples if not sample.has_interval_segments)
        interval_runs = tuple(sample for sample in samples if sample.has_interval_segments)

        easy = self._easy_target(steady_runs)
        return RunTargetCalibration(
            easy_run=easy,
            long_run=self._long_target(steady_runs, easy),
            interval=self._interval_target(interval_runs, samples),
        )

    def _samples(
        self,
        user_id: UUID,
        activities: tuple[GarminActivityRecord, ...],
    ) -> tuple[_RunSample, ...]:
        samples: list[_RunSample] = []
        for activity in activities:
            if not _looks_like_run(activity):
                continue
            distance_km = (activity.distance_meters or 0) / 1000
            duration_minutes = (activity.duration_milliseconds or 0) / 60_000
            if distance_km < 1.5 or duration_minutes < 10:
                continue
            detail = self.repository.activity_detail(user_id, activity.activity_id)
            samples.append(
                _RunSample(
                    activity_id=activity.activity_id,
                    duration_minutes=duration_minutes,
                    distance_km=distance_km,
                    pace_seconds_per_km=(duration_minutes * 60) / distance_km,
                    average_heart_rate=activity.average_heart_rate,
                    maximum_heart_rate=activity.maximum_heart_rate,
                    interval_segments=_interval_segments(detail),
                )
            )
        return tuple(samples)

    def _easy_target(self, runs: tuple[_RunSample, ...]) -> RunTarget:
        usable = tuple(run for run in runs if run.average_heart_rate is not None)
        if len(usable) < 3:
            return RunTarget(
                name="easy_run",
                calibrated=False,
                confidence=0.0,
                reasons=(
                    f"Need at least 3 recent steady runs with pace and average HR; found {len(usable)}.",
                ),
                sample_size=len(usable),
            )

        paces = tuple(run.pace_seconds_per_km for run in usable)
        heart_rates = tuple(run.average_heart_rate for run in usable if run.average_heart_rate)
        return RunTarget(
            name="easy_run",
            calibrated=True,
            confidence=_confidence(len(usable), target=6),
            pace_range=_pace_range(paces, margin_seconds=25),
            heart_rate_range=_heart_rate_range(heart_rates, margin_bpm=7),
            reasons=("Calibrated from recent steady runs.",),
            sample_size=len(usable),
        )

    def _long_target(
        self,
        runs: tuple[_RunSample, ...],
        easy: RunTarget,
    ) -> RunTarget:
        long_runs = tuple(run for run in runs if run.duration_minutes >= 45)
        if not easy.calibrated:
            return RunTarget(
                name="long_run",
                calibrated=False,
                confidence=0.0,
                reasons=("Easy-run target must be calibrated before long-run target.",),
                sample_size=len(long_runs),
            )
        if not long_runs:
            return RunTarget(
                name="long_run",
                calibrated=False,
                confidence=0.0,
                reasons=("Need at least 1 recent steady run of 45 minutes or longer.",),
                sample_size=0,
            )

        assert easy.pace_range is not None
        assert easy.heart_rate_range is not None
        return RunTarget(
            name="long_run",
            calibrated=True,
            confidence=min(easy.confidence, _confidence(len(long_runs), target=3)),
            pace_range=PaceRange(
                fast_seconds_per_km=easy.pace_range.fast_seconds_per_km + 10,
                slow_seconds_per_km=easy.pace_range.slow_seconds_per_km + 45,
            ),
            heart_rate_range=easy.heart_rate_range,
            reasons=("Calibrated from easy-run target and recent longer steady runs.",),
            sample_size=len(long_runs),
        )

    def _interval_target(
        self,
        interval_runs: tuple[_RunSample, ...],
        all_runs: tuple[_RunSample, ...],
    ) -> RunTarget:
        segments = tuple(
            segment
            for run in interval_runs
            for segment in run.interval_segments
            if segment.get("pace_seconds_per_km") and segment.get("average_heart_rate")
        )
        if len(segments) >= 3:
            return RunTarget(
                name="interval",
                calibrated=True,
                confidence=_confidence(len(segments), target=8),
                pace_range=_pace_range(
                    tuple(segment["pace_seconds_per_km"] for segment in segments),
                    margin_seconds=15,
                ),
                heart_rate_range=_heart_rate_range(
                    tuple(segment["average_heart_rate"] for segment in segments),
                    margin_bpm=5,
                ),
                reasons=("Calibrated from recent active interval segments.",),
                sample_size=len(segments),
            )

        max_hr_values = tuple(
            run.maximum_heart_rate for run in all_runs if run.maximum_heart_rate is not None
        )
        if len(all_runs) >= 5 and len(max_hr_values) >= 3:
            estimated_high = round(median(max_hr_values))
            return RunTarget(
                name="interval",
                calibrated=False,
                confidence=0.35,
                heart_rate_range=HeartRateRange(
                    low_bpm=max(0, estimated_high - 18),
                    high_bpm=estimated_high - 8,
                ),
                reasons=(
                    "Not enough structured interval segments for pace calibration.",
                    "Provided a provisional HR range from recent maximum HR values.",
                ),
                sample_size=len(max_hr_values),
            )

        return RunTarget(
            name="interval",
            calibrated=False,
            confidence=0.0,
            reasons=(
                "Need at least 3 recent active interval segments with pace and HR.",
            ),
            sample_size=len(segments),
        )


@dataclass(frozen=True)
class HRZone:
    number: int
    name: str
    min_bpm: int
    max_bpm: int

    def contains(self, bpm: int) -> bool:
        return self.min_bpm <= bpm < self.max_bpm

    def label(self) -> str:
        return f"Zone {self.number} ({self.name})"


@dataclass(frozen=True)
class HRZones:
    zones: tuple[HRZone, ...]
    lthr: int

    def classify(self, avg_bpm: int) -> HRZone:
        for zone in reversed(self.zones):
            if avg_bpm >= zone.min_bpm:
                return zone
        return self.zones[0]


def coggan_zones(lthr: int) -> HRZones:
    """Coggan 5-zone model based on LTHR."""
    zones = (
        HRZone(1, "recovery",   0,                   round(lthr * 0.81)),
        HRZone(2, "aerobic",    round(lthr * 0.81),  round(lthr * 0.90)),
        HRZone(3, "tempo",      round(lthr * 0.90),  round(lthr * 0.94)),
        HRZone(4, "threshold",  round(lthr * 0.94),  round(lthr * 1.00)),
        HRZone(5, "anaerobic",  round(lthr * 1.00),  9999),
    )
    return HRZones(zones=zones, lthr=lthr)


def _looks_like_run(activity: GarminActivityRecord) -> bool:
    return activity.activity_type.casefold() == "running"


def _confidence(sample_size: int, *, target: int) -> float:
    return round(min(0.95, 0.45 + (sample_size / target) * 0.5), 2)


def _pace_range(
    values: tuple[float, ...],
    *,
    margin_seconds: int,
) -> PaceRange:
    midpoint = _round_half_up(median(values))
    return PaceRange(
        fast_seconds_per_km=max(0, midpoint - margin_seconds),
        slow_seconds_per_km=midpoint + margin_seconds,
    )


def _heart_rate_range(
    values: tuple[int | float, ...],
    *,
    margin_bpm: int,
) -> HeartRateRange:
    midpoint = _round_half_up(median(values))
    return HeartRateRange(
        low_bpm=max(0, midpoint - margin_bpm),
        high_bpm=midpoint + margin_bpm,
    )


def _interval_segments(detail: dict[str, Any] | None) -> tuple[dict[str, float], ...]:
    if not detail:
        return ()
    rows = _detail_rows(detail)
    segments: list[dict[str, float]] = []
    for row in rows:
        if not _is_active_interval(row):
            continue
        distance = _first_number(row, "distance", "totalDistance", "splitDistance")
        duration = _first_number(row, "duration", "movingDuration", "elapsedDuration")
        average_hr = _first_number(row, "averageHR", "averageHeartRate", "avgHeartRate")
        if not distance or not duration or not average_hr:
            continue
        if distance < 100 or duration < 30:
            continue
        segments.append(
            {
                "pace_seconds_per_km": duration / (distance / 1000),
                "average_heart_rate": average_hr,
            }
        )
    return tuple(segments)


def _detail_rows(detail: dict[str, Any]) -> tuple[dict[str, Any], ...]:
    typed = detail.get("typed_splits")
    if isinstance(typed, dict):
        rows = typed.get("splits")
        if isinstance(rows, list):
            return tuple(row for row in rows if isinstance(row, dict))
    rows = detail.get("splits")
    if isinstance(rows, list):
        return tuple(row for row in rows if isinstance(row, dict))
    raw = detail.get("raw_data")
    if isinstance(raw, dict):
        return _detail_rows(
            {
                "typed_splits": raw.get("typedSplits"),
                "splits": raw.get("splits"),
            }
        )
    return ()


def _is_active_interval(row: dict[str, Any]) -> bool:
    value = row.get("type") or row.get("splitType")
    if not isinstance(value, str):
        return False
    return value.casefold() in {"interval_active", "active_interval"}


def _first_number(row: dict[str, Any], *keys: str) -> float | None:
    for key in keys:
        value = row.get(key)
        if isinstance(value, (int, float)):
            return float(value)
    return None


def _round_half_up(value: float) -> int:
    return floor(value + 0.5)
