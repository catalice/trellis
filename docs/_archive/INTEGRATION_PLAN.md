# Trellis Parallel Build Integration Plan

**Date:** 7 June 2026  
**Approach:** Build foundations in parallel; integrate one user-visible slice at a time.

## Parallel Workstreams

### Semantic Router

Produces one validated intent for each Telegram message:

- conversation;
- task state query;
- task selection;
- task completion;
- correction;
- capture;
- reminder;
- training;
- learning.

The router interprets. It does not write to PostgreSQL or Obsidian.

### Garmin Connector

Provides a typed client boundary around the Garmin health worker:

- authentication and MFA;
- daily health;
- historical metrics;
- activities;
- normalized errors and timeouts.

The connector fetches and normalizes measured data. It does not calculate readiness or modify training.

### Training Engine

Provides deterministic domain behavior:

- half-marathon goal and training phases;
- weekly plans around strength and social-run anchors;
- exact activation, run, recovery and total time blocks;
- missed-session and same-day replanning;
- mobility, holiday and deload handling;
- persistence-ready models.

The engine plans. It does not call Telegram, Garmin or Claude directly.

## Integration Order

### Gate 1: Semantic Routing

Integrate the router into Telegram before adding new domains.

Acceptance:

- greetings remain conversational;
- state queries never become captures;
- substantive dumps reach capture synthesis;
- corrections reach correction handling;
- unsupported training/reminder/learning requests receive an honest capability response;
- no existing task or capture regression.

### Gate 2: Garmin Sync

Integrate authentication and read-only health synchronization.

Acceptance:

- credentials and sessions remain protected;
- measured data is stored with provenance;
- self-report does not overwrite Garmin;
- failures do not corrupt previous health data;
- no training advice is generated yet.

### Gate 3: Readiness

Add transparent readiness calculation from trends, Garmin and subjective context.

Acceptance:

- inputs and rationale are inspectable;
- one poor metric does not dominate automatically;
- missing data degrades gracefully;
- cycle estimates remain labelled estimates.

### Gate 4: Weekly Training

Connect stored goal, anchors, readiness history and training engine.

Acceptance:

- plan progresses toward a half marathon and `2:00` stretch goal;
- strength sessions are respected;
- social run is hard only while it supplies a hard stimulus;
- every workout has exact instructions and a complete time block;
- plan is visible in Obsidian.

### Gate 5: Adaptive Training

Expose same-day and midweek replanning through Telegram.

Acceptance:

- changed availability recalculates the remaining week;
- missed sessions do not create backlog;
- holidays can become maintenance or deload;
- requested intensity is rejected when the weekly arrangement is unsafe;
- every change preserves an audit trail without creating a separate noise file.

### Gate 6: Reminders

Add natural date parsing and delivery only after routing is stable.

Acceptance:

- due dates and reminder times are distinct;
- reminders can be moved, paused or removed conversationally;
- quiet hours are enforced;
- Trellis never claims a reminder was scheduled when it was not.

## Shared Engineering Rules

- Claude interprets; Python validates and executes.
- Domain modules do not depend on Telegram.
- PostgreSQL is operational state; Obsidian is visible state.
- Every external measurement and user report preserves provenance.
- Important failures are explicit; partial success is never presented as complete success.
- Live deployment follows tests, review and a disposable end-to-end smoke test.
- One integration gate is deployed and exercised before the next gate begins.
