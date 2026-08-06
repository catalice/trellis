# Trellis Training Engine

## Status

Domain foundation only. It is deterministic, persistence-ready, and not yet wired to
Telegram, Garmin, PostgreSQL repositories, or the live bot.

## Goal

The default goal is to complete a half marathon. The stretch goal is `2:00`.
Planning favors sustainable progress across weeks rather than perfect daily
compliance.

## Weekly Rules

- Personal training strength sessions are fixed anchors, normally Monday and Thursday.
- Trellis records and plans around strength; it does not redesign the trainer's work.
- The Wednesday social run supplies the week's hard running stimulus while its actual
  effort is hard.
- A build week contains one hard stimulus, one easy run, and one progressively longer
  easy run.
- A fourth short easy run is optional and must be explicitly enabled.
- Every run includes exact activation, run, and cool-down or mobility blocks.
- Every session duration is the sum of all blocks, so the displayed commitment includes
  preparation and recovery work.
- Short mobility maintenance is planned without creating a compulsory second programme.

## Flexible Replanning

The social run may be declined before the week or changed during the week. A caller can
request a replacement hard run on a specific day, including Wednesday morning when the
evening social run is unavailable. The replacement carries an exact start time when the
user supplies one; this is distinct from merely moving it to the same weekday.

The planner:

1. Removes the social run.
2. Adds one purposeful hard session on the requested safe day.
3. Retains the rest of the useful week.
4. Increments the plan revision.
5. Rejects a hard replacement on a strength-anchor day.

When no replacement day is supplied, the planner chooses a deterministic non-strength
day. A missed session is not treated as mileage debt.

## Social Run Progression

`social_run_is_hard` describes the current training stimulus, not a permanent pace or
heart-rate threshold. When it becomes moderate rather than hard, the social run remains
in the plan and one purposeful hard session is introduced elsewhere.

The domain does not infer this transition yet. Future performance analysis may recommend
the change from completed-session evidence, but the decision must remain inspectable.

## Holiday And Deload

A holiday plan contains two short easy runs and one mobility session. It contains no
hard work, no long-run requirement, and no work to make up afterward.

A deload uses the same reduced running structure but may retain available strength
anchors.

## Exact Session Blocks

Run sessions expose typed blocks with explicit duration and instructions. For example,
a 60-minute long run is:

```text
Activation              10 minutes
Run                     60 minutes
Cool-down and mobility  10 minutes
Total                   80 minutes
```

Instructions use exact repetitions, durations, distances, and recoveries. They avoid
optional or decision-heavy wording.

## Safety Boundary

The current deterministic constraints are:

- no more than one hard run per week;
- no hard run on a strength-anchor day;
- no more than one running session per day;
- no automatic diagnosis, injury treatment, or cycle-based medical claim;
- pain or altered movement is a reason to stop hard work and reassess.

Readiness and cycle observations are stored as context. They do not provide diagnosis,
contraceptive guidance, or automatic medical decisions.

## Persistence Schema

Migration `004_training.sql` defines:

- goals and training phases;
- versioned weekly plans;
- sessions and ordered session blocks;
- completion evidence and perceived effort;
- readiness observations with source provenance;
- cycle observations reported by the user.

Repository adapters and live migrations are intentionally outside this workstream.

## Integration Contract

The eventual application layer should:

1. Create a `PlanningRequest` from goal, phase, commitments, and current context.
2. Persist the returned `WeeklyPlan` and all session blocks.
3. Present exact session totals and instructions.
4. Record actual completion separately from the plan.
5. Use `replan_social_run` for explicit midweek changes.
6. create a new plan revision rather than silently overwriting history.
