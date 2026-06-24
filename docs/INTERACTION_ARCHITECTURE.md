# Trellis Interaction Architecture

**Status:** Active refactor contract  
**Date:** 8 June 2026  
**Purpose:** Define the shared AI/Python collaboration model for every Trellis interaction.

This document exists to prevent Trellis from becoming a set of separate scripts with a thin AI gloss. The user experience must feel like one assistant, even though the implementation is modular.

## Principle

Trellis is not "AI versus Python".

It is:

1. **AI understands and communicates.**
2. **Python verifies, stores, calculates and protects state.**
3. **AI speaks from verified facts.**

Python should not write all of the reasoning sentences. AI should not directly mutate state.

## Standard Turn Flow

Every Telegram message should eventually follow the same shape:

```text
user message
  -> AI input layer interprets intent
  -> Python gathers current facts
  -> Python exposes allowed actions
  -> AI may reason/propose within those facts
  -> Python executes only validated actions
  -> module returns structured InteractionResult
  -> AI output layer writes the final message
```

The final user reply should not depend on which internal module handled the request. Task, reminder, training, health and capture interactions should share the same conversational rules.

## Ownership Boundaries

### AI Owns

- understanding typo-heavy natural language;
- recognizing whether the user is asking, proposing, correcting or instructing;
- explaining tradeoffs;
- deciding what context is most relevant to mention;
- producing a natural Telegram reply;
- asking concise clarification questions when Python marks the action unsafe or underspecified.

### Python Owns

- database writes;
- Obsidian writes;
- reminder scheduling;
- Garmin synchronization and normalization;
- readiness calculations;
- training-plan generation and safety rules;
- task matching, deduplication and lifecycle changes;
- validating model outputs before execution;
- refusing unsafe, ambiguous or impossible operations.

### Shared Boundary

Python gives AI structured reality. AI gives Python structured intent or a natural-language response. Neither layer should guess the other's job.

## InteractionResult Contract

Every module should return an `InteractionResult` or an equivalent structured result that can be converted into one.

Required fields:

- `domain`: task, reminder, training, health, capture, correction, idea, learning, morning or conversation.
- `action`: what was done or answered, for example `create_plan`, `list`, `archive`, `explain_plan`.
- `changed_state`: whether PostgreSQL, Obsidian, reminders or pending state changed.
- `user_visible_response`: deterministic fallback text.

Recommended fields:

- `confidence`: confidence in interpretation or execution where useful.
- `facts`: verified facts the output AI may use.
- `allowed_actions`: safe next actions the user could request.
- `safety_notes`: constraints the output AI must preserve.
- `raw`: small debugging metadata such as IDs, week start or references.

The output layer must treat `InteractionResult` as the source of truth.

## Response Rules

The AI output layer should:

- preserve exact dates, times, task names, training targets and readiness values;
- make state changes explicit without sounding robotic;
- answer reasoning questions directly;
- avoid generic templates when structured facts allow a specific answer;
- avoid "if you like" endings;
- avoid compliance framing, streak framing and guilt;
- not invent facts or claim changes that Python did not make.

Examples:

Bad:

```text
The plan is built around three priorities...
```

Better:

```text
I would keep the easy run on Friday for now. Your hard run is Wednesday, PT is Thursday, and the long run is Sunday, so Friday gives the week more breathing room. Tuesday can work, but only as a short easy run because it sits close to the hard session.
```

Bad:

```text
Processed: 1 tasks.
```

Better:

```text
Saved this as a task: draft running plan.
```

## Current Implementation Status

### Implemented

- Shared `InteractionResult` type exists.
- Telegram can pass `InteractionResult` into the response composer.
- The response composer receives structured execution context.
- Training has a native `telegram_interaction_result(...)` path.
- Training returns structured facts about active week, mode, total time, run count, PT anchors, hard run, easy run, long run and rationale.

### Transitional

- Tasks, reminders, health, captures, corrections and ideas are wrapped into `InteractionResult` at the Telegram layer.
- For those domains, many facts are still inferred from deterministic strings.
- This is acceptable only as a migration bridge, not the final architecture.

### Not Yet Done

- Tasks do not yet natively return structured interaction results.
- Reminders do not yet natively return structured interaction results.
- Health/readiness does not yet natively return structured interaction results.
- Capture and correction services do not yet natively return structured interaction results.
- The global command layer does not yet receive full domain context before interpreting.
- The output layer does not yet receive deep module context for all domains.

## Migration Plan

Migrate one module at a time, but against this single shared contract.

Order:

1. **Training** — highest reasoning load and highest user frustration. Started.
2. **Reminders** — scheduling needs explicit state-change and time facts.
3. **Tasks** — completion, archive, selection and list need clearer action/fact separation.
4. **Health/readiness** — Garmin facts should be structured, not rendered first.
5. **Capture/ideas/corrections** — brain-dump synthesis needs structured summary, created records and visible routing.
6. **Learning** — future module should start on this contract from day one.

Each migration should:

- keep existing behavior working;
- add native structured result output;
- add tests proving facts/actions/state are passed to the output layer;
- remove string-scraping once the native result is stable;
- avoid changing the user-facing voice separately per module.

## Non-Goals

This architecture does not mean:

- AI can directly write to PostgreSQL or Obsidian;
- AI can schedule reminders without Python validation;
- Python should stop enforcing safety rules;
- every response needs a model call;
- every internal service must become slow or model-dependent.

Small confirmations may remain deterministic when they are already human. Reasoning, tradeoffs, summaries and mixed-context replies should go through the AI conversation layer.

## Design Test

For any new Trellis behavior, ask:

1. Did AI understand the user in natural language?
2. Did Python gather and verify the relevant reality?
3. Did Python expose safe next actions?
4. Did AI speak from those facts without inventing?
5. Is the same interaction pattern used across modules?

If the answer to 5 is no, the design is drifting.
