# Trellis Readiness

## Status

Domain foundation only. It is deterministic and not wired to Telegram, PostgreSQL,
Garmin transport, or the live training planner.

## Purpose

Readiness turns normalized daily health data and optional self-report into an
inspectable daily signal. It is a planning aid, not a diagnosis engine.

The output contains:

- score from `0` to `100`;
- band: `low`, `steady`, `ready`, or `strong`;
- confidence: `low`, `medium`, or `high`;
- per-input point contributions;
- rationale sentences;
- missing metric names.

## Inputs

`DailyReadinessInput` accepts:

- sleep duration or sleep score;
- body battery;
- resting heart rate and personal baseline;
- HRV last night and personal baseline, or HRV status;
- average stress;
- self-report: energy, body, life load, and soreness from `1` to `10`;
- previous readiness results for trend adjustment.

`DailyReadinessInput.from_normalized_health` can adapt a normalized health object
without importing Garmin client or transport code. This keeps readiness reusable for
manual reports, Garmin-derived data, or later health sources.

## Scoring Rules

The score starts at `65`. Each available signal adds or subtracts explicit points.
Missing metrics contribute `0` and lower confidence.

Current contribution ranges:

- sleep: `-8` to `+13`;
- body battery: `-10` to `+8`;
- HRV: `-10` to `+7`;
- resting heart rate: `-8` to `+4`;
- stress: `-5` to `+4`;
- self-report: subjective points from energy, body, life load and soreness;
- objective-data breadth: `+9` when five Garmin-derived signals are present without self-report;
- trend: `-6`, `0`, or `+3`.

If a severe negative signal is present, the final score is capped at `89`. This means
one bad metric cannot collapse the day, but it also cannot be ignored by calling the
day `strong`.

## Bands

- `low`: below `55`;
- `steady`: `55` to `74`;
- `ready`: `75` to `89`;
- `strong`: `90` and above.

## Missing Data

Missing inputs do not produce fake precision. Trellis applies no points for missing
metrics, reports them in `missing_metrics`, and lowers confidence according to the
number of available signals.

A day with only self-report can still be useful, but should be treated as
low-confidence.

## Trend Awareness

The calculator looks at the last three supplied readiness results:

- average below `55`: subtract `6` points;
- average `75` or above: add `3` points;
- otherwise: no adjustment.

This favors the overall pattern over a single daily blip.

## Safety Boundary

Readiness does not diagnose illness, injury, burnout, menstrual-cycle effects, ADHD,
autism, or any other condition. It only summarizes available planning signals.

Application layers may use readiness to adapt training volume or intensity, but they
must keep the rationale visible and preserve the original inputs.
