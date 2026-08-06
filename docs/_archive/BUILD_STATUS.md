# Trellis Build Status

**Last updated:** 7 June 2026  
**Current stage:** Live foundation plus task, Garmin and first training vertical slices  
**Complete MVP:** No

This document explains what exists in code now and what remains to be built.

For the shared AI/Python collaboration model, see
[`INTERACTION_ARCHITECTURE.md`](INTERACTION_ARCHITECTURE.md).

## Status Labels

- **Working and tested** — implemented and verified through automated or real-service tests.
- **Live, limited scope** — running through Telegram, but only the documented capabilities are available.
- **Scaffolded** — storage or architecture exists, but the user workflow is incomplete.
- **Not built** — defined in the product specification only.

## What Works Now

### Project Foundation — Working and Tested

- Independent `Trellis-v2` repository
- Python modular-monolith structure
- Docker image
- Isolated PostgreSQL 16 database with pgvector available
- Automatic packaged database migration
- Environment-based configuration
- Telegram user allow-list support
- Canonical product specification

The project does not require the Allerac frontend, legacy Trellis bot or Ollama.

### Semantic Message Routing — Working and Tested

Trellis has a Claude-backed routing layer before any database or Obsidian writes.

It classifies each Telegram message as:

- conversation;
- health query;
- task list query;
- task selection query;
- task completion;
- correction;
- capture;
- reminder;
- training;
- learning.

The router does not execute anything. Python validates the structured routing decision, then sends the message to the correct service. If the model routing call fails, Trellis falls back to the older deterministic parser for basic task and capture availability.

This fixes the earlier failure mode where state queries such as `What tasks do I have?` could be saved as brain dumps.

### Basic Task Management — Working and Tested

Trellis can:

- create a task from phrases such as `I need to call my mum`;
- extract separate tasks from a multiline bullet list;
- preserve actionable parent lines while ignoring simple section headings;
- prevent exact open-task duplicates;
- list open tasks;
- return up to three suggested tasks for today;
- complete a task from phrases such as `Done with gestor`, `I have paid my gestor` or `Gestor paid`;
- complete a task by matching a meaningful part of its name;
- refuse ambiguous completion rather than completing the wrong task;
- store priority, energy, due-time and lifecycle fields internally;
- retain `created` and `completed` task events for later pattern learning.

Task execution remains deterministic after routing. The AI decides which service should handle the message; Python still validates, deduplicates, stores and completes tasks.

### Obsidian Task Projection — Working and Tested

Trellis can project open tasks into a managed section of:

`Calendar/Tasks.md`

It:

- preserves content outside the Trellis markers;
- writes stable task identifiers into Markdown comments;
- updates the projection after task creation or completion;
- uses an atomic file replacement to reduce partial-write risk;
- rejects malformed or duplicate Trellis markers instead of overwriting ambiguously.

Current limitation: synchronization is one-way from PostgreSQL to Obsidian. Manual Obsidian edits are not yet reconciled back into PostgreSQL.

### PostgreSQL Path — Working and Tested

The real database path has been tested for:

- applying migrations;
- creating a Trellis user from a Telegram ID;
- creating a task;
- storing its event;
- projecting it into a temporary Obsidian vault;
- matching and completing the correct task.

### Capture and Synthesis — Live, Experimental

Substantive Telegram messages now pass through Claude for structured interpretation.

Trellis:

- saves the untouched original before interpretation;
- produces a concise synthesis;
- separates concrete tasks from undeveloped ideas;
- rejects exploratory questions or speculative mechanisms when they are emitted as tasks;
- retains questions, observations and decisions without turning them all into work;
- validates the model's structured output before executing it;
- links created tasks and ideas back to the source capture;
- writes the dated original and synthesis to `Calendar/Trellis Captures/YYYY-MM-DD.md`;
- writes ideas to `Calendar/trellis-idea-inbox.md`;
- reports how many tasks and ideas it processed.

Python remains responsible for validation, duplicate prevention, database writes and permitted Obsidian paths. Claude does not receive direct filesystem or database access.

Current limitations:

- text messages only; Telegram voice transcription is not built;
- idea merging currently recognizes exact titles, not semantic similarity;
- dates such as `next week` remain visible in task wording but are not yet converted into structured due dates;
- a failed model call preserves the original but requires later reprocessing;
- interpretation quality still needs testing with genuine mixed brain dumps.

Simple greetings, task-state queries, training requests, learning requests and reminder requests are routed away from capture before synthesis.

### Conversational Correction — Live, Limited Scope

Trellis can now understand explicit corrections such as:

- `That wardrobe app item is an idea, not a task.`
- `Those wardrobe items are ideas, not tasks. Merge them into the wardrobe idea.`

For task-to-idea corrections, Trellis:

- gives Claude only the current active task and idea IDs;
- rejects IDs outside that supplied context;
- verifies every selected task still belongs to the user and is active;
- archives the task and updates or creates the idea in one database transaction;
- records task events and a correction event;
- refreshes Tasks and the idea inbox in Obsidian;
- appends a human-readable correction to the relevant dated source capture.

Trellis also supports:

- renaming one existing task without changing its classification;
- promoting one inbox idea into a concrete task;
- archiving the promoted idea so it does not remain duplicated;
- preserving the originating capture and correction audit trail.

Current correction limitations:

- reprocessing an entire capture is not yet implemented;
- task splitting and merging are not yet implemented;
- ambiguous corrections make no changes and ask for a more specific reference.

## Live Telegram Interface

### Telegram Interface — Live, Limited Scope

The bot currently understands natural messages for:

- greetings and thanks;
- brain dumps with tasks and ideas;
- task list queries such as `What tasks do I have?`;
- idea queries such as `Show my ideas` or `Do I have ideas related to running?`;
- task selection queries such as `What should I do today?`;
- task completion such as `I have paid my gestor`;
- read-only Garmin status queries such as `What does Garmin say?`;
- latest Garmin activity and workout-segment queries such as `most recent activity?`
  and `show intervals for latest run`;
- first-slice training planning such as `plan training this week`,
  `show my training plan` and `I can't make the social run Wednesday,
  give me a hard run for Friday morning`;
- explicit task-to-idea corrections;
- reminder and learning requests as unsupported-but-recognized intents.

Reminder and learning requests are not silently captured as tasks. Trellis explicitly states that those capabilities are not live yet.

The separate Trellis-v2 bot is polling Telegram. It does not conflict with the legacy bot token.

## Scaffolded but Incomplete

### Reminders — Scaffolded, Later MVP Slice

The database has a reminders table.

Still required, likely after or alongside morning readiness:

- natural-language date and time parsing;
- scheduling and delivery;
- rescheduling, pausing and cancellation;
- quiet hours;
- Telegram confirmation and failure handling.

### Task Learning — Data Foundation Only, Post-MVP Conclusions

Task events are retained so later versions can learn:

- completion patterns;
- repeated postponement;
- realistic capacity;
- useful reminder timing;
- tasks needing decomposition, urgency or another person's presence.

No automated task-pattern conclusions are currently generated. This is intentionally post-MVP; the MVP keeps the event history so later learning has evidence.

### Obsidian Synchronization — Partial

PostgreSQL-to-Obsidian task projection exists.

Still required:

- reading manual task edits from Obsidian;
- conflict detection and reconciliation;
- completed-task history in Obsidian;
- capture and idea synchronization;
- synchronization metadata updates.

## Not Built Yet

### Remaining Capture Work

- Telegram voice transcription
- Very-long-input chunking
- Capture reprocessing after a failed model call
- Semantic merging with existing ideas and tasks
- More advanced context updates to existing projects

### Ideas

- `Calendar/trellis-idea-inbox.md`
- Idea merging and deduplication
- Incubating, active and archived states
- Promotion into Efforts

### Full Task Management

- Task updates beyond completion
- Due-date extraction
- Working reminders
- Dependencies and blocked states
- Postponement reasons
- Contextual selection using readiness, available time and life load
- Natural handling of large mixed brain dumps

### Training — First Slice Live

The deterministic training engine is now connected to Telegram, PostgreSQL and Obsidian in a first vertical slice.

Trellis can:

- create an active weekly training plan;
- show the active plan;
- write the visible plan to `Calendar/Training.md`;
- persist the plan, sessions and exact blocks in PostgreSQL;
- include PT strength anchors, social run, easy run, long run and mobility;
- include exact activation, running and cooldown instructions;
- adapt the week when the social run is declined in advance;
- replace the social run with one purposeful hard run when safe;
- displace a lower-priority easy run if the replacement hard run lands on the same day;
- create holiday or deload-style weeks from explicit language.

Current limitation: this is deterministic planning, not Garmin/readiness-driven adaptation yet.

Still required:

- readiness and Garmin inputs;
- live end-to-end testing.

### Training Not Yet Live

- Half-marathon goal and phases
- Completed-training history and response learning

### Garmin Local Sync — Working and Tested

The Garmin foundation has been implemented and tested:

- Trellis-owned health-worker service copied from Allerac and stripped of debug endpoints;
- worker contract for `/connect`, `/mfa`, `/sync`, `/activities`, `/daily-health` and `/health`;
- payload and credential logging removed;
- typed Garmin client boundary with normalized models and raw source preservation;
- health persistence models and migration for daily health, activities, self-reports and sync runs;
- Compose service definition for the local worker;
- local `trellis-garmin-setup` command for interactive Garmin connection without putting credentials in Telegram or `.env`;
- encrypted PostgreSQL storage for Garmin session dumps;
- PostgreSQL health repository for daily health, activities, self-reports and sync runs;
- local `trellis-garmin-sync` command for read-only sync into PostgreSQL;
- connection status update with `last_sync_at` and sanitized failure storage;
- latest Garmin activity display in Telegram;
- Garmin activity details storage;
- workout-segment display for the latest run without assuming Garmin laps are kilometre splits.

First live verification on 7 June 2026:

- Garmin connection exists and is enabled;
- two daily-health records synced for 6 June 2026 to 7 June 2026;
- zero activities were returned for that window;
- `last_sync_at` is set;
- no connection error is stored.

Still required:

- Telegram sync commands;
- quiet scheduled sync;
- readiness calculation from stored Garmin rows;
- training adaptation from readiness trends.

### Readiness — Implemented, Not Live

The deterministic readiness calculator has been implemented and tested. It produces a score, band, confidence, input contributions, rationale and missing-data list from normalized health and self-report inputs.

Still required before it is user-visible:

- Morning interaction
- Training adjustment from trends

The health worker is available as a local service. Garmin setup and manual sync
work locally. Telegram can read the latest stored Garmin summary and latest
activity detail, but it cannot trigger a sync yet.

### Period and Symptom Context

- Period start and end recording
- Cycle estimates
- Symptoms and observations
- Use in readiness and training interpretation

### Learning Module — Post-MVP

- Persistent curricula
- Lesson progress
- Follow-up exploration
- Atlas export
- Scheduled learning

Learning is intentionally post-MVP so it does not delay the external-brain and training foundation.

### Optional UI

No web interface has been built. Telegram and Obsidian remain the planned initial interfaces.

## Verification Completed

- 97 automated tests pass.
- Python source and tests compile.
- Python wheel and source package build successfully.
- The migration is included inside the built package.
- Docker Compose configuration validates.
- The PostgreSQL container is healthy.
- The Trellis bot Docker image builds.
- The installed container can locate and apply its packaged migration.
- The real PostgreSQL task lifecycle smoke test passes.
- An isolated live Claude structured-interpretation call passes.
- The full capture pipeline passes against PostgreSQL and a temporary Obsidian vault.
- The full conversational correction path passes with Claude, PostgreSQL and a temporary Obsidian vault.
- Garmin setup stores the session encrypted in PostgreSQL.
- Manual Garmin sync stores daily-health data in PostgreSQL through the local worker.
- Manual Garmin sync can store recent activity details for workout-segment display.
- The first Telegram training-planning slice passes service and routing tests.

The tests use a temporary Obsidian vault. The real `Calendar/Tasks.md` was not modified during verification.

## Current Operational State

The PostgreSQL container is running locally on port `5433`.

The Telegram bot and PostgreSQL database are running through Docker Compose.

Trellis can be used for the basic task workflows documented above. Brain dumps
are live but still experimental. Garmin local setup, manual sync and read-only
Telegram status are working. Trellis must not yet be relied upon for timed
reminders, training, cycle context or learning.

## Recommended Build Order

1. Complete task CRUD, date parsing and real reminders.
2. Add capture preservation and brain-dump synthesis.
3. Add two-way synchronization for Trellis-owned Obsidian files.
4. Add ideas and routing.
5. Add training goals, plans, session detail and flexible replanning.
6. Connect Garmin, readiness and period context.
7. Complete MVP hardening and deploy the Telegram bot.
8. Add curriculum learning and later pattern-learning features.

## Source Documents

- Product intent and complete scope: `docs/PRODUCT_SPEC.md`
- Local setup and commands: `README.md`
- Current implementation status: `docs/BUILD_STATUS.md`
